"""Суточные операции: дрейф черт, мутации, сброс дневных счётчиков."""
from . import language, mutations, traits


def end_of_day(pet, out):
    pet.mood_days.append(pet.mood)
    if pet.rel["trust"] >= 70:
        pet.counters["trust_streak"] = pet.counters.get("trust_streak", 0) + 1
    else:
        pet.counters["trust_streak"] = 0
    if pet.name and pet.today.get("said", 0) >= 1:
        pet.name_progress = min(1.0, pet.name_progress + language.NAME_GAIN_PER_DAY)
    traits.drift(pet)
    mutations.check(pet, out)
    pet.day_index += 1
    pet.today.clear()
    pet.refusals_today = 0
