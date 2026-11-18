"""Local answer library; no WB cabinet template API is implied."""
import json,re,time

PARAMS={'composition','wash','dry','iron','fit','measurement','contents','feature','size_rule'}
TOKEN=re.compile(r'\{([a-z_]+)\}')
BUILTINS=[
 ('thanks','Благодарность','feedback','simple_positive','Спасибо за отзыв! Рады, что товар вам понравился.',[]),
 ('quality','Общая претензия','feedback','complaint','Жаль, что возникла такая ситуация с изделием. Если вопрос касается состояния товара после покупки, можно оформить обращение по заказу в Wildberries: Заказы → Возвраты. Решение принимается после рассмотрения.',[]),
 ('negative','Низкая оценка','feedback','negative','Спасибо, что поделились впечатлением. Нам жаль, что товар не оправдал ожиданий.',[]),
 ('size_mismatch','Размер не подошёл','feedback','complaint','Спасибо за отзыв. Нам жаль, что размер не подошёл. Для выбора другого размера сверьте замеры изделия с таблицей размеров.',[]),
 ('composition','Состав','both','direct_fact','По карточке товара: {composition}.',['composition']),
 ('wash','Уход и стирка','both','direct_fact','По карточке товара: {wash}.',['wash']),
 ('dry','Сушка','both','direct_fact','По карточке товара: {dry}.',['dry']),
 ('iron','Глажка','both','direct_fact','По карточке товара: {iron}.',['iron']),
 ('fit','Посадка','both','direct_fact','По карточке товара: {fit}.',['fit']),
 ('measurement','Замеры','both','direct_fact','По карточке товара: {measurement}.',['measurement']),
 ('contents','Комплектность','both','direct_fact','По карточке товара: {contents}.',['contents']),
 ('between_sizes','Выбор между размерами','question','size_advice','{size_rule}.',['size_rule']),
 ('size_clarify','Уточнить посадку','question','size_clarify','Чтобы подсказать по выбору между размерами, уточните, пожалуйста, предпочитаете ли свободную посадку, и сверьте свои замеры с таблицей изделия.',[]),
 ('missing_question','Не хватает сведений','question','unknown_fact','Уточните, пожалуйста, цвет и размер интересующего варианта. Тогда ответ о его свойствах будет точнее.',[]),
 ('missing_review','Уточнить запрос','feedback','unknown_question','Уточните, пожалуйста, какой именно характеристикой товара вы интересуетесь.',[]),
]

def ensure(cx):
    cx.execute('''CREATE TABLE IF NOT EXISTS answer_templates(
      id TEXT PRIMARY KEY,name TEXT NOT NULL,kind TEXT NOT NULL,intent TEXT NOT NULL,
      body TEXT NOT NULL,params TEXT NOT NULL,is_builtin INTEGER NOT NULL,updated_at REAL NOT NULL)''')
    for tid,name,kind,intent,body,params in BUILTINS:
        cx.execute('''INSERT INTO answer_templates VALUES(?,?,?,?,?,?,1,?)
           ON CONFLICT(id) DO UPDATE SET name=excluded.name,kind=excluded.kind,
           intent=excluded.intent,body=excluded.body,params=excluded.params,
           updated_at=excluded.updated_at WHERE answer_templates.is_builtin=1 AND
           (answer_templates.name!=excluded.name OR answer_templates.kind!=excluded.kind OR
            answer_templates.intent!=excluded.intent OR answer_templates.body!=excluded.body OR
            answer_templates.params!=excluded.params)''',
                   (tid,name,kind,intent,body,json.dumps(params),time.time()))

def validate_definition(name,kind,intent,body):
    if not 2<=len(name.strip())<=80 or not 2<=len(body.strip())<=700:raise ValueError('Имя или текст шаблона вне допустимой длины')
    if kind not in ('feedback','question','both'):raise ValueError('Неизвестный тип обращения')
    if not 1<=len(intent)<=40 or not re.fullmatch('[a-z_]+',intent):raise ValueError('Неизвестный сценарий')
    tokens=set(TOKEN.findall(body))
    if tokens-PARAMS or '{' in TOKEN.sub('',body) or '}' in TOKEN.sub('',body):raise ValueError('Неизвестный параметр шаблона')
    return sorted(tokens)

def list_templates(cx,kind=None):
    ensure(cx)
    where='WHERE kind IN (?,\'both\')' if kind else ''
    rows=cx.execute('SELECT * FROM answer_templates '+where+' ORDER BY is_builtin DESC,name',((kind,) if kind else ())).fetchall()
    return [{**dict(row),'params':json.loads(row['params'])} for row in rows]

def values_for(profile,product,kind,intent):
    facts=profile.question_facts(product['product_id']) if kind=='question' else profile.facts(product['product_id'])
    values={field:(str(f['value']).rstrip('.'),f['fact_id']) for field,f in facts.items()}
    if intent=='size_advice':
        rule=profile.advice(product['product_id'],'between_sizes')
        if rule:values['size_rule']=(str(rule['response']).rstrip('.'),None)
    return values

def render(template,profile,product,kind,intent):
    if not product or template['kind'] not in ('both',kind):raise ValueError('Шаблон не подходит к обращению')
    params=validate_definition(template['name'],template['kind'],template['intent'],template['body'])
    available=values_for(profile,product,kind,intent)
    missing=[p for p in params if p not in available]
    if missing:raise ValueError('Нет подтверждённого параметра: '+', '.join(missing))
    text=TOKEN.sub(lambda m:available[m.group(1)][0],template['body'])
    if TOKEN.search(text):raise ValueError('В ответе остался неподставленный параметр')
    return text,[available[p][1] for p in params if available[p][1]]

def default_template(cx,kind,decision,text=''):
    ensure(cx)
    key=None
    if decision.intent=='direct_fact' or (decision.intent=='negative' and decision.field):key=decision.field
    elif decision.intent=='size_advice':key='between_sizes'
    elif decision.intent in ('size_clarify','personal_size'):key='size_clarify' if kind=='question' else 'size_mismatch'
    elif decision.intent=='unknown_fact':key='missing_question' if kind=='question' else 'missing_review'
    elif decision.intent in ('unknown_question','multi_question','ambiguous','unproven_claim'):key='missing_question' if kind=='question' else 'missing_review'
    elif decision.intent=='simple_positive':key='thanks'
    elif decision.intent=='size_mismatch':key='size_mismatch'
    elif decision.intent in ('negative','complaint','photo_context'):
        key='size_mismatch' if re.search(r'размер|маломер|великоват|жм[её]т',text,re.I) else 'quality'
    if key:
        row=cx.execute('SELECT * FROM answer_templates WHERE id=?',(key,)).fetchone()
        if row:return dict(row)
    return None
