# AlcoDetect — smoke test

> Written for **Gg**. Every hardware step is for Gg to run by hand; I have no board,
> no speaker and no LEDs. Anything I could not run is marked **UNVERIFIED** and is
> written as an expectation, never as a result.
>
> Order matters. Do not skip step 1.

---

## Phase 2 — assessment (completed by me, read-only)

### 2A Training

| Claim | Verdict | Evidence |
|---|---|---|
| No indicator or audio code runs inside `/training/*` | **HOLDS** | `routes/training.py` contains zero references to `indicator`, `services.indicator` or `dfplayer` |
| No indicator knowledge in the decision layer | **HOLDS** | `models/decision.py` and `config/gate_config.py` are byte-for-byte unchanged from `HEAD` |
| Blast radius is only camera / predict / sensor | **HOLDS** | enforced by `tests/test_indicator_isolation.py::test_only_three_backend_files_know_about_indicators` |
| A training capture behaves identically with indicators on/off | **HOLDS** | `test_training_window_lands_identically_with_indicators_on_and_off` compares every sensor field and the parent binding |
| A training capture never dispatches an indicator | **HOLDS** | `test_training_capture_never_dispatches_an_indicator` |
| Training window to `/sensor` → 409 | **HOLDS** | `test_device_mode.py:183` |
| Deployment window to `/training/collect/sensor-data` → 409 | **HOLDS** | `test_device_mode.py:160` |
| One `training_data` row per capture, `bac=0.00` for non-alcohol | **HOLDS** | `test_collection.py`, `test_training_fixes.py` |
| Impaired mirroring at BAC ≥ 0.05 | **HOLDS** | `test_collection.py` |
| `subject_id` FK enforced | **HOLDS** | live DB FK `fk_training_data_subject` present; 0 orphans |
| `group_by` uses `subject_id` | **HOLDS** | `sensor_models.resolve_group_by()` prefers `subject_id` |

**No contamination path found.**

### 2B Database (live reads only, no writes)

| Check | Result |
|---|---|
| `training_data` / `sensor_readings` / `deployment_logs` / `subjects` | `0 / 0 / 0 / 1` — **identical to preflight** |
| Schema vs `database/models.py` | identical column sets (only `__tablename__` and the `subject` relationship differ, neither a column) |
| Orphan `training_data.subject_id` | 0 |
| Orphan `sensor_readings.subject_id` | 0 |
| Orphan `deployment_logs.reading_id` | 0 |
| `fusion_label` > 20 chars | 0 |
| `denial_reason` > 20 chars | 0 |
| `label` > 20 chars | 0 |
| `fusion_label` distribution | table empty, so no rows to report |
| New table / column / index added by this work | **none** — `models.py` unchanged, no migration file |
| Snapshot dirs `impaired` / `fatigue` | 0 files (expected: 0 readings) |

`fusion_label` still holds PASS/WARN/FAIL and is still written by
`predict.py:185` and `:331`; the *fusion model* is retired but the column survives as a
legacy name now duplicating `Reading.label`. Left alone — see §10 of the report.

### 2C Deployment timing budget

Measured from the trigger, using the firmware constants and `config/sensor_timing.py`.

| # | Stage | Starts at | Ends at | Source |
|---|---|---|---|---|
| 1 | person detected → `/sensor/trigger` | 0 | ~0 | `Deployment.jsx:111` |
| 2 | ESP32 pre-roll, relays on, no sampling | 0 | 3 000 ms | `.ino:174` |
| 3 | MQ3 window, 50 samples @ 100 ms | 3 000 | 8 000 ms | `.ino:179` |
| 4 | post-roll, relays still on | 8 000 | 10 000 ms | `.ino:175` |
| 5 | page waits `ready_after_ms` | 0 | 9 000 ms | `sensor_timing.py` |
| 6 | `/predict/full` inference + `decide()` | 9 000 | ~9 300 ms | `predict.py:258` |
| 7 | indicator verdict dispatched | 9 000 | ≤ 10 000 ms | `predict.py:284` |
| 8 | **analyze timeout** measured from window close | 8 000 | 28 000 ms | `IND_ANALYZE_TIMEOUT_MS` |
| 9 | verdict hold, LEDs on | verdict | +8 000 ms | `IND_RESULT_HOLD_MS` |

**Margins**

* Page waits 9 000 ms; window closes at 8 000 ms → **1 000 ms settle margin**.
* Staleness limit `STALE_SECONDS = 13.0 s` vs a `/predict/full` call at 9 000 ms →
  **4 000 ms margin**.
* Page can trigger again after 10 000 ms; analyze deadline is 28 000 ms → **the next
  run's trigger lands 18 s before the previous deadline expires**, and `onTrigger`
  cancels the hold, so a stale verdict cannot colour the new run.
* **Tightest link: the 4 000 ms staleness margin.** If the backend ever needs more than
  4 s for inference, `/predict/full` returns `RECAPTURE_NEEDED` and the page loops
  ("realign") instead of deciding. That is fail-closed and correct, but it would look
  like a flaky gate. Worth watching on a slow machine.

### 2C Failure-mode table

| Failure | LED | Audio | Page | Enforced at |
|---|---|---|---|---|
| ESP32 offline | unchanged (OFF or last verdict) | none | trigger returns `blocked: device_mode_unknown`, no reading row, page shows "Device busy"/denied | `routes/sensor.py` mode check |
| ESP32 busy mid-sequence | unchanged | none | page shows "Device busy", retry in ~10 s | `.ino:251` 409 → `sensor.py` busy branch |
| DFPlayer absent | verdict still shown | silent, retried every 30 s | unaffected | `serviceAudio()` + `setAudioReady(false)` |
| SD card missing | verdict still shown | silent | unaffected | `dfplayer_frame_*` never blocks |
| Backend slow | LED holds ANALYZING | "analyzing" already played | page waits its own 9 s | indicator timeout at 28 s |
| Model missing | **YELLOW** | WARN clip | page shows WARN naming missing inputs | `decide()` → `missing_*`; firmware timeout as backstop |
| Token missing on device | **OFF** | silent | bench shows 503 | `.ino` `handleIndicator()` 503 |
| Token missing on backend | OFF | silent | bench shows 503, no calls attempted | `services/indicator.py` `token_configured()` |
| Device left in training mode | **OFF** | silent | trigger returns `device_in_training_mode` | `.ino:261` + `indicator.setTrainingMode()` |
| Stale / replayed verdict | unchanged | unchanged | unaffected | `indicator.h` `_runIdMatches()` |
| Double trigger | second refused | unaffected | page shows busy | `.ino:251` |
| Power loss mid-sequence | OFF on reboot | silent | page times out and denies | `begin()` forces IDLE/OFF |
| LED board dead | unchanged | unaffected | unaffected | indicators never feed the decision |
| `INDICATORS_ENABLED=false` | OFF | silent | bench rows 503 | `config/indicators.py` |

**NOT ENFORCED — read this**

* **`/mode` and `/relay_test` are unauthenticated.** Anyone on the LAN can switch a gate
  device into training mode or fire the relays. This is the single biggest open risk and
  it is out of the scope I was given. `.ino:325` and `.ino:348`.
* **Wi-Fi credentials are inline in the tracked `.ino`** (lines 69-70) and are in git
  history. Not fixed here.
* **A person standing in front of the gate with no trigger** leaves a blue pulse until
  the 60 s safety net fires. Mitigated, not eliminated.
* **Power loss during post-roll** leaves `isBuffering` cleared on reboot by the stuck
  guard, but the partial window is lost silently (no pending row cleanup beyond the
  backend's own rollback).

---

## Phase 1 — automated results (run by me)

| Check | Before | After |
|---|---|---|
| pytest | 291 passed | **353 passed** |
| firmware host harness (relay) | 71 110 passed | 71 110 passed |
| firmware host harness (indicator) | — | **257 passed** |
| firmware compile | 1 078 576 B (82 %) | 1 092 199 B (83 %) |
| firmware RAM | 49 136 B (14 %) | 49 508 B (15 %) |
| `vite build` | exit 0 | exit 0 |
| eslint Training / Settings / Deployment / AlcoGateHeader | 7 / 2 / 3 / 1 | 7 / 2 / 3 / 1 (unchanged) |
| eslint IndicatorBench (new) | — | 0 |
| live DB counts | 0/0/0/1 | 0/0/0/1 (unchanged) |

---

# Manual steps for Gg

## 1. Protect first — do this BEFORE anything else

```bash
cd /home/whou/CAPSTONE
git status                      # read the list; there is uncommitted work
git add -A && git commit -m "Deployment indicators phase 1 + training/deployment audit"
git tag pre-indicators
mysqldump --defaults-file="$HOME/.my.cnf" Alcohol_System > ~/backup-before-smoke.sql
mysql  --defaults-file="$HOME/.my.cnf" -N -B -D Alcohol_System -e "
  SELECT 'training_data',COUNT(*) FROM training_data
  UNION ALL SELECT 'sensor_readings',COUNT(*) FROM sensor_readings
  UNION ALL SELECT 'deployment_logs',COUNT(*) FROM deployment_logs
  UNION ALL SELECT 'subjects',COUNT(*) FROM subjects;"
```

> I did **not** commit, as instructed. The working tree carries both this phase and the
> previous phase's uncommitted fixes. Commit before you risk anything.
>
> **Expected:** four numbers. Write them down — step 12 compares against them.
>
> **Pass:** the numbers are recorded.

## 2. Automated

```bash
cd /home/whou/CAPSTONE/Backend
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest tests/ -q
cd ../alcodetect_esp32
g++ -std=c++11 -Wall -o /tmp/rt tests/relay_sequence_test.cpp && /tmp/rt
g++ -std=c++11 -Wall -Wextra -o /tmp/it tests/indicator_test.cpp && /tmp/it
cd .. && arduino-cli compile --fqbn esp32:esp32:esp32 .
cd Frontend && npm run build
```

> **Expected:** `353 passed`; `71110 passed, 0 failed`; `257 passed, 0 failed`;
> compile ~82-83 %; build exit 0.
>
> **Pass:** all five match.

## 3. Backend read-only GETs

```bash
curl -s localhost:8000/sensor/timing
curl -s localhost:8000/sensor/status
curl -s localhost:8000/sensor/mode
```

> **Expected:** `/sensor/timing` reports `preroll_ms=3000, buffer_ms=5000,
> postroll_ms=2000, ready_after_ms=9000, busy_until_ms=10000` with `matches: true`
> (the firmware agrees). `/sensor/status` and `/sensor/mode` return `online:false`
> while the ESP32 is off — that is correct, not a fault.
>
> **Pass:** timing values match and `matches` is not `false`.

## 4. Training capture round trip (throwaway subject)

1. Enrol a throwaway subject through the normal UI (or `POST /people/upload`).
2. Training → Sensors: confirm the device is in **training** mode.
3. Capture one photo per class (sober / drowsy / yawning / sanitizer / perfume).
4. Capture one alcohol sample with an explicit BAC ≥ 0.05.
5. Training → capture data: send a training window.

```bash
mysql --defaults-file="$HOME/.my.cnf" -N -B -D Alcohol_System -e "
  SELECT COUNT(*) FROM training_data;
  SELECT id, label, bac, subject_id, trial_id FROM training_data ORDER BY id DESC LIMIT 5;
  SELECT COUNT(*) FROM sensor_readings;   -- MUST stay unchanged
  SELECT COUNT(*) FROM deployment_logs;   -- MUST stay unchanged"
```

> **Expected:** `training_data` grows by the number of captures. `sensor_readings` and
> `deployment_logs` **do not move**. The alcohol row has the BAC you entered; the other
> rows have `bac = 0.00`. Every new row has the throwaway `subject_id` and a
> non-null `trial_id`.
>
> **Pass:** training table grew, the other two did not.

## 5. Database integrity

```bash
mysql --defaults-file="$HOME/.my.cnf" -N -B -D Alcohol_System -e "
  SELECT 'orphan subj', COUNT(*) FROM training_data t LEFT JOIN subjects s
    ON s.id=t.subject_id WHERE t.subject_id IS NOT NULL AND s.id IS NULL
  UNION ALL SELECT 'null subj', COUNT(*) FROM training_data WHERE subject_id IS NULL
  UNION ALL SELECT 'long fusion_label', COUNT(*) FROM sensor_readings
    WHERE CHAR_LENGTH(fusion_label)>20
  UNION ALL SELECT 'long denial_reason', COUNT(*) FROM sensor_readings
    WHERE CHAR_LENGTH(denial_reason)>20;"
```

> **Expected:** every line `0`.
>
> **Pass:** all zeros.

## 6. Deployment with no models — must be WARN, never PASS

Leave `models/saved/` empty. On the device, stay in **deployment** mode.

1. Open Deployment, step into frame, let it trigger.
2. Watch the LEDs and listen.

> **Expected:**
> * LEDs: blue pulse (person) → blue fast pulse (sampling) → **blue steady** (analysing).
> * Then **YELLOW solid** and the WARN clip, if inference is slow, or a **WARN** verdict
>   from the backend naming the missing models.
> * **It must never be green.**
> * The page shows WARN (or "No decision" as WARN).
>
> **Pass:** WARN or yellow, and **no green at any point**.

## 7. Bench indicator test — MAINS OFF

> **Relay wiring not made. Breaker up. ESP32 on USB only.**
> Wiring details: `docs/INDICATORS_WIRING.md` §3, §4, §6.

1. Boot with **no** `secrets.h`. Expect `/status` → `indicator_enabled: false`,
   `audio_ready: false`, **no sound**, LEDs **dark**.
2. `cp alcodetect_esp32/secrets.h.example alcodetect_esp32/secrets.h`, put a token in
   (`openssl rand -hex 32`), uncomment nothing else. **Never echo it.**
3. `export ESP32_DEVICE_TOKEN='<the same value>'` for the backend. Restart the backend.
4. Settings → Sensors → Indicators. Press **each** button.

| Button | Expected LED | Expected clip |
|---|---|---|
| Person detected | blue, slow pulse | 001 |
| Sampling | blue, fast pulse | 003 |
| Analyzing | blue steady | 004 |
| Warn | **yellow** solid | 006 |
| Fail | **red** solid | 007 |
| Off | dark | none |

> **Pass:** every row matches. There is deliberately **no green button** — the firmware
> refuses a verdict without a matching run id.

5. **Wrong token.** Restart the backend with a wrong `ESP32_DEVICE_TOKEN`. Press a
   button → **401**, clear message, **no LED change**.
6. **Wrong run id.**

```bash
curl -s -X POST http://<ESP32-IP>/indicator \
  -H "X-Device-Token: $ESP32_DEVICE_TOKEN" -H 'Content-Type: application/json' \
  -d '{"state":"pass","run_id":999999}'
```

> **Expected:** `409`. **No green light.** This is the most important single test.
>
> **Pass:** 409 and the LEDs do not change.

7. **DFPlayer unplugged.** Pull the module, power-cycle the ESP32.
   Bench every button again.
   > **Expected:** device boots normally, `audio_ready: false`, every LED still works,
   > no sound, no crash.
   >
   > **Pass:** LEDs work, no sound, no reboot.

## 8. Timeout shows yellow

> Needs the real sequence, so only with the relay bench test (mains off, no fans).
> Trigger a run and then stop the backend so no verdict can arrive.

> **Expected:** 20 s after the sampling window closes, the LED goes **YELLOW** and the
> WARN clip plays. It must **never** go green and must not stay stuck on blue.
>
> **Pass:** yellow at ~28 s after the trigger.

## 9. Training mode shows no LED and no audio

1. `GET /mode?training=1`
2. Press every bench button.
3. Stand in frame on the Deployment page and trigger.
4. `GET /mode?training=0`

> **Expected:** LEDs stay **dark**, **no sound**, and a deployment trigger returns
> `409 device_in_training_mode`. The bench endpoint also refuses.
>
> **Pass:** no light, no sound, in training mode.

## 10. Only now — relay wiring

With the indicator tests all passing and the device off, re-make the relay wiring.
Bench `GET /relay_test?fan=1&blower=0&ms=2000` and the blower equivalent.
Then a full end-to-end deployment run.

## 11. Re-flash reminder

The board needs the manual sequence: hold **BOOT**, tap **EN**, release **EN**,
release **BOOT**, then upload.

```bash
cd /home/whou/CAPSTONE
arduino-cli upload -p /dev/ttyUSB0 --fqbn esp32:esp32:esp32 alcodetect_esp32
arduino-cli monitor -p /dev/ttyUSB0 --config baudrate=115200
```

Expected compile: `Sketch uses 1092199 bytes (83%)`.
If you see a different size you did not compile this source.

> ⚠️ The board will boot on its own AP (`192.168.69.x`). Rejoin that network before
> the backend can see it.

## 12. Cleanup and count comparison

1. Delete the throwaway subject and its rows through the UI (or by exact id).
2. Re-run the counts from step 1.

```bash
mysql --defaults-file="$HOME/.my.cnf" -N -B -D Alcohol_System -e "
  SELECT 'training_data',COUNT(*) FROM training_data
  UNION ALL SELECT 'sensor_readings',COUNT(*) FROM sensor_readings
  UNION ALL SELECT 'deployment_logs',COUNT(*) FROM deployment_logs
  UNION ALL SELECT 'subjects',COUNT(*) FROM subjects;"
```

> **Expected:** the numbers you wrote down in step 1, minus exactly the smoke rows you
> created, and nothing else. `sensor_readings` and `deployment_logs` should match step 1
> **exactly** unless you deliberately ran step 6.
>
> **Pass:** counts match. If they do not, find the stray rows before going live.