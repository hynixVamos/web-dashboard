"""Persistent Daily/Weekly/Report journal with owner-only editing."""
import base64
import binascii
import json
import hashlib
import hmac
import os
import secrets
import sqlite3
from datetime import date, datetime
from urllib.parse import urlsplit

from flask import Blueprint, jsonify, request
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
from new_highs_data import KST
from new_highs_notes import connection, StorageUnavailable

bp = Blueprint('daily_comments', __name__)
COOKIE = 'daily_comment_session'


def password():
    return os.environ.get('DAILY_COMMENT_PASSWORD', '')


def signer():
    return URLSafeTimedSerializer(hashlib.sha256(('daily-comments-v1:' + password()).encode()).digest())


def identity():
    if not password():
        return None
    try:
        return signer().loads(request.cookies.get(COOKIE, ''), max_age=43200)
    except (BadSignature, SignatureExpired):
        return None


def db_open():
    return connection()


def schema(db):
    db.execute('''CREATE TABLE IF NOT EXISTS daily_comments (
        id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL,
        date TEXT NOT NULL, title TEXT NOT NULL, summary TEXT NOT NULL,
        tickers TEXT NOT NULL, tags TEXT NOT NULL, body TEXT NOT NULL,
        revision INTEGER NOT NULL DEFAULT 1, updated_at TEXT NOT NULL)''')
    if 'photos' not in {row['name'] for row in db.execute('PRAGMA table_info(daily_comments)')}:
        db.execute("ALTER TABLE daily_comments ADD COLUMN photos TEXT NOT NULL DEFAULT '[]'")
    db.execute('''CREATE TABLE IF NOT EXISTS daily_login_failures (attempted_at REAL NOT NULL)''')
    db.commit()


@bp.before_request
def protect():
    if request.method == 'GET':
        return
    origin = request.headers.get('Origin')
    if (origin and urlsplit(origin).netloc != request.host) or request.headers.get('Sec-Fetch-Site') == 'cross-site':
        return jsonify(error='이 사이트에서 다시 시도해 주세요.'), 403
    if not request.is_json:
        return jsonify(error='JSON 요청이 필요합니다.'), 415
    if request.content_length is None or request.content_length > 12000000:
        return jsonify(error='입력 내용이 너무 깁니다.'), 413
    if request.path.endswith('/login'):
        return
    owner = identity()
    if not owner or not hmac.compare_digest(request.headers.get('X-Journal-CSRF', ''), owner['csrf']):
        return jsonify(error='관리자 로그인이 필요합니다.'), 401


@bp.after_request
def private(response):
    response.headers['Cache-Control'] = 'no-store'
    return response


@bp.errorhandler(sqlite3.Error)
@bp.errorhandler(OSError)
@bp.errorhandler(StorageUnavailable)
def unavailable(error):
    return jsonify(error='저장소에 연결하지 못했습니다. 작성 내용을 보관한 채 다시 시도해 주세요.'), 503


@bp.route('/api/daily-comments/session')
def session():
    owner = identity()
    return jsonify(configured=bool(password()), authenticated=bool(owner), csrf=owner['csrf'] if owner else '')


@bp.route('/api/daily-comments/login', methods=['POST'])
def login():
    if not password():
        return jsonify(error='Render 환경 변수 DAILY_COMMENT_PASSWORD를 설정해 주세요.'), 503
    payload = request.get_json(silent=True) or {}
    supplied = payload.get('password', '') if isinstance(payload, dict) else ''
    import time
    now = time.time()
    with db_open() as db, db:
        schema(db)
        db.execute('DELETE FROM daily_login_failures WHERE attempted_at < ?', (now - 300,))
        if db.execute('SELECT count(*) FROM daily_login_failures').fetchone()[0] >= 10:
            return jsonify(error='로그인 시도가 많습니다. 5분 후 다시 시도해 주세요.'), 429
        if not isinstance(supplied, str) or not hmac.compare_digest(supplied.encode(), password().encode()):
            db.execute('INSERT INTO daily_login_failures VALUES (?)', (now,))
            return jsonify(error='비밀번호가 일치하지 않습니다.'), 401
        db.execute('DELETE FROM daily_login_failures')
    owner = {'csrf': secrets.token_urlsafe(32)}
    response = jsonify(authenticated=True, csrf=owner['csrf'])
    response.set_cookie(COOKIE, signer().dumps(owner), max_age=43200, httponly=True,
                        secure=bool(os.environ.get('RENDER')) or request.is_secure, samesite='Lax')
    return response


@bp.route('/api/daily-comments/logout', methods=['POST'])
def logout():
    response = jsonify(ok=True)
    response.delete_cookie(COOKIE)
    return response


def validate(payload):
    if not isinstance(payload, dict):
        raise ValueError('입력 형식이 올바르지 않습니다.')
    values = {}
    for name, limit in [('kind', 6), ('date', 10), ('title', 200), ('summary', 500),
                        ('tickers', 500), ('tags', 500), ('body', 50000)]:
        value = payload.get(name, '')
        if not isinstance(value, str) or len(value) > limit:
            raise ValueError('입력 길이 또는 형식을 확인해 주세요.')
        values[name] = value.strip()
    if values['kind'] not in ('daily', 'weekly', 'report'):
        raise ValueError('Daily, Weekly 또는 레포트 정리를 선택해 주세요.')
    if date.fromisoformat(values['date']).isoformat() != values['date']:
        raise ValueError('날짜를 확인해 주세요.')
    if not values['title'] or not values['body']:
        raise ValueError('제목과 본문을 입력해 주세요.')
    photos = payload.get('photos', [])
    if not isinstance(photos, list) or len(photos) > 8:
        raise ValueError('사진은 최대 8장까지 첨부할 수 있습니다.')
    total = 0
    for photo in photos:
        if not isinstance(photo, str) or len(photo) > 2800000:
            raise ValueError('사진 한 장은 2MB 이하여야 합니다.')
        header, separator, encoded = photo.partition(',')
        if not separator or header not in ('data:image/jpeg;base64', 'data:image/png;base64', 'data:image/webp;base64'):
            raise ValueError('JPG, PNG, WebP 사진만 첨부할 수 있습니다.')
        try:
            raw = base64.b64decode(encoded, validate=True)
        except (ValueError, binascii.Error):
            raise ValueError('사진 형식이 올바르지 않습니다.') from None
        valid = (header == 'data:image/jpeg;base64' and raw.startswith(b'\xff\xd8\xff') or
                 header == 'data:image/png;base64' and raw.startswith(b'\x89PNG\r\n\x1a\n') or
                 header == 'data:image/webp;base64' and raw.startswith(b'RIFF') and raw[8:12] == b'WEBP')
        total += len(raw)
        if not valid or len(raw) > 2000000 or total > 8000000:
            raise ValueError('사진은 한 장 2MB, 전체 8MB 이내로 첨부해 주세요.')
    values['photos'] = json.dumps(photos)
    return values


def serialize_post(row):
    result = dict(row)
    result['photos'] = json.loads(result.get('photos', '[]'))
    return result


@bp.route('/api/daily-comments', methods=['GET', 'POST'])
@bp.route('/api/daily-comments/<int:post_id>', methods=['GET', 'PUT'])
def posts(post_id=None):
    try:
        with db_open() as db, db:
            schema(db)
            if request.method == 'GET':
                if post_id is not None:
                    row = db.execute('SELECT * FROM daily_comments WHERE id=?', (post_id,)).fetchone()
                    return (jsonify(post=serialize_post(row)) if row else (jsonify(error='글을 찾을 수 없습니다.'), 404))
                kind = request.args.get('kind', 'daily')
                rows = db.execute('''SELECT id,kind,date,title,summary,tickers,tags,revision,updated_at
                    FROM daily_comments WHERE kind=? ORDER BY date DESC,id DESC''', (kind,)).fetchall()
                return jsonify(posts=[dict(row) for row in rows])
            payload = request.get_json(silent=True)
            values = validate(payload)
            values['updated_at'] = datetime.now(KST).isoformat(timespec='seconds')
            if post_id is None:
                cursor = db.execute('''INSERT INTO daily_comments
                    (kind,date,title,summary,tickers,tags,body,updated_at,photos)
                    VALUES (:kind,:date,:title,:summary,:tickers,:tags,:body,:updated_at,:photos)''', values)
                post_id = cursor.lastrowid
            else:
                values.update(id=post_id, revision=payload.get('revision'))
                cursor = db.execute('''UPDATE daily_comments SET kind=:kind,date=:date,title=:title,
                    summary=:summary,tickers=:tickers,tags=:tags,body=:body,updated_at=:updated_at,
                    photos=:photos,revision=revision+1 WHERE id=:id AND revision=:revision''', values)
                if not cursor.rowcount:
                    return jsonify(error='다른 창에서 글이 수정되었습니다. 본문을 복사한 뒤 글을 다시 열어 주세요.'), 409
            return jsonify(post=serialize_post(db.execute('SELECT * FROM daily_comments WHERE id=?', (post_id,)).fetchone()))
    except (ValueError, TypeError) as error:
        return jsonify(error=str(error) if isinstance(error, ValueError) and not str(error).startswith('Invalid isoformat') else '날짜, 제목, 본문과 입력 길이를 확인해 주세요.'), 400
