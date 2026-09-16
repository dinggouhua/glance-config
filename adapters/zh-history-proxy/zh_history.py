#!/usr/bin/env python3
"""Simplified zh.wikipedia 'onthisday' proxy.

Calls https://zh.wikipedia.org/api/rest_v1/feed/onthisday/events/<MM>/<DD>,
converts each event.text from Traditional to Simplified Chinese via opencc,
and serves the resulting JSON on at /zh-history.

Usage:
    GET /zh-history?month=09&day=10

If month/day are omitted, defaults to today's date (UTC).
"""
import json
import subprocess
import sys
import urllib.request
import urllib.error
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

ZH_WIKI_URL = "https://zh.wikipedia.org/api/rest_v1/feed/onthisday/events/{month}/{day}"
UA = "glance-history-proxy/1.0 (+https://github.com/glanceapp/glance)"


def convert_traditional_to_simplified(text: str) -> str:
    """Run opencc -c t2s on the given text and return the converted string."""
    if not text:
        return text
    try:
        out = subprocess.run(
            ["opencc", "-c", "t2s", "-i", "/dev/stdin"],
            input=text,
            capture_output=True,
            text=True,
            check=True,
            timeout=5,
        )
        return out.stdout
    except Exception as exc:  # noqa: BLE001
        return text


def fetch_events(month: str, day: str):
    url = ZH_WIKI_URL.format(month=month, day=day)
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=20) as resp:
        payload = json.load(resp)
    for ev in payload.get("events", []):
        if "text" in ev:
            ev["text"] = convert_traditional_to_simplified(ev["text"])
    return payload


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path != "/zh-history":
            self.send_error(404, "not found")
            return
        qs = parse_qs(parsed.query)
        month = qs.get("month", [None])[0]
        day = qs.get("day", [None])[0]
        if not month or not day:
            now = datetime.utcnow()
            month = f"{now.month:02d}"
            day = f"{now.day:02d}"
        try:
            data = fetch_events(month, day)
        except urllib.error.HTTPError as exc:
            self.send_error(exc.code, exc.reason)
            return
        except Exception as exc:  # noqa: BLE001
            self.send_error(502, str(exc))
            return
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):  # noqa: A002
        sys.stderr.write("[%s] %s\n" % (self.address_string(), format % args))


def main():
    server = ThreadingHTTPServer(("127.0.0.1", 8896), Handler)
    sys.stderr.write("zh-history proxy listening on 127.0.0.1:8896\n")
    server.serve_forever()


if __name__ == "__main__":
    main()