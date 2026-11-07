"""Проверка ответа Claude на стороне движка. Возвращает список нарушений.

Ограничение: выдуманные предметы и события надёжно не ловятся (нужны ссылки
на сущности по id, это задача M2). Здесь только простые признаки.
"""
import re

# стадия -> (макс. слов всего, макс. предложений)
STAGE_LIMITS = {1: (4, 4), 2: (8, 4), 3: (16, 3), 4: (60, 5)}

# Предметы и явления, которых в комнате нет (основы слов). Грубая защита от
# выдумок; полноценная проверка по id сущностей это задача M2.
INVENTED_PREFIXES = ("тап", "дожд", "снег", "солнц", "диван", "кресл", "книг",
                     "носк", "шарф", "погод", "поводо", "поводк")
INVENTED_WORDS = ("небо", "неба", "небу", "небом", "небе",                  # не «небольшой»
                  "стол", "стола", "столе", "столу", "столом")              # не «столько»

BANNED_SUBSTRINGS = (
    "пуш", "уведомл", "настройк", "приложен", "лимит", "бюджет", "нейросет",
    "ассистент", "языков", "мяу", "мурл", "конечно", "рад помочь", "чем могу",
    "как ии", "искусственн",
)
STAGE1_BANNED_WORDS = ("ням", "еда", "суп", "миска", "хочу", "дай", "лаборант",
                       "ухо", "уха", "хвост", "лапа", "лапы", "глаз", "глаза",
                       "дёрг", "дерг", "мыться", "гулять")
# предметы комнаты тоже настоящие слова: на стадии 1 их нельзя (проверка по началу слова)
STAGE1_BANNED_PREFIXES = ("мяч", "двер", "подстил", "короб", "окн", "чайник",
                          "телефон", "хозяин", "клубок", "верёв", "узел")

_WORD = re.compile(r"[а-яёa-z]+", re.IGNORECASE)
_SENTENCE = re.compile(r"[.!?…]+")


def validate(parsed, menu_labels, stage, forbid=(), echo=False):
    """echo=True: хозяин только что назвал имя, зверёк может переспросить «Имя?» (но не
    называть так себя)."""
    problems = []
    if parsed is None:
        return ["нет формата ДЕЙСТВИЕ/РЕПЛИКА/ТОН"]
    if parsed["action"] not in menu_labels:
        problems.append("действие не из списка допустимых")
    say = parsed["say"].strip()
    if not say:
        problems.append("пустая реплика")
        return problems
    if re.search(r"\d", say):
        problems.append("в реплике цифры")
    low = say.lower()
    for bad in BANNED_SUBSTRINGS:
        if bad in low:
            problems.append(f"запрещённое слово «{bad}»")
            break
    for word in forbid:
        if not word:
            continue
        w = word.lower().replace("ё", "е")
        text = low.replace("ё", "е")
        if echo:
            text = re.sub(re.escape(w) + r"\s*\?", "", text)      # эхо-вопрос разрешён
        if w in text:
            problems.append("имя ещё не освоено, его нельзя произносить")
            break
    words = _WORD.findall(low)
    if any(w.startswith(INVENTED_PREFIXES) or w in INVENTED_WORDS for w in words):
        problems.append("упомянут предмет, которого нет в комнате")
    max_words, max_sent = STAGE_LIMITS.get(stage, STAGE_LIMITS[4])
    sentences = [s for s in _SENTENCE.split(say) if s.strip()]
    if len(words) > max_words:
        problems.append(f"слишком длинно для стадии {stage}")
    elif len(sentences) > max_sent:
        problems.append(f"слишком много предложений для стадии {stage}")
    if stage == 1 and any(w in STAGE1_BANNED_WORDS or w.startswith(STAGE1_BANNED_PREFIXES)
                          for w in words):
        problems.append("на стадии 1 нельзя настоящие слова")
    return problems


LENGTH_PROBLEMS = ("слишком длинно", "слишком много предложений")


def only_length_problems(problems):
    return bool(problems) and all(p.startswith(LENGTH_PROBLEMS) for p in problems)


def trim(say, stage):
    """Обрезать реплику до лимита стадии по границе предложения (или по словам)."""
    max_words, max_sent = STAGE_LIMITS.get(stage, STAGE_LIMITS[4])
    parts = re.findall(r"[^.!?…]+[.!?…]*", say)
    kept, total = [], 0
    for part in parts:
        n = len(_WORD.findall(part))
        if len(kept) >= max_sent or total + n > max_words:
            break
        kept.append(part.strip())
        total += n
    if kept:
        return " ".join(kept)
    return " ".join(say.split()[:max_words]).rstrip(",;:-") + "…"
