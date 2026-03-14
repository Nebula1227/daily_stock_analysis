import os
import requests
import pandas as pd
from datetime import datetime, timedelta
import time

# ====================== 1. 基础配置 ======================
os.environ['TZ'] = 'Asia/Shanghai'
try:
    time.tzset()
except:
    pass

# 从环境变量读取关注/持仓股票
HOLD_STOCK_LIST = os.getenv('HOLD_STOCK_LIST', '').split(',')
HOLD_STOCK_LIST = [code.strip() for code in HOLD_STOCK_LIST if code.strip()]

# ====================== 2. 东方财富API核心函数 ======================
def get_eastmoney_data(stock_code, data_type="realtime"):
    """
    统一获取股票数据：实时/竞价/尾盘
    :param stock_code: 股票代码（如600519）
    :param data_type: realtime(实时)/auction(竞价)/tail(尾盘)
    :return: 数据字典
    """
    if stock_code.startswith('60'):
        secid = f"1.{stock_code}"
    elif stock_code.startswith('00') or stock_code.startswith('30'):
        secid = f"0.{stock_code}"
    else:
        return None

    try:
        # 基础字段：实时价/昨收/最高/最低/成交量/名称
        base_fields = "f43,f44,f45,f46,f57,f60,f100"
        # 竞价字段：竞价量/竞价额/开盘价
        auction_fields = f"{base_fields},f84,f85,f86"
        # 尾盘字段：换手率/成交额/量比
        tail_fields = f"{base_fields},f107,f111,f168"

        if data_type == "auction":
            fields = auction_fields
        elif data_type == "tail":
            fields = tail_fields
        else:
            fields = base_fields

        url = f"http://push2.eastmoney.com/api/qt/stock/get?secid={secid}&fields={fields}"
        resp = requests.get(url, timeout=5)
        data = resp.json()['data']

        # 基础数据
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

        # 竞价数据
        if data_type == "auction" and 'f84' in data:
            result['auction_open'] = float(data['f43'])  # 竞价开盘价
            result['auction_vol'] = float(data['f84'])   # 竞价量（手）
            result['auction_pct'] = round((float(data['f43']) - float(data['f60'])) / float(data['f60']) * 100, 2)  # 竞价涨幅

        # 尾盘数据
        if data_type == "tail" and 'f107' in data:
            result['turnover'] = float(data['f107'])     # 换手率（%）
            result['amount'] = float(data['f111']) / 10000  # 成交额（万元）
            result['vol_ratio'] = float(data['f168'])    # 量比

        return result
    except Exception as e:
        print(f"❌ {stock_code} {data_type}数据获取失败: {str(e)[:50]}")
        return None

# ====================== 3. 模块1：早盘分析（竞价买入，当日大涨不回落） ======================
def morning_analysis():
    """
    早盘选股逻辑（抓当日大涨票）：
    1. 竞价高开3%-8%（强势但不极端）
    2. 竞价量≥昨日成交量的5%（资金抢筹）
    3. 竞价额≥1000万（大资金介入）
    4. 开盘5分钟不破开盘价（不回落）
    """
    morning_stocks = []
    # 早盘强势股池（可自定义）
    stock_pool = ['600519', '000858', '300750', '002594', '601012', '600036', '000333', '601899']

    for code in stock_pool:
        # 获取竞价数据
        auc_data = get_eastmoney_data(code, "auction")
        if not auc_data:
            continue

        # 核心条件（筛选当日大涨票）
        cond1 = 3 < auc_data['auction_pct'] < 8          # 竞价高开3%-8%
        cond2 = auc_data['auction_vol'] > 5000           # 竞价量≥5000手
        cond3 = auc_data['volume'] > auc_data['auction_vol'] * 1.2  # 开盘放量
        cond4 = auc_data['price'] > auc_data['auction_open'] * 0.99 # 不破开盘价

        if all([cond1, cond2, cond3, cond4]):
            # 预估当日目标价（涨停价/压力位）
            target_price = round(auc_data['pre_close'] * 1.1, 2)
            morning_stocks.append({
                **auc_data,
                'target_price': target_price,
                'support_price': round(auc_data['auction_open'] * 0.98, 2)  # 支撑位
            })

    # 按竞价量排序，取前3只
    return sorted(morning_stocks, key=lambda x: x['auction_vol'], reverse=True)[:3]

# ====================== 4. 模块2：持仓/关注票操作建议 ======================
def hold_analysis():
    """
    持仓票操作建议：
    - 上涨趋势：持有/加仓
    - 震荡趋势：持有/做T
    - 下跌趋势：减仓/止损
    """
    hold_suggestions = []

    for code in HOLD_STOCK_LIST:
        rt_data = get_eastmoney_data(code, "realtime")
        if not rt_data:
            continue

        # 趋势判断
        if rt_data['pct_chg'] > 3:
            trend = "强势上涨"
            suggestion = "✅ 持有，不破5日线不卖"
        elif 0 < rt_data['pct_chg'] <= 3:
            trend = "震荡上涨"
            suggestion = "✅ 持有，可小仓位加仓"
        elif -3 < rt_data['pct_chg'] <= 0:
            trend = "震荡调整"
            suggestion = "⚠️ 观望，做T（高抛低吸）"
        else:
            trend = "弱势下跌"
            suggestion = "❌ 减仓，止损位：" + str(round(rt_data['pre_close'] * 0.97, 2))

        hold_suggestions.append({
            **rt_data,
            'trend': trend,
            'suggestion': suggestion
        })
    return hold_suggestions

# ====================== 5. 模块3：尾盘分析（买入次日大涨/涨停） ======================
def tail_analysis():
    """
    尾盘选股逻辑（抓次日涨停票）：
    1. 尾盘涨幅1%-5%（不追高）
    2. 换手率5%-15%（活跃但不换手过高）
    3. 量比≥1.5（放量）
    4. 尾盘资金净流入（收盘价靠近最高价）
    """
    tail_stocks = []
    stock_pool = ['600519', '000858', '300750', '002594', '601012', '600036', '000333', '601899']

    for code in stock_pool:
        tail_data = get_eastmoney_data(code, "tail")
        if not tail_data:
            continue

        # 核心条件（筛选次日涨停票）
        cond1 = 1 < tail_data['pct_chg'] < 5             # 尾盘涨幅1%-5%
        cond2 = 5 < tail_data['turnover'] < 15           # 换手率5%-15%
        cond3 = tail_data['vol_ratio'] >= 1.5            # 量比≥1.5
        cond4 = tail_data['price'] > tail_data['high'] * 0.98  # 收盘价靠近最高价

        if all([cond1, cond2, cond3, cond4]):
            # 预估次日目标价（涨停价）
            next_target = round(tail_data['pre_close'] * 1.1, 2)
            tail_stocks.append({
                **tail_data,
                'next_target': next_target,
                'buy_price': round(tail_data['price'] * 1.01, 2)  # 尾盘买入价
            })

    # 按换手率排序，取前3只
    return sorted(tail_stocks, key=lambda x: x['turnover'], reverse=True)[:3]

# ====================== 6. 生成三模块完整报告 ======================
def generate_full_report():
    """生成早盘+持仓+尾盘三模块报告"""
    now = datetime.now()
    report = f"""
=====================================
📅 短线交易三模块分析报告（{now.strftime('%Y-%m-%d %H:%M:%S')}）
=====================================

【一、早盘分析（竞价买入，当日大涨不回落）】
"""
    # 早盘模块
    morning_stocks = morning_analysis()
    if morning_stocks:
        for i, stock in enumerate(morning_stocks, 1):
            report += f"""
【{i}. {stock['name']}（{stock['code']}）】
竞价高开：{stock['auction_pct']}% | 竞价量：{stock['auction_vol']}手
开盘价：{stock['auction_open']}元 | 当前价：{stock['price']}元
当日目标价：{stock['target_price']}元（涨停）| 支撑位：{stock['support_price']}元
操作建议：✅ 竞价买入，不破支撑位持有，冲击目标价止盈
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
        report += "⚠️ 未配置持仓/关注股票\n"

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
换手率：{stock['turnover']}% | 量比：{stock['vol_ratio']}
次日目标价：{stock['next_target']}元（涨停）| 买入价：{stock['buy_price']}元
操作建议：✅ 尾盘集合竞价买入，次日冲高止盈
"""
    else:
        report += "⚠️ 暂无符合条件的尾盘涨停标的\n"

    # 保存报告
    os.makedirs('reports', exist_ok=True)
    report_path = f"reports/三模块交易报告_{now.strftime('%Y%m%d_%H%M')}.txt"
    with open(report_path, 'w', encoding='utf-8') as f:
        f.write(report)

    print(report)
    return report

# ====================== 7. 主函数 ======================
if __name__ == '__main__':
    print("🚀 开始执行：早盘+持仓+尾盘 三模块分析")
    generate_full_report()
    print("✅ 三模块交易报告已生成！")
