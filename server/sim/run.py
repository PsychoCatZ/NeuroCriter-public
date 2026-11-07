"""Симуляция: один зверёк, один хозяин, N суток. Запуск из папки server:

    python -m sim.run [дни] [зерно_зверька]
"""
import sys

from engine import consolidation, events, journal, needs
from engine.brain import StubBrain
from engine.constants import DAY, HOUR, TRAIT_LABELS, TRAITS
from engine.model import Pet
from engine.mutations import label
from engine.rng import Rng

from .owners import OWNERS


def simulate(pet_seed, owner_kind, days=21, owner_seed=1, switch=None,
             brain_factory=None, night=None):
    """switch=(вид_хозяина, день): с этого дня хозяин меняется."""
    pet = Pet.new(pet_seed)
    pet_rng = Rng(pet_seed * 1000 + 7)
    brain = brain_factory(pet_rng) if brain_factory else StubBrain(pet_rng)
    owners = {k: OWNERS[k]() for k in OWNERS}
    owner_rng = Rng(owner_seed)
    stats = {"moments": {}}

    def count(moments):
        for m in moments:
            stats["moments"][m.kind] = stats["moments"].get(m.kind, 0) + 1

    for day in range(days):
        kind_today = switch[0] if switch and day >= switch[1] else owner_kind
        owner = owners[kind_today]
        plan = [(day * DAY + off, "owner") for off in owner.sessions(day, owner_rng)]
        plan.append((day * DAY + 12 * HOUR, "director"))
        plan.append((day * DAY + 6 * HOUR, "sleep"))
        plan.sort()
        n = 0
        for t, kind in plan:
            if t < pet.t:
                t = pet.t
            if kind == "sleep":
                count(needs.advance(pet, t))
                if night is not None:
                    night.consolidate(pet)
                else:
                    consolidation.apply_plan(pet, consolidation.rule_based_plan(pet))
                    consolidation.finish(pet)
            elif kind == "director":
                count(needs.advance(pet, t))
                m = events.run_director(pet, brain, pet_rng)
                if m:
                    count([m])
            else:
                owner.session(pet, t, brain, pet_rng, owner_rng, n)
                n += 1
    count(needs.advance(pet, days * DAY))
    return pet, stats


def trait_distance(a, b):
    return sum(abs(a.traits[t] - b.traits[t]) for t in TRAITS)


def mutation_distance(a, b):
    sa, sb = set(a.mutations), set(b.mutations)
    union = sa | sb
    return 0.0 if not union else 1 - len(sa & sb) / len(union)


def describe(pet, stats, owner_name):
    lines = [f"=== {pet.number}, хозяин: {owner_name}, возраст {pet.day_index} сут. ===",
             f"Стадия речи: {pet.speech_stage} (очки заботы {pet.care_points:.0f})",
             f"Здоровье {pet.health:.0f}, настроение {pet.mood:.0f}, "
             f"режим: {pet.mode}, болезнь: {pet.illness or 'нет'}",
             "Отношения: " + ", ".join(f"{k} {v:.0f}" for k, v in pet.rel.items()),
             "Черты (старт → сейчас):"]
    for t in TRAITS:
        d = pet.traits[t] - pet.base_traits[t]
        lines.append(f"  {TRAIT_LABELS[t]:<14} {pet.base_traits[t]:5.1f} → "
                     f"{pet.traits[t]:5.1f} ({d:+.1f})")
    fav = pet.favorite()
    lines.append("Любимая еда: " + (f"{fav} ({pet.prefs[fav][0]:.2f})" if fav else "ещё нет"))
    muts = ", ".join(f"{label(m)} (день {d})" for m, d in pet.mutations.items())
    lines.append("Мутации: " + (muts or "нет"))
    c = pet.counters
    lines.append(f"Предметов: {len(pet.items)}, отказов: {c.get('refusals', 0):.0f}, "
                 f"болезней: {c.get('illnesses', 0):.0f}, "
                 f"карантинов: {c.get('hibernations', 0):.0f}, "
                 f"слов выучено: {len(pet.lexicon)}")
    return "\n".join(lines)


def main():
    days = int(sys.argv[1]) if len(sys.argv) > 1 else 21
    seed = int(sys.argv[2]) if len(sys.argv) > 2 else 11
    sys.stdout.reconfigure(encoding="utf-8")
    careful, st1 = simulate(seed, "careful", days)
    sloppy, st2 = simulate(seed, "sloppy", days)
    print(describe(careful, st1, "заботливый"))
    print()
    print(describe(sloppy, st2, "раздолбай"))
    td = trait_distance(careful, sloppy)
    md = mutation_distance(careful, sloppy)
    print()
    print(f"Расхождение черт (сумма модулей): {td:.1f}")
    print(f"Расхождение мутаций (1 - Жаккар): {md:.2f}")
    print(f"Стадии речи: {careful.speech_stage} против {sloppy.speech_stage}")


if __name__ == "__main__":
    main()
