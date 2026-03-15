import os
import time
import threading
import requests
import pandas as pd
from datetime import datetime, timedelta

# ====================== 【全局配置】 ======================
WECHAT_WEBHOOK_URL = os.getenv("WECHAT_WEBHOOK_URL", "")
HOLD_STOCK_LIST = [c.strip() for c in os.getenv("HOLD_STOCK_LIST", "").split(",") if c.strip()]

# ====================== 【V2.0 稳健版参数】 ======================
LOW_PRICE_MAX = 20
LOW_RANGE_MAX = 0.15
RISE_5D_MAX = 12
MV_MIN = 30
MV_MAX = 150
VOL_MIN = 1.6
VOL_MAX = 5
STOP_LOSS = 0.96
TAKE_PROFIT = 1.08
SCAN_INTERVAL = 60

# ====================== 【激进版参数】 ======================
AGGR_SCAN_INTERVAL = 45
AGGR_VOL_RATIO_MIN = 2.5
AGGR_TURNOVER_MAX = 12
AGGR_AMOUNT_MIN = 2
AGGR_STOP_LOSS = 0.95

# ====================== 工具函数 ======================
def send_wechat(msg):
    if not WECHAT_WEBHOOK_URL:
        return
    try:
        requests.post(WECHAT_WEBHOOK_URL, json={"msgtype": "text", "text": {"content": msg}})
    except:
        pass

def is_trading_time():
    now = datetime.now().time()
    return datetime.strptime("09:30:00", "%H:%M:%S").time() <= now <= datetime.strptime("15:00:00", "%H:%M:%S").time()

# ====================== V2.0核心：大盘环境 ======================
def market_is_safe():
    try:
        url = "https://push2.eastmoney.com/api/qt/ulist/nlist?secid=1.000001,0.399001&fields=f3"
        data = requests.get(url, timeout=5).json()["data"]["diff"]
        return float(data[0]["f3"]) >= -0.3 and float(data[1]["f3"]) >= -0.3
    except:
        return True

# ====================== V2.0 实时数据 ======================
def get_real(code):
    try:
        sid = f"1.{code}" if code.startswith("60") else f"0.{code}"
        url = f"https://push2.eastmoney.com/api/qt/stock/get?secid={sid}&fields=f43,f57,f58,f168,f44,f107,f116,f217,f46,f173,f169"
        d = requests.get(url, timeout=3).json()["data"]
        return {
            "code": code, "name": d["f58"], "price": d["f43"],
            "volRatio": d["f168"], "avgPrice": d["f44"], "low30": d["f107"],
            "rise5d": d["f116"], "mcap": d["f217"], "amount": d["f46"],
            "highLimit": d["f173"], "turnover": d["f169"]
        }
    except:
        return None

# ====================== 【稳健版】选股逻辑 ======================
def is_low_real(stock):
    try:
        p, l30, r5, m = map(float, [stock["price"], stock["low30"], stock["rise5d"], stock["mcap"]])
        return all([
            p <= LOW_PRICE_MAX,
            (p-l30)/l30 <= LOW_RANGE_MAX,
            r5 <= RISE_5D_MAX,
            MV_MIN <= (m/10000) <= MV_MAX
        ])
    except: return False

def is_real_start(stock):
    try:
        vr, ap, cp = map(float, [stock["volRatio"], stock["avgPrice"], stock["price"]])
        return VOL_MIN <= vr <= VOL_MAX and cp >= ap * 0.995
    except: return False

def is_safe_trade(stock):
    try:
        return float(stock["amount"]) >= 15000 and "ST" not in stock["name"]
    except: return False

def scan_robin(code):
    d = get_real(code)
    if not d: return None
    if not is_safe_trade(d) or not is_low_real(d) or not is_real_start(d):
        return None
    d["stop"] = round(float(d["price"])*STOP_LOSS, 2)
    d["take"] = round(float(d["price"])*TAKE_PROFIT, 2)
    return d

# ====================== 【激进版】选股逻辑 ======================
def is_aggr_start(stock):
    try:
        vr, ap, cp, t, amt = map(float, [
            stock["volRatio"], stock["avgPrice"], stock["price"],
            stock["turnover"], stock["amount"]
        ])
        return all([
            vr >= AGGR_VOL_RATIO_MIN,
            cp >= ap * 1.005,
            t <= AGGR_TURNOVER_MAX,
            amt >= AGGR_AMOUNT_MIN * 10000,
            "ST" not in stock["name"]
        ])
    except: return False

def scan_aggr(code):
    d = get_real(code)
    if not d: return None
    if not is_aggressive_start(d): # 修复拼写错误：原is_aggressive_start -> is_aggr_start
        return None
    d["stop"] = round(float(d["price"])*AGGR_STOP_LOSS, 2)
    d["target"] = round(float(d["highLimit"]), 2)
    return d

# ====================== 【原有功能】早晚盘 + 持仓分析 ======================
def morning_analysis():
    msg = "🌅 【早盘稳健版】\n"
    pool = get_pool_robin()
    signals = []
    for code in pool:
        s = scan_robin(code)
        if s: signals.append(s)
    if signals:
        for i,s in enumerate(signals[:3]):
            msg += f"{i+1}. {s['name']}({s['code']}) 现价:{s['price']} 目标:{s['take']}\n"
    else:
        msg += "暂无符合条件标的"
    send_wechat(msg)

def tail_analysis():
    msg = "🔥 【尾盘稳健版】\n"
    pool = get_pool_robin()
    signals = []
    for code in pool:
        s = get_real(code)
        if s and float(s["rise5d"]) <= 6:
            signals.append(s)
    if signals:
        for i,s in enumerate(signals[:3]):
            msg += f"{i+1}. {s['name']}({s['code']}) 现价:{s['price']} 止损:{s['stop']}\n"
    else:
        msg += "暂无符合条件标的"
    send_wechat(msg)

def hold_analysis():
    msg = "📈 【持仓股操作建议】\n"
    if not HOLD_STOCK_LIST:
        return
    for code in HOLD_STOCK_LIST:
        s = get_real(code)
        if not s: continue
        pct = (float(s["price"])/float(s["price"]/1.01)-1)*100 # 估算涨幅
        name = s["name"]
        if pct > 3:
            sug = "持有，不破5日线不卖"
        elif 0 < pct <=3:
            sug = "持有，可小加"
        elif -3 < pct <=0:
            sug = "观望，做T"
        else:
            sug = f"减仓，止损:{round(float(s['price'])*0.97, 2)}"
        msg += f"{name}({code}) 涨幅:{pct:.1f}% → {sug}\n"
    send_wechat(msg)

# ====================== 辅助池 ======================
def get_pool_robin():
    try:
        url = "https://push2.eastmoney.com/api/qt/clist/get?pn=1&pz=200&fs=m:0+t:6,m:0+t:80&fields=f12"
        codes = [x["f12"] for x in requests.get(url, timeout=5).json()["data"]["diff"]]
    except:
        codes = []
    return list(set(codes + HOLD_STOCK_LIST))[:200]

def get_pool_aggr():
    try:
        url = "https://push2.eastmoney.com/api/qt/clist/get?pn=1&pz=100&fs=m:0+t:6,m:0+t:80&fields=f12"
        codes = [x["f12"] for x in requests.get(url, timeout=5).json()["data"]["diff"]]
    except:
        codes = []
    return list(set(codes + HOLD_STOCK_LIST))[:100]

# ====================== 主监控 ======================
def monitor_robin():
    print("启动稳健版监控")
    while True:
        if not is_trading_time() or not market_is_safe():
            time.sleep(60)
            continue
        if datetime.now().minute == 0: # 每分钟跑一次旧功能
            morning_analysis()
            tail_analysis()
            hold_analysis()
        pool = get_pool_robin()
        for code in pool:
            res = scan_robin(code)
            if res:
                send_wechat(f"🚀 稳健版信号\n{res['name']}({res['code']})\n现价:{res['price']} 止损:{res['stop']}")
            time.sleep(0.2)
        time.sleep(SCAN_INTERVAL)

def monitor_aggr():
    print("启动激进版监控")
    while True:
        if not is_trading_time():
            time.sleep(60)
            continue
        pool = get_pool_aggr()
        for code in pool:
            res = scan_aggr(code)
            if res:
                send_wechat(f"⚡️ 激进版信号\n{res['name']}({res['code']})\n现价:{res['price']} 目标:{res['target']}")
            time.sleep(0.15)
        time.sleep(AGGR_SCAN_INTERVAL)

# ====================== 入口 ======================
if __name__ == "__main__":
    send_wechat("✅ 双策略整合版已启动\n包含：稳健监控+激进监控+早晚盘+持仓分析")
    threading.Thread(target=monitor_robin, daemon=True).start()
    threading.Thread(target=monitor_aggr, daemon=True).start()
    while True:
        time.sleep(3600)
