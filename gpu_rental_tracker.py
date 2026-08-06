# -*- coding: utf-8 -*-
"""
1. GPU 렌탈가 자동 트래커
- Vast.ai: /api/v1/bundles/ 에서 현재 열려있는 모든 호가(offer)를 받아 GPU 모델별 최저가/25th percentile 계산
- RunPod: GraphQL API에서 gpuTypes(lowestPrice) 조회
- 결과: [{date, source, gpu_model, min_price, p25_price, sample_count}, ...]
"""

import statistics
import time
import requests
from datetime import datetime, timezone

from config import VAST_API_KEY, RUNPOD_API_KEY, GPU_MODEL_GROUPS, GPU_MODELS_TO_TRACK

# 2026년 기준 최신 엔드포인트: console.vast.ai, POST 방식, /api/v0/bundles
VAST_BUNDLES_URL = "https://console.vast.ai/api/v0/bundles"
RUNPOD_GRAPHQL_URL = "https://api.runpod.io/graphql"

TIMEOUT = 20


def _today_str():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _fetch_offers(order_direction="asc", limit=500):
    """
    gpu_name을 필터링하지 않고 전체 매물을 가져온다.
    Vast.ai의 정확한 gpu_name enum 값을 알 수 없으므로
    (asc: 저렴한 GPU 위주, desc: 비싼 멀티-GPU H100/H200/B200 위주)
    두 방향으로 가져와 합친 뒤 클라이언트에서 문자열 매칭한다.
    """
    headers = {
        "Authorization": f"Bearer {VAST_API_KEY}",
        "Content-Type": "application/json",
    }
    body = {
        "limit": limit,
        "type": "on-demand",
        "verified": {"eq": True},
        "rentable": {"eq": True},
        "rented": {"eq": False},
        "order": [["dph_total", order_direction]],
    }
    try:
        resp = requests.post(VAST_BUNDLES_URL, headers=headers, json=body, timeout=TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        print(f"[GPU][Vast.ai] {order_direction} 조회 실패: {e}")
        return []

    return data.get("offers", data if isinstance(data, list) else [])


def fetch_vast_offers_by_group():
    """
    저렴한 매물(asc)과 비싼 매물(desc)을 합쳐서 전체 GPU 스펙트럼을 커버한 뒤,
    GPU_MODEL_GROUPS에 정의된 문자열이 gpu_name에 포함되는지로 그룹핑한다.
    """
    if not VAST_API_KEY:
        print("[GPU][Vast.ai] VAST_API_KEY가 설정되지 않아 건너뜁니다.")
        return {}

    asc_offers = _fetch_offers(order_direction="asc", limit=500)
    time.sleep(0.3)
    desc_offers = _fetch_offers(order_direction="desc", limit=500)

    # id 기준 중복 제거
    combined = {}
    for offer in asc_offers + desc_offers:
        oid = offer.get("id") or offer.get("ask_contract_id")
        if oid is not None:
            combined[oid] = offer
    all_offers = list(combined.values())

    # gpu_name 문자열 매칭으로 그룹별 분류
    # (Vast.ai 표기: "H100 SXM", "H100_SXM", "H100 PCIe" 등 공백/언더스코어 혼재 가능하므로
    #  두 문자 다 제거한 뒤 비교)
    def _normalize(s):
        return str(s).lower().replace("_", "").replace(" ", "")

    result = {display_name: [] for display_name in GPU_MODEL_GROUPS}
    for offer in all_offers:
        gpu_name_norm = _normalize(offer.get("gpu_name", ""))
        for display_name, variants in GPU_MODEL_GROUPS.items():
            # 그룹명 자체의 핵심 토큰(H100, H200, B200, A100, L40S, RTX 4090, RTX 5090)으로 매칭
            key_token = _normalize(display_name.split(" (")[0])
            if key_token in gpu_name_norm:
                result[display_name].append(offer)
                break  # 한 offer는 한 그룹에만

    return result


def summarize_vast_by_gpu(offers_by_group):
    """모델 그룹별로 min / 25th percentile 가격을 계산."""
    rows = []
    now = _today_str()

    for display_name, offers in offers_by_group.items():
        prices = []
        for offer in offers:
            num_gpus = offer.get("num_gpus", 1) or 1
            dph_total = offer.get("dph_total")
            if dph_total is None:
                continue
            prices.append(dph_total / num_gpus)

        if not prices:
            rows.append({
                "date": now,
                "source": "Vast.ai",
                "gpu_model": display_name,
                "min_price_usd_hr": None,
                "p25_price_usd_hr": None,
                "sample_count": 0,
            })
            continue

        prices.sort()
        min_price = prices[0]
        p25_price = statistics.quantiles(prices, n=4)[0] if len(prices) >= 4 else prices[0]

        rows.append({
            "date": now,
            "source": "Vast.ai",
            "gpu_model": display_name,
            "min_price_usd_hr": round(min_price, 4),
            "p25_price_usd_hr": round(p25_price, 4),
            "sample_count": len(prices),
        })

    return rows


def fetch_runpod_prices():
    """RunPod GraphQL에서 GPU 타입별 lowestPrice 조회."""
    if not RUNPOD_API_KEY:
        print("[GPU][RunPod] RUNPOD_API_KEY가 설정되지 않아 건너뜁니다.")
        return []

    query = """
    query GpuTypes {
      gpuTypes {
        id
        displayName
        lowestPrice(input: {}) {
          minimumBidPrice
          uninterruptablePrice
        }
      }
    }
    """
    try:
        resp = requests.post(
            RUNPOD_GRAPHQL_URL,
            params={"api_key": RUNPOD_API_KEY},
            json={"query": query},
            timeout=TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        print(f"[GPU][RunPod] 조회 실패: {e}")
        return []

    gpu_types = data.get("data", {}).get("gpuTypes", [])
    now = _today_str()
    rows = []

    for gpu_model in GPU_MODELS_TO_TRACK:
        for gt in gpu_types:
            display_name = gt.get("displayName", "")
            if gpu_model.lower() in display_name.lower():
                lp = gt.get("lowestPrice") or {}
                on_demand = lp.get("uninterruptablePrice")
                spot = lp.get("minimumBidPrice")
                rows.append({
                    "date": now,
                    "source": "RunPod",
                    "gpu_model": gpu_model,
                    "on_demand_usd_hr": on_demand,
                    "spot_usd_hr": spot,
                })

    return rows


def run():
    """전체 GPU 렌탈가 수집을 실행하고 통합 리스트를 반환."""
    vast_offers_by_group = fetch_vast_offers_by_group()
    vast_rows = summarize_vast_by_gpu(vast_offers_by_group)
    runpod_rows = fetch_runpod_prices()

    print(f"[GPU] Vast.ai {len(vast_rows)}개 모델, RunPod {len(runpod_rows)}개 모델 수집 완료")
    return {"vast": vast_rows, "runpod": runpod_rows}


if __name__ == "__main__":
    result = run()
    for source, rows in result.items():
        print(f"--- {source} ---")
        for r in rows:
            print(r)
