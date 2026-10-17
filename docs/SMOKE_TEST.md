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
| 2 | ESP32 pre-roll, relays on, no sampling | 0 | 3 000 ms | `.ino:881` (`mayBeginSampling()` false branch) |
| 3 | MQ3 window, 50 samples @ 100 ms | 3 000 | 8 000 ms | `.ino:886-907` (`elapsed < BUFFER_DURATION_MS`) |
| 4 | post-roll, relays still on | 8 000 | 10 000 ms | `.ino:908-923` (`finishSampling()`) |
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
| ESP32 busy mid-sequence | unchanged | none | page shows "Device busy", retry in ~10 s | `.ino:445-449` 409 → `sensor.py` busy branch |
| DFPlayer absent | verdict still shown | silent, retried every 30 s | unaffected | `serviceAudio()` + `setAudioReady(false)` |
| SD card missing | verdict still shown | silent | unaffected | `dfplayer_frame_*` never blocks |
| Backend slow | LED holds ANALYZING | "analyzing" already played | page waits its own 9 s | indicator timeout at 28 s |
| Model missing | **YELLOW** | WARN clip | page shows WARN naming missing inputs | `decide()` → `missing_*`; firmware timeout as backstop |
| Token missing on device | **OFF** | silent | bench shows 503 | `.ino` `handleIndicator()` 503 |
| Token missing on backend | OFF | silent | bench shows 503, no calls attempted | `services/indicator.py` `token_configured()` |
| Device left in training mode | **OFF** | silent | trigger returns `device_in_training_mode` | `.ino:453-459` + `indicator.setTrainingMode()` |
| Stale / replayed verdict | unchanged | unchanged | unaffected | `indicator.h` `_runIdMatches()` |
| Double trigger | second refused | unaffected | page shows busy | `.ino:445-449` |
| Power loss mid-sequence | OFF on reboot | silent | page times out and denies | `begin()` forces IDLE/OFF |
| LED board dead | unchanged | unaffected | unaffected | indicators never feed the decision |
| `INDICATORS_ENABLED=false` | OFF | silent | bench rows 503 | `config/indicators.py` |

**NOT ENFORCED — read this**

* **`/mode` and `/relay_test` are unauthenticated.** Anyone on the LAN can switch a gate
  device into training mode or fire the relays. This is the single biggest open risk and
  it is out of the scope I was given. `.ino:814-815` route registration.
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
---

# PHASE A additions — upload photos

Everything below is **UNVERIFIED by me**: there is no camera, no subject and no
filesystem I am allowed to write to. Each step is written as an expectation.

An upload writes **one JPEG per photo** to
`Backend/data/faces/<sober|fatigue|impaired>/` named
`<class>_up_s<subject_id>_<12 hex>.jpg`. It creates **no** `training_data` row and
writes nothing to `data/snapshots`.

## U1. Wiring-free sanity: the namespace is disjoint

```bash
cd /home/whou/CAPSTONE/Backend
.venv/bin/python -c "
import sys; sys.path.insert(0,'.')
from models.train.face_uploads import *
cap=['sober_1.jpg','yawning_13.jpg','impaired_perfume_5.jpg']
up=[upload_filename(c,s,'abcdef123456') for c in UPLOAD_CLASSES for s in (0,7)]
print('capture->upload:', [n for n in cap if is_upload_name(n)])
print('upload->capture:', [n for n in up if is_capture_name(n)])"
```

> **Expected:** `capture->upload: []` and `upload->capture: []`.
>
> **Pass:** both empty. If not, STOP — delete_row could delete an upload.

## U2. Upload several photos at once

Training → *Collection checklist* → *Upload photos*.

1. Class **Sober**, pick the enrolled person, select **5** photos at once, submit.
2. Watch the per-file result table.

> **Expected:** `5 accepted`. Both LEDs/camera are irrelevant here. No row appears in
> `training_data`. The checklist's face count for *sober* rises by 5.
>
> **Pass:** 5 accepted, 0 rejected, no DB row.

## U3. Wrong-class guard

Set Class to something not in the list (it cannot be selected — verify), then send a
request directly:

```bash
curl -s -X POST localhost:8000/training/upload-faces \
  -F face_class=drunk -F subject_id=1 -F files=@some.jpg
```

> **Expected:** HTTP 400 `face_class must be one of sober, fatigue, impaired`.
> Nothing saved.
>
> **Pass:** 400 and no new file.

## U4. Impaired BAC gate

With Class **Impaired** selected, try in turn:

| Attempt | Expected |
|---|---|
| no BAC | 400, nothing saved |
| BAC = 0.049 | 400, nothing saved |
| BAC = 0.05, checkbox unticked | 400, nothing saved |
| BAC = 0.05, checkbox ticked | **accepted** |
| BAC = 0.2, checkbox ticked | **accepted** |

> The exact message is
> `impaired uploads need a measured BAC of at least 0.05 and confirmation that the person was really impaired`.
>
> **Pass:** the first three are refused and the last two accepted.

## U5. Duplicate detection

Re-upload one of the photos from U2.

> **Expected:** `1 duplicate`, `0 accepted`. The file's modification time does **not**
> change. The count of files on disk does not increase.
>
> **Pass:** duplicate, and no overwrite.

## U6. Delete an upload

Expand *uploaded photos*, press **delete** on one row, then **confirm delete**.

> **Expected:** the row disappears. Nothing else changes.
>
> Now try to delete something you must not. Each of these must be **refused (400)** and
> the file must survive:
>
> | Attempt | Why it must fail |
> |---|---|
> | `GET /training/uploaded-faces` for a capture file | not an upload name |
> | `DELETE .../sober/sober_42.jpg` | capture-owned |
> | `DELETE .../sober/../fatigue/<upload>.jpg` | traversal |
> | `DELETE .../fatigue/<an upload of class sober>` | class mismatch |
> | `DELETE .../registered/registered_1.jpg` | registered is never deletable |
>
> **Pass:** the upload deletes; all five refusals hold and no file outside the upload
> namespace disappears.

## U7. Counts update

Check the checklist after U2–U6:

```bash
curl -s localhost:8000/training/collection-progress | python3 -m json.tool | \
  grep -A4 '"sober"'
curl -s localhost:8000/training/uploaded-faces | python3 -m json.tool | head -20
```

> **Expected:** `faces.sober` has `captured`, `uploaded` and `images` where
> `images == captured + uploaded`. `integrity.orphan_files` is **0** — an upload is
> never an orphan. `rows_missing_image` unaffected.
>
> **Pass:** the arithmetic holds and no upload is reported as an orphan.

## U8. The subject split engages after 3 subjects

Upload at least 2 photos for each of **3 different** enrolled people.

> **Expected:** `subjects.per_class.sober` reaches 3, and a subsequent MobileNet train
> prints `Subject split: 3 subjects → … no subject overlap` with **no** fallback
> warning. With only 2 people it still warns and falls back.
>
> **Pass:** the split engages at 3 and only falls back below 3.

## U9. Nothing leaked into the wrong place

```bash
ls Backend/data/snapshots/*/ | head
mysql --defaults-file="$HOME/.my.cnf" -N -B -D Alcohol_System -e \
  "SELECT COUNT(*) FROM training_data;"
```

> **Expected:** `data/snapshots` unchanged by uploads; `training_data` unchanged by
> uploads.
>
> **Pass:** both unchanged.

---

# PHASE B additions — pins and wiring

> **Everything here is UNVERIFIED.** I have no board. Do all of it with **mains OFF**
> and the relay wiring **not** made.

## P1. Pin map — confirm on the board before wiring

| Pin | Use | Direction |
|---|---|---|
| 34 / 35 / 32 | MQ3 nose / jaw / clavicle | input (34, 35 input-only) |
| 4 | DHT11 | bidirectional |
| 2 | onboard status LED | output (existing) |
| 27 | suction-fan relay | output |
| 25 | blower relay | output |
| 26 | solenoid — reserved | **unused** |
| **22 / 23 / 18** | **indicator R / G / B** | output (PWM) |
| **19** | **ESP32 TX → DFPlayer RX** | output |
| **21** | **ESP32 RX ← DFPlayer TX** | input |

> ℹ️ **GPIO 16 and 17 are deliberately not used.** They are consumed by the PSRAM on a
> WROVER module, and Gg's board does not expose them at all. The indicator channels
> were moved to **22 / 23 / 18** for that reason. Confirm your module at boot anyway:
>
> ```bash
> arduino-cli monitor -p /dev/ttyUSB0 --config baudrate=115200
> ```
> A PSRAM board prints an **`ESP32: Detected PSRAM: ESP-PSRAM32`** line at boot. If you
> see that, **do not use 16/17** and re-plan the indicator wiring.
>
> **Pass:** the boot log shows WROOM-32 and **no** PSRAM line.

## P2. LED resistors — this is the step most likely to need a change

Measured from `docs/INDICATORS_WIRING.md` §4 with **R = 220 Ω** and both LEDs sharing
one resistor per channel:

| Channel | Vf | V_res | I per LED | Both LEDs | Verdict |
|---|---|---|---|---|---|
| red | 2.0 V | 1.3 V | 5.91 mA | **11.82 mA** | at the 12 mA GPIO limit |
| green | 3.1 V | 0.2 V | 0.91 mA | **1.82 mA** | **TOO DIM to see reliably** |
| blue | 3.1 V | 0.2 V | 0.91 mA | **1.82 mA** | **TOO DIM to see reliably** |

> **Expected:** green and blue are clearly dim, and the "yellow" WARN state reads
> **orange**, because green only contributes 0.68 mA against red's 5.91 mA.
>
> **Pass (as written):** you SEE the colours. **Pass (correct):** green and blue are
> clearly bright.
>
> **If they are too dim**, fit **one resistor per LED** (not one per channel) and use
> ~8 mA per LED: **red 150 Ω, green 100 Ω, blue 100 Ω**. That is 6 resistors. Green and
> blue have almost no voltage headroom on 3.3 V, so they will never be as bright as
> red.
>
> **If yellow still looks orange:** that is the physics, not a bug. Either accept
> orange, or lower `yellow.g` further in `indicator.h`, or use a lower-Vf green LED.

## P3. Common cathode vs common anode

Bench: press **Fail** in the indicator panel.

> **Common cathode (what is compiled in, `LED_COMMON_ANODE=false`):** the pin side
> goes **dark** and the LED **lights**.
> **Common anode:** the opposite. Set `LED_COMMON_ANODE true`, recompile, reflash.
>
> **Pass:** Fail shows **red**, steady, on BOTH LEDs.

## P4. DFPlayer

1. Speaker across `SPK1` and `SPK2` **only** — **never one leg to GND**.
2. Power from a **separate stable 5 V** with 470 µF + 100 nF at the module.
3. Common ground with the ESP32.
4. 1 kΩ in series on **each** UART line.

Bench: press **Analyzing** (a single word).

> **Expected:** one short word from the speaker. `audio_ready: true` in `/status`.
>
> **If silent:** try 38400 baud first — the datasheet commonly says 9600 but many
> modules ship 38400. Then check TX/RX are not swapped.
>
> **Pass:** a word is clearly audible.

## P5. DFPlayer absent must not block

Unplug the DFPlayer. Power-cycle the ESP32. Bench every button again.

> **Expected:** normal boot, `audio_ready: false`, every LED still correct, **no
> reboot**, no hang. The serial log shows a retry line every 30 s.
>
> **Pass:** no reboot, LEDs unaffected.

## P6. Module label

Photograph the ESP32 module marking and confirm **ESP32-WROOM-32DE** (or bare
WROOM-32), **not** WROVER/PSRAM. Tie this back to P1.
