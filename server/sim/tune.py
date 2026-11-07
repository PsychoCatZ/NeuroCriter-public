"""Сводка по многим зверькам для настройки чисел движка. Запуск из папки server:

    python -m sim.tune [дни] [сколько_зверьков]
"""
import statistics as st
import sys

from engine.constants import TRAITS

from .run import simulate


def collect(kind, days, seeds):
    rows = []
    for seed in seeds:
        pet, _ = simulate(seed, kind, days)
        c = pet.counters
        asked = c.get("asked", 0)
        rows.append({
            "ref_per_day": c.get("refusals", 0) / days,
            "ref_rate": c.get("refusals", 0) / asked if asked else 0.0,
            "grudge": pet.rel["grudge"], "trust": pet.rel["trust"],
            "mood": pet.mood, "health": pet.health,
            "ill": c.get("illnesses", 0), "stage": pet.speech_stage,
            "hib": c.get("hibernations", 0),
            "drift": {t: pet.traits[t] - pet.base_traits[t] for t in TRAITS},
        })
    return rows


def mean(rows, key):
    return st.mean(r[key] for r in rows)


def report(kind, rows):
    print(f"--- {kind} ({len(rows)} зверьков) ---")
    print(f"отказов в сутки: {mean(rows, 'ref_per_day'):.2f}; "
          f"доля отказов от запросов: {mean(rows, 'ref_rate') * 100:.1f}%")
    print(f"обида {mean(rows, 'grudge'):.0f} (макс {max(r['grudge'] for r in rows):.0f}), "
          f"доверие {mean(rows, 'trust'):.0f} (мин {min(r['trust'] for r in rows):.0f}), "
          f"настроение {mean(rows, 'mood'):.0f} (мин {min(r['mood'] for r in rows):.0f}), "
          f"здоровье {mean(rows, 'health'):.0f} (мин {min(r['health'] for r in rows):.0f})")
    print(f"болезней {mean(rows, 'ill'):.1f}, карантинов {mean(rows, 'hib'):.1f}, "
          f"стадия речи {mean(rows, 'stage'):.1f}")
    drifts = ", ".join(
        f"{t[:5]} {st.mean(r['drift'][t] for r in rows):+.1f}"
        f"(±{st.pstdev([r['drift'][t] for r in rows]):.1f})" for t in TRAITS)
    print("дрейф черт (среднее, разброс): " + drifts)


def recovery(days_bad, days_good, seeds):
    rows = []
    for seed in seeds:
        pet, _ = simulate(seed, "sloppy", days_bad + days_good,
                          switch=("careful", days_bad))
        rows.append({"grudge": pet.rel["grudge"], "trust": pet.rel["trust"],
                     "mood": pet.mood, "health": pet.health,
                     "ill": 1 if pet.illness else 0})
    print(f"--- восстановление: {days_bad} сут. раздолбай, затем {days_good} сут. "
          f"заботливый ({len(rows)} зверьков) ---")
    print(f"обида {mean(rows, 'grudge'):.0f} (макс {max(r['grudge'] for r in rows):.0f}), "
          f"доверие {mean(rows, 'trust'):.0f} (мин {min(r['trust'] for r in rows):.0f}), "
          f"настроение {mean(rows, 'mood'):.0f}, "
          f"здоровье {mean(rows, 'health'):.0f} (мин {min(r['health'] for r in rows):.0f}), "
          f"ещё больны: {sum(r['ill'] for r in rows)} из {len(rows)}")


def main():
    days = int(sys.argv[1]) if len(sys.argv) > 1 else 21
    n = int(sys.argv[2]) if len(sys.argv) > 2 else 12
    sys.stdout.reconfigure(encoding="utf-8")
    seeds = list(range(1, n + 1))
    for kind in ("careful", "sloppy"):
        report(kind, collect(kind, days, seeds))
    recovery(days, 14, seeds)


if __name__ == "__main__":
    main()
