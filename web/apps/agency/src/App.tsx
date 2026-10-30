import { ApiError, Button, LoginScreen, Spinner, useHashRoute, useT, type MeOut } from "@healthapp/ui";
import { useCallback, useEffect, useState } from "react";

import { api } from "./api";
import { Alerts } from "./pages/Alerts";
import { Briefing } from "./pages/Briefing";
import { Chat } from "./pages/Chat";
import { Dashboard } from "./pages/Dashboard";
import { Events } from "./pages/Events";

const PAGES = ["dashboard", "chat", "briefing", "alerts", "events"] as const;
const ROLES = new Set(["agency", "admin"]);

export function App() {
  const { t } = useT();
  const [me, setMe] = useState<MeOut | null>(null);
  const [checking, setChecking] = useState(true);
  const [route, go] = useHashRoute("/dashboard");

  const logout = useCallback(() => {
    api.setToken(null);
    setMe(null);
  }, []);

  useEffect(() => {
    document.title = t("agency_ui.title");
    api.onUnauthorized(logout);
    if (!api.token()) {
      setChecking(false);
      return;
    }
    api
      .get<MeOut>("/auth/me")
      .then((who) => (ROLES.has(who.role) ? setMe(who) : logout()))
      .catch(logout)
      .finally(() => setChecking(false));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  if (checking) return <Spinner />;
  if (!me)
    return (
      <LoginScreen
        title={t("agency_ui.login_title")}
        demo={t("agency_ui.login_demo")}
        labels={{
          username: t("citizen.login.username"),
          password: t("citizen.login.password"),
          submit: t("citizen.login.submit"),
        }}
        onSubmit={async (username, password) => {
          try {
            const out = await api.login(username, password);
            if (!ROLES.has(out.role)) {
              api.setToken(null);
              return t("agency_ui.not_agency");
            }
            setMe(await api.get<MeOut>("/auth/me"));
            return null;
          } catch (err) {
            return err instanceof ApiError ? err.detail : t("citizen.common.offline");
          }
        }}
      />
    );

  const page = route.split("/")[1] ?? "dashboard";
  return (
    <>
      <header className="topbar">
        <h1>{t("agency_ui.title")}</h1>
        <nav>
          {PAGES.map((p) => (
            <button key={p} type="button" aria-current={page === p ? "page" : undefined} onClick={() => go(`/${p}`)}>
              {t(`agency_ui.nav.${p}`)}
            </button>
          ))}
        </nav>
        <span>{me.display_name}</span>
        <Button variant="ghost" onClick={logout}>
          {t("agency_ui.logout")}
        </Button>
      </header>
      <main className="page">
        {page === "chat" ? (
          <Chat />
        ) : page === "briefing" ? (
          <Briefing />
        ) : page === "alerts" ? (
          <Alerts />
        ) : page === "events" ? (
          <Events />
        ) : (
          <Dashboard />
        )}
      </main>
    </>
  );
}
