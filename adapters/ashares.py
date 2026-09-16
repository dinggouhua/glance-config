#!/usr/bin/env python3
"""A-share quote adapter for Glance: JSON quotes plus SVG sparkline charts.

The JSON endpoint used to fetch upstream inside the handler, which is what made
it slow enough to hit Glance's 5s client timeout. Upstream I/O now lives in a
background refresher; the handler only serves memory (and, for the chart
endpoint, cached daily closes).
"""
import json
import re
import time
import urllib.request

import adapter_common as ac

PORT = 8900
QUOTE_TTL = 55
KLINE_TTL = 300
UPSTREAM_TIMEOUT = 4
HEADERS = {"User-Agent": "Mozilla/5.0", "Referer": "https://gu.qq.com/"}

# (symbol_with_market_prefix, display_code, name), in the display order the
# /stocks widget expects (must stay identical to the original adapter output).
STOCKS = [
    ("sh601318", "601318", "中国平安"),
    ("sh600036", "600036", "招商银行"),
    ("sh600030", "600030", "中信证券"),
    ("sh603871", "603871", "嘉友国际"),
    ("sh600887", "600887", "伊利股份"),
    ("sz002027", "002027", "分众传媒"),
    ("sh600660", "600660", "福耀玻璃"),
    ("sh601021", "601021", "春秋航空"),
]

_kline = {}                     # code -> {"at": monotonic, "closes": [...]}


def request(url, timeout=UPSTREAM_TIMEOUT):
    req = urllib.request.Request(url, headers=HEADERS)
    return urllib.request.urlopen(req, timeout=timeout).read()


def fetch_stocks():
    url = "https://qt.gtimg.cn/q=" + ",".join(s for s, _, _ in STOCKS)
    raw = request(url).decode("gbk", "replace")
    stocks = []
    for line in raw.split(";"):
        if '="' not in line:
            continue
        key, payload = line.split('="', 1)
        symbol = key.rsplit("_", 1)[-1]
        fields = payload.rstrip('"').split("~")
        if len(fields) < 33:
            continue
        try:
            price = float(fields[3] or 0)
            previous = float(fields[4] or 0)
            change = float(fields[31] or 0)
            percent = float(fields[32] or 0)
        except ValueError:
            continue
        code = symbol[2:]
        name = next((n for s, c, n in STOCKS if c == code), fields[1])
        stocks.append({
            "symbol": code, "name": name, "price": price, "previous": previous,
            "change": change, "percent": percent, "timestamp": fields[30],
            "chart_url": "https://www.dclaw.top/stock-chart/%s" % code,
            "url": "https://gu.qq.com/%s" % symbol,
        })
    if not stocks:
        raise RuntimeError("no quotes returned from Tencent")
    dates = [s["timestamp"][:8] for s in stocks if s["timestamp"]]
    return {"source": "腾讯财经", "as_of": max(dates) if dates else "",
            "stocks": stocks}


def fetch_klines(symbol):
    """Daily closes used to draw the sparkline (cached separately, 5 min)."""
    url = ("https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
           "?param=%s,day,,,30,qfq" % symbol)
    data = json.loads(request(url).decode("utf-8", "replace"))
    node = (data.get("data") or {}).get(symbol) or {}
    rows = node.get("qfqday") or node.get("day") or []
    return [float(r[2]) for r in rows if len(r) > 2]


def klines(symbol, code):
    ent = _kline.get(code)
    now = time.monotonic()
    if ent and now - ent["at"] < KLINE_TTL:
        return ent["closes"]
    closes = fetch_klines(symbol)
    _kline[code] = {"at": now, "closes": closes}
    return closes


def sparkline_svg(closes, rising):
    if not closes:
        raise RuntimeError("no kline data")
    w, h, pad = 320, 64, 4
    lo, hi = min(closes), max(closes)
    span = (hi - lo) or 1
    step = (w - 2 * pad) / max(len(closes) - 1, 1)
    pts = [(pad + i * step, h - pad - (c - lo) / span * (h - 2 * pad))
           for i, c in enumerate(closes)]
    line = " ".join("%.1f,%.1f" % p for p in pts)
    area = "%.1f,%.1f %s %.1f,%.1f" % (pts[0][0], h, line, pts[-1][0], h)
    color = "#ef4444" if rising else "#22c55e"   # A-share convention
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 %d %d" width="%d" height="%d">'
        '<polygon points="%s" fill="%s" opacity="0.16"/>'
        '<polyline points="%s" fill="none" stroke="%s" stroke-width="1.6" '
        'stroke-linejoin="round" stroke-linecap="round"/></svg>'
        % (w, h, w, h, area, color, line, color)
    ).encode("utf-8")


def chart_route(handler, path):
    code = path.rsplit("/", 1)[-1]
    if not re.fullmatch(r"\d{6}", code):
        return 404, b"not found", "text/plain"
    item = next((x for x in STOCKS if x[1] == code), None)
    if not item:
        return 404, b"not found", "text/plain"
    symbol = item[0]
    quotes = (handler.server.cache.get() or {}).get("stocks", [])
    change = next((s["change"] for s in quotes if s["symbol"] == code), 0)
    return 200, sparkline_svg(klines(symbol, code), change >= 0), \
        "image/svg+xml; charset=utf-8"


if __name__ == "__main__":
    ac.serve("ashares", PORT, fetch_stocks, QUOTE_TTL,
             paths={"/", "/stocks"}, routes={"/chart/": chart_route,
                                              "/stock-chart/": chart_route})