import streamlit as st
import requests
import pandas as pd
import xarray as xr
import tempfile
import os
import math
from datetime import datetime, timedelta, timezone
import concurrent.futures
import plotly.graph_objects as go
from plotly.subplots import make_subplots

# --------------------------------------------------------------------------------
# 1. 페이지 및 세션 설정
# --------------------------------------------------------------------------------
st.set_page_config(page_title="Captain Park's Pro NOAA Parser", layout="wide")

if 'lat' not in st.session_state: st.session_state.lat = 31.8700
if 'lon' not in st.session_state: st.session_state.lon = 126.7700
if 'offset' not in st.session_state: st.session_state.offset = 9

# NOAA GFS Filter Base URLs
URL_ATMOS = "https://nomads.ncep.noaa.gov/cgi-bin/filter_gfs_0p25.pl"
URL_WAVE = "https://nomads.ncep.noaa.gov/cgi-bin/filter_gfswave.pl"

# --------------------------------------------------------------------------------
# 2. 핵심 로직: Cycle 동기화 및 다운로드 URL 생성
# --------------------------------------------------------------------------------
def get_latest_synced_cycle():
    """
    Atmosphere와 Wave 모델 데이터가 모두 존재하는 최신 Cycle을 찾습니다 (Option A).
    """
    # 현재 UTC 시간
    now_utc = datetime.now(timezone.utc)
    
    # 가능한 Cycle 시간들 (오늘, 어제 등 최근 24시간 커버)
    candidates = []
    for i in range(0, 24, 6): # 6시간 단위로 뒤로 가며 탐색
        check_time = now_utc - timedelta(hours=i)
        cycle_hour = (check_time.hour // 6) * 6
        cycle_date = check_time.strftime("%Y%m%d")
        cycle_str = f"{cycle_date}{cycle_hour:02d}"
        candidates.append(cycle_str)

    # 헤더 체크용 User-Agent (차단 방지)
    headers = {'User-Agent': 'Mozilla/5.0'}

    for cycle in candidates:
        # 테스트용 URL (f000 파일 존재 여부 확인)
        # GFS Atmosphere Pattern: gfs.tCCz.pgrb2.0p25.f000
        url_atmos_check = f"{URL_ATMOS}?file=gfs.t{cycle[-2:]}z.pgrb2.0p25.f000&all_var=on&subregion=&leftlon=0&rightlon=1&toplat=1&bottomlat=0"
        # GFS Wave Pattern: gfswave.tCCz.global.0p25.f000.grib2
        url_wave_check = f"{URL_WAVE}?file=gfswave.t{cycle[-2:]}z.global.0p25.f000.grib2&all_var=on&subregion=&leftlon=0&rightlon=1&toplat=1&bottomlat=0"

        try:
            r_atm = requests.head(url_atmos_check, headers=headers, timeout=2)
            r_wav = requests.head(url_wave_check, headers=headers, timeout=2)
            
            if r_atm.status_code == 200 and r_wav.status_code == 200:
                return cycle # 동기화된 Cycle 발견
        except:
            continue
            
    return None

def download_file(url, params):
    """
    지정된 파라미터로 GRIB 파일을 다운로드하여 임시 파일 경로를 반환합니다.
    """
    try:
        response = requests.get(url, params=params, timeout=10)
        response.raise_for_status()
        
        # 임시 파일 생성 (GRIB2는 바이너리)
        fd, path = tempfile.mkstemp(suffix=".grib2")
        with os.fdopen(fd, 'wb') as tmp:
            tmp.write(response.content)
        return path
    except Exception as e:
        return None

# --------------------------------------------------------------------------------
# 3. 데이터 파싱 및 처리 (xarray + cfgrib)
# --------------------------------------------------------------------------------
def parse_single_timestep(cycle, forecast_hour, lat, lon):
    """
    특정 예측 시간(fXXX)의 대기 및 파도 데이터를 다운로드하고 파싱합니다.
    """
    cycle_date = cycle[:8]
    cycle_time = cycle[8:]
    f_str = f"f{forecast_hour:03d}"
    
    # Subregion 설정 (메모리 절약을 위해 타겟 지점 ±0.5도만 다운로드)
    # NOAA 필터는 leftlon, rightlon, toplat, bottomlat 필요
    # GFS는 0~360 경도 체계일 수 있으므로 lon 변환 주의 (여기선 NOAA 필터가 스마트하게 처리하길 기대하거나 -180~180 대응)
    # 안전하게: Lon을 0~360으로 변환해서 요청할 수도 있으나, NOAA 필터는 보통 입력 그대로 받음.
    # 여기서는 단순하게 ±1도 범위 설정
    margin = 0.5
    params_base = {
        'subregion': '',
        'toplat': lat + margin,
        'bottomlat': lat - margin,
        'leftlon': lon - margin,
        'rightlon': lon + margin
    }

    # --- A. Atmosphere Request ---
    # Variable: PRMSL (Mean sea level pressure)
    # Level: mean sea level
    params_atm = params_base.copy()
    params_atm['file'] = f"gfs.t{cycle_time}z.pgrb2.0p25.{f_str}"
    params_atm['var_PRMSL'] = 'on'
    params_atm['lev_mean_sea_level'] = 'on'

    # --- B. Wave Request ---
    # Variables: UGRD, VGRD, HTSGW, DIRPW, PERPW (Level: surface)
    #            SWELL, SWDIR (Level: ordered sequence of data 1)
    params_wav = params_base.copy()
    params_wav['file'] = f"gfswave.t{cycle_time}z.global.0p25.{f_str}.grib2"
    
    # Level: surface
    params_wav['var_UGRD'] = 'on' # Wind U
    params_wav['var_VGRD'] = 'on' # Wind V
    params_wav['var_HTSGW'] = 'on' # Sig Wave Height
    params_wav['var_DIRPW'] = 'on' # Pri Wave Dir
    params_wav['var_PERPW'] = 'on' # Pri Wave Period
    params_wav['lev_surface'] = 'on'

    # Level: ordered sequence 1 (Swell)
    # 주의: NOAA CGI URL 구조상 변수명과 레벨을 조합해야 함.
    # URL 쿼리 스트링을 직접 구성하는 것이 안전할 수 있음.
    # requests params는 딕셔너리라 중복 키 처리가 까다로울 수 있으므로,
    # Swell 관련은 별도 처리하거나 params에 추가
    params_wav['var_SWELL'] = 'on'
    params_wav['var_SWDIR'] = 'on'
    params_wav['lev_ordered_sequence_of_data'] = 'on' # grib filter에서 1번 시퀀스 선택 옵션
    
    # 다운로드 (병렬 처리를 위해 함수 내부에서 수행)
    path_atm = download_file(URL_ATMOS, params_atm)
    path_wav = download_file(URL_WAVE, params_wav)
    
    if not path_atm or not path_wav:
        # 실패 시 임시 파일 정리
        if path_atm: os.remove(path_atm)
        if path_wav: os.remove(path_wav)
        return None

    # xarray로 데이터 읽기
    try:
        # 1. Atmosphere
        ds_atm = xr.open_dataset(path_atm, engine='cfgrib')
        prmsl = ds_atm['prmsl'].sel(latitude=lat, longitude=lon, method='nearest').values.item() / 100.0 # Pa -> hPa
        ds_atm.close()

        # 2. Wave
        # GRIB 파일에 서로 다른 stepType이나 level이 섞여 있으면 cfgrib이 여러 dataset으로 분리해서 로드할 수 있음
        # filter_by_keys를 사용하여 명시적으로 로드하거나, try-except로 처리
        
        # Surface 데이터 읽기 (Wind, Wave)
        ds_wav_surf = xr.open_dataset(path_wav, engine='cfgrib', 
                                      backend_kwargs={'filter_by_keys': {'typeOfLevel': 'surface'}})
        
        wind_u = ds_wav_surf['u'].sel(latitude=lat, longitude=lon, method='nearest').values.item()
        wind_v = ds_wav_surf['v'].sel(latitude=lat, longitude=lon, method='nearest').values.item()
        sig_wave = ds_wav_surf['shcww'].sel(latitude=lat, longitude=lon, method='nearest').values.item() # HTSGW name in cfgrib
        prim_wave_dir = ds_wav_surf['dPw'].sel(latitude=lat, longitude=lon, method='nearest').values.item() # DIRPW name
        prim_wave_per = ds_wav_surf['pPw'].sel(latitude=lat, longitude=lon, method='nearest').values.item() # PERPW name
        ds_wav_surf.close()

        # Swell 데이터 읽기 (orderedSequence 1)
        # cfgrib에서 orderedSequenceOfData 레벨을 어떻게 잡는지 확인 필요. 
        # 통상적으로 typeOfLevel='orderedSequenceOfData'로 잡힐 것임.
        ds_wav_swell = xr.open_dataset(path_wav, engine='cfgrib', 
                                       backend_kwargs={'filter_by_keys': {'typeOfLevel': 'orderedSequenceOfData'}})
        
        # Swell 파라미터 이름 확인 (SWELL -> ssw, SWDIR -> dsw 등 cfgrib 매핑 확인)
        # 보통 HTSGW for swell is 'shts' or similar. 
        # GFS Wave grib structure: paramId 84.0.5 -> SWELL
        swell_h = ds_wav_swell['shts'].sel(latitude=lat, longitude=lon, method='nearest').values.item()
        swell_dir = ds_wav_swell['dsw'].sel(latitude=lat, longitude=lon, method='nearest').values.item()
        ds_wav_swell.close()

        # 시간 정보 계산
        valid_time = datetime.strptime(f"{cycle_date}{cycle_time}", "%Y%m%d%H") + timedelta(hours=forecast_hour)
        
        return {
            "ts": valid_time,
            "pressure": prmsl,
            "wind_u": wind_u,
            "wind_v": wind_v,
            "waves": sig_wave,
            "wave_dir": prim_wave_dir,
            "wave_period": prim_wave_per,
            "swell": swell_h,
            "swell_dir": swell_dir
        }

    except Exception as e:
        # st.error(f"Error parsing f{forecast_hour}: {e}")
        return None
    finally:
        # 파일 삭제 (필수)
        if os.path.exists(path_atm): os.remove(path_atm)
        if os.path.exists(path_wav): os.remove(path_wav)

# --------------------------------------------------------------------------------
# 4. 유틸리티 (방향 텍스트, 화살표 등)
# --------------------------------------------------------------------------------
MS_TO_KNOTS = 1.94384

def get_direction_text(deg):
    directions = ['N', 'NNE', 'NE', 'ENE', 'E', 'ESE', 'SE', 'SSE', 'S', 'SSW', 'SW', 'WSW', 'W', 'WNW', 'NW', 'NNW']
    idx = int((deg + 11.25) / 22.5) % 16
    return directions[idx]

def get_arrow_html(deg, color="#007BFF"):
    # 180도 반전 (불어오는 쪽 표시)
    rotate_deg = (deg + 180) % 360
    return f'<span style="display:inline-block; transform:rotate({rotate_deg}deg); font-size:16px; color:{color};">↑</span>'

# --------------------------------------------------------------------------------
# 5. 메인 UI
# --------------------------------------------------------------------------------
st.title("⚓ Pro NOAA GFS Parser (On-demand)")

with st.container():
    col1, col2, col3, col4 = st.columns([2, 2, 2, 1])
    with col1: st.session_state.lat = st.number_input("위도 (Lat)", value=st.session_state.lat, format="%.4f")
    with col2: st.session_state.lon = st.number_input("경도 (Lon)", value=st.session_state.lon, format="%.4f")
    with col3:
        opts = list(range(13, -13, -1))
        st.session_state.offset = st.selectbox("시간대 (UTC)", options=opts, index=opts.index(st.session_state.offset))
    with col4:
        st.write(" ")
        fetch_btn = st.button("데이터 수신", use_container_width=True)

if fetch_btn:
    progress_bar = st.progress(0, text="최신 예보 Cycle을 찾는 중...")
    
    # 1. Sync Cycle 찾기
    cycle = get_latest_synced_cycle()
    
    if not cycle:
        st.error("최신 데이터를 찾을 수 없습니다 (NOAA 서버 응답 없음 또는 동기화 실패). 잠시 후 다시 시도해주세요.")
    else:
        st.success(f"동기화된 최신 Cycle 발견: {cycle} (GFS & Wave)")
        
        # 2. 다운로드할 시간대 설정 (3일치, 3시간 간격 = 24개 포인트)
        # 7일치(56개)는 속도 문제로 일단 3일로 제한하거나, 원하시면 늘릴 수 있음.
        # 여기서는 속도와 안정성을 위해 72시간(3일)으로 설정 (Pro 모드여도 On-demand 한계 고려)
        forecast_hours = list(range(0, 75, 3)) 
        results = []
        
        progress_bar.progress(10, text=f"데이터 다운로드 및 파싱 시작 ({len(forecast_hours)} steps)...")

        # 3. 병렬 다운로드 및 파싱 실행
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
            # Lat/Lon 인자 전달
            futures = {executor.submit(parse_single_timestep, cycle, fh, st.session_state.lat, st.session_state.lon): fh for fh in forecast_hours}
            
            completed_count = 0
            for future in concurrent.futures.as_completed(futures):
                data = future.result()
                if data:
                    results.append(data)
                
                completed_count += 1
                prog = 10 + int((completed_count / len(forecast_hours)) * 90)
                progress_bar.progress(prog, text=f"처리 중... {completed_count}/{len(forecast_hours)}")

        progress_bar.empty()

        if not results:
            st.error("데이터를 가져오는 데 실패했습니다.")
        else:
            # 시간순 정렬
            results.sort(key=lambda x: x['ts'])
            
            # DataFrame 생성 및 사용자 시간대 보정
            df = pd.DataFrame(results)
            
            # UTC -> 사용자 선택 시간대 변환
            time_label = f"Time (UTC{st.session_state.offset:+})"
            df['display_time'] = df['ts'] + timedelta(hours=st.session_state.offset)
            
            # 단위 변환 및 파생 변수 계산
            df['Wind Speed(kts)'] = ((df['wind_u']**2 + df['wind_v']**2)**0.5 * MS_TO_KNOTS).round(1)
            df['Wind_Deg'] = df.apply(lambda r: (math.degrees(math.atan2(r['wind_u'], r['wind_v'])) + 180) % 360, axis=1)
            
            df['Max Waves(m)'] = (df['waves'] * 1.6).round(1)
            df['Waves(m)'] = df['waves'].round(1)
            df['Swell(m)'] = df['swell'].round(1)
            df['Pressure(hPa)'] = df['pressure'].round(1)
            
            # 테이블용 HTML 생성
            df['Wind Direction'] = df.apply(lambda r: f"{r['Wind_Deg']:.1f}° {get_direction_text(r['Wind_Deg'])} {get_arrow_html(r['Wind_Deg'])}", axis=1)
            df['Wave Direction'] = df.apply(lambda r: f"{r['wave_dir']:.1f}° {get_direction_text(r['wave_dir'])} {get_arrow_html(r['wave_dir'], '#28A745')}", axis=1)

            # 출력용 컬럼 정리
            final_df = df[[
                'display_time', 'Pressure(hPa)', 'Wind Direction', 'Wind Speed(kts)', 
                'Wave Direction', 'Waves(m)', 'Max Waves(m)', 'Swell(m)'
            ]].rename(columns={'display_time': time_label})

            # --------------------------------------------------------------------
            # 결과 화면 (기존 스타일 유지)
            # --------------------------------------------------------------------
            
            # 인쇄 최적화 CSS
            st.markdown("""
                <style>
                @media print {
                    section[data-testid="stSidebar"], .stButton, .stSelectbox, .stNumberInput, 
                    header, [data-testid="stHeader"], [role="tablist"], footer, .stSpinner, .stProgress { display: none !important; }
                    .main .block-container { padding: 0 !important; margin: 0 !important; }
                    table { font-size: 10px !important; width: 100% !important; }
                    .js-plotly-plot { height: 600px !important; width: 100% !important; }
                }
                </style>
            """, unsafe_allow_html=True)

            tab1, tab2 = st.tabs(["📊 데이터 테이블", "📈 시각화 그래프"])

            with tab1:
                st.subheader("데이터 테이블 리포트")
                st.write(final_df.to_html(escape=False, index=False, justify='center'), unsafe_allow_html=True)

            with tab2:
                st.subheader("그래프 분석 리포트")
                
                fig = make_subplots(rows=2, cols=1, shared_xaxes=False, vertical_spacing=0.2,
                                    subplot_titles=("Wind Speed & Direction (kts)", "Wave Height & Direction (m)"))

                # Wind
                fig.add_trace(go.Scatter(x=final_df[time_label], y=final_df['Wind Speed(kts)'], name="Wind", line=dict(color='firebrick')), row=1, col=1)
                
                # Arrows (Wind) - 180도 반전 적용됨
                for i in range(len(df)):
                    fig.add_annotation(dict(x=final_df[time_label].iloc[i], y=final_df['Wind Speed(kts)'].max() * 1.2, 
                                            text="↑", showarrow=False, font=dict(size=14, color="#007BFF"), 
                                            textangle=df['Wind_Deg'].iloc[i] + 180, xref="x1", yref="y1"))

                # Waves
                fig.add_trace(go.Scatter(x=final_df[time_label], y=final_df['Waves(m)'], name="Waves", line=dict(color='royalblue', width=3)), row=2, col=1)
                fig.add_trace(go.Scatter(x=final_df[time_label], y=final_df['Max Waves(m)'], name="Max Waves", line=dict(color='navy', width=1, dash='dot')), row=2, col=1)
                fig.add_trace(go.Scatter(x=final_df[time_label], y=final_df['Swell(m)'], name="Swell", line=dict(color='skyblue', dash='dash')), row=2, col=1)
                
                # Arrows (Wave) - 180도 반전 적용됨
                for i in range(len(df)):
                    fig.add_annotation(dict(x=final_df[time_label].iloc[i], y=final_df['Max Waves(m)'].max() * 1.2, 
                                            text="↑", showarrow=False, font=dict(size=14, color="#28A745"), 
                                            textangle=df['wave_dir'].iloc[i] + 180, xref="x2", yref="y2"))

                # Day Separator
                for i, day in enumerate(final_df[time_label].dt.date.unique()):
                    if i % 2 == 0: fig.add_vrect(x0=str(day), x1=str(day + timedelta(days=1)), fillcolor="gray", opacity=0.07, layer="below", line_width=0)

                fig.update_layout(height=800, hovermode="x unified", legend=dict(orientation="h", y=1.05))
                fig.update_xaxes(tickformat="%d일\n%H:%M", dtick=21600000, showgrid=True)
                
                st.plotly_chart(fig, use_container_width=True)