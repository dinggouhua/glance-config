#!/usr/bin/env python3
"""江苏省城市足球联赛（苏超）积分榜适配器，供 Glance custom-api 使用。

背景：聚合数据 fapig/football/rank 的苏超积分榜自 2026 赛季起一直返回空
（error_code=0 但 result.ranking=null），同接口的中超/英超等正常，因此改用
网易彩票联赛资料页（服务端渲染完整积分榜）作为数据源。

输出结构与聚合数据接口保持一致（result.title / result.duration /
result.ranking[]），Glance 模板无需改动。

上游请求全部在后台线程完成，HTTP handler 只序列化内存里的 payload
（原因见 /opt/adapter_common.py 顶部说明）。
"""
import html
import json
import re
import urllib.request

import adapter_common as ac

URL = "https://sports.163.com/caipiao/league/football/7306/standings"
PORT = 8909
CACHE_TTL = 1800
UPSTREAM_TIMEOUT = 6
UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

FIELDS = ("matches", "wins", "draw", "losses", "goals", "losing_goals",
          "goal_difference", "avg_goals", "avg_losing", "win_rate",
          "draw_rate", "loss_rate", "scores")


def fetch():
    req = urllib.request.Request(URL, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=UPSTREAM_TIMEOUT) as resp:
        return resp.read().decode("utf-8", "replace")


def cells_of(page):
    """把 HTML 压成单元格列表，去掉脚本/样式。"""
    text = re.sub(r"<script.*?</script>", "", page, flags=re.S)
    text = re.sub(r"<style.*?</style>", "", text, flags=re.S)
    text = re.sub(r"<[^>]+>", "|", text)
    text = html.unescape(text)
    return [c.strip() for c in re.sub(r"[|\s]+", "|", text).split("|") if c.strip()]


def build():
    page = fetch()
    cells = cells_of(page)

    season = "2026"
    m = re.search(r"\((20\d\d)\)", "".join(cells[:80]))
    if m:
        season = m.group(1)

    try:
        start = cells.index("排名/球队") + 1
    except ValueError:
        start = 0

    rows = []
    i = start
    while i < len(cells) and len(rows) < 20:
        if not cells[i].isdigit():
            i += 1
            continue
        team = cells[i + 1] if i + 1 < len(cells) else ""
        # 球队行：名次 + 「xx队」+ 13 个数据列（赛胜平负/进失/净胜/均得均失/胜平负率/积分）
        if not re.fullmatch(r"[\u4e00-\u9fa5]{2,6}队", team or ""):
            i += 1
            continue
        seg = cells[i + 2:i + 15]
        if len(seg) < 13:
            break
        rows.append({"rank_id": cells[i], "team": team,
                     **{k: ("0" if v == "-" else v) for k, v in zip(FIELDS, seg)}})
        i += 15

    if not rows:
        raise RuntimeError("网易积分榜页面解析不到球队行（页面结构可能已变化）")
    return {"result": {"title": "江苏省城市足球联赛",
                       "duration": season,
                       "ranking": rows}}


if __name__ == "__main__":
    ac.serve("jscl-rank", PORT, build, CACHE_TTL, paths={"/"})
