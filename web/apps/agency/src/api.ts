import { createApi } from "@healthapp/ui";

export const api = createApi({ storageKey: "healthapp.agency.token" });

export interface Kpi {
  metric: string;
  label: string;
  current: number | null;
  previous: number | null;
  n: number;
  direction: "better" | "worse" | "stable";
}

export interface Feature {
  type: "Feature";
  geometry: unknown;
  properties: { village_code: string; name: string; value: number | null; n: number | null; masked: boolean };
}

export interface Alert {
  id: number;
  village_code: string;
  topic: string;
  category: string;
  count: number;
  baseline: number;
  ratio: number;
  status: "open" | "analyzed";
  created_at: string;
  analysis: {
    consistent: boolean;
    summary: string;
    suggestion: string;
    queries: string[];
    report: string | null;
    approval: string;
    events: { date: string; event: string; source_url: string }[];
  } | null;
}

export interface Overview {
  k: number;
  metric: string;
  metric_label: string;
  kpis: Kpi[];
  cohort: {
    series?: Record<string, { week: string; value: number | null; n: number | null }[]>;
    hotspots?: { type: "FeatureCollection"; features: Feature[] };
  };
  masked_rows: number;
  alerts: Alert[];
  care_list: { day: string | null; rows: { site_id: string; task_type: string; n: number }[] };
}

export const METRICS = [
  "bp_uncontrolled_rate",
  "measure_freq",
  "site_usage",
  "steps_avg",
  "steps_goal_rate",
  "redeem_rate",
  "event_attend_rate",
] as const;

export function fmt(metric: string, value: number | null): string {
  if (value === null) return "—";
  if (metric === "steps_avg") return Math.round(value).toLocaleString("zh-TW");
  if (metric === "measure_freq") return value.toFixed(2);
  return `${(value * 100).toFixed(1)}%`;
}

/** 以 Bearer token 下載分析產物，在新分頁開啟（報告本身不含腳本）。 */
export async function openArtifact(name: string): Promise<void> {
  const blob = await api.blob(`/agency/artifacts/${name}`);
  const url = URL.createObjectURL(blob);
  window.open(url, "_blank", "noopener");
  window.setTimeout(() => URL.revokeObjectURL(url), 60_000);
}
