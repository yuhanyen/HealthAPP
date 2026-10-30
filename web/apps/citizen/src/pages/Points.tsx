import { Badge, Button, Card, Modal, Notice, Screen, Spinner, useAsync, useT } from "@healthapp/ui";
import { useState } from "react";

import { api, day, when } from "../api";
import { Qr } from "../Qr";
import type { ActionResult, MerchantOffers, MerchantsOut, PendingAction, PointsOut } from "../types";

export function Points({ go }: { go: (path: string) => void }) {
  const { t } = useT();
  const points = useAsync(() => api.get<PointsOut>("/me/points"));
  const offers = useAsync(() => api.get<{ merchants: MerchantOffers[] }>("/me/offers"));
  const dining = useAsync(() => api.get<MerchantsOut>("/me/merchants/recommend?category=dining"));
  const [pending, setPending] = useState<ActionResult | null>(null);
  const [coupon, setCoupon] = useState<{ token: string; title: string } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  if (points.loading) return <Spinner label={t("citizen.common.loading")} />;
  const balance = points.data?.balance ?? 0;

  const ask = async (offerId: string) => {
    setBusy(true);
    setError(null);
    try {
      setPending(await api.post<ActionResult>("/me/redeem", { offer_id: offerId }));
    } catch (err) {
      setError(t("citizen.common.error", { reason: (err as Error).message }));
    } finally {
      setBusy(false);
    }
  };

  const confirm = async () => {
    if (!pending) return;
    setBusy(true);
    try {
      const action = await api.post<PendingAction>(`/me/actions/${pending.action_id}/confirm`);
      const result = action.result ?? {};
      if (action.status === "executed" && typeof result.qr_token === "string") {
        setCoupon({ token: result.qr_token, title: String(result.offer ?? "") });
      } else {
        setError(t("citizen.common.error", { reason: String(result.error ?? action.status) }));
      }
      points.reload();
      offers.reload();
    } catch (err) {
      setError(t("citizen.common.error", { reason: (err as Error).message }));
    } finally {
      setPending(null);
      setBusy(false);
    }
  };

  const cancel = async () => {
    if (pending) await api.post(`/me/actions/${pending.action_id}/cancel`).catch(() => undefined);
    setPending(null);
  };

  const coupons = (points.data?.redemptions ?? []).filter((r) => r.qr_token);

  return (
    <Screen title={t("citizen.points.title")}>
      <Card tone="primary">
        <small>{t("citizen.points.balance")}</small>
        <div className="big-number">
          {balance} <span style={{ fontSize: "var(--fs-md)" }}>{t("citizen.points.unit")}</span>
        </div>
        {points.data?.expiring.map((e) => (
          <p key={e.expires_on} className="muted">
            {t("citizen.points.expiring", { points: e.points, date: day(e.expires_on) })}
          </p>
        ))}
      </Card>
      {error && <Notice tone="danger">{error}</Notice>}

      {coupons.length > 0 && (
        <Card title={t("citizen.points.coupons")}>
          <div className="stack">
            {coupons.map((c) => (
              <Button key={c.id} variant="secondary" block onClick={() => setCoupon({ token: c.qr_token!, title: c.offer })}>
                {c.offer}
              </Button>
            ))}
          </div>
        </Card>
      )}

      {dining.data && (
        <Card
          title={t("citizen.points.recommend", { min: dining.data.min_rating.toFixed(1) })}
          actions={
            <Button variant="ghost" onClick={() => go("/prefs")}>
              {t("citizen.events.prefs")}
            </Button>
          }
        >
          {dining.data.merchants.length === 0 && <p className="muted">{t("citizen.points.recommend_none")}</p>}
          <ul className="list">
            {dining.data.merchants.map((m) => (
              <li key={m.merchant_id}>
                <div className="row spread">
                  <strong>{m.name}</strong>
                  <Badge tone="primary">
                    ★ {m.rating.toFixed(1)}
                    {m.rating_count ? `（${m.rating_count}）` : ""}
                  </Badge>
                </div>
                <small>
                  {m.category}
                  {m.distance_km != null && `・${m.distance_km.toFixed(1)} km`}
                  {m.tag_labels.length > 0 && `・${m.tag_labels.join("、")}`}
                </small>
                <ul style={{ margin: "4px 0 0", paddingLeft: 22 }}>
                  {m.reasons.slice(1, 4).map((r) => (
                    <li key={r}>{r}</li>
                  ))}
                </ul>
              </li>
            ))}
          </ul>
          {dining.data.excluded_text && <p className="muted">{dining.data.excluded_text}</p>}
        </Card>
      )}

      <Card title={t("citizen.points.offers")}>
        {offers.data && offers.data.merchants.length === 0 && <p className="muted">{t("citizen.points.none")}</p>}
        <ul className="list">
          {offers.data?.merchants.flatMap((m) =>
            m.offers.map((o) => (
              <li key={o.offer_id} className="row spread">
                <div>
                  <div style={{ fontWeight: 700 }}>{o.title}</div>
                  <small>
                    {m.name}・{m.distance_km} km
                  </small>
                </div>
                <div className="row">
                  <Badge tone="primary">{t("citizen.points.cost", { cost: o.points_cost })}</Badge>
                  <Button disabled={busy || o.points_cost > balance} onClick={() => ask(o.offer_id)}>
                    {o.points_cost > balance ? t("citizen.points.not_enough") : t("citizen.points.redeem")}
                  </Button>
                </div>
              </li>
            )),
          )}
        </ul>
      </Card>

      <Card title={t("citizen.points.ledger")}>
        <ul className="list">
          {points.data?.ledger.map((row, i) => (
            <li key={i} className="row spread">
              <span>
                {when(row.created_at)} {row.reason}
              </span>
              <strong style={{ color: row.delta > 0 ? "var(--ok)" : "var(--danger)" }}>
                {row.delta > 0 ? `+${row.delta}` : row.delta}
              </strong>
            </li>
          ))}
        </ul>
      </Card>

      <Modal
        open={pending !== null}
        title={t("citizen.points.confirm_title")}
        onClose={cancel}
        actions={
          <>
            <Button block disabled={busy} onClick={confirm}>
              {t("citizen.common.confirm")}
            </Button>
            <Button block variant="secondary" onClick={cancel}>
              {t("citizen.common.cancel")}
            </Button>
          </>
        }
      >
        <p>{pending?.summary}</p>
      </Modal>

      <Modal
        open={coupon !== null}
        title={coupon?.title ?? ""}
        onClose={() => setCoupon(null)}
        actions={
          <Button block onClick={() => setCoupon(null)}>
            {t("citizen.common.close")}
          </Button>
        }
      >
        {coupon && <Qr value={coupon.token} label={t("citizen.points.coupon_hint")} />}
        <p className="muted" style={{ textAlign: "center" }}>
          {t("citizen.points.coupon_hint")}
        </p>
      </Modal>
    </Screen>
  );
}
