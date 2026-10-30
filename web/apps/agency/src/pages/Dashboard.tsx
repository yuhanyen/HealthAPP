import { Badge, Button, Card, Notice, Spinner, useAsync, useT } from "@healthapp/ui";
import { useState } from "react";

import { api, fmt, METRICS, openArtifact, type Overview } from "../api";
import { MapView } from "../MapView";
import { TrendChart } from "../TrendChart";
import { AlertList } from "./Alerts";

const TONE = { better: "ok", worse: "danger", stable: "neutral" } as const;

export function Dashboard() {
  const { t } = useT();
  const [metric, setMetric] = useState<string>("bp_uncontrolled_rate");
  const data = useAsync(() => api.get<Overview>(`/agency/overview?metric=${metric}&weeks=12`), [metric]);

  if (data.loading && !data.data) return <Spinner />;
  if (data.error || !data.data) return <Notice tone="danger">{t("citizen.common.offline")}</Notice>;
  const o = data.data;
  const series = o.cohort.series ?? {};
  const features = o.cohort.hotspots?.features ?? [];
  const names = Object.fromEntries(features.map((f) => [f.properties.village_code, f.properties.name]));
  // 趨勢：全區＋最近一週數值最高的 4 個里
  const latest = (code: string) => series[code]?.filter((p) => p.value !== null).at(-1)?.value ?? -1;
  const top = Object.keys(series)
    .filter((c) => c !== "ALL")
    .sort((a, b) => latest(b) - latest(a))
    .slice(0, 4);
  const lines = ["ALL", ...top]
    .filter((c) => series[c])
    .map((c) => ({ name: c === "ALL" ? t("agency_ui.all") : names[c] ?? c, points: series[c]! }));

  return (
    <div className="grid">
      <section>
        <div className="row spread">
          <h2>{t("agency_ui.kpi_title")}</h2>
          <Button
            variant="secondary"
            onClick={async () => {
              const built = await api.post<{ artifact: string }>("/agency/kpi/report?period=month");
              await openArtifact(built.artifact);
            }}
          >
            {t("agency_ui.kpi_report")}
          </Button>
        </div>
        <div className="kpis">
          {o.kpis.map((k) => (
            <div className="kpi" key={k.metric}>
              <small>{k.label}</small>
              <div className="value">{fmt(k.metric, k.current)}</div>
              <div className="row">
                <Badge tone={TONE[k.direction]}>{t(`agency_ui.direction.${k.direction}`)}</Badge>
                <small>
                  {fmt(k.metric, k.previous)} → {fmt(k.metric, k.current)}
                </small>
              </div>
            </div>
          ))}
        </div>
      </section>

      <div className="row">
        <label htmlFor="metric" style={{ fontWeight: 700 }}>
          {t("agency_ui.metric_select")}
        </label>
        <div className="field" style={{ minWidth: 220 }}>
          <select id="metric" value={metric} onChange={(e) => setMetric(e.target.value)}>
            {METRICS.map((m) => (
              <option key={m} value={m}>
                {t(`metric.${m}`)}
              </option>
            ))}
          </select>
        </div>
      </div>

      <div className="two-col">
        <Card title={t("agency_ui.map_title")}>
          <MapView
            features={features}
            describe={(p) =>
              p.masked
                ? t("agency_ui.map_popup_masked", { name: p.name, masked: t("agency_ui.map_masked") })
                : t("agency_ui.map_popup", { name: p.name, value: fmt(metric, p.value), n: p.n ?? 0 })
            }
            legend={{
              low: t("agency_ui.legend_low"),
              high: t("agency_ui.legend_high"),
              masked: t("agency_ui.map_masked"),
            }}
          />
          <small>{t("agency_ui.map_note", { k: o.k })}</small>
        </Card>
        <Card title={t("agency_ui.trend_title", { weeks: 12 })}>
          <TrendChart lines={lines} format={(v) => fmt(metric, v)} />
          <small>{t("agency_ui.masked_note", { k: o.k, masked: o.masked_rows })}</small>
        </Card>
      </div>

      <div className="two-col">
        <Card title={t("agency_ui.alerts_title")}>
          <AlertList alerts={o.alerts.slice(0, 5)} onChange={data.reload} />
        </Card>
        <Card title={t("agency_ui.care_title")}>
          {o.care_list.rows.length === 0 ? (
            <p className="muted">{t("agency_ui.care_none")}</p>
          ) : (
            <ul className="list">
              {o.care_list.rows.map((r) => (
                <li key={`${r.site_id}-${r.task_type}`}>
                  {t("agency_ui.care_row", {
                    site: r.site_id === "ALL" ? t("agency_ui.all") : r.site_id,
                    task: t(`agency_ui.task_type.${r.task_type}`),
                    n: r.n,
                  })}
                </li>
              ))}
            </ul>
          )}
        </Card>
      </div>
    </div>
  );
}
