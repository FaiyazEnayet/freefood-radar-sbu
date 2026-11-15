"""Render docs/demo.gif: an animated tour of the Free Food Radar dashboard.

Offline only: the tour renders the fixed seeded fixture, never a live
feed. Matplotlib animation (no browser needed): ranked digest bars, a
campus map scatter, the calibration curve, and the day x time heatmap,
the same numbers as the offline demo dashboard.

Run: PYTHONPATH=src .venv/bin/python scripts/make_demo_gif.py
"""

import json
import os
import sys

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.animation import FuncAnimation

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from radar import digest as digest_mod
from radar import stats as stats_mod
from radar.classifier import load_model
from radar.models import Event

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "..")
DOCS = os.path.join(ROOT, "docs")

FPS = 8


def load_events() -> list[Event]:
    with open(os.path.join(ROOT, "data", "seed_events.json")) as f:
        raw = json.load(f)
    return [Event.from_dict(d) for d in raw]


def load_places() -> dict:
    with open(os.path.join(ROOT, "data", "campus_places.json")) as f:
        return json.load(f)


def main():
    events = load_events()
    places = load_places()
    model = load_model()
    _, ranked = digest_mod.build_digest(events, model)
    with open(os.path.join(ROOT, "models", "metrics.json")) as f:
        metrics = json.load(f)
    cal = np.load(os.path.join(ROOT, "models", "calibration.npz"))
    y_true, y_prob = cal["y_true"], cal["y_prob"]

    top = ranked[:8]
    titles = [e.title[:20] for e in top]
    evs = [e.score for e in top]
    probs = [e.food_probability for e in top]

    # Map pins: jitter around building coords like the dashboard does.
    lats, lons, sizes, labels = [], [], [], []
    for e in ranked:
        coords = None
        for key, c in places["places"].items():
            if key.lower() in (e.location or "").lower() or (e.location or "").lower() in key.lower():
                coords = (c["lat"], c["lon"])
                break
        if coords:
            rng = np.random.RandomState(abs(hash(e.title)) % 2**16)
            lats.append(coords[0] + rng.uniform(-0.0006, 0.0006))
            lons.append(coords[1] + rng.uniform(-0.0006, 0.0006))
            sizes.append(40 + 160 * e.food_probability)
            labels.append(e.title[:24])
    lats, lons = np.array(lats), np.array(lons)

    # Calibration bins.
    bins = np.linspace(0, 1, 11)
    idx = np.clip(np.digitize(y_prob, bins) - 1, 0, 9)
    bx, by = [], []
    for b in range(10):
        mask = idx == b
        if mask.sum():
            bx.append(y_prob[mask].mean())
            by.append(y_true[mask].mean())

    mat = stats_mod.heatmap_matrix([e for e in events if e.food_probability >= 0.5])

    plt.style.use("dark_background")
    fig, axes = plt.subplots(2, 2, figsize=(9.6, 6.4))
    fig.suptitle("Free Food Radar - Stony Brook  |  offline demo tour",
                 fontsize=14, fontweight="bold", y=0.98)
    fig.tight_layout(rect=[0, 0, 1, 0.94], pad=2.0)
    fig.subplots_adjust(left=0.14)
    (ax_rank, ax_map), (ax_cal, ax_heat) = axes

    FRAMES_PER_PANEL, N_PANELS = 12, 4
    total = FRAMES_PER_PANEL * N_PANELS

    def draw(i):
        for ax in axes.flat:
            ax.clear()
        panel = i // FRAMES_PER_PANEL
        step = (i % FRAMES_PER_PANEL) + 1
        alpha = min(1.0, step / 4)

        # Ranked digest (always drawn, grows).
        n = min(len(top), 2 + step // 2)
        ax_rank.barh(titles[:n][::-1], evs[:n][::-1], color="#f0a832", alpha=alpha)
        ax_rank.set_title("Ranked digest - heuristic ranking score", fontsize=11)
        ax_rank.set_xlabel("ranking score = offer likelihood x quality")

        # Map pins (fade in).
        sc = ax_map.scatter(lons, lats, s=np.array(sizes) * alpha, c="#3fb950",
                            alpha=0.75 * alpha, edgecolors="white", linewidths=0.5)
        ax_map.set_title("Campus map - today's pins", fontsize=11)
        ax_map.set_xlabel("lon")
        ax_map.tick_params(labelsize=7)

        # Calibration (draws progressively).
        k = min(len(bx), 2 + step // 2)
        ax_cal.plot([0, 1], [0, 1], "--", color="gray", lw=1)
        ax_cal.plot(bx[:k], by[:k], "o-", color="#f0a832", ms=6)
        ax_cal.set_title("Calibration - predicted vs observed", fontsize=11)
        ax_cal.set_xlim(0, 1)
        ax_cal.set_ylim(0, 1)

        # Heatmap.
        im = ax_heat.imshow(mat.values, cmap="YlOrBr", aspect="auto", alpha=alpha)
        ax_heat.set_title("Best days x times", fontsize=11)
        ax_heat.set_xticks(range(len(mat.columns)))
        ax_heat.set_xticklabels([c.replace("\n", " ") for c in mat.columns],
                                fontsize=6, rotation=20)
        ax_heat.set_yticks(range(len(mat.index)))
        ax_heat.set_yticklabels(mat.index, fontsize=7)

        # Spotlight the active panel.
        for ax, p in zip(axes.flat, range(N_PANELS)):
            for spine in ax.spines.values():
                spine.set_color("#f0a832" if p == panel else "#333")
                spine.set_linewidth(2.5 if p == panel else 1.0)
        return []

    anim = FuncAnimation(fig, draw, frames=total, blit=False)
    os.makedirs(DOCS, exist_ok=True)
    out = os.path.join(DOCS, "demo.gif")
    anim.save(out, writer="pillow", fps=FPS)
    plt.close(fig)
    print(f"gif -> {out} ({os.path.getsize(out)/1e6:.2f} MB, {total} frames)")


if __name__ == "__main__":
    main()
