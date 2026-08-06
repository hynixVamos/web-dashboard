# -*- coding: utf-8 -*-
"""
3. 주가수익률 자동 트래커
- Yahoo Finance chart API를 직접 호출 (yfinance 라이브러리 대신 requests 직접 호출 -
  SK하이닉스 ADR 대시보드에서 이미 검증된, gunicorn/서버 환경에서 안정적인 방식)
- YTD / 3M / 1M / 1W / 1D 수익률 계산
"""

import time
from datetime import datetime, timezone

import requests

from config import STOCK_UNIVERSE, RETURN_WINDOWS

CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
TIMEOUT = 15


def fetch_daily_closes(ticker: str, range_="2y"):
    """
    지정 티커의 일별 종가 시계열을 (timestamp, close) 튜플 리스트로 반환.
    range를 2y로 잡는 이유: 연초(YTD 기준일) 데이터까지 안전하게 포함하기 위함.
    """
    params = {"range": range_, "interval": "1d"}
    url = CHART_URL.format(ticker=ticker)

    try:
        resp = requests.get(url, headers=HEADERS, params=params, timeout=TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        print(f"[STOCK] {ticker} 조회 실패: {e}")
        return []

    try:
        result = data["chart"]["result"][0]
        timestamps = result["timestamp"]
        closes = result["indicators"]["quote"][0]["close"]
    except (KeyError, IndexError, TypeError):
        print(f"[STOCK] {ticker} 응답 파싱 실패")
        return []

    series = [(ts, c) for ts, c in zip(timestamps, closes) if c is not None]
    series.sort(key=lambda x: x[0])
    return series


def _closest_close_on_or_before(series, target_ts):
    """target_ts 이전(또는 당일)의 가장 가까운 종가를 찾는다."""
    candidates = [pt for pt in series if pt[0] <= target_ts]
    if not candidates:
        return None
    return candidates[-1]


def compute_returns(series):
    """series(최신순 아님, 오름차순)로부터 1D/1W/1M/3M/YTD 수익률(%) 계산."""
    if len(series) < 2:
        return {}

    latest_ts, latest_close = series[-1]
    now = datetime.fromtimestamp(latest_ts, tz=timezone.utc)

    targets = {
        "1D": series[-2][0] if len(series) >= 2 else None,
        "1W": (now.timestamp() - 7 * 86400),
        "1M": (now.timestamp() - 30 * 86400),
        "3M": (now.timestamp() - 91 * 86400),
        "YTD": datetime(now.year, 1, 1, tzinfo=timezone.utc).timestamp(),
    }

    returns = {}
    for window, target_ts in targets.items():
        if target_ts is None:
            continue
        base_point = _closest_close_on_or_before(series, target_ts)
        if base_point is None or base_point[1] == 0:
            returns[window] = None
            continue
        base_close = base_point[1]
        pct = (latest_close - base_close) / base_close * 100
        returns[window] = round(pct, 2)

    returns["latest_close"] = round(latest_close, 4)
    returns["latest_date"] = now.strftime("%Y-%m-%d")
    return returns


def run():
    """전체 유니버스에 대해 수익률을 계산하고 리스트로 반환."""
    rows = []
    for stock in STOCK_UNIVERSE:
        ticker = stock["ticker"]
        name = stock["name"]

        series = fetch_daily_closes(ticker)
        if not series:
            rows.append({"ticker": ticker, "name": name, "error": "data_fetch_failed"})
            continue

        rets = compute_returns(series)
        row = {"ticker": ticker, "name": name, **rets}
        rows.append(row)

        time.sleep(0.5)  # Yahoo 레이트리밋 방지용 딜레이

    print(f"[STOCK] {len(rows)}개 종목 수익률 계산 완료")
    return rows


if __name__ == "__main__":
    for r in run():
        print(r)
