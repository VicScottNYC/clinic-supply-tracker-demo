export interface Reference {
  clock: { mode: "current" | "fixed"; demo: boolean; evaluation_date: string; evaluation_timestamp_utc: string;
           timezone: string; expiring_window_days: number };
  locations: { code: string; name: string; kind: string }[];
  areas: { location: string; area: string }[];
  products: Product[];
  actors: { user_id: string; display_name: string; roles: { location: string; role: string }[] }[];
}

export interface Product {
  sku: string;
  name: string;
  base_unit: string;
  expiry_required: boolean;
  conversions: { purchase_unit: string; factor: number; label: string }[];
}

export interface BalanceProduct {
  sku: string; product: string; base_unit: string;
  on_hand: number; available: number; expired: number; expiring_soon: number;
}

export interface BalanceLot {
  sku: string; product: string; base_unit: string; lot: string; expiration_date: string | null; area: string;
  on_hand: number; held: number; available: number; category: "expired" | "expiring_soon" | "none";
}

export interface Balances {
  location: string; as_of: string; products: BalanceProduct[]; balances: BalanceLot[];
}

export interface Movement {
  sku: string; lot: string; area: string; delta_on_hand: number; delta_held: number; reason: string; occurred_at: string;
}

export interface Transaction {
  transaction_id: number; operation: string; created_at: string; actor: string;
  packing_slip: string | null; count_id: number | null; movements: Movement[];
}

export interface CountSummary { count_id: number; status: "open" | "completed" | "cancelled"; area: string;
                                opened_at: string; closed_at: string | null; }

export interface CountLine {
  count_line_id: number; sku: string; product: string; base_unit: string; lot: string;
  pending_expiration_date: string | null; expected_qty: number;
  state: "pending" | "within" | "out" | "resolved_recount" | "adjusted";
  last_counted_qty: number | null; attempts: number; unresolved: boolean;
}

export interface CountDetail {
  count_id: number; status: string; area: string; location: string; opened_at: string;
  area_locked: boolean; unresolved_lines: number; lines: CountLine[];
}

export interface ReceiptResult {
  transaction_id: number;
  lines: { sku: string; lot: string; purchase_unit: string | null; purchase_qty: number | null; factor: number; base_qty: number }[];
  warnings: { code: string; message: string; prior_transaction_ids: number[] }[];
}

export interface IssueResult {
  transaction_id: number;
  issued: number;
  lines: { lot: string; expiration_date: string | null; area: string; base_qty: number }[];
}
