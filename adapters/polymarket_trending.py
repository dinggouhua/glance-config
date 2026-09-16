#!/usr/bin/env python3
"""Read-only Polymarket homepage carousel adapter for Glance.

Primary source : https://polymarket.com/api/homepage/carousel?locale=zh  (matches homepage)
Fallback source: gamma-api volume ranking (used only when the carousel is unavailable)
"""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse
from urllib.request import Request, urlopen
from datetime import datetime
import json, threading, time

import adapter_common as ac

HOST = "127.0.0.1"
PORT = 8908
CAROUSEL = "https://polymarket.com/api/homepage/carousel?locale=zh"
FALLBACK = ("https://gamma-api.polymarket.com/markets?limit=100"
            "&active=true&closed=false&order=volume&ascending=false")
TTL = 300
CACHE_FILE = "/tmp/polymarket_last.json"
UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

cache = {"at": 0.0, "data": None, "refreshing": False}
lock = threading.Lock()


def _get_json(url):
    req = Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    with urlopen(req, timeout=12) as r:
        return json.loads(r.read().decode("utf-8", "replace"), strict=False)


def fmt_volume(v):
    try:
        v = float(v or 0)
    except (TypeError, ValueError):
        v = 0.0
    if v <= 0:
        return ""
    if v >= 1_000_000_000:
        return "$%.2fB" % (v / 1_000_000_000)
    if v >= 1_000_000:
        return "$%.1fM" % (v / 1_000_000)
    if v >= 1_000:
        return "$%.1fK" % (v / 1_000)
    return "$%.0f" % v


def _as_list(value):
    if isinstance(value, str):
        try:
            return json.loads(value, strict=False)
        except ValueError:
            return []
    return value or []


def _pct(value):
    try:
        return round(float(value) * 100, 1)
    except (TypeError, ValueError):
        return None


LABELS = {"Up": "看涨", "Down": "看跌", "Yes": "是", "No": "否"}

# homepage shows '是' (index 0) for binaries, the leading option for multi-choice ladders,
# and the leading team for sports moneylines.
LEADING_INDEX = 0


def leading_outcome(event, kind):
    markets = [m for m in (event.get("markets") or [])
               if isinstance(m, dict) and not m.get("closed")]
    if not markets:
        return None

    if kind == "multiple-market" or any(m.get("negRisk") for m in markets):
        pool, idx = markets, LEADING_INDEX          # leading option of the ladder
    elif kind == "sports":
        pool = [next((m for m in markets if m.get("slug") == event.get("slug")), markets[0])]
        idx = None                                   # leading team (max price)
    else:
        pool = [next((m for m in markets if m.get("slug") == event.get("slug")), markets[0])]
        idx = LEADING_INDEX                          # binary: the '是' side

    cands = []
    for m in pool:
        names = _as_list(m.get("outcomes"))
        prices = _as_list(m.get("outcomePrices"))
        group = m.get("groupItemTitle")
        for i, price in enumerate(prices):
            if idx is not None and i != idx:
                continue
            try:
                p = float(price)
            except (TypeError, ValueError):
                continue
            name = str(names[i]) if i < len(names) else "?"
            label = str(group) if group else LABELS.get(name, name)
            cands.append((label, p, _pct(m.get("oneDayPriceChange"))))
    if not cands:
        return None
    cands.sort(key=lambda x: x[1], reverse=True)
    top = cands[0]
    alts = [(l, int(round(p * 100))) for l, p, _ in cands[1:3]]
    return top[0], round(top[1] * 100, 1), top[2], alts


def _delta_text(delta):
    if delta is None or abs(delta) < 0.05:
        return "", ""
    if delta > 0:
        return "▲ %.1f" % abs(delta), "pm-up"
    return "▼ %.1f" % abs(delta), "pm-down"


def _end_text(end):
    if not end:
        return ""
    try:
        dt = datetime.fromisoformat(str(end).replace("Z", "+00:00"))
    except ValueError:
        return ""
    return dt.strftime("%Y-%m-%d")


def _row(title, lead, volume, end, kind, url):
    text, css = _delta_text(lead[2])
    alts = lead[3] if len(lead) > 3 else []
    return {
        "title": title, "label": lead[0], "prob": lead[1], "delta": lead[2],
        "delta_text": text, "delta_class": css,
        "alts_text": " · ".join("%s %d%%" % (l, p) for l, p in alts),
        "volume": volume, "volume_text": fmt_volume(volume),
        "end": end or "", "end_text": _end_text(end),
        "kind": kind, "url": url,
    }


def from_carousel():
    items = _get_json(CAROUSEL)
    out = []
    for item in items if isinstance(items, list) else []:
        ev = (item or {}).get("event") or {}
        slug = ev.get("slug")
        if not slug or ev.get("closed"):
            continue
        kind = str(item.get("type") or "")
        lead = leading_outcome(ev, kind)
        if not lead:
            continue
        out.append(_row(str(ev.get("title") or "未命名事件"), lead,
                        float(ev.get("volume") or 0), ev.get("endDate"), kind,
                        "https://polymarket.com/zh/event/" + str(slug)))
    if not out:
        raise ValueError("carousel returned no usable items")
    return out


def from_gamma():
    rows = _get_json(FALLBACK)
    out = []
    for m in rows if isinstance(rows, list) else []:
        if not m.get("active") or m.get("closed"):
            continue
        lead = leading_outcome({"slug": m.get("slug"), "markets": [m]}, "single-market")
        slug = m.get("slug")
        if not lead or not slug:
            continue
        out.append(_row(str(m.get("question") or "未命名盘口"), lead,
                        float(m.get("volume") or 0), m.get("endDate"), "market",
                        "https://polymarket.com/market/" + str(slug)))
    out.sort(key=lambda x: x["volume"], reverse=True)
    return out[:7]


def fetch():
    try:
        markets, source = from_carousel(), "homepage-carousel"
    except Exception as exc:
        markets, source = from_gamma(), "gamma-volume-fallback: %s" % exc
    return {"status": "ok", "source": source, "markets": markets[:7],
            "updated": datetime.now().isoformat(timespec="seconds")}


def _load_last():
    try:
        with open(CACHE_FILE, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return
    if isinstance(data, dict) and data.get("markets"):
        cache["data"] = data
        cache["at"] = time.monotonic()


def _save_last(data):
    if not data.get("markets"):
        return
    try:
        with open(CACHE_FILE, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False)
    except OSError:
        pass


def refresh():
    try:
        data = fetch()
    except Exception as exc:
        data = {"status": "error", "message": str(exc), "markets": []}
    with lock:
        if data.get("markets") or cache["data"] is None:
            cache["data"] = data
        cache["at"] = time.monotonic()
        cache["refreshing"] = False
    _save_last(cache["data"])


def get_data():
    if cache["data"] is None:
        # Cold start: block briefly so Glance never caches an empty payload.
        refresh()
    with lock:
        if time.monotonic() - cache["at"] >= TTL and not cache["refreshing"]:
            cache["refreshing"] = True
            threading.Thread(target=refresh, daemon=True).start()
        return cache["data"] or {"status": "loading", "markets": []}


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if urlparse(self.path).path != "/polymarket":
            self.send_error(404); return
        body = json.dumps(get_data(), ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_):
        pass


if __name__ == "__main__":
    _load_last()          # serve last-known-good immediately after a restart
    if cache["data"] is None:
        try:
            refresh()     # warm up before Glance's first request
        except Exception:
            pass
    ac.Server((HOST, PORT), Handler).serve_forever()