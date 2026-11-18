import base64,hashlib,hmac,json,os,re,secrets,sqlite3,time
from pathlib import Path
from fastapi import FastAPI,HTTPException,Request,Response
from fastapi.responses import FileResponse,JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel,Field
from .core import ROOT,db,digest,read_catalog,upsert_review,normalize_review,decide,validate_draft,validate_operator_text,event
from .wb import WB,WBError,remote_row
from .hermes import complete

MODE=os.getenv('WB_MODE','demo')
if MODE not in ('demo','sandbox'):raise RuntimeError('Production mode does not exist')
DATA=Path(os.getenv('WB_DATA_DIR',str(ROOT/'.data'/MODE))).resolve()
if not DATA.is_relative_to(ROOT/'.data'):raise RuntimeError('Data must stay in this project')
DATA.mkdir(parents=True,exist_ok=True)
app=FastAPI(title='ВБ-отзовик',docs_url=None,redoc_url=None,openapi_url=None)
app.mount('/static',StaticFiles(directory=ROOT/'static'),name='static')

def catalog():
    private=DATA/'catalog.xlsx'
    return read_catalog(private if private.exists() else ROOT/'data/catalog.xlsx')

@app.middleware('http')
async def guard(request:Request,call_next):
    sid=request.cookies.get('demo_session','')
    if MODE=='demo':
        request.state.demo_session=sid if re.fullmatch('[0-9a-f]{32}',sid) else secrets.token_hex(16)
    if MODE=='sandbox':
        auth=request.headers.get('Authorization','')
        expected=(ROOT/'.data/operator-key').read_text().strip()
        try:
            user,pw=base64.b64decode(auth.removeprefix('Basic '),validate=True).decode().split(':',1)
        except Exception:user=pw=''
        if not (auth.startswith('Basic ') and hmac.compare_digest(user,'operator') and hmac.compare_digest(pw,expected)):
            return Response(status_code=401,headers={'WWW-Authenticate':'Basic realm="WB Sandbox operator"'})
    if request.method not in ('GET','HEAD'):
        origin=request.headers.get('origin')
        if origin and origin!=str(request.base_url).rstrip('/'):
            return JSONResponse({'error':'Origin mismatch'},status_code=403)
    response=await call_next(request)
    response.headers['Cache-Control']='no-store'
    response.headers['X-Content-Type-Options']='nosniff'
    response.headers['Content-Security-Policy']="default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'"
    if MODE=='demo' and sid!=request.state.demo_session:
        response.set_cookie('demo_session',request.state.demo_session,httponly=True,samesite='strict')
    return response

def connect(request):
    if MODE=='sandbox':return db(DATA/'sandbox.sqlite3')
    sid=request.state.demo_session
    cx=db(DATA/(sid+'.sqlite3'))
    if cx.execute('SELECT COUNT(*) FROM reviews').fetchone()[0]==0:
        for case in json.loads((ROOT/'data/cases.json').read_text()):
            upsert_review(cx,{'id':case['id'],'nm_id':case['nmId'],'rating':case['rating'],'body':case['text'],'pros':case['pros'],'cons':case['cons']})
    return cx

def get_row(cx,rid):
    row=cx.execute('SELECT * FROM reviews WHERE id=?',(rid,)).fetchone()
    if not row:raise HTTPException(404,'Отзыв не найден')
    return row

def snapshot(row,cat,revision):
    d=decide(cat,MODE,row)
    x=dict(row)
    x['fact_ids']=json.loads(x['fact_ids'] or '[]')
    x['product']=d['product'];x['facts']=d['facts'];x['route']=d['route'];x['route_reason']=d['reason'];x['recommendations']=d['recommendations']
    x['revision_current']=x['revision']==revision
    x['approved_current']=bool(x['approved_hash'] and x['approved_hash']==digest({'text':x['draft'],'source':x['source_hash'],'revision':revision}))
    return x

@app.get('/')
def index():return FileResponse(ROOT/'static/index.html')
@app.get('/api/state')
def state(request:Request):
    cat,rev=catalog();cx=connect(request)
    rows=cx.execute('SELECT * FROM reviews ORDER BY id').fetchall()
    result={'mode':MODE,'revision':rev[:12],'reviews':[snapshot(x,cat,rev) for x in rows], 'counts':dict(cx.execute('SELECT status,COUNT(*) FROM reviews GROUP BY status').fetchall())}
    cx.close();return result

class TextBody(BaseModel):
    text:str=Field(min_length=2,max_length=700)

@app.post('/api/reviews/{rid}/generate')
def generate(rid:str,request:Request):
    cat,rev=catalog();cx=connect(request);row=get_row(cx,rid);decision=decide(cat,MODE,row)
    if row['status'] in ('sending','api_confirmed','outcome_unknown'):raise HTTPException(409,'Отправка уже начата')
    if decision['route']=='manual':
        cx.execute('UPDATE reviews SET status=?,reason=?,approved_hash=NULL WHERE id=?',('manual',decision['reason'],rid));event(cx,rid,'manual',decision['reason']);cx.close();return {'status':'manual','reason':decision['reason']}
    if decision['facts'] or decision['recommendations']:
        payload={'review':{'text':row['body'],'pros':row['pros'],'cons':row['cons'],'rating':row['rating']},'product':{'name':decision['product']['name'],'variant':decision['product']['variant']},'facts':decision['facts'],'recommendation_candidates':[{'recommendation_id':r['recommendation_id'],'reason':r['reason']} for r in decision['recommendations']]}
        try:
            raw=complete(payload);text,ids,rec=validate_draft(raw,decision,cat);generator='Hermes'
        except Exception as exc:
            cx.execute('UPDATE reviews SET status=?,reason=?,approved_hash=NULL WHERE id=?',('manual','Генерация или проверка Hermes не завершена',rid))
            event(cx,rid,'hermes_failed',type(exc).__name__);cx.close();raise HTTPException(503,'Hermes недоступен или черновик не прошёл проверку; передано менеджеру')
    else:
        text='Спасибо за отзыв! Рады, что изделие вам понравилось.';ids=[];rec=None;generator='утверждённый шаблон'
    cx.execute('''UPDATE reviews SET status='needs_review',draft=?,fact_ids=?,recommendation_id=?,generator=?,reason=?,revision=?,approved_hash=NULL,updated_at=? WHERE id=?''',(text,json.dumps(ids),rec,generator,'Проверка оператором обязательна',rev,time.time(),rid));event(cx,rid,'draft',generator);cx.close();return {'status':'needs_review','text':text,'generator':generator}

@app.put('/api/reviews/{rid}/draft')
def edit(rid:str,data:TextBody,request:Request):
    cat,rev=catalog();cx=connect(request);row=get_row(cx,rid);decision=decide(cat,MODE,row)
    if row['status'] not in ('needs_review','draft','approved'):raise HTTPException(409,'Редактирование недоступно')
    if decision['route']!='review':raise HTTPException(409,'Нужен ручной разбор')
    try:validate_operator_text(data.text,row,decision,cat)
    except ValueError as exc:raise HTTPException(422,str(exc))
    cx.execute('UPDATE reviews SET draft=?,status=?,approved_hash=NULL,revision=?,updated_at=? WHERE id=?',(data.text,'needs_review',rev,time.time(),rid));event(cx,rid,'edit');cx.close();return {'status':'needs_review'}

@app.post('/api/reviews/{rid}/approve')
def approve(rid:str,request:Request):
    cat,rev=catalog();cx=connect(request);row=get_row(cx,rid);decision=decide(cat,MODE,row)
    if row['status']!='needs_review' or not row['draft'] or row['revision']!=rev or decision['route']!='review':raise HTTPException(409,'Черновик или данные изменились; согласуйте заново')
    try:validate_operator_text(row['draft'],row,decision,cat)
    except ValueError as exc:raise HTTPException(422,str(exc))
    approval=digest({'text':row['draft'],'source':row['source_hash'],'revision':rev})
    cx.execute('UPDATE reviews SET status=?,approved_hash=?,updated_at=? WHERE id=?',('approved',approval,time.time(),rid));event(cx,rid,'approve',approval[:12]);cx.close();return {'status':'approved'}

@app.post('/api/reviews/{rid}/manual')
def manual(rid:str,request:Request):
    cx=connect(request);row=get_row(cx,rid)
    if row['status'] in ('sending','api_confirmed','outcome_unknown'):raise HTTPException(409,'Отправка уже начата')
    cx.execute('UPDATE reviews SET status=?,approved_hash=NULL WHERE id=?',('manual',rid));event(cx,rid,'manual');cx.close();return {'status':'manual'}
@app.post('/api/reviews/{rid}/skip')
def skip(rid:str,request:Request):
    cx=connect(request);row=get_row(cx,rid)
    if row['status'] in ('sending','api_confirmed','outcome_unknown'):raise HTTPException(409,'Отправка уже начата')
    cx.execute('UPDATE reviews SET status=?,approved_hash=NULL WHERE id=?',('skipped',rid));event(cx,rid,'skip');cx.close();return {'status':'skipped'}

def remote_fingerprint(item):
    return digest(normalize_review(remote_row(item)))

@app.post('/api/reviews/{rid}/send')
def send(rid:str,request:Request):
    cat,rev=catalog();cx=connect(request)
    try:
        if MODE=='sandbox':
            try:wb=WB()
            except (ValueError,OSError):raise HTTPException(503,'Sandbox token unavailable; POST не выполнен')
        cx.execute('BEGIN IMMEDIATE');row=get_row(cx,rid);decision=decide(cat,MODE,row)
        expected=digest({'text':row['draft'],'source':row['source_hash'],'revision':rev})
        if row['status']!='approved' or row['approved_hash']!=expected or row['revision']!=rev or decision['route']!='review' or row['send_attempts']:
            raise HTTPException(409,'Нет актуального согласования или отправка уже была')
        try:validate_operator_text(row['draft'],row,decision,cat)
        except ValueError as exc:raise HTTPException(422,str(exc))
        cx.execute("UPDATE reviews SET status='sending',send_attempts=send_attempts+1,sent_text=?,updated_at=? WHERE id=?",(row['draft'],time.time(),rid));event(cx,rid,'send_reserved',expected[:12]);cx.commit()
        if MODE=='demo':
            cx.execute("UPDATE reviews SET status='api_confirmed',remote_answer=?,reason=? WHERE id=?",(row['draft'],'Локальная имитация Demo; WB API не вызывался',rid));event(cx,rid,'demo_simulated');return {'status':'api_confirmed','detail':'Demo: локальная имитация, без WB API'}
        try:
            before=wb.get(rid)
        except (WBError,Exception) as exc:
            cx.execute("UPDATE reviews SET status='failed',reason=? WHERE id=?",('Проверка перед POST не выполнена: '+type(exc).__name__,rid));event(cx,rid,'preflight_failed',type(exc).__name__);raise HTTPException(502,'Не удалось перечитать отзыв; POST не выполнен')
        details=(before or {}).get('productDetails') or {}
        expected_size=str(decision['product']['variant']).split('/')[-1].strip()
        if not before or remote_fingerprint(before)!=row['source_hash'] or (before.get('answer') or {}).get('text') or details.get('size')!=expected_size or details.get('supplierArticle')!=decision['product']['seller_article']:
            cx.execute("UPDATE reviews SET status='manual',approved_hash=NULL,reason=? WHERE id=?",('Отзыв изменился или уже имеет ответ',rid));event(cx,rid,'preflight_changed');raise HTTPException(409,'Отзыв изменился или уже имеет ответ; POST не выполнен')
        try:
            wb.answer(rid,row['draft']);post_ok=True
        except WBError as exc:
            cx.execute("UPDATE reviews SET status='failed',reason=? WHERE id=?",(f'WB HTTP {exc.status}',rid));event(cx,rid,'post_rejected',str(exc.status));raise HTTPException(502,f'WB Sandbox отклонил POST: HTTP {exc.status}')
        except Exception as exc:
            post_ok=False;event(cx,rid,'post_uncertain',type(exc).__name__)
        try:
            after=wb.get(rid)
        except Exception as exc:
            cx.execute("UPDATE reviews SET status='outcome_unknown',reason=? WHERE id=?",('Контрольное чтение не выполнено: '+type(exc).__name__,rid));event(cx,rid,'readback_failed',type(exc).__name__);return {'status':'outcome_unknown'}
        answer=(after or {}).get('answer') or {}
        if answer.get('text')==row['draft']:
            cx.execute('UPDATE reviews SET status=?,remote_answer=?,reason=? WHERE id=?',('api_confirmed',answer['text'],'Ответ подтверждён чтением; модерация/публикация не подтверждены',rid));event(cx,rid,'readback_confirmed',str(answer.get('state') or 'unknown'));return {'status':'api_confirmed','answer_state':answer.get('state')}
        cx.execute('UPDATE reviews SET status=?,reason=? WHERE id=?',('outcome_unknown','POST '+('204' if post_ok else 'неизвестен')+'; совпадающий ответ не найден контрольным чтением',rid));event(cx,rid,'readback_unconfirmed');return {'status':'outcome_unknown'}
    finally:cx.close()

@app.post('/api/sandbox/sync')
def sync(request:Request):
    if MODE!='sandbox':raise HTTPException(404)
    wb=WB();cat,rev=catalog();cx=connect(request);count=0
    for skip in range(0,1000,100):
        batch=wb.list(False,100,skip)
        for item in batch:
            if item.get('id') and not item.get('answer'):
                count+=upsert_review(cx,remote_row(item))
        if len(batch)<100:break
    cx.close();return {'imported_or_changed':count}
