// Workspace navigation and CLI-backed actions. Observation and operation streams have separate lifetimes.
const $ = id => document.getElementById(id);
const H = {'X-Agentspace': '1'};
const enc = encodeURIComponent;
const node = (tag, text = '', cls = '') => { const n = document.createElement(tag); n.textContent = text; n.className = cls; return n; };
const cut = s => String(s ?? '').slice(0, 8192);
const atBottom = p => p.scrollHeight - p.scrollTop - p.clientHeight < 12;
let toastTimer;
function toast(text) { $('toast').textContent = text; $('toast').hidden = false; clearTimeout(toastTimer); toastTimer = setTimeout(() => $('toast').hidden = true, 6000); }
async function request(url, options = {}) {
  const r = await fetch(url, options);
  if (!r.ok) {
    const error = new Error(await r.text());
    error.status = r.status;
    throw error;
  }
  return r;
}
const post = (url, body) => request(url, {method:'POST', headers:H, body});
async function stream(url, onLines, options = {}) {
  const r = await request(url, {...options, headers:H});
  const reader = r.body.getReader(), decoder = new TextDecoder();
  let rest = '';
  try {
    while (true) {
      const {value, done} = await reader.read();
      if (done) { rest += decoder.decode(); if (rest) onLines([rest]); break; }
      const lines = (rest + decoder.decode(value, {stream:true})).split('\n');
      rest = lines.pop();
      if (lines.length) onLines(lines);
    }
  } finally { reader.releaseLock(); }
}
function append(pane, items) {
  if (!items.length) return;
  const follow = atBottom(pane), frag = document.createDocumentFragment();
  items.forEach(n => frag.append(n)); pane.append(frag);
  while (pane.children.length > 5000) pane.firstElementChild.remove();
  if (follow) pane.scrollTop = pane.scrollHeight;
}
const labels = {'snap fork':'Launch environment','snap take':'Save snapshot','snap note':'Add a note','snap push':'Publish snapshot','snap pull':'Import snapshot','snap attach':'Attach text files','snap extract':'Extract files','snap show':'Snapshot details','snap tree':'Snapshot lineage','env start':'Start container','env kick':'Wake agents','env sleep':'Sleep environment','env stop':'Stop container','env kill':'Remove environment','env post':'Post to public board','env logs':'Read raw logs','env roll-sessions':'Roll agent sessions','budget topup':'Top up budget','budget show':'Budget usage','scen deactivate':'Deactivate scenario'};
const fieldLabels = {snap_ref:'Snapshot',new_env_name:'Environment name',env_name:'Environment',name:'Environment',text:'Note',message:'Message / description',amount_usd:'Amount to add (USD)',note:'Additional note',version:'Version override',attach:'Text file paths (one per line)',agent:'Agent ID',all_agents:'All agent sessions',everything:'Gateway and all agent sessions',follow:'Follow live',force:'Confirm removal',scen_name:'Scenario',host:'Host',budget_usd:'Shared budget (USD)',existing_key:'Existing API key',world_name:'World name',dest:'Destination directory',files:'File paths (quote paths containing spaces)',allow_key_leak:'Override credential scan',ghcr_tag:'Registry reference'};

// Search is local to the current catalog; all inventory stays available.
function filterCatalog() {
  if (!$('filter')) return;
  const q = $('filter').value.trim().toLowerCase();
  let visible = 0;
  document.querySelectorAll('[data-search]').forEach(n => { n.hidden = !n.dataset.search.toLowerCase().includes(q); if (!n.hidden) visible++; });
  document.querySelectorAll('[data-search-group]').forEach(group => {
    group.hidden = !group.querySelector('[data-search]:not([hidden])');
  });
  $('no-matches').hidden = visible > 0 || !q;
}
if ($('filter')) {
  $('filter').oninput = filterCatalog;
  $('filter').value = new URLSearchParams(location.search).get('q') || '';
  filterCatalog();
}
document.addEventListener('keydown', e => {
  if (e.key === '/' && !/INPUT|SELECT|TEXTAREA/.test(document.activeElement.tagName) && !$('action-dialog').open && $('filter')) { e.preventDefault(); $('filter').focus(); }
});
document.addEventListener('click', async e => {
  const copy = e.target.closest('[data-copy]');
  if (copy) {
    try { await navigator.clipboard.writeText(copy.dataset.copy); toast('Command copied. Run it in your terminal.'); }
    catch { toast('Could not access clipboard. Select and copy the command shown beside this button.'); }
  }
});

// A normal operation survives page navigation. The pane can be minimized without stopping it.
let operationController = null, operationGeneration = 0, operationTimer = null, activeOperation = null;
function saveOperation(value) { try { value ? sessionStorage.setItem('agentspace:7790:operation', JSON.stringify(value)) : sessionStorage.removeItem('agentspace:7790:operation'); } catch {} }
function revealOutput() { $('activity').hidden = false; $('activity-toggle').hidden = true; if($('output-placeholder')) $('output-placeholder').hidden = true; }
$('close-output').onclick = () => { $('activity').hidden = true; $('activity-toggle').hidden = false; };
$('activity-toggle').onclick = revealOutput;
function showResult(text, href, label) {
  const box = $('result-link'); box.replaceChildren(node('span', text));
  if (href) { const a = node('a', label); a.href = href; box.append(a); }
}
function followOperation(info, liveRequest, onEnd) {
  if (operationController) operationController.abort();
  const g = ++operationGeneration;
  operationController = new AbortController(); activeOperation = info;
  revealOutput(); $('outlabel').textContent = info.label; $('out').replaceChildren(); $('result-link').replaceChildren(); $('stop').disabled = false;
  $('elapsed').textContent = 'Starting…';
  let exit = null, rootId = null, failed = false, expired = false;
  const started = Date.now(); clearInterval(operationTimer);
  operationTimer = setInterval(() => $('elapsed').textContent = Math.round((Date.now()-started)/1000)+'s', 1000);
  if (!liveRequest) saveOperation(info);
  const options = {signal:operationController.signal};
  if (liveRequest) Object.assign(options,{method:'POST',body:liveRequest.body});
  stream(liveRequest ? liveRequest.url : '/runs/'+enc(info.id), lines => {
    if (g !== operationGeneration) return;
    const output=[];
    for (const l of lines) {
      const match = l.match(/^\[exit (-?\d+)\]$/); if (match) exit = +match[1];
      if (l.startsWith('UI_WORLD_ROOT:')) { rootId = l.slice(14).trim(); continue; }
      output.push(node('div',cut(l)));
    }
    append($('out'), output);
  }, options).catch(e => {
    if (g !== operationGeneration) return;
    failed = true;
    expired = !liveRequest && e.status === 404;
    if (expired) {
      saveOperation(null);
      activeOperation = null;
    } else append($('out'), [node('div', e.name === 'AbortError' ? 'Stopped following output.' : 'Connection error: '+e.message)]);
  }).finally(() => {
    if (g !== operationGeneration) return;
    clearInterval(operationTimer); $('stop').disabled = true; refreshRuns();
    if (expired) {
      $('elapsed').textContent = 'Unavailable';
      showResult('This operation’s output is no longer available. The server may have restarted.');
    } else if (!failed && exit === 0) {
      saveOperation(null);
      if (rootId) {
        showResult('Your world root is ready.', '/fork/'+enc(rootId), 'Launch an environment →');
        if ($('build-next')) {
          $('build-next').hidden = false; $('build-next').className = 'notice section';
          const a=node('a','Launch environment →','button'); a.href='/fork/'+enc(rootId);
          $('build-next').replaceChildren(node('span','World root built successfully.'),a);
        }
      } else if (info.destination) {
        showResult('Environment ready.', info.destination, 'Open environment →');
        location.assign(info.destination);
      } else showResult('Completed.', location.href, 'Refresh this view →');
    } else if (exit !== null && exit !== 0) {
      saveOperation(null); showResult('Operation failed. Review the output above.');
    } else if (failed && info.id) {
      showResult('Output disconnected. The operation may still be running.', '/tools', 'Check recent operations →');
    }
    if (onEnd) onEnd(exit);
  });
}
$('stop').onclick = async () => {
  if (!activeOperation?.id) { operationController?.abort(); return; }
  if (!confirm('Interrupt '+activeOperation.label+'? A build or launch may be left partially complete.')) return;
  try { await post('/runs/'+enc(activeOperation.id)+'/stop'); } catch(e) { toast(e.message); }
};
async function refreshRuns() {
  if (!$('runs')) return;
  try {
    const runs = await (await request('/runs')).json();
    $('runs').replaceChildren(...runs.map(r => {
      const b=node('button',r.label+' · '+(r.done ? (r.exit === 0 ? 'completed' : 'failed (exit '+r.exit+')') : 'running'));
      b.onclick=() => followOperation({id:r.id,label:r.label}); return b;
    }));
    if (!runs.length) $('runs').append(node('p','No operations yet.','muted'));
  } catch(e) { $('runs').textContent = e.message; }
}

async function submitOperation(form, after) {
  if (!form.reportValidity() || form.dataset.busy) return;
  const path = form.dataset.path;
  const body = new URLSearchParams(new FormData(form));
  if (path === 'env kill') {
    if (!confirm('Remove '+body.get('name')+'? This permanently deletes its container and disk and disables its key. Saved snapshots remain.')) return;
    body.set('force','on');
  }
  const submit = form.querySelector('button[type=submit], button:not([type])');
  form.dataset.busy='1'; if(submit) submit.disabled = true;
  const finished = exit => { delete form.dataset.busy; if(submit) submit.disabled=false; if(after) after(exit); };
  const url = '/run/'+path.replaceAll(' ','/');
  const info = {label:labels[path] || path};
  if (path === 'snap fork') info.destination = '/watch/'+enc(body.get('new_env_name'));
  if (path === 'env kill') info.destination = '/environments';
  try {
    if (body.get('follow')) {
      $('action-dialog').close(); followOperation(info,{url,body},finished); return;
    }
    const data=await (await post(url,body)).json();
    $('action-dialog').close(); followOperation({...info,id:data.id},null,finished);
  } catch(e) { finished(null); toast(e.message); }
}
document.querySelectorAll('form[data-path]').forEach(f => f.addEventListener('submit',e => { e.preventDefault(); submitOperation(f); }));
async function openAction(path, fields = {}, note = '') {
  try {
    const response=await request('/form/'+path.replaceAll(' ','/'));
    $('dialog-title').textContent=labels[path] || path;
    $('dialog-note').textContent=note;
    $('dialog-fields').innerHTML=await response.text(); // Only our escaped, server-generated form HTML.
    const f=$('dialog-fields').querySelector('form');
    for (const label of f.querySelectorAll('label')) {
      const control=label.querySelector('input,select,textarea');
      if (fieldLabels[control.name]) label.querySelector('span').firstChild.textContent=fieldLabels[control.name];
      if (control.name === 'force') label.hidden=true;
      if (control.name === 'message' && path === 'snap take') control.placeholder='What makes this moment worth saving?';
      if (control.type === 'checkbox') control.checked = fields[control.name] === 'on';
      else if (Object.hasOwn(fields,control.name)) control.value=fields[control.name];
      if (['name','env_name','snap_ref','scen_name'].includes(control.name) && fields[control.name]) control.readOnly=true;
    }
    f.querySelector('button').textContent=labels[path] || 'Run operation';
    f.onsubmit=e=>{e.preventDefault();submitOperation(f,exit=>{if(exit===0 && document.body.dataset.env) refreshEnvironment();});};
    $('action-dialog').showModal();
    const first=[...f.querySelectorAll('input,textarea,select')].find(x=>!x.readOnly&&x.type!=='hidden'&&x.type!=='checkbox');
    if(first) first.focus();
  } catch(e) { toast(e.message); }
}
document.addEventListener('click',e=> {
  const b=e.target.closest('[data-action]');
  if(b) openAction(b.dataset.action,JSON.parse(b.dataset.fields || '{}'),b.dataset.note || '');
  if(e.target.closest('[data-close]')) $('action-dialog').close();
});
$('action-dialog').addEventListener('click',e=>{if(e.target===$('action-dialog')) {const r=e.target.getBoundingClientRect(); if(e.clientX<r.left||e.clientX>r.right||e.clientY<r.top||e.clientY>r.bottom)e.target.close();}});
const query=new URLSearchParams(location.search);
if(query.get('open')) openAction(query.get('open'),Object.fromEntries([...query].filter(([k])=>k!=='open')));
if(query.get('group')) $('group-'+query.get('group'))?.scrollIntoView();
refreshRuns();

// Builder: keep registry validation and roster planning on the server.
if($('step3')) {
  const f=$('step3'), models=$('models');
  request('/models?runtime='+enc(f.dataset.runtime)).then(r=>r.json()).then(ids=>{
    const have=new Set([...models.options].map(o=>o.value));
    ids.forEach(id=>{if(!have.has(id))models.append(new Option(id));});
  }).catch(()=>toast('Model catalog unavailable. You can still enter a model ID.'));
  $('copyrow').onclick=e=>{e.preventDefault();const rows=[...$('roster').tBodies[0].rows];for(const r of rows)for(const selector of ['[name^=model_]','[name^=persona_]'])r.querySelector(selector).value=rows[0].querySelector(selector).value;toast('First agent’s model and persona applied to all agents.');};
  f.elements.world_name.oninput=()=>$('wname').textContent=f.elements.world_name.value||f.elements.world_name.placeholder;
  f.onsubmit=async e=>{
    e.preventDefault();if(f.dataset.busy)return;
    const button=f.querySelector('button');button.disabled=true;f.dataset.busy='1';
    try {
      const {id}=await (await post(f.dataset.build,new URLSearchParams(new FormData(f)))).json();
      followOperation({id,label:'Build world root · '+(f.elements.world_name.value||f.elements.world_name.placeholder)},null,()=>{button.disabled=false;delete f.dataset.busy;});
    } catch(e) {toast(e.message);button.disabled=false;delete f.dataset.busy;}
  };
}

// Observatory: one focused log pane, all scenario views and agent facets, live or replay.
let refreshEnvironment = () => {};
if($('pane')) {
  const env=document.body.dataset.env, pane=$('pane'), agentIds=new Set([...document.querySelectorAll('[data-agent]')].map(n=>n.dataset.agent));
  const agentChats={}, chatBusy=new Set(), facets={}, worldViews=[];
  const cards=[...document.querySelectorAll('[data-agent]')];
  let logController=null, logGeneration=0, current='', state=$('live-status').textContent.trim().split(' ')[0], refreshBusy=false, events=0;
  const safeColor=who=>['#556d29','#326979','#80502e','#604875','#3b6a53','#705d2e','#2260a6','#8b397d','#167187','#915230'][[...who].reduce((sum,c)=>(sum*31+c.charCodeAt(0))>>>0,0)%10];
  // On watch pages, command output belongs beside the logs, never over them.
  $('watch-output').append($('activity'),$('activity-toggle'));
  cards.forEach(c=>c.querySelector('b').style.color=safeColor(c.dataset.agent));
  function renderTabs() {
    const agent=current.split(':')[0];
    const tab=(name,label)=>{
      const b=node('button',label,'watch-view'+(current===name?' selected':''));
      b.type='button'; b.id='view-'+enc(name); b.dataset.view=name;
      b.setAttribute('role','tab');b.setAttribute('aria-controls','log-panel');
      b.setAttribute('aria-selected',current===name?'true':'false');b.tabIndex=current===name?0:-1;
      b.onclick=()=>{selectView(name);document.getElementById('view-'+enc(name))?.focus();};
      b.onkeydown=e=>{
        const tabs=[...$('views').querySelectorAll('[role=tab]')],index=tabs.indexOf(b);let next;
        if(e.key==='ArrowRight')next=(index+1)%tabs.length;
        if(e.key==='ArrowLeft')next=(index+tabs.length-1)%tabs.length;
        if(e.key==='Home')next=0;if(e.key==='End')next=tabs.length-1;
        if(next!==undefined){e.preventDefault();const name=tabs[next].dataset.view;selectView(name);document.getElementById('view-'+enc(name))?.focus();}
      };
      return b;
    };
    $('views').replaceChildren(...worldViews.map(n=>tab(n,n)));
    if(agentIds.has(agent)){
      const breakLine=node('span','','watch-view-break');breakLine.setAttribute('aria-hidden','true');
      const message=node('button','Message','text-button watch-message');
      message.onclick=()=>$('chat-text').focus();
      $('views').append(breakLine,node('span',agent,'watch-view-agent'),tab(agent,'session'),
        ...(facets[agent]||[]).map(n=>tab(n,n.split(':').pop())),message);
    }
    $('log-panel').setAttribute('aria-labelledby','view-'+enc(current));
    cards.forEach(c=>{const selected=c.dataset.agent===agent;c.classList.toggle('selected',selected);c.setAttribute('aria-pressed',selected?'true':'false');});
  }
  const denied=new Set((document.body.dataset.denied||'').split(',').filter(Boolean));   // the demo policy, computed by the server
  function renderActions() {
    const up=['active','dormant'].includes(state),known=['active','dormant','stopped','missing'].includes(state);
    const defs=[['env start','Start',state==='stopped'],['env kick','Wake',up],['env sleep','Sleep',state==='active'],
      ['env stop','Stop',up],['env post','Post',state==='active'],['env logs','Logs',up],['env roll-sessions','Roll sessions',up],
      ['snap take','Take snap',state!=='missing'],['env kill','Remove',true]];
    $('actions').replaceChildren(...defs.map(([verb,label,allowed])=>{
      const b=node('button',label,'button small '+(verb==='env kill'?'danger':'secondary'));
      const off=denied.has(verb);b.disabled=(known&&!allowed)||off;b.title=off?'needs the operator password':b.disabled?'Unavailable while '+state:(labels[verb]||label);
      const note=verb==='snap take'?'Capture and publish this environment’s state. A clean pause between turns is a good moment to save.':
        verb==='env kill'?'Permanently deletes this container and its disk, and disables its API key. Saved snapshots remain.':'';
      b.onclick=()=>openAction(verb,{[verb==='snap take'?'env_name':'name']:env},note);return b;
    }));
  }
  renderActions();
  refreshEnvironment=async()=>{
    if(refreshBusy)return;refreshBusy=true;
    await Promise.allSettled([
      request('/info/'+enc(env)).then(r=>r.json()).then(info=>{
        const oldState=state; state=info.Status.split(' ')[0];
        $('environment-details').replaceChildren(...Object.entries(info).filter(([key])=>key!=='Enter').flatMap(([key,value])=>[node('dt',key),node('dd',value)]));
        const pill=node('span',info.Status,'status '+(['active','dormant','stopped','missing'].includes(state)?state:'unknown'));
        pill.prepend(node('span','','status-dot'));$('live-status').replaceChildren(pill);
        $('status-check').textContent='Live status checked '+new Date().toLocaleTimeString([], {hour:'2-digit',minute:'2-digit'});
        $('runtime-fact').textContent=info.Runtime && info.Runtime!=='—'?'ran '+info.Runtime:'';
        $('started-fact').textContent=info.Started && info.Started!=='—'?'started '+info.Started:'';
        renderActions();
        if(oldState && oldState!==state && ['active','dormant'].includes(state))loadViews();
      }).catch(e=>{$('status-check').textContent='Live status unavailable: '+e.message;}),
      request('/budget/'+enc(env)).then(r=>r.json()).then(b=>{
        const used=b.used===null?null:Number(b.used),limit=b.limit===null?null:Number(b.limit),box=$('budget');
        box.querySelector('b').textContent=used===null?'Usage unavailable':'$'+used.toFixed(2);
        box.querySelector('small').textContent=limit===null?'No limit recorded':'of $'+limit.toFixed(2)+(used===null?'':' · $'+Math.max(0,limit-used).toFixed(2)+' left');
        box.querySelector('.bar>div').style.width=limit&&used!==null?Math.min(100,100*used/limit)+'%':'0';
      }).catch(e=>{$('budget').querySelector('small').textContent='Budget unavailable';$('budget').querySelector('b').textContent='—';})
    ]);refreshBusy=false;
  };
  function renderEvent(ev) {
    if(ev.error)return node('div',ev.error,'muted err');
    const n=node('div','','ev kind-'+String(ev.kind||'event').replace(/[^a-z0-9_-]/gi,''));
    if(ev.ts)n.append(node('span',ev.ts.slice(11,19),'ts'));
    if(ev.who){const who=node('span',ev.who,'who');who.style.color=safeColor(ev.who);n.append(who);}
    n.append(node('span',cut(ev.text),'text'));
    n.hidden=!n.textContent.toLowerCase().includes($('log-filter').value.toLowerCase());
    return n;
  }
  function selectView(name) {
    if(!name)return;
    if(logController)logController.abort(); logController=new AbortController();const generation=++logGeneration;
    current=name;events=0;pane.replaceChildren();renderTabs();document.title='Watch — '+env+' · '+name;
    $('event-count').textContent='0 events'; $('paused').hidden=true;
    const agent=name.split(':')[0], isAgent=agentIds.has(agent);
    $('chat').hidden=!isAgent;
    if(isAgent){$('chatwho').textContent='Private message → '+agent;$('chat').elements.text.disabled=chatBusy.has(agent);$('chatlog').replaceChildren(agentChats[agent]??=node('div'));}
    else $('chatlog').replaceChildren();
    const speed=$('speed').value; $('live').textContent=speed?'Replaying '+speed+'×':'Live · connecting';
    const loading=node('div','Waiting for events…','muted');pane.append(loading);
    stream('/stream/'+enc(env)+'/'+enc(name)+(speed?'?replay='+enc(speed):''),lines=>{
      if(generation!==logGeneration)return;
      const batch=lines.filter(Boolean).map(l=>JSON.parse(l));
      if(batch.length)loading.remove();
      events+=batch.filter(e=>!e.error).length;append(pane,batch.map(renderEvent));
      $('event-count').textContent=events+' events'+(events>5000?' · latest 5,000 retained':'');
      $('live').textContent=speed?'Replaying '+speed+'×':'Live · following';
      if(!events && loading.isConnected)loading.textContent='No events in this view yet.';
    },{signal:logController.signal}).then(()=>{if(generation===logGeneration)$('live').textContent=speed?'Replay complete':'Stream ended';}).catch(e=>{
      if(generation===logGeneration && e.name!=='AbortError'){loading.remove();pane.append(node('div','Log stream unavailable: '+e.message,'muted err'));$('live').textContent='Disconnected · reconnect to retry';}
    });
  }
  async function loadViews() {
    try {
      if(logController)logController.abort();++logGeneration;
      const data=await (await request('/views/'+enc(env))).json();
      if(data.error){
        $('views').replaceChildren(node('span','Logs unavailable','muted'));$('log-panel').removeAttribute('aria-labelledby');
        const box=node('div','','muted');box.append(node('p',data.error));
        const b=node('button',/not running/.test(data.error)?'Start container':'Open raw logs','button secondary');
        b.onclick=()=>openAction(/not running/.test(data.error)?'env start':'env logs',{name:env});box.append(b);pane.replaceChildren(box);$('live').textContent='Not streaming';return;
      }
      worldViews.length=0;
      for(const key of Object.keys(facets))delete facets[key];
      for(const [name,kids] of data.views){
        if(kids.length||agentIds.has(name))facets[name]=kids;
        else worldViews.push(name);
      }
      const available=new Set(data.views.flatMap(([name,kids])=>[name,...kids]));
      const desired=available.has(current)?current:data.views[0]?.[0];
      if(desired)selectView(desired);else {pane.replaceChildren(node('div','No views are declared for this environment.','muted'));$('views').replaceChildren(node('span','No log views','muted'));$('live').textContent='No views';}
    }catch(e){pane.replaceChildren(node('div','Could not load views: '+e.message,'muted err'));$('live').textContent='Disconnected';}
  }
  $('speed').onchange=()=>selectView(current);
  $('reconnect').onclick=()=>{loadViews();refreshEnvironment();};
  $('log-filter').oninput=()=>pane.querySelectorAll('.ev').forEach(n=>n.hidden=!n.textContent.toLowerCase().includes($('log-filter').value.toLowerCase()));
  $('jump-latest').onclick=()=>pane.scrollTop=pane.scrollHeight;
  pane.onscroll=()=>$('paused').hidden=atBottom(pane);
  $('download-logs').onclick=()=>{
    const text=[...pane.children].filter(n=>!n.hidden).map(n=>n.textContent).join('\n');
    const url=URL.createObjectURL(new Blob([text],{type:'text/plain'})),a=node('a');a.href=url;a.download=env+'-'+current.replace(/[^a-z0-9_-]/gi,'_')+'.txt';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
  };
  cards.forEach(b=>b.onclick=()=>{if(Object.hasOwn(facets,b.dataset.agent)){selectView(b.dataset.agent);if(matchMedia('(max-width: 760px)').matches)$('views').scrollIntoView({block:'start'});}else toast('This agent’s structured view is unavailable. Use Logs in the top toolbar.');});
  $('chat').onsubmit=async e=>{
    e.preventDefault();const agent=current.split(':')[0],input=$('chat').elements.text,text=input.value.trim();
    if(!text||!agentIds.has(agent)||chatBusy.has(agent))return;
    const log=agentChats[agent],waiting=node('div','Message sent. Waiting for the agent’s turn to finish…','muted');log.append(node('div','You → '+agent+': '+text),waiting);input.value='';input.disabled=true;chatBusy.add(agent);
    try{const r=await post('/chat/'+enc(env)+'/'+enc(agent),text);waiting.textContent=agent+': '+await r.text();waiting.className='';}
    catch(e){waiting.textContent=e.message;waiting.className='err';}
    finally{chatBusy.delete(agent);if(current.split(':')[0]===agent)input.disabled=false;}
  };
  const resizeMedia=matchMedia('(min-width: 1001px)');
  const layout=document.querySelector('.watch-layout');
  for(const divider of document.querySelectorAll('[data-resize]')){
    const column=$(divider.dataset.resize),isLeft=column.id==='agents',key='agentspace:7790:watch:'+column.id;
    let saved=0;try{saved=Number(localStorage.getItem(key));}catch{}
    const limits=()=>({min:isLeft?150:190,max:Math.min(isLeft?380:440,layout.clientWidth*.3)});
    const apply=(width,persist=true)=>{
      if(!resizeMedia.matches){column.style.width='';return;}
      const {min,max}=limits(),value=Math.max(min,Math.min(max,width));column.style.width=value+'px';
      divider.setAttribute('aria-valuemin',Math.round(min));divider.setAttribute('aria-valuemax',Math.round(max));divider.setAttribute('aria-valuenow',Math.round(value));
      if(persist){saved=value;try{localStorage.setItem(key,String(value));}catch{}}
    };
    const restore=()=>apply(saved|| (isLeft?232:250),false);restore();
    resizeMedia.addEventListener('change',restore);window.addEventListener('resize',restore);
    divider.onpointerdown=e=>{
      if(!resizeMedia.matches||e.button!==0)return;
      const x=e.clientX,width=column.getBoundingClientRect().width;
      divider.setPointerCapture(e.pointerId);divider.classList.add('dragging');
      divider.onpointermove=ev=>apply(width+(ev.clientX-x)*(isLeft?1:-1));
      const finish=()=>{divider.onpointermove=null;divider.classList.remove('dragging');};
      divider.onpointerup=finish;divider.onpointercancel=finish;divider.onlostpointercapture=finish;
    };
    divider.onkeydown=e=>{
      if(!['ArrowLeft','ArrowRight','Home','End'].includes(e.key)||!resizeMedia.matches)return;
      e.preventDefault();const {min,max}=limits();
      const width=e.key==='Home'?min:e.key==='End'?max:column.getBoundingClientRect().width+(e.key==='ArrowRight'?20:-20)*(isLeft?1:-1);
      apply(width);
    };
  }
  loadViews();refreshEnvironment();
  // Refresh on return to the page, coalescing focus and visibility events.
  let focusRefreshTimer;
  const refreshOnFocus=()=>{
    clearTimeout(focusRefreshTimer);
    if(!document.hidden)focusRefreshTimer=setTimeout(()=>refreshEnvironment(),100);
  };
  window.addEventListener('focus',refreshOnFocus);
  document.addEventListener('visibilitychange',refreshOnFocus);
}
// Resume only this tab's operation. No shared localStorage with other previews.
try {const saved=JSON.parse(sessionStorage.getItem('agentspace:7790:operation')||'null');if(saved && saved.id)followOperation(saved);}catch{}
