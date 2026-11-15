"""'Notify me' watchlist: favorite foods -> matching events.

A student keeps a small list of favorite foods ("pizza", "boba",
"tacos"). After scoring, every event whose text mentions a favorite
(and passes the classifier bar) becomes an alert. Pure matching logic;
delivery (email/push) is left to the caller.
"""

from __future__ import annotations

from dataclasses import dataclass

from .models import Event


@dataclass
class Alert:
    food: str
    event: Event
    reason: str


DEFAULT_FAVORITES = ["pizza", "boba", "tacos", "sushi", "wings", "ramen"]


def load_favorites(path: str | None = None) -> list[str]:
    if not path:
        return list(DEFAULT_FAVORITES)
    with open(path) as f:
        return [ln.strip().lower() for ln in f if ln.strip() and not ln.startswith("#")]


def check(events: list[Event], favorites: list[str],
          min_prob: float = 0.5) -> list[Alert]:
    alerts: list[Alert] = []
    for ev in events:
        if ev.food_probability < min_prob:
            continue
        text = ev.text().lower()
        for fav in favorites:
            fav = fav.lower().strip()
            if fav and fav in text:
                when = ev.starts_at.strftime("%a %I:%M %p") if ev.starts_at else "TBA"
                alerts.append(
                    Alert(
                        food=fav,
                        event=ev,
                        reason=f"'{fav}' mentioned ({ev.club}, {when}, "
                               f"confidence {ev.food_probability:.0%})",
                    )
                )
    # Best match per (food, event) first, then by heuristic ranking score.
    alerts.sort(key=lambda a: a.event.score, reverse=True)
    return alerts


def render_alerts(alerts: list[Alert]) -> str:
    if not alerts:
        return "No watchlist matches today. Check back tomorrow."
    lines = ["# Watchlist alerts", ""]
    for a in alerts:
        when = a.event.starts_at.strftime("%a %I:%M %p") if a.event.starts_at else "TBA"
        lines.append(
            f"- **{a.food.title()}** @ {a.event.title} "
            f"({a.event.club}, {a.event.location or 'TBA'}, {when})"
        )
    return "\n".join(lines)
