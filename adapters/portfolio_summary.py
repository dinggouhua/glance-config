#!/usr/bin/env python3
"""A-share portfolio summary adapter for Glance.
Reads the canonical PORTFOLIO.md and combines it with Tencent quotes.
"""
import json
import re
import urllib.request
from pathlib import Path

import adapter_common as ac

PORTFOLIO = Path('/etc/glance/portfolio.md')
PORT = 8907
CACHE_TTL = 55
UPSTREAM_TIMEOUT = 4
HEADERS = {'User-Agent': 'Mozilla/5.0', 'Referer': 'https://gu.qq.com/'}


def parse_portfolio():
    text = PORTFOLIO.read_text(encoding='utf-8')
    rows = []
    cash = 0.0
    in_main = False
    for line in text.splitlines():
        if line.startswith('| 股票名称 |'):
            in_main = True
            continue
        if not in_main:
            continue
        if line.startswith('| 合计 |'):
            cells = [x.strip() for x in line.strip().strip('|').split('|')]
            # columns: name, code, shares, market value, cost, logic, cash
            if len(cells) >= 7:
                m = re.search(r'[0-9]+(?:\.[0-9]+)?', cells[6].replace(',', ''))
                if m:
                    cash = float(m.group())
            break
        if not line.startswith('|') or ':---:' in line:
            continue
        cells = [x.strip() for x in line.strip().strip('|').split('|')]
        if len(cells) < 5:
            continue
        try:
            name, code = cells[0], cells[1]
            shares = int(float(cells[2]))
            cost = float(cells[4])
        except (ValueError, IndexError):
            continue
        rows.append({'name': name, 'code': code, 'shares': shares, 'cost': cost})
    if not rows:
        raise RuntimeError('no holdings found in PORTFOLIO.md')
    return rows, cash


def quote(codes):
    def prefixed(code):
        return ('sh' if code.startswith(('6', '5', '9')) else 'sz') + code
    symbols = [prefixed(c) for c in codes]
    url = 'https://qt.gtimg.cn/q=' + ','.join(symbols)
    req = urllib.request.Request(url, headers=HEADERS)
    raw = urllib.request.urlopen(req, timeout=UPSTREAM_TIMEOUT).read().decode('gbk', 'replace')
    result = {}
    for line in raw.split(';'):
        if '="' not in line:
            continue
        key, payload = line.split('="', 1)
        key = key.rsplit('_', 1)[-1]
        fields = payload.rstrip('"').split('~')
        if len(fields) <= 32:
            continue
        try:
            result[key[2:]] = {
                'price': float(fields[3] or 0),
                'previous': float(fields[4] or 0),
                'change': float(fields[31] or 0),
                'percent': float(fields[32] or 0),
                'timestamp': fields[30] if len(fields) > 30 else '',
            }
        except ValueError:
            continue
    return result


def _build():
    holdings, cash = parse_portfolio()
    quotes = quote([x['code'] for x in holdings])
    positions = []
    market_value = cost_value = today_profit = previous_value = 0.0
    latest = ''
    for h in holdings:
        q = quotes.get(h['code'])
        if not q:
            continue
        mv = q['price'] * h['shares']
        cv = h['cost'] * h['shares']
        day = q['change'] * h['shares']
        prev_mv = q['previous'] * h['shares']
        profit = mv - cv
        positions.append({**h, 'price': q['price'], 'market_value': mv,
                          'profit': profit, 'profit_pct': profit / cv * 100 if cv else 0,
                          'today_profit': day, 'today_pct': q['percent'],
                          'timestamp': q['timestamp']})
        market_value += mv
        cost_value += cv
        today_profit += day
        previous_value += prev_mv
        latest = max(latest, q['timestamp'])
    if not positions:
        raise RuntimeError('no live quotes returned')
    total_assets = market_value + cash
    total_profit = market_value - cost_value
    return {
        'status': 'ok', 'as_of': latest, 'positions': len(positions),
        'market_value': market_value, 'cost_value': cost_value, 'cash': cash,
        'total_assets': total_assets, 'profit': total_profit,
        'profit_pct': total_profit / cost_value * 100 if cost_value else 0,
        'today_profit': today_profit,
        'today_profit_pct': today_profit / previous_value * 100 if previous_value else 0,
        'holdings': positions,
    }


def build():
    """Same payload contract as before; only the transport changed."""
    data = _build()
    if data.get('status') != 'ok':
        raise RuntimeError(data.get('error') or 'upstream not ok')
    return data


if __name__ == '__main__':
    ac.serve('portfolio', PORT, build, CACHE_TTL, paths={'/', '/portfolio'})
