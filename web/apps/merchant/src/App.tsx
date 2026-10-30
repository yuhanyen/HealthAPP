import { ApiError, Button, LoginScreen, Spinner, TabBar, useHashRoute, useT, type MeOut } from "@healthapp/ui";
import { useCallback, useEffect, useState } from "react";

import { api, MonthReport, Offers, Verify } from "./pages";

const TABS = [
  { id: "verify", icon: "▣" },
  { id: "offers", icon: "★" },
  { id: "report", icon: "▤" },
] as const;

export function App() {
  const { t } = useT();
  const [me, setMe] = useState<MeOut | null>(null);
  const [checking, setChecking] = useState(true);
  const [route, go] = useHashRoute("/verify");

  const logout = useCallback(() => {
    api.setToken(null);
    setMe(null);
  }, []);

  useEffect(() => {
    document.title = t("merchant_ui.title");
    api.onUnauthorized(logout);
    if (!api.token()) {
      setChecking(false);
      return;
    }
    api
      .get<MeOut>("/auth/me")
      .then((who) => (who.role === "merchant" && who.merchant_id ? setMe(who) : logout()))
      .catch(logout)
      .finally(() => setChecking(false));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  if (checking) return <Spinner />;
  if (!me?.merchant_id)
    return (
      <LoginScreen
        title={t("merchant_ui.login_title")}
        demo={t("merchant_ui.login_demo")}
        labels={{
          username: t("citizen.login.username"),
          password: t("citizen.login.password"),
          submit: t("citizen.login.submit"),
        }}
        onSubmit={async (username, password) => {
          try {
            const out = await api.login(username, password);
            if (out.role !== "merchant") {
              api.setToken(null);
              return t("merchant_ui.not_merchant");
            }
            setMe(await api.get<MeOut>("/auth/me"));
            return null;
          } catch (err) {
            return err instanceof ApiError ? err.detail : t("citizen.common.offline");
          }
        }}
      />
    );

  const section = route.split("/")[1] ?? "verify";
  const id = me.merchant_id;
  return (
    <>
      <div className="row spread" style={{ padding: "8px 16px" }}>
        <small>{me.display_name}</small>
        <Button variant="ghost" onClick={logout}>
          {t("merchant_ui.logout")}
        </Button>
      </div>
      {section === "offers" ? <Offers merchantId={id} /> : section === "report" ? <MonthReport merchantId={id} /> : <Verify merchantId={id} />}
      <TabBar
        items={TABS.map((tab) => ({ ...tab, label: t(`merchant_ui.nav.${tab.id}`) }))}
        active={TABS.some((tab) => tab.id === section) ? section : "verify"}
        onSelect={(next) => go(`/${next}`)}
      />
    </>
  );
}
