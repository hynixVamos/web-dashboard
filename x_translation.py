"""Best-effort Korean translations of public excerpts, cached on the data disk.

Only saved public text is sent to MyMemory; never X session cookies. Page
requests read the cache and enqueue work, without waiting for a provider.
"""
import copy
import hashlib
import html
import re
import threading
import time
from queue import Queue, Full

import requests
from new_highs_data import read_json, write_json

_queue = Queue(maxsize=100)
_pending = set()
_lock = threading.Lock()
_started = False


def cache_path(text):
    from x_comments import snapshot_dir
    digest = hashlib.sha256(('ko-v1:' + text).encode()).hexdigest()
    return snapshot_dir() / 'translations' / (digest + '.json')


def translate(text):
    # MyMemory's public GET API accepts at most 500 UTF-8 bytes per query.
    if len(text.encode('utf-8')) > 500:
        raise ValueError('Excerpt exceeds translation limit')
    response = requests.get('https://api.mymemory.translated.net/get',
                            params={'q': text, 'langpair': 'en|ko'}, timeout=12)
    response.raise_for_status()
    payload = response.json()
    if payload.get('quotaFinished') or str(payload.get('responseStatus')) != '200':
        raise ValueError('Translation unavailable')
    result = html.unescape(payload['responseData']['translatedText']).strip()
    if not result or not re.search('[가-힣]', result):
        raise ValueError('No Korean translation returned')
    return result


def _work():
    while True:
        text, path = _queue.get()
        try:
            saved = read_json(path, {})
            if not saved.get('text_ko') and saved.get('retry_at', 0) <= time.time():
                try:
                    translated = translate(text)
                    write_json(path, {'text_ko': translated, 'provider': 'MyMemory'})
                except Exception:
                    # Do not expose provider errors or hammer the daily quota.
                    write_json(path, {'retry_at': time.time() + 86400})
        except OSError:
            pass
        finally:
            with _lock:
                _pending.discard(str(path))
            _queue.task_done()
        time.sleep(0.2)


def _enqueue(text, path):
    global _started
    key = str(path)
    with _lock:
        if key in _pending:
            return True
        try:
            _queue.put_nowait((text, path))
        except Full:
            return False
        _pending.add(key)
        if not _started:
            threading.Thread(target=_work, name='x-korean-translations', daemon=True).start()
            _started = True
    return True


def translated_snapshot(snapshot):
    result = copy.deepcopy(snapshot)
    pending = False
    for entry in result.get('tickers', {}).values():
        for post in entry.get('posts', [])[:5]:
            text = post.get('text', '')
            if not isinstance(text, str) or not text.strip():
                continue
            if post.get('text_ko'):
                post['translation_status'] = 'ready'
                continue
            # Korean posts already have their original language intact.
            if re.search('[가-힣]', text) and not re.search('[A-Za-z]{3,}',
                    re.sub(r'https?://\S+|\$[A-Za-z]+|@[A-Za-z0-9_]+', '', text)):
                post['text_ko'] = text
                post['translation_status'] = 'original_ko'
                continue
            path = cache_path(text)
            cached = read_json(path, {})
            if cached.get('text_ko'):
                post.update(text_ko=cached['text_ko'], translation_status='ready')
            elif cached.get('retry_at', 0) > time.time():
                post['translation_status'] = 'unavailable'
            elif len(text.encode('utf-8')) > 500:
                post['translation_status'] = 'unavailable'
            else:
                queued = _enqueue(text, path)
                post['translation_status'] = 'pending' if queued else 'unavailable'
                pending = pending or queued
    if any(entry.get('posts') for entry in result.get('tickers', {}).values()):
        result['translation_pending'] = pending
    return result
