"""
MLB Daily BvP Dashboard - Public Web App (Fixed for Streamlit Cloud)
"""
import streamlit as st
import pandas as pd
import requests
from io import BytesIO
from datetime import date

st.set_page_config(page_title="MLB Daily BvP", page_icon="⚾", layout="wide")

st.title("⚾ MLB Daily Batter vs. Pitcher Matchups")
st.caption(f"Top 30 BvP — Updated {date.today().strftime('%B %d, %Y')}")

# Your public GitHub Excel file
EXCEL_URL = "https://raw.githubusercontent.com/Mcmini1985/mlb-bvp-dashboard/main/Top_30_batter.xlsx"

@st.cache_data(ttl=300)
def load_data():
    try:
        response = requests.get(EXCEL_URL, timeout=15)
        response.raise_for_status()
        excel_data = BytesIO(response.content)
        
        df_dict = pd.read_excel(excel_data, sheet_name=None)
        
        # Find the correct sheet
        sheet_name = None
        for name in df_dict.keys():
            if "BvP" in name or "Matchup" in name:
                sheet_name = name
                break
        if not sheet_name:
            sheet_name = list(df_dict.keys())[0]
        
        data = df_dict[sheet_name].copy()
        data.columns = [str(col).strip() for col in data.columns]
        return data
    except Exception as e:
        st.error(f"❌ Could not load Excel file from GitHub:\n{e}")
        st.stop()

data = load_data()

# Header + Refresh button
col1, col2 = st.columns([3, 1])
with col1:
    st.subheader("Top 30 Batter vs. Pitcher Matchups")
with col2:
    if st.button("🔄 Refresh Data Now", type="primary"):
        with st.spinner("Fetching latest data from GitHub..."):
            st.cache_data.clear()
            st.rerun()

# OPS color coding (fixed for new pandas)
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

# Styled table - FIXED: use .map() instead of deprecated .applymap()
styled_table = data.style\
    .map(color_ops, subset=["OPS"])\
    .set_properties(**{'text-align': 'center'})\
    .set_table_styles([{'selector': 'th', 'props': [('background-color', '#1F4E79'), 
                                                    ('color', 'white'), 
                                                    ('font-weight', 'bold')]}])

st.dataframe(
    styled_table,
    use_container_width=True,
    hide_index=True,
    height=800
)

# Sidebar
with st.sidebar:
    st.header("How to Update")
    st.write("1. Run your Python generator script (`mlb_daily_bvp.py`)")
    st.write("2. Upload the new `Top_30_batter.xlsx` to GitHub")
    st.write("3. Click **Refresh Data Now** above")
    st.divider()
    st.caption("Dashboard by MinhPC • Public link ready to share")

st.success("✅ Dashboard is live and working!")