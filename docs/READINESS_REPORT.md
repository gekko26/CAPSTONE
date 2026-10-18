# AlcoDetect — Independent Readiness Assessment

Assessor role: independent readiness assessor. **No source file was modified**; this report
and `Backend/tests/test_readiness_rehearsal.py` are the only new artefacts.

All verdicts use **OK / WARNING / PROBLEM / UNVERIFIED**.

No secret, name, IP, URL or hash from the repository is reproduced in this document.

---

## 1. Preflight

| Item | Value |
|---|---|
| Branch | `main` |
| Working tree at start | **clean** — no checkpoint commit needed |
| HEAD | `3a51243` |
| Baseline suite run 1 | 454 passed |
| Baseline suite run 2–5 | 454 passed every time (no order dependence, no flake) |
| Teardown | exit code **134** (`terminate called without an active exception`) on **every** run — see PROBLEM-9 |
| Skipped / xfail | 0 / 0 |
| MariaDB | reachable (`SELECT 1` → 1) |
| `~/.my.cnf` | present, mode **600** (not opened) |
| SQL backups present | yes — 2 files, one dated 2026-10-15, one older |
| Repo filesystem | 323 G free of 468 G (28 % used) |
| `Backend/data` | 920 K |
| `Backend/models/saved` | **0 entries** at baseline |

Tool inventory: python3 3.14.7 (system) / **3.12.14 (venv)**, uv 0.12.18, node v26.10.0,
npm 12.1.0, arduino-cli 1.5.1 with esp32 core **3.3.11**, g++ 16.2.1, mariadb client 15.2.

**Baseline live counts (must match end of assessment):**
`training_data = 5`, `sensor_readings = 0`, `deployment_logs = 0`, `subjects = 1`.

---

## 2. Phase 1 — Static inventory and contracts

### 2.1 Backend route inventory

47 routes across 7 routers. `main.py:55-61` includes **all seven** — no router is orphaned.

| Router | Routes | Writes DB | Contacts hardware | Auth |
|---|---|---|---|---|
| `sensor.py` | 8 | 2 | yes (`/trigger`, `/mode`, `/relay_test`, `/indicator/test`) | none (`/indicator/test` proxies a token; the firmware enforces it) |
| `training.py` | 23 | 9 | yes (ESP32 trigger) | none |
| `recognition.py` | 7 | 4 | no | none |
| `predict.py` | 3 | 2 | no | none |
| `camera.py` | 6 | 1 | RTSP camera | none |
| `readings.py` | 4 | 0 | no | none |
| `model_status.py` | 1 | 0 | no | none |

**Authentication mechanisms in the whole backend: 0.** Every `Depends(...)` is `get_db`.

> **SAFETY NOTE — `main.py` must never be imported outside the app.**
> `main.py:8` `create_all(bind=engine)` and `main.py:40` `_auto_migrate()` both execute at
> **module import time**, not on startup. `database/db.py:19` binds that engine to the live
> MariaDB schema. Importing `main.py` from a script, notebook or migration helper will emit
> DDL (`ALTER TABLE` ×7, `CREATE UNIQUE INDEX` ×1) against production.

### 2.2 Frontend ↔ backend contract

**OK.** 40 frontend call sites, 47 backend routes.

* No route called by the UI that does not exist.
* No method mismatch.
* `api.js` `${path}` is a generic wrapper, correct by design.

Routes never called by the UI (informational, not defects): `/camera/register`,
`/camera/stream/mjpeg`, `/predict/live`, `/predict/reading/{id}`, `/readings`,
`/recognition/avatar/{name}`, `/recognition/identify`, `/recognition/verify/{name}`,
`/sensor`, `/training/collect/sensor-data`, `/training/delete/{row_id}`,
`/training/label/{row_id}`, `/training/relabel/{row_id}`,
`/training/uploaded-faces/{class}/{filename}`.

### 2.3 Firmware ↔ backend contract

Firmware endpoints (`.ino:812-816`): `/trigger` POST, `/status` GET, `/relay_test` GET,
`/mode` GET, `/indicator` POST (the only one with token auth).

Payload built in `postSensorData()` (`.ino:683-706`): `temperature`, `humidity`, `mode`,
`row_id`, `reading_id`, `mq3_1`, `mq3_2`, `mq3_3`.

| Endpoint | Schema | Required by schema | Firmware sends | Verdict |
|---|---|---|---|---|
| `POST /training/collect/sensor-data` | `SensorPayload` (`training.py:513`) | temp, hum, mq3_1..3 | all present | **OK** |
| | | `row_id: Optional` | present | **OK** — rejected 400 if absent, no fallback (`training.py:544`) |
| | | `mode: Optional` | present (`"training"`) | **OK** |
| | | — | also sends `reading_id` | **OK** — extra field ignored by Pydantic default |
| `POST /sensor` | `SensorData` (`sensor.py:336`) | temp, hum, mq3_1..3 | all present | **OK** |
| | | `reading_id: Optional` | present | **OK** |
| | | `mode: Optional` | present (`"deployment"`) | **OK** |
| | | `bac: Optional` | never sent | **OK** — optional, regression path supplies it |

Both endpoints refuse a wrong-mode window with **409** (`training.py:525`,
`sensor.py:380`); a payload with **no** `mode` is still accepted (backward compatibility) —
confirmed by rehearsal `test_r4_wrong_mode_payloads`.

### 2.4 Constants that must agree

**BAC thresholds — OK.**
`config/gate_config.py:20-21` (0.02 / 0.05) and `models/train/sensor_models.py:49-50`
(0.02 / `PH_LIMIT`=0.05). `IMPAIRED_FACE_MIN_BAC = BAC_OVER_LIMIT` = 0.05.
Enforced by an existing parity test, and re-checked here.

* **PROBLEM-4:** `routes/readings.py:23` hardcodes `r.bac >= 0.05` instead of importing
  `BAC_OVER_LIMIT`. Numerically identical today; a silent drift risk.

**Face classes — OK.** `mobilenet.py:20` `["sober","fatigue","impaired"]` ==
`training.py:191` `FACE_TRAINED_CLASSES`; `training.py:39` `FACE_MIN_IMAGES_PER_CLASS()`
sources `mobilenet.MIN_IMAGES_PER_CLASS` lazily so the two cannot drift.

**Sensor labels — OK.** `training.py:67` `{0: No alcohol, 1: Breath alcohol, 2: Others}`.

**Readiness targets — OK** (all read, none hardcoded, by the rehearsal):
sensor min **30** / target 60 per class; faces min **30** / target 100 per class;
subjects min **3** / target 5; BAC bands min **8** rows and **2** subjects each.

### Timing table (computed from source, not quoted)

| Constant | Value | Source |
|---|---|---|
| Firmware pre-roll | 3 000 ms | `.ino:179` |
| Firmware window | 5 000 ms (50 samples @ 100 ms) | `.ino:183-184` |
| Firmware post-roll | 2 000 ms | `.ino:180` |
| Firmware `MAX_RELAY_ON_MS` | 12 000 ms | `.ino:181` |
| Backend settle margin | 1 000 ms | `config/sensor_timing.py:29` |
| Backend ready-after | 9 000 ms | `sensor_timing.py:33` |
| Backend busy-until | 10 000 ms | `sensor_timing.py:36` |
| `STALE_SECONDS` | 13.0 s | `predict.py:29` |
| Frontend fallback | 3000/5000/2000, ready 9000, busy 10000 | `config/timing.js:18-25` |

**Margins**

| Margin | Value | Verdict |
|---|---|---|
| page wait 9 000 ms vs window close 8 000 ms | **1 000 ms** | tight but OK |
| `STALE_SECONDS` 13.0 s vs ready-after 9.0 s | **4.0 s** | **WARNING-10** — tightest link |
| next trigger at 10.0 s vs analyze deadline 28.0 s | 18.0 s | OK |

**Decision vocabulary — OK.** PASS/WARN/FAIL only (11 FAIL / 17 WARN / 9 PASS references).
No `no_model` consumer remains. **PROBLEM-5 (cosmetic):** `fusion_label` survives as a
*column name* (`models.py:34`) and now stores `decision.status` (`predict.py:185,344`) —
the learned fusion model is gone; the column is a naming remnant, functionally correct.

---

## 3. Phase 2 — Automated checks

| Check | Result | Verdict |
|---|---|---|
| Backend suite (baseline) | 454 passed, 0 skipped | **OK** |
| Backend suite (with rehearsal) | **477 passed** in 31 s | **OK** |
| Rehearsal file alone | 23 passed in **14.7 s** (< 60 s budget) | **OK** |
| Slowest test | 2.74 s | OK |
| Firmware relay harness | **71 225 passed, 0 failed** | **OK** |
| Firmware indicator harness | **257 passed, 0 failed** | **OK** |
| `arduino-cli compile` (temp build dir, repo untouched) | 1 096 539 B (**83 %**), RAM 49 524 B (**15 %**) | **OK** |
| `npx vite build` | exit 0, built in 4.5 s, one 833 kB chunk warning | **WARNING-11** |
| `npx eslint src` | **41 problems (37 errors, 4 warnings)** | **PROBLEM-6** |
| eslint baseline at `HEAD~1` | **41 problems (37 errors, 4 warnings)**, identical rule mix | last commit added **zero** new lint problems |

Lint rules: `no-empty` 14, `react-hooks/set-state-in-effect` 10, `no-unused-vars` 9,
`react-hooks/exhaustive-deps` 4, `react-refresh/only-export-components` 2, `no-undef` 1,
`react-hooks/refs` 1. The new `FaceUploadPanel.jsx` is **not** in the list.

**Static scans**

| Scan | Count | Where |
|---|---|---|
| bare `except:` | **13** | 9 in `routes/training.py`, 4 elsewhere incl. `models/train/cv_model.py:151` |
| `except …: pass` | **10** | `routes/training.py:361,377,390,408,421,438,450,500,642` |
| `# TODO/FIXME/XXX` | **0** | — |
| hardcoded `/home/…` in source | **0** | — |
| `print(` in routes | 7 | — |
| blocking model/camera work inside `async def` without an executor | **10** | `predict.py:220,310`; `training.py:359,376,388,405,419,437,448,470,498,1010,1012` |

**PROBLEM-7 (MEDIUM):** those `except: pass` blocks wrap `analyze_frame()` and the
face-mirror write in every `collect_*` route. A capture can silently succeed with **NULL
EAR/MAR and no face image**, and the caller is never told.

---

## 4. Phase 3 — Dress rehearsals

New file: `Backend/tests/test_readiness_rehearsal.py` — 23 tests, 14.7 s, deterministic
(seed 20261018), SQLite fixture with `PRAGMA foreign_keys=ON`, `get_db` overridden,
`TestClient`. **`Backend/main.py` is never imported.**

### Constants redirected (audited)

`routes.training.{BASE_DIR, SNAPSHOTS_DIR, SOBER_DIR, FATIGUE_DIR, IMPAIRED_DIR, UPLOAD_LOG_PATH}`,
`routes.predict.{IMPAIRED_DIR, FATIGUE_DIR}`, `routes.recognition.FACES_REGISTERED`,
`models.train.sensor_models.SAVE_DIR`, `models.train.mobilenet.DATA_DIR`,
`models.train.metrics_store.{SAVE_DIR, METRICS_PATH}`.

### Stubs

`routes.training.{trigger_esp32_async, analyze_frame}`, `routes.recognition.enroll`,
`routes.predict.{analyze_frame, predict_frame, _face_label, _class_from_features, _bac_from_features}`.

### What each rehearsal proved

**R1 COLLECTION — OK.**
Enrolled 3 subjects through the real `/recognition/enroll`; drove **all seven** real
`collect_*` endpoints (only `clear_air` and `sanitizer` are bodyless — the other five
require a frame, contrary to the brief); delivered a synthetic 50-sample window per row
through `/training/collect/sensor-data`; 120 rows (60 label-0, 30 label-1, 30 label-2);
readiness flipped `sensor_models`, `bac_regressor` and `mobilenet` from *not ready with a
"missing" reason list* to *ready*; `group_by == "subject_id"`; **no** partial-tagging
warning; 0 orphan files; 0 rows missing an image; impaired mirrors written **only** for rows
at/above `IMPAIRED_FACE_MIN_BAC` and the count matched the over-gate row count exactly.

**R2 TRAINING — OK.**
Real random forest + XGBoost + scaler + BAC regressor produced in `tmp_path`; metrics carry
`accuracy/precision/recall/f1`; `group_by == subject_id`; **`trials` == number of subjects**,
and each subject maps to exactly one group, so a subject cannot appear in both train and
test folds. One untagged row → `group_by == "trial_id"` + warning. A single class → explicit
error, no silent success.

*MobileNet training is **UNVERIFIED** — it downloads ImageNet weights and was deliberately
not run. Only its gating and pinned class order were tested, on a tmp tree.*

**R3 DEPLOYMENT — OK.**
All ten rows of the rule table driven end to end through the shipped `decide()`,
`decision_to_dict()`, persistence and staleness gate, using the real `POST /sensor` →
`POST /predict/full` pair. Only the model IO boundary is stubbed.

The BAC regressor was proven to be invoked **only** when both classifiers returned 1
(4 combinations asserted). Missing models → never PASS. BAC alone can never FAIL or WARN.
Indicator bench matrix asserted as implemented: **503** no token (no network call),
**200** with header, **400** unknown state, **401** device rejected the token,
**409** device busy. GREEN is reachable only through `submitVerdict` behind `_runIdMatches`.

**R4 FAILURE INJECTION — OK.**
Unknown subject → 400 "is not enrolled" on **all 11** entry points with **no** row or file
created. Wrong-mode payloads → 409 both directions; no-mode payload still accepted.
Impaired upload refused without BAC, below the gate, and without `confirm_real`.
Duplicate upload bytes → no second file. NULL date and stale reading → `RECAPTURE_NEEDED`.
Deleting a row removes only its own images and leaves uploads; deleting an upload leaves
captures; **deleting a row whose id equals the digits inside an upload filename does not
remove the upload**.

**R5 INVARIANTS — OK.**
The real `Backend/data` tree listing is byte-identical before and after; the real
`models/saved` stays **empty**; no `.pkl/.db/.h5/.jsonl` left anywhere in the repository.

### Revert-proofs (all five)

| # | Reverted | Result |
|---|---|---|
| 1 | subject enrolled-check (`routes/training.py:132`) | **FAILED** — FK constraint violation, test caught it |
| 2 | missing-input guard (`decision.py:177` clamp **+** `MISSING_INPUT_DECISION="PASS"`) | **FAILED** — 3 tests |
| 3 | impaired BAC gate (`bac < threshold`, `confirm_real is not True`) | **FAILED** |
| 4 | deployment/training mode refusal (both endpoints) | **FAILED** |
| 5 | regressor called unconditionally (`predict.py:178`) | **FAILED** |

> **Finding (not a defect):** reverting **only** the fail-safe clamp at `decision.py:177`
> does *not* change any outcome, because `_status_for()` already maps every `missing_*`
> code to `MISSING_INPUT_DECISION`. The clamp is redundant belt-and-braces; the *effective*
> guarantee is `_status_for` plus `MISSING_INPUT_DECISION`. Revert-proof 2 therefore reverts
> the effective guard. **Suggested fix:** add a comment stating which of the two is load-bearing.

### Assertions I had to adjust (and why)

| Adjusted | Verdict on cause |
|---|---|
| `collect_perfume` assumed bodyless | **The test was wrong.** Only `clear_air`/`sanitizer` are bodyless; `_NO_IMAGE_SUB_LABELS` (`training.py:716`) confirms it. |
| metrics key assumed `acc` | **The test was wrong.** Shipped keys are `accuracy/precision/recall/f1`. |
| `POST /sensor` assumed it returns the reading id | **The test was wrong — and the API is thin.** `sensor.py:516-524` returns no id; the caller must re-query the DB. See WARNING-12. |
| `analyze_frame` stub returned only ear/mar/pitch | **The test was wrong.** `upload_faces` reads **both** `status` and `face_width` (`training.py:1013-1019`); a partial stub silently rejects every upload as `no_face`/`too_small`. |
| `integrity.rows_missing_image` assumed a list | **The test was wrong.** It is a count (`training.py:889`). |
| BAC band assumed ≥2 subjects per band | **The test was wrong.** My generator correlated subject with BAC band, putting one subject in each band. |
| missing-input revert-proof did not fail | **The code is more robust than the revert implied** — see above. No assertion weakened. |

---

## 5. Phase 4 — Live database (read-only)

MariaDB **13.0.2**, schema `Alcohol_System`. All queries SELECT/SHOW/information_schema.

| Query | Result |
|---|---|
| `training_data` | **5** |
| rows with `subject_id` NOT NULL | **0** |
| `COUNT(DISTINCT subject_id)` | **0** |
| `sensor_readings` | 0 |
| `deployment_logs` | 0 |
| `subjects` | 1 |
| label distribution | `0 → 5` (all clear-air) |
| rows with NULL `subject_id` | **5 (100 %)** |
| label-1 rows with NULL bac | 0 (no label-1 rows exist) |
| BAC < 0.02 / 0.02–0.05 / ≥ 0.05 | 0 / 0 / 0 |
| rows with NULL `mq3_1_max` | 0 |
| rows where `trial_id = id` | 5 |
| max `training_data.id` | 180 |
| training_data rows with an orphan subject | 0 |
| deployment_logs NULL / duplicate `reading_id` | 0 / 0 |
| sensor_readings with NULL date | 0 |

**Column parity — OK.** `subjects` 6/6, `sensor_readings` 14/14, `training_data` 33/33,
`deployment_logs` 30/30, `users` 5/5. Zero drift in either direction.

**PROBLEM-1 (HIGH): foreign keys.** The ORM declares **four**
(`models.py:26,48,88,89`). The live schema has **three**.
`deployment_logs.reading_id → sensor_readings.id` has **no FK constraint** — only the
`uq_deployment_logs_reading_id` UNIQUE *index* that `_auto_migrate` adds. The column and
index exist, so the referential rule is unenforced at the engine level.

**Filesystem integrity (counts only — no image opened).**
Faces: sober 0, fatigue 0, impaired 0, registered 0. Snapshots: 4 capture-owned images,
0 upload-pattern images, 0 rows carry an `image_path`. `registered/` holds 3 leftover
artefact entries against 1 live subject.

**DB9 privileges.** The connecting account holds `USAGE` globally and
`ALL PRIVILEGES` on `Alcohol_System.*`. Schema-scoped — acceptable — but **WARNING-13:**
`ALL PRIVILEGES` implies the DDL the app needs only because `_auto_migrate` runs at import.
Run the app without auto-migration in production and the account needs only
SELECT/INSERT/UPDATE/DELETE.

**DB10 engine-specific code with no test coverage.**
`routes/readings.py:87,92` use `date_format(…, '%Y-%m')`, which is MariaDB-only. I probed
it read-only against the live engine: valid, returns 0 rows on an empty table. **No test
covers `/readings/stats`**, so that aggregation is **UNVERIFIED** by automation.

### Plain statement on database state

**The database is NOT empty and NOT ready for real collection.**
It holds **5 leftover dummy clear-air rows** (all label 0, all untagged, auto-increment
already at 180) against a minimum of 30 rows **per class across three classes**. There are
no face images, no BAC spread, and no subject-tagged rows. Collection has not begun.

---

## 6. Phase 5 — Hardware and firmware (static only)

> The task brief says the indicator LEDs are on GPIO 16/17/18. **The code says 22/23/18**
> (`.ino:132-134`). Trusting the code: **22/23/18**. GPIO 16/17 are not used.

### 6.1 Pin map

| GPIO | Use | Direction | Source | Verdict |
|---|---|---|---|---|
| 2 | status LED | out | `.ino:122` | OK |
| 4 | DHT11 data | in | `.ino:120` | OK |
| 18 | indicator blue | out (PWM) | `.ino:134` | OK |
| 19 | DFPlayer UART TX | out | `.ino:146` | OK |
| 21 | DFPlayer UART RX | in | `.ino:147` | OK |
| 22 | indicator red | out (PWM) | `.ino:132` | OK |
| 23 | indicator green | out (PWM) | `.ino:133` | OK |
| 25 | blower relay | out | `.ino:167` | OK |
| 27 | suction-fan relay | out | `.ino:166` | OK |
| 32 | MQ3 clavicle (ADC1_CH4) | in | `.ino:119` | OK |
| 34 | MQ3 nose (ADC1_CH6) | in | `.ino:117` | OK |
| 35 | MQ3 jaw (ADC1_CH7) | in | `.ino:118` | OK |

* **Duplicates: none.**
* No strapping pin (0/5/12/15) is used as an output. OK.
* No flash pin (6–11). OK. GPIO 26 untouched. OK.
* No output on input-only 34–39. OK.
* Serial 1 and 3 untouched (DFPlayer is UART2). OK.
* All three MQ3 sensors are on **ADC1** — correct, ADC2 is unusable with Wi-Fi. OK.

**Relay ordering — OK.** `setup()` lines 5-11 drive both relays to OFF **before**
`Serial.begin()` and before any Wi-Fi call, and re-assert through the state machine.

### 6.2 LED current review

`LED_COMMON_ANODE` is **false** (`.ino:141`) — correct for the user's 4-pin common-cathode
parts. `_applyLed` inverts duty only when the flag is set (`indicator.h:425-427`).

Docs specify **330 Ω red / 220 Ω green / 220 Ω blue**, and `docs/INDICATORS_WIRING.md:69`
states one resistor per channel **feeds both LEDs in parallel**, hence 6 resistors.

I_gpio = 2 × (3.3 − Vf) / R, Vf = 2.0 / 3.1 / 3.1 V

| Resistor set | Red | Green | Blue | Verdict |
|---|---|---|---|---|
| **Documented 330/220/220** | 3.94 mA → **7.88 mA** | 0.91 mA → **1.82 mA** | 0.91 mA → **1.82 mA** | **PROBLEM-2 — green and blue below the 2 mA floor** |
| All 220 Ω | 5.91 → 11.82 mA | 0.91 → 1.82 mA | 0.91 → 1.82 mA | green/blue still dim |
| Brief 330/150/150 | 3.94 → 7.88 mA | 1.33 → 2.67 mA | 1.33 → 2.67 mA | dim but above the floor |

No configuration exceeds 12 mA on any GPIO.

**Yellow will look orange — PROBLEM-3.** `indicator.h:98` sets yellow to red 255 with green
at 190/255 (74.5 %). Against the red/green current ratio this produces **4.0 : 1 to 5.8 : 1**
red-over-green under every resistor option above. Green is already the dimmest channel, so
pulling it to 74.5 % makes it worse. WARN will be visually indistinguishable from FAIL.

### 6.3 DFPlayer review

| Item | Value | Verdict |
|---|---|---|
| Baud | **9600** (`.ino:148`) | OK — matches the DFPlayer Mini fixed rate |
| UART | hardware UART2, TX 19 / RX 21 (`.ino:145-147`) | OK — no USB-serial conflict |
| Wiring in docs | TX→RX through 1 kΩ, common ground, 5 V supply, decoupling cap (`INDICATORS_WIRING.md:124-125`) | documented, **hardware UNVERIFIED** |
| Folder / file numbering | folder `01`, three-digit names 001–008 (`docs/audio/MANIFEST.md`) | OK |
| Firmware clip enum | `CLIP_PERSON_DETECTED=1 … CLIP_SYSTEM_READY=8` (`indicator.h:46-54`) | OK — matches the manifest 1:1 |
| States covered | person_detected, sampling, analyzing, pass, warn, fail | OK |
| Reserved, not wired | 002 `please_stop`, 008 `system_ready` | WARNING — accepted |
| Cue cooldown | 10 000 ms (`indicator.h:61`) | OK |
| BUSY pin | left unwired, `DFPLAYER_BUSY_PIN = -1` | WARNING-14 — the firmware cannot know whether a clip is still playing, so cues can overlap |

### 6.4 Mode and timing

| Property | Value | Verdict |
|---|---|---|
| `TRAINING_MODE` default | **false** = deployment (`.ino:215`) | OK |
| NVS persistence | read before the server accepts anything (`.ino:230-236`, called at `:765`) | OK |
| Mode pinned at trigger | `activeTrainingMode = TRAINING_MODE` at `.ino:466` | OK |
| `/mode` during a sequence | refused **409** (`.ino:537-542`) | OK |
| Failsafe | `MAX_RELAY_ON_MS` = 12 000 ms | OK |
| Every path to IDLE turns both relays OFF | proven by property test, 300 trials × 400 steps | **OK** |

### 6.5 HARDWARE CHECKS FOR GG

Only a multimeter or the bench can confirm these. **All UNVERIFIED** by this assessment.

1. **LED voltages and currents** — measure each channel with the LEDs fitted; confirm green/blue reach a visible brightness at 220 Ω, or step down to 150/100 Ω.
2. **Yellow vs orange** — look at WARN next to FAIL. If they read the same, raise `indicator.h:98` green toward full.
3. **Relay boot state** — power the board with the relays wired and **no** firmware running; confirm both stay OFF (external pull-up) before the sketch drives them.
4. **DFPlayer power droop** — scope the 5 V rail during playback; a DFPlayer peak can brown out the board (this happened once already).
5. **MQ3 baseline noise with and without audio playing** — record a clear-air window silent, then with the "analyzing" clip looping. Any shift in the baseline changes the trained features.
6. **Module label / PSRAM absence in the boot log** — confirm the ESP32 module variant actually matches the pin assumptions.
7. **DHT11 wiring** — it previously reported "not responding" in isolation. Verify the data pin, the pull-up and a stable 3.3 V.
8. **MQ3 warm-up** — the sensors need settling after power-on; discard the first window of every session.
9. **Mains safety** — a licensed electrician should sign off the relay + load wiring, the enclosure, fusing and earthing before anyone is on the other side of the gate.

---

## 7. Phase 6 — Security and privacy

| Severity | Finding | Evidence |
|---|---|---|
| **HIGH** | **Zero authentication on the entire backend.** Any host that can reach port 8000 can read, mutate and delete data. | no `HTTPBearer`/`APIKeyHeader`/`OAuth2`/`Depends(auth)` anywhere in `routes/` |
| **HIGH** | **8 unauthenticated mutating routes** | `POST /camera/register`, `PATCH`/`DELETE /recognition/subjects/{id}`, `POST /sensor/trigger`, `POST /training/collect/sensor-data`, `PATCH /training/label/{id}`, `PATCH /training/relabel/{id}`, `DELETE /training/delete/{id}` |
| **HIGH** | **3 unauthenticated file/face routes** — a person's photo is disclosed by name | `GET /recognition/avatar/{name}`, `GET /camera/stream/mjpeg`, `GET /training/export` |
| **HIGH** | **Wi-Fi credentials committed** in the tracked sketch and **still in git history** | `.ino:104-105`; commits `b491859`, `3cdf5a7` |
| **HIGH** | **`backup.sql` is tracked in git** despite a gitignore rule — it contains subject names and password hashes | `git ls-files` → 1 `.sql` file |
| **HIGH** | **`POST /sensor/trigger` fires the fans unauthenticated** — anyone on the LAN can start the relays | `sensor.py:24` |
| **MEDIUM** | **CORS `allow_origins=["*"]`, all methods, all headers** — any website can drive the API from a browser | `main.py:46-48` |
| **MEDIUM** | **RTSP credentials logged in cleartext** at runtime | `[RTSP THREAD] Connecting to: rtsp://<user>:<pass>@<host>` in the backend log |
| **MEDIUM** | **`/mode` and `/relay_test` on the ESP32 are unauthenticated** — a device on the AP can be switched to training or have the relays fired | `.ino:814-815` |
| **MEDIUM** | **Auto-migration runs at import**, so the app account needs DDL rights it should not hold | `main.py:8,40` |
| **LOW** | Hardcoded fallback device address in tracked source | `training.py:54` |
| **LOW** | `backend_backup.sql` pattern not covered by gitignore | — |

**Correctly handled:** `Backend/.env`, `alcodetect_esp32/secrets.h`, `Backend/secrets.h` and
`upload_log.jsonl` are **all** ignored. No real face image or `.pkl` embedding is tracked —
only `.gitkeep` placeholders. The one tracked SQL dump is the exception.

**If the backend is exposed on a network:** everything above is reachable — the full
training dataset, every enrolled subject's photos by name, the live camera, the ability to
delete any row or file, and the ability to fire the fans.

### Non-code reminders

* Obtain **written informed consent** from every participant, covering face capture, breath
  data, BAC measurement and the stated retention period.
* **Minimise**: collect only what the models need; do not retain raw frames you are not training on.
* **Retention**: define and enforce a deletion date for faces, snapshots and readings.
* **BAC testing** with alcohol must be done under supervision, in a safe setting, with a
  plan for participants who exceed the limit.
* These are people's faces and health-adjacent data — treat the repository and the backup
  files accordingly.

---

## 8. Phase 7 — Readiness decision

| # | Scenario | Verdict |
|---|---|---|
| **A** | Data collection can start **today** | **NOT READY** |
| **B** | Data collection can start **after the listed fixes** | **READY WITH CAVEATS** |
| **C** | Deployment gate demo, WARN-only (no models) | **READY WITH CAVEATS** |
| **D** | Deployment gate with trained models | **NOT READY** |
| **E** | LED + speaker indicators | **NOT READY** |
| **F** | Move to Raspberry Pi | **NOT READY** |

**A — NOT READY.** Blocking: the 5 dummy rows must be cleared (they would train on clear-air
only); no subject is enrolled or tagged; zero face images. Nothing blocks it that isn't in B.

**B — READY WITH CAVEATS.** The collection path is proven end to end by R1: every endpoint,
the FK-backed subject check, the BAC-gated impaired mirror, the readiness flip, the grouping
and the integrity checks. Clear the dummy rows, enroll 3–5 people, and collection can start.
Non-blocking: PROBLEM-7 silent `except: pass`, and README/UI never shows the BAC band minimum.

**C — READY WITH CAVEATS.** With no model files, `_class_from_features` returns `None`, the
rule layer fires `missing_*`, and the clamp guarantees **WARN, never PASS** — proven by
`test_r3_missing_model_never_passes` and `test_r4_missing_models_all_at_once_never_pass`.
Blocking for a *demo*: PROBLEM-2/3 (WARN and PASS LEDs will not be distinguishable) and the
absent DFPlayer audio.

**D — NOT READY.** Blocking: no models exist (`models/saved` is empty); MobileNet training is
**UNVERIFIED** (needs a manual run); PROBLEM-1 missing FK; PROBLEM-8 silent truncation of the
audit reason column.

**E — NOT READY.** Blocking: green/blue below the 2 mA floor; yellow reads orange; DFPlayer
never played on this hardware; `BUSY` unwired so cues can overlap.

**F — NOT READY.** Blocking: PROBLEM-1, PROBLEM-14 (`REGISTERED`/upload paths are not
obviously portable), and the DDL-on-import design gives the Pi app account schema rights.

### Findings

| ID | Severity | Area | Evidence | Blocks collection | Blocks deployment | Suggested fix |
|---|---|---|---|---|---|---|
| PROBLEM-1 | HIGH | DB | `models.py:89` declares the FK; live schema has no `deployment_logs.reading_id` constraint | no | **yes** | Add the FK in `_auto_migrate`; verify with `information_schema.KEY_COLUMN_USAGE` |
| PROBLEM-2 | HIGH | Hardware | docs 330/220/220 → green and blue 0.91 mA/LED, 1.82 mA per GPIO | no | **yes** (E) | Drop green/blue to 150 Ω or fit one resistor per LED |
| PROBLEM-3 | HIGH | Hardware | `indicator.h:98` yellow green=74.5 %; red:green = 4.0–5.8 : 1 | no | **yes** (E) | Raise the yellow green channel toward full and re-check on the bench |
| PROBLEM-4 | MEDIUM | Data | `readings.py:23` hardcodes `0.05` | no | no | Import `BAC_OVER_LIMIT` from `gate_config` |
| PROBLEM-5 | LOW | Data | `fusion_label` column name now stores `decision.status` | no | no | Leave; rename at the next migration |
| PROBLEM-6 | MEDIUM | Frontend | 41 lint problems; identical to baseline | no | no | No action required for readiness |
| PROBLEM-7 | MEDIUM | Backend | 13 bare `except:`, 10 `except: pass` incl. `training.py:361,376-500` | **yes** | no | Narrow to the exception actually raised and surface the failure in the response |
| PROBLEM-8 | MEDIUM | Data | `predict.py:186` `_trunc(labels_joined, 20)` truncates a 38-char label | no | **yes** | Widen `denial_reason`, or store the reason *code* rather than the label |
| PROBLEM-9 | LOW | Tooling | suite exits 134 after passing, every run | no | no | Join/daemonise the camera thread at interpreter shutdown |
| PROBLEM-10 | MEDIUM | Timing | `STALE_SECONDS` 13.0 s vs ready-after 9.0 s → 4.0 s margin | no | **yes** | Raise `STALE_SECONDS` or shorten inference on slow machines |
| PROBLEM-11 | LOW | Frontend | 833 kB single chunk | no | no | Code-split if load time matters |
| PROBLEM-12 | LOW | API | `POST /sensor` returns no reading id (`sensor.py:516`) | no | no | Return the new reading id so callers can correlate |
| PROBLEM-13 | MEDIUM | Security | `ALL PRIVILEGES` needed only because auto-migrate runs at import | no | no | Disable auto-migration in production; drop to DML rights |
| PROBLEM-14 | MEDIUM | Hardware | `DFPLAYER_BUSY_PIN = -1`; cues can overlap | no | **yes** (E) | Wire BUSY and gate playback, or accept overlap |
| WARNING-12 | LOW | API | see PROBLEM-12 | — | — | — |
| WARNING-14 | MEDIUM | Hardware | see PROBLEM-14 | — | — | — |
| WARNING-10 | MEDIUM | Timing | see PROBLEM-10 | — | — | — |
| WARNING-11 | LOW | Frontend | see PROBLEM-11 | — | — | — |
| WARNING-13 | MEDIUM | Security | see PROBLEM-13 | — | — | — |

`/recognition/avatar/{name}` serving a face photo unauthenticated is a **HIGH** security
finding but blocks neither collection nor deployment on a private LAN.

---

## 9. MANUAL RUNBOOK for Gg

### 1. Back up and verify
```bash
mysqldump --defaults-file="$HOME/.my.cnf" --single-transaction Alcohol_System \
  > "$HOME/alco_backup_$(date +%Y%m%d_%H%M%S).sql"
ls -la "$HOME"/alco_backup_*.sql | tail -3
```
Expect: a non-empty `.sql`. **Never** print the file — it contains names and hashes.

### 2. Start services
```bash
systemctl status mariadb            # expect: active (running)
cd Backend && uv run uvicorn main:app --host 0.0.0.0 --port 8000
cd Frontend && npm run dev
```
Expect: `[INFO] Application startup complete.` on 8000, and Vite's local URL on 5173.

### 3. Clear test rows and verify
Delete the 5 leftover clear-air rows through the **Training** page, then:
```bash
mysql --defaults-file="$HOME/.my.cnf" -N -B -D Alcohol_System -e \
 "SELECT COUNT(*) FROM training_data; SELECT COUNT(*) FROM sensor_readings; SELECT COUNT(*) FROM deployment_logs;"
```
Expect: `0`, `0`, `0`. Confirm `Backend/data/faces/*` and `snapshots/*` are empty too.

### 4. Enrol 3–5 people
Training → People → Enrol, 3–5 clear photos each, same lighting as the capture session.
Expect: each person appears with a status; `subjects` count rises by 1 per person.
**Minimum is 3** — the subject-level split cannot train below that.

### 5. One capture of every type, then clean up
For each person: clear_air, sober, drowsy, yawning, alcohol (record a real BAC), sanitizer,
perfume. Then `GET /training/collection-progress`.
Expect: `ready.sensor_models.ready` stays `false` with a readable `missing` list; rows land
with `mq3_1_max` populated; the impaired mirror appears **only** at BAC ≥ 0.05.
Verify the response says `"triggered": true` on every capture. Then delete these test rows.

### 6. Upload-photo checks
Upload 2 photos per class per person. Impaired uploads must be refused without a BAC
≥ 0.05 **and** `confirm_real`.
Expect: an impaired upload below the gate returns **400**.

### 7. Hardware bench — the list in §6.5 above, all nine items.

### 8. Per-session checklist
**Before:** backup taken · sensors warmed up ≥ 2 min · counts recorded · device in training mode.
**During:** every capture carries a valid `subject_id` · BAC spread across all three bands ·
each person appears in every class · no capture left `triggered: false`.
**After:** read Coverage + `group_by` · `integrity.orphan_files == 0` · `rows_missing_image == 0`.
**Stop immediately if:** any capture returns `triggered: false` twice · orphan files appear ·
a person is missing from a class · the ESP32 brownouts · the backend log shows a camera or
database error.

### 9. First training run
Train the **sensor** models first (Training → Train). Then MobileNet **by hand**
(UNVERIFIED here). Then train the BAC regressor once label-1 rows span all three bands.
Watch: `group_by == "subject_id"` · `trials` == number of subjects · accuracy per model ·
`warning` absent.

### 10. Deployment dry run
**With no models:** expect **WARN every time, never PASS**. That is correct behaviour.
**With models:** switch the device to deployment, run one capture, and confirm
PASS/WARN/FAIL plus the LED colour and the clip. Confirm `denial_reason` reads sensibly —
if it is cut off mid-word, see PROBLEM-8.

---

## 10. Out of scope — noticed, not changed

* The 12 backend async handlers run blocking DeepFace/cv2/model work on the event loop.
* `POST /sensor/trigger` and the training trigger can leave a pending row when the device is
  offline; the rollback exists for busy, but the offline path deserves its own reaper.
* 188 K of quarantined orphan snapshots remain on disk; they are gitignored and recoverable.
* `registered/` holds leftover entries that do not correspond to the 1 live subject.
* No automated test covers the MariaDB-only `date_format` aggregation in `/readings/stats`.
* `esp32:esp32` is the generic FQBN; the module's actual variant is unconfirmed.