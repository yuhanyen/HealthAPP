import {
  Badge,
  Button,
  canScan,
  Card,
  Field,
  Notice,
  Scanner,
  Screen,
  Spinner,
  createApi,
  useAsync,
  useT,
} from "@healthapp/ui";
import { useState, type FormEvent } from "react";

export const api = createApi({ storageKey: "healthapp.merchant.token" });

interface Offer {
  id: string;
  title: string;
  points_cost: number;
  stock: number;
  valid_from: string;
  valid_to: string;
}

interface Report {
  month: string;
  redemptions: number;
  points: number;
  verified: number;
  outstanding: number;
  offers: { title: string; count: number; points: number }[];
  age_bands: { age_band: string; n: number | null; _masked: boolean }[];
  category_median: number | null;
  category_merchants: number;
  note: string;
}

export function Verify({ merchantId }: { merchantId: string }) {
  const { t } = useT();
  const [token, setToken] = useState("");
  const [scan, setScan] = useState(false);
  const [message, setMessage] = useState<{ tone: "ok" | "danger"; text: string } | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setMessage(null);
    try {
      const out = await api.post<{ offer: string; points: number }>(`/merchants/${merchantId}/verify`, {
        qr_token: token.trim(),
      });
      setMessage({ tone: "ok", text: t("merchant_ui.verified", { offer: out.offer, points: out.points }) });
      setToken("");
    } catch (err) {
      setMessage({ tone: "danger", text: (err as Error).message });
    } finally {
      setBusy(false);
    }
  };

  return (
    <Screen title={t("merchant_ui.verify_title")}>
      <Notice tone="primary">{t("merchant_ui.verify_hint")}</Notice>
      <Card>
        <form className="stack" onSubmit={submit}>
          <Field id="token" label={t("merchant_ui.token")} value={token} onChange={(e) => setToken(e.target.value)} required />
          {canScan() ? (
            <Button variant="secondary" onClick={() => setScan((v) => !v)}>
              {t("merchant_ui.scan")}
            </Button>
          ) : (
            <small>{t("merchant_ui.scan_unsupported")}</small>
          )}
          {scan && (
            <Scanner
              onResult={(text) => {
                setToken(text);
                setScan(false);
              }}
              onClose={() => setScan(false)}
            />
          )}
          {message && <Notice tone={message.tone}>{message.text}</Notice>}
          <Button type="submit" block disabled={busy || !token.trim()}>
            {t("merchant_ui.verify_submit")}
          </Button>
        </form>
      </Card>
    </Screen>
  );
}

export function Offers({ merchantId }: { merchantId: string }) {
  const { t } = useT();
  const offers = useAsync(() => api.get<Offer[]>(`/merchants/${merchantId}/offers`), [merchantId]);
  const today = new Date().toISOString().slice(0, 10);
  const [form, setForm] = useState({ offer_id: "", title: "", points_cost: "30", stock: "20", valid_from: today, valid_to: today });
  const [message, setMessage] = useState<{ tone: "ok" | "danger"; text: string } | null>(null);
  const set = (key: keyof typeof form) => (e: { target: { value: string } }) => setForm((f) => ({ ...f, [key]: e.target.value }));

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    try {
      await api.post(`/merchants/${merchantId}/offers`, {
        ...form,
        points_cost: Number(form.points_cost),
        stock: Number(form.stock),
      });
      setMessage({ tone: "ok", text: t("merchant_ui.saved") });
      offers.reload();
    } catch (err) {
      setMessage({ tone: "danger", text: (err as Error).message });
    }
  };

  return (
    <Screen title={t("merchant_ui.offers_title")}>
      <Card>
        {offers.loading && <Spinner />}
        <ul className="list">
          {offers.data?.map((o) => (
            <li key={o.id}>
              <button
                type="button"
                className="btn btn-ghost"
                style={{ padding: 0, minHeight: 0 }}
                onClick={() =>
                  setForm({
                    offer_id: o.id,
                    title: o.title,
                    points_cost: String(o.points_cost),
                    stock: String(o.stock),
                    valid_from: o.valid_from,
                    valid_to: o.valid_to,
                  })
                }
              >
                {t("merchant_ui.offer_line", {
                  title: o.title,
                  cost: o.points_cost,
                  stock: o.stock,
                  from: o.valid_from,
                  to: o.valid_to,
                })}
              </button>
            </li>
          ))}
        </ul>
      </Card>
      <Card>
        <form className="stack" onSubmit={submit}>
          <Field id="offer_id" label={t("merchant_ui.offer_id")} value={form.offer_id} onChange={set("offer_id")} required />
          <Field id="title" label={t("merchant_ui.offer_title")} value={form.title} onChange={set("title")} required />
          <div className="row">
            <Field id="cost" type="number" min={1} label={t("merchant_ui.cost")} value={form.points_cost} onChange={set("points_cost")} />
            <Field id="stock" type="number" min={0} label={t("merchant_ui.stock")} value={form.stock} onChange={set("stock")} />
          </div>
          <div className="row">
            <Field id="from" type="date" label={t("merchant_ui.valid_from")} value={form.valid_from} onChange={set("valid_from")} />
            <Field id="to" type="date" label={t("merchant_ui.valid_to")} value={form.valid_to} onChange={set("valid_to")} />
          </div>
          {message && <Notice tone={message.tone}>{message.text}</Notice>}
          <Button type="submit" block>
            {t("merchant_ui.save")}
          </Button>
        </form>
      </Card>
    </Screen>
  );
}

export function MonthReport({ merchantId }: { merchantId: string }) {
  const { t } = useT();
  const [month, setMonth] = useState(new Date().toISOString().slice(0, 7));
  const report = useAsync(() => api.get<Report>(`/merchants/${merchantId}/report?month=${month}`), [merchantId, month]);
  const r = report.data;
  return (
    <Screen title={t("merchant_ui.report_title")}>
      <Field id="month" type="month" label={t("merchant_ui.month")} value={month} onChange={(e) => setMonth(e.target.value)} />
      {report.loading && <Spinner />}
      {r && r.redemptions === 0 && <p className="muted">{t("merchant_ui.none")}</p>}
      {r && (
        <>
          <Card>
            <div className="row spread">
              <span>{t("merchant_ui.redemptions")}</span>
              <strong className="big-number">{r.redemptions}</strong>
            </div>
            <div className="row">
              <Badge tone="primary">
                {t("merchant_ui.points")} {r.points}
              </Badge>
              <Badge tone="ok">
                {t("merchant_ui.verified_n")} {r.verified}
              </Badge>
              <Badge tone="warn">
                {t("merchant_ui.outstanding")} {r.outstanding}
              </Badge>
            </div>
            {r.category_median !== null && (
              <p className="muted">
                {t("merchant_ui.category_median", { n: r.category_merchants })}：{r.category_median}
              </p>
            )}
          </Card>
          <Card title={t("merchant_ui.by_offer")}>
            <ul className="list">
              {r.offers.map((o) => (
                <li key={o.title}>{t("merchant_ui.offer_row", { title: o.title, count: o.count, points: o.points })}</li>
              ))}
            </ul>
          </Card>
          <Card title={t("merchant_ui.age_bands")}>
            <ul className="list">
              {r.age_bands.map((a) => (
                <li key={a.age_band}>
                  {a._masked ? `${a.age_band}：${t("merchant_ui.masked")}` : t("merchant_ui.age_row", { band: a.age_band, n: a.n ?? 0 })}
                </li>
              ))}
            </ul>
            <small>{r.note}</small>
          </Card>
        </>
      )}
    </Screen>
  );
}
