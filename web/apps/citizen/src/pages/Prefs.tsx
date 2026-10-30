import { Button, Card, Notice, Screen, Spinner, Toggle, useAsync, useT } from "@healthapp/ui";
import { useEffect, useState } from "react";

import { api } from "../api";
import type { Option, RecommendPrefs } from "../types";

const RATINGS = [null, 3.5, 4.0, 4.5] as const;
const DISTANCES = [null, 1, 2, 3, 5] as const;

function Choices({
  title,
  options,
  selected,
  onChange,
}: {
  title: string;
  options: Option[];
  selected: string[];
  onChange: (next: string[]) => void;
}) {
  const toggle = (value: string) =>
    onChange(selected.includes(value) ? selected.filter((v) => v !== value) : [...selected, value]);
  return (
    <Card title={title}>
      <div className="chips" role="group" aria-label={title}>
        {options.map((o) => (
          <button
            key={o.value}
            type="button"
            className="chip"
            aria-pressed={selected.includes(o.value)}
            onClick={() => toggle(o.value)}
            style={
              selected.includes(o.value)
                ? { background: "var(--primary)", color: "var(--primary-ink)" }
                : undefined
            }
          >
            {selected.includes(o.value) ? "✓ " : ""}
            {o.label}
          </button>
        ))}
      </div>
    </Card>
  );
}

export function Prefs({ go }: { go: (path: string) => void }) {
  const { t } = useT();
  const loaded = useAsync(() => api.get<RecommendPrefs>("/me/prefs/recommend"));
  const [prefs, setPrefs] = useState<RecommendPrefs | null>(null);
  const [message, setMessage] = useState<{ tone: "ok" | "danger"; text: string } | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (loaded.data) setPrefs(loaded.data);
  }, [loaded.data]);

  if (!prefs) return <Spinner label={t("citizen.common.loading")} />;
  const set = (patch: Partial<RecommendPrefs>) => setPrefs({ ...prefs, ...patch });

  const save = async () => {
    setBusy(true);
    try {
      const saved = await api.put<RecommendPrefs>("/me/prefs/recommend", {
        event_categories: prefs.event_categories,
        merchant_categories: prefs.merchant_categories,
        dietary: prefs.dietary,
        min_rating: prefs.min_rating,
        max_distance_km: prefs.max_distance_km,
        event_push: prefs.event_push,
      });
      setPrefs(saved);
      setMessage({ tone: "ok", text: t("citizen.prefs.saved") });
    } catch (err) {
      setMessage({ tone: "danger", text: t("citizen.common.error", { reason: (err as Error).message }) });
    } finally {
      setBusy(false);
    }
  };

  return (
    <Screen
      title={t("citizen.prefs.title")}
      actions={
        <Button variant="ghost" onClick={() => go("/events")}>
          {t("citizen.common.back")}
        </Button>
      }
    >
      <p className="muted">{t("citizen.prefs.intro")}</p>
      <Choices
        title={t("citizen.prefs.events")}
        options={prefs.options.event_categories}
        selected={prefs.event_categories}
        onChange={(event_categories) => set({ event_categories })}
      />
      <Choices
        title={t("citizen.prefs.merchants")}
        options={prefs.options.merchant_categories}
        selected={prefs.merchant_categories}
        onChange={(merchant_categories) => set({ merchant_categories })}
      />
      <Choices
        title={t("citizen.prefs.dietary")}
        options={prefs.options.dietary}
        selected={prefs.dietary}
        onChange={(dietary) => set({ dietary })}
      />
      <Card title={t("citizen.prefs.rating")}>
        <div className="field">
          <label htmlFor="min-rating">{t("citizen.prefs.rating_hint")}</label>
          <select
            id="min-rating"
            value={prefs.min_rating ?? ""}
            onChange={(e) => set({ min_rating: e.target.value ? Number(e.target.value) : null })}
          >
            {RATINGS.map((r) => (
              <option key={String(r)} value={r ?? ""}>
                {r === null ? t("citizen.prefs.rating_default") : t("citizen.prefs.rating_value", { v: r.toFixed(1) })}
              </option>
            ))}
          </select>
        </div>
        <div className="field" style={{ marginTop: 12 }}>
          <label htmlFor="distance">{t("citizen.prefs.distance")}</label>
          <select
            id="distance"
            value={prefs.max_distance_km ?? ""}
            onChange={(e) => set({ max_distance_km: e.target.value ? Number(e.target.value) : null })}
          >
            {DISTANCES.map((d) => (
              <option key={String(d)} value={d ?? ""}>
                {d === null ? t("citizen.prefs.distance_default") : t("citizen.prefs.distance_value", { km: d })}
              </option>
            ))}
          </select>
        </div>
      </Card>
      <Card>
        <Toggle
          id="event-push"
          label={t("citizen.prefs.event_push")}
          checked={prefs.event_push}
          onChange={(event_push) => set({ event_push })}
        />
        <small>{t("citizen.prefs.event_push_hint")}</small>
      </Card>
      {message && <Notice tone={message.tone}>{message.text}</Notice>}
      <Button block disabled={busy} onClick={save}>
        {t("citizen.prefs.save")}
      </Button>
    </Screen>
  );
}
