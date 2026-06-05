"""
filter.py — Core TokenAwareFilter

Estimates the token cost of a tool call BEFORE it executes (a PreToolUse
hook) and, when the cost is high, proposes a lower-cost alternative that
preserves the information the agent actually needs.

No LLM calls are made here. Token costs are estimated from file size using
a fixed bytes-per-token heuristic. The class also tracks "actual" usage fed
back via record_actual() so we can measure how accurate the pre-execution
estimate was (RQ1).
"""

import re


def _extract_cat_filename(command: str):
    """Return the filename argument of a `cat <file>` command, else None."""
    # Match `cat foo.py`, `cat ./foo.py`, ignoring extra flags.
    m = re.search(r"\bcat\s+([^\s|;&><]+)", command)
    if not m:
        return None
    candidate = m.group(1)
    # Skip flags like -n
    if candidate.startswith("-"):
        m2 = re.search(r"\bcat\s+(?:-\S+\s+)+([^\s|;&><]+)", command)
        candidate = m2.group(1) if m2 else None
    return candidate


class TokenAwareFilter:
    """
    Estimates token cost of tool calls BEFORE execution.
    Suggests high-quality lower-cost alternatives.
    """

    BYTES_PER_TOKEN = 4  # rough estimate

    def __init__(self, session_budget_tokens: int = 20000):
        self.session_budget = session_budget_tokens
        self.tokens_used = 0
        self.call_log = []  # track all decisions

    # ------------------------------------------------------------------ #
    # Pre-execution check
    # ------------------------------------------------------------------ #
    def check(self, tool_name: str, tool_input: dict,
              file_registry: dict) -> dict:
        """
        file_registry: {filename: content_string}
        Maps filenames to their content for size estimation.

        Returns:
        {
          "decision": "PASS" | "WARN" | "BLOCK",
          "estimated_tokens": int,
          "alternative": dict | None,
          "reason": str,
          "token_savings": int  # if alternative exists
        }
        """
        if tool_name == "read_file":
            path = tool_input.get("path", "")
            return self._size_based(
                path=path,
                file_registry=file_registry,
                warn_alt={"tool": "read_file",
                          "input": {"path": path, "limit_lines": 100}},
                block_alt={"tool": "bash",
                           "input": {"command": None}},  # filled below
                source="read_file",
            )

        if tool_name == "bash":
            command = tool_input.get("command", "")

            # Broad / recursive search: always WARN, suggest scoping it.
            if "grep -r" in command or "find /" in command:
                targeted = self._scope_search(command)
                return {
                    "decision": "WARN",
                    "estimated_tokens": 1500,
                    "alternative": {"tool": "bash",
                                    "input": {"command": targeted}},
                    "reason": ("Unscoped recursive search can return huge "
                               "output; restrict to relevant files."),
                    "token_savings": 1400,
                }

            # `cat <file>`: treat like read_file.
            if re.search(r"\bcat\b", command):
                fname = _extract_cat_filename(command)
                if fname is not None:
                    return self._size_based(
                        path=fname,
                        file_registry=file_registry,
                        warn_alt={"tool": "bash",
                                  "input": {"command": f"head -50 {fname}"}},
                        block_alt={"tool": "bash",
                                   "input": {"command": None}},
                        source="cat",
                    )

            # Any other bash command (targeted grep -n, etc.) is cheap.
            return self._pass_other()

        # Unknown / other tools.
        return self._pass_other()

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #
    def _size_based(self, path, file_registry, warn_alt, block_alt, source):
        content = file_registry.get(path, "")
        size = len(content)
        estimated = size // self.BYTES_PER_TOKEN

        if estimated < 500:
            return {
                "decision": "PASS",
                "estimated_tokens": estimated,
                "alternative": None,
                "reason": f"Small file (~{estimated} tok); read in full.",
                "token_savings": 0,
            }

        if estimated < 2000:
            savings = estimated - 100
            return {
                "decision": "WARN",
                "estimated_tokens": estimated,
                "alternative": warn_alt,
                "reason": (f"Medium file (~{estimated} tok). Reading first "
                           f"100 lines likely suffices."),
                "token_savings": savings,
            }

        # estimated >= 2000  → BLOCK, grep for the function with context.
        func_hint = path.rsplit("/", 1)[-1].rsplit(".", 1)[0]
        block_cmd = f"grep -n -A 10 '{func_hint}' {path}"
        block_alt = {"tool": "bash", "input": {"command": block_cmd}}
        savings = estimated - 50
        return {
            "decision": "BLOCK",
            "estimated_tokens": estimated,
            "alternative": block_alt,
            "reason": (f"Large file (~{estimated} tok). Reading it whole is "
                       f"wasteful; grep for the relevant symbol instead."),
            "token_savings": savings,
        }

    @staticmethod
    def _scope_search(command: str) -> str:
        """Add an --include filter / restrict find scope."""
        if "grep -r" in command:
            if "--include" in command:
                return command
            return command.replace("grep -r", 'grep -r --include="*.py"', 1)
        # find /
        return command.replace("find /", "find . -name '*.py'", 1)

    @staticmethod
    def _pass_other():
        return {
            "decision": "PASS",
            "estimated_tokens": 10,
            "alternative": None,
            "reason": "Non-bulk tool call; negligible token cost.",
            "token_savings": 0,
        }

    # ------------------------------------------------------------------ #
    # Calibration tracking
    # ------------------------------------------------------------------ #
    def record_actual(self, tool_name: str, estimated: int, actual: int):
        """Record real token usage for calibration tracking."""
        self.tokens_used += actual
        self.call_log.append({
            "tool": tool_name,
            "estimated": estimated,
            "actual": actual,
            "error_pct": abs(estimated - actual) / max(actual, 1) * 100,
        })

    def calibration_accuracy(self) -> float:
        """Mean absolute percentage error of estimates."""
        if not self.call_log:
            return 0.0
        errors = [c["error_pct"] for c in self.call_log]
        return sum(errors) / len(errors)
