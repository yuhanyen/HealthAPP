import { applyElderMode, Button, Card, Notice, Screen, Toggle, useT } from "@healthapp/ui";
import { useState } from "react";

import { api } from "../api";
import type { Profile } from "../types";

export function More({
  profile,
  onProfile,
  onLogout,
  go,
}: {
  profile: Profile;
  onProfile: (p: Profile) => void;
  onLogout: () => void;
  go: (path: string) => void;
}) {
  const { t } = useT();
  const [error, setError] = useState<string | null>(null);

  const toggleElder = async (on: boolean) => {
    applyElderMode(on);
    try {
      onProfile(await api.put<Profile>("/me/prefs", { elder_mode: on }));
    } catch (err) {
      applyElderMode(!on);
      setError(t("citizen.common.error", { reason: (err as Error).message }));
    }
  };

  return (
    <Screen title={t("citizen.settings.title")}>
      <Card>
        <p style={{ fontWeight: 700 }}>
          {t("citizen.settings.profile", {
            name: profile.name,
            age: profile.age,
            village: profile.village_name ?? profile.village_code,
          })}
        </p>
        <Toggle
          id="elder-mode"
          label={t("citizen.settings.elder")}
          checked={profile.elder_mode}
          onChange={toggleElder}
        />
      </Card>
      {error && <Notice tone="danger">{error}</Notice>}
      <div className="stack">
        <Button block variant="secondary" onClick={() => go("/qr")}>
          {t("citizen.settings.qr")}
        </Button>
        <Button block variant="secondary" onClick={() => go("/family")}>
          {t("citizen.settings.family")}
        </Button>
        <Button block variant="secondary" onClick={() => go("/prefs")}>
          {t("citizen.settings.prefs")}
        </Button>
        <Button block variant="secondary" onClick={() => go("/notifications")}>
          {t("citizen.settings.notifications")}
        </Button>
        <Button block variant="secondary" onClick={() => go("/consents")}>
          {t("citizen.settings.consents")}
        </Button>
        <Button block variant="ghost" onClick={onLogout}>
          {t("citizen.settings.logout")}
        </Button>
      </div>
      <p className="muted">{t("citizen.settings.about")}</p>
    </Screen>
  );
}
