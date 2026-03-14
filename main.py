import os
import requests
import pandas as pd
from datetime import datetime, timedelta
import time

# ====================== 1. 核心工具函数：交易日处理 ======================
def get_latest_trading_day(target_date=None):
    """获取最近交易日（周六/周日返回周五，节假日返回节前最后一个交易日）"""
    if target_date is None:
        target_date = datetime.now()
    
    # 先回退到非周末
    if target_date.weekday() == 5:  # 周六
        target_date -= timedelta(days=1)
    elif target_date.weekday() == 6:  # 周日
        target_date -= timedelta(days=2)
    
    # 验证是否为交易日（处理节假日）
    target_str = target_date.strftime('%Y%m%d')
    try:
        url = f"http://push2.eastmoney.com/api/qt/stock/tradingday/get?fields=tradingDay&beginDate={target_str}&endDate={target_str}"
        resp = requests.get(url, timeout=10)
        data = resp.json()['data']['tradingDay']
        if len(data) > 0:
            return target_str
        else:
            # 非交易日往前找
            for i in range(1, 10):
                check_date = target_date - timedelta(days=i)
                check_str = check_date.strftime('%Y%m%d')
                url_check = f"http://push2.eastmoney.com/api/qt/stock/tradingday/get?fields=tradingDay&beginDate={check_str}&endDate={check_str}"
                resp_check = requests.get(url_check, timeout=10)
                data_check = resp_check.json()['data']['tradingDay']
                if len(data_check) > 0:
                    return check_str
            return None
    except:
        # 接口异常时的兜底逻辑
        if target_date.weekday() >= 5:
            return (target_date - timedelta(days=target_date.weekday() - 4)).strftime('%Y%m%d')
        else:
            return target_str

def is_trading_day():
    """判断当天是否是A股交易日"""
    today = datetime.now().strftime('%Y%m%d')
    try:
        url = f"http://push2.eastmoney.com/api/qt/stock/tradingday/get?fields=tradingDay&beginDate={today}&endDate={today}"
        resp = requests.get(url, timeout=10)
        data = resp.json()['data']['tradingDay']
        return len(data) > 0
    except:
        return datetime.now().weekday() < 5

# ====================== 2. 核心：带重试的HTTP请求（解决Connection aborted） ======================
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

# ====================== 3. 微信推送核心函数 ======================
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

# ====================== 4. 自动选股池（缩到5只，极低限流风险） ======================
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
        # 先休眠0.5秒，降低限流风险
        time.sleep(0.5)
        # 东方财富高换手个股接口
        url = f"http://push2.eastmoney.com/api/qt/clist/get?pn=1&pz=10&po=1&np=1&ut=bd1d9ddb04089700cf9c27f6f7426280e&fltt=2&invt=2&fid=f107&fs=m:0+t:6,m:0+t:13,m:0+t:80,m:1+t:2,m:1+t:23&fields=f12,f14,f107,f5,f6,f8&f107=5&f5=0&f8=50000"
        resp = request_with_retry(url)
        if not resp:
            return []
        
        data = resp.json().get('data', {}).get('diff', [])
        if not data:
            return []
        
        # 提取前5只高活跃股票（排除ST股）
        for stock in data[:5]:
            code = stock.get('f12', '')
            name = stock.get('f14', '')
            if code and name and 'ST' not in name and '*ST' not in name:
                auto_pool.append(code)
        
        print(f"✅ 自动生成选股池（{len(auto_pool)}只）：{auto_pool}")
        return auto_pool
    except Exception as e:
        print(f"⚠️ 自动生成选股池失败：{str(e)}")
        return []

# ====================== 5. 基础配置（双模式选股池核心） ======================
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

# ====================== 6. 东方财富API（重试+备用接口+0.3秒sleep） ======================
def get_eastmoney_data(stock_code, data_type="realtime"):
    """
    获取股票数据（主接口失败自动切备用接口）
    :param stock_code: 股票代码
    :param data_type: realtime/auction/tail
    :return: 数据字典/None
    """
    # 代码格式转换
    if stock_code.startswith('60'):
        secid = f"1.{stock_code}"
    elif stock_code.startswith('00') or stock_code.startswith('30'):
        secid = f"0.{stock_code}"
    else:
        print(f"❌ {stock_code} 非A股代码，跳过")
        return None

    try:
        # 平衡速度和限流：0.3秒间隔
        time.sleep(0.3)
        
        # 字段配置
        base_fields = "f43,f44,f45,f46,f57,f60,f100"
        auction_fields = f"{base_fields},f84,f85,f86"
        tail_fields = f"{base_fields},f107,f111,f168"
        fields = base_fields
        if data_type == "auction":
            fields = auction_fields
        elif data_type == "tail":
            fields = tail_fields

        # 1. 主接口请求
        if is_trading_day():
            main_url = f"http://push2.eastmoney.com/api/qt/stock/get?secid={secid}&fields={fields}"
        else:
            main_url = f"http://push2his.eastmoney.com/api/qt/stock/kline/get?secid={secid}&klt=101&fqt=1&beg={latest_trading_day}&end={latest_trading_day}"
        
        resp = request_with_retry(main_url)
        if not resp:
            # 2. 备用接口（东方财富另一个稳定数据源）
            backup_url = f"http://quote.eastmoney.com/unify/r/{secid.split('.')[0]}.0.{stock_code}"
            print(f"🔄 主接口失败，尝试备用接口：{backup_url}")
            resp = request_with_retry(backup_url)
            if not resp:
                return None

        data = resp.json().get('data', {})
        if not data:
            print(f"❌ {stock_code} 接口返回空数据")
            return None

        # 解析实时数据
        result = {
            'code': stock_code,
            'name': data.get('f100', data.get('name', stock_code)),
            'price': float(data.get('f43', data.get('price', 0))),
            'pre_close': float(data.get('f60', data.get('pre_close', 1))),
            'high': float(data.get('f44', data.get('high', 0))),
            'low': float(data.get('f45', data.get('low', 0))),
            'volume': float(data.get('f46', data.get('volume', 0))),
            'pct_chg': round((float(data.get('f43', data.get('price', 0))) - float(data.get('f60', data.get('pre_close', 1)))) / float(data.get('f60', data.get('pre_close', 1))) * 100, 2)
        }

        # 补充竞价字段
        if data_type == "auction":
            result['auction_open'] = float(data.get('f43', data.get('open', 0)))
            result['auction_vol'] = float(data.get('f84', data.get('auction_vol', 0)))
            result['auction_pct'] = round((float(data.get('f43', data.get('open', 0))) - float(data.get('f60', data.get('pre_close', 1)))) / float(data.get('f60', data.get('pre_close', 1))) * 100, 2)
        
        # 补充尾盘字段
        if data_type == "tail":
            result['turnover'] = float(data.get('f107', data.get('turnover', 0)))
            result['amount'] = float(data.get('f111', data.get('amount', 0))) / 10000
            result['vol_ratio'] = float(data.get('f168', data.get('vol_ratio', 1.0)))

        # 历史数据兜底
        if not is_trading_day() and 'klines' in data and len(data['klines']) > 0:
            kline = data['klines'][0].split(',')
            result.update({
                'price': float(kline[4]),
                'pre_close': float(kline[2]),
                'high': float(kline[3]),
                'low': float(kline[5]),
                'volume': float(kline[8]),
                'pct_chg': round((float(kline[4]) - float(kline[2])) / float(kline[2]) * 100, 2),
                'auction_open': float(kline[1]),
                'auction_vol': float(kline[8]) * 0.1,
                'auction_pct': round((float(kline[1]) - float(kline[2])) / float(kline[2]) * 100, 2),
                'turnover': float(kline[9]) / 100 if len(kline) > 9 else 0,
                'vol_ratio': 1.5
            })

        return result
    except Exception as e:
        print(f"❌ {stock_code} {data_type}数据解析失败: {str(e)[:60]}")
        return None

# ====================== 7. 早盘分析（9:27触发，9:29出结果） ======================
def morning_analysis():
    """早盘竞价分析 + 即时微信推送"""
    morning_stocks = []
    print("\n🌅 开始早盘竞价分析...")

    for code in STOCK_POOL:
        try:
            auc_data = get_eastmoney_data(code, "auction")
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
    print("\n🌅 【早盘即时选股结果】（竞价可买入）")
    wechat_content = f"🌅 早盘即时选股结果（{latest_trading_day}）\n（9:25-9:30竞价买入，不破支撑位持有）\n"
    if morning_stocks:
        for i, stock in enumerate(morning_stocks[:3], 1):
            line = f"{i}. {stock['name']}（{stock['code']}）\n  竞价高开：{stock['auction_pct']}% | 支撑位：{stock['support_price']}元 | 目标价：{stock['target_price']}元"
            print(line)
            wechat_content += line + "\n"
    else:
        wechat_content = f"🌅 早盘即时选股结果（{latest_trading_day}）\n⚠️ 暂无符合条件的早盘标的"
        print(wechat_content)
    
    send_wechat_message(wechat_content)
    return morning_stocks

# ====================== 8. 持仓分析 ======================
def hold_analysis():
    """持仓股票实时分析"""
    hold_suggestions = []
    print("\n📈 开始持仓分析...")

    for code in HOLD_STOCK_LIST:
        try:
            rt_data = get_eastmoney_data(code, "realtime")
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

# ====================== 9. 尾盘分析（14:52触发，14:54出结果） ======================
def tail_analysis():
    """尾盘分析 + 即时微信推送"""
    tail_stocks = []
    print("\n🔥 开始尾盘分析...")

    for code in STOCK_POOL:
        try:
            tail_data = get_eastmoney_data(code, "tail")
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
    print("\n🔥 【尾盘即时选股结果】（可直接买入）")
    wechat_content = f"🔥 尾盘即时选股结果（{latest_trading_day}）\n（14:57-15:00买入，次日冲高止盈）\n"
    if tail_stocks:
        for i, stock in enumerate(tail_stocks[:3], 1):
            line = f"{i}. {stock['name']}（{stock['code']}）\n  尾盘价：{stock['price']}元 | 买入价：{stock['buy_price']}元 | 次日目标：{stock['next_target']}元"
            print(line)
            wechat_content += line + "\n"
    else:
        wechat_content = f"🔥 尾盘即时选股结果（{latest_trading_day}）\n⚠️ 暂无符合条件的尾盘标的"
        print(wechat_content)
    
    send_wechat_message(wechat_content)
    return tail_stocks

# ====================== 10. 生成完整报告 + 推送 ======================
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
        report += "⚠️ 暂无符合条件的早盘标的\n"

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
        report += "⚠️ 暂无持仓数据或持仓分析失败\n"

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
        report += "⚠️ 暂无符合条件的尾盘标的\n"

    # 保存报告
    os.makedirs('reports', exist_ok=True)
    report_path = f"reports/交易分析报告_{latest_trading_day}_{now.strftime('%H%M')}.txt"
    with open(report_path, 'w', encoding='utf-8') as f:
        f.write(report)

    print("\n" + "="*60)
    print("📋 完整分析报告已生成：", report_path)
    print("="*60)

    # 推送完整报告到微信
    send_wechat_message(report, is_report=True)
    return report

# ====================== 11. 主函数 ======================
if __name__ == '__main__':
    print("🚀 开始执行：双模式选股池分析（自选+自动）")
    generate_full_report()
    print(f"\n✅ 所有分析完成！数据日期：{latest_trading_day}")
