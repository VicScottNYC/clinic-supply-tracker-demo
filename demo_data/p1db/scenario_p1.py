"""Runs the P1 scenario through the service layer only (no direct balance edits),
then checks the expected checkpoints and ledger reconciliation."""
import datetime as dt
import json
import uuid
from pathlib import Path

import inventory_p1 as svc
from loader_p1 import create_new_database, load_opening, DEMO_DIR

TODAY = dt.date(2026, 10, 18)  # 09:00 America/New_York
COUNTER_FOR = {"RFC": "counter_rfc", "HHC": "counter_hhc", "EWC": "counter_ewc"}


def key(*parts):
    return str(uuid.uuid5(svc.KEY_NS, ":".join(parts)))


def run_action(conn, scenario, a):
    if a["op"] == "issue_fefo":
        svc.issue(conn, COUNTER_FOR[a["location"]], idem=key(scenario, a["id"]), sku=a["product"],
                  loc=a["location"], area=a["area"], qty=a["qty"], today=TODAY)
    elif a["op"] == "count_complete":
        actor = a["counted_by"]
        cid = svc.open_count(conn, actor, idem=key(scenario, a["id"], "start"), loc=a["location"],
                             area=a["area"], today=TODAY)["count_id"]
        by_line = {l["line"]: l for l in a["lines"]}
        for l in a["lines"]:
            if "pending_expiration" in l:
                svc.add_count_line(conn, actor, idem=key(scenario, a["id"], l["line"], "add"), count_id=cid,
                                   sku=l["product"], lot=l["lot"], pending_exp=l["pending_expiration"], today=TODAY)
        for t in a["attempts"]:
            l = by_line[t["line"]]
            svc.submit_count(conn, actor, idem=key(scenario, a["id"], t["line"], str(t["attempt_no"])),
                             count_id=cid, sku=l["product"], lot=l["lot"], counted=t["counted"], today=TODAY)
        svc.complete_count(conn, actor, idem=key(scenario, a["id"], "complete"), count_id=cid, today=TODAY)
    else:
        raise ValueError(a["op"])


def _bal_rows(conn, sku, loc, lot=None):
    sql = """SELECT b.on_hand, b.held, l.expiration_date, l.lot_number FROM balance b
             JOIN lot l ON l.lot_id = b.lot_id JOIN product p ON p.product_id = l.product_id
             JOIN storage_area sa ON sa.storage_area_id = b.storage_area_id
             JOIN location lo ON lo.location_id = sa.location_id
             WHERE p.sku = ? AND lo.code = ?"""
    args = [sku, loc]
    if lot:
        sql += " AND l.lot_number = ?"
        args.append(lot)
    return conn.execute(sql, args).fetchall()


def _avail(row):
    on, held, exp, _ = row
    return 0 if svc.is_expired(exp, TODAY) else on - held


def evaluate(conn, c):
    w, k = c["where"], c["check"]
    if k == "balance":
        rows = _bal_rows(conn, w["product"], w["location"], w["lot"])
        row = rows[0] if rows else (0, 0, None, w["lot"])
        got = {"on_hand": row[0], "held": row[1], "available": _avail(row) if rows else 0}
    elif k == "category":
        exp = conn.execute("""SELECT l.expiration_date FROM lot l JOIN product p ON p.product_id = l.product_id
                              WHERE p.sku = ? AND l.lot_number = ?""", (w["product"], w["lot"])).fetchone()[0]
        if exp is None:
            cat = "none"
        elif svc.is_expired(exp, TODAY):
            cat = "expired"
        elif TODAY < dt.date.fromisoformat(exp) <= TODAY + dt.timedelta(days=60):
            cat = "expiring_soon"
        else:
            cat = "none"
        got = {"category": cat}
    elif k == "location_total":
        got = {"on_hand": sum(r[0] for r in _bal_rows(conn, w["product"], w["location"]))}
    elif k == "low":
        avail = sum(_avail(r) for r in _bal_rows(conn, w["product"], w["location"]))
        thr = conn.execute("""SELECT rt.min_qty FROM reorder_threshold rt JOIN product p ON p.product_id = rt.product_id
                              JOIN location lo ON lo.location_id = rt.location_id WHERE p.sku = ? AND lo.code = ?""",
                           (w["product"], w["location"])).fetchone()[0]
        got = {"low": avail <= thr}
    else:
        raise ValueError(k)
    return got


def reconcile(conn):
    """Balances must equal the sum of their movements. Returns mismatching balance ids."""
    return conn.execute("""SELECT b.balance_id FROM balance b WHERE
        b.on_hand <> (SELECT COALESCE(SUM(m.delta_on_hand), 0) FROM movement m WHERE m.balance_id = b.balance_id)
     OR b.held <> (SELECT COALESCE(SUM(m.delta_held), 0) FROM movement m WHERE m.balance_id = b.balance_id)""").fetchall()


def _checks(conn, checkpoints, after, failures):
    ran = 0
    for c in checkpoints:
        if c["after"] != after:
            continue
        ran += 1
        got = evaluate(conn, c)
        for field, want in c["expect"].items():
            if got.get(field) != want:
                failures.append(f"after {after} {c['check']} {c['where']}: {field} = {got.get(field)}, expected {want}")
    return ran


def run(db_path):
    conn = create_new_database(db_path)
    load_opening(conn)
    scen = json.loads((DEMO_DIR / "scenario_actions_p1.json").read_text(encoding="utf-8"))
    checkpoints = json.loads((DEMO_DIR / "expected_checkpoints_p1.json").read_text(encoding="utf-8"))["checkpoints"]
    failures = []
    ran = _checks(conn, checkpoints, "opening", failures)
    for a in scen["actions"]:
        run_action(conn, "P1", a)
        ran += _checks(conn, checkpoints, a["id"], failures)
    mismatches = reconcile(conn)
    return conn, ran, failures, mismatches


if __name__ == "__main__":
    import sys
    if len(sys.argv) != 2:
        sys.exit("usage: scenario_p1.py NEW_DB_PATH   (the path must not exist)")
    conn, ran, failures, mismatches = run(sys.argv[1])
    print(f"checkpoints checked: {ran}; failures: {len(failures)}; unreconciled balances: {len(mismatches)}")
    for f in failures:
        print("  " + f)
    conn.close()
    sys.exit(1 if failures or mismatches else 0)
