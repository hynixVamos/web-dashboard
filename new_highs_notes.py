"""Append-only manual annotations, separate from replaceable market snapshots."""
from contextlib import contextmanager
from datetime import date, datetime
import os
from pathlib import Path
import re
import sqlite3
import uuid

from new_highs_data import KST, data_dir


class StorageUnavailable(RuntimeError):
    pass


def database_path():
    configured = os.environ.get('NEW_HIGHS_NOTES_DB')
    if os.environ.get('RENDER') and not configured:
        # This service's persistent disk is mounted here (confirmed in Render).
        if os.path.ismount('/var/data'):
            return Path('/var/data/manual_notes.sqlite3')
        raise StorageUnavailable('누적 기록용 영구 저장소 연결이 필요합니다.')
    return Path(configured) if configured else data_dir().parent / 'manual_notes.sqlite3'


@contextmanager
def connection():
    path = database_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(str(path), timeout=15)
    db.row_factory = sqlite3.Row
    try:
        db.execute('''CREATE TABLE IF NOT EXISTS high_notes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            request_id TEXT NOT NULL UNIQUE,
            ticker TEXT NOT NULL,
            report_date TEXT NOT NULL,
            sector TEXT NOT NULL,
            reason TEXT NOT NULL,
            created_at TEXT NOT NULL
        )''')
        db.execute('CREATE INDEX IF NOT EXISTS high_notes_ticker ON high_notes(ticker, id)')
        db.commit()
        yield db
    finally:
        db.close()


def validate_ticker(ticker):
    if not isinstance(ticker, str) or not re.fullmatch(r'[0-9A-Z]{6}', ticker):
        raise ValueError('종목 코드가 올바르지 않습니다.')


def validate_note(ticker, payload):
    validate_ticker(ticker)
    if not isinstance(payload, dict):
        raise ValueError('입력 형식이 올바르지 않습니다.')
    sector, reason = payload.get('sector', ''), payload.get('reason', '')
    if not isinstance(sector, str) or not isinstance(reason, str):
        raise ValueError('섹터와 신고가 이유는 텍스트로 입력해 주세요.')
    sector, reason = sector.strip(), reason.strip()
    if len(sector) > 100 or len(reason) > 5000:
        raise ValueError('섹터는 100자, 신고가 이유는 5,000자까지 입력할 수 있습니다.')
    if not sector and not reason:
        raise ValueError('섹터 또는 신고가 이유를 입력해 주세요.')
    selected = payload.get('date')
    try:
        if not isinstance(selected, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', selected):
            raise ValueError
        if date.fromisoformat(selected) > datetime.now(KST).date():
            raise ValueError
    except ValueError:
        raise ValueError('올바른 리포트 날짜를 선택해 주세요.') from None
    try:
        request_id = str(uuid.UUID(payload.get('request_id', '')))
    except (ValueError, TypeError, AttributeError):
        raise ValueError('저장 요청 식별자가 올바르지 않습니다.') from None
    return (request_id, ticker, selected, sector, reason)


def append_note(ticker, payload):
    values = validate_note(ticker, payload)
    with connection() as db, db:
        db.execute('''INSERT OR IGNORE INTO high_notes
            (request_id, ticker, report_date, sector, reason, created_at)
            VALUES (?, ?, ?, ?, ?, ?)''', (*values, datetime.now(KST).isoformat(timespec='seconds')))
        row = db.execute('SELECT * FROM high_notes WHERE request_id = ?', (values[0],)).fetchone()
        if tuple(row[k] for k in ('request_id', 'ticker', 'report_date', 'sector', 'reason')) != values:
            raise ValueError('저장 요청이 변경됐습니다. 다시 시도해 주세요.')
        return dict(row)


def history(ticker):
    validate_ticker(ticker)
    with connection() as db:
        return [dict(row) for row in db.execute(
            'SELECT * FROM high_notes WHERE ticker = ? ORDER BY id DESC', (ticker,))]


def annotate_report(report):
    """Latest manual sector follows the ticker; reasons belong to the report date."""
    try:
        with connection() as db:
            notes = [dict(row) for row in db.execute('SELECT * FROM high_notes ORDER BY id')]
    except (StorageUnavailable, sqlite3.Error, OSError):
        return {**report, 'notes_available': False}
    sectors, reasons, counts = {}, {}, {}
    for note in notes:
        ticker = note['ticker']
        counts[ticker] = counts.get(ticker, 0) + 1
        if note['sector']:
            sectors[ticker] = note['sector']
        if note['report_date'] == report['date'] and note['reason']:
            reasons.setdefault(ticker, []).append(note['reason'])
    rows = []
    for row in report['rows']:
        ticker = row['ticker']
        rows.append({**row, 'source_sector': row['sector'],
                     'sector': sectors.get(ticker, row['sector']),
                     'manual_sector': sectors.get(ticker, ''),
                     'manual_reasons': reasons.get(ticker, []),
                     'note_count': counts.get(ticker, 0)})
    return {**report, 'rows': rows, 'notes_available': True,
            'saved_sectors': sorted(set(sectors.values()))}
