/* Pure selectors; the API retains independent booleans for every high period. */
(function(root) {
  const periods=[['20d','20일'],['60d','60일'],['52w','52주'],['all','역사적']];
  const fields={'20d':'20d_high','60d':'60d_high','52w':'52w_high','all':'all_time_high'};
  function normalize(report) {
    return {...report,rows:report.rows.map(r=>{
      const satisfied=periods.map(p=>p[0]).filter(p=>r['is_'+fields[p]]);
      return {...r,periods:satisfied,new_periods:satisfied.filter(p=>r['new_'+fields[p]]),
        market_cap:r.market_cap_eok,trading_value:r.trading_value_eok,turnover_pct:r.turnover*100,
        memo:[r.reason,r.memo].filter(Boolean).join('\n'),
        highlight:r.is_all_time_high?'strong':r.is_60d_high?'watch':null};
    })};
  }
  const representative=r=>r.periods[r.periods.length-1];
  const isNew=(r,state)=>state.scope==='all'?r.new_periods.length>0:r.new_periods.includes(state.period);
  const streak=(r,state)=>r.consecutive_by_period[state.scope==='all'?representative(r):state.period]||0;
  function filterRows(rows,state,omitSector=false) {
    const query=state.search.trim().toLocaleLowerCase();
    return rows.filter(r=>(state.scope==='all'||r.periods.includes(state.period))&&
      (omitSector||!state.sector||r.sector===state.sector)&&(!state.newOnly||isNew(r,state))&&
      (!state.memoOnly||r.memo.trim())&&r.market_cap>=state.minCap&&r.trading_value>=state.minValue&&
      (!query||`${r.name} ${r.ticker}`.toLocaleLowerCase().includes(query)));
  }
  function sortedRows(rows,state) {
    return filterRows(rows,state).map(r=>({...r,is_new:isNew(r,state),streak_days:streak(r,state)})).sort((a,b)=>
      (a[state.sort]-b[state.sort])*state.direction||a.ticker.localeCompare(b.ticker));
  }
  function counts(rows) {
    return periods.map(([key,label])=>({key,label,total:rows.filter(r=>r.periods.includes(key)).length,
      fresh:rows.filter(r=>r.new_periods.includes(key)).length}));
  }
  function exportMemos(rows,date) {
    return `${date} 신고가 리포트\n\n`+rows.filter(r=>r.memo.trim()).map(r=>`${r.name} (${r.ticker}) · ${r.sector}\n${r.memo}`).join('\n\n');
  }
  const api={periods,normalize,representative,isNew,streak,filterRows,sortedRows,counts,exportMemos};
  if(typeof module!=='undefined'&&module.exports)module.exports=api;else root.HighReportModel=api;
})(globalThis);
