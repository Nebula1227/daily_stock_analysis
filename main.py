import os
import time
import threading
import pandas as pd
import requests
from datetime import datetime, timedelta
import tushare as ts

# ====================== 【全局配置】 ======================
# 1. Tushare 配置（替换成你的 token）
TUSHARE_TOKEN = "你的Tushare token"
ts.set_token(TUSHARE_TOKEN)
pro = ts.pro_api()

# 2. 微信推送配置
WECHAT_WEBHOOK_URL = os.getenv("WECHAT_WEBHOOK_URL", "")

# 3. 选股参数（和之前一致）
LOW_PRICE_MAX = 20
LOW_RANGE_MAX = 0.15
RISE_5D_MAX = 12
MV_MIN = 30  # 亿
MV_MAX = 150 # 亿
VOL_MIN = 1.6
VOL_MAX = 5
STOP_LOSS = 0.96
TAKE_PROFIT = 1.08
SCAN_INTERVAL = 60
AGGR_SCAN_INTERVAL = 45
AGGR_VOL_RATIO_MIN = 2.5
AGGR_TURNOVER_MAX = 12
AGGR_AMOUNT_MIN = 2 # 亿

# 4. 持仓股列表
HOLD_STOCK_LIST = [c.strip() for c in os.getenv("HOLD_STOCK_LIST", "").split(",") if c.strip()]

# ====================== 工具函数 ======================
def send_wechat(msg):
    if not WECHAT_WEBHOOK_URL:
        return
    try:
        requests.post(WECHAT_WEBHOOK_URL, json={"msgtype": "text", "text": {"content": msg.strip()}})
    except Exception as e:
        print(f"微信推送失败：{e}")

def is_trading_time():
    """判断是否在交易时间"""
    now = datetime.now().time()
    trade_start = datetime.strptime("09:30:00", "%H:%M:%S").time()
    trade_end = datetime.strptime("15:00:00", "%H:%M:%S").time()
    return trade_start <= now <= trade_end

def is_auction_time(morning=True):
    """判断是否在真实竞价时段"""
    now = datetime.now().time()
    if morning:
        # 早盘真实竞价：9:24-9:25（不可撤单）
        start = datetime.strptime("09:24:00", "%H:%M:%S").time()
        end = datetime.strptime("09:25:00", "%H:%M:%S").time()
    else:
        # 尾盘竞价：14:57-15:00
        start = datetime.strptime("14:57:00", "%H:%M:%S").time()
        end = datetime.strptime("15:00:00", "%H:%M:%S").time()
    return start <= now <= end

# ====================== Tushare 数据获取核心函数 ======================
def get_stock_basic():
    """获取股票基础信息（Tushare，1次/天，消耗20积分）"""
    try:
        df = pro.stock_basic(exchange='', list_status='L', fields='ts_code,symbol,name,industry,list_date,market,circ_mv')
        # 转换流通市值为亿元
        df['circ_mv'] = df['circ_mv'].astype(float) / 10000
        return df
    except Exception as e:
        print(f"获取股票基础信息失败：{e}")
        return pd.DataFrame()

def get_auction_data_ts(ts_code):
    """获取竞价数据（Tushare，1次/只，消耗5积分）"""
    try:
        # 获取当日日期
        today = datetime.now().strftime("%Y%m%d")
        # Tushare 竞价接口
        df = pro.auction_detail(ts_code=ts_code, trade_date=today)
        if df.empty:
            return None
        
        # 取最后一笔（9:25）的竞价数据
        last_auction = df.iloc[-1]
        return {
            "ts_code": ts_code,
            "symbol": last_auction['symbol'],
            "name": last_auction['name'],
            "price": last_auction['open'],          # 竞价价格
            "auction_vol": last_auction['vol'],     # 竞价成交量（手）
            "auction_amount": last_auction['amount'], # 竞价成交额（万元）
            "rise": last_auction['pct_chg']         # 竞价涨幅（%）
        }
    except Exception as e:
        print(f"获取{ts_code}竞价数据失败：{e}")
        return None

def get_realtime_data_ts(ts_code):
    """获取实时行情数据（Tushare，1次/只，消耗2积分）"""
    try:
        # 实时行情接口
        df = pro.daily(ts_code=ts_code, start_date=datetime.now().strftime("%Y%m%d"), end_date=datetime.now().strftime("%Y%m%d"))
        if df.empty:
            # 备用：获取分时数据
            df = pro.minute_bar(ts_code=ts_code, start_time=datetime.now().strftime("%H:%M"), end_time=datetime.now().strftime("%H:%M"))
            if df.empty:
                return None
        
        # 获取基础信息
        basic_df = get_stock_basic()
        basic = basic_df[basic_df['ts_code'] == ts_code].iloc[0] if not basic_df.empty else None
        
        return {
            "ts_code": ts_code,
            "symbol": basic['symbol'] if basic else ts_code[:6],
            "name": basic['name'] if basic else "",
            "price": df.iloc[0]['close'],          # 当前价格
            "avg_price": df.iloc[0]['avg_price'],  # 均价
            "vol_ratio": df.iloc[0]['vol_ratio'],  # 量比
            "turnover": df.iloc[0]['turnover'],    # 换手率
            "amount": df.iloc[0]['amount'] / 10000, # 成交额（亿元）
            "circ_mv": basic['circ_mv'] if basic else 0, # 流通市值（亿元）
            "low30": df.iloc[0]['low_30d'],        # 30日低点
            "rise5d": df.iloc[0]['pct_chg_5d']     # 近5日涨幅
        }
    except Exception as e:
        print(f"获取{ts_code}实时数据失败：{e}")
        return None

def get_5min_verify_data_ts(ts_code):
    """获取开盘5分钟验证数据（Tushare，1次/只，消耗3积分）"""
    try:
        today = datetime.now().strftime("%Y%m%d")
        # 获取9:30-9:35的5分钟K线
        df = pro.minute_bar(ts_code=ts_code, start_time="09:30", end_time="09:35", freq='5min')
        if df.empty:
            return None
        
        return {
            "ts_code": ts_code,
            "open_price": df.iloc[0]['open'],      # 开盘价
            "close_price": df.iloc[0]['close'],    # 9:35价格
            "avg_price": df.iloc[0]['avg_price'],  # 均价
            "vol_ratio": df.iloc[0]['vol_ratio'],  # 量比
            "rise": df.iloc[0]['pct_chg']          # 涨幅
        }
    except Exception as e:
        print(f"验证{ts_code}失败：{e}")
        return None

# ====================== 竞价选股逻辑（纯 Tushare 版） ======================
def scan_auction_ts(morning=True):
    """竞价选股（纯 Tushare 数据）"""
    send_wechat(f"🔍 开始{('早盘真实' if morning else '尾盘')}竞价选股（Tushare数据源）")
    
    # 1. 获取股票池（基础信息，1次/天）
    basic_df = get_stock_basic()
    if basic_df.empty:
        send_wechat("❌ 获取股票基础信息失败，竞价选股终止")
        return
    
    # 2. 筛选基础条件（低位+流通市值）
    filter_df = basic_df[
        (basic_df['circ_mv'] >= MV_MIN) & 
        (basic_df['circ_mv'] <= MV_MAX)
    ]
    # 合并自选股
    if HOLD_STOCK_LIST:
        filter_df = filter_df[filter_df['symbol'].isin(HOLD_STOCK_LIST)]
    stock_pool = filter_df['ts_code'].tolist()[:100]  # 限制100只，控制积分消耗
    
    # 3. 扫描竞价数据
    good_stocks = []
    for ts_code in stock_pool:
        auction_data = get_auction_data_ts(ts_code)
        if not auction_data:
            continue
        
        # 竞价核心筛选条件
        try:
            price = auction_data['price']
            auction_vol = auction_data['auction_vol']
            rise = auction_data['rise']
            
            # 基础条件
            cond1 = price <= LOW_PRICE_MAX
            # 竞价量（早盘≥800手，尾盘≥300手）
            cond2 = auction_vol >= (800 if morning else 300)
            # 涨幅（早盘2%-6%，尾盘0%-3%）
            cond3 = (2 <= rise <= 6) if morning else (0 <= rise <= 3)
            
            if all([cond1, cond2, cond3]):
                good_stocks.append(auction_data)
        except Exception as e:
            print(f"筛选{ts_code}失败：{e}")
            continue
    
    # 4. 早盘竞价需要开盘验证
    if morning and good_stocks:
        send_wechat(f"📌 早盘竞价初选{len(good_stocks)}只标的，9:35验证开盘强势")
        verified_stocks = verify_open_5min_ts(good_stocks)
        
        if verified_stocks:
            msg = f"🎉 早盘竞价【最终验证通过】优质标的（Tushare）\n"
            for i, s in enumerate(verified_stocks[:3]):
                msg += f"{i+1}. {s['name']}({s['symbol']})\n"
                msg += f"   竞价价：{s['price']} | 开盘价：{s['open_price']}\n"
                msg += f"   竞价涨幅：{s['rise']}% | 开盘涨幅：{s['rise_open']}%\n"
            send_wechat(msg)
        else:
            send_wechat(f"😶 早盘竞价标的开盘后未延续强势")
    elif good_stocks:
        # 尾盘竞价直接推送
        msg = f"🎉 尾盘竞价优质标的（Tushare）\n"
        for i, s in enumerate(good_stocks[:3]):
            msg += f"{i+1}. {s['name']}({s['symbol']})\n"
            msg += f"   竞价价：{s['price']} | 竞价量：{s['auction_vol']}手 | 涨幅：{s['rise']}%\n"
        send_wechat(msg)
    else:
        send_wechat(f"😶 {('早盘' if morning else '尾盘')}竞价暂无优质标的")

# ====================== 开盘5分钟验证（纯 Tushare 版） ======================
def verify_open_5min_ts(auction_stocks):
    """开盘5分钟验证（Tushare）"""
    send_wechat("⏳ 开始验证竞价标的开盘强势度（Tushare）")
    verified_stocks = []
    
    # 等待到9:35
    while datetime.now().time() < datetime.strptime("09:35:00", "%H:%M:%S").time():
        time.sleep(10)
    
    # 验证每只标的
    for stock in auction_stocks:
        verify_data = get_5min_verify_data_ts(stock['ts_code'])
        if not verify_data:
            continue
        
        # 验证条件
        auction_price = stock['price']
        open_price = verify_data['open_price']
        current_price = verify_data['close_price']
        avg_price = verify_data['avg_price']
        vol_ratio = verify_data['vol_ratio']
        rise_auction = stock['rise']
        rise_open = verify_data['rise']
        
        # 核心验证条件
        cond1 = open_price >= auction_price * 0.995  # 开盘不低开
        cond2 = current_price >= avg_price * 0.995    # 股价在均价线上方
        cond3 = vol_ratio >= 1.2                     # 量比≥1.2
        cond4 = rise_open >= rise_auction * 0.7       # 涨幅保留70%以上
        
        if all([cond1, cond2, cond3, cond4]):
            verified_stocks.append({
                "name": stock['name'],
                "symbol": stock['symbol'],
                "price": auction_price,
                "open_price": open_price,
                "rise_auction": rise_auction,
                "rise_open": rise_open,
                "vol_ratio": vol_ratio
            })
    
    return verified_stocks

# ====================== 盘中稳健版选股（纯 Tushare 版） ======================
def scan_robin_ts():
    """稳健版选股（Tushare）"""
    basic_df = get_stock_basic()
    if basic_df.empty:
        return []
    
    # 筛选基础池
    filter_df = basic_df[
        (basic_df['circ_mv'] >= MV_MIN) & 
        (basic_df['circ_mv'] <= MV_MAX)
    ]
    stock_pool = filter_df['ts_code'].tolist()[:200]  # 限制200只
    
    good_stocks = []
    for ts_code in stock_pool:
        real_data = get_realtime_data_ts(ts_code)
        if not real_data:
            continue
        
        # 稳健版筛选条件
        try:
            price = real_data['price']
            vol_ratio = real_data['vol_ratio']
            avg_price = real_data['avg_price']
            amount = real_data['amount']
            low30 = real_data['low30']
            rise5d = real_data['rise5d']
            
            cond1 = price <= LOW_PRICE_MAX
            cond2 = (price - low30) / low30 <= LOW_RANGE_MAX
            cond3 = rise5d <= RISE_5D_MAX
            cond4 = VOL_MIN <= vol_ratio <= VOL_MAX
            cond5 = price >= avg_price * 0.995
            cond6 = amount >= 1.5  # 成交额≥1.5亿
            
            if all([cond1, cond2, cond3, cond4, cond5, cond6]):
                real_data['stop'] = round(price * STOP_LOSS, 2)
                real_data['take'] = round(price * TAKE_PROFIT, 2)
                good_stocks.append(real_data)
        except Exception as e:
            print(f"稳健版筛选{ts_code}失败：{e}")
            continue
    
    return good_stocks

# ====================== 盘中激进版选股（纯 Tushare 版） ======================
def scan_aggr_ts():
    """激进版选股（Tushare）"""
    basic_df = get_stock_basic()
    if basic_df.empty:
        return []
    
    filter_df = basic_df[
        (basic_df['circ_mv'] >= MV_MIN) & 
        (basic_df['circ_mv'] <= MV_MAX)
    ]
    stock_pool = filter_df['ts_code'].tolist()[:100]  # 限制100只
    
    good_stocks = []
    for ts_code in stock_pool:
        real_data = get_realtime_data_ts(ts_code)
        if not real_data:
            continue
        
        # 激进版筛选条件
        try:
            price = real_data['price']
            vol_ratio = real_data['vol_ratio']
            avg_price = real_data['avg_price']
            turnover = real_data['turnover']
            amount = real_data['amount']
            
            cond1 = vol_ratio >= AGGR_VOL_RATIO_MIN
            cond2 = price >= avg_price * 1.005
            cond3 = turnover <= AGGR_TURNOVER_MAX
            cond4 = amount >= AGGR_AMOUNT_MIN
            cond5 = price <= LOW_PRICE_MAX
            
            if all([cond1, cond2, cond3, cond4, cond5]):
                real_data['stop'] = round(price * 0.95, 2)  # 激进版止损5%
                real_data['target'] = round(price * 1.1, 2) # 目标涨停
                good_stocks.append(real_data)
        except Exception as e:
            print(f"激进版筛选{ts_code}失败：{e}")
            continue
    
    return good_stocks

# ====================== 持仓分析（纯 Tushare 版） ======================
def hold_analysis_ts():
    """持仓分析（Tushare）"""
    msg = "📈 【持仓操作建议】（Tushare）\n"
    if not HOLD_STOCK_LIST:
        return "暂无持仓股"
    
    for symbol in HOLD_STOCK_LIST:
        # 转换为 ts_code（6位代码+交易所）
        ts_code = f"{symbol}.SH" if symbol.startswith(('6', '9')) else f"{symbol}.SZ"
        real_data = get_realtime_data_ts(ts_code)
        
        if not real_data:
            msg += f"{symbol}：获取数据失败\n"
            continue
        
        price = real_data['price']
        rise = (price / (price/1.01) - 1) * 100
        name = real_data['name']
        
        if rise > 3:
            sug = "持有，不破5日线不卖"
        elif 0 < rise <= 3:
            sug = "持有，可小加"
        elif -3 < rise <= 0:
            sug = "观望，适合做T"
        else:
            sug = f"减仓，止损:{round(price * 0.97, 2)}"
        
        msg += f"{name}({symbol}) 涨幅:{rise:.1f}% → {sug}\n"
    
    return msg

# ====================== 监控线程 ======================
def monitor_auction():
    """竞价监控线程"""
    print("启动竞价监控（Tushare）")
    while True:
        if is_auction_time(morning=True):
            scan_auction_ts(morning=True)
            time.sleep(60)
        elif is_auction_time(morning=False):
            scan_auction_ts(morning=False)
            time.sleep(30)
        else:
            time.sleep(60)

def monitor_robin():
    """稳健版监控线程"""
    print("启动稳健版监控（Tushare）")
    last_push_hour = -1
    while True:
        if not is_trading_time():
            time.sleep(60)
            continue
        
        # 扫描稳健版
        robin_stocks = scan_robin_ts()
        if robin_stocks:
            msg = "🚀 稳健版信号（Tushare）\n"
            for i, s in enumerate(robin_stocks[:3]):
                msg += f"{i+1}. {s['name']}({s['symbol']})\n"
                msg += f"   现价：{s['price']} | 止损：{s['stop']} | 止盈：{s['take']}\n"
            send_wechat(msg)
        
        # 定时推送分析
        current_hour = datetime.now().hour
        current_min = datetime.now().minute
        if current_hour == 9 and current_min == 35:
            send_wechat(f"🌅 【早盘分析】（Tushare）\n{hold_analysis_ts()}")
            last_push_hour = current_hour
        elif current_hour == 11 and current_min == 30:
            send_wechat(f"🍱 【午盘小结】（Tushare）\n{hold_analysis_ts()}")
            last_push_hour = current_hour
        elif current_hour == 14 and current_min == 30:
            send_wechat(f"🔥 【尾盘分析】（Tushare）\n{hold_analysis_ts()}")
            last_push_hour = current_hour
        elif current_hour == 15 and current_min == 5:
            send_wechat(f"📊 【收盘总结】（Tushare）\n{hold_analysis_ts()}")
            last_push_hour = current_hour
        
        time.sleep(SCAN_INTERVAL)

def monitor_aggr():
    """激进版监控线程"""
    print("启动激进版监控（Tushare）")
    while True:
        if not is_trading_time():
            time.sleep(60)
            continue
        
        # 扫描激进版
        aggr_stocks = scan_aggr_ts()
        if aggr_stocks:
            msg = "⚡️ 激进版信号（Tushare）\n"
            for i, s in enumerate(aggr_stocks[:3]):
                msg += f"{i+1}. {s['name']}({s['symbol']})\n"
                msg += f"   现价：{s['price']} | 止损：{s['stop']} | 目标：{s['target']}\n"
            send_wechat(msg)
        
        time.sleep(AGGR_SCAN_INTERVAL)

# ====================== 主函数 ======================
if __name__ == "__main__":
    # 启动通知
    send_wechat("✅ 终极版监控系统（纯Tushare）已启动\n包含：真实竞价+开盘验证+稳健+激进+持仓分析")
    
    # 启动3个线程
    threading.Thread(target=monitor_auction, daemon=True).start()
    threading.Thread(target=monitor_robin, daemon=True).start()
    threading.Thread(target=monitor_aggr, daemon=True).start()
    
    # 主线程保持运行
    while True:
        time.sleep(3600)
