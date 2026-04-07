"""
MLB Daily BvP Dashboard - Stable Version (Fixed Inconsistent Stats)
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
    return batters

def fetch_bvp(batter_id, pitcher_id):
    """Improved: selects the split with the highest AB count (career split)"""
    data = api_get(f"/people/{batter_id}/stats", stats="vsPlayer", opposingPlayerId=pitcher_id,
                   sportId=1, group="hitting")
    best_split = None
    max_ab = -1

    for sg in data.get("stats", []):
        for split in sg.get("splits", []):
            stat = split.get("stat", {})
            ab = stat.get("atBats", 0)
            if ab > max_ab:
                max_ab = ab
                best_split = stat

    if best_split is None or max_ab == 0:
        return None

    return {
        "ab": best_split.get("atBats", 0),
        "h": best_split.get("hits", 0),
        "hr": best_split.get("homeRuns", 0),
        "rbi": best_split.get("rbi", 0),
        "bb": best_split.get("baseOnBalls", 0),
        "so": best_split.get("strikeOuts", 0),
        "avg": best_split.get("avg", ".000"),
        "obp": best_split.get("obp", ".000"),
        "slg": best_split.get("slg", ".000"),
        "ops": best_split.get("ops", ".000")
    }

@st.cache_data(ttl=1800)   # Cache for 30 minutes so numbers stay stable
def generate_bvp_dataframe():
    fetch_time = datetime.now().strftime("%H:%M:%S")
    st.session_state['last_fetched'] = fetch_time

    with st.spinner("Fetching ALL BvP matchups from MLB API..."):
        game_date = date.today().isoformat()
        games = fetch_schedule(game_date)
        if not games:
            st.error("No games found today.")
            return pd.DataFrame()

        # Use dict to guarantee unique (batter_id, pitcher_id) pairs
        matchup_dict = {}
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
                batters = fetch_roster_batters(bat_team_id)

                for bname, bid in batters:
                    if not bid:
                        continue
                    bvp = fetch_bvp(bid, sp_id)
                    if not bvp:
                        continue
                    key = (bid, sp_id)   # Unique key for this exact matchup
                    matchup_dict[key] = {
                        "Matchup": label,
                        "Batter": bname,
                        "Batter Team": bat_team,
                        "Opposing Pitcher": sp_name,
                        "Pitcher Team": pit_team,
                        "AB": bvp["ab"], "H": bvp["h"], "HR": bvp["hr"],
                        "RBI": bvp["rbi"], "BB": bvp["bb"], "SO": bvp["so"],
                        "AVG": bvp["avg"], "OBP": bvp["obp"],
                        "SLG": bvp["slg"], "OPS": bvp["ops"],
                        "Lineup?": "Projected"
                    }
                count += 1
                progress_bar.progress(min(count / total, 1.0))

        df = pd.DataFrame(matchup_dict.values())
        if not df.empty:
            df = df.sort_values(by="OPS", ascending=False).reset_index(drop=True)
        return df

data = generate_bvp_dataframe()

if 'last_fetched' in st.session_state:
    st.info(f"📅 Data last fetched at: **{st.session_state['last_fetched']}**")

col1, col2 = st.columns([3, 1])
with col1:
    st.subheader("All Batter vs. Pitcher Matchups")
with col2:
    if st.button("🔄 Refresh All Data Now", type="primary"):
        with st.spinner("Pulling fresh data from MLB API..."):
            st.cache_data.clear()
            st.rerun()

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

st.success(f"✅ Showing {len(data)} stable BvP matchups")
st.caption("Note: Career stats are now consistently selected (highest AB count). Numbers should stay the same between refreshes unless MLB itself updates them.")
