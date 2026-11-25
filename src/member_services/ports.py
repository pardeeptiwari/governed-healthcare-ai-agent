"""Dependency contracts for the application composition root."""

from dataclasses import dataclass
from datetime import date
from typing import Any, Protocol


class EvaluationClock(Protocol):
    def today(self) -> date: ...


@dataclass(frozen=True)
class FixedClock:
    as_of: date

    def today(self) -> date:
        return self.as_of


@dataclass(frozen=True)
class ModelCompletion:
    content: str
    prompt_tokens: int | None = None
    completion_tokens: int | None = None


class ModelProvider(Protocol):
    async def complete(self, *, instruction: str, projection: str, max_tokens: int) -> ModelCompletion:
        """Classify already-minimized input without invoking tools."""
        ...


class StateRepository(Protocol):
    def create(self, identifier: str, payload: dict[str, Any]) -> None: ...
    def load(self, identifier: str) -> dict[str, Any] | None: ...
    def reserve(self, identifier: str, turn: str, digest: str) -> tuple[str, str | None] | None: ...
    def save(self, identifier: str, payload: dict[str, Any]) -> None: ...
    def begin_filing(self, identifier: str, payload: dict[str, Any], turn_id: str | None = None) -> bool: ...
    def start_attempt(self, key: str) -> int: ...
    def finish_attempt(self, attempt: int, decision: str) -> None: ...
    def recover_interrupted(self) -> None: ...
    def claim_recovery(self, durable_replay: bool) -> tuple | None: ...
    def complete_recovery(self, key: str, receipt: dict | None, *, manual: bool = False) -> None: ...
    def finish(
        self, identifier: str, payload: dict[str, Any], turn: str,
        receipt: dict[str, Any], audit: dict[str, Any],
    ) -> None: ...
    def audit(self, identifier: str) -> list[dict[str, Any]]: ...
