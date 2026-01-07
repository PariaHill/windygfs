import streamlit as st
import requests
import pandas as pd
import plotly.graph_objects as go
from datetime import datetime, timedelta
import math

# 페이지 설정
st.set_page_config(page_title="Windy Marine Forecast", layout="wide")

# 세션 상태 초기화
if 'lat' not in st.session_state: st.session_state.lat = 31.8700
if 'lon' not in st.session_state: st.session_state.lon = 126.7700
if 'offset' not in st.session_state: st.session_state.offset = 9

# API 키 및 설정
API_KEY = st.secrets["WINDY_API_KEY"]
BASE_URL = "https://api.windy.com/api/point-forecast/v2"
MS_TO_KNOTS = 1.94384

# 풍향 각도 + 방위 + 화살표 결합 함수
def get_wind_dir_full(deg):
    # 16방위 텍스트
    directions = ['N', 'NNE', 'NE', 'ENE', 'E', 'ESE', 'SE', 'SSE', 'S', 'SSW', 'SW', 'WSW', 'W', 'WNW', 'NW', 'NNW']
    # 8방위 화살표 (바람이 불어오는 방향 기준)
    arrows = ['↓', '↙', '↙', '←', '←', '↖', '↖', '↑', '↑', '↗', '↗', '→', '→', '↘', '↘', '↓']
    
    idx = int((deg + 11.25) / 22.5) % 16
    return f"{deg:.1f}° {directions[idx]} {arrows[idx]}"

st.title("⚓ 실시간 해상 기상 관측 시스템")

# 상단 입력부
with st.container():
    col1, col2, col3, col4 = st.columns([2, 2, 2, 1])
    with col1:
        st.session_state.lat = st.number_input("위도 (Lat)", value=st.session_state.lat, format="%.4f")
    with col2:
        st.session_state.lon = st.number_input("경도 (Lon)", value=st.session_state.lon, format="%.4f")
    with col3:
        offset_options = list(range(13, -13, -1))
        st.session_state.offset = st.selectbox("시간대 설정 (UTC Offset)", options=offset_options, index=offset_options.index(st.session_state.offset))
    with col4:
        st.write(" ")
        fetch_btn = st.button("데이터 수신", use_container_width=True)

if fetch_btn:
    with st.spinner("해상 데이터를 불러오는 중..."):
        gfs_payload = {
            "lat": st.session_state.lat, "lon": st.session_state.lon, "model": "gfs",
            "parameters": ["pressure", "wind", "windGust"],
            "levels": ["surface", "surface", "surface"], "key": API_KEY
        }
        wave_payload = {
            "lat": st.session_state.lat, "lon": st.session_state.lon, "model": "gfsWave",
            "parameters": ["waves", "swell1"],
            "levels": ["surface", "surface"], "key": API_KEY
        }

        r_gfs = requests.post(BASE_URL, json=gfs_payload)
        r_wave = requests.post(BASE_URL, json=wave_payload)

        if r_gfs.status_code == 200 and r_wave.status_code == 200:
            data_gfs = r_gfs.json()
            data_wave = r_wave.json()

            def sanitize(data_list):
                return [x if x is not None else 0.0 for x in data_list]

            limit = 56 # 7일치
            
            # 시간대 적용
            times = [datetime.fromtimestamp(t/1000) + timedelta(hours=(st.session_state.offset - 9)) for t in data_gfs.get('ts', [])[:limit]]

            df = pd.DataFrame({
                f"Time (UTC{st.session_state.offset:+} )": times,
                "Pressure(hPa)": [round(p/100, 1) for p in data_gfs.get('pressure-surface', [])[:limit]],
                "Wind_U": data_gfs.get('wind_u-surface', [])[:limit],
                "Wind_V": data_gfs.get('wind_v-surface', [])[:limit],
                "Gust(kts)": [round(g * MS_TO_KNOTS, 1) for g in sanitize(data_gfs.get('gust-surface', [])[:limit])],
                "Waves(m)": [round(w, 1) for w in sanitize(data_wave.get('waves_height-surface', [])[:limit])],
                "Swell(m)": [round(s, 1) for s in sanitize(data_wave.get('swell1_height-surface', [])[:limit])]
            })

            # 풍속 계산 및 반올림
            df['Wind Speed(kts)'] = (((df['Wind_U']**2 + df['Wind_V']**2)**0.5) * MS_TO_KNOTS).round(1)
            # 풍향 계산
            df['Wind_Deg'] = df.apply(lambda row: (math.degrees(math.atan2(row['Wind_U'], row['Wind_V'])) + 180) % 360, axis=1)
            df['Wind Direction'] = df['Wind_Deg'].apply(get_wind_dir_full)

            # 컬럼 재정렬
            time_col = f"Time (UTC{st.session_state.offset:+} )"
            display_df = df[[time_col, "Pressure(hPa)", "Wind Direction", "Wind Speed(kts)", "Gust(kts)", "Waves(m)", "Swell(m)"]]

            tab1, tab2 = st.tabs(["📊 데이터 테이블", "📈 시각화 그래프"])
            with tab1:
                st.subheader(f"7일 해상 예보 데이터 ({time_col})")
                st.dataframe(display_df, use_container_width=True)
            with tab2:
                st.subheader("풍속 및 파고 추이")
                fig = go.Figure()
                fig.add_trace(go.Scatter(x=df[time_col], y=df['Waves(m)'], name="파고 (m)", line=dict(color='royalblue', width=3)))
                fig.add_trace(go.Scatter(x=df[time_col], y=df['Wind Speed(kts)'], name="풍속 (kts)", yaxis="y2", line=dict(color='firebrick', dash='dot')))
                fig.update_layout(yaxis=dict(title="파고 (m)"), yaxis2=dict(title="풍속 (kts)", side="right", overlaying="y", showgrid=False), hovermode="x unified")
                st.plotly_chart(fig, use_container_width=True)
        else:
            st.error("데이터 수신에 실패했습니다.")