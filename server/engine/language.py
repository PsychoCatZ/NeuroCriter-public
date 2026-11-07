"""Язык зверька: освоение имени, понимание слов хозяина, выученные слова.

Движок решает, что зверёк понимает и знает. Claude получает только то, что
зверёк «услышал»: на стадии 1 интонацию, на стадии 2 знакомые слова, с 3-й весь текст.
"""
import re

LEARNING_AT = 0.34       # с этого прогресса зверёк узнаёт имя, когда его зовут
KNOWN_AT = 1.0           # с этого прогресса зовёт себя по имени
NAME_GAIN_PER_CALL = 0.12
NAME_GAIN_PER_DAY = 0.03
FAMILIAR_AT = 2          # слово слышано не менее стольких раз: стадия 2 его понимает
LEARNED_AT = 3           # столько раз слышано: слово можно повторять на свой лад

_PUNCT = ".,!?;:«»\"'()—-…"
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


def norm(text):
    return text.lower().replace("ё", "е")


def name_state(pet):
    """none (имени нет) | unknown | learning | known."""
    if not pet.name:
        return "none"
    if pet.name_progress >= KNOWN_AT:
        return "known"
    return "learning" if pet.name_progress >= LEARNING_AT else "unknown"


def mentions_name(pet, text):
    return bool(pet.name) and norm(pet.name) in norm(text)


def words_of(text):
    out = []
    for w in text.lower().split():
        w = w.strip(_PUNCT)
        if len(w) > 2:
            out.append(w)
    return out


def understood_words(pet, text):
    """Слова текста, которые зверёк понял на своей стадии речи."""
    words = words_of(text)
    if pet.speech_stage <= 1:
        return []
    if pet.speech_stage == 2:
        return [w for w in words if pet.lexicon.get(w, 0) >= FAMILIAR_AT]
    return words


def echo_words(pet, n=3):
    """Самые частые слова хозяина, которые зверёк выучил (кроме собственного имени)."""
    skip = norm(pet.name) if pet.name else None
    known = sorted(((c, w) for w, c in pet.lexicon.items() if c >= LEARNED_AT and norm(w) != skip),
                   reverse=True)
    return [w for _, w in known[:n]]


def clean_owner_text(text, limit=200):
    """Текст хозяина в одну строку без управляющих символов и служебных слов формата."""
    text = _CONTROL.sub(" ", text or "")
    text = " ".join(text.split())[:limit]
    for key in ("ДЕЙСТВИЕ", "РЕПЛИКА", "ТОН", "ИМПУЛЬС", "ВОСПОМИНАНИЕ", "СОБЫТИЕ"):
        text = re.sub(key, key.lower(), text, flags=re.IGNORECASE)
    return text.replace("«", "\"").replace("»", "\"")
