"""Действия хозяина и их разрешение.

Движок считает готовность зверька и формирует исходы с весами. Мозг выбирает
один исход из списка. Эффекты применяет движок по таблице. Отказ ограничен
бюджетом упрямства, а острые физиологические нужды перекрывают каприз.
"""
from dataclasses import dataclass, field

from . import journal, language, needs as needs_mod
from .constants import (FIND_KINDS, FOODS, HEALTH_FLOOR, HOUR, REFUSAL_BUDGET,
                        WILL_BASE)
from .model import Moment, clamp


@dataclass
class Result:
    outcome: str
    moments: list = field(default_factory=list)

    @property
    def accepted(self):
        return self.outcome == "accept"


def willingness(pet, kind):
    tr, nd, rel = pet.traits, pet.needs, pet.rel
    w = (WILL_BASE - 0.35 * rel["grudge"] / 100 - 0.30 * nd["tiredness"] / 100
         - 0.25 * nd["hunger"] / 100 + 0.20 * (tr["curiosity"] - 50) / 100
         - 0.20 * (tr["laziness"] - 50) / 100
         + 0.15 * (rel["attachment"] - 50) / 100)
    if kind == "clean":
        w -= 0.2 + 0.1 * (tr["insolence"] - 50) / 100
    if pet.flags.get("sulk_until", 0) > pet.t:
        w -= 0.25
    return clamp(w, 0.05, 0.97)


def _ask(pet, brain, kind, w, forced_accept=False, extra=None):
    """Выбор исхода. Возвращает accept / refuse_sulk / refuse_lazy."""
    nd, rel, tr = pet.needs, pet.rel, pet.traits
    if forced_accept or (pet.refusals_today >= REFUSAL_BUDGET and nd["tiredness"] < 90):
        # исход решён движком, но озвучить его должен мозг, а не заготовка
        return brain.choose("owner_action:" + kind, [("accept", 1.0)],
                            {"pet": pet, **(extra or {})})
    pet.bump("asked")
    sulk = 0.3 + rel["grudge"] / 100
    lazy = 0.3 + tr["laziness"] / 100 + nd["tiredness"] / 100
    rest = 1 - w                  # P(принять) = w, остаток делится между отказами
    options = [
        ("accept", w),
        ("refuse_sulk", rest * sulk / (sulk + lazy)),
        ("refuse_lazy", rest * lazy / (sulk + lazy)),
    ]
    return brain.choose("owner_action:" + kind, options, {"pet": pet, **(extra or {})})


def _refuse(pet, outcome):
    pet.refusals_today += 1
    pet.bump("refusals")
    pet.flags["refused_t"] = pet.t
    pet.shift_mood(- 1)
    if outcome == "refuse_sulk":
        pet.rel["grudge"] = clamp(pet.rel["grudge"] + 0.5)


def _eat(pet, food, hunger_before, craving_bonus):
    delight = pet.taste[food] * (0.5 + hunger_before / 100) + craving_bonus
    old = pet.prefs.get(food)
    if old is None:
        pet.prefs[food] = [delight, 1]
    else:
        pet.prefs[food] = [old[0] * 0.8 + delight * 0.2, old[1] + 1]
    return delight


def act(pet, now, action, brain, rng, **kw):
    """Выполнить действие хозяина в момент now."""
    moments = needs_mod.advance(pet, now)

    if pet.mode == "hibernating":
        pet.mode = "normal"
        pet.rel["trust"] = clamp(pet.rel["trust"] - 2)
        pet.add_care(-0.1 * pet.care_points)
        pet.mood = 50.0
        moments.append(Moment("return_from_quarantine", now))

    pet.flags["absent_h"] = (now - pet.last_owner_t) / HOUR
    pet.last_owner_t = now
    if action != "sleep" and pet.sleeping:
        pet.sleeping = False
        pet.shift_mood(- 8)
        pet.rel["grudge"] = clamp(pet.rel["grudge"] + 1.5)
        pet.bump("woken")
        moments.append(Moment("woken_by_owner", now))

    handler = _HANDLERS[action]
    outcome = handler(pet, brain, rng, **kw)
    moments.append(Moment("owner_action", now, {"action": action, "outcome": outcome}))
    for m in moments[-3:]:
        if m.kind in ("woken_by_owner", "return_from_quarantine"):
            journal.moment(pet, m)
    journal.owner_action(pet, action, outcome, kw.get("food"))
    return Result(outcome, moments)


# ---------------------------------------------------------------- обработчики

def _feed(pet, brain, rng, food=None):
    nd, rel = pet.needs, pet.rel
    food = food or FOODS[0]
    hb = nd["hunger"]
    if hb >= 60:
        outcome = "accept"
    else:
        p = clamp(0.25 + hb / 100 + 0.4 * pet.taste[food], 0.05, 0.95)
        craving = pet.flags.get("craving")
        if craving and craving[1] > pet.t and craving[0] != food:
            p *= 0.6
        outcome = _ask(pet, brain, "feed", p, extra={"food": food})
    if outcome != "accept":
        _refuse(pet, outcome)
        return outcome
    craving = pet.flags.get("craving")
    bonus = 0.4 if craving and craving[1] > pet.t and craving[0] == food else 0.0
    delight = _eat(pet, food, hb, bonus)
    nd["hunger"] = clamp(nd["hunger"] - 45)
    nd["dirt"] = clamp(nd["dirt"] + 2)
    rel["trust"] = clamp(rel["trust"] + (0.8 if hb >= 50 else 0.1))
    rel["attachment"] = clamp(rel["attachment"] + (0.5 if hb >= 50 else 0.0))
    pet.shift_mood(+ 5 * delight)
    pet.bump("fed")
    if hb < 30:
        pet.bump("overfeed")
    pet.add_care(1.0 if hb >= 40 else 0.2)
    if pet.t - pet.flags.get("refused_t", -10 ** 9) <= HOUR:
        pet.bump("indulged")
    return "accept"


def _play(pet, brain, rng):
    if pet.illness:
        return "unavailable_ill"
    outcome = _ask(pet, brain, "play", willingness(pet, "play"))
    if outcome != "accept":
        _refuse(pet, outcome)
        return outcome
    nd, rel = pet.needs, pet.rel
    nd["boredom"] = clamp(nd["boredom"] - 35)
    nd["tiredness"] = clamp(nd["tiredness"] + 6)
    nd["hunger"] = clamp(nd["hunger"] + 4)
    rel["attachment"] = clamp(rel["attachment"] + 1.2)
    rel["trust"] = clamp(rel["trust"] + 0.3)
    pet.shift_mood(+ 6)
    pet.bump("played")
    pet.bump("affection")
    pet.add_care(2.0)
    return "accept"


def _cuddle(pet, brain, rng):
    outcome = _ask(pet, brain, "pet", willingness(pet, "pet") + 0.1)
    if outcome != "accept":
        _refuse(pet, outcome)
        return outcome
    nd, rel = pet.needs, pet.rel
    nd["loneliness"] = clamp(nd["loneliness"] - 25)
    rel["attachment"] = clamp(rel["attachment"] + 0.8)
    pet.shift_mood(+ 3)
    pet.bump("cuddles")
    pet.bump("affection")
    pet.add_care(1.0)
    return "accept"


def _clean(pet, brain, rng):
    outcome = _ask(pet, brain, "clean", willingness(pet, "clean"),
                   forced_accept=pet.needs["dirt"] >= 85)
    if outcome != "accept":
        _refuse(pet, outcome)
        return outcome
    pet.needs["dirt"] = 0.0
    pet.health = clamp(pet.health + 3, HEALTH_FLOOR, 100.0)
    pet.shift_mood(- 2)
    pet.rel["trust"] = clamp(pet.rel["trust"] - 0.2)
    pet.bump("cleaned")
    pet.add_care(1.0)
    return "accept"


def _walk(pet, brain, rng):
    if pet.illness or pet.needs["tiredness"] >= 85:
        return "unavailable"
    outcome = _ask(pet, brain, "walk", willingness(pet, "walk"))
    if outcome != "accept":
        _refuse(pet, outcome)
        return outcome
    nd = pet.needs
    nd["walk_need"] = 0.0
    nd["boredom"] = clamp(nd["boredom"] - 30)
    nd["dirt"] = clamp(nd["dirt"] + 25)
    nd["tiredness"] = clamp(nd["tiredness"] + 12)
    nd["hunger"] = clamp(nd["hunger"] + 10)
    pet.shift_mood(+ 5)
    pet.bump("walks")
    pet.flags["walk_t"] = pet.t
    pet.add_care(3.0)
    if rng.chance(0.35):
        add_find(pet, rng)
    return "accept"


def add_find(pet, rng):
    pet.items.append({"kind": rng.choice(FIND_KINDS), "origin": "find",
                      "t": pet.t, "location": "room"})
    pet.bump("finds")


def _sleep(pet, brain, rng):
    if pet.sleeping:
        return "already"
    if pet.needs["tiredness"] < 30:
        return "not_tired"
    pet.sleeping = True
    pet.sleep_started_t = pet.t
    pet.add_care(0.5)
    return "accept"


def _heal(pet, brain, rng):
    if not pet.illness:
        return "not_ill"
    pet.health = clamp(pet.health + 20, HEALTH_FLOOR, 100.0)
    pet.rel["trust"] = clamp(pet.rel["trust"] + 0.5)
    pet.shift_mood(- 2)
    pet.add_care(1.5)
    pet.bump("healed")
    return "accept"


def _reconcile(pet, brain, rng, food=None):
    rel, tr = pet.rel, pet.traits
    if rel["grudge"] < 10:
        return "no_need"
    p = clamp(0.8 - 0.4 * tr["vengefulness"] / 100, 0.1, 0.95)
    outcome = brain.choose("reconcile", [("accept", p), ("refuse_sulk", 1 - p)],
                           {"pet": pet})
    if outcome != "accept":
        pet.flags["sulk_until"] = pet.t + 3 * HOUR
        return outcome
    cut = 12 + 8 * rel["attachment"] / 100
    if food and pet.taste.get(food, 0) > 0.3:
        cut += 6
    rel["grudge"] = clamp(rel["grudge"] - cut)
    rel["trust"] = clamp(rel["trust"] + 1.0)
    pet.shift_mood(+ 4)
    pet.bump("reconciled")
    pet.add_care(2.0)
    return "accept"


def _say(pet, brain, rng, text=""):
    """Хозяин говорит. Меню ответа даёт движок, выбирает и озвучивает мозг."""
    nd, rel = pet.needs, pet.rel
    sulking = pet.flags.get("sulk_until", 0) > pet.t
    options = [
        ("accept", 3.0),
        ("demand", 2.0 if nd["hunger"] >= 70 else 0.0),
        ("snub", 2.0 if (rel["grudge"] >= 40 or sulking) else 0.3),
    ]
    outcome = brain.choose("owner_say", [o for o in options if o[1] > 0],
                           {"pet": pet, "text": text})
    # слова запоминаются после выбора: понимание считалось по прежним знаниям
    for word in language.words_of(text):
        pet.lexicon[word] = pet.lexicon.get(word, 0) + 1
    if language.mentions_name(pet, text):
        pet.name_progress = min(1.0, pet.name_progress + language.NAME_GAIN_PER_CALL)
        pet.bump("name_calls")
    pet.bump("said")
    nd["loneliness"] = clamp(nd["loneliness"] - 10)
    rel["attachment"] = clamp(rel["attachment"] + 0.1)
    if outcome == "accept":
        nd["loneliness"] = clamp(nd["loneliness"] - 2)
        rel["attachment"] = clamp(rel["attachment"] + 0.15)
        pet.shift_mood(+1)
        pet.bump("affection", 0.5)
        pet.add_care(0.3)
    elif outcome == "snub":
        pet.shift_mood(-1)
    return outcome


_HANDLERS = {
    "feed": _feed, "play": _play, "pet": _cuddle, "clean": _clean,
    "walk": _walk, "sleep": _sleep, "heal": _heal,
    "reconcile": _reconcile, "say": _say,
}
