import { Badge, blankDraft, Button, Card, EventForm, Notice, Spinner, useAsync, useT, type EventDraft } from "@healthapp/ui";
import { useState } from "react";

import { api } from "../api";

interface StaffEvent extends EventDraft {
  event_id: string;
  category_label: string;
  organizer: string;
  when: string;
  status: string;
  source: string;
  source_url: string | null;
  extracted_by: string | null;
  registered: number | null;
  attended: number | null;
  count_text: string | null;
}

const toDraft = (e: StaffEvent): EventDraft => ({
  title: e.title,
  category: e.category,
  site_id: e.site_id,
  venue: e.venue,
  description: e.description,
  start_at: e.start_at,
  end_at: e.end_at,
  capacity: e.capacity,
  points_reward: e.points_reward,
  tags: e.tags,
});

export function Events() {
  const { t } = useT();
  const pending = useAsync(() => api.get<StaffEvent[]>("/events?status=pending_review"));
  const published = useAsync(() => api.get<StaffEvent[]>("/events?status=published&upcoming=true"));
  const sites = useAsync(() => api.get<{ id: string; name: string }[]>("/events/sites"));
  const [creating, setCreating] = useState(false);
  const [message, setMessage] = useState<string | null>(null);

  if ((pending.loading && !pending.data) || (sites.loading && !sites.data)) return <Spinner />;
  const reload = () => {
    pending.reload();
    published.reload();
  };

  const review = async (e: StaffEvent, draft: EventDraft | null) => {
    await api.post(`/events/${e.event_id}/review`, draft ? { decision: "approve", ...draft } : { decision: "reject" });
    setMessage(t(draft ? "agency_ui.events.approved" : "agency_ui.events.rejected", { title: e.title }));
    reload();
  };

  const cancel = async (e: StaffEvent) => {
    const out = await api.post<{ notified: number }>(`/events/${e.event_id}/cancel`);
    setMessage(t("agency_ui.events.cancelled", { title: e.title, n: out.notified }));
    reload();
  };

  return (
    <div className="stack">
      {message && <Notice tone="ok">{message}</Notice>}

      <Card title={t("agency_ui.events.pending_title", { n: pending.data?.length ?? 0 })}>
        <p className="muted">{t("agency_ui.events.pending_intro")}</p>
        {pending.data?.length === 0 && <p className="muted">{t("agency_ui.events.pending_none")}</p>}
        <div className="stack">
          {pending.data?.map((e) => (
            <div key={e.event_id} className="stack" style={{ borderTop: "1px solid var(--border)", paddingTop: 12 }}>
              <div className="row spread">
                <strong>{e.title}</strong>
                <Badge tone="warn">{t(`agency_ui.events.extracted.${e.extracted_by ?? "rules"}`)}</Badge>
              </div>
              {e.source_url && (
                <small>
                  {t("agency_ui.events.source")}
                  <a href={e.source_url} target="_blank" rel="noopener noreferrer">
                    {e.source_url}
                  </a>
                </small>
              )}
              <Notice>
                <small style={{ whiteSpace: "pre-wrap" }}>{e.description}</small>
              </Notice>
              <EventForm
                initial={toDraft(e)}
                sites={sites.data ?? []}
                submitLabel={t("agency_ui.events.approve")}
                onSubmit={(draft) => review(e, draft)}
                extra={
                  <Button variant="secondary" onClick={() => review(e, null)}>
                    {t("agency_ui.events.reject")}
                  </Button>
                }
              />
            </div>
          ))}
        </div>
      </Card>

      <Card
        title={t("agency_ui.events.published_title")}
        actions={
          <Button variant="secondary" onClick={() => setCreating((v) => !v)}>
            {creating ? t("citizen.common.close") : t("agency_ui.events.create")}
          </Button>
        }
      >
        {creating && (
          <div style={{ marginBottom: 16 }}>
            <EventForm
              initial={blankDraft()}
              sites={sites.data ?? []}
              submitLabel={t("agency_ui.events.publish")}
              onSubmit={async (draft) => {
                await api.post("/events", draft);
                setCreating(false);
                setMessage(t("agency_ui.events.published", { title: draft.title }));
                reload();
              }}
            />
          </div>
        )}
        <table className="table">
          <thead>
            <tr>
              <th>{t("agency_ui.events.col.when")}</th>
              <th>{t("agency_ui.events.col.title")}</th>
              <th>{t("agency_ui.events.col.venue")}</th>
              <th>{t("agency_ui.events.col.points")}</th>
              <th>{t("agency_ui.events.col.registered")}</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {published.data?.map((e) => (
              <tr key={e.event_id}>
                <td>{e.when}</td>
                <td>
                  {e.title} <Badge>{e.category_label}</Badge>
                </td>
                <td>{e.venue}</td>
                <td>{e.points_reward || "—"}</td>
                <td>{e.count_text ?? e.registered}</td>
                <td>
                  <Button variant="ghost" onClick={() => cancel(e)}>
                    {t("agency_ui.events.cancel")}
                  </Button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        <small className="muted">{t("agency_ui.events.count_note")}</small>
      </Card>
    </div>
  );
}
