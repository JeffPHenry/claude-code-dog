#!/usr/bin/env python3
"""claude-code-dog: an animated pixel dog for the Claude Code status line,
with token-based XP, 100 levels, unlockable gear, achievements and hunger.

  dog.py                status line renderer (session JSON on stdin)
  dog.py busy | idle    UserPromptSubmit / Stop hooks: score tokens, wake / nap
  dog.py tool | tooldone | toolfail
                        PreToolUse / PostToolUse / PostToolUseFailure hooks
  dog.py toggle [on|off|pixel|ascii|treat|stats]   used by /walkies
  dog.py table          print the level table

Everything is local: it reads your own Claude Code transcripts for output-token
counts and keeps its state in ~/.claude/dog. No network access.
"""
import colorsys, hashlib, json, math, os, random, re, sys, time

HOME = os.path.dirname(os.path.abspath(__file__))   # state lives next to dog.py
SESS = os.path.join(HOME, "sessions")
TMP = os.environ.get("TMPDIR", "/tmp")
TOKENS_PER_DAY = 600_000          # roughly one busy day of Claude Code output
MAX_LEVEL = 100
WINDOW = 6                        # seconds per activity
SPEED = 5                         # columns per second
RIGHT_RESERVE = 42                # keep clear of Claude Code's right-side notices

def term_cols():
    for fd_src in ("/dev/tty",):
        try:
            fd = os.open(fd_src, os.O_RDONLY)
            try:
                return os.get_terminal_size(fd).columns
            finally:
                os.close(fd)
        except Exception:
            pass
    try:
        return int(os.environ.get("COLUMNS", ""))
    except ValueError:
        return 100

# ---------------------------------------------------------------- levels
def days_for(n):
    """Days of typical use to go from level n to n+1: ~1 day early,
    ~2 days by Lv 8, plateauing at ~2.5 weeks from Lv 30 on."""
    return min(18.0, 1 + 0.02 * (n - 1) ** 2)

THRESH = [0]                      # THRESH[n-1] = XP needed to reach level n
for n in range(1, MAX_LEVEL):
    THRESH.append(THRESH[-1] + int(days_for(n) * TOKENS_PER_DAY))

TITLES = ["Puppy", "Pup", "Good Dog", "Very Good Dog", "Fetch Pro",
          "Sniff Scholar", "Pack Leader", "Top Dog", "Legend", "Doggo Deity"]

def level_of(xp):
    lv = 1
    while lv < MAX_LEVEL and xp >= THRESH[lv]:
        lv += 1
    return lv

def rgb(h, s, v):
    r, g, b = colorsys.hsv_to_rgb(h % 1, s, v)
    return f"\x1b[38;2;{int(r*255)};{int(g*255)};{int(b*255)}m"

def level_colors(lv, t):
    """(body, accent) escape codes. Every level gets its own hue (golden-ratio
    steps); saturation deepens with tier; Lv 50+ two-tone; Lv 100 rainbow."""
    if lv >= MAX_LEVEL:
        return None, None
    h = 0.14 + (lv - 1) * 0.381966
    tier = (lv - 1) // 10
    body = rgb(h, 0.55 + 0.045 * tier, 1.0)
    accent = rgb(h + 0.5, 0.8, 1.0) if lv >= 50 else body
    return body, accent

# ---------------------------------------------------------------- scoring
def load(path, default):
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return default

def save(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(obj, f)
    os.replace(tmp, path)

def installed_at():
    p = os.path.join(HOME, "installed")
    if not os.path.exists(p):
        with open(p, "w") as f:
            f.write(str(time.time()))
    return float(open(p).read().strip() or 0)

def transcript_start(path):
    try:
        with open(path) as f:
            for _ in range(20):
                line = f.readline()
                if not line:
                    break
                ts = json.loads(line).get("timestamp")
                if ts:
                    return time.mktime(time.strptime(ts[:19], "%Y-%m-%dT%H:%M:%S")) - time.timezone
    except Exception:
        pass
    return time.time()

def score(sid, transcript):
    """Score the main transcript plus any subagent transcripts of this session
    (<transcript dir>/<sid>/subagents/*.jsonl)."""
    if not transcript:
        return
    added = score_file(sid, transcript)
    sub = transcript[:-6] + "/subagents" if transcript.endswith(".jsonl") else None
    if sub and os.path.isdir(sub):
        for name in os.listdir(sub):
            if name.endswith(".jsonl"):
                added += score_file(f"{sid}.sub.{name[:-6]}", os.path.join(sub, name))
    fed_bonus(added)

def score_file(sid, transcript):
    """Add output tokens written to the transcript since the last check."""
    if not os.path.exists(transcript):
        return 0
    p = os.path.join(SESS, f"{sid}.json")
    st = load(p, None)
    size = os.path.getsize(transcript)
    if st is None:
        # sessions that began before the dog was installed only count from now
        old = transcript_start(transcript) < installed_at()
        st = {"offset": size if old else 0, "seen": {}, "total": 0}
    if size < st["offset"]:
        st["offset"] = 0
    with open(transcript, "rb") as f:
        f.seek(st["offset"])
        chunk = f.read()
    end = chunk.rfind(b"\n") + 1          # only complete lines
    seen = st["seen"]
    before = st["total"]
    for raw in chunk[:end].splitlines():
        if b'"goal_status"' in raw and b'"met":true' in raw:
            try:
                a = json.loads(raw).get("attachment") or {}
                if a.get("type") == "goal_status" and a.get("met") is True:
                    goal_treat(a.get("condition", ""))
            except Exception:
                pass
            continue
        if b'"output_tokens"' not in raw:
            continue
        try:
            e = json.loads(raw)
            m = e["message"]
            mid, out = m["id"], int(m["usage"]["output_tokens"])
        except Exception:
            continue
        prev = seen.get(mid, 0)
        if out > prev:                     # same message logs several entries
            st["total"] += out - prev
            seen[mid] = out
    st["offset"] += end
    st["seen"] = dict(list(seen.items())[-50:])
    save(p, st)
    return st["total"] - before

def total_xp():
    xp = 0
    for name in os.listdir(SESS):
        if name.endswith(".json") and not name.endswith(".ev.json"):
            xp += load(os.path.join(SESS, name), {}).get("total", 0)
    return xp + load(ACH, {}).get("bonus", 0)

# ---------------------------------------------------------------- achievements
ACH = os.path.join(HOME, "achievements.json")
EVENT_BONUS = {"pee": 10_000, "poop": 50_000}        # XP every time it happens
ACHIEVEMENTS = [  # (key, name, test(counts), bonus XP)
    ("pee1",   "First Puddle",      lambda c: c.get("pee", 0) >= 1,    25_000),
    ("pee10",  "Territory Marked",  lambda c: c.get("pee", 0) >= 10,  100_000),
    ("pee50",  "Every Hydrant",     lambda c: c.get("pee", 0) >= 50,  500_000),
    ("poop1",  "Oops",              lambda c: c.get("poop", 0) >= 1,   50_000),
    ("both",   "Both Ends",         lambda c: c.get("pee") and c.get("poop"), 50_000),
    ("poop5",  "Number Two Pro",    lambda c: c.get("poop", 0) >= 5,  250_000),
    ("poop20", "Lawn Destroyer",    lambda c: c.get("poop", 0) >= 20, 1_000_000),
]

def achievements(event=None):
    """Award per-event bonus XP and any newly unlocked badges. Returns state."""
    a = load(ACH, {"got": {}, "bonus": 0, "last": None})
    changed = False
    if event:
        a["bonus"] += EVENT_BONUS.get(event, 0)
        changed = True
    c = event_counts()
    for key, name, test, bonus in ACHIEVEMENTS:
        if key not in a["got"] and test(c):
            a["got"][key] = time.time()
            a["bonus"] += bonus
            a["last"] = [name, bonus, time.time()]
            changed = True
    if changed:
        save(ACH, a)
    return a

def event_counts():
    c = {}
    for name in os.listdir(SESS):
        if name.endswith(".ev.json"):
            for k, v in load(os.path.join(SESS, name), {}).get("counts", {}).items():
                c[k] = c.get(k, 0) + v
    return c

# ---------------------------------------------------------------- art
# All frames face RIGHT; mirror() flips them for the run back.
STAND = "  \\_)-\\_)_) "
RUN = {
    0: ["_      __  ", "\\\\____{(''o", "(      \\_` ", " \\_)-\\_)_) "],
    1: ["       __  ", "//____{(''O", "(      \\_` ", " /_/   \\_\\ "],
}
ACT = {
  "wag": ([["_      __  ", "\\\\____{(''o", "(      \\_` ", STAND[1:]],
           ["       __  ", "//____{(^^o", "(      \\_` ", STAND[1:]]],
          ["*wag wag wag*", "*wag wag*"]),
  "scratch": ([["      __   ", ",____{(-'o ", "(___/ \\_`  ", "  \\_) \\\\   "],
               ["      __   ", ",____{(-'o ", "(___/ \\_`  ", "  \\_)  //  "]],
              ["*scritch scritch*", "ahh that's the spot"]),
  "dig": ([[".  _        ", " '\\\\_____   ", "  (     \\__o", "   \\_)-\\_)_)"],
           [" ' _        ", ".  \\\\_____  ", "  (     \\__o", "   \\_)-\\_)_)"]],
          ["*dig dig dig*", "there's a bone here somewhere"]),
  "sniff": ([["_          ", "\\\\_______  ", "(      \\__o", STAND[1:]],
             ["           ", "//_______  ", "(      \\__o", STAND[1:]]],
            ["*sniff sniff*", "*snrrrf*", "hmm, interesting"]),
  "bark": ([["_      __  ", "\\\\____{(''O", "(      \\_` ", STAND[1:]],
            ["       __  ", "//____{(''o", "(      \\_` ", STAND[1:]]],
           ["bark bark!", "BARK BARK!", "woof woof!"]),
  "pee": ([["       __  ", ",____{(''o", "(`)    \\_` ", "/ :  \\_)_) "],
           ["       __  ", ",____{(''o", "(`)    \\_` ", "/ ;  \\_)_) "],
           ["       __  ", ",____{(''o", "(`)    \\_` ", "~~~  \\_)_) "]],
          ["*psssssss*", "...", "ahhh"]),
  "poop": ([["       __  ", " ,___{(''o", "(    )\\_` ", "\\_) \\_)    "],
            ["       __  ", " ,___{(>'o", "(    )\\_` ", "\\_) \\_)  o "],
            ["       __  ", " ,___{(''o", "(    )\\_` ", "\\_) \\_)  @ "]],
           ["*strains*", "*hnnngh*", "oops."]),
}
NAP = [["            z   ", ",________,(-.-)", "(___________)_) "],
       ["           z Z  ", ",________,(-.-)", "(___________)_) "],
       ["          z Z z ", ",________,(-.-)", "(___________)_) "]]
# ---- multi-step scenes: the dog moves within its 6-second window
SIDE  = ["              ", "   ,_______{(''o", "  (________\\_` ", "   =_= =_=     "]
BELLY = ["   \\/    \\/   ", "   ||    ||    ", "  (__________)(o,,)", "                "]
CURL  = ["  .--.   ", " /(''o\\  ", " \\  ~ / ", "  '--'   "]

def canvas(parts, w=34):
    """parts: [(sprite_lines, x)] or [(char, line, x)] overlaid on a 4-line canvas."""
    rows = [[" "] * w for _ in range(4)]
    for p in parts:
        if isinstance(p[0], str):            # single item: (char, line, x)
            ch, ln, x = p
            if 0 <= x < w:
                rows[ln][x] = ch
            continue
        sprite, x = p
        for i, l in enumerate(sprite):
            for j, ch in enumerate(l):
                if ch != " " and 0 <= x + j < w:
                    rows[i][x + j] = ch
    return ["".join(r).rstrip() or " " for r in rows]

def fetch(item, step, words):
    dog_x = [0, 0, 4, 9, 14, 15][step]
    flight = [("1", 12), ("0", 19), ("1", 24), ("3", 27), ("3", 27), None][step]
    if step == 5:                                     # trotting back, item in mouth
        sprite = ["_      __    ", "\\\\____{(''o" + item, "(      \\_`   ", STAND[1:]]
    elif step <= 1:
        sprite = RUN[0] if step == 0 else ["       __  ", "\\\\____{(''O", "(      \\_` ", STAND[1:]]
    else:
        sprite = RUN[step % 2]
    parts = [(sprite, dog_x)]
    if flight:
        parts.append((item, int(flight[0]), flight[1]))
    return canvas(parts), words[step]

def roll(step, words):
    seq = [RUN[0], SIDE, BELLY, BELLY, SIDE, RUN[1]]
    return canvas([(seq[step], 2)]), words[step]

def roll_run(step, words):
    seq = [(RUN[0], 0), (RUN[1], 4), (SIDE, 8), (BELLY, 10), (SIDE, 12), (RUN[1], 16)]
    sp, x = seq[step]
    return canvas([(sp, x)]), words[step]

def tail_chase(step, words):
    seq = [RUN[0], CURL, mirror(RUN[1]), CURL, RUN[1], CURL]
    return canvas([(seq[step], 2)]), words[step]

SCENES = {
  "ball":  lambda s: fetch("●", s, ["ball?!", "BALL!!", "*zoom*", "*zoom zoom*", "GOT IT!", "again! again!"]),
  "stick": lambda s: fetch("=", s, ["stick??", "STICK!!", "*zoom*", "best stick ever", "GOT IT!", "*proud trot*"]),
  "roll":  lambda s: roll(s, ["roll over!", "*flop*", "belly rubs?", "*wiggle wiggle*", "*flop*", "ta-da!"]),
  "rollrun": lambda s: roll_run(s, ["*zoomies*", "*zoomies*", "*tumble*", "wheee!", "*tumble*", "*zoomies*"]),
  "tail":  lambda s: tail_chase(s, ["wait what's that", "*spin*", "*spin spin*", "ALMOST GOT IT", "*spin*", "dizzy..."]),
}

BARKS = ["bark bark!", "woof!", "bark bark!", "arf arf!", "bark bark!", "BORK!"]

SWAP = str.maketrans("()[]{}<>/\\", ")(][}{><\\/")
def mirror(lines):
    w = max(len(l) for l in lines)
    return [l.ljust(w)[::-1].translate(SWAP) for l in lines]

def pick_activity(sid, win):
    r = random.Random(f"{sid}:{win}").random()
    for name, p in (("poop", .0007), ("pee", .003), ("dig", .09), ("scratch", .09),
                    ("wag", .07), ("sniff", .06), ("bark", .06),
                    ("ball", .05), ("stick", .05), ("roll", .04), ("rollrun", .04),
                    ("tail", .05)):
        if r < p:
            return name
        r -= p
    return "run"

def paint(lines, lv, t):
    body, accent = level_colors(lv, t)
    out = []
    for i, l in enumerate(lines):
        if body is None:                   # Lv 100: scrolling rainbow
            out.append("".join(rgb(t * 0.2 + (i + j) * 0.04, 0.8, 1) + ch
                               for j, ch in enumerate(l)) + "\x1b[0m")
        else:
            out.append((accent if i == 0 else body) + l + "\x1b[0m")
    return out

def bar(frac, n=12):
    k = int(frac * n)
    return "▰" * k + "▱" * (n - k)

def fmt(n):
    return f"{n/1e6:.1f}M" if n >= 1e6 else f"{n/1e3:.0f}k" if n >= 1e3 else str(n)

# ---------------------------------------------------------------- pixel art
# Half-block pixels: each text row shows 2 pixel rows (▀ fg = top, bg = bottom).
# Sprites are 20x10 px (5 text lines), composed from head/body/legs/tail parts.
# Codes: L highlight, B body, s shadow, E saddle/ears (level-tinted shades),
#        W white, K black, R collar, G tag, P tongue, items/effects below.
HEADS = {   # k = eye (blinks), K = nose
  "n":     ["..BBB...", ".BLLLB..", ".EBkBBB.", "EEBBWWWK", "EEBWWWW."],
  "pant":  ["..BBB...", ".BLLLB..", ".EBkBBB.", "EEBBWWWK", "EEBWWWP.", "......P."],
  "flap":  ["E.BBB...", "EBLLLB..", ".EBkBBB.", ".EBBWWWK", "EEBWWWP.", "......P."],
  "happy": ["..BBB...", ".BLLLB..", ".EBEBBB.", "EEBBWWWK", "EEBWWWP.", "......P."],
  "bark":  ["..BBB...", ".BLLLB..", ".EBkBBB.", "EEBBWWWK", "EEBW....", "...WPP.."],
  "angry": ["..BBB...", ".BLEEB..", ".EBkEBB.", "EEBBWWWK", "EEBW....", "...WWW.."],
  "sleep": ["..BBB...", ".BLLLB..", ".EBEBBB.", "EEBBWWWK", "EEBWWWW."],
}
BODY = ["..LLLLLLLLLL", "..EEEEEEEEBBRRGW", "..BEEEEEEBBBBWW", "..ssssssssssWW"]
LEGS = {
  "stand": ["..BB.BB....BB.BB", "..WW.WW....WW.WW"],
  "run0":  [".BB...BB...BB...BB", "WW.....WW..WW....WW"],
  "run1":  ["....BBB....BBB", "....WWW....WWW"],
  "legup": [".....BB....BB.BB", ".....WW....WW.WW"],
  "squat": ["..BBB......BB.BB", ".WWW.......WW.WW"],
  "dig0":  [".....BB....BB.BB", ".....WW....WW.WW"],       # hind leg kicked (drawn in px_pose)
  "dig1":  ["..BB.BB....BB.BB", "..WW.WW....WW...WW"],     # front paw scrapes
}
TAILS = {"up":   [(1, 0, "W"), (2, 1, "B"), (3, 2, "B")],
         "back": [(4, 0, "W"), (4, 1, "B")],
         "high": [(0, 1, "W"), (1, 1, "B"), (2, 2, "B"), (3, 2, "B")],
         "curl": [(5, 0, "W"), (5, 1, "B")]}

def accessories(lv):
    """Gear unlocked by level tier (cumulative)."""
    return {k for k, need in (("bandana", 10), ("spots", 20), ("shades", 30),
                              ("cape", 40), ("crown", 70), ("sparkle", 80)) if lv >= need}

def dog(head="n", hx=12, hy=0, legs="stand", tail="up", by=4, acc=()):
    g = [["."] * 20 for _ in range(10)]
    def put(rows, oy, ox):
        for i, row in enumerate(rows):
            for j, c in enumerate(row):
                if c != "." and 0 <= oy + i < 10 and 0 <= ox + j < 20:
                    g[oy + i][ox + j] = c
    if legs:
        put(LEGS[legs], 8, 0)
    put(BODY, by, 0)
    for r, x, c in TAILS[tail]:
        g[r + by - 4][x] = c
    if "spots" in acc:
        for r, x in ((0, 5), (0, 9), (3, 4), (3, 8)):
            g[by + r][x] = "E"
    if "cape" in acc:
        put(["...cCCCCCC", ".....CCCCC", "......cCC"], by - 1, 2)
    put(HEADS[head], hy, hx)
    if "bandana" in acc:
        put(["AAA", ".AA", ".a"], by + 1, 12)
    if "shades" in acc and head not in ("sleep", "happy"):
        put(["NnN"], hy + 2, hx + 2)
    if "crown" in acc:
        put([".G.G.G", "..GRG"], hy, hx)
    return ["".join(r) for r in g]

_PX_CACHE = {}
def px_set(lv):
    acc = frozenset(accessories(lv))
    if acc not in _PX_CACHE:
        _PX_CACHE[acc] = build_px(acc)
    return _PX_CACHE[acc]

def build_px(acc):
  d = lambda **kw: dog(acc=acc, **kw)
  return {
  "stand": d(),
  "wag":   d(head="happy", tail="back"),
  "bark":  d(head="bark", tail="high"),
  "run0":  d(head="flap", legs="run0", tail="back"),
  "run1":  d(head="pant", legs="run1", tail="up"),
  "down":  d(hy=4, tail="high"),
  "down2": d(hy=5, tail="high"),
  "legup": d(legs="legup", tail="high"),
  "squat": d(legs="squat", tail="high"),
  "dig0":  d(hy=4, legs="dig0", tail="high"),
  "dig1":  d(hy=4, legs="dig1", tail="up"),
  "side":  d(hy=5, legs=None, tail="curl", by=6),
  "nap":   d(head="sleep", hy=5, legs=None, tail="curl", by=6),
  "growl": d(head="angry", tail="high"),
  "chew0": d(hy=4, legs=None, tail="curl", by=6),
  "chew1": d(head="bark", hy=4, legs=None, tail="back", by=6),
  "sit":   ["...........BBB......", "..........BLLLB.....", "..........EBkBBB....",
            ".........EEBBWWWK...", ".........EEBWWWW....", "........LBBRRGW.....",
            "......LEEEBBBWW.....", "....LEEEEEBBBWW.....", "...sssssssBBBWW.....",
            "..WWWWWW...WW.WW...."],
  "belly": ["....................", "....................", "...W.W......W.W......",
            "...B.B......B.B.....", "..sWWWWWWWWWWs......", "..BBBBBBBBBBBBWW....",
            ".WEEEEEEEEEEBBRRBBK.", "..LLLLLLLLLLLEBkWW..", "..............EE.P..",
            "...................."],
  "belly2":["....................", "....................", "....W.W......W.W....",
            "....B.B......B.B....", "..sWWWWWWWWWWs......", "..BBBBBBBBBBBBWW....",
            ".WEEEEEEEEEEBBRRBBK.", "..LLLLLLLLLLLEBkWW..", "..............EEP...",
            "...................."],
  "curl":  ["....................", "......LLLLLL........", "....LLEEEEEELL......",
            "...BEEEEEEEEEEB.....", "..BEEEBBBBBBEEEB....", "..BEEBkBBWWBBEEB....",
            "..BEEBBWWWWK.EEBW...", "...BBBBBBBBBBBBB....", "....sssssssssss.....",
            "....WW......WW......"],
  }
PX_FIXED = {"b": (240, 228, 195),                                  # bone
            "K": (20, 20, 25), "k": (20, 20, 25), "o": (38, 38, 44),
            "A": (40, 110, 235), "a": (25, 70, 170),              # bandana
            "N": (30, 30, 55), "n": (150, 190, 255),              # shades
            "X": (255, 255, 255), "Z": (255, 225, 90),            # sparkles
            "W": (245, 242, 235), "P": (255, 115, 150),
            "R": (220, 40, 50), "G": (255, 205, 40),
            "Y": (200, 235, 50), "V": (240, 255, 170),          # tennis ball
            "T": (150, 95, 45), "U": (100, 60, 25),             # stick
            "y": (250, 225, 60), "p": (120, 75, 30), "q": (85, 50, 20),
            "d": (140, 105, 65), "g": (150, 150, 150), "h": (90, 90, 90)}
STYLE = os.path.join(HOME, "style")

def style():
    try:
        return open(STYLE).read().strip() or "pixel"
    except Exception:
        return "pixel"

def px_pose(act, t, win):
    """-> ([(sprite, x)], [(code, row, x)]) for one frame."""
    s = min(int(t - win * WINDOW), WINDOW - 1)
    alt = int(t) % 2
    k3 = min(s * 3 // WINDOW, 2)
    if act == "run":
        return [(("run0", "run1")[alt], 3)], \
            ([("g", 9, 1), ("h", 8, 0)] if alt else [("g", 8, 2), ("h", 9, 0), ("g", 9, 1)])
    if act == "chew":
        bone = [("b", 8, 19), ("b", 8, 23)] + [("b", 9, x) for x in range(19, 24)] \
            if alt else [("b", 9, 19), ("b", 9, 23)] + [("b", 8, x) for x in range(19, 24)]
        return [(("chew0", "chew1")[alt], 0)], bone
    if act == "growl":   return [(("growl", "bark")[alt], 0)], []
    if act == "beg":     return [("sit", 0)], []
    if act == "eat":
        bowl = [("R", 8, x) for x in range(16, 23)] + [("R", 9, x) for x in range(17, 22)]
        kib = [("d", 7, 17), ("d", 7, 19), ("d", 7, 21)][:max(0, 3 - s // 2)]
        return [(("down", "down2")[alt], 0)], bowl + kib
    if act == "joy":
        rnd = random.Random(int(t))
        conf = [(rnd.choice("XZAPYRV"), rnd.randrange(0, 9), rnd.randrange(0, 30)) for _ in range(9)]
        return [(("wag", "bark")[alt], 6)], conf
    if act == "wag":     return [(("stand", "wag")[alt], 0)], []
    if act == "bark":    return [(("bark", "stand")[alt], 0)], []
    if act == "sniff":   return [(("down", "down2")[alt], 0)], []
    if act == "dig":
        # hind leg kicks dirt up and back in an arc; a mound grows under the nose
        x = 6
        kick = [("B", 8, x + 2), ("B", 8, x + 1), ("B", 7, x + 1), ("B", 7, x),
                ("W", 7, x - 1), ("W", 6, x - 1)]
        spray = kick + [("d", 5, x - 3), ("q", 4, x - 2), ("d", 6, x - 4)] if alt else \
                [("d", 2, x - 4), ("q", 3, x - 5), ("d", 1, x - 2), ("d", 4, x - 6)]
        mound = [("d", 9, c) for c in range(x + 18, x + 18 + min(2 + s, 6))] + \
                ([("q", 8, c) for c in range(x + 19, x + 19 + min(s, 3))] if s >= 2 else [])
        return [(("dig1", "dig0")[alt], x)], spray + mound
    if act == "scratch":
        return [("sit", 0)], ([("B", 4, 8), ("W", 3, 8)] if alt else [("B", 5, 7), ("W", 4, 7)])
    if act == "pee":
        leg = [("B", 6, 3), ("B", 5, 2), ("W", 4, 1)]
        return [("legup", 2)], leg + [[("y", 8, 5)], [("y", 8, 5), ("y", 9, 5)],
                                      [("y", 9, x) for x in range(1, 9)]][k3]
    if act == "poop":
        return [("squat", 4)], [[], [("p", 9, 2)],
                                [("q", 8, 2), ("p", 9, 1), ("p", 9, 2), ("q", 9, 3)]][k3]
    if act in ("ball", "stick"):
        def item(r, ix):
            if act == "ball":
                return [("V", r, ix), ("Y", r, ix + 1), ("Y", r + 1, ix), ("Y", r + 1, ix + 1)]
            return [("T", r + 1, ix), ("T", r + 1, ix + 1), ("U", r + 1, ix + 2),
                    ("T", r + 1, ix + 3), ("T", r + 1, ix + 4)]
        if s == 5:                                    # trotting back, item in mouth
            return [("run1M", 14)], item(4, 13) if act == "ball" else item(4, 10)
        x = [0, 0, 6, 12, 19][s]
        spr = ["run0", "bark", "run0", "run1", "down"][s]
        r, ix = [(3, 22), (0, 30), (3, 36), (8, 39), (8, 38)][s]
        return [(spr, x)], item(r, ix)
    if act == "roll":
        return [(["stand", "side", "belly", "belly2", "side", "wag"][s], 2)], []
    if act == "rollrun":
        return [[("run0", 0), ("run1", 5), ("side", 10), ("belly", 12),
                 ("side", 14), ("run1", 19)][s]], []
    if act == "tail":
        return [(["run0", "curl", "run0M", "curl", "run1", "curl"][s], 2)], []
    return [("stand", 0)], []

def px_grid(sprites, dots, flip=False, W=44, lv=1, t=0.0):
    px = px_set(lv)
    blink = int(t) % 6 == 0
    g = [[None] * W for _ in range(10)]
    for name, x in sprites:
        rows = px[name.rstrip("M")]
        if name.endswith("M"):
            rows = [r[::-1] for r in rows]
        for i, row in enumerate(rows):
            for j, c in enumerate(row):
                if c != "." and 0 <= x + j < W:
                    g[i][x + j] = c
    if blink:
        g = [[("E" if c == "k" else c) for c in row] for row in g]
    feet = [j for j in range(W) if g[9][j] or g[8][j]]
    if feet:                                       # soft ground shadow
        for j in range(max(0, feet[0] - 1), min(W, feet[-1] + 2)):
            g[9][j] = g[9][j] or "o"
    for c, r, x in dots:
        if 0 <= x < W:
            g[r][x] = c
    if lv >= 80:                                   # twinkling sparkles
        rnd = random.Random(int(t))
        span = max(j for j in range(W) for i in range(10) if g[i][j]) + 1
        for _ in range(3):
            i, j = rnd.randrange(0, 8), rnd.randrange(0, span)
            if not g[i][j]:
                g[i][j] = rnd.choice("XZ")
    w = max((j + 1 for row in g for j, c in enumerate(row) if c), default=1)
    g = [row[:w] for row in g]
    return [row[::-1] for row in g] if flip else g

def px_palette(lv, t):
    """code, row, col -> (r,g,b). Body shades follow the level hue."""
    h, s = 0.14 + (lv - 1) * 0.381966, min(1.0, 0.55 + 0.045 * ((lv - 1) // 10))
    def c255(hh, ss, vv):
        return tuple(int(x * 255) for x in colorsys.hsv_to_rgb(hh % 1, ss, vv))
    eh = h + 0.5 if lv >= 50 else h
    shades = {"L": c255(h, s * 0.5, 1.0), "B": c255(h, s, 0.92),
              "s": c255(h, s, 0.66), "E": c255(eh, min(1, s + 0.25), 0.45),
              "C": c255(h + 0.5, 0.85, 0.9), "c": c255(h + 0.5, 0.9, 0.55)}   # cape
    vs = {"L": 1.0, "B": 0.9, "s": 0.65, "E": 0.45}
    def col(c, i, j):
        if c in PX_FIXED:
            return PX_FIXED[c]
        if lv >= MAX_LEVEL:
            return c255(t * 0.2 + (i + j) * 0.03, 0.85, vs.get(c, 0.9))
        return shades.get(c, shades["B"])
    return col

AA_SRC = set("LBsEWCcRGKkAaNnPT")       # sprite codes that get smoothed edges
AA_BG = (12, 12, 14)                     # assumed (dark) terminal background
AA_MIX = 0.42

def antialias(g, col):
    """Return {(r, c): rgb} for empty pixels sitting in a staircase corner
    (exactly one vertical + one horizontal sprite neighbour, the other two empty)."""
    H, W = len(g), len(g[0]) if g else 0
    at = lambda r, c: g[r][c] if 0 <= r < H and 0 <= c < W else None
    src = lambda r, c: at(r, c) in AA_SRC
    out = {}
    for r in range(H):
        for c in range(W):
            if g[r][c]:
                continue
            v = [(r + d, c) for d in (-1, 1) if src(r + d, c)]
            h = [(r, c + d) for d in (-1, 1) if src(r, c + d)]
            if len(v) == 1 and len(h) == 1:
                cols = [col(at(*q), *q) for q in v + h]
                out[(r, c)] = tuple(int(sum(x[k] for x in cols) / 2 * AA_MIX + AA_BG[k] * (1 - AA_MIX))
                                    for k in range(3))
    return out

def px_lines(g, lv, t):
    """Render a pixel grid as truecolor half-block text lines."""
    col0 = px_palette(lv, t)
    aa = antialias(g, col0)
    g = [[(c or ("~" if (r, j) in aa else None)) for j, c in enumerate(row)] for r, row in enumerate(g)]
    col = lambda c, r, j: c if isinstance(c, tuple) else aa[(r, j)] if c == "~" else col0(c, r, j)
    fg = lambda c: "\x1b[38;2;%d;%d;%dm" % c
    bg = lambda c: "\x1b[48;2;%d;%d;%dm" % c
    lines = []
    for r in range(0, len(g), 2):
        out = ""
        for j in range(len(g[r])):
            a, b = g[r][j], g[r + 1][j]
            if a and b:
                out += fg(col(a, r, j)) + bg(col(b, r + 1, j)) + "▀"
            elif a:
                out += "\x1b[49m" + fg(col(a, r, j)) + "▀"
            elif b:
                out += "\x1b[49m" + fg(col(b, r + 1, j)) + "▄"
            else:
                out += "\x1b[0m "
        lines.append(out + "\x1b[0m")
    return lines

# ---------------------------------------------------------------- scrolling banners
# Every so often (busy only, pixel style) the dog spots a giant block-letter
# message coming, runs off screen, the message scrolls through, the dog peeks back.
BANNER_P = 0.08                    # chance per 3-window (18 s) slot
BANNER_LEN = 3 * WINDOW
BANNERS = ["BARK! BARK!", "WORKING!", "BARK BARK", "WORKING LIKE A DOG", "PAWS-ITIVELY BUSY",
           "RUFF DAY AT THE OFFICE", "FETCHING TOKENS", "SIT. STAY. CODE.", "HOUNDING BUGS",
           "DOGGEDLY DEBUGGING", "TERRIER-IFIC PROGRESS", "NO TIME TO PAWS",
           "WHO'S A GOOD COMMIT?", "BUSY AS A BEAGLE", "CHASING DEADLINES", "BONE TO BE WILD"]
_F = """A .###.#...##...#######...##...##...# B ####.#...##...#####.#...##...#####.
C .###.#...##....#....#....#...#.###. D ####.#...##...##...##...##...#####.
E ######....#....####.#....#....##### F ######....#....####.#....#....#....
G .###.#...##....#.####...##...#.#### H #...##...##...#######...##...##...#
I .###...#....#....#....#....#...###. J ..###...#....#....#....#.#..#..##..
K #...##..#.#.#..##...#.#..#..#.#...# L #....#....#....#....#....#....#####
M #...###.###.#.##.#.##...##...##...# N #...##...###..##.#.##..###...##...#
O .###.#...##...##...##...##...#.###. P ####.#...##...#####.#....#....#....
Q .###.#...##...##...##.#.##..#..##.# R ####.#...##...#####.#.#..#..#.#...#
S .#####....#.....###.....#....#####. T #####..#....#....#....#....#....#..
U #...##...##...##...##...##...#.###. V #...##...##...##...##...#.#.#...#..
W #...##...##...##.#.##.#.##.#.#.#.#. X #...##...#.#.#...#...#.#.#...##...#
Y #...##...#.#.#...#....#....#....#.. Z #####....#...#...#...#...#....#####
! ..#....#....#....#....#.........#.. ? .###.#...#....#...#...#.........#..
. ................................#.. , ...........................#...#...
' ..#....#...#....................... - ................###................
"""
FONT = {}
for _k, _v in zip(_F.split()[0::2], _F.split()[1::2]):
    FONT[_k] = [_v[i:i + 5] for i in range(0, 35, 5)]

def text_px(msg):
    """Block-letter pixels of msg: (width, [(row, col)])."""
    pts, x = [], 0
    for ch in msg.upper():
        if ch not in FONT:
            x += 4
            continue
        for r, row in enumerate(FONT[ch]):
            pts += [(r, x + c) for c, b in enumerate(row) if b == "#"]
        x += 6
    return x - 1, pts

BANNER_NOW = os.path.join(HOME, "banner_now")

def banner_at(sid, t, busy=True):
    """-> (start_time, message, direction) while a banner scene is running."""
    try:                                   # /walkies banner: play one right now
        t0 = float(open(BANNER_NOW).read())
        if t - t0 < BANNER_LEN:
            rnd = random.Random(t0)
            return t0, rnd.choice(BANNERS), rnd.choice(["lr", "rl", "tb", "bt"])
    except Exception:
        pass
    if not busy:
        return None
    win = int(t // WINDOW)
    k = win - win % 3
    rnd = random.Random(f"banner:{sid}:{k}")
    if rnd.random() >= BANNER_P:
        return None
    return k * WINDOW, rnd.choice(BANNERS), rnd.choice(["lr", "rl", "tb", "bt"])

def banner_frame(bn, t, track, lv):
    """-> (pixel grid, words) for one frame of a banner scene."""
    t0, msg, d = bn
    e = t - t0
    W = track + 22
    tw, pts = text_px(msg)
    if d in ("tb", "bt") and tw > W:
        d = "lr"
    g = [[None] * W for _ in range(10)]
    def paste(rows, x):
        for i, row in enumerate(rows):
            for j, c in enumerate(row):
                if c and 0 <= x + j < W:
                    g[i][x + j] = c
    c0 = int(t0 * SPEED) % (track * 2)
    pos0 = c0 if c0 < track else track * 2 - c0
    flee = 1 if d == "lr" or (d in ("tb", "bt") and pos0 > track / 2) else -1
    if d == "rl":
        flee = -1
    words = ""
    if e < 1:                                          # spots it
        paste(px_grid([("bark", 0)], [], flip=flee > 0, W=24, lv=lv, t=t), pos0)
        words = "!?"
    elif e < 4:                                        # bolts
        x = int(pos0 + flee * 40 * (e - 1))
        paste(px_grid([(("run0", "run1")[int(t) % 2], 0)], [], flip=flee < 0, W=24, lv=lv, t=t), x)
        words = ["RUN!!", "it's coming!", "nope nope nope"][int(e) - 1]
    elif e < BANNER_LEN - 2:                           # the message rolls through
        f = (e - 4) / (BANNER_LEN - 6)
        if d in ("lr", "rl"):
            ox = int(-tw + (W + tw) * f) if d == "lr" else int(W - (W + tw) * f)
            oy = 1
        else:
            ox = max(0, (W - tw) // 2)
            oy = int(-7 + 17 * f) if d == "tb" else int(10 - 17 * f)
        hue = lambda c: tuple(int(v * 255) for v in colorsys.hsv_to_rgb((c * 0.012 + t * 0.15) % 1, 0.75, 1.0))
        dark = lambda c: tuple(int(v * 255) for v in colorsys.hsv_to_rgb((c * 0.012 + t * 0.15) % 1, 0.8, 0.4))
        for r, c in pts:                                # drop shadow first, then the letters
            y, x = oy + r + 1, ox + c + 1
            if 0 <= y < 10 and 0 <= x < W:
                g[y][x] = dark(c)
        for r, c in pts:
            y, x = oy + r, ox + c
            if 0 <= y < 10 and 0 <= x < W:
                g[y][x] = hue(c)
    else:                                              # peeks back in
        x = int((W if flee > 0 else -22) - flee * 20 * (e - (BANNER_LEN - 2)))
        paste(px_grid([(("run0", "run1")[int(t) % 2], 0)], [], flip=flee > 0, W=24, lv=lv, t=t), x)
        words = "*peeks* is it gone?"
    return g, words

OFF = os.path.join(HOME, "off")

def toggle(arg):
    want = {"on": True, "off": False}.get(arg, os.path.exists(OFF))  # no arg = flip
    if want:
        if os.path.exists(OFF):
            os.remove(OFF)
        print("ON: the dog is back from the kennel and ready for walkies.")
    else:
        open(OFF, "w").close()
        print("OFF: the dog is in the kennel. XP keeps counting while it's away.")

# ---------------------------------------------------------------- reactions to tools
LABEL_CLEAN = re.compile(r"[\x00-\x1f\x7f]+")
TESTS = re.compile(r"\b(pytest|jest|vitest|rspec|phpunit|unittest|go test|cargo test|"
                   r"(npm|yarn|pnpm|bun)( run)? test|make test|tox|mocha)\b")
REACT_WORDS = {
    "sniff": ["*sniff sniff*", "*sniff* {label}", "hmm… {label}"],
    "dig":   ["*dig dig dig*", "digging: {label}", "*dig dig*"],
    "chew":  ["*chomp chomp*", "chewing on {label}", "*gnaw gnaw*"],
    "fetch": ["go fetch, buddy!", "fetching: {label}", "*zoom*"],
    "growl": ["GRR! BARK BARK!", "*growl* {label} bit me", "bad command! bad!"],
    "joy":   ["TESTS PASS!! 🎉", "*boing boing boing*", "who's a good dog? ME!"],
    "eat":   ["TREAT!!", "*nom nom nom*", "*crunch crunch*", "*licks bowl*"],
    "beg":   ["*whimper*", "treat? 🥺", "so… hungry…", "/walkies treat pls"],
}
GOAL_WORDS = ["GOAL ACHIEVED! 🎯", "treat earned! *nom nom*", "good human! 🦴", "*happy crunching*"]
ASCII_MAP = {"chew": "wag", "joy": "wag", "growl": "bark", "eat": "sniff",
             "beg": "wag", "fetch": "ball"}

def tool_kind(name):
    if name in ("Read", "Grep", "Glob", "LS", "WebFetch", "WebSearch", "NotebookRead", "LSP"):
        return "sniff"
    if name == "Bash":
        return "dig"
    if name in ("Edit", "Write", "MultiEdit", "NotebookEdit"):
        return "chew"
    if name in ("Agent", "Task"):
        return "fetch"
    return None

def tool_label(inp):
    path = inp.get("file_path") or inp.get("notebook_path")
    v = path or inp.get("pattern") or inp.get("command") or inp.get("url") \
        or inp.get("query") or inp.get("description") or ""
    v = os.path.basename(str(v)) if path else str(v)
    v = LABEL_CLEAN.sub(" ", v).strip()
    return v[:24] + ("…" if len(v) > 24 else "")

def react_hook(mode, data):
    sid = data.get("session_id", "default")
    p = os.path.join(TMP, f"claude-dog-tool-{sid}.json")
    name, inp = data.get("tool_name", ""), data.get("tool_input") or {}
    now = time.time()
    if mode == "tool":
        k = tool_kind(name)
        if k:
            save(p, {"kind": k, "label": tool_label(inp), "t": now, "end": None})
    elif mode == "toolfail":
        save(p, {"kind": "growl", "label": tool_label(inp) or name, "t": now, "end": now})
    else:
        if name == "Bash" and TESTS.search(str(inp.get("command", ""))):
            r = data.get("tool_response") or {}
            out = (str(r.get("stdout", "")) + str(r.get("stderr", ""))) if isinstance(r, dict) else str(r)
            if re.search(r"\b(passed|PASS|OK)\b|✓", out) and \
                    not re.search(r"\b([1-9]\d* (failed|errors?)|FAIL|FAILED)\b", out):
                save(p, {"kind": "joy", "label": "", "t": now, "end": now})
                return
        st = load(p, None)
        if st and st.get("end") is None:
            st["end"] = now
            save(p, st)

def reaction(sid, t):
    """Current tool reaction, a fresh treat, or None."""
    h = load(HUNGER, {})
    tr = h.get("treat_at", 0)
    goal = h.get("treat_why") == "goal"
    if t - tr < (15 if goal else 8):
        return {"kind": "eat", "label": "", "t": tr, "goal": goal}
    st = load(os.path.join(TMP, f"claude-dog-tool-{sid}.json"), None)
    if not st:
        return None
    end = st.get("end")
    if end is None:
        return st if t - st["t"] < 300 else None
    return st if t - end < (8 if st["kind"] in ("growl", "joy") else 3) else None

# ---------------------------------------------------------------- hunger & treats
HUNGER = os.path.join(HOME, "hunger.json")
FULL_HOURS = 8            # full bowl -> empty
FED_BONUS = 0.10          # +10% XP while more than half full

def fullness(t=None):
    h = load(HUNGER, None)
    if h is None:
        h = {"fed_at": time.time(), "treats": 0}
        save(HUNGER, h)
    t = t or time.time()
    return max(0.0, min(1.0, 1 - (t - h["fed_at"]) / (FULL_HOURS * 3600))), h

def give_treat():
    f, h = fullness()
    if f > 0.9:
        print(f"FULL: the dog sniffs the treat and flops over, already stuffed ({f:.0%} full).")
        return
    now = time.time()
    h.update(fed_at=now, treat_at=now, treat_why="manual", treats=h.get("treats", 0) + 1)
    save(HUNGER, h)
    print(f"TREAT: nom nom nom! Fullness {f:.0%} -> 100%. Treats given so far: {h['treats']}. "
          f"+{FED_BONUS:.0%} XP while it's over half full.")

def goal_treat(condition):
    """A /goal was met: the dog gets a treat, full or not (once per goal)."""
    h = load(HUNGER, {})
    key = hashlib.sha1(condition.encode()).hexdigest()[:12]
    done = h.get("goals_done", [])
    if key in done:
        return
    now = time.time()
    h.update(fed_at=now, treat_at=now, treat_why="goal", treats=h.get("treats", 0) + 1,
             goals=h.get("goals", 0) + 1, goals_done=(done + [key])[-200:])
    save(HUNGER, h)

def fed_bonus(tokens):
    if tokens > 0 and fullness()[0] > 0.5:
        a = load(ACH, {"got": {}, "bonus": 0, "last": None})
        add = int(tokens * FED_BONUS)
        a["bonus"] += add
        a["fed_bonus"] = a.get("fed_bonus", 0) + add
        save(ACH, a)

# ---------------------------------------------------------------- stats card
GEAR = [(10, "blue bandana"), (20, "spotted coat"), (30, "sunglasses"),
        (40, "superhero cape"), (50, "two-tone markings"), (70, "gold crown"),
        (80, "sparkles"), (100, "rainbow coat")]

def stats():
    now = time.time()
    xp = total_xp()
    lv = level_of(xp)
    a = load(ACH, {"got": {}, "bonus": 0})
    c = event_counts()
    main_tok = sub_tok = n_sess = n_sub = 0
    for name in os.listdir(SESS):
        if name.endswith(".json") and not name.endswith(".ev.json"):
            tot = load(os.path.join(SESS, name), {}).get("total", 0)
            if ".sub." in name:
                sub_tok += tot; n_sub += 1
            else:
                main_tok += tot; n_sess += 1
    days = max((now - installed_at()) / 86400, 1 / 24)
    rate = (main_tok + sub_tok) / days if days >= 1 else TOKENS_PER_DAY
    f, h = fullness()
    title = TITLES[min((lv - 1) // 10, 9)]
    W = 50
    out = ["STATS", "┌" + "─" * W + "┐"]
    def row(s=""):
        wide = sum(1 for ch in s if ord(ch) >= 0x1F000)      # emoji take 2 columns
        out.append("│ " + s.ljust(W - 2 - wide) + " │")
    row(f"🐕 Lv {lv} {title}")
    if lv < MAX_LEVEL:
        lo, hi = THRESH[lv - 1], THRESH[lv]
        row(f"   {bar((xp - lo) / (hi - lo), 20)} {fmt(hi - xp)} to Lv {lv + 1}")
    row()
    row(f"Total XP          {fmt(xp):>10}")
    row(f"  main sessions   {fmt(main_tok):>10}  ({n_sess} sessions)")
    row(f"  subagents       {fmt(sub_tok):>10}  ({n_sub} agents)")
    row(f"  bonus XP        {fmt(a.get('bonus', 0)):>10}  (treats {fmt(a.get('fed_bonus', 0))})")
    row(f"Pace              {fmt(rate):>10} tok/day")
    row(f"Playing for       {days:>9.1f} days")
    row()
    hunger = "stuffed" if f > 0.9 else "happy" if f > 0.5 else "peckish" if f > 0.2 else "HUNGRY"
    row(f"🍖 Belly  {bar(math.ceil(f * 10) / 10, 10)} {f:.0%} ({hunger}), {h.get('treats', 0)} treats"
        + (f", {h['goals']} 🎯" if h.get("goals") else ""))
    row(f"💧 {c.get('pee', 0)}   💩 {c.get('poop', 0)}")
    row()
    row(f"🏅 Badges {len(a.get('got', {}))}/{len(ACHIEVEMENTS)}")
    for key, name, _, bonus in ACHIEVEMENTS:
        if key in a.get("got", {}):
            row(f"   ✓ {name:<18} {time.strftime('%b %d', time.localtime(a['got'][key]))}")
        else:
            row(f"   · {name:<18} locked")
    row()
    row("🎁 Next gear")
    shown = 0
    for need, item in GEAR:
        if lv < need and shown < 3:
            d = (THRESH[need - 1] - xp) / max(rate, 1)
            when = time.strftime("%b %d %Y", time.localtime(now + d * 86400))
            row(f"   Lv {need:<3} {item:<18} ~{when}")
            shown += 1
    if not shown:
        row("   all gear unlocked. Legendary dog.")
    out.append("└" + "─" * W + "┘")
    print("\n".join(out))

def render(sid, transcript=None):
    if os.path.exists(OFF):
        return
    t = time.time()
    busy = os.path.exists(os.path.join(TMP, f"claude-dog-busy-{sid}"))
    try:   # interrupted turns never fire Stop: nap if the transcript went quiet
        if busy and t - os.path.getmtime(transcript) > 600:
            busy = False
    except Exception:
        pass
    xp = total_xp()
    lv = level_of(xp)
    D, O, BOLD, R = "\x1b[2m", "\x1b[1;38;5;208m", "\x1b[1m", "\x1b[0m"

    evp = os.path.join(SESS, f"{sid}.ev.json")
    ev = load(evp, {"counts": {}, "last_win": None, "last_lv": lv, "lvup_at": 0})
    if lv > ev.get("last_lv", lv):
        ev["lvup_at"] = t
    ev["last_lv"] = lv

    cols = term_cols()
    track = max(10, cols - RIGHT_RESERVE - (24 if style() == "pixel" else 14))
    react = reaction(sid, t)
    full, _ = fullness(t)
    bn = banner_at(sid, t, busy) if style() == "pixel" and not (
        react and react["kind"] in ("growl", "joy", "eat")) else None
    if bn:
        g, words = banner_frame(bn, t, track, lv)
        lines = px_lines(g, lv, t)
        if words:
            lines[0] += f"  {O}{words}{R}"
    elif busy or react:
        win = int(t // WINDOW)
        act = pick_activity(sid, win)
        if react:
            act = react["kind"]
            win = (t - ((t - react["t"]) % WINDOW)) / WINDOW     # scene clock starts at the event
        elif full < 0.2 and random.Random(f"beg:{sid}:{win}").random() < 0.3:
            act = "beg"
        if act in ("pee", "poop") and ev.get("last_win") != win:
            ev["counts"][act] = ev["counts"].get(act, 0) + 1
            ev["last_win"] = win
            save(evp, ev)
            achievements(act)
        when = react["t"] if react else win * WINDOW if act != "run" else t
        cyc = int(when * SPEED) % (track * 2)
        right = cyc < track
        pos = cyc if right else track * 2 - cyc
        aact = ASCII_MAP.get(act, act)
        if act == "fetch":
            act = "ball"
        if aact in SCENES:
            art, words = SCENES[aact](min(int(t - win * WINDOW), WINDOW - 1))
        elif act == "run":
            f = int(t) % 2
            art, words = RUN[f], BARKS[int(t / 2) % len(BARKS)]
        else:
            frames, says = ACT[aact]
            step = min(int(t - win * WINDOW) * len(frames) // WINDOW, len(frames) - 1) \
                if aact in ("pee", "poop") else int(t) % len(frames)
            art = frames[step]
            words = says[min(int(t - win * WINDOW) // 2, len(says) - 1)]
        if react or act == "beg":
            ws = GOAL_WORDS if (react or {}).get("goal") else REACT_WORDS[react["kind"] if react else "beg"]
            label = (react or {}).get("label") or "it"
            words = ws[int((t - (react["t"] if react else 0)) // 2) % len(ws)].format(label=label)
        if style() == "pixel":
            sprites, dots = px_pose(act, t, win)
            lines = px_lines(px_grid(sprites, dots, flip=not right, lv=lv, t=t), lv, t)
        else:
            lines = paint(art if right else mirror(art), lv, t)
        pad = " " * pos
        lines = [pad + l for l in lines]
        lines[0] += f"  {O}{words}{R}"
    else:
        if style() == "pixel":
            lines = ["  " + l for l in px_lines(px_grid([("nap", 0)], [], lv=lv, t=t + 1), lv, t)[2:]]
            lines[0] += f"  {D}{['z', 'z Z', 'z Z z'][int(t) % 3]}{R}"
        else:
            lines = ["  " + l for l in paint(NAP[int(t) % 3], lv, t)]
        lines[-1] += f"  {D}{'dreaming of treats…' if full < 0.2 else 'napping…'}{R}"

    save(evp, ev)

    # HUD
    title = TITLES[min((lv - 1) // 10, 9)]
    if lv >= MAX_LEVEL:
        prog = f"{BOLD}MAX LEVEL{R}"
    else:
        lo, hi = THRESH[lv - 1], THRESH[lv]
        prog = f"{bar((xp - lo) / (hi - lo))} {D}{fmt(hi - xp)} tok to Lv {lv+1}{R}"
    c = event_counts()
    extras = "".join(f"  {e}{c[k]}" for k, e in (("pee", "💧"), ("poop", "💩")) if c.get(k))
    ach = achievements()
    if ach["got"]:
        extras += f"  🏅{len(ach['got'])}"
    if full < 0.2:
        extras += f"  {O}🥺{bar(full, 5)} hungry{R}"
    else:
        extras += f"  🍖{bar(math.ceil(full * 5) / 5, 5)}" + (f" {D}+{FED_BONUS:.0%}{R}" if full > 0.5 else "")
    hud = f"{BOLD}Lv {lv}{R} {title}  {prog}  {D}{fmt(xp)} XP{R}{extras}"
    last = ach.get("last")
    if t - ev.get("lvup_at", 0) < 15:
        hud = f"{O}★ LEVEL UP! ★{R} " + hud
    elif last and t - last[2] < 15:
        hud = f"{O}🏅 {last[0]}! +{fmt(last[1])} XP{R}  " + hud
    print("\n".join(lines + [hud]), end="")

def main():
    os.makedirs(SESS, exist_ok=True)
    installed_at()
    mode = sys.argv[1] if len(sys.argv) > 1 else "render"
    if mode == "toggle" and len(sys.argv) > 2 and sys.argv[2].lower() in ("pixel", "ascii"):
        with open(STYLE, "w") as f:
            f.write(sys.argv[2].lower())
        print(f"STYLE: the dog is now drawn in {sys.argv[2].lower()} mode.")
        return
    arg = sys.argv[2].lower() if len(sys.argv) > 2 else ""
    if mode == "toggle" and arg == "treat":
        give_treat()
        return
    if mode == "toggle" and arg == "banner":
        with open(BANNER_NOW, "w") as f:
            f.write(str(time.time() + 2))
        print("BANNER: incoming! The dog has spotted something big on the horizon.")
        return
    if mode == "toggle" and arg == "stats":
        stats()
        return
    if mode == "toggle":
        toggle(sys.argv[2].lower() if len(sys.argv) > 2 else "")
        return
    if mode == "table":
        for n in range(1, MAX_LEVEL + 1):
            print(f"Lv {n:3}  {fmt(THRESH[n-1]):>7} XP  (~{THRESH[n-1]/TOKENS_PER_DAY:6.0f} days)  "
                  f"{TITLES[min((n-1)//10, 9)]}")
        return
    try:
        data = json.loads(sys.stdin.read() or "{}")
    except Exception:
        data = {}
    if mode in ("tool", "tooldone", "toolfail"):
        try:
            react_hook(mode, data)
        except Exception:
            pass
        return
    sid = data.get("session_id", "default")
    flag = os.path.join(TMP, f"claude-dog-busy-{sid}")
    if mode == "busy":
        open(flag, "w").close()
        score(sid, data.get("transcript_path"))
    elif mode == "idle":
        try:
            os.remove(flag)
        except FileNotFoundError:
            pass
        score(sid, data.get("transcript_path"))
    else:
        render(sid, data.get("transcript_path"))

if __name__ == "__main__":
    main()
