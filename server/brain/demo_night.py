"""Живой прогон ночных задач: память, сон, режиссёр (Sonnet 5.5) + голос (Haiku 5.5).

Из папки server:

    python -m brain.demo_night [дни] [live|replay]

Ориентир по цене: около 2 центов на сутки игрового времени (в основном Sonnet).
Бюджет здесь поднят до 30 $ в месяц, чтобы прогон не упирался в дневной лимит
реального времени: все игровые сутки проходят за минуты. Результат печатается и
пишется в brain/demo_night_results.md.
"""
import sys
import time
from pathlib import Path

from sim.run import simulate

from .budget import Budget
from .claude_brain import ClaudeBrain
from .client import MODEL_NIGHT, AnthropicTransport
from .night import NightBrain
from .recorder import LIVE, REPLAY, CallLog

HERE = Path(__file__).resolve().parent.parent


def run_demo(voice_transport, night_transport, calllog, days, monthly_limit=30.0,
             clock=time.time, seed=11, owner="careful"):
    budget = Budget(monthly_limit=monthly_limit)
    night = NightBrain(night_transport, calllog, budget, clock=clock)
    holder = []

    def factory(rng):
        brain = ClaudeBrain(voice_transport, calllog, budget, rng, clock=clock,
                            night=night)
        holder.append(brain)
        return brain

    pet, _ = simulate(seed, owner, days, brain_factory=factory, night=night)
    brain = holder[0]
    lines = [f"# Ночные задачи ({calllog.mode}), {days} сут., хозяин: {owner}\n"]

    lines.append("## Режиссёр")
    for day, eid, intro in brain.director_log:
        lines.append(f"- день {day}: {eid}: {intro}")
    if not brain.director_log:
        lines.append("- Claude не выбирал события")

    lines.append("\n## Сон (консолидация)")
    for i, r in enumerate(night.reports, 1):
        lines.append(f"- {i}. [{r.source}] {r.applied}"
                     + (f" замечания: {r.notes}" if r.notes else ""))

    lines.append("\n## Память в конце")
    lines.append(f"Кто я: {pet.memory.self_narrative or '-'}")
    for m in sorted(pet.memory.memories, key=lambda m: -m.weight):
        lines.append(f"- [{m.id}] вес {m.weight:.2f}, {m.emotion or '-'}, "
                     f"#{','.join(m.tags)}: {m.summary} (использовано {m.used_count}×)")
    lines.append("\nДосье на лаборанта:")
    for f in pet.memory.top_facts(10):
        lines.append(f"- {f['statement']} ({f['confidence']:.1f})")
    lines.append("\nДневник (последние 3):")
    for d, t in pet.diary[-3:]:
        lines.append(f"- день {d}: {t}")
    lines.append("\nСны (последние 3):")
    for d, t in pet.dreams[-3:]:
        lines.append(f"- день {d}: {t}")

    used = [r for r in brain.reactions if r.memory]
    lines.append(f"\n## Воспоминания в репликах: {len(used)} из {len(brain.reactions)}")
    for r in used[:12]:
        lines.append(f"- [ст.{r.stage}] «{r.say}»  ← {r.memory}")

    rows = calllog.db.execute(
        "SELECT kind, COUNT(*), ROUND(SUM(cost_usd), 5) FROM llm_calls GROUP BY kind"
    ).fetchall()
    lines.append("\n## Вызовы по видам (вид, число, стоимость $)")
    for kind, n, cost in rows:
        lines.append(f"- {kind}: {n}, ${cost}")
    lines.append(f"Итого в журнале: ${sum(r[2] or 0 for r in rows):.4f}")
    return "\n".join(lines)


def main():
    days = int(sys.argv[1]) if len(sys.argv) > 1 else 14
    mode = sys.argv[2] if len(sys.argv) > 2 else LIVE
    owner = sys.argv[3] if len(sys.argv) > 3 else "careful"
    sys.stdout.reconfigure(encoding="utf-8")
    calllog = CallLog(str(HERE / "demo_night_calls.sqlite"),
                      mode=REPLAY if mode == REPLAY else LIVE)
    if mode == REPLAY:
        from .client import FakeTransport
        voice = night = FakeTransport(["нет"])
    else:
        voice = AnthropicTransport()
        night = AnthropicTransport(model=MODEL_NIGHT, max_tokens=3000, effort="low")
    out = run_demo(voice, night, calllog, days, owner=owner)
    print(out)
    suffix = "" if owner == "careful" else "_" + owner
    (Path(__file__).with_name(f"demo_night_results{suffix}.md")).write_text(
        out, encoding="utf-8")


if __name__ == "__main__":
    main()
