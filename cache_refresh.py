# -*- coding: utf-8 -*-
"""
백그라운드에서 주기적으로 3개 트래커를 실행해 CACHE에 저장.
Flask 라우트는 절대 트래커를 직접 호출하지 않고 이 CACHE만 읽는다.
(요청마다 외부 API를 때리면 페이지 로딩이 느려지고, ADR 대시보드 때 겪었던
 gunicorn 워커 행(hang) 문제도 재발할 수 있어서 반드시 이 패턴을 지킨다.)

2026-08-06 수정:
- socket.setdefaulttimeout()으로 DNS 조회 단계까지 포함한 전역 타임아웃 강제
  (requests의 timeout 파라미터가 DNS resolve 단계에는 적용 안 되는 경우가 있어,
   그 구간에서 완전히 멈추는(hang) 현상이 있었음 - 이게 근본 원인으로 추정됨)
- ThreadPoolExecutor로 각 트래커 실행에 "하드" 타임아웃을 걸어서, 혹시 위 조치로도
  못 막는 형태의 hang이 발생해도 전체 갱신 사이클이 무한정 멈추지 않도록 함
"""

import socket
import threading
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from datetime import datetime, timezone

import gpu_rental_tracker
import stock_returns_tracker
import hyperscaler_tracker

# 전역 소켓 타임아웃: DNS 조회를 포함한 모든 저수준 소켓 작업에 적용된다.
# requests 자체의 timeout 파라미터보다 더 넓은 범위를 커버하는 안전망.
socket.setdefaulttimeout(30)

REFRESH_INTERVAL_SECONDS = 30 * 60  # 30분마다 갱신

# 트래커 하나당 허용하는 최대 실행 시간(초). 이 시간을 넘기면 해당 트래커는
# 실패로 처리하고 다음으로 넘어간다. (개별 요청 타임아웃과 별개의 안전망)
TRACKER_HARD_TIMEOUT = 90

CACHE = {
    "gpu": [],
    "stocks": [],
    "hyperscaler": [],
    "last_updated": None,
    "last_error": None,
}

_lock = threading.Lock()
_started = False  # gunicorn 멀티 워커 환경에서 워커당 한 번만 스레드 시작하도록 가드

# 트래커 함수들을 별도 워커 스레드에서 실행하기 위한 풀.
# max_workers=3이면 GPU/Stock/Hyperscaler가 각자 독립된 스레드에서 돌기 때문에,
# 하나가 hard timeout에 걸려 스레드가 죽지 않고 방치되어도(파이썬은 스레드를
# 강제 종료할 수 없음) 나머지 트래커와 다음 사이클 진행에는 영향을 주지 않는다.
_executor = ThreadPoolExecutor(max_workers=3, thread_name_prefix="tracker")


def _run_with_hard_timeout(label, func):
    """func()을 별도 스레드에서 실행하고, TRACKER_HARD_TIMEOUT 안에 안 끝나면 포기."""
    future = _executor.submit(func)
    try:
        return future.result(timeout=TRACKER_HARD_TIMEOUT), None
    except FutureTimeoutError:
        msg = f"{label}: {TRACKER_HARD_TIMEOUT}초 초과 (hang 의심, 강제 포기)"
        print(f"[CACHE] {msg}")
        return None, msg
    except Exception as e:
        msg = f"{label}: {e}"
        print(f"[CACHE] {msg}")
        return None, msg


def _refresh_once():
    """3개 트래커를 순서대로 실행해 CACHE를 갱신. 하나가 실패/hang이어도 나머지는 계속 진행."""
    gpu_rows = []
    stock_rows = []
    hyper_rows = []
    error_msgs = []

    gpu_result, err = _run_with_hard_timeout("GPU", gpu_rental_tracker.run)
    if err:
        error_msgs.append(err)
    elif gpu_result:
        gpu_rows = gpu_result.get("vast", []) + gpu_result.get("runpod", [])

    stock_result, err = _run_with_hard_timeout("Stock", stock_returns_tracker.run)
    if err:
        error_msgs.append(err)
    elif stock_result:
        stock_rows = stock_result

    hyper_result, err = _run_with_hard_timeout("Hyperscaler", hyperscaler_tracker.run)
    if err:
        error_msgs.append(err)
    elif hyper_result:
        hyper_rows = hyper_result

    with _lock:
        # 새로 받아온 데이터가 있으면 그것으로, 이번에 hang/실패해서 빈 리스트면
        # 기존 캐시값을 그대로 유지한다 (화면이 갑자기 텅 비지 않도록).
        if gpu_rows:
            CACHE["gpu"] = gpu_rows
        if stock_rows:
            CACHE["stocks"] = stock_rows
        if hyper_rows:
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

    threading.Thread(target=_refresh_loop, daemon=True).start()


def get_cache():
    with _lock:
        return dict(CACHE)
