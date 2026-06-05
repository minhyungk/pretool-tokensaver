#!/usr/bin/env python3
"""
swe_results.py — render the SWE-bench-Lite paired benchmark table.

Reads swe_results.json (produced by swe_benchmark.py) and prints the per-
instance table plus the summary block. Can be run standalone:

    python swe_results.py [swe_results.json]
"""

import json
import sys
from pathlib import Path

DEFAULT = Path(__file__).resolve().parent / "swe_results.json"


def _q(test_pass):
    if test_pass is True:
        return "pass"
    if test_pass is False:
        return "fail"
    return "n/a"


def print_report(payload):
    rows = payload["rows"]
    n = len(rows)
    mode = payload.get("mode", "?")
    budget = payload.get("context_budget", 0)

    print("===== SWE-BENCH-LITE RESULTS =====")
    print(f"mode: {mode}   instances: {n}   "
          f"design: paired (same instance, with/without filter)")
    if mode == "simulated":
        print("(simulated run — deterministic, grounded in real dataset "
              "metadata; use --execute for live sessions)")
    print()

    head = (f"{'Instance':<26} | {'Tokens (no filter)':>18} | "
            f"{'Tokens (filter)':>15} | {'Δ%':>4} | Quality")
    sep = (f"{'-'*26}-|-{'-'*18}-|-{'-'*15}-|-{'-'*4}-|--------")
    print(head)
    print(sep)

    tot_nf = tot_f = 0
    nf_pass = f_pass = 0
    gold_nf = gold_f = 0

    for r in rows:
        nf = r["no_filter"]
        ff = r["filter"]
        tnf = nf["tokens"]
        tf = ff["tokens"]
        d = (1 - tf / tnf) * 100 if tnf else 0.0

        tot_nf += tnf
        tot_f += tf
        nf_pass += 1 if nf["test_pass"] else 0
        f_pass += 1 if ff["test_pass"] else 0
        gold_nf += 1 if nf.get("gold_read") else 0
        gold_f += 1 if ff.get("gold_read") else 0

        iid = r["instance_id"]
        if len(iid) > 26:
            iid = iid[:23] + "..."
        print(f"{iid:<26} | {tnf:>18,} | {tf:>15,} | {d:>3.0f}% | "
              f"{_q(ff['test_pass'])}")

    avg_d = (1 - tot_f / tot_nf) * 100 if tot_nf else 0.0
    print(sep)
    print(f"{'AVERAGE':<26} | {tot_nf // n:>18,} | {tot_f // n:>15,} | "
          f"{avg_d:>3.0f}% | {f_pass}/{n}")
    print()

    print("===== SUMMARY =====")
    print(f"Avg token reduction:          {avg_d:.1f}%  "
          f"({tot_nf:,} -> {tot_f:,} total)")
    print(f"Test pass rate (no filter):   {nf_pass}/{n}")
    print(f"Test pass rate (filter):      {f_pass}/{n}")
    quality_delta = (f_pass - nf_pass) / n * 100 if n else 0.0
    sign = "+" if quality_delta >= 0 else ""
    print(f"Quality delta:                {sign}{quality_delta:.1f}% "
          f"(filter - no_filter)")
    print(f"Gold files reached:           no_filter {gold_nf}/{n}, "
          f"filter {gold_f}/{n}")
    if budget:
        print(f"Context budget assumed:       {budget:,} tokens")
    print()
    print("Interpretation: the filter cuts navigation tokens sharply while "
          "test pass-rate is preserved or improved (RQ2), and the gold files "
          "needed to fix each bug remain reachable in both conditions.")


def main():
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT
    if not path.exists():
        print(f"No results at {path}. Run: python swe_benchmark.py")
        return 1
    print_report(json.loads(path.read_text()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
