"""V4 workflow invariants on isolated synthetic SQLite and XLSX data."""
import copy,json,subprocess,sys,time
from pathlib import Path
import pytest
from fastapi import HTTPException
from openpyxl import load_workbook
from app.core import ROOT
from app.profile import load
from app.v2_store import connect,import_review
from app.v2_logic import policy_revision
from app.v4_store import init,profile_overlay,config,save_config,draft_fact,apply_fact,rollback_fact,active_facts,scenarios,save_scenario,save_campaign,campaigns,candidates,seed_candidates
from app.v4_logic import evaluate,prepare,select_campaign,validate_composed
from app import v2

@pytest.fixture
def setup(tmp_path):
    target=tmp_path/'profiles.xlsx'
    subprocess.run([sys.executable,str(ROOT/'scripts/make_v4_profile.py'),'--out',str(target)],cwd=ROOT,check=True,capture_output=True)
    p=load(target);cx=connect(tmp_path/'reviews.sqlite3');init(cx,p)
    seed_candidates(cx,json.loads((ROOT/'data/upsell_catalog_v4.json').read_text()))
    yield cx,p
    cx.close()

def row(fid,rating,body,**kwargs):
    return {'id':fid,'nm_id':920101,'rating':rating,'body':body,'pros':'','cons':'',
            'photo_urls':[],'remote_size':'M','remote_article':'SL-DR-01','user_name':'Анна',**kwargs}

def test_settings_scope_persistence_preview_and_backlog(setup):
    cx,base=setup
    import_review(cx,row('POS',5,'Очень мягкое платье, понравилось!'))
    r=cx.execute("SELECT * FROM reviews WHERE id='POS'").fetchone()
    initial=evaluate(cx,r,profile_overlay(cx,base),'demo',v2.POLICY)
    assert initial['status']=='ready_auto'
    save_config(cx,{'send_mode':'review_all','tone':'warm','length':'medium','use_name':True},'product','DR-01')
    effective=config(cx,base.products['DR-01'])
    assert effective['send_mode']=='review_all' and config(cx,base.products['SU-01'])['send_mode']=='rules'
    changed=evaluate(cx,r,profile_overlay(cx,base),'demo',v2.POLICY)
    assert changed['status']=='needs_review' and changed['text'].startswith('Анна, благодарим')
    assert 'вопрос в карточке' in changed['text']
    path=Path(cx.execute('PRAGMA database_list').fetchone()['file'])
    reread=connect(path)
    assert config(reread,base.products['DR-01'])['send_mode']=='review_all'
    assert effective['resolved_scope']['id']=='DR-01'
    reread.close()

def test_fact_draft_approval_and_rollback_are_versioned(setup):
    cx,base=setup
    key='model:DRESS:wash';old=next(x for x in active_facts(cx) if x['field']=='wash' and x['owner_id']=='DRESS')
    new_key,version=draft_fact(cx,{'scope':'model','owner_id':'DRESS','field':'wash','value':'Только ручная стирка без отжима','source':'Инструкция продавца','excerpt':'Только ручная стирка без отжима'},base)
    assert key==new_key and next(x for x in active_facts(cx) if x['field']=='wash' and x['owner_id']=='DRESS')['value']==old['value']
    approved=apply_fact(cx,key,version,base)
    assert approved['status']=='approved' and profile_overlay(cx,base).facts('DR-01')['wash']['value']=='Только ручная стирка без отжима'
    restored=rollback_fact(cx,key,1,base)
    assert restored['version']>version and profile_overlay(cx,base).facts('DR-01')['wash']['value']==old['value']

def test_campaign_selection_and_commercial_validation(setup):
    cx,base=setup
    import_review(cx,row('POS',5,'Очень мягкое платье, понравилось!'))
    r=cx.execute("SELECT * FROM reviews WHERE id='POS'").fetchone()
    c=campaigns(cx)[0];data={k:v for k,v in c.items() if k not in ('id','version')};data['status']='active';data['starts_at']=time.time()-3600;data['ends_at']=time.time()+3600
    save_campaign(cx,c['id'],data,c['version'])
    for x in candidates(cx):
        cx.execute('UPDATE v4_candidates SET data=json_set(data,"$.snapshot_at",?) WHERE candidate_id=?',(time.time(),x['id']))
    p=profile_overlay(cx,base);selection=select_campaign(cx,r,p.products['DR-01'],'demo')
    assert selection['candidate_count']==12 and selection['eligible_count']>=2 and len(selection['selected'])==2
    assert all(x['size']=='M' and x['stock_for_size']>=1 for x in selection['selected'])
    result=evaluate(cx,r,p,'demo',v2.POLICY)
    assert 'артикул WB' in result['text'] and validate_composed(result['text'],result['product'],p,'feedback',selection)
    with pytest.raises(ValueError):validate_composed(result['text'].replace(str(selection['selected'][0]['article']),'999999'),result['product'],p,'feedback',selection)
    complaint=row('BAD',5,'После стирки платье село, но оценка пять')
    import_review(cx,complaint);bad=cx.execute("SELECT * FROM reviews WHERE id='BAD'").fetchone()
    assert not select_campaign(cx,bad,p.products['DR-01'],'demo')['selected']
    data['status']='paused';save_campaign(cx,c['id'],data,2)
    assert not select_campaign(cx,r,p.products['DR-01'],'demo')['selected']

def test_question_without_rating_never_enters_campaign(setup):
    from app.v2_store import import_question
    cx,base=setup
    import_question(cx,{'id':'DEMO-QUESTION','nm_id':920101,'body':'Как ухаживать за платьем?',
                        'remote_article':'SL-DR-01','user_name':'Ольга'})
    question=cx.execute("SELECT * FROM reviews WHERE id='q:DEMO-QUESTION'").fetchone()
    assert question['rating']==0 and 'rating' not in v2.public_row(question,profile_overlay(cx,base),cx)
    selection=select_campaign(cx,question,profile_overlay(cx,base).products['DR-01'],'demo')
    assert not selection['selected'] and selection['reason']=='Вопрос: допродажа исключена'

def test_multi_size_card_requires_exact_review_size(tmp_path):
    book=load_workbook(ROOT/'data/profiles.xlsx');sheet=book['Варианты']
    original=next(r for r in list(sheet.values)[1:] if r[0]=='TS-01')
    extra=list(original);extra[0]='TS-01-L';extra[6]='L';sheet.append(extra)
    path=tmp_path/'profiles.xlsx';book.save(path);p=load(path)
    good={'nm_id':910001,'remote_size':'L','remote_article':original[4]}
    assert p.match('demo',good)[0]['product_id']=='TS-01-L'
    assert p.match('demo',{**good,'remote_size':None})[0] is None
    q=p.match_question('demo',{'nm_id':910001,'remote_article':original[4]})[0]
    assert q is not None and 'size' not in p.question_facts(q['product_id'])

def test_article_filter_combines_sizes_but_keeps_exact_review_match(tmp_path,monkeypatch):
    from app.v4_api import summary
    book=load_workbook(ROOT/'data/profiles_v4.xlsx');sheet=book['Варианты']
    original=next(r for r in list(sheet.values)[1:] if r[0]=='TS-01')
    extra=list(original);extra[0]='TS-01-L';extra[6]='L';sheet.append(extra)
    path=tmp_path/'profiles.xlsx';book.save(path);p=load(path)
    db=tmp_path/'inbox.sqlite3';cx=connect(db);init(cx,p)
    for rid,size in (('S-M','M'),('S-L','L')):
        import_review(cx,dict(row(rid,5,'Хорошая футболка',nm_id=910001),
                              remote_size=size,remote_article=original[4]))
    import_review(cx,dict(row('S-XL',5,'Размер не подтверждён',nm_id=910001),
                          remote_size='XL',remote_article=original[4]))
    assert p.match('demo',cx.execute("SELECT * FROM reviews WHERE id='S-M'").fetchone())[0]['product_id']=='TS-01'
    assert p.match('demo',cx.execute("SELECT * FROM reviews WHERE id='S-L'").fetchone())[0]['product_id']=='TS-01-L'
    assert p.match('demo',cx.execute("SELECT * FROM reviews WHERE id='S-XL'").fetchone())[0] is None
    monkeypatch.setattr(v2,'profile',lambda connection=None:p)
    monkeypatch.setattr(v2,'db_for',lambda request:connect(db))
    combined=v2.state(None,tab='all',product_id='TS-01',kind='feedback')
    assert combined['total']==2 and {item['id'] for item in combined['reviews']}=={'S-M','S-L'}
    card=next(item for item in combined['variants'] if item['product']['product_id']=='TS-01')
    assert card['sizes']==['M','L'] and card['counts']['all']==2
    assert sum(item['product']['color']=='молочный' for item in combined['variants'])==1
    assert v2.state(None,tab='all',product_id='TS-01-L',kind='feedback')['total']==2
    assert summary(cx,p,'feedback','TS-01')['received']==2
    assert summary(cx,p,'feedback','TS-01-L')['received']==2
    cx.close()

def test_100_review_fixture_has_photos_and_distinct_ids():
    rows=json.loads((ROOT/'data/demo_reviews_v4.json').read_text())
    assert len(rows)==100 and len({x['id'] for x in rows})==100
    assert all(x['nm_id']==920101 for x in rows)
    assert any(len(x.get('photo_urls',[]))>1 for x in rows)

def test_quality_signal_creates_task_without_changing_facts(setup,monkeypatch):
    from app import v4_proposals
    cx,base=setup
    for i in range(3):
        import_review(cx,row(f'QUALITY-{i}',2,'Появились катышки после носки.'))
        raw=cx.execute('SELECT * FROM reviews WHERE id=?',(f'QUALITY-{i}',)).fetchone()
        prepare(cx,raw,profile_overlay(cx,base),'demo',v2.POLICY)
    monkeypatch.setattr(v4_proposals,'complete',lambda *args,**kwargs:{'proposals':[]})
    before=len(active_facts(cx));created=v4_proposals.analyze(cx,'demo',profile_overlay(cx,base),v2.POLICY,'DR-01',time.time()-86400,time.time()+1)
    signal=next(x for x in created if x['type']=='quality_signal')
    assert signal['group_count']==3 and all(x['id'].startswith('QUALITY-') for x in signal['evidence'])
    assert v4_proposals.accept(cx,signal['id'],profile_overlay(cx,base))==0
    assert cx.execute('SELECT COUNT(*) FROM v4_tasks WHERE source_proposal_id=?',(signal['id'],)).fetchone()[0]==1
    assert len(active_facts(cx))==before
    with pytest.raises(ValueError):v4_proposals.accept(cx,signal['id'],profile_overlay(cx,base))

def test_sandbox_question_preflight_keeps_user_metadata(setup,monkeypatch):
    from app.v2_store import import_question
    from app.wb import remote_question
    cx,base=setup
    base.products['TS-01']['sandbox_nmID']=990001
    base.products['TS-01']['seller_article']='SL-TS-01-WBTEST'
    monkeypatch.setattr(v2,'MODE','sandbox')
    monkeypatch.setattr(v2,'profile',lambda cx=None:profile_overlay(cx,base) if cx is not None else base)
    remote={'id':'OWN-Q','text':'Как стирать эту модель?','userName':'Тестовый покупатель',
            'createdDate':'2026-11-17T10:00:00Z','answer':None,
            'productDetails':{'nmId':990001,'supplierArticle':'SL-TS-01-WBTEST'}}
    import_question(cx,remote_question(remote))
    raw=cx.execute("SELECT * FROM reviews WHERE id='q:OWN-Q'").fetchone()
    prepare(cx,raw,profile_overlay(cx,base),'sandbox',v2.POLICY)
    cx.execute("UPDATE reviews SET audience='public',draft_version=draft_version+1 WHERE id='q:OWN-Q'")
    raw=cx.execute("SELECT * FROM reviews WHERE id='q:OWN-Q'").fetchone()
    class FakeWB:
        calls=0
        def get_question(self,rid):return remote.copy()
        def answer_question(self,rid,text):self.calls+=1;remote['answer']={'text':text,'state':'wbRu'}
    wb=FakeWB()
    assert v2.send_reserved(cx,raw['id'],raw['draft'],raw['source_hash'],raw['revision'],'manager',wb,
                            audience='public',draft_version=raw['draft_version'])['status']=='api_confirmed'
    assert wb.calls==1
