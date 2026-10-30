import { Button, Card, Screen, Spinner, useAsync, useT } from "@healthapp/ui";

import { api } from "../api";
import { Qr } from "../Qr";

export function QrPage() {
  const { t } = useT();
  const qr = useAsync(() => api.get<{ token: string; expires_at: string }>("/me/qr"));
  return (
    <Screen title={t("citizen.settings.qr")}>
      <Card>
        {qr.loading && <Spinner />}
        {qr.data && <Qr value={qr.data.token} label={t("citizen.settings.qr")} />}
        <p className="muted" style={{ textAlign: "center", marginTop: 12 }}>
          {t("citizen.settings.qr_hint")}
        </p>
      </Card>
      <Button block variant="secondary" onClick={qr.reload}>
        {t("citizen.settings.refresh")}
      </Button>
    </Screen>
  );
}
