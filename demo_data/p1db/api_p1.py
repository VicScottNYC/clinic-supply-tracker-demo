"""P1 HTTP API. A thin layer over inventory_p1: strict request validation, the Idempotency-Key and
actor headers passed to the service, service rejections mapped to HTTP statuses, and read-only
reference and inventory views. Inventory figures and business decisions are computed here and in the
service, never in the browser.

Time: each request takes one reading of the clock. That reading gives the UTC audit timestamp and the
clinic's local business date (America/New_York), so the two always agree.
  P1_CLOCK=fixed    the approved evaluation timestamp (2026-10-18 09:00 New York), flagged as a demo date
  P1_CLOCK=current  real time (default)
Connections: each request opens its own connection to an existing database and closes it in the same
worker call. Fault injection is not exposed."""
import datetime as dt
import os
from contextlib import contextmanager
from zoneinfo import ZoneInfo

from fastapi import FastAPI, Header, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr

import inventory_p1 as svc
from inventory_p1 import Rejected

UTC = dt.timezone.utc
CLINIC_TZ_NAME = "America/New_York"
CLINIC_TZ = ZoneInfo(CLINIC_TZ_NAME)
FIXED_NOW = dt.datetime(2026, 10, 18, 13, 0, tzinfo=UTC)  # approved evaluation timestamp (09:00 New York)
EXPIRING_WINDOW_DAYS = 60
CONFLICT_CODES = {"KEY_CONFLICT", "COUNT_OPEN", "COUNT_NOT_OPEN", "LINE_UNRESOLVED",
                  "EXPIRY_CONFLICT", "INSUFFICIENT", "BALANCE_EXISTS"}


def utc_clock():
    return dt.datetime.now(UTC)


def fixed_clock():
    return FIXED_NOW


def business_stamps(clock):
    """One clock reading -> (UTC audit timestamp, clinic local business date)."""
    moment = clock()
    if moment.tzinfo is None:
        raise ValueError("clock must return an aware datetime")
    utc = moment.astimezone(UTC)
    return utc.strftime("%Y-%m-%dT%H:%M:%SZ"), utc.astimezone(CLINIC_TZ).date()


def status_for(code):
    if code == "ACTOR":
        return 403
    if code.startswith("UNKNOWN_"):
        return 404
    if code in CONFLICT_CODES:
        return 409
    return 422


ACTOR_H = Header(alias="X-Actor-Id", min_length=1, max_length=64)
IDEM_H = Header(alias="Idempotency-Key", min_length=1, max_length=128)


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class IssueIn(Strict):
    sku: StrictStr
    loc: StrictStr
    area: StrictStr
    qty: StrictInt


class ReceiveLineIn(Strict):
    sku: StrictStr
    lot: StrictStr
    base_qty: StrictInt | None = None
    purchase_unit: StrictStr | None = None
    purchase_qty: StrictInt | None = None
    expiration: StrictStr | None = None


class ReceiveIn(Strict):
    loc: StrictStr
    area: StrictStr
    packing_slip: StrictStr | None = None
    lines: list[ReceiveLineIn] = Field(min_length=1)


class OpenCountIn(Strict):
    loc: StrictStr
    area: StrictStr


class AddCountLineIn(Strict):
    sku: StrictStr
    lot: StrictStr
    pending_expiration: StrictStr | None = None


class SubmitCountIn(Strict):
    sku: StrictStr
    lot: StrictStr
    counted: StrictInt


def create_app(db_path, clock=utc_clock, clock_mode="current"):
    if clock_mode not in ("current", "fixed"):
        raise ValueError("clock_mode must be 'current' or 'fixed'")
    path = str(db_path)
    svc.connect_existing(path).close()  # fail at startup; never creates the file

    @contextmanager
    def db():
        conn = svc.connect_existing(path)
        try:
            yield conn
        finally:
            conn.close()

    app = FastAPI(title="Clinic Supply P1 API", version="0.3.0")

    @app.exception_handler(Rejected)
    def rejected(request, exc):
        return JSONResponse(status_code=status_for(exc.code),
                            content={"error": exc.code, "message": exc.message})

    @app.get("/health")
    def health():
        return {"status": "ok"}

    @app.get("/reference")
    def reference():
        stamp, today = business_stamps(clock)
        with db() as conn:
            locations = [{"code": c, "name": n, "kind": k} for c, n, k in
                         conn.execute("SELECT code, name, kind FROM location ORDER BY code").fetchall()]
            areas = [{"location": c, "area": a} for c, a in conn.execute("""SELECT lo.code, sa.name
                FROM storage_area sa JOIN location lo ON lo.location_id = sa.location_id
                ORDER BY lo.code, sa.name""").fetchall()]
            conv = {}
            for sku, unit, factor, label in conn.execute("""SELECT p.sku, uc.purchase_unit, uc.factor, uc.label FROM unit_conversion uc
                    JOIN product p ON p.product_id = uc.product_id ORDER BY p.sku, uc.purchase_unit"""):
                conv.setdefault(sku, []).append({"purchase_unit": unit, "factor": factor, "label": label})
            products = [{"sku": sku, "name": name, "base_unit": unit, "expiry_required": bool(req),
                         "conversions": conv.get(sku, [])}
                        for sku, name, unit, req in conn.execute(
                            "SELECT sku, name, base_unit, expiry_required FROM product ORDER BY sku").fetchall()]
            roles = {}
            for uid, loc, role in conn.execute("""SELECT ur.user_id, lo.code, ur.role FROM user_role ur
                    JOIN location lo ON lo.location_id = ur.location_id ORDER BY ur.user_id, lo.code""").fetchall():
                roles.setdefault(uid, []).append({"location": loc, "role": role})
            actors = [{"user_id": uid, "display_name": name, "roles": roles.get(uid, [])}
                      for uid, name in conn.execute("""SELECT user_id, display_name FROM app_user
                          WHERE active = 1 AND user_id <> 'loader' ORDER BY user_id""").fetchall()]
        return {"clock": {"mode": clock_mode, "demo": clock_mode == "fixed",
                          "evaluation_date": today.isoformat(), "evaluation_timestamp_utc": stamp,
                          "timezone": CLINIC_TZ_NAME, "expiring_window_days": EXPIRING_WINDOW_DAYS},
                "locations": locations, "areas": areas, "products": products, "actors": actors}

    @app.get("/balances")
    def balances(loc: str):
        _, today = business_stamps(clock)
        with db() as conn:
            if not conn.execute("SELECT 1 FROM location WHERE code = ?", (loc,)).fetchone():
                raise Rejected("UNKNOWN_LOCATION", loc)
            rows = conn.execute("""SELECT p.sku, p.name, p.base_unit, l.lot_number, l.expiration_date, sa.name,
                    b.on_hand, b.held
                FROM balance b JOIN lot l ON l.lot_id = b.lot_id JOIN product p ON p.product_id = l.product_id
                JOIN storage_area sa ON sa.storage_area_id = b.storage_area_id
                JOIN location lo ON lo.location_id = sa.location_id
                WHERE lo.code = ? ORDER BY p.sku, sa.name, l.expiration_date IS NULL, l.expiration_date, l.lot_number""",
                                (loc,)).fetchall()
        lots, products = [], {}
        for sku, pname, unit, lot, exp, area, on_hand, held in rows:
            expired = svc.is_expired(exp, today)
            if exp is None:
                cat = "none"
            elif expired:
                cat = "expired"
            elif dt.date.fromisoformat(exp) <= today + dt.timedelta(days=EXPIRING_WINDOW_DAYS):
                cat = "expiring_soon"
            else:
                cat = "none"
            available = 0 if expired else on_hand - held
            lots.append({"sku": sku, "product": pname, "base_unit": unit, "lot": lot, "expiration_date": exp,
                         "area": area, "on_hand": on_hand, "held": held, "available": available, "category": cat})
            t = products.setdefault(sku, {"sku": sku, "product": pname, "base_unit": unit, "on_hand": 0,
                                          "available": 0, "expired": 0, "expiring_soon": 0})
            t["on_hand"] += on_hand
            t["available"] += available
            if cat == "expired":
                t["expired"] += on_hand
            if cat == "expiring_soon":
                t["expiring_soon"] += on_hand
        return {"location": loc, "as_of": today.isoformat(), "products": list(products.values()), "balances": lots}

    @app.get("/transactions")
    def transactions(loc: str, limit: int = Query(default=50, ge=1, le=500)):
        with db() as conn:
            row = conn.execute("SELECT location_id FROM location WHERE code = ?", (loc,)).fetchone()
            if not row:
                raise Rejected("UNKNOWN_LOCATION", loc)
            txs = conn.execute("""SELECT transaction_id, operation_type, created_at, actor_id, packing_slip_ref, count_id
                FROM stock_transaction WHERE location_id = ? ORDER BY transaction_id DESC LIMIT ?""",
                               (row[0], limit)).fetchall()
            out = []
            for tid, op, created, actor, slip, cid in txs:
                moves = conn.execute("""SELECT p.sku, l.lot_number, sa.name, m.delta_on_hand, m.delta_held,
                        m.reason, m.occurred_at FROM movement m
                    JOIN balance b ON b.balance_id = m.balance_id JOIN lot l ON l.lot_id = b.lot_id
                    JOIN product p ON p.product_id = l.product_id
                    JOIN storage_area sa ON sa.storage_area_id = b.storage_area_id
                    WHERE m.transaction_id = ? ORDER BY m.movement_id""", (tid,)).fetchall()
                out.append({"transaction_id": tid, "operation": op, "created_at": created, "actor": actor,
                            "packing_slip": slip, "count_id": cid,
                            "movements": [{"sku": m[0], "lot": m[1], "area": m[2], "delta_on_hand": m[3],
                                           "delta_held": m[4], "reason": m[5], "occurred_at": m[6]}
                                          for m in moves]})
        return {"location": loc, "transactions": out}

    @app.get("/counts")
    def list_counts(loc: str, area: str | None = None):
        with db() as conn:
            if not conn.execute("SELECT 1 FROM location WHERE code = ?", (loc,)).fetchone():
                raise Rejected("UNKNOWN_LOCATION", loc)
            sql = """SELECT sc.count_id, sc.status, sa.name, sc.opened_at, sc.closed_at FROM stock_count sc
                JOIN storage_area sa ON sa.storage_area_id = sc.storage_area_id
                JOIN location lo ON lo.location_id = sa.location_id WHERE lo.code = ?"""
            args = [loc]
            if area is not None:
                sql += " AND sa.name = ?"
                args.append(area)
            rows = conn.execute(sql + " ORDER BY sc.count_id DESC LIMIT 20", args).fetchall()
        return {"location": loc, "counts": [{"count_id": r[0], "status": r[1], "area": r[2], "opened_at": r[3],
                                             "closed_at": r[4]} for r in rows]}

    @app.get("/counts/{count_id}")
    def count_detail(count_id: int):
        with db() as conn:
            head = conn.execute("""SELECT sc.status, sa.name, lo.code, sc.opened_at FROM stock_count sc
                JOIN storage_area sa ON sa.storage_area_id = sc.storage_area_id
                JOIN location lo ON lo.location_id = sa.location_id WHERE sc.count_id = ?""", (count_id,)).fetchone()
            if not head:
                raise Rejected("UNKNOWN_COUNT", str(count_id))
            lines = conn.execute("""SELECT cl.count_line_id, p.sku, p.name, p.base_unit, cl.lot_number,
                    cl.pending_expiration_date, cl.expected_qty, cl.state,
                    (SELECT ca.counted_qty FROM count_attempt ca WHERE ca.count_line_id = cl.count_line_id
                       ORDER BY ca.attempt_no DESC LIMIT 1),
                    (SELECT COUNT(*) FROM count_attempt ca WHERE ca.count_line_id = cl.count_line_id)
                FROM count_line cl JOIN product p ON p.product_id = cl.product_id
                WHERE cl.count_id = ? ORDER BY cl.count_line_id""", (count_id,)).fetchall()
        items = [{"count_line_id": r[0], "sku": r[1], "product": r[2], "base_unit": r[3], "lot": r[4],
                  "pending_expiration_date": r[5], "expected_qty": r[6], "state": r[7],
                  "last_counted_qty": r[8], "attempts": r[9],
                  "unresolved": r[7] in ("pending", "out")} for r in lines]
        return {"count_id": count_id, "status": head[0], "area": head[1], "location": head[2],
                "opened_at": head[3], "area_locked": head[0] == "open",
                "unresolved_lines": sum(1 for i in items if i["unresolved"]), "lines": items}

    # ---- writes: every write needs Idempotency-Key and X-Actor-Id ----
    @app.post("/issues")
    def post_issue(body: IssueIn, actor: str = ACTOR_H, key: str = IDEM_H):
        now, today = business_stamps(clock)
        with db() as conn:
            return svc.issue(conn, actor, idem=key, sku=body.sku, loc=body.loc, area=body.area,
                             qty=body.qty, today=today, now=now)

    @app.post("/receipts")
    def post_receipt(body: ReceiveIn, actor: str = ACTOR_H, key: str = IDEM_H):
        now, today = business_stamps(clock)
        lines = [{k: v for k, v in ln.model_dump().items() if v is not None} for ln in body.lines]
        with db() as conn:
            return svc.receive(conn, actor, idem=key, loc=body.loc, area=body.area, lines=lines,
                               today=today, packing_slip=body.packing_slip, now=now)

    @app.post("/counts")
    def post_open_count(body: OpenCountIn, actor: str = ACTOR_H, key: str = IDEM_H):
        now, today = business_stamps(clock)
        with db() as conn:
            return svc.open_count(conn, actor, idem=key, loc=body.loc, area=body.area, today=today, now=now)

    @app.post("/counts/{count_id}/lines")
    def post_count_line(count_id: int, body: AddCountLineIn, actor: str = ACTOR_H, key: str = IDEM_H):
        now, today = business_stamps(clock)
        with db() as conn:
            return svc.add_count_line(conn, actor, idem=key, count_id=count_id, sku=body.sku, lot=body.lot,
                                      pending_exp=body.pending_expiration, today=today, now=now)

    @app.post("/counts/{count_id}/submissions")
    def post_count_submission(count_id: int, body: SubmitCountIn, actor: str = ACTOR_H, key: str = IDEM_H):
        now, today = business_stamps(clock)
        with db() as conn:
            return svc.submit_count(conn, actor, idem=key, count_id=count_id, sku=body.sku, lot=body.lot,
                                    counted=body.counted, today=today, now=now)

    @app.post("/counts/{count_id}/complete")
    def post_count_complete(count_id: int, actor: str = ACTOR_H, key: str = IDEM_H):
        now, today = business_stamps(clock)
        with db() as conn:
            return svc.complete_count(conn, actor, idem=key, count_id=count_id, today=today, now=now)

    @app.post("/counts/{count_id}/cancel")
    def post_count_cancel(count_id: int, actor: str = ACTOR_H, key: str = IDEM_H):
        now, today = business_stamps(clock)
        with db() as conn:
            return svc.cancel_count(conn, actor, idem=key, count_id=count_id, today=today, now=now)

    return app


def make_app():
    """Factory for `uvicorn api_p1:make_app --factory`. Reads P1_DB_PATH and P1_CLOCK."""
    path = os.environ.get("P1_DB_PATH")
    if not path:
        raise RuntimeError("set P1_DB_PATH to an existing, initialized P1 database file")
    mode = os.environ.get("P1_CLOCK", "current")
    clock = fixed_clock if mode == "fixed" else utc_clock
    return create_app(path, clock=clock, clock_mode=mode)
