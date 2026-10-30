import { Button, Card, Notice, Screen, Spinner, Toggle, useT } from "@healthapp/ui";
import { useState } from "react";

import { api } from "../api";
import { useExplains } from "./Consents";

/** 授權引導：每一項預設不勾選，由本人逐項決定（資料最小化）。 */
export function Onboarding({ onDone }: { onDone: () => void }) {
  const { t } = useT();
  const explains = useExplains();
  const [chosen, setChosen] = useState<Record<string, boolean>>({});
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  if (explains.loading) return <Spinner label={t("citizen.common.loading")} />;

  const start = async () => {
    setBusy(true);
    setError(null);
    try {
      for (const explain of explains.data ?? []) {
        if (chosen[explain.source]) {
          await api.post("/consents", { source: explain.source, scope: { read: explain.items } });
        }
      }
      onDone();
    } catch (err) {
      setError(t("citizen.common.error", { reason: (err as Error).message }));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Screen title={t("citizen.consent.onboarding_title")}>
      <Notice tone="primary">{t("citizen.consent.onboarding_intro")}</Notice>
      {explains.data?.map((explain) => (
        <Card key={explain.source}>
          <Toggle
            id={`consent-${explain.source}`}
            label={explain.name}
            checked={Boolean(chosen[explain.source])}
            onChange={(value) => setChosen((prev) => ({ ...prev, [explain.source]: value }))}
          />
          <p>{explain.purpose}</p>
          <p className="muted">
            {t("citizen.consent.items", {
              items: explain.items.map((i) => t(`consent.items.${i}`)).join("、"),
            })}
          </p>
        </Card>
      ))}
      {error && <Notice tone="danger">{error}</Notice>}
      <Button block disabled={busy} onClick={start}>
        {t("citizen.consent.start")}
      </Button>
      <Button block variant="ghost" onClick={onDone}>
        {t("citizen.consent.later")}
      </Button>
    </Screen>
  );
}
