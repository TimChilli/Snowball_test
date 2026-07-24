"""
===============================================================================
Project: SnowBall Quant Terminal (Web Edition)
Author: TeamChilli
Version: 11.3 (ADR Filter, Anomaly Prevention & Market News)
Description: 
    - ADR 종목, 페니스탁(1$ 미만), 초소형주(50M$ 미만) 및 비정상 비율(300%+) 원천 차단
    - 상단 제목 폰트 사이즈 최적화
    - 야후 API 기반 실시간 S&P 500 시장 뉴스 대시보드 탑재
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
    page_title="Lynch's Net Cash", 
    page_icon="💰", 
    layout="wide",
    initial_sidebar_state="collapsed"
)

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

SHARED_FILE = "lynch_shared_data.pkl"
ADMIN_SECRET_CODE = "chillixlaclffl"

# =============================================================================
# 2. 커스텀 CSS UI 스타일링
# =============================================================================
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
# 3. 유틸리티 및 데이터 캐싱 함수
# =============================================================================
def save_global_data(df, sector_per_map, updated_time):
    data = {
        'df': df,
        'sector_per_map': sector_per_map,
        'updated_time': updated_time
    }
    try:
        with open(SHARED_FILE, 'wb') as f:
            pickle.dump(data, f)
        logger.info("Global data saved successfully.")
    except Exception as e:
        logger.error(f"Failed to save global data: {e}")

def load_global_data():
    if os.path.exists(SHARED_FILE):
        try:
            with open(SHARED_FILE, 'rb') as f:
                return pickle.load(f)
        except Exception as e:
            logger.error(f"Failed to load global data: {e}")
            return None
    return None

def get_bs_value(bs, possible_keys):
    if bs is None or bs.empty: 
        return 0.0
    recent_bs = bs.iloc[:, 0]
    for key in possible_keys:
        if key in recent_bs.index:
            val = recent_bs[key]
            if not pd.isna(val):
                return float(val)
    return 0.0

@st.cache_data(ttl=1800) # 30분마다 뉴스 갱신
def fetch_overnight_news():
    """S&P 500 (SPY) 최신 시장 뉴스를 가져옵니다."""
    try:
        spy = yf.Ticker("SPY")
        news = spy.news
        if not news:
            return []
        return news[:5] # 상위 5개 헤드라인만 추출
    except Exception as e:
        logger.error(f"News fetch error: {e}")
        return []

# =============================================================================
# 4. 티커 수집 모듈
# =============================================================================
def fetch_sp1500_tickers():
    session = requests.Session()
    session.headers.update({'User-Agent': 'Mozilla/5.0'})
    def get_t(url):
        try:
            res = requests.get(url, headers=session.headers)
            df = pd.read_html(io.StringIO(res.text))[0]
            return df['Symbol' if 'Symbol' in df.columns else 'Ticker symbol'].tolist()
        except Exception: return []
    
    sp500 = get_t('https://en.wikipedia.org/wiki/List_of_S%26P_500_companies')
    sp400 = get_t('https://en.wikipedia.org/wiki/List_of_S%26P_400_companies')
    sp600 = get_t('https://en.wikipedia.org/wiki/List_of_S%26P_600_companies')
    return [t.replace('.', '-') for t in list(set(sp500 + sp400 + sp600))]

def get_us_full_tickers():
    url = "ftp://ftp.nasdaqtrader.com/SymbolDirectory/nasdaqtraded.txt"
    try:
        df = pd.read_csv(url, sep="|")
        df = df[(df['Test Issue'] == 'N') & (df['ETF'] == 'N')]
        tickers = df['NASDAQ Symbol'].dropna().tolist()
        return [str(t).strip().replace('.', '-') for t in tickers if isinstance(t, str)]
    except Exception:
        try:
            headers = {'User-Agent': 'Mozilla/5.0'}
            res = requests.get('https://www.sec.gov/files/company_tickers.json', headers=headers)
            data = res.json()
            tickers = [item['ticker'].replace('.', '-') for item in data.values()]
            return list(set(tickers))
        except Exception:
            return []

# =============================================================================
# 5. 피터 린치 코어 분석 엔진
# =============================================================================
def calculate_single_stock_lynch_model(tk):
    s = yf.Ticker(tk)
    info = s.info
    
    # 💡 [필터링 1] 금융/리츠 제외 및 ADR 종목 원천 차단
    sector = info.get('sector', 'Unknown')
    if sector in ['Financial Services', 'Real Estate']: return None
    if info.get('quoteType') == 'ADR' or 'ADR' in info.get('shortName', '').upper(): return None
        
    bs = s.quarterly_balance_sheet
    if bs is None or bs.empty: bs = s.balance_sheet
        
    inc = s.financials
    price = info.get('currentPrice') or info.get('previousClose')
    shares = info.get('impliedSharesOutstanding') or info.get('sharesOutstanding')
    market_cap = info.get('marketCap', 0)
    
    # 💡 [필터링 2] 데이터 찌꺼기 방지 (가격 1달러 미만, 시총 5천만 달러 미만, 혹은 기본 데이터 누락 배제)
    if not price or not shares or shares == 0 or bs is None or bs.empty: return None
    if price < 1.0 or market_cap < 50000000: return None
        
    cash_keys = ['Cash Cash Equivalents And Short Term Investments', 'Cash And Cash Equivalents', 'Cash Financial', 'Cash', 'Other Short Term Investments']
    long_debt_keys = ['Long Term Debt Non Current', 'Long Term Debt', 'Total Debt Non Current', 'Total Long Term Debt', 'Current Debt And Capital Lease Obligation', 'Current Debt', 'Current Portion Of Long Term Debt']
    
    total_cash = get_bs_value(bs, cash_keys)
    if total_cash == 0: total_cash = float(info.get('totalCash') or 0.0)
    adjusted_long_debt = get_bs_value(bs, long_debt_keys)
    
    net_cash = total_cash - adjusted_long_debt
    net_cash_per_share = net_cash / shares
    net_cash_ratio = (net_cash_per_share / price) * 100
    
    # 💡 [필터링 3] 야후 데이터 오류(단위 믹스)로 인한 5000% 등 비정상 수치 차단
    if net_cash_ratio > 300: return None
    
    consecutive_growth = 0
    if inc is not None and not inc.empty:
        for key in ['Net Income', 'Net Income Common Stockholders']:
            if key in inc.index:
                ni_series = inc.loc[key].dropna()
                if len(ni_series) > 1:
                    ni_list = ni_series.tolist()
                    for i in range(len(ni_list) - 1):
                        if ni_list[i] > ni_list[i+1]: consecutive_growth += 1
                        else: break
                break
    growth_str = '▲' * consecutive_growth if consecutive_growth > 0 else '-'

    trailing_pe, forward_pe, eps = info.get('trailingPE', 0), info.get('forwardPE', 0), info.get('trailingEps', 0)
    net_cash_per = (price - net_cash_per_share) / eps if eps and eps > 0 else 0.0
    
    return {
        '종목': tk, '기업명': info.get('shortName', info.get('longName', tk)), '섹터': sector,
        '현재주가($)': price, '주당순현금($)': round(net_cash_per_share, 2), '순현금비율(%)': round(net_cash_ratio, 2),
        '시가총액(M$)': round(market_cap / 1e6, 2) if market_cap else 0.0, '총현금(M$)': round(total_cash / 1e6, 2),
        '실질장기부채(M$)': round(adjusted_long_debt / 1e6, 2), '순이익성장': growth_str,
        'PER': round(trailing_pe, 2) if trailing_pe and trailing_pe > 0 else 0.0,
        'Fwd_PER': round(forward_pe, 2) if forward_pe and forward_pe > 0 else 0.0,
        '순현금_PER': round(net_cash_per, 2)
    }

def process_market_data(mode="sp1500", resume=False):
    if mode == "full":
        tickers = get_us_full_tickers()
        expected_time = "1~2시간"
    else:
        tickers = fetch_sp1500_tickers()
        expected_time = "10~15분"
        
    if not tickers: raise ValueError("티커 목록을 가져오지 못했습니다.")

    existing_df = None
    processed_tickers = set()
    
    if resume and st.session_state.get('quant_data') is not None:
        existing_df = st.session_state['quant_data'].copy()
        if '종목' in existing_df.columns:
            processed_tickers = set(existing_df['종목'].tolist())
    
    tickers_to_process = [tk for tk in tickers if tk not in processed_tickers]
    total = len(tickers_to_process)
    
    if total == 0:
        return existing_df, st.session_state.get('sector_per_map', {}), st.session_state.get('last_updated', ''), "✅ 이미 모든 종목이 스크래핑 되어있습니다."

    temp_list = []
    progress_bar = st.progress(0)
    status_text = st.empty()
    
    error_streak = 0
    interrupted = False
    start_time = time.time()

    for i, tk in enumerate(tickers_to_process, 1):
        time.sleep(0.05) 
        try:
            raw_data = calculate_single_stock_lynch_model(tk)
            if raw_data: 
                temp_list.append(raw_data)
                error_streak = 0 
            else:
                error_streak += 1 
        except Exception as e:
            logger.warning(f"Error fetching {tk}: {e}")
            error_streak += 1 
        
        if i % 5 == 0 or i == total:
            progress_bar.progress(i / total)
            elapsed = int(time.time() - start_time)
            m, s = divmod(elapsed, 60)
            status_text.text(f"수집 중: {i}/{total}개 | 신규 확보: {len(temp_list)}개 | 예상: {expected_time} | 소요시간: {m}분 {s}초")
            
        if error_streak >= 30:
            interrupted = True
            break

    elapsed = int(time.time() - start_time)
    m, s = divmod(elapsed, 60)
    elapsed_str = f"{m}분 {s}초"

    progress_bar.empty()
    status_text.empty()

    new_df = pd.DataFrame(temp_list)
    
    if existing_df is not None and not existing_df.empty:
        if not new_df.empty:
            if '순위' in existing_df.columns: existing_df = existing_df.drop(columns=['순위'])
            if '순위' in new_df.columns: new_df = new_df.drop(columns=['순위'])
            combined_df = pd.concat([existing_df, new_df], ignore_index=True)
        else: combined_df = existing_df
    else:
        combined_df = new_df

    if combined_df.empty:
        raise ValueError("수집된 데이터가 없습니다. 야후 서버 차단을 의심해보세요.")

    if 'PER' in combined_df.columns and '섹터' in combined_df.columns:
        valid_per_df = combined_df[combined_df['PER'] > 0]
        sector_per_map = valid_per_df.groupby('섹터')['PER'].mean().round(1).to_dict()
        combined_df['섹터평균_PER'] = combined_df['섹터'].map(sector_per_map).fillna(0.0)
    else: sector_per_map = {}
    
    combined_df = combined_df.sort_values('순현금비율(%)', ascending=False).reset_index(drop=True)
    if '순위' in combined_df.columns: combined_df = combined_df.drop(columns=['순위'])
    combined_df.insert(0, '순위', range(1, len(combined_df) + 1))
    
    kst = pytz.timezone('Asia/Seoul')
    update_time = datetime.datetime.now(kst).strftime('%Y-%m-%d %H:%M:%S KST')
    
    added_count = len(temp_list)
    if interrupted:
        msg = f"🚨 야후 IP 차단 감지! 임시 저장됨. (이번 턴에 추가된 종목: {added_count}개 | 남은 종목: {total - i}개 | 진행시간: {elapsed_str}) ➡️ 잠시 후 [이어서 수집]을 눌러주세요."
    else:
        msg = f"✅ 수집 완료! (이번 턴에 추가된 종목: {added_count}개 | 진행시간: {elapsed_str})"
        
    return combined_df, sector_per_map, update_time, msg

# =============================================================================
# 6. 세션 상태 및 라우팅 컨트롤러
# =============================================================================
if 'quant_data' not in st.session_state: 
    st.session_state['quant_data'] = None
    st.session_state['sector_per_map'] = {}
    st.session_state['last_updated'] = "수집 전"
    st.session_state['scan_msg'] = "" 
    
    global_data = load_global_data()
    if global_data is not None:
        st.session_state['quant_data'] = global_data['df']
        st.session_state['sector_per_map'] = global_data.get('sector_per_map', {})
        st.session_state['last_updated'] = global_data['updated_time']

if 'is_admin' not in st.session_state: st.session_state['is_admin'] = False
if st.query_params.get("admin") == ADMIN_SECRET_CODE: st.session_state['is_admin'] = True

if st.session_state['quant_data'] is None:
    if st.session_state['is_admin']:
        st.markdown("## 🛠️ 데이터 수집 센터")
        
        if st.session_state['scan_msg']:
            if "완료" in st.session_state['scan_msg']: st.success(st.session_state['scan_msg'])
            else: st.warning(st.session_state['scan_msg'])
            
        col1, col2 = st.columns(2)
        with col1:
            st.subheader("1️⃣ S&P 1500 (대/중/소형주)")
            st.caption("예상 시간: 10~15분. 상대적으로 차단 확률 낮음.")
            c1, c2 = st.columns(2)
            with c1:
                if st.button("🚀 전체 새로 수집", key="sp_new"):
                    with st.spinner("S&P 1500 전체 수집 중..."):
                        try:
                            df, sector_map, updated_time, msg = process_market_data(mode="sp1500", resume=False)
                            save_global_data(df, sector_map, updated_time)
                            st.session_state['quant_data'], st.session_state['sector_per_map'] = df, sector_map
                            st.session_state['last_updated'], st.session_state['scan_msg'] = updated_time, msg
                            st.rerun()
                        except Exception as e: st.error(f"에러: {e}")
            with c2:
                if st.button("⏯️ 이어서 수집", key="sp_res"):
                    with st.spinner("S&P 1500 남은 종목 수집 중..."):
                        try:
                            df, sector_map, updated_time, msg = process_market_data(mode="sp1500", resume=True)
                            save_global_data(df, sector_map, updated_time)
                            st.session_state['quant_data'], st.session_state['sector_per_map'] = df, sector_map
                            st.session_state['last_updated'], st.session_state['scan_msg'] = updated_time, msg
                            st.rerun()
                        except Exception as e: st.error(f"에러: {e}")

        with col2:
            st.subheader("2️⃣ 미국 전체 주식 (6,000+)")
            st.caption("예상 시간: 1~2시간. 차단 확률 매우 높음 (이어서 수집 적극 활용)")
            c3, c4 = st.columns(2)
            with c3:
                if st.button("🔥 전체 새로 수집", key="us_new", type="primary"):
                    with st.spinner("미국 전체 주식 수집 중..."):
                        try:
                            df, sector_map, updated_time, msg = process_market_data(mode="full", resume=False)
                            save_global_data(df, sector_map, f"{updated_time} (전수)")
                            st.session_state['quant_data'], st.session_state['sector_per_map'] = df, sector_map
                            st.session_state['last_updated'], st.session_state['scan_msg'] = f"{updated_time} (전수)", msg
                            st.rerun()
                        except Exception as e: st.error(f"에러: {e}")
            with c4:
                if st.button("⏯️ 이어서 수집", key="us_res", type="primary"):
                    with st.spinner("미국 전체 주식 남은 종목 수집 중..."):
                        try:
                            df, sector_map, updated_time, msg = process_market_data(mode="full", resume=True)
                            save_global_data(df, sector_map, f"{updated_time} (전수)")
                            st.session_state['quant_data'], st.session_state['sector_per_map'] = df, sector_map
                            st.session_state['last_updated'], st.session_state['scan_msg'] = f"{updated_time} (전수)", msg
                            st.rerun()
                        except Exception as e: st.error(f"에러: {e}")
        st.stop()
        
    else:
        st.markdown("## 💰 피터 린치 주당 순현금 랭킹")
        st.info("관리자가 실시간 마켓 데이터를 수집하고 있습니다. 잠시 후 다시 접속해 주세요.")
        st.stop()

# =============================================================================
# 7. 메인 UI 화면 
# =============================================================================
# 💡 [UI 보완] 제목 크기 축소 (st.title -> st.markdown header)
st.markdown("## 💰 피터 린치 주당 순현금 랭킹")
st.caption(f"최근 데이터 동기화: {st.session_state['last_updated']}")

tab1, tab2, tab3 = st.tabs(["대시보드", "Net Cash 랭킹 보드", "개별 종목 딥다이브"])

with tab1:
    # 💡 [신규 기능] 간밤의 미국 증시 뉴스 배치
    st.markdown("### 📰 간밤의 미국 증시 헤드라인")
    news_list = fetch_overnight_news()
    if news_list:
        for item in news_list:
            title = item.get('title', 'No Title')
            link = item.get('link', '#')
            publisher = item.get('publisher', 'Unknown')
            st.markdown(f"- [{title}]({link}) *(출처: {publisher})*")
    else:
        st.info("현재 불러올 수 있는 최신 뉴스가 없습니다.")
    st.divider()

    st.subheader("💡 피터 린치의 오리지널 순현금 모델")
    st.markdown('''
    "어떤 회사의 주당 순현금이 3달러이고 주가가 10달러라면, 당신은 이 주식을 10달러가 아니라 **실질적으로 7달러**에 사는 것이다." 
    - *피터 린치 (Peter Lynch)*
    
    * **순현금 공식:** (순수 현금 및 단기투자자산) - (순수 장기 부채 및 1년 내 만기도래분)
    * **순현금 PER:** (현재 주가 - 주당 순현금) / 1주당 순이익(EPS)
    * **섹터 평균 PER:** 해당 업종 내 **흑자 기업(EPS > 0)**의 PER 평균값
    ''')
    st.divider()
    
    if st.session_state['is_admin']:
        st.markdown("### 🛠️ [관리자 전용] 데이터 갱신 패널")
        
        if st.session_state['scan_msg']:
            if "완료" in st.session_state['scan_msg']: st.success(st.session_state['scan_msg'])
            else: st.error(st.session_state['scan_msg'])
            
        col1, col2 = st.columns(2)
        with col1:
            st.markdown("##### 🚀 S&P 1500 (예상: 10~15분)")
            c1, c2 = st.columns(2)
            with c1:
                if st.button("새로 수집", key="sp_new_2", use_container_width=True):
                    with st.spinner("S&P 1500 전체 다시 수집 중..."):
                        try:
                            df, sector_map, updated_time, msg = process_market_data(mode="sp1500", resume=False)
                            save_global_data(df, sector_map, updated_time)
                            st.session_state['quant_data'], st.session_state['sector_per_map'] = df, sector_map
                            st.session_state['last_updated'], st.session_state['scan_msg'] = updated_time, msg
                            st.rerun()
                        except Exception as e: st.error(f"에러: {e}")
            with c2:
                if st.button("⏯️ 이어서 수집", key="sp_res_2", use_container_width=True):
                    with st.spinner("S&P 1500 남은 종목 수집 중..."):
                        try:
                            df, sector_map, updated_time, msg = process_market_data(mode="sp1500", resume=True)
                            save_global_data(df, sector_map, updated_time)
                            st.session_state['quant_data'], st.session_state['sector_per_map'] = df, sector_map
                            st.session_state['last_updated'], st.session_state['scan_msg'] = updated_time, msg
                            st.rerun()
                        except Exception as e: st.error(f"에러: {e}")

        with col2:
            st.markdown("##### 🔥 미국 전체 6000+ (예상: 1~2시간)")
            c3, c4 = st.columns(2)
            with c3:
                if st.button("새로 수집", key="us_new_2", type="primary", use_container_width=True):
                    with st.spinner("미국 전체 주식 처음부터 수집 중..."):
                        try:
                            df, sector_map, updated_time, msg = process_market_data(mode="full", resume=False)
                            save_global_data(df, sector_map, f"{updated_time} (전수)")
                            st.session_state['quant_data'], st.session_state['sector_per_map'] = df, sector_map
                            st.session_state['last_updated'], st.session_state['scan_msg'] = f"{updated_time} (전수)", msg
                            st.rerun()
                        except Exception as e: st.error(f"에러: {e}")
            with c4:
                if st.button("⏯️ 이어서 수집", key="us_res_2", type="primary", use_container_width=True):
                    with st.spinner("미국 전체 주식 남은 종목 수집 중..."):
                        try:
                            df, sector_map, updated_time, msg = process_market_data(mode="full", resume=True)
                            save_global_data(df, sector_map, f"{updated_time} (전수)")
                            st.session_state['quant_data'], st.session_state['sector_per_map'] = df, sector_map
                            st.session_state['last_updated'], st.session_state['scan_msg'] = f"{updated_time} (전수)", msg
                            st.rerun()
                        except Exception as e: st.error(f"에러: {e}")
        
        st.markdown("---")
        if st.session_state['quant_data'] is not None:
            csv_data = st.session_state['quant_data'].to_csv(index=False).encode('utf-8-sig')
            st.download_button("📥 현재 완성된 DB 로컬 다운로드 (CSV 백업용)", data=csv_data, file_name=f"PeterLynch_NetCash_Backup_{datetime.datetime.now().strftime('%Y%m%d')}.csv", mime="text/csv", use_container_width=True)

with tab2:
    st.subheader("🏆 순현금비율(%) TOP 100")
    if st.session_state['quant_data'] is not None:
        df = st.session_state['quant_data'].copy()
        df = df[df['순현금비율(%)'] > 0] 
        
        display_cols = [
            '순위', '종목', '기업명', '섹터', '시가총액(M$)', '순현금비율(%)', 
            'PER', 'Fwd_PER', '순현금_PER', '섹터평균_PER', '순이익성장', 
            '현재주가($)', '주당순현금($)', '총현금(M$)', '실질장기부채(M$)'
        ]
        for c in display_cols:
            if c not in df.columns: df[c] = 0.0
        
        st.dataframe(
            df[display_cols].head(100),
            use_container_width=True, hide_index=True,
            column_config={
                "순위": st.column_config.NumberColumn(width=50, format="%d"),
                "종목": st.column_config.TextColumn(width=80),
                "기업명": st.column_config.TextColumn(width="medium"),
                "섹터": st.column_config.TextColumn(width="medium"),
                "시가총액(M$)": st.column_config.NumberColumn(format="%,.0f M"),
                "순현금비율(%)": st.column_config.ProgressColumn(format="%d%%", min_value=0, max_value=100),
                "PER": st.column_config.NumberColumn(format="%,.1f"),
                "Fwd_PER": st.column_config.NumberColumn(format="%,.1f"),
                "순현금_PER": st.column_config.NumberColumn(format="%,.1f", help="(주가 - 주당 순현금) / EPS"),
                "섹터평균_PER": st.column_config.NumberColumn(format="%,.1f", help="섹터 내 흑자 기업 평균 PER"),
                "순이익성장": st.column_config.TextColumn(width=80, help="연간 순이익 연속 상승 횟수 (▲)"),
                "현재주가($)": st.column_config.NumberColumn(format="$%,.2f"),
                "주당순현금($)": st.column_config.NumberColumn(format="$%,.2f"),
                "총현금(M$)": st.column_config.NumberColumn(format="%,.1f M"),
                "실질장기부채(M$)": st.column_config.NumberColumn(format="%,.1f M")
            }
        )

with tab3:
    st.subheader("🔍 개별 종목 실시간 딥다이브")
    with st.form("search_form"):
        ticker_input = st.text_input("분석할 티커를 입력하세요 (예: YELP, AAPL)")
        submit_btn = st.form_submit_button("분석 시작")
        
    if submit_btn and ticker_input:
        tk = ticker_input.upper().strip()
        df = st.session_state['quant_data']
        
        if df is not None and tk in df['종목'].values:
            with st.spinner("DB 로드 중..."):
                row = df[df['종목'] == tk].iloc[0]
                price = float(row['현재주가($)'])
                net_cash_per_share = float(row['주당순현금($)'])
                ratio = float(row['순현금비율(%)'])
                
                if ratio > 50: summ = "엄청난 수준의 현금을 보유하고 있습니다. 회사 금고의 현금이 주가의 절반 이상을 보증합니다!"
                elif ratio > 20: summ = "매우 건전한 상태입니다. 든든한 순현금이 하락장을 방어해 줄 것입니다."
                elif ratio > 0: summ = "실질 장기부채보다 현금이 더 많아 재무적으로 안정적입니다."
                else: summ = "현재 보유한 현금보다 갚아야 할 실질 장기부채가 더 많아 주당 순현금이 마이너스(-) 상태입니다."

                st.success(f"### {row['기업명']} ({tk}) : 순현금비율 {ratio:,.1f}%")
                st.caption(f"섹터: {row['섹터']} | 랭킹: 비금융 전체 {row['순위']}위")
                st.info(f"💡 총평: {summ}")
                
                col1, col2, col3, col4 = st.columns(4)
                col1.metric("현재 주가", f"${price:,.2f}")
                col2.metric("주당 순현금", f"${net_cash_per_share:,.2f}", f"{ratio:,.1f}% of Price")
                col3.metric("실질 매수단가", f"${max(0, price - net_cash_per_share):,.2f}", delta_color="inverse")
                col4.metric("시가총액 (Market Cap)", f"${float(row.get('시가총액(M$)',0)):,.0f} M")
                
                col5, col6, col7, col8 = st.columns(4)
                col5.metric("PER (기본)", f"{float(row.get('PER',0)):,.1f}" if float(row.get('PER',0)) > 0 else "N/A")
                col6.metric("Forward PER", f"{float(row.get('Fwd_PER',0)):,.1f}" if float(row.get('Fwd_PER',0)) > 0 else "N/A")
                col7.metric("순현금 PER", f"{float(row.get('순현금_PER',0)):,.1f}" if float(row.get('순현금_PER',0)) > 0 else "N/A")
                col8.metric("섹터 평균 PER", f"{float(row.get('섹터평균_PER',0)):,.1f}" if float(row.get('섹터평균_PER',0)) > 0 else "N/A")
                
                col9, col10, col11 = st.columns(3)
                col9.metric("총 현금 (Total Cash)", f"${float(row.get('총현금(M$)',0)):,.1f} M")
                col10.metric("조정 장기부채 (Long Term)", f"${float(row.get('실질장기부채(M$)',0)):,.1f} M")
                col11.metric("연간 순이익 성장", str(row.get('순이익성장','-')) if str(row.get('순이익성장','-')) != '-' else "성장 안함")
                
        else:
            st.warning(f"'{tk}'는 현재 DB에 없어 야후 파이낸스에서 실시간으로 재무제표를 조회합니다.")
            with st.spinner("야후 서버에서 정보 추출 중..."):
                try:
                    raw = calculate_single_stock_lynch_model(tk)
                    if not raw:
                        st.error("데이터 부족, 상장폐지, 또는 순현금 분석에서 제외되는 조건(금융, ADR, 초소형 등)입니다.")
                    else:
                        price = raw['현재주가($)']
                        net_cash_per_share = raw['주당순현금($)']
                        ratio = raw['순현금비율(%)']
                        sec_avg_per = st.session_state.get('sector_per_map', {}).get(raw['섹터'], 0.0)
                        
                        if ratio > 50: summ = "엄청난 수준의 현금을 보유하고 있습니다. 회사 금고의 현금이 주가의 절반 이상을 보증합니다!"
                        elif ratio > 20: summ = "매우 건전한 상태입니다. 든든한 순현금이 하락장을 방어해 줄 것입니다."
                        elif ratio > 0: summ = "실질 장기부채보다 현금이 더 많아 재무적으로 안정적입니다."
                        else: summ = "현재 보유한 현금보다 갚아야 할 실질 장기부채가 더 많아 주당 순현금이 마이너스(-) 상태입니다."

                        st.success(f"### {raw['기업명']} ({tk}) : 순현금비율 {ratio:,.1f}%")
                        st.caption(f"섹터: {raw['섹터']} | (실시간 산출 데이터)")
                        st.info(f"💡 총평: {summ}")
                        
                        col1, col2, col3, col4 = st.columns(4)
                        col1.metric("현재 주가", f"${price:,.2f}")
                        col2.metric("주당 순현금", f"${net_cash_per_share:,.2f}", f"{ratio:,.1f}% of Price")
                        col3.metric("실질 매수단가", f"${max(0, price - net_cash_per_share):,.2f}", delta_color="inverse")
                        col4.metric("시가총액 (Market Cap)", f"${raw['시가총액(M$)']:,.0f} M")
                        
                        col5, col6, col7, col8 = st.columns(4)
                        col5.metric("PER (기본)", f"{raw['PER']:,.1f}" if raw['PER'] > 0 else "N/A")
                        col6.metric("Forward PER", f"{raw['Fwd_PER']:,.1f}" if raw['Fwd_PER'] > 0 else "N/A")
                        col7.metric("순현금 PER", f"{raw['순현금_PER']:,.1f}" if raw['순현금_PER'] > 0 else "N/A")
                        col8.metric("섹터 평균 PER", f"{sec_avg_per:,.1f}" if sec_avg_per > 0 else "N/A")
                        
                        col9, col10, col11 = st.columns(3)
                        col9.metric("총 현금 (Total Cash)", f"${raw['총현금(M$)']:,.1f} M")
                        col10.metric("조정 장기부채 (Long Term)", f"${raw['실질장기부채(M$)']:,.1f} M")
                        col11.metric("연간 순이익 성장", raw['순이익성장'] if raw['순이익성장'] != '-' else "성장 안함")
                        
                except Exception as e:
                    logger.error(f"Live fetch error for {tk}: {e}")
                    st.error("야후 서버 통신에 실패했습니다. 잠시 후 다시 시도해 주세요.")

# 푸터
st.markdown("<br><br><br><div style='text-align: center; color: #888; font-size: 12px;'>powered by TeamChilli</div>", unsafe_allow_html=True)
