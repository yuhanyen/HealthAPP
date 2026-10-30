// 時間序列圖（民眾端健康儀表板）。SVG 以實際像素寬度繪製（viewBox = 容器寬），
// 文字就是 CSS 的 --fs-min（18px，長者模式 22px），不會被縮小到低於最小字級。
// 規格：線 2px、資料點 r=4 加 2px 底色環、格線 1px 實線、兩條以上序列一定有圖例、
// 只標最後一點的數值；點按或滑過顯示該日數值；附「看數字」表格（不只靠顏色與圖形）。
import {
  useEffect,
  useRef,
  useState,
  type PointerEvent as ReactPointerEvent,
  type RefObject,
} from "react";

export interface ChartPoint {
  at: string; // YYYY-MM-DD 或 ISO 時間
  value: number;
}

export interface ChartSeries {
  key: string;
  label: string;
  color: string;
  points: ChartPoint[];
}

export interface RefLine {
  value: number;
  label: string;
}

// 上方留 32px：最高一條格線上的參考線標籤與數值標籤（18px 字）才不會被切掉
const PAD = { top: 32, right: 16, bottom: 36 };

function toTime(at: string): number {
  // 只有日期時當作當天中午，避免時區把點畫到前一天
  return at.length <= 10 ? new Date(`${at}T12:00:00`).getTime() : new Date(at).getTime();
}

function niceTicks(lo: number, hi: number, format: (v: number) => string, count = 4): number[] {
  const span = hi - lo || 1;
  let raw = span / count;
  // 刻度格式化後不可重複（例如 76.5 與 77 都顯示成 77）：重複就放大間距
  for (;;) {
    const mag = 10 ** Math.floor(Math.log10(raw));
    const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => s >= raw) ?? raw;
    const start = Math.floor(lo / step) * step;
    const ticks: number[] = [];
    for (let v = start; v <= hi + step * 0.01 || ticks.length < 2; v += step) ticks.push(Number(v.toFixed(6)));
    if (new Set(ticks.map(format)).size === ticks.length) return ticks;
    raw = step * 1.01;
  }
}

function useWidth<T extends HTMLElement>(): [RefObject<T | null>, number] {
  const ref = useRef<T>(null);
  const [width, setWidth] = useState(320);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const observer = new ResizeObserver((entries) => {
      const w = entries[0]?.contentRect.width;
      if (w) setWidth(Math.round(w));
    });
    observer.observe(el);
    return () => observer.disconnect();
  }, []);
  return [ref, width];
}

function md(at: string): string {
  const d = new Date(toTime(at));
  return `${d.getMonth() + 1}/${d.getDate()}`;
}

export function TimeChart({
  title,
  series,
  start,
  end,
  refs = [],
  format,
  height = 240,
  tableLabel,
  dateLabel,
}: {
  title: string;
  series: ChartSeries[];
  start: string;
  end: string;
  refs?: RefLine[];
  format: (v: number) => string;
  height?: number;
  tableLabel: string;
  dateLabel: string;
}) {
  const [wrap, width] = useWidth<HTMLDivElement>();
  const [hover, setHover] = useState<number | null>(null);
  const values = [...series.flatMap((s) => s.points.map((p) => p.value)), ...refs.map((r) => r.value)];
  if (!values.length) return null;

  const t0 = toTime(start) - 12 * 3600e3;
  const t1 = toTime(end) + 12 * 3600e3;
  const lo = Math.min(...values);
  const hi = Math.max(...values);
  const padY = (hi - lo || hi || 1) * 0.12;
  const ticks = niceTicks(Math.max(0, lo - padY), hi + padY, format);
  const yMin = ticks[0]!;
  const yMax = ticks[ticks.length - 1]!;
  const left = Math.max(...ticks.map((v) => format(v).length)) * 11 + 14;
  const x = (time: number) => left + ((width - left - PAD.right) * (time - t0)) / (t1 - t0 || 1);
  const y = (v: number) => PAD.top + (height - PAD.top - PAD.bottom) * (1 - (v - yMin) / (yMax - yMin || 1));

  // 所有點的時間（給點按／滑過找最近的一天）
  const times = [...new Set(series.flatMap((s) => s.points.map((p) => toTime(p.at))))].sort((a, b) => a - b);
  const onMove = (e: ReactPointerEvent<SVGRectElement>) => {
    const box = e.currentTarget.getBoundingClientRect();
    const px = e.clientX - box.left + left;
    let best: number | null = null;
    for (const time of times) if (best === null || Math.abs(x(time) - px) < Math.abs(x(best) - px)) best = time;
    setHover(best);
  };
  const hoverRows =
    hover === null
      ? []
      : series
          .map((s) => ({ s, p: s.points.find((p) => toTime(p.at) === hover) }))
          .filter((r): r is { s: ChartSeries; p: ChartPoint } => r.p !== undefined);
  const dense = (s: ChartSeries) => s.points.length > 31;

  return (
    <figure className="tchart" ref={wrap}>
      {series.length > 1 && (
        <figcaption className="tchart-legend">
          {series.map((s) => (
            <span key={s.key} className="tchart-key">
              <span className="tchart-swatch" style={{ background: s.color }} aria-hidden="true" />
              {s.label}
            </span>
          ))}
        </figcaption>
      )}
      <div className="tchart-plot">
        <svg width={width} height={height} viewBox={`0 0 ${width} ${height}`} role="img" aria-label={title}>
          {ticks.map((v) => (
            <g key={v}>
              <line className="tchart-grid" x1={left} x2={width - PAD.right} y1={y(v)} y2={y(v)} />
              <text className="tchart-axis" x={left - 8} y={y(v) + 6} textAnchor="end">
                {format(v)}
              </text>
            </g>
          ))}
          {[start, end].map((at, i) => (
            <text key={at} className="tchart-axis" x={i === 0 ? left : width - PAD.right} y={height - 8}
              textAnchor={i === 0 ? "start" : "end"}>
              {md(at)}
            </text>
          ))}
          {refs.map((r) => (
            <g key={r.label}>
              <line className="tchart-ref" x1={left} x2={width - PAD.right} y1={y(r.value)} y2={y(r.value)} />
              {/* 參考線標籤放左側，避開右側最新一點的數值標籤 */}
              <text className="tchart-ref-label" x={left + 6} y={y(r.value) - 6} textAnchor="start">
                {r.label}
              </text>
            </g>
          ))}
          {series.map((s) => {
            const pts = s.points.map((p) => [x(toTime(p.at)), y(p.value)] as const);
            const last = s.points[s.points.length - 1];
            const lastPt = pts[pts.length - 1];
            return (
              <g key={s.key}>
                {pts.length > 1 && (
                  <polyline
                    points={pts.map(([px, py]) => `${px.toFixed(1)},${py.toFixed(1)}`).join(" ")}
                    fill="none"
                    stroke={s.color}
                    strokeWidth={2}
                    strokeLinejoin="round"
                    strokeLinecap="round"
                  />
                )}
                {(dense(s) ? pts.slice(-1) : pts).map(([px, py], i) => (
                  <circle key={i} cx={px} cy={py} r={4} fill={s.color} className="tchart-dot" />
                ))}
                {last && lastPt && (
                  <text className="tchart-value" x={Math.min(lastPt[0], width - PAD.right)} y={lastPt[1] - 10}
                    textAnchor="end">
                    {format(last.value)}
                  </text>
                )}
              </g>
            );
          })}
          {hover !== null && (
            <line className="tchart-cross" x1={x(hover)} x2={x(hover)} y1={PAD.top} y2={height - PAD.bottom} />
          )}
          <rect
            x={left}
            y={0}
            width={Math.max(0, width - left - PAD.right)}
            height={height}
            fill="transparent"
            onPointerMove={onMove}
            onPointerDown={onMove}
            onPointerLeave={() => setHover(null)}
          />
        </svg>
        {hover !== null && hoverRows.length > 0 && (
          <div className="tchart-tip" role="status" style={{ left: Math.min(Math.max(x(hover) - 70, 0), width - 160) }}>
            <strong>{md(hoverRows[0]!.p.at)}</strong>
            {hoverRows.map(({ s, p }) => (
              <span key={s.key}>
                {series.length > 1 && <span className="tchart-swatch" style={{ background: s.color }} aria-hidden="true" />}
                {series.length > 1 ? `${s.label} ` : ""}
                {format(p.value)}
              </span>
            ))}
          </div>
        )}
      </div>
      <details className="tchart-table">
        <summary>{tableLabel}</summary>
        <table>
          <thead>
            <tr>
              <th>{dateLabel}</th>
              {series.map((s) => (
                <th key={s.key}>{s.label}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {times
              .slice()
              .reverse()
              .map((time) => (
                <tr key={time}>
                  <td>{md(new Date(time).toISOString())}</td>
                  {series.map((s) => {
                    const p = s.points.find((q) => toTime(q.at) === time);
                    return <td key={s.key}>{p ? format(p.value) : "—"}</td>;
                  })}
                </tr>
              ))}
          </tbody>
        </table>
      </details>
    </figure>
  );
}
