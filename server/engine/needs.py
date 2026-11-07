"""Ленивый пересчёт времени.

advance(pet, now) двигает состояние от pet.t к now шагами по сетке STEP
(выровненной по абсолютному времени), поэтому advance(a->c) совпадает с
advance(a->b) + advance(b->c) бит в бит. Фоновых тиков нет: функция вызывается
только когда состояние нужно (действие хозяина, вызов Claude, пуш).
"""
from . import journal, lifecycle
from .constants import (AWAKE_RATES, DAY, GRUDGE_CAP, GRUDGE_GAIN, HEALTH_FLOOR,
                        HIBERNATE_AFTER, HOUR, HUNGER_HEALTH_DAMAGE, MOOD_FLOOR,
                        MOOD_NEED_WEIGHT, NEEDS, SLEEP_MAX, SLEEP_RATES, STEP,
                        TRUST_FLOOR, TRUST_LOSS_HUNGRY)
from .model import Moment, clamp


def _rates(pet):
    r = dict(SLEEP_RATES if pet.sleeping else AWAKE_RATES)
    if not pet.sleeping:
        tr = pet.traits
        r["hunger"] *= 1 + 0.4 * (tr["greed"] - 50) / 100
        r["boredom"] *= 1 + 0.4 * (tr["curiosity"] - 50) / 100
        r["loneliness"] *= 1 + 0.5 * (tr["clinginess"] - 50) / 100
        r["tiredness"] *= 1 + 0.3 * (tr["laziness"] - 50) / 100
        if pet.illness:
            r["tiredness"] *= 1.5
    return r


def _mood_target(pet):
    n, rel = pet.needs, pet.rel
    needs_part = (0.30 * n["hunger"] + 0.15 * n["tiredness"] + 0.15 * n["dirt"]
                  + 0.20 * n["boredom"] + 0.15 * n["loneliness"])
    target = (85 - MOOD_NEED_WEIGHT * needs_part
              + 0.15 * rel["attachment"] - 0.20 * rel["grudge"])
    if pet.illness:
        target -= 15
    return clamp(target)


def _flag_threshold(pet, flag, value, up, down, out, t):
    if not pet.flags.get(flag) and value >= up:
        pet.flags[flag] = True
        out.append(Moment(flag, t))
    elif pet.flags.get(flag) and value < down:
        pet.flags[flag] = False


def _step(pet, dt, out):
    t_end = pet.grid_t + dt
    h = dt / HOUR

    if pet.mode == "normal" and t_end - pet.last_owner_t >= HIBERNATE_AFTER:
        pet.mode = "hibernating"
        pet.sleeping = False
        for k in NEEDS:
            pet.needs[k] = min(pet.needs[k], 80.0)
        pet.bump("hibernations")
        out.append(Moment("hibernate", t_end))
    if pet.mode == "hibernating":
        return

    nd, rel, tr = pet.needs, pet.rel, pet.traits

    rates = _rates(pet)
    for k in NEEDS:
        nd[k] = clamp(nd[k] + rates[k] * h)

    # здоровье: смерти нет, есть пол
    dh = 0.0
    if nd["hunger"] > 85:
        dh -= HUNGER_HEALTH_DAMAGE
    if nd["dirt"] > 85:
        dh -= 0.5
    if nd["tiredness"] > 95:
        dh -= 0.5
    if pet.illness:
        dh -= 0.4
    if max(nd["hunger"], nd["dirt"], nd["boredom"], nd["loneliness"]) < 60:
        dh += 1.0
    pet.health = clamp(pet.health + dh * h, HEALTH_FLOOR, 100.0)

    if pet.illness is None and pet.health < 40:
        if nd["dirt"] >= nd["hunger"] and nd["dirt"] >= nd["loneliness"]:
            pet.illness = "простуда"
        elif nd["hunger"] >= nd["loneliness"]:
            pet.illness = "расстройство"
        else:
            pet.illness = "хандра"
        pet.bump("illnesses")
        out.append(Moment("ill", t_end, {"kind": pet.illness}))
    elif pet.illness is not None and pet.health >= 70:
        out.append(Moment("recovered", t_end, {"kind": pet.illness}))
        pet.illness = None
        pet.bump("recovered")

    # отношения
    if nd["hunger"] > 80:
        gain = GRUDGE_GAIN * max(0.0, 1 - rel["grudge"] / GRUDGE_CAP)
        rel["grudge"] += gain * h * (1 + (tr["vengefulness"] - 50) / 100)
        rel["trust"] -= TRUST_LOSS_HUNGRY * h
        pet.bump("neglect_h", h)
        pet.bump("hungry_h", h)
    if nd["loneliness"] > 85:
        rel["attachment"] -= 0.05 * h
        pet.bump("neglect_h", h)
    if nd["loneliness"] > 70:
        pet.bump("lonely_h", h)
    if nd["boredom"] > 70:
        pet.bump("bored_h", h)
    rel["grudge"] -= 0.2 * h * (1 - 0.6 * tr["vengefulness"] / 100)
    if (t_end - pet.last_owner_t) / HOUR > 24:
        rel["attachment"] -= 0.05 * h
    for k in rel:
        rel[k] = clamp(rel[k])
    rel["trust"] = max(rel["trust"], TRUST_FLOOR)

    pet.mood = clamp(pet.mood + (_mood_target(pet) - pet.mood) * min(1.0, h / 6),
                     MOOD_FLOOR, 100.0)

    _flag_threshold(pet, "hungry", nd["hunger"], 70, 50, out, t_end)
    _flag_threshold(pet, "starving", nd["hunger"], 90, 60, out, t_end)

    # сон: ночью засыпает раньше, днём только от усталости
    hod = (t_end % DAY) // HOUR
    if not pet.sleeping:
        insomnia = pet.flags.get("insomnia_until", 0) > t_end
        if not insomnia and (nd["tiredness"] >= 85 or
                             (1 <= hod < 6 and nd["tiredness"] >= 45)):
            pet.sleeping = True
            pet.sleep_started_t = t_end
            out.append(Moment("sleep", t_end))
    elif nd["tiredness"] <= 8 or t_end - pet.sleep_started_t >= SLEEP_MAX:
        pet.sleeping = False
        out.append(Moment("wake", t_end))


def advance(pet, now):
    """Пересчитать состояние до момента now. Возвращает список моментов.

    Обрабатываются только целые шаги сетки: состояние может отставать от now
    не более чем на STEP (10 минут), зато разбиение на несколько вызовов
    даёт тот же результат бит в бит. pet.grid_t это обработанное время,
    pet.t это последнее известное «сейчас».
    """
    out = []
    while True:
        nxt = (pet.grid_t // STEP + 1) * STEP
        if nxt > now:
            break
        pet.t = nxt
        _step(pet, nxt - pet.grid_t, out)
        pet.grid_t = nxt
        if nxt % DAY == 0:
            lifecycle.end_of_day(pet, out)
    pet.t = max(pet.t, now)
    for m in out:
        journal.moment(pet, m)
    return out
