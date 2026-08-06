# -*- coding: utf-8 -*-
"""
백그라운드에서 주기적으로 4개 트래커를 실행해 캐시를 갱신.
Flask 라우트는 절대 트래커를 직접 호출하지 않고 이 모듈의 get_cache()만 읽는다.

2026-08-06 수정 이력:
1차: connect/read timeout 분리
2차: ThreadPoolExecutor 워치독 시도 -> gunicorn fork와 충돌 의심되어 롤백
3차: 워치독 제거, 재시도 축소, print flush=True
4차: IPv4 강제 패치, 소켓 전역 타임아웃 추가
5차: 메모리 캐시 대신 디스크 파일(JSON) 기반 캐시로 전면 재구성
6차: GPU 렌탈가에 "전일대비(1D)" 등락률 추가 (캐시 파일 안에 오늘/어제
    스냅샷만 같이 저장해두는 방식, 별도 DB 없음)
7차(현재): SK하이닉스 ADR-본주 괴리율 트래커 통합.
    기존 skhynix-adr.onrender.com 전용 앱(30초 실시간 갱신)을 별도로
    두지 않고, 이 대시보드의 30분 주기 트래커 목록에 4번째로 추가하는
    방식으로 통합 (구조 단순화 우선, 실시간성보다 유지보수 편의 선택).
"""

import json
import os
import socket
import tempfile
import threading
import time
from datetime import datetime, timezone, timedelta

# --- IPv4 강제 패치 ---
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
import adr_tracker

socket.setdefaulttimeout(20)

REFRESH_INTERVAL_SECONDS = 30 * 60  # 30분마다 갱신
KST = timezone(timedelta(hours=9))

CACHE_FILE_PATH = os.environ.get(
    "DASHBOARD_CACHE_FILE",
    os.path.join(tempfile.gettempdir(), "dashboard_cache.json"),
)

_DEFAULT_CACHE = {
    "gpu": [],
    "stocks": [],
    "hyperscaler": [],
    "adr": {},  # SK하이닉스 ADR-본주 괴리율 (단일 스냅샷)
    "last_updated": None,
    "last_error": None,
    "refreshing": False,
    "gpu_daily": {"date": None, "prices": {}, "prev_date": None, "prev_prices": {}},
}


def _log(msg):
    print(msg, flush=True)


def _write_cache_file(data):
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
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return dict(_DEFAULT_CACHE)
    # 예전 캐시 파일(필드 없는 버전)과 호환
    if "gpu_daily" not in data:
        data["gpu_daily"] = dict(_DEFAULT_CACHE["gpu_daily"])
    if "adr" not in data:
        data["adr"] = {}
    return data


def _apply_gpu_daily_change(gpu_rows, prev_full_cache):
    """gpu_rows(Vast.ai/RunPod 합친 리스트)에 change_1d_pct를 붙이고,
    갱신된 gpu_daily 딕셔너리를 함께 반환한다."""
    daily = dict(prev_full_cache.get("gpu_daily") or _DEFAULT_CACHE["gpu_daily"])
    today_str = datetime.now(KST).strftime("%Y-%m-%d")

    if daily.get("date") != today_str:
        if daily.get("date") is not None:
            daily["prev_date"] = daily.get("date")
            daily["prev_prices"] = daily.get("prices", {})
        daily["date"] = today_str
        daily["prices"] = {}

    prev_prices = daily.get("prev_prices", {})

    updated_rows = []
    today_prices = dict(daily.get("prices", {}))
    for row in gpu_rows:
        row = dict(row)
        model = row.get("gpu_model")
        price = row.get("min_price_usd_hr")

        if price is not None and model:
            today_prices[model] = price

        change_pct = None
        if model and price is not None:
            prev_price = prev_prices.get(model)
            if prev_price:
                change_pct = (price - prev_price) / prev_price * 100.0
        row["change_1d_pct"] = change_pct
        updated_rows.append(row)

    daily["prices"] = today_prices
    return updated_rows, daily


def _refresh_once():
    current = _read_cache_file()
    current["refreshing"] = True
    _write_cache_file(current)
    _log(f"[CACHE][DIAG] _refresh_once 시작, 캐시 파일={CACHE_FILE_PATH}, pid={os.getpid()}")

    t0 = time.time()
    gpu_rows = []
    stock_rows = []
    hyper_rows = []
    adr_result = None
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

    try:
        adr_result = adr_tracker.run()
        if adr_result.get("error"):
            error_msgs.append(f"ADR: {adr_result['error']}")
    except Exception as e:
        error_msgs.append(f"ADR: {e}")
        _log(f"[CACHE] ADR 트래커 예외: {e}")

    prev = _read_cache_file()
    final_gpu_rows = gpu_rows if gpu_rows else prev.get("gpu", [])

    gpu_daily = prev.get("gpu_daily")
    if gpu_rows:
        final_gpu_rows, gpu_daily = _apply_gpu_daily_change(gpu_rows, prev)

    # ADR: 이번에 실패(adr_price is None)했으면 직전 성공 스냅샷을 유지
    if adr_result and adr_result.get("adr_price") is not None:
        final_adr = adr_result
    else:
        final_adr = prev.get("adr", {})

    new_data = {
        "gpu": final_gpu_rows,
        "stocks": stock_rows if stock_rows else prev.get("stocks", []),
        "hyperscaler": hyper_rows if hyper_rows else prev.get("hyperscaler", []),
        "adr": final_adr,
        "last_updated": datetime.now(timezone.utc).isoformat(),
        "last_error": "; ".join(error_msgs) if error_msgs else None,
        "refreshing": False,
        "gpu_daily": gpu_daily,
    }
    _write_cache_file(new_data)

    verify = _read_cache_file()
    elapsed = round(time.time() - t0, 1)
    _log(f"[CACHE] 갱신 완료 ({elapsed}초 소요): gpu={len(gpu_rows)} stocks={len(stock_rows)} "
         f"hyperscaler={len(hyper_rows)} adr_premium={final_adr.get('premium_pct')} "
         f"errors={new_data['last_error']}")
    _log(f"[CACHE][DIAG] 파일 재확인: gpu={len(verify.get('gpu', []))} "
         f"stocks={len(verify.get('stocks', []))} hyperscaler={len(verify.get('hyperscaler', []))} "
         f"last_updated={verify.get('last_updated')} gpu_daily_date={verify.get('gpu_daily', {}).get('date')} "
         f"adr_premium={verify.get('adr', {}).get('premium_pct')}")


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


_started = False
_start_lock = threading.Lock()


def start_background_refresh():
    global _started
    with _start_lock:
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
        "adr_now": data.get("adr"),
        "last_updated_now": data.get("last_updated"),
        "refreshing_now": data.get("refreshing"),
        "gpu_daily": data.get("gpu_daily"),
    }
