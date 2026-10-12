"""Re-judge an existing drafts result (e.g. the free-model baseline results/main_drafts.json) with the configured
judge, so drafts written by different models are scored by the same judge. Writes <out_name>_drafts.json."""
import json
import re
import sys
from pathlib import Path

from scout.eval import judges, runner
from scout.icp import load_config
from scout.synthetic.spec import load_specs

src, out_name = Path(sys.argv[1]), sys.argv[2]
settings = runner._settings(None, None, [])
judge = runner._judge_model(settings, "eval_judge_rejudge")
config = load_config(settings.icp_file)
specs = {s.domain: s for s in load_specs(settings.companies_file)}
base = json.loads(src.read_text(encoding="utf-8"))
rows = []
for r in base["rows"]:
    if "variants" not in r:
        continue
    spec = specs[r["domain"]]
    emails = {}
    for v in r["variants"]:
        emails[f"{v['variant']}-final"] = v["email1"]
        if v.get("email1_first") and v["email1_first"] != v["email1"]:
            emails[f"{v['variant']}-first"] = v["email1_first"]
    audit, _ = judges.audit_emails(judge, spec, config, emails, today=settings.research_today().isoformat())
    first_body = r["variants"][0]["email1"].split("\n\n", 1)[1]
    m = re.match(r"\s*(?:Hi|Hello|Dear)\s+([^,\s]+)", first_body)
    baseline_email = judges.generic_template(m.group(1) if m else "there", spec.name, config)
    pref, reason, _ = judges.prefer(
        judge, company=spec.name, title=r["contact"], scout_email=first_body, baseline_email=baseline_email,
        key=r["domain"],
    )
    rows.append({"domain": r["domain"], "variants": r["variants"], "audit": audit, "preference": pref,
                 "preference_reason": reason})
    print(r["domain"], pref, {k: sum(1 for c in v if c.get("supported") is False) for k, v in audit.items()})


def audit_stats(suffix):
    total = bad = 0
    examples = []
    for r in rows:
        for v in r["variants"]:
            key = f"{v['variant']}-{suffix}"
            if suffix == "first" and key not in r["audit"]:
                key = f"{v['variant']}-final"
            for c in r["audit"].get(key, []):
                total += 1
                if c.get("supported") is False:
                    bad += 1
                    examples.append(f"{r['domain']} {key}: {str(c.get('text'))[:100]} ({str(c.get('reason'))[:80]})")
    return {"claims": total, "unsupported": bad, "unsupported_rate": runner.rate(bad, total),
            "examples": examples[:25]}


prefs = runner.counts(r["preference"] for r in rows)
data = {
    "model": base["model"], "judge_model": judge.label, "source": str(src), "accounts": len(rows),
    "independent_audit": {"first_drafts": audit_stats("first"), "final_drafts": audit_stats("final")},
    "preference_vs_template": {**prefs, "scout_win_rate": runner.rate(prefs.get("scout", 0), sum(prefs.values()))},
    "rows": [{k: v for k, v in r.items() if k != "variants"} for r in rows],
}
runner._write(out_name, "drafts_rejudged", data, settings)
