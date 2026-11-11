(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  const fields = ['date','title','summary','tickers','tags','body'];
  let owner = false, csrf = '', kind = 'daily', current = null, posts = [], dirty = false, preview = true, saving = false;
  const escape = s => String(s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const inline = s => escape(s).replace(/\[([^\]\n]+)\]\((https?:\/\/[^\s)]+)\)/g, '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>')
    .replace(/\*\*([^*\n]+)\*\*/g, '<strong>$1</strong>').replace(/\*([^*\n]+)\*/g, '<em>$1</em>');
  function markdown(text) {
    let code = false, list = false, result = '';
    for (const line of text.split('\n')) {
      if (line.startsWith('```')) { if(list){result+='</ul>';list=false;} result += code ? '</code></pre>' : '<pre><code>'; code = !code; continue; }
      if (code) { result += escape(line)+'\n'; continue; }
      if (/^\s*-\s+/.test(line)) { if(!list){result+='<ul>';list=true;} result+='<li>'+inline(line.replace(/^\s*-\s+/,''))+'</li>';continue; }
      if(list){result+='</ul>';list=false;}
      const heading = line.match(/^(#{1,3})\s+(.*)$/);
      if (heading) result += `<h${heading[1].length}>${inline(heading[2])}</h${heading[1].length}>`;
      else if (line.startsWith('> ')) result += '<blockquote>'+inline(line.slice(2))+'</blockquote>';
      else if (/^---+$/.test(line)) result += '<hr>';
      else result += line.trim() ? '<p>'+inline(line)+'</p>' : '<br>';
    }
    return result+(list?'</ul>':'')+(code?'</code></pre>':'');
  }
  const values = () => Object.fromEntries(fields.map(f => [f,$('post-'+f).value]));
  const status = text => { $('journal-status').textContent=text; };
  async function api(path='', options={}) {
    const response = await fetch('/api/daily-comments'+path,{...options,headers:{'Content-Type':'application/json','X-Journal-CSRF':csrf,...options.headers}});
    const data = await response.json();
    if(!response.ok) throw Error(data.error || '요청을 처리하지 못했습니다.');
    return data;
  }
  function renderPreview(){
    const v=values();
    $('post-preview').innerHTML='<h1>'+escape(v.title || '제목')+'</h1><time>'+escape(v.date)+'</time>'+(v.summary?'<p><strong>'+escape(v.summary)+'</strong></p>':'')+markdown(v.body)+(v.tickers||v.tags?'<hr><small>'+escape([v.tickers,v.tags].filter(Boolean).join(' · '))+'</small>':'');
    $('post-preview').hidden=owner&&!preview;
    $('post-body').hidden=!owner;
    document.querySelector('.journal-writing').classList.toggle('no-preview', !owner||!preview);
  }
  function permission(){
    $('login-open').hidden=owner;$('logout').hidden=!owner;$('new-post').hidden=!owner;
    $('save-post').hidden=!owner;$('post-fields').disabled=!owner;$('post-body').readOnly=!owner;
    $('read-only').hidden=owner;$('preview-toggle').hidden=!owner;$('markdown-hint').hidden=!owner;
    renderPreview();
  }
  function canLeave(){return !dirty || confirm('저장하지 않은 변경 내용이 있습니다. 이동할까요?');}
  function show(post){
    current=post;dirty=false;
    $('journal-empty').hidden=true;$('post-panel').hidden=false;
    for(const f of fields)$('post-'+f).value=post[f]||'';
    $('post-updated').textContent=post.updated_at?'마지막 저장 '+new Date(post.updated_at).toLocaleString('ko-KR'): '아직 게시되지 않은 새 글입니다. 저장하면 방문자에게 공개됩니다.';
    permission();renderList();
  }
  function renderList(){
    const query=$('journal-search').value.trim().toLowerCase();
    const filtered=posts.filter(p=>[p.title,p.summary,p.tickers,p.tags,p.date].join(' ').toLowerCase().includes(query));
    const target=$('post-list');target.replaceChildren();
    if(!filtered.length){const p=document.createElement('p');p.className='journal-hint';p.textContent=query?'검색 결과가 없습니다.':'아직 작성된 글이 없습니다.';target.append(p);return;}
    for(const p of filtered){
      const button=document.createElement('button');button.type='button';button.className='journal-post';button.setAttribute('aria-current',String(current?.id===p.id));
      button.innerHTML='<strong>'+escape(p.date)+'</strong><span>'+escape(p.title)+'</span><small>'+escape(p.summary || p.tickers || p.tags)+'</small>';
      button.onclick=async()=>{if(!canLeave())return;try{show((await api('/'+p.id)).post);status('');}catch(e){status(e.message);}};target.append(button);
    }
  }
  async function loadList(){ posts=(await api('?kind='+kind)).posts;renderList(); }
  async function switchKind(next){
    if(!canLeave())return;
    kind=next;current=null;dirty=false;
    $('daily-tab').setAttribute('aria-pressed',String(kind==='daily'));$('weekly-tab').setAttribute('aria-pressed',String(kind==='weekly'));
    try{await loadList();if(posts.length)show((await api('/'+posts[0].id)).post);else{$('post-panel').hidden=true;$('journal-empty').hidden=false;}status('');}catch(e){status(e.message);}
  }
  $('daily-tab').onclick=()=>switchKind('daily');$('weekly-tab').onclick=()=>switchKind('weekly');
  $('journal-search').oninput=renderList;
  $('login-open').onclick=()=>{$('login-error').textContent='';$('login-dialog').showModal();$('password').focus();};
  $('login-cancel').onclick=()=>{$('login-dialog').close();$('password').value='';};
  $('login-form').onsubmit=async e=>{e.preventDefault();const button=e.submitter;button.disabled=true;try{const data=await api('/login',{method:'POST',body:JSON.stringify({password:$('password').value})});owner=true;csrf=data.csrf;$('password').value='';$('login-dialog').close();permission();status('관리자로 로그인했습니다. 새 글을 작성하거나 기존 글을 선택해 수정하세요.');}catch(error){$('login-error').textContent=error.message;}finally{button.disabled=false;}};
  $('logout').onclick=async()=>{if(!canLeave())return;try{await api('/logout',{method:'POST',body:'{}'});owner=false;csrf='';dirty=false;if(current?.id)show((await api('/'+current.id)).post);else{$('post-panel').hidden=true;$('journal-empty').hidden=false;}permission();status('로그아웃했습니다.');}catch(e){status(e.message);}};
  $('new-post').onclick=()=>{if(!canLeave())return;show({date:new Intl.DateTimeFormat('sv-SE',{timeZone:'Asia/Seoul'}).format(new Date()),kind});$('post-title').focus();status('새 글은 저장 버튼을 눌러야 공개됩니다.');};
  $('post-form').oninput=()=>{if(owner){dirty=true;renderPreview();status('저장하지 않은 변경 내용이 있습니다.');}};
  $('post-form').onsubmit=async e=>{
    e.preventDefault();if(!owner||saving)return;saving=true;$('save-post').disabled=true;
    try{const data=await api(current?.id?'/'+current.id:'',{method:current?.id?'PUT':'POST',body:JSON.stringify({...values(),kind,revision:current?.revision})});show(data.post);await loadList();status('저장했습니다. 방문자에게 공개됩니다.');}catch(error){status(error.message+' 작성 내용은 화면에 유지되어 있습니다.');}finally{saving=false;$('save-post').disabled=false;}
  };
  $('preview-toggle').onclick=()=>{preview=!preview;$('preview-toggle').textContent=preview?'미리보기 끄기':'미리보기 켜기';$('preview-toggle').setAttribute('aria-pressed',String(preview));renderPreview();};
  function exportMarkdown(){const v=values();return '# '+v.title+'\n\n'+v.date+'\n\n'+(v.summary?v.summary+'\n\n':'')+v.body+'\n\n'+(v.tickers?'관련 종목: '+v.tickers+'\n':'')+(v.tags?'태그: '+v.tags+'\n':'');}
  async function copy(text){try{await navigator.clipboard.writeText(text);status('복사했습니다.');}catch(e){status('복사 권한을 확인하거나 .md 저장을 이용해 주세요.');}}
  $('copy-md').onclick=()=>copy(exportMarkdown());$('copy-html').onclick=()=>copy($('post-preview').innerHTML);
  $('download-md').onclick=()=>{const blob=new Blob([exportMarkdown()],{type:'text/markdown;charset=utf-8'});const url=URL.createObjectURL(blob);const a=document.createElement('a');a.href=url;a.download=values().date+'-'+values().title.replace(/[\\/:*?"<>|]/g,'').slice(0,70)+'.md';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);};
  window.addEventListener('beforeunload',e=>{if(dirty){e.preventDefault();e.returnValue='';}});
  (async()=>{try{const session=await api('/session');owner=session.authenticated;csrf=session.csrf;permission();await switchKind('daily');if(!session.configured)status('읽기 전용 · 관리자 설정이 아직 필요합니다.');}catch(error){status(error.message);}})();
})();
