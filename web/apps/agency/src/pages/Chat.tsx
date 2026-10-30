import { Button, Card, Notice, useT } from "@healthapp/ui";
import { useState, type FormEvent } from "react";

import { api, openArtifact } from "../api";
import { Markdown } from "../Markdown";

type Item =
  | { kind: "user"; text: string }
  | { kind: "assistant"; text: string; report?: string | null }
  | { kind: "tool"; tool: string; status: string };

export function Chat() {
  const { t, list, has } = useT();
  const [items, setItems] = useState<Item[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const send = async (message: string) => {
    const text = message.trim();
    if (!text || busy) return;
    setInput("");
    setError(null);
    setBusy(true);
    setItems((prev) => [...prev, { kind: "user", text }]);
    let answer = "";
    try {
      await api.stream("/agency/chat", { message: text }, (event, raw) => {
        const data = raw as Record<string, unknown>;
        if (event === "tool") setItems((prev) => [...prev, { kind: "tool", tool: String(data.tool), status: String(data.status) }]);
        else if (event === "text") answer += String(data.text);
        else if (event === "done") {
          const meta = data.meta as { report?: string | null } | null;
          setItems((prev) => [...prev, { kind: "assistant", text: answer || String(data.text), report: meta?.report }]);
        } else if (event === "error") setError(String(data.message));
      });
    } catch (err) {
      setError(t("citizen.common.error", { reason: (err as Error).message }));
    } finally {
      setBusy(false);
    }
  };

  const submit = (e: FormEvent) => {
    e.preventDefault();
    void send(input);
  };

  return (
    <Card title={t("agency_ui.chat_title")}>
      <div className="stack">
        <div className="chips">
          {list("agency_ui.chat_examples").map((q) => (
            <button key={q} type="button" className="chip" onClick={() => send(q)} disabled={busy}>
              {q}
            </button>
          ))}
        </div>
        <div className="chat-log" aria-live="polite">
          {items.map((item, i) =>
            item.kind === "tool" ? (
              <div key={i} className="tool-line">
                ⋯ {has(`agency_ui.tool.${item.tool}`) ? t(`agency_ui.tool.${item.tool}`) : item.tool}（{item.status}）
              </div>
            ) : item.kind === "user" ? (
              <div key={i} className="msg user">
                {item.text}
              </div>
            ) : (
              <div key={i} className="msg">
                <Markdown text={item.text} />
                {item.report && (
                  <Button variant="ghost" onClick={() => openArtifact(item.report!)}>
                    {t("agency_ui.open_report")}
                  </Button>
                )}
              </div>
            ),
          )}
          {busy && <div className="tool-line">{t("agency_ui.working")}</div>}
        </div>
        {error && <Notice tone="danger">{error}</Notice>}
        <form className="composer" onSubmit={submit}>
          <input
            aria-label={t("agency_ui.chat_placeholder")}
            placeholder={t("agency_ui.chat_placeholder")}
            value={input}
            maxLength={800}
            onChange={(e) => setInput(e.target.value)}
          />
          <Button type="submit" disabled={busy || !input.trim()}>
            {t("agency_ui.send")}
          </Button>
        </form>
      </div>
    </Card>
  );
}
