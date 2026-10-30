import { Badge, Button, Card, Modal, Notice, Screen, Spinner, useAsync, useT } from "@healthapp/ui";
import { useState } from "react";

import { api } from "../api";
import type { ActionResult, CommunityEvent, EventsOut, PendingAction } from "../types";

export function Events({ go }: { go: (path: string) => void }) {
  const { t } = useT();
  const data = useAsync(() => api.get<EventsOut>("/me/events"));
  const [pending, setPending] = useState<{ event: CommunityEvent; action: ActionResult } | null>(null);
  const [message, setMessage] = useState<{ tone: "ok" | "danger"; text: string } | null>(null);
  const [busy, setBusy] = useState(false);

  if (data.loading && !data.data) return <Spinner label={t("citizen.common.loading")} />;

  const fail = (err: unknown) =>
    setMessage({ tone: "danger", text: t("citizen.common.error", { reason: (err as Error).message }) });

  const ask = async (event: CommunityEvent) => {
    setBusy(true);
    setMessage(null);
    try {
      const action = await api.post<ActionResult>(`/me/events/${event.event_id}/register`, {});
      setPending({ event, action });
    } catch (err) {
      fail(err);
    } finally {
      setBusy(false);
    }
  };

  const confirm = async () => {
    if (!pending) return;
    setBusy(true);
    try {
      const action = await api.post<PendingAction>(`/me/actions/${pending.action.action_id}/confirm`);
      if (action.status === "executed") {
        setMessage({ tone: "ok", text: t("citizen.events.registered", { title: pending.event.title }) });
      } else {
        const reason = String(action.result?.message ?? action.result?.error ?? action.status);
        setMessage({ tone: "danger", text: t("citizen.common.error", { reason }) });
      }
      data.reload();
    } catch (err) {
      fail(err);
    } finally {
      setPending(null);
      setBusy(false);
    }
  };

  const dismiss = async () => {
    if (pending) await api.post(`/me/actions/${pending.action.action_id}/cancel`).catch(() => undefined);
    setPending(null);
  };

  const cancelRegistration = async (event: CommunityEvent) => {
    setBusy(true);
    try {
      await api.post(`/me/events/${event.event_id}/cancel`);
      setMessage({ tone: "ok", text: t("citizen.events.cancelled", { title: event.title }) });
      data.reload();
    } catch (err) {
      fail(err);
    } finally {
      setBusy(false);
    }
  };

  const upcoming = (data.data?.mine ?? []).filter((e) => new Date(e.end_at) > new Date());
  const past = (data.data?.mine ?? []).filter((e) => new Date(e.end_at) <= new Date());

  return (
    <Screen
      title={t("citizen.events.title")}
      actions={
        <Button variant="ghost" onClick={() => go("/prefs")}>
          {t("citizen.events.prefs")}
        </Button>
      }
    >
      {data.error && <Notice tone="danger">{t("citizen.common.offline")}</Notice>}
      {message && <Notice tone={message.tone}>{message.text}</Notice>}

      {upcoming.length > 0 && (
        <Card title={t("citizen.events.mine")} tone="primary">
          <ul className="list">
            {upcoming.map((e) => (
              <li key={e.event_id} className="row spread">
                <div>
                  <div style={{ fontWeight: 700 }}>{e.title}</div>
                  <small>
                    {e.when}・{e.venue}
                  </small>
                </div>
                {e.registration === "attended" ? (
                  <Badge tone="ok">{t("citizen.events.status.attended")}</Badge>
                ) : (
                  <Button variant="secondary" disabled={busy} onClick={() => cancelRegistration(e)}>
                    {t("citizen.events.cancel")}
                  </Button>
                )}
              </li>
            ))}
          </ul>
        </Card>
      )}

      {data.data && data.data.events.length === 0 && <Notice>{t("citizen.events.none")}</Notice>}

      {data.data?.events.map((e) => (
        <Card key={e.event_id}>
          <div className="row spread">
            <Badge tone="primary">{e.category_label}</Badge>
            {e.points_reward > 0 && <Badge tone="ok">{t("citizen.events.points", { points: e.points_reward })}</Badge>}
          </div>
          <h2 style={{ marginTop: 8 }}>{e.title}</h2>
          <p>
            {e.when}
            <br />
            {e.venue}
            {e.distance_km != null && `（${t("citizen.events.distance", { km: e.distance_km.toFixed(1) })}）`}
          </p>
          {e.seats_left != null && (
            <p className="muted">{t("citizen.events.seats", { n: e.seats_left })}</p>
          )}
          {e.reasons && e.reasons.length > 0 && (
            <div className="notice">
              <strong>{t("citizen.events.why")}</strong>
              <ul style={{ margin: "4px 0 0", paddingLeft: 22 }}>
                {e.reasons.slice(0, 3).map((r) => (
                  <li key={r}>{r}</li>
                ))}
              </ul>
              {e.basis?.map((b) => (
                <small key={b} style={{ display: "block" }}>
                  {b}
                </small>
              ))}
            </div>
          )}
          <div style={{ marginTop: 12 }}>
            {e.registration === "registered" || e.registration === "attended" ? (
              <Badge tone="ok">{t(`citizen.events.status.${e.registration}`)}</Badge>
            ) : (
              <Button block disabled={busy} onClick={() => ask(e)}>
                {t("citizen.events.register")}
              </Button>
            )}
          </div>
        </Card>
      ))}

      {past.length > 0 && (
        <Card title={t("citizen.events.past")}>
          <ul className="list">
            {past.map((e) => (
              <li key={e.event_id} className="row spread">
                <span>
                  {e.when.split("（")[0]} {e.title}
                </span>
                {e.registration === "attended" && (
                  <Badge tone="ok">
                    {(e.points_granted ?? 0) > 0
                      ? t("citizen.events.attended_points", { points: e.points_granted ?? 0 })
                      : t("citizen.events.status.attended")}
                  </Badge>
                )}
              </li>
            ))}
          </ul>
        </Card>
      )}

      {data.data?.note && <p className="muted">{data.data.note}</p>}

      <Modal
        open={pending !== null}
        title={t("citizen.events.confirm_title")}
        onClose={dismiss}
        actions={
          <>
            <Button block disabled={busy} onClick={confirm}>
              {t("citizen.common.confirm")}
            </Button>
            <Button block variant="secondary" onClick={dismiss}>
              {t("citizen.common.cancel")}
            </Button>
          </>
        }
      >
        <p>{pending?.action.summary}</p>
      </Modal>
    </Screen>
  );
}
