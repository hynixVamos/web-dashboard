"""Actual KOSPI/KOSDAQ daily highs: collector -> JSON snapshots -> read-only routes.

No API key; NAVER public quotes/adjusted daily candles and KRX KIND listing metadata.
High = today's adjusted intraday HIGH strictly exceeds the previous window's HIGH.
20/60: previous 20/60 sessions; 52w: previous 364 calendar days.
All-time only when the supplied history reaches the KIND listing date.
"""
import argparse
from collections import Counter, deque
from contextlib import contextmanager
from datetime import datetime, timedelta
from html.parser import HTMLParser
import logging
import math
import os
from pathlib import Path
import re
import threading
import time
import xml.etree.ElementTree as ET
import requests
from config import STOCK_UNIVERSE
from new_highs_data import KST, PERIOD_FIELDS, data_dir, read_json, write_json

log = logging.getLogger(__name__)
TIMEOUT = (5, 25)
CALCULATION_VERSION = 2

class TableParser(HTMLParser):
    def __init__(self):
        super().__init__(); self.rows = []; self.row = []; self.cell = None
    def handle_starttag(self, tag, attrs):
        if tag == 'tr': self.row = []
        elif tag in ('td', 'th'): self.cell = []
    def handle_data(self, data):
        if self.cell is not None: self.cell.append(data)
    def handle_endtag(self, tag):
        if tag in ('td', 'th') and self.cell is not None:
            self.row.append(' '.join(''.join(self.cell).split())); self.cell = None
        elif tag == 'tr' and self.row: self.rows.append(self.row)

def parse_listings(text):
    parser = TableParser(); parser.feed(text)
    header = next((r for r in parser.rows if '종목코드' in r and '상장일' in r), None)
    if not header: raise ValueError('KRX KIND 목록 형식 변경')
    indices = {name: header.index(name) for name in ('종목코드','회사명','시장구분','업종','상장일')}
    result = {}
    for row in parser.rows:
        if len(row) <= max(indices.values()): continue
        code = row[indices['종목코드']]
        if not re.fullmatch(r'[0-9A-Z]{6}', code): continue
        market = row[indices['시장구분']]
        if market not in ('유가','유가증권','코스피','코스닥'): continue
        listed = row[indices['상장일']]
        datetime.strptime(listed, '%Y-%m-%d')
        result[code] = {'name':row[indices['회사명']], 'sector':row[indices['업종']] or '미분류',
                        'listing_date':listed, 'market':'KOSDAQ' if market == '코스닥' else 'KOSPI'}
    if not result: raise ValueError('KRX KIND 상장 종목 목록이 비어 있습니다.')
    return result

def parse_candles(text):
    tree = ET.fromstring(text)
    rows = []
    for item in tree.findall('.//item'):
        fields = item.attrib['data'].split('|')
        day = datetime.strptime(fields[0], '%Y%m%d').date().isoformat()
        values = [float(v) for v in fields[1:6]]
        if len(values) != 5 or any(not math.isfinite(v) or v < 0 for v in values):
            raise ValueError('잘못된 OHLCV')
        rows.append({'date':day,'open':values[0],'high':values[1],'low':values[2],
                     'close':values[3],'volume':values[4]})
    if not rows: raise ValueError('일봉 데이터 없음')
    if len({r['date'] for r in rows}) != len(rows): raise ValueError('일봉 날짜 중복')
    return sorted(rows, key=lambda r:r['date'])

def numeric(value):
    result = float(str(value).replace(',', ''))
    if not math.isfinite(result): raise ValueError('유효하지 않은 시세 숫자')
    return result

class NaverSource:
    def __init__(self, delay=0.25):
        self.session = requests.Session()
        self.session.headers.update({'User-Agent':'Mozilla/5.0', 'Referer':'https://finance.naver.com/'})
        self.delay = max(0.2, delay)
        self.last_request = 0
    def get(self, url, **kwargs):
        for attempt in range(3):
            time.sleep(max(0, self.delay - (time.monotonic()-self.last_request)))
            self.last_request = time.monotonic()
            try:
                response = self.session.get(url, timeout=TIMEOUT, **kwargs)
                response.raise_for_status()
                return response
            except requests.RequestException:
                if attempt == 2: raise
                time.sleep(2 ** attempt)
    def listings(self):
        response = self.get('https://kind.krx.co.kr/corpgeneral/corpList.do',
                            params={'method':'download','searchType':'13'})
        response.encoding = 'euc-kr'
        listings = parse_listings(response.text)
        for market in ('KOSPI','KOSDAQ'):
            if sum(m['market']==market for m in listings.values()) < 100:
                raise ValueError('KRX KIND 시장 목록 누락/불완전')
        return listings
    def quotes(self):
        result = {}
        for market in ('KOSPI','KOSDAQ'):
            seen = set(); expected = None
            for page in range(1, 151):
                data = self.get('https://m.stock.naver.com/api/stocks/marketValue/'+market,
                                params={'page':page,'pageSize':100}).json()
                total = int(data['totalCount'])
                if expected is None: expected = total
                if expected != total: raise ValueError('수집 중 종목 목록 변경: 다시 시도합니다.')
                stocks = data['stocks']
                if not stocks: raise ValueError('시세 페이지가 조기에 종료됐습니다.')
                for stock in stocks:
                    code = stock['itemCode']
                    if code in seen: raise ValueError('시세 페이지 중복')
                    seen.add(code)
                    if stock.get('stockEndType') == 'stock':
                        result[code] = {**stock, 'market':market}
                if len(seen) >= expected: break
            if len(seen) != expected: raise ValueError('시세 종목 수 불일치')
        if not result: raise ValueError('시세 목록이 비어 있습니다.')
        return result
    def candles(self, code):
        response = self.get('https://fchart.stock.naver.com/sise.nhn',
                            params={'symbol':code,'timeframe':'day','count':6000,'requestType':0})
        return parse_candles(response.text)


def calculate_signals(candles, listing_date):
    """Linear-time rolling maxima. Missing full history never means all-time high."""
    if not candles: return []
    # Exact start match: a later KIND date can be a market transfer/relisting.
    complete = candles[0]['date'] == listing_date
    queues = {p:deque() for p in ('20d','60d','52w')}
    streak = {p:0 for p in PERIOD_FIELDS}
    running_max = 0.0
    signals = []
    for i, row in enumerate(candles):
        cutoff = (datetime.strptime(row['date'], '%Y-%m-%d').date()-timedelta(weeks=52)).isoformat()
        flags = {}
        for key, window in (('20d',20),('60d',60),('52w',None)):
            queue = queues[key]
            while queue and ((queue[0][0] < i-window) if window else (queue[0][1] < cutoff)):
                queue.popleft()
            ready = i >= window if window else candles[0]['date'] <= cutoff
            flags[key] = bool(ready and queue and row['volume'] > 0 and row['high'] > queue[0][2])
        flags['all'] = bool(complete and i > 0 and row['volume'] > 0 and row['high'] > running_max)
        previous = signals[-1]['flags'] if signals else {p:False for p in PERIOD_FIELDS}
        new = {p:flags[p] and not previous[p] for p in PERIOD_FIELDS}
        for p in PERIOD_FIELDS: streak[p] = streak[p]+1 if flags[p] else 0
        signals.append({'flags':flags,'new':new,'streak':dict(streak),'all_time_verified':complete})
        for queue in queues.values():
            while queue and queue[-1][2] <= row['high']: queue.pop()
            queue.append((i,row['date'],row['high']))
        running_max = max(running_max,row['high'])
    return signals


def build_row(code, quote, meta, candles, report_date):
    candles = [r for r in candles if r['date'] <= report_date]
    if not candles or candles[-1]['date'] != report_date: raise ValueError('일봉 최신 날짜 불일치')
    close = numeric(quote['closePrice'])
    if abs(candles[-1]['close']-close) > max(0.01, close*0.00001):
        raise ValueError('시세 종가와 일봉 종가 불일치')
    signals = calculate_signals(candles, meta['listing_date'])[-1]
    flags = signals['flags']
    if not any(flags.values()): return None, signals['all_time_verified']
    cap = numeric(quote['marketValueRaw']) / 1e8
    value = numeric(quote['accumulatedTradingValueRaw']) / 1e8
    if cap <= 0 or value < 0: raise ValueError('잘못된 시가총액/거래대금')
    suffix = '.KQ' if meta['market']=='KOSDAQ' else '.KS'
    names = {r['ticker']:r['name'] for r in STOCK_UNIVERSE}
    row = dict(date=report_date,ticker=code,name=names.get(code+suffix,quote['stockName']),
               sector=meta['sector'],close=close,change_pct=numeric(quote['fluctuationsRatio']),
               market_cap_eok=cap,trading_value_eok=value,turnover=value/cap,
               consecutive_high_days=max(signals['streak'].values()),
               consecutive_by_period=signals['streak'],all_time_verified=signals['all_time_verified'],
               history_start=candles[0]['date'],reason='',memo='')
    for key, field in PERIOD_FIELDS.items():
        row['is_'+field] = flags[key]; row['new_'+field] = signals['new'][key]
    return row, signals['all_time_verified']


@contextmanager
def collection_lock(root):
    """OS file lock released on crash; also excludes manual collector processes."""
    root.mkdir(parents=True,exist_ok=True)
    with (root/'collector.lock').open('a+b') as handle:
        if handle.tell()==0: handle.write(b'0'); handle.flush()
        handle.seek(0)
        try:
            if os.name=='nt':
                import msvcrt
                msvcrt.locking(handle.fileno(),msvcrt.LK_NBLCK,1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
        except (OSError, IOError):
            yield False; return
        try: yield True
        finally:
            handle.seek(0)
            if os.name=='nt': msvcrt.locking(handle.fileno(),msvcrt.LK_UNLCK,1)
            else: fcntl.flock(handle.fileno(),fcntl.LOCK_UN)


def collect(source=None, root=None, force=False, now=None):
    root = Path(root or data_dir()); now = now or datetime.now(KST)
    # Start after regular close; retry pending quote/candle publication every 5 minutes.
    if now.weekday()<5 and (now.hour,now.minute)<(15,30): return {'state':'waiting_for_close'}
    source = source or NaverSource()
    with collection_lock(root) as acquired:
        if not acquired: return {'state':'already_running'}
        status = {'state':'collecting','started_at':now.isoformat(),'processed':0,'failed':0}
        write_json(root/'status.json',status)
        try:
            quotes = source.quotes()
            dates = [q['localTradedAt'][:10] for q in quotes.values() if q.get('localTradedAt')]
            target = Counter(dates).most_common(1)[0][0]
            datetime.strptime(target,'%Y-%m-%d')
            if target > now.date().isoformat(): raise ValueError('미래 날짜 시세')
            existing = read_json(root/'reports'/(target+'.json'),{})
            if existing.get('status')=='complete' and existing.get('calculation_version')==CALCULATION_VERSION and not force:
                status.update(state='up_to_date',target_date=target,updated_at=existing['updated_at'])
                write_json(root/'status.json',status);return status
            listings = source.listings()
            universe = {c:m for c,m in listings.items() if m['listing_date']<=target}
            status.update(target_date=target,total=len(universe))
            checkpoint = read_json(root/'checkpoint.json',{})
            results = checkpoint.get('results',{}) if checkpoint.get('date')==target and not force else {}
            failures = {}; excluded = {}; verified=0
            for index,(code,meta) in enumerate(universe.items(),1):
                quote = quotes.get(code)
                try:
                    if quote is None: raise ValueError('시장 시세에 종목 없음')
                    if quote.get('tradeStopType',{}).get('name') != 'TRADING' or numeric(quote['accumulatedTradingVolume'])==0:
                        excluded[code]='거래정지/거래량 없음';continue
                    if quote.get('marketStatus')!='CLOSE' or quote['localTradedAt'][:10]!=target:
                        raise ValueError('확정 시세 날짜/장 상태 불일치')
                    signature = [CALCULATION_VERSION] + [quote.get(k) for k in ('closePrice','marketValueRaw','accumulatedTradingValueRaw','fluctuationsRatio')] + [meta['listing_date'],meta['sector']]
                    cached = results.get(code)
                    if not cached or cached.get('signature') != signature:
                        candle_cache = root/'candles'/(code+'.json')
                        stored = read_json(candle_cache,{})
                        candles = stored.get('candles') if stored.get('date')==target else None
                        if candles is None or force:
                            candles = source.candles(code)
                            if candles[-1]['date'] != target: raise ValueError('일봉 미갱신')
                            write_json(candle_cache,{'date':target,'candles':candles})
                        row, covered = build_row(code,quote,meta,candles,target)
                        results[code]={'signature':signature,'row':row,'all_time_verified':covered}
                except Exception as error:
                    results.pop(code,None); failures[code]=str(error)[:180]
                finally:
                    if index % 25 == 0 or index==len(universe):
                        status.update(processed=index,failed=len(failures),heartbeat_at=datetime.now(KST).isoformat())
                        write_json(root/'status.json',status)
                        write_json(root/'checkpoint.json',{'date':target,'results':results})
                        log.info('신고가 수집 %s/%s 실패 %s',index,len(universe),len(failures))
            valid = [v for c,v in results.items() if c in universe and c not in excluded and c not in failures]
            rows = [v['row'] for v in valid if v['row'] is not None]
            if not valid: raise ValueError('검증된 종목이 없습니다. 직전 정상 리포트를 유지합니다.')
            report = dict(date=target,calculation_version=CALCULATION_VERSION,source='NAVER 정규장 시세·수정 일봉 / KRX KIND 업종',is_mock=False,
                          status='partial' if failures else 'complete',updated_at=datetime.now(KST).isoformat(),
                          rows=rows,coverage={'total':len(universe),'evaluated':len(valid),'excluded':len(excluded),
                          'failed':len(failures),'all_time_verified':sum(v['all_time_verified'] for v in valid)},
                          failures=failures,excluded=excluded,
                          basis='수정 일봉 고가 > 직전 20/60 거래일 또는 52주 고가. 역사적은 상장일부터 이력 확보된 종목만 판정.')
            # Never replace an already complete day with a partial retry.
            if existing.get('status')!='complete' or report['status']=='complete':
                write_json(root/'reports'/(target+'.json'),report)
            status.update(state=report['status'],updated_at=report['updated_at'],coverage=report['coverage'])
            write_json(root/'status.json',status)
            return status
        except Exception as error:
            status.update(state='error',error=str(error)[:300],heartbeat_at=datetime.now(KST).isoformat())
            write_json(root/'status.json',status)
            log.exception('신고가 수집 실패; 기존 리포트 유지')
            return status

_started = False
_start_lock = threading.Lock()

def _loop():
    while True:
        try: collect()
        except Exception: log.exception('신고가 백그라운드 예외')
        time.sleep(5*60)

def start_background_refresh():
    global _started
    with _start_lock:
        if _started: return
        _started=True
    threading.Thread(target=_loop,name='new-highs-refresh',daemon=True).start()

if __name__=='__main__':
    parser=argparse.ArgumentParser(description='실제 한국 주식 신고가 수집')
    parser.add_argument('--force',action='store_true',help='완료된 날짜도 다시 수집')
    args=parser.parse_args()
    logging.basicConfig(level=logging.INFO,format='%(asctime)s %(message)s')
    result=collect(force=args.force)
    print(result)
    raise SystemExit(1 if result.get('state') in ('error','partial') else 0)
