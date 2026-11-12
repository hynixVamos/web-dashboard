import os
import tempfile
import unittest
from unittest.mock import patch
from flask import Flask
from daily_comments import bp


class JournalTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {'NEW_HIGHS_NOTES_DB': self.tmp.name+'/notes.db',
                                         'DAILY_COMMENT_PASSWORD': 'test-password-only', 'RENDER': ''})
        self.env.start()
        self.app = Flask(__name__)
        self.app.register_blueprint(bp)
        self.client = self.app.test_client()
        self.post = dict(kind='daily', date='2026-11-11', title='시장 복기', summary='반도체',
                         tickers='MU, SK하이닉스', tags='실적', body='**관찰**\n<script>alert(1)</script>')

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def login(self):
        response=self.client.post('/api/daily-comments/login',json={'password':'test-password-only'})
        self.assertEqual(response.status_code,200)
        return {'X-Journal-CSRF':response.json['csrf']}

    def test_public_read_owner_write_persistence_and_conflict(self):
        self.assertEqual(self.client.post('/api/daily-comments',json=self.post).status_code,401)
        headers=self.login()
        self.assertEqual(self.client.post('/api/daily-comments',json=self.post).status_code,401)
        saved=self.client.post('/api/daily-comments',json=self.post,headers=headers).json['post']
        public=self.app.test_client()
        self.assertEqual(public.get('/api/daily-comments').json['posts'][0]['title'],self.post['title'])
        path='/api/daily-comments/'+str(saved['id'])
        self.assertEqual(public.get(path).json['post']['body'],self.post['body'])
        changed={**self.post,'title':'수정된 글','revision':saved['revision']}
        self.assertEqual(self.client.put(path,json=changed,headers=headers).status_code,200)
        self.assertEqual(self.client.put(path,json=changed,headers=headers).status_code,409)
        self.assertEqual(public.get('/api/daily-comments?kind=weekly').json['posts'],[])
        self.client.post('/api/daily-comments/logout',json={},headers=headers)
        self.assertEqual(self.client.put(path,json=changed,headers=headers).status_code,401)

    def test_report_separate_storage_editing_and_permissions(self):
        headers = self.login()
        daily = self.client.post('/api/daily-comments', json=self.post, headers=headers).json['post']
        report = {**self.post, 'kind': 'report', 'title': '산업 레포트', 'tags': '반도체'}
        response = self.client.post('/api/daily-comments', json=report, headers=headers)
        self.assertEqual(response.status_code, 200)
        saved = response.json['post']
        public = self.app.test_client()
        self.assertEqual([p['id'] for p in public.get('/api/daily-comments?kind=report').json['posts']], [saved['id']])
        self.assertEqual([p['id'] for p in public.get('/api/daily-comments?kind=daily').json['posts']], [daily['id']])
        self.assertEqual(public.get('/api/daily-comments?kind=weekly').json['posts'], [])
        path = '/api/daily-comments/' + str(saved['id'])
        changed = {**report, 'body': '수정된 핵심 내용', 'revision': saved['revision']}
        self.assertEqual(public.put(path, json=changed).status_code, 401)
        self.assertEqual(self.client.put(path, json=changed, headers=headers).status_code, 200)
        self.assertEqual(public.get(path).json['post']['body'], changed['body'])

    def test_report_photos_multiple_same_day_and_edit(self):
        import base64
        photo = 'data:image/png;base64,' + base64.b64encode(bytes.fromhex('89504e470d0a1a0a')).decode()
        headers = self.login()
        payload = {**self.post, 'kind': 'report', 'photos': [photo, photo]}
        first = self.client.post('/api/daily-comments', json=payload, headers=headers).json['post']
        second = self.client.post('/api/daily-comments', json={**payload, 'title': '두 번째 레포트'}, headers=headers).json['post']
        self.assertNotEqual(first['id'], second['id'])
        public = self.app.test_client()
        listing = public.get('/api/daily-comments?kind=report').json['posts']
        self.assertEqual(len(listing), 2)
        self.assertEqual(listing[0]['id'], second['id'])
        self.assertNotIn('photos', listing[0])
        path = '/api/daily-comments/' + str(first['id'])
        self.assertEqual(public.get(path).json['post']['photos'], [photo, photo])
        changed = {**payload, 'photos': [photo], 'revision': first['revision']}
        self.assertEqual(self.client.put(path, json=changed, headers=headers).status_code, 200)
        self.assertEqual(public.get(path).json['post']['photos'], [photo])
        self.assertEqual(public.post('/api/daily-comments', json=payload).status_code, 401)
        for photos in ([photo]*9, ['data:image/svg+xml;base64,PHN2Zz4='], ['data:image/png;base64,bad'], ['data:image/jpeg;base64,SGVsbG8=']):
            self.assertEqual(self.client.post('/api/daily-comments', json={**payload, 'photos': photos}, headers=headers).status_code, 400)
        self.assertEqual(public.get(path).json['post']['photos'], [photo])

    def test_existing_database_migrates_without_losing_posts(self):
        from daily_comments import db_open, schema
        with db_open() as db:
            db.execute("CREATE TABLE daily_comments (id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL, date TEXT NOT NULL, title TEXT NOT NULL, summary TEXT NOT NULL, tickers TEXT NOT NULL, tags TEXT NOT NULL, body TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 1, updated_at TEXT NOT NULL)")
            db.execute("INSERT INTO daily_comments (kind,date,title,summary,tickers,tags,body,updated_at) VALUES ('daily','2026-11-11','기존 글','','','','보관할 본문','2026-11-11')")
            db.commit()
            schema(db)
            schema(db)
        post = self.client.get('/api/daily-comments/1').json['post']
        self.assertEqual(post['body'], '보관할 본문')
        self.assertEqual(post['photos'], [])

    def test_bad_input_and_cross_site(self):
        headers=self.login()
        self.assertEqual(self.client.post('/api/daily-comments',json=self.post,headers={**headers,'Origin':'https://evil.invalid'}).status_code,403)
        for changed in ({'date':'bad'},{'body':''},{'title':'x'*201},{'kind':'other'}):
            self.assertEqual(self.client.post('/api/daily-comments',json={**self.post,**changed},headers=headers).status_code,400)

    def test_password_change_revokes_session_and_no_secret_exposure(self):
        self.login()
        self.assertTrue(self.client.get('/api/daily-comments/session').json['authenticated'])
        with patch.dict(os.environ,{'DAILY_COMMENT_PASSWORD':'changed-test-password'}):
            self.assertFalse(self.client.get('/api/daily-comments/session').json['authenticated'])
        self.assertNotIn('test-password-only',self.client.get('/api/daily-comments/session').text)
        self.assertIn('no-store',self.client.get('/api/daily-comments/session').headers['Cache-Control'])

    def test_login_throttled(self):
        for _ in range(10):
            self.assertEqual(self.client.post('/api/daily-comments/login',json={'password':'bad'}).status_code,401)
        self.assertEqual(self.client.post('/api/daily-comments/login',json={'password':'test-password-only'}).status_code,429)


if __name__=='__main__':
    unittest.main()
