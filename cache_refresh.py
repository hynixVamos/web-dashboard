# -*- coding: utf-8 -*-
"""
백그라운드에서 주기적으로 3개 트래커를 실행해 CACHE에 저장.
Flask 라우트는 절대 트래커를 직접 호출하지 않고 이 CACHE만 읽는다.

2026-08-06 수정 이력:
1차: connect/read timeout 분리 (각 트래커 파일) - 효과 있었으나 최악의 경우
     누적 대기시간이 여전히 길었음 (주가 12종목 순차 조회 등)
2차: ThreadPoolExecutor 기반 워치독 추가 시도 -> gunicorn fork 이후 스레드풀이
     불안정해지는 것으로 의심되어 원인 파악 전 롤백. (ADR 대시보드 때 겪은
     fork+threading 문제와 같은 계열일 가능성이 높아 단순 구조로 되돌림)
3차(현재): 워치독 제거, 순차 실행 유지하되 -
     - 재시도 횟수를 줄이고 read timeout을 더 짧게 잡아 최악의 경우 누적 시간을 단축
     - print(..., flush=True)로 즉시 로그가 보이도록 강제
       (stdout이 파이프로 리다이렉트되면 기본적으로 블록 버퍼링되어, 실제로는
        진행되고 있어도 로그가 한참 뒤에 몰아서 찍히는 것처럼 보일 수 있었음)
"""

import socket
import sys
import threading
import time
from datetime import datetime, timezone

# --- IPv4 강제 패치 ---
# 일부 클라우드 컨테이너 환경(Render 포함 가능성)에서 외부 접속 시 IPv6 경로가
# 먼저 시도되는데, 그 경로가 "응답 없이 조용히 막혀있는(블랙홀)" 상태이면
# 소켓 connect 시도가 설정한 timeout을 넘어서까지 비정상적으로 오래 걸리거나
# 사실상 멈춘 것처럼 보일 수 있다. urllib3의 주소 확인 함수를 패치해서
# IPv4만 쓰도록 강제해, 이 클래스의 문제를 원천 차단한다.
try:
    import urllib3.util.connection as _urllib3_cn

    def _allowed_gai_family():
        return socket.AF_INET  # IPv4만 사용

    _urllib3_cn.allowed_gai_family = _allowed_gai_family
    print("[CACHE] IPv4 강제 패치 적용됨", flush=True)
except Exception as _e:
    print(f"[CACHE] IPv4 강제 패치 실패 (무시하고 진행): {_e}", flush=True)

import gpu_rental_tracker
import stock_returns_tracker
import hyperscaler_tracker

# DNS 조회 단계까지 포함한 전역 소켓 타임아웃 안전망.
# (ThreadPoolExecutor 기반 워치독과 달리 스레드/락을 새로 만들지 않는 단순한
#  한 줄짜리 설정이라 gunicorn fork와 충돌할 위험이 없다.)
socket.setdefaulttimeout(20)

REFRESH_INTERVAL_SECONDS = 30 * 60  # 30분마다 갱신

CACHE = {
    "gpu": [],
    "stocks": [],
    "hyperscaler": [],
    "last_updated": None,
    "last_error": None,
    "refreshing": False,  # 지금 갱신이 진행 중인지 (디버깅/health 노출용)
}

_lock = threading.Lock()
_started = False  # gunicorn 멀티 워커 환경에서 워커당 한 번만 스레드 시작하도록 가드


def _log(msg):
    print(msg, flush=True)


def _refresh_once():
    """3개 트래커를 순서대로 실행해 CACHE를 갱신. 하나가 실패해도 나머지는 계속 진행."""
    with _lock:
        CACHE["refreshing"] = True
    _log(f"[CACHE][DIAG] _refresh_once 시작, CACHE 객체 id={id(CACHE)}, 모듈 파일={__file__}")

    t0 = time.time()
    gpu_rows = []
    stock_rows = []
    hyper_rows = []
    error_msgs = []

    try:
        gpu_result = gpu_rental_tracker.run()
        gpu_rows = gpu_result.get("vast", []) + gpu_result.get("runpod", [])
    except Exception as e:
        error_msgs.append(f"GPU: {e}")
        _log(f"[CACHE] GPU 트래커 예외: {e}")

    try:
        stock_rows = stock_returns_tracker.run()
    except Exception as e:
        error_msgs.append(f"Stock: {e}")
        _log(f"[CACHE] Stock 트래커 예외: {e}")

    try:
        hyper_rows = hyperscaler_tracker.run()
    except Exception as e:
        error_msgs.append(f"Hyperscaler: {e}")
        _log(f"[CACHE] Hyperscaler 트래커 예외: {e}")

    with _lock:
        # 이번에 데이터를 못 받아왔으면(빈 리스트) 직전 캐시값을 유지해서
        # 화면이 갑자기 텅 비지 않게 한다.
        if gpu_rows:
            CACHE["gpu"] = gpu_rows
        if stock_rows:
            CACHE["stocks"] = stock_rows
        if hyper_rows:
            CACHE["hyperscaler"] = hyper_rows
        CACHE["last_updated"] = datetime.now(timezone.utc).isoformat()
        CACHE["last_error"] = "; ".join(error_msgs) if error_msgs else None
        CACHE["refreshing"] = False
        # 진단용: 이 순간 실제로 CACHE 안에 몇 개가 들어갔는지 그 자리에서 바로 확인
        _diag_gpu_len_after_write = len(CACHE["gpu"])
        _diag_stock_len_after_write = len(CACHE["stocks"])

    elapsed = round(time.time() - t0, 1)
    _log(f"[CACHE] 갱신 완료 ({elapsed}초 소요): gpu={len(gpu_rows)} stocks={len(stock_rows)} "
         f"hyperscaler={len(hyper_rows)} errors={CACHE['last_error']}")
    _log(f"[CACHE][DIAG] 쓰기 직후 CACHE 내부 실측: gpu={_diag_gpu_len_after_write} "
         f"stocks={_diag_stock_len_after_write} (CACHE id={id(CACHE)})")


def _refresh_loop():
    while True:
        try:
            _refresh_once()
        except Exception as e:
            _log(f"[CACHE] 갱신 루프 예외: {e}")
            with _lock:
                CACHE["refreshing"] = False
        time.sleep(REFRESH_INTERVAL_SECONDS)


def start_background_refresh():
    """gunicorn post_fork 훅 또는 로컬 개발 서버 시작 시 호출."""
    global _started
    with _lock:
        if _started:
            return
        _started = True

    _log("[CACHE] 백그라운드 갱신 스레드 시작 준비")
    threading.Thread(target=_refresh_loop, daemon=True).start()


def get_cache():
    with _lock:
        result = dict(CACHE)
    return result


def diag_info():
    """진단용: 이 요청을 처리 중인 프로세스에서 CACHE 객체의 정체성을 확인."""
    import os
    with _lock:
        return {
            "cache_object_id": id(CACHE),
            "module_file": __file__,
            "pid": os.getpid(),
            "started_flag": _started,
            "gpu_len_now": len(CACHE["gpu"]),
            "stocks_len_now": len(CACHE["stocks"]),
            "hyperscaler_len_now": len(CACHE["hyperscaler"]),
            "last_updated_now": CACHE["last_updated"],
        }
