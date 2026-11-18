"""Content contracts on synthetic feedback, with no external model or WB calls."""
import json,sqlite3,time
from pathlib import Path
import pytest
from app.profile import load
from app.v2_store import connect,import_review,setting
from app.v4_store import init,profile_overlay,library_draft,save_library_draft,approve_library_draft
from app.v4_logic import evaluate,prepare
from app.complaint_library import LIBRARY,plan,render,render_ai,validate,validate_ai,validate_adapted
from app.complaint_migration import counts,apply
from app.ai_drafts import generate,context
from app import ai_drafts,v2

@pytest.fixture
def setup(tmp_path):
    p=load(v2.ROOT/'data/profiles_v4.xlsx');cx=connect(tmp_path/'case.sqlite3');init(cx,p)
    yield cx,p
    cx.close()

def feedback(rid,body,product='DR-01',rating=2,pros='',cons=''):
    catalog={'DR-01':(920101,'M','SL-DR-01'),'TS-01':(910001,'M','SL-TS-01'),'TS-01B':(910006,'M','SL-TS-01-B')}
    nm,size,article=catalog[product]
    return {'id':rid,'nm_id':nm,'rating':rating,'body':body,'pros':pros,'cons':cons,
            'photo_urls':[],'remote_size':size,'remote_article':article}

def add(cx,p,key,model):
    scope='variant' if key=='shrink_cotton' and model=='TEE' else 'model'
    sid,version=library_draft(cx,key,scope,'TS-01' if scope=='variant' else model,p)
    product='DR-01' if model=='DRESS' else 'TS-01'
    data={field:LIBRARY[key][field] for field in ('goal','explanation','action','prevention','not_applicable','prohibited','seller_path')}
    data.update(approval_source='Синтетическая инструкция продавца для тестовой модели',
                label_wash=p.facts(product)['wash']['value'] if 'wash' in LIBRARY[key]['required_facts'] else '')
    save_library_draft(cx,sid,version,data);approve_library_draft(cx,sid,version,p)
    return sid,version

def result(cx,p,case):
    import_review(cx,feedback(case['id'],case['text'],case.get('product','DR-01'),case.get('rating',2),cons=case.get('cons','')))
    row=cx.execute('SELECT * FROM reviews WHERE id=?',(case['id'],)).fetchone()
    return evaluate(cx,row,profile_overlay(cx,p),'demo',v2.POLICY),row

CASES=json.loads((v2.ROOT/'data/complaint_control_cases.json').read_text())

def test_twelve_control_answers_and_facts(setup):
    cx,p=setup
    for key,model in [('pilling','DRESS'),('dye_transfer','DRESS'),('seam_damage','DRESS'),('stretched','DRESS'),
                      ('shrink_cotton','TEE'),('size_after','TEE')]:add(cx,p,key,model)
    observed=[]
    for case in CASES:
        outcome,row=result(cx,p,case);text=outcome['text']
        if case.get('blocked'):
            assert text is None and case['blocked'] in outcome['reason']
            observed.append({'id':case['id'],'strategy':case['key'],'template':None,'reason':outcome['reason']})
            continue
        assert outcome['scenario_id'].startswith(case['key'])
        assert case['must'] in text and outcome['status']=='needs_review'
        assert 'проблеме с качеством' not in text and 'Рады, что' not in text
        assert 'артикул WB' not in text and 'уксус' not in text and 'перекис' not in text
        assert len(text)<=700
        observed.append({'id':case['id'],'strategy':case['key'],'template':text})
    assert len(observed)==12
    assert 'соблюдали рекомендации' in observed[1]['template'] and 'Вы описали стирку' in observed[2]['template']
    assert set(result(cx,p,CASES[0])[0]['fact_ids'])=={'D01','D02','D03'}

def test_r056_ai_receives_full_strategy_and_preserves_steps(setup,monkeypatch):
    cx,p=setup;out,row=result(cx,p,CASES[0]);p=profile_overlay(cx,p)
    planned=out['complaint_plan'];source=dict(row);source['intent']='negative'
    payload,facts=context(source,p,p.products['DR-01'],out['text'],plan=planned)
    assert payload['approved_strategy']['action']==planned['action']
    assert {x['field'] for x in payload['approved_strategy']['facts']}=={'composition','wash','dry'}
    monkeypatch.setattr(ai_drafts,'complete',lambda *args,**kwargs:{
        'opening':'Жаль, что платье село после первой стирки.',
        'explanation':'После первой стирки платье изменило форму; для шерстяного трикотажа важен бережный уход.',
        'action_intro':'Попробуйте такой бережный шаг.',
        'prevention':'Для дальнейшего ухода стирайте вручную до 30 °C без отжима и сушите горизонтально в расправленном виде.',
        'path_intro':''})
    text,used=generate(source,p,p.products['DR-01'],out['text'],plan=planned)
    assert planned['action'] in text and planned['not_applicable'] in text and used==planned['fact_ids']
    assert 'платье изменило форму' in text and 'Для дальнейшего ухода' in text
    assert planned['explanation'] not in text and planned['prevention'] not in text
    with pytest.raises(ValueError,match='утверждённая помощь|Объяснение'):
        validate('Как вы стирали платье?',planned,p.products['DR-01'],p)
    with pytest.raises(ValueError):validate(text+' Мы вернём деньги.',planned,p.products['DR-01'],p)

def test_ai_cannot_reverse_care_conditions_even_with_keyword_overlap(setup):
    cx,base=setup;out,row=result(cx,base,CASES[0]);p=profile_overlay(cx,base)
    planned=out['complaint_plan'];review=row['body'];product=p.products['DR-01']
    parts={'opening':'Жаль, что платье изменило форму после стирки.',
           'explanation':'После первой стирки платье изменило форму; для шерстяного трикотажа важен бережный уход.','action_intro':'',
           'prevention':'Дальше стирайте вручную до 30 °C без отжима и сушите горизонтально в расправленном виде.',
           'path_intro':''}
    good=render_ai(planned,parts,review)
    assert validate_ai(good,planned,parts,review,product,p)==good
    assert validate_adapted(good,planned,product,p)==good
    for bad in ('Дальше стирайте вручную до 30 °C с отжимом и сушите вертикально в расправленном виде.',
                'Дальше стирайте вручную от 30 °C без отжима и сушите горизонтально в расправленном виде.',
                'Дальше стирайте вручную до 30 °C без отжима и сушите вертикально в расправленном виде.',
                'Дальше стирайте вручную до 30 °C без отжима и не сушите горизонтально в расправленном виде.',
                'Дальше стирайте вручную до 30 °C без отжима и сушите горизонтально в расправленном виде при 30 °F.'):
        changed={**parts,'prevention':bad}
        with pytest.raises(ValueError):render_ai(planned,changed,review)
        with pytest.raises(ValueError):validate_adapted(good.replace(parts['prevention'],bad),planned,product,p)
    with pytest.raises(ValueError):
        validate_adapted(good.replace(planned['action'],'Сушите вертикально. '+planned['action']),planned,product,p)

def test_verbose_ai_response_is_compacted_without_second_model_call(setup,monkeypatch):
    cx,base=setup;out,row=result(cx,base,CASES[0]);p=profile_overlay(cx,base)
    planned=out['complaint_plan'];calls=[]
    def verbose(*args,**kwargs):
        calls.append(1)
        return {'opening':'Жаль, что платье село после первой стирки. Понимаем, что это неприятно.',
                'explanation':'После первой стирки платье изменило форму; для шерстяного трикотажа важен бережный уход. Ситуация заслуживает внимания и бережного подхода.',
                'action_intro':'Чтобы попробовать помочь вещи сейчас, предлагаем следующий осторожный порядок действий.',
                'prevention':'Дальше стирайте вручную до 30 °C без отжима и сушите горизонтально в расправленном виде. Это поможет внимательнее соблюдать уход за моделью.',
                'path_intro':'Если понадобится другое решение, есть оформляемый через заказ путь.'}
    monkeypatch.setattr(ai_drafts,'complete',verbose)
    text,_=generate(dict(row),p,p.products['DR-01'],plan=planned)
    assert len(calls)==1 and len(text)<=700 and planned['action'] in text
    assert 'до 30 °C без отжима' in text and 'сушите горизонтально' in text

def test_long_compliant_explanation_keeps_relevant_clause(setup,monkeypatch):
    cx,base=setup;out,row=result(cx,base,CASES[1]);p=profile_overlay(cx,base)
    planned=out['complaint_plan']
    raw={'opening':'Жаль, что платье изменило форму после стирки вручную.',
         'explanation':'Для этой шерстяной модели после стирки действительно возможно изменение формы, поэтому аккуратный уход уменьшает такие риски, даже если стирка была при 30 градусах без отжима.',
         'action_intro':'','prevention':planned['prevention'],'path_intro':''}
    monkeypatch.setattr(ai_drafts,'complete',lambda *args,**kwargs:raw)
    with pytest.raises(ValueError,match='неподтверждённое общее утверждение'):
        generate(dict(row),p,p.products['DR-01'],plan=planned)
    raw['explanation']='Вы пишете, что стирали вручную при 30 градусах без отжима, но шерстяное платье изменило форму.'
    answer,_=generate(dict(row),p,p.products['DR-01'],plan=planned)
    assert len(answer)<=700 and 'стирали вручную при 30 градусах без отжима' in answer
    assert planned['action'] in answer and planned['not_applicable'] in answer

def test_draft_edit_does_not_change_active_until_approval(setup,monkeypatch):
    cx,base=setup;before,_=result(cx,base,CASES[0]);sid,v=library_draft(cx,'shrink_wool','model','DRESS',base)
    data={field:LIBRARY['shrink_wool'][field] for field in ('goal','explanation','action','prevention','not_applicable','prohibited','seller_path')}
    data['explanation']='Шерстяное платье могло изменить форму после стирки; причина конкретного случая не установлена.'
    data['action']='Если ярлык допускает ручное смачивание, разложите влажное изделие на полотенце и осторожно придайте форму. Не растягивайте с усилием.'
    data['not_applicable']='Если вещь свалялась или ярлык запрещает смачивание, пропустите этот способ и обратитесь по заказу.'
    data.update(approval_source='Новая синтетическая инструкция продавца',label_wash=base.facts('DR-01')['wash']['value'])
    save_library_draft(cx,sid,v,data)
    unchanged,_=result(cx,base,CASES[0]);assert unchanged['text']==before['text']
    approve_library_draft(cx,sid,v,base)
    after,row=result(cx,base,CASES[0]);assert all(data[x] in after['text'] for x in ('explanation','action','not_applicable')) and before['text']!=after['text']
    monkeypatch.setattr(ai_drafts,'complete',lambda *args,**kwargs:{
        'opening':'Жаль, что платье село после стирки.',
        'explanation':'После первой стирки шерстяное платье могло изменить форму; причина конкретного случая не установлена.',
        'action_intro':'',
        'prevention':'Для дальнейшего ухода стирайте вручную до 30 °C без отжима и сушите горизонтально в расправленном виде.',
        'path_intro':''})
    src=dict(row);src['intent']='negative';p=profile_overlay(cx,base)
    ai,_=generate(src,p,p.products['DR-01'],plan=after['complaint_plan'])
    assert all(data[x] in ai for x in ('action','not_applicable')) and 'После первой стирки' in ai

def test_goal_path_and_quoted_prohibition_change_public_contract(setup):
    cx,base=setup;before,row=result(cx,base,CASES[0])
    sid,v=library_draft(cx,'shrink_wool','model','DRESS',base)
    data={field:LIBRARY['shrink_wool'][field] for field in ('goal','explanation','action','prevention','not_applicable','prohibited','seller_path')}
    data['goal']='Разобраться в ситуации с изменившимся размером'
    data['seller_path']='Если способ не подойдёт, подайте заявку через заказ в Wildberries.'
    data['prohibited']='Не писать «исправим размер».'
    data.update(approval_source='Синтетическая инструкция продавца',label_wash=base.facts('DR-01')['wash']['value'])
    save_library_draft(cx,sid,v,data)
    unchanged,_=result(cx,base,CASES[0]);assert unchanged['text']==before['text']
    approve_library_draft(cx,sid,v,base)
    after,row=result(cx,base,CASES[0]);p=profile_overlay(cx,base);planned=after['complaint_plan']
    assert after['text'].startswith('Понимаем, что важно разобраться')
    assert after['text'].endswith(data['seller_path'])
    payload,_=context(dict(row),p,p.products['DR-01'],plan=planned)
    assert payload['approved_strategy']['goal']==data['goal']
    assert payload['approved_strategy']['prohibited']==data['prohibited']
    with pytest.raises(ValueError,match='запрещённую формулировку'):
        validate_adapted(after['text']+' исправим размер',planned,p.products['DR-01'],p)

def test_variant_overrides_model_and_disabled_scope_blocks_it(setup):
    cx,base=setup
    model,_=result(cx,base,CASES[0]);sid,v=library_draft(cx,'shrink_wool','variant','DR-01',base)
    data={field:LIBRARY['shrink_wool'][field] for field in ('goal','explanation','action','prevention','not_applicable','prohibited','seller_path')}
    data['action']='Если ярлык допускает смачивание, увлажните изделие и аккуратно расправьте его на полотенце. Не растягивайте с усилием.'
    data.update(approval_source='Синтетическая инструкция для варианта DR-01',label_wash=base.facts('DR-01')['wash']['value'])
    save_library_draft(cx,sid,v,data)
    unchanged,_=result(cx,base,CASES[0]);assert unchanged['text']==model['text']
    approve_library_draft(cx,sid,v,base)
    selected,_=result(cx,base,CASES[0]);assert selected['scenario_id']==sid and data['action'] in selected['text']
    cx.execute("UPDATE v4_scenarios SET status='disabled' WHERE scenario_id=? AND version=?",(sid,v))
    blocked,_=result(cx,base,CASES[0]);assert blocked['text'] is None and 'Нет утверждённой процедуры' in blocked['reason']

def test_missing_procedure_and_wrong_material_are_not_fabricated(setup):
    cx,p=setup
    cx.execute("UPDATE v4_scenarios SET status='disabled' WHERE scenario_id='shrink_wool:model:DRESS'")
    outcome,_=result(cx,p,CASES[0]);assert outcome['text'] is None and 'Нет утверждённой процедуры' in outcome['reason']
    add(cx,p,'shrink_cotton','TEE')
    wool_row=feedback('WOOL-ONLY','После стирки платье село')
    import_review(cx,wool_row);row=cx.execute("SELECT * FROM reviews WHERE id='WOOL-ONLY'").fetchone()
    assert plan(cx,row,profile_overlay(cx,p),p.products['DR-01'])[0] is None

def test_mixed_and_unknown_complaints_do_not_get_invented_recovery(setup):
    cx,p=setup
    mixed,_=result(cx,p,{'id':'MIXED','text':'Платье село и разошёлся шов'})
    assert mixed['text'] is None and 'Смешанная претензия' in mixed['reason']
    unknown,_=result(cx,p,{'id':'UNKNOWN','text':'Платье неприятно пахнет'})
    assert 'увлажните' not in (unknown['text'] or '')
    assert unknown['status']=='needs_review'
    conflict,_=result(cx,p,{'id':'CONFLICT','text':'Стирала вручную при 30 без отжима, но с полотенцами при 60, платье село'})
    assert conflict['complaint_plan']['branch']=='unknown'
    assert 'соблюдали режим' not in conflict['text'] and 'Описанный режим' not in conflict['text']

def test_preview_keeps_template_when_ai_fails_or_budget_ends(setup,monkeypatch):
    from app import v4_api
    from app.v4_api import LibraryPreview
    cx,p=setup
    result(cx,p,CASES[0])
    path=Path(cx.execute('PRAGMA database_list').fetchone()['file'])
    class Demo:
        MODE='demo'
        @staticmethod
        def profile(connection):return profile_overlay(connection,p)
        @staticmethod
        def get_row(connection,rid):return connection.execute('SELECT * FROM reviews WHERE id=?',(rid,)).fetchone()
        @staticmethod
        def reserve_hermes(connection,mode):return False
    monkeypatch.setattr(v4_api,'db',lambda request:connect(path))
    monkeypatch.setattr(v4_api,'core',lambda:Demo)
    body=LibraryPreview(scenario_id='shrink_wool:model:DRESS',version=1,review_id='R056',live_ai=True)
    limited=v4_api.preview_library(body,None)
    assert limited['ai'] is None and 'Лимит' in limited['ai_error'] and 'увлажните' in limited['template']
    monkeypatch.setattr(Demo,'reserve_hermes',staticmethod(lambda connection,mode:True))
    def fail(*args,**kwargs):raise OSError('synthetic network failure')
    monkeypatch.setattr(v4_api,'generate',fail)
    failed=v4_api.preview_library(body,None)
    assert failed['ai'] is None and 'OSError' in failed['ai_error'] and failed['template']==limited['template']

def test_migration_preserves_manual_and_sent_and_is_idempotent(setup):
    cx,p=setup
    for rid in ('MACHINE','MANUAL','SENT'):
        import_review(cx,feedback(rid,'После стирки платье село'))
        row=cx.execute('SELECT * FROM reviews WHERE id=?',(rid,)).fetchone();prepare(cx,row,profile_overlay(cx,p),'demo',v2.POLICY)
    cx.execute("UPDATE reviews SET manager_edited=1,draft='Ручная правка продавца',approval_kind='manager' WHERE id='MANUAL'")
    cx.execute("UPDATE reviews SET status='api_confirmed',sent_text='Ранее отправлено',send_attempts=1 WHERE id='SENT'")
    before=counts(cx);assert before['machine_to_reprepare']==1 and before['manual_to_recheck']==1
    first=apply(cx,p);second=apply(cx,p)
    assert first['applied'] and not second['applied']
    assert cx.execute("SELECT status,draft FROM reviews WHERE id='MACHINE'").fetchone()[:]==('new',None)
    assert cx.execute("SELECT status,draft FROM reviews WHERE id='MANUAL'").fetchone()[:]==('needs_review','Ручная правка продавца')
    assert cx.execute("SELECT status,sent_text FROM reviews WHERE id='SENT'").fetchone()[:]==('api_confirmed','Ранее отправлено')
    assert cx.execute('SELECT COUNT(*) FROM review_history').fetchone()[0]==2

def test_scenario_approval_reprepares_machine_and_keeps_manual_sendable(setup,monkeypatch):
    from app import v4_api
    from app.v4_api import RefreshReview
    cx,base=setup
    monkeypatch.setattr(v2,'profile',lambda connection=None:profile_overlay(connection,base) if connection else base)
    for rid in ('MACHINE','MANUAL','SENT'):
        import_review(cx,feedback(rid,'После первой стирки платье село'))
        row=cx.execute('SELECT * FROM reviews WHERE id=?',(rid,)).fetchone()
        prepare(cx,row,profile_overlay(cx,base),'demo',v2.POLICY)
    old_machine=cx.execute("SELECT * FROM reviews WHERE id='MACHINE'").fetchone()
    old_manual=cx.execute("SELECT * FROM reviews WHERE id='MANUAL'").fetchone()
    cx.execute("UPDATE reviews SET manager_edited=1,generator='Правка менеджера',draft=?,approval_kind='manager' WHERE id='MANUAL'",(old_manual['draft'],))
    cx.execute("UPDATE reviews SET status='api_confirmed',sent_text='Ранее отправлено',send_attempts=1 WHERE id='SENT'")
    cx.commit()
    v2.process_db(Path(cx.execute('PRAGMA database_list').fetchone()['file']))
    sid,version=library_draft(cx,'shrink_wool','model','DRESS',base)
    data={key:LIBRARY['shrink_wool'][key] for key in ('goal','explanation','action','prevention','not_applicable','prohibited','seller_path')}
    data['explanation']='После стирки шерстяной трикотаж иногда меняет форму.'
    data.update(approval_source='Синтетическая инструкция продавца',label_wash=base.facts('DR-01')['wash']['value'])
    save_library_draft(cx,sid,version,data);approve_library_draft(cx,sid,version,base);cx.commit()
    v2.process_db(Path(cx.execute('PRAGMA database_list').fetchone()['file']))
    machine=cx.execute("SELECT * FROM reviews WHERE id='MACHINE'").fetchone()
    manual=cx.execute("SELECT * FROM reviews WHERE id='MANUAL'").fetchone()
    sent=cx.execute("SELECT * FROM reviews WHERE id='SENT'").fetchone()
    assert machine['draft']!=old_machine['draft'] and data['explanation'] in machine['draft']
    assert machine['scenario_version']==version and machine['revision']!=old_machine['revision']
    assert manual['draft']==old_manual['draft'] and manual['revision'] is None and manual['model_status']=='stale'
    assert sent['sent_text']=='Ранее отправлено' and sent['status']=='api_confirmed'
    assert cx.execute("SELECT COUNT(*) FROM review_history WHERE review_id IN ('MACHINE','MANUAL')").fetchone()[0]==2
    path=Path(cx.execute('PRAGMA database_list').fetchone()['file'])
    monkeypatch.setattr(v4_api,'db',lambda request:connect(path))
    refreshed=v4_api.refresh_review('MANUAL',RefreshReview(source_hash=manual['source_hash'],draft_version=manual['draft_version']),None)
    assert refreshed['draft']==old_manual['draft']
    latest=cx.execute("SELECT * FROM reviews WHERE id='MANUAL'").fetchone()
    assert latest['revision'] and latest['revision']!=old_manual['revision']
    outcome=v2.send_reserved(cx,'MANUAL',latest['draft'],latest['source_hash'],latest['revision'],'manager',draft_version=latest['draft_version'])
    assert outcome['status']=='api_confirmed'

def test_ai_answer_passes_send_boundary_in_demo(setup,monkeypatch):
    cx,base=setup;out,row=result(cx,base,CASES[0]);p=profile_overlay(cx,base)
    prepare(cx,row,p,'demo',v2.POLICY)
    row=cx.execute("SELECT * FROM reviews WHERE id='R056'").fetchone()
    planned,_=plan(cx,row,p,p.products['DR-01'])
    monkeypatch.setattr(ai_drafts,'complete',lambda *args,**kwargs:{
        'opening':'Жаль, что платье село после первой стирки.',
        'explanation':'После первой стирки платье изменило форму; для шерстяного трикотажа важен бережный уход.',
        'action_intro':'',
        'prevention':'Для дальнейшего ухода стирайте вручную до 30 °C без отжима и сушите горизонтально в расправленном виде.',
        'path_intro':''})
    text,used=generate(dict(row),p,p.products['DR-01'],plan=planned)
    cx.execute("UPDATE reviews SET draft=?,generator='ИИ · объяснение и уход адаптированы, процедура утверждена',fact_ids=?,model_status='ready' WHERE id='R056'",
               (text,json.dumps(used)));cx.commit()
    monkeypatch.setattr(v2,'profile',lambda connection=None:profile_overlay(connection,base) if connection else base)
    final=v2.send_reserved(cx,'R056',text,row['source_hash'],row['revision'],'manager',draft_version=row['draft_version'])
    assert final['status']=='api_confirmed'
    assert cx.execute("SELECT sent_text FROM reviews WHERE id='R056'").fetchone()[0]==text

def test_independent_live_frequency_failure_is_rejected_at_generation_and_send(setup,monkeypatch):
    saved=json.loads((v2.ROOT/'tests/fixtures/rejected-answer.json').read_text())
    assert 'часто меняет форму' in saved['rephrase']['text']
    cx,base=setup;out,row=result(cx,base,CASES[0]);p=profile_overlay(cx,base)
    prepare(cx,row,p,'demo',v2.POLICY)
    row=cx.execute("SELECT * FROM reviews WHERE id='R056'").fetchone()
    planned,_=plan(cx,row,p,p.products['DR-01'])
    proposed={'opening':'Понимаем ваше расстройство из-за усадки платья.',
              'explanation':'Шерстяной трикотаж часто меняет форму после стирки, что могло произойти и с вашим изделием после первой стирки.',
              'action_intro':'','prevention':planned['prevention'],'path_intro':''}
    monkeypatch.setattr(ai_drafts,'complete',lambda *args,**kwargs:proposed)
    with pytest.raises(ValueError,match='частоту'):
        generate(dict(row),p,p.products['DR-01'],plan=planned)
    for changed in ('обычно','всегда','никогда','иногда'):
        with pytest.raises(ValueError,match='частоту'):
            render_ai(planned,{**proposed,'explanation':proposed['explanation'].replace('часто',changed)},row['body'])
    with pytest.raises(ValueError,match='общее утверждение'):
        render_ai(planned,{**proposed,'explanation':'Шерстяной трикотаж может изменить форму после стирки; для платья важен бережный уход.'},row['body'])
    with pytest.raises(ValueError,match='процент'):
        render_ai(planned,{**proposed,'explanation':'Для шерстяного трикотажа важен бережный уход, иначе он садится в 80% случаев.'},row['body'])
    monkeypatch.setattr(v2,'profile',lambda connection=None:profile_overlay(connection,base) if connection else base)
    with pytest.raises(v2.HTTPException) as rejected:
        v2.send_reserved(cx,'R056',saved['rephrase']['text'],row['source_hash'],row['revision'],'manager',draft_version=row['draft_version'])
    assert rejected.value.status_code==422 and 'частоту' in rejected.value.detail
    untouched=cx.execute("SELECT status,send_attempts FROM reviews WHERE id='R056'").fetchone()
    assert untouched['status']=='needs_review' and untouched['send_attempts']==0

def test_only_untouched_demo_seed_gets_new_care_explanation(setup):
    from app.v4_store import init
    cx,base=setup;sid='shrink_wool:model:DRESS'
    row=cx.execute('SELECT data FROM v4_scenarios WHERE scenario_id=? AND version=1',(sid,)).fetchone()
    old=json.loads(row['data']);old['explanation']='Шерстяной трикотаж может изменить форму после стирки.'
    cx.execute('UPDATE v4_scenarios SET data=? WHERE scenario_id=? AND version=1',(json.dumps(old,ensure_ascii=False),sid))
    init(cx,base);init(cx,base)
    versions=cx.execute('SELECT version,data FROM v4_scenarios WHERE scenario_id=? ORDER BY version',(sid,)).fetchall()
    assert [row['version'] for row in versions]==[1,2]
    assert json.loads(versions[-1]['data'])['explanation']==LIBRARY['shrink_wool']['explanation']
    cx.execute('UPDATE v4_scenarios SET status=? WHERE scenario_id=? AND version=2',('disabled',sid))
    init(cx,base)
    assert cx.execute('SELECT MAX(version) FROM v4_scenarios WHERE scenario_id=?',(sid,)).fetchone()[0]==2
