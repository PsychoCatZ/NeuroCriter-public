"""Режиссёр: каталог событий с условиями. Раз в сутки мозг выбирает одно.

В M0 реализованы 10 из 20 событий каталога (см. КАТАЛОГИ-v0.md). Условия и
эффекты считает движок, мозг выбирает из подходящих.
"""
from .constants import DAY, HEALTH_FLOOR, HOUR
from . import journal
from .model import Moment, clamp

COOLDOWN = 3 * DAY


def _recent(pet, key, window):
    return pet.t - pet.flags.get(key, -10 ** 9) <= window


# каждое событие: id -> (функция веса, функция эффекта)

def w_stash(pet):
    return (pet.traits["greed"] / 100) * 1.0 if pet.rel["trust"] >= 35 else 0.0


def e_stash(pet, rng):
    pet.items.append({"kind": "заначка", "origin": "stash", "t": pet.t,
                      "location": "hidden"})
    pet.bump("stashes")


def w_report(pet):
    complaints = max(pet.needs["hunger"], pet.needs["dirt"], pet.needs["boredom"])
    return 0.6 * (complaints / 100) + 0.4 * pet.rel["grudge"] / 100


def e_report(pet, rng):
    pet.shift_mood(+ 2)
    pet.rel["respect"] = clamp(pet.rel["respect"] + 0.3)
    pet.bump("reports")


def w_find(pet):
    return 0.8 if _recent(pet, "walk_t", DAY) else 0.0


def e_find(pet, rng):
    from .actions import add_find
    add_find(pet, rng)


def w_gift(pet):
    ok = pet.rel["attachment"] > 50 and pet.rel["trust"] > 50
    return 0.9 if ok else 0.0


def e_gift(pet, rng):
    pet.items.append({"kind": "подарок хозяину", "origin": "gift", "t": pet.t,
                      "location": "room"})
    pet.rel["attachment"] = clamp(pet.rel["attachment"] + 1.0)
    pet.bump("gifts")


def w_sulk(pet):
    return 1.2 * pet.rel["grudge"] / 100 if pet.rel["grudge"] > 40 else 0.0


def e_sulk(pet, rng):
    pet.flags["sulk_until"] = pet.t + 6 * HOUR
    pet.bump("sulks")


def w_sneeze(pet):
    return 0.5 if pet.needs["dirt"] > 60 and pet.illness is None else 0.0


def e_sneeze(pet, rng):
    pet.health = clamp(pet.health - 8, HEALTH_FLOOR, 100.0)
    pet.bump("sneezes")


def w_craving(pet):
    return 0.6 if pet.favorite() else 0.0


def e_craving(pet, rng):
    pet.flags["craving"] = (pet.favorite(), pet.t + DAY)
    pet.bump("cravings")


def w_achievement(pet):
    return 0.5 if pet.care_points >= 20 else 0.0


def e_achievement(pet, rng):
    pet.shift_mood(+ 5)
    pet.rel["respect"] = clamp(pet.rel["respect"] + 1.0)
    pet.bump("achievements")


def w_insomnia(pet):
    ok = pet.needs["boredom"] > 60 and pet.traits["anxiety"] > 50
    return 0.7 if ok else 0.0


def e_insomnia(pet, rng):
    pet.flags["insomnia_until"] = pet.t + 8 * HOUR
    pet.bump("insomnias")


def w_anniversary(pet):
    return 2.0 if pet.day_index in (7, 14, 21, 30, 60, 100) else 0.0


def e_anniversary(pet, rng):
    pet.shift_mood(+ 5)
    pet.rel["attachment"] = clamp(pet.rel["attachment"] + 0.5)
    pet.bump("anniversaries")


EVENTS = {
    "stash": (w_stash, e_stash), "report": (w_report, e_report),
    "find": (w_find, e_find), "gift": (w_gift, e_gift),
    "sulk_silence": (w_sulk, e_sulk), "sneeze": (w_sneeze, e_sneeze),
    "craving": (w_craving, e_craving), "achievement": (w_achievement, e_achievement),
    "insomnia": (w_insomnia, e_insomnia), "anniversary": (w_anniversary, e_anniversary),
}


def candidates(pet):
    out = []
    for eid, (weight_fn, _) in EVENTS.items():
        if _recent(pet, "ev_" + eid, COOLDOWN):
            continue
        w = weight_fn(pet)
        if w > 0:
            out.append((eid, w))
    return out


def run_director(pet, brain, rng):
    """Выбрать и применить событие дня. Возвращает Moment или None."""
    options = candidates(pet)
    if not options:
        return None
    eid = brain.choose("director", options, {"pet": pet})
    EVENTS[eid][1](pet, rng)
    pet.flags["ev_" + eid] = pet.t
    m = Moment("event:" + eid, pet.t, {"intro": getattr(brain, "last_intro", None)})
    journal.moment(pet, m)
    return m
