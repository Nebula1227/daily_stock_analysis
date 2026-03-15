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
        requests.post(WECHAT_WEBHOOK_URL, json={"msgtype": "text", "text": {"content": msg.strip()}})
    except Exception as e:
        print(f"推送失败：{e}")

def is_trading_time():
    now = datetime.now().time()
    return datetime.strptime("09:30:00", "%H:%M:%S").time() <= now <= datetime.strptime("15:00:00", "%H:%M:%S").time()

def is_auction_time(morning=True):
    """判断是否在真实竞价时段"""
    now = datetime.now().time()
    if morning:
        # 早盘真实竞价：9:24-9:25（不可撤单，最后1分钟）
        start = datetime.strptime("09:24:00", "%H:%M:%S").time()
        end = datetime.strptime("09:25:00", "%H:%M:%S").time()
    else:
        # 尾盘竞价：14:57-15:00（本身不可撤单）
        start = datetime.strptime("14:57:00", "%H:%M:%S").time()
        end = datetime.strptime("15:00:00", "%H:%M:%S").time()
    return start <= now <= end

# ====================== V2.0核心：大盘环境 ======================
def market_is_safe():
    try:
        url = "https://push2.eastmoney.com/api/qt/ulist/nlist?secid=1.000001,0.399001&fields=f3"
        data = requests.get(url, timeout=5).json()["data"]["diff"]
        return float(data[0]["f3"]) >= -0.3 and float(data[1]["f3"]) >= -0.3
    except:
        return True

# ====================== 竞价数据获取 ======================
def get_auction_data(code):
    """获取竞价数据（早盘/尾盘）"""
    try:
        sid = f"1.{code}" if code.startswith("60") else f"0.{code}"
        # 竞价核心字段：价格、量、封单
        url = f"https://push2.eastmoney.com/api/qt/stock/get?secid={sid}&fields=f43,f57,f58,f62,f64,f168,f107,f116,f217"
        d = requests.get(url, timeout=3).json()["data"]
        return {
            "code": code,
            "name": d["f58"],
            "price": d["f43"],          # 竞价价格
            "auction_vol": d["f62"],    # 竞价成交量（手）
            "auction_amount": d["f64"], # 竞价成交额（万元）
            "low30": d["f107"],         # 30日最低价
            "rise5d": d["f116"],        # 近5日涨幅
            "mcap": d["f217"],          # 流通市值（万元）
            "volRatio": d["f168"]       # 量比
        }
    except Exception as e:
        print(f"获取竞价数据失败：{e}")
        return None

# ====================== 新增：开盘5分钟验证 ======================
def verify_open_5min(auction_stocks):
    """验证竞价标的在开盘后5分钟是否延续强势"""
    if not auction_stocks:
        return []
    
    send_wechat("⏳ 开始验证竞价标的开盘后5分钟强势度...")
    verified_stocks = []
    # 等待开盘5分钟（9:30-9:35）
    wait_seconds = 0
    while datetime.now().time() < datetime.strptime("09:35:00", "%H:%M:%S").time():
        time.sleep(10)
        wait_seconds += 10
        if wait_seconds > 300:
            break
    
    for stock in auction_stocks:
        code = stock["code"]
        try:
            # 获取开盘后5分钟实时数据
            real_data = get_real(code)
            if not real_data:
                continue
            
            # 提取验证所需数据
            auction_price = float(stock["price"])
            open_price = float(real_data["price"])
            avg_price = float(real_data["avgPrice"])
            vol_ratio = float(real_data["volRatio"])
            
            # 计算涨幅
            rise_auction = (auction_price / (auction_price/1.01) - 1) * 100  # 竞价涨幅
            rise_open = (open_price / (auction_price/1.01) - 1) * 100        # 开盘后涨幅
            
            # 核心验证条件（全部满足才算强势）
            cond1 = open_price >= auction_price * 0.995  # 开盘不低开（允许0.5%以内误差）
            cond2 = open_price >= avg_price * 0.995      # 股价在均价线上方
            cond3 = vol_ratio >= 1.2                     # 开盘后继续放量
            cond4 = rise_open >= rise_auction * 0.7      # 涨幅保留70%以上
            
            if all([cond1, cond2, cond3, cond4]):
                verified_stocks.append({
                    "code": stock["code"],
                    "name": stock["name"],
                    "auction_price": auction_price,
                    "open_price": open_price,
                    "auction_vol": stock["auction_vol"],
                    "rise_auction": round(rise_auction, 1),
                    "rise_open": round(rise_open, 1),
                    "vol_ratio": round(vol_ratio, 1)
                })
        except Exception as e:
            print(f"验证{code}失败：{e}")
            continue
    
    return verified_stocks

# ====================== 竞价选股逻辑（强化版） ======================
def scan_auction(morning=True):
    """竞价选股（早盘9:24-9:25，尾盘14:57-15:00）"""
    send_wechat(f"🔍 开始{('早盘真实' if morning else '尾盘')}竞价选股（不可撤单）")
    pool = []
    try:
        # 竞价扫描池（自选股+放量股）
        url = "https://push2.eastmoney.com/api/qt/clist/get?pn=1&pz=200&fs=m:0+t:6,m:0+t:80&fields=f12"
        pool = [x["f12"] for x in requests.get(url, timeout=5).json()["data"]["diff"]]
        pool += HOLD_STOCK_LIST
        pool = list(set(pool))[:100]
    except:
        pool = HOLD_STOCK_LIST
    
    good_stocks = []
    for code in pool:
        data = get_auction_data(code)
        if not data or "ST" in data["name"]:
            continue
        
        # 竞价选股核心条件（强化版）
        try:
            price = float(data["price"])
            auction_vol = float(data["auction_vol"])
            mcap = float(data["mcap"]) / 10000  # 转亿元
            low30 = float(data["low30"])
            rise5d = float(data["rise5d"])
            
            # 1. 价格低位
            cond1 = price <= LOW_PRICE_MAX
            cond2 = (price - low30) / low30 <= LOW_RANGE_MAX
            cond3 = rise5d <= RISE_5D_MAX
            cond4 = MV_MIN <= mcap <= MV_MAX
            
            # 2. 竞价放量（早盘≥800手，尾盘≥300手）
            cond5 = auction_vol >= (800 if morning else 300)
            
            # 3. 涨幅合理（早盘2%-6%，尾盘0%-3%）
            rise = (price / (price/1.01) - 1) * 100  # 估算竞价涨幅
            cond6 = (2 <= rise <= 6) if morning else (0 <= rise <= 3)
            
            if all([cond1, cond2, cond3, cond4, cond5, cond6]):
                good_stocks.append(data)
        except Exception as e:
            print(f"筛选{code}失败：{e}")
            continue
    
    # 早盘竞价需要开盘验证，尾盘直接推送
    if good_stocks:
        if morning:
            send_wechat(f"📌 早盘竞价初选{len(good_stocks)}只标的，将在9:35验证开盘强势后推送最终结果")
            # 执行开盘5分钟验证
            verified_stocks = verify_open_5min(good_stocks)
            
            if verified_stocks:
                msg = f"🎉 早盘竞价【最终验证通过】优质标的（真实竞价+开盘强势）\n"
                for i, s in enumerate(verified_stocks[:3]):
                    msg += f"{i+1}. {s['name']}({s['code']})\n"
                    msg += f"   竞价价：{s['auction_price']} | 开盘价：{s['open_price']}\n"
                    msg += f"   竞价涨幅：{s['rise_auction']}% | 开盘涨幅：{s['rise_open']}%\n"
                    msg += f"   核心：不可撤单+开盘放量+强势延续\n"
                send_wechat(msg)
            else:
                send_wechat(f"😶 早盘竞价初选标的开盘后未延续强势，暂无可推送标的")
        else:
            # 尾盘竞价直接推送
            msg = f"🎉 尾盘竞价优质标的（不可撤单）\n"
            for i, s in enumerate(good_stocks[:3]):
                msg += f"{i+1}. {s['name']}({s['code']})\n"
                msg += f"   竞价价：{s['price']} | 竞价量：{s['auction_vol']}手 | 预估涨幅：{round((float(s['price'])/(float(s['price'])/1.01)-1)*100, 1)}%\n"
            send_wechat(msg)
    else:
        send_wechat(f"😶 {('早盘真实' if morning else '尾盘')}竞价暂无优质标的")

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
    if not is_aggr_start(d):
        return None
    d["stop"] = round(float(d["price"])*AGGR_STOP_LOSS, 2)
    d["target"] = round(float(d["highLimit"]), 2)
    return d

# ====================== 【原有功能】早晚盘 + 持仓分析 ======================
def morning_analysis():
    msg = "🌅 【早盘分析】\n"
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
    return msg

def tail_analysis():
    msg = "🔥 【尾盘分析】\n"
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
    return msg

def hold_analysis():
    msg = "📈 【持仓操作建议】\n"
    if not HOLD_STOCK_LIST:
        return "暂无持仓股"
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
    return msg

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
def monitor_auction():
    """竞价监控线程"""
    print("启动竞价监控（只看不可撤单数据+开盘验证）")
    while True:
        now = datetime.now()
        # 早盘真实竞价（9:24-9:25）
        if is_auction_time(morning=True):
            scan_auction(morning=True)
            time.sleep(60) # 竞价时段只扫一次
        # 尾盘竞价（14:57-15:00）
        elif is_auction_time(morning=False):
            scan_auction(morning=False)
            time.sleep(30) # 尾盘只扫一次
        else:
            time.sleep(60)

def monitor_robin():
    """稳健版监控线程"""
    print("启动稳健版监控")
    last_push_hour = -1
    while True:
        if not is_trading_time() or not market_is_safe():
            time.sleep(60)
            continue
        
        # 关键时间点推送分析（避免整点冗余）
        current_hour = datetime.now().hour
        current_min = datetime.now().minute
        if current_hour == 9 and current_min == 35:
            send_wechat(f"{morning_analysis()}\n{hold_analysis()}")
            last_push_hour = current_hour
        elif current_hour == 11 and current_min == 30:
            send_wechat(f"🍱 【午盘小结】\n{hold_analysis()}")
            last_push_hour = current_hour
        elif current_hour == 14 and current_min == 30:
            send_wechat(f"{tail_analysis()}\n{hold_analysis()}")
            last_push_hour = current_hour
        elif current_hour == 15 and current_min == 5:
            send_wechat(f"📊 【收盘总结】\n{hold_analysis()}")
            last_push_hour = current_hour
        
        # 实时扫描稳健版
        pool = get_pool_robin()
        for code in pool:
            res = scan_robin(code)
            if res:
                send_wechat(f"🚀 稳健版信号\n{res['name']}({res['code']})\n现价:{res['price']} 止损:{res['stop']}")
            time.sleep(0.2)
        time.sleep(SCAN_INTERVAL)

def monitor_aggr():
    """激进版监控线程"""
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
    send_wechat("✅ 终极版监控系统已启动\n包含：真实竞价+开盘验证+稳健+激进+早晚盘+持仓分析")
    # 启动3个线程：竞价+稳健+激进
    threading.Thread(target=monitor_auction, daemon=True).start()
    threading.Thread(target=monitor_robin, daemon=True).start()
    threading.Thread(target=monitor_aggr, daemon=True).start()
    # 主线程保持运行
    while True:
        time.sleep(3600)
