"""
Report generator: renders the HTML dashboard from analysis results.
"""
import json
import re
from datetime import datetime
from pathlib import Path

import plotly.graph_objects as go
import plotly.utils
from jinja2 import Environment, FileSystemLoader

ROOT = Path(__file__).parent.parent
TEMPLATES_DIR = ROOT / "templates"
DASHBOARD_DIR = ROOT / "dashboard"

# General palette for allocation / net-worth per-account lines
PALETTE = ["#4f46e5", "#7c3aed", "#0ea5e9", "#10b981", "#f59e0b", "#ef4444", "#6366f1", "#14b8a6"]

# Maximally-distinct palette for projection per-account lines (no two look alike)
PROJECTION_PALETTE = [
    "#e63946",   # vivid red
    "#2196f3",   # strong blue
    "#4caf50",   # green
    "#ff9800",   # orange
    "#9c27b0",   # purple
    "#00bcd4",   # cyan
    "#795548",   # brown
    "#e91e63",   # pink
]

_ACRONYMS = {"TFSA", "RRSP", "RPP", "RESP", "FHSA", "RRIF"}


def _readable_label(account_id: str) -> str:
    """Convert account_id to a human-readable chart label.
    'H947126K9CAD_TFSA'           → 'WS TFSA'
    'WZ0969XK4CAD_TFSA_PORTFOLIO' → 'WS TFSA Portfolio'
    'HF4253745CAD_RRSP'           → 'WS RRSP'
    'manulife_RPP'                → 'Manulife RPP'
    """
    ws_match = re.match(r'^[A-Z0-9]+CAD_(.+)$', account_id)
    if ws_match:
        words = [w if w in _ACRONYMS else w.title() for w in ws_match.group(1).split("_")]
        return "WS " + " ".join(words)
    parts = account_id.split("_", 1)
    if len(parts) == 2:
        return f"{parts[0].title()} {parts[1]}"
    return account_id.replace("_", " ").title()


def _fig_json(fig: go.Figure) -> str:
    return json.dumps(fig, cls=plotly.utils.PlotlyJSONEncoder)


def _base_layout(**kwargs) -> dict:
    base = dict(
        plot_bgcolor="white",
        paper_bgcolor="white",
        font=dict(family="-apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif", size=12, color="#1e293b"),
        margin=dict(l=60, r=20, t=50, b=40),
        hovermode="x unified",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0, bgcolor="rgba(0,0,0,0)"),
    )
    base.update(kwargs)
    return base


# ─── Net Worth Over Time ──────────────────────────────────────────────────────

def build_net_worth_chart(series: list[dict]) -> str:
    if not series:
        return "{}"

    dates = [r["date"] for r in series]
    totals = [r.get("total", 0) or 0 for r in series]
    acct_keys = [k for k in (series[0].keys() if series else []) if k not in ("date", "total")]

    fig = go.Figure()

    for i, key in enumerate(acct_keys):
        vals = [r.get(key) for r in series]
        label = _readable_label(key)
        color = PALETTE[i % len(PALETTE)]
        fig.add_trace(go.Scatter(
            x=dates, y=vals,
            name=label,
            mode="lines+markers",
            connectgaps=True,
            line=dict(color=color, width=1.5, dash="dot"),
            marker=dict(size=5, color=color),
            opacity=0.7,
            hovertemplate=f"<b>{label}</b>: $%{{y:,.0f}}<extra></extra>",
        ))

    fig.add_trace(go.Scatter(
        x=dates, y=totals,
        name="Total Portfolio",
        mode="lines",
        fill="tozeroy",
        fillcolor="rgba(79,70,229,0.08)",
        line=dict(color="#4f46e5", width=3),
        hovertemplate="<b>Total</b>: $%{y:,.0f}<extra></extra>",
    ))

    fig.update_layout(
        **_base_layout(
            xaxis=dict(title="", showgrid=False, tickformat="%b %Y"),
            yaxis=dict(
                title="Portfolio Value (CAD)",
                tickprefix="$",
                tickformat=",",
                gridcolor="#f1f5f9",
                zeroline=False,
            ),
        )
    )
    return _fig_json(fig)


# ─── Asset Allocation ─────────────────────────────────────────────────────────

def build_allocation_charts(allocation: dict) -> dict:
    charts = {}
    for key, label in [("by_asset_class", "By Asset Class"),
                        ("by_geography", "By Geography"),
                        ("by_account", "By Account")]:
        data = allocation.get(key, {})
        if not data:
            charts[key] = "{}"
            continue
        labels = list(data.keys())
        values = list(data.values())
        fig = go.Figure(go.Pie(
            labels=labels,
            values=values,
            hole=0.45,
            textinfo="label+percent",
            hovertemplate="%{label}: $%{value:,.0f} (%{percent})<extra></extra>",
            marker=dict(colors=PALETTE[:len(labels)]),
            textfont=dict(size=11),
        ))
        fig.update_layout(
            title=dict(text=label, font=dict(size=13)),
            plot_bgcolor="white",
            paper_bgcolor="white",
            margin=dict(l=10, r=10, t=50, b=10),
            showlegend=False,
        )
        charts[key] = _fig_json(fig)
    return charts


# ─── Contribution Room ────────────────────────────────────────────────────────

def _contribution_bar_chart(title: str, accounts: list[dict]) -> str:
    if not accounts:
        return "{}"

    labels = [a["label"] for a in accounts]
    used = [a["used"] for a in accounts]
    available = [a["available"] for a in accounts]

    fig = go.Figure()
    fig.add_trace(go.Bar(
        name="Contributed",
        y=labels, x=used,
        orientation="h",
        marker_color="#4f46e5",
        text=[f"${v:,.0f}" for v in used],
        textposition="inside",
        insidetextanchor="middle",
        hovertemplate="<b>%{y}</b> Contributed: $%{x:,.0f}<extra></extra>",
    ))
    fig.add_trace(go.Bar(
        name="Remaining Room",
        y=labels, x=available,
        orientation="h",
        marker_color="#e2e8f0",
        text=[f"${v:,.0f}" for v in available],
        textposition="inside",
        insidetextanchor="middle",
        textfont=dict(color="#64748b"),
        hovertemplate="<b>%{y}</b> Remaining: $%{x:,.0f}<extra></extra>",
    ))

    fig.update_layout(
        barmode="stack",
        title=dict(text=title, font=dict(size=13)),
        plot_bgcolor="white",
        paper_bgcolor="white",
        margin=dict(l=10, r=20, t=50, b=10),
        xaxis=dict(tickprefix="$", tickformat=",", gridcolor="#f1f5f9", title="Amount (CAD)"),
        yaxis=dict(showgrid=False),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0, bgcolor="rgba(0,0,0,0)"),
        height=max(180, 80 + len(accounts) * 70),
    )
    return _fig_json(fig)


def build_contribution_room_charts(contrib_room: dict) -> dict:
    tfsa_cur = contrib_room.get("tfsa", {}) or {}
    rrsp_cur = contrib_room.get("rrsp", {}) or {}
    prior = contrib_room.get("prior_year", {}) or {}

    current_accounts = []
    if tfsa_cur and not tfsa_cur.get("error"):
        used = tfsa_cur.get("ytd_contributions", 0) or 0
        rem = tfsa_cur.get("remaining_this_year", 0) or 0
        current_accounts.append({"label": "TFSA", "used": used, "available": rem})
    if rrsp_cur:
        used = rrsp_cur.get("ytd_contributions", 0) or 0
        rem = rrsp_cur.get("remaining_this_year", 0) or 0
        current_accounts.append({"label": "RRSP", "used": used, "available": rem})
    for b in (contrib_room.get("resp") or []):
        used = b.get("ytd_contributions", 0) or 0
        rem = b.get("lifetime_room_remaining", 0) or 0
        current_accounts.append({"label": f"RESP ({b['name']})", "used": used, "available": rem})

    prior_accounts = []
    prior_year = prior.get("year", "")
    py_tfsa = prior.get("tfsa", {}) or {}
    if py_tfsa.get("room_available"):
        used = py_tfsa.get("contributions", 0) or 0
        rem = py_tfsa.get("remaining", 0) or 0
        prior_accounts.append({"label": "TFSA", "used": used, "available": rem})
    py_rrsp = prior.get("rrsp", {}) or {}
    if py_rrsp.get("room_available"):
        used = py_rrsp.get("contributions", 0) or 0
        rem = py_rrsp.get("remaining", 0) or 0
        prior_accounts.append({"label": "RRSP", "used": used, "available": rem})

    return {
        "current": _contribution_bar_chart(f"{prior.get('year', 2025) + 1} Contribution Room", current_accounts),
        "prior": _contribution_bar_chart(f"{prior_year} Contribution Room", prior_accounts),
    }


# ─── Cost Basis vs Current Value ──────────────────────────────────────────────

def build_cost_basis_chart(cost_basis: list[dict]) -> str:
    """
    Horizontal stacked bar: cost basis (blue) + gain (green) or loss (red) = current value.
    Accounts without book_value (Manulife) show current value only.
    Excludes the 'Total' row — that's shown as a summary card.
    """
    rows = [r for r in cost_basis if r["account_id"] != "total" and r["current_value"]]
    if not rows:
        return "{}"

    labels = [r["label"] for r in rows]
    book_vals = [r["book_value"] or r["current_value"] for r in rows]
    gains = [max(r["gain"] or 0, 0) for r in rows]
    losses = [abs(min(r["gain"] or 0, 0)) for r in rows]
    has_book = [r["book_value"] is not None for r in rows]

    fig = go.Figure()

    fig.add_trace(go.Bar(
        name="Amount Invested",
        y=labels, x=book_vals,
        orientation="h",
        marker_color="#4f46e5",
        text=[f"${v:,.0f}" if has_book[i] else "N/A" for i, v in enumerate(book_vals)],
        textposition="inside",
        insidetextanchor="middle",
        hovertemplate="<b>%{y}</b> Invested: $%{x:,.0f}<extra></extra>",
    ))
    if any(g > 0 for g in gains):
        fig.add_trace(go.Bar(
            name="Gain",
            y=labels, x=gains,
            orientation="h",
            marker_color="#10b981",
            text=[f"+${v:,.0f}" if v > 0 else "" for v in gains],
            textposition="inside",
            insidetextanchor="middle",
            hovertemplate="<b>%{y}</b> Gain: $%{x:,.0f}<extra></extra>",
        ))
    if any(l > 0 for l in losses):
        fig.add_trace(go.Bar(
            name="Loss",
            y=labels, x=losses,
            orientation="h",
            marker_color="#ef4444",
            text=[f"-${v:,.0f}" if v > 0 else "" for v in losses],
            textposition="inside",
            insidetextanchor="middle",
            hovertemplate="<b>%{y}</b> Loss: $%{x:,.0f}<extra></extra>",
        ))

    fig.update_layout(
        barmode="stack",
        plot_bgcolor="white",
        paper_bgcolor="white",
        margin=dict(l=10, r=20, t=20, b=10),
        xaxis=dict(tickprefix="$", tickformat=",", gridcolor="#f1f5f9", title="Amount (CAD)"),
        yaxis=dict(showgrid=False),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0, bgcolor="rgba(0,0,0,0)"),
        height=max(220, 80 + len(rows) * 60),
    )
    return _fig_json(fig)


# ─── Retirement Projection ────────────────────────────────────────────────────

def build_projection_chart(projection: dict) -> str:
    total_series = projection.get("projection_series", [])
    per_account = projection.get("per_account_projections", [])
    if not total_series:
        return "{}"

    years = [p["year"] for p in total_series]
    fig = go.Figure()

    for i, acct in enumerate(per_account):
        color = PROJECTION_PALETTE[i % len(PROJECTION_PALETTE)]
        acct_years = [p["year"] for p in acct["series"]]
        acct_vals = [p["value"] for p in acct["series"]]
        rate_pct = f"{acct['growth_rate'] * 100:.1f}%"
        fig.add_trace(go.Scatter(
            x=acct_years, y=acct_vals,
            name=f"{acct['label']} ({rate_pct}/yr)",
            mode="lines",
            line=dict(color=color, width=2, dash="dot"),
            opacity=0.9,
            hovertemplate=f"<b>{acct['label']}</b>: $%{{y:,.0f}}<extra></extra>",
        ))

    avg_rate_pct = f"{projection.get('average_growth_rate', 0) * 100:.1f}%"
    fig.add_trace(go.Scatter(
        x=years, y=[p["value"] for p in total_series],
        name=f"Total ({avg_rate_pct} blended)",
        mode="lines",
        fill="tozeroy",
        fillcolor="rgba(79,70,229,0.07)",
        line=dict(color="#4f46e5", width=3),
        hovertemplate="<b>Total</b>: $%{y:,.0f}<extra></extra>",
    ))

    retirement_year = years[-1] if years else None
    if retirement_year:
        fig.add_vline(
            x=retirement_year, line_dash="dot", line_color="#ef4444", line_width=1.5,
            annotation_text=f"Retirement ({retirement_year})",
            annotation_position="top right",
            annotation_font=dict(color="#ef4444", size=11),
        )

    fig.update_layout(
        **_base_layout(
            xaxis=dict(title="Year", showgrid=False, dtick=5),
            yaxis=dict(
                title="Projected Value (CAD)",
                tickprefix="$",
                tickformat=",",
                gridcolor="#f1f5f9",
                zeroline=False,
            ),
        )
    )
    return _fig_json(fig)


# ─── Render ───────────────────────────────────────────────────────────────────

def render(analysis: dict):
    """Render the HTML dashboard from analysis results."""
    DASHBOARD_DIR.mkdir(parents=True, exist_ok=True)
    env = Environment(loader=FileSystemLoader(str(TEMPLATES_DIR)))
    template = env.get_template("dashboard.html.j2")

    contrib_charts = build_contribution_room_charts(analysis.get("contribution_room", {}))

    charts = {
        "net_worth": build_net_worth_chart(analysis.get("net_worth_series", [])),
        "allocation": build_allocation_charts(analysis.get("allocation", {})),
        "contribution_room_current": contrib_charts["current"],
        "contribution_room_prior": contrib_charts["prior"],
        "projection": build_projection_chart(analysis.get("projection", {})),
        "cost_basis": build_cost_basis_chart(analysis.get("cost_basis", [])),
    }

    output_path = DASHBOARD_DIR / "index.html"
    html = template.render(
        analysis=analysis,
        charts=charts,
        generated_at=datetime.now().strftime("%B %d, %Y at %I:%M %p"),
        current_total=analysis.get("current_total", 0),
    )

    with open(output_path, "w") as f:
        f.write(html)

    print(f"Dashboard written to: {output_path}")
    return output_path


if __name__ == "__main__":
    import sys
    sys.path.insert(0, str(ROOT / "scripts"))
    from analyze import run as analyze_run
    data = analyze_run()
    render(data)
