"""Разбор жизни зверька по копиям баз с сервера (pet.sqlite, calls.sqlite, budget.sqlite).

    python tools/analyze_server.py <папка_с_копиями> [файл_отчёта.md]

Только чтение. Копии баз не коммитить (они содержат реплики и промпты).
pet.sqlite копировать только через бэкап (deploy/backup.py или sqlite3 .backup): простая копия
файла без -wal теряет свежие данные. Файлы должны называться pet.sqlite, calls.sqlite, budget.sqlite.
Показывает: состояние зверька, расходы, как часто валидатор браковал ответы Claude,
повторяющиеся обороты (тики), использование имени, память, пинги.
"""
import json
import re
import sqlite3
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent / "server"
sys.path.insert(0, str(ROOT))

from brain.parse import parse_three_lines  # noqa: E402
from brain.validate import validate  # noqa: E402
from engine import language, persist  # noqa: E402
from engine.constants import TRAIT_LABELS  # noqa: E402

STOP = set("это как что так для или уже ещё еще его она они мне мой тебя тут там все всё "
           "был была было когда если только очень нет да ну вот чтобы себя".split())


def connect(path):
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True) if path.exists() else None


def section(out, title):
    out.append(f"\n## {title}\n")


def ngrams(texts, n, top=8, min_count=3):
    c = Counter()
    for t in texts:
        words = [w for w in re.findall(r"[а-яёa-z]+", t.lower())]
        for i in range(len(words) - n + 1):
            gram = tuple(words[i:i + n])
            if not all(w in STOP for w in gram):
                c[gram] += 1
    return [(" ".join(g), k) for g, k in c.most_common(top) if k >= min_count]


def opener_counts(texts, top=8, min_count=3):
    c = Counter()
    for t in texts:
        w = re.findall(r"[а-яёa-z]+", t.lower())[:2]
        if w:
            c[" ".join(w)] += 1
    return [(g, k) for g, k in c.most_common(top) if k >= min_count]


def report(folder):
    folder = Path(folder)
    out = [f"# Разбор зверька ({folder})\n"]
    pet_db = connect(folder / "pet.sqlite")
    calls_db = connect(folder / "calls.sqlite")
    budget_db = connect(folder / "budget.sqlite")

    pet = None
    speech = []
    if pet_db is not None:
        try:
            row = pet_db.execute("SELECT json FROM pet_state WHERE id=1").fetchone()
        except sqlite3.OperationalError:
            raise SystemExit("В pet.sqlite нет таблиц: копия сделана без -wal. Возьми копию из бэкапа.")
        pet = persist.loads(row[0]) if row else None
        speech = [dict(zip(("t", "kind", "action", "say", "tone", "source"), r)) for r in
                  pet_db.execute("SELECT t, kind, action, say, tone, source FROM speech ORDER BY id")]

    section(out, "1. Зверёк")
    if pet is None:
        out.append("Зверёк не вылуплен.")
    else:
        out.append(f"{pet.number}, имя: {pet.name or '-'} ({language.name_state(pet)}, "
                   f"прогресс {pet.name_progress:.2f}), день {pet.day_index}, "
                   f"стадия речи {pet.speech_stage}, очки заботы {pet.care_points:.0f}, "
                   f"режим {pet.mode}, болезнь: {pet.illness or 'нет'}")
        out.append("\nОтношения: " + ", ".join(f"{k} {v:.0f}" for k, v in pet.rel.items()))
        out.append("\nЧерты (старт → сейчас):")
        for k, label in TRAIT_LABELS.items():
            out.append(f"- {label}: {pet.base_traits[k]:.0f} → {pet.traits[k]:.0f} "
                       f"({pet.traits[k] - pet.base_traits[k]:+.1f})")
        out.append("\nМутации: " + (", ".join(f"{m} (день {d})" for m, d in pet.mutations.items()) or "нет"))
        c = pet.counters
        keys = ("fed", "played", "cuddles", "walks", "cleaned", "refusals", "illnesses",
                "hibernations", "said", "name_calls", "overfeed")
        out.append("\nСчётчики: " + ", ".join(f"{k} {c.get(k, 0):.0f}" for k in keys))

    section(out, "2. Расходы")
    if budget_db is not None:
        rows = budget_db.execute("SELECT day, ROUND(SUM(cost), 5), COUNT(*) FROM spend "
                                 "GROUP BY day ORDER BY day").fetchall()
        for day, cost, n in rows:
            out.append(f"- {day}: ${cost} ({n} вызовов)")
        total = sum(r[1] for r in rows)
        out.append(f"\nИтого: ${total:.4f}. Прикидка на месяц по последним суткам: "
                   f"${(rows[-1][1] * 30 if rows else 0):.2f}")
    else:
        out.append("budget.sqlite нет.")

    section(out, "3. Вызовы Claude")
    texts = []
    if calls_db is not None:
        kinds = calls_db.execute("SELECT kind, COUNT(*), ROUND(SUM(cost_usd), 5), "
                                 "ROUND(AVG(in_tokens)), ROUND(AVG(out_tokens)) "
                                 "FROM llm_calls GROUP BY kind ORDER BY 2 DESC").fetchall()
        for kind, n, cost, tin, tout in kinds:
            out.append(f"- {kind}: {n} вызовов, ${cost}, в среднем {tin:.0f} токенов на входе и {tout:.0f} на выходе")

        problems = Counter()
        checked = 0
        for req, resp in calls_db.execute("SELECT request, response FROM llm_calls"):
            m = re.search(r"Стадия речи: (\d)", req)
            menu_m = re.search(r"Допустимые действия: (.+)\.", req)
            if not m or not menu_m:
                continue
            stage = int(m.group(1))
            parsed = parse_three_lines(resp)
            forbid = ()
            if "свой номер" in req or "только привыкаешь" in req:
                forbid = tuple(re.findall(r"«([^»]+)»", req.split("ИМЯ:")[1][:80])[:1]) if "ИМЯ:" in req else ()
            probs = validate(parsed, set(menu_m.group(1).split(", ")), stage, forbid, echo=True)
            checked += 1
            if parsed:
                texts.append(parsed["say"])
            for p in probs:
                problems[re.sub(r"стадии \d", "стадии N", p)] += 1
        out.append(f"\nОтветов реплик проверено валидатором заново: {checked}")
        for p, n in problems.most_common():
            out.append(f"- {p}: {n} ({n / max(checked, 1) * 100:.0f}%)")
    else:
        out.append("calls.sqlite нет.")

    section(out, "4. Что видит хозяин (лента реплик)")
    reactions = [s for s in speech if s["kind"] == "reaction"]
    if reactions:
        by_source = Counter(s["source"].split(":")[0] for s in reactions)
        out.append("Источники реплик: " + ", ".join(f"{k} {v}" for k, v in by_source.most_common())
                   + " (engine = запасной ответ без Claude)")
    events = [s for s in speech if s["kind"] == "event"]
    out.append(f"Событий режиссёра в ленте: {len(events)}")
    for s in events[-5:]:
        out.append(f"- {s['say']}")

    section(out, "5. Тики (повторяющиеся обороты)")
    said = [s["say"] for s in reactions] + texts
    for n in (2, 3):
        found = ngrams(said, n)
        if found:
            out.append(f"Частые {n}-слова: " + "; ".join(f"«{g}» ×{k}" for g, k in found))
    openers = opener_counts(said)
    if openers:
        out.append("Частые зачины: " + "; ".join(f"«{g}» ×{k}" for g, k in openers))
    if pet is not None and pet.name:
        low = [t.lower() for t in said]
        out.append(f"Реплик с именем «{pet.name}»: "
                   f"{sum(pet.name.lower() in t for t in low)} из {len(said)}")

    section(out, "6. Память, дневник, сны")
    if pet is not None:
        out.append(f"Кто он: {pet.memory.self_narrative or '-'}")
        out.append(f"\nВоспоминаний: {len(pet.memory.memories)}, фактов о хозяине: {len(pet.memory.facts)}, "
                   f"записей дневника: {len(pet.diary)}, снов: {len(pet.dreams)}")
        for m in sorted(pet.memory.memories, key=lambda m: -m.weight)[:6]:
            out.append(f"- вес {m.weight:.2f}, #{','.join(m.tags)}: {m.summary} (использовано {m.used_count}×)")
        for f in pet.memory.top_facts(6):
            out.append(f"- факт: {f['statement']} ({f['confidence']:.1f})")
        for d, t in pet.diary[-2:]:
            out.append(f"- дневник, день {d}: {t}")

    section(out, "7. Уведомления")
    if pet_db is not None:
        pings = pet_db.execute("SELECT t, category, title, body, status FROM pings ORDER BY id DESC LIMIT 15").fetchall()
        subs = pet_db.execute("SELECT COUNT(*) FROM push_subs").fetchone()[0]
        out.append(f"Подписок: {subs}, последних пингов: {len(pings)}")
        for t, cat, title, body, status in pings:
            out.append(f"- [{status}] {cat}: {body}")
    return "\n".join(out)


def main():
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    sys.stdout.reconfigure(encoding="utf-8")
    text = report(sys.argv[1])
    print(text)
    if len(sys.argv) > 2:
        Path(sys.argv[2]).write_text(text, encoding="utf-8")


if __name__ == "__main__":
    main()
