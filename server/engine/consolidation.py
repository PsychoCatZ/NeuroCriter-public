"""Ночная консолидация: чистка и применение плана от Claude, запасной план без LLM.

Claude предлагает план (что запомнить, слить, забыть, как сдвинуть черты), а
движок режет его по жёстким рамкам и сам применяет. Без Claude (бюджет, сбой)
работает rule_based_plan: самые значимые события дня становятся воспоминаниями.
"""
from . import journal, traits
from .constants import TRAITS
from .memory import TAG_VOCAB

MAX_ADD = 4
MAX_MERGE = 2
MAX_FORGET = 4
MAX_FACTS_ADD = 2
MAX_NUDGES = 3
NUDGE_LIMIT = 2.0          # на одну черту за ночь
NUDGE_TOTAL = 4.0          # суммарно за ночь
TEXT_LIMITS = {"dream": 400, "diary": 400, "self_narrative": 400,
               "summary": 200, "statement": 120, "emotion": 20}

BANNED = ("пуш", "уведомл", "настройк", "приложен", "лимит", "бюджет",
          "нейросет", "ассистент", "искусственн", "как ии")


def _clean_text(value, limit):
    if not isinstance(value, str):
        return None
    text = " ".join(value.split())[:limit].strip()
    low = text.lower()
    if not text or any(b in low for b in BANNED):
        return None
    return text


def _num(value, default):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _tags(raw):
    tags = [t for t in (raw or []) if isinstance(t, str) and t in TAG_VOCAB]
    return tags[:5] or ["событие"]


def sanitize_plan(raw, pet):
    """Вернуть (чистый план, список замечаний). Ничего не бросает."""
    notes = []
    plan = {"dream": None, "diary": None, "self_narrative": None,
            "add": [], "merge": [], "forget": [], "facts": [], "nudges": []}
    if not isinstance(raw, dict):
        return plan, ["ответ не объект"]
    store = pet.memory

    for key in ("dream", "diary", "self_narrative"):
        text = _clean_text(raw.get(key), TEXT_LIMITS[key])
        if raw.get(key) and text is None:
            notes.append(f"{key} отброшен")
        plan[key] = text

    for item in (raw.get("memories_add") or [])[:MAX_ADD]:
        if not isinstance(item, dict):
            continue
        summary = _clean_text(item.get("summary"), TEXT_LIMITS["summary"])
        if not summary:
            notes.append("воспоминание без текста отброшено")
            continue
        plan["add"].append({
            "summary": summary,
            "emotion": _clean_text(item.get("emotion"), TEXT_LIMITS["emotion"]) or "",
            "weight": max(0.0, min(1.0, _num(item.get("weight"), 0.5))),
            "tags": _tags(item.get("tags")),
        })

    for item in (raw.get("memories_merge") or [])[:MAX_MERGE]:
        if not isinstance(item, dict):
            continue
        ids = [i for i in (item.get("ids") or []) if isinstance(i, int) and store.get(i)]
        summary = _clean_text(item.get("summary"), TEXT_LIMITS["summary"])
        if len(set(ids)) >= 2 and summary:
            plan["merge"].append({"ids": sorted(set(ids)), "summary": summary})
        else:
            notes.append("слияние отброшено")

    merged_ids = {i for m in plan["merge"] for i in m["ids"]}
    for mid in (raw.get("memories_forget") or [])[:MAX_FORGET]:
        if isinstance(mid, int) and store.get(mid) and mid not in merged_ids:
            plan["forget"].append(mid)

    for item in (raw.get("owner_facts") or [])[:MAX_FACTS_ADD]:
        if not isinstance(item, dict):
            continue
        statement = _clean_text(item.get("statement"), TEXT_LIMITS["statement"])
        if statement:
            plan["facts"].append({"statement": statement,
                                  "confidence": max(0.0, min(1.0, _num(item.get("confidence"), 0.5)))})

    total = 0.0
    for item in (raw.get("trait_nudges") or [])[:MAX_NUDGES]:
        if not isinstance(item, dict) or item.get("trait") not in TRAITS:
            continue
        delta = max(-NUDGE_LIMIT, min(NUDGE_LIMIT, _num(item.get("delta"), 0.0)))
        if abs(total) + abs(delta) > NUDGE_TOTAL:
            delta = (NUDGE_TOTAL - abs(total)) * (1 if delta >= 0 else -1)
        total += abs(delta)
        if delta:
            plan["nudges"].append({"trait": item["trait"], "delta": delta})
    return plan, notes


def rule_based_plan(pet):
    """Запасной план: самые значимые события дня становятся воспоминаниями."""
    entries = sorted(pet.event_log, key=lambda e: -e["salience"])
    plan = {"dream": None, "diary": None, "self_narrative": None,
            "add": [], "merge": [], "forget": [], "facts": [], "nudges": []}
    seen = set()
    for e in entries:
        if e["salience"] < 0.5 or e["kind"] in seen:
            continue
        seen.add(e["kind"])
        text = journal._describe(e)
        plan["add"].append({"summary": text[0].upper() + text[1:], "emotion": "",
                            "weight": e["salience"], "tags": _tags(e["tags"])})
        if len(plan["add"]) >= 2:
            break
    return plan


def apply_plan(pet, plan):
    """Применить чистый план. Возвращает краткий отчёт."""
    day = pet.day_index
    store = pet.memory
    report = {"added": 0, "merged": 0, "forgotten": 0, "facts": 0, "nudges": 0}
    for item in plan["add"]:
        store.add(day, item["summary"], item["emotion"], item["weight"], item["tags"])
        report["added"] += 1
    for item in plan["merge"]:
        if store.merge(item["ids"], item["summary"]):
            report["merged"] += 1
    for mid in plan["forget"]:
        if store.forget(mid):
            report["forgotten"] += 1
    for item in plan["facts"]:
        store.upsert_fact(item["statement"], item["confidence"], day)
        report["facts"] += 1
    if plan["self_narrative"]:
        store.self_narrative = plan["self_narrative"]
    if plan["dream"]:
        pet.dreams.append((day, plan["dream"]))
    if plan["diary"]:
        pet.diary.append((day, plan["diary"]))
    for item in plan["nudges"]:
        traits.nudge(pet, item["trait"], item["delta"])
        report["nudges"] += 1
    return report


def finish(pet):
    """Всегда после консолидации: затухание памяти, очистка журнала суток."""
    pet.memory.decay()
    journal.clear(pet)
    pet.last_consolidation_day = pet.day_index
