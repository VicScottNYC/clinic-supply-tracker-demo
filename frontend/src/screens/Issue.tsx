import { useState } from "react";
import { api } from "../api";
import { useApp } from "../context";
import { fieldMessages, useActions, useFetch } from "../hooks";
import { metaOf } from "../pending";
import { qty } from "../format";
import type { Balances, IssueResult } from "../types";
import { Banner, FieldError, Loading } from "../ui";

const WHOLE = /^[1-9]\d*$/;

export function Issue() {
  const { reference, location, actorId, bump, version } = useApp();
  const areas = reference.areas.filter((a) => a.location === location).map((a) => a.area);
  const [area, setArea] = useState(areas[0] ?? "");
  const [sku, setSku] = useState(reference.products[0]?.sku ?? "");
  const [text, setText] = useState("");
  const [clientError, setClientError] = useState<string | undefined>();
  const { run, busy, uncertain, error } = useActions();
  const balances = useFetch(() => api.get<Balances>(`/balances?loc=${encodeURIComponent(location)}`), [location, version]);
  const serverErrors = fieldMessages(error);

  const product = reference.products.find((p) => p.sku === sku);
  const lots = balances.data?.balances.filter((l) => l.sku === sku && l.area === area) ?? [];

  async function submit() {
    if (!WHOLE.test(text.trim())) {
      setClientError("Enter a whole number of at least 1.");
      document.getElementById("issue-qty")?.focus(); // error focus: keyboard users land on the field
      return;
    }
    setClientError(undefined);
    const payload = { sku, loc: location, area, qty: Number(text.trim()) };
    const unit = product?.base_unit ?? "units";
    const out = await run(metaOf<IssueResult>("Issue", actorId, location, payload, (o) => [
      `Issue #${o.transaction_id} posted: ${qty(o.issued)} ${unit} of ${sku}.`,
      ...o.lines.map((l) => `Lot ${l.lot} (expires ${l.expiration_date ?? "no date"}, ${l.area}): ${qty(l.base_qty)} ${unit}`),
    ].join("\n")), (key) =>
      api.post<IssueResult>("/issues", actorId, key, payload));
    if (out) {
      setText("");
      bump();
    }
  }

  return (
    <section>
      <h2>Issue supplies · {location}</h2>
      <Banner kind="info">
        Stock is issued <strong>earliest-expiring first</strong>. Lots without an expiration date are used last, and
        expired lots are never issued. The lots actually used are shown after a successful issue.
      </Banner>


      <fieldset disabled={busy || uncertain} className="form-block">
        <div className="grid-form">
          <label>
            Product
            <select value={sku} onChange={(e) => setSku(e.target.value)}>
              {reference.products.map((p) => (
                <option key={p.sku} value={p.sku}>{p.sku} · {p.name}</option>
              ))}
            </select>
          </label>
          <label>
            Storage area
            <select value={area} onChange={(e) => setArea(e.target.value)}>
              {areas.map((a) => (
                <option key={a} value={a}>{a}</option>
              ))}
            </select>
            <FieldError message={serverErrors["area"]} />
          </label>
          <label>
            Quantity ({product?.base_unit ?? "base units"})
            <input id="issue-qty" inputMode="numeric" value={text} onChange={(e) => setText(e.target.value)} />
            <FieldError message={clientError ?? serverErrors["qty"]} />
          </label>
        </div>
        <div className="row">
          {!uncertain && (
            <button type="button" className="primary" onClick={() => void submit()} disabled={busy}>
              {busy ? "Issuing…" : "Issue"}
            </button>
          )}
        </div>
      </fieldset>

      <h3>Lots at {area} (issue order)</h3>
      {balances.loading && <Loading />}
      {!balances.loading && lots.length === 0 && <p className="muted">No stock of this product in this area.</p>}
      {lots.length > 0 && (
        <table className="grid">
          <thead>
            <tr>
              <th>Lot</th>
              <th>Expires</th>
              <th>Status</th>
              <th className="num">Available</th>
            </tr>
          </thead>
          <tbody>
            {lots.map((l) => (
              <tr key={l.lot}>
                <td><code>{l.lot}</code></td>
                <td>{l.expiration_date ?? "no date"}</td>
                <td>{l.category === "expired" ? "Expired (not issued)" : l.category === "expiring_soon" ? "Expiring soon" : ""}</td>
                <td className="num">{qty(l.available)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}
