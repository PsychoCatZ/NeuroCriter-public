"""Два симулированных хозяина: заботливый и раздолбай.

У хозяина свой генератор случайных чисел, не связанный с генератором зверька.
"""
from engine import actions
from engine.constants import DAY, FOODS, HOUR

GREETINGS = ["привет", "как ты тут", "я дома", "смотри, что принёс", "хороший мой"]
SLOPPY_PHRASES = ["блин", "потом", "отстань", "ну что тебе", "чайник кипит"]


class CarefulOwner:
    """Приходит 4 раза в день, кормит по нужде и по разному, не будит."""
    name = "заботливый"

    def sessions(self, day, rng):
        times = [8, 13, 18, 22]
        return [int((h + rng.uniform(-0.5, 0.5)) * HOUR) for h in times]

    def session(self, pet, t, brain, pet_rng, rng, n_today):
        a = lambda action, **kw: actions.act(pet, t, action, brain, pet_rng, **kw)
        from engine import needs
        needs.advance(pet, t)
        if pet.sleeping:
            return
        a("say", text=rng.choice(GREETINGS))
        if pet.illness:
            a("heal")
        if pet.needs["hunger"] >= 40:
            food = FOODS[(pet.day_index + n_today) % len(FOODS)]
            a("feed", food=food)
        elif pet.needs["hunger"] < 30 and rng.chance(0.12):
            a("feed", food="печенье")
        if pet.rel["grudge"] >= 25:
            a("reconcile", food=pet.favorite())
        if pet.needs["dirt"] >= 50:
            a("clean")
        if pet.needs["boredom"] >= 40:
            a("play")
        if pet.needs["loneliness"] >= 40:
            a("pet")
        if t % DAY >= 16 * HOUR and pet.needs["walk_need"] >= 40:
            a("walk")
        a("say", text=rng.choice(GREETINGS))


class SloppyOwner:
    """Ночная сова: приходит под утро, пропадает на сутки и на четыре дня."""
    name = "раздолбай"

    def sessions(self, day, rng):
        if 8 <= day <= 11:                 # исчез на четыре дня
            return []
        if rng.chance(0.25):               # пропустил день
            return []
        out = [int(rng.uniform(2, 5) * HOUR)]
        if rng.chance(0.5):
            out.append(int(rng.uniform(14, 17) * HOUR))
        return out

    def session(self, pet, t, brain, pet_rng, rng, n_today):
        a = lambda action, **kw: actions.act(pet, t, action, brain, pet_rng, **kw)
        from engine import needs
        needs.advance(pet, t)
        a("say", text=rng.choice(SLOPPY_PHRASES))
        if pet.illness and rng.chance(0.5):
            a("heal")
        if pet.needs["hunger"] >= 70:
            a("feed", food="суп")
        if pet.needs["boredom"] >= 80 and rng.chance(0.3):
            a("play")
        if pet.needs["dirt"] >= 80 and rng.chance(0.2):
            a("clean")
        if pet.needs["walk_need"] >= 90 and rng.chance(0.1):
            a("walk")


OWNERS = {"careful": CarefulOwner, "sloppy": SloppyOwner}
