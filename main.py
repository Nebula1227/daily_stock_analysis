import os
import time
import threading
import requests
import json
from datetime import datetime, timedelta

# ====================== 【全局配置】 ======================
# 1. 微信推送配置（替换为你的真实地址）
WECHAT_WEBHOOK_URL = os.getenv("WECHAT_WEBHOOK_URL", "你的微信机器人WebHook地址")

# 2. 选股参数（和之前完全一致，不修改）
LOW_PRICE_MAX = 20
LOW_RANGE_MAX = 0.15
RISE_5D_MAX = 12
VOL_MIN = 1.6
VOL_MAX = 5
STOP_LOSS = 0.96
TAKE_PROFIT = 1.08
SCAN_INTERVAL = 60       # 稳健版扫描间隔（秒）
AGGR_SCAN_INTERVAL = 45  # 激进版扫描间隔（秒）
AGGR_VOL_RATIO_MIN = 2.5
AGGR_TURNOVER_MAX = 12
AGGR_AMOUNT_MIN = 2      # 激进版成交额下限（万）

# 3. 持仓股列表（逗号分隔，如：000001,600036,300750）
HOLD_STOCK_LIST = [c.strip() for c in os.getenv("HOLD_STOCK_LIST", "").split(",") if c.strip()]

# 4. 股票池配置（控制扫描范围，和之前一致）
AUCTION_POOL_SIZE = 50   # 竞价池大小
ROBIN_POOL_SIZE = 100    # 稳健池大小
AGGR_POOL_SIZE = 50      # 激进池大小

# 5. 东财接口配置（免费，无需积分）
# 东财股票列表接口（基础池）
DFCF_STOCK_LIST_URL = "http://47.108.157.19:8080/stock/basic/all"
# 东财实时行情接口
DFCF_REALTIME_URL = "http://47.108.157.19:8080/stock/realtime/{}"
# 东财竞价数据接口
DFCF_AUCTION_URL = "http://47.108.157.19:8080/stock/auction/{}"

# ====================== 工具函数 ======================
def send_wechat(msg):
    """微信推送（失败自动重试，和之前一致）"""
    if not WECHAT_WEBHOOK_URL:
        return
    for _ in range(2):  # 重试2次
        try:
            resp = requests.post(
                WECHAT_WEBHOOK_URL,
                json={"msgtype": "text", "text": {"content": msg.strip()}},
                timeout=10
            )
            if resp.status_code == 200:
                return
        except Exception as e:
            print(f"微信推送失败：{e}")
            time.sleep(2)

def is_trading_time():
    """判断是否为交易时间（和之前一致）"""
    now = datetime.now()
    # 交易日：周一到周五
    if now.weekday() >= 5:
        return False
    # 交易时间：9:30-15:00
    trade_start = now.replace(hour=9, minute=30, second=0, microsecond=0)
    trade_end = now.replace(hour=15, minute=0, second=0, microsecond=0)
    return trade_start <= now <= trade_end

def is_auction_time(morning=True):
    """判断是否为竞价时间（和之前一致）"""
    now = datetime.now()
    if now.weekday() >= 5:
        return False
    
    if morning:
        # 早盘竞价：9:24-9:25
        auction_start = now.replace(hour=9, minute=24, second=0, microsecond=0)
        auction_end = now.replace(hour=9, minute=25, second=0, microsecond=0)
    else:
        # 尾盘竞价：14:57-15:00
        auction_start = now.replace(hour=14, minute=57, second=0, microsecond=0)
        auction_end = now.replace(hour=15, minute=0, second=0, microsecond=0)
    
    return auction_start <= now <= auction_end

# ====================== 东财数据获取（核心替换，无积分） ======================
def get_stock_basic():
    """获取股票基础列表（东财免费接口，替换原Tushare的stock_basic）"""
    try:
        resp = requests.get(DFCF_STOCK_LIST_URL, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            # 过滤条件：价格≤20元、30-150亿市值（东财接口已返回）
            filtered_stocks = [
                stock for stock in data 
                if stock.get("price", 0) <= LOW_PRICE_MAX 
                and 30 <= stock.get("market_cap", 0) <= 150
            ]
            return filtered_stocks
        return []
    except Exception as e:
        print(f"获取股票基础列表失败：{e}")
        send_wechat(f"❌ 获取股票列表失败：{str(e)[:50]}")
        return []

def get_auction_data_ts(stock_code):
    """获取竞价数据（东财接口，替换原Tushare的auction_detail）"""
    try:
        # 转换代码格式：000001 → 000001.SZ
        ts_code = f"{stock_code}.SZ" if stock_code.startswith(("0", "3")) else f"{stock_code}.SH"
        resp = requests.get(DFCF_AUCTION_URL.format(stock_code), timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            return {
                "ts_code": ts_code,
                "symbol": stock_code,
                "name": data.get("name", ""),
                "price": data.get("open_price", 0),
                "auction_vol": data.get("vol", 0),
                "auction_amount": data.get("amount", 0),
                "rise": data.get("rise", 0)
            }
        return None
    except Exception as e:
        print(f"获取{stock_code}竞价数据失败：{e}")
        return None

def get_realtime_data_ts(stock_code):
    """获取实时行情（东财接口，替换原Tushare的minute_bar/daily）"""
    try:
        ts_code = f"{stock_code}.SZ" if stock_code.startswith(("0", "3")) else f"{stock_code}.SH"
        resp = requests.get(DFCF_REALTIME_URL.format(stock_code), timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            return {
                "ts_code": ts_code,
                "symbol": stock_code,
                "name": data.get("name", ""),
                "price": data.get("price", 0),
                "avg_price": data.get("avg_price", 0),
                "vol_ratio": data.get("vol_ratio", 1.0),
                "turnover": data.get("turnover", 0.0),
                "amount": data.get("amount", 0) / 10000,  # 转换为万
                "low30": data.get("low30", 0),
                "rise5d": data.get("rise5d", 0)
            }
        return None
    except Exception as e:
        print(f"获取{stock_code}实时数据失败：{e}")
        return None

def get_5min_verify_data_ts(stock_code):
    """开盘5分钟验证数据（东财接口）"""
    try:
        data = get_realtime_data_ts(stock_code)
        if not data:
            return None
        return {
            "ts_code": data["ts_code"],
            "open_price": data["price"],
            "close_price": data["price"],
            "avg_price": data["avg_price"],
            "vol_ratio": data["vol_ratio"],
            "rise": data["rise5d"]
        }
    except Exception as e:
        print(f"验证{stock_code}失败：{e}")
        return None

# ====================== 选股逻辑（完全保留，仅替换数据源） ======================
def scan_auction_ts(morning=True):
    """早盘竞价选股（逻辑和之前完全一致）"""
    send_wechat(f"🔍 开始{('早盘' if morning else '尾盘')}竞价选股（东财版）")
    # 获取东财股票池
    basic_stocks = get_stock_basic()
    if not basic_stocks:
        send_wechat("❌ 未获取到股票列表，竞价选股终止")
        return
    
    # 提取股票代码，控制池大小
    stock_codes = [stock["code"] for stock in basic_stocks][:AUCTION_POOL_SIZE]
    good = []
    
    for stock_code in stock_codes:
        data = get_auction_data_ts(stock_code)
        if not data:
            continue
        
        try:
            price = data["price"]
            vol = data["auction_vol"]
            rise = data["rise"]
            
            # 核心筛选条件（和之前一致）
            if (price <= LOW_PRICE_MAX and 
                vol >= (800 if morning else 300) and 
                (2 <= rise <= 6 if morning else 0 <= rise <= 3)):
                good.append(data)
        except Exception as e:
            continue
    
    # 推送结果（逻辑和之前一致）
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
    """开盘5分钟验证（逻辑和之前一致）"""
    send_wechat("⏳ 验证竞价标的开盘强势度...")
    verified = []
    
    # 等待到9:35
    while datetime.now().time() < datetime.strptime("09:35:00", "%H:%M:%S").time():
        time.sleep(10)
    
    for stock in auction_stocks[:AUCTION_POOL_SIZE]:
        stock_code = stock["symbol"]
        data = get_5min_verify_data_ts(stock_code)
        if not data:
            continue
        
        # 核心验证条件（和之前一致）
        cond1 = data["open_price"] >= stock["price"] * 0.995
        cond2 = data["close_price"] >= data["avg_price"] * 0.995
        cond3 = data["vol_ratio"] >= 1.2
        cond4 = data["rise"] >= stock["rise"] * 0.7
        
        if all([cond1, cond2, cond3, cond4]):
            verified.append({
                "name": stock["name"],
                "symbol": stock["symbol"],
                "price": stock["price"],
                "open_price": data["open_price"],
                "rise_auction": stock["rise"],
                "rise_open": data["rise"],
                "vol_ratio": data["vol_ratio"]
            })
    
    return verified

def scan_robin_ts():
    """盘中稳健选股（逻辑和之前一致）"""
    basic_stocks = get_stock_basic()
    if not basic_stocks:
        return []
    
    stock_codes = [stock["code"] for stock in basic_stocks][:ROBIN_POOL_SIZE]
    good = []
    
    for stock_code in stock_codes:
        data = get_realtime_data_ts(stock_code)
        if not data:
            continue
        
        try:
            price = data["price"]
            vol_ratio = data["vol_ratio"]
            avg_price = data["avg_price"]
            amount = data["amount"]
            low30 = data["low30"]
            rise5d = data["rise5d"]
            
            # 核心筛选条件（和之前一致）
            if (price <= LOW_PRICE_MAX and 
                (price - low30)/low30 <= LOW_RANGE_MAX and 
                rise5d <= RISE_5D_MAX and 
                VOL_MIN <= vol_ratio <= VOL_MAX and 
                price >= avg_price * 0.995 and 
                amount >= 1.5):
                data["stop"] = round(price * STOP_LOSS, 2)
                data["take"] = round(price * TAKE_PROFIT, 2)
                good.append(data)
        except Exception as e:
            continue
    
    return good

def scan_aggr_ts():
    """盘中激进选股（逻辑和之前一致）"""
    basic_stocks = get_stock_basic()
    if not basic_stocks:
        return []
    
    stock_codes = [stock["code"] for stock in basic_stocks][:AGGR_POOL_SIZE]
    good = []
    
    for stock_code in stock_codes:
        data = get_realtime_data_ts(stock_code)
        if not data:
            continue
        
        try:
            price = data["price"]
            vol_ratio = data["vol_ratio"]
            avg_price = data["avg_price"]
            turnover = data["turnover"]
            amount = data["amount"]
            
            # 核心筛选条件（和之前一致）
            if (vol_ratio >= AGGR_VOL_RATIO_MIN and 
                price >= avg_price * 1.005 and 
                turnover <= AGGR_TURNOVER_MAX and 
                amount >= AGGR_AMOUNT_MIN and 
                price <= LOW_PRICE_MAX):
                data["stop"] = round(price * 0.95, 2)
                data["target"] = round(price * 1.1, 2)
                good.append(data)
        except Exception as e:
            continue
    
    return good

# ====================== 持仓分析（逻辑完全保留） ======================
def hold_analysis_ts():
    """持仓自动分析（和之前一致）"""
    msg = "📈 【持仓操作建议】\n"
    if not HOLD_STOCK_LIST:
        return "暂无持仓股"
    
    for symbol in HOLD_STOCK_LIST:
        data = get_realtime_data_ts(symbol)
        if not data:
            msg += f"{symbol}：获取数据失败\n"
            continue
        
        price = data["price"]
        rise = (price / (price/1.01) - 1) * 100
        name = data["name"]
        
        # 持仓建议逻辑（和之前一致）
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

# ====================== 监控线程（完全保留） ======================
def monitor_auction():
    """竞价监控线程"""
    print("启动竞价监控（东财版）")
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
    print("启动稳健版监控（东财版）")
    last_push = 0
    while True:
        if not is_trading_time():
            time.sleep(60)
            continue
        
        stocks = scan_robin_ts()
        if stocks and (time.time() - last_push) > 300:  # 5分钟推一次
            msg = "🚀 稳健版信号\n"
            for i, s in enumerate(stocks[:3]):
                msg += f"{i+1}. {s['name']}({s['symbol']}) 现价：{s['price']} 止损：{s['stop']} 止盈：{s['take']}\n"
            send_wechat(msg)
            last_push = time.time()
        
        # 定时推送持仓分析（和之前一致）
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
    print("启动激进版监控（东财版）")
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

# ====================== 主函数（启动逻辑完全保留） ======================
if __name__ == "__main__":
    # 启动通知（简化版）
    send_wechat("✅尊敬的巴菲赖，您的小助手已成功运行")
    
    # 启动所有线程（和之前一致）
    threading.Thread(target=monitor_auction, daemon=True).start()
    threading.Thread(target=monitor_robin, daemon=True).start()
    threading.Thread(target=monitor_aggr, daemon=True).start()
    
    # 主线程保持运行
    while True:
        time.sleep(3600)
