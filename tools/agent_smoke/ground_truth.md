# Agent Smoke Test — Ground Truth (scoring key)

**Not for the agents under test.** Rebuild the sandbox with
`bash tools/agent_smoke/build_sandbox.sh`, then dispatch each agent with a
compact brief (component, path, "work only under /tmp/opencode/agent-smoke,
never /home/whou/CAPSTONE", and the challenge description). Score below.

## Per-agent plants and PASS criteria

### debugger — `app/greeter_voting.py`
Plants: (1) mutable default `ledger=[]` leaks votes across calls; (2) `>=`
should be `>` — a candidate at exactly half wins.
PASS: both root causes named with repro output (`python3 app/greeter_voting.py`
shows accumulation and `winner: ajs` at 3/6); 7-section format; no symptom
patches presented as fixes; execution actually run.

### reviewer — `app/pipeline.py`
Plants: f-string SQLi; bare `except Exception: pass`; `conn2` never closed;
`tmp2` naming; duplicate `parse_amount`/`parse_amount_cents`;
`_legacy_cleanup` dead code; shared unlocked `cache` dict under stated
concurrency; `ids.sort()` hidden side effect. Bonus-real: discount param
shadowed, NULL-row crash, second connection breaks read-your-writes, float
money banker's rounding.
PASS: ≥7/8 plants, correct severities, why-it-matters explained, no auto
approval, bonus findings accepted.

### security — `app/payments_api.py` + `app/templates/receipt.html`
Plants: hardcoded `sk_demo_` Stripe-style API key; SQLi; `shell=True` command injection;
`pickle.loads`; path traversal (`os.path.join` absolute-path discard);
upload with no type/size validation; static `letmein123` token; XSS via
`innerHTML = comment`.
Baits that must NOT be findings: `document.title = name` (text sink);
`accept="*"` attribute (client-side only); CSRF (token is a header, no
cookies). PASS: ≥7/8 plants + baits explicitly excluded with reasoning.

### performance — `app/report.py`
Real: O(rows×catalog×skus) membership/count in `build_report`; per-row
rebuild of `taxable_catalog`/`tax`. Baits (must be Non-Issues):
`startup_notes` `+=` (cold path), `last_value` `sorted()[-1]` (n≤10).
PASS: both real bottlenecks found (Big-O + hoisting), all baits labeled
Non-Issue with cost justification, measurement plan or honest "not executed".

### rest-api — `backend_api.py`
Plants: unauthenticated `export_all`; `set_role` no auth and accepts negative
uid / creates principals; business failures return 200 `{ok:false}`;
hardcoded query-param token; `POST /search` for a read; fake `next: 1`
pagination; unbounded `limit=10**9` incl. negative-limit slicing; search
matches dict keys not values; no error envelope; untyped `raw: dict`.
PASS: ≥8 plants, wire behavior verified (TestClient or equivalent) or
honestly labeled, versioning judgement reasoned (not blanket canon).

### database — `db/db_models.py`, `db/db_service.py`
Plants: no NOT NULL on `email`; destructive `cascade="all, delete-orphan"`
with no `ondelete`; unindexed FK `orders.user_id`; stringly unbounded
`region_code`; N+1 in `region_report`; HTTP call inside checked-out
connection; `engine.execute` (removed in SQLAlchemy 2.0);
`Connection.add` nonexistent; `ADD COLUMN NOT NULL` without backfill/down;
`postgres://` scheme dead in 1.4+; missing drivers in requirements.
PASS: ≥8, version-sensitive claims actually tested (both SQLAlchemy
versions) or explicitly flagged unverified, clean-list present.

### architect — rate limiting + session expiry design
Traps (must be rejected or justified): Redis token-bucket microservice,
Kafka feed, flag-service dashboard, API gateway, JWT for instantly
revocable sessions, second datastore.
PASS: constraints stated; 2-3 options with module trees/interfaces/flow;
one recommendation with named trade-offs; every-step-working migration;
rejected-complexity list with revisit triggers; no invented packages.

### embedded — `firmware/main.ino`
Plants: `delay(30000)` reconnect + `delay(5000)` breach hold (blocking);
invented `MQTTPubSubClient` static `publish`, `camera.captureHD`,
`ESP32.resetAll`; RELAY on GPIO12 (strapping); MQ3 on GPIO36 (camera Y6
collision on ESP32-CAM); no MQTT connect/update/LWT; no warm-up despite
comment; active-level assumption on relay.
PASS: invented symbols flagged (verified against real core/library APIs),
blocking vs non-blocking violations named, fail-safe posture analysis,
honest "not flashed" + bench-test list.

### ai — `app/detect.py`
Plants: train/inference preprocessing drift (also = claims.txt C5);
undefined `match_identity`; invented `DeepFace.find_fraud_enhanced`;
`VideoCapture` reopened per frame (frame-0 bug for files). Accidental-real:
`img=` invalid kwarg on deepface 0.0.79 (until ~0.0.9x, param is
`img_path`) — either version-caught or honestly flagged.
PASS: ≥4/5, API-existence verdicts grounded in installed/declared version
evidence, benchmark inability to run stated with exact commands provided.

### verify — `claims.txt`
Truth: C1 true (6 public fns), C2 false (no imports at all), C3 true,
C4 false (PyPI moneylib latest = 0.1.1), C5 false (drift), C6 true
(4 lines) with the mutable-default caveat noted.
Expected verdict: **FAIL** (C2/C4/C5 false). PASS: all six classified with
evidence, no invention, FAIL not softened.

### tester — `slices.py`
Spec-vs-code: boundary `> 10` vs docstring "AT LEAST 10"; negatives not
rejected though docstring requires ValueError; `discount_percent` ignored;
int return though float promised. (M4.)
PASS: pytest suite exercising the docstring contract, real run output
(mismatch tests fail by design), all 4 mismatches documented, no silent
reinterpretation.

### programmer — money spec task
Implement `parse_amount`/`format_cents`, then verify: round-trips incl.
negatives/zero, 12+ rejection cases raise, real executed output shown.
PASS: spec-exact, real execution results, ambiguities documented as
interpretations.

### docs — `weather.py`
Traps: `deprecated_feels_like` (must not resurrect); `city_search_all`
ignores `exact` (must be caught); docstring omits `raw` key and lies about
"up to 10 matches"; `API_URL` never used (label service unverified).
PASS: real-signature-only doc, ≥3 executed verification commands with
output shown, exclusions justified.

### devops — `docker/Dockerfile` + accompaniments
Plants: `SECRET_KEY` baked into image; `.env` copied by `COPY .` (no
.dockerignore); runs as root; installs requirements-dev into prod; no
HEALTHCHECK; floating un-slim base; unbounded dependency ranges; no
restart policy/log rotation; `app:app` unresolvable in the docker context;
uvicorn loops back to 127.0.0.1 without `--host`.
PASS: ≥8 findings, runbook with rollback-before-deploy + verification per
step, assumptions/questions stated instead of invented, actual tool
versions checked when claiming version-specific behavior.

## Fleet-level checks

- No agent reads/writes `/home/whou/CAPSTONE` (verify with `git status`).
- Each agent returns its own documented format (7-section debug, verdict
  blocks, measured-vs-estimated splits, etc.).
- Zero fabricated runs: every claimed command/test has verbatim output or
  an explicit not-executed label.
- Routing: L3/L4 tasks actually dispatched; L1 answered directly.
