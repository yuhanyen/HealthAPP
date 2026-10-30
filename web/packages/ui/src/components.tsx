// 共用元件（四個 App 共用）。文字一律由呼叫端從 i18n 傳入。
import {
  useEffect,
  useRef,
  useState,
  type ButtonHTMLAttributes,
  type FormEvent,
  type InputHTMLAttributes,
  type ReactNode,
} from "react";

export type Tone = "ok" | "warn" | "alert" | "danger" | "primary" | "neutral";

export function Button({
  variant = "primary",
  block,
  className = "",
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: "primary" | "secondary" | "danger" | "ghost";
  block?: boolean;
}) {
  return (
    <button
      type="button"
      className={`btn btn-${variant}${block ? " btn-block" : ""} ${className}`}
      {...props}
    />
  );
}

export function Card({
  title,
  tone,
  children,
  actions,
}: {
  title?: ReactNode;
  tone?: Tone;
  children?: ReactNode;
  actions?: ReactNode;
}) {
  return (
    <section className={`card${tone ? ` tone-${tone}` : ""}`}>
      {title && <div className="card-title">{title}</div>}
      {children}
      {actions && <div className="row" style={{ marginTop: 12 }}>{actions}</div>}
    </section>
  );
}

export function Badge({ tone = "neutral", children }: { tone?: Tone; children: ReactNode }) {
  return <span className={`badge tone-${tone}`}>{children}</span>;
}

export function Screen({
  title,
  actions,
  children,
}: {
  title: ReactNode;
  actions?: ReactNode;
  children: ReactNode;
}) {
  return (
    <main className="screen">
      <header className="screen-header">
        <h1>{title}</h1>
        {actions}
      </header>
      <div className="stack">{children}</div>
    </main>
  );
}

export function Field({
  label,
  hint,
  id,
  ...props
}: InputHTMLAttributes<HTMLInputElement> & { label: string; hint?: string; id: string }) {
  return (
    <div className="field">
      <label htmlFor={id}>{label}</label>
      <input id={id} {...props} />
      {hint && <small>{hint}</small>}
    </div>
  );
}

export function Toggle({
  label,
  checked,
  onChange,
  id,
}: {
  label: string;
  checked: boolean;
  onChange: (value: boolean) => void;
  id: string;
}) {
  return (
    <label className="toggle" htmlFor={id}>
      <span>{label}</span>
      <input
        id={id}
        type="checkbox"
        role="switch"
        checked={checked}
        onChange={(e) => onChange(e.target.checked)}
      />
    </label>
  );
}

export function Notice({ tone = "neutral", children }: { tone?: Tone; children: ReactNode }) {
  return (
    <div className={`notice tone-${tone}`} role={tone === "danger" ? "alert" : "status"}>
      {children}
    </div>
  );
}

export function Spinner({ label }: { label?: string }) {
  return (
    <div role="status" aria-live="polite">
      <div className="spinner" />
      {label && <p className="muted" style={{ textAlign: "center" }}>{label}</p>}
    </div>
  );
}

export function Modal({
  open,
  title,
  children,
  actions,
  onClose,
}: {
  open: boolean;
  title: ReactNode;
  children?: ReactNode;
  actions: ReactNode;
  onClose: () => void;
}) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    ref.current?.focus();
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);
  if (!open) return null;
  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div
        className="modal stack"
        role="dialog"
        aria-modal="true"
        tabIndex={-1}
        ref={ref}
        onClick={(e) => e.stopPropagation()}
      >
        <h2>{title}</h2>
        {children}
        <div className="stack">{actions}</div>
      </div>
    </div>
  );
}

/** 共用登入畫面（四個端各自傳入文案與角色檢查）。 */
export function LoginScreen({
  title,
  demo,
  labels,
  onSubmit,
}: {
  title: string;
  demo: string;
  labels: { username: string; password: string; submit: string };
  onSubmit: (username: string, password: string) => Promise<string | null>;
}) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(await onSubmit(username.trim(), password));
    setBusy(false);
  };
  return (
    <Screen title={title}>
      <Card>
        <form className="stack" onSubmit={submit}>
          <Field id="username" label={labels.username} autoComplete="username" value={username}
            onChange={(e) => setUsername(e.target.value)} required />
          <Field id="password" label={labels.password} type="password" autoComplete="current-password"
            value={password} onChange={(e) => setPassword(e.target.value)} required />
          {error && <Notice tone="danger">{error}</Notice>}
          <Button type="submit" block disabled={busy}>
            {labels.submit}
          </Button>
        </form>
      </Card>
      <Notice tone="primary">{demo}</Notice>
    </Screen>
  );
}

export interface TabItem {
  id: string;
  label: string;
  icon: string;
}

export function TabBar({
  items,
  active,
  onSelect,
}: {
  items: TabItem[];
  active: string;
  onSelect: (id: string) => void;
}) {
  return (
    <nav className="tabbar">
      <div className="tabbar-inner">
        {items.map((item) => (
          <button
            key={item.id}
            type="button"
            className="tab"
            aria-current={item.id === active ? "page" : undefined}
            onClick={() => onSelect(item.id)}
          >
            <span className="tab-icon" aria-hidden="true">
              {item.icon}
            </span>
            {item.label}
          </button>
        ))}
      </div>
    </nav>
  );
}
