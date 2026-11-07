"""Разбор ответа Claude в формате из трёх строк (формат подтверждён пробами)."""
import re

_KEYS = {"ДЕЙСТВИЕ": "action", "РЕПЛИКА": "say", "ТОН": "tone"}
_LINE = re.compile(r"^\s*[*_`>#-]*\s*(ДЕЙСТВИЕ|РЕПЛИКА|ТОН)\s*[*_`]*\s*:\s*[*_`]*\s*(.*?)\s*[*_`]*\s*$",
                   re.IGNORECASE)


def parse_three_lines(text):
    """Вернуть {'action','say','tone'} или None, если нет действия и реплики."""
    out = {}
    for line in text.splitlines():
        m = _LINE.match(line)
        if m:
            out[_KEYS[m.group(1).upper()]] = m.group(2).strip().strip("«»\"")
    if "action" not in out or "say" not in out:
        return None
    out.setdefault("tone", "")
    return out
