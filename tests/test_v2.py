import json,sqlite3,shutil
from pathlib import Path
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from openpyxl import load_workbook
from app.profile import load
from app.classifier import classify
from app.v2_store import connect,import_review,set_setting,setting
from app.v2_logic import decision_for,prepare,policy_revision
from app import v2
from app.wb import WBError

ROWS={x['id']:x for x in json.loads((v2.ROOT/'data/demo_reviews_v2.json').read_text())}

def test_variant_counts_and_photo_routing():
    assert sum(x['nm_id']==910001 for x in ROWS.values())==20
    assert sum(x['nm_id']==910006 for x in ROWS.values())==5
    p=load()
    positive=dict(ROWS['W01'],photo_urls=['/static/review-photos/cream-front.png'])
    product,d=decision_for(positive,p,'demo',v2.POLICY)
    assert product['product_id']=='TS-01' and d.route=='auto'
    for rid in ('W04','B05','W08'):
        _,d=decision_for(ROWS[rid],p,'demo',v2.POLICY)
        assert d.route=='manual'
    assert decision_for(ROWS['W04'],p,'demo',v2.POLICY)[1].intent=='photo_context'

def test_low_rating_fact_question_stays_manual_with_grounded_draft(tmp_path):
    p=load();cx=connect(tmp_path/'low.sqlite3')
    import_review(cx,{'id':'LOW','nm_id':910001,'rating':1,'body':'Как стирать молочную футболку?','pros':'','cons':'','photo_urls':[]})
    row=cx.execute("SELECT * FROM reviews WHERE id='LOW'").fetchone()
    assert prepare(cx,row,p,'demo',v2.POLICY)=='needs_review'
    prepared=cx.execute("SELECT draft,generator,fact_ids FROM reviews WHERE id='LOW'").fetchone()
    assert 'Машинная стирка' in prepared['draft']
    assert prepared['generator']=='Шаблон · Уход и стирка'
    cx.close()

def test_exact_variant_facts(tmp_path):
    p=load()
    a=p.facts('TS-01');b=p.facts('TS-01B')
    assert a['wash']['value']!=b['wash']['value']
    assert a['composition']['value']!=b['composition']['value']
    assert b['wash']['override_of']==a['wash']['fact_id']
    for rid,fragment in [('W02','Машинная стирка'),('B01','вывернуть наизнанку')]:
        cx=connect(tmp_path/(rid+'.sqlite3'))
        try:
            import_review(cx,ROWS[rid]);row=cx.execute('SELECT * FROM reviews WHERE id=?',(rid,)).fetchone()
            assert prepare(cx,row,p,'demo',v2.POLICY)=='ready_auto'
            assert fragment in cx.execute('SELECT draft FROM reviews WHERE id=?',(rid,)).fetchone()[0]
        finally:cx.close()

def test_pause_and_visible_manual_send(tmp_path,monkeypatch):
    path=tmp_path/'queue.sqlite3';cx=connect(path);p=load()
    monkeypatch.setattr(v2,'profile',lambda cx=None:p)
    set_setting(cx,'processing_enabled','0');set_setting(cx,'auto_send_enabled','1')
    import_review(cx,ROWS['W01']);v2.process_db(path)
    assert cx.execute("SELECT status FROM reviews WHERE id='W01'").fetchone()[0]=='new'
    set_setting(cx,'processing_enabled','1');set_setting(cx,'auto_send_enabled','0');v2.process_db(path)
    row=cx.execute("SELECT * FROM reviews WHERE id='W01'").fetchone()
    assert row['status']=='ready_auto'
    assert row['send_attempts']==0
    set_setting(cx,'auto_send_enabled','1');v2.process_db(path)
    assert cx.execute("SELECT status FROM reviews WHERE id='W01'").fetchone()[0]=='api_confirmed'
    import_review(cx,ROWS['W04']);v2.process_db(path)
    row=cx.execute("SELECT * FROM reviews WHERE id='W04'").fetchone()
    edited='Спасибо, что сообщили о пятне. Нам жаль, что возникла проблема.'
    result=v2.send_reserved(cx,'W04',edited,row['source_hash'],row['revision'],'manager')
    assert result['status']=='api_confirmed'
    sent=cx.execute("SELECT sent_text,approval_kind,send_attempts FROM reviews WHERE id='W04'").fetchone()
    assert sent['sent_text']==edited and sent['approval_kind']=='manager' and sent['send_attempts']==1
    with pytest.raises(HTTPException):v2.send_reserved(cx,'W04',edited,row['source_hash'],row['revision'],'manager')
    cx.close()

def test_profile_change_invalidates_answer(tmp_path,monkeypatch):
    path=tmp_path/'queue.sqlite3';cx=connect(path);p=load();monkeypatch.setattr(v2,'profile',lambda cx=None:p)
    import_review(cx,ROWS['W02']);row=cx.execute("SELECT * FROM reviews WHERE id='W02'").fetchone()
    prepare(cx,row,p,'demo',v2.POLICY)
    row=cx.execute("SELECT * FROM reviews WHERE id='W02'").fetchone()
    p.revision='changed-profile-revision'
    with pytest.raises(HTTPException) as exc:v2.send_reserved(cx,'W02',row['draft'],row['source_hash'],row['revision'],'auto_policy')
    assert exc.value.status_code==409
    assert cx.execute("SELECT send_attempts FROM reviews WHERE id='W02'").fetchone()[0]==0
    cx.close()

def test_sandbox_readback_blocks_duplicate(tmp_path,monkeypatch):
    p=load();x=p.products['TS-01'];x['sandbox_nmID']=990001;x['seller_article']='SL-TS-01-WBTEST'
    monkeypatch.setattr(v2,'MODE','sandbox');monkeypatch.setattr(v2,'profile',lambda cx=None:p)
    cx=connect(tmp_path/'sandbox.sqlite3')
    source=dict(ROWS['W01'],id='T1',nm_id=990001,remote_size='M',remote_article='SL-TS-01-WBTEST')
    import_review(cx,source);row=cx.execute("SELECT * FROM reviews WHERE id='T1'").fetchone();prepare(cx,row,p,'sandbox',v2.POLICY)
    row=cx.execute("SELECT * FROM reviews WHERE id='T1'").fetchone()
    remote={'id':'T1','text':source['body'],'pros':'','cons':'','productValuation':5,'photoLinks':[],
            'answer':None,'productDetails':{'nmId':990001,'size':'M','supplierArticle':'SL-TS-01-WBTEST'}}
    class FakeWB:
        calls=0
        def get(self,rid):return remote.copy()
        def answer(self,rid,text):self.calls+=1;remote['answer']={'text':text,'state':'pending'}
    wb=FakeWB()
    assert v2.send_reserved(cx,'T1',row['draft'],row['source_hash'],row['revision'],'manager',wb)['status']=='api_confirmed'
    assert wb.calls==1
    with pytest.raises(HTTPException):v2.send_reserved(cx,'T1',row['draft'],row['source_hash'],row['revision'],'manager',wb)
    assert wb.calls==1
    cx.close()

def test_readback_429_retries_get_only():
    class FakeWB:
        reads=0
        def get(self,rid):
            self.reads+=1
            if self.reads==1:raise WBError(429)
            return {'id':rid,'answer':{'text':'confirmed'}}
    wb=FakeWB()
    assert v2.readback_with_retry(wb,'T1')['answer']['text']=='confirmed'
    assert wb.reads==2

def test_sandbox_profile_upload_reprepares_unsent(tmp_path,monkeypatch):
    source=tmp_path/'mapped.xlsx'
    book=load_workbook(v2.ROOT/'data/profiles.xlsx')
    for cells in book['Варианты'].iter_rows(min_row=2):
        if cells[0].value=='TS-01':cells[3].value=990001;cells[4].value='SL-TS-01-WBTEST'
    book.save(source)
    shutil.copy2(source,tmp_path/'profiles.xlsx')
    monkeypatch.setattr(v2,'MODE','sandbox');monkeypatch.setattr(v2,'DATA',tmp_path)
    cx=connect(tmp_path/'sandbox.sqlite3')
    row=dict(ROWS['W04'],remote_size='M',remote_article='SL-TS-01-WBTEST',nm_id=990001)
    import_review(cx,row)
    cx.execute("UPDATE reviews SET status='needs_review',draft='Черновик' WHERE id='W04'")
    cx.close()
    client=TestClient(v2.app)
    client.auth=('operator',(v2.ROOT/'.data/operator-key').read_text().strip())
    response=client.put('/api/v2/profiles/file',content=source.read_bytes(),headers={'Content-Type':'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'})
    assert response.status_code==200,response.text
    cx=connect(tmp_path/'sandbox.sqlite3')
    assert cx.execute("SELECT status,draft FROM reviews WHERE id='W04'").fetchone()[:]==('new',None)
    cx.close()
    assert list(tmp_path.glob('profiles-before-*.xlsx'))

def test_defer_leaves_active_queue(tmp_path,monkeypatch):
    monkeypatch.setattr(v2,'MODE','demo');monkeypatch.setattr(v2,'DATA',tmp_path)
    client=TestClient(v2.app)
    before=client.get('/api/v2/state').json()
    assert before['counts']['all']==35
    sid=client.cookies.get('demo_session_v3')
    v2.process_db(tmp_path/(sid+'.sqlite3'))
    prior=client.get('/api/v2/state').json()
    assert client.post('/api/v2/reviews/W04/defer',json={'reason':'Нужно проверить фотографии'}).status_code==200
    after=client.get('/api/v2/state').json()
    assert after['counts']['attention']==prior['counts']['attention']-1
    assert after['counts']['deferred']==1
    assert client.get('/api/v2/state?tab=attention&product_id=TS-01&q=W04').json()['total']==0
    assert client.get('/api/v2/state?tab=deferred&product_id=TS-01&q=W04').json()['total']==1

def test_error_recovery_reads_only_and_safe_requeue(tmp_path,monkeypatch):
    p=load();p.products['TS-01']['sandbox_nmID']=990001;p.products['TS-01']['seller_article']='SL-TS-01-WBTEST'
    monkeypatch.setattr(v2,'MODE','sandbox');monkeypatch.setattr(v2,'DATA',tmp_path);monkeypatch.setattr(v2,'profile',lambda cx=None:p)
    cx=connect(tmp_path/'sandbox.sqlite3')
    import_review(cx,dict(ROWS['W01'],id='UNKNOWN',nm_id=990001,remote_size='M',remote_article='SL-TS-01-WBTEST'))
    import_review(cx,dict(ROWS['W01'],id='NOPOST',nm_id=990001,remote_size='M',remote_article='SL-TS-01-WBTEST'))
    cx.execute("UPDATE reviews SET status='outcome_unknown',sent_text='Точный ответ',send_attempts=1 WHERE id='UNKNOWN'")
    cx.execute("UPDATE reviews SET status='failed',sent_text='Черновик',send_attempts=1,reason='Проверка перед POST не выполнена: WBError' WHERE id='NOPOST'")
    cx.close()
    class FakeWB:
        reads=0
        def get(self,rid):self.reads+=1;return {'id':rid,'answer':{'text':'Точный ответ'}}
    fake=FakeWB();monkeypatch.setattr(v2,'WB',lambda:fake)
    client=TestClient(v2.app);client.auth=('operator',(v2.ROOT/'.data/operator-key').read_text().strip())
    assert client.post('/api/v2/reviews/UNKNOWN/reconcile').json()['status']=='api_confirmed'
    assert fake.reads==1
    assert client.post('/api/v2/reviews/NOPOST/retry-preflight').json()['status']=='needs_review'
    cx=connect(tmp_path/'sandbox.sqlite3')
    assert cx.execute("SELECT send_attempts FROM reviews WHERE id='NOPOST'").fetchone()[0]==0
    cx.close()
