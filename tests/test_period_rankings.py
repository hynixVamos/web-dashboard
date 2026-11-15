import os
import tempfile
import unittest
from datetime import date,timedelta
from pathlib import Path
from unittest.mock import patch
import period_rankings as ranks


def prices():
    days=[]; day=date(2026,11,13)
    while len(days)<100:
        if day.weekday()<5:days.append(day.isoformat())
        day-=timedelta(days=1)
    days.reverse()
    return [dict(date=d,close=100+i,volume=1000) for i,d in enumerate(days)]


class Source:
    def __init__(self):self.calls=[]
    def target_date(self,now):return prices()[-1]['date']
    def universe(self):return {'AAA':{'name':'Nasdaq A','exchange':'Q'},'BBB':{'name':'NYSE B','exchange':'N'},'CCC':{'name':'Nasdaq C','exchange':'Q'}}
    def profile(self,ticker):return {'industry':'Semiconductor Equipment & Materials','sector':'Technology'}
    def listings(self):return {'000001':{'name':'코스피 A','market':'KOSPI','sector':'반도체 제조업','listing_date':'2020-01-01'},'000002':{'name':'코스닥 B','market':'KOSDAQ','sector':'소프트웨어 개발','listing_date':'2020-01-01'},'000003':{'name':'테스트스팩','market':'KOSDAQ','sector':'금융','listing_date':'2020-01-01'}}
    def quotes(self):return {t:dict(localTradedAt=prices()[-1]['date']+'T18:00:00',marketStatus='CLOSE') for t in self.listings()}
    def candles(self,ticker):self.calls.append(ticker);return prices()


class RankingTests(unittest.TestCase):
    def test_previous_calendar_periods_exclude_current_week_and_month(self):
        c=prices(); spans=ranks.calendar_periods(date(2026,11,15),[r['date'] for r in c])
        self.assertEqual(spans['weekly']['start'],'2026-11-02')
        self.assertEqual(spans['weekly']['end'],'2026-11-06')
        self.assertEqual(spans['weekly']['base'],'2026-10-30')
        monday=ranks.calendar_periods(date(2026,11,16),[r['date'] for r in c])
        self.assertEqual(monday['weekly']['start'],'2026-11-09')
        self.assertEqual(monday['weekly']['end'],'2026-11-13')
        self.assertEqual(spans['monthly']['start'],'2026-10-01')
        self.assertEqual(spans['monthly']['end'],'2026-10-30')
        self.assertEqual(spans['monthly']['base'],'2026-09-30')
        row=ranks.return_row('AAA',dict(name='Test',market='NASDAQ'),c,c[-1]['date'],spans)
        lookup={r['date']:r['close'] for r in c}
        self.assertAlmostEqual(row['weekly'],(lookup['2026-11-06']/lookup['2026-10-30']-1)*100)
        self.assertAlmostEqual(row['monthly'],(lookup['2026-10-30']/lookup['2026-09-30']-1)*100)
        self.assertEqual(row['monthly_close'],lookup['2026-10-30'])
        missing=[r for r in c if r['date']!='2026-10-30']
        self.assertIsNone(ranks.return_row('AAA',dict(name='Test',market='NASDAQ'),missing,c[-1]['date'],spans))
        with self.assertRaises(ValueError):ranks.return_row('AAA',dict(name='Test',market='NASDAQ'),c+[c[0]],c[-1]['date'],spans)

    def test_holidays_and_year_boundary(self):
        days=[];d=date(2025,11,1)
        while d<=date(2026,1,5):
            if d.weekday()<5 and d.isoformat() not in ('2026-01-01','2026-01-02'):
                days.append(d.isoformat())
            d+=timedelta(days=1)
        spans=ranks.calendar_periods(date(2026,1,5),days)
        self.assertEqual(spans['weekly']['calendar_start'],'2025-12-29')
        self.assertEqual(spans['weekly']['calendar_end'],'2026-01-02')
        self.assertEqual(spans['weekly']['end'],'2025-12-31')
        self.assertEqual(spans['weekly']['sessions'],3)
        self.assertEqual(spans['monthly']['calendar_start'],'2025-12-01')
        self.assertEqual(spans['monthly']['end'],'2025-12-31')
        days.remove('2025-12-29')
        self.assertEqual(ranks.calendar_periods(date(2026,1,5),days)['weekly']['start'],'2025-12-30')

    def test_nasdaq_only_and_resume(self):
        with tempfile.TemporaryDirectory() as tmp:
            source=Source()
            first=ranks.collect('us',source,root=tmp,batch_size=1,today=date(2026,11,15))
            self.assertEqual(first['status'],'partial')
            self.assertEqual(first['coverage']['total'],2)
            second=ranks.collect('us',source,root=tmp,batch_size=1,today=date(2026,11,15))
            self.assertEqual(second['status'],'complete')
            self.assertEqual({r['ticker'] for r in second['rows']},{'AAA','CCC'})
            self.assertTrue(all(r['sector']=='반도체' for r in second['rows']))
            ranks.collect('us',source,root=tmp,today=date(2026,11,15))
            self.assertEqual(source.calls.count('AAA'),1)
    def test_kr_combines_both_markets_and_classifies(self):
        with tempfile.TemporaryDirectory() as tmp,patch.dict(os.environ,{'NEW_HIGHS_DATA_DIR':tmp+'/prices'}):
            report=ranks.collect('kr',Source(),root=tmp,today=date(2026,11,15))
            self.assertEqual(report['status'],'complete')
            self.assertEqual({r['market'] for r in report['rows']},{'KOSPI','KOSDAQ'})
            self.assertEqual(report['coverage']['total'],2)
            self.assertEqual({r['sector'] for r in report['rows']},{'반도체','소프트웨어·인터넷'})
    def test_failure_keeps_other_symbols_and_partial_coverage(self):
        source=Source();original=source.candles
        def candles(t):
            if t=='CCC':raise RuntimeError('provider unavailable')
            return original(t)
        source.candles=candles
        with tempfile.TemporaryDirectory() as tmp:
            report=ranks.collect('us',source,root=tmp,today=date(2026,11,15))
            self.assertEqual(report['coverage']['failed'],1)
            self.assertEqual(report['coverage']['pending'],0)
            self.assertEqual(report['status'],'partial')
            self.assertEqual(report['rows'][0]['ticker'],'AAA')
    def test_routes_are_read_only_and_validate_market(self):
        from app import app
        with tempfile.TemporaryDirectory() as tmp,patch.object(ranks,'root_dir',return_value=Path(tmp)),patch.object(ranks,'collect') as collect:
            client=app.test_client()
            self.assertEqual(client.get('/period-rankings').status_code,200)
            self.assertIn('주간·월간 상승률',client.get('/').text)
            response=client.get('/api/period-rankings?market=us')
            self.assertEqual(response.json['status'],'pending')
            self.assertEqual(response.headers['Cache-Control'],'no-store')
            self.assertEqual(client.get('/api/period-rankings?market=other').status_code,400)
            collect.assert_not_called()

if __name__=='__main__':unittest.main()
