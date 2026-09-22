(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  let report = JSON.parse($('us-initial').textContent), serial = 0, latest = true;
  const num = n => Number.isFinite(n) ? n.toLocaleString('ko-KR', {maximumFractionDigits: 2}) : '—';
  const node = (tag, text) => { const n = document.createElement(tag); n.textContent = text; return n; };
  function render() {
    const sector = $('us-sector').value, query = $('us-search').value.trim().toLowerCase();
    const sectors = [...new Set(report.rows.map(r => r.sector))].sort();
    $('us-sector').replaceChildren(new Option('전체', ''), ...sectors.map(s => new Option(s, s)));
    if (sectors.includes(sector)) $('us-sector').value = sector;
    const base = report.rows.filter(r => (!query || `${r.ticker} ${r.name}`.toLowerCase().includes(query)) &&
      (!$('us-close-only').checked || r.close > r.previous_high));
    const counts = new Map(); base.forEach(r => counts.set(r.sector, (counts.get(r.sector) || 0) + 1));
    $('us-chips').replaceChildren(...[['', base.length], ...[...counts].sort((a,b) => b[1]-a[1])].map(([s,c]) => {
      const b = node('button', `${s || '전체'} ${c}`); b.className = 'chip';
      b.setAttribute('aria-pressed', String(s === $('us-sector').value));
      b.onclick = () => { $('us-sector').value = s; render(); }; return b;
    }));
    const sort = $('us-sort').value;
    const rows = base.filter(r => !$('us-sector').value || r.sector === $('us-sector').value)
      .sort((a,b) => (sort === 'ticker' ? 0 : b[sort]-a[sort]) || a.ticker.localeCompare(b.ticker));
    $('us-rows').replaceChildren(...rows.map(r => {
      const tr = document.createElement('tr');
      [r.sector, r.ticker, r.name, num(r.close), `${r.change_pct > 0 ? '+' : ''}${num(r.change_pct)}%`,
       num(r.high), num(r.previous_high), `${num(r.pullback_pct)}%`, num(r.volume)].forEach((v,i) => {
        const td = node('td', v); if (i < 3) td.className = 'align-left';
        if (i === 4) td.className = r.change_pct >= 0 ? 'pos' : 'neg'; tr.append(td);
      }); return tr;
    }));
    if (!rows.length) {
      const tr = document.createElement('tr'), td = node('td', report.status === 'pending' ?
        '미국 시장 첫 수집 대기 중입니다. 수집이 진행되면 자동 표시됩니다.' : report.status === 'unavailable' ?
        '선택한 거래일의 저장된 리포트가 없습니다.' : '현재 수집된 데이터에서 조건에 맞는 종목이 없습니다.');
      td.colSpan = 9; td.className = 'empty'; tr.append(td); $('us-rows').append(tr);
    }
    const c = report.coverage || {}, run = report.collector || {};
    let status = report.updated_at ? `수집 ${new Date(report.updated_at).toLocaleString('ko-KR')} (한국 시간)` : '첫 수집 대기';
    if (c.total) status += ` · 처리 ${c.evaluated}/${c.total} · 이력 부족 등 제외 ${c.excluded} · 대기 ${c.pending} · 실패 ${c.failed}`;
    if (report.status === 'partial') status += ' · 수집 중이거나 일부 실패한 불완전한 목록입니다';
    if (run.state === 'collecting') status += ` · 진행 ${run.processed || 0}/${run.total || '…'}`;
    if (run.state === 'error') status += ` · 오류: ${run.error}`;
    $('us-status').textContent = status;
    $('us-count').textContent = `${report.date} 미국 거래일 · 표시 ${rows.length} / 수집된 신고가 ${report.rows.length} 종목`;
    $('us-date').value = report.date; $('us-date').max = report.today;
    $('us-prev').disabled = !report.available_dates.some(d => d < report.date);
    $('us-next').disabled = !report.available_dates.some(d => d > report.date);
  }
  async function load(selected) {
    const id = ++serial;
    $('us-status').textContent = '리포트를 불러오는 중…';
    try {
      const response = await fetch('/api/us-new-highs' + (selected ? `?date=${encodeURIComponent(selected)}` : ''),
        {cache: 'no-store', signal: AbortSignal.timeout(15000)});
      if (!response.ok) throw new Error('request failed');
      const data = await response.json(); if (id !== serial) return;
      report = data; render();
    } catch (error) {
      if (id !== serial) return;
      $('us-date').value = report.date;
      $('us-status').textContent = '조회에 실패했습니다. 아래는 이전 조회 결과입니다. 새로고침을 눌러 다시 시도하세요.';
    }
  }
  ['us-sector','us-search','us-close-only','us-sort'].forEach(id => $(id).addEventListener('input', render));
  $('us-date').onchange = () => { latest = false; load($('us-date').value); };
  $('us-prev').onclick = () => { latest = false; load(report.available_dates.filter(d => d < report.date).pop()); };
  $('us-next').onclick = () => { latest = false; load(report.available_dates.find(d => d > report.date)); };
  $('us-latest').onclick = () => { latest = true; load(); };
  $('us-refresh').onclick = () => load(latest ? null : report.date);
  $('us-reset').onclick = () => { $('us-sector').value = ''; $('us-search').value = ''; $('us-close-only').checked = false; $('us-sort').value = 'change_pct'; render(); };
  setInterval(() => { if (!document.hidden) load(latest ? null : report.date); }, 60000);
  render();
})();
