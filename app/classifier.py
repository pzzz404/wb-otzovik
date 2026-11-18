"""Conservative routing: only narrow, explicit cases qualify for automatic replies."""
import re
from dataclasses import dataclass

NEGATIVE=re.compile(r'ужасн|плох|не понрав|не подош|разочар|брак|дефект|порван|пятн|гряз|шов|села|усад|линя|линьк|просвеч|катыш|возврат|вернут|деньг|замен|не тот|маломер|великоват|жм[её]т|тесн|размер не|качество не|некачествен|хуж[её]|сыпь|аллерг|вред|не приш|доставка|упаковк|обман|жалоб|царап|дыр|крив|требу|порти|истира',re.I)
SARCASM=re.compile(r'ну спасибо|спасибо.*(?:но|за брак)|прекрасн.*но|отличн.*но|ага[,!]|конечно[,!]|пять зв[её]зд.*(?:но|за)',re.I)
INJECTION=re.compile(r'игнорируй|system\s*:|инструкци[ию] разработчик|вставь артикул|ссылк[уа] на скидку|пообещай|напиши, что|перепиши правила|отключи проверку|https?://',re.I)
PERSONAL=re.compile(r'точно подойд|мой рост|при росте|мой вес|при весе|посоветуй.*размер|какой размер мне|гарант|идеальн.*посад',re.I)
PHOTO_DEPENDENT=re.compile(r'посмотр(?:и|ите).*фото|(?:на|по) фото|что (?:на|с) фото|приложил[аи]? фото|смотри фото|видно на снимке',re.I)
UNPROVEN_NEGATIVE_CLAIM=re.compile(r'можно не гладить|не нужно гладить|без глажки|можно не стирать|не сядет|не линяет|не мн[её]тся',re.I)
POSITIVE=re.compile(r'спасибо|понрав|отличн|хорош|удобн|прекрасн|супер|довольн|классн',re.I)
NEGATION=re.compile(r'\bне\b|\bнет\b|\bни\b|\bбез\b',re.I)
TOPICS=[('composition',r'состав|из чего|какая ткань|хлопок|шерсть'),('wash',r'как стирать|режим стирки|какой уход|стирать\?|стирка\?'),('dry',r'как сушить|сушка|сушить\?'),('iron',r'гладить|утюжить|утюг|без глажки|можно не гладить'),('measurement',r'полуобхват|замер|длина рукава|обхват талии|обхват груди'),('fit',r'какая посадка|свободная посадка\?|прилегающая посадка\?'),('contents',r'сколько.*(?:комплект|вещ|предмет)|что в комплекте')]

@dataclass
class Decision:
    route:str
    intent:str
    reason:str
    field:str|None=None
    auto_allowed:bool=False
    recommendation_ids:tuple=()


def review_text(row):
    parts=[]
    for key in ('body','pros','cons'):
        value=str(dict(row).get(key) or '').strip()
        # The WB test-feedback creator fills generic placeholder pros/cons.
        if re.match(r'^Описание (?:достоинств|недостатков) товара с nmID=\d+$',value):continue
        if value and value.lower() not in ('нет','-','не указано'):parts.append(value)
    return ' '.join(parts).strip()

def classify(row,profile,product,policy):
    if dict(row).get('kind')=='question':return classify_question(row,profile,product,policy)
    if product is None:return Decision('manual','identity','Нет точного товара/варианта')
    text=review_text(row);rating=int(row['rating'])
    photos=dict(row).get('photo_urls') or []
    if isinstance(photos,str):
        import json
        try:photos=json.loads(photos)
        except ValueError:photos=[]
    if not text:
        if photos or dict(row).get('video_url'):
            return Decision('manual','photo_context','Отзыв содержит медиа без текста; нужен просмотр менеджера')
        if dict(row).get('matching_size') not in (None,'','ok'):
            return Decision('manual','size_mismatch','Покупатель отметил несоответствие размера без текста')
        return Decision('none','empty','Оценка без текста и медиа; ответ не требуется')
    if len(text)>int(policy['max_review_chars']):return Decision('manual','long','Длинный отзыв требует чтения менеджером')
    if INJECTION.search(text):return Decision('manual','instruction','В отзыве есть попытка изменить правила ответа')
    if photos and PHOTO_DEPENDENT.search(text):return Decision('manual','photo_context','Для ответа нужно посмотреть фото покупателя')
    if rating<=3:
        matched=[field for field,pattern in TOPICS if re.search(pattern,text,re.I)]
        if len(matched)==1 and not NEGATIVE.search(text) and not SARCASM.search(text):
            return Decision('manual','negative','Низкая оценка требует решения менеджера',field=matched[0])
        return Decision('manual','negative','Низкая оценка требует решения менеджера')
    if NEGATIVE.search(text) or SARCASM.search(text):return Decision('manual','complaint','Есть жалоба, отрицание или неоднозначная оценка')
    if PERSONAL.search(text):return Decision('manual','personal_size','Нельзя гарантировать размер по росту и весу')
    if UNPROVEN_NEGATIVE_CLAIM.search(text):return Decision('manual','unproven_claim','Нет подтверждения для ответа о свойстве без глажки или усадки')
    if re.search(r'между размерами|размер больше|свободн.*посадк',text,re.I):
        rule=profile.advice(product['product_id'],'between_sizes') or profile.advice(product['product_id'],'loose_fit')
        return Decision('manual','size_advice','Совет по размеру требует проверки условий менеджером',field='fit' if rule else None)
    if re.search(r'комплект|с чем сочетать|что ещё подобрать|другой цвет',text,re.I):
        recs=profile.recommendations(product['product_id'])
        return Decision('manual','complement','Сочетание требует проверки уместности',recommendation_ids=tuple(x['recommendation_id'] for x in recs))
    matched=[field for field,pattern in TOPICS if re.search(pattern,text,re.I)]
    if len(matched)>1:return Decision('manual','multi_question','Несколько вопросов требуют проверки менеджером')
    if matched:
        field=matched[0];fact=profile.facts(product['product_id']).get(field)
        if not fact:return Decision('manual','unknown_fact','Нет подтверждённого факта профиля',field=field)
        return Decision('auto','direct_fact','Прямой вопрос и утверждённый факт',field=field,auto_allowed=bool(policy['allow_direct_profile_fact']))
    if '?' in text:return Decision('manual','unknown_question','Вопрос не сопоставлен с подтверждённым фактом')
    if NEGATION.search(text):return Decision('manual','negation','Отрицание требует проверки контекста')
    if POSITIVE.search(text) and rating>=int(policy['min_rating']) and len(text)<=220 and not dict(row).get('cons'):
        return Decision('auto','simple_positive','Однозначная положительная благодарность',auto_allowed=bool(policy['allow_simple_positive']))
    # A synthetically generated WB cons placeholder is not a substantive complaint.
    cons=str(dict(row).get('cons') or '')
    if POSITIVE.search(text) and rating>=int(policy['min_rating']) and len(text)<=220 and re.match(r'^Описание недостатков товара с nmID=\d+$',cons):
        return Decision('auto','simple_positive','Однозначная положительная благодарность',auto_allowed=bool(policy['allow_simple_positive']))
    return Decision('manual','ambiguous','Смысл отзыва не попал в узкую автоматическую политику')

def classify_question(row,profile,product,policy):
    if product is None:return Decision('manual','identity','Карточка вопроса не сопоставлена с профилем')
    text=str(row['body'] or '').strip()
    if not text:return Decision('manual','empty','Нет текста вопроса')
    if len(text)>int(policy['max_review_chars']):return Decision('manual','long','Длинный вопрос требует чтения менеджером')
    if INJECTION.search(text):return Decision('manual','instruction','Вопрос содержит попытку изменить правила ответа')
    if re.search(r'между\s+(?:[a-zа-я0-9]+)\s+и\s+(?:[a-zа-я0-9]+)|между размерами|размер больше',text,re.I):
        rule=profile.advice(product['product_id'],'between_sizes')
        if rule and re.search(r'свободн|просторн|не в обтяжку',text,re.I):
            return Decision('manual','size_advice','Подтверждённое условное правило размера')
        return Decision('manual','size_clarify','Для условного совета нужно уточнить желаемую посадку и сверить замеры')
    if PERSONAL.search(text):return Decision('manual','personal_size','Для индивидуального выбора размера нужны замеры и предпочтение по посадке')
    if UNPROVEN_NEGATIVE_CLAIM.search(text):return Decision('manual','unproven_claim','Свойство не подтверждено профилем')
    matched=[field for field,pattern in TOPICS if re.search(pattern,text,re.I)]
    if len(matched)>1:return Decision('manual','multi_question','Вопрос затрагивает несколько свойств')
    if matched:
        field=matched[0];fact=profile.question_facts(product['product_id']).get(field)
        if not fact:return Decision('manual','unknown_fact','Для карточки нет общего подтверждённого факта; нужен вариант',field=field)
        return Decision('auto','direct_fact','Прямой вопрос и общий подтверждённый факт',field=field,auto_allowed=bool(policy['allow_direct_profile_fact']))
    return Decision('manual','unknown_question','Для ответа нужно уточнить запрос или сведения о товаре')
