import os
import time
import threading
import requests
from datetime import datetime

# ====================== 激进抓涨停版（与稳健版共存） ======================
WECHAT_WEBHOOK_URL = os.getenv("WECHAT_WEBHOOK_URL", "")
HOLD_STOCK_LIST = [c.strip() for c in os.getenv("HOLD_STOCK_LIST", "").split(",") if c.strip()]

# 激进策略核心参数
SCAN_INTERVAL = 45          # 45秒扫描一次
VOL_RATIO_MIN = 2.5         # 量比≥2.5（剧烈放量）
TURNOVER_MAX = 12           # 换手率≤12%（未到出货阶段）
AMOUNT_MIN = 2              # 成交额≥2亿（流动性足够）
STOP_LOSS_RATIO = 0.95      # 5%止损

# ====================== 基础工具函数 ======================
def send_wechat(msg):
    """微信消息推送（与稳健版区分标识）"""
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

# ====================== 激进版：实时数据获取 ======================
def get_real(code):
    """获取个股实时数据（聚焦涨停先兆）"""
    try:
        secid = f"1.{code}" if code.startswith("60") else f"0.{code}"
        url = f"https://push2.eastmoney.com/api/qt/stock/get?secid={secid}&fields=f43,f58,f168,f44,f173,f46,f169"
        
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        }
        resp = requests.get(url, headers=headers, timeout=2)
        data = resp.json()["data"]
        
        return {
            "code": code,
            "name": data["f58"],
            "price": float(data["f43"]),        # 当前价
            "volRatio": float(data["f168"]),    # 量比
            "avgPrice": float(data["f44"]),     # 分时均价
            "turnover": float(data["f169"]),    # 换手率
            "amount": float(data["f46"]),       # 成交额（万元）
            "highLimit": float(data["f173"])    # 涨停价
        }
    except Exception as e:
        print(f"❌ 获取{code}数据失败：{e}")
        return None

# ====================== 激进版核心：涨停先兆判断 ======================
def is_aggressive_start(stock):
    """判断是否为秒拉涨停先兆（超短暴利）"""
    try:
        current_price = stock["price"]
        avg_price = stock["avgPrice"]
        vol_ratio = stock["volRatio"]
        turnover = stock["turnover"]
        amount = stock["amount"] / 10000  # 转亿元
        high_limit = stock["highLimit"]
        
        # 激进筛选条件（全部满足）
        cond_vol = vol_ratio >= VOL_RATIO_MIN          # 剧烈放量
        cond_price = current_price >= avg_price * 1.005 # 强势在均线上方
        cond_turnover = turnover <= TURNOVER_MAX       # 换手率合理
        cond_amount = amount >= AMOUNT_MIN             # 流动性足够
        cond_not_limit = current_price < high_limit     # 未涨停（有上涨空间）
        cond_not_st = "ST" not in stock["name"]         # 非ST股
        
        return all([cond_vol, cond_price, cond_turnover, cond_amount, cond_not_limit, cond_not_st])
    except Exception as e:
        print(f"⚠️ 激进判断失败：{e}")
        return False

# ====================== 激进版：单只股票筛选 ======================
def scan_one(code):
    """单只股票激进筛选"""
    stock_data = get_real(code)
    if not stock_data:
        return None
    
    if is_aggressive_start(stock_data):
        # 计算止损价和目标价（涨停价）
        stock_data["stop_loss"] = round(stock_data["price"] * STOP_LOSS_RATIO, 2)
        stock_data["target_price"] = round(stock_data["highLimit"], 2)
        return stock_data
    return None

# ====================== 激进版：扫描池 ======================
def get_pool():
    """获取激进扫描池（放量强势股）"""
    codes = []
    try:
        # 全市场放量股（前200只）
        url = "https://push2.eastmoney.com/api/qt/clist/get?pn=1&pz=200&fs=m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23&fields=f12"
        resp = requests.get(url, timeout=3)
        data = resp.json()["data"]["diff"]
        codes = [item["f12"] for item in data]
    except Exception as e:
        print(f"⚠️ 获取放量池失败：{e}")
        codes = HOLD_STOCK_LIST
    
    # 去重并过滤A股
    codes = list(set([c for c in codes if c.startswith(('60', '00', '30'))]))
    return codes[:200]

# ====================== 激进版：盘中监控 ======================
def monitor_aggr():
    """激进版盘中监控主逻辑"""
    send_wechat("⚡️ 【激进版】秒拉涨停监控系统已启动（45秒级）")
    pushed_codes = set()  # 避免重复推送
    
    while True:
        # 非交易时间休眠
        if not is_trading_time():
            time.sleep(60)
            pushed_codes.clear()
            continue
        
        # 扫描放量强势股
        for code in get_pool():
            result = scan_one(code)
            if result and code not in pushed_codes:
                # 推送激进信号（与稳健版区分）
                msg = f"""
⚡️ 【激进版】秒拉涨停信号
📈 标的：{result['name']}（{result['code']}）
💰 现价：{result['price']} 元
🛡️ 止损：{result['stop_loss']} 元
🎯 目标：{result['target_price']} 元（涨停）
📌 仓位：1～2成（激进仓）
💡 逻辑：放量秒拉+强势承接+高流动性
                """
                send_wechat(msg)
                pushed_codes.add(code)
            time.sleep(0.15)  # 防接口限流
        
        # 按间隔休眠
        time.sleep(SCAN_INTERVAL)

# ====================== 激进版启动入口 ======================
if __name__ == "__main__":
    print("=====================================")
    print("⚡️ 激进版秒拉涨停系统 已启动")
    print("📌 监控频率：45秒/次 | 交易时间：9:30-15:00")
    print("=====================================")
    
    # 启动监控线程
    monitor_thread = threading.Thread(target=monitor_aggr)
    monitor_thread.daemon = True
    monitor_thread.start()
    
    # 主线程保持运行
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        send_wechat("🛑 【激进版】秒拉涨停监控系统已停止")
        print("\n🛑 系统已停止")
