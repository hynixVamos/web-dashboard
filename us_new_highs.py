"""Resumable US end-of-day scan. HTTP routes only read saved reports.

Universe: Nasdaq Trader's US listed securities, excluding ETFs, test issues,
preferred shares, warrants, rights, units and debt. Prices: Yahoo Finance.
52-week high: regular-session HIGH > highest prior HIGH in 364 calendar days.
"""
import argparse
import csv
import io
import logging
import math
import os
import re
import threading
import time
from collections import Counter
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
from new_highs_data import data_dir, read_json, write_json
from new_highs_tracker import collection_lock
from us_sectors import classify

NY = ZoneInfo('America/New_York')
VERSION = 1
log = logging.getLogger(__name__)
FAILURE_LABELS = {
    'rate_limit': '공급자 요청 제한', 'network': '통신 오류·시간 초과',
    'missing': '가격·거래일 데이터 미수신', 'invalid': '가격 검증 실패',
    'other': '기타 조회 오류',
}


def failure_kind(error):
    text = (type(error).__name__ + ' ' + str(error)).lower()
    if any(s in text for s in ('ratelimit', 'rate limit', 'too many requests', '429')):
        return 'rate_limit'
    if any(s in text for s in ('timeout', 'timed out', 'connection', 'connect', 'resolve', 'offline', '502', '503', '504')):
        return 'network'
    if any(s in text for s in ('미갱신', 'no price', 'pricesmissing', 'possibly delisted', 'no timezone', 'tzmissing')):
        return 'missing'
    if any(s in text for s in ('ohlc', '가격 순서', '날짜 중복')):
        return 'invalid'
    return 'other'


def failure_summary(failures, retry_meta):
    counts = Counter(retry_meta.get(t, {}).get('kind') or failure_kind(message)
                     for t, message in failures.items())
    return [{'kind': k, 'label': FAILURE_LABELS[k], 'count': count,
             'tickers': sorted(t for t, message in failures.items()
                               if (retry_meta.get(t, {}).get('kind') or failure_kind(message)) == k)[:10]}
            for k, count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))]


def retry_due(meta, now):
    try:
        return datetime.fromisoformat(meta['next_retry_at']) <= now
    except (KeyError, ValueError, TypeError):
        return True  # Existing checkpoints have no retry metadata.


def record_failure(ticker, error, failures, retry_meta, now):
    kind = failure_kind(error)
    attempts = retry_meta.get(ticker, {}).get('attempts', 0) + 1
    base = {'rate_limit': 900, 'network': 60, 'missing': 1800, 'invalid': 3600, 'other': 900}[kind]
    wait = min(21600, base * 2 ** min(attempts - 1, 5))
    failures[ticker] = str(error)[:180]
    retry_meta[ticker] = {'kind': kind, 'attempts': attempts, 'last_attempt_at': now.isoformat(),
                          'next_retry_at': (now + timedelta(seconds=wait)).isoformat()}
    return kind


def root_dir():
    return Path(os.environ.get('US_NEW_HIGHS_DATA_DIR', str(data_dir().parent / 'us_new_highs')))


def parse_universe(text):
    rows = csv.DictReader(io.StringIO(text), delimiter='|')
    required = {'Symbol', 'Security Name', 'ETF', 'Test Issue', 'Listing Exchange'}
    if not required.issubset(rows.fieldnames or []):
        raise ValueError('Nasdaq 종목 목록 형식 변경')
    result = {}
    for r in rows:
        symbol, name = r['Symbol'], r['Security Name'] or ''
        if r['ETF'] != 'N' or r['Test Issue'] != 'N':
            continue
        if not re.fullmatch(r'[A-Z]+(?:[.-][A-Z])?', symbol or ''):
            continue
        if re.search(r'\b(warrants?|rights?|units?|preferred|notes|bonds|debentures)\b', name, re.I):
            continue
        result[symbol.replace('.', '-')] = {'name': name, 'exchange': r['Listing Exchange']}
    if not result:
        raise ValueError('미국 종목 목록이 비었습니다.')
    return result


def high_row(ticker, meta, candles, target):
    """Require complete 52-week coverage; no intraday or stale observations."""
    candles = sorted((r for r in candles if r['date'] <= target), key=lambda r: r['date'])
    if not candles or candles[-1]['date'] != target:
        raise ValueError('해당 거래일 일봉 미갱신')
    if len({r['date'] for r in candles}) != len(candles):
        raise ValueError('일봉 날짜 중복')
    for r in candles:
        for k in ('open', 'high', 'low', 'close', 'volume'):
            if not isinstance(r.get(k), (float, int)) or not math.isfinite(r[k]) or r[k] < 0:
                raise ValueError('유효하지 않은 OHLCV')
        if not 0 < r['low'] <= min(r['open'], r['close']) <= max(r['open'], r['close']) <= r['high']:
            raise ValueError('OHLC 가격 순서 오류')
    cutoff = (date.fromisoformat(target) - timedelta(weeks=52)).isoformat()
    if candles[0]['date'] > cutoff:
        return None, '52주 이력 부족'
    prior = [r for r in candles[:-1] if r['date'] >= cutoff]
    if not prior or candles[-1]['volume'] <= 0:
        return None, '거래량 또는 비교 이력 없음'
    current, previous_high = candles[-1], max(r['high'] for r in prior)
    if current['high'] <= previous_high:
        return None, None
    return dict(ticker=ticker, name=meta['name'], exchange=meta['exchange'], date=target,
                sector='미분류', close=current['close'], high=current['high'],
                previous_high=previous_high, volume=current['volume'],
                change_pct=(current['close']/candles[-2]['close']-1)*100,
                pullback_pct=(1-current['close']/current['high'])*100), None


class YahooSource:
    def __init__(self):
        import yfinance as yf
        self.yf = yf
        # Avoid writing into a host user's cache directory.
        yf.set_tz_cache_location(str(root_dir() / 'yfinance-cache'))

    def universe(self):
        response = requests.get('https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqtraded.txt',
                                timeout=(5, 30))
        response.raise_for_status()
        result = parse_universe(response.text)
        if len(result) < 1000:
            raise ValueError('미국 종목 목록이 불완전합니다.')
        return result

    def candles(self, ticker):
        frame = self.yf.Ticker(ticker).history(period='2y', interval='1d', auto_adjust=False,
                                              actions=False, timeout=20, raise_errors=True)
        # Yahoo OHLC is split-adjusted; leave dividends unadjusted consistently.
        return [dict(date=idx.date().isoformat(), **{key: float(row[col]) for key, col in
                [('open','Open'), ('high','High'), ('low','Low'), ('close','Close'), ('volume','Volume')]})
                for idx, row in frame.iterrows()]

    def target_date(self, now):
        local = now.astimezone(NY)
        # Conservative 16:30 ET cutoff also works on early-close days and DST.
        limit = local.date() if (local.hour, local.minute) >= (16, 30) else local.date()-timedelta(days=1)
        sessions = [r['date'] for r in self.candles('SPY') if r['date'] <= limit.isoformat()]
        if not sessions:
            raise ValueError('미국 마감 거래일을 확인할 수 없습니다.')
        return max(sessions)

    def profile(self, ticker):
        return self.yf.Ticker(ticker).get_info()


def collect(source=None, root=None, now=None, batch_size=250, force=False, delay=0.3):
    root = Path(root or root_dir())
    now = now or datetime.now(NY)
    with collection_lock(root) as acquired:
        if not acquired:
            return {'state': 'already_running'}
        status = {'state': 'collecting', 'started_at': now.isoformat()}
        write_json(root/'status.json', status)
        try:
            source = source or YahooSource()
            target = source.target_date(now)
            if date.fromisoformat(target) > now.astimezone(NY).date():
                raise ValueError('미래 거래일')
            status['target_date'] = target
            existing = read_json(root/'reports'/f'{target}.json', {})
            if existing.get('status') == 'complete' and existing.get('calculation_version') == VERSION and not force:
                status.update(state='up_to_date', coverage=existing['coverage'])
                write_json(root/'status.json', status)
                return status
            saved = read_json(root/'checkpoint.json', {})
            if force or saved.get('date') != target or saved.get('version') != VERSION:
                saved = {'date': target, 'version': VERSION, 'universe': source.universe(), 'results': {}, 'failures': {}}
            universe, results, failures = saved['universe'], saved['results'], saved['failures']
            retry_meta = saved.setdefault('retry_meta', {})
            sector_warnings = saved.setdefault('sector_warnings', {})
            # Preserve all successful results from v1 checkpoints. Rotate retries so
            # permanently missing symbols at the front cannot starve other names.
            remaining = [t for t in universe if t not in results and retry_due(retry_meta.get(t, {}), now)]
            remaining.sort(key=lambda t: (t in failures, retry_meta.get(t, {}).get('last_attempt_at', ''), t))
            status.update(total=len(universe), processed=len(results))
            write_json(root/'status.json', status)
            consecutive_errors = 0
            provider_backoff = False
            profile_backoff = False
            for ticker in remaining[:batch_size]:
                try:
                    row, excluded = high_row(ticker, universe[ticker], source.candles(ticker), target)
                    if row:
                        info = {}
                        try:
                            if not profile_backoff:
                                info = source.profile(ticker) or {}
                        except Exception as error:
                            profile_backoff = failure_kind(error) == 'rate_limit'
                        # Sector enrichment must never discard verified price data.
                        row.update(sector=classify(ticker, info), industry=info.get('industry', ''))
                        if row['sector'] == '미분류':
                            sector_warnings[ticker] = '업종 미수신·미분류 (가격 판정 완료)'
                        else:
                            sector_warnings.pop(ticker, None)
                    results[ticker] = {'row': row, 'excluded': excluded}
                    failures.pop(ticker, None)
                    retry_meta.pop(ticker, None)
                    consecutive_errors = 0
                except Exception as error:
                    kind = record_failure(ticker, error, failures, retry_meta, now)
                    consecutive_errors = consecutive_errors + 1 if kind in ('rate_limit', 'network') else 0
                    provider_backoff = kind == 'rate_limit' or consecutive_errors >= 5
                status.update(processed=len(results), failed=len(failures), heartbeat_at=datetime.now(NY).isoformat())
                write_json(root/'checkpoint.json', saved)
                write_json(root/'status.json', status)
                if provider_backoff:
                    break  # Only provider/network errors pause the market scan.
                if delay:
                    time.sleep(delay)
            if not results:
                raise ValueError('검증된 미국 종목이 없습니다. 직전 리포트를 유지합니다.')
            coverage = {'total': len(universe), 'evaluated': len(results), 'failed': len(failures),
                        'pending': len(universe)-len(results)-len(failures),
                        'excluded': sum(bool(r['excluded']) for r in results.values())}
            diagnostics = {'failure_summary': failure_summary(failures, retry_meta),
                           'sector_unclassified': len(sector_warnings),
                           'retry_waiting': sum(not retry_due(retry_meta.get(t, {}), now) for t in failures),
                           'next_retry_at': min((m['next_retry_at'] for t, m in retry_meta.items()
                                                 if t in failures), default=None)}
            report = {'date': target, 'rows': [r['row'] for r in results.values() if r['row']],
                      'calculation_version': VERSION, 'coverage': coverage,
                      'status': 'complete' if len(results) == len(universe) else 'partial',
                      'diagnostics': diagnostics,
                      'updated_at': datetime.now(NY).isoformat(),
                      'source': 'Nasdaq Trader 종목 목록 / Yahoo Finance 일봉·업종',
                      'basis': '정규장 고가 > 직전 52주 고가 (동일 가격 제외, 52주 미만 상장 이력 제외)'}
            if existing.get('status') != 'complete' or report['status'] == 'complete':
                write_json(root/'reports'/f'{target}.json', report)
            status.update(state=report['status'], coverage=coverage, provider_backoff=provider_backoff,
                          diagnostics=diagnostics)
        except Exception as error:
            status.update(state='error', error=str(error)[:300])
            log.exception('미국 신고가 수집 실패')
        write_json(root/'status.json', status)
        return status


def read_report(selected=None):
    root = root_dir()
    today = datetime.now(NY).date().isoformat()
    if selected is not None:
        if date.fromisoformat(selected).isoformat() != selected or selected > today:
            raise ValueError('미국 날짜 기준 YYYY-MM-DD 형식의 과거 또는 오늘 날짜를 선택하세요.')
    dates = sorted(p.stem for p in (root/'reports').glob('*.json')
                   if re.fullmatch(r'\d{4}-\d{2}-\d{2}', p.stem) and p.stem <= today)
    selected = selected or (dates[-1] if dates else today)
    report = read_json(root/'reports'/f'{selected}.json', None)
    if report is None:
        report = {'date': selected, 'rows': [], 'coverage': {}, 'status': 'unavailable' if dates else 'pending'}
    overrides = read_json(Path(__file__).with_name('us_sector_overrides.json'), {})
    report = {**report, 'rows': [{**r, 'sector': overrides.get(r['ticker']) or r['sector']}
                               for r in report['rows']]}
    checkpoint = read_json(root/'checkpoint.json', {})
    if checkpoint.get('date') == selected:
        # Also diagnose old checkpoints and a scan interrupted before report save.
        failures = checkpoint.get('failures', {})
        retry_meta = checkpoint.get('retry_meta', {})
        report['diagnostics'] = {
            'failure_summary': failure_summary(failures, retry_meta),
            'sector_unclassified': len(checkpoint.get('sector_warnings', {})),
            'retry_waiting': sum(not retry_due(retry_meta.get(t, {}), datetime.now(NY)) for t in failures),
            'next_retry_at': min((m['next_retry_at'] for t, m in retry_meta.items() if t in failures), default=None),
        }
    collector = read_json(root/'status.json', {})
    heartbeat = collector.get('heartbeat_at') or collector.get('started_at')
    if collector.get('state') == 'collecting' and heartbeat:
        if (datetime.now(NY)-datetime.fromisoformat(heartbeat)).total_seconds() > 600:
            collector = {**collector, 'state': 'error', 'error': '수집 진행이 중단되었습니다. 다음 실행에서 이어서 수집합니다.'}
    return {**report, 'today': today, 'available_dates': dates, 'collector': collector}


_started = False
_lock = threading.Lock()


def _loop():
    while True:
        try:
            result = collect()
        except Exception:
            log.exception('미국 신고가 백그라운드 예외')
            result = {'state': 'error'}
        coverage = result.get('coverage', {})
        more = coverage.get('pending', 0) > 0 or coverage.get('failed', 0) > 0
        time.sleep(60 if result.get('state') == 'partial' and more and not result.get('provider_backoff') else 900)


def start_background_refresh():
    global _started
    with _lock:
        if _started:
            return
        _started = True
    threading.Thread(target=_loop, name='us-highs-refresh', daemon=True).start()
    from x_comments import start_background_refresh as start_x_comments
    start_x_comments()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='미국 52주 신고가 수집 (중단 후 재개 가능)')
    parser.add_argument('--force', action='store_true')
    parser.add_argument('--batch-size', type=int, default=250)
    args = parser.parse_args()
    if args.batch_size < 1:
        parser.error('--batch-size must be positive')
    print(collect(batch_size=args.batch_size, force=args.force))
