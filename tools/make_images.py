#!/usr/bin/env python3
"""Draw the Eats Ranked mark and the share image into ../docs.

  favicon.svg, favicon.ico (16, 32, 48), favicon-32.png, apple-touch-icon.png (180), icon-192.png, icon-512.png
  og.png (1200x630): wordmark, headline and each live app's icon, rendered by headless Chrome

Needs Pillow (e.g. chi-eats/.venv/bin/python) and Google Chrome. Rerun it when a state goes
live so the share image shows the new app icon:

    ../chi-eats/.venv/bin/python tools/make_images.py
"""
import html
import json
import pathlib
import shutil
import subprocess
import sys
import tempfile
import time

from PIL import Image, ImageDraw

TOOLS = pathlib.Path(__file__).resolve().parent
DOCS = TOOLS.parent / "docs"
SITE = json.loads((TOOLS / "site-data.json").read_text())
GEO = json.loads((TOOLS / "us-states-paths.json").read_text())
CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"

INK, PAPER, ACCENT, ACCENT_ON_INK = "#1A1814", "#FAF8F3", "#B8430F", "#E8703A"

# The mark: three bars, tall to short, the last one in the accent colour (same as the wordmark).
# Geometry on a 32-unit tile.
BARS = [(6.9, 7.5, 4.6, 17.0, PAPER), (13.7, 13.2, 4.6, 11.3, PAPER), (20.5, 17.9, 4.6, 6.6, ACCENT_ON_INK)]


def favicon_svg():
    bars = "".join(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="1.3" fill="{c}"/>' for x, y, w, h, c in BARS)
    return ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32">'
            f'<rect width="32" height="32" rx="7" fill="{INK}"/>{bars}</svg>\n')


def mark_png(size, rounded=True, pad=0.0):
    """pad: extra margin (fraction of the tile) so the bars sit smaller, e.g. for home-screen icons."""
    s = 8  # supersample
    W = size * s
    im = Image.new("RGBA", (W, W), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    if rounded:
        d.rounded_rectangle([0, 0, W - 1, W - 1], radius=W * 7 / 32, fill=INK)
    else:
        d.rectangle([0, 0, W, W], fill=INK)
    k = W / 32 * (1 - 2 * pad)
    off = W * pad
    for x, y, w, h, c in BARS:
        d.rounded_rectangle([off + x * k, off + y * k, off + (x + w) * k, off + (y + h) * k], radius=1.3 * k, fill=c)
    return im.resize((size, size), Image.LANCZOS)


def favicon_ico(out):
    """/favicon.ico, for clients that ask for it without reading the page's icon links (Bing, feed readers):
    the mark drawn at each size rather than scaled down from one."""
    frames = [mark_png(n) for n in (48, 32, 16)]
    frames[0].save(out, format="ICO", sizes=[(n, n) for n in (48, 32, 16)], append_images=frames[1:])


def og_html():
    live = [s for s in SITE["states"] if s.get("status") == "live"]
    names = {s["abbr"]: s["name"] for s in GEO["states"]}
    places = [names[s["abbr"]] for s in live]
    where = places[0] if len(places) == 1 else ", ".join(places[:-1]) + " and " + places[-1]
    icons = "".join(f'<img src="{(DOCS / s["icon"]).as_uri()}" alt="">' for s in live)
    # the icon column fits the 630 px height: 196 px icons for one or two live states, smaller as more go live
    gap = 34 if len(live) <= 2 else 22
    sz = min(196, (630 - 2 * 44 - gap * (len(live) - 1)) // max(1, len(live)))
    return f"""<!doctype html><html><head><meta charset="utf-8"><style>
html,body{{margin:0;width:1200px;height:630px;overflow:hidden}}
body{{background:{PAPER};color:{INK};font-family:system-ui,-apple-system,sans-serif;position:relative}}
.serif{{font-family:ui-serif,"New York","Iowan Old Style",Charter,Georgia,serif}}
.wm{{position:absolute;left:76px;top:70px;display:flex;align-items:center;gap:16px;font-weight:600;font-size:40px;letter-spacing:-.01em}}
.mk{{display:flex;align-items:flex-end;gap:5px;height:34px}}
.mk i{{display:block;width:9px;border-radius:2.5px;background:{INK}}}
.mk i:nth-child(1){{height:34px}}.mk i:nth-child(2){{height:23px}}.mk i:nth-child(3){{height:13px;background:{ACCENT}}}
h1{{position:absolute;left:72px;top:206px;margin:0;font-weight:500;font-size:88px;line-height:1.02;letter-spacing:-.03em;white-space:nowrap}}
h1 span{{display:block}}
/* the rule sits on the line's block, so a line that wraps to two (four or more states) pushes it up instead of running under the icons */
.sub{{position:absolute;left:76px;right:388px;bottom:70px;margin:0;padding-top:26px;border-top:2px solid #E3DED3;font-size:27px;line-height:1.2;color:#57534A}}
.sub b{{color:{INK};font-weight:600}}
.icons{{position:absolute;right:92px;top:0;bottom:0;display:flex;flex-direction:column;justify-content:center;gap:{gap}px}}
.icons img{{width:{sz}px;height:{sz}px;border-radius:{round(sz * 0.224)}px;box-shadow:0 22px 44px -18px rgba(26,24,20,.45),0 0 0 1px rgba(26,24,20,.06)}}
</style></head><body>
<div class="wm serif"><span class="mk"><i></i><i></i><i></i></span>Eats Ranked</div>
<h1 class="serif"><span>Restaurants, ranked</span><span>state by state.</span></h1>
<p class="sub">Free apps for <b>{html.escape(where)}</b>. More states coming soon.</p>
<div class="icons">{icons}</div>
</body></html>"""


def render_og(out):
    with tempfile.TemporaryDirectory() as tmp:
        page = pathlib.Path(tmp) / "og.html"
        page.write_text(og_html())
        shot = pathlib.Path(tmp) / "og.png"
        # Chrome writes the screenshot as soon as the page has loaded but may not exit on its own.
        proc = subprocess.Popen([CHROME, "--headless=new", "--disable-gpu", "--hide-scrollbars", "--no-first-run",
                                 f"--user-data-dir={tmp}/profile", "--force-device-scale-factor=1",
                                 "--window-size=1200,630", "--allow-file-access-from-files",
                                 "--virtual-time-budget=3000", f"--screenshot={shot}", page.as_uri()],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            for _ in range(300):
                if proc.poll() is not None or (shot.exists() and shot.stat().st_size > 0):
                    break
                time.sleep(0.1)
            time.sleep(0.5)
        finally:
            proc.kill()
            proc.wait()
        if not shot.exists():
            sys.exit("make_images: Chrome did not write og.png")
        im = Image.open(shot).convert("RGB")
        if im.size != (1200, 630):
            im = im.crop((0, 0, 1200, 630))
        im.save(out, optimize=True)


if __name__ == "__main__":
    DOCS.mkdir(exist_ok=True)
    (DOCS / "favicon.svg").write_text(favicon_svg())
    favicon_ico(DOCS / "favicon.ico")
    mark_png(32).save(DOCS / "favicon-32.png", optimize=True)
    mark_png(180, rounded=False, pad=0.06).convert("RGB").save(DOCS / "apple-touch-icon.png", optimize=True)
    mark_png(192, pad=0.04).save(DOCS / "icon-192.png", optimize=True)
    mark_png(512, pad=0.04).save(DOCS / "icon-512.png", optimize=True)
    if shutil.which(CHROME) or pathlib.Path(CHROME).exists():
        render_og(DOCS / "og.png")
    else:
        sys.exit("make_images: Chrome not found, og.png not rendered")
    for f in ("favicon.svg", "favicon.ico", "favicon-32.png", "apple-touch-icon.png", "icon-192.png", "icon-512.png", "og.png"):
        p = DOCS / f
        size = Image.open(p).size if p.suffix == ".png" else "svg"
        print(f"docs/{f}: {size}, {p.stat().st_size / 1024:.1f} KB")
