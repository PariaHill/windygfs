import streamlit as st
import requests
import pandas as pd
import math
from datetime import datetime, timedelta
import plotly.graph_objects as go
from plotly.subplots import make_subplots

# 1. 페이지 설정
st.set_page_config(page_title="Windy Marine Forecast", layout="wide")

# 2. 인쇄 최적화 CSS (단축키 인쇄 시 적용)
st.markdown("""
    <style>
    @media print {
        /* 인쇄 시 불필요한 UI 완전 제거 */
        section[data-testid="stSidebar"], 
        .stButton, .stSelectbox, .stNumberInput, 
        header, [data-testid="stHeader"], [role="tablist"],
        footer, [data-testid="stFooter"], .stSpinner {
            display: none !important;
        }
        /* A4 출력을 위해 테이블과 그래프를 강제로 세로 나열 */
        .main .block-container { padding: 0 !important; margin: 0 !important; }
        div[data-testid="stVerticalBlock"] > div { page-break-inside: avoid !important; }
        table { font-size: 11px !important; width: 100% !important; border-collapse: collapse; }
        th, td { border: 1px solid #ddd !important; padding: 4px !important; }
        .js-plotly-plot { height: 600px !important; width: 100% !important; }
    }
    </style>
    """, unsafe_allow_html=True)

# 3. 세션 및 API 설정
if 'lat' not in st.session_state: st.session_state.lat = 31.8700
if 'lon' not in st.session_state: st.session_state.lon = 126.7700
if 'offset' not in st.session_state: st.session_state.offset = 9

API_KEY = st.secrets["WINDY_API_KEY"]
BASE_URL = "https://api.windy.com/api/point-forecast/v2"
MS_TO_KNOTS = 1.94384

# 4. 유틸리티 함수
def get_direction_text(deg):
    directions = ['N', 'NNE', 'NE', 'ENE', 'E', 'ESE', 'SE', 'SSE', 'S', 'SSW', 'SW', 'WSW', 'W', 'WNW', 'NW', 'NNW']
    idx = int((deg + 11.25) / 22.5) % 16
    return directions[idx]

def get_arrow_html(deg, color="#007BFF"):
    return f'<span style="display:inline-block; transform:rotate({deg}deg); font-size:16px; color:{color};">↑</span>'

# 5. UI 상단
st.title("⚓ 해상 기상 관측 리포트")

with st.container():
    col1, col2, col3, col4 = st.columns([2, 2, 2, 1])
    with col1: st.session_state.lat = st.number_input("위도 (Lat)", value=st.session_state.lat, format="%.4f")
    with col2: st.session_state.lon = st.number_input("경도 (Lon)", value=st.session_state.lon, format="%.4f")
    with col3:
        opts = list(range(13, -13, -1))
        st.session_state.offset = st.selectbox("시간대 (UTC)", options=opts, index=opts.index(st.session_state.offset))
    with col4:
        st.write(" ")
        fetch_btn = st.button("수신 시작", use_container_width=True)

# 6. 데이터 렌더링
if fetch_btn or 'data_loaded' in st.session_state:
    st.session_state.data_loaded = True
    
    # API 요청 및 가공 (중략 - 기존 로직 유지)
    r_gfs = requests.post(BASE_URL, json={"lat": st.session_state.lat, "lon": st.session_state.lon, "model": "gfs", "parameters": ["pressure", "wind", "windGust"], "levels": ["surface"]*3, "key": API_KEY})
    r_wave = requests.post(BASE_URL, json={"lat": st.session_state.lat, "lon": st.session_state.lon, "model": "gfsWave", "parameters": ["waves", "swell1"], "levels": ["surface"]*2, "key": API_KEY})

    if r_gfs.status_code == 200 and r_wave.status_code == 200:
        data_gfs, data_wave = r_gfs.json(), r_wave.json()
        limit = 56
        times = [datetime.fromtimestamp(t/1000) + timedelta(hours=(st.session_state.offset - 9)) for t in data_gfs.get('ts', [])[:limit]]
        time_col = f"Time (UTC{st.session_state.offset:+} )"

        df = pd.DataFrame({
            time_col: times,
            "Pressure(hPa)": [round(p/100, 1) for p in data_gfs.get('pressure-surface', [])[:limit]],
            "Wind_U": data_gfs.get('wind_u-surface', [])[:limit], "Wind_V": data_gfs.get('wind_v-surface', [])[:limit],
            "Gust(kts)": [round(g * MS_TO_KNOTS, 1) for g in (lambda d: [x if x is not None else 0.0 for x in d])(data_gfs.get('gust-surface', [])[:limit])],
            "Waves(m)": [round(w, 1) for w in (lambda d: [x if x is not None else 0.0 for x in d])(data_wave.get('waves_height-surface', [])[:limit])],
            "Wave_Deg": (lambda d: [x if x is not None else 0.0 for x in d])(data_wave.get('waves_direction-surface', [])[:limit]),
            "Swell(m)": [round(s, 1) for s in (lambda d: [x if x is not None else 0.0 for x in d])(data_wave.get('swell1_height-surface', [])[:limit])]
        })
        df['Wind Speed(kts)'] = (((df['Wind_U']**2 + df['Wind_V']**2)**0.5) * MS_TO_KNOTS).round(1)
        df['Wind_Deg'] = df.apply(lambda row: (math.degrees(math.atan2(row['Wind_U'], row['Wind_V'])) + 180) % 360, axis=1)
        df['Wind Direction'] = df.apply(lambda r: f"{r['Wind_Deg']:.1f}° {get_direction_text(r['Wind_Deg'])} {get_arrow_html(r['Wind_Deg'])}", axis=1)
        df['Wave Direction'] = df.apply(lambda r: f"{r['Wave_Deg']:.1f}° {get_direction_text(r['Wave_Deg'])} {get_arrow_html(r['Wave_Deg'], '#28A745')}", axis=1)

        # 7. 레이아웃 배치 (인쇄를 고려하여 위아래로 배치)
        st.subheader("📊 해상 예보 데이터 테이블")
        st.write(df[[time_col, "Pressure(hPa)", "Wind Direction", "Wind Speed(kts)", "Gust(kts)", "Wave Direction", "Waves(m)", "Swell(m)"]].to_html(escape=False, index=False, justify='center'), unsafe_allow_html=True)
        
        st.markdown("<div style='page-break-after: always;'></div>", unsafe_allow_html=True) # 강제 페이지 넘김 (인쇄 시)

        st.subheader("📈 시각화 그래프")
        fig = make_subplots(rows=2, cols=1, shared_xaxes=False, vertical_spacing=0.2, subplot_titles=("Wind (kts)", "Waves (m)"))
        fig.add_trace(go.Scatter(x=df[time_col], y=df['Wind Speed(kts)'], name="Wind", line=dict(color='firebrick')), row=1, col=1)
        fig.add_trace(go.Scatter(x=df[time_col], y=df['Gust(kts)'], name="Gust", line=dict(color='orange', dash='dot'), fill='tonexty'), row=1, col=1)
        for i in range(len(df)):
            fig.add_annotation(dict(x=df[time_col].iloc[i], y=df['Wind Speed(kts)'].max() * 1.2, text="↑", showarrow=False, font=dict(size=12, color="#007BFF"), textangle=df['Wind_Deg'].iloc[i], xref="x1", yref="y1"))
        fig.add_trace(go.Scatter(x=df[time_col], y=df['Waves(m)'], name="Waves", line=dict(color='royalblue', width=3)), row=2, col=1)
        fig.add_trace(go.Scatter(x=df[time_col], y=df['Swell(m)'], name="Swell", line=dict(color='skyblue', dash='dash')), row=2, col=1)
        for i in range(len(df)):
            fig.add_annotation(dict(x=df[time_col].iloc[i], y=df['Waves(m)'].max() * 1.2, text="↑", showarrow=False, font=dict(size=12, color="#28A745"), textangle=df['Wave_Deg'].iloc[i], xref="x2", yref="y2"))

        fig.update_layout(height=800, margin=dict(t=50, b=50), hovermode="x unified")
        fig.update_xaxes(tickformat="%d일 %H:%M", dtick=43200000, showgrid=True)
        st.plotly_chart(fig, use_container_width=True)