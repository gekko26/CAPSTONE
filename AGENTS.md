# CAPSTONE — Agent Conventions

Face-alcohol detection system (breathalyzer gate): React/Vite dashboard, Python
FastAPI-style backend, ESP32 firmware, Postgres database.

## Stack

| Component | Location | Stack |
|---|---|---|
| Frontend | `Frontend/` | React + Vite + Tailwind (pages in `src/pages/`, components in `src/components/`, config in `src/config/`) |
| Backend | `Backend/` | Python, FastAPI-style routes in `routes/`, domain logic in `models/`, tests in `tests/` |
| Firmware | `alcodetect_esp32/` | ESP32/Arduino |
| Database | `backup.sql` | MariaDB (`Alcohol_System`, InnoDB, utf8mb4) — schema + data dump |
| Ops/tools | `tools/`, `docs/` | check scripts, runbooks |

## Verify commands

- Backend: `python3 -m pytest Backend/tests/` (pytest in `Backend/requirements-dev.txt`)
- Frontend lint: `npm run lint` (in `Frontend/`)
- Frontend build: `npm run build` (in `Frontend/`)
- Fleet smoke test: regenerate the sandbox with `bash tools/agent_smoke/build_sandbox.sh`,
  score per `tools/agent_smoke/ground_truth.md`

## Conventions

- Frontend: JSX components, named exports, config objects in `src/config/` (see
  `deviceMode.js`, `timing.js`); never compile in a mode where the UI asks for one.
- Backend: route handlers thin; decision/domain logic in `models/`; every
  trigger/indicator change must keep `tests/test_trigger_isolation.py`,
  `tests/test_indicator_hooks.py`, and `tests/test_device_mode.py` green.
- Firmware: non-blocking loops (`millis()` state machines); relay must stay
  fail-safe (de-energized default); indicator LED semantics documented in
  `docs/INDICATORS_WIRING.md` — read them before changing gates.
- No comments unless non-obvious; no new dependencies without justification.
- Never claim tests/builds pass without real execution output.
