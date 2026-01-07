import streamlit as st
import requests
import pandas as pd
import math
from datetime import datetime, timedelta

# 1. 페이지 설정
st.set_page_config(page_title="Windy Marine Forecast", layout="wide")

# 2. 세션 상태 초기화 (입력값 유지)
if 'lat' not in st.session_state: st.session_state.lat = 31.8700
if 'lon' not in st.session_state: st.session_state.lon = 126.7700
if 'offset' not in st.session_state: st.session_state.offset = 9

# 3. API 및 상수 설정
API_KEY = st.secrets["WINDY_API_KEY"]
BASE_URL = "https://api.windy.com/api/point-forecast/v2"
MS_TO_KNOTS = 1.94384

# 4. 유틸리티 함수
def get_wind_direction_text(deg):
    directions = ['N', 'NNE', 'NE', 'ENE', 'E', 'ESE', 'SE', 'SSE', 'S', 'SSW', 'SW', 'WSW', 'W', 'WNW', 'NW', 'NNW']
    idx = int((deg + 11.25) / 22.5) % 16
    return directions[idx]

def get_wind_arrow_html(deg):
    """불어오는 쪽을 가리키는 정밀 화살표 (0도 북풍이면 화살표가 위쪽 ↑)"""
    # 불어오는 쪽 기준: 0도(N) -> ↑, 90도(E) -> →, 180도(S) -> ↓, 270도(W) -> ←
    # 기본 화살표가 ↑(0도)라고 가정할 때, deg만큼 회전시키면 해당 방위를 가리킴
    rotate_deg = deg 
    return f'''
    <div style="display: flex; align-items: center; justify-content: center; min-width: 40px;">
        <span style="display: inline-block; transform: rotate({rotate_deg}deg); font-size: 18px; color: #007BFF; font-weight: bold;">↑</span>
    </div>
    '''

# 5. UI 상단 레이아웃
st.title("⚓ 실시간 해상 기상 관측 시스템")

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

# 6. 데이터 요청 및 시각화
if fetch_btn:
    with st.spinner("해상 데이터를 분석 중..."):
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

            limit = 56 # 7일치 (3시간 간격)
            
            # 시간 보정 (KST=UTC+9 시스템 기준 보정)
            times = [datetime.fromtimestamp(t/1000) + timedelta(hours=(st.session_state.offset - 9)) for t in data_gfs.get('ts', [])[:limit]]
            time_col_name = f"Time (UTC{st.session_state.offset:+} )"

            # 데이터 프레임 생성 (소수점 1자리)
            df = pd.DataFrame({
                time_col_name: times,
                "Pressure(hPa)": [round(p/100, 1) for p in data_gfs.get('pressure-surface', [])[:limit]],
                "Wind_U": data_gfs.get('wind_u-surface', [])[:limit],
                "Wind_V": data_gfs.get('wind_v-surface', [])[:limit],
                "Gust(kts)": [round(g * MS_TO_KNOTS, 1) for g in sanitize(data_gfs.get('gust-surface', [])[:limit])],
                "Waves(m)": [round(w, 1) for w in sanitize(data_wave.get('waves_height-surface', [])[:limit])],
                "Swell(m)": [round(s, 1) for s in sanitize(data_wave.get('swell1_height-surface', [])[:limit])]
            })

            # 풍속 계산 및 풍향(방위 + 화살표) 가공
            df['Wind Speed(kts)'] = (((df['Wind_U']**2 + df['Wind_V']**2)**0.5) * MS_TO_KNOTS).round(1)
            df['Wind_Deg'] = df.apply(lambda row: (math.degrees(math.atan2(row['Wind_U'], row['Wind_V'])) + 180) % 360, axis=1)
            
            # 테이블용 풍향 컬럼: "270.0° W [화살표]"
            df['Wind Direction'] = df.apply(lambda row: 
                f"{row['Wind_Deg']:.1f}° {get_wind_direction_text(row['Wind_Deg'])} {get_wind_arrow_html(row['Wind_Deg'])}", axis=1)

            # 출력 데이터 선택 및 순서 정렬
            display_df = df[[time_col_name, "Pressure(hPa)", "Wind Direction", "Wind Speed(kts)", "Gust(kts)", "Waves(m)", "Swell(m)"]]

            # 탭 구성
            tab1, tab2 = st.tabs(["📊 데이터 테이블", "📈 시각화 그래프"])
            
            with tab1:
                st.subheader(f"7일 해상 예보 데이터 ({time_col_name})")
                # HTML 렌더링을 통해 화살표 회전 적용
                st.write(display_df.to_html(escape=False, index=False), unsafe_allow_html=True)

            with tab2:
                st.subheader("풍속 및 파고 추이")
                fig = go.Figure()
                fig.add_trace(go.Scatter(x=df[time_col_name], y=df['Waves(m)'], name="파고 (m)", line=dict(color='royalblue', width=3)))
                fig.add_trace(go.Scatter(x=df[time_col_name], y=df['Wind Speed(kts)'], name="풍속 (kts)", yaxis="y2", line=dict(color='firebrick', dash='dot')))
                fig.update_layout(
                    yaxis=dict(title="파고 (m)"),
                    yaxis2=dict(title="풍속 (kts)", side="right", overlaying="y", showgrid=False),
                    hovermode="x unified",
                    legend=dict(orientation="h", y=1.1)
                )
                st.plotly_chart(fig, use_container_width=True)
        else:
            st.error("데이터 수신에 실패했습니다. API 키나 좌표를 확인하세요.")