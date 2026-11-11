import asyncio
import os
import tempfile
import unittest
from datetime import datetime, timezone, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import x_comments as x
from new_highs_data import write_json, read_json


class CommentsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.now = datetime(2026, 11, 11, 3, tzinfo=timezone.utc)
        self.report = {'date':'2026-11-10','rows':[{'ticker':'ZIP','change_pct':53}]}
        self.raw = {'id':'2120275391038308499','author':'Michael_ColeYT',
                    'text':'$ZIP revenue beat and guidance raised',
                    'created_at':'2026-11-10T22:00:20+00:00','likes':1}

    def tearDown(self):
        self.tmp.cleanup()

    def tweet(self, text=None):
        return SimpleNamespace(id=self.raw['id'],user=SimpleNamespace(screen_name=self.raw['author']),
                               text=text or self.raw['text'],favorite_count=1,
                               created_at='Tue Nov 10 22:00:20 +0000 2026')

    def test_only_matching_attributed_excerpts_are_published(self):
        post = x.normalize_post({**self.raw,'text':'$ZIP ' + 'word '*50,'url':'javascript:evil'}, 'ZIP')
        self.assertEqual(len(post['text'].split()),25)
        self.assertEqual(post['url'],'https://x.com/Michael_ColeYT/status/2120275391038308499')
        for raw in [{**self.raw,'text':'$ZIPPER is not ZIP'}, {**self.raw,'author':'bad/user'},
                    {**self.raw,'text':'$ZIP join our WhatsApp signals group'},
                    {**self.raw,'created_at':'bad date'}]:
            self.assertIsNone(x.normalize_post(raw,'ZIP'))

    def test_calendar_query_covers_report_date_at_year_boundary(self):
        self.assertEqual(x.search_query('ZIP','2026-01-01'),
                         '$ZIP since:2025-12-29 until:2026-01-03 -is:retweet')

    def test_collect_deduplicates_and_caches_once_per_date(self):
        calls = []
        async def search(*args, **kwargs):
            calls.append(args)
            return [self.tweet(),self.tweet()]
        client = SimpleNamespace(search_tweet=search)
        result = asyncio.run(x.collect(self.report,client,self.root,self.now,delay=0))
        self.assertEqual(len(result['tickers']['ZIP']['posts']),1)
        asyncio.run(x.collect(self.report,client,self.root,self.now,delay=0))
        self.assertEqual(len(calls),1)

    def test_error_keeps_last_posts_and_never_exposes_secret_payload(self):
        write_json(self.root/'x-comments-2026-11-10.json',{'date':'2026-11-10','tickers':{
            'ZIP':{'status':'error','posts':[x.normalize_post(self.raw,'ZIP')]}}})
        async def unavailable(*args, **kwargs):
            raise RuntimeError('cookie auth_token=private-secret')
        result = asyncio.run(x.collect(self.report,SimpleNamespace(search_tweet=unavailable),self.root,self.now,delay=0))
        self.assertEqual(len(result['tickers']['ZIP']['posts']),1)
        self.assertEqual(result['tickers']['ZIP']['status'],'error')
        self.assertNotIn('private-secret',(self.root/'x-comments-2026-11-10.json').read_text())

    def test_no_credentials_never_calls_network_or_starts_thread(self):
        with patch.dict(os.environ,{},clear=True), patch.object(x,'_started',False), patch('threading.Thread') as thread:
            self.assertEqual(x.run_once(),'not_configured')
            x.start_background_refresh()
            thread.assert_not_called()

    def test_empty_search_is_explicit_and_does_not_copy_other_tickers(self):
        async def search(*args, **kwargs): return [self.tweet('$OTHER revenue beat')]
        result = asyncio.run(x.collect(self.report,SimpleNamespace(search_tweet=search),self.root,self.now,delay=0))
        self.assertEqual(result['tickers']['ZIP'],{'status':'empty','posts':[], 'attempted_at':self.now.isoformat()})

    def test_schedule_is_korean_time_and_only_tuesday_to_saturday(self):
        # Monday 23:00 UTC is Tuesday 08:00 in Korea.
        self.assertEqual(x.due_slot(datetime(2026,11,9,23,tzinfo=timezone.utc)),
                         '2026-11-10T08:00:00+09:00')
        self.assertEqual(x.due_slot(datetime(2026,11,14,4,tzinfo=timezone.utc)),
                         '2026-11-14T13:00:00+09:00')
        self.assertIsNone(x.due_slot(datetime(2026,11,9,8,tzinfo=x.KST)))
        self.assertIsNone(x.due_slot(datetime(2026,11,15,13,tzinfo=x.KST)))
        self.assertIsNone(x.due_slot(datetime(2026,11,10,9,tzinfo=x.KST)))
        self.assertIsNone(x.due_slot(datetime(2026,11,10,8,10,tzinfo=x.KST)))
        self.assertIsNotNone(x.due_slot(datetime(2026,11,10,8,9,59,tzinfo=x.KST)))

    def test_scheduled_slot_runs_once_across_restarts_and_refreshes_at_second_slot(self):
        calls = []
        def runner(**kwargs): calls.append(kwargs); return {'collector_status':'ready'}
        morning = datetime(2026,11,10,8,tzinfo=x.KST)
        self.assertEqual(x.run_scheduled(morning,self.root,runner),'ready')
        self.assertEqual(x.run_scheduled(morning+timedelta(minutes=1),self.root,runner),'already_attempted')
        self.assertEqual(x.run_scheduled(morning+timedelta(hours=1),self.root,runner),'outside_schedule')
        self.assertEqual(x.run_scheduled(morning+timedelta(hours=5),self.root,runner),'ready')
        self.assertEqual(calls,[{'refresh':True},{'refresh':True}])

    def test_scheduled_error_never_retries_outside_next_slot_or_leaks_credentials(self):
        def runner(**kwargs): raise RuntimeError('auth_token=private-secret')
        now = datetime(2026,11,10,8,tzinfo=x.KST)
        self.assertEqual(x.run_scheduled(now,self.root,runner),'error')
        self.assertEqual(x.run_scheduled(now+timedelta(minutes=1),self.root,runner),'already_attempted')
        self.assertNotIn('private-secret',(self.root/'schedule.json').read_text())

    def test_scheduled_refresh_replaces_a_successful_cached_search(self):
        calls=[]
        async def search(*args,**kwargs): calls.append(args); return [self.tweet()]
        client=SimpleNamespace(search_tweet=search)
        asyncio.run(x.collect(self.report,client,self.root,self.now,delay=0))
        asyncio.run(x.collect(self.report,client,self.root,self.now+timedelta(hours=5),delay=0,refresh=True))
        self.assertEqual(len(calls),2)


if __name__ == '__main__': unittest.main()
