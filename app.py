import streamlit as st
import requests
import pandas as pd
import plotly.graph_objects as go
from datetime import datetime

# 페이지 설정
st.set_page_config(page_title="Windy Marine Forecast", layout="wide")

# API 키 (Secrets에서 불러오기)
API_KEY = st.secrets["WINDY_API_KEY"]
BASE_URL = "https://api.windy.com/api/point-forecast/v2"

st.title("⚓ 실시간 해상 기상 관측 데이터")

# 상단: 위치 입력 및 수신 버튼
with st.container():
    col1, col2, col3 = st.columns([2, 2, 1])
    with col1:
        lat = st.number_input("위도 (Latitude)", value=31.87, format="%.4f")
    with col2:
        lon = st.number_input("경도 (Longitude)", value=126.77, format="%.4f")
    with col3:
        st.write(" ") # 수직 정렬용
        fetch_btn = st.button("데이터 수신 시작", use_container_width=True)

if fetch_btn:
    with st.spinner("Windy 서버에서 데이터를 불러오는 중..."):
        # GFS (바람) 요청
        gfs_payload = {
            "lat": lat, "lon": lon, "model": "gfs",
            "parameters": ["pressure", "wind", "windGust"],
            "levels": ["surface", "surface", "surface"], "key": API_KEY
        }
        # GFS Wave (파도) 요청
        wave_payload = {
            "lat": lat, "lon": lon, "model": "gfsWave",
            "parameters": ["waves", "swell1"],
            "levels": ["surface", "surface"], "key": API_KEY
        }

        r_gfs = requests.post(BASE_URL, json=gfs_payload)
        r_wave = requests.post(BASE_URL, json=wave_payload)

        if r_gfs.status_code == 200 and r_wave.status_code == 200:
            data_gfs = r_gfs.json()
            data_wave = r_wave.json()

            # 데이터 가공 (실제 확인된 키 값으로 수정)
            # NULL(None) 값은 0.0으로 치환하여 오류 방지
            def sanitize(data_list):
                return [x if x is not None else 0.0 for x in data_list]

            df = pd.DataFrame({
                "Time": [datetime.fromtimestamp(t/1000) for t in data_gfs.get('ts', [])],
                "Pressure(hPa)": [p/100 for p in data_gfs.get('pressure-surface', [])],
                "Wind_U": data_gfs.get('wind_u-surface', []),
                "Wind_V": data_gfs.get('wind_v-surface', []),
                "Gust(m/s)": data_gfs.get('gust-surface', []),
                "Waves(m)": sanitize(data_wave.get('waves_height-surface', [])),
                "Swell(m)": sanitize(data_wave.get('swell1_height-surface', []))
            })
            
            # 풍속 계산
            df['Wind Speed(m/s)'] = (df['Wind_U']**2 + df['Wind_V']**2)**0.5
            
            # 탭 인터페이스
            tab1, tab2 = st.tabs(["📊 데이터 테이블", "📈 시각화 그래프"])

            with tab1:
                st.subheader("시간대별 상세 예보 (가공 데이터)")
                st.dataframe(df.drop(columns=['Wind_U', 'Wind_V']), use_container_width=True)

            with tab2:
                st.subheader("해상 상태 변화 (파고 및 풍속)")
                fig = go.Figure()
                fig.add_trace(go.Scatter(x=df['Time'], y=df['Waves(m)'], name="파고 (m)", line=dict(color='royalblue')))
                fig.add_trace(go.Scatter(x=df['Time'], y=df['Wind Speed(m/s)'], name="풍속 (m/s)", line=dict(color='firebrick', dash='dot')))
                st.plotly_chart(fig, use_container_width=True)
                
        else:
            st.error(f"데이터 수신 실패. GFS: {r_gfs.status_code}, Wave: {r_wave.status_code}")