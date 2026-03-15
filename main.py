import os
import time
import threading
import pandas as pd
import requests
from datetime import datetime, timedelta
import tushare as ts

# ====================== 【全局配置】 ======================
# 1. 第三方 Tushare 核心配置（必须按卖家给的填！）
TUSHARE_TOKEN = os.getenv("TUSHARE_TOKEN", "你的第三方Tushare Token")  # 替换为真实Token
pro = ts.pro_api(TUSHARE_TOKEN)
# 关键：第三方代理版必须的配置（不要改URL！）
pro._DataApi__token = TUSHARE_TOKEN
pro._DataApi__http_url = 'http://lianghua.nanyangqiankun.top'

# 2. 微信推送配置
WECHAT_WEBHOOK_URL = os.getenv("WECHAT_WEBHOOK_URL", "你的微信WebHook地址")

# 3. 选股参数（积分节流优化版）
LOW_PRICE_MAX = 20
LOW_RANGE_MAX = 0.15
RISE_5D_MAX = 12
MV_MIN = 30    # 流通市值下限（亿）
MV_MAX = 150   # 流通市值上限（亿）
VOL_MIN = 1.6
VOL_MAX = 5
STOP_LOSS = 0.96
TAKE_PROFIT = 1.08
SCAN_INTERVAL = 60       # 稳健版扫描间隔（秒）
AGGR_SCAN_INTERVAL = 45  # 激进版扫描间隔（秒）
AGGR_VOL_RATIO_MIN = 2.5
AGGR_TURNOVER_MAX = 12
AGGR_AMOUNT_MIN = 2      # 激进版成交额下限（亿）

# 4. 持仓股列表（逗号分隔，如：000001,600036,300750）
HOLD_STOCK_LIST = [c.strip() for c in os.getenv("HOLD_STOCK_LIST", "").split(",") if c.strip()]

# 5. 积分节流配置（核心！控制每日消耗≤1500积分）
AUCTION_POOL_SIZE = 50   # 竞价池大小（原100→50）
ROBIN_POOL_SIZE = 100    # 稳健池大小（原200→100）
AGGR_POOL_SIZE = 50      # 激进池大小（原100→50）

# ====================== 工具函数 ======================
def send_wechat(msg):
    """微信推送（失败自动重试）"""
    if not WECHAT_WEBHOOK_URL:
        return
    for _ in range(2):  # 重试2次
        try:
            requests.post(WECHAT_WEBHOOK_URL, json={"msgtype": "text", "text": {"content": msg.strip()}}, timeout=10)
            return
        except Exception as e:
            print(f"微信推送失败：{e}")
            time.sleep(2)

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
        start = datetime.strptime("09:24:00", "%H:%M:%S").time()
        end = datetime.strptime("09:25:00", "%H:%M:%S").time()
    else:
        start = datetime.strptime("14:57:00", "%H:%M:%S").time()
        end = datetime.strptime("15:00:00", "%H:%M:%S").time()
    return start <= now <= end

# ====================== Tushare 数据获取（适配第三方） ======================
def get_stock_basic():
    """获取股票基础信息（1次/天，消耗20积分）"""
    try:
        df = pro.stock_basic(exchange='', list_status='L', fields='ts_code,symbol,name,circ_mv')
        df['circ_mv'] = df['circ_mv'].astype(float) / 10000  # 转换为亿元
        return df[df['circ_mv'].notna()]
    except Exception as e:
        print(f"获取股票基础信息失败：{e}")
        send_wechat(f"❌ 获取股票基础信息失败：{str(e)[:50]}")
        return pd.DataFrame()

def get_auction_data_ts(ts_code):
    """获取竞价数据（1次/只，消耗5积分）"""
    try:
        today = datetime.now().strftime("%Y%m%d")
        df = pro.auction_detail(ts_code=ts_code, trade_date=today)
        if df.empty:
            return None
        last = df.iloc[-1]
        return {
            "ts_code": ts_code,
            "symbol": last['symbol'],
            "name": last['name'],
            "price": last['open'],
            "auction_vol": last['vol'],
            "auction_amount": last['amount'],
            "rise": last['pct_chg']
        }
    except Exception as e:
        print(f"获取{ts_code}竞价数据失败：{e}")
        return None

def get_realtime_data_ts(ts_code):
    """获取实时行情（1次/只，消耗2积分）"""
    try:
        today = datetime.now().strftime("%Y%m%d")
        df = pro.daily(ts_code=ts_code, trade_date=today)
        if df.empty:
            df = pro.minute_bar(ts_code=ts_code, start_time="09:30", end_time=datetime.now().strftime("%H:%M"))
        if df.empty:
            return None
        
        basic = get_stock_basic()
        basic_info = basic[basic['ts_code'] == ts_code].iloc[0] if not basic.empty else None
        
        return {
            "ts_code": ts_code,
            "symbol": basic_info['symbol'] if basic_info else ts_code[:6],
            "name": basic_info['name'] if basic_info else "",
            "price": df.iloc[0]['close'],
            "avg_price": df.iloc[0].get('avg_price', df.iloc[0]['close']),
            "vol_ratio": df.iloc[0].get('vol_ratio', 1.0),
            "turnover": df.iloc[0].get('turnover', 0.0),
            "amount": df.iloc[0]['amount'] / 10000,
            "circ_mv": basic_info['circ_mv'] if basic_info else 0,
            "low30": df.iloc[0].get('low_30d', df.iloc[0]['low']),
            "rise5d": df.iloc[0].get('pct_chg_5d', 0.0)
        }
    except Exception as e:
        print(f"获取{ts_code}实时数据失败：{e}")
        return None

def get_5min_verify_data_ts(ts_code):
    """开盘5分钟验证（1次/只，消耗3积分）"""
    try:
        df = pro.minute_bar(ts_code=ts_code, start_time="09:30", end_time="09:35", freq='5min')
        if df.empty:
            return None
        return {
            "ts_code": ts_code,
            "open_price": df.iloc[0]['open'],
            "close_price": df.iloc[0]['close'],
            "avg_price": df.iloc[0]['avg_price'],
            "vol_ratio": df.iloc[0]['vol_ratio'],
            "rise": df.iloc[0]['pct_chg']
        }
    except Exception as e:
        print(f"验证{ts_code}失败：{e}")
        return None

# ====================== 竞价选股（积分节流版） ======================
def scan_auction_ts(morning=True):
    """竞价选股（仅50只，每日消耗≤250积分）"""
    send_wechat(f"🔍 开始{('早盘' if morning else '尾盘')}竞价选股（第三方Tushare）")
    basic = get_stock_basic()
    if basic.empty:
        return
    
    # 筛选基础池（30-150亿流通市值）
    pool = basic[
        (basic['circ_mv'] >= MV_MIN) & 
        (basic['circ_mv'] <= MV_MAX)
    ]['ts_code'].tolist()[:AUCTION_POOL_SIZE]
    
    # 扫描竞价数据
    good = []
    for ts_code in pool:
        data = get_auction_data_ts(ts_code)
        if not data:
            continue
        try:
            price = data['price']
            vol = data['auction_vol']
            rise = data['rise']
            # 核心条件
            if (price <= LOW_PRICE_MAX and 
                vol >= (800 if morning else 300) and 
                (2 <= rise <= 6 if morning else 0 <= rise <= 3)):
                good.append(data)
        except Exception as e:
            continue
    
    # 早盘验证，尾盘直接推送
    if morning and good:
        send_wechat(f"📌 早盘竞价初选{len(good)}只标的，9:35验证后推送")
        verified = verify_open_5min_ts(good)
        if verified:
            msg = "🎉 早盘竞价【最终验证通过】\n"
            for i, s in enumerate(verified[:3]):
                msg += f"{i+1}. {s['name']}({s['symbol']})\n"
                msg += f"   竞价价：{s['price']} | 开盘价：{s['open_price']}\n"
            send_wechat(msg)
        else:
            send_wechat("😶 早盘竞价标的开盘后未延续强势")
    elif good:
        msg = "🎉 尾盘竞价优质标的\n"
        for i, s in enumerate(good[:3]):
            msg += f"{i+1}. {s['name']}({s['symbol']}) 现价：{s['price']} 涨幅：{s['rise']}%\n"
        send_wechat(msg)
    else:
        send_wechat(f"😶 {('早盘' if morning else '尾盘')}竞价暂无优质标的")

def verify_open_5min_ts(auction_stocks):
    """开盘5分钟验证（积分节流版）"""
    send_wechat("⏳ 验证竞价标的开盘强势度...")
    verified = []
    # 等待到9:35
    while datetime.now().time() < datetime.strptime("09:35:00", "%H:%M:%S").time():
        time.sleep(10)
    
    for stock in auction_stocks[:AUCTION_POOL_SIZE]:  # 限制验证数量
        data = get_5min_verify_data_ts(stock['ts_code'])
        if not data:
            continue
        # 核心验证条件
        cond1 = data['open_price'] >= stock['price'] * 0.995
        cond2 = data['close_price'] >= data['avg_price'] * 0.995
        cond3 = data['vol_ratio'] >= 1.2
        cond4 = data['rise'] >= stock['rise'] * 0.7
        if all([cond1, cond2, cond3, cond4]):
            verified.append({
                "name": stock['name'],
                "symbol": stock['symbol'],
                "price": stock['price'],
                "open_price": data['open_price'],
                "rise_auction": stock['rise'],
                "rise_open": data['rise'],
                "vol_ratio": data['vol_ratio']
            })
    return verified

# ====================== 盘中选股（积分节流版） ======================
def scan_robin_ts():
    """稳健版选股（100只，每日消耗≤1200积分）"""
    basic = get_stock_basic()
    if basic.empty:
        return []
    pool = basic[
        (basic['circ_mv'] >= MV_MIN) & 
        (basic['circ_mv'] <= MV_MAX)
    ]['ts_code'].tolist()[:ROBIN_POOL_SIZE]
    
    good = []
    for ts_code in pool:
        data = get_realtime_data_ts(ts_code)
        if not data:
            continue
        try:
            price = data['price']
            vol_ratio = data['vol_ratio']
            avg_price = data['avg_price']
            amount = data['amount']
            low30 = data['low30']
            rise5d = data['rise5d']
            
            # 筛选条件
            if (price <= LOW_PRICE_MAX and 
                (price - low30)/low30 <= LOW_RANGE_MAX and 
                rise5d <= RISE_5D_MAX and 
                VOL_MIN <= vol_ratio <= VOL_MAX and 
                price >= avg_price * 0.995 and 
                amount >= 1.5):
                data['stop'] = round(price * STOP_LOSS, 2)
                data['take'] = round(price * TAKE_PROFIT, 2)
                good.append(data)
        except Exception as e:
            continue
    return good

def scan_aggr_ts():
    """激进版选股（50只，每日消耗≤600积分）"""
    basic = get_stock_basic()
    if basic.empty:
        return []
    pool = basic[
        (basic['circ_mv'] >= MV_MIN) & 
        (basic['circ_mv'] <= MV_MAX)
    ]['ts_code'].tolist()[:AGGR_POOL_SIZE]
    
    good = []
    for ts_code in pool:
        data = get_realtime_data_ts(ts_code)
        if not data:
            continue
        try:
            price = data['price']
            vol_ratio = data['vol_ratio']
            avg_price = data['avg_price']
            turnover = data['turnover']
            amount = data['amount']
            
            # 筛选条件
            if (vol_ratio >= AGGR_VOL_RATIO_MIN and 
                price >= avg_price * 1.005 and 
                turnover <= AGGR_TURNOVER_MAX and 
                amount >= AGGR_AMOUNT_MIN and 
                price <= LOW_PRICE_MAX):
                data['stop'] = round(price * 0.95, 2)
                data['target'] = round(price * 1.1, 2)
                good.append(data)
        except Exception as e:
            continue
    return good

# ====================== 持仓分析 ======================
def hold_analysis_ts():
    """持仓分析（低消耗版）"""
    msg = "📈 【持仓操作建议】\n"
    if not HOLD_STOCK_LIST:
        return "暂无持仓股"
    
    for symbol in HOLD_STOCK_LIST:
        ts_code = f"{symbol}.SH" if symbol.startswith(('6', '9')) else f"{symbol}.SZ"
        data = get_realtime_data_ts(ts_code)
        if not data:
            msg += f"{symbol}：获取数据失败\n"
            continue
        
        price = data['price']
        rise = (price / (price/1.01) - 1) * 100
        name = data['name']
        
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
    print("启动竞价监控（第三方Tushare）")
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
    print("启动稳健版监控（第三方Tushare）")
    last_push = 0
    while True:
        if not is_trading_time():
            time.sleep(60)
            continue
        
        # 扫描并推送
        stocks = scan_robin_ts()
        if stocks and (time.time() - last_push) > 300:  # 5分钟推一次，减少积分消耗
            msg = "🚀 稳健版信号\n"
            for i, s in enumerate(stocks[:3]):
                msg += f"{i+1}. {s['name']}({s['symbol']}) 现价：{s['price']} 止损：{s['stop']} 止盈：{s['take']}\n"
            send_wechat(msg)
            last_push = time.time()
        
        # 定时推送分析
        now = datetime.now()
        if now.hour == 9 and now.minute == 35:
            send_wechat(f"🌅 【早盘分析】\n{hold_analysis_ts()}")
        elif now.hour == 11 and now.minute == 30:
            send_wechat(f"🍱 【午盘小结】\n{hold_analysis_ts()}")
        elif now.hour == 14 and now.minute == 30:
            send_wechat(f"🔥 【尾盘分析】\n{hold_analysis_ts()}")
        elif now.hour == 15 and now.minute == 5:
            send_wechat(f"📊 【收盘总结】\n{hold_analysis_ts()}")
        
        time.sleep(SCAN_INTERVAL)

def monitor_aggr():
    """激进版监控线程"""
    print("启动激进版监控（第三方Tushare）")
    last_push = 0
    while True:
        if not is_trading_time():
            time.sleep(60)
            continue
        
        stocks = scan_aggr_ts()
        if stocks and (time.time() - last_push) > 240:  # 4分钟推一次
            msg = "⚡️ 激进版信号\n"
            for i, s in enumerate(stocks[:3]):
                msg += f"{i+1}. {s['name']}({s['symbol']}) 现价：{s['price']} 止损：{s['stop']} 目标：{s['target']}\n"
            send_wechat(msg)
            last_push = time.time()
        
        time.sleep(AGGR_SCAN_INTERVAL)

# ====================== 主函数 ======================
if __name__ == "__main__":
    # 启动通知
    send_wechat("✅ 巴菲赖您好，您的助手已成功启动")
    
    # 启动线程
    threading.Thread(target=monitor_auction, daemon=True).start()
    threading.Thread(target=monitor_robin, daemon=True).start()
    threading.Thread(target=monitor_aggr, daemon=True).start()
    
    # 主线程保持运行
    while True:
        time.sleep(3600)
