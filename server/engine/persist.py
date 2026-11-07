"""Сериализация зверька в JSON и обратно (для SQLite в M3)."""
import json
from dataclasses import asdict

from .memory import Memory, MemoryStore
from .model import Pet


def pet_to_dict(pet):
    return asdict(pet)


def pet_from_dict(data):
    data = dict(data)
    mem = data.pop("memory", None) or {}
    store = MemoryStore(
        memories=[Memory(**m) for m in mem.get("memories", [])],
        facts=list(mem.get("facts", [])),
        self_narrative=mem.get("self_narrative", ""),
        next_id=mem.get("next_id", 1),
    )
    return Pet(memory=store, **data)


def dumps(pet):
    return json.dumps(pet_to_dict(pet), ensure_ascii=False)


def loads(text):
    return pet_from_dict(json.loads(text))
