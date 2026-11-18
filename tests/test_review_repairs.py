"""Regressions from the independent Grok review; isolated Demo data only."""
import re
from pathlib import Path

import pytest
from fastapi import HTTPException

from app import v2
from app import ai_drafts
from app.ai_drafts import generate,context
from app.complaint_library import LIBRARY, plan, render, validate_adapted
from app.profile import load
from app.v2_store import connect, import_question, import_review
from app.v4_logic import evaluate, prepare
from app.v4_store import init, profile_overlay, draft_fact, apply_fact, library_draft, save_library_draft, approve_library_draft


@pytest.fixture
def case(tmp_path):
    base = load(v2.ROOT / 'data/profiles_v4.xlsx')
    cx = connect(tmp_path / 'repairs.sqlite3')
    init(cx, base)
    yield cx, base
    cx.close()


def review(cx, rid, body, nm=920101, rating=2, article='SL-DR-01'):
    import_review(cx, {'id': rid, 'nm_id': nm, 'rating': rating, 'body': body,
                       'pros': '', 'cons': '', 'photo_urls': [],
                       'remote_size': 'M', 'remote_article': article})
    return cx.execute('SELECT * FROM reviews WHERE id=?', (rid,)).fetchone()


def planned(cx, base, row):
    p = profile_overlay(cx, base)
    product, _ = p.match('demo', row)
    answer, gap = plan(cx, row, p, product)
    assert answer, gap
    return p, product, answer


def test_unproved_property_and_approved_fact(case):
    cx, base = case
    row = review(cx, 'CARE', 'После первой стирки платье село')
    p, product, procedure = planned(cx, base, row)
    good = render(procedure)
    for phrase in ('У этого платья есть двойная подкладка.',
                   'В этой модели предусмотрена двойная подкладка.',
                   'Платье выполнено с двойной подкладкой.',
                   'Платье с двойной подкладкой.'):
        with pytest.raises(ValueError, match='факт|свойств'):
            validate_adapted(phrase + ' ' + good, procedure, product, p)
        with pytest.raises(ValueError, match='факт|свойств'):
            validate_adapted(good + ' ' + phrase, procedure, product, p)
    assert validate_adapted(good, procedure, product, p) == good
    key, version = draft_fact(cx, {'scope':'variant','owner_id':'DR-01','field':'feature',
                                   'value':'Двойная подкладка', 'source':'Тестовая карточка продавца',
                                   'excerpt':'Двойная подкладка','method':'manual','author':'Менеджер'}, base)
    apply_fact(cx, key, version, base)
    p = profile_overlay(cx, base)
    assert validate_adapted(good + ' У этого платья есть двойная подкладка.', procedure, product, p)


def test_care_contradictions_and_negations(case):
    cx, base = case
    row = review(cx, 'CARE', 'Стирала вручную при 30 без отжима, но платье село')
    p, product, procedure = planned(cx, base, row)
    good = render(procedure)
    for phrase in ('Можно отжать вещь.', 'Отжим допускается.',
                   'Стирайте при 60 °C.', 'Можно сушить на вешалке.'):
        with pytest.raises(ValueError, match='уход|температур|отжим|суш'):
            validate_adapted(good + ' ' + phrase, procedure, product, p)
    assert validate_adapted(good + ' Не отжимайте вещь.', procedure, product, p)
    assert validate_adapted('Вы пишете, что стирали при 60 °C. ' + good,
                            procedure, product, p, buyer_text='Стирала при 60 °C, платье село')
    with pytest.raises(ValueError):
        validate_adapted('Вы пишете, что стирали при 60 °C, поэтому стирайте при 60 °C. ' + good,
                         procedure, product, p, buyer_text='Стирала при 60 °C, платье село')
    with pytest.raises(ValueError):
        validate_adapted(good + ' Стирайте при 60 °C.', procedure, product, p,
                         buyer_text='Стирала при 60 °C, платье село')


def test_wash_branch_uses_only_reported_conditions(case):
    cx, base = case
    for rid, body, should_mention in (
        ('HOT', 'Стирала платье при 60 градусах, оно село', False),
        ('TOWELS', 'Стирала платье с полотенцами при 60 градусах, оно село', True),
        ('NEGATED', 'Не стирала с полотенцами, но платье село', False),
        ('UNKNOWN', 'После стирки платье село', False),
    ):
        row = review(cx, rid, body)
        _, _, procedure = planned(cx, base, row)
        assert ('с другими вещами' in procedure['explanation']) is should_mention
    row = review(cx, 'COMPLIANT', 'Стирала вручную при 30 без отжима, но платье село')
    p, product, procedure = planned(cx, base, row)
    bad = render(procedure).replace(procedure['explanation'],
        'Для этой шерстяной модели после стирки действительно возможно изменение формы, даже если уход соблюдён.')
    with pytest.raises(ValueError, match='свойств|модел|факт'):
        validate_adapted(bad, procedure, product, p)
    assert validate_adapted(render(procedure), procedure, product, p)


def test_color_request_and_plain_praise(case):
    cx, base = case
    p = profile_overlay(cx, base)
    row = review(cx, 'COLOR', 'Футболка понравилась, хочу такой же вариант в чёрном цвете.',
                 910001, 5, 'SL-TS-01')
    result = evaluate(cx, row, p, 'demo', v2.POLICY)
    assert 'чёрн' in result['text'].lower() and 'налич' in result['text'].lower()
    assert 'подтверждённый каталог' not in result['text']
    assert result['status'] == 'ready_auto' and not (result['selection'] or {}).get('selected')
    row = review(cx, 'PRAISE', 'Футболка понравилась.', 910001, 5, 'SL-TS-01')
    result = evaluate(cx, row, p, 'demo', v2.POLICY)
    assert result['status'] == 'ready_auto' and 'Спасибо' in result['text']
    p.products={key:value for key,value in p.products.items() if key!='TS-01B'}
    result=evaluate(cx,cx.execute("SELECT * FROM reviews WHERE id='COLOR'").fetchone(),p,'demo',v2.POLICY)
    assert result['status']=='needs_review' and 'нет подтверждённого варианта' in result['reason']


def test_size_after_and_before_purchase(case):
    cx, base = case
    sid, version = library_draft(cx, 'size_after', 'model', 'TEE', base)
    data = {key: LIBRARY['size_after'][key] for key in
            ('goal','explanation','action','prevention','not_applicable','prohibited','seller_path')}
    data.update(approval_source='Тестовая инструкция продавца', label_wash='')
    save_library_draft(cx, sid, version, data)
    approve_library_draft(cx, sid, version, base)
    row = review(cx, 'SIZE', 'После покупки размер не подошёл', 910001, 2, 'SL-TS-01')
    p = profile_overlay(cx, base)
    result = evaluate(cx, row, p, 'demo', v2.POLICY)
    assert 'Если товар ещё не куплен' not in result['text']
    import_question(cx, {'id':'Q02','nm_id':910001,'body':'Выбираю между M и L. Какой размер лучше?',
                         'remote_article':'SL-TS-01'})
    question = cx.execute("SELECT * FROM reviews WHERE id='q:Q02'").fetchone()
    result = evaluate(cx, question, p, 'demo', v2.POLICY)
    assert '52 см' in result['text'] and 'замер' in result['text'].lower()
    assert 'L' in result['text'] and '52 см' in result['text']
    assert not re.search(r'размер L.{0,30}\d+\s*см', result['text'])
    p.products=dict(p.products)
    p.products['TS-01L']={**p.products['TS-01'],'product_id':'TS-01L','size':'L'}
    p.by_field=dict(p.by_field)
    p.by_field[('variant','TS-01L','measurement')]={
        'fact_id':'TEST-L','scope':'variant','owner_id':'TS-01L','field':'measurement',
        'value':'Полуобхват груди изделия L — 56 см','source':'Тестовая карточка',
        'status':'approved','version':1}
    result=evaluate(cx,question,p,'demo',v2.POLICY)
    assert '52 см' in result['text'] and '56 см' in result['text']
    assert len(result['fact_ids'])==2 and 'TEST-L' in result['fact_ids']


def test_existing_seller_size_scenario_gets_draft_suggestion_only(case):
    import json
    cx,base=case
    old={**LIBRARY['size_after'],'library_key':'size_after','scope':'model','owner_id':'TEE',
         'not_applicable':'Если товар ещё не куплен, ориентируйтесь на замеры модели до заказа.',
         'label_wash':'','approval_source':'Старая инструкция продавца','approved_by':'Менеджер'}
    sid='size_after:model:TEE'
    cx.execute('INSERT INTO v4_scenarios VALUES(?,?,?,?,0)',
               (sid,1,json.dumps(old,ensure_ascii=False),'active'))
    draft_sid,version=library_draft(cx,'size_after','model','TEE',base)
    assert draft_sid==sid and version==2
    active=json.loads(cx.execute('SELECT data FROM v4_scenarios WHERE scenario_id=? AND version=1',(sid,)).fetchone()[0])
    proposal=json.loads(cx.execute('SELECT data FROM v4_scenarios WHERE scenario_id=? AND version=2',(sid,)).fetchone()[0])
    assert 'Если товар ещё не куплен' in active['not_applicable']
    assert 'Если товар ещё не куплен' not in proposal['not_applicable']
    assert cx.execute('SELECT status FROM v4_scenarios WHERE scenario_id=? AND version=2',(sid,)).fetchone()[0]=='draft'


def test_send_boundary_blocks_claim_then_accepts_corrected_text(case,monkeypatch):
    cx,base=case
    row=review(cx,'SEND','После первой стирки платье село')
    p=profile_overlay(cx,base)
    prepare(cx,row,p,'demo',v2.POLICY)
    row=cx.execute("SELECT * FROM reviews WHERE id='SEND'").fetchone()
    monkeypatch.setattr(v2,'profile',lambda connection=None:
                        profile_overlay(connection,base) if connection else base)
    for addition in ('У этого платья есть двойная подкладка.','Можно отжать вещь.'):
        with pytest.raises(HTTPException) as error:
            v2.send_reserved(cx,'SEND',row['draft']+' '+addition,row['source_hash'],row['revision'],'manager',
                             draft_version=row['draft_version'])
        assert error.value.status_code==422
        assert cx.execute("SELECT send_attempts FROM reviews WHERE id='SEND'").fetchone()[0]==0
    answer=v2.send_reserved(cx,'SEND',row['draft'],row['source_hash'],row['revision'],'manager',
                            draft_version=row['draft_version'])
    assert answer['status']=='api_confirmed'
    assert cx.execute("SELECT send_attempts FROM reviews WHERE id='SEND'").fetchone()[0]==1


def test_ai_requires_buyer_detail_and_receives_editorial_draft(case,monkeypatch):
    cx,base=case
    row=review(cx,'ADAPT','После первой стирки платье село')
    p,product,procedure=planned(cx,base,row)
    source=dict(row)
    payload,_=context(source,p,product,'Правка менеджера о первой стирке',plan=procedure,editorial=True)
    assert 'Правка менеджера' in payload['editorial_draft']
    assert 'утверждённый факт' in payload['editorial_rule']
    background,_=context(source,p,product,render(procedure),plan=procedure)
    assert 'editorial_draft' not in background
    monkeypatch.setattr(ai_drafts,'complete',lambda *a,**kw:{
        'opening':'Жаль, что платье изменило форму после стирки.',
        'explanation':procedure['explanation'],
        'action_intro':'',
        'prevention':procedure['prevention'].rstrip('.')+'!',
        'path_intro':''})
    with pytest.raises(ValueError,match='конкретную деталь'):
        generate(source,p,product,plan=procedure)


def pending_model_case(cx, base, monkeypatch, rid='MODEL'):
    row = review(cx, rid, 'После первой стирки платье село')
    p = profile_overlay(cx, base)
    prepare(cx, row, p, 'demo', v2.POLICY)
    cx.execute("UPDATE reviews SET model_status='pending',reason='ИИ готовит ответ' WHERE id=?", (rid,))
    monkeypatch.setattr(v2, 'profile', lambda connection=None:
                        profile_overlay(connection, base) if connection else base)
    return Path(cx.execute('PRAGMA database_list').fetchone()['file'])


def test_model_quota_and_transient_retry_with_fake_clock(case, monkeypatch):
    cx, base = case
    path = pending_model_case(cx, base, monkeypatch)
    calls = []
    def proposal(row, profile, product, existing, settings, plan):
        calls.append('model')
        if len(calls) == 1:raise TimeoutError('synthetic timeout')
        return render(plan), plan['fact_ids']
    day = 10 * 86400 + 100
    assert v2.hermes_step(path, now=day, reserve=lambda *_:False,
                          generate_fn=proposal) == 'limit'
    limited = cx.execute("SELECT * FROM reviews WHERE id='MODEL'").fetchone()
    assert limited['model_attempts'] == 0 and limited['model_retry_at'] > day
    assert v2.hermes_step(path, now=day+1, reserve=lambda *_:True,
                          generate_fn=proposal) == 'idle'
    assert calls == []
    due = limited['model_retry_at']
    assert v2.hermes_step(path, now=due, reserve=lambda *_:True,
                          generate_fn=proposal) == 'retry'
    retry = cx.execute("SELECT * FROM reviews WHERE id='MODEL'").fetchone()
    assert retry['model_attempts'] == 1 and retry['model_retry_at'] == due+30
    assert v2.hermes_step(path, now=due+30, reserve=lambda *_:True,
                          generate_fn=proposal) == 'ready'
    ready = cx.execute("SELECT * FROM reviews WHERE id='MODEL'").fetchone()
    assert ready['model_status'] == 'ready' and ready['model_attempts'] == 2
    assert 'ИИ готовит' not in ready['reason'] and len(calls) == 2


def test_model_content_error_and_late_result_preserve_manual_edit(case, monkeypatch):
    cx, base = case
    path = pending_model_case(cx, base, monkeypatch)
    assert v2.hermes_step(path, now=1000, reserve=lambda *_:True,
                          generate_fn=lambda *a, **k: (_ for _ in ()).throw(ValueError('bad content'))) == 'failed'
    failed = cx.execute("SELECT * FROM reviews WHERE id='MODEL'").fetchone()
    assert failed['model_error'] == 'content' and failed['model_retry_at'] is None
    assert v2.hermes_step(path, now=2000, reserve=lambda *_:True,
                          generate_fn=lambda *a, **k: None) == 'idle'
    cx.execute("UPDATE reviews SET model_status='pending',model_attempts=0 WHERE id='MODEL'")
    def late(row, profile, product, existing, settings, plan):
        cx.execute("UPDATE reviews SET draft='Правка менеджера',manager_edited=1,draft_version=draft_version+1,model_status='edited' WHERE id='MODEL'")
        return render(plan), plan['fact_ids']
    assert v2.hermes_step(path, now=3000, reserve=lambda *_:True,
                          generate_fn=late) == 'discarded'
    row = cx.execute("SELECT * FROM reviews WHERE id='MODEL'").fetchone()
    assert row['draft'] == 'Правка менеджера' and row['model_status'] == 'edited'


def test_model_429_configuration_and_validation_are_distinct(case,monkeypatch):
    import httpx
    cx,base=case
    path=pending_model_case(cx,base,monkeypatch)
    request=httpx.Request('POST','http://example.invalid/model')
    response=httpx.Response(429,request=request)
    throttled=httpx.HTTPStatusError('429',request=request,response=response)
    assert v2.model_failure_kind(throttled)=='transient'
    assert v2.hermes_step(path,now=1000,reserve=lambda *_:True,
        generate_fn=lambda *a,**k:(_ for _ in ()).throw(throttled))=='retry'
    cx.execute("UPDATE reviews SET model_status='pending',model_retry_at=NULL WHERE id='MODEL'")
    assert v2.hermes_step(path,now=1100,reserve=lambda *_:True,
        generate_fn=lambda *a,**k:(_ for _ in ()).throw(RuntimeError('Hermes unavailable: local key not configured')))=='failed'
    assert cx.execute("SELECT model_error FROM reviews WHERE id='MODEL'").fetchone()[0]=='configuration'
    cx.execute("UPDATE reviews SET model_status='pending',model_attempts=0 WHERE id='MODEL'")
    def unsafe(row,profile,product,existing,settings,plan):
        return render(plan)+' Можно отжать вещь.',plan['fact_ids']
    assert v2.hermes_step(path,now=1200,reserve=lambda *_:True,generate_fn=unsafe)=='failed'
    result=cx.execute("SELECT model_error,model_retry_at,draft FROM reviews WHERE id='MODEL'").fetchone()
    assert result['model_error']=='content' and result['model_retry_at'] is None
    assert 'Можно отжать' not in (result['draft'] or '')
