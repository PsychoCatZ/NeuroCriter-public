"""Живой прогон: симуляция с настоящим Claude Haiku. Запуск из папки server:

    python -m brain.demo [дни] [live|replay]

live: звонит в API (нужен ANTHROPIC_API_KEY), записывает ответы в demo_calls.sqlite.
replay: те же ответы из записи, без сети и без трат.
Результат печатается и пишется в brain/demo_results.md.
"""
import sys
import time
from pathlib import Path

from sim.run import simulate

from .budget import Budget
from .claude_brain import ClaudeBrain
from .client import AnthropicTransport, FakeTransport
from .recorder import LIVE, REPLAY, CallLog

HERE = Path(__file__).resolve().parent.parent
MONTHLY_LIMIT = 5.0


def main():
    days = int(sys.argv[1]) if len(sys.argv) > 1 else 3
    mode = sys.argv[2] if len(sys.argv) > 2 else LIVE
    sys.stdout.reconfigure(encoding="utf-8")
    calllog = CallLog(str(HERE / "demo_calls.sqlite"), mode=REPLAY if mode == REPLAY else LIVE)
    transport = FakeTransport(["нет"]) if mode == REPLAY else AnthropicTransport()
    budget = Budget(monthly_limit=MONTHLY_LIMIT)
    holder = []

    def factory(rng):
        brain = ClaudeBrain(transport, calllog, budget, rng, clock=time.time)
        holder.append(brain)
        return brain

    pet, _ = simulate(11, "careful", days, brain_factory=factory)
    brain = holder[0]
    lines = [f"# Живой прогон ({mode}), {days} сут., стадия речи в конце: {pet.speech_stage}\n"]
    sources = {}
    total_cost = 0.0
    for i, r in enumerate(brain.reactions, 1):
        sources[r.source] = sources.get(r.source, 0) + 1
        total_cost += r.cost
        lines.append(f"{i}. [ст.{r.stage}, {r.source}, попыток {r.attempts}] "
                     f"{r.key} / {r.label or '-'}: "
                     f"«{r.say}» ({r.tone})"
                     + (f"  НАРУШЕНИЯ: {'; '.join(r.problems)}" if r.problems else ""))
    lines.append("")
    by_stage = {}
    for r in brain.reactions:
        by_stage.setdefault(r.stage, []).append(r.say)
    for st in sorted(by_stage):
        said = by_stage[st]
        lines.append(f"Стадия {st}: реплик {len(said)}, разных {len(set(said))}")
    lines.append(f"Источники: {sources}")
    lines.append(f"Стоимость прогона: ${total_cost:.5f}, вызовов в журнале: {calllog.count()}")
    out = "\n".join(lines)
    print(out)
    (Path(__file__).with_name("demo_results.md")).write_text(out, encoding="utf-8")


if __name__ == "__main__":
    main()
