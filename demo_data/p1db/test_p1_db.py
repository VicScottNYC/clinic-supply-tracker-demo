"""P1 database tests. Every test uses a NEW temporary database; no demo database is touched."""
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

import inventory_p1 as svc
import scenario_p1 as scen
from inventory_p1 import Rejected
from loader_p1 import create_new_database, load_opening, ImportRejected, OPENING

TODAY = scen.TODAY
TABLES = ["location", "storage_area", "product", "lot", "balance", "movement", "stock_transaction",
          "submission_key", "stock_count", "count_line", "count_attempt", "adjustment", "issue_line"]


class DbCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.conn = create_new_database(str(Path(self._tmp.name) / "test.db"))
        load_opening(self.conn)

    def tearDown(self):
        self.conn.close()
        self._tmp.cleanup()

    def counts(self):
        return {t: self.conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in TABLES}

    def on_hand(self, sku, lot, loc, area):
        r = self.conn.execute("""SELECT b.on_hand FROM balance b JOIN lot l ON l.lot_id = b.lot_id
            JOIN product p ON p.product_id = l.product_id JOIN storage_area sa ON sa.storage_area_id = b.storage_area_id
            JOIN location lo ON lo.location_id = sa.location_id
            WHERE p.sku = ? AND l.lot_number = ? AND lo.code = ? AND sa.name = ?""", (sku, lot, loc, area)).fetchone()
        return r[0] if r else 0

    def start_count(self, actor, loc, area, label):
        return svc.open_count(self.conn, actor, idem=f"{label}-start", loc=loc, area=area, today=TODAY)["count_id"]

    def submit(self, actor, cid, sku, lot, counted, n):
        return svc.submit_count(self.conn, actor, idem=f"{cid}-{lot}-{n}", count_id=cid, sku=sku, lot=lot,
                                counted=counted, today=TODAY)

    def rfc_count_clean(self, cid, counted_by="counter_rfc"):
        """Submit every RFC Main line at expected quantity (AP-2026A deliberately not included)."""
        self.submit(counted_by, cid, "P-003", "SC-2611", 120, 1)
        self.submit(counted_by, cid, "P-002", "AP-2027B", 60, 1)
        self.submit(counted_by, cid, "P-001", "NG-LOT-1", 21, 1)


class SchemaConstraintTests(DbCase):
    def test_foreign_keys_are_enforced(self):
        self.assertEqual(self.conn.execute("PRAGMA foreign_keys").fetchone()[0], 1)
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute("INSERT INTO balance (lot_id, storage_area_id, on_hand, held) VALUES (9999, 1, 1, 0)")

    def test_duplicate_lot_area_balance_rejected(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute("""INSERT INTO balance (lot_id, storage_area_id, on_hand, held)
                SELECT lot_id, storage_area_id, 1, 0 FROM balance LIMIT 1""")

    def test_negative_on_hand_rejected(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute("UPDATE balance SET on_hand = -1 WHERE balance_id = 1")

    def test_negative_held_rejected(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute("UPDATE balance SET held = -1 WHERE balance_id = 1")

    def test_held_above_on_hand_rejected(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute("UPDATE balance SET held = on_hand + 1 WHERE balance_id = 1")

    def test_movement_cannot_be_updated_or_deleted(self):
        with self.assertRaisesRegex(sqlite3.IntegrityError, "append-only"):
            self.conn.execute("UPDATE movement SET delta_on_hand = 0 WHERE movement_id = 1")
        with self.assertRaisesRegex(sqlite3.IntegrityError, "append-only"):
            self.conn.execute("DELETE FROM movement WHERE movement_id = 1")

    def test_lot_expiration_is_immutable(self):
        lot_id = self.conn.execute("SELECT lot_id FROM lot WHERE expiration_date IS NOT NULL ORDER BY lot_id LIMIT 1").fetchone()[0]
        with self.assertRaisesRegex(sqlite3.IntegrityError, "immutable"):
            self.conn.execute("UPDATE lot SET expiration_date = '2030-01-01' WHERE lot_id = ?", (lot_id,))

    def test_expiry_required_product_rejects_new_lot_without_date(self):
        with self.assertRaisesRegex(sqlite3.IntegrityError, "expiration date required"):
            self.conn.execute("""INSERT INTO lot (product_id, lot_number, expiration_date)
                SELECT product_id, 'NO-DATE', NULL FROM product WHERE sku = 'P-002'""")

    def test_invalid_enumeration_rejected(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute("""INSERT INTO submission_key (actor_id, operation, idempotency_key, request_hash, created_at)
                VALUES ('loader', 'bogus_op', 'x', 'x', 'x')""")

    def test_loaded_opening_reconciles(self):
        self.assertEqual(scen.reconcile(self.conn), [])


class LoaderTests(unittest.TestCase):
    def test_invalid_file_leaves_inventory_unchanged(self):
        with tempfile.TemporaryDirectory() as d:
            conn = create_new_database(str(Path(d) / "t.db"))
            data = json.loads(OPENING.read_text(encoding="utf-8"))
            for row in data["opening_balances"]:
                if row["product"] == "P-002":
                    row["expiration"] = None  # expiry-required product without a date
            bad = Path(d) / "bad.json"
            bad.write_text(json.dumps(data), encoding="utf-8")
            before = {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in TABLES}
            with self.assertRaises(ImportRejected) as ctx:
                load_opening(conn, bad)
            self.assertTrue(any(e["field"] == "expiration" for e in ctx.exception.errors))
            after = {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in TABLES}
            self.assertEqual(before, after)
            self.assertEqual(conn.execute("SELECT status FROM import_attempt").fetchall(), [("rejected",)])
            conn.close()

    def test_midway_failure_rolls_back_all_opening_data(self):
        with tempfile.TemporaryDirectory() as d:
            conn = create_new_database(str(Path(d) / "t.db"))
            with self.assertRaises(RuntimeError):
                load_opening(conn, fault_after_row=5)
            for t in ["location", "storage_area", "product", "reorder_threshold", "lot", "balance",
                      "movement", "stock_transaction", "submission_key", "user_role", "import_batch"]:
                self.assertEqual(conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0], 0, t)
            self.assertEqual(conn.execute("SELECT status FROM import_attempt").fetchall(), [("rejected",)])
            conn.close()

    def test_same_file_cannot_be_accepted_twice(self):
        with tempfile.TemporaryDirectory() as d:
            conn = create_new_database(str(Path(d) / "t.db"))
            load_opening(conn)
            before = conn.execute("SELECT COUNT(*) FROM movement").fetchone()[0]
            with self.assertRaises(ImportRejected):
                load_opening(conn)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM movement").fetchone()[0], before)
            conn.close()

    def test_existing_database_is_never_replaced(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "t.db"
            path.write_bytes(b"")
            with self.assertRaises(FileExistsError):
                create_new_database(str(path))
            self.assertEqual(path.read_bytes(), b"")


class ServiceTests(DbCase):
    def test_repeated_submission_creates_no_duplicate_stock_change(self):
        first = svc.issue(self.conn, "counter_rfc", idem="K1", sku="P-002", loc="RFC", area="Main storage", qty=10, today=TODAY)
        n_mov = self.conn.execute("SELECT COUNT(*) FROM movement").fetchone()[0]
        again = svc.issue(self.conn, "counter_rfc", idem="K1", sku="P-002", loc="RFC", area="Main storage", qty=10, today=TODAY)
        self.assertEqual(first, again)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM movement").fetchone()[0], n_mov)
        self.assertEqual(self.on_hand("P-002", "AP-2026A", "RFC", "Main storage"), 30)

    def test_same_key_different_request_is_rejected_without_change(self):
        svc.issue(self.conn, "counter_rfc", idem="K2", sku="P-002", loc="RFC", area="Main storage", qty=10, today=TODAY)
        with self.assertRaises(Rejected) as ctx:
            svc.issue(self.conn, "counter_rfc", idem="K2", sku="P-002", loc="RFC", area="Main storage", qty=5, today=TODAY)
        self.assertEqual(ctx.exception.code, "KEY_CONFLICT")
        self.assertEqual(self.on_hand("P-002", "AP-2026A", "RFC", "Main storage"), 30)

    def test_rejections_are_logged_without_stock_changes(self):
        before = self.counts()
        with self.assertRaises(Rejected):
            svc.issue(self.conn, "counter_ewc", idem="K3", sku="P-005", loc="EWC", area="Main storage", qty=1, today=TODAY)
        self.assertEqual(self.conn.execute("SELECT reason_code FROM rejected_request").fetchall(), [("INSUFFICIENT",)])
        self.assertEqual(self.counts()["movement"], before["movement"])

    def test_expired_stock_is_never_issued(self):
        with self.assertRaises(Rejected) as ctx:
            svc.issue(self.conn, "counter_ewc", idem="K4", sku="P-005", loc="EWC", area="Main storage", qty=1, today=TODAY)
        self.assertEqual(ctx.exception.code, "INSUFFICIENT")

    def test_count_lock_blocks_ordinary_stock_operations(self):
        cid = self.start_count("counter_rfc", "RFC", "Main storage", "lock")
        with self.assertRaises(Rejected) as ctx:
            svc.issue(self.conn, "counter_rfc", idem="L1", sku="P-002", loc="RFC", area="Main storage", qty=1, today=TODAY)
        self.assertEqual(ctx.exception.code, "COUNT_OPEN")
        with self.assertRaises(Rejected) as ctx:
            svc.open_count(self.conn, "counter_rfc", idem="L2", loc="RFC", area="Main storage", today=TODAY)
        self.assertEqual(ctx.exception.code, "COUNT_OPEN")
        svc.issue(self.conn, "counter_hhc", idem="L3", sku="P-001", loc="HHC", area="Main storage", qty=1, today=TODAY)
        self.submit("counter_rfc", cid, "P-003", "SC-2611", 120, 1)  # the count's own operation is allowed

    def test_recount_preserves_attempts_and_applies_only_the_latest(self):
        cid = self.start_count("counter_rfc", "RFC", "Main storage", "recount")
        first = self.submit("counter_rfc", cid, "P-002", "AP-2026A", 37, 1)
        self.assertEqual(first["state"], "out")
        self.rfc_count_clean(cid)
        second = self.submit("counter_rfc", cid, "P-002", "AP-2026A", 39, 2)
        self.assertEqual(second["state"], "resolved_recount")
        self.assertEqual(svc.complete_count(self.conn, "counter_rfc", idem="R-complete", count_id=cid, today=TODAY)["adjustments_applied"], 1)
        attempts = self.conn.execute("""SELECT t.attempt_no, t.counted_qty FROM count_attempt t
            JOIN count_line cl ON cl.count_line_id = t.count_line_id WHERE cl.lot_number = 'AP-2026A' ORDER BY attempt_no""").fetchall()
        self.assertEqual(attempts, [(1, 37), (2, 39)])
        statuses = dict(self.conn.execute("SELECT status, COUNT(*) FROM adjustment GROUP BY status").fetchall())
        self.assertEqual(statuses.get("superseded"), 1)
        self.assertEqual(statuses.get("applied"), 1)
        self.assertEqual(self.on_hand("P-002", "AP-2026A", "RFC", "Main storage"), 39)

    def test_unresolved_line_rejects_completion_without_change(self):
        cid = self.start_count("counter_rfc", "RFC", "Main storage", "unresolved")
        self.rfc_count_clean(cid)  # AP-2026A has no attempt
        before = self.counts()
        with self.assertRaises(Rejected) as ctx:
            svc.complete_count(self.conn, "counter_rfc", idem="U1", count_id=cid, today=TODAY)
        self.assertEqual(ctx.exception.code, "LINE_UNRESOLVED")
        self.assertEqual(self.counts(), before)
        self.assertEqual(self.conn.execute("SELECT status FROM stock_count WHERE count_id = ?", (cid,)).fetchone()[0], "open")

    def test_zero_variance_completion_creates_no_transaction(self):
        cid = self.start_count("counter_rfc", "RFC", "Main storage", "zero")
        self.submit("counter_rfc", cid, "P-003", "SC-2611", 120, 1)
        self.submit("counter_rfc", cid, "P-002", "AP-2026A", 40, 1)
        self.submit("counter_rfc", cid, "P-002", "AP-2027B", 60, 1)
        self.submit("counter_rfc", cid, "P-001", "NG-LOT-1", 21, 1)
        before = self.conn.execute("SELECT COUNT(*) FROM stock_transaction").fetchone()[0]
        out = svc.complete_count(self.conn, "counter_rfc", idem="Z1", count_id=cid, today=TODAY)
        self.assertIsNone(out["transaction_id"])
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM stock_transaction").fetchone()[0], before)

    def test_surplus_lot_is_created_only_at_completion_and_rolls_back_on_failure(self):
        cid = self.start_count("counter_rfc", "RFC", "Main storage", "surplus")
        svc.add_count_line(self.conn, "counter_rfc", idem="S1", count_id=cid, sku="P-002", lot="AP-2028C",
                           pending_exp="2028-01-31", today=TODAY)
        self.submit("counter_rfc", cid, "P-002", "AP-2028C", 2, 1)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM lot WHERE lot_number = 'AP-2028C'").fetchone()[0], 0)
        self.submit("counter_rfc", cid, "P-003", "SC-2611", 120, 1)
        self.submit("counter_rfc", cid, "P-002", "AP-2026A", 40, 1)
        self.submit("counter_rfc", cid, "P-002", "AP-2027B", 60, 1)
        self.submit("counter_rfc", cid, "P-001", "NG-LOT-1", 21, 1)
        before = self.counts()
        with self.assertRaises(RuntimeError):
            svc.complete_count(self.conn, "counter_rfc", idem="S2", count_id=cid, today=TODAY, fault_after=1)
        self.assertEqual(self.counts(), before)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM lot WHERE lot_number = 'AP-2028C'").fetchone()[0], 0)


class CompletionRollbackTests(DbCase):
    def test_failure_midway_through_completion_rolls_back_every_change(self):
        cid = self.start_count("counter_rfc", "RFC", "Main storage", "rollback")
        self.submit("counter_rfc", cid, "P-002", "AP-2026A", 37, 1)
        self.submit("counter_rfc", cid, "P-002", "AP-2026A", 39, 2)
        svc.add_count_line(self.conn, "counter_rfc", idem="RB-S", count_id=cid, sku="P-002", lot="AP-2028C",
                           pending_exp="2028-01-31", today=TODAY)
        self.submit("counter_rfc", cid, "P-002", "AP-2028C", 2, 1)
        self.submit("counter_rfc", cid, "P-003", "SC-2611", 120, 1)
        self.submit("counter_rfc", cid, "P-002", "AP-2027B", 60, 1)
        self.submit("counter_rfc", cid, "P-001", "NG-LOT-1", 21, 1)
        before = self.counts()
        before_on = self.on_hand("P-002", "AP-2026A", "RFC", "Main storage")
        with self.assertRaises(RuntimeError):
            svc.complete_count(self.conn, "counter_rfc", idem="RB-C", count_id=cid, today=TODAY, fault_after=2)
        self.assertEqual(self.counts(), before)
        self.assertEqual(self.on_hand("P-002", "AP-2026A", "RFC", "Main storage"), before_on)
        self.assertEqual(self.conn.execute("SELECT status FROM stock_count WHERE count_id = ?", (cid,)).fetchone()[0], "open")
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM adjustment WHERE status = 'applied'").fetchone()[0], 0)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM submission_key WHERE operation = 'count_complete'").fetchone()[0], 0)
        # The count can still complete once the fault is gone.
        out = svc.complete_count(self.conn, "counter_rfc", idem="RB-C", count_id=cid, today=TODAY)
        self.assertEqual(out["adjustments_applied"], 2)


class ScenarioTests(unittest.TestCase):
    def test_p1_scenario_checkpoints_and_reconciliation(self):
        with tempfile.TemporaryDirectory() as d:
            conn, ran, failures, mismatches = scen.run(str(Path(d) / "p1.db"))
            self.assertEqual(failures, [])
            self.assertEqual(mismatches, [])
            self.assertEqual(ran, 26)
            conn.close()


class QuantityAndDateGuardTests(DbCase):
    def test_fractional_count_rejected_before_any_write(self):
        cid = self.start_count("counter_rfc", "RFC", "Main storage", "frac")
        with self.assertRaises(Rejected) as ctx:
            self.submit("counter_rfc", cid, "P-002", "AP-2026A", 39.5, 1)
        self.assertEqual(ctx.exception.code, "BAD_QUANTITY")
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM count_attempt").fetchone()[0], 0)

    def test_boolean_count_rejected(self):
        cid = self.start_count("counter_rfc", "RFC", "Main storage", "bool")
        with self.assertRaises(Rejected) as ctx:
            self.submit("counter_rfc", cid, "P-002", "AP-2026A", True, 1)
        self.assertEqual(ctx.exception.code, "BAD_QUANTITY")

    def test_fractional_boolean_and_zero_issue_quantities_rejected(self):
        for bad in (1.5, True, 0, -1):
            with self.subTest(qty=bad):
                with self.assertRaises(Rejected) as ctx:
                    svc.issue(self.conn, "counter_rfc", idem=f"q{bad}", sku="P-002", loc="RFC",
                              area="Main storage", qty=bad, today=TODAY)
                self.assertEqual(ctx.exception.code, "BAD_QUANTITY")
        self.assertEqual(self.on_hand("P-002", "AP-2026A", "RFC", "Main storage"), 40)

    def test_schema_rejects_real_balance_quantity(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute("UPDATE balance SET on_hand = 39.5 WHERE balance_id = (SELECT MIN(balance_id) FROM balance)")

    def test_schema_rejects_real_movement_delta(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute("""INSERT INTO movement (transaction_id, balance_id, delta_on_hand, reason, occurred_at)
                SELECT transaction_id, balance_id, 1.5, 'x', 'x' FROM movement LIMIT 1""")

    def test_surplus_line_rejects_invalid_expiration(self):
        cid = self.start_count("counter_rfc", "RFC", "Main storage", "date")
        for bad in ("not-a-date", "2028-02-30", "2028-1-5", 20280131):
            with self.subTest(exp=bad):
                with self.assertRaises(Rejected) as ctx:
                    svc.add_count_line(self.conn, "counter_rfc", idem=f"d{bad}", count_id=cid, sku="P-002",
                                       lot="AP-2028C", pending_exp=bad, today=TODAY)
                self.assertEqual(ctx.exception.code, "BAD_DATE")
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM count_line WHERE lot_number = 'AP-2028C'").fetchone()[0], 0)

    def test_schema_rejects_invalid_lot_date(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute("""INSERT INTO lot (product_id, lot_number, expiration_date)
                SELECT product_id, 'BADDATE', 'not-a-date' FROM product WHERE sku = 'P-001'""")


class CancellationTests(DbCase):
    def test_cancel_releases_area_without_stock_change_and_keeps_history(self):
        cid = self.start_count("counter_rfc", "RFC", "Main storage", "cx")
        self.submit("counter_rfc", cid, "P-002", "AP-2026A", 37, 1)  # out: pending count_out adjustment
        svc.add_count_line(self.conn, "counter_rfc", idem="cx-s", count_id=cid, sku="P-002", lot="AP-2028C",
                           pending_exp="2028-01-31", today=TODAY)
        self.submit("counter_rfc", cid, "P-002", "AP-2028C", 2, 1)  # surplus pending count_within adjustment
        before = self.counts()
        out = svc.cancel_count(self.conn, "counter_rfc", idem="cx-cancel", count_id=cid, today=TODAY)
        self.assertEqual(out["adjustments_cancelled"], 2)
        self.assertEqual(self.counts()["movement"], before["movement"])
        self.assertEqual(self.counts()["stock_transaction"], before["stock_transaction"])
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM lot WHERE lot_number = 'AP-2028C'").fetchone()[0], 0)
        self.assertEqual(self.conn.execute("SELECT status FROM stock_count WHERE count_id = ?", (cid,)).fetchone()[0], "cancelled")
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM count_line WHERE count_id = ?", (cid,)).fetchone()[0], 5)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM count_attempt").fetchone()[0], 2)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM adjustment WHERE status = 'cancelled'").fetchone()[0], 2)
        # The area is released: ordinary stock operations work again.
        svc.issue(self.conn, "counter_rfc", idem="cx-issue", sku="P-002", loc="RFC", area="Main storage", qty=1, today=TODAY)
        self.assertEqual(self.on_hand("P-002", "AP-2026A", "RFC", "Main storage"), 39)

    def test_cancelled_count_cannot_complete_or_cancel_again(self):
        cid = self.start_count("counter_rfc", "RFC", "Main storage", "cx2")
        svc.cancel_count(self.conn, "counter_rfc", idem="cx2-a", count_id=cid, today=TODAY)
        with self.assertRaises(Rejected) as ctx:
            svc.complete_count(self.conn, "counter_rfc", idem="cx2-c", count_id=cid, today=TODAY)
        self.assertEqual(ctx.exception.code, "COUNT_NOT_OPEN")
        with self.assertRaises(Rejected) as ctx:
            svc.cancel_count(self.conn, "counter_rfc", idem="cx2-b", count_id=cid, today=TODAY)
        self.assertEqual(ctx.exception.code, "COUNT_NOT_OPEN")

    def test_cancel_unknown_count_rejected(self):
        with self.assertRaises(Rejected) as ctx:
            svc.cancel_count(self.conn, "counter_rfc", idem="cx3", count_id=999, today=TODAY)
        self.assertEqual(ctx.exception.code, "UNKNOWN_COUNT")


class LoaderValidationTests(DbCase):
    """Rejections are tested against a database that already holds inventory, and against an empty one."""

    def _file(self, name, payload):
        p = Path(self._tmp.name) / name
        p.write_bytes(payload if isinstance(payload, bytes) else json.dumps(payload).encode("utf-8"))
        return p

    def _valid(self):
        return json.loads(OPENING.read_text(encoding="utf-8"))

    def _assert_unchanged_and_rejected(self, before, path, field=None):
        with self.assertRaises(ImportRejected) as ctx:
            load_opening(self.conn, path)
        if field:
            self.assertTrue(any(e["field"] == field for e in ctx.exception.errors), ctx.exception.errors)
        self.assertEqual(self.counts(), before)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM import_attempt WHERE status = 'rejected'").fetchone()[0],
                         self._rejected_before + 1)

    def setUp(self):
        super().setUp()
        self._rejected_before = self.conn.execute("SELECT COUNT(*) FROM import_attempt WHERE status = 'rejected'").fetchone()[0]

    def test_malformed_json_rejected_on_populated_database(self):
        before = self.counts()
        self._assert_unchanged_and_rejected(before, self._file("m.json", b"{not json"), field="file")

    def test_non_utf8_file_rejected(self):
        before = self.counts()
        self._assert_unchanged_and_rejected(before, self._file("u.json", b"\xff\xfe\x00"), field="file")

    def test_non_object_top_level_rejected(self):
        before = self.counts()
        self._assert_unchanged_and_rejected(before, self._file("l.json", []), field="document")

    def test_boolean_opening_quantity_rejected(self):
        data = self._valid()
        data["opening_balances"][0]["id"] = "X1"
        data["opening_balances"][0]["qty"] = True
        before = self.counts()
        self._assert_unchanged_and_rejected(before, self._file("b.json", data), field="qty")

    def test_wrong_types_rejected_before_any_write(self):
        cases = {
            "product_missing_name": lambda d: d["products"]["P-001"].pop("name"),
            "product_flag_not_bool": lambda d: d["products"]["P-001"].update(expiration_required="no"),
            "user_unknown_role": lambda d: d["users"]["counter_rfc"].update(role="admin"),
            "user_unknown_location": lambda d: d["users"]["counter_rfc"].update(location="ZZZ"),
            "threshold_as_text": lambda d: d["reorder_thresholds"][0].update(threshold="5"),
            "threshold_boolean": lambda d: d["reorder_thresholds"][0].update(threshold=False),
            "conversion_missing_label": lambda d: d.update(unit_conversions=[{"product": "P-002", "purchase_unit": "case", "factor": 12}]),
            "conversion_boolean_factor": lambda d: d.update(unit_conversions=[{"product": "P-002", "purchase_unit": "case", "factor": True, "label": "test"}]),
            "conversion_fraction": lambda d: d.update(unit_conversions=[{"product": "P-002", "purchase_unit": "case", "factor": 12.5, "label": "test"}]),
            "area_missing": lambda d: d["areas"].append({"location": "RFC"}),
            "locations_not_object": lambda d: d.update(locations=[]),
            "row_not_object": lambda d: d["opening_balances"].append("oops"),
        }
        for name, mutate in cases.items():
            with self.subTest(case=name):
                data = self._valid()
                data["opening_balances"][0]["id"] = name  # distinct file hash per case
                mutate(data)
                before = self.counts()
                with self.assertRaises(ImportRejected):
                    load_opening(self.conn, self._file(f"{name}.json", data))
                self.assertEqual(self.counts(), before)

    def test_populated_database_refuses_a_valid_second_file(self):
        data = self._valid()
        data["note"] = "second copy"  # different hash, otherwise valid
        before = self.counts()
        self._assert_unchanged_and_rejected(before, self._file("second.json", data), field="database")

    def test_demo_opening_file_keeps_totals_and_labels_the_case_conversion(self):
        # The approved opening file: P-002 is 100 boxes, the case conversion is a labelled demo assumption,
        # and the whole opening is 599 base units (the conversion adds no stock).
        with tempfile.TemporaryDirectory() as d:
            conn = create_new_database(str(Path(d) / "demo.db"))
            load_opening(conn)
            total = conn.execute("SELECT SUM(delta_on_hand) FROM movement").fetchone()[0]
            p2 = conn.execute("""SELECT SUM(m.delta_on_hand) FROM movement m JOIN balance b USING (balance_id)
                                 JOIN lot l USING (lot_id) JOIN product p USING (product_id)
                                 WHERE p.sku = 'P-002'""").fetchone()[0]
            factor, label = conn.execute("SELECT factor, label FROM unit_conversion").fetchone()
            conn.close()
        self.assertEqual(total, 599)
        self.assertEqual(p2, 100)
        self.assertEqual(factor, 12)
        self.assertIn("Demo packaging assumption", label)

    def test_valid_unit_conversion_loads_into_an_empty_database(self):
        with tempfile.TemporaryDirectory() as d:
            conn = create_new_database(str(Path(d) / "c.db"))
            data = self._valid()
            data["unit_conversions"] = [{"product": "P-002", "purchase_unit": "case", "factor": 12, "label": "test"}]
            load_opening(conn, self._file("conv.json", data))
            self.assertEqual(conn.execute("SELECT factor FROM unit_conversion").fetchone()[0], 12)
            conn.close()

    def test_schema_rejects_real_conversion_factor(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute("""INSERT INTO unit_conversion (product_id, purchase_unit, factor, label)
                SELECT product_id, 'pack', 12.5, 'test' FROM product WHERE sku = 'P-002'""")



class ReceivingTests(DbCase):
    def setUp(self):
        super().setUp()

    def receive(self, idem, lines, loc="RFC", area="Main storage", **kw):
        return svc.receive(self.conn, "counter_rfc", idem=idem, loc=loc, area=area, lines=lines, today=TODAY, **kw)

    def test_multi_line_receipt_converts_units_and_keeps_the_conversion(self):
        out = self.receive("R1", [
            {"sku": "P-002", "lot": "AP-2026A", "expiration": "2026-11-20", "purchase_unit": "case", "purchase_qty": 2},
            {"sku": "P-001", "lot": "NG-LOT-1", "base_qty": 10},
        ], packing_slip="PS-1001")
        self.assertEqual([l["base_qty"] for l in out["lines"]], [24, 10])
        self.assertEqual(self.on_hand("P-002", "AP-2026A", "RFC", "Main storage"), 64)
        self.assertEqual(self.on_hand("P-001", "NG-LOT-1", "RFC", "Main storage"), 31)
        rows = self.conn.execute("SELECT purchase_unit, purchase_qty, factor, base_qty FROM receipt_line ORDER BY receipt_line_id").fetchall()
        self.assertEqual(rows, [("case", 2, 12, 24), (None, None, 1, 10)])
        self.assertEqual(self.conn.execute("SELECT packing_slip_ref FROM stock_transaction WHERE transaction_id = ?",
                                           (out["transaction_id"],)).fetchone()[0], "PS-1001")
        self.assertEqual(scen.reconcile(self.conn), [])

    def test_new_lot_with_expiration_is_created_by_receiving(self):
        self.receive("R2", [{"sku": "P-002", "lot": "AP-2029A", "expiration": "2029-01-31",
                             "purchase_unit": "case", "purchase_qty": 1}])
        self.assertEqual(self.on_hand("P-002", "AP-2029A", "RFC", "Main storage"), 12)

    def test_additional_delivery_of_existing_lot_with_agreeing_expiration(self):
        self.receive("R3", [{"sku": "P-002", "lot": "AP-2026A", "expiration": "2026-11-20", "base_qty": 5}])
        self.assertEqual(self.on_hand("P-002", "AP-2026A", "RFC", "Main storage"), 45)

    def test_expiration_conflicts_are_rejected_without_change(self):
        before = self.counts()
        with self.assertRaises(Rejected) as ctx:
            self.receive("R4", [{"sku": "P-002", "lot": "AP-2026A", "expiration": "2027-01-01", "base_qty": 5}])
        self.assertEqual(ctx.exception.code, "EXPIRY_CONFLICT")
        with self.assertRaises(Rejected) as ctx:
            self.receive("R5", [{"sku": "P-002", "lot": "AP-2026A", "base_qty": 5}])
        self.assertEqual(ctx.exception.code, "EXPIRY_CONFLICT")
        with self.assertRaises(Rejected) as ctx:
            self.receive("R6", [{"sku": "P-002", "lot": "AP-2030X", "base_qty": 5}])
        self.assertEqual(ctx.exception.code, "EXPIRY_REQUIRED")
        self.assertEqual(self.counts(), before)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM rejected_request WHERE operation = 'receipt'").fetchone()[0], 3)

    def test_invalid_receipt_lines_are_rejected_without_change(self):
        cases = [
            ([], "BAD_LINES"),
            ([{"sku": "P-002", "lot": "AP-2026A", "base_qty": 1.5}], "BAD_QUANTITY"),
            ([{"sku": "P-002", "lot": "AP-2026A", "base_qty": True}], "BAD_QUANTITY"),
            ([{"sku": "P-002", "lot": "AP-2026A", "base_qty": 0}], "BAD_QUANTITY"),
            ([{"sku": "P-002", "lot": "AP-2026A", "purchase_unit": "case", "purchase_qty": 2.0}], "BAD_QUANTITY"),
            ([{"sku": "P-002", "lot": "AP-2026A", "purchase_unit": "pallet", "purchase_qty": 1}], "UNKNOWN_UNIT"),
            ([{"sku": "P-002", "lot": "AP-2026A", "base_qty": 1, "purchase_unit": "case"}], "BAD_LINE"),
            ([{"sku": "P-002", "lot": " ", "base_qty": 1}], "BAD_LINE"),
            ([{"sku": "P-002", "lot": "AP-2029A", "expiration": "not-a-date", "base_qty": 1}], "BAD_DATE"),
            ([{"sku": "P-002", "lot": "AP-2029A", "expiration": "2029-02-30", "base_qty": 1}], "BAD_DATE"),
            ([{"sku": "P-002", "lot": "AP-2026A", "expiration": "2026-11-20", "base_qty": 1},
              {"sku": "P-002", "lot": "AP-2026A", "expiration": "2026-11-20", "base_qty": 1}], "DUPLICATE_LINE"),
            ([{"sku": "NOPE", "lot": "X", "base_qty": 1}], "UNKNOWN_PRODUCT"),
        ]
        before = self.counts()
        for i, (lines, code) in enumerate(cases):
            with self.subTest(code=code, i=i):
                with self.assertRaises(Rejected) as ctx:
                    self.receive(f"bad-{i}", lines)
                self.assertEqual(ctx.exception.code, code)
        self.assertEqual(self.counts(), before)

    def test_receiving_into_a_count_locked_area_is_rejected(self):
        self.start_count("counter_rfc", "RFC", "Main storage", "rlock")
        with self.assertRaises(Rejected) as ctx:
            self.receive("RL1", [{"sku": "P-001", "lot": "NG-LOT-1", "base_qty": 1}])
        self.assertEqual(ctx.exception.code, "COUNT_OPEN")
        out = svc.receive(self.conn, "counter_hhc", idem="RL2", loc="HHC", area="Main storage",
                          lines=[{"sku": "P-001", "lot": "NG-LOT-2", "base_qty": 1}], today=TODAY)
        self.assertEqual(out["lines"][0]["base_qty"], 1)

    def test_duplicate_submission_returns_the_original_result(self):
        lines = [{"sku": "P-001", "lot": "NG-LOT-1", "base_qty": 10}]
        first = self.receive("RD", lines)
        n_mov = self.conn.execute("SELECT COUNT(*) FROM movement").fetchone()[0]
        again = self.receive("RD", lines)
        self.assertEqual(first, again)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM movement").fetchone()[0], n_mov)
        self.assertEqual(self.on_hand("P-001", "NG-LOT-1", "RFC", "Main storage"), 31)
        with self.assertRaises(Rejected) as ctx:
            self.receive("RD", [{"sku": "P-001", "lot": "NG-LOT-1", "base_qty": 11}])
        self.assertEqual(ctx.exception.code, "KEY_CONFLICT")

    def test_failure_midway_through_receiving_rolls_back_every_line(self):
        before = self.counts()
        before_on = (self.on_hand("P-002", "AP-2026A", "RFC", "Main storage"),
                     self.on_hand("P-001", "NG-LOT-1", "RFC", "Main storage"))
        lines = [
            {"sku": "P-002", "lot": "AP-2029A", "expiration": "2029-01-31", "purchase_unit": "case", "purchase_qty": 1},
            {"sku": "P-001", "lot": "NG-LOT-1", "base_qty": 10},
            {"sku": "P-002", "lot": "AP-2026A", "expiration": "2026-11-20", "base_qty": 3},
        ]
        with self.assertRaises(RuntimeError):
            self.receive("RX", lines, fault_after=2)
        self.assertEqual(self.counts(), before)
        self.assertEqual((self.on_hand("P-002", "AP-2026A", "RFC", "Main storage"),
                          self.on_hand("P-001", "NG-LOT-1", "RFC", "Main storage")), before_on)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM lot WHERE lot_number = 'AP-2029A'").fetchone()[0], 0)
        # The key was rolled back too, so the same receipt can be retried cleanly.
        out = self.receive("RX", lines)
        self.assertEqual(len(out["lines"]), 3)
        self.assertEqual(self.on_hand("P-002", "AP-2029A", "RFC", "Main storage"), 12)
        self.assertEqual(scen.reconcile(self.conn), [])

    def test_schema_rejects_inconsistent_receipt_conversion(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute("""INSERT INTO receipt_line (transaction_id, lot_id, storage_area_id, purchase_unit,
                purchase_qty, factor, base_qty) SELECT transaction_id, lot_id, storage_area_id, 'case', 2, 12, 13
                FROM movement JOIN balance USING (balance_id) LIMIT 1""")


    def test_duplicate_packing_slip_warns_but_is_still_accepted(self):
        first = self.receive("PS-1", [{"sku": "P-001", "lot": "NG-LOT-1", "base_qty": 1}], packing_slip="SLIP-7")
        self.assertEqual(first["warnings"], [])
        second = self.receive("PS-2", [{"sku": "P-001", "lot": "NG-LOT-1", "base_qty": 1}], packing_slip="SLIP-7")
        self.assertEqual(second["warnings"][0]["code"], "DUPLICATE_PACKING_SLIP")
        self.assertEqual(self.on_hand("P-001", "NG-LOT-1", "RFC", "Main storage"), 23)
        with self.assertRaises(Rejected) as ctx:
            self.receive("PS-3", [{"sku": "P-001", "lot": "NG-LOT-1", "base_qty": 1}], packing_slip="  ")
        self.assertEqual(ctx.exception.code, "BAD_PACKING_SLIP")


class ParallelWriterTests(unittest.TestCase):
    """Several connections write to the same database file at once (SQLite serializes writers
    with BEGIN IMMEDIATE and a busy timeout). These tests check that no stock change is lost or doubled."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.path = str(Path(self._tmp.name) / "parallel.db")
        conn = create_new_database(self.path)
        load_opening(conn)
        conn.close()

    def tearDown(self):
        self._tmp.cleanup()

    def _run_threads(self, fn, n):
        import threading
        errors, results = [], []
        lock = threading.Lock()

        def worker(i):
            c = svc.connect(self.path)
            try:
                out = fn(c, i)
                with lock:
                    results.append(out)
            except Exception as exc:  # collected and asserted below
                with lock:
                    errors.append(exc)
            finally:
                c.close()

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(n)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        return results, errors

    def test_parallel_distinct_issues_lose_no_stock(self):
        n = 8
        results, errors = self._run_threads(lambda c, i: svc.issue(
            c, "counter_rfc", idem=f"par-{i}", sku="P-002", loc="RFC", area="Main storage", qty=1, today=TODAY), n)
        self.assertEqual(errors, [])
        c = svc.connect(self.path)
        self.assertEqual(c.execute("""SELECT SUM(b.on_hand) FROM balance b JOIN lot l ON l.lot_id = b.lot_id
            WHERE l.lot_number = 'AP-2026A'""").fetchone()[0], 40 - n)
        self.assertEqual(scen.reconcile(c), [])
        c.close()

    def test_parallel_duplicate_submissions_apply_once(self):
        n = 8
        results, errors = self._run_threads(lambda c, i: svc.issue(
            c, "counter_rfc", idem="same-key", sku="P-002", loc="RFC", area="Main storage", qty=3, today=TODAY), n)
        self.assertEqual(errors, [])
        self.assertEqual(len({json.dumps(r, sort_keys=True) for r in results}), 1)  # every caller got the same saved result
        c = svc.connect(self.path)
        self.assertEqual(c.execute("SELECT COUNT(*) FROM stock_transaction WHERE operation_type = 'issue'").fetchone()[0], 1)
        self.assertEqual(c.execute("""SELECT on_hand FROM balance WHERE lot_id =
            (SELECT lot_id FROM lot WHERE lot_number = 'AP-2026A')""").fetchone()[0], 37)
        c.close()

    def test_parallel_receipts_and_issues_on_the_same_balance_reconcile(self):
        def op(c, i):
            if i % 2:
                return svc.issue(c, "counter_rfc", idem=f"mix-i-{i}", sku="P-001", loc="RFC", area="Main storage", qty=1, today=TODAY)
            return svc.receive(c, "counter_rfc", idem=f"mix-r-{i}", loc="RFC", area="Main storage",
                               lines=[{"sku": "P-001", "lot": "NG-LOT-1", "base_qty": 2}], today=TODAY)
        results, errors = self._run_threads(op, 10)
        self.assertEqual(errors, [])
        c = svc.connect(self.path)
        self.assertEqual(c.execute("""SELECT on_hand FROM balance WHERE lot_id =
            (SELECT lot_id FROM lot WHERE lot_number = 'NG-LOT-1')""").fetchone()[0], 21 + 5 * 2 - 5)
        self.assertEqual(scen.reconcile(c), [])
        c.close()

if __name__ == "__main__":
    unittest.main(verbosity=2)
