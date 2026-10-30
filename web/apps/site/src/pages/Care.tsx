import { Badge, Button, Card, Field, Notice, Screen, Spinner, Toggle, useAsync, useT } from "@healthapp/ui";
import { useState, type FormEvent } from "react";

import { api, type CareTask, type Decoded } from "../api";

const REASONS = ["distance", "equipment", "willingness", "health", "other"] as const;

function Report({ siteId, code, taskRef, onDone }: { siteId: string; code: string; taskRef: string; onDone: (msg: string) => void }) {
  const { t, list } = useT();
  const [reason, setReason] = useState<string>("distance");
  const [needs, setNeeds] = useState<string[]>([]);
  const [referral, setReferral] = useState(false);
  const [next, setNext] = useState("");
  const [note, setNote] = useState("");
  const [error, setError] = useState<string | null>(null);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    try {
      const out = await api.post<{ message: string }>(`/sites/${siteId}/care-tasks/${code}/report`, {
        task_ref: taskRef,
        reason_class: reason,
        needs,
        referral,
        next_followup: next || null,
        note: note || null,
      });
      onDone(out.message);
    } catch (err) {
      setError((err as Error).message);
    }
  };

  return (
    <form className="stack" onSubmit={submit}>
      <h3>{t("site_ui.report_title")}</h3>
      <div className="field">
        <label htmlFor={`reason-${code}`}>{t("site_ui.reason")}</label>
        <select id={`reason-${code}`} value={reason} onChange={(e) => setReason(e.target.value)}>
          {REASONS.map((r) => (
            <option key={r} value={r}>
              {t(`site_ui.reasons.${r}`)}
            </option>
          ))}
        </select>
      </div>
      <fieldset className="stack" style={{ border: 0, padding: 0 }}>
        <legend style={{ fontWeight: 700 }}>{t("site_ui.needs")}</legend>
        {list("site_ui.needs_options").map((option) => (
          <Toggle
            key={option}
            id={`need-${code}-${option}`}
            label={option}
            checked={needs.includes(option)}
            onChange={(on) => setNeeds((prev) => (on ? [...prev, option] : prev.filter((n) => n !== option)))}
          />
        ))}
      </fieldset>
      <Toggle id={`referral-${code}`} label={t("site_ui.referral")} checked={referral} onChange={setReferral} />
      <Field id={`next-${code}`} type="date" label={t("site_ui.next_followup")} value={next} onChange={(e) => setNext(e.target.value)} />
      <Field id={`note-${code}`} label={t("site_ui.note")} value={note} maxLength={500} onChange={(e) => setNote(e.target.value)} />
      {error && <Notice tone="danger">{error}</Notice>}
      <Button type="submit" block>
        {t("site_ui.submit_report")}
      </Button>
    </form>
  );
}

function TaskCard({ siteId, task, onChange }: { siteId: string; task: CareTask; onChange: () => void }) {
  const { t } = useT();
  const [taskRef, setTaskRef] = useState("");
  const [sheet, setSheet] = useState<Decoded | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState<string | null>(null);

  const decode = async (e: FormEvent) => {
    e.preventDefault();
    setError(null);
    try {
      setSheet(await api.post<Decoded>(`/sites/${siteId}/care-tasks/${task.code}/decode`, { task_ref: taskRef.trim() }));
    } catch (err) {
      setError((err as Error).message);
    }
  };

  return (
    <Card
      tone={task.status === "visited" ? "ok" : "primary"}
      title={
        <div className="row spread">
          <span>
            {task.code}｜{t(`agency_ui.task_type.${task.task_type}`)}
          </span>
          <span className="row">
            {task.decoded && <Badge tone="warn">{t("site_ui.decoded")}</Badge>}
            <Badge tone={task.status === "visited" ? "ok" : "neutral"}>{t(`site_ui.status.${task.status}`)}</Badge>
          </span>
        </div>
      }
    >
      <small>{task.rule_id}</small>
      {done && <Notice tone="ok">{done}</Notice>}
      {!sheet && !done && (
        <form className="stack" onSubmit={decode}>
          <Field id={`ref-${task.code}`} label={t("site_ui.task_ref")} hint={t("site_ui.task_ref_hint")} value={taskRef}
            onChange={(e) => setTaskRef(e.target.value)} required />
          <Button type="submit" disabled={!taskRef.trim()}>
            {t("site_ui.decode")}
          </Button>
        </form>
      )}
      {error && <Notice tone="danger">{error}</Notice>}
      {sheet && !done && (
        <div className="stack" style={{ marginTop: 12 }}>
          <strong>{t("site_ui.sheet_title", { code: sheet.code, task: t(`agency_ui.task_type.${sheet.task_type}`) })}</strong>
          <p>{t("site_ui.sheet_person", { name: sheet.name, phone: sheet.phone ?? "—" })}</p>
          <p>{t("site_ui.sheet_address", { address: sheet.address ?? "—" })}</p>
          <strong>{t("site_ui.sheet_guide")}</strong>
          <ol>
            {sheet.visit_guide.map((step) => (
              <li key={step}>{step}</li>
            ))}
          </ol>
          <small>{t("site_ui.sheet_note")}</small>
          <Report
            siteId={siteId}
            code={task.code}
            taskRef={taskRef.trim()}
            onDone={(msg) => {
              setDone(msg);
              setSheet(null);
              onChange();
            }}
          />
        </div>
      )}
    </Card>
  );
}

export function Care({ siteId }: { siteId: string }) {
  const { t } = useT();
  const tasks = useAsync(() => api.get<CareTask[]>(`/sites/${siteId}/care-tasks`), [siteId]);
  if (tasks.loading && !tasks.data) return <Spinner />;
  return (
    <Screen title={t("site_ui.care_title")}>
      <Notice tone="primary">{t("site_ui.care_intro")}</Notice>
      {tasks.data?.length === 0 && <p className="muted">{t("site_ui.care_none")}</p>}
      {tasks.data?.map((task) => (
        <TaskCard key={task.code} siteId={siteId} task={task} onChange={tasks.reload} />
      ))}
    </Screen>
  );
}
