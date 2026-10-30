import { ApiError, Badge, Button, Card, Modal, Notice, Screen, Spinner, useAsync, useT } from "@healthapp/ui";
import { useEffect, useState } from "react";

import { api, day, when } from "../api";
import { RISK_TONE, type ActionResult, type Booking, type Report, type ReportBrief } from "../types";

function useSpeech() {
  const [speaking, setSpeaking] = useState(false);
  useEffect(
    () => () => {
      window.speechSynthesis?.cancel();
    },
    [],
  );
  const speak = (text: string) => {
    const synth = window.speechSynthesis;
    if (!synth) return;
    synth.cancel();
    const utter = new SpeechSynthesisUtterance(text);
    utter.lang = "zh-TW";
    utter.rate = 0.9;
    utter.onend = () => setSpeaking(false);
    setSpeaking(true);
    synth.speak(utter);
  };
  const stop = () => {
    window.speechSynthesis?.cancel();
    setSpeaking(false);
  };
  return { speaking, speak, stop, supported: "speechSynthesis" in window };
}

export function Today({ go }: { go: (path: string) => void }) {
  const { t } = useT();
  const report = useAsync(async () => {
    try {
      return await api.get<Report>("/me/reports");
    } catch (err) {
      if (err instanceof ApiError && err.status === 404) return null;
      throw err;
    }
  });
  const bookings = useAsync(() => api.get<Booking[]>("/me/bookings"));
  const history = useAsync(() => api.get<ReportBrief[]>("/me/reports/history?limit=7"));
  const speech = useSpeech();
  const [pending, setPending] = useState<{ slot: string; action: ActionResult } | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  if (report.loading) return <Spinner label={t("citizen.common.loading")} />;
  const content = report.data?.content;

  const askBooking = async (slot: string) => {
    if (!content?.decision) return;
    setBusy(true);
    try {
      const action = await api.post<ActionResult>("/me/bookings", {
        site_id: content.decision.site_id,
        slot_at: slot,
      });
      setPending({ slot, action });
    } catch (err) {
      setMessage(t("citizen.common.error", { reason: (err as Error).message }));
    } finally {
      setBusy(false);
    }
  };

  const confirmBooking = async () => {
    if (!pending || !content?.decision) return;
    setBusy(true);
    try {
      await api.post(`/me/actions/${pending.action.action_id}/confirm`);
      setMessage(t("citizen.today.booked", { site: content.decision.site_name, when: when(pending.slot) }));
      bookings.reload();
    } catch (err) {
      setMessage(t("citizen.common.error", { reason: (err as Error).message }));
    } finally {
      setPending(null);
      setBusy(false);
    }
  };

  const cancelBooking = async () => {
    if (pending) await api.post(`/me/actions/${pending.action.action_id}/cancel`).catch(() => undefined);
    setPending(null);
  };

  return (
    <Screen title={content?.title ?? t("citizen.today.title")}>
      {report.error && <Notice tone="danger">{t("citizen.common.offline")}</Notice>}
      {!content && !report.error && <Notice>{t("citizen.today.no_report")}</Notice>}
      {content && (
        <>
          <Card tone={RISK_TONE[content.risk.level]}>
            <div className="row spread">
              <Badge tone={RISK_TONE[content.risk.level]}>
                {t(`citizen.today.risk.${content.risk.level}`)}
              </Badge>
              {speech.supported && (
                <Button
                  variant="secondary"
                  onClick={() => (speech.speaking ? speech.stop() : speech.speak(content.voice_script))}
                >
                  {speech.speaking ? t("citizen.today.stop") : t("citizen.today.listen")}
                </Button>
              )}
            </div>
            <p className="status-sentence" style={{ marginTop: 12 }}>
              {content.status_sentence}
            </p>
          </Card>

          {content.decision && (
            <Card title={t("citizen.today.decision")} tone="primary">
              <p>{content.decision.text.replace(/^▸\s*\S+：/, "")}</p>
              <div className="stack">
                {content.decision.slots.map((slot) => (
                  <Button key={slot} block disabled={busy} onClick={() => askBooking(slot)}>
                    {t("citizen.today.book_slot", { when: when(slot) })}
                  </Button>
                ))}
              </div>
            </Card>
          )}
          {message && <Notice tone="ok">{message}</Notice>}

          <Card>
            <ul className="report-lines">
              {content.lines
                .filter((line) => !content.decision || line !== content.decision.text)
                .filter((line) => !content.events?.length || !line.includes(content.events[0]!.title))
                .map((line) => (
                  <li key={line}>{line}</li>
                ))}
            </ul>
          </Card>

          {content.events && content.events.length > 0 && (
            <Card title={t("citizen.today.events")}>
              <p style={{ fontWeight: 700 }}>{content.events[0]!.title}</p>
              <p>
                {content.events[0]!.when}・{content.events[0]!.venue}
                {content.events[0]!.points_reward > 0 &&
                  `（${t("citizen.events.points", { points: content.events[0]!.points_reward })}）`}
              </p>
              {content.events[0]!.reasons[0] && <small>{content.events[0]!.reasons[0]}</small>}
              <div style={{ marginTop: 12 }}>
                <Button block variant="secondary" onClick={() => go("/events")}>
                  {t("citizen.today.see_events", { n: content.events.length })}
                </Button>
              </div>
            </Card>
          )}
        </>
      )}

      {bookings.data && bookings.data.length > 0 && (
        <Card title={t("citizen.today.upcoming")}>
          <ul className="list">
            {bookings.data.map((b) => (
              <li key={b.id}>
                {when(b.slot_at)} {b.site_name}（{b.purpose}）
              </li>
            ))}
          </ul>
        </Card>
      )}

      <Button variant="secondary" block onClick={() => go("/chat")}>
        {t("citizen.today.ask")}
      </Button>

      {history.data && history.data.length > 1 && (
        <Card title={t("citizen.today.history")}>
          <ul className="list">
            {history.data.slice(1).map((h) => (
              <li key={h.date} className="row spread">
                <span>
                  {day(h.date)} {h.status_sentence}
                </span>
                {h.risk_level && (
                  <Badge tone={RISK_TONE[h.risk_level]}>{t(`citizen.today.risk.${h.risk_level}`)}</Badge>
                )}
              </li>
            ))}
          </ul>
        </Card>
      )}

      <Modal
        open={pending !== null}
        title={t("citizen.today.confirm_booking")}
        onClose={cancelBooking}
        actions={
          <>
            <Button block disabled={busy} onClick={confirmBooking}>
              {t("citizen.common.confirm")}
            </Button>
            <Button block variant="secondary" onClick={cancelBooking}>
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
