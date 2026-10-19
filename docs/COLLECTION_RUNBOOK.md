# Collection Runbook — first real session

This runbook covers the first session on the real rig.
It is a **documented procedure, not a verified result**: nothing in it was executed
against the running backend, the device or the live database during development.
Every step marked **UNVERIFIED** must be confirmed by you during the first run.

No address, name or credential appears in this document. Configure via the
environment variable names listed in step 1.

---

## 0. Before you start

| Precondition | How to confirm |
|---|---|
| Schema is already converged | `docs/MIGRATION_PREVIEW.md` — planner returns **0** DDL statements |
| No dummy rows remain | preflight check reports `0` zero-sensor / fallback rows |
| Device is flashed and running | preflight check reports `device mode: training` |
| All three MQ3 channels live | preflight check reports `mq3 channels 3/3 live` |

**Migration note.** `Backend/main.py` runs DDL *as an import side effect*
(`create_all` at line 9, `_auto_migrate()` at line 94). Restarting the backend
therefore re-runs the planner. On the current schema it is a no-op, but be aware
that any schema change ships as "restart the backend", not as a separate step.

---

## 1. Configure the environment

The preflight script reads two names and invents neither:

| Variable | Meaning |
|---|---|
| `VITE_API_URL` | backend base URL |
| `ESP32_URL` | device address; used only to confirm the configured device is the one answering `/sensor/status` |

```bash
export VITE_API_URL="<backend base url>"
export ESP32_URL="<device address>"
```

There is **no built-in default**. If `VITE_API_URL` is unset the script exits `2`
rather than guessing an address — a fallback would silently check the wrong host.

---

## 2. Run the preflight check

```bash
python3 tools/precollect_check.py
```

Read-only by construction: every request is issued through one wrapper that
raises `GetOnlyGuard` on any non-`GET` method, so the script cannot mutate the
dataset, retag a row, or fire the relays. The wrapper refuses non-HTTP schemes and
refuses redirects, so the script cannot be bounced to another host.

Exit codes:

| Code | Meaning |
|---|---|
| `0` | all checks passed, or passed with warnings only |
| `1` | at least one `FAIL` — do not collect yet |
| `2` | `VITE_API_URL` is not set |

### What each result means

| Check | FAIL when | WARN when |
|---|---|---|
| `backend reachable` | unreachable / timeout / bad JSON | — |
| `device status` | unreachable, or non-object body | — |
| `device mode` | device is in **deployment** mode | mode not reported |
| `esp32 address` | — | `$ESP32_URL` is not the host serving `/sensor/status` |
| `esp32 reachable` | device reports `online: false` | — |
| `relay sequence` | device is **busy** (`sampling`, …) | — |
| `mq3 channels` | fewer than 3 channels live | — |
| `dht11` | not reporting | — |
| `wifi signal` | RSSI below −80 dBm | RSSI below −70 dBm |
| `enrolled subjects` | fewer than the minimum (3) | fewer than the target (5) |
| `zero-sensor rows` | any row with no MQ3 signal | — |
| `dht fallback rows` | any row carrying a fabricated reading | — |
| `partial-sensor rows` | — | rows with some channels at zero |
| `rows without sensor data` | — | rows that never received a window |
| `baseline stability` | — | recent clear-air readings still moving |
| `tagged rows` | untagged rows exist | — |

Notes:

- **`wifi signal` is skipped (INFO) on current firmware** — it does not expose
  RSSI. Do not read that INFO as a problem.
- **`baseline stability` is advisory.** An unstable baseline warns; it never
  blocks. The message tells you how long to wait.
- **`relay sequence` busy means wait**, do not retry in a loop — a capture is in
  progress and starting another will interleave the sensor window.

### UNVERIFIED
- Actual check ordering and severities against the live backend.
- The ESP32 host-mismatch warning with your real network topology.

---

## 3. Read the session plan

Open **Training** in the UI. The **Session plan** panel shows, per enrolled person:

- `count / quota` for each event type
- the number of event types still outstanding
- **what to capture next** — the event with the largest remaining share; ties go to
  the earlier event in the canonical order, so **clear air is always suggested
  first**.

Per-person quotas:

| Event type | Per person |
|---|---|
| clear air | 10 |
| sober | 10 |
| fatigue (drowsy / yawning) | 8 |
| alcohol | 10 |
| sanitizer | 4 |
| perfume | 4 |

Overall dataset headroom, alcohol BAC-band counts and completion fractions are
derived from the same readiness constants used by the collection checklist.

API equivalent: `GET /training/session-plan` (read-only: two SELECTs, no file
access; returns subject **id and name only**).

---

## 4. Capture

1. Device must be in **training** mode. The preflight check fails otherwise —
   switch it in Settings, then re-run the check.
2. Relay sequence must be **idle** before you start.
3. Capture **clear air first**. Sensors need a settled baseline; a clean baseline
   is what makes the later alcohol captures trustworthy.
4. After each capture the plan refreshes, so the "next" suggestion always reflects
   what has actually been stored.
5. Keep alcohol captures short — breath alcohol decays. Record BAC at capture time.

After the session, run the preflight check again. Compare counts against
`/training/collection-progress` to confirm nothing was dropped.

### UNVERIFIED
- End-to-end timing on real hardware.
- Whether one clear-air pass is enough for your room, or two are needed.

---

## 5. After the session

- Confirm the plan's completion fractions moved.
- Confirm no `FAIL` appears in the post-session check that was not there before.
- Investigate any `WARN` on `partial-sensor rows` or `rows without sensor data`
  before the next session — both indicate a capture window that did not complete.
- Do **not** edit, retag or delete rows to "clean up" the dataset. Labels and
  photos are never modified by the tooling.

---

## 6. Known limitations

| Limitation | Consequence |
|---|---|
| No Wi-Fi RSSI in firmware | `wifi signal` check is always INFO |
| Baseline advisory only | an unstable baseline warns but does not stop a session |
| Tagging is manual | untagged rows silently fall back to `trial_id` grouping; the check FAILs until fixed |
| Import-time DDL | any schema change takes effect by restarting the backend |