import { Button, canScan, Card, Field, Notice, Scanner, Screen, useT } from "@healthapp/ui";
import { useState, type FormEvent } from "react";

import { api } from "../api";

export function Measure({ siteId }: { siteId: string }) {
  const { t } = useT();
  const [qr, setQr] = useState("");
  const [scan, setScan] = useState(false);
  const [sys, setSys] = useState("");
  const [dia, setDia] = useState("");
  const [weight, setWeight] = useState("");
  const [glucose, setGlucose] = useState("");
  const [message, setMessage] = useState<{ tone: "ok" | "danger"; text: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const scannable = canScan();

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setMessage(null);
    const items: Record<string, unknown> = {};
    if (sys && dia) items.bp = [Number(sys), Number(dia)];
    if (weight) items.weight = Number(weight);
    if (glucose) items.glucose = Number(glucose);
    try {
      const out = await api.post<{ person: string; written: number }>(`/sites/${siteId}/measurements`, {
        user_qr: qr.trim(),
        items,
      });
      setMessage({ tone: "ok", text: t("site_ui.measured", { person: out.person, n: out.written }) });
      setQr("");
      setSys("");
      setDia("");
      setWeight("");
      setGlucose("");
    } catch (err) {
      setMessage({ tone: "danger", text: (err as Error).message });
    } finally {
      setBusy(false);
    }
  };

  return (
    <Screen title={t("site_ui.measure_title")}>
      <Card>
        <form className="stack" onSubmit={submit}>
          <Field id="qr" label={t("site_ui.qr_label")} hint={t("site_ui.qr_hint")} value={qr}
            onChange={(e) => setQr(e.target.value)} required />
          {scannable ? (
            <Button variant="secondary" onClick={() => setScan((v) => !v)}>
              {t("site_ui.scan")}
            </Button>
          ) : (
            <small>{t("site_ui.scan_unsupported")}</small>
          )}
          {scan && (
            <Scanner
              onResult={(text) => {
                setQr(text);
                setScan(false);
              }}
              onClose={() => setScan(false)}
            />
          )}
          <div className="row">
            <Field id="sys" label={t("site_ui.bp_sys")} inputMode="numeric" value={sys} onChange={(e) => setSys(e.target.value)} />
            <Field id="dia" label={t("site_ui.bp_dia")} inputMode="numeric" value={dia} onChange={(e) => setDia(e.target.value)} />
          </div>
          <div className="row">
            <Field id="weight" label={t("site_ui.weight")} inputMode="decimal" value={weight} onChange={(e) => setWeight(e.target.value)} />
            <Field id="glucose" label={t("site_ui.glucose")} inputMode="numeric" value={glucose} onChange={(e) => setGlucose(e.target.value)} />
          </div>
          {message && <Notice tone={message.tone}>{message.text}</Notice>}
          <Button type="submit" block disabled={busy || !qr.trim()}>
            {t("site_ui.submit_measure")}
          </Button>
        </form>
      </Card>
    </Screen>
  );
}
