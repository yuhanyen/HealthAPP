// 活動上架／審核表單（機關端、據點端共用；SPEC §17.6）。文字一律來自 i18n。
import { useState, type FormEvent, type ReactNode } from "react";

import { Button, Field, Notice } from "./components";
import { useT } from "./i18n";

export const EVENT_CATEGORIES = ["measure", "walk", "exercise", "education", "nutrition", "vaccine", "social"] as const;
export const EVENT_TAGS = ["bp", "glucose", "weight", "steps", "indoor", "outdoor", "gentle", "free"] as const;

export interface EventDraft {
  title: string;
  category: string;
  site_id: string | null;
  venue: string;
  description: string;
  start_at: string; // ISO
  end_at: string; // ISO
  capacity: number | null;
  points_reward: number;
  tags: string[];
}

const pad = (n: number) => String(n).padStart(2, "0");

function localParts(iso: string): { date: string; time: string } {
  const d = new Date(iso);
  return {
    date: `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`,
    time: `${pad(d.getHours())}:${pad(d.getMinutes())}`,
  };
}

export function blankDraft(): EventDraft {
  const start = new Date();
  start.setDate(start.getDate() + 3);
  start.setHours(9, 0, 0, 0);
  const end = new Date(start);
  end.setHours(11);
  return {
    title: "",
    category: "measure",
    site_id: null,
    venue: "",
    description: "",
    start_at: start.toISOString(),
    end_at: end.toISOString(),
    capacity: null,
    points_reward: 0,
    tags: [],
  };
}

export function EventForm({
  initial,
  sites,
  submitLabel,
  onSubmit,
  extra,
}: {
  initial: EventDraft;
  sites?: { id: string; name: string }[];
  submitLabel: string;
  onSubmit: (draft: EventDraft) => Promise<void>;
  extra?: ReactNode;
}) {
  const { t } = useT();
  const start = localParts(initial.start_at);
  const end = localParts(initial.end_at);
  const [draft, setDraft] = useState(initial);
  const [date, setDate] = useState(start.date);
  const [from, setFrom] = useState(start.time);
  const [to, setTo] = useState(end.time);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const id = (name: string) => `${initial.title || "new"}-${name}`.replace(/\s+/g, "-");
  const set = (patch: Partial<EventDraft>) => setDraft({ ...draft, ...patch });

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await onSubmit({
        ...draft,
        start_at: new Date(`${date}T${from}`).toISOString(),
        end_at: new Date(`${date}T${to}`).toISOString(),
      });
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <form className="stack" onSubmit={submit}>
      <Field id={id("title")} label={t("events_ui.field.title")} value={draft.title} required minLength={2}
        maxLength={80} onChange={(e) => set({ title: e.target.value })} />
      <div className="row">
        <div className="field" style={{ flex: 1 }}>
          <label htmlFor={id("category")}>{t("events_ui.field.category")}</label>
          <select id={id("category")} value={draft.category} onChange={(e) => set({ category: e.target.value })}>
            {EVENT_CATEGORIES.map((c) => (
              <option key={c} value={c}>
                {t(`events.category.${c}`)}
              </option>
            ))}
          </select>
        </div>
        {sites && (
          <div className="field" style={{ flex: 1 }}>
            <label htmlFor={id("site")}>{t("events_ui.field.site")}</label>
            <select id={id("site")} value={draft.site_id ?? ""}
              onChange={(e) => set({ site_id: e.target.value || null })}>
              <option value="">{t("events_ui.field.no_site")}</option>
              {sites.map((s) => (
                <option key={s.id} value={s.id}>
                  {s.name}
                </option>
              ))}
            </select>
          </div>
        )}
      </div>
      <Field id={id("venue")} label={t("events_ui.field.venue")} value={draft.venue} maxLength={80}
        hint={t("events_ui.field.venue_hint")} onChange={(e) => set({ venue: e.target.value })} />
      <div className="row">
        <Field id={id("date")} label={t("events_ui.field.date")} type="date" value={date} required
          onChange={(e) => setDate(e.target.value)} />
        <Field id={id("from")} label={t("events_ui.field.from")} type="time" value={from} required
          onChange={(e) => setFrom(e.target.value)} />
        <Field id={id("to")} label={t("events_ui.field.to")} type="time" value={to} required
          onChange={(e) => setTo(e.target.value)} />
      </div>
      <div className="row">
        <Field id={id("capacity")} label={t("events_ui.field.capacity")} type="number" min={1} inputMode="numeric"
          value={draft.capacity ?? ""} hint={t("events_ui.field.capacity_hint")}
          onChange={(e) => set({ capacity: e.target.value ? Number(e.target.value) : null })} />
        <Field id={id("points")} label={t("events_ui.field.points")} type="number" min={0} max={200}
          inputMode="numeric" value={draft.points_reward} hint={t("events_ui.field.points_hint")}
          onChange={(e) => set({ points_reward: Number(e.target.value || 0) })} />
      </div>
      <div className="field">
        <label>{t("events_ui.field.tags")}</label>
        <div className="chips">
          {EVENT_TAGS.map((tag) => {
            const on = draft.tags.includes(tag);
            return (
              <button key={tag} type="button" className="chip" aria-pressed={on}
                style={on ? { background: "var(--primary)", color: "var(--primary-ink)" } : undefined}
                onClick={() => set({ tags: on ? draft.tags.filter((x) => x !== tag) : [...draft.tags, tag] })}>
                {on ? "✓ " : ""}
                {t(`events_ui.tag.${tag}`)}
              </button>
            );
          })}
        </div>
        <small>{t("events_ui.field.tags_hint")}</small>
      </div>
      <div className="field">
        <label htmlFor={id("desc")}>{t("events_ui.field.description")}</label>
        <textarea id={id("desc")} value={draft.description} maxLength={400}
          onChange={(e) => set({ description: e.target.value })} />
      </div>
      {error && <Notice tone="danger">{error}</Notice>}
      <div className="row">
        <Button type="submit" disabled={busy}>
          {submitLabel}
        </Button>
        {extra}
      </div>
    </form>
  );
}
