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
