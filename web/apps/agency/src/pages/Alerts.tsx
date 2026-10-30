import { Badge, Button, Card, Notice, Spinner, useAsync, useT } from "@healthapp/ui";
import { useState } from "react";

import { api, openArtifact, type Alert } from "../api";

export function AlertList({ alerts, onChange }: { alerts: Alert[]; onChange: () => void }) {
  const { t } = useT();
  const [busy, setBusy] = useState<number | null>(null);
  if (!alerts.length) return <p className="muted">{t("agency_ui.alerts_none")}</p>;

  const analyze = async (id: number) => {
    setBusy(id);
    try {
      await api.post(`/agency/alerts/${id}/analyze`);
      onChange();
    } finally {
      setBusy(null);
    }
  };

  return (
    <div className="stack">
      {alerts.map((a) => (
        <div key={a.id} className="stack" style={{ borderBottom: "1px solid var(--border)", paddingBottom: 10 }}>
          <div className="row spread">
            <strong>
              {t("agency_ui.alert_line", {
                village: a.village_code,
                topic: a.topic,
                count: a.count,
                baseline: a.baseline,
                ratio: a.ratio,
              })}
            </strong>
            <Badge tone={a.status === "open" ? "warn" : "primary"}>{t(`agency_ui.alert_status.${a.status}`)}</Badge>
          </div>
          {a.analysis ? (
            <Notice tone={a.analysis.consistent ? "primary" : "neutral"}>
              <div>{a.analysis.summary}</div>
              <div>{a.analysis.suggestion}</div>
              {a.analysis.queries.length > 0 && <small>{a.analysis.queries.join("、")}</small>}
              <div>
                <small>{a.analysis.approval}</small>
              </div>
              {a.analysis.report && (
                <Button variant="ghost" onClick={() => openArtifact(a.analysis!.report!)}>
                  {t("agency_ui.open_report")}
                </Button>
              )}
            </Notice>
          ) : (
            <Button variant="secondary" disabled={busy === a.id} onClick={() => analyze(a.id)}>
              {t("agency_ui.analyze")}
            </Button>
          )}
        </div>
      ))}
    </div>
  );
}

export function Alerts() {
  const { t } = useT();
  const alerts = useAsync(() => api.get<Alert[]>("/agency/alerts?limit=30"));
  if (alerts.loading && !alerts.data) return <Spinner />;
  return (
    <Card title={t("agency_ui.alerts_title")}>
      <AlertList alerts={alerts.data ?? []} onChange={alerts.reload} />
    </Card>
  );
}
