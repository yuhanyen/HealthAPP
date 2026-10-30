import { Badge, Button, Card, Field, Modal, Notice, Screen, Spinner, useAsync, useT } from "@healthapp/ui";
import { useState, type FormEvent } from "react";

import { api } from "../api";
import { RISK_TONE, type ActionResult, type FamilyOut, type FamilySummary } from "../types";

export function Family() {
  const { t } = useT();
  const family = useAsync(() => api.get<FamilyOut>("/me/family"));
  const [code, setCode] = useState("");
  const [myCode, setMyCode] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const [pending, setPending] = useState<ActionResult | null>(null);
  const [summaries, setSummaries] = useState<Record<string, FamilySummary>>({});
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  if (family.loading) return <Spinner label={t("citizen.common.loading")} />;

  const fail = (err: unknown) => setError(t("citizen.common.error", { reason: (err as Error).message }));

  const share = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      setPending(await api.post<ActionResult>("/me/family", { viewer_code: code.trim() }));
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
      await api.post(`/me/actions/${pending.action_id}/confirm`);
      setCode("");
      family.reload();
    } catch (err) {
      fail(err);
    } finally {
      setPending(null);
      setBusy(false);
    }
  };

  const cancel = async () => {
    if (pending) await api.post(`/me/actions/${pending.action_id}/cancel`).catch(() => undefined);
    setPending(null);
  };

  const unshare = async (linkId: string) => {
    try {
      await api.del(`/me/family/${linkId}`);
      family.reload();
    } catch (err) {
      fail(err);
    }
  };

  const view = async (ownerId: string) => {
    try {
      const summary = await api.get<FamilySummary>(`/family/${ownerId}/summary`);
      setSummaries((prev) => ({ ...prev, [ownerId]: summary }));
    } catch (err) {
      fail(err);
    }
  };

  const generate = async () => {
    try {
      setMyCode((await api.get<{ code: string }>("/me/family/code")).code);
      setCopied(false);
    } catch (err) {
      fail(err);
    }
  };

  return (
    <Screen title={t("citizen.family.title")}>
      <Notice tone="primary">{t("citizen.family.intro")}</Notice>
      {error && <Notice tone="danger">{error}</Notice>}

      <Card title={t("citizen.family.watching")}>
        {family.data?.shared_with_me.length === 0 && <p className="muted">{t("citizen.family.none")}</p>}
        <ul className="list">
          {family.data?.shared_with_me.map((link) => {
            const summary = summaries[link.user_id];
            return (
              <li key={link.link_id} className="stack">
                <div className="row spread">
                  <strong>{link.display_name}</strong>
                  <Button variant="secondary" onClick={() => view(link.user_id)}>
                    {t("citizen.family.view")}
                  </Button>
                </div>
                {summary && (
                  <Card
                    tone={summary.risk_level ? RISK_TONE[summary.risk_level as keyof typeof RISK_TONE] : undefined}
                    title={t("citizen.family.summary_title", { name: summary.owner_display_name })}
                  >
                    {summary.risk_label && (
                      <Badge tone={RISK_TONE[summary.risk_level as keyof typeof RISK_TONE]}>{summary.risk_label}</Badge>
                    )}
                    <p className="status-sentence" style={{ marginTop: 8 }}>
                      {summary.status_sentence}
                    </p>
                    <p>
                      {summary.pending_action
                        ? t("citizen.family.pending", { text: summary.pending_action })
                        : t("citizen.family.no_pending")}
                    </p>
                  </Card>
                )}
              </li>
            );
          })}
        </ul>
      </Card>

      <Card title={t("citizen.family.sharing")}>
        {family.data?.sharing.length === 0 && <p className="muted">{t("citizen.family.none")}</p>}
        <ul className="list">
          {family.data?.sharing.map((link) => (
            <li key={link.link_id} className="row spread">
              <strong>{link.display_name}</strong>
              <Button variant="secondary" onClick={() => unshare(link.link_id)}>
                {t("citizen.family.unshare")}
              </Button>
            </li>
          ))}
        </ul>
        <form className="stack" onSubmit={share} style={{ marginTop: 12 }}>
          <Field
            id="viewer-code"
            label={t("citizen.family.code_label")}
            hint={t("citizen.family.code_hint")}
            value={code}
            onChange={(e) => setCode(e.target.value)}
            required
          />
          <Button type="submit" block disabled={busy || !code.trim()}>
            {t("citizen.family.add")}
          </Button>
        </form>
      </Card>

      <Card title={t("citizen.family.my_code")}>
        <p className="muted">{t("citizen.family.my_code_hint")}</p>
        {myCode ? (
          <div className="stack">
            <div className="code-box">{myCode}</div>
            <Button
              variant="secondary"
              block
              onClick={() =>
                navigator.clipboard
                  ?.writeText(myCode)
                  .then(() => setCopied(true))
                  .catch(() => undefined)
              }
            >
              {copied ? t("citizen.family.copied") : t("citizen.family.copy")}
            </Button>
          </div>
        ) : (
          <Button variant="secondary" block onClick={generate}>
            {t("citizen.family.generate")}
          </Button>
        )}
      </Card>

      <Modal
        open={pending !== null}
        title={t("citizen.family.confirm_title")}
        onClose={cancel}
        actions={
          <>
            <Button block disabled={busy} onClick={confirm}>
              {t("citizen.common.confirm")}
            </Button>
            <Button block variant="secondary" onClick={cancel}>
              {t("citizen.common.cancel")}
            </Button>
          </>
        }
      >
        <p>{pending?.summary}</p>
      </Modal>
    </Screen>
  );
}
