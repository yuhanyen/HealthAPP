// API 用戶端：Bearer token、統一錯誤、SSE 串流（POST + ReadableStream，EventSource 不支援 POST）。

export class ApiError extends Error {
  constructor(
    public status: number,
    public detail: string,
  ) {
    super(detail);
  }
}

export interface TokenOut {
  access_token: string;
  token_type: string;
  role: string;
  display_name: string;
}

export interface MeOut {
  account_id: string;
  role: string;
  display_name: string;
  user_id: string | null;
  site_id: string | null;
  merchant_id: string | null;
}

export type SseHandler = (event: string, data: unknown) => void;

function detailOf(body: unknown, fallback: string): string {
  if (body && typeof body === "object" && "detail" in body) {
    const detail = (body as { detail: unknown }).detail;
    if (typeof detail === "string") return detail;
    if (Array.isArray(detail)) {
      return detail
        .map((d) => (d && typeof d === "object" && "msg" in d ? String(d.msg) : String(d)))
        .join("；");
    }
  }
  return fallback;
}

export function createApi(options: { storageKey: string; base?: string }) {
  const base = options.base ?? "/api/v1";
  let unauthorized: (() => void) | null = null;

  const token = (): string | null => {
    try {
      return localStorage.getItem(options.storageKey);
    } catch {
      return null;
    }
  };
  const setToken = (value: string | null): void => {
    try {
      if (value) localStorage.setItem(options.storageKey, value);
      else localStorage.removeItem(options.storageKey);
    } catch {
      /* 私密模式等情況：只在記憶體中有效 */
    }
  };

  async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
    const headers: Record<string, string> = {};
    const current = token();
    if (current) headers.Authorization = `Bearer ${current}`;
    if (body !== undefined) headers["Content-Type"] = "application/json";
    const resp = await fetch(base + path, {
      method,
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    if (resp.status === 204) return undefined as T;
    const data: unknown = await resp.json().catch(() => null);
    if (!resp.ok) {
      if (resp.status === 401) unauthorized?.();
      throw new ApiError(resp.status, detailOf(data, resp.statusText));
    }
    return data as T;
  }

  return {
    token,
    setToken,
    onUnauthorized(handler: () => void) {
      unauthorized = handler;
    },
    async login(username: string, password: string): Promise<TokenOut> {
      const form = new URLSearchParams({ username, password });
      const resp = await fetch(`${base}/auth/token`, { method: "POST", body: form });
      const data: unknown = await resp.json().catch(() => null);
      if (!resp.ok) throw new ApiError(resp.status, detailOf(data, resp.statusText));
      const out = data as TokenOut;
      setToken(out.access_token);
      return out;
    },
    get: <T>(path: string) => request<T>("GET", path),
    post: <T>(path: string, body?: unknown) => request<T>("POST", path, body ?? {}),
    put: <T>(path: string, body?: unknown) => request<T>("PUT", path, body ?? {}),
    del: <T>(path: string) => request<T>("DELETE", path),
    async blob(path: string): Promise<Blob> {
      const current = token();
      const resp = await fetch(base + path, {
        headers: current ? { Authorization: `Bearer ${current}` } : {},
      });
      if (!resp.ok) throw new ApiError(resp.status, resp.statusText);
      return resp.blob();
    },
    /** POST 並逐則讀取 SSE（event: … / data: JSON）。 */
    async stream(path: string, body: unknown, onEvent: SseHandler, signal?: AbortSignal) {
      const current = token();
      const resp = await fetch(base + path, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Accept: "text/event-stream",
          ...(current ? { Authorization: `Bearer ${current}` } : {}),
        },
        body: JSON.stringify(body),
        signal,
      });
      if (!resp.ok || !resp.body) {
        const data: unknown = await resp.json().catch(() => null);
        if (resp.status === 401) unauthorized?.();
        throw new ApiError(resp.status, detailOf(data, resp.statusText));
      }
      const reader = resp.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      for (;;) {
        const { value, done } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        let cut: number;
        while ((cut = buffer.indexOf("\n\n")) >= 0) {
          const chunk = buffer.slice(0, cut);
          buffer = buffer.slice(cut + 2);
          let event = "message";
          const data: string[] = [];
          for (const line of chunk.split("\n")) {
            if (line.startsWith("event: ")) event = line.slice(7);
            else if (line.startsWith("data: ")) data.push(line.slice(6));
          }
          if (data.length) onEvent(event, JSON.parse(data.join("\n")));
        }
      }
    },
  };
}

export type Api = ReturnType<typeof createApi>;
