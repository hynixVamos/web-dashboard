# -*- coding: utf-8 -*-
"""
공통 설정 파일
- 종목 유니버스, 하이퍼스케일러 CIK, 출력 경로 등을 여기서만 관리
- 티커 추가/삭제는 이 파일의 STOCK_UNIVERSE 리스트만 수정하면 됨
"""

import os

# ---------------------------------------------------------------
# 출력 경로 (Windows에서 실행 시 본인 환경에 맞게 수정)
# ---------------------------------------------------------------
OUTPUT_EXCEL_PATH = os.environ.get(
    "TRACKER_EXCEL_PATH",
    r"C:\Users\%USERNAME%\Documents\auto_tracker\daily_tracker.xlsx"
)

# ---------------------------------------------------------------
# 1. GPU 렌탈가 트래커 설정
# ---------------------------------------------------------------
# Vast.ai API 키: https://cloud.vast.ai/api/v1/bundles/ 발급
VAST_API_KEY = os.environ.get("VAST_API_KEY", "")

# RunPod API 키: https://www.runpod.io/console/user/settings
RUNPOD_API_KEY = os.environ.get("RUNPOD_API_KEY", "")

# 트래킹할 GPU 모델 그룹.
# key = 리포트에 표시할 이름, value = Vast.ai gpu_name 실제 값 후보 리스트
# (Vast.ai는 같은 모델도 SXM/PCIe/NVL 등 폼팩터별로 gpu_name이 나뉘어 있어
#  그룹으로 묶어서 한 번에 조회함)
GPU_MODEL_GROUPS = {
    "H100 (Hopper)":  ["H100_SXM", "H100_PCIE", "H100_NVL"],
    "H200 (Hopper)":  ["H200_SXM", "H200_NVL"],
    "B200 (Blackwell)": ["B200_SXM", "B200_NVL"],
    "A100":           ["A100_SXM4", "A100_PCIE"],
    "L40S":           ["L40S"],
    "RTX 4090":       ["RTX_4090"],
    "RTX 5090 (Blackwell)": ["RTX_5090"],
}

# RunPod displayName 매칭용 (문자열 일부만 포함되면 매칭)
GPU_MODELS_TO_TRACK = list(GPU_MODEL_GROUPS.keys())

# ---------------------------------------------------------------
# 2. 주가수익률 트래커 설정 (완전자동)
# ---------------------------------------------------------------
# ticker: Yahoo Finance 표기 기준 (국내는 .KS, .KQ 접미사)
STOCK_UNIVERSE = [
    {"ticker": "005930.KS", "name": "삼성전자"},
    {"ticker": "000660.KS", "name": "SK하이닉스"},
    {"ticker": "MU",        "name": "Micron"},
    {"ticker": "NVDA",      "name": "NVIDIA"},
    {"ticker": "TSM",       "name": "TSMC (ADR)"},
    {"ticker": "MSFT",      "name": "Microsoft"},
    {"ticker": "AMZN",      "name": "Amazon"},
    {"ticker": "GOOGL",     "name": "Alphabet"},
    {"ticker": "META",      "name": "Meta"},
    {"ticker": "ORCL",      "name": "Oracle"},
    {"ticker": "CRWV",      "name": "CoreWeave"},
    {"ticker": "NBIS",      "name": "Nebius"},
]

RETURN_WINDOWS = ["1D", "1W", "1M", "3M", "YTD"]

# ---------------------------------------------------------------
# 3. 하이퍼스케일러 재무지표 트래커 설정 (SEC EDGAR XBRL)
# ---------------------------------------------------------------
# SEC는 User-Agent에 실제 연락처(이메일)를 요구함 (fair use policy)
SEC_USER_AGENT = os.environ.get(
    "SEC_USER_AGENT",
    "personal-research-tool contact-your-email@example.com"
)

HYPERSCALER_CIKS = {
    "Microsoft": "0000789019",
    "Amazon":    "0001018724",
    "Alphabet":  "0001652044",
    "Meta":      "0001326801",
    "Oracle":    "0001341439",
}

# 추출할 XBRL us-gaap 태그 (회사마다 실제 사용 태그가 다를 수 있어 후보를 여러 개 둠)
XBRL_TAGS = {
    "capex": [
        "PaymentsToAcquirePropertyPlantAndEquipment",
        "PaymentsToAcquireProductiveAssets",
    ],
    "ocf": [
        "NetCashProvidedByUsedInOperatingActivities",
        "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations",
    ],
}
