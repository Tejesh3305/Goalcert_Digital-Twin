# work — dispatch: who fixes the fault the twin just found

The twin could always **detect**. It could not say who was told, who acted, or
what they learned. This package and `scenario/` are that missing half.

```
  behaviours          work/from_finding      work/service         scenario/
      │                      │                    │                   │
  telemetry ──▶ Finding ──▶ task (open) ──▶ assigned ──▶ in_progress ──▶ run
   (graph)      (graph)      │                 ▲             │            │
                             │            supervisor      operator    graded
                             │                                          │
                        resolved ◀── XP awarded ◀───────────────────────┘
                             │
                        closed ──▶ Finding marked resolved in the graph
```

The graph stays the source of truth for **what is wrong with the plant**. These
tables only record **what people did about it** — a task points at its Finding by
node id and copies nothing. Dropping every row here would lose the dispatch
history and not one byte of twin state.

## The two role vocabularies

This is the part most worth understanding before changing anything.

| | vocabulary | answers | lives in |
|---|---|---|---|
| `role` | owner / admin / write / read | what data may you touch | `identity/models.py` |
| `persona` | supervisor / frontline | what job do you do | `identity/models.py`, on `Membership` |

They are **orthogonal**, and collapsing them into one field is the change this
design exists to prevent. A supervisor who may only *read* the twin is an
ordinary account — they dispatch people, they do not edit the plant model. An
operator may well need *write*, because closing a job writes back. One column
cannot express both.

`role` still governs tenant and data access through `server/tenancy.py`, which
this package does not widen. `persona` governs dispatch only, via
`work/authority.py`.

## Authority: two gates, both required

```python
authority.can(persona, "task.assign")                    # 1. capability
authority.may_assign_to(org, actor, persona, assignee)   # 2. scope
```

A capability check alone lets **every** supervisor dispatch **every** operator in
the organisation. Teams are what bound that: `assignable_user_ids` returns the
membership of the teams a supervisor actually supervises, and a supervisor who
supervises nothing can assign to nobody — the correct failure direction.

The frontend renders from the same capability list (`GET /api/v1/work/me`), so a
button that is drawn is a button the API will honour.

## Getting to a working state

```bash
python -m db.migrations                       # 0005 persona, 0006 work tables
python -m work.seed --tenant <your-tenant-id>  # supervisor + operator + team
```

`--tenant` matters: `from_finding` resolves the org **by tenant**, so findings on
a tenant no organisation owns dispatch to nobody. That is the most easily missed
step, and it looks identical to a broken feature.

Sign in as each account in a separate browser profile — the supervisor gets
**Dispatch**, the operator gets **My Work**.

## Which findings become tasks

`work_rules` decides, as data rather than code. The default rule is
**critical only**, on purpose: a platform that raises a task for every warning
teaches operators to ignore tasks, and that habit is much harder to undo than a
missed notification.

Dedup is by Finding **node id** over **live** tasks only — one ongoing fault is
one task however long it persists, and a closed task does not suppress the same
fault recurring next month.

## XP

A ledger, not a counter. A total that can only be incremented cannot be
explained, audited or corrected; the total is a `SUM` and every point in it has a
row saying why. `(user_id, task_id, reason)` is unique, so a double award is
refused by the database rather than by a check-then-write race in Python.

```
award = base(severity) × quality(score) + bonuses
```

Every term comes from the run. A **failed** run still earns
`xp.CONSOLATION` — zeroing it out teaches people to abandon a run they think
they are losing, which destroys the signal the score exists to produce.

## Scoring is server-side

`Step.public()` omits the answer key and `scenario/guided.py` is the only module
that reads `Step.correct`. If the page held the answers, the score would be a
claim the browser made about itself — and the whole point of scoring a run is to
produce evidence somebody else can rely on. `tests/test_work_dispatch.py`
asserts no step payload ever carries `correct`.

## Files

| file | what it owns |
|---|---|
| `models.py` | the records, the task lifecycle, legal transitions |
| `authority.py` | capability matrix + team scope |
| `store.py` | every SQL statement against the work tables |
| `service.py` | the rules of the loop — routes call only this |
| `xp.py` | what a fix is worth, and what that makes you |
| `from_finding.py` | Finding → task, and task closed → Finding resolved |
| `seed.py` | a working supervisor/operator/team, idempotently |

Routes are `server/work_routes.py` and `server/scenario_routes.py`. Procedures
are `scenario/catalog.py`, mapped to the twin's real behaviour ids — every
behaviour in `behaviors/cfp` and `behaviors/hvac` has one, and a test enforces
that.

## The dashboards

Two screens read this package rather than driving it, and both are served by
aggregates rather than by the page assembling its own numbers.

| endpoint | who | what it answers |
|---|---|---|
| `GET /work/stats/me` | operator | status counts, a per-day completion series, score average, severity mix, streak |
| `GET /work/team-stats` | supervisor | per-operator load and throughput |
| `GET /work/roster?scope=` | supervisor | `team` = who you may assign to; `all` = every operator, each flagged `assignable` |

**One payload per screen, on purpose.** The operator's dashboard draws five
things from the same rows. Five endpoints would let them disagree the moment one
was slow — a KPI saying three open jobs above a list showing four is worse than
either number alone.

**`scope=all` widens the VIEW, never the authority.** `assign` still consults
`authority.assignable_user_ids`, so a row with `assignable: false` is somebody a
supervisor can see the load of and cannot dispatch to. Showing them greyed rather
than hiding them is what lets a supervisor notice a colleague's operator is
buried; hiding them made the boundary look like an empty organisation.
`tests/test_work_dashboard_api.py` pins both halves of that.

**No schema was added for any of it.** Every number here is derived from `tasks`,
`scenario_runs` and `xp_ledger`. A denormalised stats table would be a second
source of truth for figures the task rows already carry, and the first time the
two disagreed the table would be believed.

### A gap worth knowing about

`work/README.md` above describes a read-only supervisor as an ordinary account,
because dispatch is supposed to be governed by `persona` and not by `role`. Over
HTTP that is not true today: `server/auth.py` refuses every POST from a
read-only principal before any persona check runs, so a supervisor with
`role='read'` cannot assign. `work/seed.py` issues `write`, so the shipped
product works — but the two-axis design is not fully realised at the middleware,
and anyone provisioning a supervisor by hand needs to know it.
