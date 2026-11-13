---
description: Status-line dog. No arg = toggle on/off. Args: on | off | pixel | ascii | treat | stats | banner
argument-hint: "[on|off|pixel|ascii|treat|stats|banner]"
allowed-tools: Bash(~/.claude/dog/dog.py:*)
---
!`~/.claude/dog/dog.py toggle $ARGUMENTS`

Above is the dog's response. Reply with NO tool calls:
- If it starts with STATS: show everything after the word STATS verbatim inside a ```text code block, then one short dog-pun line.
- If it starts with TREAT or FULL: one short playful line about the treat (e.g. nom nom, belly rubs, "too full to fetch").
- If it starts with STYLE: one short line about the dog's new look.
- If it starts with BANNER: one short line saying to watch the status line, something big is coming.
- If ON/OFF: one short line of dog puns (ON: walkies, zoomies, "pawsitively back"; OFF: kennel, "sit, stay", "paws for a break"), and if OFF mention XP still counts while it's away.
