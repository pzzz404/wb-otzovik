"""Narrow response policy shared by background worker and manual send API."""
import json,re,time
from .classifier import classify,TOPICS,review_text,NEGATIVE,SARCASM
from .core import digest
from .v2_store import event
from .templates import BUILTINS,default_template,render
from .content_contract import validate_content,buyer_numbers

FORBIDDEN=re.compile(r'https?://|(?<!не )гарантир|точно подойд|не линяет|не просвеч|возврат одобрен|компенсац|скидк|в наличии|бесплатн|доставим|усадк[аиу] не будет|передадим|свяжемся|разбер[её]мся|исправим|обязательно проверим|решим вашу проблему',re.I)
PILLING_INFERENCE=re.compile(r'катышк.{0,80}(?:натуральн|качеств|шерст)|(?:натуральн|качеств|шерст).{0,80}катышк',re.I)
NUMBER=re.compile(r'\d+(?:[.,]\d+)?')

def policy_revision(profile,policy):
    from .complaint_library import CONTRACT_VERSION
    return digest({'profile':profile.revision,'policy':{k:v for k,v in policy.items() if not k.startswith('_')},'builtin_templates':BUILTINS,'answer_contract':CONTRACT_VERSION})

def approval(text,source_hash,revision,audience=None):
    return digest({'text':text,'source':source_hash,'revision':revision,'audience':audience})

def decision_for(row,profile,mode,policy):
    product,match_reason=(profile.match_question(mode,row) if dict(row).get('kind')=='question' else profile.match(mode,row))
    decision=classify(row,profile,product,policy)
    if match_reason and product is None:decision.reason=match_reason
    return product,decision

def draft_for(row,product,decision,profile):
    if decision.intent=='simple_positive':
        return 'Спасибо за отзыв! Рады, что покупка вам понравилась.','утверждённый шаблон',[]
    if decision.intent=='direct_fact' and product:
        fact=profile.facts(product['product_id']).get(decision.field)
        if fact:
            return 'Спасибо за вопрос. По карточке товара: '+str(fact['value']).rstrip('.')+'.','утверждённый факт',[fact['fact_id']]
    if decision.intent=='negative' and product:
        words=review_text(row)
        matched=[field for field,pattern in TOPICS if re.search(pattern,words,re.I)]
        if '?' in words and not NEGATIVE.search(words) and not SARCASM.search(words) and len(matched)==1:
            fact=profile.facts(product['product_id']).get(matched[0])
            if fact:return 'Спасибо за вопрос. По карточке товара: '+str(fact['value']).rstrip('.')+'.','утверждённый факт',[fact['fact_id']]
    if decision.intent in ('complaint','negative','photo_context'):
        return 'Жаль, что с изделием возникла такая ситуация. Для решения вопроса после покупки можно оформить обращение по заказу в Wildberries: Заказы → Возвраты.','нейтральный черновик',[]
    if decision.intent=='size_advice' and product:
        rule=profile.advice(product['product_id'],'between_sizes') or profile.advice(product['product_id'],'loose_fit')
        if rule:return str(rule['response']),'утверждённое правило размера',[]
    return None,None,[]

def validate_reply(text,product,profile,kind='feedback',buyer_text='',extra_facts=()):
    text=text.strip()
    if not 2<=len(text)<=700:raise ValueError('Ответ должен содержать от 2 до 700 символов')
    if FORBIDDEN.search(text):raise ValueError('Ответ содержит ссылку или неподтверждённое обещание')
    if PILLING_INFERENCE.search(text):raise ValueError('Нельзя выводить свойства шерсти или качество из катышков')
    if re.search(r'\b(?:артикул|nmID)\b',text,re.I):raise ValueError('Артикул в ответе требует отдельной проверки')
    if re.search(r'\{[a-z_]+\}',text):raise ValueError('В ответе остался параметр шаблона')
    if kind=='question' and product and buyer_text:
        extra_facts=tuple(extra_facts)+tuple(profile.question_size_facts(product['product_id'],buyer_text).values())
    facts=(profile.question_facts(product['product_id']) if kind=='question' else profile.facts(product['product_id'])) if product else {}
    validate_content(text,product,profile,kind,buyer_text,extra_facts)
    allowed=' '.join(str(f['value']) for f in facts.values())+' '+' '.join(
        str(f['value']) for f in extra_facts if f.get('status')=='approved' and f.get('source') and f.get('version'))
    allowed_numbers=set(NUMBER.findall(allowed))|buyer_numbers(text,buyer_text)
    if not set(NUMBER.findall(text)).issubset(allowed_numbers):
        raise ValueError('Число в ответе отсутствует в утверждённом профиле варианта')
    return text

def prepare(cx,row,profile,mode,policy):
    product,decision=decision_for(row,profile,mode,policy)
    kind=dict(row).get('kind') or 'feedback'
    template=default_template(cx,kind,decision,review_text(row))
    text=generator=None;fids=[]
    if template and product:
        try:
            text,fids=render(template,profile,product,kind,decision.intent)
            generator='Шаблон · '+template['name']
        except ValueError:pass
    revision=policy_revision(profile,policy)
    if text:
        try:text=validate_reply(text,product,profile,kind)
        except ValueError:
            text=None;generator=None;fids=[]
            decision.route='manual';decision.reason='Черновик не прошёл проверку по профилю'
    auto=bool(policy.get('auto_enabled')) and decision.route=='auto' and decision.auto_allowed and bool(text)
    if kind=='question' and policy.get('question_auto_audience')!='public':auto=False
    status='no_reply_needed' if decision.route=='none' else ('ready_auto' if auto else 'needs_review')
    grant=approval(text,row['source_hash'],revision,'public' if kind=='question' else None) if auto else None
    model_status='pending' if mode=='sandbox' and status=='needs_review' and product and decision.intent in ('complaint','negative','ambiguous','multi_question','unknown_question','unknown_fact') else 'not_needed'
    cx.execute('''UPDATE reviews SET status=?,intent=?,reason=?,draft=?,generator=?,fact_ids=?,
       revision=?,approval_hash=?,approval_kind=?,model_status=?,audience=?,draft_version=draft_version+1,updated_at=? WHERE id=? AND status='new' ''',
       (status,decision.intent,decision.reason,text,generator,json.dumps(fids),revision,grant,
        'auto_policy' if auto else None,model_status,'public' if kind=='question' and auto else None,time.time(),row['id']))
    event(cx,row['id'],'prepared',status+': '+decision.intent)
    return status
