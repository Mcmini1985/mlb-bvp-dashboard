"""
MLB Daily BvP Dashboard - Streamlit Front-End App
Displays the Top 30 BvP table from Top_30_batter.xlsx with nice formatting.
"""
import streamlit as st
import pandas as pd
from pathlib import Path
from datetime import date

st.set_page_config(page_title="MLB Daily BvP", page_icon="⚾", layout="wide")
st.title("⚾ MLB Daily Batter vs. Pitcher Matchups")
st.caption(f"Top 30 BvP — Updated {date.today().strftime('%B %d, %Y')}")

# Path to your Excel file
EXCEL_URL = "https://raw.githubusercontent.com/yourusername/mlb-bvp-dashboard/main/Top_30_batter.xlsx"

@st.cache_data(ttl=300)  # Refresh every 5 minutes
def load_data():
    if not EXCEL_PATH.exists():
        st.error("❌ Top_30_batter.xlsx not found on Desktop!")
        st.stop()
    
    # Load the BvP Matchups sheet
    df = pd.read_excel(EXCEL_PATH, sheet_name=None)
    # Try to find the sheet with BvP data
    sheet_name = None
    for name in df.keys():
        if "BvP" in name or "Matchup" in name:
            sheet_name = name
            break
    if not sheet_name:
        sheet_name = list(df.keys())[0]  # fallback to first sheet
    
    data = df[sheet_name]
    
    # Clean column names
    data.columns = [col.strip() for col in data.columns]
    return data

# Load the data
data = load_data()

# Display filters
col1, col2 = st.columns([3, 1])
with col1:
    st.subheader("Top 30 Batter vs. Pitcher Matchups")
with col2:
    if st.button("🔄 Refresh Data Now", type="primary"):
        with st.spinner("Running MLB generator and refreshing data..."):
            # Optional: automatically run your generator script
            import subprocess
            try:
                subprocess.run(["python", "mlb_daily_bvp.py"], cwd=str(EXCEL_PATH.parent), timeout=180, check=True)
                st.success("✅ Data refreshed!")
                st.cache_data.clear()
                st.rerun()
            except Exception as e:
                st.warning(f"Could not auto-refresh generator: {e}")

# Color coding for OPS
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

# Display the table with styling
styled_table = data.style\
    .applymap(color_ops, subset=["OPS"])\
    .set_properties(**{'text-align': 'center'})\
    .set_table_styles([
        {'selector': 'th', 'props': [('background-color', '#1F4E79'), 
                                     ('color', 'white'), 
                                     ('font-weight', 'bold')]}
    ])

st.dataframe(
    styled_table,
    use_container_width=True,
    hide_index=True,
    height=800
)

# Sidebar info
with st.sidebar:
    st.header("About")
    st.write("This dashboard reads the latest data from **Top_30_batter.xlsx**.")
    st.write("Run your Python generator script anytime — the table will update automatically.")
    st.caption("Built with Streamlit for MinhPC")

st.success("✅ Dashboard ready! You can now run this app daily.")