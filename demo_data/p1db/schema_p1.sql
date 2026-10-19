-- P1 schema (SQLite). Derived from docs/step4-data-model.md. P2 tables are not created.
-- Foreign keys are enforced by the connection (PRAGMA foreign_keys = ON in inventory_p1.connect).

CREATE TABLE location (
  location_id INTEGER PRIMARY KEY,
  code TEXT NOT NULL UNIQUE,
  name TEXT NOT NULL,
  kind TEXT NOT NULL CHECK (kind IN ('clinic','supply_hub')),
  active INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0,1))
);

CREATE TABLE storage_area (
  storage_area_id INTEGER PRIMARY KEY,
  location_id INTEGER NOT NULL REFERENCES location(location_id),
  name TEXT NOT NULL,
  UNIQUE (location_id, name)
);

CREATE TABLE product (
  product_id INTEGER PRIMARY KEY,
  sku TEXT NOT NULL UNIQUE,
  name TEXT NOT NULL,
  base_unit TEXT NOT NULL,
  expiry_required INTEGER NOT NULL CHECK (expiry_required IN (0,1))
);

CREATE TABLE unit_conversion (
  conversion_id INTEGER PRIMARY KEY,
  product_id INTEGER NOT NULL REFERENCES product(product_id),
  purchase_unit TEXT NOT NULL,
  factor INTEGER NOT NULL CHECK (typeof(factor) = 'integer' AND factor > 0),
  label TEXT NOT NULL DEFAULT '' CHECK (typeof(label) = 'text'),
  UNIQUE (product_id, purchase_unit)
);

CREATE TABLE reorder_threshold (
  product_id INTEGER NOT NULL REFERENCES product(product_id),
  location_id INTEGER NOT NULL REFERENCES location(location_id),
  min_qty INTEGER NOT NULL CHECK (typeof(min_qty) = 'integer' AND min_qty >= 0),
  PRIMARY KEY (product_id, location_id)
);

CREATE TABLE app_user (
  user_id TEXT PRIMARY KEY,
  display_name TEXT NOT NULL,
  active INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0,1))
);

CREATE TABLE user_role (
  user_role_id INTEGER PRIMARY KEY,
  user_id TEXT NOT NULL REFERENCES app_user(user_id),
  location_id INTEGER NOT NULL REFERENCES location(location_id),
  role TEXT NOT NULL CHECK (role IN ('supply_clerk','clinic_nurse','clinic_manager','administrator')),
  UNIQUE (user_id, location_id, role)
);

CREATE TABLE lot (
  lot_id INTEGER PRIMARY KEY,
  product_id INTEGER NOT NULL REFERENCES product(product_id),
  lot_number TEXT NOT NULL,
  expiration_date TEXT NULL CHECK (expiration_date IS NULL OR (date(expiration_date) IS NOT NULL AND date(expiration_date) = expiration_date)),
  UNIQUE (product_id, lot_number)
);

CREATE TRIGGER lot_expiry_required_insert BEFORE INSERT ON lot
WHEN NEW.expiration_date IS NULL
 AND EXISTS (SELECT 1 FROM product p WHERE p.product_id = NEW.product_id AND p.expiry_required = 1)
BEGIN SELECT RAISE(ABORT, 'expiration date required for this product'); END;

CREATE TRIGGER lot_expiry_required_update BEFORE UPDATE OF expiration_date ON lot
WHEN NEW.expiration_date IS NULL
 AND EXISTS (SELECT 1 FROM product p WHERE p.product_id = NEW.product_id AND p.expiry_required = 1)
BEGIN SELECT RAISE(ABORT, 'expiration date required for this product'); END;

CREATE TRIGGER lot_expiry_immutable BEFORE UPDATE OF expiration_date ON lot
WHEN OLD.expiration_date IS NOT NEW.expiration_date
BEGIN SELECT RAISE(ABORT, 'lot expiration date is immutable'); END;

CREATE TABLE balance (
  balance_id INTEGER PRIMARY KEY,
  lot_id INTEGER NOT NULL REFERENCES lot(lot_id),
  storage_area_id INTEGER NOT NULL REFERENCES storage_area(storage_area_id),
  on_hand INTEGER NOT NULL CHECK (typeof(on_hand) = 'integer' AND on_hand >= 0),
  held INTEGER NOT NULL DEFAULT 0 CHECK (typeof(held) = 'integer' AND held >= 0),
  CHECK (held <= on_hand),
  UNIQUE (lot_id, storage_area_id)
);

CREATE TABLE submission_key (
  submission_key_id INTEGER PRIMARY KEY,
  actor_id TEXT NOT NULL REFERENCES app_user(user_id),
  operation TEXT NOT NULL CHECK (operation IN ('receipt','issue','count_start','count_add_line','count_submit','count_recount','count_acknowledge','count_complete','count_cancel','hold','release','discard','adjustment','dispatch','transfer_receive','transfer_return','loss_write_off','import','approval_decision')),
  idempotency_key TEXT NOT NULL,
  request_hash TEXT NOT NULL,
  response_json TEXT NULL,
  created_at TEXT NOT NULL,
  UNIQUE (actor_id, operation, idempotency_key)
);

CREATE TABLE stock_count (
  count_id INTEGER PRIMARY KEY,
  storage_area_id INTEGER NOT NULL REFERENCES storage_area(storage_area_id),
  status TEXT NOT NULL CHECK (status IN ('open','completed','cancelled')),
  opened_by TEXT NOT NULL REFERENCES app_user(user_id),
  opened_at TEXT NOT NULL,
  closed_at TEXT NULL,
  reminder_flagged_at TEXT NULL
);
CREATE UNIQUE INDEX one_open_count_per_area ON stock_count(storage_area_id) WHERE status = 'open';

CREATE TABLE stock_transaction (
  transaction_id INTEGER PRIMARY KEY,
  submission_key_id INTEGER NOT NULL UNIQUE REFERENCES submission_key(submission_key_id),
  operation_type TEXT NOT NULL CHECK (operation_type IN ('receipt','issue','count_completion','adjustment','opening','dispatch','transfer_receipt','return_dispatch','return_receipt','hold','release','discard','loss_write_off')),
  location_id INTEGER NOT NULL REFERENCES location(location_id),
  actor_id TEXT NOT NULL REFERENCES app_user(user_id),
  packing_slip_ref TEXT NULL,
  count_id INTEGER NULL REFERENCES stock_count(count_id),
  created_at TEXT NOT NULL
);

CREATE TABLE movement (
  movement_id INTEGER PRIMARY KEY,
  transaction_id INTEGER NOT NULL REFERENCES stock_transaction(transaction_id),
  balance_id INTEGER NOT NULL REFERENCES balance(balance_id),
  delta_on_hand INTEGER NOT NULL DEFAULT 0 CHECK (typeof(delta_on_hand) = 'integer'),
  delta_held INTEGER NOT NULL DEFAULT 0 CHECK (typeof(delta_held) = 'integer'),
  delta_in_transit INTEGER NOT NULL DEFAULT 0 CHECK (delta_in_transit = 0),
  reason TEXT NOT NULL,
  occurred_at TEXT NOT NULL,
  CHECK (NOT (delta_on_hand = 0 AND delta_held = 0))
);

CREATE TRIGGER movement_no_update BEFORE UPDATE ON movement
BEGIN SELECT RAISE(ABORT, 'movement is append-only'); END;

CREATE TRIGGER movement_no_delete BEFORE DELETE ON movement
BEGIN SELECT RAISE(ABORT, 'movement is append-only'); END;

CREATE TABLE issue_line (
  issue_line_id INTEGER PRIMARY KEY,
  transaction_id INTEGER NOT NULL REFERENCES stock_transaction(transaction_id),
  lot_id INTEGER NOT NULL REFERENCES lot(lot_id),
  storage_area_id INTEGER NOT NULL REFERENCES storage_area(storage_area_id),
  base_qty INTEGER NOT NULL CHECK (typeof(base_qty) = 'integer' AND base_qty > 0)
);

CREATE TABLE count_line (
  count_line_id INTEGER PRIMARY KEY,
  count_id INTEGER NOT NULL REFERENCES stock_count(count_id),
  product_id INTEGER NOT NULL REFERENCES product(product_id),
  lot_number TEXT NOT NULL,
  lot_id INTEGER NULL REFERENCES lot(lot_id),
  pending_expiration_date TEXT NULL CHECK (pending_expiration_date IS NULL OR (date(pending_expiration_date) IS NOT NULL AND date(pending_expiration_date) = pending_expiration_date)),
  storage_area_id INTEGER NOT NULL REFERENCES storage_area(storage_area_id),
  balance_id INTEGER NULL REFERENCES balance(balance_id),
  expected_qty INTEGER NOT NULL CHECK (typeof(expected_qty) = 'integer' AND expected_qty >= 0),
  state TEXT NOT NULL DEFAULT 'pending' CHECK (state IN ('pending','within','out','resolved_recount','adjusted')),
  UNIQUE (count_id, storage_area_id, product_id, lot_number)
);

CREATE TABLE count_attempt (
  count_attempt_id INTEGER PRIMARY KEY,
  count_line_id INTEGER NOT NULL REFERENCES count_line(count_line_id),
  attempt_no INTEGER NOT NULL CHECK (attempt_no >= 1),
  counted_qty INTEGER NOT NULL CHECK (typeof(counted_qty) = 'integer' AND counted_qty >= 0),
  variance INTEGER NOT NULL CHECK (typeof(variance) = 'integer'),
  within_tolerance INTEGER NOT NULL CHECK (within_tolerance IN (0,1)),
  submitted_by TEXT NOT NULL REFERENCES app_user(user_id),
  submitted_at TEXT NOT NULL,
  submission_key_id INTEGER NOT NULL REFERENCES submission_key(submission_key_id),
  UNIQUE (count_line_id, attempt_no)
);

CREATE TABLE adjustment (
  adjustment_id INTEGER PRIMARY KEY,
  count_attempt_id INTEGER NULL REFERENCES count_attempt(count_attempt_id),
  lot_id INTEGER NULL REFERENCES lot(lot_id),
  storage_area_id INTEGER NULL REFERENCES storage_area(storage_area_id),
  balance_id INTEGER NULL REFERENCES balance(balance_id),
  direction TEXT NOT NULL CHECK (direction IN ('increase','decrease')),
  qty INTEGER NOT NULL CHECK (typeof(qty) = 'integer' AND qty > 0),
  source TEXT NOT NULL CHECK (source IN ('count_within','count_out','manual')),
  reason TEXT NOT NULL,
  requested_by TEXT NOT NULL REFERENCES app_user(user_id),
  decided_by TEXT NULL REFERENCES app_user(user_id),
  decided_at TEXT NULL,
  status TEXT NOT NULL CHECK (status IN ('pending','approved','rejected','applied','cancelled','superseded')),
  transaction_id INTEGER NULL REFERENCES stock_transaction(transaction_id),
  superseded_at TEXT NULL,
  superseded_by_attempt_id INTEGER NULL REFERENCES count_attempt(count_attempt_id),
  CHECK (source <> 'count_within' OR decided_by IS NULL),
  CHECK (source = 'count_within' OR status IN ('pending','cancelled','superseded') OR decided_by IS NOT NULL),
  CHECK (decided_by IS NULL OR decided_by <> requested_by),
  CHECK (status <> 'applied' OR transaction_id IS NOT NULL),
  CHECK ((source = 'manual' AND lot_id IS NOT NULL AND storage_area_id IS NOT NULL AND count_attempt_id IS NULL)
      OR (source <> 'manual' AND count_attempt_id IS NOT NULL))
);
CREATE UNIQUE INDEX one_adjustment_per_attempt ON adjustment(count_attempt_id) WHERE count_attempt_id IS NOT NULL;

CREATE TABLE receipt_line (
  receipt_line_id INTEGER PRIMARY KEY,
  transaction_id INTEGER NOT NULL REFERENCES stock_transaction(transaction_id),
  lot_id INTEGER NOT NULL REFERENCES lot(lot_id),
  storage_area_id INTEGER NOT NULL REFERENCES storage_area(storage_area_id),
  purchase_unit TEXT NULL,
  purchase_qty INTEGER NULL CHECK (purchase_qty IS NULL OR (typeof(purchase_qty) = 'integer' AND purchase_qty > 0)),
  factor INTEGER NOT NULL CHECK (typeof(factor) = 'integer' AND factor > 0),
  base_qty INTEGER NOT NULL CHECK (typeof(base_qty) = 'integer' AND base_qty > 0),
  CHECK (purchase_qty IS NULL OR purchase_qty * factor = base_qty)
);

CREATE TABLE import_attempt (
  import_attempt_id INTEGER PRIMARY KEY,
  file_hash TEXT NOT NULL,
  file_name TEXT NOT NULL,
  actor_id TEXT NOT NULL REFERENCES app_user(user_id),
  attempted_at TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('accepted','rejected')),
  error_list TEXT NULL
);
CREATE UNIQUE INDEX one_accepted_import_per_file ON import_attempt(file_hash) WHERE status = 'accepted';

CREATE TABLE import_batch (
  import_batch_id INTEGER PRIMARY KEY,
  import_attempt_id INTEGER NOT NULL UNIQUE REFERENCES import_attempt(import_attempt_id),
  row_count INTEGER NOT NULL CHECK (row_count >= 0)
);

CREATE TABLE rejected_request (
  rejected_request_id INTEGER PRIMARY KEY,
  actor_id TEXT NULL REFERENCES app_user(user_id),
  operation TEXT NOT NULL,
  reason_code TEXT NOT NULL,
  message TEXT NOT NULL,
  attempted_at TEXT NOT NULL
);
