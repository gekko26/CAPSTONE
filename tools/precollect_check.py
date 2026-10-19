#!/usr/bin/env python3
"""Pre-collection readiness check.

Run this BEFORE a collection session and again after it. It only ever issues GET
requests, so it cannot mutate the dataset, retag a row or fire the relays.

    python3 tools/precollect_check.py

Configuration comes from the environment; no address is hardcoded and nothing about
the deployment is printed. Recognised names:

    VITE_API_URL   backend base URL
    ESP32_URL      device address, used only to confirm the configured device is the
                   one answering /sensor/status

Exit codes
    0   every check passed, or passed with warnings only
    1   at least one check FAILED
    2   the script could not run at all (no backend address configured)

Standard library only.
"""

import ipaddress
import json
import os
import socket
import sys
import urllib.error
import urllib.parse
import urllib.request

# ── tunables ─────────────────────────────────────────────────────��────────────

API_URL_ENV = "VITE_API_URL"
ESP32_URL_ENV = "ESP32_URL"

TIMEOUT_S = 5.0

WARN_RSSI_DBM = -70
FAIL_RSSI_DBM = -80

PASS, WARN, FAIL, INFO = "PASS", "WARN", "FAIL", "INFO"


class GetOnlyGuard(RuntimeError):
    """Raised when anything tries to issue a non-GET request."""


def _get(url, method="GET", params=None, timeout=TIMEOUT_S):
    """GET a URL. This is the ONLY place a request is issued.

    ``method`` exists so the read-only guarantee is enforced in code: any attempt
    to POST, PUT, DELETE or PATCH raises GetOnlyGuard before a socket is opened.
    """
    if method != "GET":
        raise GetOnlyGuard("refusing non-GET method: %r" % (method,))
    if url is None:
        raise ValueError("no url")
    scheme = urllib.parse.urlparse(url).scheme
    if scheme not in ("http", "https"):
        raise GetOnlyGuard("refusing non-HTTP scheme: %r" % (scheme,))

    if params:
        url = url + ("&" if "?" in url else "?") + urllib.parse.urlencode(params)

    request = urllib.request.Request(url, method="GET")

    class _NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            raise GetOnlyGuard("refusing redirect to another host")

    opener = urllib.request.build_opener(_NoRedirect)
    with opener.open(request, timeout=timeout) as response:
        body = response.read().decode("utf-8", "replace")
    return json.loads(body)


class Result:
    def __init__(self):
        self.rows = []

    def add(self, level, check, reason):
        self.rows.append((level, check, reason))

    def counts(self):
        return {
            PASS: sum(1 for r in self.rows if r[0] == PASS),
            WARN: sum(1 for r in self.rows if r[0] == WARN),
            FAIL: sum(1 for r in self.rows if r[0] == FAIL),
            INFO: sum(1 for r in self.rows if r[0] == INFO),
        }

    @property
    def failed(self):
        return any(r[0] == FAIL for r in self.rows)

    def render(self):
        width = max((len(r[1]) for r in self.rows), default=10)
        for level, check, reason in self.rows:
            print(f"  [{level:4s}] {check.ljust(width)}  {reason}")
        c = self.counts()
        print(f"  -- {c[PASS]} pass, {c[WARN]} warn, {c[FAIL]} fail, {c[INFO]} info")


def _describe_error(exc):
    """Never a traceback. Never leaks the address."""
    if isinstance(exc, urllib.error.HTTPError):
        return f"HTTP {exc.code}"
    if isinstance(exc, urllib.error.URLError):
        reason = getattr(exc, "reason", None)
        if isinstance(reason, (socket.timeout, TimeoutError)):
            return "timed out"
        return "connection failed"
    if isinstance(exc, json.JSONDecodeError):
        return "response was not valid JSON"
    if isinstance(exc, GetOnlyGuard):
        return "blocked a non-GET request"
    if isinstance(exc, ValueError):
        return str(exc)
    return f"{type(exc).__name__}"


def run_checks(get=None, api_url=None, esp32_url=None):
    """Perform every check. `get` is injectable so the tests never touch a network."""
    get = get or _get
    api_url = api_url or os.environ.get(API_URL_ENV)
    esp32_url = esp32_url if esp32_url is not None else os.environ.get(ESP32_URL_ENV)
    result = Result()

    # ── 1. backend reachable ──────────────────────────────────────────────────
    try:
        get(api_url + "/")
        result.add(PASS, "backend reachable", "responded")
    except Exception as exc:
        result.add(FAIL, "backend reachable", _describe_error(exc))
        return result, api_url  # nothing else can be checked

    # ── 2. device is in training mode ─────────────────────────────────────────
    device_mode = None
    try:
        status = get(api_url + "/sensor/status")
        # A proxy or an error page can answer 200 with a non-object body. Treat that
        # as a failed check rather than letting AttributeError escape as a traceback.
        if not isinstance(status, dict):
            raise ValueError("response was not a JSON object")
    except Exception as exc:
        status = None
        result.add(FAIL, "device status", _describe_error(exc))
    else:
        result.add(PASS, "device status", "reported")
        device_mode = status.get("mode")
        if device_mode == "training":
            result.add(PASS, "device mode", "training")
        elif device_mode == "deployment":
            result.add(FAIL, "device mode",
                       "device is in deployment mode — switch it in Settings first")
        else:
            result.add(WARN, "device mode", f"mode not reported (got {device_mode!r})")

    # ── 3. the device answering is the configured one ─────────────────────────
    sequence = (status or {}).get("sequence_state")
    if status is not None and esp32_url:
        # Both addresses are compared as hosts only; neither is ever printed.
        configured = urllib.parse.urlparse(esp32_url).hostname
        serving = urllib.parse.urlparse(api_url).hostname
        if not configured:
            result.add(WARN, "esp32 address", "$%s has no hostname" % ESP32_URL_ENV)
        elif _same_host(configured, serving):
            result.add(PASS, "esp32 address",
                       "configured device is the one answering")
        else:
            result.add(WARN, "esp32 address",
                       "$%s is not the host serving /sensor/status — check the "
                       "device is the one you intend to collect from" % ESP32_URL_ENV)
    elif status is not None:
        result.add(INFO, "esp32 address", "not set in $%s — skipped" % ESP32_URL_ENV)

    if (status or {}).get("online") is False:
        result.add(FAIL, "esp32 reachable", "device offline")
    else:
        result.add(PASS, "esp32 reachable", "online")
    if sequence == "idle":
        result.add(PASS, "relay sequence", "idle")
    elif sequence is None:
        result.add(INFO, "relay sequence", "not reported")
    else:
        result.add(FAIL, "relay sequence",
                   f"busy ({sequence}) — wait for the current run to finish")

    # ── 4. all three MQ3 channels live ────────────────────────────────────────
    live = (status or {}).get("mq3_live")
    if live is None:
        result.add(INFO, "mq3 channels",
                   "device does not report mq3_live — flash the newer firmware")
    elif live >= 3:
        result.add(PASS, "mq3 channels", f"{live}/3 live")
    elif live > 0:
        result.add(FAIL, "mq3 channels",
                   f"only {live}/3 live — a sensor is not mounted or not wired")
    else:
        result.add(FAIL, "mq3 channels", "0/3 live — no sensor is delivering signal")

    # ── 5. DHT ────────────────────────────────────────────────────────────────
    temperature = (status or {}).get("temperature")
    if temperature is None:
        result.add(FAIL, "dht11", "not reporting (firmware sends null on a failed read)")
    else:
        result.add(PASS, "dht11", "reporting")

    # ── 6. Wi-Fi signal, when the device exposes it ───────────────────────────
    rssi = (status or {}).get("wifi_rssi")
    if rssi is None:
        result.add(INFO, "wifi signal", "not exposed by the firmware — skipped")
    else:
        try:
            rssi = float(rssi)
        except (TypeError, ValueError):
            result.add(INFO, "wifi signal", "not a number — skipped")
        else:
            if rssi < FAIL_RSSI_DBM:
                result.add(FAIL, "wifi signal", f"{rssi:.0f} dBm — link is unusable")
            elif rssi < WARN_RSSI_DBM:
                result.add(WARN, "wifi signal", f"{rssi:.0f} dBm — weak, uploads may drop")
            else:
                result.add(PASS, "wifi signal", f"{rssi:.0f} dBm")

    # ── 7. enrolled subjects ──────────────────────────────────────────────────
    min_subjects, target_subjects = 3, 5
    enrolled = None
    try:
        progress = get(api_url + "/training/collection-progress")
        if not isinstance(progress, dict):
            raise ValueError("response was not a JSON object")
    except Exception as exc:
        progress = None
        result.add(FAIL, "collection progress", _describe_error(exc))
    if progress:
        result.add(PASS, "collection progress", "reported")
        enrolled = (progress.get("subjects") or {}).get("total_with_data")
        min_subjects = (progress.get("targets") or {}).get("min_subjects", min_subjects)
        target_subjects = (progress.get("targets") or {}).get("target_subjects",
                                                               target_subjects)
        total_subjects = len(get_subjects_safe(get, api_url))
        if total_subjects >= target_subjects:
            result.add(PASS, "enrolled subjects", f"{total_subjects} enrolled")
        elif total_subjects >= min_subjects:
            result.add(WARN, "enrolled subjects",
                       f"{total_subjects} enrolled, {target_subjects} is the target")
        else:
            result.add(FAIL, "enrolled subjects",
                       f"only {total_subjects} enrolled, {min_subjects} is the minimum")

    # ── 8/9. integrity ────────────────────────────────────────────────────────
    integrity = (progress or {}).get("integrity") or {}
    if integrity:
        zero = integrity.get("zero_sensor_rows", 0)
        fallback = integrity.get("fallback_dht_rows", 0)
        partial = integrity.get("partial_sensor_rows", 0)
        missing = integrity.get("rows_without_sensor_data", 0)

        if zero:
            result.add(FAIL, "zero-sensor rows", f"{zero} row(s) with no MQ3 signal")
        else:
            result.add(PASS, "zero-sensor rows", "0")
        if fallback:
            result.add(FAIL, "dht fallback rows",
                       f"{fallback} row(s) carry a fabricated reading")
        else:
            result.add(PASS, "dht fallback rows", "0")
        if partial:
            result.add(WARN, "partial-sensor rows", f"{partial} row(s) with some channels at zero")
        else:
            result.add(PASS, "partial-sensor rows", "0")
        if missing:
            result.add(WARN, "rows without sensor data",
                       f"{missing} row(s) never received a window")
        else:
            result.add(PASS, "rows without sensor data", "0")
    else:
        result.add(WARN, "integrity signals", "not reported")

    # baseline lives at the top level of collection-progress, alongside the other
    # dataset signals -- it is read from here only, so it is reported exactly once.
    baseline = (progress or {}).get("baseline") or {}
    bstatus = baseline.get("status")
    if bstatus == "unstable":
        result.add(WARN, "baseline stability",
                   baseline.get("message", "unstable"))
    elif bstatus == "insufficient_data":
        result.add(INFO, "baseline stability",
                   baseline.get("message", "not enough clear-air rows yet"))
    elif bstatus == "stable":
        result.add(PASS, "baseline stability", "settled")
    else:
        result.add(INFO, "baseline stability", "not reported")

    # ── untagged rows ─────────────────────────────────────────────────────────
    try:
        coverage = get(api_url + "/training/subject-coverage")
        if not isinstance(coverage, dict):
            raise ValueError("response was not a JSON object")
    except Exception as exc:
        result.add(WARN, "tagged rows", _describe_error(exc))
    else:
        untagged = len(coverage.get("untagged_row_ids") or [])
        if untagged:
            result.add(FAIL, "tagged rows",
                       f"{untagged} untagged row(s) — group_by falls back to trial_id")
        else:
            result.add(PASS, "tagged rows", "every row is tagged")

    # ── 11. session plan (ids only, never names) ──────────────────────────────
    try:
        plan = get(api_url + "/training/session-plan")
        if not isinstance(plan, dict):
            raise ValueError("response was not a JSON object")
    except Exception as exc:
        result.add(WARN, "session plan", _describe_error(exc))
    else:
        people = plan.get("people") or []
        if not people:
            result.add(FAIL, "session plan", "no enrolled subjects to plan for")
        else:
            for person in people:
                sid = person.get("subject_id")
                remaining = person.get("remaining") or {}
                nxt = person.get("next_suggested")
                pending = sum(1 for v in remaining.values() if v > 0)
                detail = f"{pending} event type(s) left"
                if nxt:
                    detail += f"; next: {nxt}"
                result.add(INFO, f"subject #{sid}", detail)

    return result, api_url


def _same_host(a, b):
    """True when two hosts are the same machine, by name or by literal address."""
    try:
        if ipaddress.ip_address(a) == ipaddress.ip_address(b):
            return True
    except ValueError:
        pass
    return (a or "").lower() == (b or "").lower()


def get_subjects_safe(get, api_url):
    try:
        subjects = get(api_url + "/recognition/subjects")
        return subjects if isinstance(subjects, list) else []
    except Exception:
        return []


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    api_url = os.environ.get(API_URL_ENV)

    print("AlcoDetect pre-collection check")
    print(f"  backend address read from ${API_URL_ENV} (value not printed)")
    print(f"  device address read from ${ESP32_URL_ENV} (value not printed)")
    print()

    if not api_url:
        print(f"  [FAIL] configuration      ${API_URL_ENV} is not set — "
              f"export the backend base URL and try again")
        return 2

    try:
        result, api_url = run_checks(api_url=api_url)
    except GetOnlyGuard as exc:
        print(f"  [FAIL] request guard      {exc}")
        return 1
    except Exception as exc:
        print(f"  [FAIL] check script       {_describe_error(exc)}")
        return 1

    result.render()
    print()
    if result.failed:
        print("RESULT: NOT READY to collect — fix the FAIL items above.")
        return 1
    if result.counts()[WARN]:
        print("RESULT: ready with warnings — read the WARN items before starting.")
        return 0
    print("RESULT: all checks passed — ready to collect.")
    return 0


if __name__ == "__main__":
    sys.exit(main())