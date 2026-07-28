import csv
from datetime import datetime, timedelta
from pathlib import Path

EXPECTED_FIELDS = [
    "security_id",
    "sec_short_name",
    "kline_type",
    "price_source",
    "issue_date",
    "issue_time",
    "open_yield",
    "high_yield",
    "low_yield",
    "close_yield",
    "ytd_close_yield",
    "open_net_price",
    "high_net_price",
    "low_net_price",
    "close_net_price",
    "tkn_trade_num",
    "gvn_trade_num",
    "trd_trade_num",
    "trade_num",
    "data_source_id",
]
FIXTURE = Path(__file__).resolve().parents[1] / "data" / "test_data.csv"


def test_built_in_market_data_fixture_uses_bond_bars_v1_contract() -> None:
    assert FIXTURE.read_bytes().startswith(b"\xef\xbb\xbf")
    with FIXTURE.open("r", encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        rows = list(reader)

    assert reader.fieldnames == EXPECTED_FIELDS
    assert len(rows) >= 120
    assert {row["security_id"] for row in rows} == {"240004.IB"}
    assert {row["kline_type"] for row in rows} == {"1"}

    issue_times = [datetime.fromisoformat(row["issue_time"]) for row in rows]
    assert issue_times == sorted(issue_times)
    assert all(item.utcoffset() == timedelta(hours=8) for item in issue_times)
    assert all(
        row["issue_date"] == item.date().isoformat()
        for row, item in zip(rows, issue_times, strict=True)
    )
    assert all(float(row["open_net_price"]) > 0 for row in rows)
    assert len({row["close_yield"] for row in rows}) > 1
    assert len({row["close_net_price"] for row in rows}) > 1
