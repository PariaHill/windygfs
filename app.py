import streamlit as st
import requests
import pandas as pd
import math
from datetime import datetime, timedelta
import plotly.graph_objects as go
from plotly.subplots import make_subplots

# 1. 페이지 설정
st.set_page_config(page_title="Windy Marine Forecast", layout="wide")

# 2. 세션 상태 및 API 설정
if 'lat' not in st.session_state: st.session_state.lat = 31.8700
if 'lon' not in st.session_state: st.session_state.lon = 126.7700
if 'offset' not in st.session_state: st.session_state.offset = 9

API_KEY = st.secrets["WINDY_API_KEY"]
BASE_URL = "https://api.windy.com/api/point-forecast/v2"
MS_TO_KNOTS = 1.94384

# 3. 유틸리티 함수
def get_direction_text(deg):
    directions = ['N', 'NNE', 'NE', 'ENE', 'E', 'ESE', 'SE', 'SSE', 'S', 'SSW', 'SW', 'WSW', 'W', 'WNW', 'NW', 'NNW']
    idx = int((deg + 11.25) / 22.5) % 16
    return directions[idx]

def get_arrow_html(deg, color="#007BFF"):
    """정밀 회전 화살표 HTML 생성 (불어오는/밀려오는 방향 기준)"""
    return f'<span style="display:inline-block; transform:rotate({deg}deg); font-size:18px; color:{color}; margin-left:5px;">↑</span>'

# 4. UI 상단
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

if fetch_btn:
    with st.spinner("해상 데이터를 분석 중..."):
        # 파라미터에 파도 방향(waves_direction) 추가 확인
        gfs_payload = {"lat": st.session_state.lat, "lon": st.session_state.lon, "model": "gfs", "parameters": ["pressure", "wind", "windGust"], "levels": ["surface"] * 3, "key": API_KEY}
        wave_payload = {"lat": st.session_state.lat, "lon": st.session_state.lon, "model": "gfsWave", "parameters": ["waves", "swell1"], "levels": ["surface"] * 2, "key": API_KEY}

        r_gfs = requests.post(BASE_URL, json=gfs_payload)
        r_wave = requests.post(BASE_URL, json=wave_payload)

        if r_gfs.status_code == 200 and r_wave.status_code == 200:
            data_gfs, data_wave = r_gfs.json(), r_wave.json()
            def sanitize(data_list): return [x if x is not None else 0.0 for x in data_list]

            limit = 56
            times = [datetime.fromtimestamp(t/1000) + timedelta(hours=(st.session_state.offset - 9)) for t in data_gfs.get('ts', [])[:limit]]
            time_col = f"Time (UTC{st.session_state.offset:+} )"

            # 데이터 가공
            df = pd.DataFrame({
                time_col: times,
                "Pressure(hPa)": [round(p/100, 1) for p in data_gfs.get('pressure-surface', [])[:limit]],
                "Wind_U": data_gfs.get('wind_u-surface', [])[:limit], "Wind_V": data_gfs.get('wind_v-surface', [])[:limit],
                "Gust(kts)": [round(g * MS_TO_KNOTS, 1) for g in sanitize(data_gfs.get('gust-surface', [])[:limit])],
                "Waves(m)": [round(w, 1) for w in sanitize(data_wave.get('waves_height-surface', [])[:limit])],
                "Wave_Deg": sanitize(data_wave.get('waves_direction-surface', [])[:limit]),
                "Swell(m)": [round(s, 1) for s in sanitize(data_wave.get('swell1_height-surface', [])[:limit])]
            })

            # 바람 및 파도 방향 계산
            df['Wind Speed(kts)'] = (((df['Wind_U']**2 + df['Wind_V']**2)**0.5) * MS_TO_KNOTS).round(1)
            df['Wind_Deg'] = df.apply(lambda row: (math.degrees(math.atan2(row['Wind_U'], row['Wind_V'])) + 180) % 360, axis=1)
            
            # 테이블용 컬럼 생성
            df['Wind Direction'] = df.apply(lambda r: f"{r['Wind_Deg']:.1f}° {get_direction_text(r['Wind_Deg'])} {get_arrow_html(r['Wind_Deg'])}", axis=1)
            df['Wave Direction'] = df.apply(lambda r: f"{r['Wave_Deg']:.1f}° {get_direction_text(r['Wave_Deg'])} {get_arrow_html(r['Wave_Deg'], '#28A745')}", axis=1)

            tab1, tab2 = st.tabs(["📊 데이터 테이블", "📈 시각화 그래프"])
            with tab1:
                display_cols = [time_col, "Pressure(hPa)", "Wind Direction", "Wind Speed(kts)", "Gust(kts)", "Wave Direction", "Waves(m)", "Swell(m)"]
                st.write(df[display_cols].to_html(escape=False, index=False, justify='center'), unsafe_allow_html=True)

            with tab2:
                fig = make_subplots(rows=2, cols=1, shared_xaxes=False, vertical_spacing=0.2,
                                    subplot_titles=("Wind Speed & Direction (kts)", "Wave Height & Direction (m)"))

                # 상단: 바람 그래프 + 화살표
                fig.add_trace(go.Scatter(x=df[time_col], y=df['Wind Speed(kts)'], name="Wind", line=dict(color='firebrick')), row=1, col=1)
                fig.add_trace(go.Scatter(x=df[time_col], y=df['Gust(kts)'], name="Gust", line=dict(color='orange', dash='dot'), fill='tonexty'), row=1, col=1)
                for i in range(len(df)):
                    fig.add_annotation(dict(x=df[time_col].iloc[i], y=df['Wind Speed(kts)'].max() * 1.2, text="↑", showarrow=False, 
                                            font=dict(size=14, color="#007BFF"), textangle=df['Wind_Deg'].iloc[i], xref="x1", yref="y1"))

                # 하단: 파도 그래프 + 화살표 (초록색)
                fig.add_trace(go.Scatter(x=df[time_col], y=df['Waves(m)'], name="Waves", line=dict(color='royalblue', width=3)), row=2, col=1)
                fig.add_trace(go.Scatter(x=df[time_col], y=df['Swell(m)'], name="Swell", line=dict(color='skyblue', dash='dash')), row=2, col=1)
                for i in range(len(df)):
                    fig.add_annotation(dict(x=df[time_col].iloc[i], y=df['Waves(m)'].max() * 1.25, text="↑", showarrow=False, 
                                            font=dict(size=14, color="#28A745"), textangle=df['Wave_Deg'].iloc[i], xref="x2", yref="y2"))

                # 날짜 구분 및 X축 설정
                for i, day in enumerate(df[time_col].dt.date.unique()):
                    if i % 2 == 0: fig.add_vrect(x0=str(day), x1=str(day + timedelta(days=1)), fillcolor="gray", opacity=0.07, layer="below", line_width=0)

                fig.update_layout(height=900, hovermode="x unified", legend=dict(orientation="h", y=1.05))
                fig.update_xaxes(tickformat="%d일\n%H:%M", dtick=21600000, showgrid=True, row=1, col=1)
                fig.update_xaxes(tickformat="%d일\n%H:%M", dtick=21600000, showgrid=True, row=2, col=1)
                fig.update_yaxes(range=[0, df['Wind Speed(kts)'].max() * 1.4], row=1, col=1)
                fig.update_yaxes(range=[0, df['Waves(m)'].max() * 1.45], row=2, col=1)
                
                st.plotly_chart(fig, use_container_width=True)
        else:
            st.error("데이터 수신 실패")