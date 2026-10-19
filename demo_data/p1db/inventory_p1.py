"""P1 service layer (SQLite). Every stock change is a keyed operation:
one write transaction, a submission key stored with its response, movements
appended, and cached balances updated in the same transaction."""
import datetime as dt
import hashlib
import json
import re
import sqlite3
import uuid
from contextlib import contextmanager
from pathlib import Path

NOW = "2026-10-18T13:00:00Z"  # loader only: fixed stamp so the opening data is reproducible


def utc_now():
    """Actual UTC event time, in the stored format."""
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _stamp(now):
    return now if now is not None else utc_now()
KEY_NS = uuid.UUID("6f1c2d3e-4a5b-4c6d-8e7f-0123456789ab")
SCHEMA_PATH = Path(__file__).with_name("schema_p1.sql")


class Rejected(Exception):
    def __init__(self, code, message):
        super().__init__(f"{code}: {message}")
        self.code, self.message = code, message


def connect_existing(path):
    """Opens an existing, initialized database read-write. Never creates a file."""
    from pathlib import Path as _P
    target = _P(path)
    if not target.is_file():
        raise FileNotFoundError(f"database not found: {path}")
    uri = target.resolve().as_uri() + "?mode=rw"
    try:
        conn = sqlite3.connect(uri, uri=True, isolation_level=None)
    except sqlite3.OperationalError as exc:
        raise FileNotFoundError(f"database cannot be opened: {path} ({exc})") from exc
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 5000")
    conn.execute("PRAGMA journal_mode = WAL")
    have = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    required = {"location", "product", "balance", "stock_transaction", "submission_key", "app_user"}
    if not required <= have:
        conn.close()
        raise RuntimeError(f"database is not an initialized P1 database: {path}")
    return conn


def connect(path):
    conn = sqlite3.connect(path, isolation_level=None)  # explicit transaction control
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 5000")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


@contextmanager
def write_tx(conn):
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    conn.execute("COMMIT")


def digest(obj):
    return hashlib.sha256(json.dumps(obj, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def is_expired(exp, today):
    return exp is not None and dt.date.fromisoformat(exp) <= today


def tolerance(expected):
    return max(2, (2 * expected) // 100)


def is_int(v):
    """True for real integers only: rejects floats, strings, and booleans (bool is a subclass of int)."""
    return isinstance(v, int) and not isinstance(v, bool)


def require_qty(v, field, minimum):
    if not is_int(v) or v < minimum:
        raise Rejected("BAD_QUANTITY", f"{field} must be an integer of at least {minimum}")


_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def parse_date(v, field):
    if not isinstance(v, str) or not _ISO_DATE.match(v):
        raise Rejected("BAD_DATE", f"{field} must be a YYYY-MM-DD date")
    try:
        return dt.date.fromisoformat(v)
    except ValueError:
        raise Rejected("BAD_DATE", f"{field} is not a real calendar date: {v}")


# ----- lookups and guards -----
def _active_actor(conn, actor):
    r = conn.execute("SELECT active FROM app_user WHERE user_id = ?", (actor,)).fetchone()
    if not r or not r[0]:
        raise Rejected("ACTOR", f"{actor} is not an active user")


def _area(conn, loc, area):
    r = conn.execute("""SELECT sa.storage_area_id, l.location_id FROM storage_area sa
                        JOIN location l ON l.location_id = sa.location_id
                        WHERE l.code = ? AND sa.name = ?""", (loc, area)).fetchone()
    if not r:
        raise Rejected("UNKNOWN_AREA", f"{loc} / {area}")
    return r


def _product(conn, sku):
    r = conn.execute("SELECT product_id, expiry_required FROM product WHERE sku = ?", (sku,)).fetchone()
    if not r:
        raise Rejected("UNKNOWN_PRODUCT", sku)
    return r


def _count(conn, count_id):
    r = conn.execute("SELECT storage_area_id, status FROM stock_count WHERE count_id = ?", (count_id,)).fetchone()
    if not r:
        raise Rejected("UNKNOWN_COUNT", str(count_id))
    return r


def _open_count_id(conn, area_id):
    r = conn.execute("SELECT count_id FROM stock_count WHERE storage_area_id = ? AND status = 'open'", (area_id,)).fetchone()
    return r[0] if r else None


def _guard_area(conn, area_id):
    c = _open_count_id(conn, area_id)
    if c is not None:
        raise Rejected("COUNT_OPEN", f"storage area is locked by open count {c}")


def _insert_tx(conn, key_id, op_type, location_id, actor, count_id, now):
    cur = conn.execute("""INSERT INTO stock_transaction
        (submission_key_id, operation_type, location_id, actor_id, count_id, created_at)
        VALUES (?,?,?,?,?,?)""", (key_id, op_type, location_id, actor, count_id, now))
    return cur.lastrowid


def _post(conn, tx, balance_id, d_on, d_held, reason):
    conn.execute("UPDATE balance SET on_hand = on_hand + ?, held = held + ? WHERE balance_id = ?",
                 (d_on, d_held, balance_id))
    # A movement is stamped with its transaction's time, so the two always agree.
    occurred = conn.execute("SELECT created_at FROM stock_transaction WHERE transaction_id = ?", (tx,)).fetchone()[0]
    conn.execute("""INSERT INTO movement (transaction_id, balance_id, delta_on_hand, delta_held,
                    delta_in_transit, reason, occurred_at) VALUES (?,?,?,?,0,?,?)""",
                 (tx, balance_id, d_on, d_held, reason, occurred))


# ----- keyed execution and rejection logging -----
def keyed(conn, actor, operation, idem, body, work, now=None):
    """Runs work once per (actor, operation, idempotency key). A retry with the same
    request returns the saved response; the same key with a different request is rejected."""
    rh = digest(body)
    with write_tx(conn):
        _active_actor(conn, actor)
        row = conn.execute("""SELECT request_hash, response_json FROM submission_key
                              WHERE actor_id = ? AND operation = ? AND idempotency_key = ?""",
                           (actor, operation, idem)).fetchone()
        if row:
            if row[0] != rh:
                raise Rejected("KEY_CONFLICT", "idempotency key was used for a different request")
            return json.loads(row[1])
        key_id = conn.execute("""INSERT INTO submission_key
            (actor_id, operation, idempotency_key, request_hash, response_json, created_at)
            VALUES (?,?,?,?,NULL,?)""", (actor, operation, idem, rh, _stamp(now))).lastrowid
        resp = work(conn, key_id)
        conn.execute("UPDATE submission_key SET response_json = ? WHERE submission_key_id = ?",
                     (json.dumps(resp, sort_keys=True), key_id))
        return resp


def audited(operation):
    def wrap(fn):
        def inner(conn, actor, *args, **kwargs):
            try:
                return fn(conn, actor, *args, **kwargs)
            except Rejected as err:
                # The failed write rolled back; only the rejection record is kept.
                conn.execute("""INSERT INTO rejected_request (actor_id, operation, reason_code, message, attempted_at)
                    VALUES ((SELECT user_id FROM app_user WHERE user_id = ?), ?, ?, ?, ?)""",
                             (actor, operation, err.code, err.message[:500], _stamp(kwargs.get("now"))))
                raise
        return inner
    return wrap


# ----- stock operations -----
@audited("issue")
def issue(conn, actor, *, idem, sku, loc, area, qty, today, now=None):
    now = _stamp(now)
    require_qty(qty, "qty", 1)
    body = {"op": "issue", "sku": sku, "loc": loc, "area": area, "qty": qty}

    def work(conn, key_id):
        area_id, loc_id = _area(conn, loc, area)
        _guard_area(conn, area_id)
        product_id, _ = _product(conn, sku)
        rows = conn.execute("""SELECT b.balance_id, b.lot_id, l.expiration_date, b.on_hand - b.held, l.lot_number
            FROM balance b JOIN lot l ON l.lot_id = b.lot_id
            WHERE b.storage_area_id = ? AND l.product_id = ? AND b.on_hand - b.held > 0
            ORDER BY (l.expiration_date IS NULL), l.expiration_date, l.lot_number""",
                            (area_id, product_id)).fetchall()
        rows = [r for r in rows if not is_expired(r[2], today)]  # expired stock is never issued
        if sum(r[3] for r in rows) < qty:
            raise Rejected("INSUFFICIENT", f"available {sum(r[3] for r in rows)} is less than {qty}")
        tx = _insert_tx(conn, key_id, "issue", loc_id, actor, None, now)
        left = qty
        used = []
        for bal_id, lot_id, exp, avail, lot_number in rows:
            take = min(left, avail)
            _post(conn, tx, bal_id, -take, 0, "issue")
            conn.execute("INSERT INTO issue_line (transaction_id, lot_id, storage_area_id, base_qty) VALUES (?,?,?,?)",
                         (tx, lot_id, area_id, take))
            used.append({"lot": lot_number, "expiration_date": exp, "area": area, "base_qty": take})
            left -= take
            if left == 0:
                break
        return {"transaction_id": tx, "issued": qty, "lines": used}

    return keyed(conn, actor, "issue", idem, body, work, now=now)


@audited("count_start")
def open_count(conn, actor, *, idem, loc, area, today, now=None):
    now = _stamp(now)
    body = {"op": "count_start", "loc": loc, "area": area}

    def work(conn, key_id):
        area_id, _ = _area(conn, loc, area)
        if _open_count_id(conn, area_id) is not None:
            raise Rejected("COUNT_OPEN", "this storage area already has an open count")
        cid = conn.execute("""INSERT INTO stock_count (storage_area_id, status, opened_by, opened_at)
                              VALUES (?, 'open', ?, ?)""", (area_id, actor, now)).lastrowid
        snap = conn.execute("""SELECT b.balance_id, b.lot_id, l.product_id, l.lot_number, b.on_hand
            FROM balance b JOIN lot l ON l.lot_id = b.lot_id WHERE b.storage_area_id = ? ORDER BY b.balance_id""",
                            (area_id,)).fetchall()
        for bal_id, lot_id, product_id, lot_number, on_hand in snap:
            conn.execute("""INSERT INTO count_line (count_id, product_id, lot_number, lot_id, storage_area_id,
                            balance_id, expected_qty, state) VALUES (?,?,?,?,?,?,?,'pending')""",
                         (cid, product_id, lot_number, lot_id, area_id, bal_id, on_hand))
        return {"count_id": cid, "lines": len(snap)}

    return keyed(conn, actor, "count_start", idem, body, work, now=now)


@audited("count_add_line")
def add_count_line(conn, actor, *, idem, count_id, sku, lot, pending_exp, today, now=None):
    now = _stamp(now)
    if pending_exp is not None:
        parse_date(pending_exp, "pending_exp")
    body = {"op": "count_add_line", "count_id": count_id, "sku": sku, "lot": lot, "pending_exp": pending_exp}

    def work(conn, key_id):
        area_id, status = _count(conn, count_id)
        if status != "open":
            raise Rejected("COUNT_NOT_OPEN", f"count {count_id} is {status}")
        product_id, expiry_required = _product(conn, sku)
        if conn.execute("""SELECT 1 FROM count_line WHERE count_id = ? AND storage_area_id = ?
                           AND product_id = ? AND lot_number = ?""", (count_id, area_id, product_id, lot)).fetchone():
            raise Rejected("DUPLICATE_LINE", f"{sku} {lot} is already on count {count_id}")
        lot_row = conn.execute("SELECT lot_id FROM lot WHERE product_id = ? AND lot_number = ?",
                               (product_id, lot)).fetchone()
        lot_id, pend, bal_id, expected = None, None, None, 0
        if lot_row:
            lot_id = lot_row[0]
            b = conn.execute("SELECT balance_id, on_hand FROM balance WHERE lot_id = ? AND storage_area_id = ?",
                             (lot_id, area_id)).fetchone()
            if b:
                bal_id, expected = b
        else:
            if expiry_required and not pending_exp:
                raise Rejected("EXPIRY_REQUIRED", f"new lot {lot} needs an expiration date")
            pend = pending_exp  # the lot is created only at completion
        conn.execute("""INSERT INTO count_line (count_id, product_id, lot_number, lot_id, pending_expiration_date,
                        storage_area_id, balance_id, expected_qty, state) VALUES (?,?,?,?,?,?,?,?,'pending')""",
                     (count_id, product_id, lot, lot_id, pend, area_id, bal_id, expected))
        return {"count_id": count_id, "lot": lot, "expected": expected}

    return keyed(conn, actor, "count_add_line", idem, body, work, now=now)


@audited("count_submit")
def submit_count(conn, actor, *, idem, count_id, sku, lot, counted, today, now=None):
    now = _stamp(now)
    require_qty(counted, "counted", 0)
    body = {"op": "count_submit", "count_id": count_id, "sku": sku, "lot": lot, "counted": counted}

    def work(conn, key_id):
        area_id, status = _count(conn, count_id)
        if status != "open":
            raise Rejected("COUNT_NOT_OPEN", f"count {count_id} is {status}")
        product_id, _ = _product(conn, sku)
        line = conn.execute("""SELECT count_line_id, expected_qty FROM count_line
                               WHERE count_id = ? AND product_id = ? AND lot_number = ?""",
                            (count_id, product_id, lot)).fetchone()
        if not line:
            raise Rejected("UNKNOWN_LINE", f"{sku} {lot} is not on count {count_id}")
        lid, expected = line
        prior = conn.execute("""SELECT attempt_no, within_tolerance FROM count_attempt
                                WHERE count_line_id = ? ORDER BY attempt_no""", (lid,)).fetchall()
        n = prior[-1][0] + 1 if prior else 1
        variance = counted - expected
        within = abs(variance) <= tolerance(expected)
        new_id = conn.execute("""INSERT INTO count_attempt (count_line_id, attempt_no, counted_qty, variance,
            within_tolerance, submitted_by, submitted_at, submission_key_id) VALUES (?,?,?,?,?,?,?,?)""",
            (lid, n, counted, variance, int(within), actor, now, key_id)).lastrowid
        # A newer attempt supersedes this line's earlier open adjustments. No decider is recorded.
        conn.execute("""UPDATE adjustment SET status = 'superseded', superseded_at = ?, superseded_by_attempt_id = ?
            WHERE status IN ('pending','approved') AND count_attempt_id IN
                  (SELECT count_attempt_id FROM count_attempt WHERE count_line_id = ?)
              AND count_attempt_id <> ?""", (now, new_id, lid, new_id))
        if variance != 0:
            conn.execute("""INSERT INTO adjustment (count_attempt_id, direction, qty, source, reason, requested_by, status)
                VALUES (?,?,?,?,?,?, 'pending')""",
                         (new_id, "increase" if variance > 0 else "decrease", abs(variance),
                          "count_within" if within else "count_out", f"count attempt {n}", actor))
        earlier_out = any(not w for _, w in prior)
        state = "out" if not within else ("resolved_recount" if earlier_out else "within")
        conn.execute("UPDATE count_line SET state = ? WHERE count_line_id = ?", (state, lid))
        return {"attempt_no": n, "variance": variance, "within_tolerance": within, "state": state}

    return keyed(conn, actor, "count_submit", idem, body, work, now=now)


def _resolve_surplus(conn, product_id, expiry_required, lot_number, pend, area_id):
    if pend is not None:
        parse_date(pend, "pending_expiration")
    r = conn.execute("SELECT lot_id, expiration_date FROM lot WHERE product_id = ? AND lot_number = ?",
                     (product_id, lot_number)).fetchone()
    if r:
        if pend is not None and pend != r[1]:
            raise Rejected("EXPIRY_CONFLICT", f"lot {lot_number} exists with a different expiration")
        lot_id = r[0]
    else:
        if expiry_required and pend is None:
            raise Rejected("EXPIRY_REQUIRED", f"new lot {lot_number} needs an expiration date")
        lot_id = conn.execute("INSERT INTO lot (product_id, lot_number, expiration_date) VALUES (?,?,?)",
                              (product_id, lot_number, pend)).lastrowid
    b = conn.execute("SELECT balance_id FROM balance WHERE lot_id = ? AND storage_area_id = ?",
                     (lot_id, area_id)).fetchone()
    if b:
        return lot_id, b[0]
    bal = conn.execute("INSERT INTO balance (lot_id, storage_area_id, on_hand, held) VALUES (?,?,0,0)",
                       (lot_id, area_id)).lastrowid
    return lot_id, bal


@audited("count_complete")
def complete_count(conn, actor, *, idem, count_id, today, fault_after=None, now=None):
    now = _stamp(now)
    body = {"op": "count_complete", "count_id": count_id}

    def work(conn, key_id):
        area_id, status = _count(conn, count_id)
        if status != "open":
            raise Rejected("COUNT_NOT_OPEN", f"count {count_id} is {status}")
        lines = conn.execute("""SELECT cl.count_line_id, cl.product_id, cl.lot_number, cl.pending_expiration_date,
                                       cl.balance_id, cl.state, p.expiry_required, cl.lot_number
                                FROM count_line cl JOIN product p ON p.product_id = cl.product_id
                                WHERE cl.count_id = ? ORDER BY cl.count_line_id""", (count_id,)).fetchall()
        for row in lines:
            if row[5] not in ("within", "resolved_recount"):
                raise Rejected("LINE_UNRESOLVED", f"line {row[2]} is {row[5]}; complete or recount it first")
        by_id = {r[0]: r for r in lines}
        adjs = conn.execute("""SELECT a.adjustment_id, a.direction, a.qty, t.count_line_id
            FROM adjustment a
            JOIN count_attempt t ON t.count_attempt_id = a.count_attempt_id
            JOIN count_line cl ON cl.count_line_id = t.count_line_id
            WHERE cl.count_id = ? AND a.status = 'pending' ORDER BY a.adjustment_id""", (count_id,)).fetchall()
        loc_id = conn.execute("SELECT location_id FROM storage_area WHERE storage_area_id = ?", (area_id,)).fetchone()[0]
        tx = _insert_tx(conn, key_id, "count_completion", loc_id, actor, count_id, now) if adjs else None
        resolved, applied = {}, 0
        for adj_id, direction, qty, line_id in adjs:
            _, product_id, lot_number, pend, bal_id, _st, expiry_req, _ = by_id[line_id]
            if bal_id is None:  # surplus line: the lot and balance are created here, not at count start
                if line_id not in resolved:
                    resolved[line_id] = _resolve_surplus(conn, product_id, expiry_req, lot_number, pend, area_id)
                bal_id = resolved[line_id][1]
            _post(conn, tx, bal_id, qty if direction == "increase" else -qty, 0, "count completion")
            conn.execute("""UPDATE adjustment SET status = 'applied', transaction_id = ?, balance_id = ?
                            WHERE adjustment_id = ?""", (tx, bal_id, adj_id))
            applied += 1
            if fault_after is not None and applied == fault_after:
                raise RuntimeError("injected fault during count completion")
        for line_id, (lot_id, bal_id) in resolved.items():
            conn.execute("UPDATE count_line SET lot_id = ?, balance_id = ?, state = 'adjusted' WHERE count_line_id = ?",
                         (lot_id, bal_id, line_id))
        conn.execute("UPDATE stock_count SET status = 'completed', closed_at = ? WHERE count_id = ?", (now, count_id))
        return {"count_id": count_id, "transaction_id": tx, "adjustments_applied": applied}

    return keyed(conn, actor, "count_complete", idem, body, work, now=now)


@audited("count_cancel")
def cancel_count(conn, actor, *, idem, count_id, today, now=None):
    now = _stamp(now)
    """Releases the area without stock changes. Keeps lines and attempts; cancels unposted adjustments."""
    body = {"op": "count_cancel", "count_id": count_id}

    def work(conn, key_id):
        _area_id, status = _count(conn, count_id)
        if status != "open":
            raise Rejected("COUNT_NOT_OPEN", f"count {count_id} is {status}")
        cancelled = conn.execute("""UPDATE adjustment SET status = 'cancelled'
            WHERE status = 'pending' AND count_attempt_id IN (
                SELECT t.count_attempt_id FROM count_attempt t
                JOIN count_line cl ON cl.count_line_id = t.count_line_id WHERE cl.count_id = ?)""",
            (count_id,)).rowcount
        conn.execute("UPDATE stock_count SET status = 'cancelled', closed_at = ? WHERE count_id = ?", (now, count_id))
        return {"count_id": count_id, "adjustments_cancelled": cancelled}

    return keyed(conn, actor, "count_cancel", idem, body, work, now=now)


@audited("receipt")
def receive(conn, actor, *, idem, loc, area, lines, today, packing_slip=None, fault_after=None, now=None):
    now = _stamp(now)
    """Supplier receiving. Each line is either base units (base_qty) or a purchase unit with a
    conversion (purchase_qty x factor). One submission, one stock transaction, all lines or none."""
    if not isinstance(lines, list) or not lines:
        raise Rejected("BAD_LINES", "a receipt needs at least one line")
    for i, ln in enumerate(lines):
        if not isinstance(ln, dict) or not isinstance(ln.get("sku"), str) or not isinstance(ln.get("lot"), str) \
                or not ln["lot"].strip():
            raise Rejected("BAD_LINE", f"line {i} needs text sku and lot")
        if ("base_qty" in ln) == ("purchase_unit" in ln):
            raise Rejected("BAD_LINE", f"line {i} needs exactly one of base_qty or purchase_unit + purchase_qty")
        if "base_qty" in ln:
            require_qty(ln["base_qty"], f"line {i} base_qty", 1)
        else:
            if not isinstance(ln.get("purchase_unit"), str) or not ln["purchase_unit"]:
                raise Rejected("BAD_LINE", f"line {i} purchase_unit must be text")
            require_qty(ln.get("purchase_qty"), f"line {i} purchase_qty", 1)
        if ln.get("expiration") is not None:
            parse_date(ln["expiration"], f"line {i} expiration")
    if packing_slip is not None and (not isinstance(packing_slip, str) or not packing_slip.strip()):
        raise Rejected("BAD_PACKING_SLIP", "packing_slip must be non-empty text")
    body = {"op": "receipt", "loc": loc, "area": area, "packing_slip": packing_slip, "lines": lines}

    def work(conn, key_id):
        area_id, loc_id = _area(conn, loc, area)
        _guard_area(conn, area_id)
        warnings = []  # nonblocking: the receipt is still accepted
        if packing_slip is not None:
            prior = [r[0] for r in conn.execute("""SELECT transaction_id FROM stock_transaction
                WHERE packing_slip_ref = ? AND location_id = ? AND operation_type = 'receipt'""",
                (packing_slip, loc_id)).fetchall()]
            if prior:
                warnings.append({"code": "DUPLICATE_PACKING_SLIP",
                                 "message": f"packing slip {packing_slip} was already received at {loc}",
                                 "prior_transaction_ids": prior})
        tx = _insert_tx(conn, key_id, "receipt", loc_id, actor, None, now)
        if packing_slip is not None:
            conn.execute("UPDATE stock_transaction SET packing_slip_ref = ? WHERE transaction_id = ?", (packing_slip, tx))
        seen, summary = set(), []
        for i, ln in enumerate(lines):
            product_id, expiry_required = _product(conn, ln["sku"])
            if (ln["sku"], ln["lot"]) in seen:
                raise Rejected("DUPLICATE_LINE", f"{ln['sku']} {ln['lot']} appears twice in this receipt")
            seen.add((ln["sku"], ln["lot"]))
            if "base_qty" in ln:
                unit, pq, factor, base = None, None, 1, ln["base_qty"]
            else:
                r = conn.execute("SELECT factor FROM unit_conversion WHERE product_id = ? AND purchase_unit = ?",
                                 (product_id, ln["purchase_unit"])).fetchone()
                if not r:
                    raise Rejected("UNKNOWN_UNIT", f"{ln['sku']} has no conversion for {ln['purchase_unit']}")
                unit, pq, factor = ln["purchase_unit"], ln["purchase_qty"], r[0]
                base = pq * factor
            exp = ln.get("expiration")
            lot = conn.execute("SELECT lot_id, expiration_date FROM lot WHERE product_id = ? AND lot_number = ?",
                               (product_id, ln["lot"])).fetchone()
            if lot:
                if lot[1] != exp:
                    raise Rejected("EXPIRY_CONFLICT",
                                   f"lot {ln['lot']} is recorded with expiration {lot[1]}; receipt says {exp}")
                lot_id = lot[0]
            else:
                if expiry_required and exp is None:
                    raise Rejected("EXPIRY_REQUIRED", f"new lot {ln['lot']} needs an expiration date")
                lot_id = conn.execute("INSERT INTO lot (product_id, lot_number, expiration_date) VALUES (?,?,?)",
                                      (product_id, ln["lot"], exp)).lastrowid
            b = conn.execute("SELECT balance_id FROM balance WHERE lot_id = ? AND storage_area_id = ?",
                             (lot_id, area_id)).fetchone()
            bal_id = b[0] if b else conn.execute(
                "INSERT INTO balance (lot_id, storage_area_id, on_hand, held) VALUES (?,?,0,0)",
                (lot_id, area_id)).lastrowid
            _post(conn, tx, bal_id, base, 0, "receipt")
            conn.execute("""INSERT INTO receipt_line (transaction_id, lot_id, storage_area_id, purchase_unit,
                            purchase_qty, factor, base_qty) VALUES (?,?,?,?,?,?,?)""",
                         (tx, lot_id, area_id, unit, pq, factor, base))
            summary.append({"sku": ln["sku"], "lot": ln["lot"], "purchase_unit": unit, "purchase_qty": pq,
                            "factor": factor, "base_qty": base})
            if fault_after is not None and i + 1 == fault_after:
                raise RuntimeError("injected fault during receiving")
        return {"transaction_id": tx, "lines": summary, "warnings": warnings}

    return keyed(conn, actor, "receipt", idem, body, work, now=now)
