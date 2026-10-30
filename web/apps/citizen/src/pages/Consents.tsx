import { Badge, Button, Card, Modal, Notice, Screen, Spinner, useAsync, useT } from "@healthapp/ui";
import { useState } from "react";

import { api } from "../api";
import { SOURCES, type Consent, type Explain } from "../types";

interface RevokeOut {
  status: string;
  message: string;
  action_id: string | null;
}

export function useExplains() {
  return useAsync(() =>
    Promise.all(SOURCES.map((s) => api.get<Explain>(`/consents/explain/${s}`))),
  );
}

export function Consents() {
  const { t } = useT();
  const consents = useAsync(() => api.get<Consent[]>("/consents"));
  const explains = useExplains();
  const [open, setOpen] = useState<string | null>(null);
  const [pending, setPending] = useState<RevokeOut | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  if (consents.loading || explains.loading) return <Spinner label={t("citizen.common.loading")} />;

  const active = new Map((consents.data ?? []).map((c) => [c.source, c]));

  const grant = async (explain: Explain) => {
    setBusy(true);
    try {
      await api.post("/consents", { source: explain.source, scope: { read: explain.items } });
      consents.reload();
    } catch (err) {
      setMessage(t("citizen.common.error", { reason: (err as Error).message }));
    } finally {
      setBusy(false);
    }
  };

  // 撤回：先拿系統的確認文字（只有「確定要撤回嗎？撤回後您將失去：…」），本人按確認才執行
  const revoke = async (consent: Consent) => {
    setBusy(true);
    try {
      setPending(await api.del<RevokeOut>(`/consents/${consent.id}`));
    } catch (err) {
      setMessage(t("citizen.common.error", { reason: (err as Error).message }));
    } finally {
      setBusy(false);
    }
  };

  const confirm = async () => {
    if (!pending?.action_id) return;
    setBusy(true);
    try {
      await api.post(`/me/actions/${pending.action_id}/confirm`);
      setMessage(t("consent.revoke.done"));
      consents.reload();
    } catch (err) {
      setMessage(t("citizen.common.error", { reason: (err as Error).message }));
    } finally {
      setPending(null);
      setBusy(false);
    }
  };

  const cancel = async () => {
    if (pending?.action_id) await api.post(`/me/actions/${pending.action_id}/cancel`).catch(() => undefined);
    setPending(null);
  };

  return (
    <Screen title={t("citizen.consent.title")}>
      <Notice tone="primary">{t("citizen.consent.intro")}</Notice>
      {message && <Notice tone="ok">{message}</Notice>}
      {explains.data?.map((explain) => {
        const consent = active.get(explain.source);
        return (
          <Card
            key={explain.source}
            tone={consent ? "ok" : undefined}
            title={
              <div className="row spread">
                <span>{explain.name}</span>
                <Badge tone={consent ? "ok" : "neutral"}>
                  {consent ? t("citizen.consent.active") : t("citizen.consent.inactive")}
                </Badge>
              </div>
            }
            actions={
              consent ? (
                <Button variant="secondary" disabled={busy} onClick={() => revoke(consent)}>
                  {t("citizen.consent.revoke")}
                </Button>
              ) : (
                <Button disabled={busy} onClick={() => grant(explain)}>
                  {t("citizen.consent.grant")}
                </Button>
              )
            }
          >
            <p>{explain.purpose}</p>
            <p className="muted">
              {t("citizen.consent.items", {
                items: (consent?.scope.read ?? explain.items).map((i) => t(`consent.items.${i}`)).join("、"),
              })}
            </p>
            <Button variant="ghost" onClick={() => setOpen(open === explain.source ? null : explain.source)}>
              {t("citizen.consent.who")}
            </Button>
            {open === explain.source && (
              <ul>
                {explain.who_sees.map((line) => (
                  <li key={line}>{line}</li>
                ))}
              </ul>
            )}
          </Card>
        );
      })}

      <Modal
        open={pending !== null}
        title={t("citizen.consent.revoke")}
        onClose={cancel}
        actions={
          <>
            <Button block variant="danger" disabled={busy} onClick={confirm}>
              {t("citizen.common.confirm")}
            </Button>
            <Button block variant="secondary" onClick={cancel}>
              {t("citizen.common.cancel")}
            </Button>
          </>
        }
      >
        <p>{pending?.message}</p>
      </Modal>
    </Screen>
  );
}
