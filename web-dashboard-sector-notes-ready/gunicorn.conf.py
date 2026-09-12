# -*- coding: utf-8 -*-
"""
gunicorn 설정.
핵심: 백그라운드 캐시 갱신 스레드는 반드시 post_fork 훅에서,
즉 '워커 프로세스' 안에서 시작해야 한다.
master 프로세스에서 미리 시작해버리면(ADR 대시보드 때 겪은 버그) 워커가
포크된 후 스레드가 죽어있거나 중복 실행되는 문제가 생긴다.
"""

import os

bind = f"0.0.0.0:{os.environ.get('PORT', '10000')}"
workers = 1  # 캐시가 프로세스 메모리에 있으므로 워커 1개로 고정 (여러 개면 캐시가 워커마다 따로 놀아 갱신 낭비)
timeout = 120


def post_fork(server, worker):
    import cache_refresh
    cache_refresh.start_background_refresh()
    server.log.info(f"[gunicorn] worker {worker.pid} 캐시 갱신 스레드 시작")
