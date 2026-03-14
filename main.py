import os
import tushare as ts
import pandas as pd
from datetime import datetime

# ====================== 1. 初始化配置 ======================
# 设置时区为北京时间
os.environ['TZ'] = 'Asia/Shanghai'
try:
    import time
    time.tzset()
except:
    pass

# 初始化Tushare（从环境变量获取token）
ts_token = os.getenv('TUSHARE_TOKEN', '')
if ts_token:
    ts.set_token(ts_token)
    pro = ts.pro_api()
else:
    print("⚠️ 未配置TUSHARE_TOKEN，选股功能将禁用")
    pro = None

# 获取固定股票列表（从环境变量）
FIXED_STOCK_LIST = os.getenv('FIXED_STOCK_LIST', '').split(',')
FIXED_STOCK_LIST = [code.strip() for code in FIXED_STOCK_LIST if code.strip()]

# ====================== 2. 短线选股函数 ======================
def select_short_term_stocks():
    """选股逻辑：MACD金叉 + 量能放大 + 股价站5日线（短线强势股）"""
    if not pro:
        return []
    
    selected_stocks = []
    try:
        # 获取A股基础列表（过滤ST/北交所/退市股）
        stock_basic = pro.stock_basic(exchange='', list_status='L', fields='ts_code,symbol,name')
        stock_basic = stock_basic[~stock_basic['name'].str.contains('ST|*ST', na=False)]
        stock_basic = stock_basic[~stock_basic['ts_code'].str.endswith('BJ', na=False)]
        
        # 只筛选沪深主板/创业板/科创板（短线活跃）
        valid_prefix = ['00', '30', '60', '68']
        stock_basic = stock_basic[stock_basic['symbol'].str[:2].isin(valid_prefix)]
        
        # 取前200只候选（避免请求超限）
        candidate_codes = stock_basic['ts_code'].tolist()[:200]
        
        # 时间范围（近20天）
        today = datetime.now().strftime('%Y%m%d')
        start_date = (datetime.now() - pd.Timedelta(days=20)).strftime('%Y%m%d')
        
        for ts_code in candidate_codes:
            try:
                # 获取日线数据
                df = pro.daily(ts_code=ts_code, start_date=start_date, end_date=today)
                if len(df) < 10:
                    continue
                
                # 按日期排序（最新在前）
                df = df.sort_values('trade_date', ascending=False)
                
                # 计算MACD
                df['ema12'] = df['close'].ewm(span=12, adjust=False).mean()
                df['ema26'] = df['close'].ewm(span=26, adjust=False).mean()
                df['dif'] = df['ema12'] - df['ema26']
                df['dea'] = df['dif'].ewm(span=9, adjust=False).mean()
                
                # 计算5日均线
                df['ma5'] = df['close'].rolling(window=5).mean()
                
                # 最新数据
                latest = df.iloc[0]
                prev = df.iloc[1]
                
                # 选股条件（缺一不可）
                macd_gold = (latest['dif'] > latest['dea']) and (prev['dif'] < prev['dea'])  # MACD金叉
                vol_increase = latest['vol'] > prev['vol'] * 1.5  # 量能放大1.5倍
                price_above_ma5 = latest['close'] > latest['ma5']  # 站5日线
                valid_pct = -2 < latest['pct_chg'] < 5  # 涨跌幅合理
                
                if all([macd_gold, vol_increase, price_above_ma5, valid_pct]):
                    # 获取股票名称
                    stock_name = stock_basic[stock_basic['ts_code'] == ts_code]['name'].iloc[0]
                    selected_stocks.append({
                        'ts_code': ts_code,
                        'symbol': stock_basic[stock_basic['ts_code'] == ts_code]['symbol'].iloc[0],
                        'name': stock_name,
                        'close': round(latest['close'], 2),
                        'pct_chg': round(latest['pct_chg'], 2),
                        'vol_ratio': round(latest['vol']/prev['vol'], 2)
                    })
            except Exception as e:
                continue
        
        # 只返回前10只（精选短线标的）
        return selected_stocks[:10]
    except Exception as e:
        print(f"❌ 选股失败：{str(e)}")
        return []

# ====================== 3. 股票分析函数（适配你的原有逻辑） ======================
def analyze_stock(stock_code):
    """分析单只股票（基础版，可替换为你的完整分析逻辑）"""
    if not pro:
        return f"⚠️ {stock_code}：未配置Tushare，无法分析\n"
    
    try:
        # 转换代码格式（600519 → 600519.SH，300750 → 300750.SZ）
        ts_code = f"{stock_code}.SH" if stock_code.startswith('60') else f"{stock_code}.SZ"
        
        # 获取近10天数据
        df = pro.daily(ts_code=ts_code, start_date=(datetime.now()-pd.Timedelta(days=10)).strftime('%Y%m%d'))
        if len(df) == 0:
            return f"⚠️ {stock_code}：无交易数据\n"
        
        latest = df.iloc[0]
        stock_name = pro.stock_basic(ts_code=ts_code)['name'].iloc[0]
        
        # 基础分析（可替换为你的原有分析逻辑）
        analysis = f"""
【{stock_name}（{stock_code}）】
最新价：{latest['close']} 元
涨跌幅：{latest['pct_chg']}%
成交量：{latest['vol']} 手
5日均线：{round(df['ma5'].iloc[0], 2)} 元
支撑位：{round(latest['low']*0.99, 2)} 元
压力位：{round(latest['high']*1.01, 2)} 元
短线建议：{'✅ 持仓' if latest['pct_chg'] > 0 else '⚠️ 观望'}
"""
        return analysis
    except Exception as e:
        return f"❌ {stock_code}：分析失败 - {str(e)[:50]}\n"

# ====================== 4. 生成分版面最终报告 ======================
def generate_split_report():
    """生成「固定股票+选股结果」分版面报告"""
    # 报告头部
    report = f"""
=====================================
📅 股票分析报告（{datetime.now().strftime('%Y-%m-%d %H:%M')}）
=====================================

【一、核心持仓分析（固定关注）】
"""
    # 分析固定股票
    if FIXED_STOCK_LIST:
        for code in FIXED_STOCK_LIST:
            report += analyze_stock(code)
    else:
        report += "⚠️ 未配置固定关注股票\n"
    
    # 分隔线 + 选股结果分析
    report += """
-------------------------------------
【二、今日短线标的分析（自动选股）】
"""
    # 选股并分析
    selected_stocks = select_short_term_stocks()
    if selected_stocks:
        for stock in selected_stocks:
            report += f"""
【{stock['name']}（{stock['symbol']}）】
最新价：{stock['close']} 元
涨跌幅：{stock['pct_chg']}%
量能放大：{stock['vol_ratio']} 倍
选股逻辑：MACD金叉 + 量能放大 + 站5日线
短线操作：建议开盘关注，止损位{round(stock['close']*0.98, 2)}元
"""
    else:
        report += "⚠️ 今日无符合条件的短线标的\n"
    
    # 保存报告到文件
    os.makedirs('reports', exist_ok=True)
    report_path = f"reports/股票分析报告_{datetime.now().strftime('%Y%m%d_%H%M')}.txt"
    with open(report_path, 'w', encoding='utf-8') as f:
        f.write(report)
    
    # 打印报告（方便日志查看）
    print(report)
    return report

# ====================== 5. 主函数（程序入口） ======================
if __name__ == '__main__':
    print("🚀 开始执行：选股 + 分版面分析")
    final_report = generate_split_report()
    print("✅ 分版面报告已生成！")
