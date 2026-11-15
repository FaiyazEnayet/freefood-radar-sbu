"""End-to-end demo pipeline: seed events -> classify -> rank -> digest.

Offline only: the input is the fixed fixture data/seed_events.json,
never a live feed.

Run: PYTHONPATH=src .venv/bin/python scripts/build_digest.py
Writes digest.md and prints the ranked list.
"""

import json
import os
import sys
from datetime import date

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from radar import digest as digest_mod
from radar.classifier import load_model
from radar.models import Event
from radar import watchlist as watchlist_mod

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "..")


def load_seed_events() -> list[Event]:
    with open(os.path.join(ROOT, "data", "seed_events.json")) as f:
        raw = json.load(f)
    return [Event.from_dict(d) for d in raw]


def main():
    events = load_seed_events()
    model = load_model()
    text, ranked = digest_mod.build_digest(events, model)
    with open(os.path.join(ROOT, "digest.md"), "w") as f:
        f.write(text)

    print(f"{len(events)} candidates -> {len(ranked)} likely free-food events\n")
    for i, e in enumerate(ranked[:12], 1):
        when = e.starts_at.strftime("%a %I:%M %p") if e.starts_at else "TBA"
        print(f"{i:2d}. [{e.food_probability:5.1%} | ranking score {e.score:4.2f}] "
              f"{e.title} - {e.club} @ {e.location} ({when})")

    favs = watchlist_mod.load_favorites()
    alerts = watchlist_mod.check(ranked, favs)
    print(f"\n{len(alerts)} watchlist alerts for {favs}")
    for a in alerts[:8]:
        print(f"  ! {a.food}: {a.event.title} ({a.event.club})")


if __name__ == "__main__":
    main()
