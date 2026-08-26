# Verification scripts

Three standalone checks used throughout development. They are **not yet pytest**
— converting them, with fixtures for isolation, is the top pending task in
`CLAUDE.md`.

Run from the project root, in this order:

```bash
python seed.py                      # REQUIRED first: see the warning below
python tests/verify_constraints.py  # re-audits every assignment from scratch
python tests/verify_workflows.py    # 43 end-to-end checks
python tests/verify_routes.py       # renders every route as all four roles
```

| Script | What it proves |
|---|---|
| `verify_constraints.py` | Independently re-checks every live assignment against all hard rules, without using the engine's own code. Also prints per-department fairness. |
| `verify_workflows.py` | Login, roster generation, publication and notifications, leave approval driving re-optimisation, the attendance lifecycle, override rejection, audit tampering detection. |
| `verify_routes.py` | Every GET route as Admin, HR, Manager and Staff, confirming RBAC denies correctly. |

> **`verify_workflows.py` mutates the database and is not idempotent.** It
> creates and publishes a roster, so a second run against the same database
> fails five checks spuriously. Always `python seed.py` immediately before it.

Expected on a fresh seed: **0 violations**, **43/43 checks**, all routes as
expected.

`verify_routes.py` reports `/dashboard/api/presence` as 404 — that path is
wrong in the script; the real endpoint is `/api/presence` and it works. Harmless,
worth fixing during the pytest conversion.
