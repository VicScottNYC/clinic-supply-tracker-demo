# Step 5: Demo Data

*Northbridge Community Clinics is a fictional demo organization. Product names, lot numbers, quantities, and policies are invented.*

## 5.1 Principles

- **Scenario-driven.** Every record demonstrates a rule from Step 3 or Step 4.
- **Three separate inputs.** Opening data, scenario actions, and expected checkpoints are stored in separate files. Expected values are written by hand. The generator replays the actions from the opening data and fails if any checkpoint disagrees, so the expected values are verified rather than trusted.
- **P1 and P2 are separate.** P2 scenarios each start from the opening data in their own isolated database.
- **Fixed evaluation time.** 2026-10-18 09:00 America/New_York (13:00Z). The local date is 2026-10-18.
- **Opening stock is posted as transactions** (`operation_type = 'opening'`). No balance is written directly.
- **Count tolerance** is MAX(2, (2 × expected) ÷ 100), using integer division. Variances are stored with their sign. Tolerance uses the absolute value.
- **Generator:** `demo_data/generate_demo_data.py`, which writes the files listed in 5.8.

## 5.2 Reference Data (P1)

**Locations and storage areas**

| Code | Name | Storage areas |
|---|---|---|
| RFC | Riverside Family Clinic | Main storage |
| HHC | Hillcrest Health Center | Main storage |
| EWC | Eastgate Walk-In Clinic | Main storage |
| CSR | Central Supply Room | Main storage, Reserve storage |

**Products**

| Code | Product | Base unit | Expiration required | Note |
|---|---|---|---|---|
| P-001 | Nitrile gloves, size M | box | No | No expiration date is an invented property of this demo product. |
| P-002 | Alcohol prep pads | box | Yes | |
| P-003 | Disposable specimen containers, 4 oz | each | No | No expiration date is an invented property of this demo product. |
| P-004 | Gauze pads, 4×4 | pack | No | No expiration date is an invented property of this demo product. |
| P-005 | Tuberculin syringe, 1 mL | each | Yes | |

**Reorder thresholds** (product, location, threshold in base units)

| Product | Location | Threshold | Purpose |
|---|---|---|---|
| P-001 | RFC | 20 | Available 21 → not low |
| P-001 | HHC | 0 | Available 0 → **low** (zero-available case) |
| P-003 | HHC | 0 | Available 80 → not low (zero threshold does not flag positive stock) |
| P-004 | EWC | 20 | Available 20 → low |
| P-005 | EWC | 5 | Low only because expired lots are excluded |

**Users** (P1 stores roles in `user_role`; enforcement is P2)

| User | Role | Location |
|---|---|---|
| counter_rfc | counter | RFC |
| manager_rfc | manager | RFC |
| counter_hhc | counter | HHC |
| counter_ewc | counter | EWC |
| manager_all | manager | All locations |

## 5.3 Opening Data (`opening_p1.json`)

| Product | Lot | Expiration | Location / area | Quantity |
|---|---|---|---|---:|
| P-003 | SC-2611 | none | RFC / Main | 120 |
| P-003 | SC-2611 | none | HHC / Main | 80 |
| P-002 | AP-2026A | 2026-11-20 | RFC / Main | 40 |
| P-002 | AP-2027B | 2027-06-30 | RFC / Main | 60 |
| P-005 | TB-EXP-YDAY | 2026-10-17 | EWC / Main | 10 |
| P-005 | TB-EXP-TODAY | 2026-10-18 | EWC / Main | 8 |
| P-005 | TB-PLUS60 | 2026-12-17 | HHC / Main | 15 |
| P-005 | TB-PLUS61 | 2026-12-18 | HHC / Main | 15 |
| P-004 | GZ-LOT-1 | none | EWC / Main | 20 |
| P-001 | NG-LOT-1 | none | RFC / Main | 21 |
| P-001 | NG-LOT-2 | none | HHC / Main | 10 |
| P-003 | SC-3001 | none | CSR / Reserve storage | 200 |

Twelve balances total 599 base units. This is a reconciliation checksum only, because products use different units. The earlier total of 589 across 11 balances changed when NG-LOT-2 was added for the zero-available case.

## 5.4 P1 Scenario Actions (`scenario_actions_p1.json`)

**A01: Count C-RFC-01, RFC Main storage, counted by counter_rfc.** The count covers all four balances in the area, so each has an attempt. Unchanged lines record zero variance and create no adjustment.

| Line | Product / lot | Expected | Attempts (counted) | Signed variance | Tolerance | Outcome |
|---|---|---:|---|---:|---:|---|
| L1 | P-003 SC-2611 | 120 | 1: 120 | 0 | 2 | No adjustment |
| L2 | P-002 AP-2026A | 40 | 1: 37, 2: 39 | 1: −3, 2: −1 | 2 | Attempt 1 (−3, out of tolerance) is superseded by attempt 2 with no decider. Attempt 2 (−1) is within tolerance and applies as `count_within`. |
| L3 | P-002 AP-2027B | 60 | 1: 60 | 0 | 2 | No adjustment |
| L4 | P-001 NG-LOT-1 | 21 | 1: 21 | 0 | 2 | No adjustment |
| L5 | P-002 AP-2028C (surplus) | 0 | 1: 2 | +2 | 2 | Within tolerance, applies as `count_within`. Lot AP-2028C is created at completion with pending expiration 2028-01-31. |

P-002 at RFC moves from 100 (opening) to **101** after completion (39 + 60 + 2). Each attempt with non-zero variance has exactly one adjustment. Zero-variance attempts have none.

**A02: Issue 10 of P-002 at RFC.** Earliest-expiring lot first: AP-2026A drops from 39 to 29. P-002 at RFC totals 91.

**A03: Issue 10 of P-001 at HHC.** NG-LOT-2 drops to 0 on hand and 0 available. Threshold 0 → **low**.

## 5.5 P1 Expected Checkpoints (`expected_checkpoints_p1.json`)

- **After opening:** P-003 SC-2611 at RFC 120 and at HHC 80; P-002 at RFC totals 100; P-003 at CSR totals 200; TB-EXP-YDAY and TB-EXP-TODAY expired; TB-PLUS60 expiring soon; TB-PLUS61 neither; AP-2026A expiring soon (33 days); TB-EXP-TODAY available 0.
- **After A01:** AP-2026A 39; AP-2027B 60; AP-2028C 2 (available 2); P-002 at RFC totals 101; SC-2611 at RFC 120 (unchanged); NG-LOT-1 at RFC 21 (unchanged).
- **After A02:** AP-2026A 29; P-002 at RFC totals 91.
- **After A03:** NG-LOT-2 at HHC 0 on hand and 0 available; P-001 at HHC **low**; P-003 at HHC **not low** (available 80, threshold 0); P-005 at EWC **low** (available 0, threshold 5); P-004 at EWC **low** (available 20, threshold 20); P-001 at RFC **not low** (available 21, threshold 20); TB-EXP-TODAY at EWC available 0.

## 5.6 P2 Scenarios (`scenario_actions_p2.json`, `expected_checkpoints_p2.json`)

These run in isolated databases that start from the opening data only.

### 5.6.1 `hold_transfer`: hold reconciliation and in-transit conservation

| Step | Operation | HHC SC-2611 on hand / held / available | RFC SC-2611 on hand | EWC SC-2611 on hand | Transfer line in transit |
|---|---|---|---|---|---|
| A01 | Hold H-1, 30 | 80 / 30 / 50 | 120 | — | — |
| A02 | Release 10 (H-1 remaining 20) | 80 / 20 / 60 | 120 | — | — |
| A03 | Discard 5 (H-1 remaining 15, active) | 75 / 15 / 60 | 120 | — | — |
| A04 | Dispatch 25 on line T-1 from RFC | 75 / 15 / 60 | 95 | — | T-1: 25 |
| A05 | Receive 15 on T-1 at EWC | 75 / 15 / 60 | 95 | 15 | T-1: 10 |
| A06 | Internal return: 10 moves from T-1 to return line T-2 | 75 / 15 / 60 | 95 | 15 | T-1: 0, T-2: 10 |
| A07 | Receive 10 on T-2 at RFC | 75 / 15 / 60 | 105 | 15 | T-2: 0 |

Checks: `held` ≤ `on_hand` after each step. Each transfer line satisfies `in_transit` = dispatched − received − returned_out − written_off. The internal return moves undelivered units from the original line; it does not reduce EWC on-hand.

### 5.6.2 `approval_hhc`: two approved adjustments in one count completion

Count C-HHC-01, HHC Main storage, counted by counter_hhc. The count covers all four HHC balances.

| Line | Product / lot | Expected | Counted | Variance | Tolerance | Decision |
|---|---|---:|---:|---:|---:|---|
| L1 | P-003 SC-2611 | 80 | 75 | −5 | 2 | Out of tolerance, approved by manager_all |
| L2 | P-005 TB-PLUS60 | 15 | 12 | −3 | 2 | Out of tolerance, approved by manager_all |
| L3 | P-005 TB-PLUS61 | 15 | 15 | 0 | 2 | No adjustment |
| L4 | P-001 NG-LOT-2 | 10 | 10 | 0 | 2 | No adjustment |

Expected: SC-2611 at HHC 75; TB-PLUS60 at HHC 12; **two** `approval_execution` rows in the one completion transaction. The approver (manager_all) differs from the counter (counter_hhc).

### 5.6.3 `surplus_rfc`: surplus above tolerance requires approval

Count C-RFC-02, RFC Main storage, counted by counter_rfc. Lines L1 to L4 match the opening data exactly. L5 is a surplus of **24** units of AP-2028C (expected 0, tolerance 2, variance +24), approved by manager_rfc.

Expected: AP-2028C at RFC 24; P-002 at RFC totals 124; one `approval_execution` row.

## 5.7 Coverage Map

| Rule (Step 3 / 4) | Covered by |
|---|---|
| Same lot at two locations | P-003 SC-2611 at RFC and HHC |
| Earliest-expiring selection | A02 (AP-2026A before AP-2027B) |
| Expiration boundaries | TB-EXP-YDAY, TB-EXP-TODAY, TB-PLUS60, TB-PLUS61 |
| Expired lots excluded from availability | TB-EXP-TODAY, P-005 at EWC |
| Thresholds: at, above, zero | P-004 at EWC (20 vs 20), P-001 at RFC (21 vs 20), P-003 at HHC (80 vs 0), P-001 at HHC (0 vs 0) |
| Count recount and supersession | A01 line L2 |
| Surplus lot created at completion | A01 line L5 (+2 within tolerance); `surplus_rfc` (+24 requires approval) |
| Count-generated adjustments | A01 (one per non-zero attempt) |
| Hold, release, discard | `hold_transfer` A01–A03 |
| Transfer and internal return | `hold_transfer` A04–A07 |
| Approval execution and exclusivity | `approval_hhc`, `surplus_rfc` |

## 5.8 Generator Output

| File | Contents |
|---|---|
| `opening_p1.json` | Evaluation time, locations, areas, products, users, thresholds, opening balances |
| `scenario_actions_p1.json` | P1 actions A01–A03 |
| `expected_checkpoints_p1.json` | P1 checkpoints by action |
| `scenario_actions_p2.json` | Three P2 scenarios |
| `expected_checkpoints_p2.json` | Checkpoints for each P2 scenario |

## 5.9 Verification Scope and Role Mapping

**What the generator replay verifies (P1: 26 checkpoints; P2: 24 checkpoints; 2 focused cases):**
- Quantities: on-hand, held, and available per balance; location totals; in-transit per transfer line; hold remaining and status.
- Classifications: expired, expiring soon, and none; low-stock flags at, above, and zero thresholds.
- Count rules: tolerance per line, signed variances, one adjustment per non-zero latest attempt, zero-variance lines create none, supersession of earlier out-of-tolerance attempts, every area line must have an attempt, and every balance in the area must be covered.
- Focused cases: expired stock can be held in full while over-hold is still rejected; a count with a missing line attempt is rejected with no balance change.

**What the replay does not yet verify:**
- Movement history (the `movement` ledger rows and their balance and transfer-line effects).
- Superseded adjustment records (`superseded_at`, `superseded_by_attempt_id`).
- `approval_execution` links and transaction grouping, beyond a count of executions per count.
- Count locking, submission-key idempotency, and rollback behavior.
- Database constraints. These are tested in the database-loading step.

**Role mapping** (fixture aliases to the model's actual roles):

| Fixture alias | Model meaning |
|---|---|
| `counter` | `user_role` row with role `counter` at the named location. Submits counts and holds transfer and hold actions. |
| `manager` | `user_role` row with role `manager` at the named location. Decides approvals. |
| `location ALL` | One `user_role` row per location (RFC, HHC, EWC, CSR), so `manager_all` holds `manager` at every location. |

The approver must differ from the counter who submitted the count. The replay enforces this.

## 5.10 Not Included

- Report output formats, and database loading (next step, after the generator is verified).
- Disposal of expired stock (a P2 action, not seeded here).
- Cost or value data (out of scope).
