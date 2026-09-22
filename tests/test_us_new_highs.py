import os
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import app
import us_new_highs as us
from new_highs_data import read_json, write_json, read_report
from new_highs_filters import excluded_security
from new_highs_tracker import collect as kr_collect
from test_new_highs import candles, FakeSource
from us_sectors import classify


class SignalTests(unittest.TestCase):
    def test_us_close_cutoff_and_holiday_follow_actual_sessions(self):
        source = object.__new__(us.YahooSource)
        source.candles = lambda ticker: [{'date':'2026-09-18'}, {'date':'2026-09-21'}]
        self.assertEqual(source.target_date(datetime(2026,9,21,16,29,tzinfo=us.NY)), '2026-09-18')
        self.assertEqual(source.target_date(datetime(2026,9,21,16,30,tzinfo=us.NY)), '2026-09-21')
        self.assertEqual(source.target_date(datetime(2026,9,20,18,tzinfo=us.NY)), '2026-09-18')
    def test_high_window_equal_wick_and_short_history(self):
        data = candles([300]+[100]*364+[200])
        target = data[-1]['date']
        meta = {'name':'Example', 'exchange':'Q'}
        row, _ = us.high_row('EX', meta, data, target)
        self.assertEqual(row['previous_high'], 100)
        data[-1].update(open=100, low=90, close=95)
        row, _ = us.high_row('EX', meta, data, target)
        self.assertGreater(row['pullback_pct'], 50)
        data[-2]['high'] = 200
        self.assertIsNone(us.high_row('EX', meta, data, target)[0])
        self.assertEqual(us.high_row('EX', meta, data[-50:], target)[1], '52주 이력 부족')
        with self.assertRaises(ValueError): us.high_row('EX', meta, data[:-1], target)
    def test_universe_and_korean_sector_labels(self):
        header = 'Symbol|Security Name|ETF|Test Issue|Listing Exchange\n'
        data = header + 'ABC|ABC Common Stock|N|N|Q\nETF|ETF|Y|N|P\nTST|Test|N|Y|Q\nABCW|ABC Warrants|N|N|Q\nBRK.B|Berkshire Class B|N|N|N\n'
        self.assertEqual(set(us.parse_universe(data)), {'ABC','BRK-B'})
        self.assertEqual(classify('X', {'industry':'Semiconductor Equipment & Materials'}, {}), '반도체')
        self.assertEqual(classify('X', {'industry':'Biotechnology'}, {}), '바이오')
        self.assertEqual(classify('X', {'industry':'Software - Application'}, {'X':'AI 인프라'}), 'AI 인프라')
        self.assertEqual(classify('X', {}, {}), '미분류')
    def test_korean_security_exclusions(self):
        for name in ['KB제33호스팩', '신한 제12호 스팩', '기업인수목적회사', '롯데리츠', '미래에셋맵스리츠']:
            self.assertTrue(excluded_security(name), name)
        for name in ['메리츠금융지주', '스페코', '삼성전자', 'SK디스커버리']:
            self.assertIsNone(excluded_security(name), name)


class Source:
    def __init__(self): self.calls = []; self.fail = False
    def target_date(self, now): return '2026-01-01'
    def universe(self): return {t:{'name':t,'exchange':'Q'} for t in ('AAA','BBB')}
    def candles(self, ticker):
        self.calls.append(ticker)
        if self.fail: raise RuntimeError('offline')
        return candles([100]*365+[120])
    def profile(self, ticker): return {'industry':'Biotechnology'}


class CollectorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.root = Path(self.tmp.name)
        self.now = datetime(2026,1,2,18,tzinfo=us.NY)
    def tearDown(self): self.tmp.cleanup()
    def test_resumes_batches_and_does_not_replace_complete_on_failure(self):
        source = Source()
        first = us.collect(source,self.root,self.now,batch_size=1,delay=0)
        self.assertEqual(first['state'],'partial')
        self.assertEqual(first['coverage']['pending'],1)
        second = us.collect(source,self.root,self.now,batch_size=1,delay=0)
        self.assertEqual(second['state'],'complete')
        self.assertEqual(source.calls,['AAA','BBB'])
        path = self.root/'reports/2026-01-01.json'; original = path.read_bytes()
        source.fail = True
        self.assertEqual(us.collect(source,self.root,self.now,force=True,delay=0)['state'],'error')
        self.assertEqual(path.read_bytes(),original)
    def test_malformed_future_and_cache_only_routes(self):
        with patch.dict(os.environ, {'US_NEW_HIGHS_DATA_DIR':str(self.root)}), patch('requests.sessions.Session.request',side_effect=AssertionError('No route network')):
            client = app.app.test_client()
            self.assertEqual(client.get('/us-new-highs').status_code,200)
            self.assertEqual(client.get('/api/us-new-highs').json['rows'],[])
            for day in ('oops','2026-02-30','2999-01-01','20260101',''):
                self.assertEqual(client.get('/api/us-new-highs',query_string={'date':day}).status_code,400)
    def test_old_korean_reports_hidden_until_recalculated(self):
        write_json(self.root/'reports/2026-01-01.json',{'date':'2026-01-01','calculation_version':2,'status':'complete','rows':[{'name':'old'}]})
        with patch.dict(os.environ,{'NEW_HIGHS_DATA_DIR':str(self.root)}):
            self.assertEqual(read_report('2026-01-01')['rows'],[])
            self.assertIn('재수집',read_report('2026-01-01')['migration_notice'])
    def test_sector_override_applies_to_saved_report_without_network(self):
        write_json(self.root/'reports/2026-01-01.json', {'date':'2026-01-01','rows':[{'ticker':'NVDA','sector':'old'}]})
        with patch.dict(os.environ,{'US_NEW_HIGHS_DATA_DIR':str(self.root)}):
            self.assertEqual(us.read_report('2026-01-01')['rows'][0]['sector'],'반도체')
    def test_background_start_once(self):
        with patch.object(us,'_started',False), patch('threading.Thread') as thread:
            us.start_background_refresh(); us.start_background_refresh()
            thread.assert_called_once()
    def test_kr_exclusions_before_fetch_and_old_complete_migration(self):
        source = FakeSource()
        original_listings, original_quotes = source.listings, source.quotes
        source.listings = lambda:{**original_listings(),'000002':{'name':'롯데리츠','listing_date':'2020-01-01','market':'KOSPI','sector':'부동산'}}
        source.quotes = lambda:{**original_quotes(),'000002':{**original_quotes()['000001'],'stockName':'롯데리츠'}}
        write_json(self.root/'reports/2026-09-04.json',{'status':'complete','calculation_version':2,'rows':[]})
        result = kr_collect(source,self.root,now=datetime(2026,9,5,18,tzinfo=us.NY))
        self.assertEqual(result['state'],'complete')
        self.assertEqual(source.calls,1)
        report = read_json(self.root/'reports/2026-09-04.json',{})
        self.assertEqual(report['excluded']['000002'],'리츠')
        self.assertEqual(report['calculation_version'],3)


if __name__ == '__main__': unittest.main()
