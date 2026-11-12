function usResearchLinks(ticker, day) {
  // Use the selected report's calendar date, independent of the visitor's timezone.
  const offset = days => {
    const value = new Date(`${day}T12:00:00Z`);
    value.setUTCDate(value.getUTCDate() + days);
    return value.toISOString().slice(0, 10);
  };
  const since = offset(-3), until = offset(2);
  const window = `since:${since} until:${until}`;
  const cashtag = `$${ticker}`;
  const x = query => `https://x.com/search?${new URLSearchParams({q: query, src: 'typed_query', f: 'live'})}`;
  return [
    ['X 검색', x(`${cashtag} ${window}`)],
    ['X 상승 배경', x(`${cashtag} (earnings OR guidance OR upgrade OR contract OR approval OR catalyst) ${window}`)],
    ['X 한국어', x(`${cashtag} lang:ko ${window}`)],
    ['관련 뉴스 검색', `https://www.google.com/search?${new URLSearchParams({q: `${ticker} stock after:${since} before:${until}`, tbm: 'nws'})}`]
  ];
}
if (typeof module !== 'undefined' && module.exports) module.exports = {usResearchLinks};

if (typeof document !== 'undefined') (() => {
  'use strict';
  const $ = id => document.getElementById(id);
  let report = JSON.parse($('us-initial').textContent), serial = 0, latest = true;
  const num = n => Number.isFinite(n) ? n.toLocaleString('ko-KR', {maximumFractionDigits: 2}) : '—';
  const node = (tag, text) => { const n = document.createElement(tag); n.textContent = text; return n; };
  const commentCache = new Map(), commentSlots = new Map();
  function drawComments(slot, ticker, selected) {
    slot.replaceChildren();
    const cached = commentCache.get(selected);
    if (!cached || cached.state === 'loading') { slot.append(node('p', 'X 코멘트를 불러오는 중…')); return; }
    const snapshot = cached.data, entry = snapshot?.tickers?.[ticker];
    if (!entry) {
      slot.append(node('p', '이 거래일의 수집된 코멘트가 아직 없습니다. X 검색에서 확인할 수 있습니다.'));
      return;
    }
    const posts = (entry.posts || []).filter(p => /^https:\/\/x\.com\/[A-Za-z0-9_]{1,15}\/status\/\d+$/.test(p.url || '') && typeof p.text === 'string').slice(0, 5);
    slot.append(node('strong', `X 코멘트 ${posts.length} · 원문 발췌`));
    if (!posts.length) slot.append(node('p', entry.status === 'empty' ? '해당 검색에서 표시할 관련 코멘트를 찾지 못했습니다.' : 'X 수집을 완료하지 못했습니다. 기존 검색 링크를 이용하세요.'));
    posts.forEach(p => {
      const card = node('article', ''), heading = node('a', `@${p.author}`);
      card.className = 'us-x-post'; heading.href = p.url; heading.target = '_blank'; heading.rel = 'noopener noreferrer';
      const date = new Date(p.created_at);
      const stamp = Number.isFinite(date.getTime()) ? date.toLocaleDateString('ko-KR', {timeZone:'America/New_York',month:'2-digit',day:'2-digit'}) : '';
      card.append(heading, node('small', ` ${stamp} · ♥${num(p.likes)} `), node('p', `${p.text} …`));
      slot.append(card);
    });
    if (entry.status === 'error') slot.append(node('small', '재조회에 실패해 이전 수집 내용을 표시합니다.'));
    if (snapshot.collected_at) slot.append(node('small', `수집 ${new Date(snapshot.collected_at).toLocaleString('ko-KR', {timeZone:'Asia/Seoul'})} (한국 시간) · 저장된 코멘트`));
  }
  async function loadComments(selected) {
    const current = commentCache.get(selected);
    if (current && Date.now() - current.at < 300000) return;
    commentCache.set(selected, {state:'loading',at:Date.now()});
    try {
      const response = await fetch(`/api/us-new-highs/x-comments?date=${encodeURIComponent(selected)}`, {cache:'no-store',signal:AbortSignal.timeout(15000)});
      if (!response.ok) throw new Error('Snapshot unavailable');
      const data = await response.json();
      if (data.date !== selected || !data.tickers) throw new Error('Invalid snapshot');
      commentCache.set(selected,{state:'ready',at:Date.now(),data});
    } catch (_) { commentCache.set(selected,{state:'unavailable',at:Date.now()}); }
    if (report.date === selected) commentSlots.forEach((slot,ticker)=>drawComments(slot,ticker,selected));
  }
  const filters = document.querySelector('.nh-filters');
  const toggle = (id, text, checked = false) => {
    const label = node('label', ''), input = document.createElement('input');
    input.type = 'checkbox'; input.id = id; input.checked = checked;
    label.append(input, document.createTextNode(` ${text}`)); filters.append(label);
  };
  toggle('us-positive-only', '상승 종목만');
  toggle('us-top20', '상승률 Top 20', true);
  const volumeLabel = node('label', '최소 거래량(주) '), volumeInput = document.createElement('input');
  volumeInput.type = 'number'; volumeInput.min = '0'; volumeInput.step = '10000';
  volumeInput.id = 'us-min-volume'; volumeInput.placeholder = '0';
  volumeLabel.append(volumeInput); filters.append(volumeLabel);
  const help = node('p', 'X 코멘트는 공개 게시물의 저장된 발췌이며 작성자 링크에서 원문을 볼 수 있습니다. 미수집 종목은 X 검색을 이용하세요. 코멘트는 작성자의 의견이며 검증된 상승 원인이 아닙니다.');
  help.className = 'nh-legend'; $('us-count').before(help);
  const style = node('style', '.us-research{text-align:left;white-space:normal;background:var(--panel)}.us-research a{display:inline-block;padding:4px 7px;margin:2px;border:1px solid var(--border);border-radius:4px;color:var(--accent);text-decoration:none}.us-research a:hover{background:var(--accent-soft)}.us-x-comments{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:4px 12px;margin:8px 0;font-size:12px;line-height:1.7}.us-x-comments>strong,.us-x-comments>small,.us-x-comments>p{grid-column:1/-1}.us-x-comments p{margin:5px 0;overflow-wrap:anywhere}.us-x-comments small{color:var(--text-secondary)}.us-x-post{border-left:2px solid var(--accent);padding:3px 9px;margin:6px 0}.us-x-post a{border:0;margin:0;padding:0;font-weight:600}.us-x-post p{white-space:normal}.us-x-comments>strong{color:var(--accent)}.us-stock-row td{font-weight:500}.us-research-row{cursor:default!important}');
  document.head.append(style);
  const diagnostics = node('details', '');
  diagnostics.append(node('summary', '수집 실패 원인 / 재시도 현황'));
  const diagnosticBody = node('div', ''); diagnosticBody.id = 'us-diagnostics';
  diagnostics.append(diagnosticBody); diagnostics.className = 'nh-legend';
  $('us-status').after(diagnostics);
  function render() {
    commentSlots.clear();
    const sector = $('us-sector').value, query = $('us-search').value.trim().toLowerCase();
    const sectors = [...new Set(report.rows.map(r => r.sector))].sort();
    $('us-sector').replaceChildren(new Option('전체', ''), ...sectors.map(s => new Option(s, s)));
    if (sectors.includes(sector)) $('us-sector').value = sector;
    const base = report.rows.filter(r => (!query || `${r.ticker} ${r.name}`.toLowerCase().includes(query)) &&
      (!$('us-close-only').checked || r.close > r.previous_high) &&
      (!$('us-positive-only').checked || r.change_pct > 0) &&
      r.volume >= Math.max(0, Number($('us-min-volume').value) || 0));
    const counts = new Map(); base.forEach(r => counts.set(r.sector, (counts.get(r.sector) || 0) + 1));
    $('us-chips').replaceChildren(...[['', base.length], ...[...counts].sort((a,b) => b[1]-a[1])].map(([s,c]) => {
      const b = node('button', `${s || '전체'} ${c}`); b.className = 'chip';
      b.setAttribute('aria-pressed', String(s === $('us-sector').value));
      b.onclick = () => { $('us-sector').value = s; render(); }; return b;
    }));
    const sort = $('us-sort').value;
    let rows = base.filter(r => !$('us-sector').value || r.sector === $('us-sector').value)
      .sort((a,b) => (sort === 'ticker' ? 0 : b[sort]-a[sort]) || a.ticker.localeCompare(b.ticker));
    if ($('us-top20').checked) rows = rows.filter(r => r.change_pct > 0)
      .sort((a,b) => b.change_pct-a.change_pct || a.ticker.localeCompare(b.ticker)).slice(0, 20);
    $('us-sort').disabled = $('us-top20').checked;
    $('us-rows').replaceChildren(...rows.flatMap(r => {
      const tr = document.createElement('tr');
      tr.className = 'us-stock-row';
      [r.sector, r.ticker, r.name, num(r.close), `${r.change_pct > 0 ? '+' : ''}${num(r.change_pct)}%`,
       num(r.high), num(r.previous_high), `${num(r.pullback_pct)}%`, num(r.volume)].forEach((v,i) => {
        const td = node('td', v); if (i < 3) td.className = 'align-left';
        if (i === 4) td.className = r.change_pct >= 0 ? 'pos' : 'neg'; tr.append(td);
      });
      const research = node('td', ''); research.className = 'us-research';
      research.colSpan = 9;
      usResearchLinks(r.ticker, report.date).forEach(([label, url]) => {
        const link = node('a', label); link.href = url; link.target = '_blank';
        link.rel = 'noopener noreferrer'; link.setAttribute('aria-label', `${r.ticker} ${label} (새 창)`);
        research.append(link);
      });
      const comments = node('div', ''); comments.className = 'us-x-comments';
      research.prepend(comments); commentSlots.set(r.ticker, comments);
      drawComments(comments, r.ticker, report.date);
      const researchRow = node('tr',''); researchRow.className='us-research-row'; researchRow.append(research);
      return [tr, researchRow];
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
    const diagnostic = report.diagnostics || {};
    diagnosticBody.replaceChildren();
    const failures = diagnostic.failure_summary || [];
    if (failures.length) failures.forEach(group => {
      diagnosticBody.append(node('p', `${group.label}: ${group.count}종목 · 예: ${(group.tickers || []).join(', ')}`));
    });
    else diagnosticBody.append(node('p', c.failed ? '상세 진단을 준비 중입니다. 다음 수집 후 표시됩니다.' : '현재 기록된 가격 수집 실패가 없습니다.'));
    if (diagnostic.retry_waiting) diagnosticBody.append(node('p', `재시도 대기: ${diagnostic.retry_waiting}종목 · 오류 종류와 시도 횟수에 따라 간격을 늘려 자동 재시도합니다.`));
    if (diagnostic.next_retry_at) diagnosticBody.append(node('p', `가장 이른 재시도 예정: ${new Date(diagnostic.next_retry_at).toLocaleString('ko-KR', {timeZone: 'Asia/Seoul'})} (한국 시간) · 수집 주기에 따라 늦어질 수 있습니다.`));
    if (diagnostic.sector_unclassified) diagnosticBody.append(node('p', `업종 미분류: ${diagnostic.sector_unclassified}종목 · 가격 판정은 완료했으며 신고가 목록에 포함됩니다.`));
    $('us-count').textContent = `${report.date} 미국 거래일 · 표시 ${rows.length} / 수집된 신고가 ${report.rows.length} 종목${$('us-top20').checked ? ' · 현재 필터 내 상승률 Top 20' : ''}`;
    $('us-date').value = report.date; $('us-date').max = report.today;
    $('us-prev').disabled = !report.available_dates.some(d => d < report.date);
    $('us-next').disabled = !report.available_dates.some(d => d > report.date);
    loadComments(report.date);
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
  ['us-sector','us-search','us-close-only','us-sort','us-positive-only','us-top20','us-min-volume'].forEach(id => $(id).addEventListener('input', render));
  $('us-date').onchange = () => { latest = false; load($('us-date').value); };
  $('us-prev').onclick = () => { latest = false; load(report.available_dates.filter(d => d < report.date).pop()); };
  $('us-next').onclick = () => { latest = false; load(report.available_dates.find(d => d > report.date)); };
  $('us-latest').onclick = () => { latest = true; load(); };
  $('us-refresh').onclick = () => load(latest ? null : report.date);
  $('us-reset').onclick = () => {
    $('us-sector').value = ''; $('us-search').value = ''; $('us-sort').value = 'change_pct';
    ['us-close-only','us-positive-only','us-top20'].forEach(id => $(id).checked = false);
    $('us-min-volume').value = ''; render();
  };
  setInterval(() => { if (!document.hidden) load(latest ? null : report.date); }, 60000);
  render();
})();
