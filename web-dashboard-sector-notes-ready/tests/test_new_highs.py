import os
import tempfile
import unittest
from copy import deepcopy
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch
import app as dashboard
from new_highs_data import KST, read_report, read_json, write_json
from new_highs_tracker import (calculate_signals, build_row, collect, collection_lock, parse_listings, parse_candles)


def candles(highs, start='2025-01-01'):
    first=datetime.strptime(start,'%Y-%m-%d')
    return [dict(date=(first+timedelta(days=i)).date().isoformat(),open=h,high=h,low=h,close=h,volume=100) for i,h in enumerate(highs)]

class CalculationTests(unittest.TestCase):
    def test_new_entry_streak_and_reset_per_period(self):
        data=candles([100]*60+[110,120,90,125])
        states=calculate_signals(data,'2025-01-01')
        self.assertTrue(states[60]['new']['20d']);self.assertTrue(states[60]['new']['60d'])
        self.assertFalse(states[61]['new']['20d']);self.assertEqual(states[61]['streak']['20d'],2)
        self.assertEqual(states[62]['streak']['20d'],0)
        self.assertTrue(states[63]['new']['20d']);self.assertEqual(states[63]['streak']['20d'],1)
    def test_equal_high_is_not_new_high(self):
        state=calculate_signals(candles([100]*80),'2025-01-01')[-1]
        self.assertFalse(any(state['flags'].values()))
    def test_windows_independent_and_expired_peak(self):
        data=candles([150]+[100]*60+[120])
        state=calculate_signals(data,'2025-01-01')[-1]
        self.assertTrue(state['flags']['20d']);self.assertTrue(state['flags']['60d'])
        self.assertFalse(state['flags']['all'])
    def test_no_history_no_all_time_and_short_ipo(self):
        state=calculate_signals(candles([10,20]),'2025-01-01')[-1]
        self.assertTrue(state['flags']['all']);self.assertFalse(state['flags']['20d'])
        state=calculate_signals(candles([10,20]),'2000-01-01')[-1]
        self.assertFalse(state['flags']['all']);self.assertFalse(state['all_time_verified'])
    def test_market_transfer_does_not_prove_all_time_coverage(self):
        state=calculate_signals(candles([10,20]),'2025-01-02')[-1]
        self.assertFalse(state['all_time_verified'])
        self.assertFalse(state['flags']['all'])
    def test_52_weeks_uses_calendar_window(self):
        data=candles([300]+[100]*364+[200])
        state=calculate_signals(data,'2025-01-01')[-1]
        self.assertTrue(state['flags']['52w']);self.assertFalse(state['flags']['all'])
        data[1]['high']=250
        self.assertFalse(calculate_signals(data,'2025-01-01')[-1]['flags']['52w'])
    def test_suspended_day_resets(self):
        data=candles([100]*60+[110,120]);data[-1]['volume']=0
        state=calculate_signals(data,'2025-01-01')[-1]
        self.assertFalse(any(state['flags'].values()))
    def test_real_units_name_reuse_and_date_mismatch(self):
        data=candles([100]*60+[120]);date=data[-1]['date']
        quote=dict(closePrice='120',marketValueRaw='10000000000',accumulatedTradingValueRaw='200000000',fluctuationsRatio='20',stockName='source')
        meta=dict(market='KOSPI',sector='전자',listing_date=data[0]['date'])
        row,_=build_row('005930',quote,meta,data,date)
        self.assertEqual(row['name'],'삼성전자');self.assertEqual(row['market_cap_eok'],100)
        self.assertEqual(row['trading_value_eok'],2);self.assertAlmostEqual(row['turnover'],.02)
        with self.assertRaises(ValueError):build_row('005930',quote,meta,data,'2025-12-31')
        with self.assertRaises(ValueError):build_row('005930',{**quote,'closePrice':'121'},meta,data,date)
    def test_parsers_accept_both_markets_and_preserve_codes(self):
        html='<table><tr><th>회사명</th><th>시장구분</th><th>종목코드</th><th>업종</th><th>상장일</th></tr><tr><td>A</td><td>유가</td><td>005930</td><td>전자</td><td>1975-06-11</td></tr><tr><td>B</td><td>코스닥</td><td>000001</td><td>기계</td><td>2020-01-01</td></tr></table>'
        self.assertEqual(len(parse_listings(html)),2)
        rows=parse_candles('<protocol><chartdata><item data="20260904|10|12|9|11|100" /></chartdata></protocol>')
        self.assertEqual(rows[0]['high'],12)

class FakeSource:
    def __init__(self,fail=False): self.fail=fail;self.calls=0
    def quotes(self):
        return {'000001':dict(localTradedAt='2026-09-04T16:30:00+09:00',marketStatus='CLOSE',tradeStopType={'name':'TRADING'},accumulatedTradingVolume='100',closePrice='120',marketValueRaw='10000000000',accumulatedTradingValueRaw='200000000',fluctuationsRatio='20',stockName='검증종목')}
    def listings(self):return {'000001':dict(name='검증종목',sector='기계',market='KOSDAQ',listing_date='2026-07-05')}
    def candles(self,code):
        self.calls+=1
        if self.fail:raise RuntimeError('source unavailable')
        return candles([100]*61+[120],'2026-07-05')

class CacheTests(unittest.TestCase):
    def setUp(self):self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.now=datetime(2026,9,5,18,tzinfo=KST)
    def tearDown(self):self.tmp.cleanup()
    def test_collection_and_completed_day_deduplication(self):
        source=FakeSource();result=collect(source,self.root,now=self.now)
        self.assertEqual(result['state'],'complete');self.assertEqual(source.calls,1)
        result=collect(source,self.root,now=self.now)
        self.assertEqual(result['state'],'up_to_date');self.assertEqual(source.calls,1)
        with patch.dict(os.environ,{'NEW_HIGHS_DATA_DIR':str(self.root)}):
            data=read_report('2026-09-04');self.assertFalse(data['is_mock']);self.assertEqual(len(data['rows']),1)
    def test_failure_preserves_complete_report(self):
        collect(FakeSource(),self.root,now=self.now)
        original=(self.root/'reports/2026-09-04.json').read_bytes()
        result=collect(FakeSource(True),self.root,force=True,now=self.now)
        self.assertEqual(result['state'],'error');self.assertEqual(original,(self.root/'reports/2026-09-04.json').read_bytes())
    def test_partial_results_are_labeled_and_retry_only_missing(self):
        source=FakeSource()
        original=source.listings
        source.listings=lambda:{**original(),'000002':dict(name='missing',sector='기계',market='KOSDAQ',listing_date='2020-01-01')}
        result=collect(source,self.root,now=self.now)
        self.assertEqual(result['state'],'partial')
        report=read_json(self.root/'reports/2026-09-04.json',{})
        self.assertEqual(report['coverage']['failed'],1)
        self.assertIn('000002',report['failures'])
        collect(source,self.root,now=self.now)
        self.assertEqual(source.calls,1)
    def test_no_intraday_collection_and_process_lock(self):
        source=FakeSource();result=collect(source,self.root,now=datetime(2026,9,4,15,tzinfo=KST))
        self.assertEqual(result['state'],'waiting_for_close');self.assertEqual(source.calls,0)
        with collection_lock(self.root) as first:
            self.assertTrue(first)
            with collection_lock(self.root) as second:self.assertFalse(second)
    def test_initial_missing_and_corrupt_cache_no_mock(self):
        write_json(self.root/'status.json',{'state':'collecting'})
        with patch.dict(os.environ,{'NEW_HIGHS_DATA_DIR':str(self.root)}):
            data=read_report();self.assertEqual(data['rows'],[]);self.assertFalse(data['is_mock'])
            self.assertEqual(read_report('2026-01-01')['rows'],[])

class RouteTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.env=patch.dict(os.environ,{'NEW_HIGHS_DATA_DIR':self.tmp.name});self.env.start()
        self.client=dashboard.app.test_client()
        self.cache=patch.object(dashboard.cache_refresh,'get_cache',return_value={'stocks':[],'gpu':[],'hyperscaler':[],'adr':{},'last_updated':None,'last_error':None});self.cache.start()
    def tearDown(self):self.cache.stop();self.env.stop();self.tmp.cleanup()
    def test_all_routes_without_outbound_requests(self):
        with patch('requests.sessions.Session.request',side_effect=AssertionError('No route network calls')):
            for route in ['/','/gpu','/stocks','/hyperscaler','/adr','/new-highs']:
                response=self.client.get(route);self.assertEqual(response.status_code,200,route)
                self.assertEqual(b'new-highs.css' in response.data,route=='/new-highs')
            for route in ['/api/gpu','/api/stocks','/api/hyperscaler','/api/adr','/api/new-highs','/health']:
                self.assertEqual(self.client.get(route).status_code,200,route)
    def test_background_start_is_idempotent(self):
        with patch.object(dashboard.cache_refresh,'_started',False), patch('threading.Thread') as thread, patch('new_highs_tracker.start_background_refresh') as highs:
            dashboard.cache_refresh.start_background_refresh()
            dashboard.cache_refresh.start_background_refresh()
            self.assertEqual(thread.call_count,1)
            highs.assert_called_once()
    def test_future_malformed_and_missing_dates(self):
        for value in ['oops','2026-02-30','20260825','','2999-01-01']:
            self.assertEqual(self.client.get('/api/new-highs',query_string={'date':value}).status_code,400)
        data=self.client.get('/api/new-highs?date=2026-01-01').json
        self.assertEqual(data['rows'],[]);self.assertFalse(data['is_mock'])

if __name__=='__main__':unittest.main()
