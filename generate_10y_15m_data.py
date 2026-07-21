"""
生成 10 年期 15 分钟 K 线模拟行情数据脚本

格式完全对齐 data/data.csv 包含的所有 16 个原生字段：
security_id, sec_short_name, kline_type, issue_date, trade_date, issue_time,
open_value, high_value, low_value, close_value, cur_volume, cur_amount,
width_percent, up_down_value, price_up_down_pct, ytd_close_value
"""

import os
import random
from datetime import datetime, timedelta

import pandas as pd

# 15分钟 K 线标准交易时间点 (每天 16 条)
TIME_SLOTS = [
    "09:45:00",
    "10:00:00",
    "10:15:00",
    "10:30:00",
    "10:45:00",
    "11:00:00",
    "11:15:00",
    "11:30:00",
    "13:15:00",
    "13:30:00",
    "13:45:00",
    "14:00:00",
    "14:15:00",
    "14:30:00",
    "14:45:00",
    "15:00:00",
]


def generate_10y_15m_market_data(
    security_id: str = "000001.SZ",
    sec_short_name: str = "平安银行",
    start_date_str: str = "2016-07-21",
    end_date_str: str = "2026-07-21",
    initial_price: float = 10.0,
) -> pd.DataFrame:
    """生成 10 年历史 15 分钟 K 线数据"""
    start_dt = datetime.strptime(start_date_str, "%Y-%m-%d")
    end_dt = datetime.strptime(end_date_str, "%Y-%m-%d")

    rows = []
    curr_date = start_dt
    curr_close = initial_price
    prev_day_close = initial_price

    random.seed(42)  # 固定种子确保结果可复现但有自然随机性

    print(f"正在生成 [{security_id}] 从 {start_date_str} 到 {end_date_str} 的 10年15分钟K线数据...")

    day_count = 0

    while curr_date <= end_dt:
        # 跳过周末 (5=周六, 6=周日)
        if curr_date.weekday() < 5:
            date_str = curr_date.strftime("%Y-%m-%d")
            day_open = curr_close

            # 每日开盘跳空/微幅高开低走模拟 (-1.5% 到 +1.5%)
            gap = random.gauss(0, 0.005)
            open_price = max(1.0, round(day_open * (1 + gap), 2))

            bar_price = open_price

            for _idx, slot_time in enumerate(TIME_SLOTS):
                time_str = f"{date_str} {slot_time}"

                # 15分钟价格随机游走波动 (-0.8% 到 +0.8%)
                pct_change = random.gauss(0.0001, 0.003)
                c_val = max(1.0, round(bar_price * (1 + pct_change), 2))
                o_val = round(bar_price, 2)

                # 计算 High 和 Low，确保 High >= max(o, c) 且 Low <= min(o, c)
                max_oc = max(o_val, c_val)
                min_oc = min(o_val, c_val)
                h_val = round(max_oc + abs(random.gauss(0, 0.03)), 2)
                l_val = round(max(0.5, min_oc - abs(random.gauss(0, 0.03))), 2)

                # 成交量与成交额模拟 (结合价格与震幅)
                vol = random.randint(80000, 350000)
                amount = int(vol * ((o_val + c_val) / 2))

                # 振幅与涨跌幅计算
                width_pct = round(((h_val - l_val) / prev_day_close) * 100, 2)
                up_down = round(c_val - prev_day_close, 2)
                price_pct = round((up_down / prev_day_close) * 100, 2)

                row = {
                    "security_id": security_id,
                    "sec_short_name": sec_short_name,
                    "kline_type": 3,
                    "issue_date": date_str,
                    "trade_date": date_str,
                    "issue_time": time_str,
                    "open_value": o_val,
                    "high_value": h_val,
                    "low_value": l_val,
                    "close_value": c_val,
                    "cur_volume": vol,
                    "cur_amount": amount,
                    "width_percent": width_pct,
                    "up_down_value": up_down,
                    "price_up_down_pct": price_pct,
                    "ytd_close_value": prev_day_close,
                }
                rows.append(row)
                bar_price = c_val

            # 当天交易结束，更新昨收价
            prev_day_close = bar_price
            curr_close = bar_price
            day_count += 1

        curr_date += timedelta(days=1)

    df = pd.DataFrame(rows)
    print(f"数据生成完成！共包含 {len(df)} 行记录 (涵盖约 {day_count} 个交易日)。")
    return df


def main():
    target_file = "data/data.csv"
    os.makedirs(os.path.dirname(os.path.abspath(target_file)), exist_ok=True)

    df = generate_10y_15m_market_data(
        security_id="000001.SZ",
        sec_short_name="平安银行",
        start_date_str="2016-07-21",
        end_date_str="2026-07-21",
        initial_price=10.0,
    )

    # 保存为 UTF-8 BOM 编码，支持 Excel 直接打开且符合标准 CSV 读取
    df.to_csv(target_file, encoding="utf-8-sig", index=False)
    print(f"成功将 10 年 15分钟行情数据写入: {os.path.abspath(target_file)}")


if __name__ == "__main__":
    main()
