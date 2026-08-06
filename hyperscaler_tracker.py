# -*- coding: utf-8 -*-
"""
4. 하이퍼스케일러(MSFT/AMZN/GOOGL/META/ORCL) 재무지표 자동 트래커
- SEC EDGAR의 XBRL Company Facts API를 사용 (무료, 공식, 분기 실적 공시 즉시 반영)
- Capex, OCF를 뽑아 FCF = OCF - Capex 계산
- 각 회사 최근 8개 분기(10-Q 기준) 데이터를 반환
"""

import time
import requests

from config import SEC_USER_AGENT, HYPERSCALER_CIKS, XBRL_TAGS

FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
HEADERS = {"User-Agent": SEC_USER_AGENT}
TIMEOUT = 20


def fetch_company_facts(cik: str):
    url = FACTS_URL.format(cik=cik)
    try:
        resp = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        print(f"[HYPERSCALER] CIK {cik} 조회 실패: {e}")
        return None


def _extract_quarterly_values(facts, tag_candidates):
    """
    us-gaap 태그 후보들의 10-Q 분기 데이터를 모두 합쳐서 반환.

    주의: 회사마다, 그리고 같은 회사라도 시기에 따라 다른 태그를 쓸 수 있다.
    예) 아마존은 2016년 이전엔 PaymentsToAcquirePropertyPlantAndEquipment를,
        2016년 이후엔 PaymentsToAcquireProductiveAssets를 쓴다.
    "첫 번째로 데이터가 있는 태그"만 채택하면, 그 태그에 옛날 데이터만 남아있어도
    거기서 멈춰버려 정작 최근 분기(다른 태그)를 놓치게 된다.
    그래서 후보 태그 전부를 순회하며 10-Q 데이터를 end date 기준으로 병합한다.
    """
    us_gaap = facts.get("facts", {}).get("us-gaap", {})
    merged = {}  # end_date -> quarterly dict (같은 end date는 최신 태그/값으로 덮어씀)

    for tag in tag_candidates:
        if tag not in us_gaap:
            continue
        units = us_gaap[tag].get("units", {})
        usd_values = units.get("USD", [])

        for item in usd_values:
            if item.get("form") != "10-Q":
                continue
            end = item.get("end")
            if end is None:
                continue
            merged[end] = {
                "end": end,
                "start": item.get("start"),
                "val": item.get("val"),
                "fy": item.get("fy"),
                "fp": item.get("fp"),
            }

    return sorted(merged.values(), key=lambda x: x["end"])


def run():
    """전체 하이퍼스케일러에 대해 최근 8개 분기 Capex/OCF/FCF를 계산."""
    all_rows = []

    for company, cik in HYPERSCALER_CIKS.items():
        facts = fetch_company_facts(cik)
        if facts is None:
            continue

        capex_q = _extract_quarterly_values(facts, XBRL_TAGS["capex"])
        ocf_q = _extract_quarterly_values(facts, XBRL_TAGS["ocf"])

        capex_by_end = {q["end"]: q["val"] for q in capex_q}
        ocf_by_end = {q["end"]: q["val"] for q in ocf_q}

        all_ends = sorted(set(capex_by_end) | set(ocf_by_end))[-8:]  # 최근 8개 분기만

        for end in all_ends:
            capex = capex_by_end.get(end)
            ocf = ocf_by_end.get(end)
            fcf = (ocf - capex) if (capex is not None and ocf is not None) else None

            all_rows.append({
                "company": company,
                "period_end": end,
                "capex_usd": capex,
                "ocf_usd": ocf,
                "fcf_usd": fcf,
            })

        time.sleep(0.3)  # SEC fair use rate limit 준수

    print(f"[HYPERSCALER] {len(all_rows)}개 분기 데이터 수집 완료")
    return all_rows


if __name__ == "__main__":
    for r in run():
        print(r)
