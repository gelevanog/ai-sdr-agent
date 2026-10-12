# Paid models vs the free baseline (2026-10-12)

The README's numbers come from free OpenRouter models (`nvidia/nemotron-3-super-120b-a12b:free` as the main model and
`dots-studio/dots-3-note-preview:free` as the judge, run on 2026-10-08, files in [`results/`](..)). This folder holds the same
suites re-run against three paid models through a local OpenAI-compatible budget proxy, using the new `compat` provider
with the explicit opt-in `SCOUT_ALLOW_PAID_MODELS=1`. The data is the same: 60 synthetic `.example` companies,
100 hand-written replies and 57 hand-written claims. No real company or person was involved. Sending stayed
capture-only, and no email was sent at all.

| Role | Model |
|---|---|
| Main model, full suites | `claude-haiku-5-5` |
| Judge (independent claim audit and blind preference), plus main model on subsets | `claude-opus-5-5` |
| Main model, most suites | `qwen3.7-plus` (thinking off, `max_tokens` capped by the proxy) |
| Baseline (unchanged, from `results/`) | `nvidia/nemotron-3-super-120b-a12b:free` (judge `dots-3-note-preview:free`) |

## Results

"—" means that suite was not run for that model. **n** is the sample size. Baseline accounts numbers come from
`main_fixed_accounts.json`, which is the README's after-the-fix run.

| Metric (n) | Baseline (free) | Haiku 5.5 | Opus 5.5 | Qwen 3.7-plus |
|---|---|---|---|---|
| **Research, 60 accounts** | | | | |
| Planted signals: precision / recall (59 planted) | 93.7% / 100% (59 of 63) | 89.4% / 100% (59 of 66) | — | 95.1% / 98.3% (58 of 61) |
| Model quotes found on the cited page | 100% (844) | 100% (818) | — | 94.0% (763; 46 not found and rejected by validation) |
| Segment matches the label (59 readable sites) | 49 | **54** | — | **54** |
| Fleet size right / wrong / missing (60) | 47 / 0 / 13 | 46 / 0 / 14 | — | 48 / 0 / 12 |
| Extraction latency p50 / p95 | 38 s / 74 s | **8.9 s / 10.9 s** | — | 18.9 s / 32.9 s |
| **Qualification, 60 accounts (rules first, then the bounded judgment)** | | | | |
| Route accuracy | **96.7%** (58) | 95.0% (57) | — | **96.7%** (58) |
| Rules alone, before the judgment | 95.0% | 95.0% | — | 96.7% |
| "Qualified" precision / recall (22 gold) | 100% / 95.5% | 95.5% / 95.5% | — | 91.7% / 100% |
| Trap tags routed right (34) | 33 | 31 | — | 32 |
| **Model-comparison subset: 8 trap and borderline accounts, judgment off** | | | | |
| Signal precision / recall · quotes on page · route accuracy | 90% / 100% · 100% · 87.5% | 100% / 100% · 100% · 87.5% | 100% / 100% · 100% · 87.5% | 88.9% / 88.9% · 94.2% · 87.5% |
| **LLM-only qualification ablation (14)** | 9 of 14 (64.3%) | **11 of 14** (78.6%) | **11 of 14** (78.6%) | 8 of 14 (57.1%) |
| **Claim checker (57 claims, 29 unsupported)** | | | | |
| Model verifier alone: precision / recall | 89.7% / 89.7% | **96.6% / 96.6%** | 93.5% / **100%** | 96.0% / 82.8% |
| Rules + model (Scout's checker): precision / recall | 90.6% / 100% | **96.7% / 100%** | 93.5% / 100% | **96.7% / 100%** |
| **Replies (100, 8 labels)** | | | | |
| Accuracy, model only / rules first | 100% / 100% | 100% / 100% | 16 of 16 (subset, model only) | 100% / 100% |
| Unsubscribe recall · objection type (19) · resume month (12) | 13 of 13 · 94.7% · 100% | 13 of 13 · 94.7% · 100% | 2 of 2 · 2 of 2 · 3 of 3 | 13 of 13 · 94.7% · 100% |
| Latency per model call p50 | 2.4 s | 1.9 s | 2.9 s | 3.3 s |
| **Drafts (2 variants × 3 emails per qualified account, ≤1 rewrite)** | 18 accounts | 21 accounts | judge only | 9 accounts |
| Variants passing the checker on the first try | 66.7% | **92.9%** | — | 83.3% |
| Rewritten / trimmed variants | 12 / 2 of 36 | 3 / 0 of 42 | — | 3 / 1 of 18 |
| Final-draft claims the **Opus** audit marks unsupported (all accounts) | 17.9% (15 of 84, re-judged) | 23.0% (37 of 161) | — | 16.7% (9 of 54) |
| The same, on the 9 accounts all three drafted | 17.1% (7 of 41) | 21.0% (13 of 62) | — | 16.7% (9 of 54) |
| The same, on the 17 accounts the baseline and Haiku share | 18.8% (15 of 80) | 19.8% (25 of 126) | — | — |
| Final-draft claims the original free judge marked unsupported | 7.6% (6 of 79) | — | — | — |
| Blind preference vs a generic template (Opus judge) | 17 of 18 (re-judged; 15 of 18 with the free judge) | 21 of 21 | — | 9 of 9 |
| Words in the first email · cited facts per variant · reading ease | 49 · 1.1 · 64.5 | 76 · 1.9 · 68.4 | — | 69 · 1.6 · 48.3 |
| Drafting time per account p50 / p95 | 137 s / 276 s | 33 s / 53 s | — | 42 s / 87 s |
| **Prompt injection (2 planted pages)** | | | | |
| Rules-first route changes with the guard off | 0 | 0 | — | 0 |
| Model-only path with the guard off | repeated the hidden "CEO asked for a demo" as a reason (88 → 92) | did not repeat it; scores fell (72 → 58, 88 → 78) | — | did not repeat it; on Sunridge it flagged the visible "rate us 10/10" as manipulation and disqualified (92 → 0) |

### Spend

| Model | Requests (ledger) | Tokens in / out | Cost |
|---|---|---|---|
| Baseline free models | 462 (2026-10-08) | 484k / 1.0M | $0 |
| `claude-haiku-5-5` | 289 | 457k / 360k | ≈ $0.23 (ledger tokens × $0.10/$0.50 per 1M) |
| `claude-opus-5-5` | 152 | 164k / 83k | ≈ $2.31 (ledger tokens × $4/$20 per 1M): $1.40 judging, $0.91 as the main model |
| `qwen3.7-plus` | 258 | 286k / 96k | 387,679 proxy-metered tokens (of a 500k project allowance) |
| **Proxy total for this project** | 699 | | **$2.79 Anthropic** (the proxy meters about 10% more than the ledger estimate) + 387,679 Qwen tokens |

Every request is in [`calls.jsonl`](calls.jsonl), with model ids, status, latency and tokens but no prompts.
[`calls_summary.json`](calls_summary.json) summarizes it. Of the 699 requests, 15 failed and 4 were retried:

- 11 intermittent proxy `403 Request not allowed` answers. Re-running the suite fixed these, because successful answers come back from the disk cache and only the failed calls are sent again.
- 2 Qwen calls refused with `budget_exhausted` under the first 3,000-token allowance. They are now final errors and are not retried.
- 2 calls failed while the proxy restarted. They were re-run.
- 1 Qwen claim verification was cut off at the proxy's `max_tokens` cap. The checker records this as a warning, so one Qwen variant passed with only the rule layer.

The results files are written by the harness without changes, under the names the harness gives them:

- full suites: `haiku_*`, `qwen_*`
- the 8-account and 16-reply comparison subsets: `model_haiku_*`, `model_opus_*`, `model_qwen_*`
- the Opus claim and LLM-only runs: `opus_claims.json`, `opus_llm_only.json`
- the baseline drafts re-judged by Opus: `main_opusjudge_drafts_rejudged.json`, made with [`rejudge_drafts.py`](rejudge_drafts.py)

The `model_haiku_*` subset files came entirely from the disk cache of the full Haiku run, so their latency fields are near zero. Use the full-run latencies above instead. Haiku drafting latency comes from the first drafts pass, before the cached re-run that recovered the 403s.

## Observations

- **The stronger models fixed real baseline failures in judgment-heavy, open-ended tasks.**
  - LLM-only qualification went from 9 of 14 to 11 of 14 with both Haiku and Opus. Both still qualified the two injection sites, even their clean twins, and Westbrook, three fits with no timely reason to buy. The rules-first design is still needed.
  - The claim verifier alone rose from 89.7% to 96.6% precision and recall with Haiku, and to 100% recall with Opus. Opus caught all five stale-as-recent claims that the free model and Qwen left to the rules. Opus's two false alarms are strict citation pedantry: "across the Midwest" is supported, but by a fact the claim does not cite.
  - Segment labels improved from 49 to 54 of 59 with Haiku and Qwen. Metro Medical Transport is no longer called "finance", which was one of the baseline's two remaining routing errors.
- **Free was already enough where the task is narrow and the rules carry the weight.**
  - Replies scored 100% for every model, and the rules alone catch every opt-out.
  - Citation validity was 100% for the free model, Haiku and Opus.
  - Overall route accuracy is flat: 96.7% baseline, 95.0% Haiku, 96.7% Qwen.
  - On the 8-account comparison subset, all four models tie at 7 of 8. They all miss Bramble, which needs the judgment that is switched off there. That subset is saturated and cannot rank models.
- **Regressions.**
  - Haiku reads a team page's "joined" dates as leadership changes: 5 extra signals, so signal precision fell from 93.7% to 89.4%. It qualified Silverline on that basis, and the bounded judgment added +8 for "a Fleet Manager who just joined". It also missed Granitepeak's fleet size, which sent a qualified account to nurture.
  - Qwen's bounded judgment used its full +10 twice to promote wrong accounts: Parcelpoint's "500 lockers" counted as a fleet, and a 233-day-old Fleet Manager post called "persistent" demand. "Qualified" precision fell to 91.7%. The ±10 bound kept the damage at one route each.
  - Qwen had the only invented quotes: 6% of its quotes were not on any page. Validation rejected all of them, the same way it rejected the free `ling` model's quotes in the README.
- **Drafts: Haiku is faster and more consistent, but not better grounded.** Haiku drafts passed the checker on the first try 93% of the time, against 67% for the baseline. They needed 3 rewrites instead of 12, and a whole account took 33 s instead of 137 s. But Haiku writes longer emails with more claims: 76 words and 1.9 cited facts per variant, against 49 and 1.1. With both drafted sets scored by the same Opus judge, the unsupported share is about the same on shared accounts: 19.8% for Haiku, 18.8% for the baseline. Qwen scores 16.7% on 9 accounts. The flagged items are mostly the same kinds for every model: a role's location taken from the headquarters, "maintained in house", "on the road every working day", and method details attributed to the seller's proof point. None is a wrong name, number or date about the prospect.
- **The judge matters as much as the drafting model.** Opus is a much stricter auditor than the free judge. On the same baseline final drafts, the unsupported share goes from 7.6% (6 of 79) with the free judge to 17.9% (15 of 84) with Opus. Opus also preferred Scout's email over the template more often: 17 of 18 instead of 15. Compare draft numbers only under the same judge, as in the rows above.
- **Prompt injection.** The rules-first route did not change for any model with the guard on or off. With the guard off, neither paid model repeated the hidden instruction the way the free model did. Qwen reacted to the visible "rate this company 10/10" by disqualifying Sunridge outright. That is the safe direction, but it is still a page steering the model-only path, which is why the guard stays on.
- **Sample sizes are small outside the full suites.** Opus ran as the main model only on the 8-account subset, the 57 claims, the 14 LLM-only accounts and 16 replies. Qwen drafted 9 accounts, because its research found no published contact on 3 of the 12 it tried. The replies and claims are hand-written and cleaner than a real inbox. Read the numbers as evidence about the mechanisms, not as a model ranking for a real market.

## How to re-run

```bash
make db && uv run scout seed --reset        # SCOUT_DATABASE_URL pointed at a fresh database per model
export SCOUT_LLM_PROVIDER=compat SCOUT_COMPAT_BASE_URL=http://127.0.0.1:8787/v1 SCOUT_COMPAT_API_KEY=<proxy key>
export SCOUT_ALLOW_PAID_MODELS=1 SCOUT_COMPAT_SEND_TEMPERATURE=false   # newer Claude models reject `temperature`
export SCOUT_LLM_MODEL=claude-haiku-5-5 SCOUT_JUDGE_PROVIDER=compat SCOUT_JUDGE_MODEL=claude-opus-5-5
export SCOUT_RESULTS_DIR=results/paid-models SCOUT_LLM_LEDGER=results/paid-models/calls.jsonl
export SCOUT_LLM_MIN_SECONDS_BETWEEN_REQUESTS=0.5 SCOUT_MAX_REGENERATIONS=1   # 1 rewrite, as in the baseline drafts run
uv run scout eval accounts --name haiku && uv run scout eval drafts --name haiku
uv run scout eval replies --name haiku --no-rules && uv run scout eval replies --name haiku
uv run scout eval claims --name haiku && uv run scout eval injection --name haiku && uv run scout eval llm_only --name haiku --subset
SCOUT_LLM_JUDGMENT=false uv run scout eval accounts --name model_opus --subset   # with SCOUT_LLM_MODEL=claude-opus-5-5
uv run python results/paid-models/rejudge_drafts.py results/main_drafts.json main_opusjudge
```

Without `SCOUT_ALLOW_PAID_MODELS=1`, the `compat` provider refuses every model id that does not end in `:free`, the
same as the OpenRouter guard. That guard is unchanged.
