#!/usr/bin/env python3
"""Check the built site against tools/site-data.json, independently of build.py.

    python3 tools/verify.py            # static checks + serve docs/ locally and fetch every link
    python3 tools/verify.py --browser  # also run tools/browser_check.mjs in headless Chrome

Every number on the page is recomputed here from the raw BLS series (this file does
not import build.py), and every number found in the page's text must be one of them,
a year, or a number that appears in site-data.json itself. Exits 1 on any failure.
"""
import base64
import colorsys
import datetime
import hashlib
import html
import html.parser
import http.server
import json
import math
import pathlib
import re
import socket
import struct
import subprocess
import sys
import threading
import unicodedata
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from functools import partial

TOOLS = pathlib.Path(__file__).resolve().parent
DOCS = TOOLS.parent / "docs"
D = json.loads((TOOLS / "site-data.json").read_text())
GEO = json.loads((TOOLS / "us-states-paths.json").read_text())
SERVED = (DOCS / "index.html").read_text()   # as served; PAGE below is the same page with styles back inline

def unhoist(doc):
    """The page as written before build.py's hoist_styles: each generated zs class back into a style attribute, so the
    checks below can read chart positions and card colours where the templates put them."""
    rules = dict(re.findall(r"\.(zs\d+)(?:\.\1){3}\{([^}]*)\}", doc))

    def tag(m):
        t = m.group(0)
        cm = re.search(r'\sclass="([^"]*)"', t)
        classes = cm.group(1).split()
        rest, style = [c for c in classes if c not in rules], ";".join(rules[c] for c in classes if c in rules)
        if not style:
            return t
        return t[:cm.start()] + (f' class="{" ".join(rest)}"' if rest else "") + t[cm.end():-1].rstrip("/") + f' style="{style}">'

    return re.sub(r'<[a-zA-Z][^>]*\sclass="[^"]*\bzs\d+\b[^"]*"[^>]*>', tag, doc)


PAGE = PAGE_STYLED = unhoist(SERVED)   # content checks read where the templates put things

fails = []


def check(label, ok, detail=""):
    print(f"{'OK ' if ok else 'BAD'} {label}" + (f"  [{detail}]" if detail and not ok else ""))
    if not ok:
        fails.append(label)


def text_of(fragment):
    fragment = re.sub(r"<(script|style)\b.*?</\1>", " ", fragment, flags=re.S)
    fragment = re.sub(r"<svg\b.*?</svg>", " ", fragment, flags=re.S)
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", fragment))).strip()


def section(pattern):
    m = re.search(pattern, PAGE, re.S)
    return m.group(1) if m else ""


# ============================================================ recompute the numbers
S = {sid: {m: (None if v in (None, "-") else float(v)) for m, v in s["monthly"]} for sid, s in D["prices"]["series"].items()}
R, FULL, FAST, G, A, CHI, MW = ("CUUR0000SEFV", "CUUR0000SEFV01", "CUUR0000SEFV02", "CUUR0000SAF11",
                                "CUUR0000SA0", "CUURS23ASEFV", "CUUR0200SEFV")
MON = ["January", "February", "March", "April", "May", "June", "July", "August", "September",
       "October", "November", "December"]
base = "2020-01"


def ok(sid, m):
    return S[sid].get(m) is not None


def yr_before(m):
    return f"{int(m[:4]) - 1}-{m[5:]}"


# latest month in which the U.S. restaurant, full-service, fast-food, grocery and all-items series all have a value
last = sorted(m for m in S[R] if all(ok(sid, m) for sid in (R, FULL, FAST, G, A)))[-1]
# the 12-month stat: the latest month (up to `last`) whose value a year earlier exists in the Chicago,
# Midwest and U.S. series (BLS skipped October 2025, so October 2026 has no year-ago value)
to12 = sorted(m for m in S[R] if m <= last and all(ok(sid, m) and ok(sid, yr_before(m)) for sid in (CHI, MW, R)))[-1]
ago = yr_before(to12)


def long(m):
    return f"{MON[int(m[5:]) - 1]} {m[:4]}"


def short(m):
    return f"{MON[int(m[5:]) - 1][:3]} {m[:4]}"


def chg(sid, a, b):
    return (S[sid][b] / S[sid][a] - 1) * 100


def went(x):
    return f"{'rose' if x >= 0 else 'fell'} {abs(x):.1f}%"


def f(x):
    r = f"{abs(x):.1f}"
    return "0.0%" if r == "0.0" else ("+" if x > 0 else "−") + r + "%"


axis = []
y, mo = 2020, 1
while f"{y}-{mo:02d}" <= last:
    axis.append(f"{y}-{mo:02d}")
    y, mo = (y + 1, 1) if mo == 12 else (y, mo + 1)
missing = [m for m in axis if S[R].get(m) is None]

E = {
    "rest": chg(R, base, last), "all": chg(A, base, last), "groc": chg(G, base, last),
    "fast": chg(FAST, base, last), "full": chg(FULL, base, last),
    "chi": chg(CHI, ago, to12), "mw": chg(MW, ago, to12), "us": chg(R, ago, to12),
}
bill = f"${20 * S[R][last] / S[R][base]:.2f}"
print(f"recomputed from site-data.json (latest month {last}, 12-month stat to {to12}, missing {missing}):")
for k, v in E.items():
    print(f"    {k:5s} {v:9.4f} -> {f(v)}")
print(f"    $20 in {short(base)} -> {bill} in {short(last)}")

# ============================================================ the numbers on the page
facts = text_of(section(r'<dl class="facts">(.*?)</dl>'))
want_facts = (f"{f(E['rest'])} Restaurant and takeout prices since {long(base)}. All prices "
              f"{went(E['all'])}; groceries {went(E['groc'])}. "
              f"{f(E['fast'])} Fast food and counter service since {long(base)}, "
              f"{'outpacing' if E['fast'] > E['full'] else 'compared with'} sit-down restaurants ({f(E['full'])}). "
              f"{f(E['chi'])} Chicago-area restaurant prices in the 12 months to {long(to12)}. "
              f"Midwest: {f(E['mw'])}. U.S.: {f(E['us'])}.")
facts = re.sub(r"\s+%", "%", facts)
check("three stats and their notes", facts == want_facts, f"page: {facts!r}\nwant: {want_facts!r}")

bill_text = text_of(section(r'<p class="bill">(.*?)</p>'))
check("$20 comparison", bill_text == f"A $20.00 restaurant bill from {long(base)} comes to {bill} at {long(last)} prices.", bill_text)

ends = text_of(section(r'<div class="ends"[^>]*>(.*?)</div>'))
want_ends = {f"{f(E['rest'])} Restaurants", f"{f(E['groc'])} Groceries", f"{f(E['all'])} All prices"}
check("chart end labels", all(w in ends for w in want_ends), ends)

alt = html.unescape(section(r'<div class="plot"[^>]*aria-label="([^"]*)"'))
check("chart description (aria-label) numbers",
      all(x in alt for x in (f(E["rest"]), f(E["groc"]), f(E["all"]), long(base), long(last)))
      and all(long(m) in alt for m in missing), alt)

# table rows
rows = re.findall(r'<tr><th scope="row">([A-Z][a-z]{2} \d{4})</th>(.*?)</tr>', section(r"<details class=\"tbl\">(.*?)</details>"))
bad_rows = []
for lab, cells in rows:
    m = f"{lab[4:]}-{[x[:3] for x in MON].index(lab[:3]) + 1:02d}"
    got = [text_of(c) for c in re.findall(r"<td[^>]*>(.*?)</td>", cells)]
    want = [("not published" if S[sid].get(m) is None else f(chg(sid, base, m))) for sid in (R, G, A)]
    if got != want:
        bad_rows.append(f"{lab}: {got} != {want}")
check(f"table: {len(rows)} rows match the data", rows and not bad_rows, "; ".join(bad_rows[:3]))
check("table includes the months around the gap and the latest month",
      {short(m) for m in missing} | {short(last)} <= {lab for lab, _ in rows})

# chart data used by the hover/keyboard readout
cd = json.loads(section(r'<script id="chart-data" type="application/json">(.*?)</script>'))
bad = []
check("chart readout months", cd["m"] == [short(m) for m in axis])
for s, sid in zip(cd["s"], (R, G, A)):
    for m, v in zip(axis, s["v"]):
        want = None if S[sid].get(m) is None else f(chg(sid, base, m))
        if v != want:
            bad.append(f"{s['k']} {m}: {v} != {want}")
check(f"chart readout values ({len(axis)} months x 3 series)", not bad, "; ".join(bad[:3]))

# the drawn lines: read the axis scale off the gridlines, then every point back into a percentage
grid = [float(v) for v in re.findall(r'<line class="grid[^"]*" x1="0" x2="1000" y1="([\d.]+)"', PAGE)]
ticks = [text_of(t) for t in re.findall(r'<div class="yax"[^>]*>(.*?)</div>', PAGE)[0].split("</span>") if text_of(t)]
tick_vals = [float(t.replace("−", "-").replace("+", "").replace("%", "")) for t in ticks]
check("y-axis ticks line up with gridlines", len(grid) == len(tick_vals) >= 2, f"{grid} {ticks}")
(y0, t0), (y1, t1) = (grid[0], tick_vals[0]), (grid[-1], tick_vals[-1])


def to_pct(yy):
    return t0 + (yy - y0) * (t1 - t0) / (y1 - y0)


worst, npts, starts = 0.0, 0, {}
for cls, sid in (("rest", R), ("groc", G), ("all", A)):
    d = section(rf'<path class="ln {cls}" d="([^"]+)"')
    pts = re.findall(r"([ML])([\d.]+),([\d.]+)", d)
    starts[cls] = [axis[round(float(x) / 1000 * (len(axis) - 1))] for c, x, _ in pts if c == "M"]
    for c, x, yy in pts:
        m = axis[round(float(x) / 1000 * (len(axis) - 1))]
        worst = max(worst, abs(to_pct(float(yy)) - chg(sid, base, m)))
        npts += 1
want_starts = [axis[0]] + [axis[axis.index(m) + 1] for m in missing if axis.index(m) + 1 < len(axis)]
check(f"chart lines: {npts} points within 0.01 points of the data", npts == 3 * (len(axis) - len(missing)) and worst < 0.01, f"worst {worst}")
check("chart lines break at the unpublished months", all(v == want_starts for v in starts.values()), str(starts))
dots = dict(re.findall(r'<span class="dot (\w+)" style="left:100%;top:([\d.]+)%"', PAGE_STYLED))
check("end dots sit on each line's last value",
      all(abs(to_pct(float(dots[k]) * 10) - E[k2]) < 0.01 for k, k2 in (("rest", "rest"), ("groc", "groc"), ("all", "all"))), str(dots))
gapnotes = [text_of(g) for g in re.findall(r'<span class="gapnote"[^>]*>(.*?)</span>', PAGE)]
check("gap note for each unpublished month", gapnotes == [f"{short(m)} not published" for m in missing], str(gapnotes))
zero = [float(v) for v in re.findall(r'<div class="yax"[^>]*>.*?<span style="top:([\d.]+)%">0%</span>', PAGE_STYLED)]
gtops = [float(v) for v in re.findall(r'<span class="gapnote" style="left:[\d.]+%;top:([\d.]+)%"', PAGE_STYLED)]
check("gap notes are anchored to the 0% line (and drawn above it)",
      zero and len(gtops) == len(missing) and all(abs(g - zero[0]) < 0.01 for g in gtops)
      and "translate(-100%,calc(-100% - 6px))" in PAGE, f"{zero} {gtops}")

# every number anywhere in the page's words must be explained
visible = text_of(PAGE[PAGE.index("<body"):])
attrs = " ".join(html.unescape(a) for a in re.findall(r'\b(?:aria-label|alt|title)="([^"]*)"', PAGE))
attrs += " " + " ".join(html.unescape(a) for a in re.findall(r'<meta (?:name|property)="(?:description|og:description|og:title|og:image:alt|twitter:[a-z:]+)" content="([^"]*)"', PAGE))
data_text = json.dumps(D, ensure_ascii=False)
allowed = {v for v in (f(x) for x in E.values())} | {f(x)[:-1] for x in E.values()} | {bill, "$20.00", "12"}
allowed |= {f"{abs(E['all']):.1f}%", f"{abs(E['groc']):.1f}%"}
allowed |= {str(yr) for yr in range(int(base[:4]), int(last[:4]) + 1)}
allowed |= {t for t in ticks}
allowed |= set(cd["m"]) | {v for s in cd["s"] for v in s["v"] if v}
allowed |= {"1", "2", "3"}  # the how-it-works step numbers
unexplained = []
for tok in re.findall(r"[+−$]?\d(?:[\d,]*\d)?(?:\.\d+)?%?\+?", visible + " " + attrs):
    core = tok.rstrip("+")
    if core in allowed or tok in data_text or core in data_text:
        continue
    if re.fullmatch(r"\d{4}", core) and core in data_text:
        continue
    unexplained.append(tok)
check("every number in the page text is recomputed or comes from site-data.json", not unexplained, str(sorted(set(unexplained))))

# ============================================================ states, links, copy
live = [s for s in D["states"] if s.get("status") == "live"]
names = {s["abbr"]: s["name"] for s in GEO["states"]}


def icon_src(s):
    """The icon the page should load, worked out here rather than taken from build.py: make_images.py's WebP copy of
    the state's PNG, icons/<name>.<first 10 hex digits of the PNG's SHA-256>.webp, when it is there for this exact PNG;
    otherwise the PNG named in site-data.json."""
    png = DOCS / s["icon"]
    if png.suffix.lower() != ".png" or not png.is_file():
        return s["icon"]
    webp = png.with_name(f"{png.stem}.{hashlib.sha256(png.read_bytes()).hexdigest()[:10]}.webp")
    return webp.relative_to(DOCS).as_posix() if webp.is_file() else s["icon"]


def png_size(p):
    b = p.read_bytes()[:24]
    return struct.unpack(">II", b[16:24]) if b[:8] == b"\x89PNG\r\n\x1a\n" else None


def webp_size(p):
    """Width and height from a WebP file's header (lossy VP8, lossless VP8L or extended VP8X), or None if it isn't one."""
    b = p.read_bytes()
    if len(b) < 30 or b[:4] != b"RIFF" or b[8:12] != b"WEBP" or int.from_bytes(b[4:8], "little") + 8 != len(b):
        return None
    kind, d = b[12:16], b[20:]
    if kind == b"VP8 " and d[3:6] == b"\x9d\x01\x2a":
        return int.from_bytes(d[6:8], "little") & 0x3FFF, int.from_bytes(d[8:10], "little") & 0x3FFF
    if kind == b"VP8L" and d[0] == 0x2F:
        bits = int.from_bytes(d[1:5], "little")
        return (bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1
    if kind == b"VP8X":
        return int.from_bytes(d[4:7], "little") + 1, int.from_bytes(d[7:10], "little") + 1
    return None


ICON = {s["abbr"]: icon_src(s) for s in live}
USED = {}
for s in live:
    a = s["abbr"].lower()
    # The icon shows five times (hero row, map pin, map pop-up, phone list, card), always as the same file: make_images.py's
    # WebP of this exact PNG (a fifth of the bytes) or the PNG itself. Never another WebP: one named after an earlier
    # PNG would show the old icon. The PNG still passes (a state added before make_images.py ran), with a warning.
    stem = pathlib.PurePosixPath(s["icon"]).stem
    uses = re.findall(rf'<img\b[^>]*\ssrc="(icons/{re.escape(stem)}(?:\.[0-9a-f]{{10}})?\.(?:png|webp))"', PAGE)
    used = USED[s["abbr"]] = uses[0] if uses else s["icon"]
    check(f"{s['abbr']} icon loads as one file in the hero row, map pin, map pop-up, phone list and card: "
          f"{ICON[s['abbr']]}" + (f" or {s['icon']}" if ICON[s["abbr"]] != s["icon"] else ""),
          len(uses) == 5 and len(set(uses)) == 1 and used in (ICON[s["abbr"]], s["icon"]), str(uses))
    if used.endswith(".webp"):
        size = png_size(DOCS / s["icon"])
        got = webp_size(DOCS / used) if (DOCS / used).is_file() else "missing"   # a page naming a WebP that isn't there
        check(f"{s['abbr']} {used} is a WebP the size of its PNG {size}", size is not None and got == size, str(got))
    elif ICON[s["abbr"]] != s["icon"]:
        print(f"WARN: {s['abbr']} icon loads as the PNG, but make_images.py has made {ICON[s['abbr']]}: "
              f"run python3 tools/build.py again so the page uses it (a fifth of the bytes)")
    else:
        print(f"WARN: {s['abbr']} icon loads as the PNG: run ../chi-eats/.venv/bin/python tools/make_images.py --icons, "
              f"then python3 tools/build.py, for its WebP (a fifth of the bytes)")
    card = section(rf'(<article class="app" id="app-{a}".*?</article>)')
    check(f"{s['abbr']} card: app name, tagline, region, colours, icon",
          all(x in text_of(card) for x in (s["app"], s["tagline"], s.get("region") or names[s["abbr"]]))
          and all(x in card for x in (s["colors"]["bg"], s["colors"]["text"]))
          and f'<img class="icon" src="{html.escape(used)}"' in card)
    check(f"{s['abbr']} card: website link {s['site']}", f'href="{s["site"]}"' in card)
    if s.get("web_app"):
        check(f"{s['abbr']} card: {s.get('web_app_label')} -> {s['web_app']}",
              f'href="{s["web_app"]}"' in card and html.escape(s.get("web_app_label", "")) in card)
    # the App Store badge is on the state's card and on its map pop-up: one link in each once site-data has the
    # App Store URL, otherwise a "Coming soon" label (not a link) whose hover text is site-data's app_store_note
    pop = section(rf'(<article class="pop" id="pop-{a}".*?</article>)')
    for where_, frag in (("card", card), ("map pop-up", pop)):
        if s.get("app_store"):
            check(f"{s['abbr']} {where_}: one App Store link ({s['app_store']}), no 'Coming soon' badge",
                  frag.count(f'href="{html.escape(s["app_store"])}"') == 1 and 'class="badge soon"' not in frag
                  and frag.count("apps.apple.com") == (1 if "apps.apple.com" in s["app_store"] else 0))
        else:
            note = s.get("app_store_note") or "Coming soon to the App Store"
            check(f"{s['abbr']} {where_}: App Store badge is 'Coming soon', not a link, with hover text {note!r}",
                  frag.count('class="badge soon"') == 1 and f'class="badge soon" title="{html.escape(note)}"' in frag
                  and "Coming soon to the" in frag and "apps.apple.com" not in frag)
    check(f"{s['abbr']} is filled on the map in its colour with its icon",
          re.search(rf'<a class="st live" href="#app-{a}" id="st-{s["abbr"]}"[^>]*--fill:{s["colors"]["bg"]}', PAGE) is not None
          and re.search(rf'<a class="pin"[^>]*data-abbr="{s["abbr"]}"[^>]*>\s*<img src="{re.escape(USED[s["abbr"]])}"', PAGE) is not None)
    check(f"{s['abbr']} has a map card", f'id="pop-{a}"' in PAGE)
    check(f"{s['abbr']} icon file exists", (DOCS / s["icon"]).is_file())
on_store = all(s.get("app_store") for s in live)
label = "Live now" if on_store else "On the web now"
check(f"live wording follows App Store status ({label!r})",
      PAGE.count(f">{label}<") >= 3 and f'data-live="{label}"' in PAGE
      and (on_store or ("Live now" not in text_of(PAGE[PAGE.index("<body"):]) and "Live in" not in PAGE
                        and "live now" not in PAGE)))
desc_text = html.unescape(section(r'<meta name="description" content="([^"]*)"'))
check("description makes no install or 'live' claim before the App Store links exist",
      on_store or not re.search(r"\b(?:live|free iphone|download)\b", desc_text, re.I), desc_text)
first_st = re.search(r'<(?:a|path) class="st( live)?"', PAGE)
check("live states come first in the map's reading order", first_st is not None and first_st.group(1) == " live")
adj = {a: set(v.split()) for a, v in re.findall(r'data-abbr="([A-Z]{2})" data-name="[^"]*" data-x="[^"]*" data-y="[^"]*" data-adj="([^"]*)"', PAGE)}
check(f"tile neighbours for arrow keys: {len(adj)} states, symmetric",
      len(adj) >= 48 and all(a in adj.get(b, set()) for a in adj for b in adj[a]) and {"WI", "IN", "IA", "MI", "MN"} <= adj.get("IL", set()))   # hex tiles touching Illinois
soon = [a for a in names if a not in {s["abbr"] for s in live}]
check(f"{len(soon)} other states are on the map as coming soon",
      all(f'aria-label="{html.escape(names[a])}: coming soon"' in PAGE for a in soon))
n_store = sum(1 for s in live if s.get("app_store") and "apps.apple.com" in s["app_store"])
check(f"App Store links only where site-data has one: {2 * n_store} on the page (a card and a map pop-up per app)",
      PAGE.count("apps.apple.com") == 2 * n_store)

must = [
    D["coming_soon_message"], "This site uses no cookies and no analytics.",
    "Not affiliated with any government agency. Official inspection results are shown as published; grades of our own are labeled as ours.",
    f'href="mailto:{D["brand"]["email"]}">{D["brand"]["email"]}</a>', "Tell us which state", html.escape(D["how_it_works_note"]),
    html.escape(D["prices"]["source"]), D["prices"]["source_url"], "Restaurants, ranked", "state by state.",
    "Eating out, by the numbers", "How it works", "Show the numbers",
]
for m in must:
    check(f"page has: {m[:60]}", m in PAGE)
# A card may name another company's mark as a fact (Florida's and California's "MICHELIN and James Beard honors"):
# nominative use, safe with a trademark line and no hint of sponsorship (chi-eats/ios/playbook/app-review-risk.md 5.2.1).
# So the footer names the owner of each mark the rest of the page mentions, says Eats Ranked isn't affiliated with or
# endorsed by them, and names no owner whose marks the page doesn't mention.
MARKS = {"Michelin": (r"\b(?:michelin|bib gourmand)\b", "MICHELIN, MICHELIN Guide and Bib Gourmand are trademarks of Michelin."),
         "the James Beard Foundation": (r"\bjames beard\b", "James Beard Foundation and James Beard Award are trademarks of the James Beard Foundation.")}
tm = re.search(r"<p>([^<]*\btrademarks? of\b[^<]*)</p>", section(r'(<footer class="foot">.*?</footer>)'))
body = PAGE[PAGE.index("<body"):]
named = [o for o, (pat, _) in MARKS.items() if re.search(pat, text_of(body.replace(tm.group(0), "") if tm else body), re.I)]
if named:
    tm_line = html.unescape(tm.group(1)) if tm else ""
    who = named[0] if len(named) == 1 else "either"
    check(f"footer trademark line for {' and '.join(named)} (named on the page), not affiliated or endorsed",
          tm_line == " ".join(MARKS[o][1] for o in named) + f" Eats Ranked is not affiliated with or endorsed by {who}.", tm_line)
else:
    check("no trademark line, as the page names no MICHELIN or James Beard mark", tm is None, tm.group(0) if tm else "")
check("'Tell us which state' is a mailto link",
      re.search(rf'<a href="mailto:{re.escape(D["brand"]["email"])}\?subject=[^"]+">Tell us which state</a>', PAGE) is not None)

# ============================================================ card colours: contrast on every live state
def lum(hexc):
    def lin(c):
        c = int(c, 16) / 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    return 0.2126 * lin(hexc[1:3]) + 0.7152 * lin(hexc[3:5]) + 0.0722 * lin(hexc[5:7])


def ratio(a, b):
    la, lb = lum(a), lum(b)
    return (max(la, lb) + 0.05) / (min(la, lb) + 0.05)


def blend(fg, bg, alpha):
    return "#" + "".join(f"{round(alpha * int(fg[i:i + 2], 16) + (1 - alpha) * int(bg[i:i + 2], 16)):02X}" for i in (1, 3, 5))


def hls(hexc):
    return colorsys.rgb_to_hls(*(int(hexc[i:i + 2], 16) / 255 for i in (1, 3, 5)))


# The faintest text on a card is "Coming soon to the" on the App Store badge: .badge.soon (.9) x .badge small (.82).
FAINT = 0.9 * 0.82
check("card CSS still has the opacities the contrast gate assumes",
      ".badge.soon{opacity:.9;" in PAGE and re.search(r"\.badge small\{[^}]*opacity:\.82", PAGE) is not None
      and not re.search(r"\.(?:app|pop) [^{]*\{[^}]*opacity:\.(?:[0-6]|7[0-3])", PAGE))
for s in live:
    a, c = s["abbr"].lower(), s["colors"]
    styles = re.findall(rf'<article class="(?:app|pop)" id="(?:app|pop)-{a}"[^>]*style="--bg:(#[0-9A-Fa-f]{{6}});--ac:(#[0-9A-Fa-f]{{6}});--fg:(#[0-9A-Fa-f]{{6}})"', PAGE_STYLED)
    if len(styles) != 2 or styles[0] != styles[1]:
        check(f"{s['abbr']} card and pop-up colours", False, str(styles))
        continue
    bg, ac, fg = styles[0]
    check(f"{s['abbr']} card colours come from site-data (bg {c['bg']}, text {c['text']})", bg == c["bg"] and fg == c["text"], f"{bg} {fg}")
    lab, ring, txt, faint = ratio(ac, bg), ratio(ac, bg), ratio(fg, bg), ratio(blend(fg, bg, FAINT), bg)
    check(f"{s['abbr']} contrast on its card and pop-up: label {lab:.2f}, Website button {ratio(bg, ac):.2f}, focus ring {ring:.2f}, "
          f"text {txt:.2f}, faintest text {faint:.2f} (need 4.5, 4.5, 3, 4.5, 4.5)",
          lab >= 4.5 and ratio(bg, ac) >= 4.5 and ring >= 3 and txt >= 4.5 and faint >= 4.5)
    if ratio(c["accent"], c["bg"]) >= 4.5:
        check(f"{s['abbr']} accent {c['accent']} passes, so it is used unchanged", ac == c["accent"], ac)
    else:
        (h0, _, s0), (h1, _, s1) = hls(c["accent"]), hls(ac)
        check(f"{s['abbr']} accent {c['accent']} is {ratio(c['accent'], c['bg']):.2f}:1, so text uses {ac}: same hue and saturation",
              min(abs(h0 - h1), 1 - abs(h0 - h1)) < 0.02 and abs(s0 - s1) < 0.06, f"hls {hls(c['accent'])} -> {hls(ac)}")
    check(f"{s['abbr']} map keeps the brand accent {c['accent']} as its dark-mode fill",
          re.search(rf'id="st-{s["abbr"]}"[^>]*--fill-dark:{c["accent"]}"', PAGE) is not None)

# ============================================================ headings and structured data
heads = [(int(lv), text_of(t)) for lv, t in re.findall(r"<h([1-6])\b[^>]*>(.*?)</h\1>", PAGE, re.S)]
check("headings never skip a level", all(b[0] <= a[0] + 1 for a, b in zip(heads, heads[1:])), str(heads))
dupes = sorted({t for _, t in heads if [x for _, x in heads].count(t) > 1})
check("no heading text repeats (map pop-up titles are not headings)", not dupes, str(dupes))
check("the apps section's heading names what it is; the live label is an eyebrow",
      re.search(rf'<section class="sec wrap" id="apps"[^>]*>\s*<p class="eyebrow">{label}</p>\s*<h2 [^>]*id="apps-h">Restaurant guides by state</h2>', PAGE) is not None)
check("the map comes first, straight under the hero, and the state cards follow it (Nick, 2026-10-09)",
      0 < PAGE.find('id="h1"') < PAGE.find('id="states"') < PAGE.find('id="apps"') < PAGE.find('id="how"'))
pin_links = dict((ab, href) for href, ab in re.findall(r'<a class="pin" href="([^"]+)" rel="noopener" data-abbr="(\w+)"', PAGE))
check("each map icon links straight to its state's own site (Nick, 2026-10-09)",
      pin_links == {s["abbr"]: s["site"] for s in live}, str(pin_links))
check("the price table's scroll box can be reached and scrolled by keyboard",
      re.search(r'<div class="tw" tabindex="0" role="region" aria-label="[^"]+">', PAGE) is not None)
ld_org = next((g for g in json.loads(section(r'<script type="application/ld\+json">(.*?)</script>'))["@graph"] if g["@type"] == "Organization"), {})
subs = ld_org.get("subOrganization", [])
check(f"JSON-LD Organization lists the {len(live)} live state sites as subOrganization, linked by <site>#org",
      [(o.get("@type"), o.get("url"), o.get("@id")) for o in subs]
      == [("Organization", s["site"], s["site"].rstrip("/") + "/#org") for s in live], json.dumps(subs)[:300])
check("JSON-LD subOrganization ids and urls are https URLs",
      all(isinstance(o.get(k), str) and o[k].startswith("https://") for o in subs for k in ("@id", "url")))
check("JSON-LD Organization sameAs is a list of https URLs",
      isinstance(ld_org.get("sameAs"), list) and ld_org["sameAs"] and all(u.startswith("https://") for u in ld_org["sameAs"]))


# ============================================================ Content-Security-Policy
class Scripts(html.parser.HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=False)
        self.out, self.cur = [], None

    def handle_starttag(self, tag, attrs):
        if tag == "script":
            self.cur = [dict(attrs), ""]

    def handle_data(self, data):
        if self.cur is not None:
            self.cur[1] += data

    def handle_endtag(self, tag):
        if tag == "script" and self.cur is not None:
            self.out.append(tuple(self.cur))
            self.cur = None


def csp_checks(name, doc):
    m = re.search(r'<meta http-equiv="Content-Security-Policy" content="([^"]+)">', doc)
    check(f"{name}: has a Content-Security-Policy", m is not None)
    if not m:
        return
    pol = html.unescape(m.group(1))
    d = {p.split()[0]: p.split()[1:] for p in pol.split(";") if p.strip()}
    check(f"{name}: the CSP comes right after <meta charset>, before any script, style or link",
          re.match(r'<!doctype html>\s*<html lang="en">\s*<head>\s*<meta charset="utf-8">\s*$', doc[:m.start()]) is not None)
    p = Scripts()
    p.feed(doc)
    run = [body for attrs, body in p.out if attrs.get("type") not in ("application/json", "application/ld+json") and "src" not in attrs]
    want = ["'sha256-" + base64.b64encode(hashlib.sha256(b.encode("utf-8")).digest()).decode() + "'" for b in run]
    want_src = want or ["'none'"]
    check(f"{name}: no script files, and script-src allows exactly its {len(run)} inline script(s), by hash ('none' when it has none)",
          d.get("script-src") == want_src and "\r" not in doc and not any("src" in a for a, _ in p.out),
          f"{d.get('script-src')} != {want_src}")
    styles = re.findall(r"<style>(.*?)</style>", doc, re.S)
    want_css = ["'sha256-" + base64.b64encode(hashlib.sha256(b.encode("utf-8")).digest()).decode() + "'" for b in styles] or ["'none'"]
    check(f"{name}: style-src allows exactly its {len(styles)} <style> block(s), by hash, and nothing inline",
          d.get("style-src") == want_css, f"{d.get('style-src')} != {want_css}")
    uses_data = re.search(r"""(?:\ssrc=["']?|url\(\s*["']?)data:""", doc) is not None
    fixed = {"default-src": ["'none'"], "img-src": ["'self'"] + (["data:"] if uses_data else []),
             "font-src": ["'self'"], "connect-src": ["'self'"], "manifest-src": ["'self'"], "base-uri": ["'none'"], "form-action": ["'none'"],
             "object-src": ["'none'"], "upgrade-insecure-requests": []}
    check(f"{name}: CSP blocks other hosts, plugins, <base> and forms", all(d.get(k) == v for k, v in fixed.items())
          and set(d) == set(fixed) | {"script-src", "style-src"} and not re.search(r"https?:|\*", pol), pol)
    markup = re.sub(r"<(script|style)\b.*?</\1>", "", doc, flags=re.S)
    check(f"{name}: no style attributes in the markup (the CSP allows styles only by hash)",
          not re.search(r"<[a-zA-Z][^>]*\sstyle=", markup))
    check(f"{name}: a referrer policy that keeps paths private", '<meta name="referrer" content="strict-origin-when-cross-origin">' in doc)
    check(f"{name}: no inline event handlers or javascript: links (the CSP would block them)",
          not re.search(r"<[^>]*\son[a-z]+\s*=", markup) and "javascript:" not in markup.lower())


csp_checks("index.html", SERVED)
csp_checks("404.html", (DOCS / "404.html").read_text())


# ============================================================ link schemes
class Links(html.parser.HTMLParser):
    """Every <a> and its href as the browser reads it (character references decoded)."""
    def __init__(self):
        super().__init__()
        self.hrefs = []

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            self.hrefs.append(dict(attrs).get("href"))


def safe_href(h):
    # A browser reads a backslash as a slash in http(s) links (/\evil.com opens https://evil.com/), so none at all.
    if h is None or "\\" in h or any(unicodedata.category(ch) in ("Cc", "Cf") or unicodedata.category(ch).startswith("Z") for ch in h):
        return False
    return (re.match(r"https://[A-Za-z0-9.-]+(?::\d+)?/", h) is not None
            or re.fullmatch(r"mailto:[^\s:/?]+@[A-Za-z0-9.-]+(?:\?[^\s]*)?", h) is not None
            or re.fullmatch(r"#[A-Za-z][\w-]*", h) is not None
            or re.match(r"/(?![/\\])", h) is not None)


for name, doc in (("index.html", SERVED), ("404.html", (DOCS / "404.html").read_text())):
    lp = Links()
    lp.feed(doc)
    odd = [h for h in lp.hrefs if not safe_href(h)]
    check(f"{name}: all {len(lp.hrefs)} links are https://, mailto:, #fragment or /path (no http:, data:, javascript:, "
          f"no spaces or control characters)", lp.hrefs and not odd, repr(odd[:5]))

# ============================================================ SEO, privacy, accessibility basics
title = html.unescape(section(r"<title>(.*?)</title>"))
desc = html.unescape(section(r'<meta name="description" content="([^"]*)"'))
check(f"title length 50-60 ({len(title)})", 50 <= len(title) <= 60, title)
check(f"description length 140-160 ({len(desc)})", 140 <= len(desc) <= 160, desc)
check("canonical https://eatsranked.com/", '<link rel="canonical" href="https://eatsranked.com/">' in PAGE)
for prop in ("og:title", "og:description", "og:type", "og:site_name", "og:image:width", "og:image:height", "og:image:alt"):
    check(f"meta {prop}", f'property="{prop}"' in PAGE)
check("og:url and og:image absolute", 'property="og:url" content="https://eatsranked.com/"' in PAGE
      and 'property="og:image" content="https://eatsranked.com/og.png"' in PAGE)
for n in ("twitter:card", "twitter:title", "twitter:description", "twitter:image"):
    check(f"meta {n}", f'name="{n}"' in PAGE)
ld = json.loads(section(r'<script type="application/ld\+json">(.*?)</script>'))
check("JSON-LD WebSite + founder Person + Organization", [g["@type"] for g in ld["@graph"]] == ["WebSite", "Person", "Organization"]
      and ld["@graph"][2].get("founder", {}).get("@id") == ld["@graph"][1]["@id"] == "https://work-with-nick.com/#person"
      and ld["@graph"][1].get("url") == "https://work-with-nick.com/")
check("exactly one <h1>", PAGE.count("<h1") == 1)
check("robots meta", '<meta name="robots" content="index,follow,max-image-preview:large">' in PAGE)
imgs = re.findall(r"<img\b[^>]*>", PAGE)
check(f"all {len(imgs)} images have alt, width and height", all(" alt=" in i and " width=" in i and " height=" in i for i in imgs))
ext_links = re.findall(r'<a\b[^>]*href="https?://[^"]*"[^>]*>', PAGE)
check(f"all {len(ext_links)} external links have rel=noopener", all(re.search(r'\srel="(?:[^"]*\s)?noopener(?:\s[^"]*)?"', a) for a in ext_links))
check("no external scripts, styles, fonts or images",
      not re.search(r"""<(?:script|img|iframe|source|video|audio)\b[^>]*\ssrc=["']?(?:https?:)?//""", PAGE)
      and not re.search(r"""<link\b(?![^>]*rel="canonical")[^>]*href=["']?(?:https?:)?//""", PAGE)
      and not re.search(r"url\(\s*['\"]?(?:https?:)?//", PAGE) and "@import" not in PAGE)
check("no cookies or storage in the page script", not re.search(r"document\.cookie|localStorage|sessionStorage|indexedDB", PAGE))
check("reduced motion respected", "prefers-reduced-motion" in PAGE)
check("map is keyboard usable (roving focus + arrow keys + Enter)", all(k in PAGE for k in ("ArrowLeft", "tabindex", "Enter", 'aria-describedby="map-d"')))

# ============================================================ files next to the page
check("CNAME is exactly eatsranked.com", (DOCS / "CNAME").read_bytes() == b"eatsranked.com")
robots = (DOCS / "robots.txt").read_text()
check("robots.txt allows all and lists the sitemap", "User-agent: *" in robots and "Allow: /" in robots
      and "Sitemap: https://eatsranked.com/sitemap.xml" in robots)
sm = ET.parse(DOCS / "sitemap.xml").getroot()
ns = {"s": "http://www.sitemaps.org/schemas/sitemap/0.9"}
check("sitemap.xml lists https://eatsranked.com/ with a lastmod",
      [u.findtext("s:loc", namespaces=ns) for u in sm.findall("s:url", ns)] == ["https://eatsranked.com/"]
      and re.fullmatch(r"\d{4}-\d{2}-\d{2}", sm.find("s:url/s:lastmod", ns).text or "") is not None)
p404 = (DOCS / "404.html").read_text()
check("404.html is noindex and links home", 'content="noindex"' in p404 and 'href="/"' in p404)
man = json.loads((DOCS / "site.webmanifest").read_text())
check("site.webmanifest parses and lists icons", man.get("name") == "Eats Ranked" and len(man.get("icons", [])) >= 2)


for name, size in (("favicon-32.png", (32, 32)), ("apple-touch-icon.png", (180, 180)), ("icon-192.png", (192, 192)),
                   ("icon-512.png", (512, 512)), ("og.png", (1200, 630))):
    p = DOCS / name
    check(f"{name} is {size[0]}x{size[1]}", p.exists() and png_size(p) == size, str(png_size(p) if p.exists() else "missing"))
check("favicon.svg exists", (DOCS / "favicon.svg").read_text().startswith("<svg"))
ico = (DOCS / "favicon.ico").read_bytes() if (DOCS / "favicon.ico").exists() else b""
ico_sizes = sorted(ico[6 + 16 * i] or 256 for i in range(int.from_bytes(ico[4:6], "little"))) if ico[:4] == b"\x00\x00\x01\x00" else []
check(f"favicon.ico is an icon with 16, 32 and 48 px images ({ico_sizes})", {16, 32, 48} <= set(ico_sizes))
sec = DOCS / ".well-known" / "security.txt"
sec_f = dict(ln.split(": ", 1) for ln in sec.read_text().splitlines() if ": " in ln) if sec.exists() else {}
try:
    expires = datetime.datetime.strptime(sec_f.get("Expires", ""), "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=datetime.timezone.utc)
except ValueError:
    expires = None
now = datetime.datetime.now(datetime.timezone.utc)
check("security.txt: contact, language and canonical URL",
      sec_f.get("Contact") == f"mailto:{D['brand']['email']}" and sec_f.get("Preferred-Languages") == "en"
      and sec_f.get("Canonical") == "https://eatsranked.com/.well-known/security.txt", str(sec_f))
check(f"security.txt: Expires is in the future and under a year away ({sec_f.get('Expires')}; renew it in build.py)",
      expires is not None and now < expires <= now + datetime.timedelta(days=366))
if expires is not None and now < expires < now + datetime.timedelta(days=60):
    print(f"WARN: security.txt expires {sec_f['Expires']}: move SECURITY_TXT_EXPIRES in build.py a year ahead and rebuild")
check(".nojekyll is there, so Pages serves .well-known/ (and no page uses Jekyll)", (DOCS / ".nojekyll").exists()
      and not (DOCS / "_config.yml").exists()
      and not any(re.search(r"\{%|\{\{|\A---\n", f.read_text()) for f in DOCS.glob("*.html")))
# 150 KB was set with two states; each state adds about 4 KB of card, map and data (Nick, 2026-10-08: 200 KB)
check("index.html under 200 KB", len(SERVED.encode()) < 200 * 1024, f"{len(SERVED.encode()) / 1024:.0f} KB")

# ============================================================ serve docs/ and fetch every local link
def free_port():
    with socket.socket() as s_:
        s_.bind(("127.0.0.1", 0))
        return s_.getsockname()[1]


class Quiet(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *a):
        pass


class Server(http.server.ThreadingHTTPServer):
    # Chrome opens several connections at once, and socketserver's default listen backlog of 5 now and then
    # dropped one (ERR_CONNECTION_RESET on an icon: 2 page loads in 400), failing the browser checks for nothing
    request_queue_size = 64


port = free_port()
srv = Server(("127.0.0.1", port), partial(Quiet, directory=str(DOCS)))
threading.Thread(target=srv.serve_forever, daemon=True).start()
base_url = f"http://127.0.0.1:{port}/"


def status(path):
    try:
        with urllib.request.urlopen(base_url + path.lstrip("/"), timeout=10) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code


local = set()
for doc in (PAGE, p404):
    for ref in re.findall(r'(?:href|src)="([^"#:]+)"', doc):
        local.add(ref)
for i in man["icons"]:
    local.add(i["src"])
local |= {"og.png", "robots.txt", "sitemap.xml", "CNAME", "404.html", "favicon.ico", ".well-known/security.txt"}
bad = [(p, status(p)) for p in sorted(local)]
bad = [b for b in bad if b[1] != 200]
check(f"served locally: {len(local)} local links and files all return 200", not bad, str(bad))
check("a missing page returns 404", status("no-such-page/") == 404)
with urllib.request.urlopen(base_url + ".well-known/security.txt", timeout=10) as r_:
    check("security.txt is served as text/plain", r_.headers.get_content_type() == "text/plain")

if "--browser" in sys.argv:
    r = subprocess.run(["node", str(TOOLS / "browser_check.mjs"), "--url", base_url], capture_output=True, text=True, timeout=300)
    print(r.stdout.strip())
    check("browser checks (headless Chrome)", r.returncode == 0, r.stderr.strip()[-400:])
srv.shutdown()

print(f"\n{'FAILED: ' + '; '.join(fails) if fails else 'ALL CHECKS PASS'}")
sys.exit(1 if fails else 0)
