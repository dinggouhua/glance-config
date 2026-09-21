#!/usr/bin/env python3
"""英雄联盟世界赛（Worlds）赛程 / 比分适配器，供 Glance custom-api 使用。

数据源：Riot 官方电竞 persisted API（lolesports.com 前端同一套接口，需公开 header
x-api-key）+ feed.lolesports.com 逐秒实时数据。

上游请求全部在后台线程完成，HTTP handler 只序列化内存里的 payload
（原因见 /opt/adapter_common.py 顶部说明）。刷新间隔自适应：
比赛进行中 30s、临赛 60-300s、无比赛 1800s。
"""
import html
import json
import os
import re
import threading
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

import adapter_common as ac

HOST_API = "https://esports-api.lolesports.com/persisted/gw"
FEED_API = "https://feed.lolesports.com/livestats/v1"
WORLDS_LEAGUE_ID = "98767975604431411"
CST = timezone(timedelta(hours=8))       # 展示统一用北京时间
WEEKDAYS = "一二三四五六日"
PORT = 8910
TTL = 1800                               # 兜底间隔，实际由 payload["ttl"] 决定
UPSTREAM_TIMEOUT = 5
UA = "GlanceDashboard/1.0 (personal)"

FIELDS_RE = re.compile(r"^[\w\s\.\-']+$")


def api_key():
    return (os.environ.get("LOLESPORTS_API_KEY") or "").strip()


def fetch_json(url, key=None, timeout=UPSTREAM_TIMEOUT):
    headers = {"User-Agent": UA}
    if key:
        headers["x-api-key"] = key
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8", "replace"))


def gw(path, params):
    params = dict(params, hl="zh-CN")
    url = "%s/%s?%s" % (HOST_API, path, urllib.parse.urlencode(params))
    return fetch_json(url, key=api_key())


def parse_iso(iso_utc):
    try:
        return datetime.fromisoformat(str(iso_utc).replace("Z", "+00:00"))
    except ValueError:
        return None


def to_local(iso_utc):
    """2026-10-20T11:00:00Z -> ('10-20', '19:00', '周二')，统一按北京时间。"""
    dt = parse_iso(iso_utc)
    if dt is None:
        return str(iso_utc or "")[:10], "", ""
    local = dt.astimezone(CST)
    return local.strftime("%m-%d"), local.strftime("%H:%M"), "周" + WEEKDAYS[local.weekday()]


def clean(text):
    return re.sub(r"\s+", " ", html.unescape(str(text or ""))).strip()


def teams_of(event):
    teams = ((event.get("match") or {}).get("teams") or [])
    out = []
    for t in teams[:2]:
        out.append({
            "name": clean(t.get("name") or t.get("code") or "TBD"),
            "code": clean(t.get("code") or ""),
            "wins": (t.get("result") or {}).get("gameWins"),
        })
    while len(out) < 2:
        out.append({"name": "TBD", "code": "", "wins": None})
    return out


def best_of(event):
    strat = ((event.get("match") or {}).get("strategy") or {})
    return strat.get("count") or 1


def live_stats(game_id, teams):
    """从 live stats feed 取双方人头/经济（participantId 1-5 蓝方，6-10 红方）。"""
    data = fetch_json("%s/window/%s" % (FEED_API, game_id), timeout=4)
    frames = data.get("frames") or []
    if not frames:
        return None
    last = frames[-1]
    parts = last.get("participants") or []
    meta = (data.get("gameMetadata") or {})
    blue_meta = {p["participantId"]: p for p in
                 ((meta.get("blueTeamMetadata") or {}).get("participantMetadata") or [])}
    red_meta = {p["participantId"]: p for p in
                ((meta.get("redTeamMetadata") or {}).get("participantMetadata") or [])}
    for p in parts:
        pid = p.get("participantId")
        if pid in blue_meta:
            blue_meta[pid].update({k: p.get(k) for k in ("kills", "deaths", "totalGoldEarned", "creepScore")})
        elif pid in red_meta:
            red_meta[pid].update({k: p.get(k) for k in ("kills", "deaths", "totalGoldEarned", "creepScore")})

    def side(meta_map, team):
        players = list(meta_map.values())
        return {
            "name": team["name"],
            "code": team["code"],
            "kills": sum(int(p.get("kills") or 0) for p in players),
            "gold": sum(int(p.get("totalGoldEarned") or 0) for p in players),
            "champions": [clean(p.get("championId") or "") for p in players if p.get("championId")],
        }

    stamp = str(last.get("rfc460Timestamp") or "")
    frame_time = stamp[11:19] if len(stamp) >= 19 else ""
    return {"blue": side(blue_meta, teams[0]), "red": side(red_meta, teams[1]),
            "frame_time": frame_time, "patch": clean(meta.get("patchVersion") or "")}


def build():
    if not api_key():
        return {"status": "no_key", "message": "等待填写 LOLESPORTS_API_KEY"}

    # 1) 赛事区间 + 阶段骨架
    season, start_text, end_text, stages = "", "", "", []
    start_iso = ""
    tournaments = gw("getTournamentsForLeague", {"leagueId": WORLDS_LEAGUE_ID})
    items = (tournaments.get("data", {}).get("leagues") or [{}])[0].get("tournaments") or []
    current = None
    for t in items:
        if t.get("slug", "").startswith("worlds_"):
            current = t
            break
    tournament_id = (current or {}).get("id")
    if current:
        season = clean(current.get("slug", "")).replace("worlds_", "")
        start_iso = current.get("startDate") or ""
        start_text, _, _ = to_local(start_iso)
        end_text, _, _ = to_local(current.get("endDate") or "")
    if tournament_id:
        try:
            st = gw("getStandings", {"tournamentId": tournament_id})
            for stage in (st.get("data", {}).get("standings") or [{}])[0].get("stages", []):
                count = sum(len(sec.get("matches") or []) for sec in (stage.get("sections") or []))
                stages.append({"name": clean(stage.get("name")), "count": count})
        except Exception as exc:
            ac.log("worlds: standings failed: %s" % exc)

    # 2) 赛程（世界赛最新一页）
    events = []
    try:
        sched = gw("getSchedule", {"leagueId": WORLDS_LEAGUE_ID})
        events = (sched.get("data", {}).get("schedule") or {}).get("events") or []
    except Exception as exc:
        ac.log("worlds: schedule failed: %s" % exc)

    upcoming, recent = [], []
    now_dt = datetime.now(timezone.utc)
    for ev in events:
        state = ev.get("state")
        date_text, time_text, week = to_local(ev.get("startTime") or "")
        teams = teams_of(ev)
        stage = clean(ev.get("blockName") or "")
        if state == "completed":
            started = parse_iso(ev.get("startTime"))
            age_days = ((now_dt - started).total_seconds() / 86400.0) if started else 999.0
            if age_days <= 30:                       # 只展示一个月内的结果，避免显示去年决赛
                recent.append({"date_text": date_text, "stage": stage,
                               "team1": teams[0]["name"], "team2": teams[1]["name"],
                               "score": "%s : %s" % (teams[0]["wins"] if teams[0]["wins"] is not None else "-",
                                                     teams[1]["wins"] if teams[1]["wins"] is not None else "-")})
        elif state == "unstarted":
            upcoming.append({"date_text": date_text, "time_text": time_text, "week": week,
                             "stage": stage, "team1": teams[0]["name"], "team2": teams[1]["name"],
                             "best_of": best_of(ev), "start_ts": ev.get("startTime") or ""})
    recent = recent[-3:]
    upcoming.sort(key=lambda x: x["start_ts"])
    upcoming = upcoming[:5]

    # 3) 世界赛进行中的比赛（getLive）
    live = None
    try:
        live_data = gw("getLive", {})
        for ev in (live_data.get("data", {}).get("schedule") or {}).get("events") or []:
            if ((ev.get("league") or {}).get("slug") or "") != "worlds":
                continue
            if ev.get("state") not in ("inProgress",):
                continue
            match_id = (ev.get("match") or {}).get("id")
            teams = teams_of(ev)
            live = {"stage": clean(ev.get("blockName") or ""),
                    "team1": teams[0]["name"], "team2": teams[1]["name"],
                    "score1": teams[0]["wins"] if teams[0]["wins"] is not None else 0,
                    "score2": teams[1]["wins"] if teams[1]["wins"] is not None else 0,
                    "best_of": best_of(ev), "stats": None}
            try:
                det = gw("getEventDetails", {"id": match_id})
                games = (((det.get("data") or {}).get("event") or {}).get("match") or {}).get("games") or []
                running = [g for g in games if g.get("state") == "inProgress"] or games[-1:]
                if running:
                    live["stats"] = live_stats(running[0]["id"], teams)
                    live["game_number"] = running[0].get("number")
            except Exception as exc:
                ac.log("worlds: live stats failed: %s" % exc)
            break
    except Exception as exc:
        ac.log("worlds: getLive failed: %s" % exc)

    # 4) 状态与刷新间隔
    if live:
        phase, ttl = "live", 30
    elif upcoming:
        first_dt = parse_iso(upcoming[0].get("start_ts"))
        hours = ((first_dt - now_dt).total_seconds() / 3600.0) if first_dt else 999.0
        phase = "upcoming"
        ttl = 60 if hours <= 6 else (300 if hours <= 24 else 1800)
    else:
        phase, ttl = "idle", 1800

    payload = {
        "status": "ok",
        "season": season,
        "phase": phase,
        "ttl": ttl,
        "start_text": start_text,
        "end_text": end_text,
        "days_to_start": None,
        "live": live,
        "next": upcoming,
        "recent": recent,
        "stages": stages,
        "updated": time.strftime("%H:%M:%S", time.localtime()),
        # 拍平的实时字段：Glance 模板的 .JSON.Map 不接受路径参数，只能读标量
        "live_stage": "",
        "live_team1": "",
        "live_team2": "",
        "live_score1": 0,
        "live_score2": 0,
        "live_best_of": 0,
        "live_game_number": 0,
        "live_blue_kills": 0,
        "live_blue_gold_k": 0,
        "live_red_kills": 0,
        "live_red_gold_k": 0,
        "live_patch": "",
    }
    if live:
        payload.update({
            "live_stage": live.get("stage") or "",
            "live_team1": live.get("team1") or "",
            "live_team2": live.get("team2") or "",
            "live_score1": int(live.get("score1") or 0),
            "live_score2": int(live.get("score2") or 0),
            "live_best_of": int(live.get("best_of") or 0),
            "live_game_number": int(live.get("game_number") or 0),
        })
        stats = live.get("stats") or {}
        if stats:
            payload.update({
                "live_blue_kills": int((stats.get("blue") or {}).get("kills") or 0),
                "live_blue_gold_k": int(((stats.get("blue") or {}).get("gold") or 0) / 1000),
                "live_red_kills": int((stats.get("red") or {}).get("kills") or 0),
                "live_red_gold_k": int(((stats.get("red") or {}).get("gold") or 0) / 1000),
                "live_patch": stats.get("patch") or "",
            })
    if start_iso:
        start_dt = parse_iso(start_iso)
        if start_dt:
            delta = (start_dt.astimezone(CST).date() - datetime.now(CST).date()).days
            payload["days_to_start"] = max(0, delta)
    return payload


def main():
    cache = ac.WarmCache("lol-worlds", build, TTL)
    if cache.get() is None:
        ac.log("lol-worlds: no cached payload, warming up before serving...")
        cache.refresh()

    def loop():
        while True:
            data = cache.get() or {}
            time.sleep(max(30, int(data.get("ttl") or TTL)))
            cache.refresh()

    threading.Thread(target=loop, daemon=True).start()
    ac.log("lol-worlds: listening on %s:%d" % (ac.HOST, PORT))
    server = ac.Server((ac.HOST, PORT), ac.make_handler(cache, paths={"/"}))
    server.serve_forever()


if __name__ == "__main__":
    main()
