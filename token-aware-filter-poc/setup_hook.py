#!/usr/bin/env python3
"""
setup_hook.py — install / inspect the Aegis PreToolUse hook in Claude Code.

Usage:
    python setup_hook.py install     register the hook in .claude/settings.json
    python setup_hook.py uninstall   remove the hook
    python setup_hook.py status      show install state + live session tokens
    python setup_hook.py reset       clear /tmp/aegis_state.json (+ rotate log)
    python setup_hook.py log         print the last 10 decisions

The hook is registered at the project level (.claude/settings.json in the repo
root) with an absolute path to filter_hook.py, matched on Read and Bash.
"""

import json
import os
import sys
from pathlib import Path

HOOK_DIR = Path(__file__).resolve().parent
HOOK_SCRIPT = HOOK_DIR / "filter_hook.py"
# .claude/settings.json lives in the repo root (parent of the poc dir).
REPO_ROOT = HOOK_DIR.parent
SETTINGS_PATH = REPO_ROOT / ".claude" / "settings.json"

LOG_PATH = "/tmp/aegis_hook.jsonl"
STATE_PATH = "/tmp/aegis_state.json"

MATCHER = "Read|Bash"
HOOK_COMMAND = f"{sys.executable} {HOOK_SCRIPT}"
TAG = "filter_hook.py"  # how we recognize our own hook entry


# --------------------------------------------------------------------------- #
# settings.json helpers
# --------------------------------------------------------------------------- #
def load_settings():
    if SETTINGS_PATH.exists():
        try:
            return json.loads(SETTINGS_PATH.read_text())
        except Exception:
            print(f"[setup] WARNING: {SETTINGS_PATH} is not valid JSON; "
                  f"starting fresh.")
    return {}


def save_settings(data):
    SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    SETTINGS_PATH.write_text(json.dumps(data, indent=2) + "\n")


def _is_ours(hook_block):
    for h in hook_block.get("hooks", []):
        if TAG in h.get("command", ""):
            return True
    return False


# --------------------------------------------------------------------------- #
# Commands
# --------------------------------------------------------------------------- #
def cmd_install():
    if not HOOK_SCRIPT.exists():
        print(f"[setup] ERROR: {HOOK_SCRIPT} not found.")
        return 1

    settings = load_settings()
    hooks = settings.setdefault("hooks", {})
    pre = hooks.setdefault("PreToolUse", [])

    # Drop any prior Aegis entry so re-install is idempotent.
    pre = [b for b in pre if not _is_ours(b)]
    pre.append({
        "matcher": MATCHER,
        "hooks": [{"type": "command", "command": HOOK_COMMAND}],
    })
    hooks["PreToolUse"] = pre
    save_settings(settings)

    print(f"[setup] Installed Aegis PreToolUse hook.")
    print(f"        settings : {SETTINGS_PATH}")
    print(f"        matcher  : {MATCHER}")
    print(f"        command  : {HOOK_COMMAND}")
    print(f"[setup] Restart / start a Claude Code session in {REPO_ROOT} "
          f"for it to take effect.")
    return 0


def cmd_uninstall():
    settings = load_settings()
    pre = settings.get("hooks", {}).get("PreToolUse", [])
    new_pre = [b for b in pre if not _is_ours(b)]
    removed = len(pre) - len(new_pre)

    if "hooks" in settings:
        if new_pre:
            settings["hooks"]["PreToolUse"] = new_pre
        else:
            settings["hooks"].pop("PreToolUse", None)
        if not settings["hooks"]:
            settings.pop("hooks")
    save_settings(settings)
    print(f"[setup] Removed {removed} Aegis hook entr"
          f"{'y' if removed == 1 else 'ies'}.")
    return 0


def cmd_status():
    settings = load_settings()
    pre = settings.get("hooks", {}).get("PreToolUse", [])
    installed = any(_is_ours(b) for b in pre)
    print("===== AEGIS HOOK STATUS =====")
    print(f"settings file : {SETTINGS_PATH}")
    print(f"installed     : {'YES' if installed else 'no'}")
    print(f"hook script   : {HOOK_SCRIPT} "
          f"({'exists' if HOOK_SCRIPT.exists() else 'MISSING'})")

    if os.path.exists(STATE_PATH):
        with open(STATE_PATH) as f:
            st = json.load(f)
        base = st.get("baseline_tokens", 0)
        used = st.get("session_tokens", 0)
        saved = base - used
        pct = (saved / base * 100) if base else 0.0
        print("\n----- current session -----")
        print(f"session_id      : {st.get('session_id')}")
        print(f"tool calls      : {st.get('calls', 0)} "
              f"(PASS {st.get('passes',0)} / WARN {st.get('warns',0)} / "
              f"BLOCK {st.get('blocks',0)})")
        print(f"baseline tokens : {base:,}")
        print(f"filtered tokens : {used:,}")
        print(f"saved           : {saved:,}  ({pct:.1f}%)")
    else:
        print("\n(no session state yet — run a session or check the log)")
    return 0


def cmd_reset():
    # Rotate the log instead of deleting, so history isn't lost.
    if os.path.exists(LOG_PATH):
        bak = LOG_PATH + ".prev"
        os.replace(LOG_PATH, bak)
        print(f"[setup] Rotated {LOG_PATH} -> {bak}")
    if os.path.exists(STATE_PATH):
        os.remove(STATE_PATH)
        print(f"[setup] Cleared {STATE_PATH}")
    print("[setup] Session state reset.")
    return 0


def cmd_log(n=10):
    if not os.path.exists(LOG_PATH):
        print(f"[setup] No log at {LOG_PATH} yet.")
        return 0
    with open(LOG_PATH) as f:
        lines = f.readlines()
    print(f"===== LAST {min(n, len(lines))} DECISIONS "
          f"({len(lines)} total) =====")
    for line in lines[-n:]:
        try:
            e = json.loads(line)
        except Exception:
            continue
        tgt = e.get("tool_input", {})
        tgt = tgt.get("path") or tgt.get("command") or ""
        if len(tgt) > 40:
            tgt = tgt[:37] + "..."
        print(f"  {e.get('decision','?'):<5} {e.get('cc_tool',''):<5} "
              f"est={e.get('estimated_tokens',0):>6} "
              f"-> {e.get('counted_tokens',0):>6}  {tgt}")
    return 0


COMMANDS = {
    "install": cmd_install,
    "uninstall": cmd_uninstall,
    "status": cmd_status,
    "reset": cmd_reset,
    "log": cmd_log,
}


def main():
    if len(sys.argv) < 2 or sys.argv[1] not in COMMANDS:
        print(f"usage: python setup_hook.py "
              f"{{{'|'.join(COMMANDS)}}}")
        return 1
    return COMMANDS[sys.argv[1]]()


if __name__ == "__main__":
    sys.exit(main())
