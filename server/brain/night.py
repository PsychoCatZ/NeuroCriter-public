"""Ночные задачи на Sonnet 5.5: консолидация памяти («сон») и режиссёр.

Claude предлагает, движок режет по рамкам и применяет (engine/consolidation.py).
Если Claude недоступен (бюджет, сбой), работает запасной план движка.
"""
import json
import re
import time
from dataclasses import dataclass, field

from collections import deque

from engine import consolidation, journal
from engine.constants import HOUR
from engine.journal import EVENT_DESCRIPTIONS
from engine.memory import TAG_VOCAB

from .caller import Caller
from .client import MODEL_NIGHT
from .prompts import trait_phrase

EST_COST_SLEEP = 0.03
EST_COST_DIRECTOR = 0.01

SLEEP_SYSTEM = """Ты — ночная память зверька по имени Недоделок, подопытного образца НИИ ИИ \
КотоИмперии (странный зверёк из лишних запчастей, хозяин для него «лаборант»). \
Пока зверёк спит, ты разбираешь его сутки: решаешь, что запомнить, что слить в \
одно воспоминание, что забыть, что он понял о хозяине и кто он теперь. Ты пишешь \
от лица зверька, коротко и по-своему, но без цифр и шкал.

Правила:
- Воспоминание это «событие, эмоция, вес»: summary (одна фраза, до 200 знаков), \
emotion (одно слово), weight (0..1, насколько важно), tags (1–5 штук ТОЛЬКО из \
словаря: {tags}).
- Запоминай только значимое: обиду, прощение, необычное, перемены. Рутину \
(«покормили») не запоминай, если в ней ничего не случилось.
- Не выдумывай того, чего нет в выжимке суток.
- Не упоминай приложение, настройки, уведомления, лимиты.
- Факт о хозяине добавляй, только если он НЕ повторяет ни один из уже записанных \
в досье (проверь список). Лучше ноль фактов, чем пересказ, но и пустым досье не \
оставляй: пока фактов меньше пяти, записывай главное, что понял о хозяине. \
Фактов не больше 2 за ночь.
- Не больше 4 новых воспоминаний, 2 слияний, 4 забываний, 3 сдвигов черт. Сдвиг черты это число от -2 до 2: двигай только если сутки \
действительно её меняли.
- self_narrative: 3–5 коротких фраз о том, кто зверёк сейчас (до 400 знаков), \
переписывай бережно, не теряя старого.
- dream: один странный сон по мотивам суток (до 400 знаков). diary: запись \
в дневник от первого лица (до 400 знаков).

Ответ строго JSON без пояснений и без markdown:
{{"dream": "...", "diary": "...", "self_narrative": "...",
 "memories_add": [{{"summary": "...", "emotion": "...", "weight": 0.7, "tags": ["обида"]}}],
 "memories_merge": [{{"ids": [3, 5], "summary": "..."}}],
 "memories_forget": [7],
 "owner_facts": [{{"statement": "...", "confidence": 0.6}}],
 "trait_nudges": [{{"trait": "anxiety", "delta": 1}}]}}
Допустимые trait: insolence, curiosity, vengefulness, greed, laziness, anxiety, \
clinginess.""".format(tags=", ".join(TAG_VOCAB))

DIRECTOR_SYSTEM = """Ты — режиссёр дня странного зверька-образца НИИ КотоИмперии по имени \
Недоделок. Из списка подходящих событий выбери ОДНО, которое лучше всего ложится \
на то, что с ним сейчас происходит, и не повторяет недавнее. Напиши одно короткое \
вступление в третьем лице (до 25 слов), без цифр и без упоминания приложения, \
настроек и уведомлений. Каждый раз новая сцена: не повторяй зачины и образы \
прошлых вступлений. Не придумывай мест и предметов, которых нет в данных (никаких \
углов и бумажек): есть только комната, миска, подстилка, мячик, клубок, верёвка, \
коробка, дверь, окно, чайник и найденное. Зверька зовут Недоделок (мужской род): когда \
он подлежащее, пиши «Недоделок» в именительном падеже, не «Недоделка».

Формат ответа строго две строки:
СОБЫТИЕ: <id из списка>
ВСТУПЛЕНИЕ: <одно предложение>"""

def parse_json(text):
    """Вытащить первый JSON-объект из ответа. None, если не вышло."""
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        data = json.loads(text[start:end + 1])
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def build_sleep_user(pet, hint=None):
    store = pet.memory
    lines = [f"День {pet.day_index}. Стадия речи: {pet.speech_stage}. "
             f"Черты: {trait_phrase(pet)}. Настроение {pet.mood:.0f}/100, "
             f"доверие {pet.rel['trust']:.0f}/100, обида {pet.rel['grudge']:.0f}/100, "
             f"привязанность {pet.rel['attachment']:.0f}/100."]
    if store.self_narrative:
        lines.append("КТО Я СЕЙЧАС: " + store.self_narrative)
    if store.memories:
        lines.append("ПАМЯТЬ СЕЙЧАС:")
        for m in sorted(store.memories, key=lambda m: -m.weight)[:20]:
            lines.append(f"[{m.id}] (вес {m.weight:.2f}, {m.emotion or '-'}) "
                         f"{m.summary} #{','.join(m.tags)}")
    if store.facts:
        lines.append("ДОСЬЕ НА ЛАБОРАНТА:")
        for f in store.top_facts(8):
            lines.append(f"- {f['statement']} ({f['confidence']:.1f})")
    if len(store.facts) < 5:
        lines.append("В досье пока мало фактов (меньше пяти): запиши 1–2 самых "
                     "важных факта о лаборанте за эти сутки, если они есть.")
    lines.append("ЧТО БЫЛО ЗА СУТКИ:")
    lines.extend(journal.digest(pet) or ["ничего особенного"])
    if pet.mutations:
        lines.append("Тело изменилось: " + ", ".join(pet.mutations))
    if hint:
        lines.append("ВНИМАНИЕ: прошлый ответ был непригоден (" + hint
                     + "). Ответь JSON строго по формату.")
    return "\n".join(lines)


def build_director_user(pet, options, hint=None, recent_intros=()):
    recent = [e for e in pet.event_log if e["kind"].startswith("event:")][-3:]
    lines = [f"День {pet.day_index}. Черты: {trait_phrase(pet)}. "
             f"Настроение {pet.mood:.0f}/100, доверие {pet.rel['trust']:.0f}/100, "
             f"обида {pet.rel['grudge']:.0f}/100. Хозяин отсутствовал "
             f"{(pet.t - pet.last_owner_t) / HOUR:.0f} ч."]
    if pet.memory.self_narrative:
        lines.append("КТО Я СЕЙЧАС: " + pet.memory.self_narrative)
    lines.append("Подходящие события:")
    for eid, weight in options:
        lines.append(f"- {eid}: {EVENT_DESCRIPTIONS.get(eid, eid)} "
                     f"(уместность {weight:.1f})")
    if recent:
        lines.append("Недавно уже было: " + ", ".join(e["kind"][6:] for e in recent))
    if recent_intros:
        lines.append("ПРОШЛЫЕ ВСТУПЛЕНИЯ (не повторяй их зачин и образы):\n"
                     + "\n".join("- " + r for r in recent_intros))
    if hint:
        lines.append("ВНИМАНИЕ: прошлый ответ был непригоден (" + hint + ").")
    return "\n".join(lines)


@dataclass
class SleepReport:
    source: str                  # claude | replay | fallback:<причина>
    notes: list = field(default_factory=list)
    applied: dict = field(default_factory=dict)
    cost: float = 0.0


class NightBrain:
    def __init__(self, transport, calllog, budget, clock=time.time,
                 model=MODEL_NIGHT, max_attempts=2):
        self.caller = Caller(transport, calllog, budget, model, clock)
        self.max_attempts = max_attempts
        self.reports = []
        self.last_intro = None
        self.recent_intros = deque(maxlen=3)

    # --- сон
    def consolidate(self, pet):
        report = self._consolidate(pet)
        consolidation.finish(pet)
        self.reports.append(report)
        return report

    def _consolidate(self, pet):
        if not pet.event_log:
            return SleepReport("skip:no_events")
        if not self.caller.allowed(EST_COST_SLEEP):
            return self._fallback(pet, "tired")
        hint, cost, notes = None, 0.0, []
        for _ in range(self.max_attempts):
            user = build_sleep_user(pet, hint)
            text, source, spent = self.caller.call("sleep", SLEEP_SYSTEM, user)
            cost += spent
            raw = parse_json(text)
            if raw is None:
                hint = "это не JSON"
                continue
            plan, notes = consolidation.sanitize_plan(raw, pet)
            applied = consolidation.apply_plan(pet, plan)
            return SleepReport(source, notes, applied, cost)
        r = self._fallback(pet, "invalid")
        r.cost = cost
        return r

    def _fallback(self, pet, why):
        plan = consolidation.rule_based_plan(pet)
        applied = consolidation.apply_plan(pet, plan)
        return SleepReport("fallback:" + why, [], applied)

    # --- режиссёр
    def choose_event(self, pet, options):
        """Вернуть id события из options или None (тогда решает заглушка движка)."""
        if len(options) <= 1:
            return options[0][0] if options else None
        if not self.caller.allowed(EST_COST_DIRECTOR):
            return None
        hint = None
        ids = {eid for eid, _ in options}
        for _ in range(self.max_attempts):
            user = build_director_user(pet, options, hint, list(self.recent_intros))
            text, source, _ = self.caller.call("director", DIRECTOR_SYSTEM, user)
            eid = _grab(text, "СОБЫТИЕ")
            intro = _grab(text, "ВСТУПЛЕНИЕ")
            if eid in ids and intro and len(intro.split()) <= 25 and not re.search(r"\d", intro):
                self.last_intro = intro
                self.recent_intros.append(intro)
                return eid
            hint = "событие не из списка или вступление не по формату"
        return None


def _grab(text, key):
    m = re.search(rf"^\s*[*_`>#-]*\s*{key}\s*[*_`]*\s*:\s*[*_`]*\s*(.+?)\s*[*_`]*\s*$",
                  text, re.IGNORECASE | re.MULTILINE)
    return m.group(1).strip() if m else None
