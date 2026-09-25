import { useCallback, useEffect, useRef, useState } from "react";
import { errorMessage } from "../api";

export interface AsyncState<T> {
  data: T | undefined;
  error: string | null;
  loading: boolean;
  reload: () => void;
  setData: (v: T | undefined) => void;
}

/** Runs `fn` on mount and whenever `deps` change or `reload()` is called. Stale responses are dropped. */
export function useAsync<T>(fn: () => Promise<T>, deps: readonly unknown[] = []): AsyncState<T> {
  const [data, setData] = useState<T | undefined>(undefined);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [tick, setTick] = useState(0);
  const fnRef = useRef(fn);
  fnRef.current = fn;

  useEffect(() => {
    let live = true;
    setLoading(true);
    setError(null);
    fnRef.current().then(
      (v) => {
        if (live) {
          setData(v);
          setLoading(false);
        }
      },
      (e: unknown) => {
        if (live) {
          setError(errorMessage(e));
          setLoading(false);
        }
      },
    );
    return () => {
      live = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tick, ...deps]);

  const reload = useCallback(() => setTick((t) => t + 1), []);
  return { data, error, loading, reload, setData };
}

export interface ActionState {
  busy: boolean;
  error: string | null;
  message: string | null;
  /** Runs an async action; returns its result, or undefined if it threw (the error is kept in `error`). */
  run: <R>(fn: () => Promise<R>, success?: string | ((r: R) => string)) => Promise<R | undefined>;
  clear: () => void;
}

/** Busy/error/success bookkeeping for buttons that call the API. */
export function useAction(): ActionState {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const run = useCallback(async <R,>(fn: () => Promise<R>, success?: string | ((r: R) => string)) => {
    setBusy(true);
    setError(null);
    setMessage(null);
    try {
      const r = await fn();
      if (success) setMessage(typeof success === "function" ? success(r) : success);
      return r;
    } catch (e) {
      setError(errorMessage(e));
      return undefined;
    } finally {
      setBusy(false);
    }
  }, []);
  const clear = useCallback(() => {
    setError(null);
    setMessage(null);
  }, []);
  return { busy, error, message, run, clear };
}
