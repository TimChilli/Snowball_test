"""
===============================================================================
Project: SnowBall Quant Terminal (Web Edition)
Author: TeamChilli
Version: 11.9 (Ultimate ADR/ADS Eradication & Country Blacklist)
Description: 
    - 야후 파이낸스에서 이름을 위장한 중국계 ADS(BILI, ATAT 등)를 잡아내기 위한 국적(Country) 블랙리스트 추가
    - 'QUADRANT' 등 정상 단어의 오탐지를 막기 위해 Regex(정규식) 기반 Word Boundary 필터링 적용
    - 재무제표 통화(Currency) 환산 오류로 인한 5000%+ 기형 데이터 원천 차단
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
import re

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
    data = {'df': df, 'sector_per_map': sector_per_map, 'updated_time': updated_time}
    try:
        with open(SHARED_FILE, 'wb') as f:
            pickle.dump(data, f)
    except Exception as e:
        logger.error(f"Failed to save global data: {e}")

def load_global_data():
    if os.path.exists(SHARED_FILE):
        try:
            with open(SHARED_FILE, 'rb') as f:
                return pickle.load(f)
        except Exception:
            return None
    return None

def clear_global_data():
    if os.path.exists(SHARED_FILE):
        os.remove(SHARED_FILE)

def get_bs_value(bs, possible_keys):
    if bs is None or bs.empty: return 0.0
    recent_bs = bs.iloc[:, 0]
    for key in possible_keys:
        if key in recent_bs.index:
            val = recent_bs[key]
            if not pd.isna(val): return float(val)
    return 0.0

# =============================================================================
# 4. 티커 수집 모듈 (캐싱 적용)
# =============================================================================
@st.cache_data(ttl=86400)
def get_cached_sp1500_tickers():
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
    return sorted(list(set([t.replace('.', '-') for t in (sp500 + sp400 + sp600)])))

@st.cache_data(ttl=86400)
def get_cached_us_full_tickers():
    url = "ftp://ftp.nasdaqtrader.com/SymbolDirectory/nasdaqtraded.txt"
    try:
        df = pd.read_csv(url, sep="|")
        df = df[(df['Test Issue'] == 'N') & (df['ETF'] == 'N')]
        tickers = df['NASDAQ Symbol'].dropna().tolist()
        return sorted([str(t).strip().replace('.', '-') for t in tickers if isinstance(t, str)])
    except Exception:
        try:
            headers = {'User-Agent': 'Mozilla/5.0'}
            res = requests.get('https://www.sec.gov/files/company_tickers.json', headers=headers)
            data = res.json()
            tickers = [item['ticker'].replace('.', '-') for item in data.values()]
            return sorted(list(set(tickers)))
        except Exception:
            return []

# =============================================================================
# 5. 피터 린치 코어 분석 엔진
# =============================================================================
def calculate_single_stock_lynch_model(tk):
    s = yf.Ticker(tk)
    info = s.info
    
    # 💡 [필터링 1] 섹터 제외
    sector = info.get('sector', 'Unknown')
    if sector in ['Financial Services', 'Real Estate']: return None
    
    # 💡 [필터링 2] 국적(Country) 기반 야후 환율 오류/ADR 원천 차단
    country = info.get('country', 'Unknown').upper()
    blocked_countries = ['CHINA', 'HONG KONG', 'TAIWAN', 'MACAU', 'RUSSIA', 'BRAZIL', 'INDIA', 'ARGENTINA', 'MEXICO', 'SOUTH KOREA', 'SOUTH AFRICA']
    if country in blocked_countries:
        return None
    
    # 💡 [필터링 3] 정규식(Regex)을 이용한 오탐 없는 ADR/ADS 정밀 필터링
    short_name = info.get('shortName', '').upper()
    long_name = info.get('longName', '').upper()
    quote_type = info.get('quoteType', '')
    
    if quote_type == 'ADR': return None
    name_str = f"{short_name} {long_name}"
    # 단어 경계(\b)를 사용하여 독립된 단어일 때만 차단 (예: QUADRANT 통과, BILI ADS 차단)
    if re.search(r'\b(ADR|ADS|DEPOSITARY|DEPOSITORY|RECEIPT)\b', name_str):
        return None
        
    bs = s.quarterly_balance_sheet
    if bs is None or bs.empty: bs = s.balance_sheet
        
    inc = s.financials
    price = info.get('currentPrice') or info.get('previousClose')
    shares = info.get('impliedSharesOutstanding') or info.get('sharesOutstanding')
    market_cap = info.get('marketCap', 0)
    
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
    
    # 💡 [필터링 4] 비정상 환율/데이터 단위 꼬임으로 인한 극단적 오류값 차단
    if net_cash_ratio > 400: return None
    
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

def process_market_data(target_tickers, expected_time="10~15분"):
    existing_df = st.session_state.get('quant_data')
    processed_tickers = set()
    
    if existing_df is not None and not existing_df.empty:
        if '종목' in existing_df.columns:
            processed_tickers = set(existing_df['종목'].dropna().tolist())
    
    tickers_to_process = [tk for tk in target_tickers if tk not in processed_tickers]
    total = len(tickers_to_process)
    
    if total == 0:
        return existing_df, st.session_state.get('sector_per_map', {}), st.session_state.get('last_updated', ''), "✅ 이 그룹의 모든 유효 종목이 이미 DB에 수집되어 있습니다."

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
            combined_df = combined_df.drop_duplicates(subset=['종목'], keep='last')
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
    db_total = len(combined_df)
    
    if interrupted:
        msg = f"🚨 야후 IP 차단 감지! 임시 저장됨. (이번 턴 추가: {added_count}개 | DB 누적: {db_total}개) ➡️ 차단이 풀리면 다시 버튼을 눌러주세요."
    else:
        msg = f"✅ 해당 그룹 수집 완료! (이번 턴 추가: {added_count}개 | DB 누적: {db_total}개 | 소요시간: {elapsed_str})"
        
    return combined_df, sector_per_map, update_time, msg

@st.cache_data(ttl=1800)
def fetch_overnight_news():
    try:
        spy = yf.Ticker("SPY")
        news = spy.news
        if not news: return []
        cleaned_news = []
        for item in news[:5]:
            title = item.get('title') or item.get('headline') or 'No Title'
            link = item.get('link') or item.get('url') or '#'
            publisher = item.get('publisher') or item.get('provider') or item.get('source')
            if isinstance(publisher, dict): publisher = publisher.get('displayName', 'Yahoo Finance')
            elif not publisher: publisher = 'Market News'
            cleaned_news.append({'title': title, 'link': link, 'publisher': publisher})
        return cleaned_news
    except Exception: return []

# =============================================================================
# 6. 세션 상태 및 그룹 UI 렌더링 함수
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

def render_admin_panel():
    if st.session_state['scan_msg']:
        if "완료" in st.session_state['scan_msg'] or "성공" in st.session_state['scan_msg']: 
            st.success(st.session_state['scan_msg'])
        else: 
            st.error(st.session_state['scan_msg'])
            
    col1, col2 = st.columns([1, 1.2])
    
    db_tickers = set()
    if st.session_state['quant_data'] is not None and '종목' in st.session_state['quant_data'].columns:
        db_tickers = set(st.session_state['quant_data']['종목'].dropna().tolist())
        
    with col1:
        st.markdown("##### 1️⃣ 1단계: S&P 1500 필수 스캔")
        sp_tks = get_cached_sp1500_tickers()
        sp_done = len([t for t in sp_tks if t in db_tickers])
        st.caption(f"현재 S&P 1500 종목 확보량: **{sp_done} / {len(sp_tks)}**")
        
        if st.button("🚀 S&P 1500 그룹 전체 스캔 (부족분 보충)", use_container_width=True):
            with st.spinner("S&P 1500 누락 종목 수집 중..."):
                try:
                    df, sector_map, updated_time, msg = process_market_data(sp_tks, "10~15분")
                    save_global_data(df, sector_map, updated_time)
                    st.session_state['quant_data'], st.session_state['sector_per_map'] = df, sector_map
                    st.session_state['last_updated'], st.session_state['scan_msg'] = updated_time, msg
                    st.rerun()
                except Exception as e: st.error(f"에러: {e}")
                
    with col2:
        st.markdown("##### 2️⃣ 2단계: 미국 전체(6000+) 그룹별 확장 스캔")
        st.caption("아래에서 원하는 그룹을 선택하여 핀포인트로 수집하세요.")
        
        us_tks = get_cached_us_full_tickers()
        chunk_size = 500
        total_chunks = (len(us_tks) + chunk_size - 1) // chunk_size
        
        chunk_options = {}
        for i in range(total_chunks):
            start_idx = i * chunk_size
            end_idx = min((i + 1) * chunk_size, len(us_tks))
            chunk_slice = us_tks[start_idx:end_idx]
            done_cnt = len([t for t in chunk_slice if t in db_tickers])
            
            label = f"그룹 {i+1} ({start_idx+1} ~ {end_idx}) - [수집완료: {done_cnt} / {len(chunk_slice)}]"
            chunk_options[label] = chunk_slice
            
        selected_chunk_label = st.selectbox("📌 500개 단위 그룹 선택", options=list(chunk_options.keys()), label_visibility="collapsed")
        selected_chunk_tickers = chunk_options[selected_chunk_label]
        
        if st.button("🔥 선택한 그룹 스캔", type="primary", use_container_width=True):
            with st.spinner(f"해당 그룹의 누락 종목을 수집 중입니다..."):
                try:
                    df, sector_map, updated_time, msg = process_market_data(selected_chunk_tickers, "10~15분")
                    save_global_data(df, sector_map, f"{updated_time} (그룹 스캔)")
                    st.session_state['quant_data'], st.session_state['sector_per_map'] = df, sector_map
                    st.session_state['last_updated'], st.session_state['scan_msg'] = f"{updated_time} (그룹 스캔)", msg
                    st.rerun()
                except Exception as e: st.error(f"에러: {e}")

    st.markdown("---")
    st.markdown("##### 📁 데이터베이스 관리 (수동 백업 / 복구 / 리셋)")
    c_dl, c_up, c_rst = st.columns([2, 2, 1])
    with c_dl:
        if st.session_state['quant_data'] is not None:
            csv_data = st.session_state['quant_data'].to_csv(index=False).encode('utf-8-sig')
            st.download_button("📥 현재 DB 로컬 다운로드", data=csv_data, file_name=f"PeterLynch_NetCash_Backup_{datetime.datetime.now().strftime('%Y%m%d')}.csv", mime="text/csv", use_container_width=True)
    with c_up:
        uploaded_file = st.file_uploader("📤 완성된 CSV 수동 복구", type=["csv"], label_visibility="collapsed")
        if uploaded_file is not None:
            try:
                df = pd.read_csv(uploaded_file)
                valid_df = df[df['PER'] > 0] if 'PER' in df.columns else df
                sector_map = valid_df.groupby('섹터')['PER'].mean().round(1).to_dict() if '섹터' in df.columns else {}
                if '섹터' in df.columns: df['섹터평균_PER'] = df['섹터'].map(sector_map).fillna(0.0)
                save_global_data(df, sector_map, "수동 파일 동기화 완료")
                st.session_state['quant_data'], st.session_state['sector_per_map'] = df, sector_map
                st.session_state['last_updated'], st.session_state['scan_msg'] = "수동 파일 동기화 완료", "✅ 수동 백업 데이터 복구 완료!"
                time.sleep(1)
                st.rerun()
            except Exception as e: st.error(f"업로드 에러: {e}")
    with c_rst:
        if st.button("🗑️ DB 리셋", help="처음부터 백지 상태에서 다시 시작합니다.", use_container_width=True):
            clear_global_data()
            st.session_state['quant_data'] = None
            st.session_state['scan_msg'] = ""
            st.rerun()

# =============================================================================
# 7. 메인 라우팅
# =============================================================================
if st.session_state['quant_data'] is None:
    if st.session_state['is_admin']:
        st.markdown("## 🛠️ 데이터 수집 센터")
        st.info("현재 DB가 비어있습니다. 아래 패널에서 1단계부터 차근차근 데이터를 쌓아보세요.")
        render_admin_panel()
        st.stop()
    else:
        st.markdown("## 💰 피터 린치 주당 순현금 랭킹")
        st.info("관리자가 마켓 데이터를 준비하고 있습니다. 잠시 후 다시 접속해 주세요.")
        st.stop()

# =============================================================================
# 8. 메인 UI 대시보드 화면
# =============================================================================
st.markdown("## 💰 피터 린치 주당 순현금 랭킹")
st.caption(f"최근 데이터 동기화: {st.session_state['last_updated']}")

tab1, tab2, tab3 = st.tabs(["대시보드", "Net Cash 랭킹 보드", "개별 종목 딥다이브"])



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
        st.markdown("### 🛠️ [관리자 전용] 스마트 데이터 누적 패널")
        render_admin_panel()

with tab2:
    db_size = len(st.session_state['quant_data']) if st.session_state['quant_data'] is not None else 0
    st.subheader(f"🏆 순현금비율(%) TOP 100 (현재 DB: {db_size:,}개)")
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
