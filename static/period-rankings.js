(()=>{'use strict';
 const $=id=>document.getElementById(id), number=n=>Number.isFinite(n)?n.toLocaleString('ko-KR',{maximumFractionDigits:2}):'—';
 let market='kr',period='weekly',report={rows:[],coverage:{},basis:{}},serial=0;
 function render(){
  for(const m of ['kr','us'])$('rank-'+m).setAttribute('aria-pressed',String(market===m));
  for(const p of ['weekly','monthly'])$('rank-'+p).setAttribute('aria-pressed',String(period===p));
  const c=report.coverage||{},basis=report.basis||{},label=period==='weekly'?'주간':'월간';
  $('rank-date').textContent=report.date||'수집 대기';$('rank-base').textContent=basis[period]||'—';
  $('rank-coverage').textContent=c.total?`${number(c.evaluated)} / ${number(c.total)}`:'첫 수집 대기';
  $('rank-return-head').textContent=label+' 상승률';
  $('rank-status').textContent=(report.updated_at?`갱신 ${new Date(report.updated_at).toLocaleString('ko-KR',{timeZone:'Asia/Seoul'})} (한국 시간) · `:'')+(c.total?`처리 ${c.evaluated} · 대기 ${c.pending} · 조회 실패 ${c.failed} · 거래·이력 부족 제외 ${c.excluded}`:'전체 종목의 확정 종가를 수집하고 있습니다.');
  $('rank-notice').textContent=report.status==='error'?report.error:report.collection_error?'최근 갱신에 실패해 직전 저장 자료를 표시합니다.':report.status!=='complete'?(c.total&&!c.pending?`가격 조회에 실패한 ${c.failed}개 종목을 제외한 잠정 순위입니다. 누락 종목은 자동 재시도합니다.`:'수집 중인 잠정 순위입니다. 미처리 종목에 따라 순위가 달라질 수 있습니다.'):'';
  const leaders=(report.rows||[]).filter(r=>Number.isFinite(r[period])).sort((a,b)=>b[period]-a[period]||a.ticker.localeCompare(b.ticker)).slice(0,100).map((r,i)=>({...r,rank:i+1}));
  const selected=$('rank-sector').value;
  const sectors=[...new Set(leaders.map(r=>r.sector||'미분류'))].sort();$('rank-sector').replaceChildren(new Option('전체',''),...sectors.map(s=>new Option(s,s)));
  if(sectors.includes(selected))$('rank-sector').value=selected;
  const query=$('rank-search').value.trim().toLowerCase();
  const rows=leaders.filter(r=>(!$('rank-sector').value||(r.sector||'미분류')===$('rank-sector').value)&&`${r.name} ${r.ticker}`.toLowerCase().includes(query));
  const tbody=$('rank-rows');tbody.replaceChildren();
  for(const row of rows){const tr=document.createElement('tr');
   [row.rank,row.sector||'미분류',row.name,`${row.ticker} · ${row.market}`,`${row[period]>0?'+':''}${number(row[period])}%`,`${market==='kr'?'₩':'$'}${number(row.close)}`,number(row.volume)].forEach((v,i)=>{const td=document.createElement('td');td.textContent=v;if(i===4)td.className=row[period]>=0?'pos':'neg';tr.append(td);});tbody.append(tr);}
  if(!rows.length){const tr=document.createElement('tr'),td=document.createElement('td');td.colSpan=7;td.className='rank-empty';td.textContent=leaders.length?'조건에 맞는 종목이 없습니다.':'수집된 순위가 아직 없습니다. 자료가 준비되면 자동으로 표시됩니다.';tr.append(td);tbody.append(tr);}
  $('rank-count').textContent=`${market==='kr'?'코스피·코스닥 통합':'나스닥 상장 종목'} · ${label} 상위 ${leaders.length}개 중 ${rows.length}개 표시 · 통화 ${market==='kr'?'KRW':'USD'}`;
 }
 async function load(){const id=++serial;$('rank-refresh').disabled=true;try{const response=await fetch('/api/period-rankings?market='+market,{cache:'no-store',signal:AbortSignal.timeout(20000)});if(!response.ok)throw Error();const data=await response.json();if(id!==serial)return;report=data;render();}catch(error){if(id===serial)$('rank-status').textContent='조회에 실패했습니다. 이전 자료를 유지합니다. 새로고침으로 다시 시도해 주세요.';}finally{if(id===serial)$('rank-refresh').disabled=false;}}
 for(const m of ['kr','us'])$('rank-'+m).onclick=()=>{if(m===market)return;market=m;report={rows:[],coverage:{},basis:{},status:'pending'};$('rank-sector').value='';$('rank-search').value='';render();load();};
 for(const p of ['weekly','monthly'])$('rank-'+p).onclick=()=>{period=p;$('rank-sector').value='';render();};
 $('rank-search').oninput=render;$('rank-sector').onchange=render;$('rank-refresh').onclick=load;
 setInterval(()=>{if(!document.hidden)load();},60000);render();load();
})();
