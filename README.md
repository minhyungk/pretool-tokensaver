# Token-Aware PreToolUse Filter

A PreToolUse hook for Claude Code that intercepts large file reads before they execute, redirects the model to a targeted alternative, and eliminates token waste without degrading task quality.

## Results (SWE-bench-Lite, n = 20, executed)

**97.7% average token reduction** with **zero change in task outcomes** across 40 real Claude Sonnet sessions.

| Metric | No filter | Filter |
|--------|-----------|--------|
| Avg tokens / task | 66,906 | **789** |
| Total tokens (20 tasks) | 1,338,119 | **15,784** |
| Test pass rate | 14 / 20 | **14 / 20** |
| Gold file reached | 18 / 20 | 18 / 20 |
| Sessions over 50k budget | 9 / 20 | **0 / 20** |

Every pass/fail outcome was identical between the two conditions — the filter did not cause a single additional failure. Nine of twenty unfiltered sessions exceeded the 50,000-token context budget and would crash in production; all filtered sessions stayed within budget.

---

## Problem

AI coding agents waste tokens reading large files unnecessarily. Before writing a single line of code, an agent may read entire modules, build logs, and test suites — most of which are irrelevant to the fix. Once the context window fills, the session crashes.

In our evaluation, a single unfiltered task consumed **252,394 tokens** (5× the 50k session budget) reading one large source file.

## Approach

Intercept tool calls **before** they execute via a `PreToolUse` hook. Estimate token cost from file size (`bytes // 4`), classify as **PASS / WARN / BLOCK**, and offer a targeted alternative that still surfaces the relevant content.

| Decision | Est. tokens | Action |
|----------|-------------|--------|
| **PASS** | < 500 | Allow — read in full |
| **WARN** | 500 – 2,000 | Allow + suggest `head -100` |
| **BLOCK** | ≥ 2,000 | Reject + suggest scoped `grep` with 10 lines of context |

On a BLOCK the hook exits with code 2 — Claude Code treats this as a tool failure, feeds the rejection message back to the model, and the model re-issues a cheaper call automatically.

---

## Files

| File | Purpose |
|------|---------|
| `filter.py` | `TokenAwareFilter` — the PreToolUse check and calibration tracking |
| `filter_hook.py` | Claude Code adapter: reads JSON stdin, calls `filter.check()`, handles PASS/WARN/BLOCK |
| `setup_hook.py` | Install, uninstall, and inspect the hook |
| `tasks.py` | Loads HumanEvalPack bugs; falls back to an embedded task set if offline |
| `harness.py` | Runs each task baseline vs. filtered (Phase 0 simulation) |
| `results.py` | Renders benchmark table and summary |
| `swe_benchmark.py` | Phase 2 SWE-bench-Lite evaluation harness |
| `swe_results.py` | Renders per-instance table from `swe_results.json` |
| `swe_results.json` | Summary metrics from the 40-run executed evaluation |
| `predictions/` | Full model-generated patches (JSONL) + per-instance `patch.diff` and `report.json` from the SWE-bench Docker harness |

---

## Phase 0 — Simulation Harness

Runs a deterministic 5-step bug-fix workflow (read source → read build log → read module context → grep → cat test) with and without the filter, using real or embedded task data. No API calls needed.

```bash
pip install datasets
python harness.py
```

**Simulation results (embedded fallback set):**

```
Avg token reduction:     95.2%
Quality preserved:       20/20 tasks
Alternatives triggered:  40 (20 WARNs, 20 BLOCKs)
Calibration MAPE (RQ1):  8.0% over 80 file reads
```

---

## Phase 1 — Claude Code Hook Integration

Wires `filter.py` into Claude Code's PreToolUse hook so the filter runs on every real `Read` or `Bash` call. State persists across hook invocations via `/tmp/aegis_hook.jsonl` and `/tmp/aegis_state.json`.

```bash
python setup_hook.py install     # register hook in .claude/settings.json
python setup_hook.py status      # install state + live session token totals
python setup_hook.py log         # last 10 decisions
python setup_hook.py reset       # rotate log + clear session state
python setup_hook.py uninstall   # remove the hook
```

Verified live: in a real headless `claude -p` session the hook fired, blocked a ~21k-token `Read` (exit 2), the model received the feedback, and decisions accumulated in `/tmp/aegis_hook.jsonl`.

---

## Phase 2 — SWE-bench-Lite Evaluation

Paired within-subject design: each of 20 SWE-bench-Lite instances run twice (with and without filter) against the same repo checkout and issue description. See [Results](#results-swe-bench-lite-n--20-executed) above.

```bash
python swe_benchmark.py --n 20              # deterministic simulation (no API/Docker needed)
python swe_benchmark.py --n 20 --execute    # real paired claude -p sessions
python swe_benchmark.py --n 20 --execute --run-tests   # + SWE-bench Docker tests
```

---

## Research Questions

**RQ1 — How accurately can we predict token cost before execution?**
`bytes // 4` achieves ~8% MAPE vs. ground-truth token counts — accurate enough to drive reliable PASS/WARN/BLOCK gating.

**RQ2 — How much can we reduce tokens without quality loss?**
97.7% reduction with identical pass/fail outcomes on all 20 instances.

**RQ3 — Which tool-call patterns are the biggest offenders?**
Bulk reads of large source files and build logs dominate waste. Targeted greps and small source reads are negligible.

---

## Design Notes (from live testing)

Two issues surfaced during Phase 1 testing — tracked but not fixed under the PoC constraint that `filter.py` cannot be modified:

1. A BLOCK's suggested `grep` should target a path the agent's sandbox permits.
2. The size rule should honor `limit` / partial-read arguments instead of blocking them outright.

---

## Limitations

**Scale & generalizability**
- n=20 out of 300 SWE-bench-Lite instances; results may not be representative
- Only two repos tested (`astropy`, `django`), both Python — behavior on other languages or monorepos is unknown
- The 6 instances that fail in both conditions may share a root cause unrelated to the filter, but this cannot be ruled out at current scale

**Token savings ≠ cost savings**
- The filter reduces *input* tokens only. Output tokens (patch text, re-reasoning after a BLOCK) are unaffected and may increase slightly — the cost reduction will be proportionally smaller than the 97.7% input-token reduction
- Each tool call spawns a Python subprocess for the hook — this adds per-call latency overhead that is not captured in token metrics
- Token savings do not directly relieve API rate limits. Jimenez et al. (2025) show that in agentic coding tasks rate limits and token costs are distinct constraints — a session that reads fewer tokens may still exhaust request-per-minute or time-window limits at the same rate ([arXiv:2604.22750](https://arxiv.org/abs/2604.22750)). The same work reports up to 30× token variance across runs of identical tasks, suggesting our 40-run baseline averages may not be stable across repetitions.

**Filter design**
- The BLOCK threshold (2,000 tokens) was chosen by hand and not tuned per project type
- The `grep` alternative is only useful when the model already knows what symbol to search for; during early exploration it may not, forcing extra round-trips
- Partial reads (`Read` with a `limit` argument) are evaluated against the full file size and may be blocked unnecessarily

**Relationship to prompt caching**
The filter and prompt caching are complementary, not competing. Caching reduces the cost of content *already in context* on repeated API calls; the filter prevents large content from entering context at all. In practice, most large-file reads in agentic sessions are one-time events with no prior cache entry, and each SWE-bench instance runs in a fresh session. The two mechanisms operate at different pipeline stages and can be combined for greater savings.

**Statistical rigor**
- No significance testing was performed; the 14/20 pass rate agreement could be coincidental at this sample size
- The single missing patch (`has_patch` 19/20 vs 20/20) may represent a real 5% productivity regression, not noise

---

## Requirements

- Python 3.10+
- `datasets` (for HumanEvalPack / SWE-bench-Lite loading; not needed for embedded fallback)
- No LLM calls in Phase 0; Claude Code + API key for Phase 1 / Phase 2 `--execute`
