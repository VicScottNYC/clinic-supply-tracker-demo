import { createContext, useContext } from "react";
import type { Reference } from "./types";

export interface AppState {
  reference: Reference;
  location: string;
  actorId: string;
  version: number; // bumps after every successful write so screens reload their data
  bump: () => void;
}

export const AppContext = createContext<AppState | null>(null);

export function useApp(): AppState {
  const ctx = useContext(AppContext);
  if (!ctx) throw new Error("useApp must be used inside AppContext");
  return ctx;
}
