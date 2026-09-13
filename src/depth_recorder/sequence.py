from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Gap:
    symbol: str
    prev_u: int
    first_update_id: int
    final_update_id: int

    @property
    def missing_span(self) -> int:
        return self.first_update_id - self.prev_u - 1


class SequenceTracker:
    def __init__(self) -> None:
        self._last: dict[str, int] = {}

    def observe(self, symbol: str, first_update_id: int, final_update_id: int) -> Gap | None:
        if first_update_id > final_update_id:
            raise ValueError("first update ID exceeds final update ID")
        previous = self._last.get(symbol)
        gap = None
        if previous is not None and first_update_id > previous + 1:
            gap = Gap(symbol, previous, first_update_id, final_update_id)
        self._last[symbol] = max(previous or final_update_id, final_update_id)
        return gap

    def last_update_id(self, symbol: str) -> int | None:
        return self._last.get(symbol)
