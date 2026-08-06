# -*- coding: utf-8 -*-
"""
백그라운드에서 주기적으로 3개 트래커를 실행해 CACHE에 저장.
Flask 라우트는 절대 트래커를 직접 호출하지 않고 이 CACHE만 읽는다.
(요청마다 외부 API를 때리면 페이지 로딩이 느려지고, ADR 대시보드 때 겪었던
 gunicorn 워커 행(hang) 문제도 재발할 수 있어서 반드시 이 패턴을 지킨다.)
"""

import threading
import time
from datetime import datetime, timezone

import gpu_rental_tracker
import stock_returns_tracker
import hyperscaler_tracker

REFRESH_INTERVAL_SECONDS = 30 * 60  # 30분마다 갱신

CACHE = {
    "gpu": [],
    "stocks": [],
    "hyperscaler": [],
    "last_updated": None,
    "last_error": None,
}

_lock = threading.Lock()
_started = False  # gunicorn 멀티 워커 환경에서 워커당 한 번만 스레드 시작하도록 가드


def _refresh_once():
    """3개 트래커를 순서대로 실행해 CACHE를 갱신. 하나가 실패해도 나머지는 계속 진행."""
    gpu_rows = []
    stock_rows = []
    hyper_rows = []
    error_msgs = []

    try:
        gpu_result = gpu_rental_tracker.run()
        gpu_rows = gpu_result.get("vast", []) + gpu_result.get("runpod", [])
    except Exception as e:
        error_msgs.append(f"GPU: {e}")

    try:
        stock_rows = stock_returns_tracker.run()
    except Exception as e:
        error_msgs.append(f"Stock: {e}")

    try:
        hyper_rows = hyperscaler_tracker.run()
    except Exception as e:
        error_msgs.append(f"Hyperscaler: {e}")

    with _lock:
        CACHE["gpu"] = gpu_rows
        CACHE["stocks"] = stock_rows
        CACHE["hyperscaler"] = hyper_rows
        CACHE["last_updated"] = datetime.now(timezone.utc).isoformat()
        CACHE["last_error"] = "; ".join(error_msgs) if error_msgs else None

    print(f"[CACHE] 갱신 완료: gpu={len(gpu_rows)} stocks={len(stock_rows)} "
          f"hyperscaler={len(hyper_rows)} errors={CACHE['last_error']}")


def _refresh_loop():
    while True:
        try:
            _refresh_once()
        except Exception as e:
            print(f"[CACHE] 갱신 루프 예외: {e}")
        time.sleep(REFRESH_INTERVAL_SECONDS)


def start_background_refresh():
    """gunicorn post_fork 훅 또는 로컬 개발 서버 시작 시 호출."""
    global _started
    with _lock:
        if _started:
            return
        _started = True

    # _refresh_loop이 시작하자마자 첫 갱신을 수행하므로 별도 즉시 호출은 하지 않음
    # (중복 동시 실행 방지)
    threading.Thread(target=_refresh_loop, daemon=True).start()


def get_cache():
    with _lock:
        return dict(CACHE)
