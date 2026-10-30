import { useCallback, useEffect, useRef, useState } from "react";

/** 以 hash 做路由（#/today）：靜態檔掛在 /app/<名稱>/ 下，不需要伺服器端 fallback。 */
export function useHashRoute(fallback: string): [string, (path: string) => void] {
  const read = () => window.location.hash.replace(/^#/, "") || fallback;
  const [route, setRoute] = useState(read);
  useEffect(() => {
    const onChange = () => setRoute(read());
    window.addEventListener("hashchange", onChange);
    return () => window.removeEventListener("hashchange", onChange);
  });
  const go = useCallback((path: string) => {
    window.location.hash = path;
  }, []);
  return [route, go];
}

export interface AsyncState<T> {
  data: T | null;
  error: Error | null;
  loading: boolean;
  reload: () => void;
}

/** 載入資料；deps 變動或呼叫 reload() 時重新載入，元件卸載後不再 setState。 */
export function useAsync<T>(load: () => Promise<T>, deps: unknown[] = []): AsyncState<T> {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<Error | null>(null);
  const [loading, setLoading] = useState(true);
  const [tick, setTick] = useState(0);
  const alive = useRef(true);
  useEffect(() => {
    alive.current = true;
    setLoading(true);
    load()
      .then((value) => {
        if (!alive.current) return;
        setData(value);
        setError(null);
      })
      .catch((err: unknown) => alive.current && setError(err as Error))
      .finally(() => alive.current && setLoading(false));
    return () => {
      alive.current = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, tick]);
  return { data, error, loading, reload: () => setTick((n) => n + 1) };
}

/** 長者模式：在 <html data-elder="on"> 切換字級與對比（tokens 見 styles.css）。 */
export function applyElderMode(on: boolean): void {
  document.documentElement.dataset.elder = on ? "on" : "off";
}
