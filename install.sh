#!/usr/bin/env bash
# Install claude-code-dog into your Claude Code config (default ~/.claude).
set -euo pipefail

SRC="$(cd "$(dirname "$0")" && pwd)"
CLAUDE="${CLAUDE_CONFIG_DIR:-$HOME/.claude}"
DOG="$CLAUDE/dog/dog.py"

command -v python3 >/dev/null || { echo "python3 (3.8+) is required"; exit 1; }
python3 -c 'import sys; sys.exit(sys.version_info < (3, 8))' || { echo "python3 3.8+ is required"; exit 1; }

echo "Installing claude-code-dog into $CLAUDE"
mkdir -p "$CLAUDE/dog" "$CLAUDE/commands"
install -m 755 "$SRC/dog.py" "$DOG"
sed "s#~/.claude/dog/dog.py#$DOG#g" "$SRC/commands/walkies.md" > "$CLAUDE/commands/walkies.md"
python3 "$SRC/scripts/settings.py" install "$CLAUDE/settings.json" "$DOG"

cat <<EOF

Done. Restart Claude Code (or open /hooks once) and the dog moves in.

  /walkies          toggle the dog on/off
  /walkies treat    feed it (+10% XP while it's over half full)
  /walkies stats    level, XP, badges, gear dates
  /walkies ascii    old-school text dog   (/walkies pixel to switch back)

Uninstall any time with ./uninstall.sh
EOF
