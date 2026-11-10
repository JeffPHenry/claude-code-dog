#!/usr/bin/env bash
# Remove claude-code-dog's settings entries and files. Pass --keep-xp to keep
# your level/XP state in ~/.claude/dog.
set -euo pipefail

SRC="$(cd "$(dirname "$0")" && pwd)"
CLAUDE="${CLAUDE_CONFIG_DIR:-$HOME/.claude}"

if [ -f "$CLAUDE/settings.json" ]; then
  python3 "$SRC/scripts/settings.py" uninstall "$CLAUDE/settings.json"
fi
rm -f "$CLAUDE/commands/walkies.md"
if [ "${1:-}" = "--keep-xp" ]; then
  rm -f "$CLAUDE/dog/dog.py"
  echo "Removed. XP kept in $CLAUDE/dog"
else
  rm -rf "$CLAUDE/dog"
  echo "Removed. Good dog."
fi
