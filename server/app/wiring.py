"""Сборка сервиса из переменных окружения.

NEUROCRITTER_DATA          папка данных (по умолчанию ./data)
NEUROCRITTER_TOKEN         токен доступа (не короче 16 знаков)
NEUROCRITTER_DEV=1         разрешить запуск без токена (только для локальной разработки)
NEUROCRITTER_BRAIN         openai | claude | stub (автовыбор по имеющемуся ключу)
NEUROCRITTER_PROVIDER      закреплённый провайдер; BRAIN=stub всегда выключает мозг
NEUROCRITTER_MODEL_VOICE   gpt-5.4-mini для OpenAI; claude-haiku-5-5 для Claude
NEUROCRITTER_MODEL_NIGHT   gpt-5.4 для OpenAI; claude-sonnet-5-5 для Claude
OPENAI_API_KEY            ключ OpenAI
NEUROCRITTER_MONTHLY_LIMIT потолок расходов на API, $ в месяц (по умолчанию 5)
ANTHROPIC_API_KEY          ключ Claude (читает SDK, здесь только проверяется наличие)
"""
import os
from dataclasses import dataclass
from pathlib import Path

from brain.budget import Budget
from brain.claude_brain import ClaudeBrain
from brain.client import (MODEL_NIGHT, MODEL_VOICE, OPENAI_NIGHT, OPENAI_VOICE,
                          AnthropicTransport, OpenAITransport)
from brain.night import NightBrain
from brain.recorder import LIVE, CallLog
from engine.brain import StubBrain

from .push import PushService, load_or_create_vapid, make_webpush_sender
from .service import PetService
from .storage import Storage

MIN_TOKEN_LEN = 16


@dataclass
class Config:
    data_dir: Path
    token: str
    dev: bool
    brain: str
    monthly_limit: float
    voice_model: str = ""
    night_model: str = ""

    @classmethod
    def from_env(cls, env=None):
        env = os.environ if env is None else env
        token = env.get("NEUROCRITTER_TOKEN", "")
        dev = env.get("NEUROCRITTER_DEV") == "1"
        if not dev and len(token) < MIN_TOKEN_LEN:
            raise SystemExit("NEUROCRITTER_TOKEN обязателен и не короче "
                             f"{MIN_TOKEN_LEN} знаков (или NEUROCRITTER_DEV=1 для разработки)")
        wanted = env.get("NEUROCRITTER_BRAIN", "")
        has_key = bool(env.get("ANTHROPIC_API_KEY"))
        brain = wanted or ("openai" if env.get("OPENAI_API_KEY") else "claude" if has_key else "stub")
        if brain not in ("openai", "claude", "stub"):
            raise SystemExit("NEUROCRITTER_BRAIN: допустимы openai, claude, stub")
        provider = env.get("NEUROCRITTER_PROVIDER", "")
        if provider and provider not in ("openai", "claude"):
            raise SystemExit("NEUROCRITTER_PROVIDER: допустимы openai, claude")
        if wanted != "stub" and provider:
            brain = provider
        if brain == "openai" and not env.get("OPENAI_API_KEY"):
            raise SystemExit("NEUROCRITTER_BRAIN=openai, но OPENAI_API_KEY не задан")
        if brain == "claude" and not has_key:
            raise SystemExit("NEUROCRITTER_BRAIN=claude, но ANTHROPIC_API_KEY не задан")
        return cls(Path(env.get("NEUROCRITTER_DATA", "data")), token, dev, brain,
                   float(env.get("NEUROCRITTER_MONTHLY_LIMIT", "5")),
                   env.get("NEUROCRITTER_MODEL_VOICE", ""),
                   env.get("NEUROCRITTER_MODEL_NIGHT", ""))


def _build_push(config, storage):
    """Web Push, если установлены pywebpush и py_vapid; иначе сервис живёт без пушей."""
    try:
        vapid_path = str(config.data_dir / "vapid_private.pem")
        _, public_key = load_or_create_vapid(vapid_path)
    except ImportError:
        return None
    subject = os.environ.get("NEUROCRITTER_VAPID_SUB", "https://example.com")
    return PushService(storage, make_webpush_sender(vapid_path, subject), public_key)


def build_service(config, clock=None):
    config.data_dir.mkdir(parents=True, exist_ok=True)
    storage = Storage(str(config.data_dir / "pet.sqlite"))
    kwargs = {} if clock is None else {"clock": clock}
    push = _build_push(config, storage)
    if push is not None:
        kwargs["push"] = push
    if config.brain == "stub":
        return PetService(storage, **kwargs)

    budget = Budget(str(config.data_dir / "budget.sqlite"),
                    monthly_limit=config.monthly_limit)
    calllog = CallLog(str(config.data_dir / "calls.sqlite"), mode=LIVE)
    if config.brain == "openai":
        voice_model = config.voice_model or OPENAI_VOICE
        night_model = config.night_model or OPENAI_NIGHT
        voice = OpenAITransport(model=voice_model)
        night_transport = OpenAITransport(model=night_model, max_tokens=3000)
    else:
        voice_model = config.voice_model or MODEL_VOICE
        night_model = config.night_model or MODEL_NIGHT
        voice = AnthropicTransport(model=voice_model)
        night_transport = AnthropicTransport(model=night_model, max_tokens=3000, effort="low")
    night = NightBrain(night_transport, calllog, budget, model=night_model)
    holder = {}

    def factory(rng):
        brain = holder.get("brain")
        if brain is None:
            brain = ClaudeBrain(voice, calllog, budget, rng, night=night, model=voice_model)
            holder["brain"] = brain
        brain.rng = rng
        brain.stub = StubBrain(rng)
        del brain.reactions[:-50]          # не копим историю в памяти без конца
        return brain

    return PetService(storage, brain_factory=factory, night=night, budget=budget,
                      provider=config.brain, **kwargs)
