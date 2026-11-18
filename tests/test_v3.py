import json
from pathlib import Path
import pytest
from fastapi.testclient import TestClient
from app import v2
from app.profile import load
from app.v2_store import connect,import_review,import_question,observe_remote
from app.v2_logic import prepare
from app.templates import render,validate_definition
from app.ai_drafts import saved_for
from app.v2_logic import policy_revision

def question(qid='SAME',nm_id=910001,body='Выбираю между M и L. Какой размер лучше?'):
    return {'id':qid,'nm_id':nm_id,'body':body}

def feedback(fid='SAME',rating=2,body='После стирки футболка села.',**extra):
    return {'id':fid,'nm_id':910001,'rating':rating,'body':body,'pros':'','cons':'','photo_urls':[],**extra}

def test_source_type_is_not_inferred_from_question_mark(tmp_path):
    cx=connect(tmp_path/'x.sqlite3');p=load()
    import_review(cx,feedback(body='Как стирать эту футболку?'))
    import_question(cx,question())
    assert {r['id'] for r in cx.execute('SELECT id FROM reviews')}=={'SAME','q:SAME'}
    for r in cx.execute('SELECT * FROM reviews').fetchall():prepare(cx,r,p,'demo',v2.POLICY)
    assert cx.execute("SELECT kind FROM reviews WHERE id='SAME'").fetchone()[0]=='feedback'
    q=cx.execute("SELECT * FROM reviews WHERE id='q:SAME'").fetchone()
    assert q['kind']=='question' and q['rating']==0 and q['remote_size'] is None
    assert v2.public_row(q,p).get('rating') is None
    assert v2.public_row(q,p).get('remote_size') is None
    cx.close()

def test_question_common_facts_and_conditional_size_rule(tmp_path):
    p=load();cx=connect(tmp_path/'q.sqlite3')
    for q in [question('WASH_TEE',body='Как стирать эту футболку?'),
              question('WASH_CARD',910005,'Как стирать эту модель?'),
              question('SIZE_UNKNOWN'),
              question('SIZE_LOOSE',body='Выбираю между M и L, предпочитаю свободную посадку.')]:
        import_question(cx,q)
    for r in cx.execute('SELECT * FROM reviews').fetchall():prepare(cx,r,p,'demo',v2.POLICY)
    rows={r['remote_id']:r for r in cx.execute('SELECT * FROM reviews')}
    assert rows['WASH_TEE']['intent']=='unknown_fact' and 'цвет и размер' in rows['WASH_TEE']['draft']
    assert rows['WASH_CARD']['intent']=='direct_fact' and 'Ручная деликатная' in rows['WASH_CARD']['draft']
    assert rows['SIZE_UNKNOWN']['intent']=='size_clarify' and 'уточните' in rows['SIZE_UNKNOWN']['draft']
    assert rows['SIZE_LOOSE']['intent']=='size_advice' and 'следующий размер' in rows['SIZE_LOOSE']['draft']
    cx.close()

def test_rating_only_is_not_an_endless_manual_case(tmp_path):
    cx=connect(tmp_path/'e.sqlite3');p=load()
    import_review(cx,feedback('RATING',5,''))
    row=cx.execute("SELECT * FROM reviews WHERE id='RATING'").fetchone()
    assert prepare(cx,row,p,'demo',v2.POLICY)=='no_reply_needed'
    assert cx.execute("SELECT draft FROM reviews WHERE id='RATING'").fetchone()[0] is None
    import_review(cx,feedback('MEDIA',1,'',photo_urls=['/static/review-photos/cream-front.png']))
    row=cx.execute("SELECT * FROM reviews WHERE id='MEDIA'").fetchone()
    assert prepare(cx,row,p,'demo',v2.POLICY)=='needs_review'
    assert 'медиа' in cx.execute("SELECT reason FROM reviews WHERE id='MEDIA'").fetchone()[0]
    cx.close()

def test_not_bought_review_does_not_thank_for_purchase(tmp_path):
    cx=connect(tmp_path/'order.sqlite3');p=load()
    import_review(cx,feedback('NO_BUY',5,'Хорошая ткань, спасибо!',order_status='not_buyout'))
    row=cx.execute("SELECT * FROM reviews WHERE id='NO_BUY'").fetchone()
    prepare(cx,row,p,'demo',v2.POLICY)
    prepared=cx.execute("SELECT draft,order_status FROM reviews WHERE id='NO_BUY'").fetchone()
    assert prepared['order_status']=='not_buyout'
    assert 'покупк' not in prepared['draft'].lower()
    cx.close()

def test_remote_answer_is_distinct_from_local_post(tmp_path):
    cx=connect(tmp_path/'remote.sqlite3')
    import_question(cx,question('REMOTE'))
    observe_remote(cx,'q:REMOTE',{'state':'wbRu','answer':{'text':'Ответ из WB','editable':True}})
    row=cx.execute("SELECT status,send_attempts,remote_answer,remote_state,remote_answer_editable FROM reviews WHERE id='q:REMOTE'").fetchone()
    assert row[:]==('remote_answered',0,'Ответ из WB','wbRu',1)
    observe_remote(cx,'q:REMOTE',{'state':'suppliersPortalSynch','answer':None})
    assert cx.execute("SELECT status,remote_answer FROM reviews WHERE id='q:REMOTE'").fetchone()[:]==('new',None)
    cx.close()

def test_template_parameter_validation_and_profile_override():
    p=load();item={'id':'composition','name':'Состав','kind':'feedback','intent':'direct_fact','body':'Состав: {composition}.'}
    cream,_=render(item,p,p.products['TS-01'],'feedback','direct_fact')
    black,_=render(item,p,p.products['TS-01B'],'feedback','direct_fact')
    assert cream!=black and '95%' in cream and '100%' in black
    with pytest.raises(ValueError):validate_definition('X','feedback','direct_fact','Ответ: {unknown}')
    with pytest.raises(ValueError):render(item,p,p.products['TS-01'],'question','direct_fact')

def test_changed_feedback_revokes_draft_and_preserves_sent_history(tmp_path):
    cx=connect(tmp_path/'changed.sqlite3');p=load()
    import_review(cx,feedback('EDIT',5,'Хорошая футболка, спасибо!'))
    row=cx.execute("SELECT * FROM reviews WHERE id='EDIT'").fetchone();prepare(cx,row,p,'demo',v2.POLICY)
    assert cx.execute("SELECT status FROM reviews WHERE id='EDIT'").fetchone()[0]=='ready_auto'
    import_review(cx,feedback('EDIT',2,'После стирки футболка села.'))
    assert cx.execute("SELECT status,draft,approval_hash FROM reviews WHERE id='EDIT'").fetchone()[:]==('new',None,None)
    cx.execute("UPDATE reviews SET status='api_confirmed',send_attempts=1,sent_text='Старый ответ' WHERE id='EDIT'")
    import_review(cx,feedback('EDIT',1,'После стирки футболка села и шов разошёлся.',child_feedback_id='CHILD'))
    row=cx.execute("SELECT * FROM reviews WHERE id='EDIT'").fetchone()
    assert row['status']=='api_confirmed' and row['sent_text']=='Старый ответ' and row['source_changed']==1
    assert cx.execute("SELECT COUNT(*) FROM review_history WHERE review_id='EDIT'").fetchone()[0]==1
    cx.close()

def test_legacy_saved_ai_is_rejected_by_new_answer_contract(tmp_path):
    from app.v2_store import normalize
    from app.core import digest
    p=load();cx=connect(tmp_path/'saved.sqlite3')
    row=next(x for x in json.loads((v2.ROOT/'data/demo_reviews_v2.json').read_text()) if x['id']=='W20')
    import_review(cx,row);original=cx.execute("SELECT * FROM reviews WHERE id='W20'").fetchone()
    path=v2.ROOT/'data/saved_ai_answers_v3.json'
    assert saved_for(original,p,policy_revision(p,v2.POLICY),path) is None
    for changed in (dict(row,rating=1),dict(row,cons='Товар порван, размер не подошёл')):
        import_review(cx,changed)
        current=cx.execute("SELECT * FROM reviews WHERE id='W20'").fetchone()
        assert saved_for(current,p,policy_revision(p,v2.POLICY),path) is None
        import_review(cx,row)
    cx.close()

def test_private_question_never_calls_wb_and_public_uses_question_adapter(tmp_path,monkeypatch):
    p=load();p.products['TS-01']['sandbox_nmID']=990001;p.products['TS-01']['seller_article']='SL-TS-01-WBTEST'
    monkeypatch.setattr(v2,'MODE','sandbox');monkeypatch.setattr(v2,'DATA',tmp_path);monkeypatch.setattr(v2,'profile',lambda cx=None:p)
    cx=connect(tmp_path/'sandbox.sqlite3')
    import_review(cx,dict(feedback('SAME',5,'Хорошая футболка, спасибо!'),nm_id=990001,remote_size='M',remote_article='SL-TS-01-WBTEST'))
    import_question(cx,dict(question('SAME',990001,'Как стирать эту модель?'),remote_article='SL-TS-01-WBTEST'))
    row=cx.execute("SELECT * FROM reviews WHERE id='q:SAME'").fetchone();prepare(cx,row,p,'sandbox',v2.POLICY)
    row=cx.execute("SELECT * FROM reviews WHERE id='q:SAME'").fetchone()
    remote={'id':'SAME','text':row['body'],'answer':None,'state':'suppliersPortalSynch',
            'productDetails':{'nmId':990001,'supplierArticle':'SL-TS-01-WBTEST'}}
    class FakeWB:
        question_posts=0;feedback_posts=0
        def get_question(self,rid):return remote.copy()
        def answer_question(self,rid,text):self.question_posts+=1;remote['answer']={'text':text}
        def get(self,rid):raise AssertionError('feedback GET used for question')
        def answer(self,rid,text):self.feedback_posts+=1
    wb=FakeWB()
    from fastapi import HTTPException
    with pytest.raises(HTTPException):v2.send_reserved(cx,'q:SAME','Уточните, пожалуйста, цвет.',row['source_hash'],row['revision'],'manager',wb,audience='private')
    assert wb.question_posts==wb.feedback_posts==0
    cx.execute("UPDATE reviews SET audience='public' WHERE id='q:SAME'")
    assert v2.send_reserved(cx,'q:SAME','Уточните, пожалуйста, цвет.',row['source_hash'],row['revision'],'manager',wb,audience='public')['status']=='api_confirmed'
    assert wb.question_posts==1 and wb.feedback_posts==0
    with pytest.raises(HTTPException):v2.send_reserved(cx,'q:SAME','Уточните, пожалуйста, цвет.',row['source_hash'],row['revision'],'manager',wb,audience='public')
    cx.close()

def test_ai_failure_keeps_template_and_late_result_does_not_erase_edit(tmp_path,monkeypatch):
    monkeypatch.setattr(v2,'MODE','demo');monkeypatch.setattr(v2,'DATA',tmp_path)
    client=TestClient(v2.app);client.get('/api/v2/state')
    sid=client.cookies.get('demo_session_v3');path=tmp_path/(sid+'.sqlite3');v2.process_db(path)
    row=client.get('/api/v2/reviews/W03').json();before=row['draft']
    monkeypatch.setattr(v2,'reserve_hermes',lambda cx,mode:True)
    def fail(*a,**kw):raise RuntimeError('unavailable')
    monkeypatch.setattr(v2,'generate',fail)
    body={'text':before,'source_hash':row['source_hash'],'revision':row['revision'],'draft_version':row['draft_version']}
    result=client.post('/api/v2/reviews/W03/rephrase',json=body).json()
    assert result['status']=='failed'
    assert client.get('/api/v2/reviews/W03').json()['draft']==before
    def late(*a,**kw):
        cx=connect(path)
        cx.execute("UPDATE reviews SET draft='Правка менеджера',draft_version=draft_version+1,manager_edited=1 WHERE id='W03'")
        cx.close()
        return 'Новый ответ ИИ',[]
    monkeypatch.setattr(v2,'generate',late)
    assert client.post('/api/v2/reviews/W03/rephrase',json=body).status_code==409
    assert client.get('/api/v2/reviews/W03').json()['draft']=='Правка менеджера'

def test_custom_template_save_edit_and_insert(tmp_path,monkeypatch):
    monkeypatch.setattr(v2,'MODE','demo');monkeypatch.setattr(v2,'DATA',tmp_path)
    client=TestClient(v2.app);client.get('/api/v2/state?kind=question')
    sid=client.cookies.get('demo_session_v3');v2.process_db(tmp_path/(sid+'.sqlite3'))
    item={'name':'Мой уход','kind':'question','intent':'direct_fact','body':'Для ухода за моделью: {wash}.'}
    made=client.post('/api/v2/templates',json=item);assert made.status_code==200
    tid=made.json()['id'];item['body']='Рекомендация по уходу: {wash}.'
    assert client.put('/api/v2/templates/'+tid,json=item).status_code==200
    assert client.put('/api/v2/templates/wash',json=item).status_code==403
    row=client.get('/api/v2/reviews/q:Q01').json()
    inserted=client.post('/api/v2/reviews/q:Q01/template',json={
        'template_id':tid,'source_hash':row['source_hash'],'revision':row['revision'],
        'draft_version':row['draft_version']})
    assert inserted.status_code==200,inserted.text
    assert 'Ручная деликатная' in inserted.json()['text']
    after=client.get('/api/v2/reviews/q:Q01').json()
    assert after['generator']=='Шаблон · Мой уход' and after['draft_version']==row['draft_version']+1
    assert client.post('/api/v2/reviews/q:Q01/template',json={
        'template_id':tid,'source_hash':row['source_hash'],'revision':row['revision'],
        'draft_version':row['draft_version']}).status_code==409

def test_question_audience_change_invalidates_stale_send(tmp_path,monkeypatch):
    monkeypatch.setattr(v2,'MODE','demo');monkeypatch.setattr(v2,'DATA',tmp_path)
    client=TestClient(v2.app);client.get('/api/v2/state?kind=question')
    sid=client.cookies.get('demo_session_v3');v2.process_db(tmp_path/(sid+'.sqlite3'))
    row=client.get('/api/v2/reviews/q:Q02').json()
    base={'source_hash':row['source_hash'],'revision':row['revision'],'draft_version':row['draft_version']}
    public=client.put('/api/v2/reviews/q:Q02/audience',json={**base,'audience':'public'})
    assert public.status_code==200 and public.json()['api_supported']
    row=client.get('/api/v2/reviews/q:Q02').json()
    private=client.put('/api/v2/reviews/q:Q02/audience',json={
        'source_hash':row['source_hash'],'revision':row['revision'],
        'draft_version':row['draft_version'],'audience':'private'})
    assert private.status_code==200 and private.json()['api_supported'] is False
    stale=client.post('/api/v2/reviews/q:Q02/send',json={
        'text':row['draft'],'source_hash':row['source_hash'],'revision':row['revision'],
        'draft_version':row['draft_version'],'audience':'public'})
    assert stale.status_code==409
    assert client.get('/api/v2/reviews/q:Q02').json()['send_attempts']==0
