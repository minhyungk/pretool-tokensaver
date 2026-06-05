"""
tasks.py — Load HumanEvalPack bug-fix tasks and synthesize, for each one,
the realistic sequence of tool calls a coding agent would make while fixing
the bug, plus the in-memory "file registry" those calls read from.

Primary path uses bigcode/humanevalpack (HuggingFace). If `datasets` is not
installed or the dataset cannot be fetched (e.g. offline), we fall back to a
small embedded set of tasks so the benchmark still runs deterministically.
"""

N_TASKS = 20


def load_tasks(n: int = N_TASKS) -> list:
    """Return the first `n` Python tasks from HumanEvalPack (or a fallback)."""
    try:
        from datasets import load_dataset
        ds = load_dataset(
            "bigcode/humanevalpack",
            "python",
            split="test",
            trust_remote_code=True,
        )
        return list(ds)[:n]
    except Exception as e:  # offline / no datasets / network failure
        print(f"[tasks] Falling back to embedded tasks ({type(e).__name__}: {e})")
        return _fallback_tasks(n)


def make_agent_workflow(task: dict):
    """
    Returns (tool_calls, file_registry).

    Simulates a realistic agent file-access pattern for fixing one bug:
    a small source file, a bloated build log, a medium module-context file,
    a targeted grep, and reading the test file.
    """
    buggy_code = task["buggy_solution"]
    func_name = task["entry_point"]

    file_registry = {
        f"{func_name}.py": buggy_code,
        f"{func_name}_test.py": task["test"],
        # ~4000 tokens of mostly-irrelevant log noise around one error line.
        "build.log": ("INFO: build started\n" * 500
                      + f"ERROR: {func_name} failed\n"
                      + "INFO: ...\n" * 500),
        # Medium-sized "module context" (the function repeated 20x).
        "module_context.py": buggy_code * 20,
    }

    tool_calls = [
        # Step 1: read the buggy file (small, fine).
        {"tool": "read_file",
         "input": {"path": f"{func_name}.py"}},

        # Step 2: read the full build log (EXPENSIVE).
        {"tool": "read_file",
         "input": {"path": "build.log"}},

        # Step 3: read the full module context (MEDIUM).
        {"tool": "read_file",
         "input": {"path": "module_context.py"}},

        # Step 4: run a targeted grep (cheap).
        {"tool": "bash",
         "input": {"command": f"grep -n '{func_name}' {func_name}.py"}},

        # Step 5: cat the test file (medium).
        {"tool": "bash",
         "input": {"command": f"cat {func_name}_test.py"}},
    ]

    return tool_calls, file_registry


# ---------------------------------------------------------------------- #
# Fallback tasks (used only if HuggingFace dataset is unavailable)
# ---------------------------------------------------------------------- #
def _fallback_tasks(n: int) -> list:
    base = []
    for i in range(max(n, N_TASKS)):
        func = f"solve_{i}"
        buggy = (
            f"def {func}(xs):\n"
            f"    \"\"\"Return the sum of xs (BUG: starts at 1).\"\"\"\n"
            f"    total = 1\n"
            f"    for x in xs:\n"
            f"        total += x\n"
            f"    return total\n"
        )
        test = (
            f"def check({func}):\n"
            f"    assert {func}([1, 2, 3]) == 6\n"
            f"    assert {func}([]) == 0\n"
        )
        base.append({
            "task_id": f"HumanEval/{i}",
            "buggy_solution": buggy,
            "prompt": f"def {func}(xs):\n    \"\"\"Return the sum of xs.\"\"\"\n",
            "test": test,
            "entry_point": func,
        })
    return base[:n]
