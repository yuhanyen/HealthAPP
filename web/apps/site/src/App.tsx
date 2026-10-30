import { ApiError, Button, LoginScreen, Spinner, TabBar, useHashRoute, useT, type MeOut } from "@healthapp/ui";
import { useCallback, useEffect, useState } from "react";

import { api } from "./api";
import { Care } from "./pages/Care";
import { Events } from "./pages/Events";
import { Measure } from "./pages/Measure";
import { Today } from "./pages/Today";

const TABS = [
  { id: "today", icon: "☀" },
  { id: "measure", icon: "♥" },
  { id: "events", icon: "📅" },
  { id: "care", icon: "☎" },
] as const;

export function App() {
  const { t } = useT();
  const [me, setMe] = useState<MeOut | null>(null);
  const [checking, setChecking] = useState(true);
  const [route, go] = useHashRoute("/today");

  const logout = useCallback(() => {
    api.setToken(null);
    setMe(null);
  }, []);

  useEffect(() => {
    document.title = t("site_ui.title");
    api.onUnauthorized(logout);
    if (!api.token()) {
      setChecking(false);
      return;
    }
    api
      .get<MeOut>("/auth/me")
      .then((who) => (who.role === "site_staff" && who.site_id ? setMe(who) : logout()))
      .catch(logout)
      .finally(() => setChecking(false));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  if (checking) return <Spinner />;
  if (!me?.site_id)
    return (
      <LoginScreen
        title={t("site_ui.login_title")}
        demo={t("site_ui.login_demo")}
        labels={{
          username: t("citizen.login.username"),
          password: t("citizen.login.password"),
          submit: t("citizen.login.submit"),
        }}
        onSubmit={async (username, password) => {
          try {
            const out = await api.login(username, password);
            if (out.role !== "site_staff") {
              api.setToken(null);
              return t("site_ui.not_staff");
            }
            setMe(await api.get<MeOut>("/auth/me"));
            return null;
          } catch (err) {
            return err instanceof ApiError ? err.detail : t("citizen.common.offline");
          }
        }}
      />
    );

  const section = route.split("/")[1] ?? "today";
  const siteId = me.site_id;
  return (
    <>
      <div className="row spread" style={{ padding: "8px 16px" }}>
        <small>{me.display_name}</small>
        <Button variant="ghost" onClick={logout}>
          {t("site_ui.logout")}
        </Button>
      </div>
      {section === "measure" ? (
        <Measure siteId={siteId} />
      ) : section === "events" ? (
        <Events siteId={siteId} />
      ) : section === "care" ? (
        <Care siteId={siteId} />
      ) : (
        <Today siteId={siteId} />
      )}
      <TabBar
        items={TABS.map((tab) => ({ ...tab, label: t(`site_ui.nav.${tab.id}`) }))}
        active={TABS.some((tab) => tab.id === section) ? section : "today"}
        onSelect={(id) => go(`/${id}`)}
      />
    </>
  );
}
