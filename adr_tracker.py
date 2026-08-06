# -*- coding: utf-8 -*-
"""
SK하이닉스 ADR(SKHY) vs 본주(000660.KS) 괴리율 계산.
기존 skhynix-adr.onrender.com 앱에서 쓰던, Yahoo Finance chart API를
requests로 직접 호출하는 방식을 그대로 재사용한다 (yfinance의 curl_cffi가
클라우드 환경에서 무한 hang 나는 문제가 있었어서 이 방식으로 정착했었음).

이 트래커는 web_dashboard의 cache_refresh.py에서 30분마다 다른 트래커들과
함께 실행된다 (기존 ADR 전용 앱의 30초 실시간 갱신과는 다른 주기).

이론가치(USD) = (000660 원화가 / ADR_RATIO) / USD-KRW
괴리율(%)    = (SKHY가 - 이론가치) / 이론가치 * 100
"""

import requests

ADR_TICKER = "SKHY"
KRX_TICKER = "000660.KS"
FX_TICKER = "KRW=X"
ADR_RATIO = 10  # ADR 1주 = 본주 1/10주

YF_CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; PersonalDashboard/1.0)"}

CONNECT_TIMEOUT = 4
READ_TIMEOUT = 10
MAX_RETRIES = 1


def _fetch_quote(symbol):
    """Yahoo Finance chart API에서 단일 심볼의 최신가/전일종가를 가져온다."""
    last_err = None
    for attempt in range(MAX_RETRIES + 1):
        try:
            resp = requests.get(
                YF_CHART_URL.format(symbol=symbol),
                headers=HEADERS,
                timeout=(CONNECT_TIMEOUT, READ_TIMEOUT),
            )
            resp.raise_for_status()
            data = resp.json()
            result = data.get("chart", {}).get("result")
            if not result:
                raise ValueError(f"{symbol}: 빈 결과")
            meta = result[0]["meta"]
            price = meta.get("regularMarketPrice")
            if price is None:
                raise ValueError(f"{symbol}: regularMarketPrice 없음")
            return {
                "price": price,
                "prev_close": meta.get("chartPreviousClose") or meta.get("previousClose"),
                "currency": meta.get("currency"),
                "market_time": meta.get("regularMarketTime"),
            }
        except Exception as e:
            last_err = e
    raise RuntimeError(f"{symbol} 조회 실패: {last_err}")


def run():
    """
    반환 형식:
    {
        "adr_price": float, "krx_price": float, "fx_rate": float,
        "theo_price": float, "premium_pct": float,
        "adr_currency": str, "error": None,
    }
    하나라도 실패하면 error 메시지를 담아 반환 (호출부에서 이전 캐시값 유지 처리).
    """
    try:
        adr = _fetch_quote(ADR_TICKER)
        krx = _fetch_quote(KRX_TICKER)
        fx = _fetch_quote(FX_TICKER)

        theo_price = (krx["price"] / ADR_RATIO) / fx["price"]
        premium_pct = (adr["price"] - theo_price) / theo_price * 100.0

        return {
            "adr_price": adr["price"],
            "adr_prev_close": adr["prev_close"],
            "krx_price": krx["price"],
            "krx_prev_close": krx["prev_close"],
            "fx_rate": fx["price"],
            "theo_price": theo_price,
            "premium_pct": premium_pct,
            "error": None,
        }
    except Exception as e:
        return {
            "adr_price": None,
            "adr_prev_close": None,
            "krx_price": None,
            "krx_prev_close": None,
            "fx_rate": None,
            "theo_price": None,
            "premium_pct": None,
            "error": str(e),
        }
