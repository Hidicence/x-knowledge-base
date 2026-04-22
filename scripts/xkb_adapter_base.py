from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class XKBInferenceAdapter(ABC):
    name: str = "base"

    @abstractmethod
    def run(self, request_path: str) -> dict[str, Any]:
        """Run one request artifact and return normalized result."""
        raise NotImplementedError
