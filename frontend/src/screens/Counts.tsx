import { useState } from "react";
import { api } from "../api";
import { useApp } from "../context";
import { fieldMessages, useActions, useFetch } from "../hooks";
import { metaOf } from "../pending";
import { STATE_LABEL, STATUS_LABEL, localTime, qty } from "../format";
import type { CountDetail, CountSummary } from "../types";
import { Banner, Badge, FieldError, Loading } from "../ui";

const WHOLE_OR_ZERO = /^(0|[1-9]\d*)$/;
const DATE = /^\d{4}-\d{2}-\d{2}$/;

export function Counts() {
  const { reference, location, actorId, bump, version } = useApp();
  const areas = reference.areas.filter((a) => a.location === location).map((a) => a.area);
  const [area, setArea] = useState(areas[0] ?? "");
  const [inputs, setInputs] = useState<Record<number, string>>({});
  const [rowErrors, setRowErrors] = useState<Record<number, string>>({});
  const [surplus, setSurplus] = useState({ sku: reference.products[0]?.sku ?? "", lot: "", expiration: "" });
  const [surplusError, setSurplusError] = useState<string | undefined>();
  const [confirmCancel, setConfirmCancel] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const { run, busy, uncertain, error } = useActions();

  const list = useFetch(
    () => api.get<{ counts: CountSummary[] }>(`/counts?loc=${encodeURIComponent(location)}&area=${encodeURIComponent(area)}`),
    [location, area, version],
  );
  const openCount = list.data?.counts.find((c) => c.status === "open");
  const detail = useFetch<CountDetail | null>(
    () => (openCount ? api.get<CountDetail>(`/counts/${openCount.count_id}`) : Promise.resolve(null)),
    [openCount?.count_id, version],
  );
  const serverErrors = fieldMessages(error);
  const locked = busy || uncertain;

  function after(message: string) {
    setNotice(message);
    bump();
  }

  async function startCount() {
    const payload = { loc: location, area };
    const out = await run(metaOf("Start count", actorId, location, { op: "start", payload }), (key) =>
      api.post<{ count_id: number }>("/counts", actorId, key, payload));
    if (out) after(`Count #${out.count_id} started. Receipts and issues into ${area} are now blocked.`);
  }

  async function submitLine(lineId: number, sku: string, lot: string) {
    const text = (inputs[lineId] ?? "").trim();
    if (!WHOLE_OR_ZERO.test(text)) {
      setRowErrors((r) => ({ ...r, [lineId]: "Enter a whole number (zero is allowed)." }));
      document.getElementById(`count-${lineId}`)?.focus();
      return;
    }
    setRowErrors((r) => ({ ...r, [lineId]: "" }));
    const id = openCount!.count_id;
    const payload = { sku, lot, counted: Number(text) };
    const out = await run(metaOf("Count submission", actorId, location, { op: "submit", id, payload }), (key) =>
      api.post(`/counts/${id}/submissions`, actorId, key, payload));
    if (out) {
      setInputs((i) => ({ ...i, [lineId]: "" }));
      after(`Count recorded for ${sku} lot ${lot}.`);
    }
  }

  async function addSurplus() {
    if (!surplus.lot.trim()) return setSurplusError("Enter the lot number.");
    if (surplus.expiration && !DATE.test(surplus.expiration)) return setSurplusError("Enter a date.");
    setSurplusError(undefined);
    const id = openCount!.count_id;
    const payload = {
      sku: surplus.sku,
      lot: surplus.lot.trim(),
      pending_expiration: surplus.expiration || null,
    };
    const out = await run(metaOf("Surplus line", actorId, location, { op: "surplus", id, payload }), (key) =>
      api.post(`/counts/${id}/lines`, actorId, key, payload));
    if (out) {
      setSurplus({ sku: surplus.sku, lot: "", expiration: "" });
      after(`Surplus line added for ${surplus.sku}. The stock is recorded only when the count completes.`);
    }
  }

  async function complete() {
    const id = openCount!.count_id;
    const out = await run(metaOf("Complete count", actorId, location, { op: "complete", id }), (key) =>
      api.post(`/counts/${id}/complete`, actorId, key));
    if (out) after("Count completed. Adjustments were applied where lines were out of tolerance.");
  }

  async function cancel() {
    const id = openCount!.count_id;
    const out = await run(metaOf("Cancel count", actorId, location, { op: "cancel", id }), (key) =>
      api.post(`/counts/${id}/cancel`, actorId, key));
    if (out) {
      setConfirmCancel(false);
      after("Count cancelled. No stock was changed and the area is free again.");
    }
  }

  const d = detail.data;
  const unresolved = d?.lines.filter((l) => l.unresolved) ?? [];

  return (
    <section>
      <h2>Counts · {location}</h2>
      <label className="inline">
        Storage area{" "}
        <select value={area} onChange={(e) => { setArea(e.target.value); setNotice(null); }}>
          {areas.map((a) => (
            <option key={a} value={a}>{a}</option>
          ))}
        </select>
      </label>

      {notice && <Banner kind="ok">{notice}</Banner>}
      {list.loading && <Loading />}
      {list.error && <Banner kind="error">{list.error}</Banner>}

      {!list.loading && !openCount && (
        <Banner kind="info">
          No count is open for {area}. Receipts and issues are allowed.{" "}
          <button className="primary" disabled={locked} onClick={startCount}>
            Start count
          </button>
        </Banner>
      )}

      {openCount && d && (
        <>
          <Banner kind="warn">
            <strong>Area locked for counting.</strong> Receipts and issues into {area} are blocked until this count is
            completed or cancelled. Count #{d.count_id}, opened {localTime(d.opened_at)}.
          </Banner>

          <h3>Lines</h3>
          <p className="muted small">
            {d.unresolved_lines === 0
              ? "Every line is resolved. You can complete the count."
              : `${d.unresolved_lines} line(s) still need a count or recount: ${unresolved.map((l) => `${l.sku} lot ${l.lot}`).join(", ")}.`}
          </p>
          <table className="grid">
            <thead>
              <tr>
                <th>Product</th>
                <th>Lot</th>
                <th className="num">Expected</th>
                <th>Status</th>
                <th className="num">Last counted</th>
                <th className="num">Attempts</th>
                <th>Physical count</th>
              </tr>
            </thead>
            <tbody>
              {d.lines.map((l) => (
                <tr key={l.count_line_id} className={l.unresolved ? "needs-count" : ""}>
                  <td>{l.sku} · {l.product}</td>
                  <td><code>{l.lot}</code>{l.pending_expiration_date && <div className="muted small">pending expiry {l.pending_expiration_date}</div>}</td>
                  <td className="num">{qty(l.expected_qty)}</td>
                  <td>
                    <Badge tone={l.state === "out" ? "danger" : l.unresolved ? "warn" : "ok"}>{STATE_LABEL[l.state]}</Badge>
                  </td>
                  <td className="num">{l.last_counted_qty ?? "—"}</td>
                  <td className="num">{l.attempts}</td>
                  <td>
                    {l.unresolved ? (
                      <div className="row tight">
                        <input
                          className="qty"
                          inputMode="numeric"
                          id={`count-${l.count_line_id}`} aria-label={`Physical count ${l.sku} lot ${l.lot}`}
                          value={inputs[l.count_line_id] ?? ""}
                          disabled={locked}
                          onChange={(e) => setInputs((i) => ({ ...i, [l.count_line_id]: e.target.value }))}
                        />
                        <button disabled={locked} onClick={() => submitLine(l.count_line_id, l.sku, l.lot)}>
                          {l.state === "out" ? "Recount" : "Submit"}
                        </button>
                        <FieldError message={rowErrors[l.count_line_id] || undefined} />
                      </div>
                    ) : (
                      <span className="muted small">Recorded</span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>

          <details className="surplus">
            <summary>Add a surplus line (stock found that the system does not show)</summary>
            <div className="grid-form">
              <label>
                Product
                <select value={surplus.sku} onChange={(e) => setSurplus({ ...surplus, sku: e.target.value })} disabled={locked}>
                  {reference.products.map((p) => (
                    <option key={p.sku} value={p.sku}>{p.sku} · {p.name}</option>
                  ))}
                </select>
              </label>
              <label>
                Lot
                <input value={surplus.lot} onChange={(e) => setSurplus({ ...surplus, lot: e.target.value })} disabled={locked} />
              </label>
              <label>
                Expiration (if known)
                <input type="date" value={surplus.expiration} onChange={(e) => setSurplus({ ...surplus, expiration: e.target.value })} disabled={locked} />
                <FieldError message={surplusError ?? serverErrors["pending_expiration"]} />
              </label>
              <div className="row tight">
                <button disabled={locked} onClick={addSurplus}>Add surplus line</button>
              </div>
            </div>
          </details>

          <div className="row">
            <button
              className="primary"
              disabled={locked || d.unresolved_lines > 0}
              onClick={complete}
              title={d.unresolved_lines > 0 ? "Resolve every line first" : undefined}
            >
              Complete count
            </button>
            {!confirmCancel ? (
              <button disabled={locked} onClick={() => setConfirmCancel(true)}>Cancel count…</button>
            ) : (
              <button className="danger" disabled={locked} onClick={cancel}>
                Confirm cancel (no stock changes)
              </button>
            )}
          </div>
        </>
      )}


      {list.data && list.data.counts.length > 0 && (
        <>
          <h3>Recent counts for {area}</h3>
          <table className="grid">
            <thead><tr><th>Count</th><th>Status</th><th>Opened</th><th>Closed</th></tr></thead>
            <tbody>
              {list.data.counts.map((c) => (
                <tr key={c.count_id}>
                  <td>#{c.count_id}</td>
                  <td>{STATUS_LABEL[c.status]}</td>
                  <td className="small">{localTime(c.opened_at)}</td>
                  <td className="small">{c.closed_at ? localTime(c.closed_at) : "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}
    </section>
  );
}
