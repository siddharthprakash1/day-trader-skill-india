"""Render a recorded terminal session (a scenes file) as SVG pictures for the README.

The scenes file holds real tool output, cut but not edited:
    === scene title
    $ command that was run
    what it printed
Writes one animated SVG that plays every scene in turn (a recording that GitHub
shows inline), plus one still SVG per group of scenes. Standard library only.

    python media/render.py media/replay-2026-11-26.txt
"""
import io
import os
import sys
from xml.sax.saxutils import escape

COLS = 116            # terminal width in characters; longer lines wrap
FONT = 13             # px
CW = 7.83             # px per character at FONT (monospace)
LH = 18               # line height, px
PAD = 18
BAR = 58              # title bar plus the replay banner
BANNER = ("REPLAY of Thu 26 Nov 2026 on real Yahoo 5-minute bars, run after the close. "
          "Practice trades only, not live.")
STEP = 0.11           # seconds between lines in the animation
HOLD = 4.5            # seconds a finished scene stays on screen
STILLS = {"scan": [1], "trade": [2, 3, 4], "scoreboard": [5, 6]}

COLORS = {"text": "#d6deeb", "dim": "#7f8ea3", "cmd": "#7fdbca", "prompt": "#c792ea",
          "good": "#addb67", "bad": "#ef5350", "warn": "#ffcb6b", "title": "#82aaff",
          "bg": "#0b1622", "bar": "#13202f", "banner": "#ffcb6b"}


def read_scenes(path):
    scenes = []
    with io.open(path, encoding="utf-8") as fh:
        for raw in fh.read().splitlines():
            if raw.startswith("=== "):
                scenes.append((raw[4:], []))
            elif scenes:
                scenes[-1][1].append(raw)
    return scenes


def wrap(line):
    """Soft-wrap like a terminal: continuation rows start two spaces in."""
    if line.startswith("$ "):
        kind = "cmd"
    else:
        kind = "out"
    rows, first = [], True
    while True:
        width = COLS if first else COLS - 2
        if len(line) <= width:
            rows.append((("" if first else "  ") + line, kind, first))
            return rows
        cut = line.rfind(" ", 0, width + 1)
        if cut <= width // 2:
            cut = width
        rows.append((("" if first else "  ") + line[:cut].rstrip(), kind, first))
        line = line[cut:].lstrip()
        first = False


def color_of(row, kind, first, prev_kind):
    s = row.strip()
    if kind == "cmd":
        return "cmd"
    if s.startswith(("PASS", "TARGET", "TRIGGERED", "Every order passed")):
        return "good"
    if s.startswith(("BLOCK", "STOP")):
        return "bad"
    if s.startswith(("FLAT", "Close to break-even", "Leans toward")):
        return "warn"
    if s.startswith(("A stop is a plan", "Simulated =", "Numbers only", "Education and tooling",
                     "Only 2 scored", "A scan is a list", "Rates:", "Set before the entry")):
        return "dim"
    return "text"


def tspans(row, y, color):
    """One <text> per row, cut at each rupee sign so the columns stay aligned in any font."""
    parts, buf, start = [], "", 0
    for i, ch in enumerate(row):
        if ch == "₹":
            if buf:
                parts.append((start, buf))
            parts.append((i, ch))
            buf, start = "", i + 1
        else:
            if not buf:
                start = i
            buf += ch
    if buf:
        parts.append((start, buf))
    out = []
    for col, txt in parts:
        if not txt.strip() and txt != "₹":
            continue
        out.append('<tspan x="%.1f" textLength="%.1f" lengthAdjust="spacing">%s</tspan>'
                   % (PAD + col * CW, len(txt) * CW, escape(txt)))
    return '<text y="%d" fill="%s">%s</text>' % (y, COLORS[color], "".join(out))


def scene_rows(title, body):
    rows = [("# " + title, "title")]
    prev = None
    for line in body:
        for r, kind, first in wrap(line):
            if kind == "cmd" and first:
                rows.append(("$ " + r[2:], "cmd"))
            else:
                rows.append((r, color_of(r, kind, first, prev)))
        prev = kind
    return rows


def render_row(row, color, y):
    if color == "cmd":
        return ('<text y="%d"><tspan x="%.1f" fill="%s">$</tspan><tspan x="%.1f" fill="%s" textLength="%.1f" lengthAdjust="spacing">%s</tspan></text>'
                % (y, PAD, COLORS["prompt"], PAD + 2 * CW, COLORS["cmd"], (len(row) - 2) * CW, escape(row[2:])))
    if color == "title":
        return '<text y="%d" fill="%s" font-weight="bold">%s</text>' % (y, COLORS["title"], escape(row))
    return tspans(row, y, color)


def frame(width, height, inner, title):
    dots = "".join('<circle cx="%d" cy="16" r="5.5" fill="%s"/>' % (20 + 18 * i, c)
                   for i, c in enumerate(("#ff5f57", "#febc2e", "#28c840")))
    return """<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" viewBox="0 0 {w} {h}" role="img" aria-label="{t}">
<title>{t}</title>
<style>text{{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,"DejaVu Sans Mono","Liberation Mono",monospace;font-size:{fs}px;white-space:pre}}</style>
<rect width="{w}" height="{h}" rx="10" fill="{bg}"/>
<path d="M0 10a10 10 0 0 1 10-10h{iw}a10 10 0 0 1 10 10v{bh}H0z" fill="{bar}"/>
{dots}
<text x="{cx}" y="21" fill="{dim}" text-anchor="middle">{t}</text>
<text x="{pad}" y="46" fill="{banner}" font-weight="bold">{banner_text}</text>
{inner}
</svg>
""".format(w=width, h=height, iw=width - 20, bh=BAR - 10, bg=COLORS["bg"], bar=COLORS["bar"], dots=dots,
           cx=width // 2, dim=COLORS["dim"], t=escape(title), pad=PAD, banner=COLORS["banner"],
           banner_text=escape(BANNER), fs=FONT, inner=inner)


def width_px():
    return int(PAD * 2 + COLS * CW + 4)


def still(scenes, numbers, title):
    rows = []
    for n in numbers:
        if rows:
            rows.append(("", "text"))
        rows.extend(scene_rows(*scenes[n - 1]))
    h = BAR + PAD + len(rows) * LH + PAD
    inner = "\n".join(render_row(r, c, BAR + PAD + 12 + i * LH) for i, (r, c) in enumerate(rows))
    return frame(width_px(), h, inner, title)


def animated(scenes, title):
    all_rows = [scene_rows(t, b) for t, b in scenes]
    tallest = max(len(r) for r in all_rows)
    starts, t = [], 0.0
    for rows in all_rows:
        starts.append(t)
        t += len(rows) * STEP + HOLD
    total = t
    css, groups = [], []
    for k, rows in enumerate(all_rows):
        end = (starts[k + 1] if k + 1 < len(all_rows) else total) / total * 100
        lines = []
        for i, (r, c) in enumerate(rows):
            on = (starts[k] + i * STEP) / total * 100
            name = "s%dl%d" % (k, i)
            css.append("@keyframes %s{0%%,%.3f%%{opacity:0}%.3f%%,%.3f%%{opacity:1}%.3f%%,100%%{opacity:0}}"
                       ".%s{opacity:0;animation:%s %.2fs linear infinite}"
                       % (name, on, on + 0.01, end - 0.01, end, name, name, total))
            row = render_row(r, c, BAR + PAD + 12 + i * LH)
            lines.append(row.replace("<text ", '<text class="%s" ' % name, 1))
        groups.append("\n".join(lines))
    h = BAR + PAD + tallest * LH + PAD
    inner = "<style>%s</style>\n%s" % ("\n".join(css), "\n".join(groups))
    return frame(width_px(), h, inner, title)


def main(path):
    scenes = read_scenes(path)
    base = os.path.splitext(path)[0]
    out = {base + ".svg": animated(scenes, "day-trader-skill-india: one practice trade, start to finish")}
    for name, nums in STILLS.items():
        out["%s-%s.svg" % (base, name)] = still(scenes, nums, "day-trader-skill-india: " + name)
    for p, svg in out.items():
        with io.open(p, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(svg)
        print("wrote %s (%d KB)" % (p, len(svg.encode("utf-8")) // 1024))


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(__file__), "replay-2026-11-26.txt"))
