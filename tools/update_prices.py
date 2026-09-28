#!/usr/bin/env python3
"""Refresh the BLS price series in tools/site-data.json, then rebuild the page.

    python3 tools/update_prices.py              # fetch, update site-data.json, run build.py
    python3 tools/update_prices.py --out x.json # fetch and write the result elsewhere (no rebuild)

Uses the public BLS API v1 (no key): one POST for all series, the last 10 years.
v1 allows 25 queries a day per IP, so run it once per monthly CPI release, not in a loop.
Months BLS did not publish (value "-", e.g. October 2025) are kept as gaps (null).
Nothing is written unless every series comes back complete and no newer than today, and the page
builds from the new data (checked with `build.py --data <new file> --dry-run` before site-data.json
is replaced), so site-data.json and docs/index.html never disagree.
"""
import datetime
import json
import os
import pathlib
import subprocess
import sys
import urllib.request

TOOLS = pathlib.Path(__file__).resolve().parent
DATA = TOOLS / "site-data.json"
API = "https://api.bls.gov/publicAPI/v1/timeseries/data/"
REST = "CUUR0000SEFV"
MONTHS = ["January", "February", "March", "April", "May", "June", "July",
          "August", "September", "October", "November", "December"]


def month_long(ym):
    return f"{MONTHS[int(ym[5:]) - 1]} {ym[:4]}"


def and_list(items):
    items = list(items)
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]


def fetch(series_ids, start, end, email):
    body = json.dumps({"seriesid": series_ids, "startyear": str(start), "endyear": str(end)}).encode()
    req = urllib.request.Request(API, data=body, method="POST", headers={
        "Content-Type": "application/json",
        "User-Agent": f"eatsranked.com price updater (+https://eatsranked.com/; {email})",
    })
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode())


def monthly(series):
    """BLS rows -> [["YYYY-MM", value or None], ...], oldest first; skips annual averages (M13)."""
    out = {}
    for row in series.get("data", []):
        p = row.get("period", "")
        if not (p.startswith("M") and p[1:].isdigit() and 1 <= int(p[1:]) <= 12):
            continue
        v = row.get("value", "").strip()
        try:
            val = float(v)
        except ValueError:
            val = None           # "-": BLS did not publish this month
        out[f"{row['year']}-{int(p[1:]):02d}"] = val
    return [[m, out[m]] for m in sorted(out)]


def main():
    out_path = None
    if "--out" in sys.argv:
        out_path = pathlib.Path(sys.argv[sys.argv.index("--out") + 1])
    raw = DATA.read_text()
    d = json.loads(raw)
    ids = list(d["prices"]["series"])
    today = datetime.date.today()
    end = today.year
    start = end - 9                      # v1 returns at most 10 years per query

    res = fetch(ids, start, end, d["brand"]["email"])
    if res.get("status") != "REQUEST_SUCCEEDED":
        sys.exit(f"update_prices: BLS said {res.get('status')}: {res.get('message')}")
    for msg in res.get("message") or []:
        print("BLS:", msg)
    got = {s["seriesID"]: monthly(s) for s in res["Results"]["series"]}

    this_month = f"{today.year}-{today.month:02d}"
    for sid in ids:
        rows = got.get(sid)
        if not rows or sum(v is not None for _, v in rows) < 24:
            sys.exit(f"update_prices: {sid} came back empty or short; nothing written")
        if rows[-1][0] > this_month:
            sys.exit(f"update_prices: {sid} has a month in the future ({rows[-1][0]}); nothing written")
        old = {m: v for m, v in d["prices"]["series"][sid]["monthly"]}
        old_last = max(m for m, v in old.items() if v is not None)
        new_last = max(m for m, v in rows if v is not None)
        if new_last < old_last:
            sys.exit(f"update_prices: {sid} would go back from {old_last} to {new_last}; nothing written")
        changed = [m for m, v in rows if m in old and old[m] is not None and v is not None and abs(old[m] - v) > 1e-9]
        if changed:
            print(f"note: BLS revised {sid} for {', '.join(changed[:6])}{'…' if len(changed) > 6 else ''}")

    # the latest month every series has, so the page compares like with like
    last = min(max(m for m, v in got[sid] if v is not None) for sid in ids)
    for sid in ids:
        # trim to the common latest month; keep unpublished months inside the range as null
        d["prices"]["series"][sid]["monthly"] = [[m, v] for m, v in got[sid] if m <= last]
    gaps = sorted({m for sid in ids for m, v in d["prices"]["series"][sid]["monthly"] if v is None})
    src = ("U.S. Bureau of Labor Statistics, Consumer Price Index for All Urban Consumers (CPI-U), "
           f"not seasonally adjusted, through {month_long(last)}.")
    if gaps:
        src += f" BLS did not publish {and_list(month_long(m) for m in gaps)} data."
    d["prices"]["source"] = src

    text = json.dumps(d, indent=1) + ("\n" if raw.endswith("\n") else "")
    if out_path:
        out_path.write_text(text)
        print(f"wrote {out_path} (latest month {last}, gaps {gaps}); site-data.json untouched")
        builds(out_path)
        return
    if text == raw:
        print(f"prices unchanged (latest month {last})")
    else:
        # Check the page builds from the new data before site-data.json changes.
        tmp = DATA.with_name(".site-data.new.json")
        tmp.write_text(text)
        try:
            if not builds(tmp):
                sys.exit("update_prices: the page doesn't build from the new data; site-data.json and docs/ untouched")
            os.replace(tmp, DATA)
        finally:
            if tmp.exists():
                tmp.unlink()
        print(f"site-data.json updated: latest month {last}, gaps {gaps}")
    r = subprocess.run([sys.executable, str(TOOLS / "build.py")])
    if r.returncode:
        DATA.write_text(raw)
        sys.exit("update_prices: build.py failed; site-data.json restored")


def builds(path):
    """True if build.py can build the page from this data file (writes nothing)."""
    r = subprocess.run([sys.executable, str(TOOLS / "build.py"), "--data", str(path), "--dry-run"],
                       capture_output=True, text=True)
    print((r.stdout + r.stderr).strip())
    return r.returncode == 0


if __name__ == "__main__":
    main()
