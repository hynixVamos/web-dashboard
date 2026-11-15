"""Persistent rolling 5/20-session equity return rankings; routes never fetch prices."""
import math
import os
import re
import threading
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

from new_highs_data import data_dir, read_json, write_json, KST
from new_highs_tracker import NaverSource, collection_lock
from us_new_highs import YahooSource, NY
from us_sectors import classify

VERSION = 1
WINDOWS = {'weekly': 5, 'monthly': 20}


def root_dir():
    return data_dir().parent / 'period_rankings'


def kr_sector(industry):
    # KIND industry classification, using specific industries before broad groups.
    groups = [
        ('반도체', ('반도체',)), ('바이오·헬스케어', ('의약', '의료', '생물', '제약')),
        ('소프트웨어·인터넷', ('소프트웨어', '정보서비스', '컴퓨터 프로그래밍')),
        ('통신', ('통신',)), ('자동차', ('자동차',)),
        ('조선·운송장비', ('선박', '기타 운송장비')),
        ('전기·전자', ('전자', '전기장비', '전동기', '일차전지', '축전지')),
        ('금융', ('금융', '보험', '신탁', '연금')),
        ('리츠·부동산', ('부동산',)), ('철강·금속', ('철강', '금속')),
        ('화학·소재', ('화학', '고무', '플라스틱', '비금속', '종이', '목재')),
        ('에너지·유틸리티', ('석유', '가스', '전기업', '증기', '연료')),
        ('기계·장비', ('기계', '장비')), ('건설', ('건설', '토목')),
        ('운송·물류', ('운송', '운수', '창고')),
        ('미디어·엔터', ('영상', '방송', '출판', '창작', '예술', '스포츠')),
        ('소비재·유통', ('음식', '식료', '음료', '섬유', '의복', '가죽', '도매', '소매', '숙박', '가구')),
        ('서비스', ('서비스', '교육', '연구', '사업지원')),
    ]
    for label, words in groups:
        if any(word in (industry or '') for word in words):
            return label
    return industry or '미분류'


def return_row(ticker, meta, candles, target, bases):
    relevant = [r for r in candles if r['date'] <= target]
    if len({r['date'] for r in relevant}) != len(relevant):
        raise ValueError('일봉 날짜 중복')
    by_date = {r['date']: r for r in relevant}
    last = by_date.get(target)
    if not last or not math.isfinite(float(last['close'])) or last['close'] <= 0:
        raise ValueError('기준일 종가 미수신')
    if float(last.get('volume', 0)) <= 0:
        return None
    row = dict(ticker=ticker, name=meta['name'], market=meta['market'], sector=meta.get('sector', '미분류'),
               industry=meta.get('industry', ''), close=last['close'], volume=last['volume'])
    for period, base in bases.items():
        previous = by_date.get(base)
        value = float(previous['close']) if previous else 0
        row[period] = (float(last['close']) / value - 1) * 100 if math.isfinite(value) and value > 0 else None
    return row if any(row.get(p) is not None for p in WINDOWS) else None


def read_report(market):
    if market not in ('kr', 'us'):
        raise ValueError('한국 또는 미국 시장을 선택해 주세요.')
    saved = read_json(root_dir()/market/'report.json', None)
    if saved is None:
        return dict(market=market, rows=[], status='pending', coverage={}, basis={}, date=None)
    return saved


def collect(market, source=None, root=None, batch_size=400):
    directory = Path(root or root_dir()) / market
    with collection_lock(directory) as acquired:
        if not acquired:
            return {'status': 'already_running'}
        previous = read_json(directory/'report.json', {})
        try:
            source = source or (NaverSource() if market == 'kr' else YahooSource())
            if market == 'kr':
                if datetime.now(KST).weekday() < 5 and datetime.now(KST).hour < 16:
                    return {'status': 'waiting_for_close'}
                quotes = source.quotes()
                target = Counter(q['localTradedAt'][:10] for q in quotes.values() if q.get('localTradedAt')).most_common(1)[0][0]
                if not any(q.get('marketStatus') == 'CLOSE' for q in quotes.values()):
                    return {'status': 'waiting_for_close'}
                reference = source.candles('005930')
            else:
                target = source.target_date(datetime.now(NY))
                reference = source.candles('SPY')
            if previous.get('date') == target and previous.get('status') == 'complete' and previous.get('version') == VERSION:
                return previous
            dates = sorted({r['date'] for r in reference if r['date'] <= target})
            if len(dates) < 21 or dates[-1] != target:
                raise ValueError('기간별 기준 거래일을 확인하지 못했습니다.')
            bases = {p: dates[-n-1] for p, n in WINDOWS.items()}
            state = read_json(directory/'checkpoint.json', {})
            if state.get('date') != target or state.get('version') != VERSION:
                if market == 'kr':
                    universe = {t: dict(name=m['name'], market=m['market'], industry=m.get('sector',''), sector=kr_sector(m.get('sector','')))
                                for t,m in source.listings().items() if m['listing_date'] <= target and not re.search(r'스팩|기업인수목적',m['name'])}
                else:
                    universe = {t: dict(name=m['name'], market='NASDAQ') for t,m in source.universe().items() if m['exchange'] == 'Q'}
                if not universe:
                    raise ValueError('상장 종목 목록이 비어 있습니다.')
                state = dict(date=target, version=VERSION, universe=universe, results={}, failures={})
            universe, results, failures = state['universe'], state['results'], state['failures']
            remaining = [t for t in universe if t not in results and t not in failures]
            # Finish all unattempted symbols before retrying provider failures.
            remaining = remaining or [t for t in failures if t not in results]
            def fetch(ticker):
                if market == 'kr':
                    quote = quotes.get(ticker, {})
                    if quote.get('marketStatus') != 'CLOSE' or quote.get('localTradedAt','')[:10] != target:
                        raise ValueError('확정 종가 미수신')
                    cached = read_json(data_dir()/'candles'/(ticker+'.json'), {})
                    candles = cached.get('candles') if cached.get('date') == target else None
                    if not candles:
                        candles = source.candles(ticker)
                else:
                    candles = source.candles(ticker)
                return return_row(ticker, universe[ticker], candles, target, bases)
            def save():
                rows = [r for r in results.values() if r is not None]
                pending = len(universe)-len(results)-len(failures)
                report = dict(version=VERSION,market=market,date=target,basis=bases,rows=rows,
                              status='complete' if len(results)==len(universe) else 'partial',
                              updated_at=datetime.now(KST).isoformat(),
                              coverage=dict(total=len(universe),evaluated=len(results),failed=len(failures),pending=pending,
                                            excluded=sum(r is None for r in results.values())))
                write_json(directory/'checkpoint.json',state)
                write_json(directory/'report.json',report)
                return report
            save()
            # A single KR source has its own rate limiter; US requests are bounded.
            with ThreadPoolExecutor(max_workers=1 if market=='kr' else 4) as pool:
                futures = {pool.submit(fetch,t):t for t in remaining[:batch_size]}
                for index, future in enumerate(as_completed(futures),1):
                    ticker = futures[future]
                    try:
                        results[ticker] = future.result()
                        failures.pop(ticker,None)
                    except Exception:
                        failures[ticker] = '가격 조회 실패'
                    if index % 25 == 0:
                        save()
            if market == 'us' and not (len(universe)-len(results)-len(failures)):
                sectors = read_json(directory/'sectors.json',{})
                candidates = set()
                rows = [r for r in results.values() if r]
                for period in WINDOWS:
                    candidates.update(r['ticker'] for r in sorted((r for r in rows if r[period] is not None),key=lambda r:-r[period])[:100])
                for ticker in candidates:
                    if not sectors.get(ticker) or sectors[ticker]['sector']=='미분류':
                        try:
                            info = source.profile(ticker)
                            sectors[ticker] = dict(sector=classify(ticker,info),industry=info.get('industry',''))
                        except Exception:
                            continue
                    results[ticker].update(sectors[ticker])
                for ticker,row in results.items():
                    if row and ticker in sectors:
                        row.update(sectors[ticker])
                write_json(directory/'sectors.json',sectors)
            return save()
        except Exception:
            if previous:
                failed = {**previous,'collection_error':True}
                write_json(directory/'report.json',failed)
                return failed
            report = dict(market=market,rows=[],date=None,basis={},coverage={},status='error',error='가격 공급자 연결 또는 거래일 확인에 실패했습니다. 자동 재시도합니다.')
            write_json(directory/'report.json',report)
            return report


_started = False
_lock = threading.Lock()


def start_background_refresh():
    global _started
    with _lock:
        if _started:
            return
        _started=True
    def loop(market):
        while True:
            report=collect(market)
            pending=report.get('coverage',{}).get('pending',0)
            threading.Event().wait(30 if pending else 900)
    for market in ('kr','us'):
        threading.Thread(target=loop,args=(market,),daemon=True,name='period-rankings-'+market).start()
