import os
import requests
import pandas as pd
from datetime import datetime
import time

# ====================== 1. 初始化配置 ======================
# 设置时区为北京时间
os.environ['TZ'] = 'Asia/Shanghai'
try:
    time.tzset()
except:
    pass

# 固定关注股票列表（从GitHub Secrets读取）
FIXED_STOCK_LIST = os.getenv('FIXED_STOCK_LIST', '').split(',')
FIXED_STOCK_LIST = [code.strip() for code in FIXED_STOCK_LIST if code.strip()]

# ====================== 2. 东方财富实时行情接口（0成本） ======================
def get_eastmoney_realtime(stock_code):
    """
    获取单只股票实时数据（延迟1-3分钟，完全免费）
    :param stock_code: 股票代码，如 '600519'
    :return: 实时数据字典 / None
    """
    # 代码格式转换：60开头→sh，00/30开头→sz
    if stock_code.startswith('60'):
        secid = f"1.{stock_code}"
    elif stock_code.startswith('00') or stock_code.startswith('30'):
        secid = f"0.{stock_code}"
    else:
        print(f"❌ {stock_code} 代码格式错误")
        return None

    try:
        # 东方财富免费API（无调用限制）
        url = f"http://push2.eastmoney.com/api/qt/stock/get?secid={secid}&fields=f43,f44,f45,f46,f57,f60,f100"
        resp = requests.get(url, timeout=5)
        data = resp.json()['data']

        # 整理核心数据
        realtime_data = {
            'code': stock_code,
            'name': data['f100'],          # 股票名称
            'price': float(data['f43']),   # 最新价
            'pre_close': float(data['f60']), # 昨收价
            'high': float(data['f44']),    # 最高价
            'low': float(data['f45']),     # 最低价
            'volume': float(data['f46']),  # 成交量（手）
            'pct_chg': round((float(data['f43']) - float(data['f60'])) / float(data['f60']) * 100, 2) # 涨跌幅
        }
        return realtime_data
    except Exception as e:
        print(f"❌ {stock_code} 实时数据获取失败：{str(e)[:50]}")
        return None

# ====================== 3. 实时选股逻辑（短线强势股） ======================
def select_eastmoney_stocks():
    """
    选股条件（适配短线交易）：
    1. 涨幅 0-5%（不追高）
    2. 成交量 > 1万手（活跃度）
    3. 股价 > 昨收价（上涨趋势）
    4. 未到日内最高价（有上涨空间）
    """
    selected_stocks = []
    # 短线活跃股票池（可自定义，替换成你关注的股票）
    stock_pool = ['600519', '000858', '300750', '002594', '601012', '600036', '000333']

    for code in stock_pool:
        rt_data = get_eastmoney_realtime(code)
        if not rt_data:
            continue

        # 筛选条件
        cond1 = 0 < rt_data['pct_chg'] < 5          # 涨幅合理
        cond2 = rt_data['volume'] > 10000           # 成交量活跃
        cond3 = rt_data['price'] > rt_data['pre_close'] # 上涨趋势
        cond4 = rt_data['price'] < rt_data['high'] * 0.98 # 未到高点

        if all([cond1, cond2, cond3, cond4]):
            selected_stocks.append(rt_data)

    # 按涨幅排序，取前5只
    selected_stocks = sorted(selected_stocks, key=lambda x: x['pct_chg'], reverse=True)[:5]
    return selected_stocks

# ====================== 4. 生成分版面报告（固定票+选股结果） ======================
def generate_split_report():
    """生成清晰的分版面报告"""
    # 报告头部
    report = f"""
=====================================
📅 东方财富实时选股报告（{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}）
=====================================

【一、核心持仓分析（固定关注）】
"""
    # 分析固定股票
    if FIXED_STOCK_LIST:
        for code in FIXED_STOCK_LIST:
            rt_data = get_eastmoney_realtime(code)
            if rt_data:
                report += f"""
【{rt_data['name']}（{rt_data['code']}）】
实时价：{rt_data['price']} 元 | 涨跌幅：{rt_data['pct_chg']}%
成交量：{rt_data['volume']} 手
支撑位：{round(rt_data['low'], 2)} 元 | 压力位：{round(rt_data['high'], 2)} 元
短线建议：{'✅ 持有' if rt_data['pct_chg'] > 0 else '⚠️ 观望'}
"""
            else:
                report += f"⚠️ {code}：无法获取实时数据\n"
    else:
        report += "⚠️ 未配置固定关注股票\n"

    # 分隔线 + 选股结果
    report += """
-------------------------------------
【二、今日实时短线标的（东方财富精选）】
"""
    # 实时选股结果
    selected_stocks = select_eastmoney_stocks()
    if selected_stocks:
        for i, stock in enumerate(selected_stocks, 1):
            stop_loss = round(stock['price'] * 0.97, 2)  # 3%止损
            take_profit = round(stock['price'] * 1.05, 2) # 5%止盈
            report += f"""
【{i}. {stock['name']}（{stock['code']}）】
实时价：{stock['price']} 元 | 涨跌幅：{stock['pct_chg']}%
成交量：{stock['volume']} 手
操作建议：回调至{round(stock['price']*0.99, 2)}元建仓，止损{stop_loss}元，止盈{take_profit}元
"""
    else:
        report += "⚠️ 暂无符合条件的实时短线标的\n"

    # 保存报告到文件
    os.makedirs('reports', exist_ok=True)
    report_path = f"reports/东方财富实时选股报告_{datetime.now().strftime('%Y%m%d_%H%M')}.txt"
    with open(report_path, 'w', encoding='utf-8') as f:
        f.write(report)

    # 打印报告（方便GitHub日志查看）
    print(report)
    return report

# ====================== 5. 主函数（程序入口） ======================
if __name__ == '__main__':
    print("🚀 开始执行：东方财富实时选股 + 分版面分析")
    final_report = generate_split_report()
    print("✅ 分版面报告已生成！")
