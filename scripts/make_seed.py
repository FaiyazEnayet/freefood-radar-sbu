"""Generate data/seed_events.json: realistic SBU event candidates.

Offline only: these stand in for live feed output in the demo. The
file is a fixed fixture; the demo never fetches a live feed. Each
entry mirrors what the scrapers produce (title, description, club,
location, time, source). Roughly half mention free food; the rest are
tricky negatives so the classifier has real work to do in the dashboard.
"""

import json
import os
from datetime import datetime, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "..", "data", "seed_events.json")

# Base week: Monday Oct 12, 2026 through Sunday Oct 18, 2026.
MON = datetime(2026, 10, 12)

# (day_offset, hour, minute, club, title, description, location, source)
RAW = [
    (0, 12, 0, "CS Club", "GBM #6: Internship Panel",
     "Free pizza for all attendees! Alumni from Google and Jane Street answer questions. SAC 302, bring friends.",
     "SAC", "corq"),
    (0, 17, 30, "SBU Robotics", "Build Night",
     "Weekly build session. We provide tools and parts; dinner is on your own.",
     "Engineering Building", "corq"),
    (0, 19, 0, "Anime Club", "Premiere Night: Fall Season",
     "Free boba for the first 50 attendees! Screening the top 3 premieres with subtitles.",
     "SAC Auditorium", "instagram"),
    (1, 11, 0, "Career Center", "Resume Workshop",
     "Free sandwiches for everyone who attends. Bring a printed resume for review.",
     "Melville Library", "corq"),
    (1, 13, 0, "Philosophy Society", "Food for Thought: AI Ethics",
     "Weekly debate. This week's motion: should AI write your essays? No food, just arguments.",
     "Harriman Hall", "discord"),
    (1, 18, 0, "IEEE", "Soldering Workshop",
     "Learn to solder! All materials included, $10 materials fee. Pizza for e-board only.",
     "Frey Hall", "corq"),
    (1, 20, 0, "Esports Club", "Tournament Finals Watch Party",
     "Pizza, wings, and soda - all free! Big screen in the Union basement, brackets on the projector.",
     "Stony Brook Union", "discord"),
    (2, 9, 0, "Campus Rec", "Sunrise Yoga",
     "Free donuts for early risers after the session. Mats provided, all levels.",
     "Campus Rec Center", "corq"),
    (2, 12, 30, "Latin American Student Org", "Fiesta Wednesday",
     "Free empanadas and live music! Dance performances at 1 PM, food while supplies last.",
     "SAC Plaza", "instagram"),
    (2, 15, 0, "Math Club", "Problem Solving Seminar",
     "Weekly olympiad-style problems. Quiet room, no food allowed in the library.",
     "Melville Library", "corq"),
    (2, 18, 30, "Korean Student Association", "Ramen Night",
     "Free ramen tasting - three broths, all toppings. Veggie options available!",
     "Roth Cafe", "instagram"),
    (3, 12, 0, "Women in Tech", "Alumni Lunch",
     "Catered lunch from Panera will be provided. RSVP required, limited to 40 seats.",
     "Frey Hall", "corq"),
    (3, 14, 0, "Photography Club", "Food Photography Walk",
     "Bring your camera - we style fake food around campus. No real food (sorry).",
     "Wang Center", "discord"),
    (3, 17, 0, "Muslim Student Association", "Community Dinner",
     "Free biryani dinner for all attendees! Community announcements after Maghrib.",
     "SAC Ballroom", "corq"),
    (3, 19, 30, "Board Game Club", "Marathon Night",
     "Free pizza rolls and a 200-game library. SAC 304 until midnight.",
     "SAC", "discord"),
    (4, 10, 0, "Pre-Health Society", "Med School Panel",
     "Free bagels and coffee. Alumni from Stony Brook Medicine answer questions.",
     "Javits Lecture Center", "corq"),
    (4, 12, 0, "Taco Club", "Taco Tuesday",
     "Free tacos al pastor for the first 100 students! Union Commons, come hungry.",
     "Union Commons", "instagram"),
    (4, 16, 0, "Chess Club", "Blitz Tournament",
     "$50 prize for first place. No food - the pieces are the only thing getting eaten.",
     "SAC", "corq"),
    (4, 18, 0, "Caribbean Student Org", "Cookout",
     "Free jerk chicken, rice and peas, and plantains! Music by the DJ collective.",
     "Roth Pond", "instagram"),
    (5, 11, 0, "Involvement Fair", "Club Tabling",
     "Free soft pretzels and lemonade at the welcome tent! 80+ clubs recruiting.",
     "SAC Plaza", "corq"),
    (5, 13, 0, "Sustainability Club", "Campus Cleanup",
     "Gloves and bags provided. Help green the campus - lunch not included.",
     "Roth Pond", "corq"),
    (5, 19, 0, "Film Club", "Outdoor Screening",
     "Free popcorn! We're showing a classic under the stars. Bring blankets.",
     "SAC Plaza", "discord"),
    (6, 10, 0, "Run Club", "5K Fun Run",
     "Free breakfast burritos at the finish line! All paces welcome, South P Lot start.",
     "South P Lot", "instagram"),
    (6, 14, 0, "Transfer Students", "Welcome Brunch",
     "Brunch is on us! Free bagels, fruit, and coffee. Meet other transfers.",
     "Tabler", "corq"),
    (6, 18, 0, "Italian Club", "Movie Night: Cinema Paradiso",
     "Pasta dinner provided - free for all attendees! Discussion after the film.",
     "Tabler", "corq"),
    (0, 13, 0, "Debate Team", "Practice Rounds",
     "Weekly practice. Spectators welcome, no food served.",
     "Harriman Hall", "corq"),
    (1, 16, 0, "Data Science Club", "Kaggle Workshop",
     "Bring your laptop - we'll work through a real dataset together. Snacks not provided.",
     "Engineering Building", "discord"),
    (2, 20, 0, "Poetry Society", "Open Mic Slam",
     "Free brownies and lemonade for the audience! Sign up to perform at the door.",
     "Union Crafts Center", "instagram"),
    (3, 11, 0, "Blood Drive", "Donate Blood",
     "Free pizza slices for every donor! Walk-ins welcome, ID required.",
     "SAC Ballroom", "corq"),
    (4, 13, 30, "Vietnamese Student Association", "Pho Night",
     "Pho and spring rolls - free for the first 75 attendees! Broth taste-off at 2.",
     "Roth Cafe", "instagram"),
    (5, 15, 0, "Hackathon Club", "Info Session",
     "Learn the rules, meet teammates, see last year's winners. No food at this one.",
     "Frey Hall", "corq"),
    (6, 12, 0, "Garden Club", "Harvest Day",
     "Help harvest the community garden. We're a food-adjacent club but this is work, not lunch.",
     "Campus Garden", "discord"),
    (0, 18, 0, "Finance Society", "Stock Pitch Night",
     "Free wings during the pitches! Top 3 pitches win interview prep sessions.",
     "Frey Hall", "corq"),
    (1, 12, 0, "Commuter Students", "Lunch Meetup",
     "Bring your own lunch - we provide the company and the microwaves.",
     "Stony Brook Union", "discord"),
    (2, 17, 0, "Chinese Culture Club", "Dim Sum Social",
     "Free dumplings, bao, and shumai! Calligraphy station while you eat.",
     "Wang Center", "instagram"),
    (3, 20, 0, "Comedy Club", "Stand-Up Night",
     "Free show! Concessions sell snacks (not free, but cheap).",
     "SAC Auditorium", "corq"),
    (4, 11, 30, "Transfer Ambassadors", "Coffee Chat",
     "Free coffee and donuts. Ask anything about transferring credits.",
     "Melville Library", "corq"),
    (5, 17, 30, "Homecoming Committee", "Tailgate",
     "Free hot dogs and burgers before the game! LaValle Stadium east lot.",
     "LaValle Stadium", "instagram"),
    (6, 16, 0, "Hiking Club", "Fall Foliage Hike",
     "BYO lunch - we'll stop at the deli on the way to the trailhead.",
     "South P Lot", "discord"),
    (0, 15, 0, "Art Club", "Gallery Opening",
     "Free admission (no refreshments). Student work on display through Friday.",
     "Wang Center", "corq"),
    (1, 19, 0, "Muslim Student Association", "Halaqa & Snacks",
     "Short reminder followed by free samosas and chai for everyone.",
     "SAC 302", "corq"),
    (2, 12, 0, "Biology Club", "Research Talks",
     "Free subs from Jersey Mike's! Three 15-minute talks by undergrad researchers.",
     "Javits Lecture Center", "corq"),
    (3, 18, 0, "Game Dev Club", "Playtest Night",
     "Play student-made games and give feedback. Free pizza for playtesters!",
     "Engineering Building", "discord"),
    (4, 15, 0, "Economics Society", "Fed Speaker",
     "Guest lecture on monetary policy. No food - just interest rates.",
     "Frey Hall", "corq"),
    (5, 12, 0, "Pride Club", "Picnic",
     "Free sandwiches, fruit, and lemonade on the lawn! All welcome.",
     "Roth Pond", "instagram"),
    (6, 13, 0, "Volunteers", "Food Bank Trip",
     "We're collecting donations for the food bank - bring cans, not appetites.",
     "SAC Lobby", "corq"),
    (0, 20, 0, "Karaoke Club", "Open Mic",
     "Sign up to sing! Audience gets free popcorn between sets.",
     "Union Basement", "discord"),
    (2, 18, 0, "History Club", "Trivia Night",
     "Winning team gets a $20 gift card. No food prizes, just glory.",
     "SAC 304", "corq"),
]


def main():
    events = []
    for i, (d, h, m, club, title, desc, loc, src) in enumerate(RAW):
        events.append({
            "id": f"seed-{i:03d}",
            "title": title,
            "description": desc,
            "club": club,
            "location": loc,
            "starts_at": (MON + timedelta(days=d, hours=h, minutes=m)).isoformat(),
            "url": f"https://stonybrook.campusgroups.com/events/seed-{i:03d}",
            "source": src,
        })
    os.makedirs(os.path.join(HERE, "..", "data"), exist_ok=True)
    with open(OUT, "w") as f:
        json.dump(events, f, indent=2)
    n_food = sum(1 for r in RAW if any(
        w in (r[5] + r[4]).lower()
        for w in ("free pizza", "free boba", "free tacos", "free donuts", "free wings",
                  "free empanadas", "free ramen", "free biryani", "free pizza rolls",
                  "free bagels", "free jerk chicken", "free popcorn", "free breakfast",
                  "free brownies", "free dumplings", "free sandwiches", "free samosas",
                  "free subs", "free coffee", "free hot dogs", "catered", "will be provided",
                  "provided", "ice cream")))
    print(f"wrote {OUT}: {len(events)} events")


if __name__ == "__main__":
    main()
