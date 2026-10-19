#!/usr/bin/env python3
"""Step 5 demo data generator (Northbridge Community Clinics, fictional).

Writes separate files for opening data, scenario actions, and expected checkpoints.
Every scenario is replayed from the opening data. Expected values are hand-written
in this file; the replay must agree with them or the generator stops.
"""
import copy
import datetime as dt
import json
import pathlib

HERE = pathlib.Path(__file__).resolve().parent
EVAL = dt.date(2026, 10, 18)  # 2026-10-18 09:00 America/New_York (13:00Z)
SOON_DAYS = 60


def D(s):
    return dt.date.fromisoformat(s) if s else None


LOCATIONS = {"RFC": "Riverside Family Clinic", "HHC": "Hillcrest Health Center",
             "EWC": "Eastgate Walk-In Clinic", "CSR": "Central Supply Room"}
AREAS = [{"location": "RFC", "area": "Main storage"}, {"location": "HHC", "area": "Main storage"},
         {"location": "EWC", "area": "Main storage"}, {"location": "CSR", "area": "Main storage"},
         {"location": "CSR", "area": "Reserve storage"}]
NO_EXP_NOTE = "No expiration date is an invented property of this demo product."
PRODUCTS = {
    "P-001": {"name": "Nitrile gloves, size M", "unit": "box", "expiration_required": False, "note": NO_EXP_NOTE},
    "P-002": {"name": "Alcohol prep pads", "unit": "box", "expiration_required": True},
    "P-003": {"name": "Disposable specimen containers, 4 oz", "unit": "each", "expiration_required": False, "note": NO_EXP_NOTE},
    "P-004": {"name": "Gauze pads, 4x4", "unit": "pack", "expiration_required": False, "note": NO_EXP_NOTE},
    "P-005": {"name": "Tuberculin syringe, 1 mL", "unit": "each", "expiration_required": True},
}
USERS = {
    "counter_rfc": {"role": "counter", "location": "RFC"},
    "manager_rfc": {"role": "manager", "location": "RFC"},
    "counter_hhc": {"role": "counter", "location": "HHC"},
    "counter_ewc": {"role": "counter", "location": "EWC"},
    "manager_all": {"role": "manager", "location": "ALL"},
}
THRESHOLDS = [
    {"product": "P-001", "location": "RFC", "threshold": 20},
    {"product": "P-001", "location": "HHC", "threshold": 0},
    {"product": "P-003", "location": "HHC", "threshold": 0},
    {"product": "P-004", "location": "EWC", "threshold": 20},
    {"product": "P-005", "location": "EWC", "threshold": 5},
]
OPENING = [
    {"id": "O01", "product": "P-003", "lot": "SC-2611", "expiration": None, "location": "RFC", "area": "Main storage", "qty": 120},
    {"id": "O02", "product": "P-003", "lot": "SC-2611", "expiration": None, "location": "HHC", "area": "Main storage", "qty": 80},
    {"id": "O03", "product": "P-002", "lot": "AP-2026A", "expiration": "2026-11-20", "location": "RFC", "area": "Main storage", "qty": 40},
    {"id": "O04", "product": "P-002", "lot": "AP-2027B", "expiration": "2027-06-30", "location": "RFC", "area": "Main storage", "qty": 60},
    {"id": "O05", "product": "P-005", "lot": "TB-EXP-YDAY", "expiration": "2026-10-17", "location": "EWC", "area": "Main storage", "qty": 10},
    {"id": "O06", "product": "P-005", "lot": "TB-EXP-TODAY", "expiration": "2026-10-18", "location": "EWC", "area": "Main storage", "qty": 8},
    {"id": "O07", "product": "P-005", "lot": "TB-PLUS60", "expiration": "2026-12-17", "location": "HHC", "area": "Main storage", "qty": 15},
    {"id": "O08", "product": "P-005", "lot": "TB-PLUS61", "expiration": "2026-12-18", "location": "HHC", "area": "Main storage", "qty": 15},
    {"id": "O09", "product": "P-004", "lot": "GZ-LOT-1", "expiration": None, "location": "EWC", "area": "Main storage", "qty": 20},
    {"id": "O10", "product": "P-001", "lot": "NG-LOT-1", "expiration": None, "location": "RFC", "area": "Main storage", "qty": 21},
    {"id": "O11", "product": "P-001", "lot": "NG-LOT-2", "expiration": None, "location": "HHC", "area": "Main storage", "qty": 10},
    {"id": "O12", "product": "P-003", "lot": "SC-3001", "expiration": None, "location": "CSR", "area": "Reserve storage", "qty": 200},
]


def line(lid, product, lot, expected, pending=None):
    d = {"line": lid, "product": product, "lot": lot, "expected": expected}
    if pending:
        d["pending_expiration"] = pending
    return d


def att(lid, n, counted):
    return {"line": lid, "attempt_no": n, "counted": counted}


RFC_SNAPSHOT = [line("L1", "P-003", "SC-2611", 120), line("L2", "P-002", "AP-2026A", 40),
                line("L3", "P-002", "AP-2027B", 60), line("L4", "P-001", "NG-LOT-1", 21)]

P1_ACTIONS = [
    {"id": "A01", "op": "count_complete", "count": "C-RFC-01", "location": "RFC", "area": "Main storage",
     "counted_by": "counter_rfc",
     "lines": RFC_SNAPSHOT + [line("L5", "P-002", "AP-2028C", 0, pending="2028-01-31")],
     "attempts": [att("L1", 1, 120), att("L2", 1, 37), att("L3", 1, 60), att("L4", 1, 21),
                  att("L5", 1, 2), att("L2", 2, 39)],
     "approvals": []},
    {"id": "A02", "op": "issue_fefo", "product": "P-002", "location": "RFC", "area": "Main storage", "qty": 10},
    {"id": "A03", "op": "issue_fefo", "product": "P-001", "location": "HHC", "area": "Main storage", "qty": 10},
]


def bal(product, lot, location, area, **expect):
    return {"where": {"product": product, "lot": lot, "location": location, "area": area}, "expect": expect}


def loc_total(product, location, on_hand):
    return {"where": {"product": product, "location": location}, "expect": {"on_hand": on_hand}}


def cat(product, lot, category):
    return {"where": {"product": product, "lot": lot}, "expect": {"category": category}}


def low(product, location, flagged):
    return {"where": {"product": product, "location": location}, "expect": {"low": flagged}}


def cp(after, check, c):
    return dict(after=after, check=check, **c)


P1_CHECKPOINTS = [
    cp("opening", "balance", bal("P-003", "SC-2611", "RFC", "Main storage", on_hand=120, held=0, available=120)),
    cp("opening", "balance", bal("P-003", "SC-2611", "HHC", "Main storage", on_hand=80, available=80)),
    cp("opening", "location_total", loc_total("P-002", "RFC", 100)),
    cp("opening", "location_total", loc_total("P-003", "CSR", 200)),
    cp("opening", "category", cat("P-005", "TB-EXP-YDAY", "expired")),
    cp("opening", "category", cat("P-005", "TB-EXP-TODAY", "expired")),
    cp("opening", "category", cat("P-005", "TB-PLUS60", "expiring_soon")),
    cp("opening", "category", cat("P-005", "TB-PLUS61", "none")),
    cp("opening", "category", cat("P-002", "AP-2026A", "expiring_soon")),
    cp("opening", "balance", bal("P-005", "TB-EXP-TODAY", "EWC", "Main storage", on_hand=8, available=0)),
    cp("opening", "balance", bal("P-005", "TB-PLUS61", "HHC", "Main storage", available=15)),
    cp("A01", "balance", bal("P-002", "AP-2026A", "RFC", "Main storage", on_hand=39)),
    cp("A01", "balance", bal("P-002", "AP-2027B", "RFC", "Main storage", on_hand=60)),
    cp("A01", "balance", bal("P-002", "AP-2028C", "RFC", "Main storage", on_hand=2, available=2)),
    cp("A01", "location_total", loc_total("P-002", "RFC", 101)),
    cp("A01", "balance", bal("P-003", "SC-2611", "RFC", "Main storage", on_hand=120)),
    cp("A01", "balance", bal("P-001", "NG-LOT-1", "RFC", "Main storage", on_hand=21)),
    cp("A02", "balance", bal("P-002", "AP-2026A", "RFC", "Main storage", on_hand=29)),
    cp("A02", "location_total", loc_total("P-002", "RFC", 91)),
    cp("A03", "balance", bal("P-001", "NG-LOT-2", "HHC", "Main storage", on_hand=0, available=0)),
    cp("A03", "low", low("P-001", "HHC", True)),
    cp("A03", "low", low("P-003", "HHC", False)),
    cp("A03", "low", low("P-005", "EWC", True)),
    cp("A03", "low", low("P-004", "EWC", True)),
    cp("A03", "low", low("P-001", "RFC", False)),
    cp("A03", "balance", bal("P-005", "TB-EXP-TODAY", "EWC", "Main storage", available=0)),
]

HOLD_TRANSFER = {
    "id": "hold_transfer",
    "actions": [
        {"id": "A01", "op": "hold", "hold": "H-1", "product": "P-003", "lot": "SC-2611", "location": "HHC", "area": "Main storage", "qty": 30},
        {"id": "A02", "op": "release", "hold": "H-1", "qty": 10},
        {"id": "A03", "op": "discard", "hold": "H-1", "qty": 5},
        {"id": "A04", "op": "transfer_dispatch", "line": "T-1", "product": "P-003", "lot": "SC-2611", "location": "RFC", "area": "Main storage", "qty": 25},
        {"id": "A05", "op": "transfer_receive", "line": "T-1", "location": "EWC", "area": "Main storage", "qty": 15},
        {"id": "A06", "op": "transfer_return_dispatch", "from_line": "T-1", "to_line": "T-2", "qty": 10},
        {"id": "A07", "op": "transfer_receive", "line": "T-2", "location": "RFC", "area": "Main storage", "qty": 10},
    ],
    "checkpoints": [
        cp("A01", "hold", {"where": {"hold": "H-1"}, "expect": {"remaining": 30, "status": "active"}}),
        cp("A01", "balance", bal("P-003", "SC-2611", "HHC", "Main storage", on_hand=80, held=30, available=50)),
        cp("A02", "hold", {"where": {"hold": "H-1"}, "expect": {"remaining": 20, "status": "active"}}),
        cp("A02", "balance", bal("P-003", "SC-2611", "HHC", "Main storage", on_hand=80, held=20, available=60)),
        cp("A03", "hold", {"where": {"hold": "H-1"}, "expect": {"remaining": 15, "status": "active"}}),
        cp("A03", "balance", bal("P-003", "SC-2611", "HHC", "Main storage", on_hand=75, held=15, available=60)),
        cp("A04", "balance", bal("P-003", "SC-2611", "RFC", "Main storage", on_hand=95)),
        cp("A04", "transit", {"where": {"line": "T-1"}, "expect": {"in_transit": 25}}),
        cp("A05", "balance", bal("P-003", "SC-2611", "EWC", "Main storage", on_hand=15)),
        cp("A05", "transit", {"where": {"line": "T-1"}, "expect": {"in_transit": 10}}),
        cp("A06", "transit", {"where": {"line": "T-1"}, "expect": {"in_transit": 0}}),
        cp("A06", "transit", {"where": {"line": "T-2"}, "expect": {"in_transit": 10}}),
        cp("A06", "balance", bal("P-003", "SC-2611", "RFC", "Main storage", on_hand=95)),
        cp("A06", "balance", bal("P-003", "SC-2611", "EWC", "Main storage", on_hand=15)),
        cp("A07", "balance", bal("P-003", "SC-2611", "RFC", "Main storage", on_hand=105)),
        cp("A07", "balance", bal("P-003", "SC-2611", "EWC", "Main storage", on_hand=15)),
        cp("A07", "transit", {"where": {"line": "T-2"}, "expect": {"in_transit": 0}}),
    ],
}

APPROVAL_HHC = {
    "id": "approval_hhc",
    "actions": [
        {"id": "A01", "op": "count_complete", "count": "C-HHC-01", "location": "HHC", "area": "Main storage",
         "counted_by": "counter_hhc",
         "lines": [line("L1", "P-003", "SC-2611", 80), line("L2", "P-005", "TB-PLUS60", 15),
                   line("L3", "P-005", "TB-PLUS61", 15), line("L4", "P-001", "NG-LOT-2", 10)],
         "attempts": [att("L1", 1, 75), att("L2", 1, 12), att("L3", 1, 15), att("L4", 1, 10)],
         "approvals": [{"line": "L1", "approver": "manager_all", "decision": "approved"},
                       {"line": "L2", "approver": "manager_all", "decision": "approved"}]},
    ],
    "checkpoints": [
        cp("A01", "balance", bal("P-003", "SC-2611", "HHC", "Main storage", on_hand=75)),
        cp("A01", "balance", bal("P-005", "TB-PLUS60", "HHC", "Main storage", on_hand=12)),
        cp("A01", "balance", bal("P-005", "TB-PLUS61", "HHC", "Main storage", on_hand=15)),
        cp("A01", "executions", {"where": {"count": "C-HHC-01"}, "expect": {"executions": 2}}),
    ],
}

SURPLUS_RFC = {
    "id": "surplus_rfc",
    "actions": [
        {"id": "A01", "op": "count_complete", "count": "C-RFC-02", "location": "RFC", "area": "Main storage",
         "counted_by": "counter_rfc",
         "lines": RFC_SNAPSHOT + [line("L5", "P-002", "AP-2028C", 0, pending="2028-01-31")],
         "attempts": [att("L1", 1, 120), att("L2", 1, 40), att("L3", 1, 60), att("L4", 1, 21), att("L5", 1, 24)],
         "approvals": [{"line": "L5", "approver": "manager_rfc", "decision": "approved"}]},
    ],
    "checkpoints": [
        cp("A01", "balance", bal("P-002", "AP-2028C", "RFC", "Main storage", on_hand=24)),
        cp("A01", "location_total", loc_total("P-002", "RFC", 124)),
        cp("A01", "executions", {"where": {"count": "C-RFC-02"}, "expect": {"executions": 1}}),
    ],
}

P2_SCENARIOS = [HOLD_TRANSFER, APPROVAL_HHC, SURPLUS_RFC]


class Ledger:
    def __init__(self):
        self.lots, self.bal, self.transit, self.transit_info = {}, {}, {}, {}
        self.holds, self.executions = {}, []

    # ----- basics -----
    def register_lot(self, product, lot, exp):
        k = (product, lot)
        if k in self.lots:
            assert self.lots[k] == exp, f"lot {k} has conflicting expiration"
        else:
            self.lots[k] = exp

    def add(self, k, delta):
        b = self.bal.setdefault(k, {"on": 0, "held": 0})
        b["on"] += delta
        assert b["on"] >= 0, f"negative on-hand at {k}"
        assert b["held"] <= b["on"], f"held exceeds on-hand at {k}"

    def available(self, k):
        b = self.bal.get(k)
        if not b:
            return 0
        exp = self.lots[(k[0], k[1])]
        if exp is not None and exp <= EVAL:
            return 0
        return b["on"] - b["held"]

    def category(self, product, lot):
        exp = self.lots[(product, lot)]
        if exp is None:
            return "none"
        if exp <= EVAL:
            return "expired"
        if (exp - EVAL).days <= SOON_DAYS:
            return "expiring_soon"
        return "none"

    def key(self, a):
        return (a["product"], a["lot"], a["location"], a["area"])

    def load_opening(self):
        for o in OPENING:
            self.register_lot(o["product"], o["lot"], D(o["expiration"]))
            self.add((o["product"], o["lot"], o["location"], o["area"]), o["qty"])

    # ----- operations -----
    def issue_fefo(self, a):
        keys = [k for k in self.bal if k[0] == a["product"] and k[2] == a["location"]
                and k[3] == a["area"] and self.available(k) > 0]
        keys.sort(key=lambda k: (self.lots[(k[0], k[1])] is None,
                                 self.lots[(k[0], k[1])] or dt.date.max))
        left = a["qty"]
        for k in keys:
            take = min(left, self.available(k))
            self.add(k, -take)
            left -= take
            if left == 0:
                break
        assert left == 0, f"{a['id']}: insufficient available stock"

    def unheld(self, k):
        b = self.bal.get(k)
        return b["on"] - b["held"] if b else 0

    def hold(self, a):
        k = self.key(a)
        # Holds check unheld on-hand, not availability: expired stock may be held (Step 3 rule).
        assert self.unheld(k) >= a["qty"], f"{a['id']}: not enough unheld on-hand to hold"
        self.bal[k]["held"] += a["qty"]
        self.holds[a["hold"]] = {"key": k, "remaining": a["qty"], "status": "active"}

    def release(self, a):
        h = self.holds[a["hold"]]
        assert 0 < a["qty"] <= h["remaining"]
        self.bal[h["key"]]["held"] -= a["qty"]
        h["remaining"] -= a["qty"]
        h["status"] = "closed" if h["remaining"] == 0 else "active"

    def discard(self, a):
        h = self.holds[a["hold"]]
        assert 0 < a["qty"] <= h["remaining"]
        self.bal[h["key"]]["held"] -= a["qty"]
        self.add(h["key"], -a["qty"])
        h["remaining"] -= a["qty"]
        h["status"] = "closed" if h["remaining"] == 0 else "active"

    def transfer_dispatch(self, a):
        k = self.key(a)
        assert self.available(k) >= a["qty"]
        self.add(k, -a["qty"])
        self.transit[a["line"]] = self.transit.get(a["line"], 0) + a["qty"]
        self.transit_info[a["line"]] = (a["product"], a["lot"])

    def transfer_receive(self, a):
        t = self.transit.get(a["line"], 0)
        assert t >= a["qty"], f"{a['id']}: receive exceeds in-transit"
        self.transit[a["line"]] = t - a["qty"]
        p, lot = self.transit_info[a["line"]]
        self.add((p, lot, a["location"], a["area"]), a["qty"])

    def transfer_return_dispatch(self, a):
        t = self.transit.get(a["from_line"], 0)
        assert t >= a["qty"], f"{a['id']}: return exceeds in-transit"
        self.transit[a["from_line"]] = t - a["qty"]
        self.transit[a["to_line"]] = self.transit.get(a["to_line"], 0) + a["qty"]
        self.transit_info[a["to_line"]] = self.transit_info[a["from_line"]]

    def authorize(self, approval, count):
        counter, approver = USERS[count["counted_by"]], USERS[approval["approver"]]
        assert counter["role"] == "counter" and counter["location"] == count["location"]
        assert approver["role"] == "manager" and approver["location"] in (count["location"], "ALL")
        assert approval["approver"] != count["counted_by"], "approver must differ from counter"

    def count_complete(self, a):
        lines = {l["line"]: l for l in a["lines"]}
        for l in a["lines"]:
            k = (l["product"], l["lot"], a["location"], a["area"])
            on = self.bal.get(k, {"on": 0})["on"]
            assert on == l["expected"], f"{a['count']} {l['line']}: snapshot {on} != expected {l['expected']}"
            if "pending_expiration" in l:
                self.register_lot(l["product"], l["lot"], D(l["pending_expiration"]))
        covered = {(l["product"], l["lot"]) for l in a["lines"]}
        for k in self.bal:
            if k[2] == a["location"] and k[3] == a["area"]:
                assert (k[0], k[1]) in covered, f"{a['count']} does not cover {k[0]} {k[1]}"

        attempted = {t["line"] for t in a["attempts"]}
        unknown = attempted - set(lines)
        assert not unknown, f"{a['count']}: attempts for unknown lines {sorted(unknown)}"
        missing = [lid for lid in lines if lid not in attempted]
        assert not missing, f"{a['count']}: lines without an attempt: {missing}"

        approvals = {x["line"]: x for x in a["approvals"]}
        used, by_line = set(), {}
        for t in a["attempts"]:
            by_line.setdefault(t["line"], []).append(t)
        for lid, atts in by_line.items():
            l = lines[lid]
            atts.sort(key=lambda t: t["attempt_no"])
            assert [t["attempt_no"] for t in atts] == list(range(1, len(atts) + 1))
            tol = max(2, (2 * l["expected"]) // 100)
            k = (l["product"], l["lot"], a["location"], a["area"])
            for t in atts:
                var = t["counted"] - l["expected"]
                if var == 0 or t is not atts[-1]:
                    continue  # zero variance: no adjustment; earlier attempts: superseded, no balance effect
                if abs(var) > tol:
                    ap = approvals.get(lid)
                    assert ap and ap["decision"] == "approved", f"{a['count']} {lid}: variance {var} needs approval"
                    self.authorize(ap, a)
                    used.add(lid)
                    self.executions.append((a["count"], lid, ap["approver"]))
                self.add(k, var)
        assert used == set(approvals), "an approval has no matching pending adjustment"

    def apply(self, a):
        op = a["op"]
        if op == "count_complete": self.count_complete(a)
        elif op == "issue_fefo": self.issue_fefo(a)
        elif op == "hold": self.hold(a)
        elif op == "release": self.release(a)
        elif op == "discard": self.discard(a)
        elif op == "transfer_dispatch": self.transfer_dispatch(a)
        elif op == "transfer_receive": self.transfer_receive(a)
        elif op == "transfer_return_dispatch": self.transfer_return_dispatch(a)
        else: raise ValueError(op)

    def check(self, c):
        w, k = c["where"], c["check"]
        if k == "balance":
            key = (w["product"], w["lot"], w["location"], w["area"])
            b = self.bal.get(key, {"on": 0, "held": 0})
            got = {"on_hand": b["on"], "held": b["held"], "available": self.available(key)}
        elif k == "category":
            got = {"category": self.category(w["product"], w["lot"])}
        elif k == "location_total":
            got = {"on_hand": sum(v["on"] for kk, v in self.bal.items()
                                  if kk[0] == w["product"] and kk[2] == w["location"])}
        elif k == "low":
            avail = sum(self.available(kk) for kk in self.bal if kk[0] == w["product"] and kk[2] == w["location"])
            thr = [t["threshold"] for t in THRESHOLDS if t["product"] == w["product"] and t["location"] == w["location"]][0]
            got = {"low": avail <= thr}
        elif k == "transit":
            got = {"in_transit": self.transit.get(w["line"], 0)}
        elif k == "hold":
            h = self.holds[w["hold"]]
            got = {"remaining": h["remaining"], "status": h["status"]}
        elif k == "executions":
            got = {"executions": sum(1 for e in self.executions if e[0] == w["count"])}
        else:
            raise ValueError(k)
        return got


def replay(name, actions, checkpoints, ids_known):
    ledger = Ledger()
    ledger.load_opening()
    failures, ran = [], 0

    def run_checks(after):
        nonlocal ran
        for c in checkpoints:
            if c["after"] == after:
                got = ledger.check(c)
                ran += 1
                for field, want in c["expect"].items():
                    if got.get(field) != want:
                        failures.append(f"{name} after {after} {c['check']} {c['where']}: {field} = {got.get(field)}, expected {want}")

    run_checks("opening")
    for a in actions:
        ledger.apply(a)
        run_checks(a["id"])
    for c in checkpoints:
        assert c["after"] in ids_known, f"{name}: checkpoint refers to unknown step {c['after']}"
    assert not failures, "\n".join(failures)
    return ran


def focused_cases():
    done = []
    # Hold of expired stock: TB-EXP-TODAY (expired, 8 on hand at EWC) can be held in full.
    L = Ledger()
    L.load_opening()
    k = ("P-005", "TB-EXP-TODAY", "EWC", "Main storage")
    L.hold({"id": "F1", "hold": "H-X", "product": "P-005", "lot": "TB-EXP-TODAY",
            "location": "EWC", "area": "Main storage", "qty": 8})
    assert L.bal[k]["held"] == 8 and L.bal[k]["on"] == 8 and L.available(k) == 0
    try:
        L.hold({"id": "F2", "hold": "H-Y", "product": "P-005", "lot": "TB-EXP-TODAY",
                "location": "EWC", "area": "Main storage", "qty": 1})
        raise SystemExit("over-hold was accepted")
    except AssertionError:
        pass
    done.append("expired stock can be held in full; over-hold is still rejected")

    # Count completion with a missing line attempt must be rejected before any change.
    L = Ledger()
    L.load_opening()
    action = copy.deepcopy(P1_ACTIONS[0])
    action["attempts"] = [t for t in action["attempts"] if t["line"] != "L1"]
    try:
        L.count_complete(action)
        raise SystemExit("count with a missing attempt completed")
    except AssertionError as e:
        assert "lines without an attempt" in str(e), str(e)
    assert L.bal[("P-003", "SC-2611", "RFC", "Main storage")]["on"] == 120
    done.append("count completion rejects a line with no attempt, with no balance change")
    return done


def main():
    ids = lambda acts: {"opening"} | {a["id"] for a in acts}
    ran1 = replay("P1", P1_ACTIONS, P1_CHECKPOINTS, ids(P1_ACTIONS))
    ran2 = 0
    for s in P2_SCENARIOS:
        ran2 += replay(s["id"], s["actions"], s["checkpoints"], ids(s["actions"]))

    opening = {
        "fictional_demo": True,
        "evaluation": {"date": EVAL.isoformat(), "time": "09:00", "timezone": "America/New_York", "utc": "2026-10-18T13:00:00Z"},
        "soon_window_days": SOON_DAYS,
        "locations": LOCATIONS, "areas": AREAS, "products": PRODUCTS, "users": USERS,
        "reorder_thresholds": THRESHOLDS, "opening_balances": OPENING,
    }
    files = {
        "opening_p1.json": opening,
        "scenario_actions_p1.json": {"scenario": "P1 seed", "actions": P1_ACTIONS},
        "expected_checkpoints_p1.json": {"scenario": "P1 seed", "checkpoints": P1_CHECKPOINTS},
        "scenario_actions_p2.json": {"scenarios": [{"id": s["id"], "actions": s["actions"]} for s in P2_SCENARIOS]},
        "expected_checkpoints_p2.json": {"scenarios": [{"id": s["id"], "checkpoints": s["checkpoints"]} for s in P2_SCENARIOS]},
    }
    for name, data in files.items():
        (HERE / name).write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"P1 checkpoints verified: {ran1}; P2 checkpoints verified: {ran2}")
    for t in focused_cases():
        print("focused: " + t)
    print("wrote: " + ", ".join(files))


if __name__ == "__main__":
    main()
