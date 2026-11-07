"""Состояние зверька (в памяти). В M3 то же самое ляжет в SQLite."""
from dataclasses import dataclass, field
from typing import Optional

from .constants import FOODS, HOUR, TRAITS
from .memory import MemoryStore
from .rng import Rng


def clamp(v, lo=0.0, hi=100.0):
    return max(lo, min(hi, v))


@dataclass
class Moment:
    """Значимый момент, который движок заметил при пересчёте."""
    kind: str
    t: int
    data: dict = field(default_factory=dict)


@dataclass
class Pet:
    seed: int
    number: str
    born_t: int
    t: int
    base_traits: dict
    traits: dict
    taste: dict                       # скрытый вкус к еде, -1..1
    needs: dict
    health: float
    mood: float
    rel: dict                         # trust, attachment, grudge, respect
    grid_t: int = 0                   # время, до которого обработана сетка шагов
    sleeping: bool = False
    sleep_started_t: int = 0
    illness: Optional[str] = None
    mode: str = "normal"              # normal | hibernating
    last_owner_t: int = 0
    care_points: float = 0.0
    speech_stage: int = 1
    day_index: int = 0
    refusals_today: int = 0
    prefs: dict = field(default_factory=dict)      # еда -> [оценка, число раз]
    counters: dict = field(default_factory=dict)   # накопительные счётчики
    today: dict = field(default_factory=dict)      # счётчики текущих суток
    flags: dict = field(default_factory=dict)
    mutations: dict = field(default_factory=dict)  # имя -> день появления
    items: list = field(default_factory=list)
    lexicon: dict = field(default_factory=dict)    # слово -> сколько раз слышал
    mood_days: list = field(default_factory=list)
    memory: MemoryStore = field(default_factory=MemoryStore)
    event_log: list = field(default_factory=list)  # журнал значимых событий
    diary: list = field(default_factory=list)      # (день, текст)
    dreams: list = field(default_factory=list)     # (день, текст)
    last_consolidation_day: int = -1
    name: Optional[str] = None
    name_progress: float = 0.0       # 0..1: насколько зверёк освоил своё имя

    @classmethod
    def new(cls, seed, born_t=8 * HOUR):
        rng = Rng(seed)
        base = {t: clamp(rng.gauss(50, 18), 10, 90) for t in TRAITS}
        taste = {f: rng.uniform(-0.5, 1.0) for f in FOODS}
        return cls(
            seed=seed, number=f"НП-{seed:04d}", born_t=born_t, t=born_t,
            base_traits=dict(base), traits=dict(base), taste=taste,
            needs={"hunger": 30.0, "tiredness": 10.0, "dirt": 10.0,
                   "boredom": 30.0, "loneliness": 20.0, "walk_need": 20.0},
            health=100.0, mood=65.0,
            rel={"trust": 40.0, "attachment": 30.0, "grudge": 0.0,
                 "respect": 30.0},
            last_owner_t=born_t, grid_t=born_t,
        )

    def bump(self, key, n=1.0):
        self.counters[key] = self.counters.get(key, 0.0) + n
        self.today[key] = self.today.get(key, 0.0) + n

    def shift_mood(self, delta):
        from .constants import MOOD_FLOOR
        self.mood = clamp(self.mood + delta, MOOD_FLOOR, 100.0)

    def add_care(self, n):
        from .speech import stage_for
        self.care_points = max(0.0, self.care_points + n)
        self.speech_stage = stage_for(self.care_points)

    def favorite(self):
        """Любимая еда: лучшая оценка среди блюд, которые ели не менее 3 раз."""
        known = {f: v for f, v in self.prefs.items() if v[1] >= 3}
        if not known:
            return None
        return max(known, key=lambda f: known[f][0])
