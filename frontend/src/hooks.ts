import { useEffect, useState } from "react";
import { ApiError, messageOf } from "./api";
import { usePending, type PendingMeta } from "./pending";

export function useFetch<T>(load: () => Promise<T>, deps: unknown[]) {
  const [state, setState] = useState<{ data?: T; error?: string; loading: boolean }>({ loading: true });
  const [tick, setTick] = useState(0);
  useEffect(() => {
    let alive = true;
    setState((s) => ({ ...s, loading: true, error: undefined }));
    load()
      .then((data) => alive && setState({ data, loading: false }))
      .catch((e: unknown) => alive && setState({ loading: false, error: messageOf(e) }));
    return () => {
      alive = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, tick]);
  return { ...state, reload: () => setTick((t) => t + 1) };
}

// Screens submit through the app-level pending store. While any write is pending or unknown, every
// screen's actions are disabled, so there is only ever one submission key in play.
export function useActions() {
  const store = usePending();
  return {
    run: <T,>(meta: PendingMeta, send: (key: string) => Promise<T>) => store.run(meta, send),
    busy: store.busy,
    uncertain: store.uncertain,
    error: store.error,
    clear: store.clearError,
  };
}

// Field-level messages from a 422 (keys like "lines.0.base_qty") or a service rejection's message.
export function fieldMessages(error: unknown): Record<string, string> {
  return error instanceof ApiError ? error.fields : {};
}
