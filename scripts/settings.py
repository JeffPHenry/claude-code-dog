#!/usr/bin/env python3
"""Merge (or remove) the dog's entries in a Claude Code settings.json.

  settings.py install   <settings.json> <path to dog.py>
  settings.py uninstall <settings.json>

Existing settings, permissions and hooks are preserved. A timestamped backup
is written next to the file before any change.
"""
import json, os, shutil, sys, time

MARK = "dog/dog.py"
VERBS = ["Bark bark", "Woofing", "Sniffing around", "Fetching", "Chasing tail",
         "Wagging", "Digging", "Borking", "Zooming", "Chewing on it"]
HOOKS = {"UserPromptSubmit": "busy", "Stop": "idle", "StopFailure": "idle",
         "PreToolUse": "tool", "PostToolUse": "tooldone", "PostToolUseFailure": "toolfail"}


def ours(entry):
    return any(MARK in h.get("command", "") for h in entry.get("hooks", []))


def main():
    mode, path = sys.argv[1], os.path.expanduser(sys.argv[2])
    cfg = {}
    if os.path.exists(path):
        with open(path) as f:
            cfg = json.load(f)
        backup = f"{path}.bak-dog-{int(time.time())}"
        shutil.copy2(path, backup)
        print(f"  backed up settings to {backup}")
    hooks = cfg.setdefault("hooks", {})

    # always start from a clean slate for our own entries
    for event in list(hooks):
        hooks[event] = [e for e in hooks[event] or [] if not ours(e)]
        if not hooks[event]:
            del hooks[event]
    if MARK in (cfg.get("statusLine") or {}).get("command", ""):
        del cfg["statusLine"]
    if (cfg.get("spinnerVerbs") or {}).get("verbs") == VERBS:
        del cfg["spinnerVerbs"]

    if mode == "install":
        dog = sys.argv[3]
        if cfg.get("statusLine"):
            print(f"  note: replacing your existing statusLine "
                  f"({cfg['statusLine'].get('command', '?')}); it's in the backup")
        cfg["statusLine"] = {"type": "command", "command": f'"{dog}"', "refreshInterval": 1}
        cfg.setdefault("spinnerVerbs", {"mode": "replace", "verbs": VERBS})
        for event, arg in HOOKS.items():
            hooks.setdefault(event, []).append(
                {"hooks": [{"type": "command", "command": f'"{dog}" {arg}', "timeout": 5}]})
    if not hooks:
        cfg.pop("hooks", None)

    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(cfg, f, indent=2)
        f.write("\n")
    os.replace(tmp, path)


if __name__ == "__main__":
    main()
