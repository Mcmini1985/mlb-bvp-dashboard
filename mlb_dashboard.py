"""
MLB Daily BvP Dashboard - Stable Version with Shared Cache + AI Hit Probability Model v3.0
Default filter: AB > 10 and AVG > .250
Statcast metrics (xBA, hard-hit%, avg EV) from Baseball Savant + pybaseball for pitcher velo
"""
import streamlit as st
import pandas as pd
import requests
import io
from datetime import datetime
from zoneinfo import ZoneInfo
from concurrent.futures import ThreadPoolExecutor, as_completed
import time

# ── NEW: pybaseball for reliable pitcher velocity ─────────────────────────────
from pybaseball import statcast_pitcher_pitch_arsenal

st.set_page_config(page_title="MLB Daily BvP + AI Hit Prob v3", page_icon="⚾", layout="wide")
st.title("⚾ All Batter vs. Pitcher Matchups + AI Hit Probability v3.0")

# ── CONFIGURATION ────────────────────────────────────────────────────────────
BASE = "https://statsapi.mlb.com/api/v1"
CACHE_TTL_SECONDS = 600
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

# ── BASEBALL SAVANT STATCAST LEADERBOARDS (unchanged) ────────────────────────
@st.cache_data(ttl=86400)
def fetch_savant_batter_data():
    """Improved fetch with proper headers (fixes connection refused)."""
    et_now = datetime.now(tz=ZoneInfo("America/New_York"))
    season = et_now.year
    url = (
        f"https://baseballsavant.mlb.com/leaderboard/custom"
        f"?year={season}&type=batter&filter=&min=5"
        f"&selections=xba,hard_hit_percent,exit_velocity_avg&csv=true"
    )
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36"
    }
    try:
        resp = requests.get(url, headers=headers, timeout=20)
        resp.raise_for_status()
        df = pd.read_csv(io.StringIO(resp.text))
        df = df.rename(columns={"player_id": "mlb_id"})
        df["mlb_id"] = pd.to_numeric(df["mlb_id"], errors="coerce").astype("Int64")
        if "hard_hit_percent" in df.columns:
            df["hard_hit_percent"] = pd.to_numeric(df["hard_hit_percent"], errors="coerce") / 100.0
        if "xba" in df.columns:
            df["xba"] = pd.to_numeric(df["xba"], errors="coerce")
        if "exit_velocity_avg" in df.columns:
            df["exit_velocity_avg"] = pd.to_numeric(df["exit_velocity_avg"], errors="coerce")
        return df.set_index("mlb_id").to_dict("index")
    except Exception as e:
        st.warning(f"⚠️ Could not load batter Statcast data from Baseball Savant: {str(e)[:80]}")
        return {}

@st.cache_data(ttl=86400)
def fetch_savant_pitcher_data():
    """Improved fetch with proper headers (fixes connection refused)."""
    et_now = datetime.now(tz=ZoneInfo("America/New_York"))
    season = et_now.year
    url = (
        f"https://baseballsavant.mlb.com/leaderboard/custom"
        f"?year={season}&type=pitcher&filter=&min=5"
        f"&selections=effective_speed&csv=true"
    )
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36"
    }
    try:
        resp = requests.get(url, headers=headers, timeout=20)
        resp.raise_for_status()
        df = pd.read_csv(io.StringIO(resp.text))
        df = df.rename(columns={"player_id": "mlb_id"})
        df["mlb_id"] = pd.to_numeric(df["mlb_id"], errors="coerce").astype("Int64")
        if "effective_speed" in df.columns:
            df["effective_speed"] = pd.to_numeric(df["effective_speed"], errors="coerce")
        return df.set_index("mlb_id").to_dict("index")
    except Exception as e:
        st.warning(f"⚠️ Could not load pitcher Statcast data from Baseball Savant: {str(e)[:80]}")
        return {}

# ── NEW: pybaseball for Pitcher Velocity (Primary Source) ─────────────────────
@st.cache_data(ttl=3600)
def get_pitcher_velo_pybaseball(pitcher_id: int) -> float | None:
    """Primary source for pitcher velocity using pybaseball."""
    try:
        df = statcast_pitcher_pitch_arsenal(2026, player_id=pitcher_id)
        if not df.empty and "average_speed" in df.columns:
            fb_row = df[df["pitch_type"] == "FF"]
            if not fb_row.empty:
                return round(float(fb_row["average_speed"].iloc[0]), 1)
            return round(float(df["average_speed"].max()), 1)
    except Exception:
        pass
    return None

def get_pitcher_velo(pitcher_id: int, savant_pitchers: dict) -> float:
    """Try pybaseball first → Savant → 94.6 mph fallback (2026 average)."""
    velo = get_pitcher_velo_pybaseball(pitcher_id)
    if velo is not None:
        return velo
    # Fallback to Savant
    row = savant_pitchers.get(pitcher_id, {})
    velo = row.get("effective_speed")
    if velo is not None and not pd.isna(velo):
        return float(velo)
    return 94.6

# ── PITCHER WHIP FROM MLB STATS API ──────────────────────────────────────────
@st.cache_data(ttl=3600)
def get_pitcher_season_whip(pitcher_id: int) -> float:
    et_now = datetime.now(tz=ZoneInfo("America/New_York"))
    season = et_now.year
    data = api_get(f"/people/{pitcher_id}/stats", stats="season", group="pitching", season=season)
    for sg in data.get("stats", []):
        for split in sg.get("splits", []):
            stat = split.get("stat", {})
            whip = stat.get("whip")
            if whip:
                return float(whip)
    return 1.32

# ── PLAYER DATA FUNCTIONS (unchanged) ────────────────────────────────────────
# ... [All your original functions from get_batter_statcast onward remain exactly the same until process_batter]

def get_batter_statcast(batter_id: int, savant_batters: dict) -> tuple[float, float, float]:
    row = savant_batters.get(batter_id, {})
    xba = row.get("xba")
    hard_hit = row.get("hard_hit_percent")
    avg_ev = row.get("exit_velocity_avg")
    return (
        float(xba) if xba is not None and not pd.isna(xba) else 0.250,
        float(hard_hit) if hard_hit is not None and not pd.isna(hard_hit) else 0.38,
        float(avg_ev) if avg_ev is not None and not pd.isna(avg_ev) else 88.5,
    )

# ── PER-BATTER WORKER (updated to use pybaseball) ────────────────────────────
def process_batter(bname, bid, sp_name, sp_id, bat_team, pit_team, pit_team_id, label,
                   pitcher_hand, savant_batters, savant_pitchers):
    bvp = fetch_bvp(bid, sp_id)
    if not bvp:
        return None
    last_20_ab, streak = get_recent_batter_stats(bid)
    batter_hand, _ = get_player_handedness(bid)
    l_avg, l_ops, r_avg, r_ops = get_batter_vs_hand(bid)
    vs_team_avg, vs_team_ops = get_batter_vs_team(bid, pit_team_id)
    vs_bp_avg, vs_bp_ops = get_batter_vs_bullpen(bid, pit_team_id)

    # Statcast from Baseball Savant
    batter_xba, batter_hard_hit_pct, batter_avg_ev = get_batter_statcast(bid, savant_batters)
    # Pitcher velocity now uses pybaseball as primary
    pitcher_velo = get_pitcher_velo(sp_id, savant_pitchers)

    pitcher_whip = get_pitcher_season_whip(sp_id)

    matchup_ba = float(bvp.get("avg", 0.250))
    recent_ba = 0.265
    if last_20_ab and "-" in last_20_ab:
        try:
            hits_str, ab_str = last_20_ab.split("-")
            hits = int(hits_str)
            ab = int(ab_str)
            recent_ba = hits / ab if ab > 0 else 0.265
        except Exception:
            pass

    hit_prob = calculate_hit_probability_v3(
        matchup_ba=matchup_ba,
        recent_ba=recent_ba,
        pitcher_whip=pitcher_whip,
        pitcher_velo=pitcher_velo,
        batter_xba=batter_xba,
        batter_hard_hit_pct=batter_hard_hit_pct,
        batter_hand=batter_hand,
        pitcher_hand=pitcher_hand,
        batter_avg_ev=batter_avg_ev
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
        "xBA": round(batter_xba, 3),
        "Hard-Hit %": round(batter_hard_hit_pct * 100, 1),
        "Avg EV (mph)": round(batter_avg_ev, 1),
        "Pitcher Velo (mph)": round(pitcher_velo, 1),
        "Est. Hit % (v3)": hit_prob,
        "Lineup?": "Projected"
    }

# ── GENERATE BVP DATAFRAME, FILTERS, DISPLAY (unchanged from your script) ─────
# [The rest of your original script from generate_bvp_dataframe to the end remains exactly the same]

# ── GENERATE BVP DATAFRAME ────────────────────────────────────────────────────
@st.cache_data(ttl=CACHE_TTL_SECONDS)
def generate_bvp_dataframe():
    et_now = datetime.now(tz=ZoneInfo("America/New_York"))
    st.session_state['last_fetched'] = et_now.strftime("%H:%M:%S")
    game_date = et_now.date().isoformat()
    games = fetch_schedule(game_date)
    if not games:
        st.error(f"No games found for {game_date}.")
        return pd.DataFrame()
    with st.spinner("Loading Statcast data from Baseball Savant..."):
        savant_batters = fetch_savant_batter_data()
        savant_pitchers = fetch_savant_pitcher_data()
    savant_batter_count = len(savant_batters)
    savant_pitcher_count = len(savant_pitchers)
    tasks = []
    pitcher_hand_cache = {}
    for g in games:
        home_name, home_id = team_info(g, "home")
        away_name, away_id = team_info(g, "away")
        label = f"{away_name} @ {home_name}"
        h_sp, h_sp_id = probable_pitcher(g, "home")
        a_sp, a_sp_id = probable_pitcher(g, "away")
        sides = [(away_name, away_id, h_sp, h_sp_id, home_name, home_id),
                 (home_name, home_id, a_sp, a_sp_id, away_name, away_id)]
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
                                  bat_team, pit_team, pit_team_id, label,
                                  pitcher_hand, savant_batters, savant_pitchers))
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
        df = df.sort_values(by="Est. Hit % (v3)", ascending=False).reset_index(drop=True)
    if not df.empty and savant_batter_count > 0:
        real_xba = (df["xBA"] != 0.250).sum()
        st.info(f"📊 Statcast coverage: {real_xba}/{len(df)} batters matched in Baseball Savant leaderboard ({savant_batter_count} total in Savant, {savant_pitcher_count} pitchers).")
    return df

# ── FETCH DATA ────────────────────────────────────────────────────────────────
data = generate_bvp_dataframe()
if 'last_fetched' in st.session_state:
    st.info(f"📅 Data last fetched at: **{st.session_state['last_fetched']} ET**")

# ── FILTERS, DISPLAY, COLORING (unchanged) ───────────────────────────────────
# [The rest of your original script from the sidebar filters to the end is unchanged]

st.success(f"✅ Showing {len(filtered_data)} matchups (AB > 10 and AVG > .250) | AI Hit Probability v3.0 active | Pitcher velo from pybaseball")
st.caption("""
**Est. Hit % (v3)** = optimized probability using:
30% xBA + 20% BvP + 15% recent form + 15% hard-hit/exit velocity + 8% WHIP + 5% velocity differential + 2% platoon.
Statcast metrics (xBA, Hard-Hit%, Avg EV) sourced from **Baseball Savant**.  
Pitcher velocity sourced from **pybaseball** (primary) with Savant fallback.
WHIP sourced from **MLB Stats API**.
""")