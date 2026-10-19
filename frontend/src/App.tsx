import { useEffect, useRef, useState } from "react";
import { api } from "./api";
import { AppContext } from "./context";
import { useFetch } from "./hooks";
import { PendingProvider, usePendingStore } from "./pending";
import { messageOf as messageOfError } from "./api";
import type { Reference } from "./types";
import { ActionError, Banner, Loading } from "./ui";
import { Inventory } from "./screens/Inventory";
import { Receive } from "./screens/Receive";
import { Issue } from "./screens/Issue";
import { Counts } from "./screens/Counts";
import { History } from "./screens/History";

const TABS = [
  { id: "inventory", label: "Inventory" },
  { id: "receive", label: "Receive supplies" },
  { id: "issue", label: "Issue supplies" },
  { id: "counts", label: "Counts" },
  { id: "history", label: "History" },
] as const;
type Tab = (typeof TABS)[number]["id"];

export default function App() {
  const ref = useFetch(() => api.get<Reference>("/reference"), []);
  const [tab, setTab] = useState<Tab>("inventory");
  const [location, setLocation] = useState("RFC");
  const [actorId, setActorId] = useState("counter_rfc");
  const [version, setVersion] = useState(0);
  const bump = () => setVersion((v) => v + 1);
  // One pending-write store for the whole app, above the screens: tab changes do not drop it.
  const store = usePendingStore(bump);
  const locked = store.busy; // clinic and actor cannot change while a write is pending or unknown
  // When an outcome becomes unknown, move keyboard focus to the banner so the retry is one Tab away.
  const pendingBanner = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (store.pending?.uncertain) pendingBanner.current?.focus();
  }, [store.pending?.uncertain]);

  if (ref.loading) return <main className="shell"><Loading /></main>;
  if (ref.error || !ref.data) {
    return (
      <main className="shell">
        <Banner kind="error">
          Cannot load reference data: {ref.error ?? "unknown error"}. Start the API first (see frontend/README.md).
        </Banner>
      </main>
    );
  }
  const reference = ref.data;
  const clock = reference.clock;
  const actor = reference.actors.find((a) => a.user_id === actorId);
  const ctx = { reference, location, actorId, version, bump };

  return (
    <PendingProvider value={store}>
    <AppContext.Provider value={ctx}>
      <main className="shell">
        <header className="topbar">
          <div>
            <h1>Clinic Supply &amp; Expiration Tracker</h1>
            <p className="muted">Northbridge Community Clinics · P1 demo</p>
          </div>
          <div className={clock.demo ? "clock clock-demo" : "clock"}>
            {clock.demo ? (
              <>
                <strong>DEMO DATE</strong> · Evaluation date {clock.evaluation_date} ({clock.timezone})
                <div className="muted small">Fixed for reproducible demonstration.</div>
              </>
            ) : (
              <>
                <strong>Live clock</strong> · Today {clock.evaluation_date} ({clock.timezone})
              </>
            )}
          </div>
        </header>

        <section className="controls">
          <label>
            Clinic
            <select value={location} disabled={locked} onChange={(e) => setLocation(e.target.value)}>
              {reference.locations.map((l) => (
                <option key={l.code} value={l.code}>{l.code} · {l.name}</option>
              ))}
            </select>
          </label>
          <label>
            Demo actor
            <select value={actorId} disabled={locked} onChange={(e) => setActorId(e.target.value)}>
              {reference.actors.map((a) => (
                <option key={a.user_id} value={a.user_id}>{a.display_name}</option>
              ))}
            </select>
          </label>
          <p className="muted small actor-note">
            {actor ? `Acting as ${actor.roles.map((r) => `${r.role.replace("_", " ")} at ${r.location}`).join(", ")}. ` : ""}
            Demo actor selection is not a login.
            {locked && " Clinic and actor are locked until the pending write finishes."}
          </p>
        </section>

        {store.pending && (
          <div ref={pendingBanner} tabIndex={-1} className="focus-target">
          <Banner kind="warn">
            {store.pending.uncertain ? (
              <>
                <strong>Pending: {store.pending.label}.</strong> Outcome unknown.{" "}
                {messageOfError(store.error)} Retry sends the original request again (same actor{" "}
                {store.pending.actor}, clinic {store.pending.location}, same key). Nothing else can be submitted until
                it resolves.
                <div className="warn-text">
                  Keep this tab open. Reloading or closing the browser discards the pending request and its key, and
                  the outcome then has to be checked by hand. The pending request is held in memory only.
                </div>
                <div className="row">
                  <button type="button" className="primary" onClick={() => void store.retry()}>Retry same request</button>
                </div>
              </>
            ) : (
              <>Sending {store.pending.label}…</>
            )}
          </Banner>
          </div>
        )}
        {store.notice && (
          <Banner kind="ok">
            <span className="pre-line">{store.notice}</span>{" "}
            <button type="button" className="link" onClick={store.clearNotice}>Dismiss</button>
          </Banner>
        )}
        {Boolean(store.error) && !store.pending && <ActionError error={store.error} uncertain={false} />}

        <nav className="tabs" aria-label="Screens">
          {TABS.map((t) => (
            <button key={t.id} className={tab === t.id ? "tab active" : "tab"} onClick={() => setTab(t.id)}>
              {t.label}
            </button>
          ))}
        </nav>

        <div key={`${location}`} className="screen">
          {tab === "inventory" && <Inventory />}
          {tab === "receive" && <Receive />}
          {tab === "issue" && <Issue />}
          {tab === "counts" && <Counts />}
          {tab === "history" && <History />}
        </div>
        <footer className="muted small">
          Stock figures, expiry categories and count rules come from the API.
        </footer>
      </main>
    </AppContext.Provider>
    </PendingProvider>
  );
}

