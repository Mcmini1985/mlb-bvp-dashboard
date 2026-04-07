"""
MLB Daily BvP Dashboard - Clean & Stable Version
Default filter: AB > 20 and AVG > .250
"""
import streamlit as st
import pandas as pd
import requests
from datetime import date, datetime
import time

st.set_page_config(page_title="MLB Daily BvP", page_icon="⚾", layout="wide")

st.title("⚾ All Batter vs. Pitcher Matchups")
st.caption(f"Full BvP Data — Live from MLB API • {date.today().strftime('%B %d, %Y')}")

# ── CONFIGURATION ────────────────────────────────────────────────────────────
BASE = "https://statsapi.mlb.com/api/v1"

@st.cache_data(ttl=86400)
def get_player_handedness(player_id):
    data = api_get(f"/people/{player_id}")
    person = data.get("people", [{}])[0]
    bats = person.get("bats", "")
    throws = person.get("throws", "")
    batter_hand = {"R": "Right", "L": "Left", "S": "Switch"}.get(bats.upper(), "Unknown")
    pitcher_hand = {"R": "Right", "L": "Left"}.get(throws.upper(), "Unknown")
    return batter_hand, pitcher_hand

@st.cache_data(ttl=86400)
def get_batter_vs_hand(batter_id):
    vs_l = api_get(f"/people/{batter_id}/stats", stats="career", group="hitting", opposingPlayerHand="L")
    vs_r = api_get(f"/people/{batter_id}/stats", stats="career", group="hitting", opposingPlayerHand="R")
    
    l_avg = l_ops = ".000"
    r_avg = r_ops = ".000"
    for stat in vs_l.get("stats", []):
        for split in stat.get("splits", []):
            s = split.get("stat", {})
            l_avg = s.get("avg", ".000")
            l_ops = s.get("ops", ".000")
    for stat in vs_r.get("stats", []):
        for split in stat.get("splits", []):
            s = split.get("stat", {})
            r_avg = s.get("avg", ".000")
            r_ops = s.get("ops", ".000")
    return l_avg, l_ops, r_avg, r_ops

@st.cache_data(ttl=3600)
def get_recent_batter_stats(batter_id):
    season = date.today().year
    data = api_get(f"/people/{batter_id}/stats", stats="gameLog", group="hitting", season=season)
    last_20_hits = 0
    last_20_ab = 0
    streak = 0
    current_streak = 0

    games = []
    for sg in data.get("stats", []):
        games.extend(sg.get("splits", []))

    games = sorted(games, key=lambda x: x.get("date", ""), reverse=True)

    for game in games:
        stat = game.get("stat", {})
        ab = stat.get("atBats", 0)
        hits = stat.get("hits", 0)

        if last_20_ab < 20:
            needed = 20 - last_20_ab
            add_ab = min(ab, needed)
            last_20_ab += add_ab
            last_20_hits += min(hits, add_ab)

        if hits > 0:
            current_streak += 1
        else:
            current_streak = 0
        streak = max(streak, current_streak)

    last_20_str = f"{last_20_hits}-{last_20_ab}" if last_20_ab > 0 else "0-0"
    return last_20_str, streak

def api_get(path, **params):
    for attempt in range(1, 4):
        try:
            r = requests.get(BASE + path, params=params, timeout=30)
            r.raise_for_status()
            return r.json()
        except Exception:
            if attempt == 3:
                return {}
            time.sleep(2 ** attempt)
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
    data = api_get(f"/people/{batter_id}/stats", stats="vsPlayer", opposingPlayerId=pitcher_id,
                   sportId=1, group="hitting")
    best = None
    max_ab = -1
    for sg in data.get("stats", []):
        for split in sg.get("splits", []):
            stat = split.get("stat", {})
            ab = stat.get("atBats", 0)
            if ab > max_ab:
                max_ab = ab
                best = stat
    if best is None or max_ab == 0:
        return None
    return {
        "ab": best.get("atBats", 0),
        "h": best.get("hits", 0),
        "hr": best.get("homeRuns", 0),
        "rbi": best.get("rbi", 0),
        "bb": best.get("baseOnBalls", 0),
        "so": best.get("strikeOuts", 0),
        "avg": best.get("avg", ".000"),
        "obp": best.get("obp", ".000"),
        "slg": best.get("slg", ".000"),
        "ops": best.get("ops", ".000")
    }

@st.cache_data(ttl=1800)
def generate_bvp_dataframe():
    fetch_time = datetime.now().strftime("%H:%M:%S")
    st.session_state['last_fetched'] = fetch_time

    with st.spinner("Fetching ALL BvP matchups..."):
        game_date = date.today().isoformat()
        games = fetch_schedule(game_date)
        if not games:
            st.error("No games found today.")
            return pd.DataFrame()

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
                    
                    last_20_ab, streak = get_recent_batter_stats(bid)
                    batter_hand, pitcher_hand = get_player_handedness(bid)
                    l_avg, l_ops, r_avg, r_ops = get_batter_vs_hand(bid)
                    
                    key = (bid, sp_id)
                    matchup_dict[key] = {
                        "Matchup": label,
                        "Batter": bname,
                        "Batter Hand": batter_hand,
                        "Batter Team": bat_team,
                        "Opposing Pitcher": sp_name,
                        "Pitcher Hand": pitcher_hand,
                        "Pitcher Team": pit_team,
                        "Last 20 AB": last_20_ab,
                        "Hitting Streak": streak,
                        "Batter vs LHP": f"{l_avg} / {l_ops}",
                        "Batter vs RHP": f"{r_avg} / {r_ops}",
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
            df["AVG"] = pd.to_numeric(df["AVG"], errors="coerce")
            df = df[(df["AB"] > 20) & (df["AVG"] > 0.250)]
            df = df.sort_values(by="OPS", ascending=False).reset_index(drop=True)
        return df

data = generate_bvp_dataframe()

if 'last_fetched' in st.session_state:
    st.info(f"📅 Data last fetched at: **{st.session_state['last_fetched']}**")

# ── FILTERS ─────────────────────────────────────────────────────────────────
st.sidebar.header("🔎 Filters")

batter_search = st.sidebar.text_input("Search Batter", "")

batter_teams = sorted(data["Batter Team"].unique()) if not data.empty else []
pitcher_teams = sorted(data["Pitcher Team"].unique()) if not data.empty else []
batter_hands = sorted(data["Batter Hand"].unique()) if not data.empty else []
pitcher_hands = sorted(data["Pitcher Hand"].unique()) if not data.empty else []

selected_batter_team = st.sidebar.multiselect("Batter Team", options=batter_teams, default=[])
selected_pitcher_team = st.sidebar.multiselect("Pitcher Team", options=pitcher_teams, default=[])
selected_batter_hand = st.sidebar.multiselect("Batter Hand", options=batter_hands, default=[])
selected_pitcher_hand = st.sidebar.multiselect("Pitcher Hand", options=pitcher_hands, default=[])

# Apply filters
filtered_data = data.copy()
if batter_search:
    filtered_data = filtered_data[filtered_data["Batter"].str.contains(batter_search, case=False, na=False)]
if selected_batter_team:
    filtered_data = filtered_data[filtered_data["Batter Team"].isin(selected_batter_team)]
if selected_pitcher_team:
    filtered_data = filtered_data[filtered_data["Pitcher Team"].isin(selected_pitcher_team)]
if selected_batter_hand:
    filtered_data = filtered_data[filtered_data["Batter Hand"].isin(selected_batter_hand)]
if selected_pitcher_hand:
    filtered_data = filtered_data[filtered_data["Pitcher Hand"].isin(selected_pitcher_hand)]

# ── Display ─────────────────────────────────────────────────────────────────
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

styled = filtered_data.style\
    .map(color_ops, subset=["OPS"])\
    .set_properties(**{'text-align': 'center'})\
    .set_table_styles([{'selector': 'th', 'props': [('background-color', '#1F4E79'), 
                                                    ('color', 'white'), 
                                                    ('font-weight', 'bold')]}])

st.dataframe(styled, use_container_width=True, hide_index=True, height=900)

st.success(f"✅ Showing {len(filtered_data)} matchups (AB > 20 and AVG > .250)")