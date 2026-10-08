"""Lists the current free OpenRouter models and smoke-tests candidates on Scout's hardest JSON task (extraction of
one company with citations) plus a reply classification, before any long run."""

from __future__ import annotations

import datetime as dt
import json
import time
from pathlib import Path
from typing import Any

import httpx
from rich.console import Console

from scout.config import Settings
from scout.icp import load_config
from scout.llm.base import LLMError
from scout.llm.factory import budgeted, build_chat_model
from scout.replies.classify import classify_reply
from scout.research.agent import research_account
from scout.research.crawler import Crawler, FileFetcher

console = Console()


def list_free_models(settings: Settings) -> list[dict[str, Any]]:
    response = httpx.get(f"{settings.openrouter_base_url}/models", timeout=30)
    response.raise_for_status()
    models = [
        {"id": m["id"], "context_length": m.get("context_length"), "structured_outputs": "response_format" in (m.get("supported_parameters") or [])}
        for m in response.json()["data"]
        if str(m["id"]).endswith(":free")
    ]
    out = settings.results_dir / "free_models.json"
    out.write_text(json.dumps({"date": dt.date.today().isoformat(), "models": models}, indent=1), encoding="utf-8")
    for m in models:
        console.print(m["id"])
    return models


def smoke(settings: Settings, models: list[str]) -> None:
    config = load_config(settings.icp_file)
    crawler = Crawler(FileFetcher(settings.synthetic_web_dir), user_agent=settings.crawl_user_agent)
    results = []
    for model_id in models:
        model = budgeted(build_chat_model(settings, provider="openrouter", model=model_id, fallback_models=[]), settings, tag=f"smoke:{model_id}")
        row: dict[str, Any] = {"model": model_id}
        t0 = time.monotonic()
        try:
            res = research_account("https://brightwater-fs.example/", crawler=crawler, model=model, config=config, today=settings.research_today())
            p = res.profile
            row["extract"] = {
                "ok": res.error is None,
                "facts": len(p.facts),
                "signals": sorted(s.type for s in p.signals),
                "rejected": len(p.rejected),
                "seconds": round(time.monotonic() - t0, 1),
                "error": res.error,
            }
        except LLMError as exc:
            row["extract"] = {"ok": False, "error": str(exc)[:200]}
        t1 = time.monotonic()
        try:
            label, _ = classify_reply("Re: fleet", "We already use TrackRight and we're happy with it.", "p@x.example", model=model, seller="Wayline", today=settings.research_today())
            row["classify"] = {"label": label.label, "objection": label.objection_type, "seconds": round(time.monotonic() - t1, 1)}
        except LLMError as exc:
            row["classify"] = {"error": str(exc)[:200]}
        console.print(row)
        results.append(row)
    path = settings.results_dir / "smoke.json"
    previous = json.loads(path.read_text(encoding="utf-8")).get("runs", []) if path.exists() else []
    path.write_text(json.dumps({"runs": [*previous, {"date": dt.date.today().isoformat(), "results": results}]}, indent=1), encoding="utf-8")
    console.print(f"wrote {Path(path)}")
