import { createContext, useContext } from "react";
import type { Controls, Me } from "../api";

export interface AppCtx {
  me: Me;
  /** Admins can write. Viewers get a read-only UI (the server enforces this too). */
  canWrite: boolean;
  controls: Controls | undefined;
  refreshControls: () => void;
  navigate: (hash: string) => void;
}

export const AppContext = createContext<AppCtx | null>(null);

export function useApp(): AppCtx {
  const ctx = useContext(AppContext);
  if (!ctx) throw new Error("useApp must be used inside AppContext.Provider");
  return ctx;
}
