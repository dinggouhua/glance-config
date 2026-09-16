#!/usr/bin/env python3
"""A股市场环境总览适配器：指数、涨跌家数、成交额和涨跌停近似统计。

上游请求在后台线程完成，HTTP handler 只序列化内存 payload
（原因见 /opt/adapter_common.py 顶部说明）。
"""
import json
import time
import urllib.request
import urllib.parse
import urllib.error
from datetime import datetime

import adapter_common as ac

PORT = 8905
CACHE_TTL = 60
UPSTREAM_TIMEOUT = 6
INDEX_URL = "https://qt.gtimg.cn/q=sh000001,sh000300,sz399001,sz399006"
ALL_URL = "https://push2.eastmoney.com/api/qt/clist/get"
ALL_PARAMS = {
    "pz": "100", "po": "1", "np": "1", "fltt": "2", "invt": "2", "fid": "f12",
    "fs": "m:0+t:6,m:1+t:2", "fields": "f2,f3,f6,f12,f14",
}


def get(url, timeout=UPSTREAM_TIMEOUT):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def num(v, default=0.0):
    try:
        return float(v) if v not in (None, "", "-") else default
    except (TypeError, ValueError):
        return default


def fetch_indices():
    text = get(INDEX_URL).decode("gbk", "replace")
    names = {"000001": "上证指数", "000300": "沪深300", "399001": "深证成指", "399006": "创业板指"}
    result = []
    for line in text.split(";"):
        if '="' not in line:
            continue
        key = line.split("=", 1)[0].split("_")[-1]
        f = line.split('"', 2)[1].split("~")
        if len(f) < 33:
            continue
        code = f[2]
        result.append({"code": code, "name": names.get(code, f[1]),
                       "price": num(f[3]), "change": num(f[31]),
                       "percent": num(f[32])})
    return result


def fetch_market():
    # 上证/深证综合指数的汇总字段分别包含沪市/深市涨跌家数和成交额。
    url = ("https://push2.eastmoney.com/api/qt/ulist.np/get?"
           "fltt=2&secids=1.000001,0.399001&fields=f2,f3,f4,f6,f104,f105,f106")
    data = json.loads(get(url).decode("utf-8"))
    rows = (data.get("data") or {}).get("diff") or []
    if len(rows) < 2:
        raise RuntimeError("沪深市场汇总数据不完整")
    up = sum(int(num(r.get("f104"))) for r in rows)
    down = sum(int(num(r.get("f105"))) for r in rows)
    flat = sum(int(num(r.get("f106"))) for r in rows)
    amount = sum(num(r.get("f6")) for r in rows)
    return {
        "up": up, "down": down, "flat": flat,
        "limit_up": 0, "limit_down": 0,
        "total": up + down + flat, "sampled": 0,
        "amount_yi": round(amount / 100000000, 2),
    }


def fetch():
    try:
        indices = fetch_indices()
        market = fetch_market()
        return {"status": "ok", "date": datetime.now().strftime("%Y-%m-%d %H:%M"),
                "indices": indices, "market": market,
                "note": "上涨/下跌/平盘为沪深市场汇总口径，非交易时段可能为上一交易日数据"}
    except Exception as exc:
        return {"status": "error", "message": "市场数据暂时不可用: " + str(exc)[:160]}


def build():
    """Transient failures raise so the warm cache keeps the last good payload."""
    data = fetch()
    if data.get("status") != "ok":
        raise RuntimeError(data.get("message") or "市场数据异常")
    return data


if __name__ == "__main__":
    ac.serve("market-overview", PORT, build, CACHE_TTL, paths={"/", "/overview"})
