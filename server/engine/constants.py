"""Константы движка. Все числа стартовые и подлежат настройке по симуляциям."""

HOUR = 3600
DAY = 86400
STEP = 600                      # сетка ленивого пересчёта, секунд
HIBERNATE_AFTER = 72 * HOUR     # без хозяина дольше этого: «карантин лаборатории»
HEALTH_FLOOR = 25.0             # смерти нет: здоровье не опускается ниже
REFUSAL_BUDGET = 3              # сколько раз за сутки зверёк может отказаться
SLEEP_MAX = 9 * HOUR

NEEDS = ("hunger", "tiredness", "dirt", "boredom", "loneliness", "walk_need")

TRAITS = ("insolence", "curiosity", "vengefulness", "greed",
          "laziness", "anxiety", "clinginess")
TRAIT_LABELS = {
    "insolence": "наглость", "curiosity": "любопытство",
    "vengefulness": "злопамятность", "greed": "жадность",
    "laziness": "лень", "anxiety": "тревожность", "clinginess": "привязчивость",
}
TRAIT_DRIFT_LIMIT = 30.0        # дрейф не дальше этого от стартового значения

FOODS = ("сосиски", "суп", "каша", "рыба", "печенье")

# Скорость изменения потребностей, единиц в час.
AWAKE_RATES = {"hunger": 6.0, "tiredness": 4.0, "dirt": 2.0,
               "boredom": 5.0, "loneliness": 3.0, "walk_need": 2.5}
SLEEP_RATES = {"hunger": 2.0, "tiredness": -12.0, "dirt": 0.0,
               "boredom": 0.0, "loneliness": 1.0, "walk_need": 0.0}

# Очки заботы, с которых начинается стадия речи 1..4.
STAGE_THRESHOLDS = (0, 50, 200, 600)

FIND_KINDS = ("пуговица", "ложка без пары", "ключ от неизвестного", "фантик",
              "гайка", "перо", "странная шестерёнка")

# --- настройка (M0, подлежит правке по симуляциям: python -m sim.tune) ---
WILL_BASE = 1.0                 # базовая готовность зверька сотрудничать
GRUDGE_GAIN = 0.8               # прирост обиды в час при сильном голоде
GRUDGE_CAP = 80.0               # обида насыщается около этого значения
TRUST_FLOOR = 10.0              # доверие не падает ниже (путь назад всегда есть)
MOOD_FLOOR = 20.0               # настроение не падает ниже
HUNGER_HEALTH_DAMAGE = 1.0      # потеря здоровья в час при сильном голоде
TRUST_LOSS_HUNGRY = 0.08        # потеря доверия в час при сильном голоде
MOOD_NEED_WEIGHT = 0.7          # множитель влияния потребностей на настроение
