#!/usr/bin/env python3
"""Shared runtime for the local Glance adapters.

Why this exists
---------------
Glance aborts every widget request after a hard 5s
(internal/glance/widget-utils.go: `const defaultClientTimeout = 5 * time.Second`,
used by widget-custom-api.go). It is not configurable.

So if an adapter talks to its upstream *inside* the HTTP handler, any upstream
stall turns into a visible widget error, and the adapter log fills with
BrokenPipeError (the client gave up mid-write). That is exactly what used to
happen here.

Contract enforced by this module
--------------------------------
1. The request handler NEVER performs an upstream request. It only serialises a
   dict held in memory, so it answers in ~1ms regardless of upstream health.
2. A background thread refreshes the payload every `ttl` seconds.
3. If a refresh fails, the previous payload keeps being served (stale beats
   broken) and the failure is logged once per state change, not per cycle.
4. The last good payload is persisted to disk, so a service restart serves real
   data immediately instead of an empty/loading payload that Glance would then
   cache for the whole widget cache window.
5. Only the first ever start (no disk cache) blocks on a warm-up fetch, so the
   widget never receives a "loading" placeholder.
"""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse
import json
import sys
import threading
import time

HOST = "127.0.0.1"
CACHE_DIR = "/tmp"


def log(msg):
    print("[adapter] %s" % msg, file=sys.stderr, flush=True)


class Server(ThreadingHTTPServer):
    """Threaded server: one slow request can never block the others."""
    daemon_threads = True
    allow_reuse_address = True          # avoids Errno 98 on quick restarts
    request_queue_size = 32             # default is only 5

    def handle_error(self, request, client_address):
        exc = sys.exc_info()[1]
        if isinstance(exc, (BrokenPipeError, ConnectionResetError, TimeoutError)):
            return                      # Glance navigated away or gave up; not our fault
        super().handle_error(request, client_address)


class WarmCache:
    """Holds the payload, refreshes it in the background, serves it from memory."""

    def __init__(self, name, builder, ttl, label=None):
        self.name = name
        self.builder = builder
        self.ttl = max(int(ttl), 5)
        self.label = label or name
        self.file = "%s/glance-%s-adapter.json" % (CACHE_DIR, name)
        self._lock = threading.Lock()
        self._data = None
        self._at = 0.0
        self._healthy = None            # None = unknown, so the first result logs
        self._load()

    # ---------- persistence ----------
    def _load(self):
        try:
            with open(self.file, encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError):
            return
        if isinstance(data, dict) and data:
            with self._lock:
                self._data = data
                self._at = time.monotonic()
            log("%s: served last payload from %s" % (self.label, self.file))

    def _save(self, data):
        try:
            with open(self.file, "w", encoding="utf-8") as fh:
                json.dump(data, fh, ensure_ascii=False)
        except OSError:
            pass

    # ---------- refresh ----------
    def refresh(self):
        try:
            data = self.builder()
            if not isinstance(data, dict) or not data:
                raise TypeError("builder must return a non-empty dict, got %r" % (data,))
        except Exception as exc:
            first_failure = self._data is None
            with self._lock:
                self._at = time.monotonic()
                if first_failure:
                    # No previous payload: surface the reason instead of "loading".
                    self._data = {"status": "error",
                                  "message": "上游数据暂时不可用: %s" % str(exc)[:160]}
            if self._healthy is not False:
                log("%s: refresh FAILED (%s)%s" % (
                    self.label, exc,
                    "" if first_failure else " -- keeping previous payload"))
                self._healthy = False
            return False
        with self._lock:
            self._data = data
            self._at = time.monotonic()
        self._save(data)
        if self._healthy is not True:
            log("%s: refresh OK (ttl=%ss)" % (self.label, self.ttl))
            self._healthy = True
        return True

    # ---------- access ----------
    def get(self):
        with self._lock:
            return self._data

    def loop(self):
        while True:
            time.sleep(self.ttl)
            self.refresh()


def json_bytes(data):
    return json.dumps(data if data is not None else {"status": "loading"},
                      ensure_ascii=False).encode("utf-8")


def make_handler(cache, paths=None, routes=None):
    """Build a BaseHTTPRequestHandler serving `cache` from memory.

    paths  : optional set of allowed paths (None = allow everything)
    routes : optional {path_prefix: callable(handler, path) -> (status, body, ctype)}
             for non-JSON endpoints that legitimately need their own logic.
    """
    allowed = set(paths) if paths else None

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        server_version = "glance-adapter/1.0"

        def do_GET(self):
            path = urlparse(self.path).path
            if routes:
                for prefix, fn in routes.items():
                    if path.startswith(prefix):
                        try:
                            status, body, ctype = fn(self, path)
                        except Exception as exc:
                            status, body, ctype = 502, str(exc).encode(), "text/plain"
                        return self.send_payload(status, body, ctype)
            if allowed is not None and path not in allowed:
                return self.send_payload(404, b"not found", "text/plain")
            return self.send_payload(200, json_bytes(cache.get()),
                                     "application/json; charset=utf-8")

        def send_payload(self, status, body, ctype):
            try:
                self.send_response(status)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass                    # never log-spam on client abort

        def log_message(self, *_):
            pass

    return Handler


def serve(name, port, builder, ttl, paths=None, routes=None):
    cache = WarmCache(name, builder, ttl)
    if cache.get() is None:
        log("%s: no cached payload, warming up before serving..." % name)
        cache.refresh()                 # only blocks on the very first start
    threading.Thread(target=cache.loop, daemon=True).start()
    log("%s: listening on %s:%d (ttl=%ss)" % (name, HOST, port, ttl))
    server = Server((HOST, port), make_handler(cache, paths, routes))
    server.cache = cache                # routes reach it via handler.server.cache
    server.serve_forever()