"""
results.py — Results collector and printer.

Renders the per-task benchmark table and the summary block from the rows
produced by harness.run_task().
"""


def print_report(rows, filt):
    """
    rows: list of (task_id, baseline_result, filtered_result)
    filt: the TokenAwareFilter (for calibration accuracy + budget).
    """
    n = len(rows)

    print("===== TOKEN-AWARE PREFILTER BENCHMARK =====")
    print(f"Dataset: HumanEvalPack Python ({n} tasks)")
    print()

    header = f"{'Task ID':<16} | {'Baseline':>8} | {'Filtered':>8} | " \
             f"{'Reduction':>9} | Quality"
    sep = f"{'-'*16}-|-{'-'*8}-|-{'-'*8}-|-{'-'*9}-|--------"
    print(header)
    print(sep)

    tot_base = tot_filt = 0
    quality_ok = 0
    total_warns = total_blocks = total_alts = 0

    for task_id, base, filt_res in rows:
        b = base["total_tokens"]
        f = filt_res["total_tokens"]
        red = (1 - f / b) * 100 if b else 0.0
        ok = filt_res["quality_preserved"]
        quality_ok += 1 if ok else 0
        mark = "OK " if ok else "DEG"

        tot_base += b
        tot_filt += f
        total_warns += filt_res["warned"]
        total_blocks += filt_res["blocked"]
        total_alts += filt_res["alternatives_used"]

        print(f"{task_id:<16} | {b:>8,} | {f:>8,} | {red:>8.1f}% |  {mark}")

    avg_base = tot_base / n if n else 0
    avg_filt = tot_filt / n if n else 0
    avg_red = (1 - tot_filt / tot_base) * 100 if tot_base else 0.0

    print(sep)
    print(f"{'AVERAGE':<16} | {avg_base:>8,.0f} | {avg_filt:>8,.0f} | "
          f"{avg_red:>8.1f}% |  {quality_ok}/{n}")
    print()

    print("===== SUMMARY =====")
    print(f"Avg token reduction:     {avg_red:.1f}%")
    print(f"Quality preserved:       {quality_ok}/{n} tasks")
    print(f"Alternatives triggered:  {total_alts} times")
    print(f"  - WARNs:               {total_warns}")
    print(f"  - BLOCKs:              {total_blocks}")

    budget = filt.session_budget
    # Tokens saved per session vs. fraction of budget reclaimed.
    saved = tot_base - tot_filt
    avg_saved_per_task = saved / n if n else 0
    budget_pct = avg_saved_per_task / budget * 100 if budget else 0.0
    print(f"Estimated cost saving:   {budget_pct:.1f}% of session budget "
          f"(~{avg_saved_per_task:,.0f} tok/task, budget {budget:,})")
    print()

    if filt.call_log:
        mape = filt.calibration_accuracy()
        print("===== CALIBRATION (RQ1) =====")
        print(f"Pre-execution estimate MAPE: {mape:.1f}% "
              f"over {len(filt.call_log)} measured calls")
        print(f"(lower is better; estimate vs. deterministic ground truth)")
