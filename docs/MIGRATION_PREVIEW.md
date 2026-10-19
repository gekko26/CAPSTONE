# Migration Preview — what runs when the backend next starts

Read-only preview produced **before** any backend restart.
No statement below was executed. No write was issued to the live database.

- Engine: MariaDB 13.0.2, schema `Alcohol_System`
- Preview produced from the **shipped planner** `_plan_migrations` in `Backend/main.py`,
  extracted via AST so that `main.py` itself was never imported (importing it would run DDL).
- Method: **computed by running the real planner** against a fake inspector built from live
  `information_schema` reads.

---

## 1. What runs at import

`Backend/main.py` executes DDL as a side effect of being imported:

| Line | Statement | When |
|---|---|---|
| `main.py:9` | `models.Base.metadata.create_all(bind=engine)` | at import |
| `main.py:94` | `_auto_migrate()` → `_plan_migrations(...)` then execute each | at import |

There is **no** separate startup migration. Importing `main.py` is the migration.
Anything that imports it — the ASGI server, a script, a notebook — runs it.

## 2. The planned statement list

| # | Table | Statement | Effect | Reversible | Can fail on current data |
|---|---|---|---|---|---|
| — | — | **NONE** | — | — | — |

**The planner returned 0 statements. The live schema is fully converged.**

Evidence: the four columns/types the planner tests for are already present at their
target definitions, and the one constraint it adds already exists.

| Column the planner checks | Live type | Target | Status |
|---|---|---|---|
| `sensor_readings.denial_reason` | `varchar(64)` | `VARCHAR(64)` | already at target |
| `deployment_logs.denial_reason` | `varchar(64)` | `VARCHAR(64)` | already at target |
| `training_data.auto_labeled` | `tinyint(1)` | present | present |
| `training_data.label_confirmed` | `tinyint(1)` | present | present |
| `*.height_offset_cm` (3 tables) | `float` | present | present |
| `deployment_logs.reading_id` | `int(11)` | present | present |

| Constraint the planner checks | Live state |
|---|---|
| `deployment_logs.reading_id → sensor_readings.id` | **already present** as `fk_deployment_logs_reading` |

Foreign keys currently on the live schema:

| Table | Constraint | Column | References |
|---|---|---|---|
| `deployment_logs` | `fk_deployment_logs_subject` | `subject_id` | `subjects` |
| `deployment_logs` | `fk_deployment_logs_reading` | `reading_id` | `sensor_readings` |
| `sensor_readings` | `sensor_readings_ibfk_1` | `subject_id` | `subjects` |
| `training_data` | `fk_training_data_subject` | `subject_id` | `subjects` |

## 3. Had the FK still been missing — could it have failed?

The planner emits `ALTER TABLE deployment_logs ADD CONSTRAINT fk_deployment_logs_reading
FOREIGN KEY (reading_id) REFERENCES sensor_readings(id)`. Adding an FK fails if any existing
row violates the reference. Read-only orphan check:

| Check | Value | Meaning |
|---|---|---|
| `deployment_logs` rows total | 0 | nothing to violate |
| rows with `reading_id` NOT NULL | 0 | nothing to violate |
| rows pointing at a **missing** reading (would **block** the FK) | 0 | **FK would have succeeded** |
| duplicate `reading_id` values (would block the unique index) | 0 | **index would have succeeded** |
| `sensor_readings` rows (FK target table) | 0 | target table exists and is non-empty-safe |

Both tables are empty, so **no data-side failure was possible** in any case.

## 4. SAFE TO START BACKEND: **YES**

Reasons:

1. The shipped planner, run against the real schema, returns **zero** DDL statements.
2. Therefore `_auto_migrate` will execute nothing and cannot fail.
3. `create_all` remains a no-op: every table the ORM declares already exists with
   matching column definitions (verified in Phase 0).
4. Even if the FK were absent, both tables are empty so the `ADD CONSTRAINT` would succeed.
5. No lock contention risk — no DDL means no metadata lock is taken.

**Verdict: starting the backend will not modify the schema in any way.**

## 5. Read-only queries executed to produce this preview

All via `mysql --defaults-file="$HOME/.my.cnf"`:

1. `SELECT 'training_data',COUNT(*) FROM training_data UNION ALL …` (×4 tables)
2. `information_schema.COLUMNS` — `TABLE_NAME,COLUMN_NAME,COLUMN_TYPE,IS_NULLABLE`
3. `information_schema.KEY_COLUMN_USAGE` — constrained columns with a referenced table
4. `information_schema.COLUMNS` — full column/type map for the fake inspector
5. `SELECT COUNT(*) FROM deployment_logs` (+ `WHERE reading_id IS NOT NULL`)
6. `LEFT JOIN` orphan count against `sensor_readings`
7. `GROUP BY reading_id HAVING COUNT(*)>1` duplicate count
8. `SELECT COUNT(*) FROM sensor_readings`

Every one is a `SELECT` or an `information_schema` read. No `INSERT`, `UPDATE`, `DELETE`,
`ALTER`, `DROP`, `TRUNCATE` or `CREATE` was issued at any point.

## 6. Caveat

This preview reflects the schema **at the time it was generated**. If another process
applies DDL afterwards, re-run the preview before starting the backend.
The `_plan_migrations` logic is idempotent, so a repeat run is harmless.