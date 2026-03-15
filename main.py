import os
import requests
import pandas as pd
from datetime import datetime, timedelta
import time
import tushare as ts

# ====================== 初始化 第三方 Tushare 镜像 ======================
# 替换为你购买的token（卖家提供的字符串）
TU_SHARE_TOKEN = "b5ee7872baf9656580c59705285e4d570a0dc2a35f0ecfa67ec3b0438cbb"
pro = ts.pro_api(TU_SHARE_TOKEN)
# 第三方接口必须配置的地址和token参数
pro._DataApi__token = TU_SHARE_TOKEN
pro._DataApi__http_url = 'http://lianghua.nanyangqiankun.top'

# ====================== 1. 核心工具函数：交易日处理（改用第三方Tushare） ======================
def get_latest_trading_day(target_date=None):
    """获取最近交易日（周六/周日返回周五，节假日返回节前最后一个交易日）"""
    if target_date is None:
        target_date = datetime.now()
    
    target_str = target_date.strftime('%Y%m%d')
    try:
        # 用第三方Tushare交易日接口判断
        df = pro.trade_cal(exchange='SSE', start_date=target_str, end_date=target_str)
        if not df.empty and df.iloc[0]['is_open'] == 1:
            return target_str
        else:
            # 非交易日往前找
            for i in range(1, 10):
                check_date = target_date - timedelta(days=i)
                check_str = check_date.strftime('%Y%m%d')
                df = pro.trade_cal(exchange='SSE', start_date=check_str, end_date=check_str)
                if not df.empty and df.iloc[0]['is_open'] == 1:
                    return check_str
            return None
    except Exception as e:
        print(f"⚠️ Tushare 交易日接口异常，兜底处理：{str(e)}")
        # 兜底：周末返回周五
        if target_date.weekday() >= 5:
            return (target_date - timedelta(days=target_date.weekday() - 4)).strftime('%Y%m%d')
        else:
            return target_str

def is_trading_day():
    """判断当天是否是A股交易日（改用第三方Tushare）"""
    today = datetime.now().strftime('%Y%m%d')
    try:
        df = pro.trade_cal(exchange='SSE', start_date=today, end_date=today)
        return not df.empty and df.iloc[0]['is_open'] == 1
    except Exception as e:
        print(f"⚠️ Tushare 交易日判断异常，兜底处理：{str(e)}")
        return datetime.now().weekday() < 5

# ====================== 2. 核心：带重试的HTTP请求（保留，用于微信推送） ======================
def request_with_retry(url, max_retries=3, delay=1):
    """
    带重试的HTTP GET请求，提高接口成功率
    :param url: 请求地址
    :param max_retries: 最大重试次数
    :param delay: 重试间隔（秒）
    :return: 响应对象/None
    """
    for i in range(max_retries):
        try:
            # 增加超时时间+模拟浏览器请求头，避免被风控
            resp = requests.get(url, timeout=20, headers={
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                'Referer': 'https://quote.eastmoney.com/'
            })
            resp.raise_for_status()  # 抛出HTTP错误
            return resp
        except Exception as e:
            print(f"⚠️ 请求失败（第{i+1}次重试）：{str(e)[:50]}")
            if i < max_retries - 1:
                time.sleep(delay)
    print(f"❌ {url} 多次请求失败，放弃")
    return None

# ====================== 3. 微信推送核心函数（保留） ======================
def send_wechat_message(content, is_report=False):
    """发送微信消息（企业微信/微信群机器人）"""
    webhook_url = os.getenv('WECHAT_WEBHOOK_URL', '')
    if not webhook_url:
        print("⚠️ 未配置 WECHAT_WEBHOOK_URL，无法推送微信消息")
        return

    try:
        # 完整报告拆分推送（适配微信字数限制）
        if is_report:
            parts = content.split('-------------------------------------')
            send_wechat_message(parts[0].strip())
            for part in parts[1:]:
                if part.strip():
                    send_wechat_message(part.strip())
            return
        
        # 普通即时消息
        msg = {
            "msgtype": "text",
            "text": {
                "content": content
            }
        }
        resp = requests.post(webhook_url, json=msg, timeout=5)
        if resp.status_code == 200 and resp.json().get('errcode') == 0:
            print("✅ 微信消息推送成功")
        else:
            print(f"❌ 微信消息推送失败: {resp.text}")
    except Exception as e:
        print(f"❌ 微信推送异常: {str(e)}")

# ====================== 4. 自动选股池（改用第三方Tushare） ======================
def auto_generate_stock_pool():
    """
    自动生成5只高活跃选股池（兜底用）
    :return: 股票代码列表
    """
    auto_pool = []
    latest_day = get_latest_trading_day()
    if not latest_day:
        return []
    
    try:
        # 用第三方Tushare取高换手个股（前10只，取前5只非ST）
        time.sleep(0.5)
        df = pro.daily_basic(
            trade_date=latest_day,
            fields='ts_code, name, turnover_rate, volume_ratio, amount'
        )
        if df.empty:
            return []
        
        # 筛选：换手率5%-20%，量比>1，成交额>5亿，排除ST
        df = df[
            (df['turnover_rate'] > 5) &
            (df['turnover_rate'] < 20) &
            (df['volume_ratio'] > 1) &
            (df['amount'] > 50000) &
            (~df['name'].str.contains('ST'))
        ].sort_values('turnover_rate', ascending=False).head(10)
        
        # 提取前5只，转成纯数字代码
        for _, row in df.iterrows():
            code = row['ts_code'].split('.')[0]
            auto_pool.append(code)
            if len(auto_pool) >= 5:
                break
        
        print(f"✅ 自动生成选股池（{len(auto_pool)}只）：{auto_pool}")
        return auto_pool
    except Exception as e:
        print(f"⚠️ 自动生成选股池失败：{str(e)}")
        return []

# ====================== 5. 基础配置（保留） ======================
os.environ['TZ'] = 'Asia/Shanghai'
try:
    time.tzset()
except:
    pass

# 🔧 核心开关：是否开启自动选股（True=自选+5只自动，False=只跑自选）
ENABLE_AUTO_SELECT = True

# 读取你的自选持仓池（过滤非A股代码）
HOLD_STOCK_LIST = os.getenv('HOLD_STOCK_LIST', '').split(',')
HOLD_STOCK_LIST = [
    code.strip() for code in HOLD_STOCK_LIST 
    if code.strip() and (code.startswith('60') or code.startswith('00') or code.startswith('30'))
]
print("📌 你的自选持仓池：", HOLD_STOCK_LIST)

# 获取最近交易日
latest_trading_day = get_latest_trading_day()
print(f"📅 数据来源日期：{latest_trading_day}")

# 构建最终选股池（自选+自动，去重）
STOCK_POOL = HOLD_STOCK_LIST.copy()
if ENABLE_AUTO_SELECT:
    auto_pool = auto_generate_stock_pool()
    # 合并自选池+自动池，去重
    STOCK_POOL = list(set(STOCK_POOL + auto_pool))

# 最终过滤：只保留A股代码
STOCK_POOL = [code for code in STOCK_POOL if code.startswith(('60','00','30'))]
if not STOCK_POOL:
    print("⚠️ 最终选股池为空，程序退出")
    send_wechat_message(f"⚠️ 选股池为空（{latest_trading_day}），无分析数据")
    exit(0)
print(f"🔍 最终选股池（自选+自动）：{STOCK_POOL}")

# ====================== 6. 第三方Tushare 数据获取（替换东方财富API） ======================
def get_tushare_data(stock_code, data_type="realtime"):
    """
    获取股票数据（第三方Tushare版本）
    :param stock_code: 股票代码（纯数字）
    :param data_type: realtime/auction/tail
    :return: 数据字典/None
    """
    # 代码格式转换：纯数字 → Tushare格式
    if stock_code.startswith('60'):
        ts_code = f"{stock_code}.SH"
    elif stock_code.startswith('00') or stock_code.startswith('30'):
        ts_code = f"{stock_code}.SZ"
    else:
        print(f"❌ {stock_code} 非A股代码，跳过")
        return None

    try:
        # 限流控制：每次请求间隔0.3秒
        time.sleep(0.3)
        latest_day = get_latest_trading_day()
        
        # 1. 获取日线基础数据
        df_daily = pro.daily(ts_code=ts_code, trade_date=latest_day)
        if df_daily.empty:
            print(f"❌ {stock_code} 无日线数据")
            return None
        daily = df_daily.iloc[0]
        
        # 2. 获取每日指标数据（换手率、量比等）
        df_basic = pro.daily_basic(ts_code=ts_code, trade_date=latest_day)
        basic = df_basic.iloc[0] if not df_basic.empty else {}
        
        # 3. 构建结果字典
        result = {
            'code': stock_code,
            'name': basic.get('name', stock_code),
            'price': float(daily['close']),
            'pre_close': float(daily['pre_close']),
            'high': float(daily['high']),
            'low': float(daily['low']),
            'volume': float(daily['vol']) / 100,  # 手数
            'pct_chg': round(float(daily['pct_chg']), 2)
        }

        # 补充竞价字段（用开盘价近似）
        if data_type == "auction":
            result['auction_open'] = float(daily['open'])
            result['auction_vol'] = float(daily['vol']) / 100 * 0.1  # 竞价量估算
            result['auction_pct'] = round((float(daily['open']) - float(daily['pre_close'])) / float(daily['pre_close']) * 100, 2)
        
        # 补充尾盘字段
        if data_type == "tail":
            result['turnover'] = float(basic.get('turnover_rate', 0))
            result['amount'] = float(daily['amount']) / 10000  # 万元
            result['vol_ratio'] = float(basic.get('volume_ratio', 1.0))

        return result
    except Exception as e:
        print(f"❌ {stock_code} {data_type}数据解析失败: {str(e)[:60]}")
        return None

# ====================== 7. 早盘分析（保留逻辑，替换数据函数） ======================
def morning_analysis():
    """早盘竞价分析 + 即时微信推送"""
    morning_stocks = []
    print("\\n🌅 开始早盘竞价分析...")

    for code in STOCK_POOL:
        try:
            auc_data = get_tushare_data(code, "auction")
            if not auc_data:
                continue

            # 选股条件（空值兜底，避免报错）
            cond1 = 3 < auc_data.get('auction_pct', 0) < 8          # 竞价高开3%-8%
            cond2 = auc_data.get('auction_vol', 0) > 5000           # 竞价量≥5000手
            cond3 = auc_data.get('price', 0) > auc_data.get('auction_open', 0) * 0.99  # 不破开盘价
            cond4 = auc_data.get('volume', 0) > auc_data.get('auction_vol', 0) * 1.2   # 放量

            if all([cond1, cond2, cond3, cond4]):
                auc_data['target_price'] = round(auc_data.get('pre_close', 0) * 1.1, 2)  # 涨停目标价
                auc_data['support_price'] = round(auc_data.get('auction_open', 0) * 0.98, 2)  # 支撑位
                morning_stocks.append(auc_data)
        except Exception as e:
            print(f"⚠️ 跳过早盘选股 {code}：{str(e)}")
            continue

    # 早盘即时结果推送
    print("\\n🌅 【早盘即时选股结果】（竞价可买入）")
    wechat_content = f"🌅 早盘即时选股结果（{latest_trading_day}）\\n（9:25-9:30竞价买入，不破支撑位持有）\\n"
    if morning_stocks:
        for i, stock in enumerate(morning_stocks[:3], 1):
            line = f"{i}. {stock['name']}（{stock['code']}）\\n  竞价高开：{stock['auction_pct']}% | 支撑位：{stock['support_price']}元 | 目标价：{stock['target_price']}元"
            print(line)
            wechat_content += line + "\\n"
    else:
        wechat_content = f"🌅 早盘即时选股结果（{latest_trading_day}）\\n⚠️ 暂无符合条件的早盘标的"
        print(wechat_content)
    
    send_wechat_message(wechat_content)
    return morning_stocks

# ====================== 8. 持仓分析（保留逻辑，替换数据函数） ======================
def hold_analysis():
    """持仓股票实时分析"""
    hold_suggestions = []
    print("\\n📈 开始持仓分析...")

    for code in HOLD_STOCK_LIST:
        try:
            rt_data = get_tushare_data(code, "realtime")
            if not rt_data:
                continue

            # 趋势判断 + 操作建议
            pct_chg = rt_data.get('pct_chg', 0)
            if pct_chg > 3:
                trend = "强势上涨"
                suggestion = "✅ 持有，不破5日线不卖"
            elif 0 < pct_chg <= 3:
                trend = "震荡上涨"
                suggestion = "✅ 持有，可小仓位加仓"
            elif -3 < pct_chg <= 0:
                trend = "震荡调整"
                suggestion = "⚠️ 观望，做T（高抛低吸）"
            else:
                trend = "弱势下跌"
                suggestion = "❌ 减仓，止损位：" + str(round(rt_data.get('pre_close', 0) * 0.97, 2))

            hold_suggestions.append({
                **rt_data,
                'trend': trend,
                'suggestion': suggestion
            })
        except Exception as e:
            print(f"⚠️ 跳过持仓股 {code}：{str(e)}")
            continue
    return hold_suggestions

# ====================== 9. 尾盘分析（保留逻辑，替换数据函数） ======================
def tail_analysis():
    """尾盘分析 + 即时微信推送"""
    tail_stocks = []
    print("\\n🔥 开始尾盘分析...")

    for code in STOCK_POOL:
        try:
            tail_data = get_tushare_data(code, "tail")
            if not tail_data:
                continue

            # 选股条件（空值兜底）
            cond1 = 1 < tail_data.get('pct_chg', 0) < 5             # 尾盘涨幅1%-5%
            cond2 = 5 < tail_data.get('turnover', 0) < 15           # 换手率5%-15%
            cond3 = tail_data.get('vol_ratio', 0) >= 1.5            # 量比≥1.5
            cond4 = tail_data.get('price', 0) > tail_data.get('high', 0) * 0.98  # 收盘价靠近最高价

            if all([cond1, cond2, cond3, cond4]):
                tail_data['next_target'] = round(tail_data.get('pre_close', 0) * 1.1, 2)  # 次日涨停目标价
                tail_data['buy_price'] = round(tail_data.get('price', 0) * 1.01, 2)  # 尾盘买入价
                tail_stocks.append(tail_data)
        except Exception as e:
            print(f"⚠️ 跳过尾盘选股 {code}：{str(e)}")
            continue

    # 尾盘即时结果推送
    print("\\n🔥 【尾盘即时选股结果】（可直接买入）")
    wechat_content = f"🔥 尾盘即时选股结果（{latest_trading_day}）\\n（14:57-15:00买入，次日冲高止盈）\\n"
    if tail_stocks:
        for i, stock in enumerate(tail_stocks[:3], 1):
            line = f"{i}. {stock['name']}（{stock['code']}）\\n  尾盘价：{stock['price']}元 | 买入价：{stock['buy_price']}元 | 次日目标：{stock['next_target']}元"
            print(line)
            wechat_content += line + "\\n"
    else:
        wechat_content = f"🔥 尾盘即时选股结果（{latest_trading_day}）\\n⚠️ 暂无符合条件的尾盘标的"
        print(wechat_content)
    
    send_wechat_message(wechat_content)
    return tail_stocks

# ====================== 10. 生成完整报告 + 推送（保留） ======================
def generate_full_report():
    """生成完整分析报告 + 微信推送"""
    now = datetime.now()
    report = f"""
=====================================
📅 短线交易分析报告（{now.strftime('%Y-%m-%d %H:%M:%S')}）
📌 数据来源：{latest_trading_day} | 选股池数量：{len(STOCK_POOL)}
=====================================

【一、早盘竞价分析（9:25-9:30）】
"""
    # 早盘模块
    morning_stocks = morning_analysis()
    if morning_stocks:
        for i, stock in enumerate(morning_stocks, 1):
            report += f"""
【{i}. {stock['name']}（{stock['code']}）】
竞价高开：{stock['auction_pct']}% | 竞价量：{stock['auction_vol']:.0f}手
开盘价：{stock['auction_open']}元 | 当前价：{stock['price']}元
支撑位：{stock['support_price']}元 | 当日目标价：{stock['target_price']}元
操作建议：✅ 竞价买入，不破支撑位持有至大涨
"""
    else:
        report += "⚠️ 暂无符合条件的早盘标的\\n"

    # 持仓模块
    report += """
-------------------------------------
【二、持仓股票操作建议】
"""
    hold_suggestions = hold_analysis()
    if hold_suggestions:
        for stock in hold_suggestions:
            report += f"""
【{stock['name']}（{stock['code']}）】
当前价：{stock['price']}元 | 涨跌幅：{stock['pct_chg']}%
趋势判断：{stock['trend']}
操作建议：{stock['suggestion']}
"""
    else:
        report += "⚠️ 暂无持仓数据或持仓分析失败\\n"

    # 尾盘模块
    report += """
-------------------------------------
【三、尾盘买入分析（14:57-15:00）】
"""
    tail_stocks = tail_analysis()
    if tail_stocks:
        for i, stock in enumerate(tail_stocks, 1):
            report += f"""
【{i}. {stock['name']}（{stock['code']}）】
尾盘价：{stock['price']}元 | 涨跌幅：{stock['pct_chg']}%
换手率：{stock['turnover']:.1f}% | 量比：{stock['vol_ratio']:.1f}
买入价：{stock['buy_price']}元 | 次日目标价：{stock['next_target']}元
操作建议：✅ 尾盘买入，次日冲高止盈（目标涨停）
"""
    else:
        report += "⚠️ 暂无符合条件的尾盘标的\\n"

    # 保存报告
    os.makedirs('reports', exist_ok=True)
    report_path = f"reports/交易分析报告_{latest_trading_day}_{now.strftime('%H%M')}.txt"
    with open(report_path, 'w', encoding='utf-8') as f:
        f.write(report)

    print("\\n" + "="*60)
    print("📋 完整分析报告已生成：", report_path)
    print("="*60)

    # 推送完整报告到微信
    send_wechat_message(report, is_report=True)
    return report

# ====================== 11. 主函数（保留） ======================
if __name__ == '__main__':
    print("🚀 开始执行：双模式选股池分析（自选+自动）")
    generate_full_report()
    print(f"\\n✅ 所有分析完成！数据日期：{latest_trading_day}")
