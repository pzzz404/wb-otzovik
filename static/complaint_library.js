'use strict';
// Library workspace stays inside the existing Knowledge tab.
const baseKnowledgeRender=renderKnowledge;
renderKnowledge=function(){
  baseKnowledgeRender();
  const select=$('#scenario-select');if(!select)return;
  const button=document.createElement('button');button.id='library-entry';button.type='button';button.textContent='Выбрать из библиотеки';
  select.insertAdjacentElement('afterend',button);
  button.onclick=()=>{state.libraryOpen=!state.libraryOpen;renderKnowledge()};
  if(state.libraryOpen){$('#settings-content .settings-grid').hidden=true;button.classList.add('active');renderComplaintLibrary()}
};
const libraryFields=['goal','explanation','action','prevention','not_applicable','seller_path','prohibited','approval_source','label_wash'];
const materialLabel={wool:'Шерстяной трикотаж',cotton:'Хлопок',colored:'Окрашенные изделия',any:'Любой состав'};
function localKey(item){return 'complaint:'+item.id+':'+item.version}
function editable(item){try{return {...item,...JSON.parse(sessionStorage.getItem(localKey(item))||'{}')}}catch{return item}}
function libraryField(name,label,value,disabled){
  return `<label>${label}<textarea name="${name}" maxlength="700" ${disabled?'disabled':''}>${esc(value||'')}</textarea></label>`;
}
function libraryAdaptation(list,key,product){
  const rows=list.filter(x=>x.library_key===key);
  return rows.find(x=>x.scope==='variant'&&x.owner_id===product.product_id)||rows.find(x=>x.scope==='model'&&x.owner_id===product.model_id);
}
function renderComplaintLibrary(){
  const k=state.knowledge,product=k.products.find(x=>x.product_id===state.productKnowledge);
  if(!product)return;
  const selected=k.library.find(x=>x.key===state.libraryKey)||k.library[0];state.libraryKey=selected.key;
  const current=libraryAdaptation(k.library_adaptations,selected.key,product),values=current&&current.status==='draft'?editable(current):current;
  const query=state.libraryQuery||'',filter=state.libraryFilter||'all';
  const shown=k.library.filter(x=>(filter==='all'||x.material===filter||x.material==='any')&&
    (x.name+' '+x.goal+' '+x.action).toLowerCase().includes(query.toLowerCase()));
  const editor=state.libraryView==='editor'&&current;
  const work=document.createElement('section');work.className='library-workspace';work.id='library-workspace';
  work.innerHTML=`<div class="library-heading"><button id="library-back" type="button">← К сценариям</button><div><h2>Готовые решения для претензий</h2><p>Выберите ситуацию, адаптируйте решение и проверьте ответ.</p></div></div>
    <div class="library-layout"><div class="library-chooser"><div class="library-search"><input id="library-search" type="search" value="${esc(query)}" placeholder="Найти ситуацию" aria-label="Поиск"><select id="library-filter" aria-label="Тип изделия"><option value="all">Все изделия</option>${[['wool','Шерсть'],['cotton','Хлопок'],['colored','Окрашенные']].map(([v,l])=>`<option value="${v}" ${filter===v?'selected':''}>${l}</option>`).join('')}</select></div>
    <div class="library-list">${shown.map(x=>`<button type="button" data-library-key="${esc(x.key)}" class="library-item ${x.key===selected.key?'selected':''}"><strong>${esc(x.name)}</strong><span>${esc(materialLabel[x.material])} · ${esc(x.goal)}</span></button>`).join('')||'<p>Ничего не найдено</p>'}</div></div>
    <div class="library-detail"><div class="library-context"><img src="${esc(product.photo)}" alt=""><div><strong>${esc(product.name)} · ${esc(product.color)} · ${esc(product.size)}</strong><small>${esc(product.product_id)} · ${esc(product.model_id)}${current?` · ${current.status==='draft'?'черновик':'утверждено'} v${current.version}`:''}</small></div></div>
    ${editor?libraryEditor(selected,values):libraryOverview(selected,current,product)}</div></div>`;
  $('#settings-content').append(work);
  const actions=work.querySelector('.library-actions');
  if(actions){$('#settings-footer').replaceChildren(actions)}
  else $('#settings-footer').innerHTML='<p>Заготовка действует только после адаптации и утверждения для товара.</p>';
  $('#library-back').onclick=()=>{state.libraryOpen=false;renderKnowledge()};
  $('#library-search').oninput=e=>{state.libraryQuery=e.target.value;rerenderLibrary()};
  $('#library-filter').onchange=e=>{state.libraryFilter=e.target.value;rerenderLibrary()};
  work.querySelectorAll('[data-library-key]').forEach(b=>b.onclick=()=>{state.libraryKey=b.dataset.libraryKey;state.libraryView='overview';rerenderLibrary()});
  if($('#library-adapt'))$('#library-adapt').onclick=()=>attempt(async()=>{
    const scope=$('#library-scope').value;
    const x=await api('/api/v4/knowledge/library/adapt','POST',{key:selected.key,scope,owner_id:scope==='model'?product.model_id:product.product_id});
    state.libraryView='editor';notify(x.detail);await loadKnowledge();
  });
  $('#library-edit')?.addEventListener('click',()=>{state.libraryView='editor';rerenderLibrary()});
  $('#library-overview')?.addEventListener('click',()=>{state.libraryView='overview';rerenderLibrary()});
  if(editor)bindLibraryEditor(current);
}
function rerenderLibrary(){$('#library-workspace')?.remove();renderComplaintLibrary()}
function libraryOverview(item,current,product){
  return `<h3>${esc(item.name)}</h3><p class="library-purpose">${esc(item.goal)}</p>
  ${item.key==='size_after'&&current&&current.not_applicable?.includes('Если товар ещё не куплен')?'<p class="micro">В утверждённой версии осталась фраза для покупателя до заказа. Нажмите «Адаптировать для товара», проверьте предложенный черновик и утвердите новую версию; действующий ответ до этого не изменится.</p>':''}
  <div class="library-summary"><strong>Что объясняем</strong><p>${esc(item.explanation)}</p><strong>Что покупатель может сделать сейчас</strong><p>${esc(item.action)}</p><strong>Когда совет не подходит</strong><p>${esc(item.not_applicable)}</p></div>
  <details><summary>Основание и источник</summary><p>${esc(item.source_summary)}</p><a href="${esc(item.source_url)}" target="_blank" rel="noopener noreferrer">Открыть источник</a><small>Проверено ${esc(item.source_checked)} · заготовка не действует до утверждения</small></details>
  <div class="library-start"><label>Область применения<select id="library-scope"><option value="model">Модель ${esc(product.model_id)}</option><option value="variant">Только ${esc(product.product_id)}</option></select></label><button id="library-adapt" class="primary" type="button">Адаптировать для товара</button>${current?`<button id="library-edit" type="button">Открыть ${current.status==='draft'?'черновик':'утверждённую версию'}</button>`:''}</div>`;
}
function libraryEditor(item,v){
  const draft=v.status==='draft',disabled=!draft,scope=v.scope==='model'?'модели':'варианта';
  return `<div class="library-editor-head"><button id="library-overview" type="button">← О заготовке</button><h3>${esc(item.name)}</h3><p>${draft?'Черновик':'Утверждённая версия'} для ${scope} · v${v.version}</p></div>
  <form id="library-form"><div class="library-block"><h4>1. Объяснение</h4>${libraryField('explanation','Что объясняем',v.explanation,disabled)}</div>
  <div class="library-block"><h4>2. Помощь сейчас</h4>${libraryField('action','Действия покупателя',v.action,disabled)}${libraryField('not_applicable','Когда совет неприменим',v.not_applicable,disabled)}</div>
  <div class="library-block"><h4>3. Профилактика</h4>${libraryField('prevention','Как предотвратить повторение',v.prevention,disabled)}</div>
  <details class="library-extra"><summary>Цель, источник и правила ответа</summary>${libraryField('goal','Цель',v.goal,disabled)}<p class="micro">Цель задаёт акцент ИИ и вступление шаблона.</p>${libraryField('seller_path','Дальнейший путь решения',v.seller_path,disabled)}<p class="micro">Путь включается в ответ, если не повторяет утверждённые действия.</p>${libraryField('prohibited','Запрещённые утверждения',v.prohibited,disabled)}<p class="micro">Общие ограничения передаются ИИ; конкретные фразы в «кавычках» дополнительно проверяются сервером дословно.</p>${libraryField('approval_source','Источник процедуры и применимость',v.approval_source,disabled)}${libraryField('label_wash','Режим ухода по ярлыку',v.label_wash,disabled)}<p class="micro">Основание: ${esc(v.source_summary)} · <a href="${esc(v.source_url)}" target="_blank" rel="noopener noreferrer">источник</a></p></details>
  <div class="library-check"><h4>Проверить на отзыве</h4><label>ID отзыва из очереди<input id="library-review" value="R056" list="library-review-list"><datalist id="library-review-list">${state.rows.map(r=>`<option value="${esc(r.id)}">${esc((r.body||r.cons||'').slice(0,60))}</option>`).join('')}</datalist></label>
  <div id="library-review-text" class="library-quote"></div><div class="library-preview-actions"><button id="library-preview" type="button">Проверить пример</button><button id="library-ai" type="button">Запустить ИИ · расходует лимит</button></div>
  <div id="library-preview-result" aria-live="polite"><p class="micro">Пример не отправляется покупателю.</p></div></div>
  <div class="library-actions">${draft?'<button id="library-save" type="submit" form="library-form">Сохранить черновик</button>':''}<span id="library-approval-note" class="micro">${draft&&!v.approval_source?.trim()?'Для утверждения укажите источник процедуры.':'Новая версия действует после утверждения.'}</span>${draft?`<button id="library-approve" class="primary" type="button" ${!v.approval_source?.trim()?'disabled':''}>Утвердить для ${scope}</button>`:''}</div></form>`;
}
function bindLibraryEditor(current){
  const draft=current.status==='draft',form=$('#library-form');
  const reviewText=async()=>{const id=$('#library-review').value.trim();if(!id)return;try{const r=await api('/api/v2/reviews/'+encodeURIComponent(id));$('#library-review-text').textContent=`${id} · ${r.rating||''}★ · ${r.body||r.cons||r.pros||''}`}catch{$('#library-review-text').textContent='Отзыв не найден: '+id}};
  $('#library-review').onchange=reviewText;reviewText();
  const values=()=>Object.fromEntries(libraryFields.map(name=>[name,form.elements[name].value]));
  form.oninput=()=>{if(!draft)return;sessionStorage.setItem(localKey(current),JSON.stringify(values()));const source=form.elements.approval_source.value.trim();$('#library-approve').disabled=!source;$('#library-approval-note').textContent=source?'Проверьте пример перед утверждением.':'Для утверждения укажите источник процедуры.'};
  const save=async()=>{if(!draft)return;await api('/api/v4/knowledge/library/'+encodeURIComponent(current.id),'PUT',{version:current.version,data:values()});sessionStorage.removeItem(localKey(current));notify('Черновик сохранён')};
  form.onsubmit=e=>{e.preventDefault();attempt(save)};
  const preview=live_ai=>attempt(async()=>{
    if(draft)await save();
    const x=await api('/api/v4/knowledge/library/preview','POST',{scenario_id:current.id,version:current.version,review_id:$('#library-review').value,live_ai});
    state.libraryPreview=x;state.libraryPreviewTab=live_ai?'ai':'template';showLibraryPreview();
  });
  $('#library-preview').onclick=()=>preview(false);$('#library-ai').onclick=()=>preview(true);
  $('#library-approve')?.addEventListener('click',()=>attempt(async()=>{await save();await api('/api/v4/knowledge/library/'+encodeURIComponent(current.id)+'/approve?version='+current.version,'POST');notify('Версия утверждена');await loadKnowledge()}));
}
function showLibraryPreview(){
  const x=state.libraryPreview,box=$('#library-preview-result');if(!x||!box)return;
  const tab=state.libraryPreviewTab==='ai'&&x.ai?'ai':'template';
  box.innerHTML=`${x.ai_error?`<p class="warning-box">${esc(x.ai_error)}</p>`:''}<div class="library-tabs"><button type="button" data-preview-tab="template" class="${tab==='template'?'active':''}">Шаблон</button><button type="button" data-preview-tab="ai" class="${tab==='ai'?'active':''}" ${x.ai?'':'disabled'}>ИИ</button></div><p class="library-answer">${esc(tab==='ai'?x.ai:x.template)}</p><small>${esc(x.scenario_id)} · v${x.version} · ${tab==='ai'?'ИИ адаптировал ответ; точная процедура утверждена продавцом':'Утверждённый шаблон'} · не отправлено</small>`;
  box.querySelectorAll('[data-preview-tab]').forEach(b=>b.onclick=()=>{state.libraryPreviewTab=b.dataset.previewTab;showLibraryPreview()});
  box.scrollIntoView({block:'nearest'});
}
