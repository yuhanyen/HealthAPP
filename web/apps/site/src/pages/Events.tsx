import {
  Badge,
  blankDraft,
  Button,
  canScan,
  Card,
  EventForm,
  Field,
  Notice,
  Scanner,
  Screen,
  Spinner,
  useAsync,
  useT,
} from "@healthapp/ui";
import { useState, type FormEvent } from "react";

import { api } from "../api";

interface SiteEvent {
  event_id: string;
  title: string;
  category_label: string;
  when: string;
  venue: string;
  points_reward: number;
  capacity: number | null;
  registered: number | null;
  attended: number | null;
  status: string;
}

interface Registrations {
  rows: { person: string; status: string; checked_in_at: string | null; points_granted: number }[];
}

function Checkin({ event, onDone }: { event: SiteEvent; onDone: () => void }) {
  const { t } = useT();
  const list = useAsync(() => api.get<Registrations>(`/events/${event.event_id}/registrations`), [event.event_id]);
  const [qr, setQr] = useState("");
  const [scan, setScan] = useState(false);
  const [message, setMessage] = useState<{ tone: "ok" | "danger"; text: string } | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    try {
      const out = await api.post<{ message: string }>(`/events/${event.event_id}/checkin`, { user_qr: qr.trim() });
      setMessage({ tone: "ok", text: out.message });
      setQr("");
      list.reload();
      onDone();
    } catch (err) {
      setMessage({ tone: "danger", text: (err as Error).message });
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="stack">
      <form className="stack" onSubmit={submit}>
        <Field id={`qr-${event.event_id}`} label={t("site_ui.events.qr")} hint={t("site_ui.events.qr_hint")}
          value={qr} onChange={(e) => setQr(e.target.value)} required />
        {canScan() ? (
          <Button variant="secondary" onClick={() => setScan((v) => !v)}>
            {t("site_ui.scan")}
          </Button>
        ) : (
          <small>{t("site_ui.scan_unsupported")}</small>
        )}
        {scan && (
          <Scanner
            onResult={(text) => {
              setQr(text);
              setScan(false);
            }}
            onClose={() => setScan(false)}
          />
        )}
        {message && <Notice tone={message.tone}>{message.text}</Notice>}
        <Button type="submit" block disabled={busy || !qr.trim()}>
          {t("site_ui.events.checkin")}
        </Button>
      </form>
      <strong>{t("site_ui.events.list", { n: list.data?.rows.length ?? 0 })}</strong>
      <ul className="list">
        {list.data?.rows.map((r, i) => (
          <li key={i} className="row spread">
            <span>{r.person}</span>
            <Badge tone={r.status === "attended" ? "ok" : "primary"}>
              {t(`citizen.events.status.${r.status}`)}
              {r.points_granted > 0 ? ` +${r.points_granted}` : ""}
            </Badge>
          </li>
        ))}
      </ul>
      <small className="muted">{t("site_ui.events.list_note")}</small>
    </div>
  );
}

export function Events({ siteId }: { siteId: string }) {
  const { t } = useT();
  const events = useAsync(() => api.get<SiteEvent[]>("/events?upcoming=true"));
  const [creating, setCreating] = useState(false);
  const [open, setOpen] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);

  if (events.loading && !events.data) return <Spinner />;
  const active = (events.data ?? []).filter((e) => e.status === "published");

  return (
    <Screen
      title={t("site_ui.events.title")}
      actions={
        <Button variant="secondary" onClick={() => setCreating((v) => !v)}>
          {creating ? t("citizen.common.close") : t("site_ui.events.create")}
        </Button>
      }
    >
      {message && <Notice tone="ok">{message}</Notice>}
      {creating && (
        <Card>
          <EventForm
            initial={{ ...blankDraft(), site_id: siteId }}
            submitLabel={t("site_ui.events.publish")}
            onSubmit={async (draft) => {
              await api.post("/events", draft);
              setCreating(false);
              setMessage(t("site_ui.events.published", { title: draft.title }));
              events.reload();
            }}
          />
        </Card>
      )}
      {active.length === 0 && <Notice>{t("site_ui.events.none")}</Notice>}
      {active.map((e) => (
        <Card key={e.event_id}>
          <div className="row spread">
            <Badge tone="primary">{e.category_label}</Badge>
            {e.points_reward > 0 && <Badge tone="ok">{t("citizen.events.points", { points: e.points_reward })}</Badge>}
          </div>
          <h2 style={{ marginTop: 8 }}>{e.title}</h2>
          <p>
            {e.when}・{e.venue}
          </p>
          <p className="muted">
            {t("site_ui.events.counts", {
              registered: e.registered ?? 0,
              attended: e.attended ?? 0,
              capacity: e.capacity ?? "—",
            })}
          </p>
          <Button block variant={open === e.event_id ? "secondary" : "primary"}
            onClick={() => setOpen(open === e.event_id ? null : e.event_id)}>
            {open === e.event_id ? t("citizen.common.close") : t("site_ui.events.open")}
          </Button>
          {open === e.event_id && (
            <div style={{ marginTop: 12 }}>
              <Checkin event={e} onDone={events.reload} />
            </div>
          )}
        </Card>
      ))}
    </Screen>
  );
}
