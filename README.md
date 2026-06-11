# OpenBooks — Arizona Checkbook Analytics

Analytics layer over the State of Arizona OpenBooks checkbook (FY2016–2025),
built for municipal-bondholder due diligence. Turns ~$476B of raw cash-basis
spending into a tiered, human-curated review queue.

> **Data contract:** every tier, marker, and flag is a **lead warranting
> confirmation — never a finding of fraud or wrongdoing.** No entity is
> accused. Cash-basis checkbook ≠ audited GAAP; the ACFR governs.
> This disclaimer must travel with every surfaced row.

## Architecture

```
parquet/  (typed source of truth, 2.4 GB)
   │  sql/ pipeline (deterministic DuckDB DAG — see INTEGRATION_BRIEF §7)
   ▼
warehouse.duckdb  (tx_tiered, tier_entities, rollups…)
   │
   ▼
openbooks/        ← the Python package (this is what you embed)
 ├── _sql.py        canonical entity-key normalization (single source)
 ├── db.py          engine seam: DuckDBBackend / PostgresBackend
 ├── bootstrap.py   one-time schema setup (verdicts table + views)
 ├── queries.py     OpenBooks — the 9-method query API
 ├── server.py      stdlib HTTP wrapper (localhost / small deployments)
 └── __main__.py    CLI: python -m openbooks <command>
index.html        ← single-file SPA consuming /api/*
```

## Quick start (local, DuckDB)

```bash
pip install -e .                                    # or: pip install -r requirements.txt
python -m openbooks.bootstrap --duckdb warehouse.duckdb   # once per database
python -m openbooks.server                          # → http://127.0.0.1:8765
```

CLI queries:

```bash
python -m openbooks entity FONDOMONTE
python -m openbooks leads --tier 1 --status genuine_review
python -m openbooks waterfall
```

Library use:

```python
from openbooks import OpenBooks

with OpenBooks("warehouse.duckdb") as ob:          # read-only by default
    ob.entity("FONDOMONTE")
    ob.agency_card("DEPT OF TRANSPORTATION")

# Verdict curation needs a writable handle:
with OpenBooks("warehouse.duckdb", writable=True) as ob:
    ob.set_verdict("FONDOMONTE ARIZONA LLC", "genuine_review", 5, "…")
```

## Deploying against Postgres

The query layer emits ANSI-portable SQL through a backend seam, so the
**same `OpenBooks` class** runs against Postgres:

```bash
pip install -e '.[postgres]'
```

```python
ob = OpenBooks.from_postgres("postgresql://app@dbhost/openbooks",
                             mart_dir="/srv/openbooks/mart")
```

### Migration steps

1. **Move the tables.** The read path needs: `tx_tiered`, `tier_entities`,
   `tier_agency_summary`, `tier_agency_year`, `tier_program_summary`.
   Easiest path — DuckDB's Postgres extension pushes them directly:

   ```sql
   -- inside duckdb warehouse.duckdb
   INSTALL postgres; LOAD postgres;
   ATTACH 'postgresql://app@dbhost/openbooks' AS pg (TYPE postgres);
   CREATE TABLE pg.tx_tiered AS SELECT * FROM tx_tiered;
   CREATE TABLE pg.tier_entities AS SELECT * FROM tier_entities;
   CREATE TABLE pg.tier_agency_summary AS SELECT * FROM tier_agency_summary;
   CREATE TABLE pg.tier_agency_year AS SELECT * FROM tier_agency_year;
   CREATE TABLE pg.tier_program_summary AS SELECT * FROM tier_program_summary;
   ```

2. **Bootstrap** (creates `vendor_verdicts` + the `tx_with_verdict` view):

   ```bash
   python -m openbooks.bootstrap --postgres postgresql://app@dbhost/openbooks
   ```

3. **Ship `mart/entity_crosswalk.csv`** alongside the app and point
   `mart_dir` at it (it is a build artifact read at runtime for the
   "names merged" panel; missing file degrades gracefully).

4. **Dialect notes** (kept deliberately small):
   - `fired_markers` is a DuckDB `VARCHAR[]`; it maps to Postgres
     `text[]` automatically via the ATTACH copy. `array_to_string`
     exists in both engines.
   - `mode()` (used in one `agency_card` rollup) is an ordered-set
     aggregate in Postgres: if the copy doesn't carry it, replace with
     `mode() WITHIN GROUP (ORDER BY payee)`. This is the **only** known
     dialect divergence; everything else is ANSI.
   - Refresh stays in DuckDB. Postgres serves reads; each pipeline
     rebuild re-pushes tables (truncate-and-replace per
     INTEGRATION_BRIEF §7).

## HTTP API

| Route | Method | Params |
|---|---|---|
| `/api/entity` | GET | `q` (name or key) |
| `/api/leads` | GET | `tier`, `status`, `agency`, `limit` |
| `/api/agency` | GET | `q` |
| `/api/explain` | GET | `q` (transaction_id) |
| `/api/search` | GET | `q` |
| `/api/waterfall` | GET | — |
| `/api/pending` | GET | `limit` |
| `/api/health` | GET | — |
| `/api/verdict` | POST | JSON `{entity_key, verdict, overtaker_interest, public_context, recommended_action}` |

Errors return JSON `{"error": …}` with proper status codes (400/404/500).
CORS is **off by default**; enable with `--cors <origin>` or `OPENBOOKS_CORS`.

## Tests

```bash
pip install -e '.[dev]'
pytest
```

Golden-number tests assert the verified counts from `INTEGRATION_BRIEF.md`
(306,604 tiered transactions; Tier-1 = 1,094 / $5.05B; 1,700 entities; …)
against the live warehouse, plus SQL↔Python parity of the entity-key
normalization — the load-bearing join contract.

## Repository layout notes

- Data artifacts (`parquet/`, `*.duckdb`, raw CSVs) are **git-ignored**;
  the repo carries code, config, docs, and the mart CSVs only.
- `tier_config.yaml` is the single source of truth for thresholds,
  marker scores, category multipliers, and allowlists.
- `query.py` / `server.py` at the repo root are thin compatibility
  shims; new code should import from `openbooks`.
- `overtaker_handoff/` is the static feed bundle for integrators —
  see `INTEGRATION_BRIEF.md` inside it.
