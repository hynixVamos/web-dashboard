"""Free public X search snapshots. Cookies remain in environment variables only.

Optional unattended collection uses Twikit and a user-provided X web session,
not the paid X API. HTTP page requests never call X. Missing credentials leave
the last browser-collected snapshot readable.
"""
import asyncio
import json
import os
import re
import threading
from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

from new_highs_data import read_json, write_json

STATIC = Path(__file__).with_name('static')
KST = timezone(timedelta(hours=9))
_started = False
_lock = threading.Lock()


def search_query(ticker, day):
    target = date.fromisoformat(day)
    return (f'${ticker} since:{target-timedelta(days=3)} '
            f'until:{target+timedelta(days=2)} -is:retweet')


def normalize_post(post, ticker):
    """Only publish attributed excerpts with a canonical post URL."""
    text = str(post.get('text') or '').strip()
    author = str(post.get('author') or '').lstrip('@')
    post_id = str(post.get('id') or '')
    if not re.fullmatch(r'\w{1,15}', author) or not re.fullmatch(r'\d{10,25}', post_id):
        return None
    if not re.search(r'(?<!\w)\$' + re.escape(ticker) + r'(?![\w-])', text, re.I):
        return None
    if re.search(r'whatsapp|wa\.me/|join.{0,30}(?:group|signals)|t\.me/', text, re.I):
        return None
    try:
        created = datetime.fromisoformat(str(post.get('created_at', '')).replace('Z', '+00:00'))
        if created.tzinfo is None:
            return None
        likes = max(0, int(post.get('likes') or 0))
    except (ValueError, TypeError):
        return None
    words = text.split()
    return {'id': post_id, 'author': author, 'url': f'https://x.com/{author}/status/{post_id}',
            'text': ' '.join(words[:25]), 'excerpt': True,
            'created_at': created.isoformat(), 'likes': likes}


async def collect(report, client, directory=None, now=None, delay=5, refresh=False):
    """A bounded daily Top 20 pass; keep good snapshots if a session fails."""
    now = now or datetime.now(timezone.utc)
    directory = Path(directory or STATIC)
    day = report['date']
    path = directory / f'x-comments-{date.fromisoformat(day).isoformat()}.json'
    saved = read_json(path, {'date': day, 'tickers': {}})
    rows = sorted((r for r in report.get('rows', []) if r.get('change_pct', 0) > 0),
                  key=lambda r: (-r['change_pct'], r['ticker']))[:20]
    for row in rows:
        ticker = row['ticker']
        previous = saved['tickers'].get(ticker, {})
        # One successful search per report date; failed searches retry after 1h.
        if not refresh and previous.get('status') in ('ok', 'empty'):
            continue
        attempted = previous.get('attempted_at')
        if not refresh and attempted and now - datetime.fromisoformat(attempted) < timedelta(hours=1):
            continue
        try:
            tweets = await client.search_tweet(search_query(ticker, day), 'Top', count=20)
            posts, seen = [], set()
            for tweet in tweets:
                raw = {'id': tweet.id, 'author': tweet.user.screen_name,
                       'text': tweet.text, 'likes': tweet.favorite_count,
                       'created_at': parsedate_to_datetime(tweet.created_at).isoformat()}
                post = normalize_post(raw, ticker)
                if post and post['id'] not in seen:
                    seen.add(post['id']); posts.append(post)
                if len(posts) == 5:
                    break
            saved['tickers'][ticker] = {'status': 'ok' if posts else 'empty',
                                        'attempted_at': now.isoformat(), 'posts': posts}
        except Exception as error:
            # Do not serialize provider error text: it may contain session details.
            name = type(error).__name__.lower()
            kind = 'session' if any(s in name for s in ('unauthorized', 'forbidden', 'suspended')) else 'unavailable'
            saved['tickers'][ticker] = {**previous, 'status': 'error', 'error': kind,
                                       'attempted_at': now.isoformat(), 'posts': previous.get('posts', [])}
            saved['collector_status'] = kind
            write_json(path, saved)
            break  # Stop on rate limits, login challenges or provider outages.
        saved.update(collected_at=now.isoformat(), source='X public search (web session)', collector_status='ready')
        write_json(path, saved)
        if delay:
            await asyncio.sleep(delay)
    return saved


def run_once(refresh=False):
    raw = os.environ.get('X_SESSION_COOKIES_JSON', '')
    if not raw:
        return 'not_configured'
    cookies = json.loads(raw)
    if not isinstance(cookies, dict) or not cookies.get('auth_token') or not cookies.get('ct0'):
        raise ValueError('X web session configuration is incomplete')
    from x_web_compat import PublicSearchClient
    from us_new_highs import read_report
    report = read_report()
    if not report.get('rows'):
        return 'waiting_for_report'

    async def run():
        client = PublicSearchClient('en-US', timeout=30)
        client.set_cookies(cookies)
        try:
            return await collect(report, client, refresh=refresh)
        finally:
            await client.http.aclose()
    return asyncio.run(run())


def due_slot(now, times=None, weekdays=None):
    """Korean wall-clock slots, with a 10-minute startup grace window."""
    local = now.astimezone(KST)
    times = times if times is not None else os.environ.get('X_REFRESH_TIMES_KST', '08:00,13:00')
    weekdays = weekdays if weekdays is not None else os.environ.get('X_REFRESH_WEEKDAYS_KST', '2,3,4,5,6')
    if local.isoweekday() not in {int(d.strip()) for d in weekdays.split(',') if d.strip()}:
        return None
    slots = []
    for value in times.split(','):
        value = value.strip()
        if not re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d', value):
            raise ValueError('X refresh times must use HH:MM')
        hour, minute = map(int, value.split(':'))
        slot = local.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if timedelta(0) <= local - slot < timedelta(minutes=10):
            slots.append(slot.isoformat())
    return max(slots) if slots else None


def run_scheduled(now=None, state_dir=None, runner=None):
    """Claim a slot on disk before network I/O, so restarts never repeat it."""
    now = now or datetime.now(timezone.utc)
    slot = due_slot(now)
    if slot is None:
        return 'outside_schedule'
    from us_new_highs import root_dir
    from new_highs_tracker import collection_lock
    directory = Path(state_dir or root_dir() / 'x-comment-schedule')
    with collection_lock(directory) as acquired:
        if not acquired:
            return 'already_running'
        path = directory / 'schedule.json'
        state = read_json(path, {})
        if state.get('last_slot', '') >= slot:
            return 'already_attempted'
        state = {'last_slot': slot, 'started_at': now.isoformat(), 'status': 'running'}
        write_json(path, state)
        try:
            result = (runner or run_once)(refresh=True)
            state['status'] = result if isinstance(result, str) else result.get('collector_status', 'ready')
        except Exception:
            state['status'] = 'error'  # Never log a session-bearing exception.
        write_json(path, state)
        return state['status']


def _loop():
    while True:
        try:
            run_scheduled()
        except Exception:
            # Never log credentials or raw HTTP exception payloads.
            pass
        # Clock checks are local; no X requests happen outside a due slot.
        threading.Event().wait(30)


def start_background_refresh():
    global _started
    with _lock:
        if _started or not os.environ.get('X_SESSION_COOKIES_JSON'):
            return
        _started = True
    threading.Thread(target=_loop, name='x-comments-refresh', daemon=True).start()


if __name__ == '__main__':
    result = run_once()
    print(result if isinstance(result, str) else 'X comments snapshot updated')
