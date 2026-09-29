"""Dashboard 6 panel đọc trực tiếp data/logs.jsonl theo contract config/dashboard.yaml.

    python scripts/dashboard.py --serve                 # http://127.0.0.1:8050, tự refresh
    python scripts/dashboard.py --out dashboard.html    # xuất snapshot HTML tĩnh
    python scripts/dashboard.py --until 2026-09-29T08:30:00Z --out incident.html

Mỗi lần mở trang, dashboard đọc lại log và tính trên cửa sổ `time_range_minutes`
(60 phút) kết thúc tại `--until` hoặc thời điểm hiện tại.
"""
from __future__ import annotations

import argparse
import html
import json
import math
import sys
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from statistics import mean

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.cli import configure_utf8_stdio
from app.metrics import percentile

CONFIG_PATH = REPO_ROOT / "config" / "dashboard.yaml"
ALERTS_PATH = REPO_ROOT / "config" / "alert_rules.yaml"
LOG_PATH = REPO_ROOT / "data" / "logs.jsonl"


def load_contract(path: Path = CONFIG_PATH) -> dict:
    dashboard = yaml.safe_load(path.read_text(encoding="utf-8"))["dashboard"]
    dashboard["panels_by_id"] = {panel["id"]: panel for panel in dashboard["panels"]}
    dashboard["alerts_by_panel"] = load_alert_lines()
    return dashboard


def load_alert_lines(path: Path = ALERTS_PATH) -> dict[str, list[dict]]:
    """Ngưỡng alert có khai báo `dashboard:` trong alert_rules.yaml, nhóm theo panel."""
    if not path.exists():
        return {}
    by_panel: dict[str, list[dict]] = {}
    for alert in (yaml.safe_load(path.read_text(encoding="utf-8")) or {}).get("alerts", []):
        line = alert.get("dashboard")
        if isinstance(line, dict) and line.get("panel"):
            by_panel.setdefault(line["panel"], []).append(
                {**line, "name": alert["name"], "duration": alert.get("duration", "")}
            )
    return by_panel


def parse_ts(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def load_records(path: Path = LOG_PATH) -> list[dict]:
    if not path.exists():
        return []
    records = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            rec = json.loads(line)
            rec["_ts"] = parse_ts(rec["ts"])
        except (json.JSONDecodeError, KeyError, ValueError):
            continue
        records.append(rec)
    return records


@dataclass
class Bucket:
    received: int = 0
    failed: int = 0
    latency: list[int] = field(default_factory=list)
    ttft: list[int] = field(default_factory=list)
    cost: float = 0.0
    tokens_in: int = 0
    tokens_out: int = 0
    quality: list[float] = field(default_factory=list)
    tool_total: int = 0
    tool_ok: int = 0


def compute(records: list[dict], until: datetime, minutes: int) -> dict:
    start = until - timedelta(minutes=minutes)
    buckets = [Bucket() for _ in range(minutes)]
    errors: Counter[str] = Counter()
    window = [r for r in records if start <= r["_ts"] < until]
    for rec in window:
        b = buckets[min(minutes - 1, int((rec["_ts"] - start).total_seconds() // 60))]
        event = rec.get("event")
        if event == "request_received":
            b.received += 1
        elif event == "request_failed":
            b.failed += 1
            errors[rec.get("error_type") or "unknown"] += 1
        elif event == "response_sent":
            b.latency.append(rec["latency_ms"])
            b.ttft.append(rec["ttft_ms"])
            b.cost += rec.get("cost_usd", 0.0)
            b.tokens_in += rec.get("tokens_in", 0)
            b.tokens_out += rec.get("tokens_out", 0)
            b.quality.append(rec.get("quality_score", 0.0))
        if rec.get("tool_success") is not None:
            b.tool_total += 1
            b.tool_ok += rec["tool_success"] is True

    all_latency = [v for b in buckets for v in b.latency]
    all_ttft = [v for b in buckets for v in b.ttft]
    all_quality = [v for b in buckets for v in b.quality]
    received = sum(b.received for b in buckets)
    failed = sum(b.failed for b in buckets)
    tool_total = sum(b.tool_total for b in buckets)
    active_minutes = sum(1 for b in buckets if b.received)
    return {
        "start": start,
        "until": until,
        "minutes": minutes,
        "buckets": buckets,
        "records_in_window": len(window),
        "summary": {
            "p50": percentile(all_latency, 50),
            "p95": percentile(all_latency, 95),
            "p99": percentile(all_latency, 99),
            "ttft_p95": percentile(all_ttft, 95),
            "count": received,
            "rate_per_minute": received / active_minutes if active_minutes else 0.0,
            "peak_rate_per_minute": max((b.received for b in buckets), default=0),
            "error_rate_pct": failed / received * 100 if received else 0.0,
            "count_by_value": dict(errors),
            "tool_success_rate_pct": (
                sum(b.tool_ok for b in buckets) / tool_total * 100 if tool_total else 100.0
            ),
            "total_cost": sum(b.cost for b in buckets),
            "tokens_in": sum(b.tokens_in for b in buckets),
            "tokens_out": sum(b.tokens_out for b in buckets),
            "quality_mean": mean(all_quality) if all_quality else 0.0,
        },
    }


def breached(value: float, threshold: dict) -> bool:
    if threshold["operator"] == "lte":
        return value > threshold["value"]
    return value < threshold["value"]


# ---------------------------------------------------------------- rendering

W, H = 560, 200
PAD_L, PAD_R, PAD_T, PAD_B = 52, 16, 14, 26


def _fmt(value: float, unit: str) -> str:
    if unit == "usd":
        return f"${value:.4f}"
    if unit == "percent":
        return f"{value:.1f}%"
    if unit == "score_0_to_1":
        return f"{value:.2f}"
    if abs(value) >= 1000:
        return f"{value:,.0f}"
    return f"{value:.0f}" if float(value).is_integer() else f"{value:.1f}"


def _nice_ceiling(value: float) -> float:
    """Làm tròn lên 1/2/2.5/5 x 10^n để vạch trục y là số tròn."""
    if value <= 0:
        return 1.0
    exp = 10 ** math.floor(math.log10(value))
    for step in (1, 2, 2.5, 5, 10):
        if value <= step * exp:
            return step * exp
    return 10 * exp


def _x(idx: int, minutes: int) -> float:
    return PAD_L + (W - PAD_L - PAD_R) * (idx + 0.5) / minutes


def _y(value: float, y_max: float) -> float:
    return PAD_T + (H - PAD_T - PAD_B) * (1 - value / y_max if y_max else 1)


def svg_chart(
    ctx: dict,
    series: list[tuple[str, str, list[tuple[int, float]]]],
    unit: str,
    threshold: dict | None,
    kind: str = "line",
    y_floor: float = 0.0,
    alerts: list[dict] = (),
) -> str:
    minutes = ctx["minutes"]
    data_max = max([v for _, _, pts in series for _, v in pts] + [y_floor, 0])
    th_value = threshold["value"] if threshold else None
    # Chỉ đưa threshold vào thang đo khi nó gần dữ liệu; nếu quá xa thì ghi chú để
    # không ép toàn bộ dữ liệu thành một đường phẳng dưới đáy.
    th_in_scale = th_value is not None and (data_max == 0 or th_value <= data_max * 4)
    # Ngưỡng alert trùng threshold của contract thì không vẽ lại lần nữa.
    alerts = [a for a in alerts if a["value"] != th_value]
    alert_max = max([a["value"] for a in alerts] + [0])
    y_max = _nice_ceiling(max(data_max, th_value if th_in_scale else 0, alert_max) * 1.1)
    if y_floor and data_max <= y_floor:
        y_max = y_floor  # thang cố định cho đại lượng có trần tự nhiên (100%, score 1.0)
    parts = [f'<svg viewBox="0 0 {W} {H}" role="img" class="chart">']
    for frac in (0, 0.25, 0.5, 0.75, 1):
        y = _y(y_max * frac, y_max)
        parts.append(f'<line class="grid" x1="{PAD_L}" x2="{W - PAD_R}" y1="{y:.1f}" y2="{y:.1f}"/>')
        parts.append(f'<text class="tick" x="{PAD_L - 6}" y="{y + 4:.1f}" text-anchor="end">{_fmt(y_max * frac, unit)}</text>')
    for m in range(0, minutes + 1, 15):
        x = PAD_L + (W - PAD_L - PAD_R) * m / minutes
        label = (ctx["start"] + timedelta(minutes=m)).strftime("%H:%M")
        parts.append(f'<text class="tick" x="{x:.1f}" y="{H - 8}" text-anchor="middle">{label}</text>')
    parts.append(f'<line class="axis" x1="{PAD_L}" x2="{W - PAD_R}" y1="{_y(0, y_max):.1f}" y2="{_y(0, y_max):.1f}"/>')

    bar_w = max(2.0, (W - PAD_L - PAD_R) / minutes - 2)
    for s_idx, (name, color, points) in enumerate(series):
        if kind == "bar":
            offset = (s_idx - (len(series) - 1) / 2) * bar_w / len(series)
            for idx, value in points:
                if value <= 0:
                    continue
                x = _x(idx, minutes) - bar_w / (2 * len(series)) + offset
                y = _y(value, y_max)
                h = _y(0, y_max) - y
                ts = (ctx["start"] + timedelta(minutes=idx)).strftime("%H:%M")
                parts.append(
                    f'<rect x="{x:.1f}" y="{y:.1f}" width="{bar_w / len(series):.1f}" height="{h:.1f}" rx="2" '
                    f'fill="{color}"><title>{ts} UTC · {html.escape(name)}: {_fmt(value, unit)}</title></rect>'
                )
            continue
        coords = [(_x(idx, minutes), _y(value, y_max), idx, value) for idx, value in points]
        if len(coords) > 1:
            path = " ".join(f"{'M' if i == 0 else 'L'}{x:.1f},{y:.1f}" for i, (x, y, _, _) in enumerate(coords))
            parts.append(f'<path d="{path}" fill="none" stroke="{color}" stroke-width="2" stroke-linejoin="round"/>')
        for x, y, idx, value in coords:
            ts = (ctx["start"] + timedelta(minutes=idx)).strftime("%H:%M")
            parts.append(
                f'<circle cx="{x:.1f}" cy="{y:.1f}" r="4" fill="{color}" class="dot">'
                f'<title>{ts} UTC · {html.escape(name)}: {_fmt(value, unit)}</title></circle>'
            )

    if th_value is not None:
        label = f"threshold {threshold['operator']} {_fmt(th_value, unit)} ({threshold['aggregation']})"
        if th_in_scale:
            y = _y(th_value, y_max)
            parts.append(f'<line class="threshold" x1="{PAD_L}" x2="{W - PAD_R}" y1="{y:.1f}" y2="{y:.1f}"/>')
            parts.append(f'<text class="th-label" x="{PAD_L + 4}" y="{y - 4:.1f}">{label}</text>')
        else:
            parts.append(f'<text class="th-label" x="{PAD_L + 4}" y="{PAD_T + 2}">{label} · ngoài thang đo</text>')
    for alert in alerts:
        y = _y(alert["value"], y_max)
        label = f"alert {alert['name']}: {alert['aggregation']} > {_fmt(alert['value'], unit)} trong {alert['duration']}"
        parts.append(f'<line class="alert-line" x1="{PAD_L}" x2="{W - PAD_R}" y1="{y:.1f}" y2="{y:.1f}"/>')
        parts.append(f'<text class="alert-label" x="{PAD_L + 4}" y="{y - 4:.1f}">{html.escape(label)}</text>')
    parts.append("</svg>")
    return "".join(parts)


def legend(series: list[tuple[str, str]]) -> str:
    if len(series) < 2:
        return ""
    items = "".join(
        f'<span class="key"><i style="background:{color}"></i>{html.escape(name)}</span>' for name, color in series
    )
    return f'<div class="legend">{items}</div>'


def tile(label: str, value: str) -> str:
    return f'<div class="stat"><span>{html.escape(label)}</span><b>{html.escape(value)}</b></div>'


def status_badge(is_breached: bool, fired: list[str] = ()) -> str:
    if fired and not is_breached:
        return f'<span class="badge warn">▲ Điều kiện alert: {html.escape(", ".join(fired))}</span>'
    if is_breached:
        return '<span class="badge bad">▲ Vượt ngưỡng</span>'
    return '<span class="badge ok">✓ Trong ngưỡng</span>'


def panel_html(
    panel: dict, badge_breached: bool, stats: str, chart: str, legend_html: str, fired: list[str] = ()
) -> str:
    return (
        f'<section class="panel"><header><h2>{html.escape(panel["title"])}</h2>'
        f'{status_badge(badge_breached, fired)}</header>'
        f'<p class="meta">unit: <code>{panel["unit"]}</code> · events: {", ".join(panel["events"])}</p>'
        f'<div class="stats">{stats}</div>{legend_html}{chart}</section>'
    )


def per_minute(ctx: dict, fn) -> list[tuple[int, float]]:
    points = []
    for idx, bucket in enumerate(ctx["buckets"]):
        value = fn(bucket)
        if value is not None:
            points.append((idx, value))
    return points


def cumulative(points: list[tuple[int, float]]) -> list[tuple[int, float]]:
    total, out = 0.0, []
    for idx, value in points:
        total += value
        out.append((idx, total))
    return out


C1, C2, C3, C4 = "var(--s1)", "var(--s2)", "var(--s3)", "var(--s4)"


def render(contract: dict, ctx: dict, refresh: bool) -> str:
    p = contract["panels_by_id"]
    s = ctx["summary"]
    has = lambda b: b.latency  # noqa: E731
    alerts = contract.get("alerts_by_panel", {})

    def fired(panel_id: str) -> list[str]:
        return [a["name"] for a in alerts.get(panel_id, []) if breached(s[a["aggregation"]], a)]

    lat_series = [
        ("P50", C1, per_minute(ctx, lambda b: percentile(b.latency, 50) if has(b) else None)),
        ("P95", C2, per_minute(ctx, lambda b: percentile(b.latency, 95) if has(b) else None)),
        ("P99", C3, per_minute(ctx, lambda b: percentile(b.latency, 99) if has(b) else None)),
        ("TTFT P95", C4, per_minute(ctx, lambda b: percentile(b.ttft, 95) if b.ttft else None)),
    ]
    latency = panel_html(
        p["latency"],
        breached(s["p95"], p["latency"]["threshold"]),
        tile("P50", f"{s['p50']:.0f} ms") + tile("P95", f"{s['p95']:.0f} ms")
        + tile("P99", f"{s['p99']:.0f} ms") + tile("TTFT P95", f"{s['ttft_p95']:.0f} ms"),
        svg_chart(ctx, lat_series, "ms", p["latency"]["threshold"], alerts=alerts.get("latency", [])),
        legend([(n, c) for n, c, _ in lat_series]),
        fired("latency"),
    )

    traffic_series = [("requests/phút", C1, per_minute(ctx, lambda b: b.received or None))]
    traffic = panel_html(
        p["traffic"],
        breached(s["rate_per_minute"], p["traffic"]["threshold"]),
        tile("Tổng request", f"{s['count']}") + tile("Rate TB (phút có traffic)", f"{s['rate_per_minute']:.1f}/phút")
        + tile("Peak", f"{s['peak_rate_per_minute']}/phút"),
        svg_chart(ctx, traffic_series, "requests_per_minute", p["traffic"]["threshold"], kind="bar"),
        "",
    )

    err_series = [
        ("Error rate %", C2, per_minute(ctx, lambda b: b.failed / b.received * 100 if b.received else None)),
        ("Retrieval success %", C1, per_minute(ctx, lambda b: b.tool_ok / b.tool_total * 100 if b.tool_total else None)),
    ]
    breakdown = ", ".join(f"{k}={v}" for k, v in s["count_by_value"].items()) or "không có lỗi"
    errors = panel_html(
        p["errors"],
        breached(s["error_rate_pct"], p["errors"]["threshold"]),
        tile("Error rate", f"{s['error_rate_pct']:.1f}%") + tile("Retrieval success", f"{s['tool_success_rate_pct']:.1f}%")
        + tile("Theo error_type", breakdown),
        svg_chart(ctx, err_series, "percent", p["errors"]["threshold"], y_floor=100),
        legend([(n, c) for n, c, _ in err_series]),
    )

    cost_points = per_minute(ctx, lambda b: b.cost if b.latency else None)
    cost_series = [("Chi phí tích lũy", C1, cumulative(cost_points))]
    cost = panel_html(
        p["cost"],
        breached(s["total_cost"], p["cost"]["threshold"]),
        tile("Tổng 60 phút", f"${s['total_cost']:.4f}")
        + tile("Cao nhất/phút", f"${max((v for _, v in cost_points), default=0):.4f}"),
        svg_chart(ctx, cost_series, "usd", p["cost"]["threshold"]),
        "",
    )

    tok_series = [
        ("tokens_in (tích lũy)", C1, cumulative(per_minute(ctx, lambda b: b.tokens_in if b.latency else None))),
        ("tokens_out (tích lũy)", C2, cumulative(per_minute(ctx, lambda b: b.tokens_out if b.latency else None))),
    ]
    tokens = panel_html(
        p["tokens"],
        breached(max(s["tokens_in"], s["tokens_out"]), p["tokens"]["threshold"]),
        tile("tokens_in", f"{s['tokens_in']:,}") + tile("tokens_out", f"{s['tokens_out']:,}"),
        svg_chart(ctx, tok_series, "tokens", p["tokens"]["threshold"]),
        legend([(n, c) for n, c, _ in tok_series]),
    )

    q_series = [("Quality mean", C1, per_minute(ctx, lambda b: mean(b.quality) if b.quality else None))]
    quality = panel_html(
        p["quality"],
        breached(s["quality_mean"], p["quality"]["threshold"]),
        tile("Mean", f"{s['quality_mean']:.2f}"),
        svg_chart(ctx, q_series, "score_0_to_1", p["quality"]["threshold"], y_floor=1.0),
        "",
    )

    window = f"{ctx['start']:%Y-%m-%d %H:%M} → {ctx['until']:%H:%M} UTC"
    meta_refresh = f'<meta http-equiv="refresh" content="{contract["refresh_seconds"]}">' if refresh else ""
    return TEMPLATE.format(
        refresh=meta_refresh,
        title=html.escape(contract["title"]),
        window=window,
        minutes=contract["time_range_minutes"],
        refresh_s=contract["refresh_seconds"],
        n=ctx["records_in_window"],
        generated=datetime.now(timezone.utc).strftime("%H:%M:%S UTC"),
        panels=latency + traffic + errors + cost + tokens + quality,
    )


TEMPLATE = """<!doctype html>
<html lang="vi"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">{refresh}
<title>{title}</title>
<style>
:root {{ color-scheme: light; --bg:#f9f9f7; --surface:#fcfcfb; --ink:#0b0b0b; --ink2:#52514e; --muted:#898781;
  --grid:#e1e0d9; --axis:#c3c2b7; --border:rgba(11,11,11,.10); --crit:#d03b3b; --good:#006300; --serious:#ec835a; --serious-ink:#b3501f;
  --s1:#2a78d6; --s2:#eb6834; --s3:#1baf7a; --s4:#4a3aa7; }}
@media (prefers-color-scheme: dark) {{ :root {{ color-scheme: dark; --bg:#0d0d0d; --surface:#1a1a19; --ink:#fff;
  --ink2:#c3c2b7; --grid:#2c2c2a; --axis:#383835; --border:rgba(255,255,255,.10); --good:#0ca30c; --serious-ink:#ec835a;
  --s1:#3987e5; --s2:#d95926; --s3:#199e70; --s4:#9085e9; }} }}
body {{ margin:0; padding:20px 16px; background:var(--bg); color:var(--ink);
  font:14px system-ui,-apple-system,"Segoe UI",sans-serif; }}
.top {{ display:flex; flex-wrap:wrap; gap:8px 24px; align-items:baseline; margin:0 auto 16px; max-width:1200px; }}
h1 {{ font-size:20px; margin:0; }} .top span {{ color:var(--ink2); }}
.grid6 {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(min(100%,520px),1fr)); gap:16px; max-width:1200px; margin:auto; }}
.panel {{ background:var(--surface); border:1px solid var(--border); border-radius:10px; padding:14px 16px; }}
.panel header {{ display:flex; justify-content:space-between; gap:8px; align-items:center; }}
h2 {{ font-size:15px; margin:0; }}
.meta {{ margin:4px 0 8px; color:var(--muted); font-size:12px; }}
.stats {{ display:flex; flex-wrap:wrap; gap:6px 20px; margin-bottom:6px; }}
.stat span {{ display:block; color:var(--ink2); font-size:12px; }} .stat b {{ font-size:18px; font-variant-numeric:tabular-nums; }}
.badge {{ font-size:12px; font-weight:600; white-space:nowrap; }} .badge.ok {{ color:var(--good); }} .badge.bad {{ color:var(--crit); }}
.legend {{ display:flex; flex-wrap:wrap; gap:4px 14px; font-size:12px; color:var(--ink2); }}
.key i {{ display:inline-block; width:10px; height:10px; border-radius:2px; margin-right:5px; vertical-align:-1px; }}
.chart {{ width:100%; height:auto; display:block; }}
.chart .grid {{ stroke:var(--grid); stroke-width:1; }} .chart .axis {{ stroke:var(--axis); stroke-width:1; }}
.chart .tick {{ fill:var(--muted); font-size:10px; font-variant-numeric:tabular-nums; }}
.chart .threshold {{ stroke:var(--crit); stroke-width:1.5; stroke-dasharray:6 4; }}
.chart .alert-line {{ stroke:var(--serious); stroke-width:1.5; stroke-dasharray:2 3; }}
.chart .alert-label {{ fill:var(--serious-ink); font-size:10px; font-weight:600; paint-order:stroke; stroke:var(--surface); stroke-width:3px; }}
.badge.warn {{ color:var(--serious-ink); }}
.chart .th-label {{ fill:var(--crit); font-size:10px; font-weight:600; paint-order:stroke; stroke:var(--surface); stroke-width:3px; }}
.chart .dot {{ stroke:var(--surface); stroke-width:2; }}
</style></head><body>
<div class="top"><h1>{title}</h1><span>Time range: <b>{minutes} phút</b> ({window})</span>
<span>Refresh {refresh_s}s · nguồn <code>data/logs.jsonl</code> · {n} log records · cập nhật {generated}</span></div>
<main class="grid6">{panels}</main>
</body></html>"""


def build_html(until: datetime | None = None, refresh: bool = False, log_path: Path = LOG_PATH) -> str:
    contract = load_contract()
    until = until or datetime.now(timezone.utc)
    ctx = compute(load_records(log_path), until, contract["time_range_minutes"])
    return render(contract, ctx, refresh)


def main() -> int:
    configure_utf8_stdio()
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--serve", action="store_true")
    parser.add_argument("--port", type=int, default=8050)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--until", help="Kết thúc cửa sổ 60 phút (ISO 8601, UTC); mặc định là bây giờ")
    args = parser.parse_args()
    until = parse_ts(args.until) if args.until else None

    if args.out:
        args.out.write_text(build_html(until), encoding="utf-8")
        print(f"Đã ghi {args.out}")
    if args.serve:
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802
                body = build_html(until, refresh=True).encode("utf-8")
                self.send_response(200)
                self.send_header("content-type", "text/html; charset=utf-8")
                self.send_header("content-length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                return

        print(f"Dashboard: http://127.0.0.1:{args.port}")
        ThreadingHTTPServer(("127.0.0.1", args.port), Handler).serve_forever()
    if not args.out and not args.serve:
        parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
