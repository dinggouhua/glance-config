#!/usr/bin/env python3
"""美股恐慌指数 (VIX) adapter for Glance.

Upstream: Yahoo chart API, falling back to the official CBOE daily CSV.
All upstream I/O happens in a background thread; the HTTP handler only
serialises the in-memory payload (see /opt/adapter_common.py for why).
"""
import csv
import datetime
import io
import json
import urllib.request

import adapter_common as ac

PORT = 8897
TTL = 240                      # 4 min: Yahoo is fine at this cadence, CBOE is 1/day
YAHOO = "https://query1.finance.yahoo.com/v8/finance/chart/%5EVIX?range=5d&interval=1d"
CBOE = "https://cdn.cboe.com/api/global/us_indices/daily_prices/VIX_History.csv"
UPSTREAM_TIMEOUT = 4           # keep well under Glance's hard 5s client timeout


def from_yahoo():
    req = urllib.request.Request(YAHOO, headers={"User-Agent": "Mozilla/5.0"})
    payload = json.loads(urllib.request.urlopen(req, timeout=UPSTREAM_TIMEOUT).read())
    meta = payload["chart"]["result"][0]["meta"]
    value = float(meta["regularMarketPrice"])
    previous = float(meta.get("chartPreviousClose") or meta["previousClose"])
    stamp = int(meta.get("regularMarketTime", 0))
    date = (datetime.datetime.fromtimestamp(stamp, datetime.timezone.utc)
            .strftime("%m/%d/%Y") if stamp else "latest")
    return make_result(value, previous, date, "yahoo")


def from_cboe():
    req = urllib.request.Request(CBOE, headers={
        "User-Agent": "Glance-VIX/1.0", "Cache-Control": "no-cache"})
    raw = urllib.request.urlopen(req, timeout=UPSTREAM_TIMEOUT).read().decode("utf-8")
    rows = [r for r in csv.DictReader(io.StringIO(raw))
            if r.get("CLOSE") not in (None, "", ".")]
    latest, previous_row = rows[-1], rows[-2]
    return make_result(float(latest["CLOSE"]), float(previous_row["CLOSE"]),
                       latest["DATE"], "cboe")


def make_result(value, previous, date, source):
    """Field set mirrors the pre-refactor payload exactly (no extra keys)."""
    if value < 15:
        level = "低波动"
    elif value < 20:
        level = "正常"
    elif value < 30:
        level = "市场紧张"
    else:
        level = "高风险"
    change = value - previous
    return {"value": round(value, 2), "previous": round(previous, 2),
            "change": round(change, 2), "pct": round(change / previous * 100, 2) if previous else 0,
            "date": date, "level": level}


def build():
    try:
        return from_yahoo()
    except Exception as exc:
        ac.log("vix: yahoo failed (%s), trying CBOE" % exc)
        return from_cboe()


if __name__ == "__main__":
    ac.serve("vix", PORT, build, TTL)