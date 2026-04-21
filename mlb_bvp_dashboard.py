"""
MLB Daily BvP Dashboard - Stable Version with Shared Cache + AI Hit Probability Model
Default filter: AB > 10 and AVG > .250
"""
import streamlit as st
import pandas as pd
import requests
from datetime import datetime
from zoneinfo import ZoneInfo
from concurrent.futures import ThreadPoolExecutor, as_completed
import time

st.set_page_config(page_title="MLB Daily BvP + AI Hit Prob", page_icon="⚾", layout="wide")
st.title("⚾ All Batter vs. Pitcher Matchups + AI Hit Probability")

# ── CONFIGURATION ────────────────────────────────────────────────────────────
BASE = "https://statsapi.mlb.com/api/v1"
CACHE_TTL_SECONDS = 600  # 10 minutes
MAX_WORKERS = 20

def api_get(path, **params):
    """Fetch data from MLB API with retries"""
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

# ── PLAYER DATA FUNCTIONS (unchanged) ───────────────────────────────────────
@st.cache_data(ttl=86400)
def get_player_handedness(player_id):
    data = api_get(f"/people/{player_id}")
    person = data.get("people", [{}])[0]
    bats = person.get("batSide", {}).get("code", "") or person.get("bats", "")
    throws = person.get("pitchHand", {}).get("code", "") or person.get("throws", "")
    batter_hand = {"R": "Right", "L": "Left", "S": "Switch"}.get(bats.upper(), "Unknown")
    pitcher_hand = {"R": "Right", "L": "Left"}.get(throws.upper(), "Unknown")
    return batter_hand, pitcher_hand

@st.cache_data(ttl=86400)
def get_batter_vs_hand(batter_id):
    data = api_get(f"/people/{batter_id}/stats", stats="careerStatSplits",
                   group="hitting", sitCodes="vl,vr", sportId=1)
    l_avg = l_ops = r_avg = r_ops = ".000"
    for stat in data.get("stats", []):
        for split in stat.get("splits", []):
            hand = split.get("split", {}).get("code", "")
            s = split.get("stat", {})
            if hand == "vl":
                l_avg = s.get("avg", ".000")
                l_ops = s.get("ops", ".000")
            elif hand == "vr":
                r_avg = s.get("avg", ".000")
                r_ops = s.get("ops", ".000")
    return l_avg, l_ops, r_avg, r_ops

@st.cache_data(ttl=3600)
def get_recent_batter_stats(batter_id):
    et_now = datetime.now(tz=ZoneInfo("America/New_York"))
    season = et_now.year
    data = api_get(f"/people/{batter_id}/stats", stats="gameLog", group="hitting", season=season)
    games = []
    for sg in data.get("stats", []):
        games.extend(sg.get("splits", []))
    games = sorted(games, key=lambda x: x.get("date", ""), reverse=True)

    # Last 20 AB
    last_20_hits = 0
    last_20_ab = 0
    for game in games:
        if last_20_ab >= 20:
            break
        stat = game.get("stat", {})
        ab = stat.get("atBats", 0)
        hits = stat.get("hits", 0)
        if ab == 0:
            continue
        remaining = 20 - last_20_ab
        if ab <= remaining:
            last_20_ab += ab
            last_20_hits += hits
        else:
            last_20_hits += round(hits * remaining / ab)
            last_20_ab += remaining
    last_20_str = f"{last_20_hits}-{last_20_ab}" if last_20_ab > 0 else "0-0"
    
    # Hitting streak
    current_streak = 0
    for game in games:
        stat = game.get("stat", {})
        ab = stat.get("atBats", 0)
        hits = stat.get("hits", 0)
        if ab > 0:
            if hits > 0:
                current_streak += 1
            else:
                break
    return last_20_str, current_streak

@st.cache_data(ttl=3600)
def fetch_bvp(batter_id, pitcher_id):
    data = api_get(f"/people/{batter_id}/stats", stats="vsPlayer",
                   opposingPlayerId=pitcher_id, sportId=1, group="hitting")
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

# ── NEW: Pitcher WHIP function (added for the probability model) ─────────────
@st.cache_data(ttl=3600)
def get_pitcher_whip(pitcher_id):
    """Current season WHIP for the pitcher (league average fallback ~1.32)"""
    et_now = datetime.now(tz=ZoneInfo("America/New_York"))
    season = et_now.year
    data = api_get(f"/people/{pitcher_id}/stats", stats="season", group="pitching", season=season)
    for sg in data.get("stats", []):
        for split in sg.get("splits", []):
            stat = split.get("stat", {})
            if stat.get("whip"):
                return float(stat.get("whip"))
    return 1.32  # 2026 MLB average approximation

# ── NEW: AI Hit Probability Model (blended formula) ─────────────────────────
def calculate_hit_probability(matchup_ba: float, recent_ba: float,
                              pitcher_whip: float, batter_hand: str, pitcher_hand: str) -> float:
    """
    Simple yet effective model:
    - 60% weight on historical BvP
    - 30% weight on hitter's recent form
    - WHIP adjustment (lower WHIP = harder for hitter)
    - Platoon (opposite-handed) bonus
    Velocity_diff omitted for speed (can be added later via Statcast).
    """
    # Base blend
    base = (matchup_ba * 0.60) + (recent_ba * 0.30)
    
    # Pitcher effectiveness adjustment
    whip_adjust = (1.32 - pitcher_whip) * 0.25
    
    # Platoon advantage
    is_opposite = False
    if batter_hand == "Switch":
        is_opposite = True
    elif batter_hand and pitcher_hand:
        is_opposite = batter_hand[0] != pitcher_hand[0]
    platoon_bonus = 0.018 if is_opposite else -0.012
    
    prob = base + whip_adjust + platoon_bonus
    
    # Clamp to realistic range
    prob = max(0.12, min(0.48, prob))
    return round(prob * 100, 1)

# ── VS TEAM & VS BULLPEN (unchanged) ────────────────────────────────────────
@st.cache_data(ttl=86400)
def get_batter_vs_team(batter_id, opp_team_id):
    data = api_get(f"/people/{batter_id}/stats", stats="vsTeam",
                   opposingTeamId=opp_team_id, group="hitting", sportId=1)
    avg = ops = ".000"
    for stat in data.get("stats", []):
        for split in stat.get("splits", []):
            s = split.get("stat", {})
            avg = s.get("avg", ".000")
            ops = s.get("ops", ".000")
    return avg, ops

@st.cache_data(ttl=86400)
def get_team_bullpen(team_id):
    et_now = datetime.now(tz=ZoneInfo("America/New_York"))
    data = api_get(f"/teams/{team_id}/roster", rosterType="active", season=et_now.year)
    relievers = []
    for p in data.get("roster", []):
        pos = p.get("position", {})
        if pos.get("type", "") == "Pitcher" and pos.get("abbreviation", "") != "SP":
            pid = p.get("person", {}).get("id")
            if pid:
                relievers.append(pid)
    return relievers

@st.cache_data(ttl=86400)
def get_batter_vs_bullpen(batter_id, opp_team_id):
    relievers = get_team_bullpen(opp_team_id)
    if not relievers:
        return ".000", ".000"
    total_ab = total_h = total_bb = total_tb = 0

    def fetch_vs_reliever(pid):
        data = api_get(f"/people/{batter_id}/stats", stats="vsPlayer",
                       opposingPlayerId=pid, sportId=1, group="hitting")
        for sg in data.get("stats", []):
            for split in sg.get("splits", []):
                s = split.get("stat", {})
                ab = s.get("atBats", 0)
                if ab > 0:
                    return (
                        ab,
                        s.get("hits", 0),
                        s.get("baseOnBalls", 0),
                        s.get("hitByPitch", 0),
                        s.get("sacFlies", 0),
                        s.get("hits", 0) + s.get("doubles", 0) + 2 * s.get("triples", 0) + 3 * s.get("homeRuns", 0),
                    )
        return (0, 0, 0, 0, 0, 0)

    with ThreadPoolExecutor(max_workers=10) as ex:
        futures = {ex.submit(fetch_vs_reliever, pid): pid for pid in relievers}
        for future in as_completed(futures):
            ab, h, bb, hbp, sf, tb = future.result()
            total_ab += ab
            total_h += h
            total_bb += bb
            total_tb += tb

    if total_ab == 0:
        return ".000", ".000"
    avg = total_h / total_ab
    obp = (total_h + total_bb) / (total_ab + total_bb) if (total_ab + total_bb) > 0 else 0
    slg = total_tb / total_ab if total_ab > 0 else 0
    ops = obp + slg
    return f"{avg:.3f}", f"{ops:.3f}"

# ── SCHEDULE / ROSTER FUNCTIONS (unchanged) ─────────────────────────────────
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
    et_now = datetime.now(tz=ZoneInfo("America/New_York"))
    data = api_get(f"/teams/{team_id}/roster", rosterType="active", season=et_now.year)
    batters = []
    for p in data.get("roster", []):
        if p.get("position", {}).get("type", "") != "Pitcher":
            person = p.get("person", {})
            batters.append((person.get("fullName", "?"), person.get("id")))
    return batters

# ── PER-BATTER WORKER (UPDATED with probability model) ───────────────────────
def process_batter(bname, bid, sp_name, sp_id, bat_team, pit_team, pit_team_id, label, pitcher_hand):
    """All API calls for a single batter + AI hit probability calculation"""
    bvp = fetch_bvp(bid, sp_id)
    if not bvp:
        return None

    last_20_ab, streak = get_recent_batter_stats(bid)
    batter_hand, _ = get_player_handedness(bid)
    l_avg, l_ops, r_avg, r_ops = get_batter_vs_hand(bid)
    vs_team_avg, vs_team_ops = get_batter_vs_team(bid, pit_team_id)
    vs_bp_avg, vs_bp_ops = get_batter_vs_bullpen(bid, pit_team_id)

    # ── AI Hit Probability (new) ─────────────────────────────────────────────
    pitcher_whip = get_pitcher_whip(sp_id)
    
    # Parse matchup BA
    matchup_ba = float(bvp.get("avg", 0.250))
    
    # Parse recent form
    recent_ba = 0.265
    if last_20_ab and "-" in last_20_ab:
        try:
            hits_str, ab_str = last_20_ab.split("-")
            hits = int(hits_str)
            ab = int(ab_str)
            recent_ba = hits / ab if ab > 0 else 0.265
        except:
            pass
    
    hit_prob = calculate_hit_probability(
        matchup_ba=matchup_ba,
        recent_ba=recent_ba,
        pitcher_whip=pitcher_whip,
        batter_hand=batter_hand,
        pitcher_hand=pitcher_hand
    )

    return {
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
        "vs Team (AVG/OPS)": f"{vs_team_avg} / {vs_team_ops}",
        "vs Bullpen (AVG/OPS)": f"{vs_bp_avg} / {vs_bp_ops}",
        "AB": bvp["ab"], "H": bvp["h"], "HR": bvp["hr"],
        "RBI": bvp["rbi"], "BB": bvp["bb"], "SO": bvp["so"],
        "AVG": bvp["avg"], "OBP": bvp["obp"],
        "SLG": bvp["slg"], "OPS": bvp["ops"],
        "Est. Hit %": hit_prob,          # ← NEW AI column
        "Lineup?": "Projected"
    }

# ── GENERATE BVP DATAFRAME (unchanged core logic) ───────────────────────────
@st.cache_data(ttl=CACHE_TTL_SECONDS)
def generate_bvp_dataframe():
    et_now = datetime.now(tz=ZoneInfo("America/New_York"))
    st.session_state['last_fetched'] = et_now.strftime("%H:%M:%S")
    game_date = et_now.date().isoformat()
    games = fetch_schedule(game_date)
    if not games:
        st.error(f"No games found for {game_date}.")
        return pd.DataFrame()

    tasks = []
    pitcher_hand_cache = {}
    for g in games:
        home_name, home_id = team_info(g, "home")
        away_name, away_id = team_info(g, "away")
        label = f"{away_name} @ {home_name}"
        h_sp, h_sp_id = probable_pitcher(g, "home")
        a_sp, a_sp_id = probable_pitcher(g, "away")
        sides = [
            (away_name, away_id, h_sp, h_sp_id, home_name, home_id),
            (home_name, home_id, a_sp, a_sp_id, away_name, away_id),
        ]
        for bat_team, bat_team_id, sp_name, sp_id, pit_team, pit_team_id in sides:
            if not sp_id:
                continue
            if sp_id not in pitcher_hand_cache:
                _, p_hand = get_player_handedness(sp_id)
                pitcher_hand_cache[sp_id] = p_hand
            pitcher_hand = pitcher_hand_cache[sp_id]
            batters = fetch_roster_batters(bat_team_id)
            for bname, bid in batters:
                if bid:
                    tasks.append((bname, bid, sp_name, sp_id,
                                  bat_team, pit_team, pit_team_id, label, pitcher_hand))

    progress_bar = st.progress(0)
    total = len(tasks)
    results = {}
    completed = 0
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        future_to_key = {executor.submit(process_batter, *t): (t[1], t[3]) for t in tasks}
        for future in as_completed(future_to_key):
            key = future_to_key[future]
            try:
                row = future.result()
                if row:
                    results[key] = row
            except Exception:
                pass
            completed += 1
            progress_bar.progress(min(completed / total, 1.0))

    df = pd.DataFrame(results.values())
    if not df.empty:
        df["AVG"] = pd.to_numeric(df["AVG"], errors="coerce")
        df = df[(df["AB"] > 10) & (df["AVG"] > 0.250)]
        df = df.sort_values(by="OPS", ascending=False).reset_index(drop=True)
    return df

# ── FETCH DATA ──────────────────────────────────────────────────────────────
data = generate_bvp_dataframe()
if 'last_fetched' in st.session_state:
    st.info(f"📅 Data last fetched at: **{st.session_state['last_fetched']} ET**")

# ── FILTERS (unchanged) ─────────────────────────────────────────────────────
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

# ── DISPLAY ─────────────────────────────────────────────────────────────────
col1, col2 = st.columns([3, 1])
with col1:
    st.subheader("All Batter vs. Pitcher Matchups + AI Hit Probability")
with col2:
    if st.button("🔄 Refresh All Data Now", type="primary"):
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

def color_hit_prob(val):
    try:
        v = float(val)
        if v >= 35:
            return "background-color: #C6EFCE; color: #006100"
        elif v >= 28:
            return "background-color: #FFEB9C; color: #9C6500"
        else:
            return "background-color: #FFC7CE; color: #9C0006"
    except:
        return ""

styled = filtered_data.style\
    .map(color_ops, subset=["OPS"])\
    .map(color_hit_prob, subset=["Est. Hit %"])\
    .set_properties(**{'text-align': 'center'})\
    .set_table_styles([{'selector': 'th', 'props': [('background-color', '#1F4E79'),
                                                    ('color', 'white'),
                                                    ('font-weight', 'bold')]}])

st.dataframe(styled, use_container_width=True, hide_index=True, height=900)
st.success(f"✅ Showing {len(filtered_data)} matchups (AB > 10 and AVG > .250) | AI Hit Probability model active")
st.caption("**Est. Hit %** = blended probability of a hit in this matchup (historical BvP + recent form + pitcher WHIP + platoon advantage).")