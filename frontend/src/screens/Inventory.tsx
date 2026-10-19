import { useState } from "react";
import { api } from "../api";
import { useApp } from "../context";
import { useFetch } from "../hooks";
import { CATEGORY_LABEL, qty } from "../format";
import type { Balances } from "../types";
import { Banner, Loading } from "../ui";

export function Inventory() {
  const { location, version } = useApp();
  const [search, setSearch] = useState("");
  const [openSku, setOpenSku] = useState<string | null>(null);
  const data = useFetch(() => api.get<Balances>(`/balances?loc=${encodeURIComponent(location)}`), [location, version]);

  if (data.loading) return <Loading />;
  if (data.error || !data.data) return <Banner kind="error">{data.error ?? "Could not load inventory."}</Banner>;

  const b = data.data;
  const q = search.trim().toLowerCase();
  const products = b.products.filter((p) => !q || p.sku.toLowerCase().includes(q) || p.product.toLowerCase().includes(q));

  return (
    <section>
      <h2>Inventory · {location}</h2>
      <p className="muted">
        Figures as of {b.as_of} (clinic local date). Available excludes expired stock and held stock.
      </p>
      <input
        className="search"
        type="search"
        placeholder="Search by product name or SKU"
        aria-label="Search products"
        value={search}
        onChange={(e) => setSearch(e.target.value)}
      />
      {products.length === 0 && <p className="muted">No products with stock at this clinic match.</p>}
      <table className="grid">
        <thead>
          <tr>
            <th>Product</th>
            <th className="num">On hand</th>
            <th className="num">Available</th>
            <th className="num">Expired</th>
            <th className="num">Expiring soon</th>
          </tr>
        </thead>
        <tbody>
          {products.map((p) => (
            <ProductRows
              key={p.sku}
              p={p}
              open={openSku === p.sku}
              onToggle={() => setOpenSku(openSku === p.sku ? null : p.sku)}
              lots={b.balances.filter((l) => l.sku === p.sku)}
            />
          ))}
        </tbody>
      </table>
      <p className="muted small">Select a product to see its lots, areas and expiry categories.</p>
    </section>
  );
}

function ProductRows({
  p, open, onToggle, lots,
}: {
  p: Balances["products"][number];
  open: boolean;
  onToggle: () => void;
  lots: Balances["balances"];
}) {
  return (
    <>
      <tr>
        <td>
          <button type="button" className="row-toggle" aria-expanded={open} onClick={onToggle}>
            <span className="caret" aria-hidden="true">{open ? "▾" : "▸"}</span> <strong>{p.product}</strong>{" "}
            <span className="muted small">{p.sku} · {p.base_unit}</span>
          </button>
        </td>
        <td className="num">{qty(p.on_hand)}</td>
        <td className="num">{qty(p.available)}</td>
        <td className={`num ${p.expired > 0 ? "danger" : ""}`}>{qty(p.expired)}</td>
        <td className={`num ${p.expiring_soon > 0 ? "warn" : ""}`}>{qty(p.expiring_soon)}</td>
      </tr>
      {open &&
        lots.map((l) => (
          <tr key={`${l.lot}-${l.area}`} className="lot-row">
            <td colSpan={2}>
              Lot <code>{l.lot}</code> · {l.area} · expires {l.expiration_date ?? "no date"}{" "}
              {CATEGORY_LABEL[l.category] && <em className={l.category === "expired" ? "danger" : "warn"}>({CATEGORY_LABEL[l.category]})</em>}
            </td>
            <td className="num">{qty(l.available)}</td>
            <td className="num">{l.category === "expired" ? qty(l.on_hand) : ""}</td>
            <td className="num">{l.category === "expiring_soon" ? qty(l.on_hand) : ""}</td>
          </tr>
        ))}
    </>
  );
}
