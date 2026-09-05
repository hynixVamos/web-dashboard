"""Read-only report contract/cache. Routes never collect external data."""
import json
import os
import re
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import TypedDict, List, Dict, Optional

KST = timezone(timedelta(hours=9))
PERIOD_FIELDS = {'20d': '20d_high', '60d': '60d_high', '52w': '52w_high', 'all': 'all_time_high'}

class HighRow(TypedDict):
    date: str
    ticker: str
    name: str
    sector: str
    close: float
    change_pct: float
    market_cap_eok: float
    trading_value_eok: float
    turnover: float
    is_20d_high: bool
    is_60d_high: bool
    is_52w_high: bool
    is_all_time_high: bool
    new_20d_high: bool
    new_60d_high: bool
    new_52w_high: bool
    new_all_time_high: bool
    consecutive_high_days: int
    consecutive_by_period: Dict[str, int]
    all_time_verified: bool
    history_start: str
    reason: str
    memo: str

def data_dir():
    return Path(os.environ.get('NEW_HIGHS_DATA_DIR', str(Path(__file__).resolve().parent / 'data' / 'new_highs')))

def read_json(path, default):
    try:
        with Path(path).open(encoding='utf-8') as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return default

def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix='.writing-', dir=str(path.parent))
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as handle:
            json.dump(value, handle, ensure_ascii=False, allow_nan=False, separators=(',', ':'))
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)

def read_report(selected_date: Optional[str] = None):
    today = datetime.now(KST).date().isoformat()
    root = data_dir()
    dates = sorted(p.stem for p in (root / 'reports').glob('*.json')
                   if re.fullmatch(r'\d{4}-\d{2}-\d{2}', p.stem) and p.stem <= today)
    selected = selected_date or (dates[-1] if dates else today)
    if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', selected):
        raise ValueError('날짜는 YYYY-MM-DD 형식이어야 합니다.')
    status = read_json(root / 'status.json', {})
    if status.get('state') == 'collecting':
        heartbeat = status.get('heartbeat_at') or status.get('started_at')
        try:
            if (datetime.now(KST)-datetime.fromisoformat(heartbeat)).total_seconds() > 300:
                status = {**status, 'state':'error', 'error':'수집 진행 확인이 오래 중단됐습니다. 다음 실행에서 이어서 수집합니다.'}
        except (ValueError, TypeError):
            pass
    report = read_json(root / 'reports' / (selected + '.json'), None)
    if report is None:
        report = dict(date=selected, rows=[], source='NAVER 시세 / KRX KIND 업종',
                      status='pending' if not dates else 'unavailable', coverage={}, updated_at=None)
    return {**report, 'available_dates': dates, 'today': today, 'is_mock': False,
            'collector': status, 'latest_date': dates[-1] if dates else None}
