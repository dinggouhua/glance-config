#!/usr/bin/env python3
"""聚合数据 id=540 国内油价适配器，供 Glance custom-api 使用。

上游请求全部在后台线程完成，HTTP handler 只序列化内存里的 payload
（原因见 /opt/adapter_common.py 顶部说明）。
"""
import json
import os
import time
import urllib.parse
import urllib.request

import adapter_common as ac

URL = "https://apis.juhe.cn/gnyj/query"
PORT = 8903
CACHE_TTL = 300
UPSTREAM_TIMEOUT = 6


def fetch():
    key = os.environ.get("JUHE_OIL_APIKEY", "").strip()
    if not key:
        return {"status": "no_key", "message": "等待填写 JUHE_OIL_APIKEY"}
    query = urllib.parse.urlencode({"key": key})
    req = urllib.request.Request(URL + "?" + query, headers={"User-Agent": "Glance-oil-price/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=UPSTREAM_TIMEOUT) as response:
            data = json.loads(response.read().decode("utf-8"))
        if data.get("error_code", 0) != 0 or not isinstance(data.get("result"), list):
            return {"status": "error", "message": data.get("reason", "接口返回异常")}
        jiangsu = next((row for row in data["result"] if row.get("city") == "江苏"), None)
        if not jiangsu:
            return {"status": "error", "message": "未找到江苏油价"}
        return {
            "status": "ok",
            "city": "江苏",
            "date": time.strftime("%Y-%m-%d"),
            "gas92": jiangsu.get("92h", ""),
            "gas95": jiangsu.get("95h", ""),
            "gas98": jiangsu.get("98h", ""),
            "diesel": jiangsu.get("0h", ""),
        }
    except Exception as exc:
        return {"status": "error", "message": "油价接口暂时不可用: " + str(exc)[:120]}


def build():
    """Transient failures raise so the warm cache keeps the last good payload.

    A missing API key is a permanent configuration state, not a transient
    failure, so it is passed straight through for the widget to display.
    """
    data = fetch()
    if data.get("status") == "error":
        raise RuntimeError(data.get("message") or "油价接口异常")
    return data


if __name__ == "__main__":
    ac.serve("oil", PORT, build, CACHE_TTL, paths={"/", "/oil"})
