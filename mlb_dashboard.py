# 🚀 Improved MLB Daily BvP Dashboard (Faster + Cleaner)
import streamlit as st
import pandas as pd
import requests
from datetime import date, datetime
import time
from concurrent.futures import ThreadPoolExecutor

st.set_page_config(page_title="MLB Daily BvP", page_icon="⚾", layout="wide")

st.title("⚾ All Batter vs. Pitcher Matchups")
st.caption(f"Live MLB Data • {date.today().strftime('%B %d, %Y')}")

BASE = "https://statsapi.mlb.com/api/v1"

# ── API HELPER ─────────────────────────────────────────────
def api_get(path, **params):
    for attempt in range(3):
        try:
            r = requests.get(BASE + path, params=params, timeout=20)
            r.raise_for_status()
            return r.json()
        except:
            time.sleep(2 ** attempt)
    return {}

# ── CACHED HELPERS ─────────────────────────────────────────
@st.cache_data(ttl=86400)
def get_player_handedness(player_id):
    data = api_get(f"/people/{player_id}")
    person = data.get("people", [{}])[0]

    bats = person.get("batSide", {}).get("code", "")
    throws = person.get("pitchHand", {}).get("code", "")

    batter_hand = {"R": "Right", "L": "Left", "S": "Switch"}.get(bats, "Unknown")
    pitcher_hand = {"R": "Right", "L": "Left"}.get(throws, "Unknown")

    return batter_hand, pitcher_hand

@st.cache_data(ttl=86400)
def fetch_bvp(batter_id, pitcher_id):
    data = api_get(f"/people/{batter_id}/stats",
                   stats="vsPlayer",
                   opposingPlayerId=pitcher_id,
                   sportId=1,
                   group="hitting")

    for sg in data.get("stats", []):
        for split in sg.get("splits", []):
            stat = split.get("stat", {})
            if stat.get("atBats", 0) > 0:
                return stat
    return None

@st.cache_data(ttl=3600)
def get_recent_stats(batter_id):
    season = date.today().year
    data = api_get(f"/people/{batter_id}/stats",
                   stats="gameLog",
                   group="hitting",
                   season=season)

    ab = hits = streak = 0

    games = []
    for s in data.get("stats", []):
        games.extend(s.get("splits", []))

    games = sorted(games, key=lambda x: x.get("date", ""), reverse=True)

    current_streak = 0
    for g in games:
        stat = g.get("stat", {})
        game_ab = stat.get("atBats", 0)
        game_hits = stat.get("hits", 0)

        for _ in range(game_ab):
            if ab >= 20:
                break
            ab += 1
            if game_hits > 0:
                hits += 1
                game_hits -= 1

        if stat.get("hits", 0) > 0:
            current_streak += 1
        else:
            current_streak = 0

        streak = max(streak, current_streak)

    return f"{hits}-{ab}", streak

# ── GAME DATA ──────────────────────────────────────────────
def fetch_schedule():
    data = api_get("/schedule", sportId=1, date=date.today().isoformat(), hydrate="probablePitcher")
    games = []
    for d in data.get("dates", []):
        games.extend(d.get("games", []))
    return games


def get_roster(team_id):
    data = api_get(f"/teams/{team_id}/roster", rosterType="active")
    return [(p["person"]["fullName"], p["person"]["id"])
            for p in data.get("roster", [])
            if p.get("position", {}).get("type") != "Pitcher"]

# ── MAIN DATA BUILDER ──────────────────────────────────────
@st.cache_data(ttl=1800)
def build_dataframe():
    games = fetch_schedule()
    rows = []

    def process(batter, pitcher_id, context):
        name, bid = batter
        bvp = fetch_bvp(bid, pitcher_id)
        if not bvp or bvp.get("atBats", 0) < 10:
            return None

        avg = float(bvp.get("avg", 0))
        if avg < 0.25:
            return None

        last20, streak = get_recent_stats(bid)
        b_hand, p_hand = get_player_handedness(bid)

        return {
            **context,
            "Batter": name,
            "Batter Hand": b_hand,
            "Pitcher Hand": p_hand,
            "Last 20 AB": last20,
            "Streak": streak,
            "AB": bvp.get("atBats"),
            "H": bvp.get("hits"),
            "HR": bvp.get("homeRuns"),
            "AVG": avg,
            "OPS": float(bvp.get("ops", 0))
        }

    with ThreadPoolExecutor(max_workers=12) as executor:
        futures = []

        for g in games:
            home = g["teams"]["home"]
            away = g["teams"]["away"]

            match = f"{away['team']['name']} @ {home['team']['name']}"

            home_sp = home.get("probablePitcher", {}).get("id")
            away_sp = away.get("probablePitcher", {}).get("id")

            if home_sp:
                for batter in get_roster(away["team"]["id"]):
                    futures.append(executor.submit(process, batter, home_sp, {
                        "Matchup": match,
                        "Pitcher": home.get("probablePitcher", {}).get("fullName", "")
                    }))

            if away_sp:
                for batter in get_roster(home["team"]["id"]):
                    futures.append(executor.submit(process, batter, away_sp, {
                        "Matchup": match,
                        "Pitcher": away.get("probablePitcher", {}).get("fullName", "")
                    }))

        for f in futures:
            r = f.result()
            if r:
                rows.append(r)

    df = pd.DataFrame(rows)
    if not df.empty:
        df = df.sort_values("OPS", ascending=False)
    return df

# ── LOAD DATA ──────────────────────────────────────────────
data = build_dataframe()

# ── UI FILTERS ─────────────────────────────────────────────
st.sidebar.header("Filters")
search = st.sidebar.text_input("Search Batter")
min_ops = st.sidebar.slider("Min OPS", 0.5, 1.5, 0.7)

filtered = data.copy()

if search:
    filtered = filtered[filtered["Batter"].str.contains(search, case=False)]

filtered = filtered[filtered["OPS"] >= min_ops]

# ── DISPLAY ────────────────────────────────────────────────
st.subheader("Matchups")

st.dataframe(filtered, use_container_width=True, height=800)

st.success(f"Showing {len(filtered)} matchups")

# ── DOWNLOAD ───────────────────────────────────────────────
st.download_button(
    "Download CSV",
    filtered.to_csv(index=False),
    "bvp.csv",
    "text/csv"
)
