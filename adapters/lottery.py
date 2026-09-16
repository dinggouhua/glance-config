#!/usr/bin/env python3
"""大乐透开奖结果 + 固定号码中奖对比 -> Glance custom-api JSON 适配器。

数据源：极速数据 https://api.jisuapi.com/caipiao/query (caipiaoid=14 大乐透)
appkey 从环境变量 JISUAPI_APPKEY 读取，未设置时返回占位状态（不报错）。
固定号码（用户投注）：前区 08 19 24 26 31，后区 01 06。
监听 127.0.0.1:8901。
"""
import json
import os
import time
import threading
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import adapter_common as ac

CAIPIAO_ID = 14          # 大乐透
CACHE_TTL = 30 * 60      # 30 分钟
URL = "https://api.jisuapi.com/caipiao/query"

# 用户固定投注号码
MY_FRONT = ["08", "19", "24", "26", "31"]
MY_BACK = ["01", "06"]

# key 从环境变量读取
APPKEY = os.environ.get("JISUAPI_APPKEY", "").strip()

_cached = {"at": 0.0, "body": None, "refreshing": False}
_cache_lock = threading.Lock()


def check_prize(front_hit, back_hit):
    """按体彩官方大乐透中奖规则返回奖级名称，None 表示未中奖。

    规则（前区命中数 + 后区命中数）：
      一等奖 5+2
      二等奖 5+1
      三等奖 5+0
      四等奖 4+2
      五等奖 4+1
      六等奖 3+2
      七等奖 4+0
      八等奖 3+1 或 2+2
      九等奖 3+0 或 2+1 或 1+2 或 0+2
    """
    key = (front_hit, back_hit)
    if key == (5, 2):
        return "一等奖"
    if key == (5, 1):
        return "二等奖"
    if key == (5, 0):
        return "三等奖"
    if key == (4, 2):
        return "四等奖"
    if key == (4, 1):
        return "五等奖"
    if key == (3, 2):
        return "六等奖"
    if key == (4, 0):
        return "七等奖"
    if key in ((3, 1), (2, 2)):
        return "八等奖"
    if key in ((3, 0), (2, 1), (1, 2), (0, 2)):
        return "九等奖"
    return None


def normalize(nums):
    """把号码串清洗为 2 位补零的字符串列表，去重且保持顺序无关（用于集合比较）。"""
    out = []
    for n in nums:
        s = str(n).strip().zfill(2)
        if s not in out:
            out.append(s)
    return out


def fetch():
    if not APPKEY:
        return {
            "status": "no_key",
            "message": "等待填写 JISUAPI_APPKEY",
            "ready": False,
            "my_front": MY_FRONT,
            "my_back": MY_BACK,
        }

    params = urllib.parse.urlencode({
        "appkey": APPKEY,
        "caipiaoid": CAIPIAO_ID,
        "issueno": "",
    })
    req = urllib.request.Request(f"{URL}?{params}", headers={"User-Agent": "Mozilla/5.0"})
    resp = urllib.request.urlopen(req, timeout=15)
    data = json.loads(resp.read().decode("utf-8"))

    if data.get("status") != 0:
        return {
            "status": "error",
            "message": data.get("msg", "接口返回错误"),
            "ready": False,
            "my_front": MY_FRONT,
            "my_back": MY_BACK,
        }

    r = data.get("result", {})
    number = r.get("number", "")          # 前区 5 个号码（空格分隔）
    refer = r.get("refernumber", "")       # 后区 2 个号码（空格分隔）
    prize = r.get("prize", [])
    sale = r.get("saleamount", "")
    total = r.get("totalmoney", "")

    front = normalize(number.replace("+", " ").split())
    back = normalize(refer.replace("+", " ").split())

    # 中奖对比：集合求交，统计前区/后区命中个数
    front_hit = len(set(front) & set(MY_FRONT))
    back_hit = len(set(back) & set(MY_BACK))
    prize_name = check_prize(front_hit, back_hit)

    return {
        "status": "ok",
        "ready": True,
        "issueno": r.get("issueno", ""),
        "opendate": r.get("opendate", ""),
        "deadline": r.get("deadline", ""),
        "front": front,
        "back": back,
        "my_front": MY_FRONT,
        "my_back": MY_BACK,
        "front_hit": front_hit,
        "back_hit": back_hit,
        "prize": prize_name,              # 中奖则为奖级名，未中为 null
        "number_raw": number,
        "refernumber": refer,
        "saleamount": sale,
        "totalmoney": total,
        "prize_count": len(prize) if prize else 0,
    }


def _refresh():
    try:
        body = json.dumps(fetch(), ensure_ascii=False).encode("utf-8")
        with _cache_lock:
            _cached.update(at=time.monotonic(), body=body, refreshing=False)
    except Exception as exc:
        fallback = json.dumps({"status": "error", "message": str(exc), "ready": False,
                               "my_front": MY_FRONT, "my_back": MY_BACK}, ensure_ascii=False).encode("utf-8")
        with _cache_lock:
            if not _cached["body"]:
                _cached.update(at=time.monotonic(), body=fallback)
            _cached["refreshing"] = False


def payload():
    now = time.monotonic()
    with _cache_lock:
        body = _cached["body"]
        stale = not body or now - _cached["at"] >= CACHE_TTL
        if stale and not _cached["refreshing"]:
            _cached["refreshing"] = True
            threading.Thread(target=_refresh, daemon=True).start()
        if body:
            return body
    # First request returns immediately; never blocks on the upstream API.
    return json.dumps({"status": "loading", "message": "正在更新开奖结果", "ready": False,
                       "my_front": MY_FRONT, "my_back": MY_BACK}, ensure_ascii=False).encode("utf-8")


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path not in ("/", "/lottery"):
            self.send_error(404)
            return
        try:
            body = payload()
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except Exception as exc:
            body = json.dumps(
                {"status": "error", "message": str(exc), "ready": False,
                 "my_front": MY_FRONT, "my_back": MY_BACK}
            ).encode("utf-8")
            self.send_response(502)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    def log_message(self, *_):
        pass


if __name__ == "__main__":
    ac.Server(("127.0.0.1", 8901), Handler).serve_forever()