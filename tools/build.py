#!/usr/bin/env python3
"""Build the eatsranked.com hub site into ../docs.

Everything on the page comes from tools/site-data.json (states, taglines,
links, App Store status, colours, icons, BLS price series, source line) and
tools/us-states-paths.json (state shapes). Nothing about a state or a price
is typed into the template. Run it after editing site-data.json:

    python3 tools/build.py
    python3 tools/build.py --data other.json --dry-run   # check that a data file builds; writes nothing

Writes docs/index.html, docs/404.html, docs/sitemap.xml (lastmod moves only
when index.html actually changes), docs/robots.txt, docs/CNAME,
docs/site.webmanifest and docs/.well-known/security.txt. The page makes no
requests to other hosts: styles, script, map and chart are all inline; icons
are local files. Each page carries a Content-Security-Policy that allows only
its own inline scripts, by hash, recomputed on every build.
"""
import base64
import colorsys
import datetime
import hashlib
import html
import json
import math
import pathlib
import re
import sys

TOOLS = pathlib.Path(__file__).resolve().parent
ROOT = TOOLS.parent
DOCS = ROOT / "docs"

ARGS = sys.argv[1:]
DATA = pathlib.Path(ARGS[ARGS.index("--data") + 1]) if "--data" in ARGS else TOOLS / "site-data.json"
DRY_RUN = "--dry-run" in ARGS

SITE = json.loads(DATA.read_text())
GEO = json.loads((TOOLS / "us-states-paths.json").read_text())
TEMPLATE = (TOOLS / "template.html").read_text()
TEMPLATE_404 = (TOOLS / "template-404.html").read_text()
APP_JS = (TOOLS / "app.js").read_text()

BRAND = SITE["brand"]
DOMAIN = BRAND["domain"]
URL = f"https://{DOMAIN}/"
EMAIL = BRAND["email"]
REPO = "https://github.com/nickstrom5/eatsranked"   # the site's public source, the Organization's sameAs
SECURITY_TXT_EXPIRES = "2027-10-01T00:00:00Z"       # RFC 9116: under a year ahead; verify.py fails once it passes


def esc(s):
    return html.escape(str(s), quote=True)


def jsonscript(obj, **kw):
    """JSON that is safe inside a <script> element."""
    return json.dumps(obj, ensure_ascii=False, **kw).replace("</", "<\\/")


def and_list(items):
    items = list(items)
    if len(items) <= 1:
        return "".join(items)
    if len(items) == 2:
        return f"{items[0]} and {items[1]}"
    return ", ".join(items[:-1]) + f" and {items[-1]}"


# ====================================================================== prices
MONTHS = ["January", "February", "March", "April", "May", "June", "July",
          "August", "September", "October", "November", "December"]


def month_long(ym):
    y, m = ym.split("-")
    return f"{MONTHS[int(m) - 1]} {y}"


def month_short(ym):
    y, m = ym.split("-")
    return f"{MONTHS[int(m) - 1][:3]} {y}"


def month_range(a, b):
    y, m = map(int, a.split("-"))
    out = []
    while f"{y}-{m:02d}" <= b:
        out.append(f"{y}-{m:02d}")
        m += 1
        if m == 13:
            y, m = y + 1, 1
    return out


def value(v):
    """A monthly value; BLS publishes "-" (stored as null) for months it skipped."""
    if v is None or v == "-":
        return None
    return float(v)


P = SITE["prices"]
SER = {sid: {m: value(v) for m, v in s["monthly"]} for sid, s in P["series"].items()}

REST = "CUUR0000SEFV"      # restaurants and takeout (food away from home), U.S.
FULL = "CUUR0000SEFV01"    # full-service restaurants
FAST = "CUUR0000SEFV02"    # limited-service
GROC = "CUUR0000SAF11"     # groceries (food at home)
ALL = "CUUR0000SA0"        # all items
CHI = "CUURS23ASEFV"       # food away from home, Chicago-Naperville-Elgin
MW = "CUUR0200SEFV"        # food away from home, Midwest
BASE = "2020-01"

CORE = (REST, FULL, FAST, GROC, ALL)   # the chart, the $20 bill and the two since-2020 stats
TWELVE = (CHI, MW, REST)              # the 12-month stat


def year_before(ym):
    return f"{int(ym[:4]) - 1}{ym[4:]}"


def has(sid, m):
    return SER[sid].get(m) is not None


# the latest month every core series has
LAST = max((m for m in SER[REST] if all(has(sid, m) for sid in CORE)), default=None)
if LAST is None:
    sys.exit("build: no month has a value in every price series")
for sid in CORE + TWELVE:
    if not has(sid, BASE):
        sys.exit(f"build: series {sid} has no value for {BASE}")
# The 12-month change needs the same month a year earlier. BLS skipped October 2025, so when the
# latest month is October 2026 there is no year-ago value; use the latest month that has one.
TWELVE_TO = max((m for m in SER[REST] if m <= LAST and all(has(sid, m) and has(sid, year_before(m)) for sid in TWELVE)),
                default=None)
if TWELVE_TO is None:
    sys.exit("build: no month has a year-ago value in the Chicago, Midwest and U.S. series")
YEAR_AGO = year_before(TWELVE_TO)


def pct(sid, a, b):
    return 100.0 * (SER[sid][b] / SER[sid][a] - 1.0)


def fmt_pct(x):
    s = f"{abs(x):.1f}"
    if s == "0.0":
        return "0.0%"
    return ("+" if x > 0 else "−") + s + "%"


def moved(x):
    return f"{'rose' if x >= 0 else 'fell'} {abs(x):.1f}%"


F = {
    "rest": pct(REST, BASE, LAST), "all": pct(ALL, BASE, LAST), "groc": pct(GROC, BASE, LAST),
    "fast": pct(FAST, BASE, LAST), "full": pct(FULL, BASE, LAST),
    "chi12": pct(CHI, YEAR_AGO, TWELVE_TO), "mw12": pct(MW, YEAR_AGO, TWELVE_TO), "us12": pct(REST, YEAR_AGO, TWELVE_TO),
}
BILL = 20.0 * SER[REST][LAST] / SER[REST][BASE]
SINCE = month_long(BASE)
LAST_LONG = month_long(LAST)
TWELVE_LONG = month_long(TWELVE_TO)


def big(x):
    s = fmt_pct(x)
    return f'{s[:-1]}<span class="u">%</span>'


BILL_HTML = (f"A $20.00 restaurant bill from {esc(SINCE)} comes to "
             f"<b>${BILL:.2f}</b> at {esc(LAST_LONG)} prices.")

FACTS = f"""      <div class="fact">
        <dt>{big(F['rest'])}</dt>
        <dd><b>Restaurant and takeout prices</b> since {SINCE}. All prices {moved(F['all'])}; groceries {moved(F['groc'])}.</dd>
      </div>
      <div class="fact">
        <dt>{big(F['fast'])}</dt>
        <dd><b>Fast food and counter service</b> since {SINCE}, {'outpacing' if F['fast'] > F['full'] else 'compared with'} sit-down restaurants ({fmt_pct(F['full'])}).</dd>
      </div>
      <div class="fact">
        <dt>{big(F['chi12'])}</dt>
        <dd><b>Chicago-area restaurant prices</b> in the 12 months to {TWELVE_LONG}. Midwest: {fmt_pct(F['mw12'])}. U.S.: {fmt_pct(F['us12'])}.</dd>
      </div>"""

# ---------------------------------------------------------------- chart
MONTHS_AXIS = month_range(BASE, LAST)
N = len(MONTHS_AXIS)
CHART = [("rest", REST, "Restaurants and takeout", "Restaurants"),
         ("groc", GROC, "Groceries", "Groceries"),
         ("all", ALL, "All prices", "All prices")]
CHG = {}
for key, sid, _, _ in CHART:
    b = SER[sid][BASE]
    CHG[key] = [(100.0 * (SER[sid][m] / b - 1.0) if SER[sid].get(m) is not None else None) for m in MONTHS_AXIS]
MISSING = [m for i, m in enumerate(MONTHS_AXIS) if CHG["rest"][i] is None]

vals = [v for k in CHG for v in CHG[k] if v is not None]
lo, hi = min(vals), max(vals)
YMIN = min(0.0, lo) - 4.0
YMAX = max(hi + 4.0, math.ceil(hi / 10.0) * 10.0 + 1.0)
TICKS = list(range(int(math.ceil(YMIN / 10.0)) * 10, int(math.floor(YMAX / 10.0)) * 10 + 1, 10))


def X(i):
    return 1000.0 * i / (N - 1)


def Y(v):
    return 1000.0 * (YMAX - v) / (YMAX - YMIN)


def xp(i):
    return f"{100.0 * i / (N - 1):.3f}%"


def yp(v):
    return f"{100.0 * (YMAX - v) / (YMAX - YMIN):.3f}%"


def tick_label(t):
    return "0%" if t == 0 else f"{'+' if t > 0 else chr(0x2212)}{abs(t)}%"


svg = []
for t in TICKS:
    svg.append(f'<line class="grid{" base" if t == 0 else ""}" x1="0" x2="1000" y1="{Y(t):.1f}" y2="{Y(t):.1f}"/>')
step = 1000.0 / (N - 1)
for m in MISSING:
    i = MONTHS_AXIS.index(m)
    svg.append(f'<rect class="gap" x="{X(i) - step / 2:.2f}" y="0" width="{step:.2f}" height="1000"/>')
for key, _, _, _ in reversed(CHART):
    d, pen = [], False
    for i, v in enumerate(CHG[key]):
        if v is None:
            pen = False
            continue
        d.append(f"{'L' if pen else 'M'}{X(i):.2f},{Y(v):.2f}")
        pen = True
    svg.append(f'<path class="ln {key}" d="{"".join(d)}"/>')
CHART_SVG = "\n".join("            " + s for s in svg)

chart_html = []
for m in MISSING:
    i = MONTHS_AXIS.index(m)
    chart_html.append(f'          <span class="gapnote" style="left:{xp(i - 0.5)};top:{yp(0)}">{esc(month_short(m))}<br>not published</span>')
for key, _, _, _ in CHART:
    chart_html.append(f'          <span class="dot {key}" style="left:100%;top:{yp(CHG[key][-1])}"></span>')
CHART_HTML = "\n".join(chart_html)

YTICKS = "".join(f'<span style="top:{yp(t)}">{tick_label(t)}</span>' for t in TICKS)
XTICKS = "".join(
    f'<span class="{"odd" if int(m[:4]) % 2 else "even"}" style="left:{xp(i)}">{m[:4]}</span>'
    for i, m in enumerate(MONTHS_AXIS) if m.endswith("-01"))

# end labels: at each line's last value, nudged apart so they never overlap
ends = sorted(({"k": k, "short": short, "v": CHG[k][-1], "y": 100.0 * (YMAX - CHG[k][-1]) / (YMAX - YMIN)}
               for k, _, _, short in CHART), key=lambda e: e["y"])
GAP = 5.2  # percent of the plot height
for j in range(1, len(ends)):
    ends[j]["y"] = max(ends[j]["y"], ends[j - 1]["y"] + GAP)
over = ends[-1]["y"] - 100.0
if over > 0:
    for e in ends:
        e["y"] -= over
ENDS = "".join(f'<span style="top:{e["y"]:.3f}%"><b>{fmt_pct(e["v"])}</b><span class="nm"> {esc(e["short"])}</span></span>'
               for e in ends)

rows = {m for m in MONTHS_AXIS if m.endswith("-01")} | {LAST}
for m in MISSING:
    i = MONTHS_AXIS.index(m)
    rows |= {MONTHS_AXIS[j] for j in (i - 1, i, i + 1) if 0 <= j < N}
tbl = ['<table><caption>Change in prices since ' + esc(SINCE) + ', U.S. city average</caption>'
       '<thead><tr><th scope="col">Month</th>'
       + "".join(f'<th scope="col">{esc(short)}</th>' for _, _, _, short in CHART) + "</tr></thead><tbody>"]
for m in sorted(rows):
    i = MONTHS_AXIS.index(m)
    cells = "".join(f"<td>{fmt_pct(CHG[k][i])}</td>" if CHG[k][i] is not None else '<td class="na">not published</td>'
                    for k, _, _, _ in CHART)
    tbl.append(f'<tr><th scope="row">{esc(month_short(m))}</th>{cells}</tr>')
tbl.append("</tbody></table>")
TABLE = "".join(tbl)

outpaced = F["rest"] > F["all"]
CHART_TITLE = (f"Restaurant prices have outpaced inflation since {BASE[:4]}" if outpaced
               else f"Restaurant prices and inflation since {BASE[:4]}")
CHART_SUB = f"Change in prices since {SINCE}, U.S. city average, monthly to {LAST_LONG}."
gap_words = and_list(month_long(m) for m in MISSING)
CHART_ALT = (f"Line chart of price changes from {SINCE} to {LAST_LONG}. "
             f"Restaurants and takeout {fmt_pct(CHG['rest'][-1])}, groceries {fmt_pct(CHG['groc'][-1])}, "
             f"all prices {fmt_pct(CHG['all'][-1])}."
             + (f" No data for {gap_words}." if MISSING else "")
             + " Use the left and right arrow keys to read each month.")
CHART_DATA = {
    "m": [month_short(m) for m in MONTHS_AXIS],
    "s": [{"k": k, "n": n, "sn": sn, "v": [None if v is None else fmt_pct(v) for v in CHG[k]]} for k, _, n, sn in CHART],
    "since": month_short(BASE),
}

# ====================================================================== states
NAMES = {s["abbr"]: s["name"] for s in GEO["states"]}
LIVE = [s for s in SITE["states"] if s.get("status") == "live"]
LIVE_BY = {s["abbr"]: s for s in LIVE}
for s in LIVE:
    if s["abbr"] not in NAMES:
        sys.exit(f"build: {s['abbr']} is not in us-states-paths.json")
    if not (DOCS / s["icon"]).is_file():
        sys.exit(f"build: missing icon docs/{s['icon']}")
LIVE_NAMES = and_list(NAMES[s["abbr"]] for s in LIVE)
# "Live now" only once every live app has its App Store link; until then the websites are what's live.
ON_STORE = all(s.get("app_store") for s in LIVE)
LIVE_LABEL = "Live now" if ON_STORE else "On the web now"
VB = [float(v) for v in GEO["viewBox"].split()]
VBW, VBH = VB[2], VB[3]

ARROW_OUT = ('<svg class="ic" viewBox="0 0 14 14" aria-hidden="true"><path d="M4 3h7v7M11 3 3 11" fill="none" '
             'stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg>')
CHEVRON = ('<svg class="go" viewBox="0 0 16 16" aria-hidden="true"><path d="M6 3.5 10.5 8 6 12.5" fill="none" '
           'stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg>')
CLOSE = ('<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M4 4l8 8M12 4l-8 8" fill="none" '
         'stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/></svg>')


def where(s):
    return s.get("region") or NAMES[s["abbr"]]


def platforms(s):
    a, _, b = s["platforms"].partition(",")
    return f"{esc(a.strip())} · {esc(b.strip().capitalize())}" if b.strip() else esc(a)


def luminance(hexc):
    """WCAG relative luminance of #RRGGBB."""
    def lin(v):
        v /= 255
        return v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4
    r, g, b = (int(hexc[i:i + 2], 16) for i in (1, 3, 5))
    return 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b)


def contrast(a, b):
    hi, lo = sorted((luminance(a), luminance(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


# 4.5:1 for the region label on the card and the card colour on the Website button (WCAG 1.4.3); the focus
# ring, drawn on the card in the same colour, needs 3:1 (WCAG 1.4.11), so it passes too. A shade that has to be
# made aims a little higher (TEXT_AIM) so a later tweak like a slight opacity can't tip it under the floor
TEXT_MIN = 4.5
TEXT_AIM = 4.8


def readable_accent(accent, bg):
    """The accent as cards and pop-ups use it for text, the button fill and focus rings. A state's own accent
    is used as is when it reads at 4.5:1 on its card; otherwise the nearest lighter or darker shade with the
    same hue and saturation that reads at TEXT_AIM. The map keeps the brand accent (its dark-mode fill is decoration)."""
    if contrast(accent, bg) >= TEXT_MIN:
        return accent
    h, l, sat = colorsys.rgb_to_hls(*(int(accent[i:i + 2], 16) / 255 for i in (1, 3, 5)))
    for step in range(1, 201):
        for ll in (l + step / 200, l - step / 200):
            if 0 <= ll <= 1:
                c = "#" + "".join(f"{round(v * 255):02X}" for v in colorsys.hls_to_rgb(h, ll, sat))
                if contrast(c, bg) >= TEXT_AIM:
                    return c
    sys.exit(f"build: no shade of {accent} reads at {TEXT_AIM}:1 on {bg}; pick another card colour")


ACCENT = {s["abbr"]: readable_accent(s["colors"]["accent"], s["colors"]["bg"]) for s in LIVE}


def colors(s):
    c = s["colors"]
    return f'--bg:{c["bg"]};--ac:{ACCENT[s["abbr"]]};--fg:{c["text"]}'


def ext(href):
    return f'href="{esc(href)}" rel="noopener"'


def store(s):
    """App Store badge: a link once site-data has the App Store URL, a quiet label until then."""
    if s.get("app_store"):
        return (f'<a class="badge" {ext(s["app_store"])}><small>Download on the</small><b>App Store</b>'
                f'<span class="sr-only"> ({esc(s["app"])})</span></a>')
    note = s.get("app_store_note") or "Coming soon to the App Store"
    return f'<span class="badge soon" title="{esc(note)}"><small>Coming soon to the</small><b>App Store</b></span>'


def more_links(s):
    out = []
    if s.get("how") and s["how"].rstrip("/") != s["site"].rstrip("/"):
        out.append(f'<a class="more" {ext(s["how"])}>How it works<span class="sr-only"> in {esc(s["app"])}</span> '
                   f'<span aria-hidden="true">&rarr;</span></a>')
    if s.get("web_app"):
        out.append(f'<a class="more" {ext(s["web_app"])}>{esc(s.get("web_app_label") or "Search on the web")}'
                   f'<span class="sr-only"> with {esc(s["app"])}</span> <span aria-hidden="true">&rarr;</span></a>')
    return f'<div class="links">{"".join(out)}</div>' if out else ""


def website(s):
    return (f'<a class="btn" {ext(s["site"])}>Website<span class="sr-only"> for {esc(s["app"])}</span> {ARROW_OUT}</a>')


cards = []
for s in LIVE:
    a = s["abbr"].lower()
    cards.append(f"""      <article class="app" id="app-{a}" style="{colors(s)}" aria-labelledby="app-{a}-h">
        <div class="head">
          <img class="icon" src="{esc(s['icon'])}" width="88" height="88" alt="{esc(s['app'])} app icon" decoding="async">
          <div><p class="where">{esc(where(s))}</p><p class="plat">{platforms(s)}</p></div>
        </div>
        <h3 id="app-{a}-h">{esc(s['app'])}</h3>
        <p class="tag">{esc(s['tagline'])}</p>
        <div class="actions">
          {website(s)}
          {store(s)}
        </div>
        {more_links(s)}
      </article>""")
CARDS = "\n".join(cards)


# ---------------------------------------------------------------- map shapes
def rings(d):
    out, cur = [], None
    for cmd, body in re.findall(r"([MLZ])([^MLZ]*)", d):
        if cmd == "M":
            cur = []
            out.append(cur)
        if cmd in "ML":
            nums = [float(x) for x in re.findall(r"-?\d+(?:\.\d+)?", body)]
            cur.extend(zip(nums[0::2], nums[1::2]))
    return [r for r in out if len(r) >= 3]


def area(r):
    return 0.5 * sum(x0 * y1 - x1 * y0 for (x0, y0), (x1, y1) in zip(r, r[1:] + r[:1]))


def inside(pt, r):
    x, y = pt
    c = False
    for (x0, y0), (x1, y1) in zip(r, r[1:] + r[:1]):
        if (y0 > y) != (y1 > y) and x < (x1 - x0) * (y - y0) / (y1 - y0) + x0:
            c = not c
    return c


def edge_dist(pt, r):
    x, y = pt
    best = float("inf")
    for (x0, y0), (x1, y1) in zip(r, r[1:] + r[:1]):
        dx, dy = x1 - x0, y1 - y0
        L = dx * dx + dy * dy
        t = 0.0 if L == 0 else max(0.0, min(1.0, ((x - x0) * dx + (y - y0) * dy) / L))
        px, py = x0 + t * dx - x, y0 + t * dy - y
        best = min(best, px * px + py * py)
    return math.sqrt(best)


def pole(d):
    """The point deepest inside a state's largest piece: where its icon and label sit."""
    r = max(rings(d), key=lambda r: abs(area(r)))
    xs, ys = [p[0] for p in r], [p[1] for p in r]
    x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
    best, bd = ((x0 + x1) / 2, (y0 + y1) / 2), -1.0
    cell = max(x1 - x0, y1 - y0) / 40.0
    cx, cy = best
    span = max(x1 - x0, y1 - y0)
    for _ in range(3):
        n = int(span / 2 / cell) + 1
        for i in range(-n, n + 1):
            for j in range(-n, n + 1):
                p = (cx + i * cell, cy + j * cell)
                if x0 <= p[0] <= x1 and y0 <= p[1] <= y1 and inside(p, r):
                    dd = edge_dist(p, r)
                    if dd > bd:
                        best, bd = p, dd
        cx, cy = best
        span, cell = cell * 2, cell / 5.0
    return round(best[0], 1), round(best[1], 1)


def compact(d):
    """Same shape, fewer bytes: tenths of a unit, relative line-tos."""
    def num(t):
        s = f"{abs(t) / 10:.1f}".rstrip("0").rstrip(".")
        if s.startswith("0."):
            s = s[1:]
        return ("-" if t < 0 else "") + (s or "0")

    out = []
    for r in rings(d):
        pts = [(round(x * 10), round(y * 10)) for x, y in r]
        seg = f"M{num(pts[0][0])},{num(pts[0][1])}l"
        px, py = pts[0]
        first = True
        for x, y in pts[1:]:
            dx, dy = x - px, y - py
            if dx == 0 and dy == 0:
                continue
            a, b = num(dx), num(dy)
            if not first and not a.startswith("-"):
                seg += " "
            seg += a + ("" if b.startswith("-") else ",") + b
            px, py, first = x, y, False
        out.append(seg + "z")
    return "".join(out)


def adjacency():
    """States that share a border (at least two boundary points in common)."""
    pts = {}
    for st in GEO["states"]:
        pts[st["abbr"]] = {(round(x, 1), round(y, 1)) for r in rings(st["d"]) for x, y in r}
    return {a: sorted(b for b in pts if b != a and len(pts[a] & pts[b]) >= 2) for a in pts}


ADJ = adjacency()
shapes, live_shapes, pins = [], [], []
for st in GEO["states"]:
    a = st["abbr"]
    x, y = pole(st["d"])
    d = compact(st["d"])
    adj = f' data-adj="{" ".join(ADJ[a])}"' if ADJ[a] else ""
    if a in LIVE_BY:
        s = LIVE_BY[a]
        c = s["colors"]
        label = f'{st["name"]}: {s["app"]}, {LIVE_LABEL.lower()}'
        live_shapes.append(
            f'<a class="st live" href="#app-{a.lower()}" id="st-{a}" data-abbr="{a}" data-name="{esc(st["name"])}" '
            f'data-x="{x}" data-y="{y}"{adj} aria-label="{esc(label)}" aria-controls="pop-{a.lower()}" aria-expanded="false" '
            f'style="--fill:{c["bg"]};--fill-dark:{c["accent"]}"><path d="{d}"/></a>')
        pins.append(
            f'<a class="pin" href="#app-{a.lower()}" data-abbr="{a}" tabindex="-1" aria-hidden="true" '
            f'style="left:{100 * x / VBW:.3f}%;top:{100 * y / VBH:.3f}%">'
            f'<img src="{esc(s["icon"])}" width="50" height="50" alt="" decoding="async"></a>')
    else:
        shapes.append(f'<path class="st" id="st-{a}" data-abbr="{a}" data-name="{esc(st["name"])}" '
                      f'data-x="{x}" data-y="{y}"{adj} role="img" aria-label="{esc(st["name"])}: coming soon" d="{d}"/>')
# Live states first, so a screen reader meets them before the 49 coming-soon shapes. Borders are drawn in
# the same colour and width on every state, so paint order doesn't change how the map looks.
MAP = "\n".join(live_shapes + shapes)
PINS = "\n".join("          " + p for p in pins)


def swatch(key):
    cs = [s["colors"][key] for s in LIVE]
    n = len(cs)
    stops = ",".join(f"{c} {100 * i / n:.1f}% {100 * (i + 1) / n:.1f}%" for i, c in enumerate(cs))
    return f"linear-gradient(90deg,{stops})"


LEGEND_STYLE = f"--sw:{swatch('bg')};--sw-dark:{swatch('accent')}"

pops = []
for s in LIVE:
    a = s["abbr"].lower()
    pops.append(f"""          <article class="pop" id="pop-{a}" data-abbr="{s['abbr']}" style="{colors(s)}" tabindex="-1" aria-labelledby="pop-{a}-h" hidden>
            <button class="x" type="button" aria-label="Close {esc(s['app'])}">{CLOSE}</button>
            <div class="row"><img src="{esc(s['icon'])}" width="56" height="56" alt="" loading="lazy" decoding="async">
              <div><p class="w">{esc(where(s))}</p><p class="n" id="pop-{a}-h">{esc(s['app'])}</p></div></div>
            <p class="tg">{esc(s.get('tagline_short') or s['tagline'])}</p>
            <p class="pl">{platforms(s)}</p>
            <div class="actions">
              {website(s)}
              {store(s)}
            </div>
            {more_links(s)}
          </article>""")
POPS = "\n".join(pops)

picks = []
for s in LIVE:
    a = s["abbr"].lower()
    picks.append(f'          <li><a class="pick" href="#app-{a}" data-abbr="{s["abbr"]}" aria-controls="pop-{a}" aria-expanded="false">'
                 f'<img src="{esc(s["icon"])}" width="44" height="44" alt="" loading="lazy" decoding="async">'
                 f'<span class="pk"><span class="ps">{esc(where(s))}</span><span class="pa">{esc(s["app"])}</span></span>{CHEVRON}</a></li>')
PICKS = "\n".join(picks)

MAILTO_NEXT = f"mailto:{EMAIL}?subject=" + "Next%20state%20for%20Eats%20Ranked"
MAP_TITLE = f"Map of the United States. {LIVE_LABEL}: {LIVE_NAMES}. Every other state is coming soon."
LEDE_LIVE = f"Live now in {LIVE_NAMES}." if ON_STORE else f"Now in {LIVE_NAMES}."

footer_apps = "".join(f'<li><a {ext(s["site"])}>{esc(s["app"])}</a></li>' for s in LIVE)

# ====================================================================== head
TITLE = "Eats Ranked: Restaurant Grades & Rankings, State by State"
# True of every app: Chicago grades from City inspections; Wisconsin grades only Madison/Dane County and is
# mostly open map data plus hand-checked lists. No "free iPhone apps" claim: none may be on the App Store yet.
DESC_BODY = ("Restaurant apps from public records and open data: our own inspection grades where cities "
             "publish them, hand-checked local picks.")
DESC = f"{DESC_BODY} Now in {LIVE_NAMES}."
if len(DESC) > 160:
    DESC = f"{DESC_BODY} Now in {len(LIVE)} states."
OG_ALT = f"Eats Ranked: restaurants, ranked state by state, with the app icons for {LIVE_NAMES}."
JSONLD = jsonscript({
    "@context": "https://schema.org",
    "@graph": [
        {"@type": "WebSite", "@id": URL + "#website", "url": URL, "name": BRAND["name"],
         "description": DESC, "inLanguage": "en-US", "publisher": {"@id": URL + "#organization"}},
        {"@type": "Organization", "@id": URL + "#organization", "name": BRAND["name"], "url": URL,
         "logo": URL + "icon-512.png", "email": EMAIL,
         "contactPoint": {"@type": "ContactPoint", "email": EMAIL, "contactType": "customer support"},
         "sameAs": [REPO],
         # each state site names its own Organization <site>#org (and owns its name), so the two point at the same node
         "subOrganization": [{"@type": "Organization", "@id": s["site"].rstrip("/") + "/#org", "url": s["site"]}
                             for s in LIVE]},
    ],
}, indent=1)

def slim_js(js):
    """The script as inlined: tools/app.js (the readable copy) without whole-line comments, blank lines and
    indentation. Same code, about 3.5 KB less page; it has no template literals, so no string spans lines."""
    return re.sub(r"(?m)^[ \t]*(?://.*)?\n|^[ \t]+", "", js)


SUBS = {
    "TITLE": esc(TITLE), "DESC": esc(DESC), "URL": esc(URL), "OG_ALT": esc(OG_ALT), "JSONLD": JSONLD,
    "CARDS": CARDS, "COMING_SOON": esc(SITE["coming_soon_message"]), "LIVE_LABEL": esc(LIVE_LABEL),
    "LEDE_LIVE": esc(LEDE_LIVE),
    "MAILTO_NEXT": esc(MAILTO_NEXT), "VIEWBOX": esc(GEO["viewBox"]), "MAP_TITLE": esc(MAP_TITLE), "MAP": MAP,
    "PINS": PINS, "POPS": POPS, "PICKS": PICKS, "LEGEND_STYLE": LEGEND_STYLE,
    "HOW_NOTE": esc(SITE.get("how_it_works_note", "")), "BILL": BILL_HTML, "FACTS": FACTS,
    "CHART_TITLE": esc(CHART_TITLE), "CHART_SUB": esc(CHART_SUB), "CHART_ALT": esc(CHART_ALT),
    "YTICKS": YTICKS, "XTICKS": XTICKS, "CHART_SVG": CHART_SVG, "CHART_HTML": CHART_HTML, "ENDS": ENDS,
    "TABLE": TABLE, "SOURCE": esc(P["source"]), "SOURCE_URL": esc(P["source_url"]),
    "CHART_JSON": jsonscript(CHART_DATA, separators=(",", ":")),
    "EMAIL": esc(EMAIL), "FOOTER_APPS": footer_apps, "APP_JS": slim_js(APP_JS).strip(),
}


def fill(tpl, subs):
    out = tpl
    for k, v in subs.items():
        out = out.replace(f"@@{k}@@", v)
    left = [s for s in re.findall(r"@@\w+@@", out) if s != "@@CSP@@"]   # the CSP goes in last, see with_csp
    if left:
        sys.exit(f"build: unfilled template slots {left}")
    return out


def with_csp(doc):
    """Fill @@CSP@@ with a policy for this finished page: nothing from other hosts, and only the page's own
    inline scripts, by SHA-256 of their exact text (JSON blocks never run, so they need none). Runs after
    every other change to the page, so editing app.js or a template can't leave a stale hash behind."""
    hashes, external = [], False
    for attrs, body in re.findall(r"<script\b([^>]*)>(.*?)</script>", doc, re.S):
        if re.search(r'\btype="application/(?:ld\+)?json"', attrs):
            continue
        if re.search(r"\bsrc=", attrs):
            external = True
            continue
        hashes.append(f"'sha256-{base64.b64encode(hashlib.sha256(body.encode()).digest()).decode()}'")
    script = " ".join(["'self'"] + hashes) if hashes or external else "'none'"
    img = "'self' data:" if re.search(r"""(?:\ssrc=["']?|url\(\s*["']?)data:""", doc) else "'self'"
    policy = (f"default-src 'none'; script-src {script}; style-src 'self' 'unsafe-inline'; img-src {img}; "
              "connect-src 'self'; manifest-src 'self'; base-uri 'none'; form-action 'none'; object-src 'none'; "
              "upgrade-insecure-requests")
    if doc.count("@@CSP@@") != 1:
        sys.exit("build: a page needs exactly one @@CSP@@ slot")
    return doc.replace("@@CSP@@", html.escape(policy, quote=False))   # no double quotes in it; keeps 'self' readable


page = fill(TEMPLATE, SUBS)
page = page.replace("A–F", '<span class="nw">A–F</span>')
page = with_csp(page)

# nothing on the page may load from another host
assert not re.search(r"""\s(?:src|srcset|poster|data)=["']?(?:https?:)?//""", page), "remote src"
assert not re.search(r"""<link\b[^>]*\bhref=["']?(?:https?:)?//""", page.replace(f'<link rel="canonical" href="{URL}">', "")), "remote link"
assert not re.search(r"""url\(\s*["']?(?:https?:)?//""", page) and "@import" not in page, "remote css"
assert page.count("<h1") == 1

page404 = with_csp(fill(TEMPLATE_404, {"URL": esc(URL), "EMAIL": esc(EMAIL)}))

# ====================================================================== write
if DRY_RUN:
    print(f"dry run: {DATA} builds (latest month {LAST}, 12-month stat to {TWELVE_TO}); nothing written")
    sys.exit(0)
DOCS.mkdir(exist_ok=True)
index = DOCS / "index.html"
changed = not index.exists() or index.read_text() != page
if changed:
    index.write_text(page)
(DOCS / "404.html").write_text(page404)
(DOCS / "CNAME").write_text(DOMAIN)
(DOCS / "robots.txt").write_text(f"User-agent: *\nAllow: /\n\nSitemap: {URL}sitemap.xml\n")
(DOCS / ".nojekyll").write_text("")   # nothing here uses Jekyll, and without this Pages skips .well-known/
(DOCS / ".well-known").mkdir(exist_ok=True)
(DOCS / ".well-known" / "security.txt").write_text(
    f"Contact: mailto:{EMAIL}\nExpires: {SECURITY_TXT_EXPIRES}\nPreferred-Languages: en\n"
    f"Canonical: {URL}.well-known/security.txt\n")
(DOCS / "site.webmanifest").write_text(json.dumps({
    "name": BRAND["name"], "short_name": BRAND["name"],
    "description": "Restaurants, ranked state by state.",
    "start_url": "/", "scope": "/", "display": "browser",
    "background_color": "#FAF8F3", "theme_color": "#FAF8F3",
    "icons": [
        {"src": "/favicon.svg", "sizes": "any", "type": "image/svg+xml"},
        {"src": "/icon-192.png", "sizes": "192x192", "type": "image/png"},
        {"src": "/icon-512.png", "sizes": "512x512", "type": "image/png"},
    ],
}, indent=2) + "\n")

sitemap = DOCS / "sitemap.xml"
old = re.search(r"<lastmod>([\d-]+)</lastmod>", sitemap.read_text()) if sitemap.exists() else None
lastmod = datetime.date.today().isoformat() if (changed or not old) else old.group(1)
sitemap.write_text(
    '<?xml version="1.0" encoding="UTF-8"?>\n'
    '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
    f"  <url>\n    <loc>{URL}</loc>\n    <lastmod>{lastmod}</lastmod>\n  </url>\n"
    "</urlset>\n")

print(f"docs/index.html {'written' if changed else 'unchanged'} ({len(page.encode()) / 1024:.0f} KB), sitemap lastmod {lastmod}")
print(f"  live: {LIVE_NAMES} ({LIVE_LABEL}); latest BLS month {LAST}; missing months {MISSING}")
if TWELVE_TO != LAST:
    print(f"  note: no year-ago value for {LAST}; the 12-month stat runs to {TWELVE_TO}")
for s in LIVE:
    c = s["colors"]
    if ACCENT[s["abbr"]] != c["accent"]:
        print(f"  note: {s['abbr']} accent {c['accent']} is {contrast(c['accent'], c['bg']):.2f}:1 on {c['bg']}; its card and "
              f"pop-up use {ACCENT[s['abbr']]} ({contrast(ACCENT[s['abbr']], c['bg']):.2f}:1), the map keeps {c['accent']}")
for k, v in F.items():
    print(f"  {k:6s} {v:9.4f} -> {fmt_pct(v)}")
print(f"  $20 bill -> ${BILL:.4f}")
