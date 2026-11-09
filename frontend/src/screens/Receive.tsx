import { useState } from "react";
import { api } from "../api";
import { useApp } from "../context";
import { fieldMessages, useActions } from "../hooks";
import { metaOf } from "../pending";
import type { ReceiptResult } from "../types";
import { FieldError } from "../ui";

interface Line {
  id: number;
  sku: string;
  lot: string;
  expiration: string;
  unit: string; // "" = base units; otherwise a purchase unit with a conversion
  qty: string;
}

let lineSeq = 1;
const newLine = (sku: string): Line => ({ id: lineSeq++, sku, lot: "", expiration: "", unit: "", qty: "" });
const WHOLE = /^[1-9]\d*$/;
const DATE = /^\d{4}-\d{2}-\d{2}$/;

// Success text shown above the screens (the store keeps it even if the screen is left before the reply).
function receiptSummary(o: ReceiptResult): string {
  return [
    `Receipt #${o.transaction_id} posted.`,
    ...o.lines.map((l) =>
      `${l.sku} · lot ${l.lot}: ${l.purchase_unit ? `${l.purchase_qty} ${l.purchase_unit} × ${l.factor} = ` : ""}${l.base_qty} base units`),
    ...o.warnings.map((w) => `Possible duplicate delivery: ${w.message}. The receipt was still accepted.`),
  ].join("\n");
}

export function Receive() {
  const { reference, location, actorId, bump } = useApp();
  const products = reference.products;
  const productBySku = new Map(products.map((p) => [p.sku, p]));
  const areas = reference.areas.filter((a) => a.location === location).map((a) => a.area);

  const [area, setArea] = useState(areas[0] ?? "");
  const [slip, setSlip] = useState("");
  const [lines, setLines] = useState<Line[]>(() => [newLine(products[0]?.sku ?? "")]);
  const [clientErrors, setClientErrors] = useState<Record<string, string>>({});
  const { run, busy, uncertain, error } = useActions();
  const serverErrors = fieldMessages(error);

  const update = (id: number, patch: Partial<Line>) =>
    setLines((ls) => ls.map((l) => (l.id === id ? { ...l, ...patch } : l)));

  function buildBody(): { payload: Record<string, unknown> | null; errors: Record<string, string> } {
    const errors: Record<string, string> = {};
    const wire: Record<string, unknown>[] = [];
    for (const ln of lines) {
      const before = Object.keys(errors).length;
      if (!ln.lot.trim()) errors[`${ln.id}.lot`] = "Enter the lot number.";
      if (!WHOLE.test(ln.qty.trim())) errors[`${ln.id}.qty`] = "Enter a whole number of at least 1.";
      if (ln.expiration && !DATE.test(ln.expiration)) errors[`${ln.id}.expiration`] = "Enter a date.";
      if (Object.keys(errors).length === before) {
        const n = Number(ln.qty.trim());
        wire.push({
          sku: ln.sku,
          lot: ln.lot.trim(),
          ...(ln.unit ? { purchase_unit: ln.unit, purchase_qty: n } : { base_qty: n }),
          ...(ln.expiration ? { expiration: ln.expiration } : {}),
        });
      }
    }
    if (!area) errors["area"] = "Choose a storage area.";
    if (Object.keys(errors).length) return { payload: null, errors };
    return {
      payload: { loc: location, area, ...(slip.trim() ? { packing_slip: slip.trim() } : {}), lines: wire },
      errors,
    };
  }

  async function submit() {
    const { payload, errors } = buildBody();
    setClientErrors(errors);
    const firstKey = Object.keys(errors)[0];
    if (firstKey) {
      // Error focus: move to the first invalid field ("area", or "<lineId>.<field>" -> "<field>-<lineId>").
      const [lineId, field] = firstKey.split(".");
      const target = field === "qty" ? `quantity-${lineId}` : field ? `${field}-${lineId}` : "receive-area";
      document.getElementById(target)?.focus();
    }
    if (!payload) return;
    const out = await run(metaOf<ReceiptResult>("Receipt", actorId, location, payload, receiptSummary), (key) =>
      api.post<ReceiptResult>("/receipts", actorId, key, payload));
    if (out) {
      setLines([newLine(products[0]?.sku ?? "")]);
      setSlip("");
      bump();
    }
  }

  const fieldErr = (i: number, field: string, id: number) =>
    clientErrors[`${id}.${field}`] ?? serverErrors[`lines.${i}.${field}`];

  return (
    <section>
      <h2>Receive supplies · {location}</h2>
      <p className="muted">
        One receipt can hold several lines. All lines post together or none do. Quantities may be entered in base
        units or in a purchase unit (converted by the server).
      </p>


      <fieldset disabled={busy || uncertain} className="form-block">
        <div className="grid-form">
          <label>
            Storage area
            <select id="receive-area" value={area} onChange={(e) => setArea(e.target.value)}>
              {areas.map((a) => (
                <option key={a} value={a}>{a}</option>
              ))}
            </select>
            <FieldError message={clientErrors["area"] ?? serverErrors["area"]} />
          </label>
          <label>
            Packing slip reference (optional)
            <input value={slip} onChange={(e) => setSlip(e.target.value)} placeholder="e.g. PS-1001" />
            <FieldError message={serverErrors["packing_slip"]} />
          </label>
        </div>

        <table className="grid lines">
          <thead>
            <tr>
              <th>Product</th>
              <th>Lot</th>
              <th>Expiration</th>
              <th>Unit</th>
              <th className="num">Quantity</th>
              <th><span className="sr-only">Remove</span></th>
            </tr>
          </thead>
          <tbody>
            {lines.map((ln, i) => {
              const p = productBySku.get(ln.sku);
              return (
                <tr key={ln.id}>
                  <td data-label="Product">
                    <select aria-label={`Product for line ${i + 1}`} value={ln.sku} onChange={(e) => update(ln.id, { sku: e.target.value, unit: "" })}>
                      {products.map((pr) => (
                        <option key={pr.sku} value={pr.sku}>{pr.sku} · {pr.name}</option>
                      ))}
                    </select>
                  </td>
                  <td data-label="Lot">
                    <input id={`lot-${ln.id}`} aria-label={`Lot for line ${i + 1}`} value={ln.lot} onChange={(e) => update(ln.id, { lot: e.target.value })} />
                    <FieldError message={fieldErr(i, "lot", ln.id)} />
                  </td>
                  <td data-label="Expiration">
                    <input id={`expiration-${ln.id}`} type="date" aria-label={`Expiration for line ${i + 1}`} value={ln.expiration} onChange={(e) => update(ln.id, { expiration: e.target.value })} />
                    <div className="hint small muted">
                      {p?.expiry_required
                        ? "Required for this product. Must match the recorded date if the lot already exists."
                        : "Not needed for this product."}
                    </div>
                    <FieldError message={fieldErr(i, "expiration", ln.id)} />
                  </td>
                  <td data-label="Unit">
                    <select aria-label={`Unit for line ${i + 1}`} value={ln.unit} onChange={(e) => update(ln.id, { unit: e.target.value })}>
                      <option value="">Base · {p?.base_unit}</option>
                      {p?.conversions.map((c) => (
                        <option key={c.purchase_unit} value={c.purchase_unit}>
                          {c.purchase_unit} (= {c.factor} {p.base_unit})
                        </option>
                      ))}
                    </select>
                    {p?.conversions.find((c) => c.purchase_unit === ln.unit) && (
                      <div className="hint small muted">
                        {p.conversions.find((c) => c.purchase_unit === ln.unit)?.label}
                      </div>
                    )}
                  </td>
                  <td className="num" data-label="Quantity">
                    <input
                      id={`quantity-${ln.id}`}
                      className="qty"
                      inputMode="numeric"
                      value={ln.qty}
                      onChange={(e) => update(ln.id, { qty: e.target.value })}
                      aria-label={`Quantity for line ${i + 1}`}
                    />
                    <FieldError message={fieldErr(i, "base_qty", ln.id) ?? fieldErr(i, "purchase_qty", ln.id) ?? clientErrors[`${ln.id}.qty`]} />
                  </td>
                  <td className="remove-cell">
                    {lines.length > 1 && (
                      <button type="button" className="link" aria-label={`Remove line ${i + 1}`} onClick={() => setLines((ls) => ls.filter((x) => x.id !== ln.id))}>
                        Remove
                      </button>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
        <div className="row">
          <button type="button" onClick={() => setLines((ls) => [...ls, newLine(products[0]?.sku ?? "")])}>
            Add line
          </button>
          {!uncertain && (
            <button type="button" className="primary" onClick={() => void submit()} disabled={busy}>
              {busy ? "Posting…" : "Post receipt"}
            </button>
          )}
        </div>
      </fieldset>

    </section>
  );
}
