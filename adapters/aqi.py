#!/usr/bin/env python3
"""新浪 A股行情 -> JSON 转换服务（旧版持仓口径，供 Glance custom-api 使用）。

注意：当前没有任何 widget 引用 8899；持仓口径也已由 portfolio-summary 取代。
上游请求改到后台线程，HTTP handler 只序列化内存 payload
（原因见 /opt/adapter_common.py 顶部说明）。
"""
import json, urllib.request

import adapter_common as ac

PORT = 8899
TTL = 60
UPSTREAM_TIMEOUT = 5

# 关注标的：代码 -> 名称   (sh=沪, sz=深)
# 关注标的：代码 -> {名称、持仓数量、平均成本}
STOCKS = {
    "sh600036": {"name": "招商银行", "quantity": 600, "cost": 37.481},
    "sh601318": {"name": "中国平安", "quantity": 400, "cost": 56.244},
    "sh603871": {"name": "嘉友国际", "quantity": 1000, "cost": 12.441},
    "sh600887": {"name": "伊利股份", "quantity": 400, "cost": 26.103},
    "sz002027": {"name": "分众传媒", "quantity": 1000, "cost": 4.944},
    "sh600660": {"name": "福耀玻璃", "quantity": 100, "cost": 57.401},
    "sz000786": {"name": "北新建材", "quantity": 300, "cost": 16.637},
    "sh601021": {"name": "春秋航空", "quantity": 100, "cost": 43.710},
}

def fetch(codes):
    url = "https://hq.sinajs.cn/list=" + ",".join(codes)
    req = urllib.request.Request(url, headers={"Referer": "https://finance.sina.com.cn"})
    raw = urllib.request.urlopen(req, timeout=UPSTREAM_TIMEOUT).read().decode("gbk", "ignore")
    out = []
    for line in raw.strip().splitlines():
        key, _, val = line.partition("=")
        code = key.replace("var hq_str_", "").strip()
        val = val.strip().strip('";')
        f = val.split(",")
        if len(f) < 32:
            continue
        holding = STOCKS.get(code, {})
        name = f[0]
        open_p   = float(f[1]) if f[1] else 0
        prev_close = float(f[2]) if f[2] else 0
        price    = float(f[3]) if f[3] else 0
        high     = float(f[4]) if f[4] else 0
        low      = float(f[5]) if f[5] else 0
        change   = price - prev_close
        pct      = (change / prev_close * 100) if prev_close else 0
        quantity = holding.get("quantity", 0)
        cost = holding.get("cost", 0)
        cost_amount = cost * quantity
        market_value = price * quantity
        profit = market_value - cost_amount
        profit_pct = (profit / cost_amount * 100) if cost_amount else 0
        out.append({
            "code": code,
            "name": holding.get("name", name),
            "price": price,
            "change": round(change, 2),
            "pct": round(pct, 2),
            "high": high,
            "low": low,
            "open": open_p,
            "prev_close": prev_close,
            "quantity": quantity,
            "cost": cost,
            "cost_amount": round(cost_amount, 2),
            "market_value": round(market_value, 2),
            "profit": round(profit, 2),
            "profit_pct": round(profit_pct, 2),
        })
    total_cost = sum(item["cost_amount"] for item in out)
    total_value = sum(item["market_value"] for item in out)
    total_profit = total_value - total_cost
    total_profit_pct = (total_profit / total_cost * 100) if total_cost else 0
    return {
        "stocks": out,
        "summary": {
            "cost_amount": round(total_cost, 2),
            "market_value": round(total_value, 2),
            "profit": round(total_profit, 2),
            "profit_pct": round(total_profit_pct, 2),
        },
    }

def build():
    data = fetch(list(STOCKS.keys()))
    if not data.get("stocks"):
        raise RuntimeError("新浪行情未返回任何标的")
    return data


if __name__ == "__main__":
    ac.serve("aqi", PORT, build, TTL)