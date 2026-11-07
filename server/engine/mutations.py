"""Мутации вида от стиля ухода. Каждая срабатывает один раз.

Отложено (нужен календарь): «воротник» для зимы.
"""
from .model import Moment

MUTATION_LABELS = {
    "big_ear": "ухо-антенна выросла",
    "anxious_eye": "появился тревожный глаз",
    "extra_leg": "две лишние лапки",
    "stamp_accepted": "штамп «ПРИНЯТО»",
    "round_belly": "круглое брюшко",
    "find_jewelry": "носит находку как украшение",
    "patch": "заплатка после болезни",
    "tail_fluffy": "пушистый спокойный хвост",
    "tail_spiky": "колючий нервный хвост",
}


def label(name):
    if name.startswith("color_"):
        return f"окраска под любимую еду ({name[6:]})"
    return MUTATION_LABELS.get(name, name)


def check(pet, out):
    c = pet.counters

    def add(name):
        if name not in pet.mutations:
            pet.mutations[name] = pet.day_index
            out.append(Moment("mutation", pet.t, {"name": name}))

    if c.get("affection", 0) >= 60:
        add("big_ear")
    if c.get("lonely_h", 0) >= 120:
        add("anxious_eye")
    fav = pet.favorite()
    if fav and pet.prefs[fav][1] >= 15 and pet.prefs[fav][0] > 0.3:
        add("color_" + fav)
    if pet.day_index >= 14 and not any(m.startswith("tail_") for m in pet.mutations):
        avg = sum(pet.mood_days) / len(pet.mood_days) if pet.mood_days else 50
        if avg >= 60:
            add("tail_fluffy")
        elif avg <= 45:
            add("tail_spiky")
    if c.get("walks", 0) >= 15:
        add("extra_leg")
    if c.get("trust_streak", 0) >= 7:
        add("stamp_accepted")
    if c.get("overfeed", 0) >= 12:
        add("round_belly")
    if c.get("finds", 0) >= 5:
        add("find_jewelry")
    if c.get("recovered", 0) >= 1:
        add("patch")
