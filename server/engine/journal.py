"""Журнал значимых событий за сутки: сырьё для ночной консолидации.

В запросы к Claude журнал целиком не идёт, только сжатая выжимка (digest).
"""
from .constants import DAY, HOUR

SALIENCE = {
    "starving": 0.8, "hungry": 0.3, "ill": 0.7, "recovered": 0.5, "hibernate": 0.9,
    "return_from_quarantine": 0.8, "woken_by_owner": 0.5, "mutation": 0.6,
}
TAGS = {
    "starving": ["еда", "голод", "обида"], "hungry": ["еда", "голод"],
    "ill": ["болезнь"], "recovered": ["болезнь"],
    "hibernate": ["пропал", "одиночество"],
    "return_from_quarantine": ["пропал", "возвращение", "хозяин"],
    "woken_by_owner": ["сон", "ночь", "хозяин"], "mutation": ["мутация"],
}
ACTION_TAGS = {
    "play": ["игра"], "pet": ["ласка"], "feed": ["еда"], "clean": ["мытьё"],
    "walk": ["прогулка"], "reconcile": ["обида", "прощение"], "heal": ["болезнь"],
}
KIND_TEXT = {
    "starving": "сильно голодал", "hungry": "был голоден", "ill": "заболел",
    "recovered": "выздоровел", "hibernate": "хозяин пропал, зверька забрали в карантин",
    "return_from_quarantine": "хозяин вернулся после карантина",
    "woken_by_owner": "хозяин разбудил", "mutation": "изменилось тело",
}
ACTION_TEXT = {
    "play": "играли", "pet": "чесали", "feed": "кормили", "clean": "мыли",
    "walk": "гуляли", "reconcile": "мирились", "heal": "лечили", "say": "говорили",
}


EVENT_DESCRIPTIONS = {
    "stash": "прячет предмет в комнате",
    "report": "пишет докладную с жалобой",
    "find": "приносит находку с прогулки",
    "gift": "приносит хозяину подарок",
    "sulk_silence": "сидит спиной и молчит, обижен",
    "sneeze": "чихает, начинается простуда",
    "craving": "требует одно конкретное блюдо",
    "achievement": "гордится новым умением",
    "insomnia": "не спит ночью, ходит по гнезду",
    "anniversary": "годовщина: столько-то дней вместе",
}


def _log(pet, kind, salience, tags, data=None):
    pet.event_log.append({"t": pet.t, "day": pet.day_index, "kind": kind,
                          "salience": salience, "tags": list(tags),
                          "data": data or {}})


def moment(pet, m):
    """Записать момент движка, если он значим."""
    if m.kind.startswith("event:"):
        _log(pet, m.kind, 0.5, ["событие"], m.data)
    elif m.kind in SALIENCE:
        _log(pet, m.kind, SALIENCE[m.kind], TAGS.get(m.kind, []), m.data)


def owner_action(pet, action, outcome, food=None):
    tags = list(ACTION_TAGS.get(action, []))
    if food and action == "feed":
        tags.append(food)
    if outcome.startswith("refuse"):
        _log(pet, f"refused:{action}", 0.4, tags + ["обида"], {"outcome": outcome})
    elif action == "reconcile" and outcome == "accept":
        _log(pet, "reconciled", 0.7, tags)
    elif outcome == "accept":
        _log(pet, f"done:{action}", 0.1, tags)
    # недоступные действия и «не нужно» не пишем


def digest(pet, limit=25):
    """Выжимка журнала строками для консолидации. Мелкие дела сжимаются в счётчики."""
    big, counts = [], {}
    for e in pet.event_log:
        if e["salience"] >= 0.3:
            big.append(e)
        elif e["kind"].startswith("done:"):
            a = e["kind"].split(":", 1)[1]
            counts[a] = counts.get(a, 0) + 1
    big.sort(key=lambda e: (-e["salience"], e["t"]))
    big = sorted(big[:limit], key=lambda e: e["t"])
    lines = []
    for e in big:
        hh = (e["t"] % DAY) // HOUR
        what = _describe(e)
        lines.append(f"день {e['day']}, {hh:02d}:00: {what}")
    if counts:
        lines.append("обычные дела: " + ", ".join(
            f"{ACTION_TEXT.get(a, a)} {n:.0f}×" for a, n in sorted(counts.items())))
    return lines


def _describe(e):
    k = e["kind"]
    if k.startswith("event:"):
        # художественное вступление режиссёра в память не идёт: оно выдумывает детали
        eid = k[6:]
        return f"событие «{eid}»: {EVENT_DESCRIPTIONS.get(eid, eid)}"
    if k.startswith("refused:"):
        return "отказался: " + ACTION_TEXT.get(k.split(":", 1)[1], k)
    if k == "reconciled":
        return "помирились"
    return KIND_TEXT.get(k, k)


def clear(pet, keep_last=0):
    pet.event_log = pet.event_log[-keep_last:] if keep_last else []
