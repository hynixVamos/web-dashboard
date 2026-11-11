"""Twikit 2.3.3 parser compatibility for X's indexed webpack chunk names.

Based on the format documented in upstream twikit PR #410. Does not alter
authentication, challenge handling, or rate limits.
"""
import re
from types import SimpleNamespace


def ondemand_urls(html):
    hashes = []
    direct = re.search(r'''["']ondemand\.s["']\s*:\s*["']([a-zA-Z0-9_]+)["']''', html)
    if direct:
        hashes.append(direct.group(1))
    for chunk in re.findall(r'''(?:^|[,{])\s*["']?(\d+)["']?\s*:\s*["']ondemand\.s["']''', html):
        match = re.search(r'''(?:^|[,{])\s*["']?''' + re.escape(chunk) + r'''["']?\s*:\s*["']([a-fA-F0-9]{4,40})["']''', html)
        if match:
            hashes.append(match.group(1))
    return list(dict.fromkeys(f'https://abs.twimg.com/responsive-web/client-web/ondemand.s.{h}a.js' for h in hashes))


def parse_indices(script):
    values = [int(value) for value in re.findall(r'\[(\d+)\]\s*,\s*16', script)]
    if len(values) < 2 or any(value > 255 for value in values):
        raise ValueError('X client script indices unavailable')
    return values[0], values[1:]


def install():
    from twikit.x_client_transaction.transaction import ClientTransaction
    from twikit.client.gql import GQLClient, Endpoint, FEATURES

    async def get_indices(self, home_page_response, session, headers):
        page = self.validate_response(home_page_response) or self.home_page_response
        for url in ondemand_urls(str(page)):
            response = await session.request(method='GET', url=url, headers=headers)
            response.raise_for_status()
            try:
                return parse_indices(response.text)
            except ValueError:
                continue
        raise ValueError('X client script format not supported')

    ClientTransaction.get_indices = get_indices

    async def search_timeline(self, query, product, count, cursor=None):
        variables = {'rawQuery': query, 'count': count,
                     'querySource': 'typed_query', 'product': product}
        if cursor is not None:
            variables['cursor'] = cursor
        # Upstream PR #412: X moved search parameters into a POST body.
        return await self.gql_post(Endpoint.SEARCH_TIMELINE, variables, FEATURES)

    GQLClient.search_timeline = search_timeline


def public_tweets(payload):
    """Read the fields we display without Twikit's brittle full User model."""
    def entries(value):
        if isinstance(value, dict):
            for key, child in value.items():
                if key == 'entries' and isinstance(child, list):
                    yield from child
                else:
                    yield from entries(child)
        elif isinstance(value, list):
            for child in value:
                yield from entries(child)
    results, seen = [], set()
    tweet_entries = 0
    for entry in entries(payload):
        if not entry.get('entryId', '').startswith('tweet-'):
            continue
        tweet_entries += 1
        item = entry.get('content', {}).get('itemContent', {})
        result = item.get('tweet_results', {}).get('result', {})
        result = result.get('tweet', result)
        legacy = result.get('legacy', {})
        user = result.get('core', {}).get('user_results', {}).get('result', {})
        author = user.get('core', {}).get('screen_name') or user.get('legacy', {}).get('screen_name')
        text, created = legacy.get('full_text'), legacy.get('created_at')
        post_id = result.get('rest_id') or legacy.get('id_str')
        if not all((author, text, created, post_id)) or post_id in seen:
            continue
        seen.add(post_id)
        results.append(SimpleNamespace(id=post_id, text=text, created_at=created,
                       favorite_count=legacy.get('favorite_count', 0), user=SimpleNamespace(screen_name=author)))
    if tweet_entries and not results:
        raise ValueError('X tweet response format not supported')
    return results


class PublicSearchClient:
    def __init__(self, *args, **kwargs):
        from twikit import Client
        install()
        self.client = Client(*args, **kwargs)

    @property
    def http(self):
        return self.client.http

    def set_cookies(self, cookies):
        self.client.set_cookies(cookies)

    async def search_tweet(self, query, product, count=20):
        payload, _ = await self.client.gql.search_timeline(query, product, count, None)
        return public_tweets(payload)
