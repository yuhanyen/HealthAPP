import { applyElderMode, Spinner, TabBar, useHashRoute, useT, type MeOut } from "@healthapp/ui";
import { useCallback, useEffect, useState } from "react";

import { api } from "./api";
import { Chat } from "./pages/Chat";
import { Consents } from "./pages/Consents";
import { Events } from "./pages/Events";
import { Family } from "./pages/Family";
import { Health } from "./pages/Health";
import { Login } from "./pages/Login";
import { More } from "./pages/More";
import { Notifications } from "./pages/Notifications";
import { Onboarding } from "./pages/Onboarding";
import { Points } from "./pages/Points";
import { Prefs } from "./pages/Prefs";
import { QrPage } from "./pages/QrPage";
import { Today } from "./pages/Today";
import type { Consent, Profile } from "./types";

// 六個分頁（守護圈移到「更多」，見 docs/DECISIONS.md）
const TABS = [
  { id: "today", icon: "☀" },
  { id: "health", icon: "📈" },
  { id: "events", icon: "📅" },
  { id: "chat", icon: "💬" },
  { id: "points", icon: "★" },
  { id: "more", icon: "≡" },
] as const;
// 不在分頁列上的頁面，亮起所屬分頁
const PARENT: Record<string, string> = { report: "health", prefs: "events" };

export function App() {
  const { t } = useT();
  const [me, setMe] = useState<MeOut | null>(null);
  const [profile, setProfile] = useState<Profile | null>(null);
  const [onboarding, setOnboarding] = useState(false);
  const [checking, setChecking] = useState(true);
  const [route, go] = useHashRoute("/today");

  const logout = useCallback(() => {
    api.setToken(null);
    setMe(null);
    setProfile(null);
  }, []);

  const load = useCallback(async () => {
    setChecking(true);
    try {
      if (!api.token()) return;
      const who = await api.get<MeOut>("/auth/me");
      if (who.role !== "citizen") {
        logout();
        return;
      }
      const [mine, consents] = await Promise.all([
        api.get<Profile>("/me/profile"),
        api.get<Consent[]>("/consents"),
      ]);
      applyElderMode(mine.elder_mode);
      setProfile(mine);
      setOnboarding(consents.length === 0);
      setMe(who);
    } catch {
      logout();
    } finally {
      setChecking(false);
    }
  }, [logout]);

  useEffect(() => {
    document.title = t("citizen.title");
    api.onUnauthorized(logout);
    void load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  if (checking) return <Spinner label={t("citizen.common.loading")} />;
  if (!me || !profile) return <Login onDone={load} />;
  if (onboarding) return <Onboarding onDone={() => setOnboarding(false)} />;

  const section = route.split("/")[1] ?? "today";
  const page = (() => {
    switch (section) {
      case "health":
        return <Health go={go} profile={profile} />;
      case "report":
        return <Health go={go} profile={profile} report />;
      case "events":
        return <Events go={go} />;
      case "prefs":
        return <Prefs go={go} />;
      case "chat":
        return <Chat />;
      case "points":
        return <Points go={go} />;
      case "family":
        return <Family />;
      case "more":
        return <More profile={profile} onProfile={setProfile} onLogout={logout} go={go} />;
      case "consents":
        return <Consents />;
      case "notifications":
        return <Notifications go={go} />;
      case "qr":
        return <QrPage />;
      default:
        return <Today go={go} />;
    }
  })();
  const active = TABS.some((tab) => tab.id === section) ? section : (PARENT[section] ?? "more");

  return (
    <>
      {page}
      <TabBar
        items={TABS.map((tab) => ({ ...tab, label: t(`citizen.nav.${tab.id}`) }))}
        active={active}
        onSelect={(id) => go(`/${id}`)}
      />
    </>
  );
}
