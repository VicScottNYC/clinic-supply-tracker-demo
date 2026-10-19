import { useState } from "react";
import { api } from "../api";
import { useApp } from "../context";
import { useFetch } from "../hooks";
import { OPERATION_LABEL, localTime, signed } from "../format";
import type { Transaction } from "../types";
import { Banner, Loading } from "../ui";

export function History() {
  const { location, version } = useApp();
  const [openId, setOpenId] = useState<number | null>(null);
  const data = useFetch(
    () => api.get<{ transactions: Transaction[] }>(`/transactions?loc=${encodeURIComponent(location)}&limit=100`),
    [location, version],
  );

  if (data.loading) return <Loading />;
  if (data.error || !data.data) return <Banner kind="error">{data.error ?? "Could not load history."}</Banner>;
  const txs = data.data.transactions;

  return (
    <section>
      <h2>History · {location}</h2>
      <p className="muted">Newest first. Times are shown in clinic time (America/New_York); stored times are UTC.</p>
      {txs.length === 0 && <p className="muted">No transactions yet at this clinic.</p>}
      <table className="grid">
        <thead>
          <tr>
            <th>When</th>
            <th>Operation</th>
            <th>Actor</th>
            <th>Detail</th>
            <th className="num">Lines</th>
          </tr>
        </thead>
        <tbody>
          {txs.map((t) => (
            <TransactionRows
              key={t.transaction_id}
              t={t}
              open={openId === t.transaction_id}
              onToggle={() => setOpenId(openId === t.transaction_id ? null : t.transaction_id)}
            />
          ))}
        </tbody>
      </table>
    </section>
  );
}

function TransactionRows({ t, open, onToggle }: { t: Transaction; open: boolean; onToggle: () => void }) {
  const detail = [
    t.packing_slip ? `Packing slip ${t.packing_slip}` : null,
    t.count_id !== null ? `Count #${t.count_id}` : null,
    `Transaction #${t.transaction_id}`,
  ].filter(Boolean).join(" · ");
  return (
    <>
      <tr>
        <td>
          <button type="button" className="row-toggle" aria-expanded={open} onClick={onToggle}>
            <span className="caret" aria-hidden="true">{open ? "▾" : "▸"}</span> {localTime(t.created_at)}
            <div className="muted small">{t.created_at}</div>
          </button>
        </td>
        <td>{OPERATION_LABEL[t.operation] ?? t.operation}</td>
        <td>{t.actor}</td>
        <td className="small">{detail}</td>
        <td className="num">{t.movements.length}</td>
      </tr>
      {open && (
        <tr className="lot-row">
          <td colSpan={5}>
            <table className="grid inner">
              <thead>
                <tr>
                  <th>Product</th>
                  <th>Lot</th>
                  <th>Area</th>
                  <th className="num">On hand</th>
                  <th className="num">Held</th>
                  <th>Reason</th>
                  <th>Occurred (UTC)</th>
                </tr>
              </thead>
              <tbody>
                {t.movements.map((m, i) => (
                  <tr key={i}>
                    <td>{m.sku}</td>
                    <td><code>{m.lot}</code></td>
                    <td>{m.area}</td>
                    <td className="num">{signed(m.delta_on_hand)}</td>
                    <td className="num">{signed(m.delta_held)}</td>
                    <td>{m.reason}</td>
                    <td className="small">{m.occurred_at}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </td>
        </tr>
      )}
    </>
  );
}
