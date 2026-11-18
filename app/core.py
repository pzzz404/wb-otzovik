import hashlib, json, re, sqlite3, time
from pathlib import Path
from openpyxl import load_workbook

ROOT=Path(__file__).resolve().parents[1]
POLICY=ROOT/'response_policy.md'

def digest(value):
    return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True).encode()).hexdigest()

def read_catalog(path=ROOT/'data/catalog.xlsx'):
    book=load_workbook(path,read_only=True,data_only=True)
    expected={'Товары':['product_id','demo_nmID','sandbox_nmID','seller_article','name','variant','category','active'], 'Факты':['fact_id','product_id','field','value','source','verified_at'], 'Рекомендации':['recommendation_id','source_product_id','target_product_id','scenario','reason','forbidden_context','active','verified_at']}
    result={}
    for name,columns in expected.items():
        if name not in book: raise ValueError(f'Нет листа {name}')
        rows=list(book[name].values)
        if list(rows[0])!=columns:raise ValueError(f'Неверные столбцы: {name}')
        result[name]=[dict(zip(columns,row)) for row in rows[1:] if any(v is not None for v in row)]
    ps=result['Товары'];fs=result['Факты'];rs=result['Рекомендации']
    ids=set();nm={'demo':set(),'sandbox':set()}
    for p in ps:
        if not all(p.get(k) for k in ['product_id','seller_article','name','variant','category']) or p['product_id'] in ids:raise ValueError('Пустой или повторный товар')
        ids.add(p['product_id'])
        for mode in nm:
            value=p.get(mode+'_nmID')
            if value is not None:
                if not isinstance(value,int) or value<=0 or value in nm[mode]:raise ValueError(f'Некорректный или повторный nmID: {mode}')
                nm[mode].add(value)
    fids=set();field_values={}
    for f in fs:
        if not all(f.get(k) for k in ['fact_id','product_id','field','value','source','verified_at']) or f['product_id'] not in ids or f['fact_id'] in fids:raise ValueError('Некорректный факт')
        fids.add(f['fact_id']);key=(f['product_id'],f['field'])
        if key in field_values and field_values[key]!=f['value']:raise ValueError('Конфликт фактов')
        field_values[key]=f['value']
    rids=set()
    for r in rs:
        if not all(r.get(k) for k in ['recommendation_id','source_product_id','target_product_id','scenario','reason','forbidden_context','verified_at']) or r['recommendation_id'] in rids or r['source_product_id'] not in ids or r['target_product_id'] not in ids or r['source_product_id']==r['target_product_id']:raise ValueError('Некорректная рекомендация')
        rids.add(r['recommendation_id'])
    return result,digest({'catalog':result,'policy':POLICY.read_text()})

def db(path):
    path.parent.mkdir(parents=True,exist_ok=True)
    cx=sqlite3.connect(path,timeout=30,isolation_level=None)
    cx.row_factory=sqlite3.Row
    cx.execute('PRAGMA journal_mode=WAL')
    cx.execute('''CREATE TABLE IF NOT EXISTS reviews(id TEXT PRIMARY KEY, nm_id INTEGER, rating INTEGER, body TEXT, pros TEXT, cons TEXT, source_hash TEXT, status TEXT DEFAULT 'new', draft TEXT, fact_ids TEXT DEFAULT '[]', recommendation_id TEXT, generator TEXT, reason TEXT, revision TEXT, approved_hash TEXT, send_attempts INTEGER DEFAULT 0, sent_text TEXT, remote_answer TEXT, updated_at REAL, remote_size TEXT, remote_article TEXT)''')
    columns={x['name'] for x in cx.execute('PRAGMA table_info(reviews)')}
    for name in ('remote_size','remote_article'):
        if name not in columns:cx.execute('ALTER TABLE reviews ADD COLUMN '+name+' TEXT')
    cx.execute('''CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY AUTOINCREMENT, review_id TEXT, action TEXT, detail TEXT, at REAL)''')
    return cx

def event(cx,rid,action,detail=''):
    cx.execute('INSERT INTO events(review_id,action,detail,at) VALUES(?,?,?,?)',(rid,action,detail,time.time()))

def normalize_review(row):
    return {'id':str(row['id']),'nm_id':int(row['nm_id']),'rating':int(row['rating']),'body':str(row.get('body') or ''),'pros':str(row.get('pros') or ''),'cons':str(row.get('cons') or ''),'remote_size':row.get('remote_size'),'remote_article':row.get('remote_article')}

def upsert_review(cx,row):
    v=normalize_review(row); h=digest(v)
    old=cx.execute('SELECT source_hash FROM reviews WHERE id=?',(v['id'],)).fetchone()
    if old and old['source_hash']==h:return False
    cx.execute('''INSERT INTO reviews(id,nm_id,rating,body,pros,cons,source_hash,status,updated_at,remote_size,remote_article) VALUES(?,?,?,?,?,?,?,'new',?,?,?) ON CONFLICT(id) DO UPDATE SET nm_id=excluded.nm_id,rating=excluded.rating,body=excluded.body,pros=excluded.pros,cons=excluded.cons,source_hash=excluded.source_hash,status='new',draft=NULL,approved_hash=NULL,updated_at=excluded.updated_at,remote_size=excluded.remote_size,remote_article=excluded.remote_article''',(v['id'],v['nm_id'],v['rating'],v['body'],v['pros'],v['cons'],h,time.time(),v['remote_size'],v['remote_article']))
    event(cx,v['id'],'import','new or changed source')
    return True

def product_for(catalog,mode,nm_id):
    found=[p for p in catalog['Товары'] if p.get(mode+'_nmID')==nm_id and p['active']]
    return found[0] if len(found)==1 else None

RISK=re.compile(r'брак|порван|пятн|шов|сыпь|аллерг|вред|вернут|возврат|деньг|разочар|не тот|не приш|претенз|разош[её]л|маломер|оказался мал|села|лин[яь]|просвеч|фото|не под|ошибк|размер 48|жалоб|отмен',re.I)
UNKNOWN=re.compile(r'рост|вес|точно подойд|гарант|не линяет|не просвеч|усад|стирать по состав|стирку по состав|всем по росту',re.I)
INJECTION=re.compile(r'игнорируй|system:|вставь артикул|ссылку на скидку|пообещай|напиши, что',re.I)
FACT_HINTS=[('состав','состав'),('полуобхват груди','полуобхват груди'),('полуобхват талии','полуобхват талии'),('рукав','длина рукава'),('ярлык','уход'),('уход','уход'),('комплект','комплектность')]

def decide(catalog,mode,row):
    p=product_for(catalog,mode,row['nm_id'])
    if not p:return {'route':'manual','reason':'Нет точного товара и варианта в каталоге','product':None,'facts':[],'recommendations':[]}
    if mode=='sandbox':
        supplied=dict(row)
        size=str(p['variant']).split('/')[-1].strip()
        if supplied.get('remote_size')!=size or supplied.get('remote_article')!=p['seller_article']:
            return {'route':'manual','reason':'Вариант или артикул WB не совпадает с утверждённой картой','product':p,'facts':[],'recommendations':[]}
    body=' '.join(str(row[k] or '') for k in ['body','pros','cons'])
    if INJECTION.search(body):reason='Попытка изменить правила ответа';route='manual'
    elif RISK.search(body):reason='Жалоба или неразрешённая претензия';route='manual'
    elif UNKNOWN.search(body):reason='Нет подтверждённого факта или нельзя гарантировать результат';route='manual'
    else:reason='';route='review'
    own=[f for f in catalog['Факты'] if f['product_id']==p['product_id']]
    matched=[]
    for token,pattern in FACT_HINTS:
        if token in body.lower():
            matched=[f for f in own if pattern in str(f['value']).lower() or f['field']==pattern]
            break
    wants_companion=bool(re.search(r'комплект|подобрать|что ещё',body,re.I))
    if route=='review' and ('?' in body or matched) and not matched and not wants_companion:
        route='manual';reason='Вопрос без подтверждённого факта'
    recs=[]
    if route=='review' and wants_companion:
        recs=[r for r in catalog['Рекомендации'] if r['source_product_id']==p['product_id'] and r['active']]
    return {'route':route,'reason':reason,'product':p,'facts':matched,'recommendations':recs}

def validate_draft(payload,decision,catalog):
    if not isinstance(payload,dict):raise ValueError('Нет JSON объекта')
    text=payload.get('text');ids=payload.get('fact_ids');rec=payload.get('recommendation_id')
    if not isinstance(text,str) or not 2<=len(text)<=700 or not isinstance(ids,list) or len(ids)!=len(set(ids)):raise ValueError('Некорректный текст или fact_ids')
    allowed={f['fact_id'] for f in decision['facts']}
    if set(ids)!=allowed:raise ValueError('Не подтверждены нужные факты')
    allowed_recs={r['recommendation_id'] for r in decision['recommendations']}
    if rec is not None and rec not in allowed_recs:raise ValueError('Недопустимая рекомендация')
    if re.search(r'https?://|артикул|nmID|скидк|в наличии|гарантир|точно подойд|усад|не линяет|не просвеч|возврат одобрен|компенсац|бесплатн|доставим',text,re.I):raise ValueError('Запрещённое обещание, артикул или ссылка')
    nums=set(re.findall(r'\d+(?:[.,]\d+)?',text));ground=' '.join(str(f['value']) for f in decision['facts'])
    if not nums.issubset(set(re.findall(r'\d+(?:[.,]\d+)?',ground))):raise ValueError('Число без подтверждённого факта')
    if decision['facts']:
        text='Спасибо за вопрос. По утверждённой карточке: '+'; '.join(str(f['value']) for f in decision['facts'])+'.'
    else:
        text='Спасибо за отзыв! Рады, что изделие вам понравилось.'
    if rec:
        target=next(r['target_product_id'] for r in decision['recommendations'] if r['recommendation_id']==rec)
        prod=next(p for p in catalog['Товары'] if p['product_id']==target)
        text=text.rstrip()+' Если подбираете комплект, можно рассмотреть '+prod['name']+' (артикул '+prod['seller_article']+').'
    return text,ids,rec

def validate_operator_text(text,row,decision,catalog):
    if not 2<=len(text)<=700 or re.search(r'https?://|гарантир|точно подойд|усадк[аиу] не будет|не линяет|не просвеч|возврат одобрен|компенсац|скидк|в наличии',text,re.I):
        raise ValueError('Недопустимый текст, ссылка или обещание')
    facts={f['fact_id']:f for f in catalog['Факты'] if f['product_id']==decision['product']['product_id']}
    used=json.loads(row['fact_ids'] or '[]')
    if any(fid not in facts for fid in used):raise ValueError('Факт больше не принадлежит товару')
    allowed=' '.join(str(facts[fid]['value']) for fid in used)
    if used and any(str(facts[fid]['value']) not in text for fid in used):raise ValueError('Утверждённое значение факта изменено')
    rec=row['recommendation_id'];article=None
    if rec:
        link=next((r for r in decision['recommendations'] if r['recommendation_id']==rec),None)
        if not link:raise ValueError('Рекомендация больше не разрешена')
        article=next(p['seller_article'] for p in catalog['Товары'] if p['product_id']==link['target_product_id'])
    articles=re.findall(r'артикул\s+([\w-]+)',text,re.I)
    if articles and (not article or articles!=[article]):raise ValueError('Чужой артикул')
    if article:allowed+=' '+article
    numbers=set(re.findall(r'\d+(?:[.,]\d+)?',text))
    if not numbers.issubset(set(re.findall(r'\d+(?:[.,]\d+)?',allowed))):raise ValueError('Число без подтверждённого источника')
