import { Button, Card, Field, Notice, Spinner, useT } from "@healthapp/ui";
import { useState, type FormEvent } from "react";

import { api, openArtifact } from "../api";
import { Markdown } from "../Markdown";

interface BriefingOut {
  markdown: string;
  meta: { report?: string | null } | null;
}

export function Briefing() {
  const { t, list } = useT();
  const occasions = list("agency_ui.occasions");
  const [occasion, setOccasion] = useState(occasions[0] ?? "");
  const [topic, setTopic] = useState("");
  const [result, setResult] = useState<BriefingOut | null>(null);
  const [busy, setBusy] = useState(false);
  const [copied, setCopied] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    setCopied(false);
    try {
      setResult(await api.post<BriefingOut>("/agency/briefing", { occasion, topic }));
    } catch (err) {
      setError(t("citizen.common.error", { reason: (err as Error).message }));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="two-col">
      <Card title={t("agency_ui.briefing_title")}>
        <form className="stack" onSubmit={submit}>
          <div className="field">
            <label htmlFor="occasion">{t("agency_ui.occasion")}</label>
            <select id="occasion" value={occasion} onChange={(e) => setOccasion(e.target.value)}>
              {occasions.map((o) => (
                <option key={o}>{o}</option>
              ))}
            </select>
          </div>
          <Field
            id="topic"
            label={t("agency_ui.topic")}
            placeholder={t("agency_ui.topic_placeholder")}
            value={topic}
            onChange={(e) => setTopic(e.target.value)}
            required
            minLength={2}
          />
          <Button type="submit" disabled={busy || topic.trim().length < 2}>
            {t("agency_ui.generate")}
          </Button>
        </form>
        {error && <Notice tone="danger">{error}</Notice>}
      </Card>
      <Card>
        {busy && <Spinner label={t("agency_ui.working")} />}
        {result && (
          <div className="stack">
            <Markdown text={result.markdown} />
            <div className="row">
              <Button
                variant="secondary"
                onClick={() => navigator.clipboard?.writeText(result.markdown).then(() => setCopied(true))}
              >
                {copied ? t("agency_ui.copied") : t("agency_ui.copy")}
              </Button>
              {result.meta?.report && (
                <Button variant="ghost" onClick={() => openArtifact(result.meta!.report!)}>
                  {t("agency_ui.open_report")}
                </Button>
              )}
            </div>
          </div>
        )}
      </Card>
    </div>
  );
}
