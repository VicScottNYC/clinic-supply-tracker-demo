"""API tests: validation, retries, conflicts, count locks, rollback, injected clock, concurrent requests.
Each test uses a new temporary database. Nothing touches the demo database files."""
import datetime as dt
import json
import sqlite3
import tempfile
import threading
import os
import sqlite3
import unittest
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

import inventory_p1 as svc
from api_p1 import create_app, make_app
from loader_p1 import create_new_database, load_opening

FIXED_NOW = dt.datetime(2026, 10, 18, 13, 0, tzinfo=dt.timezone.utc)  # 09:00 in New York


class ApiCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.path = str(Path(self._tmp.name) / "api.db")
        conn = create_new_database(self.path)
        load_opening(conn)
        conn.close()
        self.now = FIXED_NOW
        self.app = create_app(self.path, clock=lambda: self.now)
        self.client = TestClient(self.app)

    def tearDown(self):
        self.client.close()
        self._tmp.cleanup()

    @staticmethod
    def h(key, actor="counter_rfc"):
        return {"Idempotency-Key": key, "X-Actor-Id": actor}

    def total(self, sku, loc, area):
        c = svc.connect(self.path)
        try:
            return c.execute("""SELECT COALESCE(SUM(b.on_hand), 0) FROM balance b
                JOIN lot l ON l.lot_id = b.lot_id JOIN product p ON p.product_id = l.product_id
                JOIN storage_area sa ON sa.storage_area_id = b.storage_area_id
                JOIN location lo ON lo.location_id = sa.location_id
                WHERE p.sku = ? AND lo.code = ? AND sa.name = ?""", (sku, loc, area)).fetchone()[0]
        finally:
            c.close()

    def db_counts(self):
        c = svc.connect(self.path)
        try:
            return tuple(c.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                         for t in ("movement", "lot", "balance", "stock_transaction", "submission_key"))
        finally:
            c.close()

    def open_count(self, key="cnt-open", loc="RFC", area="Main storage"):
        r = self.client.post("/counts", headers=self.h(key), json={"loc": loc, "area": area})
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()["count_id"]


class ReadTests(ApiCase):
    def test_balances_show_opening_stock_with_expiry_categories(self):
        r = self.client.get("/balances", params={"loc": "RFC"})
        self.assertEqual(r.status_code, 200)
        rows = {(b["sku"], b["lot"], b["area"]): b for b in r.json()["balances"]}
        ap = rows[("P-002", "AP-2026A", "Main storage")]
        self.assertEqual(ap["on_hand"], 40)
        self.assertEqual(ap["category"], "expiring_soon")  # expires 2026-11-20, within 60 days

    def test_injected_clock_changes_expiry_categories(self):
        self.now = dt.datetime(2026, 12, 1, 13, 0, tzinfo=dt.timezone.utc)  # after 2026-11-20
        body = self.client.get("/balances", params={"loc": "RFC"}).json()
        self.assertEqual(body["as_of"], "2026-12-01")
        ap = [b for b in body["balances"] if b["lot"] == "AP-2026A" and b["area"] == "Main storage"][0]
        self.assertEqual(ap["category"], "expired")
        self.assertEqual(ap["available"], 0)

    def test_unknown_location_and_count_return_404(self):
        self.assertEqual(self.client.get("/balances", params={"loc": "NOPE"}).status_code, 404)
        self.assertEqual(self.client.get("/counts/99999").status_code, 404)

    def test_health(self):
        self.assertEqual(self.client.get("/health").json(), {"status": "ok"})


class ValidationTests(ApiCase):
    def test_booleans_fractions_strings_and_extra_fields_are_rejected(self):
        before = self.db_counts()
        base = {"sku": "P-002", "loc": "RFC", "area": "Main storage"}
        bodies = [
            {**base, "qty": True},
            {**base, "qty": 1.5},
            {**base, "qty": 2.0},
            {**base, "qty": "2"},
            {**base, "qty": 2, "fault_after": 1},
            {**base, "qty": 2, "extra": "x"},
        ]
        for i, body in enumerate(bodies):
            with self.subTest(i=i):
                r = self.client.post("/issues", headers=self.h(f"v-{i}"), json=body)
                self.assertEqual(r.status_code, 422, r.text)
        self.assertEqual(self.db_counts(), before)

    def test_receipt_quantities_are_strict_too(self):
        r = self.client.post("/receipts", headers=self.h("rv-1"), json={
            "loc": "RFC", "area": "Main storage",
            "lines": [{"sku": "P-001", "lot": "NG-LOT-1", "base_qty": 2.5}]})
        self.assertEqual(r.status_code, 422)
        r = self.client.post("/receipts", headers=self.h("rv-2"), json={
            "loc": "RFC", "area": "Main storage",
            "lines": [{"sku": "P-002", "lot": "AP-2026A", "purchase_unit": "case", "purchase_qty": True}]})
        self.assertEqual(r.status_code, 422)

    def test_missing_headers_and_unknown_actor(self):
        body = {"sku": "P-002", "loc": "RFC", "area": "Main storage", "qty": 1}
        self.assertEqual(self.client.post("/issues", json=body, headers={"X-Actor-Id": "counter_rfc"}).status_code, 422)
        self.assertEqual(self.client.post("/issues", json=body, headers={"Idempotency-Key": "k"}).status_code, 422)
        r = self.client.post("/issues", headers=self.h("k-actor", actor="nobody"), json=body)
        self.assertEqual(r.status_code, 403)
        self.assertEqual(r.json()["error"], "ACTOR")


class IssueAndRetryTests(ApiCase):
    def test_issue_retry_returns_the_same_response_and_moves_stock_once(self):
        body = {"sku": "P-002", "loc": "RFC", "area": "Main storage", "qty": 3}
        before = self.total("P-002", "RFC", "Main storage")
        first = self.client.post("/issues", headers=self.h("i-1"), json=body)
        again = self.client.post("/issues", headers=self.h("i-1"), json=body)
        self.assertEqual(first.status_code, 200)
        self.assertEqual(first.json(), again.json())
        self.assertEqual(self.total("P-002", "RFC", "Main storage"), before - 3)

    def test_same_key_with_different_body_conflicts(self):
        self.client.post("/issues", headers=self.h("i-2"),
                         json={"sku": "P-002", "loc": "RFC", "area": "Main storage", "qty": 3})
        r = self.client.post("/issues", headers=self.h("i-2"),
                             json={"sku": "P-002", "loc": "RFC", "area": "Main storage", "qty": 4})
        self.assertEqual(r.status_code, 409)
        self.assertEqual(r.json()["error"], "KEY_CONFLICT")

    def test_insufficient_stock_is_409_and_writes_nothing(self):
        before = self.db_counts()
        r = self.client.post("/issues", headers=self.h("i-3"),
                             json={"sku": "P-002", "loc": "RFC", "area": "Main storage", "qty": 100000})
        self.assertEqual(r.status_code, 409)
        self.assertEqual(r.json()["error"], "INSUFFICIENT")
        self.assertEqual(self.db_counts(), before)

    def test_unknown_product_is_404(self):
        r = self.client.post("/issues", headers=self.h("i-4"),
                             json={"sku": "P-999", "loc": "RFC", "area": "Main storage", "qty": 1})
        self.assertEqual(r.status_code, 404)


class ReceivingApiTests(ApiCase):
    def test_purchase_unit_receipt_and_duplicate_slip_warning(self):
        start = self.total("P-002", "RFC", "Main storage")
        body = {"loc": "RFC", "area": "Main storage", "packing_slip": "S-9",
                "lines": [{"sku": "P-002", "lot": "AP-2026A", "expiration": "2026-11-20",
                                 "purchase_unit": "case", "purchase_qty": 2}]}
        first = self.client.post("/receipts", headers=self.h("r-1"), json=body)
        self.assertEqual(first.status_code, 200)
        self.assertEqual(first.json()["warnings"], [])
        self.assertEqual(first.json()["lines"][0]["base_qty"], 24)
        second = self.client.post("/receipts", headers=self.h("r-2"), json=body)
        self.assertEqual(second.status_code, 200)  # warning only; not blocked
        self.assertEqual(second.json()["warnings"][0]["code"], "DUPLICATE_PACKING_SLIP")
        self.assertEqual(self.total("P-002", "RFC", "Main storage"), start + 48)

    def test_failed_receipt_rolls_back_every_line_and_the_key_is_reusable(self):
        before = self.total("P-001", "RFC", "Main storage")
        bad = {"loc": "RFC", "area": "Main storage", "lines": [
            {"sku": "P-001", "lot": "NG-LOT-1", "base_qty": 5},
            {"sku": "P-002", "lot": "AP-2026A", "expiration": "2027-01-01", "base_qty": 5}]}
        r = self.client.post("/receipts", headers=self.h("r-rb"), json=bad)
        self.assertEqual(r.status_code, 409)
        self.assertEqual(r.json()["error"], "EXPIRY_CONFLICT")
        self.assertEqual(self.total("P-001", "RFC", "Main storage"), before)
        good = {"loc": "RFC", "area": "Main storage", "lines": [
            {"sku": "P-001", "lot": "NG-LOT-1", "base_qty": 5},
            {"sku": "P-002", "lot": "AP-2026A", "expiration": "2026-11-20", "base_qty": 5}]}
        ok = self.client.post("/receipts", headers=self.h("r-rb"), json=good)
        self.assertEqual(ok.status_code, 200, ok.text)
        self.assertEqual(self.total("P-001", "RFC", "Main storage"), before + 5)

    def test_fault_injection_is_not_reachable_through_the_api(self):
        r = self.client.post("/receipts", headers=self.h("r-f"), json={
            "loc": "RFC", "area": "Main storage", "lines": [{"sku": "P-001", "lot": "NG-LOT-1", "base_qty": 1}],
            "fault_after": 1})
        self.assertEqual(r.status_code, 422)
        r = self.client.post("/counts/1/complete", headers=self.h("c-f"))
        self.assertIn(r.status_code, (404, 409))  # no count 1 here; the endpoint takes no fault option


class CountApiTests(ApiCase):
    def test_count_lock_blocks_receipts_and_issues_until_completion(self):
        count_id = self.open_count()
        r = self.client.post("/receipts", headers=self.h("lock-r"), json={
            "loc": "RFC", "area": "Main storage", "lines": [{"sku": "P-001", "lot": "NG-LOT-1", "base_qty": 1}]})
        self.assertEqual((r.status_code, r.json()["error"]), (409, "COUNT_OPEN"))
        r = self.client.post("/issues", headers=self.h("lock-i"),
                             json={"sku": "P-001", "loc": "RFC", "area": "Main storage", "qty": 1})
        self.assertEqual((r.status_code, r.json()["error"]), (409, "COUNT_OPEN"))
        # A different area is not locked.
        r = self.client.post("/counts", headers=self.h("other-area"), json={"loc": "HHC", "area": "Main storage"})
        self.assertEqual(r.status_code, 200)
        self.assertIsNotNone(count_id)

    def test_full_count_through_the_api_then_stock_is_free(self):
        count_id = self.open_count("full")
        detail = self.client.get(f"/counts/{count_id}").json()
        self.assertEqual(detail["status"], "open")
        for i, line in enumerate(detail["lines"]):
            r = self.client.post(f"/counts/{count_id}/submissions", headers=self.h(f"sub-{i}"), json={
                "sku": line["sku"], "lot": line["lot"], "counted": line["expected_qty"]})
            self.assertEqual(r.status_code, 200, r.text)
        r = self.client.post(f"/counts/{count_id}/complete", headers=self.h("done"))
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(self.client.get(f"/counts/{count_id}").json()["status"], "completed")
        r = self.client.post("/issues", headers=self.h("after"),
                             json={"sku": "P-001", "loc": "RFC", "area": "Main storage", "qty": 1})
        self.assertEqual(r.status_code, 200)
        again = self.client.post(f"/counts/{count_id}/complete", headers=self.h("done-2"))
        self.assertEqual((again.status_code, again.json()["error"]), (409, "COUNT_NOT_OPEN"))

    def test_cancelled_count_releases_the_area_without_stock_change(self):
        count_id = self.open_count("cancel-me")
        before = self.total("P-001", "RFC", "Main storage")
        r = self.client.post(f"/counts/{count_id}/cancel", headers=self.h("c-x"))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.total("P-001", "RFC", "Main storage"), before)
        r = self.client.post("/issues", headers=self.h("c-y"),
                             json={"sku": "P-001", "loc": "RFC", "area": "Main storage", "qty": 1})
        self.assertEqual(r.status_code, 200)

    def test_line_for_unknown_count_is_404(self):
        r = self.client.post("/counts/424242/lines", headers=self.h("nl"),
                             json={"sku": "P-001", "lot": "NG-LOT-1"})
        self.assertEqual(r.status_code, 404)


class ConcurrentRequestTests(ApiCase):
    """Each thread uses its own client, so each request opens its own SQLite connection."""

    def _parallel(self, fn, n):
        results, errors = [None] * n, []
        lock = threading.Lock()

        def run(i):
            try:
                with TestClient(self.app) as c:
                    results[i] = fn(c, i)
            except Exception as exc:
                with lock:
                    errors.append(repr(exc))

        threads = [threading.Thread(target=run, args=(i,)) for i in range(n)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        return results, errors

    def test_same_key_from_many_requests_applies_once(self):
        before = self.total("P-002", "RFC", "Main storage")
        body = {"sku": "P-002", "loc": "RFC", "area": "Main storage", "qty": 3}
        results, errors = self._parallel(lambda c, i: c.post("/issues", headers=self.h("same"), json=body), 8)
        self.assertEqual(errors, [])
        self.assertTrue(all(r.status_code == 200 for r in results), [r.status_code for r in results])
        self.assertEqual(len({json.dumps(r.json(), sort_keys=True) for r in results}), 1)
        self.assertEqual(self.total("P-002", "RFC", "Main storage"), before - 3)

    def test_distinct_requests_lose_no_stock(self):
        before = self.total("P-002", "RFC", "Main storage")
        results, errors = self._parallel(lambda c, i: c.post(
            "/issues", headers=self.h(f"d-{i}"),
            json={"sku": "P-002", "loc": "RFC", "area": "Main storage", "qty": 1}), 8)
        self.assertEqual(errors, [])
        self.assertTrue(all(r.status_code == 200 for r in results))
        self.assertEqual(self.total("P-002", "RFC", "Main storage"), before - 8)

    def test_concurrent_receipts_are_blocked_while_the_area_is_counted(self):
        self.open_count("held-open")
        before = self.total("P-001", "RFC", "Main storage")
        body = {"loc": "RFC", "area": "Main storage", "lines": [{"sku": "P-001", "lot": "NG-LOT-1", "base_qty": 2}]}
        results, errors = self._parallel(lambda c, i: c.post("/receipts", headers=self.h(f"cr-{i}"), json=body), 6)
        self.assertEqual(errors, [])
        self.assertTrue(all(r.status_code == 409 and r.json()["error"] == "COUNT_OPEN" for r in results))
        self.assertEqual(self.total("P-001", "RFC", "Main storage"), before)


class ClockTests(ApiCase):
    def test_stored_timestamps_follow_the_injected_clock(self):
        r = self.client.post("/issues", headers=self.h("clk-1"),
                             json={"sku": "P-001", "loc": "RFC", "area": "Main storage", "qty": 1})
        self.assertEqual(r.status_code, 200)
        c = svc.connect(self.path)
        try:
            tx_time = c.execute("SELECT created_at FROM stock_transaction ORDER BY transaction_id DESC LIMIT 1").fetchone()[0]
            mv_time = c.execute("SELECT MAX(occurred_at) FROM movement").fetchone()[0]
            key_time = c.execute("SELECT created_at FROM submission_key WHERE idempotency_key = 'clk-1'").fetchone()[0]
        finally:
            c.close()
        self.assertEqual(tx_time, "2026-10-18T13:00:00Z")
        self.assertEqual(mv_time, "2026-10-18T13:00:00Z")
        self.assertEqual(key_time, "2026-10-18T13:00:00Z")

    def test_business_date_and_utc_stamp_come_from_one_reading(self):
        # 02:00 UTC on 19 Oct is 22:00 on 18 Oct in New York: the business date must be the 18th.
        self.now = dt.datetime(2026, 10, 19, 2, 0, tzinfo=dt.timezone.utc)
        self.assertEqual(self.client.get("/balances", params={"loc": "RFC"}).json()["as_of"], "2026-10-18")
        self.client.post("/issues", headers=self.h("clk-2"),
                         json={"sku": "P-001", "loc": "RFC", "area": "Main storage", "qty": 1})
        tx = self.client.get("/transactions", params={"loc": "RFC"}).json()["transactions"][0]
        self.assertEqual(tx["created_at"], "2026-10-19T02:00:00Z")

    def test_default_clock_records_real_time(self):
        app = create_app(self.path)  # no clock injected
        with TestClient(app) as c:
            before = dt.datetime.now(dt.timezone.utc)
            self.assertEqual(c.post("/issues", headers=self.h("clk-3"),
                                    json={"sku": "P-001", "loc": "RFC", "area": "Main storage", "qty": 1}).status_code, 200)
            after = dt.datetime.now(dt.timezone.utc)
            stamp = c.get("/transactions", params={"loc": "RFC"}).json()["transactions"][0]["created_at"]
        recorded = dt.datetime.strptime(stamp, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc)
        self.assertTrue(before.replace(microsecond=0) <= recorded <= after)


class ConnectionOwnershipTests(ApiCase):
    def test_each_connection_is_opened_and_closed_by_the_same_worker(self):
        real = svc.connect_existing
        log = []
        lock = threading.Lock()

        class Tracked:
            def __init__(self, conn):
                self._c = conn
                self.opened_in = threading.get_ident()

            def close(self):
                with lock:
                    log.append((self.opened_in, threading.get_ident()))
                self._c.close()

            def __getattr__(self, name):
                return getattr(self._c, name)

        def run(i, errors):
            try:
                with TestClient(self.app) as c:
                    c.get("/balances", params={"loc": "RFC"})
                    c.post("/issues", headers=self.h(f"own-{i}"),
                           json={"sku": "P-001", "loc": "RFC", "area": "Main storage", "qty": 1})
            except Exception as exc:
                errors.append(repr(exc))

        errors, threads = [], []
        svc.connect_existing = lambda p: Tracked(real(p))
        try:
            threads = [threading.Thread(target=run, args=(i, errors)) for i in range(10)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
        finally:
            svc.connect_existing = real
        self.assertEqual(errors, [])
        self.assertGreaterEqual(len(log), 20)
        self.assertTrue(all(opened == closed for opened, closed in log))


class DatabaseGuardTests(unittest.TestCase):
    def test_missing_database_is_refused_and_not_created(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "absent.db"
            with self.assertRaises(FileNotFoundError):
                create_app(path)
            self.assertFalse(path.exists())

    def test_uninitialized_database_is_refused(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "empty.db"
            sqlite3.connect(str(path)).close()
            with self.assertRaises(RuntimeError):
                create_app(path)
            c = sqlite3.connect(str(path))
            try:
                self.assertEqual(c.execute("SELECT COUNT(*) FROM sqlite_master").fetchone()[0], 0)
            finally:
                c.close()

    def test_factory_requires_the_database_path(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("P1_DB_PATH", None)
            with self.assertRaises(RuntimeError):
                make_app()


class HistoryTests(ApiCase):
    def test_history_lists_receipts_and_issues_with_their_movements(self):
        self.client.post("/receipts", headers=self.h("hist-r"), json={
            "loc": "RFC", "area": "Main storage", "packing_slip": "H-1",
            "lines": [{"sku": "P-001", "lot": "NG-LOT-1", "base_qty": 4}]})
        self.client.post("/issues", headers=self.h("hist-i"),
                         json={"sku": "P-001", "loc": "RFC", "area": "Main storage", "qty": 2})
        txs = self.client.get("/transactions", params={"loc": "RFC"}).json()["transactions"]
        self.assertEqual([t["operation"] for t in txs[:2]], ["issue", "receipt"])
        receipt = txs[1]
        self.assertEqual(receipt["packing_slip"], "H-1")
        self.assertEqual(receipt["created_at"], "2026-10-18T13:00:00Z")
        self.assertEqual(sum(m["delta_on_hand"] for m in receipt["movements"]), 4)
        self.assertEqual(sum(m["delta_on_hand"] for m in txs[0]["movements"]), -2)
        self.assertEqual(self.client.get("/transactions", params={"loc": "NOPE"}).status_code, 404)


if __name__ == "__main__":
    unittest.main()
