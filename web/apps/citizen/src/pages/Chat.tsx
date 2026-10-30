import { Button, Card, Notice, Screen, useT } from "@healthapp/ui";
import { useEffect, useRef, useState, type FormEvent } from "react";

import { api } from "../api";
import type { ChatTurn, PendingAction } from "../types";

type Item =
  | { kind: "user" | "assistant"; text: string }
  | { kind: "tool"; tool: string }
  | {
      kind: "card";
      actionId: string;
      summary: string;
      state: "pending" | "done" | "cancelled" | "failed";
      note?: string;
    };

export function Chat() {
  const { t, list, has } = useT();
  const [items, setItems] = useState<Item[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [mock, setMock] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const bottom = useRef<HTMLDivElement>(null);

  useEffect(() => {
    api
      .get<ChatTurn[]>("/me/chat")
      .then((turns) => setItems(turns.map((m) => ({ kind: m.role, text: m.content }))))
      .catch(() => undefined);
  }, []);
  useEffect(() => {
    // 不可寫成運算式箭頭：新版瀏覽器的 scrollIntoView 會回傳 Promise，React 會把它當清除函式呼叫而崩潰
    bottom.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [items]);

  const send = async (message: string) => {
    const text = message.trim();
    if (!text || busy) return;
    setInput("");
    setError(null);
    setBusy(true);
    setItems((prev) => [...prev, { kind: "user", text }, { kind: "assistant", text: "" }]);
    const appendText = (piece: string) =>
      setItems((prev) => {
        const next = [...prev];
        for (let i = next.length - 1; i >= 0; i--) {
          const item = next[i]!;
          if (item.kind === "assistant") {
            next[i] = { ...item, text: item.text + piece };
            break;
          }
        }
        return next;
      });
    try {
      await api.stream("/me/chat", { message: text }, (event, raw) => {
        const data = raw as Record<string, unknown>;
        if (event === "text") appendText(String(data.text));
        else if (event === "tool" && data.status === "ok")
          setItems((prev) => {
            const last = prev[prev.length - 1];
            const tool = { kind: "tool" as const, tool: String(data.tool) };
            // 工具狀態列插在回覆泡泡之前
            return last?.kind === "assistant" && !last.text
              ? [...prev.slice(0, -1), tool, last]
              : [...prev, tool];
          });
        else if (event === "confirmation")
          setItems((prev) => [
            ...prev,
            {
              kind: "card",
              actionId: String(data.action_id),
              summary: String(data.summary),
              state: "pending",
            },
          ]);
        else if (event === "done" && data.planner === "mock") setMock(true);
        else if (event === "error") setError(String(data.message));
      });
    } catch (err) {
      setError(t("citizen.common.error", { reason: (err as Error).message }));
    } finally {
      setItems((prev) => prev.filter((item) => item.kind !== "assistant" || item.text));
      setBusy(false);
    }
  };

  const decide = async (actionId: string, accept: boolean) => {
    const update = (patch: Partial<Extract<Item, { kind: "card" }>>) =>
      setItems((prev) =>
        prev.map((item) =>
          item.kind === "card" && item.actionId === actionId ? { ...item, ...patch } : item,
        ),
      );
    try {
      if (!accept) {
        await api.post(`/me/actions/${actionId}/cancel`);
        update({ state: "cancelled" });
        return;
      }
      const action = await api.post<PendingAction>(`/me/actions/${actionId}/confirm`);
      const message = action.result && typeof action.result.message === "string" ? action.result.message : undefined;
      update({ state: action.status === "executed" ? "done" : "failed", note: message });
    } catch (err) {
      update({ state: "failed", note: (err as Error).message });
    }
  };

  const submit = (e: FormEvent) => {
    e.preventDefault();
    void send(input);
  };

  return (
    <Screen title={t("citizen.chat.title")}>
      {mock && <Notice>{t("citizen.chat.mock")}</Notice>}
      <div className="chat" aria-live="polite">
        {items.map((item, i) => {
          if (item.kind === "tool")
            return (
              <div key={i} className="tool-line">
                ⋯ {has(`citizen.chat.tool.${item.tool}`) ? t(`citizen.chat.tool.${item.tool}`) : item.tool}
              </div>
            );
          if (item.kind === "card")
            return (
              <Card key={i} title={t("citizen.chat.card_title")} tone="primary">
                <p>{item.summary}</p>
                {item.state === "pending" ? (
                  <div className="stack">
                    <Button block onClick={() => decide(item.actionId, true)}>
                      {t("citizen.common.confirm")}
                    </Button>
                    <Button block variant="secondary" onClick={() => decide(item.actionId, false)}>
                      {t("citizen.common.cancel")}
                    </Button>
                  </div>
                ) : (
                  <Notice tone={item.state === "done" ? "ok" : item.state === "failed" ? "danger" : "neutral"}>
                    {item.note ??
                      t(item.state === "done" ? "citizen.chat.done" : "citizen.chat.cancelled")}
                  </Notice>
                )}
              </Card>
            );
          return (
            <div key={i} className={`bubble ${item.kind}`}>
              {item.text}
            </div>
          );
        })}
        {busy && <div className="tool-line">{t("citizen.chat.thinking")}</div>}
        <div ref={bottom} />
      </div>
      {error && <Notice tone="danger">{error}</Notice>}
      {items.length === 0 && (
        <div className="chips">
          {list("citizen.chat.suggestions").map((s) => (
            <button key={s} type="button" className="chip" onClick={() => send(s)}>
              {s}
            </button>
          ))}
        </div>
      )}
      <form className="composer" onSubmit={submit}>
        <input
          aria-label={t("citizen.chat.placeholder")}
          placeholder={t("citizen.chat.placeholder")}
          value={input}
          maxLength={500}
          onChange={(e) => setInput(e.target.value)}
        />
        <Button type="submit" disabled={busy || !input.trim()}>
          {t("citizen.chat.send")}
        </Button>
      </form>
    </Screen>
  );
}
