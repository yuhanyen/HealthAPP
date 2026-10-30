// 介面文案一律來自 i18n/zh-TW.json（後端 /api/v1/i18n 提供），前端不寫死任何使用者可見文字。
import { createContext, useContext, useEffect, useState, type ReactNode } from "react";

type Catalog = Record<string, unknown>;

const I18nContext = createContext<Catalog | null>(null);

function lookup(catalog: Catalog, key: string): unknown {
  let node: unknown = catalog;
  for (const part of key.split(".")) {
    if (!node || typeof node !== "object" || !(part in node)) return undefined;
    node = (node as Record<string, unknown>)[part];
  }
  return node;
}

export function format(template: string, params?: Record<string, string | number>): string {
  if (!params) return template;
  return template.replace(/\{(\w+)\}/g, (match, name: string) =>
    name in params ? String(params[name]) : match,
  );
}

export function I18nProvider({ children, loading }: { children: ReactNode; loading: ReactNode }) {
  const [catalog, setCatalog] = useState<Catalog | null>(null);
  useEffect(() => {
    let cancelled = false;
    const load = () =>
      fetch("/api/v1/i18n")
        .then((r) => r.json())
        .then((data: Catalog) => !cancelled && setCatalog(data))
        .catch(() => {
          if (!cancelled) window.setTimeout(load, 3000); // 伺服器還沒起來時自動重試
        });
    load();
    return () => {
      cancelled = true;
    };
  }, []);
  if (!catalog) return <>{loading}</>;
  return <I18nContext.Provider value={catalog}>{children}</I18nContext.Provider>;
}

export function useT() {
  const catalog = useContext(I18nContext);
  if (!catalog) throw new Error("useT() 必須在 I18nProvider 內使用");
  const t = (key: string, params?: Record<string, string | number>): string => {
    const value = lookup(catalog, key);
    return typeof value === "string" ? format(value, params) : key;
  };
  const list = (key: string): string[] => {
    const value = lookup(catalog, key);
    return Array.isArray(value) ? value.map(String) : [];
  };
  const has = (key: string): boolean => typeof lookup(catalog, key) === "string";
  return { t, list, has };
}
