"""
===============================================================================
Project: SnowBall Quant Terminal (Web Edition)
Author: TeamChilli
Version: 9.0 (Masterpiece)
Description: 
    S&P 900 종목 기반 6-Pillar (건전성, 수익성, 성장성, 가성비, 모멘텀, 환원율) 
    퀀트 분석 웹 애플리케이션. 
    관리자 모드 전용 시크릿 URL을 통한 글로벌 데이터 동기화 지원.
===============================================================================
"""

import streamlit as st
import yfinance as yf
import pandas as pd
import numpy as np
import requests
import io
import time
import datetime
import pytz
import os
import pickle
import logging

# =============================================================================
# 1. 시스템 설정 및 로깅 초기화
# =============================================================================
st.set_page_config(
    page_title="SnowBall", 
    page_icon="☃", 
    layout="wide",
    initial_sidebar_state="collapsed"
)

# 로깅 설정 (서버 백그라운드에서 동작 상태를 추적하기 위함)
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

SHARED_FILE = "snowball_shared_data.pkl"
ADMIN_SECRET_CODE = "chillixlaclffl"

# =============================================================================
# 2. 커스텀 CSS UI 스타일링
# =============================================================================
# 사이드바를 완전히 숨기고 메인 컨텐츠 영역을 극대화하는 CSS 트릭
st.markdown("""
    <style>
        [data-testid="collapsedControl"] { display: none; }
        section[data-testid="stSidebar"] { display: none; }
        .stMetric { background-color: #1E1E1E; padding: 15px; border-radius: 10px; border: 1px solid #333; }
        .stMetric label { color: #A0A0A0 !important; font-weight: bold; }
        .title-text { color: #E0E0E0; font-family: 'Helvetica Neue', sans-serif; }
    </style>
""", unsafe_allow_html=True)


# =============================================================================
# 3. 유틸리티 및 헬퍼 함수
# =============================================================================
def save_global_data(df, sec_s, trk_s, updated_time):
    """서버 내 공용 하드디스크(Pickle)에 퀀트 데이터를 저장합니다."""
    data = {
        'df': df,
        'sec_s': sec_s,
        'trk_s': trk_s,
        'updated_time': updated_time
    }
    try:
        with open(SHARED_FILE, 'wb') as f:
            pickle.dump(data, f)
        logger.info("Global data saved successfully.")
    except Exception as e:
        logger.error(f"Failed to save global data: {e}")

def load_global_data():
    """서버 내 공용 하드디스크에서 최신 퀀트 데이터를 불러옵니다."""
    if os.path.exists(SHARED_FILE):
        try:
            with open(SHARED_FILE, 'rb') as f:
                logger.info("Global data loaded successfully.")
                return pickle.load(f)
        except Exception as e:
            logger.error(f"Failed to load global data: {e}")
            return None
    return None

def get_grade(z):
    """Z-Score를 기반으로 직관적인 알파벳 등급(A~F)을 부여합니다."""
    if z >= 1.0: return 'A'
    elif z >= 0.3: return 'B'
    elif z >= -0.3: return 'C'
    elif z >= -1.0: return 'D'
    else: return 'F'

def get_rating(score):
    """최종 100점 만점 점수를 바탕으로 투자의견을 산출합니다."""
    if score >= 80: return 'Strong Buy'
    elif score >= 60: return 'Buy'
    elif score >= 40: return 'Hold'
    elif score >= 20: return 'Sell'
    else: return 'Strong Sell'

def get_trade_day():
    """미국 동부 시간(EST) 기준 프리장 오픈 시간(새벽 4시)을 계산합니다."""
    tz = pytz.timezone('US/Eastern')
    now = datetime.datetime.now(tz)
    if now.hour < 4:
        return (now - datetime.timedelta(days=1)).strftime('%Y-%m-%d')
    return now.strftime('%Y-%m-%d')


# =============================================================================
# 4. 퀀트 코어 엔진 (데이터 수집 및 점수 산출)
# =============================================================================
def fetch_sp900_tickers(session):
    """위키피디아에서 S&P 500 및 S&P 400(MidCap) 티커 목록을 스크래핑합니다."""
    logger.info("Fetching S&P 900 tickers from Wikipedia...")
    try:
        def get_t(url):
            res = requests.get(url, headers=session.headers)
            df = pd.read_html(io.StringIO(res.text))[0]
            return df['Symbol' if 'Symbol' in df.columns else 'Ticker symbol'].tolist()
        
        tickers = list(set(get_t('https://en.wikipedia.org/wiki/List_of_S%26P_500_companies') + 
                           get_t('https://en.wikipedia.org/wiki/List_of_S%26P_400_companies')))
        return [t.replace('.', '-') for t in tickers]
    except Exception as e:
        logger.error(f"Failed to fetch tickers: {e}")
        return []

def calculate_single_stock_raw_factors(tk):
    """단일 종목의 Yahoo Finance 데이터를 조회하여 Raw Factor를 계산합니다."""
    # Yahoo 통신은 순정 상태로 진행하여 차단 위험 최소화
    s = yf.Ticker(tk)
    info = s.info
    hist = s.history(period="1y")
    
    # 상장폐지 또는 데이터 부족 종목 필터링
    if hist.empty or len(hist) < 22:
        return None
    if 'sector' not in info and 'currentPrice' not in info:
        return None
        
    c_name = info.get('shortName', info.get('longName', tk))
    sector = info.get('sector', 'Unknown')
    track = 'FIN' if sector in ['Financial Services', 'Real Estate'] else 'STD'
    
    # -------------------------------------------------------------------------
    # Factor 1: 건전성 (Health) 
    # -------------------------------------------------------------------------
    debt_eq = info.get('debtToEquity')
    debt_to_asset = (float(debt_eq) / 100.0) / (1 + float(debt_eq) / 100.0) * 100.0 if debt_eq and float(debt_eq) >= 0 else 100.0
    
    ebitda_val = info.get('ebitda', 1.0)
    ie_val = info.get('interestExpense', 1.0) if info.get('interestExpense') not in [None, 0] else 1.0
    icr_val = ebitda_val / ie_val

    # -------------------------------------------------------------------------
    # Factor 2 & 3 & 4: 수익성(Profitability), 가성비(Value), 환원율(Yield)
    # -------------------------------------------------------------------------
    mcap = info.get('marketCap', 1) if info.get('marketCap') else 1
    div_yield = float(info.get('dividendYield') or 0.0)
    
    if track == 'FIN':
        # 금융/리츠 섹터 로직
        hybrid_prf = float(info.get('returnOnEquity') or 0.0)
        total_shareholder_yield = div_yield
        
        pe = info.get('trailingPE')
        pb = info.get('priceToBook')
        val_score = ((1/float(pe) if pe and float(pe) > 0 else 0)*0.5) + ((1/float(pb) if pb and float(pb) > 0 else 0)*0.5)
    else:
        # 스탠다드 섹터 로직
        fcf = float(info.get('freeCashflow') or 0.0)
        fcf_yield = fcf / mcap
        total_shareholder_yield = div_yield + max(0, fcf_yield)
        hybrid_prf = (float(info.get('returnOnAssets', 0))*0.5) + (float(info.get('operatingMargins', 0))*0.5)

        # [팀칠리 맞춤 가성비 튜닝] PEG(40%) + Fwd PER(30%) + EV/EBITDA(30%)
        peg = info.get('pegRatio')
        val_peg = 1 / float(peg) if peg and float(peg) > 0 else 0
        
        fwd_pe = info.get('forwardPE')
        val_fwd_pe = 1 / float(fwd_pe) if fwd_pe and float(fwd_pe) > 0 else 0
        
        ev_ebitda = info.get('enterpriseToEbitda')
        val_ev_ebitda = 1 / float(ev_ebitda) if ev_ebitda and float(ev_ebitda) > 0 else 0
        
        val_score = (val_peg * 0.4) + (val_fwd_pe * 0.3) + (val_ev_ebitda * 0.3)

    # -------------------------------------------------------------------------
    # Factor 5: 성장성 (Growth)
    # -------------------------------------------------------------------------
    rev_g = max(-0.5, min(0.5, float(info.get('revenueGrowth') or 0.0)))
    earn_g = max(-0.5, min(0.5, float(info.get('earningsGrowth') or 0.0)))
    grw_score = (rev_g + earn_g) / 2
    
    # -------------------------------------------------------------------------
    # Factor 6: 모멘텀 (Momentum) - 6개월 트렌드 추종 (노이즈 방지)
    # -------------------------------------------------------------------------
    if len(hist) >= 126:
        mom_score = (hist['Close'].iloc[-1] / hist['Close'].iloc[-126]) - 1
    else:
        mom_score = (hist['Close'].iloc[-1] / hist['Close'].iloc[0]) - 1

    payout_ratio = float(info.get('payoutRatio') or 0.0)

    return {
        '종목': tk, '기업명': c_name, '섹터': sector, '트랙': track, 'PayoutRatio': payout_ratio,
        'VAL': val_score, 'MOM': mom_score, 'GRW': grw_score, 'PRF': hybrid_prf, 
        'YLD': total_shareholder_yield, 'DEBT': debt_to_asset, 'ICR': icr_val
    }

@st.cache_data(show_spinner=False)
def process_market_data(trade_day):
    """전체 시장 데이터를 수집하고 Z-Score 정규화를 거쳐 최종 데이터프레임을 반환합니다."""
    session = requests.Session()
    session.headers.update({'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'})
    
    tickers = fetch_sp900_tickers(session)
    if not tickers:
        raise ValueError("티커 목록을 가져오지 못했습니다.")

    temp_list = []
    progress_bar = st.progress(0)
    status_text = st.empty()
    total = len(tickers)

    for i, tk in enumerate(tickers, 1):
        time.sleep(0.1) # 야후 파이낸스 차단 회피를 위한 매너 타임
        try:
            raw_data = calculate_single_stock_raw_factors(tk)
            if raw_data:
                temp_list.append(raw_data)
        except Exception as e:
            logger.warning(f"Error fetching {tk}: {e}")
            pass
        
        if i % 10 == 0 or i == total:
            progress_bar.progress(i / total)
            status_text.text(f"시장 데이터 딥다이브 중... ({i}/{total})")

    progress_bar.empty()
    status_text.empty()

    if len(temp_list) == 0:
        raise ValueError("야후 파이낸스 서버가 접근을 전면 차단했습니다. (Too Many Requests)")

    # 1. 데이터프레임 변환 및 이상치(Outlier) 윈저라이징
    df = pd.DataFrame(temp_list).replace([np.inf, -np.inf], 0).fillna(0)
    cols = ['VAL', 'MOM', 'GRW', 'PRF', 'YLD', 'DEBT', 'ICR']
    for c in cols: 
        df[c] = df[c].clip(df[c].quantile(0.01), df[c].quantile(0.99))

    # 2. 섹터별 & 트랙별 통계량 추출 (상대평가 기준점)
    sector_stats = {sct: {c: {'mean': df[df['섹터']==sct][c].mean(), 'std': df[df['섹터']==sct][c].std()} for c in cols} for sct in df['섹터'].unique()}
    track_stats = {trk: {c: {'mean': df[df['트랙']==trk][c].mean(), 'std': df[df['트랙']==trk][c].std()} for c in cols} for trk in df['트랙'].unique()}

    # 3. Z-Score 정규화 (표준정규분포 변환)
    z_data = {}
    for c in cols:
        sign = -1 if c in ['DEBT'] else 1 # 부채는 낮을수록 좋음
        sct_z = (df[c] - df.groupby('섹터')[c].transform('mean')) / (df.groupby('섹터')[c].transform('std') + 1e-9)
        trk_z = (df[c] - df.groupby('트랙')[c].transform('mean')) / (df.groupby('트랙')[c].transform('std') + 1e-9)
        z_data[c] = ((sct_z * 0.5) + (trk_z * 0.5)) * sign
        z_data[c] = z_data[c].clip(-3.0, 3.0)

    z_hlt = (z_data['DEBT'] + z_data['ICR']) / 2
    
    # 개별 종목 검색 시 즉시 로드를 위해 Z-score 보존
    df['Z_HLT'] = z_hlt
    df['Z_PRF'] = z_data['PRF']
    df['Z_GRW'] = z_data['GRW']
    df['Z_VAL'] = z_data['VAL']
    df['Z_MOM'] = z_data['MOM']
    df['Z_YLD'] = z_data['YLD']
    
    # 4. 페널티 검열 (부채 폭탄 및 배당 함정 필터링)
    penalty, trap_penalty = [], []
    for i, row in df.iterrows():
        p = 0.15 * ((row['DEBT']/50)**2.5) if row['트랙'] == 'STD' and row['DEBT'] >= 50 else 0
        t_p = 2.0 if row['PayoutRatio'] > 1.0 or row['PayoutRatio'] < 0 else 0
        penalty.append(p)
        trap_penalty.append(t_p)

    # =========================================================================
    # 🌟 [팀칠리 커스텀 가중치] 
    # 가성비 20% | 모멘텀 15% | 건전성 20% | 수익성 20% | 성장성 15% | 환원율 10%
    # =========================================================================
    df['Base'] = (z_data['VAL']*0.20 + z_data['MOM']*0.15 + z_data['GRW']*0.15 + 
                  z_data['PRF']*0.20 + z_data['YLD']*0.10 + z_hlt*0.20) - penalty - trap_penalty
    
    df['최종점수'] = round(((df['Base'] - (-3.0)) / 6.0) * 100, 1).clip(0, 100)
    df['투자의견'] = df['최종점수'].apply(get_rating)

    df['건전성'] = df['Z_HLT'].apply(get_grade)
    df['수익성'] = df['Z_PRF'].apply(get_grade)
    df['성장성'] = df['Z_GRW'].apply(get_grade)
    df['가성비'] = df['Z_VAL'].apply(get_grade)
    df['모멘텀'] = df['Z_MOM'].apply(get_grade)
    df['환원율'] = df['Z_YLD'].apply(get_grade)

    df = df.sort_values('최종점수', ascending=False).reset_index(drop=True)
    df.insert(0, '순위', range(1, len(df) + 1))
    
    kst = pytz.timezone('Asia/Seoul')
    update_time = datetime.datetime.now(kst).strftime('%Y-%m-%d %H:%M:%S KST')
    
    return df, sector_stats, track_stats, update_time


# =============================================================================
# 5. 세션 상태 및 라우팅 컨트롤러 (시크릿 URL 로직)
# =============================================================================
if 'quant_data' not in st.session_state: 
    st.session_state['quant_data'] = None
    st.session_state['sector_stats'] = None
    st.session_state['track_stats'] = None
    st.session_state['last_updated'] = "수집 전"
    
    # 앱 부팅 시 글로벌 하드디스크(Pickle)에 데이터가 있다면 즉시 메모리로 로드
    global_data = load_global_data()
    if global_data is not None:
        st.session_state['quant_data'] = global_data['df']
        st.session_state['sector_stats'] = global_data['sec_s']
        st.session_state['track_stats'] = global_data['trk_s']
        st.session_state['last_updated'] = global_data['updated_time']

# 사이드바 로그인 없이 쿼리 파라미터로만 Admin 권한 획득
if 'is_admin' not in st.session_state: 
    st.session_state['is_admin'] = False

# 최신 Streamlit 문법을 사용한 Query Params 조회
query_params = st.query_params
if query_params.get("admin") == ADMIN_SECRET_CODE:
    st.session_state['is_admin'] = True

# 데이터가 비어있을 때 (최초 세팅 전) 관리자용 설정 화면 표시
if st.session_state['quant_data'] is None:
    if st.session_state['is_admin']:
        st.title("🛠️ SnowBall 데이터 초기화 센터")
        st.info("현재 서버에 공용 데이터가 없습니다. 운영 방식을 선택해주세요.")

        col1, col2 = st.columns(2)
        with col1:
            st.subheader("1️⃣ 신규 자동 수집 (야후 API)")
            st.caption("주의: 클라우드 IP 차단으로 실패할 확률이 높습니다.")
            if st.button("실시간 수집 가동", use_container_width=True):
                with st.spinner("야후 서버와 통신 중... (약 3~5분 소요)"):
                    try:
                        trade_day = get_trade_day()
                        df, sec_s, trk_s, updated_time = process_market_data(trade_day)
                        save_global_data(df, sec_s, trk_s, updated_time)
                        st.session_state['quant_data'] = df
                        st.session_state['sector_stats'] = sec_s
                        st.session_state['track_stats'] = trk_s
                        st.session_state['last_updated'] = updated_time
                        st.rerun()
                    except Exception as e:
                        st.error(f"서버 접근 차단 발생: {e}")
        
        with col2:
            st.subheader("2️⃣ 로컬 백업 업로드 (권장)")
            st.caption("로컬 추출기(extract_snowball.py)에서 생성된 파일을 올리세요.")
            uploaded_file = st.file_uploader("기존 엑셀/CSV 업로드", type=["xlsx", "csv"], label_visibility="collapsed")
            if uploaded_file is not None:
                try:
                    df = pd.read_csv(uploaded_file) if uploaded_file.name.endswith('.csv') else pd.read_excel(uploaded_file)
                    
                    # 수동 파일의 데이터 통계량 재계산
                    cols = ['VAL', 'MOM', 'GRW', 'PRF', 'YLD', 'DEBT', 'ICR']
                    sec_s = {sct: {c: {'mean': df[df['섹터']==sct][c].mean(), 'std': df[df['섹터']==sct][c].std()} for c in cols if c in df.columns} for sct in df['섹터'].unique()}
                    trk_s = {trk: {c: {'mean': df[df['트랙']==trk][c].mean(), 'std': df[df['트랙']==trk][c].std()} for c in cols if c in df.columns} for trk in df['트랙'].unique()} if '트랙' in df.columns else {}
                    
                    # 글로벌 파일로 영구 보존
                    save_global_data(df, sec_s, trk_s, "수동 파일 동기화 완료")
                    
                    st.session_state['quant_data'] = df
                    st.session_state['sector_stats'] = sec_s
                    st.session_state['track_stats'] = trk_s
                    st.session_state['last_updated'] = "수동 파일 동기화 완료"
                    st.success("로드 성공! 글로벌 환경에 즉시 적용되었습니다.")
                    time.sleep(1)
                    st.rerun()
                except Exception as e:
                    st.error(f"업로드 에러: {e}")
        
        st.markdown("<br><br><br><div style='text-align: center; color: #888; font-size: 12px;'>powered by TeamChilli</div>", unsafe_allow_html=True)
        st.stop()
        
    else:
        # 일반 사용자는 데이터가 없으면 대기 안내만 표시
        st.title("☃ SnowBall")
        st.info("관리자가 마켓 데이터를 준비하고 있습니다. 잠시 후 다시 접속해 주세요.")
        st.markdown("<br><br><br><div style='text-align: center; color: #888; font-size: 12px;'>powered by TeamChilli</div>", unsafe_allow_html=True)
        st.stop()


# =============================================================================
# 6. 메인 UI 화면 (데이터 로드 완료 상태)
# =============================================================================
st.title("☃ SnowBall")
st.caption(f"최근 데이터 동기화: {st.session_state['last_updated']}")

tab1, tab2, tab3 = st.tabs(["대시보드", "Snow Ball Quant TOP 100", "개별 종목 분석"])

# -----------------------------------------------------------------------------
# 탭 1: 대시보드 및 철학 안내
# -----------------------------------------------------------------------------
with tab1:
    st.subheader("SnowBall 평가 모델")
    st.markdown("""
    * **건전성:** 재무적으로 얼마나 안정적인지 판단
    * **수익성:** 얼마나 효율적으로 이익을 내는지를 판단
    * **성장성:** 매출과 이익, 펀더멘털 확장성
    * **모멘텀:** 시장의 중장기 트렌드 추세 판단
    * **가성비:** 실적이나 자산에 대비한 값어치를 판단
    * **환원율:** 주주에게 얼마나 적극적으로 이익을 돌려주는지를 판단
    """)
    st.divider()
    
    # Admin 접속 시에만 나타나는 데이터 갱신 패널
    if st.session_state['is_admin']:
        st.markdown("### 🛠️ [관리자 전용] 데이터 갱신 패널")
        col1, col2 = st.columns(2)
        with col1:
            st.markdown("##### 온라인 강제 갱신")
            if st.button("야후 API 강제 재수집", use_container_width=True):
                try:
                    trade_day = get_trade_day()
                    df, sec_s, trk_s, updated_time = process_market_data(trade_day)
                    save_global_data(df, sec_s, trk_s, updated_time)
                    st.session_state['quant_data'] = df
                    st.session_state['sector_stats'] = sec_s
                    st.session_state['track_stats'] = trk_s
                    st.session_state['last_updated'] = updated_time
                    st.success("글로벌 데이터가 갱신되었습니다.")
                    time.sleep(1)
                    st.rerun()
                except Exception:
                    st.error("야후 통신 실패")
            
            st.markdown("##### DB 파일 백업")
            if st.session_state['quant_data'] is not None:
                csv_data = st.session_state['quant_data'].to_csv(index=False).encode('utf-8-sig')
                st.download_button(
                    label="현재 데이터 다운로드 (CSV)", 
                    data=csv_data, 
                    file_name=f"SnowBall_{datetime.datetime.now().strftime('%Y%m%d')}.csv", 
                    mime="text/csv", 
                    use_container_width=True
                )
        with col2:
            st.markdown("##### 로컬 파일 수동 동기화")
            uploaded_file = st.file_uploader("백업 엑셀/CSV 업로드", type=["xlsx", "csv"], label_visibility="collapsed")
            if uploaded_file is not None:
                try:
                    df = pd.read_csv(uploaded_file) if uploaded_file.name.endswith('.csv') else pd.read_excel(uploaded_file)
                    
                    cols = ['VAL', 'MOM', 'GRW', 'PRF', 'YLD', 'DEBT', 'ICR']
                    sec_s = {sct: {c: {'mean': df[df['섹터']==sct][c].mean(), 'std': df[df['섹터']==sct][c].std()} for c in cols if c in df.columns} for sct in df['섹터'].unique()}
                    trk_s = {trk: {c: {'mean': df[df['트랙']==trk][c].mean(), 'std': df[df['트랙']==trk][c].std()} for c in cols if c in df.columns} for trk in df['트랙'].unique()} if '트랙' in df.columns else {}
                    
                    save_global_data(df, sec_s, trk_s, "수동 파일 동기화 완료")
                    
                    st.session_state['quant_data'] = df
                    st.session_state['sector_stats'] = sec_s
                    st.session_state['track_stats'] = trk_s
                    st.session_state['last_updated'] = "수동 파일 동기화 완료"
                    st.success("데이터 로드 성공! 손님들에게 즉시 노출됩니다.")
                    time.sleep(1)
                    st.rerun()
                except Exception as e:
                    st.error(f"업로드 에러: {e}")
    else:
        st.info("데이터가 백그라운드에 캐싱되어 안전하게 작동 중입니다. 탭을 이동하며 분석 결과를 확인하세요.")

# -----------------------------------------------------------------------------
# 탭 2: Snow Ball Quant TOP 100 
# -----------------------------------------------------------------------------
with tab2:
    st.subheader("🏆 Snow Ball Quant TOP 100")
    if st.session_state['quant_data'] is not None:
        # 노출할 핵심 컬럼만 추출하여 깔끔하게 렌더링
        display_cols = ['순위', '종목', '기업명', '최종점수', '투자의견', '섹터', '건전성', '수익성', '성장성', '가성비', '모멘텀', '환원율']
        st.dataframe(
            st.session_state['quant_data'][display_cols].head(100),
            use_container_width=True,
            hide_index=True,
            column_config={
                "순위": st.column_config.NumberColumn(width="small"),
                "종목": st.column_config.TextColumn(width="small"),
                "기업명": st.column_config.TextColumn(width="medium"),
                "최종점수": st.column_config.NumberColumn(width="small", format="%.1f"),
                "투자의견": st.column_config.TextColumn(width="small"),
                "섹터": st.column_config.TextColumn(width="medium"),
                "건전성": st.column_config.TextColumn(width="small"),
                "수익성": st.column_config.TextColumn(width="small"),
                "성장성": st.column_config.TextColumn(width="small"),
                "가성비": st.column_config.TextColumn(width="small"),
                "모멘텀": st.column_config.TextColumn(width="small"),
                "환원율": st.column_config.TextColumn(width="small")
            }
        )

# -----------------------------------------------------------------------------
# 탭 3: 개별 종목 딥다이브
# -----------------------------------------------------------------------------
with tab3:
    st.subheader("🔍 개별 종목 분석")
    with st.form("search_form"):
        ticker_input = st.text_input("분석할 티커를 입력하세요 (예: AAPL, MSFT, META)")
        submit_btn = st.form_submit_button("분석 시작")
        
    if submit_btn and ticker_input:
        tk = ticker_input.upper().strip()
        df = st.session_state['quant_data']
        
        # 1차 시도: 자체 DB (Memory) 내부 조회 - 야후 차단 원천 방어
        if df is not None and tk in df['종목'].values:
            with st.spinner("DB 최적화 로드 중..."):
                row = df[df['종목'] == tk].iloc[0]
                
                final_score = row['최종점수']
                rating = get_rating(final_score)
                c_name = row['기업명']
                sector = row['섹터']
                
                eval_scores = {
                    '가성비': row.get('Z_VAL', 0), '모멘텀': row.get('Z_MOM', 0), '성장성': row.get('Z_GRW', 0), 
                    '수익성': row.get('Z_PRF', 0), '환원율': row.get('Z_YLD', 0), '건전성': row.get('Z_HLT', 0)
                }
                best_p = max(eval_scores, key=eval_scores.get) if eval_scores else "N/A"
                worst_p = min(eval_scores, key=eval_scores.get) if eval_scores else "N/A"
                
                trap_penalty = row.get('TrapPenalty', 0)
                
                # 자연어 요약 생성
                if trap_penalty > 0: summ = "배당 함정(Yield Trap) 종목입니다. 절대 주의하세요."
                elif final_score >= 80: summ = "흠잡을 데 없는 완벽한 수치입니다."
                elif final_score >= 60: summ = f"[{best_p}] 지표가 훌륭합니다. [{worst_p}] 지표만 유의하시면 안정적인 종목입니다."
                elif final_score >= 40: summ = f"무난한 수준입니다. [{best_p}] 지표는 긍정적이나 [{worst_p}] 지표 확인이 필요합니다."
                elif final_score >= 20: summ = f"[{worst_p}] 지표가 심각하여 매수에 주의가 필요합니다."
                else: summ = f"[{worst_p}] 지표 등 전반적인 상태가 매우 부진합니다."

                st.success(f"### {c_name} ({tk}) : {final_score} 점 ({rating})")
                st.caption(f"섹터: {sector} | 랭킹: S&P 전체 {row['순위']}위")
                st.info(f"💡 총평: {summ}")
                
                col1, col2, col3 = st.columns(3)
                col1.metric("건전성", row.get('건전성', 'N/A'))
                col2.metric("수익성", row.get('수익성', 'N/A'))
                col3.metric("성장성", row.get('성장성', 'N/A'))
                
                col4, col5, col6 = st.columns(3)
                col4.metric("가성비", row.get('가성비', 'N/A'))
                col5.metric("모멘텀", row.get('모멘텀', 'N/A'))
                col6.metric("환원율", row.get('환원율', 'N/A'))
                
        # 2차 시도: S&P 900 목록에 없는 종목일 경우에만 야후 API 실시간 호출
        else:
            st.warning(f"'{tk}'는 S&P 900 목록에 없어 실시간 수집을 시도합니다.")
            with st.spinner("야후 서버에서 정보 추출 중..."):
                try:
                    raw = calculate_single_stock_raw_factors(tk)
                    if not raw:
                        st.error("데이터 부족 또는 상장폐지/재무 미제공 종목입니다.")
                    else:
                        sct_stats = st.session_state.get('sector_stats')
                        trk_stats = st.session_state.get('track_stats')
                        
                        if not sct_stats or not trk_stats:
                            st.error("기준 데이터(통계량)가 없어 상대 평가를 진행할 수 없습니다.")
                        else:
                            # 통계량 추출 (Unknown 섹터는 평균 활용)
                            sector = raw['섹터']
                            track = raw['트랙']
                            s_stat = sct_stats.get(sector, sct_stats[list(sct_stats.keys())[0]])
                            t_stat = trk_stats.get(track, trk_stats[list(trk_stats.keys())[0]])

                            # Z-score 계산
                            z_scores = {}
                            for key in ['VAL', 'MOM', 'GRW', 'PRF', 'YLD', 'DEBT', 'ICR']:
                                s_z = (raw[key] - s_stat[key]['mean']) / (s_stat[key]['std'] + 1e-9)
                                t_z = (raw[key] - t_stat[key]['mean']) / (t_stat[key]['std'] + 1e-9)
                                z = (s_z * 0.5 + t_z * 0.5) * (-1 if key == 'DEBT' else 1)
                                z_scores[key] = max(-3.0, min(3.0, z))

                            z_hlt = (z_scores['DEBT'] + z_scores['ICR']) / 2
                            
                            # 페널티 계산
                            debt_to_asset = raw['DEBT']
                            payout_ratio = raw['PayoutRatio']
                            penalty = 0.15 * ((debt_to_asset/50)**2.5) if track == 'STD' and debt_to_asset >= 50 else 0
                            trap_penalty = 2.0 if payout_ratio > 1.0 or payout_ratio < 0 else 0

                            # 최종 점수 산출 (가중치: 25-10-15-20-10-20)
                            base = (z_scores['VAL']*0.25 + z_scores['MOM']*0.10 + z_scores['GRW']*0.15 + 
                                    z_scores['PRF']*0.20 + z_scores['YLD']*0.10 + z_hlt*0.20) - penalty - trap_penalty
                            final_score = round(max(0, min(100, ((base - (-3.0)) / 6.0) * 100)), 1)
                            
                            eval_scores = {'가성비': z_scores['VAL'], '모멘텀': z_scores['MOM'], '성장성': z_scores['GRW'], '수익성': z_scores['PRF'], '환원율': z_scores['YLD'], '건전성': z_hlt}
                            best_p = max(eval_scores, key=eval_scores.get)
                            worst_p = min(eval_scores, key=eval_scores.get)
                            
                            if trap_penalty > 0: summ = "배당 함정(Yield Trap) 종목입니다. 절대 주의하세요."
                            elif final_score >= 80: summ = "흠잡을 데 없는 완벽한 수치입니다."
                            elif final_score >= 60: summ = f"[{best_p}] 지표가 훌륭합니다. [{worst_p}] 지표만 유의하시면 안정적인 종목입니다."
                            elif final_score >= 40: summ = f"무난한 수준입니다. [{best_p}] 지표는 긍정적이나 [{worst_p}] 지표 확인이 필요합니다."
                            elif final_score >= 20: summ = f"[{worst_p}] 지표가 심각하여 매수에 주의가 필요합니다."
                            else: summ = f"[{worst_p}] 지표 등 전반적인 상태가 매우 부진합니다."

                            st.success(f"### {raw['기업명']} ({tk}) : {final_score} 점 ({get_rating(final_score)})")
                            st.caption(f"섹터: {sector} | 유니버스: {'금융/리츠 트랙' if track=='FIN' else '스탠다드 트랙'} (실시간 계산됨)")
                            st.info(f"💡 총평: {summ}")
                            
                            col1, col2, col3 = st.columns(3)
                            col1.metric("건전성", get_grade(z_hlt))
                            col2.metric("수익성", get_grade(z_scores['PRF']))
                            col3.metric("성장성", get_grade(z_scores['GRW']))
                            col4, col5, col6 = st.columns(3)
                            col4.metric("가성비", get_grade(z_scores['VAL']))
                            col5.metric("모멘텀", get_grade(z_scores['MOM']))
                            col6.metric("환원율", get_grade(z_scores['YLD']))
                            
                            if penalty > 0: st.warning("부채 위험에 따른 징벌적 감점 적용됨")

                except Exception as e:
                    logger.error(f"Live fetch error for {tk}: {e}")
                    st.error("야후 서버 통신에 실패했습니다. 잠시 후 다시 시도해 주세요.")

# 푸터 (사이드바 대체)
st.markdown("<br><br><br><div style='text-align: center; color: #888; font-size: 12px;'>powered by TeamChilli</div>", unsafe_allow_html=True)
