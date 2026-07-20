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

st.set_page_config(page_title="Lynch's Net Cash", page_icon="💰", layout="wide")

SHARED_FILE = "lynch_shared_data.pkl"

def save_global_data(df, updated_time):
    data = {'df': df, 'updated_time': updated_time}
    with open(SHARED_FILE, 'wb') as f:
        pickle.dump(data, f)

def load_global_data():
    if os.path.exists(SHARED_FILE):
        try:
            with open(SHARED_FILE, 'rb') as f:
                return pickle.load(f)
        except: return None
    return None

def get_trade_day():
    tz = pytz.timezone('US/Eastern')
    now = datetime.datetime.now(tz)
    if now.hour < 4: return (now - datetime.timedelta(days=1)).strftime('%Y-%m-%d')
    return now.strftime('%Y-%m-%d')

# 글로벌 세션 초기화
if 'quant_data' not in st.session_state: 
    st.session_state['quant_data'] = None
    st.session_state['last_updated'] = "수집 전"
    
    global_data = load_global_data()
    if global_data is not None:
        st.session_state['quant_data'] = global_data['df']
        st.session_state['last_updated'] = global_data['updated_time']

if 'is_admin' not in st.session_state: st.session_state['is_admin'] = False
if st.query_params.get("admin") == "chillixlaclffl": st.session_state['is_admin'] = True

if st.session_state['quant_data'] is None:
    if st.session_state['is_admin']:
        st.title("💰 피터 린치 순현금 초기 설정")
        st.info("데이터가 없습니다. 엑셀 백업 파일을 업로드해주세요.")
        uploaded_file = st.file_uploader("기존 엑셀/CSV 업로드", type=["xlsx", "csv"])
        if uploaded_file is not None:
            try:
                df = pd.read_csv(uploaded_file) if uploaded_file.name.endswith('.csv') else pd.read_excel(uploaded_file)
                save_global_data(df, "수동 파일 동기화 완료")
                st.session_state['quant_data'] = df
                st.session_state['last_updated'] = "수동 파일 동기화 완료"
                st.success("로드 성공! 글로벌 환경에 적용되었습니다.")
                time.sleep(1)
                st.rerun()
            except Exception as e: st.error(f"업로드 에러: {e}")
        st.stop()
    else:
        st.info("관리자가 마켓 데이터를 준비하고 있습니다. 잠시 후 다시 접속해 주세요.")
        st.stop()

# 메인 UI
st.title("💰 피터 린치 주당 순현금 랭킹")
st.caption(f"최근 데이터 동기화: {st.session_state['last_updated']}")

tab1, tab2, tab3 = st.tabs(["대시보드", "Net Cash TOP 100", "개별 종목 분석"])

with tab1:
    st.subheader("💡 피터 린치의 순현금 (Net Cash per Share) 모델")
    st.markdown('''
    "어떤 회사의 주당 순현금이 3달러이고 주가가 10달러라면, 당신은 이 주식을 10달러가 아니라 **실질적으로 7달러**에 사는 것이다." 
    - *피터 린치 (Peter Lynch)*
    
    * **순현금 공식:** (현금 및 단기투자자산) - (총 부채)
    * **주당 순현금:** 순현금 / 총 발행 주식 수
    * **순현금비율(%):** (주당 순현금 / 현재 주가) × 100
    
    비율이 높을수록 기업이 보유한 현금이 주가를 강력하게 지지하고 있다는 뜻이며 하락장에 극강의 방어력을 보여줍니다.
    ''')
    st.divider()
    
    if st.session_state['is_admin']:
        st.markdown("### 🛠️ [관리자 전용] 데이터 갱신 패널")
        uploaded_file = st.file_uploader("백업 엑셀/CSV 수동 업로드 (동기화)", type=["xlsx", "csv"])
        if uploaded_file is not None:
            try:
                df = pd.read_csv(uploaded_file) if uploaded_file.name.endswith('.csv') else pd.read_excel(uploaded_file)
                save_global_data(df, "수동 파일 동기화 완료")
                st.session_state['quant_data'] = df
                st.session_state['last_updated'] = "수동 파일 동기화 완료"
                st.success("데이터 로드 성공! 손님들에게 즉시 노출됩니다.")
                time.sleep(1)
                st.rerun()
            except Exception as e: st.error(f"업로드 에러: {e}")

with tab2:
    st.subheader("🏆 순현금비율(%) TOP 100 (S&P 1500)")
    if st.session_state['quant_data'] is not None:
        df = st.session_state['quant_data'].copy()
        df = df[df['순현금비율(%)'] > 0] # 순현금이 플러스인 기업만 필터링
        
        st.dataframe(
            df.head(100),
            use_container_width=True,
            hide_index=True,
            column_config={
                "순위": st.column_config.NumberColumn(width="small"),
                "종목": st.column_config.TextColumn(width="small"),
                "기업명": st.column_config.TextColumn(width="medium"),
                "현재주가($)": st.column_config.NumberColumn(format="$%.2f"),
                "주당순현금($)": st.column_config.NumberColumn(format="$%.2f"),
                "순현금비율(%)": st.column_config.ProgressColumn(
                    format="%d%%",
                    min_value=0,
                    max_value=100
                ),
                "총현금(B$)": st.column_config.NumberColumn(format="%.2f B"),
                "총부채(B$)": st.column_config.NumberColumn(format="%.2f B")
            }
        )

with tab3:
    st.subheader("🔍 개별 종목 분석")
    with st.form("search_form"):
        ticker_input = st.text_input("분석할 티커 (예: AAPL, META)")
        submit_btn = st.form_submit_button("분석 시작")
        
    if submit_btn and ticker_input:
        tk = ticker_input.upper().strip()
        df = st.session_state['quant_data']
        
        if df is not None and tk in df['종목'].values:
            with st.spinner("조회 중..."):
                row = df[df['종목'] == tk].iloc[0]
                
                price = row['현재주가($)']
                net_cash_per_share = row['주당순현금($)']
                ratio = row['순현금비율(%)']
                
                if ratio > 50: summ = "엄청난 수준의 현금을 보유하고 있습니다. 회사 금고의 현금이 주가의 절반 이상을 보증합니다!"
                elif ratio > 20: summ = "매우 건전한 상태입니다. 든든한 순현금이 하락장을 방어해 줄 것입니다."
                elif ratio > 0: summ = "부채보다 현금이 더 많아 재무적으로 안정적입니다."
                else: summ = "현재 현금보다 갚아야 할 부채가 더 많아 주당 순현금이 마이너스(-) 상태입니다."

                st.success(f"### {row['기업명']} ({tk}) : 순현금비율 {ratio}%")
                st.info(f"💡 총평: {summ}")
                
                col1, col2, col3 = st.columns(3)
                col1.metric("현재 주가", f"${price}")
                col2.metric("주당 순현금", f"${net_cash_per_share}", f"{ratio}% of Price")
                col3.metric("실질 매수단가", f"${max(0, price - net_cash_per_share):.2f}", delta_color="inverse")
                
                col4, col5 = st.columns(2)
                col4.metric("총 현금 (Total Cash)", f"${row['총현금(B$)']} Billion")
                col5.metric("총 부채 (Total Debt)", f"${row['총부채(B$)']} Billion")
        else:
            st.error(f"'{tk}'는 DB에 없거나 순현금 분석 대상이 아닙니다. (현재 S&P 1500만 조회 가능)")

st.markdown("<br><br><br><div style='text-align: center; color: #888; font-size: 12px;'>powered by TeamChilli</div>", unsafe_allow_html=True)
```
