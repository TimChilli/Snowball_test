import streamlit as st
import yfinance as yf
import pandas as pd
import numpy as np
import requests
import io
import time
import datetime
import pytz

# --- 1. 페이지 및 기본 설정 ---
st.set_page_config(page_title="SnowBall", page_icon="☃", layout="wide")

def get_grade(z):
    if z >= 1.0: return 'A'
    elif z >= 0.3: return 'B'
    elif z >= -0.3: return 'C'
    elif z >= -1.0: return 'D'
    else: return 'F'

def get_rating(score):
    if score >= 80: return 'Strong Buy'
    elif score >= 60: return 'Buy'
    elif score >= 40: return 'Hold'
    elif score >= 20: return 'Sell'
    else: return 'Strong Sell'

def get_trade_day():
    tz = pytz.timezone('US/Eastern')
    now = datetime.datetime.now(tz)
    if now.hour < 4:
        return (now - datetime.timedelta(days=1)).strftime('%Y-%m-%d')
    return now.strftime('%Y-%m-%d')

# --- 2. 자동 수집 함수 ---
@st.cache_data(show_spinner=False)
def fetch_market_data(trade_day):
    session = requests.Session()
    session.headers.update({'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'})
    
    def get_t(url):
        res = requests.get(url, headers=session.headers)
        df = pd.read_html(io.StringIO(res.text))[0]
        return df['Symbol' if 'Symbol' in df.columns else 'Ticker symbol'].tolist()
    
    tickers = list(set(get_t('https://en.wikipedia.org/wiki/List_of_S%26P_500_companies') + get_t('https://en.wikipedia.org/wiki/List_of_S%26P_400_companies')))
    tickers = [t.replace('.', '-') for t in tickers]

    temp_list = []
    progress_bar = st.progress(0)
    status_text = st.empty()
    total = len(tickers)

    for i, ticker in enumerate(tickers, 1):
        time.sleep(0.1) 
        try:
            s = yf.Ticker(ticker)
            info = s.info
            hist = s.history(period="1y")
            if hist.empty or len(hist) < 22: continue
            if 'sector' not in info and 'currentPrice' not in info: continue
            
            c_name = info.get('shortName', info.get('longName', ticker))
            sector = info.get('sector', 'Unknown')
            track = 'FIN' if sector in ['Financial Services', 'Real Estate'] else 'STD'
            
            payout_ratio = float(info.get('payoutRatio') or 0.0)
            debt_eq = info.get('debtToEquity')
            debt_to_asset = (float(debt_eq) / 100.0) / (1 + float(debt_eq) / 100.0) * 100.0 if debt_eq and float(debt_eq) >= 0 else 100.0
            ebitda_val = info.get('ebitda', 1.0)
            ie_val = info.get('interestExpense', 1.0) if info.get('interestExpense') not in [None, 0] else 1.0
            mcap = info.get('marketCap', 1) if info.get('marketCap') else 1
            div_yield = float(info.get('dividendYield') or 0.0)
            
            if track == 'FIN':
                hybrid_prf = float(info.get('returnOnEquity') or 0.0)
                total_shareholder_yield = div_yield
                pe = info.get('trailingPE')
                pb = info.get('priceToBook')
                val_score = ((1/float(pe) if pe and float(pe) > 0 else 0)*0.5) + ((1/float(pb) if pb and float(pb) > 0 else 0)*0.5)
            else:
                fcf = float(info.get('freeCashflow') or 0.0)
                fcf_yield = fcf / mcap
                total_shareholder_yield = div_yield + max(0, fcf_yield)
                hybrid_prf = (float(info.get('returnOnAssets', 0))*0.5) + (float(info.get('operatingMargins', 0))*0.5)

                peg = info.get('pegRatio')
                pb = info.get('priceToBook')
                if peg and float(peg) > 0: val_peg = 1 / float(peg)
                else:
                    pe = info.get('forwardPE') or info.get('trailingPE')
                    val_peg = 1 / (float(pe) / 15) if pe and float(pe) > 0 else 0
                val_score = (val_peg * 0.7) + ((1/float(pb) if pb and float(pb) > 0 else 0)*0.3)

            rev_g = max(-0.5, min(0.5, float(info.get('revenueGrowth') or 0.0)))
            earn_g = max(-0.5, min(0.5, float(info.get('earningsGrowth') or 0.0)))
            grw_score = (rev_g + earn_g) / 2
            mom_score = (hist['Close'].iloc[-21] / hist['Close'].iloc[0]) - 1

            temp_list.append({
                '종목': ticker, '기업명': c_name, '섹터': sector, '트랙': track, 'PayoutRatio': payout_ratio,
                'VAL': val_score, 'MOM': mom_score, 'GRW': grw_score, 'PRF': hybrid_prf, 
                'YLD': total_shareholder_yield, 'DEBT': debt_to_asset, 'ICR': (ebitda_val / ie_val)
            })
        except Exception: pass
        
        if i % 10 == 0 or i == total:
            progress_bar.progress(i / total)
            # 💡 [버그 픽스] 진행률 텍스트 더 직관적으로 표시
            status_text.text(f"⏳ 데이터 수집 및 분석 중... ({i}/{total})")

    progress_bar.empty()
    status_text.empty()

    if len(temp_list) == 0:
        raise ValueError("야후 파이낸스 서버 접속 차단 (Too Many Requests).")

    df = pd.DataFrame(temp_list).replace([np.inf, -np.inf], 0).fillna(0)
    cols = ['VAL', 'MOM', 'GRW', 'PRF', 'YLD', 'DEBT', 'ICR']
    for c in cols: df[c] = df[c].clip(df[c].quantile(0.01), df[c].quantile(0.99))

    sector_stats = {sct: {c: {'mean': df[df['섹터']==sct][c].mean(), 'std': df[df['섹터']==sct][c].std()} for c in cols} for sct in df['섹터'].unique()}
    track_stats = {trk: {c: {'mean': df[df['트랙']==trk][c].mean(), 'std': df[df['트랙']==trk][c].std()} for c in cols} for trk in df['트랙'].unique()}

    z_data = {}
    for c in cols:
        sign = -1 if c in ['DEBT'] else 1
        sct_z = (df[c] - df.groupby('섹터')[c].transform('mean')) / (df.groupby('섹터')[c].transform('std') + 1e-9)
        trk_z = (df[c] - df.groupby('트랙')[c].transform('mean')) / (df.groupby('트랙')[c].transform('std') + 1e-9)
        z_data[c] = ((sct_z * 0.5) + (trk_z * 0.5)) * sign
        z_data[c] = z_data[c].clip(-3.0, 3.0)

    z_hlt = (z_data['DEBT'] + z_data['ICR']) / 2
    
    penalty, trap_penalty = [], []
    for i, row in df.iterrows():
        p = 0.15 * ((row['DEBT']/50)**2.5) if row['트랙'] == 'STD' and row['DEBT'] >= 50 else 0
        t_p = 2.0 if row['PayoutRatio'] > 1.0 or row['PayoutRatio'] < 0 else 0
        penalty.append(p)
        trap_penalty.append(t_p)

    df['Base'] = (z_data['VAL']*0.15 + z_data['MOM']*0.15 + z_data['GRW']*0.20 + z_data['PRF']*0.20 + z_data['YLD']*0.10 + z_hlt*0.20) - penalty - trap_penalty
    df['최종점수'] = round(((df['Base'] - (-3.0)) / 6.0) * 100, 1).clip(0, 100)
    df['투자의견'] = df['최종점수'].apply(get_rating)

    df['등급요약'] = [f"건전[{get_grade(z_hlt.iloc[i])}] 수익[{get_grade(z_data['PRF'].iloc[i])}] 성장[{get_grade(z_data['GRW'].iloc[i])}] 가치[{get_grade(z_data['VAL'].iloc[i])}] 모멘[{get_grade(z_data['MOM'].iloc[i])}] 환원[{get_grade(z_data['YLD'].iloc[i])}]" for i in range(len(df))]

    df = df.sort_values('최종점수', ascending=False).reset_index(drop=True)
    df.insert(0, '순위', range(1, len(df) + 1))
    
    kst = pytz.timezone('Asia/Seoul')
    update_time = datetime.datetime.now(kst).strftime('%Y-%m-%d %H:%M:%S KST')
    
    return df[['순위', '종목', '기업명', '최종점수', '투자의견', '섹터', '등급요약']], sector_stats, track_stats, update_time

# --- 3. 세션 초기화 및 관리자 모드 ---
if 'quant_data' not in st.session_state: st.session_state['quant_data'] = None
if 'sector_stats' not in st.session_state: st.session_state['sector_stats'] = None
if 'track_stats' not in st.session_state: st.session_state['track_stats'] = None
if 'last_updated' not in st.session_state: st.session_state['last_updated'] = "수집 전"
if 'is_admin' not in st.session_state: st.session_state['is_admin'] = False

# URL 파라미터 체크
if st.query_params.get("admin") == "chilli2026":
    st.session_state['is_admin'] = True

with st.sidebar:
    st.markdown("<br>"*15, unsafe_allow_html=True)
    
    # 투명한 공간을 눌러야 열리는 관리자 암호 입력칸
    with st.expander(" ", expanded=False): 
        admin_pw = st.text_input("Admin Code", type="password", label_visibility="collapsed")
        if admin_pw == "chilli2026":
            st.session_state['is_admin'] = True
            st.success("Admin 모드 활성")

    st.caption("powered by TeamChilli")

if st.session_state['quant_data'] is None:
    # 💡 [버그 픽스] 스피너 메시지에 소요 시간 명확히 안내
    with st.spinner("🔄 오늘의 마켓 데이터를 준비하고 있습니다 (최초 1회 약 3~5분 소요)..."):
        try:
            current_trade_day = get_trade_day()
            df, sec_s, trk_s, updated_time = fetch_market_data(current_trade_day)
            st.session_state['quant_data'] = df
            st.session_state['sector_stats'] = sec_s
            st.session_state['track_stats'] = trk_s
            st.session_state['last_updated'] = updated_time
            st.rerun()
        except Exception:
            st.error("야후 파이낸스 서버 접근이 차단되었습니다 (Too Many Requests).")
            st.info("관리자 모드로 접속하여 로컬에서 추출한 백업 데이터를 업로드해주세요.")

# --- 4. 메인 화면 ---
st.title("☃ SnowBall")
st.caption(f"최근 데이터 동기화: {st.session_state['last_updated']} (매일 미국 프리장 오픈 시 갱신)")

tab1, tab2, tab3 = st.tabs(["대시보드", "SnowBall TOP 100", "개별 종목 분석"])

with tab1:
    st.subheader("SnowBall 6-Pillar 평가 모델")
    st.markdown("""
    * **건전성:** 재무 리스크 방어력 (D/A, 이자보상배율)
    * **수익성:** [일반] ROA+영업이익률 / [금융·리츠] ROE
    * **성장성:** 펀더멘털 확장성 (매출 및 이익 성장률)
    * **모멘텀:** 시장의 중장기 트렌드 추세 (12개월)
    * **가치:** [일반] PEG+P/B / [금융·리츠] P/E+P/B
    * **환원율:** 주주 친화 정책 및 잉여현금흐름
    """)
    st.divider()
    
    if st.session_state['is_admin']:
        st.markdown("### [관리자 전용] 데이터 컨트롤 패널")
        col1, col2 = st.columns(2)
        with col1:
            if st.button("강제 재수집 (캐시 초기화)", use_container_width=True):
                st.cache_data.clear()
                st.rerun()
            if st.session_state['quant_data'] is not None:
                csv_data = st.session_state['quant_data'].to_csv(index=False).encode('utf-8-sig')
                st.download_button("현재 데이터 다운로드 (CSV)", data=csv_data, file_name=f"SnowBall_{datetime.datetime.now().strftime('%Y%m%d')}.csv", mime="text/csv", use_container_width=True)
        with col2:
            uploaded_file = st.file_uploader("백업 엑셀/CSV 수동 업로드", type=["xlsx", "csv"])
            if uploaded_file is not None:
                try:
                    df = pd.read_csv(uploaded_file) if uploaded_file.name.endswith('.csv') else pd.read_excel(uploaded_file)
                    st.session_state['quant_data'] = df
                    cols = ['VAL', 'MOM', 'GRW', 'PRF', 'YLD', 'DEBT', 'ICR']
                    st.session_state['sector_stats'] = {sct: {c: {'mean': df[df['섹터']==sct][c].mean(), 'std': df[df['섹터']==sct][c].std()} for c in cols if c in df.columns} for sct in df['섹터'].unique()}
                    st.session_state['track_stats'] = {trk: {c: {'mean': df[df['트랙']==trk][c].mean(), 'std': df[df['트랙']==trk][c].std()} for c in cols if c in df.columns} for trk in df['트랙'].unique()} if '트랙' in df.columns else {}
                    st.session_state['last_updated'] = "수동 데이터 업로드 완료"
                    st.success("데이터 로드 성공!")
                    time.sleep(1)
                    st.rerun()
                except Exception as e:
                    st.error(f"업로드 에러: {e}")
    else:
        st.info("데이터가 캐싱되어 작동 중입니다. 탭을 이동하며 분석 결과를 확인하세요.")

with tab2:
    st.subheader("SnowBall 퀀트 랭킹")
    if st.session_state['quant_data'] is not None:
        st.dataframe(
            st.session_state['quant_data'].head(100),
            use_container_width=True,
            hide_index=True,
            column_config={
                "순위": st.column_config.NumberColumn(width="small"),
                "종목": st.column_config.TextColumn(width="small"),
                "기업명": st.column_config.TextColumn(width="medium"),
                "최종점수": st.column_config.NumberColumn(width="small", format="%.1f"),
                "투자의견": st.column_config.TextColumn(width="small"),
                "섹터": st.column_config.TextColumn(width="medium"),
                "등급요약": st.column_config.TextColumn(width="large"),
            }
        )

with tab3:
    st.subheader("개별 종목 분석")
    with st.form("search_form"):
        ticker_input = st.text_input("분석할 티커 (예: AAPL)")
        submit_btn = st.form_submit_button("분석 시작")
        
    if submit_btn and ticker_input:
        if st.session_state['sector_stats'] is None:
            st.error("기준 데이터가 없습니다. 엑셀 데이터를 업로드해주세요.")
        else:
            with st.spinner("분석 중..."):
                tk = ticker_input.upper().strip()
                try:
                    s = yf.Ticker(tk)
                    info = s.info
                    hist = s.history(period="1y")
                    
                    if hist.empty or len(hist) < 22:
                        st.error(f"[{tk}] 데이터 부족 또는 상장폐지 종목입니다.")
                    elif 'sector' not in info and 'currentPrice' not in info:
                        st.error(f"[{tk}] ETF 또는 재무 데이터 미제공 종목입니다.")
                    else:
                        sector = info.get('sector', 'Unknown')
                        track = 'FIN' if sector in ['Financial Services', 'Real Estate'] else 'STD'
                        c_name = info.get('shortName', tk)
                        
                        debt_eq = info.get('debtToEquity')
                        debt_to_asset = (float(debt_eq) / 100.0) / (1 + float(debt_eq) / 100.0) * 100.0 if debt_eq and float(debt_eq) >= 0 else 100.0
                        ie_val = info.get('interestExpense', 1.0) if info.get('interestExpense') not in [None, 0] else 1.0
                        icr_val = info.get('ebitda', 1.0) / ie_val
                        div_yield = float(info.get('dividendYield') or 0.0)
                        
                        if track == 'FIN':
                            hybrid_prf = float(info.get('returnOnEquity') or 0.0)
                            total_shareholder_yield = div_yield
                            pe = info.get('trailingPE')
                            pb = info.get('priceToBook')
                            val_score = ((1/float(pe) if pe and float(pe)>0 else 0)*0.5) + ((1/float(pb) if pb and float(pb)>0 else 0)*0.5)
                        else:
                            mcap = info.get('marketCap', 1) or 1
                            fcf_yield = float(info.get('freeCashflow') or 0.0) / mcap
                            total_shareholder_yield = div_yield + max(0, fcf_yield)
                            hybrid_prf = (float(info.get('returnOnAssets') or 0.0)*0.5) + (float(info.get('operatingMargins') or 0.0)*0.5)
                            peg = info.get('pegRatio')
                            pb = info.get('priceToBook')
                            if peg and float(peg) > 0: val_peg = 1 / float(peg)
                            else:
                                pe = info.get('forwardPE') or info.get('trailingPE')
                                val_peg = 1 / (float(pe) / 15) if pe and float(pe) > 0 else 0
                            val_score = (val_peg * 0.7) + ((1/float(pb) if pb and float(pb)>0 else 0)*0.3)

                        rev_g = max(-0.5, min(0.5, float(info.get('revenueGrowth') or 0.0)))
                        earn_g = max(-0.5, min(0.5, float(info.get('earningsGrowth') or 0.0)))
                        grw_score = (rev_g + earn_g) / 2
                        mom_score = (hist['Close'].iloc[-21] / hist['Close'].iloc[0]) - 1

                        raw = {'VAL': val_score, 'MOM': mom_score, 'GRW': grw_score, 'PRF': hybrid_prf, 'YLD': total_shareholder_yield, 'DEBT': debt_to_asset, 'ICR': icr_val}
                        sct_stats = st.session_state['sector_stats'].get(sector, st.session_state['sector_stats'][list(st.session_state['sector_stats'].keys())[0]])
                        trk_stats = st.session_state['track_stats'].get(track, st.session_state['track_stats'][list(st.session_state['track_stats'].keys())[0]])

                        z_scores = {}
                        for key in raw.keys():
                            s_z = (raw[key] - sct_stats[key]['mean']) / (sct_stats[key]['std'] + 1e-9)
                            t_z = (raw[key] - trk_stats[key]['mean']) / (trk_stats[key]['std'] + 1e-9)
                            z = (s_z * 0.5 + t_z * 0.5) * (-1 if key == 'DEBT' else 1)
                            z_scores[key] = max(-3.0, min(3.0, z))

                        z_hlt = (z_scores['DEBT'] + z_scores['ICR']) / 2
                        penalty = 0.15 * ((debt_to_asset/50)**2.5) if track == 'STD' and debt_to_asset >= 50 else 0
                        payout_ratio = float(info.get('payoutRatio') or 0.0)
                        trap_penalty = 2.0 if payout_ratio > 1.0 or payout_ratio < 0 else 0

                        base = (z_scores['VAL']*0.15 + z_scores['MOM']*0.15 + z_scores['GRW']*0.20 + z_scores['PRF']*0.20 + z_scores['YLD']*0.10 + z_hlt*0.20) - penalty - trap_penalty
                        final_score = round(max(0, min(100, ((base - (-3.0)) / 6.0) * 100)), 1)
                        
                        eval_scores = {'가치': z_scores['VAL'], '모멘텀': z_scores['MOM'], '성장성': z_scores['GRW'], '수익성': z_scores['PRF'], '환원율': z_scores['YLD'], '건전성': z_hlt}
                        best_p = max(eval_scores, key=eval_scores.get)
                        worst_p = min(eval_scores, key=eval_scores.get)
                        
                        if trap_penalty > 0: summ = "배당 함정(Yield Trap) 종목입니다. 절대 주의하세요."
                        elif final_score >= 80: summ = "흠잡을 데 없는 완벽한 수치입니다."
                        elif final_score >= 60: summ = f"[{best_p}] 지표가 훌륭합니다. [{worst_p}] 지표만 유의하시면 안정적인 종목입니다."
                        elif final_score >= 40: summ = f"무난한 수준입니다. [{best_p}] 지표는 긍정적이나 [{worst_p}] 지표 확인이 필요합니다."
                        elif final_score >= 20: summ = f"[{worst_p}] 지표가 심각하여 매수에 주의가 필요합니다."
                        else: summ = f"[{worst_p}] 지표 등 전반적인 상태가 매우 부진합니다."

                        st.success(f"### {c_name} ({tk}) : {final_score} 점 ({get_rating(final_score)})")
                        st.caption(f"섹터: {sector} | 유니버스: {'금융/리츠 트랙' if track=='FIN' else '스탠다드 트랙'}")
                        st.info(f"총평: {summ}")
                        
                        col1, col2, col3 = st.columns(3)
                        col1.metric("건전성", get_grade(z_hlt))
                        col2.metric("수익성", get_grade(z_scores['PRF']))
                        col3.metric("성장성", get_grade(z_scores['GRW']))
                        col4, col5, col6 = st.columns(3)
                        col4.metric("가치", get_grade(z_scores['VAL']))
                        col5.metric("모멘텀", get_grade(z_scores['MOM']))
                        col6.metric("환원율", get_grade(z_scores['YLD']))
                        
                        if penalty > 0: st.warning("부채 위험에 따른 징벌적 감점 적용됨")

                except Exception as e:
                    if "429" in str(e) or "Rate limited" in str(e): st.error("야후 서버가 일시적으로 요청을 차단했습니다.")
                    else: st.error(f"분석 실패: 티커를 다시 확인해 주세요.")
