import { Card, Notice, Screen, Spinner, useAsync, useT } from "@healthapp/ui";

import { api, hhmm, type Board } from "../api";

export function Today({ siteId }: { siteId: string }) {
  const { t } = useT();
  const board = useAsync(() => api.get<Board>(`/sites/${siteId}/today`), [siteId]);
  if (board.loading && !board.data) return <Spinner />;
  if (!board.data) return <Notice tone="danger">{t("citizen.common.offline")}</Notice>;
  const b = board.data;
  return (
    <Screen title={t("site_ui.board_title", { site: b.site_name, date: b.date })}>
      <Card title={t("site_ui.bookings", { n: b.booking_total })}>
        {b.bookings.length === 0 ? (
          <p className="muted">{t("site_ui.no_bookings")}</p>
        ) : (
          <ul className="list">
            {b.bookings.map((s) => (
              <li key={s.slot_at}>{t("site_ui.slot", { time: hhmm(s.slot_at), n: s.count })}</li>
            ))}
          </ul>
        )}
      </Card>
      <Card title={t("site_ui.care_summary", { ...b.care_tasks })} />
      <Notice>{b.equipment}</Notice>
    </Screen>
  );
}
