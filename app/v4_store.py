"""Per-database V4 configuration, knowledge, campaigns and proposal history.

Every Demo browser session has its own SQLite database. Nothing in this module
reads production WB data or shares mutable configuration between sessions.
"""
import copy,json,time
from .core import digest
from .profile import Profile

DEFAULT_CONFIG={
 'version':1,'send_mode':'rules','generation_mode':'combined','only_new':True,
 'rating_actions':{'1':'review','2':'review','3':'review','4':'auto','5':'auto'},
 'exceptions':{'complaint':True,'missing_fact':True,'personal_size':True,'photo_context':True},
 'tone':'respectful','length':'short','use_name':False,
 'llm_budget_per_day':30,'llm_max_per_cycle':2,
 'scope':{'type':'all','id':None},'backlog_cutoff':0,
}
SCENARIOS={
 'thanks':{'name':'Благодарность','enabled':True,'when':'Положительный отзыв без жалобы','goal':'Поблагодарить покупателя','include':['thanks'],'advice':'','restrictions':'Не благодарить за выкуп, если он не подтверждён.','review_on_complaint':True},
 'material':{'name':'Преимущества материала','enabled':True,'when':'Похвала материалу','goal':'Кратко отметить подтверждённое свойство','include':['composition','feature'],'advice':'','restrictions':'Не выводить мягкость только из состава.','review_on_complaint':True},
 'care':{'name':'Уход после покупки','enabled':True,'when':'Вопрос об уходе или уместная положительная благодарность','goal':'Дать один короткий совет по утверждённому уходу','include':['wash'],'advice':'','restrictions':'Уход только для той же модели и варианта.','review_on_complaint':True},
 'size':{'name':'Размер','enabled':True,'when':'Вопрос о размере или посадке','goal':'Напомнить сверить замеры','include':['fit','measurement'],'advice':'','restrictions':'Не обещать точную посадку.','review_on_complaint':True},
 'pilling':{'name':'Катышки: объяснение и уход','enabled':True,'when':'Обращение о катышках или профилактике','goal':'Ответить осторожно, с подходящим советом по уходу','include':['composition','wash'],'advice':'','restrictions':'Не считать катышки доказательством качества и не обвинять покупателя.','review_on_complaint':True},
 'complaint':{'name':'Жалоба','enabled':True,'when':'Есть претензия к качеству, размеру или доставке','goal':'Ответить с эмпатией без обещаний','include':[],'advice':'','restrictions':'Всегда проверка менеджером; не утверждать причину дефекта.','review_on_complaint':True},
}

def init(cx,base_profile):
    cx.executescript('''
    CREATE TABLE IF NOT EXISTS v4_config(scope_type TEXT NOT NULL,scope_id TEXT NOT NULL,version INTEGER NOT NULL,data TEXT NOT NULL,updated_at REAL NOT NULL,PRIMARY KEY(scope_type,scope_id));
    CREATE TABLE IF NOT EXISTS v4_facts(fact_key TEXT NOT NULL,version INTEGER NOT NULL,data TEXT NOT NULL,status TEXT NOT NULL,updated_at REAL NOT NULL,PRIMARY KEY(fact_key,version));
    CREATE TABLE IF NOT EXISTS v4_scenarios(scenario_id TEXT NOT NULL,version INTEGER NOT NULL,data TEXT NOT NULL,status TEXT NOT NULL,updated_at REAL NOT NULL,PRIMARY KEY(scenario_id,version));
    CREATE TABLE IF NOT EXISTS v4_seller_advice(model_id TEXT NOT NULL,version INTEGER NOT NULL,data TEXT NOT NULL,status TEXT NOT NULL,updated_at REAL NOT NULL,PRIMARY KEY(model_id,version));
    CREATE TABLE IF NOT EXISTS v4_campaigns(campaign_id TEXT NOT NULL,version INTEGER NOT NULL,data TEXT NOT NULL,status TEXT NOT NULL,updated_at REAL NOT NULL,PRIMARY KEY(campaign_id,version));
    CREATE TABLE IF NOT EXISTS v4_candidates(candidate_id TEXT PRIMARY KEY,data TEXT NOT NULL,updated_at REAL NOT NULL);
    CREATE TABLE IF NOT EXISTS v4_proposals(proposal_id TEXT PRIMARY KEY,data TEXT NOT NULL,status TEXT NOT NULL,created_at REAL NOT NULL,updated_at REAL NOT NULL);
    CREATE TABLE IF NOT EXISTS v4_imports(id INTEGER PRIMARY KEY AUTOINCREMENT,kind TEXT NOT NULL,source TEXT NOT NULL,detail TEXT,created_at REAL NOT NULL);
    CREATE TABLE IF NOT EXISTS v4_tasks(id TEXT PRIMARY KEY,kind TEXT NOT NULL,product_id TEXT NOT NULL,source_proposal_id TEXT NOT NULL,status TEXT NOT NULL,detail TEXT NOT NULL,created_at REAL NOT NULL);
    ''')
    now=time.time()
    cx.execute('INSERT OR IGNORE INTO v4_config VALUES(?,?,?,?,?)',('all','',1,json.dumps(DEFAULT_CONFIG,ensure_ascii=False),now))
    for fact in base_profile.data['Факты']:
        item=dict(fact);item['method']='seed_xlsx';item['excerpt']=str(fact['source']);item['author']='Исходный профиль';
        key=f"{item['scope']}:{item['owner_id']}:{item['field']}"
        if not cx.execute('SELECT 1 FROM v4_facts WHERE fact_key=? LIMIT 1',(key,)).fetchone():
            cx.execute('INSERT INTO v4_facts VALUES(?,?,?,?,?)',(key,int(item['version']),json.dumps(item,ensure_ascii=False,default=str),item['status'],now))
    if not cx.execute('SELECT 1 FROM v4_scenarios LIMIT 1').fetchone():
        for key,scenario in SCENARIOS.items():
            cx.execute('INSERT INTO v4_scenarios VALUES(?,?,?,?,?)',(key,1,json.dumps(scenario,ensure_ascii=False),'active',now))
    # A synthetic seller-approved procedure is attached only to the Demo dress.
    # The general library is never activated by its presence alone.
    if 'DR-01' in base_profile.products:
        from .complaint_library import LIBRARY
        sid='shrink_wool:model:DRESS'
        if not cx.execute('SELECT 1 FROM v4_scenarios WHERE scenario_id=? LIMIT 1',(sid,)).fetchone():
            fact=next((x for x in base_profile.data['Факты'] if x['scope']=='model' and x['owner_id']=='DRESS' and x['field']=='wash' and x['status']=='approved'),None)
            item={**LIBRARY['shrink_wool'],'library_key':'shrink_wool','scope':'model','owner_id':'DRESS',
                  'label_wash':str(fact['value']) if fact else '',
                  'approval_source':'Синтетическая инструкция продавца Demo, адаптирована к DR-01',
                  'approved_by':'Demo seller seed'}
            cx.execute('INSERT INTO v4_scenarios VALUES(?,?,?,?,?)',(sid,1,json.dumps(item,ensure_ascii=False),'active',now))
        else:
            # Upgrade only the untouched synthetic seed. Seller versions keep their own approval history.
            rows=cx.execute('SELECT version,data,status FROM v4_scenarios WHERE scenario_id=? ORDER BY version',(sid,)).fetchall()
            if rows and rows[-1]['status']=='active':
                old=json.loads(rows[-1]['data'])
                old_seed=old.get('approved_by')=='Demo seller seed'
                old_procedure='полного восстановления обещать нельзя' in old.get('action','')
                old_explanation=old.get('explanation')=='Шерстяной трикотаж может изменить форму после стирки.'
                if old_seed and (old_procedure or old_explanation):
                    updated={**old,**{key:LIBRARY['shrink_wool'][key] for key in
                                   ('explanation','action','prevention','not_applicable','seller_path')}}
                    cx.execute('INSERT OR IGNORE INTO v4_scenarios VALUES(?,?,?,?,?)',
                               (sid,rows[-1]['version']+1,json.dumps(updated,ensure_ascii=False),'active',now))


def _latest(cx,table,keyfield):
    rows=cx.execute(f'SELECT * FROM {table} ORDER BY {keyfield},version DESC').fetchall()
    seen=set();out=[]
    for row in rows:
        if row[keyfield] in seen:continue
        seen.add(row[keyfield]);out.append(dict(row))
    return out

def config(cx,product=None,include_product=True):
    row=cx.execute("SELECT * FROM v4_config WHERE scope_type='all' AND scope_id=''").fetchone()
    result=copy.deepcopy(DEFAULT_CONFIG)
    if row:result.update(json.loads(row['data']))
    scope_used={'type':'all','id':None,'version':row['version'] if row else 1}
    if product:
        scopes=[('model',product['model_id'])]
        if include_product:scopes.append(('product',product['product_id']))
        for typ,key in scopes:
            override=cx.execute('SELECT * FROM v4_config WHERE scope_type=? AND scope_id=?',(typ,key)).fetchone()
            if override:
                payload=json.loads(override['data'])
                for field,value in payload.items():
                    if field=='rating_actions':result[field].update(value)
                    elif field=='exceptions':result[field].update(value)
                    else:result[field]=value
                scope_used={'type':typ,'id':key,'version':override['version']}
    result['resolved_scope']=scope_used
    return result

def save_config(cx,payload,scope_type='all',scope_id=''):
    if scope_type not in ('all','model','product') or (scope_type!='all' and not scope_id):raise ValueError('Неверная область правил')
    allowed={'send_mode','generation_mode','only_new','rating_actions','exceptions','tone','length','use_name','llm_budget_per_day','llm_max_per_cycle'}
    if set(payload)-allowed:raise ValueError('Неизвестная настройка')
    old=cx.execute('SELECT * FROM v4_config WHERE scope_type=? AND scope_id=?',(scope_type,scope_id)).fetchone()
    data=json.loads(old['data']) if old else (copy.deepcopy(DEFAULT_CONFIG) if scope_type=='all' else {})
    data.update(payload)
    if data.get('send_mode','rules') not in ('review_all','rules'):raise ValueError('Неверный режим отправки')
    if data.get('only_new') is False:raise ValueError('Накопленные обращения обрабатываются отдельным безопасным действием')
    if data.get('generation_mode','combined') not in ('templates','ai','combined'):raise ValueError('Неверный режим подготовки')
    if data.get('tone','respectful') not in ('respectful','warm','neutral') or data.get('length','short') not in ('short','medium'):raise ValueError('Неверный тон или длина')
    if not 1<=int(data.get('llm_budget_per_day',30))<=30 or not 1<=int(data.get('llm_max_per_cycle',2))<=2:raise ValueError('Лимит ИИ вне безопасных границ')
    if any(k not in '12345' or v not in ('auto','review') for k,v in data.get('rating_actions',{}).items()):raise ValueError('Неверное правило оценки')
    if any(data.get('exceptions',{}).get(k) is False for k in ('complaint','missing_fact','personal_size')):
        raise ValueError('Защитные исключения для жалоб, отсутствующих фактов и личного размера обязательны')
    data['backlog_cutoff']=time.time()
    version=(old['version']+1) if old else 1
    cx.execute('INSERT INTO v4_config VALUES(?,?,?,?,?) ON CONFLICT(scope_type,scope_id) DO UPDATE SET version=excluded.version,data=excluded.data,updated_at=excluded.updated_at',
               (scope_type,scope_id,version,json.dumps(data,ensure_ascii=False),time.time()))
    return version,data

def active_facts(cx):
    result=[]
    rows=cx.execute('SELECT * FROM v4_facts ORDER BY fact_key,version DESC').fetchall()
    seen=set()
    for row in rows:
        if row['fact_key'] in seen or row['status']=='draft':continue
        seen.add(row['fact_key'])
        if row['status']=='approved':result.append(json.loads(row['data']))
    return result

def facts_for_ui(cx,product=None):
    result=[]
    for row in cx.execute('SELECT * FROM v4_facts ORDER BY fact_key,version DESC'):
        item=json.loads(row['data']);item.update({'fact_key':row['fact_key'],'version':row['version'],'status':row['status']})
        if product and item['owner_id'] not in (product['product_id'],product['model_id']):continue
        result.append(item)
    return result

def next_fact_version(cx,key):
    row=cx.execute('SELECT MAX(version) FROM v4_facts WHERE fact_key=?',(key,)).fetchone()
    return (row[0] or 0)+1

def draft_fact(cx,item,base_profile):
    scope=item.get('scope');owner=item.get('owner_id');field=item.get('field')
    if scope not in ('model','variant') or field not in ('composition','wash','dry','iron','fit','measurement','feature','contents'):
        raise ValueError('Неверная область или поле')
    if scope=='model' and owner not in base_profile.models or scope=='variant' and owner not in base_profile.products:raise ValueError('Неизвестный товар')
    for key in ('value','source','excerpt'):
        if not isinstance(item.get(key),str) or not item[key].strip() or len(item[key])>700:raise ValueError('Укажите значение, источник и фрагмент основания')
    key=f'{scope}:{owner}:{field}';version=next_fact_version(cx,key)
    previous=next((x for x in active_facts(cx) if x['scope']==scope and x['owner_id']==owner and x['field']==field),None)
    status='conflict' if previous and previous['value']!=item['value'].strip() and item.get('method')=='ai_extract' else 'draft'
    data={'fact_id':item.get('fact_id') or f'V4-{scope}-{owner}-{field}-{version}',
          'scope':scope,'owner_id':owner,'field':field,'value':item['value'].strip(),
          'override_of':item.get('override_of') or None,'source':item['source'].strip(),
          'excerpt':item['excerpt'].strip(),'method':item.get('method','manual'),
          'author':item.get('author','Менеджер'),'status':status,'verified_at':None,'version':version}
    cx.execute('INSERT INTO v4_facts VALUES(?,?,?,?,?)',(key,version,json.dumps(data,ensure_ascii=False),status,time.time()))
    return key,version

def apply_fact(cx,key,version,base_profile):
    row=cx.execute('SELECT * FROM v4_facts WHERE fact_key=? AND version=?',(key,version)).fetchone()
    if not row or row['status'] not in ('draft','conflict'):raise ValueError('Черновик факта не найден или уже обработан')
    item=json.loads(row['data'])
    if item['scope']=='variant':
        inherited=next((x for x in active_facts(cx) if x['scope']=='model' and x['owner_id']==base_profile.products[item['owner_id']]['model_id'] and x['field']==item['field']),None)
        item['override_of']=inherited['fact_id'] if inherited else None
    item['status']='approved';item['verified_at']=time.strftime('%Y-%m-%d',time.gmtime())
    cx.execute("UPDATE v4_facts SET status='retired' WHERE fact_key=? AND status='approved'",(key,))
    cx.execute('UPDATE v4_facts SET data=?,status=?,updated_at=? WHERE fact_key=? AND version=?',
               (json.dumps(item,ensure_ascii=False),'approved',time.time(),key,version))
    return item

def rollback_fact(cx,key,version,base_profile):
    row=cx.execute('SELECT data FROM v4_facts WHERE fact_key=? AND version=?',(key,version)).fetchone()
    if not row:raise ValueError('Версия не найдена')
    item=json.loads(row['data']);item['method']='rollback';item['author']='Откат менеджера'
    new_version=next_fact_version(cx,key);item['version']=new_version;item['status']='draft'
    cx.execute('INSERT INTO v4_facts VALUES(?,?,?,?,?)',(key,new_version,json.dumps(item,ensure_ascii=False),'draft',time.time()))
    return apply_fact(cx,key,new_version,base_profile)

def scenarios(cx):
    return [{**json.loads(r['data']),'id':r['scenario_id'],'version':r['version'],'status':r['status']} for r in _latest(cx,'v4_scenarios','scenario_id')]

def active_scenarios(cx):
    rows=cx.execute("SELECT * FROM v4_scenarios WHERE status='active' ORDER BY scenario_id,version DESC").fetchall()
    seen=set();out=[]
    for row in rows:
        if row['scenario_id'] in seen:continue
        latest=cx.execute('SELECT status FROM v4_scenarios WHERE scenario_id=? ORDER BY version DESC LIMIT 1',(row['scenario_id'],)).fetchone()
        if latest['status']=='disabled':continue
        seen.add(row['scenario_id'])
        out.append({**json.loads(row['data']),'id':row['scenario_id'],'version':row['version'],'status':'active'})
    return out

def library_draft(cx,key,scope,owner,base_profile):
    from .complaint_library import LIBRARY
    if key not in LIBRARY or scope not in ('model','variant'):raise ValueError('Неизвестная заготовка или область')
    product=(base_profile.products.get(owner) if scope=='variant' else next((x for x in base_profile.products.values() if x['model_id']==owner),None))
    if not product:raise ValueError('Неизвестная модель или вариант')
    sid=f'{key}:{scope}:{owner}'
    latest=cx.execute('SELECT version,status FROM v4_scenarios WHERE scenario_id=? ORDER BY version DESC LIMIT 1',(sid,)).fetchone()
    if latest and latest['status']=='draft':return sid,latest['version']
    old=cx.execute('SELECT MAX(version) FROM v4_scenarios WHERE scenario_id=?',(sid,)).fetchone()[0] or 0
    facts=base_profile.facts(product['product_id'])
    active=cx.execute("SELECT data FROM v4_scenarios WHERE scenario_id=? AND status='active' ORDER BY version DESC LIMIT 1",(sid,)).fetchone()
    data=({**json.loads(active['data'])} if active else {**LIBRARY[key],'library_key':key,'scope':scope,'owner_id':owner,
          'label_wash':str(facts['wash']['value']) if 'wash' in facts and 'wash' in LIBRARY[key]['required_facts'] else '',
          'approval_source':'','approved_by':''})
    if active and key=='size_after' and 'Если товар ещё не куплен' in data.get('not_applicable',''):
        # Only the new draft receives the suggestion; the approved version stays intact.
        data['not_applicable']=LIBRARY[key]['not_applicable']
    data['approved_by']=''
    cx.execute('INSERT INTO v4_scenarios VALUES(?,?,?,?,?)',(sid,old+1,json.dumps(data,ensure_ascii=False),'draft',time.time()))
    return sid,old+1

def save_library_draft(cx,sid,version,data):
    from .complaint_library import LIBRARY
    old=cx.execute('SELECT * FROM v4_scenarios WHERE scenario_id=? ORDER BY version DESC LIMIT 1',(sid,)).fetchone()
    if not old or old['version']!=version or old['status']!='draft':raise ValueError('Черновик изменился; обновите экран')
    previous=json.loads(old['data']);allowed={'goal','explanation','action','prevention','not_applicable','prohibited','seller_path','approval_source','label_wash'}
    if set(data)!=allowed:raise ValueError('Неверные поля стратегии')
    for key,value in data.items():
        if not isinstance(value,str) or len(value)>700:raise ValueError('Поле стратегии длиннее 700 символов')
    previous.update({k:v.strip() for k,v in data.items()})
    if previous['library_key'] not in LIBRARY:raise ValueError('Неизвестная заготовка')
    cx.execute('UPDATE v4_scenarios SET data=?,updated_at=? WHERE scenario_id=? AND version=?',(json.dumps(previous,ensure_ascii=False),time.time(),sid,version))
    return previous

def approve_library_draft(cx,sid,version,base_profile):
    from .complaint_library import material_of,LIBRARY,plan,render,validate
    row=cx.execute('SELECT * FROM v4_scenarios WHERE scenario_id=? ORDER BY version DESC LIMIT 1',(sid,)).fetchone()
    if not row or row['version']!=version or row['status']!='draft':raise ValueError('Черновик изменился; обновите экран')
    data=json.loads(row['data']);product=(base_profile.products.get(data['owner_id']) if data['scope']=='variant' else next((x for x in base_profile.products.values() if x['model_id']==data['owner_id']),None))
    if not product or data['library_key'] not in LIBRARY:raise ValueError('Товар или заготовка не найдены')
    if data['material'] not in ('any','colored',material_of(base_profile,product)):raise ValueError('Состав товара не подходит')
    facts=base_profile.facts(product['product_id']);missing=[x for x in data['required_facts'].split(',') if x and x not in facts]
    if missing:raise ValueError('Не хватает фактов: '+', '.join(missing))
    if data['label_wash'] and data['label_wash']!=str(facts['wash']['value']):raise ValueError('Режим ухода противоречит профилю')
    if data['scope']=='model' and data['label_wash']:
        for variant in base_profile.products.values():
            if variant['model_id']==product['model_id'] and str(base_profile.facts(variant['product_id']).get('wash',{}).get('value',''))!=data['label_wash']:
                raise ValueError('У вариантов модели различается уход; выберите область «Вариант товара»')
    if not all(data[k].strip() for k in ('goal','explanation','action','not_applicable','approval_source')):
        raise ValueError('Заполните цель, объяснение, помощь, исключение и источник процедуры')
    if any('{' in data[k] or '}' in data[k] for k in ('explanation','action','prevention','not_applicable','seller_path')):
        raise ValueError('Заполните все переменные перед утверждением')
    sample={'shrink_wool':'После первой стирки село','shrink_cotton':'После стирки село','pilling':'Появились катышки',
            'dye_transfer':'Вещь линяет','seam_damage':'Разошёлся шов','size_after':'Размер не подошёл после покупки',
            'stretched':'Трикотаж вытянулся','wool_care':'Как ухаживать за шерстью?'}[data['library_key']]
    fake={'kind':'feedback','body':sample,'pros':'','cons':'','rating':2}
    planned,gap=plan(cx,fake,base_profile,product,{**data,'id':sid,'version':version})
    if not planned:raise ValueError(gap)
    validate(render(planned),planned,product,base_profile)
    data['approved_by']='Менеджер'
    cx.execute("UPDATE v4_scenarios SET data=?,status='active',updated_at=? WHERE scenario_id=? AND version=?",(json.dumps(data,ensure_ascii=False),time.time(),sid,version))
    return data

def save_scenario(cx,sid,body,expected_version):
    old=cx.execute('SELECT * FROM v4_scenarios WHERE scenario_id=? ORDER BY version DESC LIMIT 1',(sid,)).fetchone()
    if not old or old['version']!=expected_version:raise ValueError('Сценарий изменился; обновите экран')
    required={'name','enabled','when','goal','include','advice','restrictions','review_on_complaint'}
    if set(body)!=required or not isinstance(body['include'],list) or any(x not in ('composition','wash','dry','iron','fit','measurement','feature','contents','thanks') for x in body['include']):raise ValueError('Неверный сценарий')
    if any(len(str(body[k]))>700 for k in ('name','when','goal','advice','restrictions')):raise ValueError('Текст сценария слишком длинный')
    version=old['version']+1
    cx.execute('INSERT INTO v4_scenarios VALUES(?,?,?,?,?)',(sid,version,json.dumps(body,ensure_ascii=False),'active',time.time()))
    return version

def rollback_scenario(cx,sid,version):
    row=cx.execute('SELECT data FROM v4_scenarios WHERE scenario_id=? AND version=?',(sid,version)).fetchone()
    if not row:raise ValueError('Версия сценария не найдена')
    latest=cx.execute('SELECT MAX(version) FROM v4_scenarios WHERE scenario_id=?',(sid,)).fetchone()[0]
    return save_scenario(cx,sid,json.loads(row['data']),latest)

def advice(cx,model_id=None):
    rows=_latest(cx,'v4_seller_advice','model_id')
    return [{**json.loads(r['data']),'model_id':r['model_id'],'version':r['version'],'status':r['status']}
            for r in rows if not model_id or r['model_id']==model_id]

def save_advice(cx,model_id,body,expected_version,base_profile):
    if model_id not in base_profile.models:raise ValueError('Неизвестная модель')
    if set(body)!={'text','applies_to','source'} or not 2<=len(body['text'].strip())<=500 or not body['source'].strip():raise ValueError('Неверный совет продавца')
    from .complaint_library import LIBRARY
    valid=set(SCENARIOS)|set(LIBRARY)|{x['id'] for x in scenarios(cx) if x.get('library_key')}
    if not isinstance(body['applies_to'],list) or any(x not in valid for x in body['applies_to']):raise ValueError('Неверная область совета')
    old=cx.execute('SELECT MAX(version) FROM v4_seller_advice WHERE model_id=?',(model_id,)).fetchone()[0] or 0
    if old!=expected_version:raise ValueError('Совет изменился; обновите экран')
    version=old+1
    cx.execute('INSERT INTO v4_seller_advice VALUES(?,?,?,?,?)',(model_id,version,json.dumps(body,ensure_ascii=False),'active',time.time()))
    return version

def profile_overlay(cx,base):
    chosen=active_facts(cx)
    by_field={}
    for item in chosen:by_field[(item['scope'],item['owner_id'],item['field'])]=item
    scenes=active_scenarios(cx);active_campaigns=[x for x in campaigns(cx) if x['status']=='active'];seller_advice=advice(cx)
    seeded=all(x.get('method')=='seed_xlsx' for x in chosen) and len(chosen)==len([x for x in base.data['Факты'] if x['status']=='approved'])
    default_scenes=all(x['version']==1 and x['id'] in SCENARIOS for x in scenes)
    revision=(base.revision if seeded and default_scenes and not active_campaigns and not seller_advice else
              digest({'base':base.revision,'facts':chosen,'scenarios':scenes,
                      'seller_advice':seller_advice,'campaigns':active_campaigns,
                      'candidates':candidates(cx) if active_campaigns else []}))
    p=Profile(base.data,revision,base.models,base.products,by_field)
    return p

def campaigns(cx):
    return [{**json.loads(r['data']),'id':r['campaign_id'],'version':r['version'],'status':r['status']} for r in _latest(cx,'v4_campaigns','campaign_id')]

def candidates(cx):
    return [{**json.loads(r['data']),'id':r['candidate_id']} for r in cx.execute('SELECT * FROM v4_candidates ORDER BY candidate_id')]

def save_campaign(cx,cid,body,expected_version=None):
    old=cx.execute('SELECT * FROM v4_campaigns WHERE campaign_id=? ORDER BY version DESC LIMIT 1',(cid,)).fetchone()
    if old and old['version']!=expected_version:raise ValueError('Кампания изменена; обновите экран')
    if not old and expected_version not in (None,0):raise ValueError('Кампания не найдена')
    required={'name','status','starts_at','ends_at','timezone','source_product_ids','candidate_ids','trigger','mode','max_items','min_stock','max_age_minutes','promo_only','priority','article_view','jam_state','jam_source','exclude_complaints'}
    if set(body)!=required or body['status'] not in ('draft','active','paused','finished') or body['mode'] not in ('alternatives','kit') or body['article_view'] not in ('text','clickable_preview') or body['timezone']!='Europe/Moscow':
        raise ValueError('Неверная кампания')
    if body['jam_state'] not in ('unknown','available','unavailable') or not 1<=int(body['max_items'])<=2 or not 1<=int(body['min_stock'])<=20 or not 1<=int(body['max_age_minutes'])<=1440:
        raise ValueError('Неверные ограничения кампании')
    if float(body['starts_at'])>=float(body['ends_at']) or not body['source_product_ids'] or not body['candidate_ids']:
        raise ValueError('Проверьте период и товары кампании')
    if len(set(body['source_product_ids']))!=len(body['source_product_ids']) or len(set(body['candidate_ids']))!=len(body['candidate_ids']):
        raise ValueError('Повтор товара в кампании')
    known={x['candidate_id'] for x in cx.execute('SELECT candidate_id FROM v4_candidates')}
    if not set(body['candidate_ids']).issubset(known):raise ValueError('Неизвестный кандидат')
    version=(old['version']+1) if old else 1
    cx.execute('INSERT INTO v4_campaigns VALUES(?,?,?,?,?)',(cid,version,json.dumps(body,ensure_ascii=False),body['status'],time.time()))
    return version

def save_candidate(cx,cid,data):
    required={'name','nm_id','sandbox_nm_id','photo','category','tags','sizes','stock','promo_start','promo_end','snapshot_at','source','approved','priority','relations','kit_id'}
    if (set(data)!=required or not isinstance(data['nm_id'],int) or data['nm_id']<=0 or
        not isinstance(data['stock'],dict) or not isinstance(data['sizes'],list) or not data['sizes'] or
        not isinstance(data['tags'],list) or not isinstance(data['relations'],dict) or
        any(not isinstance(x,str) or not x.strip() for x in data['sizes']) or
        any(not isinstance(data['stock'].get(x),int) or data['stock'][x]<0 for x in data['sizes']) or
        not isinstance(data['name'],str) or not data['name'].strip() or not isinstance(data['source'],str) or not data['source'].strip() or
        float(data['promo_start'])>=float(data['promo_end'])):raise ValueError('Неверный кандидат')
    cx.execute('INSERT INTO v4_candidates VALUES(?,?,?) ON CONFLICT(candidate_id) DO UPDATE SET data=excluded.data,updated_at=excluded.updated_at',
               (cid,json.dumps(data,ensure_ascii=False),time.time()))

def seed_candidates(cx,rows):
    if cx.execute('SELECT 1 FROM v4_candidates LIMIT 1').fetchone():return
    now=time.time()
    for item in rows:
        data={k:v for k,v in item.items() if k!='id'}
        if not data['snapshot_at']:data['snapshot_at']=now
        if not data['promo_start']:data['promo_start']=now-86400
        if not data['promo_end']:data['promo_end']=now+3*86400
        save_candidate(cx,item['id'],data)
    body={'name':'Тёплый ноябрь · подборка трикотажа','status':'draft',
          'starts_at':now-3600,'ends_at':now+3*86400,'timezone':'Europe/Moscow',
          'source_product_ids':['DR-01','SU-01','SU-02','TS-01','SH-03','CD-05'],
          'candidate_ids':[x['id'] for x in rows],
          'trigger':'softness','mode':'alternatives','max_items':2,'min_stock':1,
          'max_age_minutes':15,'promo_only':True,'priority':10,'article_view':'text',
          'jam_state':'unknown','jam_source':'Не проверено в российском кабинете',
          'exclude_complaints':True}
    save_campaign(cx,'seasonal-warm',body)
