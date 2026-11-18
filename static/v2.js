const $ = id => document.getElementById(id);
const state = {kind:'feedback',tab:'waiting',reviewOnly:true,modelId:null,productId:null,selected:null,
  offset:0,q:'',data:null,detail:null,dirty:false,loading:false,profiles:false,templates:{}};
const labels = {new:'Получен',ready_auto:'Готов к автоответу',needs_review:'Нужен ответ',
  deferred:'Отложен',sending:'Отправляется',api_confirmed:'Подтверждён чтением',
  outcome_unknown:'Исход неизвестен',failed:'Ошибка',remote_answered:'Ответ в WB',
  no_reply_needed:'Без текста · ответ не нужен',remote_changed:'Проверить в WB'};
const titles = {waiting:'Ждут ответа',done:'Есть ответ',deferred:'Отложены',errors:'Ошибки',all:'Все обращения'};
const noun = () => state.kind==='question' ? 'вопрос' : 'отзыв';
const plural = () => state.kind==='question' ? 'вопросов' : 'отзывов';
const itemPath = id => '/api/v2/reviews/'+encodeURIComponent(id);
function node(tag,cls,text){const n=document.createElement(tag);if(cls)n.className=cls;if(text!==undefined)n.textContent=text;return n}
async function api(path,method='GET',body){
  const r=await fetch(path,{method,headers:{'Content-Type':'application/json'},body:body===undefined?undefined:JSON.stringify(body)});
  const data=await r.json().catch(()=>({}));
  if(!r.ok)throw Error(typeof data.detail==='string'?data.detail:'Ошибка HTTP '+r.status);
  return data;
}
function notice(message,error=false){const n=$('notice');n.textContent=message;n.className='notice'+(error?' error':'');n.hidden=!message;
  if(message)setTimeout(()=>{if(n.textContent===message)n.hidden=true},7000)}
function narrow(){return matchMedia('(max-width:900px)').matches}
function view(which){$('workbench').dataset.view=which;history.replaceState(null,'',which==='detail'&&state.selected?'#item-'+encodeURIComponent(state.selected):'#queue')}
function resize(area){area.style.height='auto';area.style.height=Math.min(area.scrollHeight,Math.max(260,innerHeight*.6))+'px'}
function statusClass(s){return s==='api_confirmed'?'done':(['failed','outcome_unknown'].includes(s)?'error':'')}
function activeTab(){return state.tab==='waiting'&&state.reviewOnly?'attention':state.tab}
function countFor(v){return v.counts[activeTab()]||0}
function timeLabel(v){return v?'Синхронизация: '+new Date(Number(v)*1000).toLocaleString('ru-RU',{day:'numeric',month:'short',hour:'2-digit',minute:'2-digit'}):'Синхронизация ожидается'}
function renderHeader(){const d=state.data;if(!d)return;
  $('run-dot').classList.toggle('off',!d.processing_enabled);
  $('run-state').textContent=d.processing_enabled?'Получение и подготовка: включены'+(d.counts.processing?' · '+d.counts.processing+' в работе':''):'Получение и подготовка: пауза';
  $('processing-toggle').textContent=d.processing_enabled?'Приостановить':'Возобновить';
  $('auto-state').textContent='Автоотправка отзывов: '+(d.auto_send_enabled?'включена':'выключена')+(state.kind==='question'?' · вопросы проверяет менеджер':'');
  $('auto-toggle').textContent=d.auto_send_enabled?'Выключить':'Включить';
  $('sync-state').textContent=d.mode==='sandbox'?timeLabel(d.last_sync):'Локальная демонстрация';
  $('mode').textContent=d.mode==='sandbox'?'SANDBOX · тестовый API':'DEMO · без WB';
  $('mode').classList.toggle('sandbox',d.mode==='sandbox');
  for(const k of ['waiting','done','deferred','errors','attention','all'])$('count-'+k).textContent=d.counts[k];
  $('queue-processing').textContent=(d.counts.processing?'Готовится: '+d.counts.processing:'')+
    (d.counts.no_reply?' · Оценок без содержания: '+d.counts.no_reply:'');
  $('review-only').disabled=state.tab!=='waiting';
  document.querySelectorAll('.entity-tabs button').forEach(b=>b.classList.toggle('active',b.dataset.kind===state.kind));
}
function renderVariants(){const d=state.data;if(!d)return;const models=new Map();
  for(const v of d.variants)if(!models.has(v.product.model_id))models.set(v.product.model_id,v.product.name);
  if(!state.modelId||!models.has(state.modelId))state.modelId=models.keys().next().value;
  $('model-select').replaceChildren(...Array.from(models,([id,name])=>{const o=node('option','',name);o.value=id;return o}));
  $('model-select').value=state.modelId;
  const parent=$('variant-list');parent.replaceChildren();
  for(const v of d.variants.filter(v=>v.product.model_id===state.modelId)){
    const b=node('button','variant'+(v.product.product_id===state.productId?' active':''));b.type='button';
    b.setAttribute('aria-pressed',String(v.product.product_id===state.productId));
    const img=node('img');img.src=v.product.photo;img.alt='';
    const label=v.product.color+' · карточка';
    b.append(img,node('span','',label),node('em','',countFor(v)+' к просмотру · '+v.counts.all+' всего'));
    b.onclick=async()=>{await flushDraft();state.productId=v.product.product_id;state.selected=null;state.offset=0;view('list');load(false)};
    parent.append(b);
  }
}
function renderList(){const d=state.data;if(!d)return;
  $('queue-title').textContent=titles[state.tab];$('list-title').textContent='Отдельные '+plural();
  $('list-total').textContent=d.total+' '+plural();
  const list=$('review-list'),position=list.scrollTop;list.replaceChildren();
  if(!d.reviews.length){const empty=state.tab==='waiting'&&d.counts.processing?'Новые обращения обрабатываются.':
    state.tab==='waiting'?'Здесь нет обращений. Снимите фильтр или выберите другую карточку.':'Нет обращений по фильтру.';
    list.append(node('div','empty',empty))}
  for(const r of d.reviews){const b=node('button','review-item'+(state.selected===r.id?' selected':''));b.type='button';
    b.setAttribute('aria-current',state.selected===r.id?'true':'false');
    const top=node('div','item-top');if(r.kind==='feedback')top.append(node('span','rating','★ '+r.rating));
    top.append(node('span','item-meta',r.remote_id||r.id),node('span','item-status '+statusClass(r.status),labels[r.status]||r.status));
    b.append(top,node('div','item-body',r.body||r.pros||r.cons||'Без текста'));
    if(r.kind==='feedback'&&r.photo_urls.length)b.append(node('span','item-photo','Фото покупателя: '+r.photo_urls.length));
    b.onclick=()=>selectItem(r.id,true);list.append(b)}
  list.scrollTop=position;
  const page=Math.floor(d.offset/d.limit)+1,pages=Math.max(1,Math.ceil(d.total/d.limit));
  $('page-label').textContent=page+' / '+pages;$('prev-page').disabled=d.offset<=0;$('next-page').disabled=d.offset+d.limit>=d.total;
  document.querySelectorAll('.tabs button').forEach(b=>b.classList.toggle('active',b.dataset.tab===state.tab));
}
async function load(preserve=true){if(state.loading)return;state.loading=true;
  try{
    const q=new URLSearchParams({kind:state.kind,tab:activeTab(),product_id:state.q?'':(state.productId||''),q:state.q,offset:String(state.offset),limit:'30'});
    let d=await api('/api/v2/state?'+q);
    if(!state.productId&&!state.q&&d.variants.length){const preferred=d.variants.find(v=>v.counts[activeTab()]>0)||d.variants[0];
      state.productId=preferred.product.product_id;state.modelId=preferred.product.model_id;q.set('product_id',state.productId);d=await api('/api/v2/state?'+q)}
    state.data=d;
    if(state.offset>=d.total&&state.offset>0){state.offset=Math.max(0,state.offset-30);state.loading=false;return load(false)}
    if(state.selected&&!d.reviews.some(r=>r.id===state.selected)){state.selected=null;state.detail=null;state.dirty=false;if(narrow())view('list')}
    if(!state.selected&&d.reviews.length&&!narrow())state.selected=d.reviews[0].id;
    renderHeader();renderVariants();renderList();
    if(state.selected&&!state.dirty&&(!preserve||!state.detail||state.detail.id!==state.selected||state.detail.model_status==='running'||state.detail.model_status==='pending')){
      await selectItem(state.selected,false);
    }else if(!state.selected)renderEmpty();
  }catch(e){notice(e.message,true)}finally{state.loading=false}
}
function renderEmpty(){$('detail').replaceChildren(node('div','empty','Выберите '+(state.kind==='question'?'вопрос':'отзыв')+' из списка.'))}
let selectionVersion=0;
async function selectItem(id,show){const version=++selectionVersion;try{
  if(state.selected&&state.selected!==id)await flushDraft();
  if(state.selected!==id)state.selectedTemplate=null;
  const r=await api(itemPath(id));if(version!==selectionVersion)return;
  state.selected=id;state.detail=r;state.dirty=false;renderList();renderDetail(r);
  if(show&&narrow()){view('detail');scrollTo(0,0)}
}catch(e){notice(e.message,true)}}
let draftTimer=null,draftPromise=Promise.resolve();
async function persistDraft(){const r=state.detail,area=$('reply');if(!r||!area||r.id!==state.selected)return;
  const text=area.value;if(text===r.draft){state.dirty=false;return}
  for(let attempt=0;attempt<2;attempt++){
    try{const result=await api(itemPath(r.id)+'/draft','PUT',{text,source_hash:r.source_hash,revision:r.revision,draft_version:r.draft_version});
      if(state.detail?.id===r.id){state.detail.draft=text;state.detail.draft_version=result.draft_version;
        state.detail.generator='Правка менеджера';state.dirty=area.value!==text;
        const source=$('draft-source');if(source)source.textContent='Источник: правка менеджера';}
      return;
    }catch(e){if(!e.message.includes('Черновик изменился')||attempt)return notice(e.message,true);
      const fresh=await api(itemPath(r.id));if(fresh.source_hash!==r.source_hash||fresh.revision!==r.revision)return notice('Обращение изменилось; проверьте его перед отправкой',true);
      r.draft_version=fresh.draft_version;r.status=fresh.status;
    }
  }
}
function scheduleDraft(){state.dirty=true;clearTimeout(draftTimer);draftTimer=setTimeout(()=>{draftPromise=draftPromise.then(persistDraft)},450)}
async function flushDraft(){clearTimeout(draftTimer);try{await draftPromise;await persistDraft()}catch(e){notice(e.message,true)}}
function modelLabel(r){if(r.model_status==='running')return 'ИИ готовит ответ…';
  if(r.model_status==='pending')return 'ИИ подготовит вариант в фоне; шаблон уже доступен.';
  if(r.model_status==='retry')return r.reason||'Повтор ИИ запланирован; черновик сохранён.';
  if(r.model_status==='failed')return r.reason||'ИИ не подготовил проверенный ответ; черновик сохранён.';
  if(r.model_status==='limit')return 'Лимит ИИ исчерпан; доступен шаблонный текст.';
  return ''}
async function templatesFor(kind){if(!state.templates[kind])state.templates[kind]=(await api('/api/v2/templates?kind='+kind)).templates;return state.templates[kind]}
function renderDetail(r){const root=$('detail');root.replaceChildren();
  if(narrow()){const back=node('button','back','← К '+(r.kind==='question'?'вопросам':'отзывам'));back.onclick=async()=>{await flushDraft();view('list');scrollTo(0,0)};root.append(back)}
  const head=node('div','detail-head');
  if(r.product){const img=node('img','product-photo');img.src=r.product.photo;img.alt='Фото карточки';head.append(img)}
  const title=node('div');title.append(node('h2','',r.product?r.product.name:'Карточка не сопоставлена'));
  title.append(node('p','',r.product?(r.kind==='question'?r.product.color+' · вопрос о карточке':r.product.variant)+' · '+r.remote_id:noun()+' '+r.remote_id));
  head.append(title,node('span','item-status '+statusClass(r.status),labels[r.status]||r.status));root.append(head);
  const meta=node('div','review-meta');
  if(r.kind==='feedback')meta.append(node('strong','rating','★ '+r.rating+' из 5'));
  meta.append(node('span','',r.kind==='question'?'Вопрос '+r.remote_id:'Отзыв '+r.remote_id));root.append(meta);
  root.append(node('p','review-text',r.body||'Без основного текста'));
  if(r.kind==='feedback'){
    if(r.pros)root.append(node('p','review-extra','Плюсы: '+r.pros));
    if(r.cons)root.append(node('p','review-extra','Минусы: '+r.cons));
    if(r.photo_urls.length){const photos=node('section','photo-area');photos.append(node('h3','','Фото покупателя · '+r.photo_urls.length),
      node('p','','Фото доступны для просмотра; система не анализирует их содержимое автоматически.'));
      const strip=node('div','photo-strip');r.photo_urls.forEach((url,i)=>{const b=node('button','photo-thumb');b.type='button';
        b.setAttribute('aria-label','Открыть фото покупателя '+(i+1));const img=node('img');img.src=url;img.alt='Фото покупателя '+(i+1);b.append(img);
        b.onclick=()=>{$('photo-large').src=url;$('photo-dialog').showModal()};strip.append(b)});photos.append(strip);root.append(photos)}
  }
  if(['needs_review','deferred','ready_auto','failed','outcome_unknown'].includes(r.status)){
    const reason=node('div','reason');reason.append(node('strong','','Почему требуется проверка'),
      node('span','',r.status==='deferred'?(r.defer_reason||'Отложено менеджером'):(r.reason||'Требуется решение менеджера')));root.append(reason)}
  if(r.source_changed)root.append(node('p','private-note','Отзыв изменён или дополнен после отправки. Прежний ответ сохранён в истории; новая отправка заблокирована.'));
  if(r.kind==='feedback'){
    const parts=[];if(r.order_status)parts.push('Статус заказа WB: '+r.order_status);
    if(r.matching_size)parts.push('Отметка о размере: '+r.matching_size);
    if(r.parent_feedback_id)parts.push('Дополнение к отзыву: '+r.parent_feedback_id);
    if(r.child_feedback_id)parts.push('Есть дополнение: '+r.child_feedback_id);
    if(parts.length)root.append(node('p','editor-meta',parts.join(' · ')));
    if(r.video_url){const link=node('a','','Видео покупателя');link.href=r.video_url;link.target='_blank';link.rel='noopener noreferrer';root.append(link)}
  }
  if(r.status==='no_reply_needed'){root.append(node('p','editor-meta','WB считает оценку без содержания обработанной. Ответ не требуется.'));return}
  if(r.status==='api_confirmed'||r.status==='remote_answered'){
    root.append(node('h3','editor-label','Ответ покупателю'),node('p','review-text',r.remote_answer||r.sent_text||'Текст отсутствует'));
    root.append(node('p','editor-meta','Основание: '+(r.approval_kind==='manager'?'менеджер':'автоматическая политика')+' · '+(r.reason||'')));
    if(r.status==='remote_answered')root.append(node('p','editor-meta','Ответ обнаружен в WB; приложение его не отправляло.'));
    if(r.remote_answer_state)root.append(node('p','editor-meta','Статус ответа WB: '+r.remote_answer_state));
    if(r.kind==='question')root.append(node('p','editor-meta','Аудитория операции: '+(r.audience==='public'?'всем покупателям':'не указана')+'. Контрольное чтение не доказывает публикацию после модерации.'));
    return;
  }
  if(['failed','outcome_unknown','sending','remote_changed'].includes(r.status)){
    const box=node('div','error-actions');box.append(node('p','',r.status==='outcome_unknown'?'Отправка не повторяется. Можно ещё раз прочитать ответ в Sandbox.':'Автоматический повтор заблокирован.'));
    if(r.status==='outcome_unknown'&&state.data.mode==='sandbox'){
      const b=node('button','primary','Проверить ответ в Sandbox');b.onclick=async()=>{b.disabled=true;try{const d=await api(itemPath(r.id)+'/reconcile','POST');notice(d.status==='api_confirmed'?'Ответ подтверждён чтением.':'Совпадающий ответ пока не найден.');state.selected=null;state.detail=null;await load(false)}catch(e){notice(e.message,true);b.disabled=false}};box.append(b)}
    if(r.status==='failed'&&((r.reason||'').startsWith('Проверка перед POST не выполнена')||(r.reason||'').startsWith('Проверка перед отправкой не выполнена'))){
      const b=node('button','','Вернуть на разбор');b.onclick=async()=>{b.disabled=true;try{await api(itemPath(r.id)+'/retry-preflight','POST');state.tab='waiting';state.selected=null;state.detail=null;await load(false)}catch(e){notice(e.message,true);b.disabled=false}};box.append(b)}
    root.append(box);return;
  }
  const ml=modelLabel(r);if(ml)root.append(node('p','model-status'+(r.model_status==='running'?' running':''),ml));
  const label=node('label','editor-label','Ответ покупателю');label.htmlFor='reply';root.append(label);
  const area=node('textarea','reply');area.id='reply';area.value=r.draft||'';area.placeholder='Напишите ответ на основании известных сведений';area.maxLength=700;
  area.oninput=()=>{$('char-count').textContent=area.value.length+' / 700';resize(area);scheduleDraft();const b=$('send-button');if(b)b.disabled=!area.value.trim()||(r.kind==='question'&&r.audience!=='public')};
  root.append(area);resize(area);
  const editorMeta=node('div','editor-meta');const source=node('span','',r.generator?'Источник: '+r.generator:'Черновик не подготовлен: требуются сведения менеджера');source.id='draft-source';
  const chars=node('span','',area.value.length+' / 700');chars.id='char-count';editorMeta.append(source,chars);root.append(editorMeta);
  const tools=node('div','editor-tools');const selector=node('select');selector.id='template-select';
  const placeholder=node('option','','Выбрать готовый ответ');placeholder.value='';selector.append(placeholder);
  selector.onchange=()=>{state.selectedTemplate={id:r.id,value:selector.value}};
  tools.append(selector);
  templatesFor(r.kind).then(list=>{if(state.selected!==r.id||!$('template-select'))return;
    for(const t of list){const o=node('option','',t.name+' · '+t.intent);o.value=t.id;selector.append(o)}
    if(state.selectedTemplate?.id===r.id)selector.value=state.selectedTemplate.value}).catch(e=>notice(e.message,true));
  const insert=node('button','','Вставить шаблон');insert.onclick=async()=>{const templateId=state.selectedTemplate?.id===r.id?state.selectedTemplate.value:selector.value;
    if(!templateId)return notice('Выберите шаблон');
    try{await flushDraft();const d=await api(itemPath(r.id)+'/template','POST',{template_id:templateId,source_hash:r.source_hash,revision:r.revision,draft_version:r.draft_version});
      if(state.selected!==r.id)return;
      const current=state.detail?.id===r.id?state.detail:r;
      current.draft=d.text;current.draft_version=d.draft_version;current.generator=d.source;current.status='needs_review';state.dirty=false;
      renderDetail(current);
      const b=$('send-button');if(b)b.disabled=r.kind==='question'&&r.audience!=='public';notice('Шаблон вставлен в редактор.');
    }catch(e){notice(e.message,true)}};
  const manage=node('button','','Мои шаблоны');manage.onclick=()=>openTemplates();
  const rephrase=node('button','','Переформулировать ИИ');rephrase.onclick=async()=>{try{await flushDraft();const snapshot=area.value;
      rephrase.disabled=true;rephrase.textContent='ИИ готовит…';state.dirty=true;
      const d=await api(itemPath(r.id)+'/rephrase','POST',{text:snapshot,source_hash:r.source_hash,revision:r.revision,draft_version:r.draft_version});
      if(d.status==='ready'){
        r.draft=d.text;r.draft_version=d.draft_version;r.generator=d.source;
        if(area.value===snapshot){area.value=d.text;resize(area);source.textContent='Источник: '+d.source;chars.textContent=d.text.length+' / 700';state.dirty=false;notice('ИИ предложил новый проверенный текст.')}
        else{scheduleDraft();notice('Вы изменили текст во время генерации; правки сохранены, предложение ИИ не вставлено.')}
      }else{state.dirty=false;notice(d.detail||'ИИ не подготовил ответ; текущий текст сохранён.',true)}
    }catch(e){state.dirty=false;notice(e.message,true)}finally{rephrase.disabled=false;rephrase.textContent='Переформулировать ИИ'}};
  tools.append(insert,manage,rephrase);root.append(tools);
  if(r.facts_used?.length){const details=node('details','facts');details.append(node('summary','','Факты, использованные в ответе · '+r.facts_used.length));
    details.append(node('p','','Эти сведения взяты из профиля выбранного варианта. Изменение профиля отменяет разрешение на ещё не отправленный ответ.'));
    const ul=node('ul');r.facts_used.forEach(f=>{const li=node('li');li.append(node('span','',f.value),node('small','',f.source));ul.append(li)});details.append(ul);root.append(details)}
  if(r.kind==='question'){
    const box=node('div','audience-choice');const al=node('label','','Кто увидит ответ');al.htmlFor='audience';
    const select=node('select');select.id='audience';for(const [value,name] of [['','Выберите аудиторию'],['public','Всем покупателям'],['private','Только автору']]){const o=node('option','',name);o.value=value;select.append(o)}
    select.value=r.audience||'';
    const info=node('p','',r.audience==='private'?'Личный ответ через публичный API недоступен. Скопируйте текст для WB Partners.':'Публичный ответ пройдёт модерацию WB; контрольное чтение не доказывает видимость.');
    select.onchange=async()=>{try{await flushDraft();if(!select.value){return notice('Выберите аудиторию вопроса')}
      const d=await api(itemPath(r.id)+'/audience','PUT',{audience:select.value,source_hash:r.source_hash,revision:r.revision,draft_version:r.draft_version});
      r.audience=d.audience;r.draft_version=d.draft_version;
      info.textContent=d.audience==='private'?'Личный ответ через публичный API недоступен. Скопируйте текст для WB Partners.':'Публичный ответ пройдёт модерацию WB; контрольное чтение не доказывает видимость.';
      const send=$('send-button');if(send)send.disabled=!area.value.trim()||d.audience!=='public';
      const copy=$('private-copy');if(copy)copy.hidden=d.audience!=='private';
      const portal=$('private-portal');if(portal)portal.hidden=d.audience!=='private';
    }catch(e){notice(e.message,true);select.value=r.audience||''}};
    box.append(al,select,info);root.append(box);
  }
  const actions=node('div','actions');const note=node('span','send-note',state.data.mode==='sandbox'?'Тестовый API; видимость после модерации не подтверждена.':'Локальная имитация без WB.');
  const defer=node('button','secondary','Отложить');defer.onclick=()=>{$('defer-reason').value=r.defer_reason||'';$('defer-dialog').showModal()};
  const send=node('button','primary',state.data.mode==='sandbox'?'Отправить в Sandbox':'Имитировать отправку');send.id='send-button';
  send.disabled=!area.value.trim()||(r.kind==='question'&&r.audience!=='public');
  send.onclick=async()=>{try{await flushDraft();send.disabled=true;
      const d=await api(itemPath(r.id)+'/send','POST',{text:area.value,source_hash:r.source_hash,revision:r.revision,audience:r.kind==='question'?r.audience:null,draft_version:r.draft_version});
      notice(d.status==='api_confirmed'?'Ответ подтверждён контрольным чтением.':d.detail||'Проверьте результат в ошибках.',d.status!=='api_confirmed');await advance();
    }catch(e){notice(e.message,true);send.disabled=false}};
  actions.append(note,defer);
  if(r.kind==='question'){
    const copy=node('button','secondary','Скопировать для WB Partners');copy.id='private-copy';copy.hidden=r.audience!=='private';
    copy.onclick=async()=>{await flushDraft();if(!area.value.trim())return notice('Нет текста для копирования',true);
      try{if(navigator.clipboard&&isSecureContext)await navigator.clipboard.writeText(area.value);
        else{area.select();if(!document.execCommand('copy'))throw Error('Clipboard unavailable');area.setSelectionRange(area.value.length,area.value.length)}
        notice('Текст скопирован. Отправьте его автору через WB Partners; здесь вопрос останется без ответа.');
      }catch(e){notice('Не удалось скопировать автоматически; выделите текст в редакторе.',true)}};actions.append(copy)
    const portal=node('a','','Открыть WB Partners');portal.href='https://seller.wildberries.ru/';portal.target='_blank';portal.rel='noopener noreferrer';
    portal.id='private-portal';portal.hidden=r.audience!=='private';actions.append(portal);
  }
  actions.append(send);root.append(actions);
}
async function openTemplates(){try{await renderTemplateLibrary();$('templates-dialog').showModal()}catch(e){notice(e.message,true)}}
async function renderTemplateLibrary(){state.templates={};const list=(await templatesFor(state.kind));const root=$('template-library');root.replaceChildren();
  for(const t of list){const row=node('div','template-entry');const info=node('div');info.append(node('strong','',t.name),node('small','',t.kind+' · '+t.intent+' · '+(t.params.length?t.params.join(', '):'без параметров')),
    node('span','',t.body));row.append(info);
    if(!t.is_builtin){const edit=node('button','','Редактировать');edit.onclick=()=>{$('template-id').value=t.id;$('template-name').value=t.name;$('template-kind').value=t.kind;$('template-intent').value=t.intent;$('template-body').value=t.body};row.append(edit)}root.append(row)}
  $('template-kind').value=state.kind;
}
async function renderProfiles(){try{const d=await api('/api/v2/profiles');const root=$('profiles-content');root.replaceChildren();
  $('profile-upload-label').hidden=state.data?.mode!=='sandbox';
  for(const v of d.variants){const card=node('article','profile-card');const img=node('img');img.src=v.product.photo;img.alt='Фото варианта';
    card.append(img,node('h2','',v.product.name+' · '+v.product.variant));const facts=node('div','fact-list');
    for(const f of v.facts)facts.append(node('div','',({composition:'Состав',wash:'Стирка',dry:'Сушка',iron:'Глажка',fit:'Посадка',measurement:'Замеры',contents:'Комплектность',feature:'Свойства'}[f.field]||f.field)+': '+f.value));
    card.append(facts);root.append(card)}}catch(e){notice(e.message,true)}}
async function advance(){state.tab='waiting';state.selected=null;state.detail=null;state.dirty=false;await load(false);
  if(narrow()){if(state.data?.reviews.length)await selectItem(state.data.reviews[0].id,true);else view('list')}}
$('processing-toggle').onclick=async()=>{try{await api('/api/v2/settings','PUT',{processing_enabled:!state.data.processing_enabled});await load(false)}catch(e){notice(e.message,true)}};
$('auto-toggle').onclick=async()=>{try{await api('/api/v2/settings','PUT',{auto_send_enabled:!state.data.auto_send_enabled});await load(false)}catch(e){notice(e.message,true)}};
$('model-select').onchange=async e=>{await flushDraft();state.modelId=e.target.value;const variants=state.data.variants.filter(v=>v.product.model_id===state.modelId);
  state.productId=(variants.find(v=>countFor(v)>0)||variants[0])?.product.product_id||null;state.selected=null;state.offset=0;view('list');load(false)};
let searchTimer;$('search').oninput=e=>{clearTimeout(searchTimer);searchTimer=setTimeout(()=>{state.q=e.target.value.trim();state.offset=0;state.selected=null;load(false)},300)};
$('prev-page').onclick=()=>{state.offset=Math.max(0,state.offset-30);state.selected=null;load(false)};
$('next-page').onclick=()=>{state.offset+=30;state.selected=null;load(false)};
$('review-only').onchange=()=>{state.reviewOnly=$('review-only').checked;state.offset=0;state.selected=null;load(false)};
document.querySelectorAll('.entity-tabs button').forEach(b=>b.onclick=async()=>{await flushDraft();state.kind=b.dataset.kind;state.tab='waiting';state.productId=null;state.modelId=null;state.selected=null;state.detail=null;state.offset=0;state.q='';$('search').value='';view('list');load(false)});
document.querySelectorAll('.tabs button').forEach(b=>b.onclick=async()=>{await flushDraft();state.tab=b.dataset.tab;state.offset=0;state.selected=null;state.detail=null;view('list');load(false)});
$('profiles-toggle').onclick=async()=>{await flushDraft();state.profiles=true;$('workbench').hidden=true;document.querySelector('.tabs').hidden=true;document.querySelector('.entity-tabs').hidden=true;document.querySelector('.queue-filter').hidden=true;$('profiles-view').hidden=false;await renderProfiles()};
$('profiles-close').onclick=()=>{state.profiles=false;$('profiles-view').hidden=true;$('workbench').hidden=false;document.querySelector('.tabs').hidden=false;document.querySelector('.entity-tabs').hidden=false;document.querySelector('.queue-filter').hidden=false};
$('defer-dialog').addEventListener('close',async()=>{if($('defer-dialog').returnValue!=='submit')return;const reason=$('defer-reason').value.trim();if(reason.length<3)return notice('Укажите причину отложения',true);
  try{await flushDraft();await api(itemPath(state.selected)+'/defer','POST',{reason});notice('Обращение отложено.');await advance()}catch(e){notice(e.message,true)}});
$('profile-upload').onchange=async e=>{const file=e.target.files?.[0];if(!file)return;
  try{const response=await fetch('/api/v2/profiles/file',{method:'PUT',headers:{'Content-Type':'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'},body:file});
    const data=await response.json();if(!response.ok)throw Error(data.detail||'Не удалось обновить профиль');notice(data.detail);await renderProfiles();await load(false)}
  catch(err){notice(err.message,true)}finally{e.target.value=''}};
$('template-cancel').onclick=()=>$('templates-dialog').close();
$('templates-dialog').onclose=async()=>{await flushDraft();if(state.detail&&!state.dirty)renderDetail(state.detail)};
$('template-form').onsubmit=async e=>{e.preventDefault();const id=$('template-id').value;
  const body={name:$('template-name').value,kind:$('template-kind').value,intent:$('template-intent').value,body:$('template-body').value};
  try{await api('/api/v2/templates'+(id?'/'+encodeURIComponent(id):''),id?'PUT':'POST',body);
    $('template-form').reset();$('template-id').value='';await renderTemplateLibrary();notice('Готовый ответ сохранён в локальной библиотеке.')}
  catch(err){notice(err.message,true)}};
$('photo-close').onclick=()=>$('photo-dialog').close();$('photo-dialog').onclick=e=>{if(e.target===$('photo-dialog'))$('photo-dialog').close()};
let wasNarrow=narrow();addEventListener('resize',()=>{const now=narrow();if(now===wasNarrow)return;wasNarrow=now;if(state.detail&&!state.dirty)renderDetail(state.detail)});
load(false);setInterval(()=>{if(!state.profiles&&!state.dirty)load(true)},4000);
