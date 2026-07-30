"""Cumulative, retry-safe task tool budget accounting."""

from __future__ import annotations

from dataclasses import dataclass
from threading import Lock


class ToolBudgetExceeded(PermissionError):
    pass


@dataclass(frozen=True, slots=True)
class ToolBudgetLimits:
    max_calls: int
    max_read_bytes: int
    max_result_bytes: int
    max_write_bytes: int

    @classmethod
    def from_claims(cls, values: dict[str, int] | object) -> "ToolBudgetLimits":
        if not isinstance(values, dict):
            values = dict(values)  # type: ignore[arg-type]
        return cls(
            max_calls=int(values.get("max_calls", 0)),
            max_read_bytes=int(values.get("max_read_bytes", 0)),
            max_result_bytes=int(values.get("max_result_bytes", 0)),
            max_write_bytes=int(values.get("max_write_bytes", 0)),
        )


@dataclass(frozen=True, slots=True)
class BudgetUsage:
    calls: int = 0
    read_bytes: int = 0
    result_bytes: int = 0
    write_bytes: int = 0

    def plus(self, other: "BudgetUsage") -> "BudgetUsage":
        return BudgetUsage(
            calls=self.calls + other.calls,
            read_bytes=self.read_bytes + other.read_bytes,
            result_bytes=self.result_bytes + other.result_bytes,
            write_bytes=self.write_bytes + other.write_bytes,
        )


class CumulativeBudgetLedger:
    """In-memory atomic ledger; persistence adapter can replay the same receipts later."""

    def __init__(self, limits: ToolBudgetLimits) -> None:
        self._limits = limits
        self._usage = BudgetUsage()
        self._operations: dict[str, BudgetUsage] = {}
        self._lock = Lock()

    @property
    def usage(self) -> BudgetUsage:
        return self._usage

    @property
    def limits(self) -> ToolBudgetLimits:
        return self._limits

    def consume(self, *, operation_id: str, delta: BudgetUsage) -> BudgetUsage:
        if not operation_id or any(value < 0 for value in (delta.calls, delta.read_bytes, delta.result_bytes, delta.write_bytes)):
            raise ValueError("operation id and non-negative usage delta are required")
        with self._lock:
            prior = self._operations.get(operation_id)
            if prior is not None:
                if prior != delta:
                    raise ToolBudgetExceeded("reused operation id has a different budget delta")
                return self._usage
            next_usage = self._usage.plus(delta)
            if (
                next_usage.calls > self._limits.max_calls
                or next_usage.read_bytes > self._limits.max_read_bytes
                or next_usage.result_bytes > self._limits.max_result_bytes
                or next_usage.write_bytes > self._limits.max_write_bytes
            ):
                raise ToolBudgetExceeded("cumulative task tool budget exceeded")
            self._operations[operation_id] = delta
            self._usage = next_usage
            return self._usage
