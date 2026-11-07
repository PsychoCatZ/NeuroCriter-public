"""FastAPI-приложение: API зверька и раздача PWA."""
import asyncio
import hmac
import logging
import time
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .service import PetService, ServiceError

CLIENT_DIR = Path(__file__).resolve().parent.parent.parent / "client"

RATE_LIMIT = 120          # запросов в минуту с одного адреса
AUTH_FAIL_LIMIT = 10      # неудачных входов в минуту с одного адреса


class Limiter:
    def __init__(self, limit, window=60.0, clock=time.monotonic):
        self.limit, self.window, self.clock = limit, window, clock
        self.hits = defaultdict(deque)

    def hit(self, key):
        """Записать обращение. True, если лимит ещё не превышен."""
        now = self.clock()
        q = self.hits[key]
        while q and now - q[0] > self.window:
            q.popleft()
        q.append(now)
        return len(q) <= self.limit

    def blocked(self, key):
        now = self.clock()
        q = self.hits[key]
        while q and now - q[0] > self.window:
            q.popleft()
        return len(q) > self.limit


class ActBody(BaseModel):
    action: str
    food: str | None = None
    text: str = Field(default="", max_length=400)


class HatchBody(BaseModel):
    tz_offset_min: int = 0


class NameBody(BaseModel):
    name: str = Field(max_length=60)


class AdvanceBody(BaseModel):
    hours: float = Field(ge=0, le=24 * 60)


class SettingsBody(BaseModel):
    quiet_start: int | None = None
    quiet_end: int | None = None
    pings_per_day: int | None = None


class PushKeys(BaseModel):
    p256dh: str = Field(max_length=600)
    auth: str = Field(max_length=600)


class PushSubBody(BaseModel):
    endpoint: str = Field(max_length=1000)
    keys: PushKeys


class PushUnsubBody(BaseModel):
    endpoint: str = Field(max_length=1000)


def client_ip(request: Request):
    host = request.client.host if request.client else "?"
    if host in ("127.0.0.1", "::1"):       # за Caddy берём адрес из заголовка
        fwd = request.headers.get("x-forwarded-for", "")
        if fwd:
            return fwd.split(",")[0].strip()
    return host


log = logging.getLogger("neurocritter")
TICK_SECONDS = 60


def create_app(service: PetService, token: str, dev=False, static_dir=CLIENT_DIR,
               rate_clock=time.monotonic, background=False):
    @asynccontextmanager
    async def lifespan(app):
        task = None
        if background:
            async def loop():
                while True:
                    await asyncio.sleep(TICK_SECONDS)
                    try:
                        await asyncio.to_thread(service.background_tick)
                    except Exception:
                        log.exception("фоновый цикл: ошибка")
            task = asyncio.create_task(loop())
        yield
        if task:
            task.cancel()

    app = FastAPI(title="Нейрозверёк", docs_url=None, redoc_url=None, openapi_url=None,
                  lifespan=lifespan)
    rate = Limiter(RATE_LIMIT, clock=rate_clock)
    fails = Limiter(AUTH_FAIL_LIMIT, clock=rate_clock)

    @app.exception_handler(ServiceError)
    async def service_error(request, exc):
        return JSONResponse({"error": str(exc)}, status_code=exc.status)

    def auth(request: Request):
        ip = client_ip(request)
        if fails.blocked(ip):
            raise ServiceError("слишком много неудачных попыток", 429)
        if not rate.hit(ip):
            raise ServiceError("слишком много запросов", 429)
        if dev and not token:
            return
        header = request.headers.get("authorization", "")
        given = header[7:] if header.lower().startswith("bearer ") else ""
        if not given or not hmac.compare_digest(given.encode(), token.encode()):
            fails.hit(ip)
            raise ServiceError("нет доступа", 401)

    @app.get("/api/health")
    def health():
        return {"ok": True}

    @app.get("/api/state", dependencies=[Depends(auth)])
    def state():
        if not service.has_pet():
            return {"hatched": False}
        return {"hatched": True, **service.state()}

    @app.post("/api/hatch", dependencies=[Depends(auth)])
    def hatch(body: HatchBody):
        return {"hatched": True, **service.hatch(body.tz_offset_min)}

    @app.post("/api/name", dependencies=[Depends(auth)])
    def name(body: NameBody):
        return {"hatched": True, **service.set_name(body.name)}

    @app.post("/api/act", dependencies=[Depends(auth)])
    def act(body: ActBody):
        return service.act(body.action, body.food, body.text)

    @app.get("/api/inspect", dependencies=[Depends(auth)])
    def inspect():
        return service.inspect()

    @app.get("/api/dossier", dependencies=[Depends(auth)])
    def dossier():
        return service.dossier()

    @app.get("/api/diary", dependencies=[Depends(auth)])
    def diary():
        return service.diary()

    def need_push():
        if service.push is None:
            raise ServiceError("push не настроен на сервере", 409)
        return service.push

    @app.get("/api/push/key", dependencies=[Depends(auth)])
    def push_key():
        push = need_push()
        return {"key": push.public_key, "subscriptions": push.count_subs()}

    @app.post("/api/push/subscribe", dependencies=[Depends(auth)])
    def push_subscribe(body: PushSubBody):
        push = need_push()
        try:
            push.subscribe(body.endpoint, body.keys.p256dh, body.keys.auth, time.time())
        except ValueError as e:
            raise ServiceError(str(e))
        return {"subscriptions": push.count_subs()}

    @app.post("/api/push/unsubscribe", dependencies=[Depends(auth)])
    def push_unsubscribe(body: PushUnsubBody):
        push = need_push()
        push.unsubscribe(body.endpoint)
        return {"subscriptions": push.count_subs()}

    @app.post("/api/push/test", dependencies=[Depends(auth)])
    def push_test():
        push = need_push()
        return push.notify("test", "Недоделок", "проверка уведомлений",
                           service.local_now(), force=True)

    @app.get("/api/settings", dependencies=[Depends(auth)])
    def get_settings():
        return service.get_settings()

    @app.post("/api/settings", dependencies=[Depends(auth)])
    def set_settings(body: SettingsBody):
        return service.set_settings(body.quiet_start, body.quiet_end, body.pings_per_day)

    if dev:
        @app.post("/api/dev/advance", dependencies=[Depends(auth)])
        def dev_advance(body: AdvanceBody):
            return {"hatched": True, **service.dev_advance(body.hours)}

    if static_dir and Path(static_dir).exists():
        app.mount("/", StaticFiles(directory=str(static_dir), html=True), name="client")
    return app
