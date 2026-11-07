"""Дрейф черт от опыта. Вызывается раз в сутки по счётчикам этих суток."""
from .constants import TRAITS, TRAIT_DRIFT_LIMIT
from .model import clamp


def _nudge(pet, trait, delta):
    v = pet.traits[trait]
    # у края шкалы двигаться труднее: дрейф зависит от самого зверька
    room = (100 - v) if delta > 0 else v
    delta *= clamp(room / 50, 0.2, 1.6)
    base = pet.base_traits[trait]
    lo = max(0.0, base - TRAIT_DRIFT_LIMIT)
    hi = min(100.0, base + TRAIT_DRIFT_LIMIT)
    pet.traits[trait] = clamp(pet.traits[trait] + delta, lo, hi)


nudge = _nudge


def drift(pet):
    d = pet.today
    rel = pet.rel
    neglect = d.get("neglect_h", 0.0)
    hungry_h = d.get("hungry_h", 0.0)
    cuddles = d.get("affection", 0.0)

    # тревожность
    if neglect >= 6:
        _nudge(pet, "anxiety", +0.8)
    if d.get("woken", 0) >= 1:
        _nudge(pet, "anxiety", +0.4)
    if neglect == 0 and cuddles >= 1:
        _nudge(pet, "anxiety", -0.4)

    # привязчивость
    if cuddles >= 2:
        _nudge(pet, "clinginess", +0.5)
    if (pet.t - pet.last_owner_t) > 30 * 3600:
        _nudge(pet, "clinginess", -0.6)

    # любопытство и лень
    if d.get("walks", 0) >= 1:
        _nudge(pet, "curiosity", +0.5)
        _nudge(pet, "laziness", -0.4)
    else:
        if d.get("bored_h", 0.0) >= 8:
            _nudge(pet, "curiosity", -0.3)
        if d.get("played", 0) == 0:
            _nudge(pet, "laziness", +0.4)

    # злопамятность
    if rel["grudge"] > 50:
        _nudge(pet, "vengefulness", +0.6)
    elif rel["grudge"] < 10:
        _nudge(pet, "vengefulness", -0.3)

    # жадность
    if hungry_h >= 4:
        _nudge(pet, "greed", +0.6)
    elif d.get("fed", 0) >= 3 and hungry_h < 2:
        _nudge(pet, "greed", -0.3)

    # наглость: растёт от потакания и от злости, спадает от спокойной уверенности
    if d.get("indulged", 0) >= 1:
        _nudge(pet, "insolence", +0.7)
    if d.get("overfeed", 0) >= 1:
        _nudge(pet, "insolence", +0.4)
    if rel["grudge"] > 50:
        _nudge(pet, "insolence", +0.6)
    elif rel["trust"] >= 65 and rel["grudge"] < 10 and d.get("refusals", 0) == 0:
        _nudge(pet, "insolence", -0.4)
