#!/usr/bin/env python3
"""AI relay (OpenAI-compatible) balance adapter for Glance.

Listens on 127.0.0.1:8906, reads the target relay URL + API key from the
environment (set via systemd EnvironmentFile, never hardcoded here), queries
the relay's usage endpoint, and returns a compact JSON the Glance template can
render. Mirrors the approach used by the hvoy.ai "api-key-balance" tool.
"""
import json
import os
import urllib.request
import urllib.error

import adapter_common as ac

RELAY_USER_URL = os.environ.get("RELAY_USER_URL", "").strip()
API_KEY = os.environ.get("RELAY_API_KEY", "").strip()
PORT = int(os.environ.get("RELAY_BALANCE_PORT", "8906"))
TTL = 300                      # matches the widget's 5m cache
UPSTREAM_TIMEOUT = 5


def resolved_base(url: str) -> str:
    """Normalize the relay base; keep whatever the user configured verbatim."""
    return url.rstrip("/")


def query_usage(base: str, key: str):
    """Try the standard usage endpoints; return (payload, used_endpoint)."""
    candidates = [
        base + "/v1/usage",
        base + "/api/usage/token/",
        base + "/usage",
    ]
    last_err = None
    for ep in candidates:
        req = urllib.request.Request(
            ep,
            headers={"Authorization": "Bearer " + key, "Accept": "application/json"},
            method="GET",
        )
        try:
            with urllib.request.urlopen(req, timeout=UPSTREAM_TIMEOUT) as r:
                data = json.loads(r.read().decode("utf-8", "replace"))
                if isinstance(data, dict) and ("balance" in data or "remaining" in data):
                    return data, ep, r.status
                last_err = "endpoint returned no balance field"
        except urllib.error.HTTPError as e:
            last_err = f"HTTP {e.code}"
        except (urllib.error.URLError, OSError) as e:
            last_err = str(e)
    raise RuntimeError(last_err or "all endpoints failed")


def parse_balance(payload: dict):
    """Extract remaining/total/used and status. Unit is taken verbatim."""
    rem = payload.get("remaining") if payload.get("remaining") is not None else payload.get("balance")
    unit = payload.get("unit") or "USD"
    usage = payload.get("usage") or {}
    used = None
    if isinstance(usage, dict):
        t = usage.get("total")
        if isinstance(t, dict):
            used = t.get("actual_cost")
            if used is None:
                used = t.get("cost")
    today = None
    if isinstance(usage, dict) and isinstance(usage.get("today"), dict):
        today = usage["today"].get("actual_cost")
        if today is None:
            today = usage["today"].get("cost")
    return {
        "remaining": rem,
        "unit": unit,
        "used": used,
        "today": today,
        "isValid": bool(payload.get("isValid", True)),
        "plan": payload.get("planName") or "",
        "unrestricted": payload.get("mode") == "unrestricted",
    }


def build_response(ok: bool, **fields):
    resp = {"status": "ok" if ok else "error"}
    resp.update(fields)
    return resp


def build():
    """Payload contract unchanged; upstream I/O now runs outside the handler."""
    if not RELAY_USER_URL or not API_KEY:
        # Permanent configuration state, not a transient failure: surface it.
        return build_response(False, error="relay URL or API key not configured")
    payload, ep, http_status = query_usage(RELAY_USER_URL, API_KEY)
    return build_response(True, data=parse_balance(payload), endpoint=ep,
                          source=RELAY_USER_URL)


if __name__ == "__main__":
    if not RELAY_USER_URL:
        ac.log("WARNING: RELAY_USER_URL not set; serving empty.")
    if not API_KEY:
        ac.log("WARNING: RELAY_API_KEY not set; serving empty.")
    ac.serve("relay-balance", PORT, build, TTL)