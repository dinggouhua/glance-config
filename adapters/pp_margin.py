#!/usr/bin/env python3
"""PP 加工利润适配器（隆众资讯 → Glance widget）。

数据源：dc.oilchem.net 需登录 cookie（/etc/glance/oilchem-cookies.json，640 root:glance）
模型：  PP产品规划与测算表.xlsx 的完全成本法
输出：  3 个牌号的净利润/现金流（元/吨）+ 原料价格，全部拍平成顶层标量键
        （Glance 模板的 .JSON.Map 不接受路径参数，嵌套对象取不到）

cookie 失效时 builder 抛异常 → adapter_common 保留上次成功 payload，
widget 上仍显示旧值与 updated 时间，不会变成「请登录」。
"""
import json
import os
import re
import sys
import time
import urllib.request
from datetime import datetime

sys.path.insert(0, "/opt")
import adapter_common as ac  # noqa: E402

PORT = 8911
TTL = 7200                      # 隆众日评一天一次；2h 足够，且对订阅账号友好
COOKIES_FILE = os.environ.get("OILCHEM_COOKIES", "/etc/glance/oilchem-cookies.json")
API = "https://dc.oilchem.net/ndc/price/list/queryPricePage"
UPSTREAM_TIMEOUT = 6            # 必须 < Glance 的 5s 硬超时余量

# ===== 定价模型（来自 PP产品规划与测算表.xlsx）=====
VAT13 = 1.13
CAPACITY = 40
DEPRECIATION = 7926
TOTAL_INVEST = 132100

# 固定/期间费用三项（三个牌号共用）
LABOR = 70 * 20 / VAT13 / CAPACITY
DEP_UNIT = DEPRECIATION / CAPACITY
REPAIR = 1321 / CAPACITY
OTHER_MFG = 800 / CAPACITY
ADMIN = 320 / CAPACITY
FINANCE = TOTAL_INVEST * 0.0355 * 0.7 / CAPACITY

QUERIES = {
    "acr":    {"varietiesId": "116", "businessType": "3", "twoLevelBusinessType": 0,
               "timeType": 0, "pageNum": 1, "pageSize": 10},
    "eth":    {"varietiesId": "196", "businessType": "3", "twoLevelBusinessType": 0,
               "timeType": 0, "pageNum": 1, "pageSize": 10},
    "1102k":  {"varietiesId": 317, "businessType": "3", "twoLevelBusinessType": "19",
               "timeType": 0, "pageNum": 1, "pageSize": 10,
               "specificationsIds": ["9303"], "brandIdList": ["1886"]},
    "2500hy": {"varietiesId": 317, "businessType": "3", "twoLevelBusinessType": "19",
               "timeType": 0, "pageNum": 1, "pageSize": 100, "specificationsIds": ["9540"]},
    "3248r":  {"varietiesId": 317, "businessType": "3", "twoLevelBusinessType": "19",
               "timeType": 0, "pageNum": 1, "pageSize": 10, "specificationsIds": ["11358"]},
}


def load_cookies():
    with open(COOKIES_FILE, encoding="utf-8") as fh:
        cks = json.load(fh)
    return "; ".join("%s=%s" % (c["name"], c["value"]) for c in cks)


def api_post(payload, cookie_str):
    req = urllib.request.Request(
        API,
        data=json.dumps(payload, ensure_ascii=False).encode(),
        headers={
            "Content-Type": "application/json",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Referer": "https://dc.oilchem.net/page/",
            "Cookie": cookie_str,
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=UPSTREAM_TIMEOUT) as resp:
        return json.loads(resp.read())


def _num(val):
    """把 '9500' / '9500~9600' 归一成 float；'请登录'/'询价中' 返回 None。"""
    if val is None:
        return None
    s = str(val).strip()
    if "~" in s:
        parts = s.split("~")
        try:
            return (float(parts[0]) + float(parts[1])) / 2
        except ValueError:
            return None
    if re.fullmatch(r"\d+(\.\d+)?", s):
        return float(s)
    return None


def extract_price(data, market, prefer="主流价"):
    """取指定市场的最新有效价格，返回 (price, date)。

    匹配 region 或 internalMarketName 任一含关键词——隆众的层级不统一：
    乙烯的 region 是「中国」而市场名才是「华东」；3248R 的 region 是「华南地区」
    而市场名是「厦门」。只查 region 会漏掉这两种。
    日期必须降序取最新——升序列表取 [-1] 会拿到最旧的日期（原脚本的 bug）。
    """
    pmap = (data.get("response") or {}).get("priceBodyMap") or {}
    for region, entries in pmap.items():
        for entry in entries:
            if market not in region and market not in str(entry.get("internalMarketName") or ""):
                continue
            dates = sorted([k for k in entry if str(k).startswith("202")], reverse=True)
            for d in dates:
                info = entry[d]
                if not isinstance(info, dict):
                    continue
                price = info.get("price") or {}
                # 先试首选口径，再退回 最低价/最高价 取均值
                val = _num(price.get(prefer))
                if val is None:
                    lo, hi = _num(price.get("最低价")), _num(price.get("最高价"))
                    if lo is not None and hi is not None:
                        val = (lo + hi) / 2
                if val is not None:
                    return val, d.replace("/", "-")
    return None, None


def _unit_costs(spec):
    """按牌号配方算单位变动成本（不含原料）。"""
    catalyst = spec["cat"] * 0.5 / VAT13
    cocatalyst = 150 * 85 / 1000 / VAT13
    silane = 56 / 1000 * spec["silane"] / VAT13
    hydrogen = spec["h2"] / 90 * 1.26
    white_oil = 11 / 1000 * 450 / VAT13
    steam = 169.65 * spec["steam"]
    electric = 0.62 / VAT13 * (spec["elec"] + 50)
    nitrogen = 0.81 * 10
    comp_air = 0.1 * 22
    water = 6.81 * 0.044
    circ_water = 0.15 * spec["circ"]
    wastewater = 0.1 * 11 / 1.06
    solid_waste = 18 * 0.2 / CAPACITY / 1.06
    to_furnace = 48 / CAPACITY
    package = 65
    additive = spec["add"]
    extra = spec.get("ipa", 0.0)
    return (catalyst + cocatalyst + silane + hydrogen + white_oil + steam + electric +
            nitrogen + comp_air + water + circ_water + wastewater + solid_waste +
            to_furnace + package + additive + extra)


SPECS = {
    "1102k":  {"cat": 30, "silane": 5.3, "h2": 39, "steam": 0.064, "elec": 345,
               "circ": 95, "add": 39, "acr": 1.002, "eth": 0.0, "ipa": 0.0},
    "2500hy": {"cat": 32, "silane": 15, "h2": 54, "steam": 0.079, "elec": 415,
               "circ": 115, "add": 117, "acr": 0.882, "eth": 0.12, "ipa": 0.0},
    "3248r":  {"cat": 30, "silane": 5.3, "h2": 325, "steam": 0.064, "elec": 345,
               "circ": 95, "add": 39, "acr": 0.972, "eth": 0.03, "ipa": 0.0},
}
# 2500HY 的 IPA 消耗单独加（原脚本的 ipa 项）
SPECS["2500hy"]["ipa"] = 11.6 / 1000 * (150 * 0.53) / VAT13


def calc(key, acr_tax, eth_tax, pp_tax):
    """返回 (净利润, 现金流)，元/吨。"""
    s = SPECS[key]
    variable = _unit_costs(s)
    variable += s["acr"] * acr_tax / VAT13          # 丙烯
    variable += s["eth"] * eth_tax / VAT13          # 乙烯
    fixed = LABOR + DEP_UNIT + REPAIR + OTHER_MFG
    period = ADMIN + (pp_tax * 0.01 / VAT13) + FINANCE
    np_ = (pp_tax / VAT13) - period - variable - fixed
    return np_, np_ + DEP_UNIT


def build():
    cookie_str = load_cookies()
    raw = {}
    for key, payload in QUERIES.items():
        raw[key] = api_post(payload, cookie_str)

    acr, acr_d = extract_price(raw["acr"], "华东")
    eth, eth_d = extract_price(raw["eth"], "华东")
    p1102, p1102_d = extract_price(raw["1102k"], "华东")
    p2500, p2500_d = extract_price(raw["2500hy"], "华东")
    p3248, p3248_d = extract_price(raw["3248r"], "厦门")

    missing = [n for n, v in [("丙烯", acr), ("乙烯", eth), ("1102K", p1102),
                              ("2500HY", p2500), ("3248R", p3248)] if v is None]
    if missing:
        # 抛异常 → adapter_common 保留上次成功 payload（widget 显示旧值+更新时间）
        raise RuntimeError("取价失败（cookie 可能已过期）: %s" % "、".join(missing))

    out = {
        "status": "ok",
        "data_date": acr_d or "",
        "updated": datetime.now().strftime("%m-%d %H:%M"),
        "acr": round(acr), "eth": round(eth),
        "pp_1102k": round(p1102), "pp_2500hy": round(p2500), "pp_3248r": round(p3248),
    }
    for key, name, pp in [("1102k", "1102k", p1102), ("2500hy", "2500hy", p2500),
                          ("3248r", "3248r", p3248)]:
        np_, cf = calc(key, acr, eth, pp)
        out["np_" + name] = round(np_)
        out["cf_" + name] = round(cf)
    return out


if __name__ == "__main__":
    ac.serve("pp-margin", PORT, build, TTL, paths={"/"})
