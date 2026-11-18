import json,shutil
from pathlib import Path
import pytest
from fastapi.testclient import TestClient
from app.core import ROOT,db,decide,digest,read_catalog,upsert_review,validate_draft
from app import main
from app.wb import WBError

CASES=json.loads((ROOT/'data/cases.json').read_text())

def test_frozen_40_decisions():
    cat,_=read_catalog();wrong=[]
    for case in CASES:
        d=decide(cat,'demo',{'nm_id':case['nmId'],'rating':case['rating'],'body':case['text'],'pros':case['pros'],'cons':case['cons']})
        if d['route']!=case['expected_route']:wrong.append(case['id'])
        if case['expected_route']=='manual':assert d['recommendations']==[]
        if case['expected_fact_ids']:assert [x['fact_id'] for x in d['facts']]==case['expected_fact_ids']
    assert len(CASES)==40
    assert wrong==[]

def test_catalog_and_draft_rejects_fabrication():
    cat,_=read_catalog();d=decide(cat,'demo',{'nm_id':910001,'rating':4,'body':'Какой состав?','pros':'','cons':''})
    assert d['facts'][0]['fact_id']=='F01'
    with pytest.raises(ValueError):validate_draft({'text':'Гарантируем отсутствие усадки','fact_ids':['F01'],'recommendation_id':None},d,cat)
    with pytest.raises(ValueError):validate_draft({'text':'Состав 97% хлопка','fact_ids':['F01'],'recommendation_id':None},d,cat)
    with pytest.raises(ValueError):validate_draft({'text':'Состав 95% хлопка','fact_ids':['F99'],'recommendation_id':None},d,cat)
    valid=validate_draft({'text':'Состав 95% хлопка, 5% эластан','fact_ids':['F01'],'recommendation_id':None},d,cat)
    assert 'молочный / M: 95% хлопок, 5% эластан' in valid[0]

def test_sandbox_variant_must_match_exactly():
    cat,_=read_catalog();cat['Товары'][0]['sandbox_nmID']=990001;cat['Товары'][0]['seller_article']='SL-TS-01-WBTEST'
    row={'nm_id':990001,'rating':5,'body':'Какой состав?','pros':'','cons':'','remote_size':'L','remote_article':'SL-TS-01-WBTEST'}
    assert decide(cat,'sandbox',row)['route']=='manual'
    row['remote_size']='M';row['remote_article']='OTHER'
    assert decide(cat,'sandbox',row)['route']=='manual'

@pytest.fixture
def sandbox(tmp_path,monkeypatch):
    data=ROOT/'.data'/('pytest-'+tmp_path.name);data.mkdir(parents=True,exist_ok=True)
    shutil.copy(ROOT/'data/catalog.xlsx',data/'catalog.xlsx')
    from openpyxl import load_workbook
    w=load_workbook(data/'catalog.xlsx')
    for row in w['Товары'].iter_rows(min_row=2):
        if row[0].value=='TS-01':row[2].value=990001;row[3].value='SL-TS-01-WBTEST'
    w.save(data/'catalog.xlsx')
    monkeypatch.setattr(main,'MODE','sandbox');monkeypatch.setattr(main,'DATA',data)
    cx=db(data/'sandbox.sqlite3');upsert_review(cx,{'id':'TEST','nm_id':990001,'rating':5,'body':'Спасибо, футболка понравилась!','pros':'','cons':'','remote_size':'M','remote_article':'SL-TS-01-WBTEST'});cx.close()
    item={'id':'TEST','text':'Спасибо, футболка понравилась!','pros':'','cons':'','productValuation':5,'answer':None,'productDetails':{'nmId':990001,'size':'M','supplierArticle':'SL-TS-01-WBTEST'}}
    class FakeWB:
        calls=0; fail=None
        def __init__(self):pass
        def get(self,rid):
            if self.fail=='429':raise WBError(429)
            return item.copy() if rid=='TEST' else None
        def answer(self,rid,text):
            type(self).calls+=1;item['answer']={'text':text,'state':'pending'}
            if self.fail=='timeout':raise TimeoutError()
    monkeypatch.setattr(main,'WB',FakeWB)
    with TestClient(main.app) as client:
        client.auth=('operator',(ROOT/'.data/operator-key').read_text().strip())
        yield client,FakeWB,data
    shutil.rmtree(data)

def setup_approval(client):
    assert client.post('/api/reviews/TEST/generate').status_code==200
    assert client.post('/api/reviews/TEST/approve').status_code==200

def test_send_readback_no_duplicate(sandbox):
    client,wb,_=sandbox;setup_approval(client)
    assert client.post('/api/reviews/TEST/send').json()['status']=='api_confirmed'
    assert client.post('/api/reviews/TEST/send').status_code==409
    assert wb.calls==1

def test_timeout_after_write_reconciles(sandbox):
    client,wb,_=sandbox;setup_approval(client);wb.fail='timeout'
    assert client.post('/api/reviews/TEST/send').json()['status']=='api_confirmed'
    assert wb.calls==1

def test_429_blocks_before_post(sandbox):
    client,wb,_=sandbox;setup_approval(client);wb.fail='429'
    assert client.post('/api/reviews/TEST/send').status_code==502
    assert wb.calls==0

def test_stale_approval_and_wrong_id(sandbox):
    client,wb,data=sandbox;setup_approval(client)
    from openpyxl import load_workbook
    w=load_workbook(data/'catalog.xlsx');w['Факты']['D2']='96% хлопок';w.save(data/'catalog.xlsx')
    assert client.post('/api/reviews/TEST/send').status_code==409
    assert wb.calls==0
    assert client.post('/api/reviews/OTHER/send').status_code==404

def test_foreign_answer_and_changed_review_block_post(sandbox,monkeypatch):
    client,wb,_=sandbox;setup_approval(client)
    original=wb.get
    def answered(self,rid):
        item=original(self,rid);item['answer']={'text':'Чужой ответ'};return item
    monkeypatch.setattr(wb,'get',answered)
    assert client.post('/api/reviews/TEST/send').status_code==409
    assert wb.calls==0

def test_missing_token_keeps_approval(sandbox,monkeypatch):
    client,wb,data=sandbox;setup_approval(client)
    def missing():raise ValueError('missing')
    monkeypatch.setattr(main,'WB',missing)
    assert client.post('/api/reviews/TEST/send').status_code==503
    cx=db(data/'sandbox.sqlite3');row=cx.execute('SELECT status,send_attempts FROM reviews WHERE id=?',('TEST',)).fetchone();cx.close()
    assert row['status']=='approved' and row['send_attempts']==0

def test_changed_remote_review_blocks_post(sandbox,monkeypatch):
    client,wb,_=sandbox;setup_approval(client)
    original=wb.get
    def changed(self,rid):
        item=original(self,rid);item['text']='Текст изменён в WB';return item
    monkeypatch.setattr(wb,'get',changed)
    assert client.post('/api/reviews/TEST/send').status_code==409
    assert wb.calls==0

def test_post_without_readback_is_unknown_and_not_retried(sandbox,monkeypatch):
    client,wb,_=sandbox;setup_approval(client)
    def accepted_without_visible_answer(self,rid,text):type(self).calls+=1
    monkeypatch.setattr(wb,'answer',accepted_without_visible_answer)
    assert client.post('/api/reviews/TEST/send').json()['status']=='outcome_unknown'
    assert client.post('/api/reviews/TEST/send').status_code==409
    assert wb.calls==1

def test_edited_number_requires_source(sandbox):
    client,wb,_=sandbox
    assert client.post('/api/reviews/TEST/generate').status_code==200
    assert client.put('/api/reviews/TEST/draft',json={'text':'Размер 999 подходит всем.'}).status_code==422
    assert client.post('/api/reviews/TEST/approve').status_code==200

def test_auth_and_demo_separation(sandbox,monkeypatch):
    client,wb,_=sandbox
    with TestClient(main.app) as unauth:
        assert unauth.post('/api/reviews/TEST/send').status_code==401
    monkeypatch.setattr(main,'MODE','demo')
    assert client.post('/api/sandbox/sync').status_code==404
