"""Seller-adapted complaint strategies. Library entries are never publication authority."""
import json,re,time
from .classifier import review_text
from .v2_logic import validate_reply

CONTRACT_VERSION='complaint-library-9'
WOOL_WASH='https://www.woolmark.com/care/how-to-wash-wool/'
WOOL_DRY='https://www.woolmark.com/care/how-to-dry-wool-sweater/'
WOOL_PILL='https://www.woolmark.com/care/pilling/'
FJALL='https://support-na.fjallraven.com/hc/en-us/articles/360016828539-How-do-I-care-for-my-wool-garments'
COTTON='https://www.cottoninc.com/wp-content/uploads/2017/12/FQR-1004-Maintaining-the-Quality-of-Cotton-Sweaters.pdf'
DYE='https://www.clorox.com/learn/how-to-remove-color-bleeding-from-clothes/'
WB_RETURN='https://seller.wildberries.ru/instructions/ru/ru/material/item-returns-by-customer-request'

def entry(name,material,goal,explanation,action,prevention,not_applicable,required_facts,source_url,source_summary,**more):
    return dict(name=name,material=material,goal=goal,explanation=explanation,action=action,
                prevention=prevention,not_applicable=not_applicable,required_facts=required_facts,
                source_url=source_url,source_summary=source_summary,source_checked='2026-11-17',
                prohibited='Не признавать непроверенный дефект, не обвинять покупателя, не обещать восстановление или возврат.',
                seller_path='Если способ не подойдёт или результат не устроит, оформите заявку в Wildberries: Заказы → Возвраты. Решение принимается по заявке.',
                **more)

LIBRARY={
 'wool_care':entry('Бережный уход за шерстяным трикотажем','wool','Предотвратить изменение формы',
    'Шерстяной трикотаж требует бережного ухода согласно ярлыку.',
    'При следующей стирке следуйте указанию по уходу для этой модели.',
    'После стирки осторожно придайте форму и сушите вещь горизонтально.',
    'Если ярлык запрещает смачивание или указывает иной режим, следуйте ярлыку.','wash,dry',WOOL_WASH,
    'Woolmark различает ручную и машинную стирку по ярлыку; для ручной указывает около 30 °C и мягкое средство.'),
 'shrink_wool':entry('Усадка шерстяного трикотажа','wool','Помочь с уже изменившей форму вещью',
    'Для шерстяного трикотажа важен бережный уход после стирки.',
    'Если ярлык допускает ручное смачивание и вещь не свалялась, осторожно увлажните её водой до 30 °C, разложите на полотенце и мягко придайте форму по замерам. Не растягивайте с усилием; результат может быть частичным.',
    'Дальше стирайте вручную до 30 °C без отжима и сушите горизонтально в расправленном виде.',
    'Если вещь свалялась или ярлык запрещает смачивание, не пробуйте этот способ.','composition,wash,dry',FJALL,
    'Fjällräven описывает бережное смачивание, раскладку на полотенце и мягкое придание формы; это не гарантия для иной модели.',
    supporting_url=WOOL_DRY),
 'shrink_cotton':entry('Усадка хлопкового изделия','cotton','Помочь разобраться с изменением размера',
    'Размер хлопкового трикотажа после стирки может меняться; причина зависит также от конструкции и обработки изделия.',
    'Сверьте фактические замеры с таблицей модели. Если ярлык допускает повторное смачивание, разложите влажную вещь по форме без сильного растяжения; результат не гарантирован.',
    'Для следующей стирки соблюдайте режим ярлыка этой модели.',
    'Если ярлык запрещает смачивание, пропустите этот шаг и обратитесь по заказу.','composition,wash,measurement',COTTON,
    'Cotton Incorporated отмечает роль конструкции, обработки, ухода и различия замеров; универсального восстановления не обещает.'),
 'pilling':entry('Катышки на трикотаже','wool','Объяснить трение и безопасное действие',
    'Катышки могут появляться в местах трения, например под ремнём или сумкой.',
    'Если ярлык не запрещает, осторожно снимите отдельные катышки руками или подходящей машинкой для трикотажа, сначала проверив на незаметном участке.',
    'Старайтесь уменьшать постоянное трение и выворачивайте изделие перед стиркой, если ярлык это допускает.',
    'Не используйте машинку на рыхлой вязке, декоре или повреждённых нитях без проверки.','composition,wash',WOOL_PILL,
    'Woolmark связывает катышки с трением и описывает ручное удаление, гребень или машинку; не обещает отсутствие повторения.'),
 'dye_transfer':entry('Линька и перенос красителя','colored','Не усугубить перенос цвета',
    'На перенос цвета могут влиять режим стирки и соседние вещи.',
    'Пока не стирайте вещь вместе со светлыми изделиями. Сохраните ярлык и зафиксируйте изменение цвета для обращения по заказу.',
    'В дальнейшем разделяйте светлые и окрашенные вещи и следуйте ярлыку.',
    'Не применяйте отбеливатель и другие средства без проверки ткани и окраски.','wash',DYE,
    'Clorox рекомендует разделять новые и светлые вещи; отбеливание допустимо лишь после проверки совместимости и запрещено для шерсти.'),
 'seam_damage':entry('Разошедшийся шов или повреждение','any','Дать путь решения без вывода о причине',
    'Шов разошёлся; сохраним возможность рассмотреть ситуацию по заявке.',
    'Сохраните изделие и фото шва; оформите заявку по заказу в Wildberries: Заказы → Возвраты. Решение принимается после рассмотрения заявки.',
    'До рассмотрения лучше не нагружать повреждённый участок.',
    'До рассмотрения заявки не ремонтируйте шов самостоятельно.','',WB_RETURN,
    'Официальная инструкция WB описывает заявку покупателя из Заказы → Возвраты и последующее решение продавца.'),
 'size_after':entry('Размер после покупки не подошёл','any','Помочь сверить размер после покупки',
    'Посадка зависит от замеров изделия и предпочтения по свободе облегания.',
    'Сверьте замеры полученной вещи с таблицей модели. Если покупка уже совершена и посадка не подходит, воспользуйтесь заявкой по заказу в Wildberries: Заказы → Возвраты.',
    'Для следующего выбора ориентируйтесь на замеры конкретного варианта.',
    'Если замеры полученной вещи расходятся с таблицей модели, укажите это в заявке по заказу.','fit,measurement',WB_RETURN,
    'Официальная инструкция WB описывает путь заявки после покупки; размерный совет требует замеров модели.'),
 'stretched':entry('Вытягивание и потеря формы','wool','Помочь восстановить форму без обещания',
    'Влажный трикотаж может вытягиваться при сушке на весу.',
    'Если ярлык допускает ручное смачивание, слегка увлажните вытянувшийся участок и мягко придайте форму на горизонтальной поверхности; не тяните с усилием.',
    'После стирки сушите горизонтально, вдали от прямого нагрева.',
    'Если есть декор, сваливание или запрет смачивания на ярлыке, не пробуйте этот способ.','wash,dry',WOOL_DRY,
    'Woolmark рекомендует плоскую сушку и описывает мягкое изменение формы влажного шерстяного участка.'),
}

EVENTS=(('shrink_wool',r'сел[ао]?\b|усад|уменьшил.{0,12}размер'),
        ('shrink_cotton',r'сел[ао]?\b|усад|уменьшил.{0,12}размер'),
        ('pilling',r'катыш|скатал'),('dye_transfer',r'линя|линьк|покрасил|окрасил|краск.{0,10}перешл'),
        ('seam_damage',r'шов|разош[её]л|порвал|дыр'),('size_after',r'размер.{0,20}не подош|не подош.{0,20}размер|маломер|великоват|жм[её]т'),
        ('stretched',r'растянул|вытянул|потерял.{0,10}форм|деформир'),
        ('wool_care',r'как стирать|как ухаживать|уход'))
COMPLIANT=re.compile(r'по инструкц|по ярлык|вручную.{0,35}30|30.{0,35}без отжима|без отжима.{0,35}30',re.I)
WRONG=re.compile(r'\b60\b|с полотенц|в машинк|отжимала|сильн.{0,10}отжим',re.I)
TOGETHER=re.compile(r'\b(?:с|вместе\s+с)\s+(?:полотенц\w*|другими\s+вещами)\b',re.I)
HOT_WASH=re.compile(r'\b(?:при\s+)?60\s*(?:°\s*[cс]|градус\w*)\b|\bстир\w*[^.;]{0,30}\bпри\s+60\b',re.I)
OTHER_WASH=re.compile(r'\bв\s+машинк\w*|\bотжимал\w*|\bсильн\w*\s+отжим\w*',re.I)

def wash_evidence(text):
    """Keep reported conditions separate; a negated report is not an event."""
    together=bool(TOGETHER.search(text)) and not bool(re.search(
        r'\bне\s+стирал\w*[^.;]{0,35}\b(?:с|вместе\s+с)\s+(?:полотенц\w*|другими\s+вещами)',text,re.I))
    hot=bool(HOT_WASH.search(text)) and not bool(re.search(r'\bне\s+стирал\w*[^.;]{0,25}\bпри\s+60',text,re.I))
    other=bool(OTHER_WASH.search(text)) and not bool(re.search(r'\bне\s+(?:стирал\w*\s+в\s+машинк\w*|отжимал\w*)',text,re.I))
    compliant=bool(COMPLIANT.search(text)) and not bool(re.search(r'\bне\s+(?:по\s+инструкц\w*|по\s+ярлык\w*|вручную)',text,re.I))
    wrong=hot or together or other
    return {'compliant':compliant,'wrong':wrong,'hot':hot,'together':together,'other':other}
UNSAFE=re.compile(r'проблем[а-я ]{0,15}качеств|товар.{0,12}брак|вы неправильн|вы нарушил|восстанов.{0,15}полностью|точно восстанов|(?<!не )гарантир|это ваша вина|верн[её]м деньги',re.I)
INTERNAL=re.compile(r'по одному отзыву нельзя установить причину|полного восстановления обещать нельзя|это не доказывает причину каждого случая',re.I)
FREQUENCY=re.compile(r'\b(?:часто|чаще|нередко|регулярно|обычно|всегда|никогда|постоянно|неизбежно|иногда|как правило|в большинстве случаев|у большинства|большинство|скорее всего|несомненно|безусловно)\b',re.I)

def check_supported_frequency(source,candidate,plan=None):
    """A buyer report or a composition percentage cannot establish product-wide frequency."""
    approved={match.group().casefold() for match in FREQUENCY.finditer(source)}
    for match in FREQUENCY.finditer(candidate):
        if match.group().casefold() not in approved:
            raise ValueError('Ответ добавил неподтверждённую частоту или категоричность: '+match.group())
    for match in re.finditer(r'\b\d{1,3}\s*%',candidate):
        claim=match.group().replace(' ','')
        if claim in source.replace(' ',''):continue
        context=candidate[max(0,match.start()-25):match.end()+30].casefold()
        if re.search(r'случа|вероят|покупател|отзыв|заказ',context):
            raise ValueError('Ответ добавил неподтверждённый процент: '+claim)
        composition=any(claim in str(fact['value']).replace(' ','') for fact in (plan or {}).get('facts',[]) if fact['field']=='composition')
        if not composition or not re.search(r'шерст|хлоп|полиамид|полиэстер|состав',context):
            raise ValueError('Ответ добавил неподтверждённый процент: '+claim)

def check_general_wool_claim(plan,candidate):
    """The customer's shrinkage is not evidence that wool garments generally shrink."""
    if plan['key']!='shrink_wool' or re.search(r'измен|меня|усад|садит',plan['base_explanation'],re.I):return
    general=re.search(r'\bшерстян\w*\s+(?:трикотаж|издели\w*|вещ\w*).{0,55}?\b(?:меня\w*\s+форм\w*|изменя\w*\s+форм\w*|может\s+изменить\s+форм\w*|садится|усаживается)',candidate,re.I)
    model_claim=re.search(r'\b(?:эт\w*|данн\w*|шерстян\w*)\s+(?:шерстян\w*\s+)?(?:модел\w*|плать\w*|издели\w*).{0,65}?\b(?:возможно|может|способн\w*|обычно|действительно).{0,45}?\b(?:измен\w*\s+форм\w*|меня\w*\s+форм\w*|сесть|садит\w*|усадк\w*)',candidate,re.I)
    if general or model_claim:raise ValueError('Ответ добавил неподтверждённое общее утверждение или свойство модели об изменении формы')

def material_of(profile,product):
    fact=profile.facts(product['product_id']).get('composition')
    value=str(fact['value']).lower() if fact else ''
    return 'wool' if 'шерст' in value else 'cotton' if 'хлоп' in value else 'other'

def choose(row,profile,product):
    if row['kind']!='feedback' or not product:return None
    text=review_text(row);material=material_of(profile,product)
    for key,pattern in EVENTS:
        if re.search(pattern,text,re.I):
            if key=='shrink_wool' and material!='wool':continue
            if key=='shrink_cotton' and material!='cotton':continue
            if key=='wool_care' and material!='wool':continue
            return key
    return None

def issues(row,profile,product):
    if row['kind']!='feedback' or not product:return []
    found=[];text=review_text(row);material=material_of(profile,product)
    for key,pattern in EVENTS:
        if key=='wool_care' or key=='shrink_wool' and material!='wool' or key=='shrink_cotton' and material!='cotton':continue
        if re.search(pattern,text,re.I):found.append(key)
    return found

def active_for(cx,key,product):
    for scope,owner in (('variant',product['product_id']),('model',product['model_id'])):
        sid=f'{key}:{scope}:{owner}'
        latest=cx.execute("SELECT status FROM v4_scenarios WHERE scenario_id=? ORDER BY version DESC LIMIT 1",(sid,)).fetchone()
        if latest and latest['status']=='disabled':return None
        row=cx.execute("SELECT version,data FROM v4_scenarios WHERE scenario_id=? AND status='active' ORDER BY version DESC LIMIT 1",(sid,)).fetchone()
        if row:return {**json.loads(row['data']),'id':sid,'version':row['version']}
    return None

def plan(cx,row,profile,product,scenario_override=None):
    key=choose(row,profile,product)
    if not key:return None,'Нет предметного сценария'
    distinct=issues(row,profile,product)
    if len(distinct)>1:return None,'Смешанная претензия ('+', '.join(LIBRARY[x]['name'] for x in distinct)+'); нужен отдельный утверждённый план ответа'
    scenario=scenario_override if scenario_override and scenario_override.get('library_key')==key else active_for(cx,key,product)
    if not scenario:return None,'Нет утверждённой процедуры для '+LIBRARY[key]['name']+'; откройте библиотеку сценариев'
    facts=profile.facts(product['product_id']);required=[x for x in scenario['required_facts'].split(',') if x]
    missing=[x for x in required if x not in facts]
    if missing:return None,'Нет утверждённых фактов для процедуры: '+', '.join(missing)
    if scenario['material'] not in ('any','colored',material_of(profile,product)):
        return None,'Состав товара не подходит к утверждённой процедуре'
    if scenario['material']=='colored' and str(product.get('color','')).lower() in ('белый','молочный'):
        return None,'Процедура для окрашенного варианта не подходит'
    if scenario.get('label_wash') and scenario['label_wash']!=str(facts['wash']['value']):
        return None,'Уход в сценарии противоречит текущему профилю или ярлыку'
    from .v4_store import advice
    seller=next((x for x in advice(cx,product['model_id']) if x['status']=='active' and
                 (key in x['applies_to'] or scenario['id'] in x['applies_to'])),None)
    text=review_text(row);evidence=wash_evidence(text);compliant=evidence['compliant'];wrong=evidence['wrong']
    branch='unknown' if compliant and wrong else 'compliant' if compliant else 'wrong' if wrong else 'unknown'
    explanation=scenario['explanation']
    if key=='shrink_wool':
        if branch=='compliant':
            explanation='Вы пишете, что соблюдали рекомендации по уходу, но шерстяное изделие всё же изменило форму.'
        elif branch=='wrong':
            explanation=('Вы описали стирку с другими вещами при температуре выше указанной; такой режим может влиять на форму шерстяного трикотажа.'
                         if evidence['hot'] and evidence['together'] else
                         'Вы описали стирку при температуре выше указанной; такой режим может влиять на форму шерстяного трикотажа.'
                         if evidence['hot'] else
                         'Вы описали стирку с другими вещами; такой режим может влиять на форму шерстяного трикотажа.'
                         if evidence['together'] else 'Описанный режим стирки может влиять на форму шерстяного трикотажа.')
        if branch!='unknown' and scenario['explanation']!=LIBRARY['shrink_wool']['explanation']:
            explanation+=' '+scenario['explanation']
    used=[facts[x] for x in required]
    action=scenario['action']+(' '+seller['text'] if seller else '')
    path=scenario['seller_path'].strip() if scenario.get('seller_path') and 'Заказы → Возвраты' not in (action+' '+scenario['not_applicable']) else ''
    return {'key':key,'scenario_id':scenario['id'],'version':scenario['version'],'goal':scenario['goal'],
            'base_explanation':scenario['explanation'],'explanation':explanation,'action':action,'prevention':scenario['prevention'],
            'not_applicable':scenario['not_applicable'],'prohibited':scenario['prohibited'],
            'seller_path':path,'source_url':scenario['source_url'],
            'source_summary':scenario['source_summary'],'branch':branch,'seller_advice_version':seller['version'] if seller else None,
            'fact_ids':[x['fact_id'] for x in used],'facts':[{'fact_id':x['fact_id'],'field':x['field'],'value':x['value'],'source':x['source']} for x in used]},None

def render(plan,lead=None):
    if lead is None:
        goal=plan['goal'].lower()
        lead=('Понимаем, что важно разобраться в ситуации.' if 'разобра' in goal else
              'Спасибо, что обратились за советом по уходу.' if 'предотврат' in goal else
              'Жаль, что вещь доставила неудобство.')
    lead=lead.strip()
    blocks=(lead,plan['explanation'],plan['action'],plan['prevention'],plan['not_applicable'],plan['seller_path'])
    return ' '.join(x for x in blocks if x).strip()

AI_FIELDS=('opening','explanation','action_intro','prevention','path_intro')
def fit_ai_parts(plan,parts):
    """Shorten a verbose model proposal locally; never shorten the seller's locked blocks."""
    if not isinstance(parts,dict) or set(parts)!=set(AI_FIELDS) or any(not isinstance(parts[k],str) for k in AI_FIELDS):
        raise ValueError('ИИ вернул неполную структуру ответа')
    out={k:parts[k].strip() for k in AI_FIELDS}
    for key in AI_FIELDS:
        check_supported_frequency(plan[key] if key in ('explanation','prevention') else '',out[key],plan)
    check_general_wool_claim(plan,out['explanation'])
    def size():
        return len(' '.join(x for x in (out['opening'],out['explanation'],out['action_intro'],
                   plan['action'],out['prevention'],plan['not_applicable'],out['path_intro'],plan['seller_path']) if x))
    def best_sentence(key,target=170):
        source=plan[key];roots=_content_roots(source)
        candidates=[]
        for sentence in re.split(r'(?<=[.!?])\s+',out[key]):
            sentence=sentence.strip()
            if 15<=len(sentence)<=target:candidates.append(sentence)
            clauses=[x.strip() for x in re.split(r'[,;]\s*',sentence) if x.strip()]
            for start in range(len(clauses)):
                for end in range(start+1,min(len(clauses),start+3)+1):
                    selected=clauses[start:end]
                    if len(selected)==len(clauses):continue
                    choice=', '.join(selected).rstrip(' .')+'.'
                    if 15<=len(choice)<=target:candidates.append(choice)
            if len(clauses)>=3:
                choice=clauses[0].rstrip(' .')+', '+clauses[-1].rstrip(' .')+'.'
                if 15<=len(choice)<=target:candidates.append(choice)
        ranked=sorted(set(candidates),key=lambda x:(len(roots&_content_roots(x)),len(x)),reverse=True)
        for candidate in ranked:
            try:_check_care_invariants(source,candidate)
            except ValueError:continue
            if len(roots&_content_roots(candidate))>=min(2,len(roots)) and _branch_context_ok(plan,candidate,key):
                if key=='explanation' and plan['key']=='shrink_wool' and not _explanation_subject_ok(plan,candidate):
                    enriched=candidate+' '+plan['base_explanation']
                    if len(enriched)<=target:return enriched
                    continue
                return candidate
        return source
    for key in AI_FIELDS:
        if len(out[key])>170:
            if key in ('explanation','prevention'):out[key]=best_sentence(key)
            else:
                first=re.split(r'(?<=[.!?])\s+',out[key],maxsplit=1)[0]
                out[key]=first if len(first)<=170 else ('Жаль, что вещь доставила неудобство.' if key=='opening' else '')
    for key in ('explanation','prevention'):
        roots=_content_roots(plan[key])
        if roots and len(roots&_content_roots(out[key]))<min(2,len(roots)):
            out[key]=plan[key]
        elif any(number not in out[key] for number in re.findall(r'\d+(?:[.,]\d+)?',plan[key])):
            out[key]=plan[key]
        else:
            try:_check_care_invariants(plan[key],out[key])
            except ValueError:out[key]=plan[key]
    if size()>700:
        out['action_intro']='';out['path_intro']=''
    if size()>700 and len(out['opening'])>55:
        out['opening']='Жаль, что вещь доставила неудобство.'
    if size()>700:
        remaining=700-(size()-len(out['explanation']))
        out['explanation']=best_sentence('explanation',remaining)
    if size()>700:out['prevention']=plan['prevention']
    if size()>700:out['explanation']=plan['explanation']
    if size()>700:out['opening']='Жаль, что вещь доставила неудобство.'
    if size()>700:raise ValueError('Даже сокращённый ответ длиннее 700 символов')
    if out['explanation']==plan['explanation'] and out['prevention']==plan['prevention']:
        raise ValueError('ИИ не сохранил содержательную адаптацию; доступен утверждённый шаблон')
    return out

def _content_roots(value):
    return {word[:4] for word in re.findall(r'[а-яё]{5,}',value.lower())
            if word not in {'который','которая','которые','потому','может','можете','после','этого','вещь','изделие','сейчас'}}

def _check_substantive_adaptation(plan,parts,review):
    """Require a buyer detail in the changed explanation, not cosmetic edits."""
    changed=_content_roots(parts['explanation'])-_content_roots(plan['explanation'])
    buyer=_content_roots(review)
    if not changed & buyer:
        raise ValueError('ИИ не учёл конкретную деталь обращения в объяснении; доступен утверждённый шаблон')

def _branch_context_ok(plan,value,key='explanation'):
    if key!='explanation' or plan['key']!='shrink_wool':return True
    if plan['branch']=='compliant':
        return bool(re.search(r'соблюд|вручн|по инструкции|по ярлыку|без отжима|при 30',value,re.I))
    if plan['branch']=='wrong':
        return bool(re.search(r'температур|режим|полотенц',value,re.I))
    return True

def _explanation_subject_ok(plan,value):
    if plan['key']!='shrink_wool':return True
    changed=bool(re.search(r'форм|усад|сел[ао]?\b|измен',value,re.I))
    material=bool(re.search(r'шерст|трикотаж',value,re.I))
    care=bool(re.search(r'бережн.{0,15}уход|уход.{0,15}бережн',value,re.I))
    if plan['branch']=='compliant':return changed or care and material
    return material and (changed or care)

def care_invariants(source):
    source_lower=source.casefold()
    protected=[]
    protected.extend(re.findall(r'\bдо\s+\d+\s*°\s*c\b',source_lower))
    protected.extend(re.findall(r'\bбез\s+отжима\b',source_lower))
    protected.extend(re.findall(r'\b(?:вручную|ручную стирку)\b',source_lower))
    protected.extend(re.findall(r'\b(?:сушите|сушить)\s+горизонтально\b',source_lower))
    protected.extend(re.findall(r'\bв расправленном виде\b',source_lower))
    protected.extend(re.findall(r'\bесли\s+ярлык[^,.;]{0,70}',source_lower))
    return list(dict.fromkeys(protected))

def _check_care_invariants(source,candidate):
    """Keep measurable care limits and direction, even when wording around them changes."""
    source_lower=source.casefold();candidate_lower=candidate.casefold()
    for phrase in care_invariants(source):
        if phrase not in candidate_lower:raise ValueError('ИИ изменил условие ухода: '+phrase)
    if 'без отжима' in source_lower and re.search(r'\b(?:с\s+отжимом|(?<!не )отжимайте|(?<!не )отожмите)\b',candidate_lower):
        raise ValueError('ИИ изменил запрет на отжим')
    if 'без отжима' in source_lower and re.search(r'\bне\s+(?:нужно\s+)?(?:стирать|стирайте)[^.;]{0,35}\bбез отжима',candidate_lower):
        raise ValueError('ИИ инвертировал запрет на отжим')
    if 'горизонтально' in source_lower and any(not re.search(r'\b(?:не|нельзя|избегайте)\b',candidate_lower[max(0,m.start()-25):m.start()])
        for m in re.finditer(r'\b(?:вертикально|на\s+весу|на\s+вешалке)\b',candidate_lower)):
        raise ValueError('ИИ изменил способ сушки')
    if 'горизонтально' in source_lower and re.search(r'\bне\s+(?:нужно\s+)?суши(?:ть|те)\s+горизонтально',candidate_lower):
        raise ValueError('ИИ инвертировал способ сушки')
    if 'вручную' in source_lower and re.search(r'\bне\s+(?:нужно\s+)?(?:стирай(?:те|ть)\s+)?вручную',candidate_lower):
        raise ValueError('ИИ инвертировал ручную стирку')
    if re.search(r'\bдо\s+\d+\s*°\s*c',source_lower) and re.search(r'\b(?:от\s+\d+\s*°\s*c|выше\s+\d+\s*°\s*c|\d+\s*°\s*f|фаренгейт)',candidate_lower):
        raise ValueError('ИИ изменил предел или единицу температуры')

def render_ai(plan,parts,review):
    if not isinstance(parts,dict) or set(parts)!=set(AI_FIELDS):raise ValueError('ИИ вернул неполную структуру ответа')
    for key in AI_FIELDS:
        value=parts[key]
        if not isinstance(value,str) or len(value)>170 or '?' in value or ('{' in value or '}' in value):
            raise ValueError('ИИ изменил структуру или добавил вопрос: '+key)
        if key in ('opening','explanation','prevention') and len(value.strip())<15:
            raise ValueError('ИИ не адаптировал содержание: '+key)
        if UNSAFE.search(value):raise ValueError('ИИ добавил недопустимое утверждение: '+key)
        check_supported_frequency(plan[key] if key in ('explanation','prevention') else '',value,plan)
        if key=='explanation':check_general_wool_claim(plan,value)
        for number in re.findall(r'\d+(?:[.,]\d+)?',value):
            if number not in review and number not in ' '.join(str(plan[k]) for k in ('explanation','action','prevention','not_applicable','seller_path')):
                raise ValueError('ИИ добавил неподтверждённое число: '+number)
    if not plan['seller_path'] and parts['path_intro'].strip():raise ValueError('ИИ предложил ненастроенный путь решения')
    for key in ('explanation','prevention'):
        approved=_content_roots(plan[key]);proposed=_content_roots(parts[key])
        if approved and len(approved&proposed)<min(2,len(approved)):
            raise ValueError('ИИ не сохранил смысл утверждённого поля: '+key)
        if not set(re.findall(r'\d+(?:[.,]\d+)?',plan[key])).issubset(set(re.findall(r'\d+(?:[.,]\d+)?',parts[key]))):
            raise ValueError('ИИ потерял числовое условие: '+key)
        _check_care_invariants(plan[key],parts[key])
    if not _branch_context_ok(plan,parts['explanation']):raise ValueError('ИИ не учёл сообщённый режим ухода')
    if not _explanation_subject_ok(plan,parts['explanation']):raise ValueError('ИИ не объяснил изменение формы шерстяного изделия')
    text=' '.join(x.strip() for x in (parts['opening'],parts['explanation'],
                  parts['action_intro'],plan['action'],parts['prevention'],
                  plan['not_applicable'],parts['path_intro'],plan['seller_path']) if x and x.strip())
    return text

def validate_ai(text,plan,parts,review,product,profile):
    if text!=render_ai(plan,parts,review):raise ValueError('ИИ изменил утверждённые блоки')
    _check_substantive_adaptation(plan,parts,review)
    if UNSAFE.search(text) or INTERNAL.search(text):raise ValueError('Ответ содержит обвинение, внутреннее правило или обещание')
    for phrase in re.findall(r'«([^»]+)»',plan['prohibited']):
        if phrase.casefold() in text.casefold():raise ValueError('Ответ содержит запрещённую формулировку: '+phrase)
    if plan['branch']=='compliant' and re.search(r'нарушил|неправильн.{0,12}стир',text,re.I):raise ValueError('Обвинение при соблюдённом уходе')
    return validate_adapted(text,plan,product,profile,buyer_text=review)

def validate_adapted(text,plan,product,profile,buyer_text=''):
    """Check the seller-approved meaning at the send boundary, including manual edits."""
    if not plan or not plan['action']:raise ValueError('Нет утверждённой помощи покупателю')
    if UNSAFE.search(text) or INTERNAL.search(text):raise ValueError('Ответ содержит обвинение, внутреннее правило или обещание')
    for phrase in re.findall(r'«([^»]+)»',plan['prohibited']):
        if phrase.casefold() in text.casefold():raise ValueError('Ответ содержит запрещённую формулировку: '+phrase)
    action_at=text.find(plan['action'])
    exclusion_at=text.find(plan['not_applicable'],action_at+len(plan['action']))
    if action_at<0 or exclusion_at<0:raise ValueError('Утверждённая помощь или ограничение отсутствует')
    explanation=text[:action_at]
    prevention=text[action_at+len(plan['action']):exclusion_at]
    check_supported_frequency(plan['explanation'],explanation,plan)
    check_general_wool_claim(plan,explanation)
    check_supported_frequency(plan['prevention'],prevention,plan)
    if not _branch_context_ok(plan,explanation):raise ValueError('Ответ не учитывает сообщённый режим ухода')
    if not _explanation_subject_ok(plan,explanation):raise ValueError('Ответ не объясняет изменение формы шерстяного изделия')
    for label,source,segment in (('объяснение',plan['explanation'],explanation),('профилактика',plan['prevention'],prevention)):
        roots=_content_roots(source)
        if roots and len(roots&_content_roots(segment))<min(2,len(roots)):
            raise ValueError('В ответе потеряно утверждённое '+label)
        if not set(re.findall(r'\d+(?:[.,]\d+)?',source)).issubset(set(re.findall(r'\d+(?:[.,]\d+)?',segment))):
            raise ValueError('В ответе потеряно числовое условие: '+label)
        _check_care_invariants(source,segment)
    if plan['seller_path'] and text.find(plan['seller_path'],exclusion_at+len(plan['not_applicable']))<0:
        raise ValueError('Дальнейший путь решения отсутствует')
    _check_care_invariants(plan['action']+' '+plan['prevention'],text)
    check_general_wool_claim(plan,text)
    return validate_reply(text,product,profile,'feedback',buyer_text=buyer_text)

def validate(text,plan,product,profile):
    if not plan:raise ValueError('Нет утверждённого плана ответа')
    if UNSAFE.search(text) or INTERNAL.search(text):raise ValueError('Ответ содержит обвинение, внутреннее правило или обещание восстановления')
    fixed=' '.join(x for x in (plan['explanation'],plan['action'],plan['prevention'],plan['not_applicable'],plan['seller_path']) if x)
    if not text.endswith(fixed):raise ValueError('Объяснение, помощь или профилактика изменены; обновите утверждённый сценарий')
    lead=text[:-len(fixed)].strip()
    if not 10<=len(lead)<=120 or '?' in lead or re.search(r'\d|шерст|хлоп|средств|суш|дефект|брак|виноват|качест',lead,re.I):
        raise ValueError('Вступление содержит неподтверждённый факт, вопрос или обещание')
    if not plan['action']:raise ValueError('В сценарии нет утверждённой помощи покупателю')
    for phrase in re.findall(r'«([^»]+)»',plan['prohibited']):
        if phrase.casefold() in text.casefold():raise ValueError('Ответ содержит запрещённую формулировку: '+phrase)
    return validate_reply(text,product,profile,'feedback')
