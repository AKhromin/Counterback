from __future__ import annotations

import heapq
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from .models import DepthEvent, Snapshot, TickerEvent


class BookDataError(ValueError):
    pass


def _number(value: str) -> Decimal:
    try:
        result = Decimal(value)
    except InvalidOperation as exc:
        raise BookDataError(f"invalid decimal: {value!r}") from exc
    if not result.is_finite():
        raise BookDataError(f"non-finite decimal: {value!r}")
    return result


@dataclass(slots=True)
class ApplyResult:
    unseen_removals: int = 0
    outer_unseen_removals: int = 0


class HorizonBook:
    """L2 reconstruction that preserves the snapshot's unknown outer horizon."""

    def __init__(self, snapshot: Snapshot) -> None:
        self.symbol = snapshot.symbol
        self.last_update_id = snapshot.last_update_id
        self.bids = self._load_side(snapshot.bids)
        self.asks = self._load_side(snapshot.asks)
        if not self.bids or not self.asks:
            raise BookDataError("snapshot must contain both sides")
        self.bid_boundary = min(self.bids)
        self.ask_boundary = max(self.asks)
        self._bid_heap = [-price for price in self.bids]
        self._ask_heap = list(self.asks)
        heapq.heapify(self._bid_heap)
        heapq.heapify(self._ask_heap)
        self._trusted_bid_levels = sum(price >= self.bid_boundary for price in self.bids)
        self._trusted_ask_levels = sum(price <= self.ask_boundary for price in self.asks)

    @staticmethod
    def _load_side(levels: tuple[tuple[str, str], ...]) -> dict[Decimal, Decimal]:
        result: dict[Decimal, Decimal] = {}
        for price_text, quantity_text in levels:
            price, quantity = _number(price_text), _number(quantity_text)
            if price <= 0 or quantity < 0:
                raise BookDataError("snapshot prices must be positive and quantities non-negative")
            if quantity:
                result[price] = quantity
        return result

    @property
    def bid_trusted(self) -> bool:
        return self._trusted_bid_levels > 0

    @property
    def ask_trusted(self) -> bool:
        return self._trusted_ask_levels > 0

    @property
    def top_trusted(self) -> bool:
        return self.bid_trusted and self.ask_trusted

    @property
    def best_bid(self) -> tuple[Decimal, Decimal] | None:
        if not self.bid_trusted:
            return None
        while self._bid_heap and -self._bid_heap[0] not in self.bids:
            heapq.heappop(self._bid_heap)
        if not self._bid_heap:
            return None
        price = -self._bid_heap[0]
        return price, self.bids[price]

    @property
    def best_ask(self) -> tuple[Decimal, Decimal] | None:
        if not self.ask_trusted:
            return None
        while self._ask_heap and self._ask_heap[0] not in self.asks:
            heapq.heappop(self._ask_heap)
        if not self._ask_heap:
            return None
        price = self._ask_heap[0]
        return price, self.asks[price]

    def apply(self, event: DepthEvent) -> ApplyResult:
        if event.symbol != self.symbol:
            raise BookDataError("event symbol does not match book")
        if event.final_update_id <= self.last_update_id:
            return ApplyResult()
        if event.first_update_id > self.last_update_id + 1:
            raise BookDataError("sequence gap")
        result = ApplyResult()
        self._apply_side(self.bids, event.bids, True, result)
        self._apply_side(self.asks, event.asks, False, result)
        self.last_update_id = event.final_update_id
        if self.top_trusted:
            bid = self.best_bid
            ask = self.best_ask
            assert bid is not None and ask is not None
            if bid[0] >= ask[0]:
                raise BookDataError("crossed or locked reconstructed book")
        return result

    def _apply_side(
        self,
        side: dict[Decimal, Decimal],
        updates: tuple[tuple[str, str], ...],
        is_bid: bool,
        result: ApplyResult,
    ) -> None:
        boundary = self.bid_boundary if is_bid else self.ask_boundary
        for price_text, quantity_text in updates:
            price, quantity = _number(price_text), _number(quantity_text)
            if price <= 0 or quantity < 0:
                raise BookDataError("update prices must be positive and quantities non-negative")
            was_present = price in side
            inside_horizon = price >= boundary if is_bid else price <= boundary
            if quantity == 0:
                if not was_present:
                    result.unseen_removals += 1
                    outside = price < boundary if is_bid else price > boundary
                    if outside:
                        result.outer_unseen_removals += 1
                else:
                    side.pop(price)
                    if inside_horizon:
                        if is_bid:
                            self._trusted_bid_levels -= 1
                        else:
                            self._trusted_ask_levels -= 1
            else:
                side[price] = quantity
                if not was_present:
                    if is_bid:
                        heapq.heappush(self._bid_heap, -price)
                        if inside_horizon:
                            self._trusted_bid_levels += 1
                    else:
                        heapq.heappush(self._ask_heap, price)
                        if inside_horizon:
                            self._trusted_ask_levels += 1

    def matches_ticker(self, ticker: TickerEvent) -> bool | None:
        if ticker.symbol != self.symbol or not self.top_trusted:
            return None
        bid, ask = self.best_bid, self.best_ask
        assert bid is not None and ask is not None
        return bid == (_number(ticker.bid_price), _number(ticker.bid_quantity)) and ask == (
            _number(ticker.ask_price),
            _number(ticker.ask_quantity),
        )
