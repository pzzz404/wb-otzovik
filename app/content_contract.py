"""Bounded, source-aware checks for public seller claims.

This is a safety gate for identifiable claims, not a general proof of meaning.
The same gate is used for templates, model proposals, and manager edits.
"""
import re

WORD = re.compile(r'[а-яёa-z]{4,}', re.I)
PROPERTY = re.compile(
    r'(?:у\s+(?:(?:этого|этой|данного|данной)\s+)?(?:платья|платье|товара|модели|изделия|футболки)\s+(?:есть|имеетс\w*|предусмотрен\w*)|'
    r'в\s+(?:этой|данной)\s+модели\s+(?:есть|предусмотрен\w*|имеетс\w*)|'
    r'(?:платье|футболка|изделие|товар|модель)\s+(?:имеет|содержит|оснащен\w*|выполнен\w*\s+с|с))'
    r'\s+(?P<claim>[^.!?;]+)', re.I)
ATTRIBUTION = re.compile(r'вы\s+(?:пишете|указали|сообщили|отмечаете)|по\s+вашим\s+словам|в\s+вашем\s+отзыве', re.I)
NUMBER = re.compile(r'\d+(?:[.,]\d+)?')
TEMP = re.compile(r'\b(?:до|при|на|выше)\s*(\d{1,3})\s*(?:°\s*[cс]|градус\w*)', re.I)
SPIN_PERMISSION = re.compile(
    r'\b(?:можно|допускаетс\w*|допустим\w*|разрешен\w*|разрешён\w*|рекомендуетс\w*)'
    r'(?:\s+\w+){0,4}\s+(?:отжим\w*|отжат\w*|выжат\w*)\b|'
    r'\b(?:отжим\w*|отжат\w*|выжат\w*)(?:\s+\w+){0,3}\s+'
    r'(?:можно|допускаетс\w*|допустим\w*|разрешен\w*|разрешён\w*)\b|'
    r'\b(?:отжимайте|отожмите|выжимайте|выжмите)\b|\bс\s+отжимом\b', re.I)
DRY_CONTRADICTION = re.compile(r'\b(?:сушите|сушить|можно\s+сушить|сушка\s+(?:допускается|разрешена))[^.!?;]{0,45}\b(?:вертикально|на\s+весу|на\s+вешалке)\b', re.I)
NEGATED = re.compile(r'\b(?:не|нельзя|запрещено|избегайте|без)\b', re.I)
CARE_ACTION = re.compile(r'\b(?:стирай\w*|стирать|стирк\w*|увлажн\w*|замачив\w*|можно|допускаетс\w*|рекомендуетс\w*)\b', re.I)
SELLER_DIRECTION = re.compile(r'\b(?:стирайте|сушите|отжимайте|можно|допускаетс\w*|рекомендуем|следует)\b', re.I)
ROOT_STOP = {'этог','этои','дани','данн','плат','плате','това','моде','изде','футб','есть','имеет','выпо','пред','сост'}


def _roots(value):
    return {word.casefold()[:5] for word in WORD.findall(value) if word.casefold()[:5] not in ROOT_STOP}


def _clauses(text):
    return [part.strip() for part in re.split(r'(?<=[.!?;])\s+|\n+', text) if part.strip()]


def _approved(fact):
    return fact.get('status')=='approved' and bool(fact.get('source')) and bool(fact.get('version'))


def _reported_at(clause,match,buyer_text=''):
    attribution=ATTRIBUTION.search(clause)
    value=match.group(1) if match.lastindex else match.group()
    return bool(buyer_text and attribution and attribution.end()<match.start() and
                value in buyer_text and not SELLER_DIRECTION.search(clause[attribution.end():match.start()]))


def buyer_numbers(text, buyer_text):
    """Numbers remain buyer statements only inside an attributed clause."""
    return {match.group() for clause in _clauses(text) for match in NUMBER.finditer(clause)
            if _reported_at(clause,match,buyer_text)}


def validate_content(text, product, profile, kind='feedback', buyer_text='', extra_facts=()):
    facts = list((profile.question_facts(product['product_id']) if kind == 'question'
                  else profile.facts(product['product_id'])).values()) if product else []
    facts += [fact for fact in extra_facts if _approved(fact)]
    approved_text = ' '.join(str(fact['value']) for fact in facts if _approved(fact))
    approved_roots = _roots(approved_text)
    for clause in _clauses(text):
        # Quoted buyer observations are evidence about one order, never product facts.
        for match in PROPERTY.finditer(clause):
            if ATTRIBUTION.search(clause[:match.start()]):
                continue
            claim = match.group('claim').strip(' «»\"')
            roots = _roots(claim)
            if roots and not roots.issubset(approved_roots):
                raise ValueError('Свойство товара «' + claim[:80] +
                                 '» не подтверждено утверждённым фактом. Исправьте текст или утвердите факт в знаниях товара.')
        if not product:
            continue
        care = profile.facts(product['product_id'])
        wash = str(care.get('wash', {}).get('value', '')).casefold()
        dry = str(care.get('dry', {}).get('value', '')).casefold()
        if 'без отжима' in wash:
            for match in SPIN_PERMISSION.finditer(clause):
                prefix = clause[max(0, match.start()-24):match.start()]
                if not NEGATED.search(prefix) and not ATTRIBUTION.search(clause[:match.start()]):
                    raise ValueError('Совет об отжиме противоречит утверждённому уходу «без отжима».')
        if 'горизонтально' in dry:
            match = DRY_CONTRADICTION.search(clause)
            if match and not NEGATED.search(clause[max(0, match.start()-15):match.end()]):
                if not ATTRIBUTION.search(clause[:match.start()]):
                    raise ValueError('Совет о сушке противоречит утверждённой горизонтальной сушке.')
        limits = [int(number) for number in re.findall(r'\bдо\s*(\d{1,3})\s*(?:°\s*[cс]|градус)', wash)]
        if limits:
            for match in TEMP.finditer(clause):
                if int(match.group(1)) <= min(limits):
                    continue
                if _reported_at(clause,match,buyer_text):
                    continue
                if CARE_ACTION.search(clause):
                    raise ValueError('Совет по температуре превышает утверждённый предел ухода.')
    return text
