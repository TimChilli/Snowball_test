"""
===============================================================================
Project: SnowBall Quant Terminal (Web Edition)
Author: TeamChilli
Version: 10.3 (Lynch Net Cash - Precision & UI Final)
Description: 
    - 리스 부채 등 회계 노이즈를 완벽히 제거한 순수 현금/부채 추출
    - 천 단위 콤마(,) 렌더링 및 PER 3종 세트 테이블 편입
    - 순위 칼럼 최소화 및 컨테이너 핏 최적화
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
def save_global_data(df, updated_time):
    data = {'df': df, 'updated_time': updated_time}
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

def get_trade_day():
    tz = pytz.timezone('US/Eastern')
    now = datetime.datetime.now(tz)
    if now.hour < 4:
        return (now - datetime.timedelta(days=1)).strftime('%Y-%m-%d')
    return now.strftime('%Y-%m-%d')

def get_bs_value(bs, possible_keys):
    """지정된 정확한 키값만 순차적으로 찾아 반환 (노이즈 배제)"""
    if bs.empty: return 0
    recent_bs = bs.iloc[:, 0]
    for key in possible_keys:
        if key in recent_bs.index:
            val = recent_bs[key]
            if not pd.isna(val):
                return float(val)
    return 0

# =============================================================================
# 4. 피터 린치 코어 엔진
# =============================================================================
def fetch_sp1500_tickers():
    logger.info("Fetching S&P 1500 tickers from Wikipedia...")
    session = requests.Session()
    session.headers.update({'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'})
    
    def get_t(url):
        try:
            res = requests.get(url, headers=session.headers)
            df = pd.read_html(io.StringIO(res.text))[0]
            return df['Symbol' if 'Symbol' in df.columns else 'Ticker symbol'].tolist()
        except Exception as e:
            logger.error(f"Ticker fetching error for URL {url}: {e}")
            return []
    
    sp500 = get_t('https://en.wikipedia.org/wiki/List_of_S%26P_500_companies')
    sp400 = get_t('https://en.wikipedia.org/wiki/List_of_S%26P_400_companies')
    sp600 = get_t('https://en.wikipedia.org/wiki/List_of_S%26P_600_companies')
    
    tickers = list(set(sp500 + sp400 + sp600))
    return [t.replace('.', '-') for t in tickers]

def calculate_single_stock_lynch_model(tk):
    s = yf.Ticker(tk)
    info = s.info
    
    sector = info.get('sector', 'Unknown')
    if sector in ['Financial Services', 'Real Estate']:
        return None
        
    bs = s.balance_sheet
    inc = s.financials
    
    price = info.get('currentPrice') or info.get('previousClose')
    shares = info.get('impliedSharesOutstanding') or info.get('sharesOutstanding')
    
    if not price or not shares or shares == 0 or bs.empty:
        return None
        
    c_name = info.get('shortName', info.get('longName', tk))
    
    # -------------------------------------------------------------------------
    # 💡 [노이즈 완전 제거] 현금 및 부채 핀셋 추출 (리스 부채 제외)
    # -------------------------------------------------------------------------
    # 가장 순수한 '현금 및 현금성 자산'과 '단기 투자'만 포함
    cash = get_bs_value(bs, ['Cash And Cash Equivalents'])
    short_inv = get_bs_value(bs, ['Other Short Term Investments'])
    total_cash = cash + short_inv
    
    # 리스 부채(Lease Obligation)가 포함된 항목을 철저히 배제하고 '순수 은행 부채'만 포함
    long_debt = get_bs_value(bs, ['Long Term Debt'])
    current_long_debt = get_bs_value(bs, ['Current Debt', 'Current Portion Of Long Term Debt'])
    adjusted_long_debt = long_debt + current_long_debt
    
    net_cash = total_cash - adjusted_long_debt
    net_cash_per_share = net_cash / shares
    net_cash_ratio = (net_cash_per_share / price) * 100
    
    # 순이익 연속 성장(▲) 로직
    consecutive_growth = 0
    if not inc.empty:
        for key in ['Net Income', 'Net Income Common Stockholders']:
            if key in inc.index:
                ni_series = inc.loc[key].dropna()
                if len(ni_series) > 1:
                    ni_list = ni_series.tolist()
                    for i in range(len(ni_list) - 1):
                        if ni_list[i] > ni_list[i+1]:
                            consecutive_growth += 1
                        else:
                            break
                break
    growth_str = '▲' * consecutive_growth if consecutive_growth > 0 else '-'

    # PER 3종 세트 산출
    trailing_pe = info.get('trailingPE', 0)
    forward_pe = info.get('forwardPE', 0)
    eps = info.get('trailingEps', 0)
    
    if eps and eps > 0:
        net_cash_per = (price - net_cash_per_share) / eps
    else:
        net_cash_per = 0.0
    
    return {
        '종목': tk,
        '기업명': c_name,
        '섹터': sector,
        '현재주가($)': price,
        '주당순현금($)': round(net_cash_per_share, 2),
        '순현금비율(%)': round(net_cash_ratio, 2),
        '총현금(M$)': round(total_cash / 1e6, 2),
        '실질장기부채(M$)': round(adjusted_long_debt / 1e6, 2),
        '순이익성장': growth_str,
        'PER': round(trailing_pe, 2) if trailing_pe else 0.0,
        'Fwd_PER': round(forward_pe, 2) if forward_pe else 0.0,
        '순현금_PER': round(net_cash_per, 2)
    }

def process_market_data():
    tickers = fetch_sp1500_tickers()
    if not tickers:
        raise ValueError("티커 목록을 가져오지 못했습니다. 네트워크 상태를 확인하세요.")

    temp_list = []
    progress_bar = st.progress(0)
    status_text = st.empty()
    total = len(tickers)

    for i, tk in enumerate(tickers, 1):
        time.sleep(0.05) 
        try:
            raw_data = calculate_single_stock_lynch_model(tk)
            if raw_data:
                temp_list.append(raw_data)
        except Exception as e:
            logger.warning(f"Error fetching {tk}: {e}")
            pass
        
        if i % 10 == 0 or i == total:
            progress_bar.progress(i / total)
            status_text.text(f"API 통신: 피터 린치 순현금 발굴 중... ({i}/{total})")

    progress_bar.empty()
    status_text.empty()

    if len(temp_list) == 0:
        raise ValueError("야후 파이낸스 서버가 접근을 차단하여 데이터를 가져오지 못했습니다.")

    df = pd.DataFrame(temp_list).replace([np.inf, -np.inf], 0).fillna(0)
    df = df.sort_values('순현금비율(%)', ascending=False).reset_index(drop=True)
    df.insert(0, '순위', range(1, len(df) + 1))
    
    kst = pytz.timezone('Asia/Seoul')
    update_time = datetime.datetime.now(kst).strftime('%Y-%m-%d %H:%M:%S KST')
    
    return df, update_time

# =============================================================================
# 5. 세션 상태 및 라우팅 컨트롤러
# =============================================================================
if 'quant_data' not in st.session_state: 
    st.session_state['quant_data'] = None
    st.session_state['last_updated'] = "수집 전"
    
    global_data = load_global_data()
    if global_data is not None:
        st.session_state['quant_data'] = global_data['df']
        st.session_state['last_updated'] = global_data['updated_time']

if 'is_admin' not in st.session_state: 
    st.session_state['is_admin'] = False

query_params = st.query_params
if query_params.get("admin") == ADMIN_SECRET_CODE:
    st.session_state['is_admin'] = True

if st.session_state['quant_data'] is None:
    if st.session_state['is_admin']:
        st.title("🛠️ 데이터 수집 센터")
        st.info("현재 서버에 랭킹 데이터가 없습니다. 야후 파이낸스 통신을 가동하세요.")

        if st.button("🚀 전체 S&P 1500 실시간 스크래핑 시작", use_container_width=True):
            with st.spinner("야후 파이낸스 재무상태표 원본을 분석 중입니다... (약 10~15분 소요)"):
                try:
                    df, updated_time = process_market_data()
                    save_global_data(df, updated_time)
                    st.session_state['quant_data'] = df
                    st.session_state['last_updated'] = updated_time
                    st.rerun()
                except Exception as e:
                    st.error(f"서버 접근 차단 또는 에러 발생: {e}")
        
        st.markdown("<br><br><br><div style='text-align: center; color: #888; font-size: 12px;'>powered by TeamChilli</div>", unsafe_allow_html=True)
        st.stop()
        
    else:
        st.title("💰 피터 린치 주당 순현금 랭킹")
        st.info("관리자가 API를 통해 실시간 마켓 데이터를 수집하고 있습니다. 잠시 후 다시 접속해 주세요.")
        st.markdown("<br><br><br><div style='text-align: center; color: #888; font-size: 12px;'>powered by TeamChilli</div>", unsafe_allow_html=True)
        st.stop()

# =============================================================================
# 6. 메인 UI 화면 
# =============================================================================
st.title("💰 피터 린치 주당 순현금 랭킹")
st.caption(f"최근 데이터 동기화: {st.session_state['last_updated']}")

tab1, tab2, tab3 = st.tabs(["대시보드", "Net Cash TOP 100", "개별 종목 분석"])

with tab1:
    st.subheader("💡 피터 린치의 오리지널 순현금 모델")
    st.markdown('''
    "어떤 회사의 주당 순현금이 3달러이고 주가가 10달러라면, 당신은 이 주식을 10달러가 아니라 **실질적으로 7달러**에 사는 것이다." 
    - *피터 린치 (Peter Lynch)*
    
    * **순현금 공식:** (순수 현금 및 단기투자자산) - (순수 장기 부채 및 1년 내 만기도래분)
    * **순현금 PER:** (현재 주가 - 주당 순현금) / 1주당 순이익(EPS)
    
    영업을 위한 단기적인 외상값이나 가짜 부채(매장 리스 등)는 무시하고, 금고에 당장 현금화할 수 있는 자산에서 은행 빚을 
    모두 털어낸 깐깐한 오리지널 공식을 사용합니다. 겉으로 보이는 PER에 속지 마시고, 
    기업의 알짜 현금을 차감한 **'순현금 PER'**을 통해 숨겨진 저평가 기업을 발굴하세요.
    ''')
    st.divider()
    
    if st.session_state['is_admin']:
        st.markdown("### 🛠️ [관리자 전용] 데이터 갱신 패널")
        col1, col2 = st.columns(2)
        with col1:
            if st.button("야후 API 전체 강제 재수집", use_container_width=True):
                with st.spinner("야후 서버에서 전체 데이터를 다시 긁어오는 중..."):
                    try:
                        df, updated_time = process_market_data()
                        save_global_data(df, updated_time)
                        st.session_state['quant_data'] = df
                        st.session_state['last_updated'] = updated_time
                        st.success("글로벌 데이터가 갱신되었습니다.")
                        time.sleep(1)
                        st.rerun()
                    except Exception as e:
                        st.error(f"통신 실패: {e}")
        with col2:
            if st.session_state['quant_data'] is not None:
                csv_data = st.session_state['quant_data'].to_csv(index=False).encode('utf-8-sig')
                st.download_button(
                    label="현재 데이터 다운로드 (CSV)", 
                    data=csv_data, 
                    file_name=f"PeterLynch_NetCash_{datetime.datetime.now().strftime('%Y%m%d')}.csv", 
                    mime="text/csv", 
                    use_container_width=True
                )

with tab2:
    st.subheader("🏆 순현금비율(%) TOP 100 (S&P 1500 비금융)")
    if st.session_state['quant_data'] is not None:
        df = st.session_state['quant_data'].copy()
        df = df[df['순현금비율(%)'] > 0] 
        
        display_cols = ['순위', '종목', '기업명', '순현금비율(%)', 'PER', 'Fwd_PER', '순현금_PER', '순이익성장', '현재주가($)', '주당순현금($)', '총현금(M$)', '실질장기부채(M$)']
        
        # 💡 [UI 업데이트] 천 단위 콤마 렌더링 및 칼럼 폭 최적화
        st.dataframe(
            df[display_cols].head(100),
            use_container_width=True,
            hide_index=True,
            column_config={
                "순위": st.column_config.NumberColumn(width=50, format="%d"),
                "종목": st.column_config.TextColumn(width=80),
                "기업명": st.column_config.TextColumn(width="medium"),
                "순현금비율(%)": st.column_config.ProgressColumn(format="%d%%", min_value=0, max_value=100),
                "PER": st.column_config.NumberColumn(format="%,.1f"),
                "Fwd_PER": st.column_config.NumberColumn(format="%,.1f"),
                "순현금_PER": st.column_config.NumberColumn(format="%,.1f", help="(현재 주가 - 주당 순현금) / EPS"),
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
        ticker_input = st.text_input("분석할 티커를 입력하세요 (예: AAPL, GOOGL, META)")
        submit_btn = st.form_submit_button("분석 시작")
        
    if submit_btn and ticker_input:
        tk = ticker_input.upper().strip()
        df = st.session_state['quant_data']
        
        if df is not None and tk in df['종목'].values:
            with st.spinner("DB 로드 중..."):
                row = df[df['종목'] == tk].iloc[0]
                
                price = row['현재주가($)']
                net_cash_per_share = row['주당순현금($)']
                ratio = row['순현금비율(%)']
                
                if ratio > 50: summ = "엄청난 수준의 현금을 보유하고 있습니다. 회사 금고의 현금이 주가의 절반 이상을 보증합니다!"
                elif ratio > 20: summ = "매우 건전한 상태입니다. 든든한 순현금이 하락장을 방어해 줄 것입니다."
                elif ratio > 0: summ = "실질 장기부채보다 현금이 더 많아 재무적으로 안정적입니다."
                else: summ = "현재 보유한 현금보다 갚아야 할 실질 장기부채가 더 많아 주당 순현금이 마이너스(-) 상태입니다."

                st.success(f"### {row['기업명']} ({tk}) : 순현금비율 {ratio:,.1f}%")
                st.caption(f"섹터: {row['섹터']} | 랭킹: 비금융 전체 {row['순위']}위")
                st.info(f"💡 총평: {summ}")
                
                # 💡 [UI 업데이트] 개별 종목 분석 패널 천 단위 콤마 반영
                col1, col2, col3 = st.columns(3)
                col1.metric("현재 주가", f"${price:,.2f}")
                col2.metric("주당 순현금", f"${net_cash_per_share:,.2f}", f"{ratio:,.1f}% of Price")
                col3.metric("실질 매수단가", f"${max(0, price - net_cash_per_share):,.2f}", delta_color="inverse")
                
                col4, col5, col6 = st.columns(3)
                col4.metric("PER (기본)", f"{row['PER']:,.1f}" if row['PER'] > 0 else "N/A")
                col5.metric("Forward PER", f"{row['Fwd_PER']:,.1f}" if row['Fwd_PER'] > 0 else "N/A")
                col6.metric("순현금 PER", f"{row['순현금_PER']:,.1f}" if row['순현금_PER'] > 0 else "N/A", help="실질 매수단가 / EPS")
                
                col7, col8, col9 = st.columns(3)
                col7.metric("총 현금 (Total Cash)", f"${row['총현금(M$)']:,.1f} M")
                col8.metric("조정 장기부채 (Long Term)", f"${row['실질장기부채(M$)']:,.1f} M")
                col9.metric("연간 순이익 성장", row['순이익성장'] if row['순이익성장'] != '-' else "성장 안함")
                
        else:
            st.warning(f"'{tk}'는 현재 DB에 없어 야후 파이낸스에서 실시간으로 재무제표를 조회합니다.")
            with st.spinner("야후 서버에서 정보 추출 중..."):
                try:
                    raw = calculate_single_stock_lynch_model(tk)
                    if not raw:
                        st.error("데이터 부족, 상장폐지, 또는 순현금 분석에서 제외되는 금융/리츠 섹터입니다.")
                    else:
                        price = raw['현재주가($)']
                        net_cash_per_share = raw['주당순현금($)']
                        ratio = raw['순현금비율(%)']
                        
                        if ratio > 50: summ = "엄청난 수준의 현금을 보유하고 있습니다. 회사 금고의 현금이 주가의 절반 이상을 보증합니다!"
                        elif ratio > 20: summ = "매우 건전한 상태입니다. 든든한 순현금이 하락장을 방어해 줄 것입니다."
                        elif ratio > 0: summ = "실질 장기부채보다 현금이 더 많아 재무적으로 안정적입니다."
                        else: summ = "현재 보유한 현금보다 갚아야 할 실질 장기부채가 더 많아 주당 순현금이 마이너스(-) 상태입니다."

                        st.success(f"### {raw['기업명']} ({tk}) : 순현금비율 {ratio:,.1f}%")
                        st.caption(f"섹터: {raw['섹터']} | (실시간 산출 데이터)")
                        st.info(f"💡 총평: {summ}")
                        
                        col1, col2, col3 = st.columns(3)
                        col1.metric("현재 주가", f"${price:,.2f}")
                        col2.metric("주당 순현금", f"${net_cash_per_share:,.2f}", f"{ratio:,.1f}% of Price")
                        col3.metric("실질 매수단가", f"${max(0, price - net_cash_per_share):,.2f}", delta_color="inverse")
                        
                        col4, col5, col6 = st.columns(3)
                        col4.metric("PER (기본)", f"{raw['PER']:,.1f}" if raw['PER'] > 0 else "N/A")
                        col5.metric("Forward PER", f"{raw['Fwd_PER']:,.1f}" if raw['Fwd_PER'] > 0 else "N/A")
                        col6.metric("순현금 PER", f"{raw['순현금_PER']:,.1f}" if raw['순현금_PER'] > 0 else "N/A")
                        
                        col7, col8, col9 = st.columns(3)
                        col7.metric("총 현금 (Total Cash)", f"${raw['총현금(M$)']:,.1f} M")
                        col8.metric("조정 장기부채 (Long Term)", f"${raw['실질장기부채(M$)']:,.1f} M")
                        col9.metric("연간 순이익 성장", raw['순이익성장'] if raw['순이익성장'] != '-' else "성장 안함")
                        
                except Exception as e:
                    logger.error(f"Live fetch error for {tk}: {e}")
                    st.error("야후 서버 통신에 실패했습니다. 잠시 후 다시 시도해 주세요.")

# 푸터
st.markdown("<br><br><br><div style='text-align: center; color: #888; font-size: 12px;'>powered by TeamChilli</div>", unsafe_allow_html=True)
