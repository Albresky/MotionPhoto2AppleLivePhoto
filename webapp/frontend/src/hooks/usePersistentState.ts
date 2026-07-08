import { useCallback, useEffect, useRef, useState } from "react";

/**
 * Persist a piece of state to localStorage.
 *
 * Why: the user picked an input/output directory last time — we want to
 * restore it on next launch so they don't have to navigate the directory
 * browser from scratch each session.
 *
 * SSR-safe (checks typeof window) and defensive against quota/privacy
 * errors — falls back to in-memory state if localStorage is unavailable.
 */
export function usePersistentState<T>(
  key: string,
  initial: T,
): [T, (v: T | ((prev: T) => T)) => void] {
  const [value, setValue] = useState<T>(() => {
    if (typeof window === "undefined") return initial;
    try {
      const raw = window.localStorage.getItem(key);
      if (raw == null) return initial;
      return JSON.parse(raw) as T;
    } catch {
      return initial;
    }
  });

  // Debounce writes with a ref so rapid updates don't thrash localStorage.
  const timer = useRef<number | undefined>(undefined);
  const update = useCallback(
    (v: T | ((prev: T) => T)) => {
      setValue((prev) => {
        const next =
          typeof v === "function" ? (v as (p: T) => T)(prev) : v;
        if (timer.current) window.clearTimeout(timer.current);
        timer.current = window.setTimeout(() => {
          try {
            window.localStorage.setItem(key, JSON.stringify(next));
          } catch {
            // Quota exceeded or disabled — silently ignore.
          }
        }, 100);
        return next;
      });
    },
    [key],
  );

  // Sync across tabs / windows.
  useEffect(() => {
    const onStorage = (e: StorageEvent) => {
      if (e.key !== key || e.newValue == null) return;
      try {
        setValue(JSON.parse(e.newValue) as T);
      } catch {
        // Ignore malformed.
      }
    };
    window.addEventListener("storage", onStorage);
    return () => window.removeEventListener("storage", onStorage);
  }, [key]);

  return [value, update];
}
