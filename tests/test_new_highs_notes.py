import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import uuid

import app as dashboard
from new_highs_notes import append_note, history, annotate_report, database_path, StorageUnavailable


class NoteTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {'NEW_HIGHS_NOTES_DB': str(Path(self.tmp.name) / 'notes.sqlite3')})
        self.env.start()
        self.client = dashboard.app.test_client()

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def payload(self, **changes):
        return dict(date='2026-09-04', sector='반도체', reason='수주 확대', request_id=str(uuid.uuid4()), **changes)

    def test_append_survives_reopen_and_market_report_replacement(self):
        first = self.payload()
        append_note('005930', first)
        second = {**self.payload(), 'sector': 'AI 반도체', 'reason': '실적 개선'}
        append_note('005930', second)
        append_note('005930', {**self.payload(), 'date': '2026-09-05', 'sector': '', 'reason': '다음 날 이유'})
        self.assertEqual(len(history('005930')), 3)
        source = {'date': '2026-09-04', 'rows': [{'ticker': '005930', 'sector': '전자', 'reason': '', 'memo': ''}]}
        result = annotate_report(source)
        self.assertEqual(result['rows'][0]['sector'], 'AI 반도체')
        self.assertEqual(result['rows'][0]['manual_reasons'], ['수주 확대', '실적 개선'])
        self.assertEqual(source['rows'][0]['sector'], '전자')
        later = annotate_report({**source, 'date': '2026-09-06'})
        self.assertEqual(later['rows'][0]['manual_reasons'], [])
        self.assertEqual(later['rows'][0]['sector'], 'AI 반도체')
        self.assertEqual(later['rows'][0]['note_count'], 3)

    def test_retry_does_not_duplicate_and_changed_request_is_rejected(self):
        payload = self.payload()
        a = append_note('005930', payload)
        b = append_note('005930', payload)
        self.assertEqual(a['id'], b['id'])
        with self.assertRaises(ValueError):
            append_note('005930', {**payload, 'reason': '변경'})
        self.assertEqual(len(history('005930')), 1)

    def test_concurrent_entries_are_all_preserved(self):
        history('005930')
        with ThreadPoolExecutor(max_workers=6) as executor:
            list(executor.map(lambda _: append_note('005930', self.payload()), range(12)))
        self.assertEqual(len(history('005930')), 12)

    def test_sector_only_reason_only_and_ticker_isolation(self):
        append_note('005930', {**self.payload(), 'reason': ''})
        append_note('005930', {**self.payload(), 'sector': ''})
        self.assertEqual(len(history('005930')), 2)
        self.assertEqual(history('000660'), [])

    def test_routes_invalid_input_origin_and_storage_failure(self):
        url = '/api/new-highs/005930/notes'
        self.assertEqual(self.client.post(url, json=self.payload()).status_code, 201)
        self.assertEqual(len(self.client.get(url).get_json()['notes']), 1)
        for delta in ({'sector': '', 'reason': ' '}, {'reason': []}, {'sector': 'x'*101},
                      {'reason': 'x'*5001}, {'date': '2099-01-01'}, {'date': '2026-02-30'}, {'request_id': 'bad'}):
            self.assertEqual(self.client.post(url, json={**self.payload(), **delta}).status_code, 400)
        self.assertEqual(self.client.post(url, json=[]).status_code, 400)
        self.assertEqual(self.client.post(url, data='plain').status_code, 415)
        self.assertEqual(self.client.post(url, json=self.payload(), headers={'Origin': 'https://other.example'}).status_code, 403)
        self.assertEqual(self.client.post('/api/new-highs/invalid/notes', json=self.payload()).status_code, 400)
        with patch('new_highs_notes.database_path', side_effect=StorageUnavailable):
            self.assertEqual(self.client.post(url, json=self.payload()).status_code, 503)

    def test_render_cannot_silently_use_ephemeral_default(self):
        with patch.dict(os.environ, {'RENDER': 'true'}):
            with patch.dict(os.environ):
                os.environ.pop('NEW_HIGHS_NOTES_DB', None)
                with patch('new_highs_notes.os.path.ismount', return_value=False), self.assertRaises(StorageUnavailable):
                    database_path()

    def test_render_uses_confirmed_persistent_mount_without_extra_configuration(self):
        with patch.dict(os.environ, {'RENDER': 'true'}), patch('new_highs_notes.os.path.ismount', return_value=True):
            os.environ.pop('NEW_HIGHS_NOTES_DB', None)
            self.assertEqual(database_path(), Path('/var/data/manual_notes.sqlite3'))

    def test_report_api_reloads_saved_annotations(self):
        raw = {'date': '2026-09-04', 'rows': [{'ticker': '005930', 'sector': '전자', 'reason': '', 'memo': ''}]}
        with patch.dict(dashboard.app.config, {'NEW_HIGHS_PROVIDER': lambda selected: raw}):
            self.client.post('/api/new-highs/005930/notes', json=self.payload())
            other_client = dashboard.app.test_client()
            result = other_client.get('/api/new-highs?date=2026-09-04').get_json()
            self.assertEqual(result['rows'][0]['sector'], '반도체')
            self.assertEqual(result['rows'][0]['manual_reasons'], ['수주 확대'])
            self.assertEqual(result['rows'][0]['note_count'], 1)


if __name__ == '__main__':
    unittest.main()
