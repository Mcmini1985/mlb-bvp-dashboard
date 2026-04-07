"""
MLB Daily BvP Dashboard - Standalone (ALL Batters - No Limit)
"""
import streamlit as st
import pandas as pd
import requests
from datetime import date, datetime
from io import BytesIO
import time

st.set_page_config(page_title="MLB Daily BvP", page_icon="⚾", layout="wide")

st.title("⚾ All Batter vs. Pitcher Matchups")
st.caption(f"Full BvP Data — Live from MLB API • {date.today().strftime('%B %d, %Y')}")

# ── CONFIGURATION ────────────────────────────────────────────────────────────
BASE = "https://statsapi.mlb.com/api/v1"
MAX_BATTERS_PER_TEAM = None   # ←←← No limit (uses full active roster)

def api_get(path, **params):
    for attempt in range(1, 4):
        try:
            r = requests.get(BASE + path, params=params, timeout=30)
            r.raise_for_status()
            return r.json()
        except requests.exceptions.Timeout:
            if attempt == 3:
                st.warning(f"API timeout after 3 attempts: {path}")
                return {}
            time.sleep(2 ** attempt)
        except Exception as e:
            st.warning(f"API error: {path} → {e}")
            return {}
    return {}

def fetch_schedule(game_date):
    data = api_get("/schedule", sportId=1, date=game_date, hydrate="probablePitcher,lineups,team")
    games = []
    for d in data.get("dates", []):
        games.extend(d.get("games", []))
    return games

def probable_pitcher(game, side):
    pp = game.get("teams", {}).get(side, {}).get("probablePitcher", {})
    return pp.get("fullName", "TBD"), pp.get("id")

def team_info(game, side):
    t = game.get("teams", {}).get(side, {}).get("team", {})
    return t.get("name", "?"), t.get("id")

def fetch_roster_batters(team_id):
    data = api_get(f"/teams/{team_id}/roster", rosterType="active", season=date.today().year)
    batters = []
    for p in data.get("roster", []):
        if p.get("position", {}).get("type", "") != "Pitcher":
            person = p.get("person", {})
            batters.append((person.get("fullName", "?"), person.get("id")))
    return batters   # ← No slicing = ALL batters

def fetch_confirmed_lineup(game, side):
    order = game.get("lineups", {}).get(f"{side}Players", [])
    return [(p.get("fullName", "?"), p.get("id")) for p in order]

def fetch_bvp(batter_id, pitcher_id):
    data = api_get(f"/people/{batter_id}/stats", stats="vsPlayer", opposingPlayerId=pitcher_id,
                   sportId=1, group="hitting")
    for sg in data.get("stats", []):
        splits = sg.get("splits", [])
        if splits:
            s = splits[0].get("stat", {})
            ab = s.get("atBats", 0)
            if ab == 0:
                return None
            return {
                "ab": ab, "h": s.get("hits", 0), "hr": s.get("homeRuns", 0),
                "rbi": s.get("rbi", 0), "bb": s.get("baseOnBalls", 0),
                "so": s.get("strikeOuts", 0),
                "avg": s.get("avg", ".000"), "obp": s.get("obp", ".000"),
                "slg": s.get("slg", ".000"), "ops": s.get("ops", ".000")
            }
    return None

# ── Generate DataFrame (ALL batters) ────────────────────────────────────────
@st.cache_data(ttl=300)
def generate_bvp_dataframe():
    with st.spinner("Fetching ALL BvP matchups from MLB API..."):
        game_date = date.today().isoformat()
        games = fetch_schedule(game_date)
        if not games:
            st.error("No games found today.")
            return pd.DataFrame()

        rows = []
        progress_bar = st.progress(0)
        total = len(games) * 2
        count = 0

        for g in games:
            home_name, home_id = team_info(g, "home")
            away_name, away_id = team_info(g, "away")
            label = f"{away_name} @ {home_name}"
            h_sp, h_sp_id = probable_pitcher(g, "home")
            a_sp, a_sp_id = probable_pitcher(g, "away")

            sides = [
                (away_name, away_id, h_sp, h_sp_id, home_name, "away"),
                (home_name, home_id, a_sp, a_sp_id, away_name, "home"),
            ]
            for bat_team, bat_team_id, sp_name, sp_id, pit_team, side_key in sides:
                if not sp_id:
                    continue
                confirmed = fetch_confirmed_lineup(g, side_key)
                batters = confirmed if confirmed else fetch_roster_batters(bat_team_id)
                lineup_tag = "✓ Confirmed" if confirmed else "Projected"

                for bname, bid in batters:
                    if not bid:
                        continue
                    bvp = fetch_bvp(bid, sp_id)
                    if not bvp:
                        continue
                    rows.append({
                        "Matchup": label,
                        "Batter": bname,
                        "Batter Team": bat_team,
                        "Opposing Pitcher": sp_name,
                        "Pitcher Team": pit_team,
                        "AB": bvp["ab"], "H": bvp["h"], "HR": bvp["hr"],
                        "RBI": bvp["rbi"], "BB": bvp["bb"], "SO": bvp["so"],
                        "AVG": bvp["avg"], "OBP": bvp["obp"],
                        "SLG": bvp["slg"], "OPS": bvp["ops"],
                        "Lineup?": lineup_tag
                    })
                count += 1
                progress_bar.progress(min(count / total, 1.0))

        df = pd.DataFrame(rows)
        return df

data = generate_bvp_dataframe()

# Buttons
col1, col2 = st.columns([3, 1])
with col1:
    st.subheader("All Batter vs. Pitcher Matchups")
with col2:
    if st.button("🔄 Refresh All Data Now", type="primary"):
        with st.spinner("Pulling fresh data from MLB API..."):
            st.cache_data.clear()
            st.rerun()

# OPS color coding
def color_ops(val):
    try:
        v = float(val)
        if v >= 0.900:
            return "background-color: #C6EFCE; color: #006100"
        elif v >= 0.700:
            return "background-color: #FFEB9C; color: #9C6500"
        else:
            return "background-color: #FFC7CE; color: #9C0006"
    except:
        return ""

styled = data.style\
    .map(color_ops, subset=["OPS"])\
    .set_properties(**{'text-align': 'center'})\
    .set_table_styles([{'selector': 'th', 'props': [('background-color', '#1F4E79'), 
                                                    ('color', 'white'), 
                                                    ('font-weight', 'bold')]}])

st.dataframe(styled, use_container_width=True, hide_index=True, height=900)

st.success(f"✅ Showing ALL batters with career BvP stats ({len(data)} rows)")