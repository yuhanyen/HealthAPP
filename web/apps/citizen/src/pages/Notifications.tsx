import { Badge, Card, Screen, Spinner, useAsync, useT } from "@healthapp/ui";

import { api, when } from "../api";
import type { Notification } from "../types";

export function Notifications({ go }: { go: (path: string) => void }) {
  const { t } = useT();
  const notes = useAsync(() => api.get<Notification[]>("/me/notifications"));
  if (notes.loading) return <Spinner label={t("citizen.common.loading")} />;

  const open = async (note: Notification) => {
    if (!note.read_at) await api.post(`/me/notifications/${note.id}/read`).catch(() => undefined);
    if (note.payload?.report_date) go("/today");
    else if (note.payload?.event_id) go("/events");
    else notes.reload();
  };

  return (
    <Screen title={t("citizen.notifications.title")}>
      {notes.data?.length === 0 && <p className="muted">{t("citizen.notifications.none")}</p>}
      {notes.data?.map((note) => (
        <button
          key={note.id}
          type="button"
          onClick={() => open(note)}
          style={{ all: "unset", cursor: "pointer", display: "block" }}
        >
          <Card tone={note.kind === "alert" ? "alert" : note.read_at ? undefined : "primary"}>
            <div className="row spread">
              <strong>{note.title}</strong>
              {!note.read_at && <Badge tone="primary">●</Badge>}
            </div>
            <p style={{ whiteSpace: "pre-wrap" }}>{note.body}</p>
            <small>{when(note.created_at)}</small>
          </Card>
        </button>
      ))}
    </Screen>
  );
}
