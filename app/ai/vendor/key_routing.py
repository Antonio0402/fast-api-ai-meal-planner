"""Key routing copied from GeminiAgentMealPLanner/ai_module/key_routing.py on 2026-10-11."""

from dataclasses import dataclass
from typing import Iterable


TIER_PRIORITY = {"free": 0, "low_cost": 1, "standard": 2}


@dataclass(frozen=True)
class ApiKeyEntry:
    name: str
    api_key: str
    tier: str = "standard"


class ApiKeyRouter:
    def __init__(self, keys: Iterable[ApiKeyEntry]) -> None:
        self._keys = sorted(
            (key for key in keys if key.api_key),
            key=lambda key: (TIER_PRIORITY.get(key.tier, 99), key.name),
        )
        self._blocked: set[str] = set()

    def next_key(self) -> ApiKeyEntry:
        for entry in self._keys:
            if entry.name not in self._blocked:
                return entry
        raise RuntimeError("No API key available.")

    def block(self, key_name: str) -> None:
        self._blocked.add(key_name)
