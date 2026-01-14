import streamlit as st
import requests
import pandas as pd
import numpy as np
import math
import xarray as xr
import tempfile
import os
from datetime import datetime, timedelta, timezone
from io import BytesIO
import plotly.graph_objects as go
from plotly.subplots import make_subplots

# ============================================================
# 1. 페이지 설정
# ============================================================
st.set_page_config(page_title="Captain Park's Marine Forecast", layout="wide")

# 인쇄 최적화 CSS
st.markdown("""
    <style>
    @media print {
        section[data-testid="stSidebar"], .stButton, .stSelectbox, .stNumberInput, 
        header, [data-testid="stHeader"], .stTabs [role="tablist"],
        footer, [data-testid="stFooter"] { display: none !important; }
        .main .block-container { padding-top: 1rem !important; }
        table { font-size: 10px !important; width: 100% !important; }
        .js-plotly-plot { height: 750px !important; }
    }
    </style>
    """, unsafe_allow_html=True)

# ============================================================
# 2. 세션 상태 초기화
# ============================================================
if 'lat' not in st.session_state: st.session_state.lat = 31.8700
if 'lon' not in st.session_state: st.session_state.lon = 126.7700
if 'offset' not in st.session_state: st.session_state.offset = 9

MS_TO_KNOTS = 1.94384

# ============================================================
# 3. 유틸리티 함수
# ============================================================
def get_direction_text(deg):
    """16방위 텍스트 반환"""
    directions = ['N', 'NNE', 'NE', 'ENE', 'E', 'ESE', 'SE', 'SSE', 
                  'S', 'SSW', 'SW', 'WSW', 'W', 'WNW', 'NW', 'NNW']
    idx = int((deg + 11.25) / 22.5) % 16
    return directions[idx]

def get_arrow_html(deg, color="#007BFF"):
    """불어오는 쪽을 가리키도록 180도 반전"""
    rotate_deg = (deg + 180) % 360 
    return f'<span style="display:inline-block; transform:rotate({rotate_deg}deg); font-size:16px; color:{color}; margin-left:5px;">↑</span>'

def get_available_cycle():
    """
    최신 사용 가능한 GFS cycle 탐지 (HEAD 요청)
    GFS는 보통 런타임 후 3.5~4시간 후에 데이터가 준비됨
    """
    now_utc = datetime.now(timezone.utc)
    cycles = [18, 12, 6, 0]
    
    # 오늘과 어제 날짜 시도
    for days_ago in range(2):
        check_date = now_utc - timedelta(days=days_ago)
        date_str = check_date.strftime("%Y%m%d")
        
        for cycle in cycles:
            # 해당 cycle이 현재 시간보다 미래면 스킵
            cycle_time = check_date.replace(hour=cycle, minute=0, second=0, microsecond=0)
            if cycle_time > now_utc:
                continue
            
            # 데이터가 준비되었는지 확인 (최소 4시간 경과)
            hours_since_cycle = (now_utc - cycle_time).total_seconds() / 3600
            if hours_since_cycle < 4:
                continue
            
            # HEAD 요청으로 f000 파일 존재 확인 (Atmosphere)
            url = (f"https://nomads.ncep.noaa.gov/cgi-bin/filter_gfs_0p25.pl?"
                   f"dir=%2Fgfs.{date_str}%2F{cycle:02d}%2Fatmos&"
                   f"file=gfs.t{cycle:02d}z.pgrb2.0p25.f000&"
                   f"var_PRMSL=on&lev_mean_sea_level=on&"
                   f"subregion=&toplat=32&leftlon=126&rightlon=127&bottomlat=31")
            
            try:
                resp = requests.head(url, timeout=10)
                if resp.status_code == 200:
                    return date_str, cycle, cycle_time
            except:
                continue
    
    return None, None, None

def build_subregion_params(lat, lon, margin=0.25):
    """입력 좌표 기준 ±0.25도 서브리전 파라미터 생성"""
    # 0.25도 그리드에 맞춰 정렬
    lat_min = math.floor((lat - margin) * 4) / 4
    lat_max = math.ceil((lat + margin) * 4) / 4
    lon_min = math.floor((lon - margin) * 4) / 4
    lon_max = math.ceil((lon + margin) * 4) / 4
    
    return f"subregion=&toplat={lat_max}&leftlon={lon_min}&rightlon={lon_max}&bottomlat={lat_min}"

def get_forecast_hours():
    """
    예보 시간 목록 반환
    0-120시간: 3시간 간격
    120-168시간: 일부 모델에서 12시간 간격일 수 있음
    """
    hours = list(range(0, 121, 3))  # 0, 3, 6, ... 120
    # 120시간 이후는 존재하는지 확인 필요, 일단 3시간 간격으로 시도
    hours.extend(range(123, 169, 3))  # 123, 126, ... 168
    return hours

def fetch_gfs_atmosphere(date_str, cycle, fhour, lat, lon):
    """
    GFS Atmosphere 모델에서 PRMSL(기압), GUST(돌풍) 가져오기
    """
    subregion = build_subregion_params(lat, lon)
    url = (f"https://nomads.ncep.noaa.gov/cgi-bin/filter_gfs_0p25.pl?"
           f"dir=%2Fgfs.{date_str}%2F{cycle:02d}%2Fatmos&"
           f"file=gfs.t{cycle:02d}z.pgrb2.0p25.f{fhour:03d}&"
           f"var_PRMSL=on&var_GUST=on&"
           f"lev_mean_sea_level=on&lev_surface=on&"
           f"{subregion}")
    
    try:
        resp = requests.get(url, timeout=30)
        if resp.status_code == 200 and len(resp.content) > 100:
            return resp.content
    except Exception as e:
        pass
    return None

def fetch_gfswave(date_str, cycle, fhour, lat, lon):
    """
    GFS Wave 모델에서 바람 및 파도 데이터 가져오기
    - surface: UGRD, VGRD, HTSGW, DIRPW, PERPW
    - 1 in sequence: SWELL, SWDIR
    """
    subregion = build_subregion_params(lat, lon)
    
    # URL 인코딩 주의: 공백은 %20 또는 +
    url = (f"https://nomads.ncep.noaa.gov/cgi-bin/filter_gfswave.pl?"
           f"dir=%2Fgfs.{date_str}%2F{cycle:02d}%2Fwave%2Fgridded&"
           f"file=gfswave.t{cycle:02d}z.global.0p25.f{fhour:03d}.grib2&"
           f"var_UGRD=on&var_VGRD=on&var_HTSGW=on&var_DIRPW=on&var_PERPW=on&"
           f"var_SWELL=on&var_SWDIR=on&"
           f"lev_surface=on&lev_1_in_sequence=on&"
           f"{subregion}")
    
    try:
        resp = requests.get(url, timeout=30)
        if resp.status_code == 200 and len(resp.content) > 100:
            return resp.content
    except Exception as e:
        pass
    return None

def parse_grib_data(grib_bytes, lat, lon):
    """
    GRIB2 바이트 데이터를 파싱하여 지정 좌표의 값 추출
    """
    if grib_bytes is None or len(grib_bytes) < 100:
        return {}
    
    result = {}
    
    try:
        # 임시 파일로 저장 후 xarray로 읽기
        with tempfile.NamedTemporaryFile(suffix='.grib2', delete=False) as f:
            f.write(grib_bytes)
            temp_path = f.name
        
        try:
            # cfgrib은 여러 메시지가 있을 때 filter_by_keys로 구분
            # 먼저 모든 데이터셋을 읽어보기
            datasets = []
            
            # 다양한 typeOfLevel로 시도
            for type_of_level in ['surface', 'meanSea', 'orderedSequence', None]:
                try:
                    if type_of_level:
                        ds = xr.open_dataset(temp_path, engine='cfgrib',
                                           backend_kwargs={'filter_by_keys': {'typeOfLevel': type_of_level}})
                    else:
                        ds = xr.open_dataset(temp_path, engine='cfgrib')
                    datasets.append(ds)
                except:
                    continue
            
            # 각 데이터셋에서 변수 추출
            for ds in datasets:
                # 좌표 찾기
                lat_name = 'latitude' if 'latitude' in ds.coords else 'lat'
                lon_name = 'longitude' if 'longitude' in ds.coords else 'lon'
                
                if lat_name not in ds.coords or lon_name not in ds.coords:
                    continue
                
                # 가장 가까운 포인트 선택
                try:
                    point = ds.sel({lat_name: lat, lon_name: lon}, method='nearest')
                except:
                    continue
                
                # 변수 추출
                var_mapping = {
                    'prmsl': 'pressure',      # Pa -> hPa 변환 필요
                    'gust': 'gust',           # m/s
                    'u10': 'wind_u',          # m/s (10m wind)
                    'v10': 'wind_v',          # m/s (10m wind)
                    'u': 'wind_u',            # m/s (surface wind from wave model)
                    'v': 'wind_v',            # m/s
                    'htsgw': 'wave_height',   # m
                    'dirpw': 'wave_dir',      # degrees
                    'perpw': 'wave_period',   # seconds
                    'swell': 'swell_height',  # m
                    'swdir': 'swell_dir',     # degrees
                }
                
                for var in ds.data_vars:
                    var_lower = var.lower()
                    if var_lower in var_mapping:
                        try:
                            val = float(point[var].values)
                            if not np.isnan(val):
                                result[var_mapping[var_lower]] = val
                        except:
                            pass
                
                ds.close()
                
        finally:
            os.unlink(temp_path)
            
    except Exception as e:
        pass
    
    return result

def fetch_all_forecasts(date_str, cycle, cycle_time, lat, lon, progress_bar, status_text):
    """
    모든 예보 시간에 대해 데이터 수집
    """
    forecast_hours = get_forecast_hours()
    all_data = []
    
    total = len(forecast_hours)
    successful = 0
    
    for i, fhour in enumerate(forecast_hours):
        progress_bar.progress((i + 1) / total)
        status_text.text(f"데이터 수신 중... f{fhour:03d} ({i+1}/{total})")
        
        # 예보 시각 계산
        valid_time = cycle_time + timedelta(hours=fhour)
        
        row = {
            'valid_time': valid_time,
            'fhour': fhour,
        }
        
        # Atmosphere 데이터 (기압, 돌풍)
        atmos_data = fetch_gfs_atmosphere(date_str, cycle, fhour, lat, lon)
        atmos_parsed = parse_grib_data(atmos_data, lat, lon)
        
        # Wave 데이터 (바람, 파도, 스웰)
        wave_data = fetch_gfswave(date_str, cycle, fhour, lat, lon)
        wave_parsed = parse_grib_data(wave_data, lat, lon)
        
        # 데이터 병합
        row.update(atmos_parsed)
        row.update(wave_parsed)
        
        # 최소한 일부 데이터가 있으면 추가
        if len(row) > 2:
            all_data.append(row)
            successful += 1
        
        # 120시간 이후 데이터가 없으면 중단
        if fhour > 120 and len(row) <= 2:
            status_text.text(f"f{fhour:03d} 이후 데이터 없음, 수집 완료")
            break
    
    return all_data, successful

# ============================================================
# 4. UI 상단
# ============================================================
st.title("⚓ 실시간 해상 기상 관측 시스템")
st.caption("Data Source: NOAA GFS & GFS-Wave (0.25° Resolution)")

with st.container():
    col1, col2, col3, col4 = st.columns([2, 2, 2, 1])
    with col1: 
        st.session_state.lat = st.number_input("위도 (Lat)", value=st.session_state.lat, format="%.4f")
    with col2: 
        st.session_state.lon = st.number_input("경도 (Lon)", value=st.session_state.lon, format="%.4f")
    with col3:
        opts = list(range(13, -13, -1))
        st.session_state.offset = st.selectbox("시간대 설정 (UTC Offset)", 
                                                options=opts, 
                                                index=opts.index(st.session_state.offset))
    with col4:
        st.write(" ")
        fetch_btn = st.button("데이터 수신 시작", use_container_width=True)

# ============================================================
# 5. 데이터 수집 및 표시
# ============================================================
if fetch_btn or 'data_loaded' in st.session_state:
    
    with st.spinner("최신 GFS Cycle 탐지 중..."):
        date_str, cycle, cycle_time = get_available_cycle()
    
    if date_str is None:
        st.error("❌ 사용 가능한 GFS 데이터를 찾을 수 없습니다. 잠시 후 다시 시도해주세요.")
    else:
        st.success(f"✅ GFS Cycle: {date_str} {cycle:02d}Z (UTC)")
        
        progress_bar = st.progress(0)
        status_text = st.empty()
        
        all_data, successful = fetch_all_forecasts(
            date_str, cycle, cycle_time, 
            st.session_state.lat, st.session_state.lon,
            progress_bar, status_text
        )
        
        progress_bar.empty()
        status_text.empty()
        
        if successful == 0:
            st.error("❌ 데이터를 가져오지 못했습니다.")
        else:
            st.session_state.data_loaded = True
            st.info(f"📊 {successful}개 시간대 데이터 수신 완료")
            
            # DataFrame 생성
            df = pd.DataFrame(all_data)
            
            # 시간대 적용
            time_col = f"Time (UTC{st.session_state.offset:+})"
            df[time_col] = df['valid_time'] + timedelta(hours=(st.session_state.offset - 0))  # UTC 기준
            
            # 기압 변환 (Pa -> hPa)
            if 'pressure' in df.columns:
                df['Pressure(hPa)'] = (df['pressure'] / 100).round(1)
            else:
                df['Pressure(hPa)'] = np.nan
            
            # 바람 계산
            if 'wind_u' in df.columns and 'wind_v' in df.columns:
                df['Wind Speed(kts)'] = (np.sqrt(df['wind_u']**2 + df['wind_v']**2) * MS_TO_KNOTS).round(1)
                df['Wind_Deg'] = (np.degrees(np.arctan2(df['wind_u'], df['wind_v'])) + 180) % 360
                df['Wind Direction'] = df.apply(
                    lambda r: f"{r['Wind_Deg']:.1f}° {get_direction_text(r['Wind_Deg'])} {get_arrow_html(r['Wind_Deg'])}", 
                    axis=1
                )
            else:
                df['Wind Speed(kts)'] = np.nan
                df['Wind_Deg'] = np.nan
                df['Wind Direction'] = '-'
            
            # 돌풍 변환
            if 'gust' in df.columns:
                df['Gust(kts)'] = (df['gust'] * MS_TO_KNOTS).round(1)
            else:
                df['Gust(kts)'] = np.nan
            
            # 파도 데이터
            if 'wave_height' in df.columns:
                df['Waves(m)'] = df['wave_height'].round(1)
                df['Max Waves(m)'] = (df['wave_height'] * 1.6).round(1)
            else:
                df['Waves(m)'] = np.nan
                df['Max Waves(m)'] = np.nan
            
            if 'wave_dir' in df.columns:
                df['Wave_Deg'] = df['wave_dir']
                df['Wave Direction'] = df.apply(
                    lambda r: f"{r['Wave_Deg']:.1f}° {get_direction_text(r['Wave_Deg'])} {get_arrow_html(r['Wave_Deg'], '#28A745')}" 
                    if pd.notna(r['Wave_Deg']) else '-',
                    axis=1
                )
            else:
                df['Wave_Deg'] = np.nan
                df['Wave Direction'] = '-'
            
            # 파도 주기
            if 'wave_period' in df.columns:
                df['Wave Period(s)'] = df['wave_period'].round(1)
            else:
                df['Wave Period(s)'] = np.nan
            
            # 스웰 데이터
            if 'swell_height' in df.columns:
                df['Swell(m)'] = df['swell_height'].round(1)
            else:
                df['Swell(m)'] = np.nan
            
            if 'swell_dir' in df.columns:
                df['Swell_Deg'] = df['swell_dir']
                df['Swell Direction'] = df.apply(
                    lambda r: f"{r['Swell_Deg']:.1f}° {get_direction_text(r['Swell_Deg'])} {get_arrow_html(r['Swell_Deg'], '#9932CC')}" 
                    if pd.notna(r['Swell_Deg']) else '-',
                    axis=1
                )
            else:
                df['Swell_Deg'] = np.nan
                df['Swell Direction'] = '-'
            
            # ============================================================
            # 탭 표시
            # ============================================================
            tab1, tab2 = st.tabs(["📊 데이터 테이블", "📈 시각화 그래프"])
            
            with tab1:
                st.subheader("데이터 테이블 리포트")
                if st.button("🖨️ 테이블 인쇄 / PDF 저장", key="p_t1"): 
                    st.components.v1.html("<script>window.parent.print();</script>", height=0)
                
                display_cols = [
                    time_col, "Pressure(hPa)", 
                    "Wind Direction", "Wind Speed(kts)", "Gust(kts)", 
                    "Wave Direction", "Waves(m)", "Max Waves(m)", "Wave Period(s)",
                    "Swell Direction", "Swell(m)"
                ]
                
                # 존재하는 컬럼만 선택
                display_cols = [c for c in display_cols if c in df.columns]
                
                st.write(df[display_cols].to_html(escape=False, index=False, justify='center'), 
                        unsafe_allow_html=True)
            
            with tab2:
                st.subheader("그래프 분석 리포트")
                if st.button("🖨️ 그래프 인쇄 / PDF 저장", key="p_t2"): 
                    st.components.v1.html("<script>window.parent.print();</script>", height=0)
                
                fig = make_subplots(
                    rows=2, cols=1, 
                    shared_xaxes=False, 
                    vertical_spacing=0.2,
                    subplot_titles=("Wind Speed & Direction (kts)", "Wave Height & Direction (m)")
                )
                
                # 상단: 바람
                if 'Wind Speed(kts)' in df.columns:
                    fig.add_trace(
                        go.Scatter(x=df[time_col], y=df['Wind Speed(kts)'], 
                                  name="Wind", line=dict(color='firebrick')), 
                        row=1, col=1
                    )
                
                if 'Gust(kts)' in df.columns:
                    fig.add_trace(
                        go.Scatter(x=df[time_col], y=df['Gust(kts)'], 
                                  name="Gust", line=dict(color='orange', dash='dot'), fill='tonexty'), 
                        row=1, col=1
                    )
                
                # 바람 방향 화살표
                if 'Wind_Deg' in df.columns and 'Wind Speed(kts)' in df.columns:
                    wind_max = df['Wind Speed(kts)'].max()
                    if pd.notna(wind_max):
                        for i in range(len(df)):
                            if pd.notna(df['Wind_Deg'].iloc[i]):
                                fig.add_annotation(
                                    dict(x=df[time_col].iloc[i], y=wind_max * 1.2, 
                                         text="↑", showarrow=False,
                                         font=dict(size=12, color="#007BFF"), 
                                         textangle=df['Wind_Deg'].iloc[i]+180, 
                                         xref="x1", yref="y1")
                                )
                
                # 하단: 파도
                if 'Waves(m)' in df.columns:
                    fig.add_trace(
                        go.Scatter(x=df[time_col], y=df['Waves(m)'], 
                                  name="Waves", line=dict(color='royalblue', width=3)), 
                        row=2, col=1
                    )
                
                if 'Max Waves(m)' in df.columns:
                    fig.add_trace(
                        go.Scatter(x=df[time_col], y=df['Max Waves(m)'], 
                                  name="Max Waves", line=dict(color='navy', width=1, dash='dot')), 
                        row=2, col=1
                    )
                
                if 'Swell(m)' in df.columns:
                    fig.add_trace(
                        go.Scatter(x=df[time_col], y=df['Swell(m)'], 
                                  name="Swell", line=dict(color='skyblue', dash='dash')), 
                        row=2, col=1
                    )
                
                # 파도 방향 화살표
                if 'Wave_Deg' in df.columns and 'Max Waves(m)' in df.columns:
                    y_max_wave = df['Max Waves(m)'].max()
                    if pd.notna(y_max_wave):
                        for i in range(len(df)):
                            if pd.notna(df['Wave_Deg'].iloc[i]):
                                fig.add_annotation(
                                    dict(x=df[time_col].iloc[i], y=y_max_wave * 1.2, 
                                         text="↑", showarrow=False,
                                         font=dict(size=12, color="#28A745"), 
                                         textangle=df['Wave_Deg'].iloc[i]+180, 
                                         xref="x2", yref="y2")
                                )
                
                # 날짜 구분 배경
                for i, day in enumerate(df[time_col].dt.date.unique()):
                    if i % 2 == 0:
                        fig.add_vrect(
                            x0=str(day), x1=str(day + timedelta(days=1)), 
                            fillcolor="gray", opacity=0.07, layer="below", line_width=0
                        )
                
                fig.update_layout(
                    height=800, 
                    hovermode="x unified", 
                    legend=dict(orientation="h", y=1.05)
                )
                fig.update_xaxes(tickformat="%d일\n%H:%M", dtick=21600000, showgrid=True, row=1, col=1)
                fig.update_xaxes(tickformat="%d일\n%H:%M", dtick=21600000, showgrid=True, row=2, col=1)
                
                # Y축 범위 설정
                if 'Wind Speed(kts)' in df.columns:
                    wind_max = df['Wind Speed(kts)'].max()
                    if pd.notna(wind_max):
                        fig.update_yaxes(range=[0, wind_max * 1.4], row=1, col=1)
                
                if 'Max Waves(m)' in df.columns:
                    wave_max = df['Max Waves(m)'].max()
                    if pd.notna(wave_max):
                        fig.update_yaxes(range=[0, wave_max * 1.4], row=2, col=1)
                
                st.plotly_chart(fig, use_container_width=True)
