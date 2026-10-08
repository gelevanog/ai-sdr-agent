"""Folds every results/*.json run and the call ledger into results/summary.json (the dashboard's evaluation page
and the README tables read it)."""

from __future__ import annotations

import datetime as dt
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from scout.config import Settings


def ledger_summary(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"requests": 0}
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    requested = Counter(r.get("requested_model") for r in rows)
    served = Counter(r.get("served_model") for r in rows if r.get("served_model"))
    by_tag: dict[str, int] = defaultdict(int)
    for r in rows:
        by_tag[str(r.get("tag", "")).split(":")[0]] += 1
    all_ids = [m for m in list(requested) + list(served) if m]
    return {
        "requests": len(rows),
        "ok": sum(1 for r in rows if r.get("status") == "ok"),
        "retried": sum(1 for r in rows if r.get("status") == "retryable_error"),
        "errors": sum(1 for r in rows if r.get("status") == "error"),
        "requested_models": dict(requested),
        "served_models": dict(served),
        "by_tag": dict(by_tag),
        "all_free": all(str(m).endswith(":free") for m in all_ids),
        "first": rows[0]["ts"] if rows else None,
        "last": rows[-1]["ts"] if rows else None,
        "input_tokens": sum(int(r.get("input_tokens") or 0) for r in rows),
        "output_tokens": sum(int(r.get("output_tokens") or 0) for r in rows),
    }


def _strip(data: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in data.items() if k != "rows"}


def write_summary(settings: Settings) -> Path:
    results = settings.results_dir
    runs: dict[str, dict[str, Any]] = defaultdict(dict)
    for path in sorted(results.glob("*_*.json")):
        if path.name in {"summary.json", "calls_summary.json", "free_models.json"}:
            continue
        data = json.loads(path.read_text(encoding="utf-8"))
        if "suite" not in data:
            continue
        runs[data["name"]][path.stem[len(data["name"]) + 1 :]] = _strip(data)
    ledger = ledger_summary(settings.llm_ledger or results / "calls.jsonl")
    (results / "calls_summary.json").write_text(json.dumps(ledger, indent=1), encoding="utf-8")
    summary = {"generated": dt.date.today().isoformat(), "runs": runs, "calls": ledger}
    out = results / "summary.json"
    out.write_text(json.dumps(summary, indent=1, ensure_ascii=False, default=str), encoding="utf-8")
    print(f"wrote {out}")
    return out
