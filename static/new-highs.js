(() => {
  'use strict';
  const $=id=>document.getElementById(id),model=window.HighReportModel;
  let report=model.normalize(JSON.parse($('initial-report').textContent)),requestId=0,pending=false,failed=false,followLatest=true;
  let visible=[];
  const defaults={scope:'all',period:'20d',sector:'',newOnly:false,memoOnly:false,minCap:0,minValue:0,search:'',sort:'market_cap',direction:-1};
  let state={...defaults};
  const number=n=>new Intl.NumberFormat('ko-KR',{maximumFractionDigits:3}).format(n);
  const el=(tag,text,cls)=>{const node=document.createElement(tag);if(text!==undefined)node.textContent=text;if(cls)node.className=cls;return node;};
  const label=key=>model.periods.find(p=>p[0]===key)[1];
  function nav(){
    const date=$('report-date').value,dates=report.available_dates;
    $('report-date').max=report.today;
    $('prev-date').disabled=pending||!dates.some(d=>d<date);
    $('next-date').disabled=pending||!dates.some(d=>d>date);
  }
  function collectionStatus(){
    const c=report.collector||{},coverage=report.coverage||{};
    let text=report.updated_at?`수집 ${report.updated_at.slice(0,16).replace('T',' ')} KST`:'첫 실제 데이터 수집 대기';
    if(coverage.total)text+=` · 분석 ${coverage.evaluated}/${coverage.total} · 거래정지 등 제외 ${coverage.excluded} · 실패 ${coverage.failed} · 전체 이력 확인 ${coverage.all_time_verified}`;
    if(report.status==='partial')text+=' · 일부 수집 실패: 집계가 불완전합니다';
    if(c.state==='collecting')text+=` · 수집 중 ${c.processed||0}/${c.total||'…'}`;
    if(c.state==='error')text+=' · 수집 오류: '+c.error;
    if(c.state==='waiting_for_source')text+=' · 거래일 데이터 갱신 대기';
    if(report.latest_date&&report.latest_date<report.today)text+=` · 최신 제공 거래일 ${report.latest_date}`;
    $('data-status').textContent=text;
    $('data-status').classList.toggle('nh-warning',report.status==='partial'||c.state==='error');
  }
  function render(){
    if(pending||failed)return;
    $('source-label').textContent='실제 데이터 · 장 마감 후 자동 갱신';
    collectionStatus();
    $('period-tabs').replaceChildren(...model.counts(report.rows).map(p=>{
      const b=el('button',`${p.label} ${p.total}`);b.setAttribute('aria-pressed',String(state.scope==='period'&&state.period===p.key));
      b.append(el('small',`신규 ${p.fresh}`));b.onclick=()=>{if(pending)return;state.period=p.key;state.scope='period';$('scope').value=p.key;render();};return b;
    }));
    const sectors=[...new Set(report.rows.map(r=>r.sector))].sort();
    if(!sectors.includes(state.sector))state.sector='';
    $('sector').replaceChildren(new Option('업종 전체',''),...sectors.map(s=>new Option(s,s)));$('sector').value=state.sector;
    const sectorRows=model.filterRows(report.rows,state,true),chipCounts=new Map();
    sectorRows.forEach(r=>chipCounts.set(r.sector,(chipCounts.get(r.sector)||0)+1));
    $('sector-chips').replaceChildren(...[['',sectorRows.length],...Array.from(chipCounts).sort((a,b)=>b[1]-a[1])].map(([sector,count])=>{
      const b=el('button',`${sector||'전체'} ${count}`,'chip');b.setAttribute('aria-pressed',String(state.sector===sector));
      b.onclick=()=>{if(pending)return;state.sector=state.sector===sector?'':sector;render();};return b;
    }));
    visible=model.sortedRows(report.rows,state);
    $('result-status').textContent=`표시 ${visible.length} / 신고가 ${report.rows.length} 종목 · ${report.date}`;
    const rows=visible.map(r=>{
      const tr=el('tr');if(['watch','strong'].includes(r.highlight))tr.dataset.highlight=r.highlight;
      tr.append(el('td',r.sector,'align-left'));
      const name=el('td',undefined,'align-left'),button=el('button',r.name,'nh-stock');button.setAttribute('aria-label',`${r.name} 상세 정보`);name.append(button);tr.append(name);
      tr.append(el('td',`${r.change_pct>0?'+':''}${r.change_pct.toFixed(1)}%`,`num ${r.change_pct>0?'pos':r.change_pct<0?'neg':''}`));
      for(const key of ['market_cap','trading_value','turnover_pct'])tr.append(el('td',number(r[key]),'num'));
      const period=el('td');period.append(el('span',label(model.representative(r)),'nh-badge'));period.title=r.periods.map(label).join(' / ');tr.append(period);
      tr.append(el('td',r.streak_days>1?`연속 ${r.streak_days}일`:'1일'),el('td',r.is_new?'N':'—',r.is_new?'nh-new':''));
      const memo=el('td',r.memo||'—','align-left nh-memo');memo.title=r.memo;tr.append(memo);
      tr.onclick=()=>detail(r);return tr;
    });
    if(!rows.length){const tr=el('tr');let text='조건에 맞는 종목이 없습니다. 필터를 조정해 주세요.';
      if(!report.rows.length)text=report.status==='pending'?'실제 데이터 수집 대기 중입니다. 완료되면 자동 표시됩니다.':report.status==='unavailable'?'이 날짜의 저장된 리포트가 없습니다.':'이 날짜의 분석 대상 중 신고가 종목이 없습니다.';
      const td=el('td',text,'empty');td.colSpan=10;tr.append(td);rows.push(tr);}
    $('report-rows').replaceChildren(...rows);
    document.querySelectorAll('[data-sort]').forEach(b=>{const active=b.dataset.sort===state.sort;b.parentElement.setAttribute('aria-sort',active?(state.direction===-1?'descending':'ascending'):'none');b.querySelector('span').textContent=active?(state.direction===-1?'↓':'↑'):'↕';});
    $('export-memo').disabled=!visible.some(r=>r.memo.trim());nav();
  }
  function detail(r){
    $('detail-title').textContent=r.name;$('detail-subtitle').textContent=`${r.ticker} · ${r.sector} · ${report.date}`;
    const values=[['종가',`${number(r.close)}원`],['등락률',`${r.change_pct>0?'+':''}${r.change_pct}%`],['시가총액',`${number(r.market_cap)}억 원`],['거래대금',`${number(r.trading_value)}억 원`],['회전율',`${number(r.turnover_pct)}%`],['신고가 기준',r.periods.map(label).join(' / ')],['연속 신고',`${r.streak_days}일`],['신규 기준',r.new_periods.map(label).join(' / ')||'없음'],['가격 이력 시작',r.history_start],['역사적 판정',r.all_time_verified?'상장일부터 이력 확인':'전체 이력 부족 · 판정 제외']];
    $('detail-values').replaceChildren(...values.flatMap(([k,v])=>[el('dt',k),el('dd',v)]));$('detail-memo').textContent=r.memo||'기재된 이유 / 메모가 없습니다.';$('stock-detail').showModal();
  }
  async function loadDate(date,background=false){
    if(date&&date>report.today){$('result-status').textContent='미래 날짜는 선택할 수 없습니다.';$('report-date').value=report.date;return;}
    const id=++requestId;pending=true;failed=false;$('retry').hidden=true;
    if(!background){if(date)$('report-date').value=date;$('result-status').textContent='데이터를 불러오는 중…';$('report-rows').replaceChildren();}
    $('export-memo').disabled=true;nav();$('highs-table').setAttribute('aria-busy','true');
    const controller=new AbortController(),timeout=setTimeout(()=>controller.abort(),15000);
    try{
      const response=await fetch(date?`/api/new-highs?date=${encodeURIComponent(date)}`:'/api/new-highs',{signal:controller.signal});
      if(!response.ok)throw new Error('load');
      const data=await response.json();if(id!==requestId)return;report=model.normalize(data);pending=false;$('report-date').value=report.date;render();
    }catch(error){if(id!==requestId)return;pending=false;failed=true;$('result-status').textContent='리포트를 불러오지 못했습니다. 다시 시도해 주세요.';$('retry').hidden=false;nav();}
    finally{clearTimeout(timeout);if(id===requestId)$('highs-table').removeAttribute('aria-busy');}
  }
  $('report-date').onchange=e=>{if(!e.target.value){e.target.value=report.date;return;}followLatest=false;loadDate(e.target.value);};
  $('prev-date').onclick=()=>{followLatest=false;loadDate(report.available_dates.filter(d=>d<$('report-date').value).sort().pop());};
  $('next-date').onclick=()=>{followLatest=false;loadDate(report.available_dates.filter(d=>d>$('report-date').value).sort()[0]);};
  $('latest-date').onclick=()=>{followLatest=true;loadDate(null);};
  $('retry').onclick=()=>loadDate(followLatest?null:$('report-date').value);
  const bindings={'sector':'sector','new-only':'newOnly','memo-only':'memoOnly','min-cap':'minCap','min-value':'minValue','search':'search'};
  $('scope').onchange=e=>{state.scope=e.target.value==='all-periods'?'all':'period';if(state.scope==='period')state.period=e.target.value;render();};
  for(const[id,key]of Object.entries(bindings))$(id).addEventListener('input',e=>{state[key]=e.target.type==='checkbox'?e.target.checked:e.target.type==='number'?Math.max(0,Number(e.target.value)||0):e.target.value;render();});
  document.querySelectorAll('[data-sort]').forEach(b=>b.onclick=()=>{state.direction=state.sort===b.dataset.sort?-state.direction:-1;state.sort=b.dataset.sort;render();});
  $('reset-filters').onclick=()=>{state={...defaults};$('scope').value='all-periods';for(const[id]of Object.entries(bindings)){if($(id).type==='checkbox')$(id).checked=false;else $(id).value='';}render();};
  $('export-memo').onclick=()=>{const blob=new Blob(['\ufeff',model.exportMemos(visible,report.date)],{type:'text/plain;charset=utf-8'}),url=URL.createObjectURL(blob),a=el('a');a.href=url;a.download=`신고가_메모_${report.date}.txt`;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);};
  $('close-detail').onclick=()=>$('stock-detail').close();
  setInterval(()=>{if(!pending&&document.visibilityState==='visible'&&!$('stock-detail').open)loadDate(followLatest?null:report.date,true);},60000);
  render();
})();
