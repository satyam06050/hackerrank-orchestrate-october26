"""Thread-safe JSONL usage logging and aggregation for model API calls."""

from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import json
from pathlib import Path
import threading
from typing import Any

from config import CACHE_DIR


DEFAULT_USAGE_LOG = Path(CACHE_DIR) / "usage.jsonl"
_LOCKS_GUARD = threading.Lock()
_PATH_LOCKS: dict[Path, threading.Lock] = {}


def _lock_for(path: Path) -> threading.Lock:
    normalized = path.expanduser().resolve()
    with _LOCKS_GUARD:
        return _PATH_LOCKS.setdefault(normalized, threading.Lock())


def _decimal_cost(value: Decimal | int | float | str) -> Decimal:
    try:
        cost = Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError(f"invalid cost value: {value!r}") from exc
    if not cost.is_finite() or cost < 0:
        raise ValueError("cost must be a finite, non-negative amount")
    return cost


class UsageLogger:
    """Append one sanitized accounting record per completed provider call."""

    def __init__(self, path: str | Path = DEFAULT_USAGE_LOG) -> None:
        self.path = Path(path).expanduser().resolve()
        self._lock = _lock_for(self.path)

    def record_call(
        self,
        *,
        provider: str,
        model: str,
        case_id: str,
        purpose: str,
        input_tokens: int,
        output_tokens: int,
        cache_read_tokens: int,
        cache_write_tokens: int,
        cost: Decimal | int | float | str,
    ) -> dict[str, Any]:
        token_values = {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cache_read_tokens": cache_read_tokens,
            "cache_write_tokens": cache_write_tokens,
        }
        for name, value in token_values.items():
            if not isinstance(value, int) or value < 0:
                raise ValueError(f"{name} must be a non-negative integer")

        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "provider": provider,
            "model": model,
            "case_id": case_id,
            "purpose": purpose,
            **token_values,
            "cost": str(_decimal_cost(cost)),
        }
        line = json.dumps(entry, ensure_ascii=False, separators=(",", ":")) + "\n"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock, self.path.open("a", encoding="utf-8", newline="\n") as stream:
            stream.write(line)
            stream.flush()
        return entry


def _empty_totals() -> dict[str, Any]:
    return {
        "calls": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_read_tokens": 0,
        "cache_write_tokens": 0,
        "cost": Decimal("0"),
    }


def _add_entry(totals: dict[str, Any], entry: dict[str, Any]) -> None:
    totals["calls"] += 1
    for field in ("input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens"):
        totals[field] += int(entry[field])
    totals["cost"] += _decimal_cost(entry["cost"])


def _serialize_totals(totals: dict[str, Any]) -> dict[str, Any]:
    calls = totals["calls"]
    result = {**totals, "cost": str(totals["cost"])}
    result["total_tokens"] = totals["input_tokens"] + totals["output_tokens"]
    result["average_input_tokens_per_call"] = totals["input_tokens"] / calls if calls else 0
    result["average_output_tokens_per_call"] = totals["output_tokens"] / calls if calls else 0
    result["average_tokens_per_call"] = result["total_tokens"] / calls if calls else 0
    result["average_cost_per_call"] = str(totals["cost"] / calls) if calls else "0"
    return result


def aggregate_usage(path: str | Path = DEFAULT_USAGE_LOG) -> dict[str, Any]:
    """Aggregate JSONL usage into overall totals and provider/model totals."""
    usage_path = Path(path).expanduser().resolve()
    grouped: dict[tuple[str, str], dict[str, Any]] = defaultdict(_empty_totals)
    overall = _empty_totals()
    lock = _lock_for(usage_path)
    if not usage_path.exists():
        return {"overall": _serialize_totals(overall), "by_model": {}}

    with lock, usage_path.open("r", encoding="utf-8") as stream:
        for line_no, line in enumerate(stream, start=1):
            if not line.strip():
                continue
            try:
                entry = json.loads(line)
                _add_entry(overall, entry)
                _add_entry(grouped[(entry["provider"], entry["model"])], entry)
            except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
                raise ValueError(f"invalid usage log entry at {usage_path}:{line_no}") from exc

    by_model = {
        f"{provider}/{model}": _serialize_totals(totals)
        for (provider, model), totals in sorted(grouped.items())
    }
    return {"overall": _serialize_totals(overall), "by_model": by_model}
