import unittest
import asyncio
from x_web_compat import ondemand_urls, parse_indices, install, public_tweets


class WebCompatibilityTests(unittest.TestCase):
    def test_legacy_and_new_chunk_layouts(self):
        expected=['https://abs.twimg.com/responsive-web/client-web/ondemand.s.abcd1234a.js']
        self.assertEqual(ondemand_urls('{"ondemand.s":"abcd1234"}'),expected)
        self.assertEqual(ondemand_urls('{"42":"ondemand.s"};{42:"abcd1234"}'),expected)
        self.assertEqual(ondemand_urls("{42:'ondemand.s'};{'42':'abcd1234'}"),expected)

    def test_no_arbitrary_script_locations_or_silent_bad_indices(self):
        self.assertEqual(ondemand_urls('{42:"ondemand.s"};{42:"https://example.com"}'),[])
        with self.assertRaises(ValueError): parse_indices('no matches')
        with self.assertRaises(ValueError): parse_indices('x[999],16;y[2],16')

    def test_multi_character_javascript_variables_and_spaces(self):
        self.assertEqual(parse_indices('parseInt(token[2],16);parseInt(key[12], 16);key[45],16'),(2,[12,45]))

    def test_search_uses_post_and_preserves_query_and_cursor(self):
        from twikit.client.gql import GQLClient
        install()
        client=GQLClient(None)
        calls=[]
        async def post(endpoint, variables, features):
            calls.append((endpoint,variables)); return {'ok':True}
        client.gql_post=post
        result=asyncio.run(client.search_timeline('$ZIP','Top',20,'next-cursor'))
        self.assertTrue(result['ok'])
        self.assertTrue(calls[0][0].endswith('/SearchTimeline'))
        self.assertEqual(calls[0][1],{'rawQuery':'$ZIP','count':20,'product':'Top',
                                     'querySource':'typed_query','cursor':'next-cursor'})

    def test_old_and_current_user_fields_are_read_without_unrelated_profile_fields(self):
        for user in ({'core':{'screen_name':'example'}},{'legacy':{'screen_name':'example'}}):
            tweet={'rest_id':'2120275391038308499','legacy':{'full_text':'$ZIP earnings beat',
                   'created_at':'Tue Nov 10 22:00:20 +0000 2026','favorite_count':3},
                   'core':{'user_results':{'result':user}}}
            entry={'entryId':'tweet-2120275391038308499','content':{'itemContent':{'tweet_results':{'result':tweet}}}}
            payload={'data':{'timeline':{'instructions':[{'entries':[entry,entry]}]}}}
            posts=public_tweets(payload)
            self.assertEqual(len(posts),1)
            self.assertEqual(posts[0].user.screen_name,'example')
            self.assertEqual(posts[0].text,'$ZIP earnings beat')
            self.assertEqual(posts[0].favorite_count,3)

    def test_unrecognized_tweet_data_does_not_silently_clear_good_cache(self):
        with self.assertRaises(ValueError):
            public_tweets({'entries':[{'entryId':'tweet-123','content':{}}]})
        self.assertEqual(public_tweets({'entries':[{'entryId':'cursor-bottom'}]}),[])


if __name__=='__main__': unittest.main()
