def calculate_single_stock_lynch_model(tk):
    s = yf.Ticker(tk)
    info = s.info
    
    sector = info.get('sector', 'Unknown')
    if sector in ['Financial Services', 'Real Estate']:
        return None
        
    # 💡 [보완 1] 연간(balance_sheet) 대신 '최신 분기(quarterly_balance_sheet)' 우선 조회
    bs = s.quarterly_balance_sheet
    if bs.empty:
        bs = s.balance_sheet
        
    inc = s.quarterly_financials
    if inc.empty:
        inc = s.financials
    
    price = info.get('currentPrice') or info.get('previousClose')
    shares = info.get('impliedSharesOutstanding') or info.get('sharesOutstanding')
    market_cap = info.get('marketCap', 0)
    
    if not price or not shares or shares == 0 or bs.empty:
        return None
        
    c_name = info.get('shortName', info.get('longName', tk))
    
    # 💡 [보완 2] 야후 파이낸스의 다양한 계정과목 키값(Key) 모조리 등록
    cash_keys = [
        'Cash Cash Equivalents And Short Term Investments',
        'Cash And Cash Equivalents',
        'Cash',
        'Other Short Term Investments'
    ]
    
    long_debt_keys = [
        'Long Term Debt Non Current',
        'Long Term Debt',
        'Total Long Term Debt',
        'Long Term Provisions',
        'Current Debt And Capital Lease Obligation',
        'Current Debt',
        'Current Portion Of Long Term Debt'
    ]
    
    # 1. 현금 및 장기부채 정밀 추출
    total_cash = get_bs_value(bs, cash_keys)
    adjusted_long_debt = get_bs_value(bs, long_debt_keys)
    
    # 만약 재무상태표 항목이 0으로 나올 경우 info의 totalCash / totalDebt를 2차 안전장치로 활용
    if total_cash == 0:
        total_cash = float(info.get('totalCash') or 0)
        
    net_cash = total_cash - adjusted_long_debt
    net_cash_per_share = net_cash / shares
    net_cash_ratio = (net_cash_per_share / price) * 100
    
    # 2. 순이익 연속 성장(▲) 로직
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

    # 3. PER 3종 세트
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
        '시가총액(M$)': round(market_cap / 1e6, 2) if market_cap else 0.0,
        '총현금(M$)': round(total_cash / 1e6, 2),
        '실질장기부채(M$)': round(adjusted_long_debt / 1e6, 2),
        '순이익성장': growth_str,
        'PER': round(trailing_pe, 2) if trailing_pe and trailing_pe > 0 else 0.0,
        'Fwd_PER': round(forward_pe, 2) if forward_pe and forward_pe > 0 else 0.0,
        '순현금_PER': round(net_cash_per, 2)
    }
