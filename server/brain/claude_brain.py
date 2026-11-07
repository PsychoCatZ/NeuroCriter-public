"""ClaudeBrain: выбирает исход из меню движка и озвучивает его.

Порядок: ворота (бюджет) -> сборка запроса -> вызов (запись/воспроизведение)
-> разбор -> проверка -> при нарушении один повтор -> запасной ответ.
Совместим с engine.brain.Brain: choose() возвращает ключ исхода, а реплика
и тон остаются в self.last и в журнале self.reactions.
"""
import time
from collections import deque
from dataclasses import dataclass

from engine.brain import StubBrain
from engine.constants import HOUR

from .budget import Budget
from .client import MODEL_VOICE, TransportError, cost_usd
from .fallback import fallback_say
from .parse import parse_three_lines
from engine import language

from .prompts import (ACTION_LABELS, EVENT_TEXT, FORMS, IMPULSES, SYSTEM,
                      build_user, context_tags, say_lines)
from .recorder import REPLAY, CallLog, ReplayMiss
from .validate import only_length_problems, trim, validate

EST_COST = 0.0005  # грубая оценка стоимости одного вызова для ворот


@dataclass
class Reaction:
    key: str               # исход движка (accept, refuse_sulk, ...)
    label: str             # название действия для Claude
    say: str
    tone: str
    source: str            # claude | replay | fallback:<причина>
    attempts: int = 0
    stage: int = 0
    cost: float = 0.0
    problems: tuple = ()
    memory: str = ""


class ClaudeBrain:
    def __init__(self, transport, calllog, budget, rng, clock=time.time,
                 model=MODEL_VOICE, max_attempts=2, night=None, memory_chance=0.4,
                 name_chance=0.3):
        self.night = night
        self.name_chance = name_chance
        self.memory_chance = memory_chance
        self.last_intro = None
        self.director_log = []     # (день, событие, вступление)
        self.transport = transport
        self.calllog = calllog
        self.budget = budget
        self.rng = rng
        self.clock = clock
        self.model = model
        self.max_attempts = max_attempts
        self.stub = StubBrain(rng)
        self.history = deque(maxlen=3)
        self.reactions = []
        self.last = None

    # --- интерфейс движка
    def choose(self, kind, options, ctx=None):
        if kind == "director" and self.night and ctx and "pet" in ctx:
            eid = self.night.choose_event(ctx["pet"], options)
            self.last_intro = self.night.last_intro if eid else None
            if eid:
                self.director_log.append((ctx["pet"].day_index, eid, self.last_intro))
                return eid
            return self.stub.choose(kind, options, ctx)
        base = self._base_kind(kind)
        if ctx is None or "pet" not in ctx or base is None:
            return self.stub.choose(kind, options, ctx)
        reaction = self._react(base, options, ctx)
        reaction.stage = ctx["pet"].speech_stage
        self.last = reaction
        self.reactions.append(reaction)
        return reaction.key

    @staticmethod
    def _base_kind(kind):
        if kind == "reconcile":
            return "reconcile"
        if kind == "owner_say":
            return "say"
        if kind.startswith("owner_action:"):
            base = kind.split(":", 1)[1]
            return base if base in EVENT_TEXT else None
        return None

    # --- основная логика
    def _react(self, base, options, ctx):
        pet = ctx["pet"]
        by_label = {}
        menu = []
        for key, weight in options:
            label = ACTION_LABELS.get((base, key))
            if label is None or weight <= 0:
                continue
            by_label[label] = key
            menu.append((label, weight))
        if not menu:
            key = self.stub.choose(base, options, ctx)
            return Reaction(key, "", "", "", "fallback:no_menu")

        event = EVENT_TEXT[base].format(food=ctx.get("food", "еда"))
        impulse = self.rng.choice(IMPULSES)
        forms = FORMS.get(pet.speech_stage)
        form = self.rng.choice(forms) if forms else None
        absent_h = pet.flags.get("absent_h", 0.0)
        now = self.clock()
        extra = say_lines(pet, ctx.get("text", "")) if base == "say" else []
        forbid = (pet.name,) if pet.name and language.name_state(pet) != "known" else ()
        echo = base == "say" and language.mentions_name(pet, ctx.get("text", ""))
        use_name = (language.name_state(pet) == "known" and pet.speech_stage >= 2
                    and self.rng.chance(self.name_chance))
        mem = None
        if pet.speech_stage >= 2 and pet.memory.memories:
            cand = pet.memory.retrieve(context_tags(base, pet, absent_h), pet.t)
            if cand is not None and self.rng.chance(self.memory_chance):
                mem = cand
                pet.memory.mark_used(cand, pet.t)

        if self.calllog.mode != REPLAY and not self.budget.allow(now, EST_COST):
            return self._fallback(pet, options, "tired")

        hint = None
        problems = ()
        spent = 0.0
        for attempt in range(1, self.max_attempts + 1):
            user = build_user(pet, base, menu, event, impulse,
                              mem.summary if mem else None,
                              list(self.history), absent_h, hint, form, extra, use_name)
            key = self.calllog.key(self.model, SYSTEM, user)
            source = getattr(self.transport, "provider", "claude")
            rec = self.calllog.get(key)
            if rec is not None and self.calllog.mode == REPLAY:
                text, source = rec["text"], "replay"
            elif self.calllog.mode == REPLAY:
                raise ReplayMiss(f"нет записи для запроса {key[:12]}")
            else:
                estimate = (self.transport.estimate_cost(SYSTEM, user)
                            if hasattr(self.transport, "estimate_cost") else EST_COST)
                if not self.budget.allow(self.clock(), estimate):
                    return self._fallback(pet, options, "tired")
                try:
                    comp = self.transport.complete(SYSTEM, user)
                except TransportError:
                    return self._fallback(pet, options, "api")
                cost = cost_usd(self.model, comp.in_tokens, comp.out_tokens)
                spent += cost
                self.budget.record(now, cost)
                self.calllog.put(key, base, self.model, user, comp.text,
                                 comp.in_tokens, comp.out_tokens, cost, "ok")
                text = comp.text
            parsed = parse_three_lines(text)
            problems = tuple(validate(parsed, set(by_label), pet.speech_stage, forbid, echo))
            if only_length_problems(problems):
                # длина единственная беда: режем по границе предложения и оставляем голос Claude
                cut = dict(parsed, say=trim(parsed["say"], pet.speech_stage))
                if not validate(cut, set(by_label), pet.speech_stage, forbid, echo):
                    parsed, problems = cut, ()
                    source += ":trimmed"
            if not problems:
                self.history.append(parsed["say"])
                return Reaction(by_label[parsed["action"]], parsed["action"],
                                parsed["say"], parsed["tone"], source,
                                attempts=attempt, cost=spent,
                                memory=mem.summary if mem else "")
            hint = "; ".join(problems)
        r = self._fallback(pet, options, "invalid")
        r.attempts, r.cost, r.problems = self.max_attempts, spent, problems
        return r

    def _fallback(self, pet, options, why):
        key = self.stub.choose("fallback", options)
        say = fallback_say(pet.speech_stage, key == "accept", self.rng)
        self.history.append(say)
        return Reaction(key, "", say, "нейтрально", "fallback:" + why)
