import streamlit as st
import requests
import pandas as pd
import math
from datetime import datetime, timedelta
import plotly.graph_objects as go
from plotly.subplots import make_subplots

# 1. 페이지 설정
st.set_page_config(page_title="Windy Marine Forecast", layout="wide")

# 2. 출력 최적화용 CSS (A4용지 맞춤 및 불필요 요소 제거)
st.markdown("""
    <style>
    @media print {
        /* 입력창, 버튼, 탭 바 등 출력에 불필요한 요소 숨김 */
        section[data-testid="stSidebar"], 
        .stButton, .stSelectbox, .stNumberInput, 
        [data-testid="stHeader"], [data-testid="stTabs"] {
            display: none !important;
        }
        /* 페이지 여백 조정 및 A4 최적화 */
        .main .block-container {
            padding: 0 !important;
            margin: 0 !important;
        }
        /* 테이블 폰트 크기 조정 */
        table { font-size: 10px !important; width: 100% !important; }
        /* 그래프 크기 강제 고정 */
        .js-plotly-plot { width: 100% !important; height: 500px !important; }
        /* 출력 시 강제 줄바꿈 방지 */
        tr, td { page-break-inside: avoid !important; }
    }
    </style>
    """, unsafe_allow_html=True)

# 3. 세션 상태 및 API 설정
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

# 5. UI 상단 레이아웃
st.title("⚓ 실시간 해상 기상 관측 보고서")

with st.container():
    col1, col2, col3, col4 = st.columns([2, 2, 2, 1])
    with col1:
        st.session_state.lat = st.number_input("위도 (Lat)", value=st.session_state.lat, format="%.4f")
    with col2:
        st.session_state.lon = st.number_input("경도 (Lon)", value=st.session_state.lon, format="%.4f")
    with col3:
        offset_options = list(range(13, -12, -1))
        st.session_state.offset = st.selectbox("시간대 설정 (UTC Offset)", options=offset_options, index=offset_options.index(st.session_state.offset))
    with col4:
        st.write(" ")
        fetch_btn = st.button("데이터 수신", use_container_width=True)

# 6. 데이터 요청 및 처리
if fetch_btn:
    with st.spinner("데이터 분석 중..."):
        gfs_payload = {"lat": st.session_state.lat, "lon": st.session_state.lon, "model": "gfs", "parameters": ["pressure", "wind", "windGust"], "levels": ["surface"] * 3, "key": API_KEY}
        wave_payload = {"lat": st.session_state.lat, "lon": st.session_state.lon, "model": "gfsWave", "parameters": ["waves", "swell1"], "levels": ["surface"] * 2, "key": API_KEY}

        r_gfs, r_wave = requests.post(BASE_URL, json=gfs_payload), requests.post(BASE_URL, json=wave_payload)

        if r_gfs.status_code == 200 and r_wave.status_code == 200:
            data_gfs, data_wave = r_gfs.json(), r_wave.json()
            def sanitize(data_list): return [x if x is not None else 0.0 for x in data_list]

            limit = 56
            times = [datetime.fromtimestamp(t/1000) + timedelta(hours=(st.session_state.offset - 9)) for t in data_gfs.get('ts', [])[:limit]]
            time_col = f"Time (UTC{st.session_state.offset:+} )"

            df = pd.DataFrame({
                time_col: times,
                "Pressure(hPa)": [round(p/100, 1) for p in data_gfs.get('pressure-surface', [])[:limit]],
                "Wind_U": data_gfs.get('wind_u-surface', [])[:limit], "Wind_V": data_gfs.get('wind_v-surface', [])[:limit],
                "Gust(kts)": [round(g * MS_TO_KNOTS, 1) for g in sanitize(data_gfs.get('gust-surface', [])[:limit])],
                "Waves(m)": [round(w, 1) for w in sanitize(data_wave.get('waves_height-surface', [])[:limit])],
                "Wave_Deg": sanitize(data_wave.get('waves_direction-surface', [])[:limit]),
                "Swell(m)": [round(s, 1) for s in sanitize(data_wave.get('swell1_height-surface', [])[:limit])]
            })

            df['Wind Speed(kts)'] = (((df['Wind_U']**2 + df['Wind_V']**2)**0.5) * MS_TO_KNOTS).round(1)
            df['Wind_Deg'] = df.apply(lambda row: (math.degrees(math.atan2(row['Wind_U'], row['Wind_V'])) + 180) % 360, axis=1)
            df['Wind Direction'] = df.apply(lambda r: f"{r['Wind_Deg']:.1f}° {get_direction_text(r['Wind_Deg'])} {get_arrow_html(r['Wind_Deg'])}", axis=1)
            df['Wave Direction'] = df.apply(lambda r: f"{r['Wave_Deg']:.1f}° {get_direction_text(r['Wave_Deg'])} {get_arrow_html(r['Wave_Deg'], '#28A745')}", axis=1)

            # --- 출력 섹션 ---
            # 인쇄 버튼
            st.button("📄 리포트 인쇄 / PDF 저장", on_click=lambda: st.write('<script>window.print();</script>', unsafe_allow_html=True))
            
            st.markdown(f"**관측 위치:** 위도 {st.session_state.lat}, 경도 {st.session_state.lon} | **생성 시각:** {datetime.now().strftime('%Y-%m-%d %H:%M')}")

            # 테이블과 그래프를 탭 대신 나란히(또는 위아래로) 배치 (인쇄를 위해)
            st.subheader("📊 해상 예보 데이터 테이블")
            display_cols = [time_col, "Pressure(hPa)", "Wind Direction", "Wind Speed(kts)", "Gust(kts)", "Wave Direction", "Waves(m)", "Swell(m)"]
            st.write(df[display_cols].to_html(escape=False, index=False, justify='center'), unsafe_allow_html=True)

            st.write("---") # 구분선

            st.subheader("📈 시각화 차트")
            fig = make_subplots(rows=2, cols=1, shared_xaxes=False, vertical_spacing=0.15,
                                subplot_titles=("Wind (kts)", "Waves (m)"))

            fig.add_trace(go.Scatter(x=df[time_col], y=df['Wind Speed(kts)'], name="Wind", line=dict(color='firebrick')), row=1, col=1)
            fig.add_trace(go.Scatter(x=df[time_col], y=df['Gust(kts)'], name="Gust", line=dict(color='orange', dash='dot'), fill='tonexty'), row=1, col=1)
            for i in range(len(df)):
                fig.add_annotation(dict(x=df[time_col].iloc[i], y=df['Wind Speed(kts)'].max() * 1.2, text="↑", showarrow=False, 
                                        font=dict(size=12, color="#007BFF"), textangle=df['Wind_Deg'].iloc[i], xref="x1", yref="y1"))

            fig.add_trace(go.Scatter(x=df[time_col], y=df['Waves(m)'], name="Waves", line=dict(color='royalblue', width=3)), row=2, col=1)
            fig.add_trace(go.Scatter(x=df[time_col], y=df['Swell(m)'], name="Swell", line=dict(color='skyblue', dash='dash')), row=2, col=1)
            for i in range(len(df)):
                fig.add_annotation(dict(x=df[time_col].iloc[i], y=df['Waves(m)'].max() * 1.2, text="↑", showarrow=False, 
                                        font=dict(size=12, color="#28A745"), textangle=df['Wave_Deg'].iloc[i], xref="x2", yref="y2"))

            fig.update_layout(height=700, margin=dict(l=20, r=20, t=50, b=20), hovermode="x unified")
            fig.update_xaxes(tickformat="%d일 %H시", dtick=43200000, showgrid=True) # 12시간 간격 표시
            st.plotly_chart(fig, use_container_width=True)

        else:
            st.error("데이터 수신 실패")
