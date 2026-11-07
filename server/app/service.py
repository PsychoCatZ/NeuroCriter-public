"""Сервис зверька: ленивое время, действия хозяина, ночные задачи, вид для клиента.

Здесь нет HTTP: сервис принимает часы (clock) и мозг, поэтому его можно
проверять тестами без сети и без реального времени.
"""
import secrets
import threading
import time

from brain.fallback import fallback_say
from engine import actions, consolidation, events, language, needs
from engine.brain import StubBrain
from engine.constants import DAY, FOODS, HOUR, STEP
from engine.journal import EVENT_DESCRIPTIONS
from engine.model import Pet
from engine.mutations import label as mutation_label
from engine.rng import Rng

ACTIONS = ("feed", "play", "pet", "clean", "walk", "sleep", "heal", "reconcile", "say")
MAX_SAY_LEN = 200


class ServiceError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def band(value):
    return 0 if value < 25 else 1 if value < 50 else 2 if value < 75 else 3


class PetService:
    def __init__(self, storage, brain_factory=None, night=None, budget=None,
                 clock=time.time, push=None, provider=None):
        self.storage = storage
        self.brain_factory = brain_factory       # (rng) -> мозг, по умолчанию заглушка
        self.provider = provider or ("claude" if brain_factory else "stub")
        self.night = night
        self.budget = budget
        self.clock = clock
        self.push = push
        self.time_shift = 0          # только для режима разработки
        self.lock = threading.RLock()

    # --- время и настройки
    def tz_offset_s(self):
        return int(self.storage.get_setting("tz_offset_min", "0")) * 60

    def local_now(self):
        return int(self.clock()) + self.tz_offset_s() + self.time_shift

    def _rng(self, pet):
        n = int(pet.flags.get("rng_n", 0))
        pet.flags["rng_n"] = n + 1
        return Rng(f"{pet.seed}:{n}")

    def _brain(self, rng):
        if self.brain_factory is None:
            return StubBrain(rng)
        brain = self.brain_factory(rng)
        return brain

    def _load(self):
        pet = self.storage.load_pet()
        if pet is None:
            raise ServiceError("зверька ещё нет: сначала вылупить", 409)
        return pet

    def _save(self, pet):
        self.storage.save_pet(pet, self.clock())

    # --- рождение
    def has_pet(self):
        return self.storage.load_pet() is not None

    def hatch(self, tz_offset_min=0):
        with self.lock:
            if self.storage.load_pet() is not None:
                raise ServiceError("зверёк уже есть", 409)
            if not -14 * 60 <= tz_offset_min <= 14 * 60:
                raise ServiceError("неверный часовой пояс")
            self.storage.set_setting("tz_offset_min", int(tz_offset_min))
            now = self.local_now()
            born = (now // STEP) * STEP + STEP
            pet = Pet.new(secrets.randbelow(10000), born_t=born)
            pet.flags["director_day"] = -1
            self._save(pet)
            return self.view(pet)

    def set_name(self, name):
        name = " ".join((name or "").split())[:24]
        if not name:
            raise ServiceError("имя пустое")
        with self.lock:
            pet = self._load()
            if pet.name != name:
                pet.name = name
                pet.name_progress = 0.0          # новое имя зверёк осваивает заново
            self._save(pet)
            return self.view(pet)

    # --- ночные и дневные задачи
    def _maintenance(self, pet, now, notify=False):
        """Время идёт лениво: пересчёт, затем то, что подошло по расписанию.

        notify=True только из фонового цикла: пинг имеет смысл, когда хозяин не в приложении.
        """
        moments = needs.advance(pet, now)
        if pet.mode != "normal":
            if notify:
                self._send_pings(pet, moments, now)
            return
        hod = (now % DAY) // HOUR
        day = now // DAY
        if hod >= 6 and pet.last_consolidation_day < pet.day_index:
            if self.night is not None:
                self.night.clock = self.clock
                self.night.consolidate(pet)
            else:
                consolidation.apply_plan(pet, consolidation.rule_based_plan(pet))
                consolidation.finish(pet)
        if hod >= 12 and pet.flags.get("director_day") != day:
            rng = self._rng(pet)
            brain = self._brain(rng)
            moment = events.run_director(pet, brain, rng)
            pet.flags["director_day"] = day
            if moment is not None:
                eid = moment.kind[6:]
                intro = (getattr(brain, "last_intro", None)
                         or f"{pet.name or 'Недоделок'} {EVENT_DESCRIPTIONS.get(eid, eid)}.")
                self.storage.add_speech(now, "event", moment.kind[6:], intro, "", "director")
                moment.data["intro"] = intro
                moments = list(moments) + [moment]
        if notify:
            self._send_pings(pet, moments, now)

    # --- пинги
    def _plan_pings(self, pet, moments, now):
        """Что можно сообщить хозяину, по убыванию срочности: (категория, текст, срочно)."""
        absent_h = (now - pet.last_owner_t) / HOUR
        who = pet.name or "Недоделок"
        plan = []
        for m in moments:
            if m.kind == "starving":
                plan.append(("need", "очень голоден", True))
            elif m.kind == "ill":
                plan.append(("health", "заболел: " + m.data.get("kind", ""), True))
            elif m.kind == "hungry" and absent_h >= 3:
                plan.append(("need", "хочет есть", False))
            elif m.kind == "hibernate":
                plan.append(("health", "ушёл в карантин лаборатории", False))
            elif m.kind.startswith("event:") and absent_h >= 2:
                plan.append(("event", m.data.get("intro") or who, False))
        if not plan and absent_h >= 8 and pet.mode == "normal" and not pet.sleeping:
            if pet.flags.get("miss_sent_for") != pet.last_owner_t:      # один раз за отсутствие
                plan.append(("miss", "скучает по лаборанту", False))
        plan.sort(key=lambda p: not p[2])
        return plan

    def _send_pings(self, pet, moments, now):
        if self.push is None or self.push.count_subs() == 0:
            return 0
        who = pet.name or "Недоделок"
        for category, body, urgent in self._plan_pings(pet, moments, now):
            res = self.push.notify(category, who, body, now, urgent=urgent)
            if res["sent"]:
                if category == "miss":
                    pet.flags["miss_sent_for"] = pet.last_owner_t
                return res["sent"]
        return 0

    def background_tick(self):
        """Вызывается фоновым циклом раз в минуту: время идёт и без открытого приложения."""
        with self.lock:
            pet = self.storage.load_pet()
            if pet is None:
                return False
            self._maintenance(pet, self.local_now(), notify=True)
            self._save(pet)
            return True

    # --- чтение
    def state(self):
        with self.lock:
            pet = self._load()
            self._maintenance(pet, self.local_now())
            self._save(pet)
            return self.view(pet)

    # --- действия хозяина
    def act(self, action, food=None, text=""):
        if action not in ACTIONS:
            raise ServiceError("неизвестное действие")
        if action == "feed" and food not in FOODS:
            raise ServiceError("неизвестная еда")
        text = (text or "")[:MAX_SAY_LEN]
        with self.lock:
            pet = self._load()
            now = self.local_now()
            self._maintenance(pet, now)
            rng = self._rng(pet)
            brain = self._brain(rng)
            before = len(getattr(brain, "reactions", []))
            kw = {"food": food} if action == "feed" else {"text": text} if action == "say" else {}
            if action == "say" and text.strip():
                self.storage.add_speech(now, "owner", "say", " ".join(text.split())[:MAX_SAY_LEN], "", "owner")
            result = actions.act(pet, now, action, brain, rng, **kw)
            speech = self._speech_for(pet, action, result, brain, before, rng, now)
            self._save(pet)
            return {"outcome": result.outcome, "speech": speech, "state": self.view(pet)}

    def _speech_for(self, pet, action, result, brain, before, rng, now):
        reactions = getattr(brain, "reactions", [])
        if len(reactions) > before:
            r = reactions[-1]
            speech = {"say": r.say, "tone": r.tone, "label": r.label, "source": r.source}
        elif action in ("feed", "play", "pet", "clean", "walk", "reconcile", "say") \
                and result.outcome not in ("unavailable", "unavailable_ill", "no_need"):
            say = fallback_say(pet.speech_stage, result.accepted, rng)
            speech = {"say": say, "tone": "", "label": "", "source": "engine"}
        else:
            return None
        self.storage.add_speech(now, "reaction", speech["label"] or action,
                                speech["say"], speech["tone"], speech["source"])
        return speech

    # --- вид для клиента
    def view(self, pet):
        n, rel = pet.needs, pet.rel
        sulk = pet.flags.get("sulk_until", 0) > pet.t
        tired_budget = bool(self.budget and self.budget.status(self.clock()) == "tired")
        mood = pet.mood
        mood_band = ("angry" if rel["grudge"] >= 50 else "happy" if mood >= 70
                     else "ok" if mood >= 45 else "sad")
        if pet.mode == "hibernating":
            pose = "hibernate"
        elif pet.sleeping:
            pose = "sleep"
        elif pet.illness:
            pose = "ill"
        elif sulk or mood_band == "angry":
            pose = "sulk"
        elif n["hunger"] >= 70:
            pose = "hungry"
        elif n["boredom"] >= 70:
            pose = "bored"
        elif mood_band == "happy":
            pose = "happy"
        else:
            pose = "idle"
        hod = (pet.t % DAY) // HOUR
        minute = (pet.t % HOUR) // 60
        return {
            "id": pet.number, "name": pet.name, "name_state": language.name_state(pet),
            "brain": self.provider,
            "day": pet.day_index,
            "stage": pet.speech_stage,
            "clock": {"hour": int(hod), "minute": int(minute)},
            # ключевые шкалы для главного экрана (решение владельца: видно всегда)
            "meters": {"hunger": round(n["hunger"]), "mood": round(pet.mood),
                       "tiredness": round(n["tiredness"]), "health": round(pet.health),
                       "boredom": round(n["boredom"])},
            "state": {
                "pose": pose, "mood": mood_band, "sleeping": pet.sleeping,
                "mode": pet.mode, "illness": pet.illness, "sulking": sulk,
                "tired_budget": tired_budget,
                "hunger": band(n["hunger"]), "tiredness": band(n["tiredness"]),
                "dirt": band(n["dirt"]), "boredom": band(n["boredom"]),
                "loneliness": band(n["loneliness"]), "walk": band(n["walk_need"]),
            },
            "form": {"mutations": sorted(pet.mutations),
                     "labels": {m: mutation_label(m) for m in pet.mutations},
                     "favorite": pet.favorite()},
            "items": [i["kind"] for i in pet.items if i.get("location") != "hidden"][-6:],
            "foods": list(FOODS),
            "can": self._can(pet),
            "speech": self.storage.recent_speech(8),
        }

    @staticmethod
    def _can(pet):
        n = pet.needs
        return {
            "feed": True, "pet": True, "clean": True, "say": True,
            "play": not pet.illness,
            "walk": not pet.illness and n["tiredness"] < 85,
            "sleep": n["tiredness"] >= 30 and not pet.sleeping,
            "heal": bool(pet.illness),
            "reconcile": pet.rel["grudge"] >= 10,
        }

    def dev_advance(self, hours):
        """Перемотка времени для проверки интерфейса (доступна только в режиме dev)."""
        with self.lock:
            self.time_shift += int(hours * HOUR)
            return self.state()

    # --- экраны
    def inspect(self):
        with self.lock:
            pet = self._load()
            self._maintenance(pet, self.local_now())
            self._save(pet)
            return {
                "needs": {k: round(v) for k, v in pet.needs.items()},
                "health": round(pet.health), "mood": round(pet.mood),
                "relationship": {k: round(v) for k, v in pet.rel.items()},
                "traits": {k: round(v) for k, v in pet.traits.items()},
                "care_points": round(pet.care_points),
                "counters": {k: round(v, 1) for k, v in pet.counters.items()},
            }

    def dossier(self):
        with self.lock:
            pet = self._load()
            return {
                "narrative": pet.memory.self_narrative,
                "facts": [f["statement"] for f in pet.memory.top_facts(20)],
                "memories": [{"day": m.day, "summary": m.summary, "emotion": m.emotion}
                             for m in sorted(pet.memory.memories, key=lambda m: -m.weight)[:12]],
                # только то, что уже отросло, без намёков на остальное
                "mutations": [{"day": day, "text": mutation_label(name)}
                              for name, day in sorted(pet.mutations.items(),
                                                      key=lambda kv: kv[1])],
            }

    def diary(self):
        with self.lock:
            pet = self._load()
            return {"diary": [{"day": d, "text": t} for d, t in pet.diary[-14:]],
                    "dreams": [{"day": d, "text": t} for d, t in pet.dreams[-14:]]}

    def get_settings(self):
        return {"tz_offset_min": int(self.storage.get_setting("tz_offset_min", "0")),
                "quiet_start": int(self.storage.get_setting("quiet_start", "0")),
                "quiet_end": int(self.storage.get_setting("quiet_end", "11")),
                "brain": self.provider,
                "pings_per_day": int(self.storage.get_setting("pings_per_day", "4")),
                "push": {"configured": self.push is not None,
                         "subscriptions": self.push.count_subs() if self.push else 0}}

    def set_settings(self, quiet_start=None, quiet_end=None, pings_per_day=None):
        if pings_per_day is not None:
            if not 1 <= int(pings_per_day) <= 12:
                raise ServiceError("пингов в сутки от 1 до 12")
            self.storage.set_setting("pings_per_day", int(pings_per_day))
        for key, value in (("quiet_start", quiet_start), ("quiet_end", quiet_end)):
            if value is not None:
                if not 0 <= int(value) <= 23:
                    raise ServiceError("час от 0 до 23")
                self.storage.set_setting(key, int(value))
        return self.get_settings()
