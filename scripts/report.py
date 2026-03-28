"""
Report generator: renders the HTML dashboard from analysis results.
"""
import json
from datetime import datetime
from pathlib import Path

import plotly.graph_objects as go
import plotly.utils
from jinja2 import Environment, FileSystemLoader

ROOT = Path(__file__).parent.parent
TEMPLATES_DIR = ROOT / "templates"
DASHBOARD_DIR = ROOT / "dashboard"


def _fig_json(fig: go.Figure) -> str:
    return json.dumps(fig, cls=plotly.utils.PlotlyJSONEncoder)


def build_net_worth_chart(series: list[dict]) -> str:
    if not series:
        return "{}"
    dates = [r["date"] for r in series]
    totals = [r.get("total", 0) for r in series]

    # Get all account columns
    acct_keys = [k for k in (series[-1].keys() if series else []) if k not in ("date", "total")]

    fig = go.Figure()

    if acct_keys:
        for key in acct_keys:
            vals = [r.get(key, 0) or 0 for r in series]
            label = key.replace("_", " ").title()
            fig.add_trace(go.Scatter(x=dates, y=vals, name=label, stackgroup="one", hovertemplate="$%{y:,.0f}"))
    else:
        fig.add_trace(go.Scatter(x=dates, y=totals, name="Total", fill="tozeroy",
                                  line=dict(color="#4f46e5", width=3),
                                  hovertemplate="$%{y:,.2f}"))

    fig.update_layout(
        title="Net Worth Over Time",
        xaxis_title="Date",
        yaxis_title="Value (CAD)",
        yaxis_tickprefix="$",
        yaxis_tickformat=",",
        hovermode="x unified",
        plot_bgcolor="white",
        paper_bgcolor="white",
        legend=dict(orientation="h", yanchor="bottom", y=1.02),
        margin=dict(l=60, r=20, t=60, b=40),
    )
    return _fig_json(fig)


def build_allocation_charts(allocation: dict) -> dict:
    charts = {}

    palette = ["#4f46e5", "#7c3aed", "#0ea5e9", "#10b981", "#f59e0b", "#ef4444", "#6b7280"]

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
            hole=0.4,
            textinfo="label+percent",
            hovertemplate="%{label}: $%{value:,.0f} (%{percent})<extra></extra>",
            marker=dict(colors=palette[:len(labels)]),
        ))
        fig.update_layout(
            title=label,
            plot_bgcolor="white",
            paper_bgcolor="white",
            margin=dict(l=20, r=20, t=60, b=20),
            showlegend=False,
        )
        charts[key] = _fig_json(fig)

    return charts


def build_projection_chart(projection: dict) -> str:
    series = projection.get("projection_series", [])
    if not series:
        return "{}"

    years = [p["year"] for p in series]
    nominal = [p["value"] for p in series]
    real = [p["value_real"] for p in series]

    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=years, y=nominal, name="Projected (Nominal)",
        fill="tozeroy", line=dict(color="#4f46e5", width=3),
        hovertemplate="Year %{x}: $%{y:,.0f}<extra>Nominal</extra>",
    ))
    fig.add_trace(go.Scatter(
        x=years, y=real, name="Projected (Real / Today's $)",
        line=dict(color="#10b981", width=2, dash="dash"),
        hovertemplate="Year %{x}: $%{y:,.0f}<extra>Real</extra>",
    ))

    # Mark retirement age
    target_year = years[-1] if years else None
    if target_year:
        fig.add_vline(x=target_year, line_dash="dot", line_color="#ef4444",
                      annotation_text="Retirement", annotation_position="top right")

    fig.update_layout(
        title="Portfolio Projection to Retirement",
        xaxis_title="Year",
        yaxis_title="Value (CAD)",
        yaxis_tickprefix="$",
        yaxis_tickformat=",",
        hovermode="x unified",
        plot_bgcolor="white",
        paper_bgcolor="white",
        legend=dict(orientation="h", yanchor="bottom", y=1.02),
        margin=dict(l=60, r=20, t=60, b=40),
    )
    return _fig_json(fig)


def build_contribution_room_chart(contrib_room: dict) -> str:
    categories = []
    used = []
    remaining = []

    tfsa = contrib_room.get("tfsa", {})
    if tfsa and not tfsa.get("error"):
        total = (tfsa.get("ytd_contributions", 0) or 0) + (tfsa.get("remaining_this_year", 0) or 0)
        categories.append("TFSA")
        used.append(tfsa.get("ytd_contributions", 0) or 0)
        remaining.append(tfsa.get("remaining_this_year", 0) or 0)

    rrsp = contrib_room.get("rrsp", {})
    if rrsp:
        categories.append("RRSP")
        used.append(rrsp.get("ytd_contributions", 0) or 0)
        remaining.append(rrsp.get("remaining_this_year", 0) or 0)

    for b in (contrib_room.get("resp") or []):
        categories.append(f"RESP ({b['name']})")
        used.append(b.get("ytd_contributions", 0) or 0)
        remaining.append((b.get("lifetime_room_remaining", 0) or 0))

    if not categories:
        return "{}"

    fig = go.Figure()
    fig.add_trace(go.Bar(name="Contributed YTD", x=categories, y=used,
                          marker_color="#4f46e5", hovertemplate="$%{y:,.0f}<extra>Used</extra>"))
    fig.add_trace(go.Bar(name="Remaining Room", x=categories, y=remaining,
                          marker_color="#d1d5db", hovertemplate="$%{y:,.0f}<extra>Remaining</extra>"))

    fig.update_layout(
        barmode="stack",
        title="Contribution Room",
        yaxis_title="Amount (CAD)",
        yaxis_tickprefix="$",
        yaxis_tickformat=",",
        plot_bgcolor="white",
        paper_bgcolor="white",
        legend=dict(orientation="h", yanchor="bottom", y=1.02),
        margin=dict(l=60, r=20, t=60, b=40),
    )
    return _fig_json(fig)


def render(analysis: dict):
    """Render the HTML dashboard from analysis results."""
    DASHBOARD_DIR.mkdir(parents=True, exist_ok=True)
    env = Environment(loader=FileSystemLoader(str(TEMPLATES_DIR)))
    template = env.get_template("dashboard.html.j2")

    charts = {
        "net_worth": build_net_worth_chart(analysis.get("net_worth_series", [])),
        "allocation": build_allocation_charts(analysis.get("allocation", {})),
        "contribution_room": build_contribution_room_chart(analysis.get("contribution_room", {})),
        "projection": build_projection_chart(analysis.get("projection", {})),
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
