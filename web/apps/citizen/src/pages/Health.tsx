import { Button, Card, Notice, Screen, Spinner, TimeChart, useAsync, useT } from "@healthapp/ui";
import { useState, type ReactNode } from "react";

import { api, day } from "../api";
import type { HealthSummary, Profile } from "../types";

const PERIODS = ["week", "month", "quarter"] as const;
type Period = (typeof PERIODS)[number];
// 序列顏色：分類色票前兩格（藍、橘），已用 dataviz 驗證器檢查色覺辨識度與對比
const BLUE = "#2a78d6";
const ORANGE = "#eb6834";

const n0 = (v: number) => Math.round(v).toLocaleString("zh-TW");
const n1 = (v: number) => (Math.round(v * 10) / 10).toLocaleString("zh-TW");

function Tile({ label, value, delta }: { label: string; value: ReactNode; delta?: ReactNode }) {
  return (
    <div className="tile">
      <small>{label}</small>
      <div className="tile-value">{value}</div>
      {delta}
    </div>
  );
}

function Delta({ pct, upIsGood, label }: { pct: number | null; upIsGood: boolean; label: string }) {
  if (pct === null) return null;
  const sign = pct > 0 ? "+" : pct < 0 ? "−" : "±";
  const good = pct === 0 ? null : (pct > 0) === upIsGood;
  const cls = good === null ? "" : `${pct > 0 ? "up" : "down"}-${good ? "good" : "bad"}`;
  return (
    <small className={`tile-delta ${cls}`}>
      {pct > 0 ? "▲" : pct < 0 ? "▼" : "＝"} {label} {sign}
      {Math.abs(pct)}%
    </small>
  );
}

export function Health({
  go,
  profile,
  report = false,
}: {
  go: (path: string) => void;
  profile: Profile;
  report?: boolean;
}) {
  const { t } = useT();
  const [period, setPeriod] = useState<Period>("month");
  const data = useAsync(() => api.get<HealthSummary>(`/me/health?period=${report ? "month" : period}`), [period, report]);

  if (data.loading && !data.data) return <Spinner label={t("citizen.common.loading")} />;
  const h = data.data;
  const m = h?.metrics ?? {};
  const chart = (_name: string, body: ReactNode) => body ?? <p className="muted">{t("citizen.health.no_data")}</p>;
  const common = h ? { start: h.start, end: h.end, tableLabel: t("citizen.health.table"), dateLabel: t("citizen.health.date") } : null;

  const title = report
    ? t("citizen.health.report_title", { name: profile.name })
    : t("citizen.health.title");

  return (
    <Screen
      title={title}
      actions={
        report ? (
          <Button variant="ghost" className="no-print" onClick={() => go("/health")}>
            {t("citizen.health.back")}
          </Button>
        ) : undefined
      }
    >
      {data.error && <Notice tone="danger">{t("citizen.common.offline")}</Notice>}

      {!report && (
        <div className="segmented no-print" role="group" aria-label={t("citizen.health.period_label")}>
          {PERIODS.map((p) => (
            <Button key={p} variant="secondary" aria-pressed={p === period} onClick={() => setPeriod(p)}>
              {t(`citizen.health.period.${p}`)}
            </Button>
          ))}
        </div>
      )}

      {h?.empty && <Notice>{t("citizen.health.empty")}</Notice>}

      {h && !h.empty && common && (
        <>
          {report && (
            <p className="muted">
              {t("citizen.health.report_range", {
                start: day(h.start),
                end: day(h.end),
                today: day(new Date().toISOString()),
              })}
            </p>
          )}
          <Card title={h.title} tone="primary">
            {h.lines.length ? (
              <ul className="report-lines">
                {h.lines.map((line) => (
                  <li key={line}>{line}</li>
                ))}
              </ul>
            ) : (
              <p className="muted">{t("citizen.health.no_data")}</p>
            )}
          </Card>

          {m.steps && (
            <Card title={t("citizen.health.metric.steps")}>
              <div className="tiles">
                <Tile
                  label={t("citizen.health.tile.avg_steps")}
                  value={m.steps.avg !== null ? `${n0(m.steps.avg)} ${t("citizen.health.unit.steps")}` : "—"}
                  delta={<Delta pct={m.steps.change_pct} upIsGood label={t("citizen.health.tile.change")} />}
                />
                <Tile
                  label={t("citizen.health.tile.goal_days", { goal: n0(m.steps.goal) })}
                  value={t("citizen.health.tile.days_value", { n: m.steps.goal_days, days: m.steps.days_with_data })}
                />
                <Tile
                  label={t("citizen.health.tile.peer", { band: m.steps.peer_band })}
                  value={m.steps.peer_avg !== null ? `${n0(m.steps.peer_avg)} ${t("citizen.health.unit.steps")}` : "—"}
                  delta={m.steps.peer_avg === null ? <small>{t("citizen.health.tile.peer_masked")}</small> : undefined}
                />
              </div>
              {chart(
                t("citizen.health.metric.steps"),
                m.steps.series.length > 0 ? (
                  <TimeChart
                    {...common}
                    title={t("citizen.health.metric.steps")}
                    series={[
                      {
                        key: "steps",
                        label: t("citizen.health.metric.steps"),
                        color: BLUE,
                        points: m.steps.series.map((p) => ({ at: p.date, value: p.value })),
                      },
                    ]}
                    refs={[{ value: m.steps.goal, label: t("citizen.health.goal_line", { goal: n0(m.steps.goal) }) }]}
                    format={n0}
                  />
                ) : null,
              )}
            </Card>
          )}

          {m.bp && (
            <Card title={t("citizen.health.metric.bp")}>
              <div className="tiles">
                <Tile label={t("citizen.health.tile.count")} value={`${m.bp.count} ${t("citizen.health.unit.times")}`} />
                <Tile label={t("citizen.health.tile.avg")} value={m.bp.avg ? `${m.bp.avg[0]}/${m.bp.avg[1]}` : "—"} />
                <Tile
                  label={t("citizen.health.tile.high")}
                  value={`${m.bp.high} ${t("citizen.health.unit.times")}`}
                  delta={<small>{t("citizen.health.tile.in_range", { n: m.bp.in_range })}</small>}
                />
              </div>
              {chart(
                t("citizen.health.metric.bp"),
                m.bp.series.length > 0 ? (
                  <TimeChart
                    {...common}
                    title={t("citizen.health.metric.bp")}
                    series={[
                      {
                        key: "sys",
                        label: t("citizen.health.systolic"),
                        color: BLUE,
                        points: m.bp.series.map((r) => ({ at: r.at, value: r.systolic })),
                      },
                      {
                        key: "dia",
                        label: t("citizen.health.diastolic"),
                        color: ORANGE,
                        points: m.bp.series.map((r) => ({ at: r.at, value: r.diastolic })),
                      },
                    ]}
                    refs={[
                      { value: m.bp.reference[0], label: t("citizen.health.ref_sys", { v: m.bp.reference[0] }) },
                      { value: m.bp.reference[1], label: t("citizen.health.ref_dia", { v: m.bp.reference[1] }) },
                    ]}
                    format={n0}
                  />
                ) : null,
              )}
              <small>{t("citizen.health.bp_note")}</small>
            </Card>
          )}

          {m.weight && (
            <Card title={t("citizen.health.metric.weight")}>
              {m.weight.count > 0 && (
              <div className="tiles">
                <Tile
                  label={t("citizen.health.tile.latest")}
                  value={m.weight.latest ? `${n1(m.weight.latest.value)} ${t("citizen.health.unit.kg")}` : "—"}
                />
                <Tile
                  label={t("citizen.health.tile.change_abs")}
                  value={m.weight.change != null ? `${m.weight.change > 0 ? "+" : ""}${n1(m.weight.change)}` : "—"}
                />
                {m.weight.bmi != null && <Tile label="BMI" value={n1(m.weight.bmi)} />}
              </div>
              )}
              {chart(
                t("citizen.health.metric.weight"),
                m.weight.series.length > 0 ? (
                  <TimeChart
                    {...common}
                    title={t("citizen.health.metric.weight")}
                    series={[{ key: "w", label: t("citizen.health.metric.weight"), color: BLUE, points: m.weight.series }]}
                    format={n1}
                  />
                ) : null,
              )}
            </Card>
          )}

          {m.glucose && (
            <Card title={t("citizen.health.metric.glucose")}>
              {m.glucose.count > 0 && (
                <div className="tiles">
                  <Tile label={t("citizen.health.tile.count")} value={`${m.glucose.count} ${t("citizen.health.unit.times")}`} />
                  <Tile label={t("citizen.health.tile.latest")} value={m.glucose.latest ? n0(m.glucose.latest.value) : "—"} />
                  <Tile label={t("citizen.health.tile.avg")} value={m.glucose.avg != null ? n0(m.glucose.avg) : "—"} />
                </div>
              )}
              {chart(
                t("citizen.health.metric.glucose"),
                m.glucose.series.length > 0 ? (
                  <TimeChart
                    {...common}
                    title={t("citizen.health.metric.glucose")}
                    series={[{ key: "g", label: t("citizen.health.metric.glucose"), color: BLUE, points: m.glucose.series }]}
                    format={n0}
                  />
                ) : null,
              )}
            </Card>
          )}

          {m.heart_rate && (
            <Card title={t("citizen.health.metric.heart_rate")}>
              <div className="tiles">
                <Tile
                  label={t("citizen.health.tile.hr_avg")}
                  value={m.heart_rate.avg !== null ? `${m.heart_rate.avg} ${t("citizen.health.unit.bpm")}` : "—"}
                />
              </div>
              {chart(
                t("citizen.health.metric.heart_rate"),
                m.heart_rate.series.length > 0 ? (
                  <TimeChart
                    {...common}
                    title={t("citizen.health.metric.heart_rate")}
                    series={[
                      {
                        key: "hr",
                        label: t("citizen.health.metric.heart_rate"),
                        color: BLUE,
                        points: m.heart_rate.series.map((p) => ({ at: p.date, value: p.value })),
                      },
                    ]}
                    format={n0}
                  />
                ) : null,
              )}
            </Card>
          )}

          {h.activity && (
            <Card title={t("citizen.health.activity")}>
              <div className="tiles">
                <Tile label={t("citizen.health.tile.points")} value={`+${h.activity.points_earned}`} />
                <Tile
                  label={t("citizen.health.tile.events")}
                  value={`${h.activity.events_attended} ${t("citizen.health.unit.times")}`}
                />
              </div>
            </Card>
          )}

          {h.not_authorized.length > 0 && (
            <Card>
              <p>
                {t("citizen.health.not_authorized", {
                  items: h.not_authorized.map((k) => t(`citizen.health.metric.${k}`)).join("、"),
                })}
              </p>
              <Button variant="secondary" className="no-print" onClick={() => go("/consents")}>
                {t("citizen.health.go_consent")}
              </Button>
            </Card>
          )}

          {h.note && <p className="muted">{h.note}</p>}

          <div className="stack no-print">
            {report ? (
              <Button block onClick={() => window.print()}>
                {t("citizen.health.print")}
              </Button>
            ) : (
              <Button block variant="secondary" onClick={() => go("/report")}>
                {t("citizen.health.report")}
              </Button>
            )}
          </div>
        </>
      )}
    </Screen>
  );
}
