"""chart_render：以 SVG 畫折線圖／長條圖（不依賴繪圖套件，可在無網路沙箱產出）。"""

from __future__ import annotations

from html import escape
from typing import Any

PALETTE = ["#2563eb", "#dc2626", "#16a34a", "#d97706", "#7c3aed", "#0891b2", "#be185d"]
W, H = 760, 380
PAD = {"l": 64, "r": 160, "t": 48, "b": 56}


def _fmt(v: float) -> str:
    return f"{v:,.0f}" if abs(v) >= 100 else f"{v:.2f}"


def render_svg(spec: dict[str, Any]) -> str:
    """spec：{type: line|bar, title, x: [...], series: [{name, values: [...]}], y_label?}"""
    kind = spec.get("type", "line")
    xs = [str(x) for x in spec["x"]]
    series = spec["series"]
    values = [float(v) for s in series for v in s["values"] if v is not None]
    if not xs or not values:
        raise ValueError("chart_render 需要至少一個資料點")
    lo = min(0.0, min(values))
    hi = max(values) * 1.1 or 1.0
    pw, ph = W - PAD["l"] - PAD["r"], H - PAD["t"] - PAD["b"]

    def px(i: int) -> float:
        return PAD["l"] + (pw * (i + 0.5) / len(xs))

    def py(v: float) -> float:
        return PAD["t"] + ph * (1 - (v - lo) / (hi - lo))

    out = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" '
        f'font-family="Noto Sans TC, Microsoft JhengHei, sans-serif" font-size="12">',
        f'<rect width="{W}" height="{H}" fill="#fff"/>',
        f'<text x="{PAD["l"]}" y="28" font-size="16" font-weight="600">'
        f"{escape(str(spec.get('title', '')))}</text>",
    ]
    for t in range(5):
        v = lo + (hi - lo) * t / 4
        y = py(v)
        out.append(
            f'<line x1="{PAD["l"]}" x2="{W - PAD["r"]}" y1="{y:.1f}" y2="{y:.1f}" '
            f'stroke="#e5e7eb"/>'
        )
        out.append(
            f'<text x="{PAD["l"] - 6}" y="{y + 4:.1f}" text-anchor="end" fill="#6b7280">'
            f"{_fmt(v)}</text>"
        )
    step = max(1, len(xs) // 8)
    for i, x in enumerate(xs):
        if i % step == 0:
            out.append(
                f'<text x="{px(i):.1f}" y="{H - PAD["b"] + 18}" text-anchor="middle" '
                f'fill="#6b7280">{escape(x)}</text>'
            )
    if spec.get("y_label"):
        out.append(
            f'<text x="14" y="{PAD["t"] + ph / 2}" transform="rotate(-90 14 '
            f'{PAD["t"] + ph / 2})" text-anchor="middle" fill="#6b7280">'
            f"{escape(str(spec['y_label']))}</text>"
        )
    bar_w = pw / len(xs) / (len(series) + 1)
    for si, s in enumerate(series):
        color = PALETTE[si % len(PALETTE)]
        points = [(px(i), py(v)) for i, v in enumerate(s["values"]) if v is not None]
        if kind == "bar":
            for i, v in enumerate(s["values"]):
                if v is None:
                    continue
                x0 = px(i) - pw / len(xs) / 2 + bar_w * (si + 0.5)
                out.append(
                    f'<rect x="{x0:.1f}" y="{py(v):.1f}" width="{bar_w:.1f}" '
                    f'height="{py(lo) - py(v):.1f}" fill="{color}"/>'
                )
        else:
            path = " ".join(f"{x:.1f},{y:.1f}" for x, y in points)
            out.append(
                f'<polyline points="{path}" fill="none" stroke="{color}" stroke-width="2.5"/>'
            )
            out += [f'<circle cx="{x:.1f}" cy="{y:.1f}" r="3" fill="{color}"/>' for x, y in points]
        ly = PAD["t"] + 18 * si
        out.append(
            f'<rect x="{W - PAD["r"] + 16}" y="{ly}" width="12" height="12" fill="{color}"/>'
        )
        out.append(f'<text x="{W - PAD["r"] + 34}" y="{ly + 11}">{escape(str(s["name"]))}</text>')
    out.append("</svg>")
    return "".join(out)
