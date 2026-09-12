const assert=require('node:assert/strict'),m=require('../static/new-highs-model.js');
const base={scope:'all',period:'20d',sector:'',newOnly:false,memoOnly:false,minCap:0,minValue:0,search:'',sort:'market_cap',direction:-1};
const common={is_20d_high:true,is_60d_high:false,is_52w_high:false,is_all_time_high:false,new_20d_high:false,new_60d_high:false,new_52w_high:false,new_all_time_high:false,consecutive_by_period:{'20d':3,'60d':1},reason:'',memo:'',turnover:.02};
const rows=m.normalize({rows:[
 {...common,ticker:'B',name:'삼성전자',sector:'전자',market_cap_eok:100,trading_value_eok:8,change_pct:3,is_60d_high:true,new_60d_high:true,memo:'메모 B'},
 {...common,ticker:'A',name:'테스트',sector:'기계',market_cap_eok:200,trading_value_eok:2,change_pct:-1},
 {...common,ticker:'C',name:'다른 종목',sector:'전자',market_cap_eok:50,trading_value_eok:20,change_pct:12,new_20d_high:true,memo:'메모 C'}]}).rows;
assert.deepEqual(m.sortedRows(rows,base).map(r=>r.ticker),['A','B','C']);
assert.deepEqual(m.sortedRows(rows,{...base,sort:'change_pct',direction:1}).map(r=>r.ticker),['A','B','C']);
assert.equal(m.representative(rows[0]),'60d');
assert.equal(m.isNew(rows[0],{...base,scope:'period',period:'20d'}),false);
assert.equal(m.isNew(rows[0],{...base,scope:'period',period:'60d'}),true);
assert.equal(m.streak(rows[0],{...base,scope:'period',period:'20d'}),3);
assert.equal(m.streak(rows[0],base),1);
assert.deepEqual(m.filterRows(rows,{...base,scope:'period',period:'60d',sector:'전자',newOnly:true,memoOnly:true,minCap:80,minValue:5,search:'삼성'}).map(r=>r.ticker),['B']);
assert.equal(m.filterRows(rows,{...base,search:' c '})[0].ticker,'C');
assert.equal(m.filterRows(rows,{...base,minCap:999}).length,0);
assert.equal(m.filterRows(rows,{...base,sector:'기계'},true).length,3);
assert.deepEqual(m.counts(rows).map(p=>[p.total,p.fresh]),[[3,1],[1,1],[0,0],[0,0]]);
assert.equal(m.exportMemos([rows[0]],'2026-09-04'),'2026-09-04 신고가 리포트\n\n삼성전자 (B) · 전자\n메모 B');
assert.equal(rows[0].turnover_pct,2);
console.log('14 assertions passed');
const manual=m.normalize({rows:[{...common,ticker:'005930',name:'삼성전자',sector:'AI 반도체',market_cap_eok:100,trading_value_eok:8,reason:'기존 이유',memo:'기존 메모',manual_reasons:['수주 확대','실적 개선']}]}).rows;
assert.equal(manual[0].memo,'기존 이유\n기존 메모\n수주 확대\n실적 개선');
assert.equal(m.filterRows(manual,{...base,sector:'AI 반도체',memoOnly:true}).length,1);
assert.equal(m.exportMemos(manual,'2026-09-04'),'2026-09-04 신고가 리포트\n\n삼성전자 (005930) · AI 반도체\n기존 이유\n기존 메모\n수주 확대\n실적 개선');
console.log('3 manual note assertions passed');
