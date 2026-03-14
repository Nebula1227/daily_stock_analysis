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

# ====================== 2. 微信推送核心函数（即时指令+完整报告） ======================
def send_wechat_message(content, is_report=False):
    """
    发送微信消息（企业微信/微信群机器人）
    :param content: 推送内容
    :param is_report: 是否为完整报告（超长内容适配）
    """
    webhook_url = os.getenv('WECHAT_WEBHOOK_URL', '')
    if not webhook_url:
        print("⚠️ 未配置 WECHAT_WEBHOOK_URL，无法推送微信消息")
        return

    try:
        # 完整报告超长，拆分推送（微信单条消息限2048字）
        if is_report:
            # 按分隔符拆分报告
            parts = content.split('-------------------------------------')
            # 先推送标题
            send_wechat_message(parts[0].strip())
            # 再推送各模块
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

# ====================== 3. 全自动生成选股池（兜底池=你的持仓 + 防限流） ======================
def auto_generate_stock_pool():
    """
    自动生成高活跃选股池（缩到8只，降低限流风险）：
    - 筛选条件：换手率≥5%、成交量≥5万手、涨幅≥0、排除ST股
    - 兜底逻辑：自动池失败时，用你的持仓股票作为兜底
    """
    auto_pool = []
    latest_day = get_latest_trading_day()
    if not latest_day:
        return HOLD_STOCK_LIST  # 兜底：用持仓
    
    try:
        # 降低请求频率，避免限流
        time.sleep(0.5)
        # 东方财富高换手个股接口（自动筛选符合条件的股票）
        url = f"http://push2.eastmoney.com/api/qt/clist/get?pn=1&pz=20&po=1&np=1&ut=bd1d9ddb04089700cf9c27f6f7426280e&fltt=2&invt=2&fid=f107&fs=m:0+t:6,m:0+t:13,m:0+t:80,m:1+t:2,m:1+t:23&fields=f12,f14,f107,f5,f6,f8&f107=5&f5=0&f8=50000"
        resp = requests.get(url, timeout=15)  # 延长超时时间
        resp.raise_for_status()
        data = resp.json()['data']['diff']
        
        # 提取前8只高活跃股票（进一步降低限流风险）
        for stock in data[:8]:
            code = stock['f12']  # 股票代码
            name = stock['f14']  # 股票名称
            if 'ST' not in name and '*ST' not in name:
                auto_pool.append(code)
        
        print(f"✅ 自动生成选股池（{len(auto_pool)}只）：{auto_pool}")
        # 自动池为空时，用持仓兜底
        return auto_pool if auto_pool else HOLD_STOCK_LIST
    except Exception as e:
        print(f"⚠️ 自动生成选股池失败，使用持仓作为兜底池：{str(e)}")
        return HOLD_STOCK_LIST

# ====================== 4. 基础配置（过滤基金代码 + 防错） ======================
os.environ['TZ'] = 'Asia/Shanghai'
try:
    time.tzset()
except:
    pass

# 读取你的持仓/关注股票（过滤基金代码，只保留60/00/30开头的A股）
HOLD_STOCK_LIST = os.getenv('HOLD_STOCK_LIST', '').split(',')
# 核心修复：过滤非A股代码 + 空值
HOLD_STOCK_LIST = [
    code.strip() for code in HOLD_STOCK_LIST 
    if code.strip() and (code.startswith('60') or code.startswith('00') or code.startswith('30'))
]
print("📌 过滤后的持仓/关注股票（仅A股）：", HOLD_STOCK_LIST)

# 获取最近交易日
latest_trading_day = get_latest_trading_day()
print(f"📅 数据来源日期：{latest_trading_day}")

# 自动生成选股池（核心：兜底=你的持仓，缩到8只）
STOCK_POOL = auto_generate_stock_pool()
# 最终过滤：确保选股池里只有A股代码
STOCK_POOL = [
    code for code in STOCK_POOL 
    if code.startswith('60') or code.startswith('00') or code.startswith('30')
]
print(f"🔍 最终选股池（仅A股）：{STOCK_POOL}")

# ====================== 5. 东方财富API核心函数（低延迟+高稳定） ======================
def get_eastmoney_data(stock_code, data_type="realtime"):
    """统一获取股票数据（实时/竞价/尾盘，非交易日取历史数据）"""
    # 代码格式转换
    if stock_code.startswith('60'):
        secid = f"1.{stock_code}"
    elif stock_code.startswith('00') or stock_code.startswith('30'):
        secid = f"0.{stock_code}"
    else:
        print(f"❌ {stock_code} 非A股代码，跳过")
        return None

    try:
        # 核心平衡：0.2秒sleep（既不慢，也不容易被限流）
        time.sleep(0.2)
        
        # 字段配置
        base_fields = "f43,f44,f45,f46,f57,f60,f100"  # 基础字段
        auction_fields = f"{base_fields},f84,f85,f86"  # 竞价字段
        tail_fields = f"{base_fields},f107,f111,f168"  # 尾盘字段
        fields = base_fields
        
        if data_type == "auction":
            fields = auction_fields
        elif data_type == "tail":
            fields = tail_fields

        # 交易日取实时数据，非交易日取历史数据
        if is_trading_day():
            url = f"http://push2.eastmoney.com/api/qt/stock/get?secid={secid}&fields={fields}"
        else:
            url = f"http://push2his.eastmoney.com/api/qt/stock/kline/get?secid={secid}&klt=101&fqt=1&beg={latest_trading_day}&end={latest_trading_day}"

        resp = requests.get(url, timeout=15)  # 延长超时时间，提高稳定性
        resp.raise_for_status()
        data = resp.json()['data']

        # 处理实时数据
        if is_trading_day():
            result = {
                'code': stock_code,
                'name': data['f100'],
                'price': float(data['f43']),
                'pre_close': float(data['f60']),
                'high': float(data['f44']),
                'low': float(data['f45']),
                'volume': float(data['f46']),
                'pct_chg': round((float(data['f43']) - float(data['f60'])) / float(data['f60']) * 100, 2)
            }
            # 补充竞价字段（空值判断）
            if data_type == "auction":
                result['auction_open'] = float(data.get('f43', 0))
                result['auction_vol'] = float(data.get('f84', 0))
                result['auction_pct'] = round((float(data.get('f43', 0)) - float(data.get('f60', 0))) / float(data.get('f60', 1)), 2)
            # 补充尾盘字段（空值判断）
            if data_type == "tail":
                result['turnover'] = float(data.get('f107', 0))
                result['amount'] = float(data.get('f111', 0)) / 10000
                result['vol_ratio'] = float(data.get('f168', 1.0))
        # 处理历史数据（周六/周日）
        else:
            if 'klines' in data and len(data['klines']) > 0:
                kline = data['klines'][0].split(',')
                result = {
                    'code': stock_code,
                    'name': data.get('name', stock_code),
                    'price': float(kline[4]),  # 收盘价
                    'pre_close': float(kline[2]),  # 昨收价
                    'high': float(kline[3]),  # 最高价
                    'low': float(kline[5]),  # 最低价
                    'volume': float(kline[8]),  # 成交量（手）
                    'pct_chg': round((float(kline[4]) - float(kline[2])) / float(kline[2]) * 100, 2),
                    # 模拟竞价/尾盘字段（空值兜底）
                    'auction_open': float(kline[1]),
                    'auction_vol': float(kline[8]) * 0.1,
                    'auction_pct': round((float(kline[1]) - float(kline[2])) / float(kline[2]) * 100, 2),
                    'turnover': float(kline[9]) / 100 if len(kline) > 9 else 0,
                    'vol_ratio': 1.5
                }
            else:
                print(f"❌ {stock_code} 无历史数据，跳过")
                return None

        return result
    except Exception as e:
        print(f"❌ {stock_code} {data_type}数据获取失败: {str(e)[:60]}")
        return None

# ====================== 6. 模块1：早盘分析（竞价买入 + 即时推送 + 空值防错） ======================
def morning_analysis():
    """早盘选股：抓竞价强势、当日大涨不回落的票 + 即时微信推送"""
    morning_stocks = []
    if not STOCK_POOL:
        print("⚠️ 选股池为空，跳过早盘分析")
        send_wechat_message(f"🌅 早盘即时选股结果（{latest_trading_day}）\n⚠️ 选股池为空，无分析数据")
        return []

    for code in STOCK_POOL:
        try:
            auc_data = get_eastmoney_data(code, "auction")
            if not auc_data:
                continue  # 核心修复：空值直接跳过

            # 选股条件（空值兜底，避免报错）
            cond1 = 3 < auc_data.get('auction_pct', 0) < 8          # 竞价高开3%-8%
            cond2 = auc_data.get('auction_vol', 0) > 5000           # 竞价量≥5000手
            cond3 = auc_data.get('price', 0) > auc_data.get('auction_open', 0) * 0.99  # 不破开盘价
            cond4 = auc_data.get('volume', 0) > auc_data.get('auction_vol', 0) * 1.2   # 开盘放量

            if all([cond1, cond2, cond3, cond4]):
                auc_data['target_price'] = round(auc_data.get('pre_close', 0) * 1.1, 2)  # 当日目标价（涨停）
                auc_data['support_price'] = round(auc_data.get('auction_open', 0) * 0.98, 2)  # 支撑位
                morning_stocks.append(auc_data)
        except Exception as e:
            print(f"⚠️ 跳过早盘选股 {code}：{str(e)}")
            continue

    # ========== 早盘即时结果 + 微信推送 ==========
    print("\n🌅 【早盘即时选股结果】（竞价可买入）")
    wechat_content = f"🌅 早盘即时选股结果（{latest_trading_day}）\n（竞价可买入，不破支撑位持有至大涨）\n"
    if morning_stocks:
        for i, stock in enumerate(morning_stocks[:3], 1):
            line = f"{i}. {stock['name']}（{stock['code']}）\n  竞价高开：{stock['auction_pct']}% | 支撑位：{stock['support_price']}元 | 目标价：{stock['target_price']}元"
            print(line)
            wechat_content += line + "\n"
    else:
        wechat_content = f"🌅 早盘即时选股结果（{latest_trading_day}）\n⚠️ 暂无符合条件的早盘标的"
        print(wechat_content)
    
    # 推送早盘即时指令到微信
    send_wechat_message(wechat_content)
    # ========== 推送结束 ==========

    # 按竞价量排序，取前3只
    return sorted(morning_stocks, key=lambda x: x.get('auction_vol', 0), reverse=True)[:3]

# ====================== 7. 模块2：持仓/关注票操作建议（空值防错） ======================
def hold_analysis():
    """针对你的持仓，给出当日操作建议"""
    hold_suggestions = []
    if not HOLD_STOCK_LIST:
        print("⚠️ 持仓列表为空，跳过持仓分析")
        return []

    for code in HOLD_STOCK_LIST:
        try:
            rt_data = get_eastmoney_data(code, "realtime")
            if not rt_data:
                continue  # 核心修复：空值直接跳过

            # 趋势判断 + 操作建议（空值兜底）
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

# ====================== 8. 模块3：尾盘分析（买入次日大涨 + 即时推送 + 空值防错） ======================
def tail_analysis():
    """尾盘选股：抓尾盘放量、次日大概率大涨/涨停的票 + 即时微信推送"""
    tail_stocks = []
    if not STOCK_POOL:
        print("⚠️ 选股池为空，跳过尾盘分析")
        send_wechat_message(f"🔥 尾盘即时选股结果（{latest_trading_day}）\n⚠️ 选股池为空，无分析数据")
        return []

    for code in STOCK_POOL:
        try:
            tail_data = get_eastmoney_data(code, "tail")
            if not tail_data:
                continue  # 核心修复：空值直接跳过

            # 选股条件（空值兜底，避免报错）
            cond1 = 1 < tail_data.get('pct_chg', 0) < 5             # 尾盘涨幅1%-5%
            cond2 = 5 < tail_data.get('turnover', 0) < 15           # 换手率5%-15%
            cond3 = tail_data.get('vol_ratio', 0) >= 1.5            # 量比≥1.5
            cond4 = tail_data.get('price', 0) > tail_data.get('high', 0) * 0.98  # 收盘价靠近最高价

            if all([cond1, cond2, cond3, cond4]):
                tail_data['next_target'] = round(tail_data.get('pre_close', 0) * 1.1, 2)  # 次日目标价（涨停）
                tail_data['buy_price'] = round(tail_data.get('price', 0) * 1.01, 2)  # 尾盘买入价
                tail_stocks.append(tail_data)
        except Exception as e:
            print(f"⚠️ 跳过尾盘选股 {code}：{str(e)}")
            continue

    # ========== 尾盘即时结果 + 微信推送 ==========
    print("\n🔥 【尾盘即时选股结果】（可直接买入）")
    wechat_content = f"🔥 尾盘即时选股结果（{latest_trading_day}）\n（尾盘买入，次日冲高止盈，目标涨停）\n"
    if tail_stocks:
        for i, stock in enumerate(tail_stocks[:3], 1):
            line = f"{i}. {stock['name']}（{stock['code']}）\n  尾盘价：{stock['price']}元 | 买入价：{stock['buy_price']}元 | 次日目标：{stock['next_target']}元"
            print(line)
            wechat_content += line + "\n"
    else:
        wechat_content = f"🔥 尾盘即时选股结果（{latest_trading_day}）\n⚠️ 暂无符合条件的尾盘标的"
        print(wechat_content)
    
    # 推送尾盘即时指令到微信
    send_wechat_message(wechat_content)
    # ========== 推送结束 ==========

    # 按换手率排序，取前3只
    return sorted(tail_stocks, key=lambda x: x.get('turnover', 0), reverse=True)[:3]

# ====================== 9. 生成三模块完整报告 + 报告推送 ======================
def generate_full_report():
    """生成「早盘+持仓+尾盘」三模块分析报告 + 微信推送完整报告"""
    now = datetime.now()
    report = f"""
=====================================
📅 短线交易三模块分析报告（{now.strftime('%Y-%m-%d %H:%M:%S')}）
📌 数据来源：{latest_trading_day}（最近交易日）
=====================================

【一、早盘分析（竞价买入，当日大涨不回落）】
"""
    # 早盘模块
    morning_stocks = morning_analysis()
    if morning_stocks:
        for i, stock in enumerate(morning_stocks, 1):
            report += f"""
【{i}. {stock['name']}（{stock['code']}）】
竞价高开：{stock['auction_pct']}% | 竞价量：{stock['auction_vol']:.0f}手
开盘价：{stock['auction_open']}元 | 当前价：{stock['price']}元
当日目标价：{stock['target_price']}元 | 支撑位：{stock['support_price']}元
操作建议：✅ 竞价买入，不破支撑位持有至大涨
"""
    else:
        report += "⚠️ 暂无符合条件的早盘大涨标的\n"

    # 持仓模块
    report += """
-------------------------------------
【二、持仓/关注票今日操作建议】
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
        report += "⚠️ 暂无持仓/关注股票数据\n"

    # 尾盘模块
    report += """
-------------------------------------
【三、尾盘分析（买入次日大涨/涨停）】
"""
    tail_stocks = tail_analysis()
    if tail_stocks:
        for i, stock in enumerate(tail_stocks, 1):
            report += f"""
【{i}. {stock['name']}（{stock['code']}）】
尾盘价：{stock['price']}元 | 涨跌幅：{stock['pct_chg']}%
换手率：{stock['turnover']:.1f}% | 量比：{stock['vol_ratio']:.1f}
次日目标价：{stock['next_target']}元 | 买入价：{stock['buy_price']}元
操作建议：✅ 尾盘买入，次日冲高止盈（目标涨停）
"""
    else:
        report += "⚠️ 暂无符合条件的尾盘涨停标的\n"

    # 保存报告
    os.makedirs('reports', exist_ok=True)
    report_path = f"reports/三模块交易报告_{latest_trading_day}_{now.strftime('%H%M')}.txt"
    with open(report_path, 'w', encoding='utf-8') as f:
        f.write(report)

    print("\n" + "="*50)
    print("📋 完整分析报告已生成")
    print("="*50)
    print(report)

    # ========== 推送完整报告到微信 ==========
    send_wechat_message(report, is_report=True)

    return report

# ====================== 10. 主函数 ======================
if __name__ == '__main__':
    print("🚀 开始执行：早盘+持仓+尾盘 三模块分析")
    generate_full_report()
    print(f"✅ 三模块交易报告已生成（数据日期：{latest_trading_day}）！")
