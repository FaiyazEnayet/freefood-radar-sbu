"""Build the dashboard HTML.

Two explicit modes:

- Production (default): renders the validated public SB Engaged snapshot to
  docs/dashboard.html via portfolio_ui/renderer.py in production mode. If no
  public snapshot exists, it refuses to build anything instead of silently
  falling back to seeded demo content.
- Demo (--demo): builds the explicit offline demonstration dashboard from
  data/seed_events.json. The demo is never written to the production artifact
  path docs/dashboard.html; it defaults to docs/demo-dashboard.html.

The daily workflow never invokes the demo path.

Run: PYTHONPATH=src .venv/bin/python scripts/make_dashboard.py [--demo]
"""

import json
import os
import sys
from datetime import datetime

import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from plotly.subplots import make_subplots

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from radar import digest as digest_mod
from radar import stats as stats_mod
from radar import watchlist as watchlist_mod
from radar.classifier import load_model
from radar.models import Event

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "..")
DOCS = os.path.join(ROOT, "docs")

BG = "#0e1117"
CARD = "#161b22"
TEXT = "#e6edf3"
ACCENT = "#f0a832"
GREEN = "#3fb950"
BLUE = "#58a6ff"


def load_events(root=None) -> list[Event]:
    root = root or ROOT
    with open(os.path.join(root, "data", "seed_events.json")) as f:
        raw = json.load(f)
    return [Event.from_dict(d) for d in raw]


def load_places(root=None) -> dict:
    root = root or ROOT
    with open(os.path.join(root, "data", "campus_places.json")) as f:
        return json.load(f)


def place_coords(name: str, places: dict) -> tuple[float, float] | None:
    for key, coords in places["places"].items():
        if key.lower() in name.lower() or name.lower() in key.lower():
            return coords["lat"], coords["lon"]
    return None


def kpi_cards(s: dict) -> str:
    cards = [
        ("Events scanned", str(s["n_events"])),
        ("Free-food hits", str(s["n_food_events"])),
        ("Hit rate", f"{s['food_rate']:.0%}"),
        ("Avg confidence", f"{s['avg_confidence']:.0%}"),
    ]
    inner = "".join(
        f"<div style='background:{CARD};border-radius:12px;padding:18px 22px;"
        f"min-width:150px;text-align:center'>"
        f"<div style='font-size:13px;color:#8b949e'>{label}</div>"
        f"<div style='font-size:32px;font-weight:700;color:{TEXT}'>{val}</div></div>"
        for label, val in cards
    )
    return f"<div style='display:flex;gap:14px;flex-wrap:wrap;margin:18px 0'>{inner}</div>"


def map_figure(ranked: list[Event], places: dict) -> go.Figure:
    rows = []
    for e in ranked:
        coords = place_coords(e.location or "", places)
        if not coords:
            continue
        when = e.starts_at.strftime("%a %I:%M %p") if e.starts_at else "TBA"
        rows.append({
            "lat": coords[0] + np.random.RandomState(hash(e.title) % 2**16).uniform(-0.0006, 0.0006),
            "lon": coords[1] + np.random.RandomState(hash(e.title) % 2**16 + 1).uniform(-0.0006, 0.0006),
            "label": f"<b>{e.title}</b><br>{e.club}<br>{e.location} · {when}<br>"
                     f"confidence {e.food_probability:.0%} · ranking score {e.score:.2f}",
            "prob": e.food_probability,
            "title": e.title,
        })
    df = pd.DataFrame(rows)
    fig = px.scatter_map(
        df, lat="lat", lon="lon", color="prob", hover_name="title",
        hover_data={"label": True, "lat": False, "lon": False, "prob": False},
        color_continuous_scale=["#ff6b6b", "#ffd166", "#3fb950"],
        range_color=[0.5, 1.0],
        zoom=14.2,
        center={"lat": places["campus_center"]["lat"], "lon": places["campus_center"]["lon"]},
        map_style="open-street-map",
        title="Campus map — today's free-food pins (color = confidence)",
    )
    fig.update_layout(height=480, margin=dict(l=0, r=0, t=40, b=0),
                      paper_bgcolor=BG, font_color=TEXT)
    fig.update_traces(marker=dict(size=13, opacity=0.92))
    return fig


def ranked_table(ranked: list[Event]) -> go.Figure:
    rows = []
    for i, e in enumerate(ranked[:15], 1):
        when = e.starts_at.strftime("%a %I:%M %p") if e.starts_at else "TBA"
        rows.append([i, e.title, e.club, e.location or "TBA", when,
                     f"{e.food_probability:.0%}", f"{e.score:.2f}",
                     ", ".join(e.food_keywords[:3])])
    fig = go.Figure(data=[go.Table(
        header=dict(values=["#", "Event", "Club", "Where", "When", "Confidence", "Ranking score", "Signals"],
                    fill_color="#1f6feb", font=dict(color="white", size=12), align="left"),
        cells=dict(values=list(zip(*rows)) if rows else [[]] * 8,
                   fill_color=CARD, font=dict(color=TEXT, size=11), align="left"),
    )])
    fig.update_layout(title="Ranked digest - heuristic ranking score = offer likelihood x food quality",
                      paper_bgcolor=BG, font_color=TEXT, height=520,
                      margin=dict(l=0, r=0, t=40, b=0))
    return fig


def classifier_report(root=None) -> tuple[go.Figure, go.Figure]:
    root = root or ROOT
    with open(os.path.join(root, "models", "metrics.json")) as f:
        m = json.load(f)
    cal = np.load(os.path.join(root, "models", "calibration.npz"))
    y_true, y_prob = cal["y_true"], cal["y_prob"]

    perf = go.Figure(data=[go.Bar(
        x=["Accuracy", "Precision", "Recall", "F1"],
        y=[m["accuracy"], m["precision"], m["recall"], m["f1"]],
        marker_color=[BLUE, GREEN, ACCENT, "#a371f7"],
        text=[f"{v:.1%}" for v in [m["accuracy"], m["precision"], m["recall"], m["f1"]]],
        textposition="outside",
    )])
    perf.update_layout(title=f"Classifier report — TF-IDF + logistic regression "
                             f"(n={m['n_total']}, {m['n_train']} train / {m['n_test']} test)",
                       yaxis_range=[0, 1.15], paper_bgcolor=BG, plot_bgcolor=BG,
                       font_color=TEXT, height=360)

    # Calibration curve: mean predicted prob vs observed rate per bin.
    bins = np.linspace(0, 1, 11)
    idx = np.clip(np.digitize(y_prob, bins) - 1, 0, 9)
    bx, by, bn = [], [], []
    for b in range(10):
        mask = idx == b
        if mask.sum() > 0:
            bx.append(y_prob[mask].mean())
            by.append(y_true[mask].mean())
            bn.append(mask.sum())
    calib = go.Figure()
    calib.add_trace(go.Scatter(x=[0, 1], y=[0, 1], mode="lines",
                               line=dict(dash="dash", color="#8b949e"), name="perfect"))
    calib.add_trace(go.Scatter(x=bx, y=by, mode="markers+lines", name="model",
                               marker=dict(size=10, color=ACCENT)))
    calib.update_layout(title="Calibration — predicted probability vs observed rate",
                        xaxis_title="mean predicted probability",
                        yaxis_title="observed free-food rate",
                        paper_bgcolor=BG, plot_bgcolor=BG, font_color=TEXT, height=360)
    return perf, calib


def analytics_figures(events: list[Event]):
    scored = [e for e in events if e.food_probability >= 0.5]
    top = stats_mod.top_clubs(scored, 10)
    clubs_fig = go.Figure(data=[go.Bar(
        y=[c for c, _ in reversed(top)], x=[n for _, n in reversed(top)],
        orientation="h", marker_color=GREEN,
        text=[n for _, n in reversed(top)], textposition="outside",
    )])
    clubs_fig.update_layout(title="Which clubs feed people most",
                            paper_bgcolor=BG, plot_bgcolor=BG, font_color=TEXT, height=420)

    mat = stats_mod.heatmap_matrix(scored)
    heat = go.Figure(data=[go.Heatmap(
        z=mat.values, x=mat.columns, y=mat.index,
        colorscale=["#161b22", "#f0a832"], showscale=True,
        text=mat.values, texttemplate="%{text}",
    )])
    heat.update_layout(title="Best days x times for free food",
                       paper_bgcolor=BG, font_color=TEXT, height=380)
    return clubs_fig, heat


def watchlist_html(alerts: list[watchlist_mod.Alert], favorites: list[str]) -> str:
    if not alerts:
        return "<p>No watchlist matches today.</p>"
    items = "".join(
        f"<div style='background:{CARD};border-left:4px solid {ACCENT};"
        f"border-radius:8px;padding:10px 14px;margin:8px 0'>"
        f"<b style='color:{ACCENT}'>{a.food.title()}</b> — {a.event.title} "
        f"<span style='color:#8b949e'>({a.event.club}, "
        f"{a.event.starts_at.strftime('%a %I:%M %p') if a.event.starts_at else 'TBA'})</span>"
        f"</div>"
        for a in alerts[:10]
    )
    return (f"<p style='color:#8b949e'>Watching: {', '.join(favorites)}</p>{items}")


def main(root=None, demo=False, output=None):
    """Build the dashboard. Returns a process exit code.

    Production (demo=False) renders only the validated public snapshot to
    docs/dashboard.html and refuses to build anything when no public snapshot
    exists. Demo (demo=True) builds the explicit offline seeded demonstration
    to a non-production output path.
    """
    from pathlib import Path as _Path
    root = _Path(root) if root else _Path(__file__).resolve().parents[1]
    if not demo:
        snapshot = root / "data/sbengaged-events.json"
        if not snapshot.exists():
            print("No public source snapshot exists; refusing to build a seeded demo as the production artifact. Use --demo for the explicit offline demonstration.")
            return 1
        sys.path.insert(0, str(root / "portfolio_ui"))
        from renderer import render
        render(root, "freefood-radar", root / "portfolio_ui/original-dashboard-snapshot.html", root / "docs/dashboard.html", mode="production")
        print("production dashboard -> %s" % (root / "docs/dashboard.html"))
        return 0
    out = _Path(output) if output else root / "docs/demo-dashboard.html"
    prod = (root / "docs/dashboard.html").resolve()
    if out.resolve() == prod:
        print("Refusing to write the demo to the production artifact path docs/dashboard.html.")
        return 1
    return _build_demo(root, out)


def _build_demo(root, out):
    events = load_events(root)
    places = load_places(root)
    model = load_model()
    _, ranked = digest_mod.build_digest(events, model)
    s = stats_mod.summary(events)
    favorites = watchlist_mod.load_favorites()
    alerts = watchlist_mod.check(ranked, favorites)

    perf_fig, calib_fig = classifier_report(root)
    clubs_fig, heat_fig = analytics_figures(events)

    parts = [
        f"<h1 style='color:{TEXT}'>Free Food Radar <span style='font-size:18px;color:#8b949e'>"
        f"Stony Brook University</span></h1>",
        f"<p style='color:#8b949e'>Offline demo: {s['n_events']} seeded events from the fixed fixture, "
        f"scored with a sigmoid-scaled NLP classifier, ranked by heuristic ranking score. "
        f"Week of Oct 12-18, 2026. Calibration on feed text is unmeasured.</p>",
        kpi_cards(s),
        "<h2 style='color:" + TEXT + "'>Today's map</h2>",
        map_figure(ranked, places).to_html(full_html=False, include_plotlyjs=True),
        "<h2 style='color:" + TEXT + "'>Ranked digest</h2>",
        ranked_table(ranked).to_html(full_html=False, include_plotlyjs=False),
        "<h2 style='color:" + TEXT + "'>The model behind the ranking</h2>",
        "<div style='display:flex;gap:14px;flex-wrap:wrap'>"
        "<div style='flex:1;min-width:320px'>" + perf_fig.to_html(full_html=False, include_plotlyjs=False) + "</div>"
        "<div style='flex:1;min-width:320px'>" + calib_fig.to_html(full_html=False, include_plotlyjs=False) + "</div></div>",
        "<h2 style='color:" + TEXT + "'>Historical analytics</h2>",
        "<div style='display:flex;gap:14px;flex-wrap:wrap'>"
        "<div style='flex:1;min-width:320px'>" + clubs_fig.to_html(full_html=False, include_plotlyjs=False) + "</div>"
        "<div style='flex:1;min-width:320px'>" + heat_fig.to_html(full_html=False, include_plotlyjs=False) + "</div></div>",
        "<h2 style='color:" + TEXT + "'>Watchlist alerts</h2>",
        watchlist_html(alerts, favorites),
        "<p style='color:#8b949e;font-size:12px;margin-top:30px'>Built with scikit-learn, "
        "Plotly, and BeautifulSoup. Classifier metrics from a real held-out test split.</p>",
    ]
    html = ("<!DOCTYPE html><html><head><meta charset='utf-8'>"
            "<meta name='viewport' content='width=device-width,initial-scale=1'>"
            "<title>Free Food Radar — Stony Brook</title></head>"
            f"<body style='background:{BG};color:{TEXT};font-family:system-ui,sans-serif;"
            "max-width:1100px;margin:0 auto;padding:24px'>" + "\n".join(parts) + "</body></html>")

    os.makedirs(os.path.dirname(str(out)), exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        f.write(_portfolio_html(html, mode="demo"))
    print(f"demo dashboard -> {out} ({os.path.getsize(out)/1e6:.1f} MB)")
    return 0



def _portfolio_html(raw, mode="production"):
    """Presentation-only transform of this run’s freshly calculated figures."""
    import sys
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root / "portfolio_ui"))
    from renderer import render_html
    return render_html(root, 'freefood-radar', raw, mode=mode)[0]

if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--demo", action="store_true", help="Build the explicit offline demonstration dashboard from seeded data (never the production artifact).")
    ap.add_argument("--output", default=None, help="Demo output path (demo mode only).")
    a = ap.parse_args()
    raise SystemExit(main(demo=a.demo, output=a.output))
