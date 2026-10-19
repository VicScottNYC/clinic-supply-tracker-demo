"""Opening-data loader (SQLite). Creates only NEW demo databases, never resets an existing one.
Parses and validates the whole file (structure, reference data, rows) before any write.
Loads reference data and opening stock in ONE transaction. Every attempt is recorded in
import_attempt; a rejected or failed attempt changes no stock."""
import hashlib
import json
import uuid
from pathlib import Path

import inventory_p1 as svc
from inventory_p1 import Rejected, write_tx, connect, digest, NOW, KEY_NS, SCHEMA_PATH, is_int, parse_date

DEMO_DIR = Path(__file__).resolve().parent.parent
OPENING = DEMO_DIR / "opening_p1.json"
ROLE_MAP = {"counter": "supply_clerk", "manager": "clinic_manager"}


class ImportRejected(Exception):
    def __init__(self, errors):
        super().__init__(f"{len(errors)} import error(s)")
        self.errors = errors


def create_new_database(db_path):
    """Creates a new database file with the P1 schema and the loader user. Refuses an existing path."""
    if Path(db_path).exists():
        raise FileExistsError(f"{db_path} already exists; this loader only creates new demo databases")
    conn = connect(db_path)
    conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
    conn.execute("INSERT INTO app_user (user_id, display_name, active) VALUES ('loader', 'Demo loader', 1)")
    return conn


def _text(v):
    return isinstance(v, str) and v.strip() != ""


def _parse(raw):
    try:
        return json.loads(raw.decode("utf-8")), []
    except UnicodeDecodeError as exc:
        return None, [{"row": None, "field": "file", "message": f"file is not UTF-8: {exc}"}]
    except json.JSONDecodeError as exc:
        return None, [{"row": None, "field": "file", "message": f"malformed JSON: {exc}"}]


def _structure_errors(data):
    """Shape and type checks on everything except opening rows. Never raises on bad input."""
    if not isinstance(data, dict):
        return [{"row": None, "field": "document", "message": "top level must be an object"}]
    errors = []

    def e(field, msg):
        errors.append({"row": None, "field": field, "message": msg})

    for key, typ in [("locations", dict), ("areas", list), ("products", dict), ("users", dict),
                     ("reorder_thresholds", list), ("opening_balances", list)]:
        if not isinstance(data.get(key), typ):
            e(key, f"must be a {typ.__name__}")
    if errors:
        return errors
    locs = data["locations"]
    for code, name in locs.items():
        if not _text(code) or not _text(name):
            e(f"locations.{code}", "code and name must be non-empty text")
    for i, a in enumerate(data["areas"]):
        if not isinstance(a, dict) or not _text(a.get("location")) or not _text(a.get("area")):
            e(f"areas[{i}]", "needs text fields location and area")
        elif a["location"] not in locs:
            e(f"areas[{i}]", f"unknown location {a['location']}")
    for sku, p in data["products"].items():
        if not isinstance(p, dict) or not _text(p.get("name")) or not _text(p.get("unit")) \
                or not isinstance(p.get("expiration_required"), bool):
            e(f"products.{sku}", "needs text name and unit and a boolean expiration_required")
    for uid, u in data["users"].items():
        if not isinstance(u, dict) or u.get("role") not in ROLE_MAP or not _text(u.get("location")):
            e(f"users.{uid}", "needs role counter or manager and a location")
        elif u["location"] != "ALL" and u["location"] not in locs:
            e(f"users.{uid}", f"unknown location {u['location']}")
    for i, t in enumerate(data["reorder_thresholds"]):
        if not isinstance(t, dict) or not _text(t.get("product")) or not _text(t.get("location")) \
                or not is_int(t.get("threshold")) or t["threshold"] < 0:
            e(f"reorder_thresholds[{i}]", "needs product, location, and a non-negative integer threshold")
            continue
        if t["product"] not in data["products"]:
            e(f"reorder_thresholds[{i}]", f"unknown product {t['product']}")
        if t["location"] not in locs:
            e(f"reorder_thresholds[{i}]", f"unknown location {t['location']}")
    for i, c in enumerate(data.get("unit_conversions", [])):
        if not isinstance(c, dict) or not _text(c.get("product")) or not _text(c.get("purchase_unit")) \
                or not is_int(c.get("factor")) or c["factor"] <= 0:
            e(f"unit_conversions[{i}]", "needs product, purchase_unit, and an integer factor above zero")
        elif c["product"] not in data["products"]:
            e(f"unit_conversions[{i}]", f"unknown product {c['product']}")
        elif not _text(c.get("label")):
            e(f"unit_conversions[{i}]", "needs a label stating the conversion is a demo assumption or source")
    return errors


def _row_errors(data):
    errors = []
    products = data["products"]
    areas = {(a["location"], a["area"]) for a in data["areas"]}
    seen, lot_exp = {}, {}
    for row in data["opening_balances"]:
        if not isinstance(row, dict):
            errors.append({"row": None, "field": "opening_balances", "message": "each row must be an object"})
            continue
        rid = row.get("id")

        def err(field, msg):
            errors.append({"row": rid, "field": field, "message": msg})

        for f in ("id", "product", "lot", "location", "area"):
            if not _text(row.get(f)):
                err(f, "must be non-empty text")
        if not is_int(row.get("qty")) or row["qty"] <= 0:
            err("qty", "must be an integer greater than zero (booleans and decimals are rejected)")
        exp = row.get("expiration")
        if exp is not None:
            try:
                parse_date(exp, "expiration")
            except Rejected as r:
                err("expiration", r.message)
        prod, lot, loc, area = row.get("product"), row.get("lot"), row.get("location"), row.get("area")
        if not all(_text(x) for x in (prod, lot, loc, area)):
            continue
        if prod not in products:
            err("product", f"unknown product {prod}")
        elif products[prod]["expiration_required"] and exp is None:
            err("expiration", "required for this product")
        if (loc, area) not in areas:
            err("area", f"unknown location/area {loc} / {area}")
        if (prod, lot) in lot_exp and lot_exp[(prod, lot)] != exp:
            err("expiration", "conflicts with an earlier row for the same lot")
        lot_exp[(prod, lot)] = exp
        k = (prod, lot, loc, area)
        if k in seen:
            err("lot", f"duplicate balance, also in row {seen[k]}")
        seen[k] = rid
    return errors


def _record(conn, file_hash, name, status, errors):
    conn.execute("""INSERT INTO import_attempt (file_hash, file_name, actor_id, attempted_at, status, error_list)
                    VALUES (?,?,'loader',?,?,?)""",
                 (file_hash, name, NOW, status, json.dumps(errors) if errors else None))


def _load_reference(conn, data):
    for code, name in data["locations"].items():
        conn.execute("INSERT INTO location (code, name, kind) VALUES (?,?,?)",
                     (code, name, "supply_hub" if code == "CSR" else "clinic"))
    loc_ids = dict(conn.execute("SELECT code, location_id FROM location").fetchall())
    for a in data["areas"]:
        conn.execute("INSERT INTO storage_area (location_id, name) VALUES (?,?)", (loc_ids[a["location"]], a["area"]))
    for sku, p in data["products"].items():
        conn.execute("INSERT INTO product (sku, name, base_unit, expiry_required) VALUES (?,?,?,?)",
                     (sku, p["name"], p["unit"], int(p["expiration_required"])))
    for c in data.get("unit_conversions", []):
        conn.execute("""INSERT INTO unit_conversion (product_id, purchase_unit, factor, label)
                        SELECT product_id, ?, ?, ? FROM product WHERE sku = ?""",
                     (c["purchase_unit"], c["factor"], c["label"], c["product"]))
    for t in data["reorder_thresholds"]:
        conn.execute("""INSERT INTO reorder_threshold (product_id, location_id, min_qty)
                        SELECT product_id, ?, ? FROM product WHERE sku = ?""",
                     (loc_ids[t["location"]], t["threshold"], t["product"]))
    for uid, u in data["users"].items():
        conn.execute("INSERT INTO app_user (user_id, display_name, active) VALUES (?,?,1)", (uid, uid))
        for code in (list(loc_ids) if u["location"] == "ALL" else [u["location"]]):
            conn.execute("INSERT INTO user_role (user_id, location_id, role) VALUES (?,?,?)",
                         (uid, loc_ids[code], ROLE_MAP[u["role"]]))


def load_opening(conn, path=OPENING, fault_after_row=None):
    raw = Path(path).read_bytes()
    file_hash = hashlib.sha256(raw).hexdigest()
    name = Path(path).name
    data, errors = _parse(raw)
    if not errors:
        errors = _structure_errors(data)
    if not errors:
        errors = _row_errors(data)
    if not errors and conn.execute("SELECT 1 FROM location LIMIT 1").fetchone():
        errors = [{"row": None, "field": "database", "message": "database already holds reference data; opening load is refused"}]
    if not errors and conn.execute("SELECT 1 FROM import_attempt WHERE file_hash = ? AND status = 'accepted'",
                                   (file_hash,)).fetchone():
        errors = [{"row": None, "field": "file", "message": "this file was already accepted"}]
    if errors:
        _record(conn, file_hash, name, "rejected", errors)
        raise ImportRejected(errors)
    try:
        with write_tx(conn):
            _load_reference(conn, data)
            ia = conn.execute("""INSERT INTO import_attempt (file_hash, file_name, actor_id, attempted_at, status)
                                 VALUES (?,?,'loader',?,'accepted')""", (file_hash, name, NOW)).lastrowid
            n = 0
            for row in data["opening_balances"]:
                n += 1
                product_id, _ = svc._product(conn, row["product"])
                area_id, loc_id = svc._area(conn, row["location"], row["area"])
                lot = conn.execute("SELECT lot_id FROM lot WHERE product_id = ? AND lot_number = ?",
                                   (product_id, row["lot"])).fetchone()
                lot_id = lot[0] if lot else conn.execute(
                    "INSERT INTO lot (product_id, lot_number, expiration_date) VALUES (?,?,?)",
                    (product_id, row["lot"], row["expiration"])).lastrowid
                if conn.execute("SELECT 1 FROM balance WHERE lot_id = ? AND storage_area_id = ?",
                                (lot_id, area_id)).fetchone():
                    raise Rejected("BALANCE_EXISTS", f"row {row['id']}: balance already exists")
                bal = conn.execute("INSERT INTO balance (lot_id, storage_area_id, on_hand, held) VALUES (?,?,0,0)",
                                   (lot_id, area_id)).lastrowid
                key_id = conn.execute("""INSERT INTO submission_key (actor_id, operation, idempotency_key,
                    request_hash, response_json, created_at) VALUES ('loader','import',?,?,NULL,?)""",
                    (str(uuid.uuid5(KEY_NS, f"{file_hash}:{row['id']}")), digest(row), NOW)).lastrowid
                tx = svc._insert_tx(conn, key_id, "opening", loc_id, "loader", None, NOW)
                svc._post(conn, tx, bal, row["qty"], 0, "opening balance")
                conn.execute("UPDATE submission_key SET response_json = ? WHERE submission_key_id = ?",
                             (json.dumps({"transaction_id": tx}), key_id))
                if fault_after_row == n:
                    raise RuntimeError("injected fault during opening load")
            conn.execute("INSERT INTO import_batch (import_attempt_id, row_count) VALUES (?,?)", (ia, n))
    except Exception as exc:
        _record(conn, file_hash, name, "rejected", [{"row": None, "field": "load", "message": str(exc)}])
        raise
    return n


if __name__ == "__main__":
    import sys
    if len(sys.argv) != 2:
        sys.exit("usage: python3 loader_p1.py NEW_DB_PATH   (the path must not exist)")
    db = create_new_database(sys.argv[1])
    rows = load_opening(db)
    db.close()
    print(f"created {sys.argv[1]} with {rows} opening rows")
