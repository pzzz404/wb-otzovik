"""One deterministic decision path for background preparation and read-only preview.

The model may suggest wording. Product facts, campaign eligibility, WB article IDs
and the authority to send stay on the server.
"""
import json,re,time
from datetime import datetime
from zoneinfo import ZoneInfo
from .classifier import review_text
from .profile import requested_sizes
from .v2_logic import decision_for,validate_reply
from .templates import default_template,render as render_template
from .v4_store import config,scenarios,campaigns,candidates,advice,DEFAULT_CONFIG
from .complaint_library import choose as complaint_choose,plan as complaint_plan,render as render_complaint,validate as validate_complaint

POSITIVE_TRIGGER={
 'softness':re.compile(r'мягк|приятн.*ткан|комфортн|уютн',re.I),
 'fit':re.compile(r'посадк|размер подош|сидит|фасон|силуэт|вырез',re.I),
 'look':re.compile(r'образ|комплект|сочета|наряд|костюм',re.I),
}
COMPLAINT=re.compile(r'не подош|не понрав|разочар|дефект|брак|возврат|вернут|жалоб|плох|ужас|катыш|разош[её]л|села|усад|жм[её]т|тесн|маломер|пятн|дыр|просвеч',re.I)
COLOR_REQUEST=re.compile(r'\b(?:хочу|нужен|нужна|ищу|есть\s+ли|подскажите)[^.!?]{0,65}?\b(ч[её]рн\w*|бел\w*|молочн\w*|син\w*|сер\w*|красн\w*|зел\w*)\b',re.I)
COLOR_STEMS={'черн':'чёрный','чёрн':'чёрный','бел':'белый','молочн':'молочный',
             'син':'синий','сер':'серый','красн':'красный','зел':'зелёный'}

def color_request(row,profile,product):
    if row['kind']!='feedback' or not product:return None
    match=COLOR_REQUEST.search(review_text(row))
    if not match:return None
    token=match.group(1).casefold()
    requested=next((color for stem,color in COLOR_STEMS.items() if token.startswith(stem)),None)
    if not requested:return None
    variants=[item for item in profile.products.values() if item['active'] and
              item['model_id']==product['model_id'] and item['color'].casefold()==requested]
    return requested,variants

def size_question_answer(row,profile,product):
    if row['kind']!='question' or not product:return None
    body=str(row['body'] or '')
    facts=profile.question_size_facts(product['product_id'],body)
    named=requested_sizes(body)
    if not named:return None
    available=[str(facts[size]['value']).rstrip('.')+'.' for size in named if size in facts]
    missing=[size for size in named if size not in facts]
    if not available:return None
    text=' '.join(available)
    if missing:text+=' Замер размера '+', '.join(missing)+' мы пока не можем назвать.'
    text+=' Это замеры изделия: сравните их со своими мерками и желаемой свободой посадки.'
    return text,[facts[size]['fact_id'] for size in named if size in facts]

def style_text(text,row,settings,product,profile):
    """Apply only bounded, product-neutral presentation choices."""
    if not text:return text
    if settings.get('tone')=='neutral' and text.startswith('Спасибо за отзыв! Рады, что товар вам понравился.'):
        text='Спасибо за отзыв.'+text[len('Спасибо за отзыв! Рады, что товар вам понравился.'):]
    elif settings.get('tone')=='warm' and text.startswith('Спасибо за отзыв!'):
        text='Благодарим за тёплый отзыв!'+text[len('Спасибо за отзыв!'):]
    if settings.get('length')=='medium' and row['kind']=='feedback' and not COMPLAINT.search(review_text(row)):
        text+=' Если захотите уточнить свойства товара, задайте вопрос в карточке.'
    name=(dict(row).get('user_name') or '').strip()
    if settings.get('use_name') and re.fullmatch(r'[А-Яа-яЁёA-Za-z][А-Яа-яЁёA-Za-z\- ]{1,29}',name):
        text=name+', '+text[0].lower()+text[1:]
    return validate_reply(text,product,profile,row['kind'],buyer_text=str(row['body'] or ''))

def policy_for(cx,base,product=None):
    result=dict(base);settings=config(cx,product)
    result['auto_enabled']=settings['send_mode']=='rules'
    result['min_rating']=min((int(k) for k,v in settings['rating_actions'].items() if v=='auto'),default=5)
    meaningful={k:v for k,v in settings.items() if k!='resolved_scope'}
    if meaningful!=DEFAULT_CONFIG:result['v4_effective']=meaningful
    result['_v4']=settings
    return result

def _scenarios(cx):return {x['id']:x for x in scenarios(cx)}

def _trigger(text,trigger):
    if trigger=='any_positive':return any(x.search(text) for x in POSITIVE_TRIGGER.values())
    return bool(POSITIVE_TRIGGER.get(trigger,re.compile(r'(?!)')).search(text))

def _size(row,product):
    # A question has no purchased size. For a feedback, WB size is authoritative;
    # Demo's unique exact-variant mapping supplies its known size only if no WB size.
    if row['kind']=='question':return None
    return dict(row).get('remote_size') or product.get('size')

def select_campaign(cx,row,product,mode,now=None):
    now=time.time() if now is None else now
    result={'candidate_count':0,'eligible_count':0,'selected':[],'reserve':[],
            'excluded':[],'campaign':None,'reason':'Нет подходящей активной кампании'}
    if row['kind']!='feedback':
        result['reason']='Вопрос: допродажа исключена';return result
    text=review_text(row);rating=int(row['rating'])
    if rating<4 or COMPLAINT.search(text):
        result['reason']='Жалоба, низкая оценка или вопрос: допродажа исключена';return result
    available=[]
    for campaign in sorted(campaigns(cx),key=lambda x:(-int(x['priority']),x['id'])):
        if campaign['status']!='active' or not float(campaign['starts_at'])<=now<=float(campaign['ends_at']):continue
        if product['product_id'] not in campaign['source_product_ids'] or not _trigger(text,campaign['trigger']):continue
        available.append(campaign)
    if not available:return result
    campaign=available[0];result['campaign']=campaign
    catalog={x['id']:x for x in candidates(cx)};result['candidate_count']=len(campaign['candidate_ids'])
    size=_size(row,product)
    for cid in campaign['candidate_ids']:
        candidate=catalog.get(cid)
        reason=None
        if not candidate:reason='Кандидат удалён'
        elif candidate['nm_id']==product.get(mode+'_nmID'):reason='Тот же товар'
        elif not candidate['approved']:reason='Товар не утверждён продавцом'
        elif mode=='sandbox' and not candidate.get('sandbox_nm_id'):reason='Нет собственной тестовой карточки Sandbox'
        elif not size:reason='Неизвестен размер покупки'
        elif size not in candidate['sizes']:reason='Нет подтверждённого соответствия размера'
        elif int(candidate['stock'].get(size,0))<int(campaign['min_stock']):reason='Недостаточный остаток по размеру'
        elif now-float(candidate['snapshot_at'])>int(campaign['max_age_minutes'])*60 or now<float(candidate['snapshot_at']):reason='Снимок остатка устарел'
        elif campaign['promo_only'] and not float(candidate['promo_start'])<=now<=float(candidate['promo_end']):reason='Акция не действует'
        elif product['product_id'] not in candidate['relations']:reason='Связь с исходным товаром не утверждена'
        elif campaign['trigger'] not in candidate['tags'] and campaign['trigger']!='any_positive':reason='Свойство не соответствует похвале'
        if reason:result['excluded'].append({'id':cid,'reason':reason});continue
        relation=candidate['relations'][product['product_id']]
        item={**candidate,'reason':relation,'size':size,'stock_for_size':int(candidate['stock'][size]),
              'article':candidate['sandbox_nm_id'] if mode=='sandbox' else candidate['nm_id']}
        result['reserve'].append(item)
    result['reserve'].sort(key=lambda x:(-int(x['priority']),x['id']))
    unique=[];seen_articles=set()
    for item in result['reserve']:
        if item['article'] in seen_articles:
            result['excluded'].append({'id':item['id'],'reason':'Повтор той же карточки WB'})
        else:
            seen_articles.add(item['article']);unique.append(item)
    result['reserve']=unique
    result['eligible_count']=len(result['reserve'])
    if campaign['mode']=='kit':
        groups={};required={}
        for cid in campaign['candidate_ids']:
            item=catalog.get(cid)
            if item and item.get('kit_id'):required.setdefault(item['kit_id'],set()).add(cid)
        for item in result['reserve']:
            if item['kit_id']:groups.setdefault(item['kit_id'],[]).append(item)
        complete=[xs for kit_id,xs in groups.items() if len(xs)>=2 and len(xs)<=int(campaign['max_items'])
                  and {x['id'] for x in xs}==required.get(kit_id)]
        if complete:
            result['selected']=complete[0]
        else:result['reason']='Полный совместимый комплект недоступен'
    else:result['selected']=result['reserve'][:int(campaign['max_items'])]
    selected_ids={x['id'] for x in result['selected']}
    result['reserve']=[x for x in result['reserve'] if x['id'] not in selected_ids]
    if result['selected']:result['reason']='Подобраны проверенные товары'
    elif result['reason']=='Нет подходящей активной кампании':result['reason']='Нет подходящих товаров; основной ответ сохраняется'
    return result

def upsell_text(selection):
    chosen=selection['selected'];campaign=selection['campaign']
    if not chosen:return ''
    names=' и '.join(f"{x['name']} — артикул WB {x['article']}" for x in chosen)
    text=' Если подбираете дополнение, можно рассмотреть '+names+'.'
    if campaign['promo_only']:
        zone=ZoneInfo(campaign['timezone'])
        end=datetime.fromtimestamp(float(campaign['ends_at']),zone)
        text+=' Предложение действует до '+end.strftime('%d.%m.%Y')+'. Актуальные цены проверьте в карточках.'
    return text

def validate_composed(text,product,profile,kind,selection=None):
    if not selection or not selection['selected']:return validate_reply(text,product,profile,kind)
    tail=upsell_text(selection)
    if not text.endswith(tail):raise ValueError('Коммерческий фрагмент изменён или устарел')
    core=text[:-len(tail)].rstrip()
    validate_reply(core,product,profile,kind)
    for item in selection['selected']:
        if str(item['article']) not in tail:raise ValueError('Артикул не совпадает с выбранным товаром')
    if re.search(r'https?://|www\.',tail,re.I):raise ValueError('Ссылки в ответе запрещены')
    if len(text)>700:raise ValueError('Ответ длиннее 700 символов')
    return text

def evaluate(cx,row,profile,mode,base_policy,now=None,include_campaign=True):
    product,_=(profile.match_question(mode,row) if row['kind']=='question' else profile.match(mode,row))
    policy=policy_for(cx,base_policy,product)
    product,decision=decision_for(row,profile,mode,policy)
    settings=policy['_v4'];scene_map=_scenarios(cx)
    scenario_id=('complaint' if decision.intent in ('negative','complaint','photo_context') else
                 'size' if decision.intent in ('size_advice','size_clarify','personal_size','size_mismatch') else
                 'care' if decision.field in ('wash','dry','iron') else
                 'material' if decision.field in ('composition','feature') else 'thanks')
    scene=scene_map.get(scenario_id)
    specific=complaint_choose(row,profile,product) if decision.intent in ('negative','complaint','photo_context','instruction') else None
    complaint,complaint_gap=complaint_plan(cx,row,profile,product) if specific else (None,None)
    if complaint:
        scenario_id=complaint['scenario_id'];scene={'name':complaint['key'],'enabled':True,'version':complaint['version'],'advice':''}
    template=None if specific else default_template(cx,row['kind'],decision,review_text(row))
    text=generator=None;fids=[]
    if complaint:
        try:
            text=validate_complaint(render_complaint(complaint),complaint,product,profile)
            fids=complaint['fact_ids'];generator='Утверждённый сценарий · '+complaint['key']
        except ValueError as exc:
            complaint_gap='Сценарий не прошёл проверку: '+str(exc)
    if template and product:
        try:
            text,fids=render_template(template,profile,product,row['kind'],decision.intent)
            text=style_text(text,row,settings,product,profile);generator='Шаблон · '+template['name']
        except ValueError:text=None;fids=[]
    if decision.intent in ('size_clarify','size_advice','personal_size'):
        measured=size_question_answer(row,profile,product)
        if measured:
            text,fids=measured
            text=validate_reply(text,product,profile,'question',buyer_text=str(row['body'] or ''))
            generator='Утверждённые замеры вариантов'
    requested=color_request(row,profile,product) if decision.intent not in ('complaint','negative','instruction') else None
    if requested:
        color,variants=requested
        if variants:
            text=f'Спасибо за отзыв! В каталоге есть вариант этой модели в цвете «{color}». Актуальное наличие проверьте в карточке товара.'
            decision.reason='Запрос другого цвета; вариант той же модели подтверждён'
        else:
            text='Спасибо за отзыв! Рады, что товар вам понравился. Актуальные цвета можно проверить в карточке товара.'
            decision.route='manual'
            decision.reason='Для запрошенного цвета нет подтверждённого варианта этой модели'
        text=validate_reply(text,product,profile,'feedback')
        generator='Шаблон · запрос другого цвета';fids=[]
    if scene and not scene['enabled']:
        decision.route='manual';decision.reason='Сценарий выключен продавцом'
    if row['kind']=='feedback' and settings['exceptions'].get('photo_context') and json.loads(dict(row).get('photo_urls') or '[]') and decision.route=='auto':
        decision.route='manual';decision.reason='Фото покупателя требует проверки'
    seller=next((x for x in advice(cx,product['model_id']) if scenario_id in x['applies_to'] and x['status']=='active'),None) if product else None
    tip=(scene['advice'] if scene and scene['advice'] else seller['text'] if seller else '').strip()
    if tip and text and decision.route=='auto' and row['kind']=='feedback' and not COMPLAINT.search(review_text(row)):
        proposed=text.rstrip()+' '+tip
        try:text=validate_reply(proposed,product,profile,row['kind']);generator+=' · сценарий '+scene['name']
        except ValueError:
            decision.route='manual';decision.reason='Совет сценария не прошёл проверку фактов'
    if row['kind']=='feedback' and settings['rating_actions'].get(str(row['rating']))!='auto' and decision.route=='auto':
        decision.route='manual';decision.reason='Оценка направлена менеджеру правилом продавца'
    if settings['send_mode']=='review_all' and decision.route=='auto':
        decision.route='manual';decision.reason='Все ответы направлены на проверку'
    if row['kind']=='question' and decision.route=='auto':
        decision.route='manual';decision.reason='Для вопроса менеджер выбирает аудиторию'
    if settings['only_new'] and (dict(row).get('ingested_at') or row['updated_at'])<settings['backlog_cutoff'] and decision.route=='auto':
        decision.route='manual';decision.reason='Накопленное обращение требует отдельного решения'
    selection=select_campaign(cx,row,product,mode,now) if include_campaign and product and not requested else None
    if selection and selection['selected'] and text and decision.route=='auto':
        proposed=text+upsell_text(selection)
        try:text=validate_composed(proposed,product,profile,row['kind'],selection);generator+=' · сезонная кампания'
        except ValueError:selection['selected']=[];selection['reason']='Коммерческий фрагмент не прошёл проверку; основной ответ сохранён'
    elif selection and selection['selected'] and decision.route!='auto':
        selection['selected']=[];selection['reason']='Допродажа исключена для обращения на ручной проверке'
    ai_active=mode=='sandbox' or settings['backlog_cutoff']>0
    requires_ai=bool(complaint) and decision.intent!='instruction' and ai_active and settings['generation_mode'] in ('ai','combined') if specific else (
        ai_active and (settings['generation_mode']=='ai' or (settings['generation_mode']=='combined' and decision.intent not in ('simple_positive','direct_fact','empty'))))
    if requested:requires_ai=False
    if decision.route=='none':status='no_reply_needed'
    elif requires_ai and product:status='needs_review'
    else:status='ready_auto' if decision.route=='auto' and text else 'needs_review'
    if status=='needs_review' and decision.route=='auto' and requires_ai:reason='ИИ готовит ответ; отправка после проверки генерации'
    else:reason=complaint_gap or decision.reason
    return {'product':product,'decision':decision,'text':text,'generator':generator,'fact_ids':fids,
            'status':status,'reason':reason,'scenario_id':scenario_id,'scenario_version':scene['version'] if scene else None,
            'settings':settings,'requires_ai':requires_ai and decision.route!='none',
            'selection':selection,'policy':policy,'complaint_plan':complaint}

def prepare(cx,row,profile,mode,base_policy):
    """Persist the same decision returned by preview, without an external call."""
    from .v2_logic import policy_revision,approval
    result=evaluate(cx,row,profile,mode,base_policy)
    selection=result['selection'] or {};campaign=selection.get('campaign') if selection.get('selected') else None
    chosen=[x['id'] for x in selection.get('selected',[])]
    text=result['text'];revision=policy_revision(profile,result['policy'])
    auto=result['status']=='ready_auto' and bool(text)
    grant=approval(text,row['source_hash'],revision,'public' if row['kind']=='question' else None) if auto else None
    model_status='pending' if result['requires_ai'] and result['status']=='needs_review' and result['product'] else 'not_needed'
    cx.execute('''UPDATE reviews SET status=?,intent=?,reason=?,draft=?,generator=?,fact_ids=?,
      revision=?,approval_hash=?,approval_kind=?,model_status=?,audience=?,draft_version=draft_version+1,
      scenario_id=?,scenario_version=?,campaign_id=?,campaign_version=?,recommendation_ids=?,v4_route_auto=?,updated_at=?
      WHERE id=? AND status='new' ''',
      (result['status'],result['decision'].intent,result['reason'],text,result['generator'],json.dumps(result['fact_ids']),
       revision,grant,'auto_policy' if auto else None,model_status,
       'public' if row['kind']=='question' and auto else None,result['scenario_id'],result['scenario_version'],
       campaign['id'] if campaign else None,campaign['version'] if campaign else None,json.dumps(chosen),
       int(result['decision'].route=='auto'),time.time(),row['id']))
    from .v2_store import event
    event(cx,row['id'],'prepared_v4',result['status']+': '+result['decision'].intent)
    return result['status']
