import { ApiError, Button, Card, Field, Notice, Screen, useT } from "@healthapp/ui";
import { useState, type FormEvent } from "react";

import { api } from "../api";

export function Login({ onDone }: { onDone: () => void }) {
  const { t } = useT();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const out = await api.login(username.trim(), password);
      if (out.role !== "citizen") {
        api.setToken(null);
        setError(t("citizen.login.not_citizen"));
        return;
      }
      onDone();
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : t("citizen.common.offline"));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Screen title={t("citizen.login.title")}>
      <Card>
        <form className="stack" onSubmit={submit}>
          <Field
            id="username"
            label={t("citizen.login.username")}
            autoComplete="username"
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            required
          />
          <Field
            id="password"
            label={t("citizen.login.password")}
            type="password"
            autoComplete="current-password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            required
          />
          {error && <Notice tone="danger">{error}</Notice>}
          <Button type="submit" block disabled={busy}>
            {t("citizen.login.submit")}
          </Button>
        </form>
      </Card>
      <Notice tone="primary">{t("citizen.login.demo")}</Notice>
    </Screen>
  );
}
