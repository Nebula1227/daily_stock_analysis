import os
import time
import threading
import requests
import pandas as pd
from datetime import datetime, timedelta

# ====================== 【V2.0 最终版配置】 ======================
# 你的第三方TOKEN（非必须，本版已适配免费接口）
TU_SHARE_TOKEN = "b5ee7872baf9656580c59705285e4d570a0dc2a35f0ecfa67ec3b0438cbb"
WECHAT_WEBHOOK_URL = os.getenv("WECHAT_WEBHOOK_URL", "")
HOLD_STOCK_LIST = [c.strip() for c in os.getenv("HOLD_STOCK_LIST", "").split(",") if c.strip()]

# 核心策略参数（已实战优化）
LOW_PRICE_MAX = 20          # 股价≤20元
LOW_RANGE_MAX = 0.15        # 距30日低点≤15%
RISE_5D_MAX = 12            # 近5日涨幅≤12%
MV_MIN = 30                 # 流通市值≥30亿
MV_MAX = 150                # 流通市值≤150亿
VOL_MIN = 1.6               # 量比≥1.6
VOL_MAX = 5                 # 量比≤5
STOP_LOSS = 0.96            # 4%止损
TAKE_PROFIT = 1.08          # 8%止盈
SCAN_INTERVAL = 60          # 1分钟监控

# ====================== 基础工具函数 ======================
def send_wechat(msg):
    """微信消息推送"""
    if not WECHAT_WEBHOOK_URL:
        print("⚠️ 未配置微信Webhook，跳过推送")
        return
    try:
        requests.post(WECHAT_WEBHOOK_URL, json={"msgtype": "text", "text": {"content": msg.strip()}})
        print("✅ 微信推送成功")
    except Exception as e:
        print(f"❌ 微信推送失败：{e}")

def is_trading_time():
    """判断是否为交易时间"""
    now = datetime.now().time()
    start = datetime.strptime("09:30:00", "%H:%M:%S").time()
    end = datetime.strptime("15:00:00", "%H:%M:%S").time()
    return start <= now <= end

# ====================== V2.0核心：大盘环境判断 ======================
def market_is_safe():
    """判断大盘环境是否安全（暴跌时不交易）"""
    try:
        # 沪深指数实时数据
        url = "https://push2.eastmoney.com/api/qt/ulist/nlist?secid=1.000001,0.399001&fields=f3"
        resp = requests.get(url, timeout=5)
        data = resp.json()["data"]["diff"]
        shanghai = float(data[0]["f3"])  # 上证指数
        shenzhen = float(data[1]["f3"])  # 深证成指
        # 大盘跌幅≤0.3%视为安全
        return shanghai >= -0.3 and shenzhen >= -0.3
    except Exception as e:
        print(f"⚠️ 大盘环境判断失败：{e}，默认视为安全")
        return True

# ====================== V2.0核心：低位过滤 ======================
def is_low_real(stock):
    """严格低位判断（4条铁律）"""
    try:
        price = float(stock["price"])
        low30 = float(stock["low30"])
        rise5d = float(stock["rise5d"])
        mv = float(stock["mcap"]) / 10000  # 流通市值（万元转亿元）
        
        # 低位条件全部满足
        cond_price = price <= LOW_PRICE_MAX
        cond_low_range = (price - low30) / low30 <= LOW_RANGE_MAX
        cond_rise = rise5d <= RISE_5D_MAX
        cond_mv = MV_MIN <= mv <= MV_MAX
        
        return all([cond_price, cond_low_range, cond_rise, cond_mv])
    except Exception as e:
        print(f"⚠️ 低位判断失败：{e}")
        return False

# ====================== V2.0核心：真启动判断 ======================
def is_real_start(stock):
    """判断是否为真启动（过滤假放量/假突破）"""
    try:
        vol_ratio = float(stock["volRatio"])
        avg_price = float(stock["avgPrice"])
        current_price = float(stock["price"])
        
        # 量比合理 + 股价紧贴均价线（资金承接强）
        cond_vol = VOL_MIN <= vol_ratio <= VOL_MAX
        cond_price = current_price >= avg_price * 0.995
        
        return cond_vol and cond_price
    except Exception as e:
        print(f"⚠️ 启动判断失败：{e}")
        return False

# ====================== V2.0核心：流动性安全 ======================
def is_safe_trade(stock):
    """判断交易安全性（排除ST/低流动性）"""
    try:
        amount = float(stock["amount"]) / 10000  # 成交额（万元）
        name = stock["name"]
        
        # 成交额≥1.5亿 + 非ST股
        cond_amount = amount >= 1.5
        cond_st = "ST" not in name
        
        return cond_amount and cond_st
    except Exception as e:
        print(f"⚠️ 安全判断失败：{e}")
        return False

# ====================== V2.0 实时数据获取 ======================
def get_real(code):
    """获取个股实时数据（免费接口）"""
    try:
        # 东方财富secid转换
        secid = f"1.{code}" if code.startswith("60") else f"0.{code}"
        url = f"https://push2.eastmoney.com/api/qt/stock/get?secid={secid}&fields=f43,f57,f58,f168,f44,f107,f116,f217,f46,f173"
        
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        }
        resp = requests.get(url, headers=headers, timeout=3)
        data = resp.json()["data"]
        
        return {
            "code": code,
            "name": data["f58"],
            "price": data["f43"],          # 当前价
            "volRatio": data["f168"],      # 量比
            "avgPrice": data["f44"],       # 分时均价
            "low30": data["f107"],         # 30日最低价
            "rise5d": data["f116"],        # 近5日涨幅
            "mcap": data["f217"],          # 流通市值（万元）
            "amount": data["f46"],         # 成交额（万元）
            "highLimit": data["f173"]      # 涨停价
        }
    except Exception as e:
        print(f"❌ 获取{code}数据失败：{e}")
        return None

# ====================== V2.0 最终选股 ======================
def scan_one(code):
    """单只股票筛选"""
    stock_data = get_real(code)
    if not stock_data:
        return None
    
    # 安全过滤 → 低位过滤 → 启动判断
    if not is_safe_trade(stock_data):
        return None
    if not is_low_real(stock_data):
        return None
    if not is_real_start(stock_data):
        return None
    
    # 计算止损/止盈价
    stock_data["stop_loss"] = round(float(stock_data["price"]) * STOP_LOSS, 2)
    stock_data["take_profit"] = round(float(stock_data["price"]) * TAKE_PROFIT, 2)
    return stock_data

# ====================== 全市场扫描池 ======================
def get_pool():
    """获取扫描池（全市场低位放量股+自选股）"""
    codes = []
    try:
        # 全市场A股列表（前300只）
        url = "https://push2.eastmoney.com/api/qt/clist/get?pn=1&pz=300&fs=m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23&fields=f12"
        resp = requests.get(url, timeout=5)
        data = resp.json()["data"]["diff"]
        codes = [item["f12"] for item in data]
    except Exception as e:
        print(f"⚠️ 获取全市场股票失败：{e}")
    
    # 加入自选股并去重
    codes += HOLD_STOCK_LIST
    codes = list(set([c for c in codes if c.startswith(('60', '00', '30'))]))
    
    return codes[:300]  # 限制扫描数量，避免卡顿

# ====================== 盘中实时监控 ======================
def monitor():
    """盘中实时监控主逻辑"""
    send_wechat("✅ 【稳健版】低位启动涨停系统已启动（1分钟级监控）")
    pushed_codes = set()  # 避免重复推送
    
    while True:
        # 非交易时间休眠
        if not is_trading_time():
            time.sleep(60)
            pushed_codes.clear()
            continue
        
        # 大盘环境不安全时，暂停推送
        if not market_is_safe():
            time.sleep(60)
            continue
        
        # 扫描全市场股票
        for code in get_pool():
            result = scan_one(code)
            if result and code not in pushed_codes:
                # 推送消息
                msg = f"""
🚀 【稳健版】低位启动信号
📈 标的：{result['name']}（{result['code']}）
💰 现价：{result['price']} 元
🛡️ 止损：{result['stop_loss']} 元
🎯 止盈：{result['take_profit']} 元
📌 仓位：1～3成
💡 逻辑：低位+真启动+安全环境
                """
                send_wechat(msg)
                pushed_codes.add(code)
            time.sleep(0.2)  # 防接口限流
        
        # 按间隔休眠
        time.sleep(SCAN_INTERVAL)

# ====================== 启动入口 ======================
if __name__ == "__main__":
    print("=====================================")
    print("🚀 稳健版低位启动涨停系统 V2.0 已启动")
    print("📌 监控频率：1分钟/次 | 交易时间：9:30-15:00")
    print("=====================================")
    
    # 启动监控线程
    monitor_thread = threading.Thread(target=monitor)
    monitor_thread.daemon = True
    monitor_thread.start()
    
    # 主线程保持运行
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        send_wechat("🛑 【稳健版】低位启动监控系统已停止")
        print("\n🛑 系统已停止")
