#!/usr/bin/env python3
"""
filter_hook.py — Claude Code PreToolUse hook adapter for TokenAwareFilter.

Claude Code invokes this once per tool call, passing a JSON payload on stdin:

    {
      "session_id": "...",
      "cwd": "/abs/path",
      "hook_event_name": "PreToolUse",
      "tool_name": "Read" | "Bash" | "Edit" | ...,
      "tool_input": { "file_path": "..." } | { "command": "..." } | {...}
    }

We translate that payload into the vocabulary filter.py already understands
(read_file / bash, path / command), build a file_registry from the *real*
filesystem so size estimates are real, run filter.check(), then:

  * BLOCK  -> print reason to stderr and exit(2)  (Claude Code aborts the call
             and feeds stderr back to the model)
  * WARN   -> allow, but surface the cheaper alternative as additionalContext
  * PASS   -> allow silently

filter.py is used as-is and never modified.

Cross-process state (each hook call is a fresh process) is kept in two files:
  /tmp/aegis_hook.jsonl   append-only log of every decision
  /tmp/aegis_state.json   running per-session token accounting
"""

import json
import os
import sys
import time
from pathlib import Path

# Make filter.py importable regardless of Claude Code's cwd.
HOOK_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(HOOK_DIR))

from filter import TokenAwareFilter, _extract_cat_filename  # noqa: E402

LOG_PATH = "/tmp/aegis_hook.jsonl"
STATE_PATH = "/tmp/aegis_state.json"

# Claude Code tool name -> (filter tool name, input-key mapping)
READ_TOOLS = {"Read"}
BASH_TOOLS = {"Bash"}


# --------------------------------------------------------------------------- #
# State persistence
# --------------------------------------------------------------------------- #
def load_state():
    try:
        with open(STATE_PATH) as f:
            return json.load(f)
    except Exception:
        return _empty_state(None)


def _empty_state(session_id):
    return {
        "session_id": session_id,
        "session_tokens": 0,
        "baseline_tokens": 0,   # what an unfiltered agent would have spent
        "calls": 0,
        "passes": 0,
        "warns": 0,
        "blocks": 0,
        "started_at": time.time(),
    }


def save_state(state):
    tmp = STATE_PATH + ".tmp"
    with open(tmp, "w") as f:
        json.dump(state, f, indent=2)
    os.replace(tmp, STATE_PATH)


# --------------------------------------------------------------------------- #
# Payload -> filter vocabulary
# --------------------------------------------------------------------------- #
def map_tool(cc_tool, cc_input):
    """Return (filter_tool_name, filter_tool_input)."""
    if cc_tool in READ_TOOLS:
        path = cc_input.get("file_path") or cc_input.get("path") or ""
        return "read_file", {"path": path}
    if cc_tool in BASH_TOOLS:
        return "bash", {"command": cc_input.get("command", "")}
    # Anything else falls through filter.check()'s "other tools" -> PASS.
    return cc_tool, cc_input


def _resolve(path, cwd):
    p = Path(path)
    if not p.is_absolute() and cwd:
        p = Path(cwd) / p
    return p


def build_registry(filter_tool, filter_input, cwd):
    """Read real files referenced by the call so size estimates are real.

    Keys MUST match what filter.py looks up:
      * read_file -> tool_input["path"]   (the original path string)
      * bash cat  -> _extract_cat_filename(command)
    """
    registry = {}

    def add(key, fs_path):
        try:
            fp = _resolve(fs_path, cwd)
            if fp.is_file():
                registry[key] = fp.read_text(errors="replace")
        except Exception:
            pass

    if filter_tool == "read_file":
        path = filter_input.get("path", "")
        if path:
            add(path, path)
    elif filter_tool == "bash":
        fname = _extract_cat_filename(filter_input.get("command", ""))
        if fname:
            add(fname, fname)
    return registry


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #
def main():
    raw = sys.stdin.read()
    try:
        payload = json.loads(raw)
    except Exception:
        # Never break the agent on a malformed payload.
        sys.exit(0)

    cc_tool = payload.get("tool_name", "")
    cc_input = payload.get("tool_input", {}) or {}
    cwd = payload.get("cwd") or os.getcwd()
    session_id = payload.get("session_id")

    filter_tool, filter_input = map_tool(cc_tool, cc_input)
    registry = build_registry(filter_tool, filter_input, cwd)

    filt = TokenAwareFilter()
    result = filt.check(filter_tool, filter_input, registry)
    decision = result["decision"]
    estimated = result["estimated_tokens"]
    savings = result.get("token_savings", 0)

    # Shadow mode (AEGIS_SHADOW=1): observe-only. We still compute and log the
    # decision the filter WOULD have made, but we never block/redirect and we
    # count the FULL token cost. This is how the SWE-bench no-filter baseline
    # is measured — the agent reads everything, and we record what it spent.
    shadow = os.environ.get("AEGIS_SHADOW") == "1"

    # Cost actually counted: cheaper alternative on WARN/BLOCK, full on PASS.
    if shadow or decision == "PASS":
        counted = estimated
    else:
        counted = max(estimated - savings, 0)

    # ---- update per-session accounting -----------------------------------
    state = load_state()
    if session_id is not None and state.get("session_id") != session_id:
        state = _empty_state(session_id)
    state["session_id"] = session_id
    state["calls"] += 1
    state["session_tokens"] += counted
    state["baseline_tokens"] += estimated
    state[{"PASS": "passes", "WARN": "warns", "BLOCK": "blocks"}[decision]] += 1
    save_state(state)

    # ---- append decision to the audit log --------------------------------
    entry = {
        "ts": time.time(),
        "session_id": session_id,
        "cwd": cwd,
        "cc_tool": cc_tool,
        "filter_tool": filter_tool,
        "tool_input": filter_input,
        "decision": decision,
        "estimated_tokens": estimated,
        "counted_tokens": counted,
        "token_savings": savings,
        "reason": result["reason"],
        "alternative": result["alternative"],
        "session_tokens_total": state["session_tokens"],
        "baseline_tokens_total": state["baseline_tokens"],
        "shadow": shadow,
    }
    try:
        with open(LOG_PATH, "a") as f:
            f.write(json.dumps(entry) + "\n")
    except Exception:
        pass  # logging must never crash the agent

    # ---- enforce decision -------------------------------------------------
    if shadow:
        # Observe-only: allow everything, regardless of decision.
        sys.exit(0)

    if decision == "BLOCK":
        alt = result.get("alternative") or {}
        alt_cmd = (alt.get("input") or {})
        sys.stderr.write(f"[aegis] BLOCKED {cc_tool}: {result['reason']}\n")
        if alt_cmd:
            sys.stderr.write(
                f"[aegis] Use this instead: {json.dumps(alt_cmd)}\n")
        sys.stderr.write(
            f"[aegis] Est. {estimated} tok -> {counted} tok "
            f"(saves ~{savings}).\n")
        sys.exit(2)  # block the call; stderr is fed back to the model

    if decision == "WARN":
        # Non-blocking: nudge the model via additionalContext on stdout.
        alt = result.get("alternative") or {}
        ctx = (f"[aegis] {result['reason']} Cheaper option: "
               f"{json.dumps(alt.get('input', {}))} "
               f"(~{estimated}->{counted} tok).")
        print(json.dumps({
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "additionalContext": ctx,
            }
        }))
        sys.exit(0)

    # PASS
    sys.exit(0)


if __name__ == "__main__":
    main()
