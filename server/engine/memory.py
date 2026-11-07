"""Долговременная память зверька (M2): эпизоды, досье на хозяина, самонарратив.

Воспоминание хранится в виде «событие, эмоция, вес» с тегами, чтобы его можно
было хранить и отбирать без LLM. Забывание обязательно: вес затухает каждые сутки.
"""
from dataclasses import dataclass, field

from .constants import DAY

MAX_MEMORIES = 40
MAX_FACTS = 20
DECAY_PER_DAY = 0.97
FORGET_BELOW = 0.12
COOLDOWN_DAYS = 2

# Закрытый словарь тегов: по нему отбираются воспоминания.
TAG_VOCAB = (
    "еда", "голод", "сон", "ночь", "хозяин", "пропал", "возвращение", "обида",
    "прощение", "игра", "прогулка", "ласка", "мытьё", "болезнь", "одиночество",
    "находка", "заначка", "мутация", "событие", "телефон", "чайник",
)


def _stems(text):
    return {w[:5] for w in text.lower().replace("ё", "е").split()
            if len(w.strip(".,!?;:«»\"'()-—")) > 3}


def similar(a, b, threshold):
    """Грубое сходство по основам слов (Жаккар), чтобы не копить пересказы."""
    sa, sb = _stems(a), _stems(b)
    if not sa or not sb:
        return False
    return len(sa & sb) / len(sa | sb) >= threshold


@dataclass
class Memory:
    id: int
    day: int
    summary: str
    emotion: str
    weight: float
    tags: list
    used_count: int = 0
    last_used_t: int = 0
    cooldown_until_t: int = 0


@dataclass
class MemoryStore:
    memories: list = field(default_factory=list)
    facts: list = field(default_factory=list)       # {"statement","confidence","day"}
    self_narrative: str = ""
    next_id: int = 1

    # --- эпизоды
    def get(self, mid):
        for m in self.memories:
            if m.id == mid:
                return m
        return None

    def add(self, day, summary, emotion, weight, tags):
        weight = max(0.0, min(1.0, float(weight)))
        for m in self.memories:                      # тот же эпизод: усиливаем
            if (m.summary.strip().lower() == summary.strip().lower()
                    or similar(m.summary, summary, 0.6)):
                m.weight = max(m.weight, weight)
                return m
        mem = Memory(self.next_id, day, summary.strip(), emotion.strip(), weight,
                     list(tags))
        self.next_id += 1
        self.memories.append(mem)
        self._evict()
        return mem

    def merge(self, ids, summary):
        parts = [self.get(i) for i in ids]
        parts = [m for m in parts if m is not None]
        if len(parts) < 2:
            return None
        tags = []
        for m in parts:
            for t in m.tags:
                if t not in tags:
                    tags.append(t)
        merged = Memory(self.next_id, max(m.day for m in parts), summary.strip(),
                        parts[0].emotion, min(1.0, max(m.weight for m in parts) + 0.05),
                        tags)
        self.next_id += 1
        for m in parts:
            self.memories.remove(m)
        self.memories.append(merged)
        return merged

    def forget(self, mid):
        m = self.get(mid)
        if m is not None:
            self.memories.remove(m)
            return True
        return False

    def decay(self):
        for m in self.memories:
            m.weight *= DECAY_PER_DAY
        self.memories = [m for m in self.memories if m.weight >= FORGET_BELOW]

    def _evict(self):
        while len(self.memories) > MAX_MEMORIES:
            weakest = min(self.memories, key=lambda m: (m.weight, m.day))
            self.memories.remove(weakest)

    # --- досье на хозяина
    def upsert_fact(self, statement, confidence, day):
        key = statement.strip().lower()
        confidence = max(0.0, min(1.0, float(confidence)))
        for f in self.facts:
            if (f["statement"].strip().lower() == key
                    or similar(f["statement"], statement, 0.45)):
                f["confidence"] = max(f["confidence"] * 0.5 + confidence * 0.5, confidence)
                f["day"] = day
                return f
        fact = {"statement": statement.strip(), "confidence": confidence, "day": day}
        self.facts.append(fact)
        while len(self.facts) > MAX_FACTS:
            self.facts.remove(min(self.facts, key=lambda f: (f["confidence"], f["day"])))
        return fact

    # --- отбор
    def retrieve(self, tags, now):
        """Лучшее воспоминание по пересечению тегов, весу и свежести (с кулдауном)."""
        tags = set(tags)
        best, best_score = None, 0.0
        for m in self.memories:
            if m.cooldown_until_t > now:
                continue
            overlap = len(tags & set(m.tags))
            if not overlap:
                continue
            age_days = max(0.0, now / DAY - m.day)
            score = overlap * m.weight / (1.0 + age_days / 30.0)
            if score > best_score:
                best, best_score = m, score
        return best

    def mark_used(self, m, now):
        m.used_count += 1
        m.last_used_t = now
        m.cooldown_until_t = now + COOLDOWN_DAYS * DAY

    def top_facts(self, n=5):
        return sorted(self.facts, key=lambda f: -f["confidence"])[:n]
