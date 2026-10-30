const COLORS = ["#1f3a5f", "#b8430c", "#1e7a3c", "#7c3aed", "#0891b2", "#9a5b00"];
const W = 640;
const H = 260;
const PAD = { l: 52, r: 110, t: 16, b: 34 };

export interface Line {
  name: string;
  points: { week: string; value: number | null }[];
}

/** 簡易折線圖（SVG）：缺值（遮蔽）處斷開，不補值。 */
export function TrendChart({ lines, format }: { lines: Line[]; format: (v: number) => string }) {
  const weeks = [...new Set(lines.flatMap((l) => l.points.map((p) => p.week)))].sort();
  const values = lines.flatMap((l) => l.points.map((p) => p.value)).filter((v): v is number => v !== null);
  if (!weeks.length || !values.length) return null;
  const lo = Math.min(0, ...values);
  const hi = Math.max(...values) * 1.1 || 1;
  const x = (i: number) => PAD.l + ((W - PAD.l - PAD.r) * (i + 0.5)) / weeks.length;
  const y = (v: number) => PAD.t + (H - PAD.t - PAD.b) * (1 - (v - lo) / (hi - lo));
  const ticks = [0, 0.25, 0.5, 0.75, 1].map((f) => lo + (hi - lo) * f);
  return (
    <div className="chart">
      <svg viewBox={`0 0 ${W} ${H}`} role="img">
        {ticks.map((v) => (
          <g key={v}>
            <line x1={PAD.l} x2={W - PAD.r} y1={y(v)} y2={y(v)} stroke="#e5e9ee" />
            <text x={PAD.l - 6} y={y(v) + 4} textAnchor="end" fontSize="11" fill="#5b6672">
              {format(v)}
            </text>
          </g>
        ))}
        {weeks.map((w, i) =>
          i % Math.max(1, Math.ceil(weeks.length / 8)) === 0 ? (
            <text key={w} x={x(i)} y={H - 12} textAnchor="middle" fontSize="11" fill="#5b6672">
              {`${Number(w.slice(5, 7))}/${Number(w.slice(8, 10))}`}
            </text>
          ) : null,
        )}
        {lines.map((line, li) => {
          const color = COLORS[li % COLORS.length];
          const byWeek = new Map(line.points.map((p) => [p.week, p.value]));
          const segments: string[][] = [[]];
          weeks.forEach((w, i) => {
            const v = byWeek.get(w);
            if (v === null || v === undefined) segments.push([]);
            else segments[segments.length - 1]!.push(`${x(i).toFixed(1)},${y(v).toFixed(1)}`);
          });
          return (
            <g key={line.name}>
              {segments
                .filter((s) => s.length)
                .map((s, si) => (
                  <polyline key={si} points={s.join(" ")} fill="none" stroke={color} strokeWidth={li === 0 ? 3 : 2} />
                ))}
              <rect x={W - PAD.r + 12} y={PAD.t + li * 18} width="10" height="10" fill={color} />
              <text x={W - PAD.r + 28} y={PAD.t + li * 18 + 9} fontSize="12" fill="#1c2430">
                {line.name}
              </text>
            </g>
          );
        })}
      </svg>
    </div>
  );
}
