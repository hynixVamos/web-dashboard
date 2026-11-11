import asyncio
import os
import tempfile
import unittest
from datetime import datetime, timezone
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


if __name__ == '__main__': unittest.main()
