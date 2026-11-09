// The one pending write for the whole app. It lives above the screens so that changing tab does not drop
// it, and it stores the original actor, endpoint request, and Idempotency-Key. Retry re-sends that stored
// request exactly; it never reads the form again. Clinic and actor changes are blocked while it exists.
import { createContext, useCallback, useContext, useRef, useState, type ReactNode } from "react";
import { ApiError } from "./api";
import { ActionGate, fingerprintOf, submitAction } from "./intent";

export interface PendingMeta {
  label: string; // human name shown in the banner, e.g. "Receipt at RFC"
  actor: string;
  location: string;
  fingerprint: string; // identifies the exact request (actor, endpoint, payload)
  done?: (out: never) => string; // success message shown above the screens, even after navigation
}

interface Stored extends PendingMeta {
  send: (key: string) => Promise<unknown>;
  uncertain: boolean;
}

export interface PendingState {
  pending: Stored | null; // non-null while an action is in flight or its outcome is unknown
  busy: boolean;
  uncertain: boolean;
  error: unknown;
  notice: string | null;
  run<T>(meta: PendingMeta, send: (key: string) => Promise<T>): Promise<T | undefined>;
  retry(): Promise<unknown>;
  clearError(): void;
  clearNotice(): void;
}

export const PendingContext = createContext<PendingState | null>(null);

export function usePendingStore(onSuccess: () => void): PendingState {
  const gate = useRef(new ActionGate()).current;
  const cell = useRef<Stored | null>(null); // source of truth for the stored request
  const [pending, setPending] = useState<Stored | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const successRef = useRef(onSuccess);
  successRef.current = onSuccess;

  const run = useCallback(async <T,>(meta: PendingMeta, send: (key: string) => Promise<T>): Promise<T | undefined> => {
    const current = cell.current;
    if (current && current.fingerprint !== meta.fingerprint) {
      setError(new Error(current.uncertain
        ? `${current.label} has an unknown outcome. Retry that same request before doing anything else.`
        : `${current.label} is still in progress. Wait for it to finish.`));
      return undefined;
    }
    // A retry reuses the stored request, which is identical by fingerprint; the first send is kept as is.
    const target: Stored = current ?? { ...meta, send: send as (key: string) => Promise<unknown>, uncertain: false };
    cell.current = target;
    setPending({ ...target });
    setError(null);
    setNotice(null);
    try {
      const out = (await submitAction(gate, target.fingerprint, target.send)) as T;
      cell.current = null;
      setPending(null);
      setNotice(target.done ? (target.done as (o: unknown) => string)(out) : `${target.label} recorded.`);
      successRef.current();
      return out;
    } catch (e) {
      const unknown = e instanceof ApiError && e.uncertain;
      if (unknown) target.uncertain = true;
      else cell.current = null;
      setPending(unknown ? { ...target } : null);
      setError(e);
      return undefined;
    }
  }, [gate]);

  const retry = useCallback(async () => {
    const stored = cell.current;
    if (!stored) return undefined;
    return run(stored, stored.send);
  }, [run]);

  return {
    pending,
    busy: pending !== null,
    uncertain: pending?.uncertain ?? false,
    error,
    notice,
    run,
    retry,
    clearError: () => setError(null),
    clearNotice: () => setNotice(null),
  };
}

export function PendingProvider({ value, children }: { value: PendingState; children: ReactNode }) {
  return <PendingContext.Provider value={value}>{children}</PendingContext.Provider>;
}

export function usePending(): PendingState {
  const ctx = useContext(PendingContext);
  if (!ctx) throw new Error("usePending must be used inside PendingProvider");
  return ctx;
}

// Describes one intended write: who sends it, where, and the exact request parts (the fingerprint).
export function metaOf<T = never>(
  label: string, actor: string, location: string, parts: unknown, done?: (out: T) => string,
): PendingMeta {
  return { label, actor, location, fingerprint: fingerprintOf({ actor, location, parts }), done: done as PendingMeta["done"] };
}
