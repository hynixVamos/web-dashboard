# -*- coding: utf-8 -*-
"""
백그라운드에서 주기적으로 3개 트래커를 실행해 캐시를 갱신.
Flask 라우트는 절대 트래커를 직접 호출하지 않고 이 모듈의 get_cache()만 읽는다.

2026-08-06 수정 이력:
1차: connect/read timeout 분리
2차: ThreadPoolExecutor 워치독 시도 -> gunicorn fork와 충돌 의심되어 롤백
3차: 워치독 제거, 재시도 축소, print flush=True
4차: IPv4 강제 패치, 소켓 전역 타임아웃 추가
5차(현재): 배경 스레드가 로그로는 분명히 메모리 캐시에 데이터를 썼다고
     확인되는데도(진단 코드로 캐시 객체 identity까지 일치함을 확인) HTTP
     요청 쪽에서는 여전히 빈 값이 읽히는 현상이 반복 관찰됨. 파이썬
     프로세스 내부 메모리 공유 방식에 계속 의존하는 대신, 갱신 스레드는
     결과를 디스크 파일(JSON)에 직접 쓰고 Flask 쪽은 항상 그 파일을 다시
     읽어오는 구조로 바꿔서 이 불확실성 자체를 우회한다. (부수 효과로
     프로세스가 재시작돼도 마지막 데이터가 남아있는 장점도 있음)
"""

import json
import os
import socket
import tempfile
import threading
import time
from datetime import datetime, timezone

try:
    import urllib3.util.connection as _urllib3_cn

    def _allowed_gai_family():
        return socket.AF_INET

    _urllib3_cn.allowed_gai_family = _allowed_gai_family
    print("[CACHE] IPv4 강제 패치 적용됨", flush=True)
except Exception as _e:
    print(f"[CACHE] IPv4 강제 패치 실패 (무시하고 진행): {_e}", flush=True)

import gpu_rental_tracker
import stock_returns_tracker
import hyperscaler_tracker

socket.setdefaulttimeout(20)

REFRESH_INTERVAL_SECONDS = 30 * 60  # 30분마다 갱신

CACHE_FILE_PATH = os.environ.get(
    "DASHBOARD_CACHE_FILE",
    os.path.join(tempfile.gettempdir(), "dashboard_cache.json"),
)

_DEFAULT_CACHE = {
    "gpu": [],
    "stocks": [],
    "hyperscaler": [],
    "last_updated": None,
    "last_error": None,
    "refreshing": False,
}

_file_lock = threading.Lock()
_started = False


def _log(msg):
    print(msg, flush=True)


def _write_cache_file(data):
    with _file_lock:
        dir_name = os.path.dirname(CACHE_FILE_PATH) or "."
        fd, tmp_path = tempfile.mkstemp(dir=dir_name, prefix=".cache_tmp_")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False)
            os.replace(tmp_path, CACHE_FILE_PATH)
        except Exception:
            try:
                os.remove(tmp_path)
            except OSError:
                pass
            raise


def _read_cache_file():
    try:
        with open(CACHE_FILE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return dict(_DEFAULT_CACHE)


def _refresh_once():
    current = _read_cache_file()
    current["refreshing"] = True
    _write_cache_file(current)
    _log(f"[CACHE][DIAG] _refresh_once 시작, 캐시 파일={CACHE_FILE_PATH}, pid={os.getpid()}")

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

    prev = _read_cache_file()
    new_data = {
        "gpu": gpu_rows if gpu_rows else prev.get("gpu", []),
        "stocks": stock_rows if stock_rows else prev.get("stocks", []),
        "hyperscaler": hyper_rows if hyper_rows else prev.get("hyperscaler", []),
        "last_updated": datetime.now(timezone.utc).isoformat(),
        "last_error": "; ".join(error_msgs) if error_msgs else None,
        "refreshing": False,
    }
    _write_cache_file(new_data)

    verify = _read_cache_file()
    elapsed = round(time.time() - t0, 1)
    _log(f"[CACHE] 갱신 완료 ({elapsed}초 소요): gpu={len(gpu_rows)} stocks={len(stock_rows)} "
         f"hyperscaler={len(hyper_rows)} errors={new_data['last_error']}")
    _log(f"[CACHE][DIAG] 파일 재확인: gpu={len(verify.get('gpu', []))} "
         f"stocks={len(verify.get('stocks', []))} hyperscaler={len(verify.get('hyperscaler', []))} "
         f"last_updated={verify.get('last_updated')}")


def _refresh_loop():
    while True:
        try:
            _refresh_once()
        except Exception as e:
            _log(f"[CACHE] 갱신 루프 예외: {e}")
            try:
                current = _read_cache_file()
                current["refreshing"] = False
                _write_cache_file(current)
            except Exception:
                pass
        time.sleep(REFRESH_INTERVAL_SECONDS)


def start_background_refresh():
    global _started
    with _file_lock:
        if _started:
            return
        _started = True

    _log(f"[CACHE] 백그라운드 갱신 스레드 시작 준비 (캐시 파일: {CACHE_FILE_PATH})")
    threading.Thread(target=_refresh_loop, daemon=True).start()


def get_cache():
    return _read_cache_file()


def diag_info():
    data = _read_cache_file()
    return {
        "pid": os.getpid(),
        "started_flag": _started,
        "cache_file_path": CACHE_FILE_PATH,
        "cache_file_exists": os.path.exists(CACHE_FILE_PATH),
        "gpu_len_now": len(data.get("gpu", [])),
        "stocks_len_now": len(data.get("stocks", [])),
        "hyperscaler_len_now": len(data.get("hyperscaler", [])),
        "last_updated_now": data.get("last_updated"),
        "refreshing_now": data.get("refreshing"),
    }
