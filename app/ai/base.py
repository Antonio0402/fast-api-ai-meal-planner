from abc import ABC, abstractmethod
from typing import Any


class AIProvider(ABC):
    @abstractmethod
    def generate_meal_plan(self, preferences: dict[str, Any]) -> str:
        """Generate a meal plan from user preferences."""
        raise NotImplementedError
