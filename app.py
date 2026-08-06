# -*- coding: utf-8 -*-
"""
AI 인프라 트래킹 대시보드 (탭 기반 다중 페이지)
- /            개요 (요약 카드 + 퀵 배지)
- /gpu         GPU 렌탈가 전체 표
- /stocks      주가수익률 전체 표
- /hyperscaler 하이퍼스케일러 Capex/OCF/FCF 전체 표
- /adr         SK하이닉스 ADR-본주 괴리율

이 파일은 캐시(cache_refresh.get_cache())만 읽는다. 외부 API는 절대 여기서 호출하지 않는다.
"""

import os
from flask import Flask, render_template, jsonify

import cache_refresh

app = Flask(__name__)


def _format_updated(iso_str):
    if not iso_str:
        return "아직 갱신 전"
    try:
        date_part, time_part = iso_str.split("T")
        hh_mm = time_part[:5]
        return f"{date_part} {hh_mm} UTC"
    except Exception:
        return iso_str


def _common_context(active_tab):
    cache = cache_refresh.get_cache()
    return {
        "active_tab": active_tab,
        "last_updated": _format_updated(cache["last_updated"]),
        "last_error": cache["last_error"],
        "_cache": cache,
    }


@app.route("/")
def overview():
    ctx = _common_context("overview")
    cache = ctx.pop("_cache")

    stocks = cache["stocks"]
    gpu_rows = [r for r in cache["gpu"] if r.get("source") == "Vast.ai"]

    valid_gpu = [r for r in gpu_rows if r.get("min_price_usd_hr") is not None]
    gpu_model_count = len(valid_gpu)
    cheapest_gpu = min(valid_gpu, key=lambda r: r["min_price_usd_hr"]) if valid_gpu else None

    stock_count = len(stocks)
    ytd_values = [r["YTD"] for r in stocks if r.get("YTD") is not None]
    avg_ytd = sum(ytd_values) / len(ytd_values) if ytd_values else None

    hyper_rows = cache["hyperscaler"]
    latest_fcf_by_company = {}
    for row in hyper_rows:
        company = row["company"]
        if row.get("fcf_usd") is None:
            continue
        if company not in latest_fcf_by_company or row["period_end"] > latest_fcf_by_company[company][0]:
            latest_fcf_by_company[company] = (row["period_end"], row["fcf_usd"])
    fcf_values = [v[1] for v in latest_fcf_by_company.values()]
    avg_fcf = sum(fcf_values) / len(fcf_values) if fcf_values else None

    adr = cache.get("adr") or {}

    return render_template(
        "overview.html",
        stocks=stocks,
        gpu_model_count=gpu_model_count,
        cheapest_gpu=cheapest_gpu,
        stock_count=stock_count,
        avg_ytd=avg_ytd,
        avg_fcf=avg_fcf,
        adr=adr,
        **ctx,
    )


@app.route("/gpu")
def gpu_page():
    ctx = _common_context("gpu")
    cache = ctx.pop("_cache")
    gpu_vast = [r for r in cache["gpu"] if r.get("source") == "Vast.ai"]
    gpu_runpod = [r for r in cache["gpu"] if r.get("source") == "RunPod"]
    return render_template("gpu.html", gpu_vast=gpu_vast, gpu_runpod=gpu_runpod, **ctx)


@app.route("/stocks")
def stocks_page():
    ctx = _common_context("stocks")
    cache = ctx.pop("_cache")
    return render_template("stocks.html", stocks=cache["stocks"], **ctx)


@app.route("/hyperscaler")
def hyperscaler_page():
    ctx = _common_context("hyperscaler")
    cache = ctx.pop("_cache")
    return render_template("hyperscaler.html", hyperscaler=cache["hyperscaler"], **ctx)


@app.route("/adr")
def adr_page():
    ctx = _common_context("adr")
    cache = ctx.pop("_cache")
    return render_template("adr.html", adr=cache.get("adr") or {}, **ctx)


@app.route("/api/gpu")
def api_gpu():
    return jsonify(cache_refresh.get_cache()["gpu"])


@app.route("/api/stocks")
def api_stocks():
    return jsonify(cache_refresh.get_cache()["stocks"])


@app.route("/api/hyperscaler")
def api_hyperscaler():
    return jsonify(cache_refresh.get_cache()["hyperscaler"])


@app.route("/api/adr")
def api_adr():
    return jsonify(cache_refresh.get_cache().get("adr") or {})


@app.route("/health")
def health():
    cache = cache_refresh.get_cache()
    return jsonify({
        "status": "ok",
        "last_updated": cache["last_updated"],
        "last_error": cache["last_error"],
    })


@app.route("/diag")
def diag():
    return jsonify(cache_refresh.diag_info())


if __name__ == "__main__":
    cache_refresh.start_background_refresh()
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
