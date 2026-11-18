"""Background review workbench. External writes are confined to WB Sandbox."""
import base64,hashlib,hmac,json,os,re,secrets,shutil,sqlite3,threading,time
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI,HTTPException,Request,Response
from fastapi.responses import FileResponse,JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel,Field
from .core import ROOT,digest
from .profile import load as load_profile
from .v2_store import connect,setting,set_setting,event,import_review,import_question,observe_remote,normalize,normalize_question
from .v2_logic import policy_revision,approval,decision_for,validate_reply
from .v4_store import init as init_v4,profile_overlay,seed_candidates,config as v4_config
from .v4_logic import policy_for,prepare as prepare_v4,select_campaign,upsell_text,validate_composed,evaluate as evaluate_v4
from .complaint_library import choose as complaint_choose,plan as complaint_plan,validate_adapted
from .wb import WB,WBError,remote_row,remote_question
from .hermes import complete,MANUAL_SYSTEM
from .classifier import TOPICS,review_text
from .ai_drafts import generate,saved_for
from .templates import ensure as ensure_templates,list_templates,render as render_template,validate_definition

MODE=os.getenv('WB_MODE','demo')
if MODE not in ('demo','sandbox'):raise RuntimeError('Only Demo and Sandbox exist')
DATA=Path(os.getenv('WB_DATA_DIR',str(ROOT/'.data'/(MODE+'-v2')))).resolve()
if not DATA.is_relative_to(ROOT/'.data'):raise RuntimeError('Data must stay in project')
DATA.mkdir(parents=True,exist_ok=True)
POLICY=json.loads((ROOT/'data/auto_policy.json').read_text())
WAKE=threading.Event();STOP=threading.Event()

def profile(cx=None):
    private=DATA/'profiles.xlsx'
    bundled=ROOT/'data/profiles_v4.xlsx' if os.getenv('WB_UI_V4')=='1' and MODE=='demo' else ROOT/'data/profiles.xlsx'
    path=private if private.exists() else bundled
    if MODE=='sandbox' and not private.exists():raise RuntimeError('Sandbox profile not installed')
    base=load_profile(path)
    if cx is None:return base
    init_v4(cx,base)
    if MODE=='demo':seed_candidates(cx,json.loads((ROOT/'data/upsell_catalog_v4.json').read_text()))
    return profile_overlay(cx,base)

def card_where(p,mode,kind,product_id):
    """Use the same card identity rules as Profile.match for inbox filters."""
    if product_id not in p.products:raise HTTPException(400,'Неизвестный товар')
    members=p.card_members(product_id)
    terms=[];args=[]
    for nm_id in sorted({item.get(mode+'_nmID') for item in members if item.get(mode+'_nmID')}):
        found=[item for item in p.products.values() if item['active'] and item.get(mode+'_nmID')==nm_id]
        on_card=[item for item in members if item.get(mode+'_nmID')==nm_id]
        if kind=='question':
            if len({(item['model_id'],item['seller_article'],item['color']) for item in found})!=1:continue
            if mode=='sandbox':
                terms.append('(nm_id=? AND (remote_article IS NULL OR remote_article=? OR remote_article=?))')
                args.extend((nm_id,'',on_card[0]['seller_article']))
            else:
                terms.append('nm_id=?');args.append(nm_id)
        elif len(found)>1 or mode=='sandbox':
            for item in on_card:
                if len(found)>1 and sum(other['size']==item['size'] and other['seller_article']==item['seller_article'] for other in found)!=1:
                    continue
                terms.append('(nm_id=? AND remote_size=? AND remote_article=?)')
                args.extend((nm_id,str(item['size']),str(item['seller_article'])))
        else:
            terms.append('nm_id=?');args.append(nm_id)
    return '('+' OR '.join(terms)+')' if terms else '0',args

def database_for(request):
    if MODE=='sandbox':return DATA/'sandbox.sqlite3'
    return DATA/(request.state.demo_session+'.sqlite3')

def seed_demo(cx):
    if setting(cx,'seeded')!='1':
        for x in json.loads((ROOT/'data/demo_reviews_v2.json').read_text()):import_review(cx,x)
        set_setting(cx,'seeded','1')
        set_setting(cx,'processing_enabled','1')
        set_setting(cx,'auto_send_enabled','1')
        WAKE.set()
    if setting(cx,'questions_seeded')!='1':
        for x in json.loads((ROOT/'data/demo_questions_v3.json').read_text()):import_question(cx,x)
        set_setting(cx,'questions_seeded','1');WAKE.set()
    if setting(cx,'fixture_v3')!='1':
        for x in json.loads((ROOT/'data/demo_reviews_v2.json').read_text()):import_review(cx,x)
        set_setting(cx,'fixture_v3','1');WAKE.set()
    if os.getenv('WB_V4_FIXTURES')=='1' and setting(cx,'fixture_v4')!='1':
        for x in json.loads((ROOT/'data/demo_reviews_v4.json').read_text()):import_review(cx,x)
        set_setting(cx,'fixture_v4','1');WAKE.set()

def db_for(request):
    cx=connect(database_for(request))
    if MODE=='demo':seed_demo(cx)
    init_v4(cx,profile())
    profile(cx)
    return cx

def get_row(cx,rid):
    row=cx.execute('SELECT * FROM reviews WHERE id=?',(rid,)).fetchone()
    if row is None:raise HTTPException(404,'Отзыв не найден')
    return row

def public_row(row,p,cx=None):
    x=dict(row);x['photo_urls']=json.loads(x['photo_urls'] or '[]')
    x['fact_ids']=json.loads(x['fact_ids'] or '[]')
    found,_=(p.match_question(MODE,row) if x['kind']=='question' else p.match(MODE,row))
    x['product']=p.present(found) if found else None
    facts=(p.question_facts(found['product_id']) if x['kind']=='question' else p.facts(found['product_id'])) if found else {}
    if found and x['kind']=='question':
        facts={**facts,**{'measurement:'+size:fact for size,fact in p.question_size_facts(found['product_id'],row['body']).items()}}
    x['facts_used']=[{'field':f['field'],'value':f['value'],'source':f['source'],'version':f['version']} for f in facts.values() if f['fact_id'] in x['fact_ids']]
    if cx and found and x['kind']=='feedback':
        planned,_=complaint_plan(cx,row,p,found)
        if planned:x['scenario_ground']={'id':planned['scenario_id'],'version':planned['version'],
                                         'action':planned['action'],'source':planned['source_url']}
    if x['kind']=='question':
        for field in ('rating','pros','cons','photo_urls','remote_size'):x.pop(field,None)
    x['revision_current']=x['revision']==policy_revision(p,policy_for(cx,POLICY,found) if cx else POLICY)
    x['recommendation_ids']=json.loads(x.get('recommendation_ids') or '[]')
    return x

def _reconcile(cx,wb=None):
    rows=cx.execute("SELECT * FROM reviews WHERE status IN ('sending','outcome_unknown')").fetchall()
    for row in rows:
        if MODE=='demo':
            cx.execute("UPDATE reviews SET status='outcome_unknown',reason='Процесс был прерван после резервирования отправки' WHERE id=?",(row['id'],))
            event(cx,row['id'],'reconcile_unknown','demo interrupted')
            continue
        try:
            wb=wb or WB();remote=readback_with_retry(wb,row['remote_id'] or row['id'],row['kind']);answer=(remote or {}).get('answer') or {}
            if answer.get('text')==row['sent_text']:
                cx.execute("UPDATE reviews SET status='api_confirmed',remote_answer=?,reason='Ответ подтверждён контрольным чтением' WHERE id=?",(answer['text'],row['id']))
                observe_remote(cx,row['id'],remote)
                event(cx,row['id'],'reconcile_confirmed')
            else:
                cx.execute("UPDATE reviews SET status='outcome_unknown',reason='Исход отправки не подтверждён контрольным чтением' WHERE id=?",(row['id'],))
                event(cx,row['id'],'reconcile_unknown')
        except Exception as exc:
            cx.execute("UPDATE reviews SET status='outcome_unknown',reason=? WHERE id=?",('Контрольное чтение не выполнено: '+type(exc).__name__,row['id']))
            event(cx,row['id'],'reconcile_failed',type(exc).__name__)

def readback_with_retry(wb,rid,kind='feedback'):
    for attempt in range(3):
        try:return wb.get_question(rid) if kind=='question' else wb.get(rid)
        except WBError as exc:
            if exc.status!=429 or attempt==2:raise
            time.sleep(1.25*(attempt+1))

def send_reserved(cx,rid,text,source_hash,revision,kind,wb=None,audience=None,draft_version=None):
    init_v4(cx,profile())
    p=profile(cx)
    cx.execute('BEGIN IMMEDIATE')
    try:
        row=get_row(cx,rid)
        item_kind=row['kind'];remote_id=row['remote_id'] or row['id']
        if draft_version is not None and row['draft_version']!=draft_version:
            raise HTTPException(409,'Черновик или аудитория изменились; обновите обращение')
        if item_kind=='question' and (audience!='public' or row['audience']!='public'):
            raise HTTPException(409,'Для публичного ответа явно выберите аудиторию. Личный ответ доступен только в WB Partners')
        if item_kind=='feedback' and audience not in (None,'public'):
            raise HTTPException(400,'У отзыва нет настройки аудитории')
        found,_=(p.match_question(MODE,row) if item_kind=='question' else p.match(MODE,row))
        if not found:raise HTTPException(409,'Точный вариант товара не подтверждён')
        current=policy_revision(p,policy_for(cx,POLICY,found))
        if revision!=current or row['revision']!=current:raise HTTPException(409,'Профиль или политика изменились; обновите отзыв')
        if row['source_hash']!=source_hash or row['send_attempts'] or row['status'] not in ('needs_review','ready_auto','deferred'):
            raise HTTPException(409,'Отзыв изменился или отправка уже началась')
        if kind=='auto_policy':
            if setting(cx,'processing_enabled','1')!='1' or setting(cx,'auto_send_enabled','0')!='1' or row['status']!='ready_auto' or row['approval_hash']!=approval(text,source_hash,revision,audience if item_kind=='question' else None):
                raise HTTPException(409,'Автоотправка отключена или разрешение устарело')
        elif kind!='manager':raise HTTPException(400,'Недопустимое основание')
        if item_kind=='feedback' and complaint_choose(row,p,found):
            complaint,gap=complaint_plan(cx,row,p,found)
            if not complaint:raise HTTPException(409,gap)
            try:validate_adapted(text,complaint,found,p,buyer_text=review_text(row))
            except ValueError as exc:raise HTTPException(422,str(exc))
        if row['campaign_id']:
            selection=select_campaign(cx,row,found,MODE)
            expected=json.loads(row['recommendation_ids'] or '[]')
            if not selection['campaign'] or selection['campaign']['id']!=row['campaign_id'] or selection['campaign']['version']!=row['campaign_version'] or [x['id'] for x in selection['selected']]!=expected:
                raise HTTPException(409,'Допродажа устарела; обновите черновик перед отправкой')
            text=validate_composed(text,found,p,item_kind,selection)
        else:text=validate_reply(text,found,p,item_kind,buyer_text=review_text(row) if item_kind=='feedback' else row['body'])
        cx.execute("UPDATE reviews SET status='sending',draft=?,sent_text=?,approval_hash=?,approval_kind=?,send_attempts=send_attempts+1,updated_at=? WHERE id=?",
                   (text,text,approval(text,source_hash,revision,audience if item_kind=='question' else None),kind,time.time(),rid))
        event(cx,rid,'send_reserved',kind)
        cx.commit()
    except Exception:
        cx.rollback();raise
    if MODE=='demo':
        cx.execute("UPDATE reviews SET status='api_confirmed',remote_answer=?,reason='Локальная имитация Demo; WB не вызывался',updated_at=? WHERE id=?",(text,time.time(),rid))
        event(cx,rid,'demo_simulated',kind)
        return {'status':'api_confirmed','detail':'Demo: имитация; WB не вызывался'}
    try:
        wb=wb or WB();before=readback_with_retry(wb,remote_id,item_kind)
    except Exception as exc:
        cx.execute("UPDATE reviews SET status='failed',reason=?,updated_at=? WHERE id=?",('Проверка перед отправкой не выполнена: '+type(exc).__name__,time.time(),rid))
        event(cx,rid,'preflight_failed',type(exc).__name__)
        return {'status':'failed','detail':'Отправка не выполнялась'}
    expected=(normalize_question(remote_question(before)) if item_kind=='question' else normalize(remote_row(before))) if before else None
    row=cx.execute('SELECT * FROM reviews WHERE id=?',(rid,)).fetchone()
    local=(normalize_question({'id':remote_id,'nm_id':row['nm_id'],'body':row['body'],'remote_article':row['remote_article'],
                               'user_name':row['user_name'],'created_date':row['created_date']}) if item_kind=='question' else normalize(dict(row)))
    details=(before or {}).get('productDetails') or {}
    identity_ok=(not details.get('supplierArticle') or details.get('supplierArticle')==found['seller_article']) if item_kind=='question' else (details.get('size')==str(found['size']) and details.get('supplierArticle')==found['seller_article'])
    if not before or expected!=local or (before.get('answer') or {}).get('text') or not found or not identity_ok:
        cx.execute("UPDATE reviews SET status='failed',reason='Отзыв в WB изменился, уже отвечен или вариант не совпал',updated_at=? WHERE id=?",(time.time(),rid))
        event(cx,rid,'preflight_changed')
        return {'status':'failed','detail':'Отправка не выполнялась: данные WB изменились'}
    try:
        (wb.answer_question(remote_id,text) if item_kind=='question' else wb.answer(remote_id,text));post_ok=True
    except WBError as exc:
        cx.execute("UPDATE reviews SET status='failed',reason=?,updated_at=? WHERE id=?",('WB Sandbox отклонил отправку: HTTP '+str(exc.status),time.time(),rid))
        event(cx,rid,'write_rejected',str(exc.status))
        return {'status':'failed','detail':'WB Sandbox отклонил отправку'}
    except Exception as exc:
        post_ok=False;event(cx,rid,'post_uncertain',type(exc).__name__)
    try:
        after=readback_with_retry(wb,remote_id,item_kind);answer=(after or {}).get('answer') or {}
    except Exception as exc:
        cx.execute("UPDATE reviews SET status='outcome_unknown',reason=?,updated_at=? WHERE id=?",('Контрольное чтение не выполнено: '+type(exc).__name__,time.time(),rid))
        event(cx,rid,'readback_failed',type(exc).__name__)
        return {'status':'outcome_unknown'}
    if answer.get('text')==text:
        cx.execute("UPDATE reviews SET status='api_confirmed',remote_answer=?,reason='Ответ подтверждён чтением; публикация не подтверждена',updated_at=? WHERE id=?",(text,time.time(),rid))
        observe_remote(cx,rid,after)
        event(cx,rid,'readback_confirmed',str(answer.get('state') or 'unknown'))
        return {'status':'api_confirmed'}
    cx.execute("UPDATE reviews SET status='outcome_unknown',reason=?,updated_at=? WHERE id=?",('Запрос отправки '+('выполнен' if post_ok else 'с неопределённым результатом')+'; ответ не найден контрольным чтением',time.time(),rid))
    event(cx,rid,'readback_unconfirmed')
    return {'status':'outcome_unknown'}

def sync_sandbox(cx):
    wb=WB();count=0;size=min(int(POLICY['sync_page_size']),100)
    for kind in ('feedback','question'):
        for answered in (False,True):
            key=f'{kind}_{"answered" if answered else "waiting"}_offset'
            offset=int(setting(cx,key,'0'))
            max_offset=199990 if kind=='feedback' else 10000-size
            if offset>max_offset:offset=0
            for _ in range(int(POLICY['sync_max_pages_per_cycle'])):
                batch=(wb.list_questions(answered,size,offset) if kind=='question' else wb.list(answered,size,offset))
                for item in batch:
                    if not item.get('id'):continue
                    if kind=='question':
                        raw=remote_question(item);count+=int(import_question(cx,raw));key_id='q:'+raw['id']
                    else:
                        raw=remote_row(item);count+=int(import_review(cx,raw));key_id=raw['id']
                    observe_remote(cx,key_id,item)
                offset+=len(batch)
                if len(batch)<size or offset>max_offset:offset=0;break
            set_setting(cx,key,offset)
    set_setting(cx,'last_sync',time.time())
    return count

def process_db(path):
    cx=connect(path)
    try:
        if setting(cx,'processing_enabled','1')!='1':return
        init_v4(cx,profile())
        p=profile(cx)
        if MODE=='sandbox':
            try:sync_sandbox(cx)
            except Exception as exc:event(cx,None,'sync_failed',type(exc).__name__)
        _reconcile(cx)
        from .complaint_library import CONTRACT_VERSION
        scan_token=digest({'profile':p.revision,'configs':[tuple(x) for x in cx.execute('SELECT scope_type,scope_id,version FROM v4_config ORDER BY scope_type,scope_id')],
                           'policy':POLICY,'answer_contract':CONTRACT_VERSION})
        if setting(cx,'v4_scan_token')!=scan_token:
            stale=0
            cx.execute('BEGIN IMMEDIATE')
            try:
                remaining=False
                for old in cx.execute("SELECT * FROM reviews WHERE status IN ('needs_review','ready_auto','deferred') AND send_attempts=0 AND revision IS NOT NULL ORDER BY id").fetchall():
                    product,_=(p.match_question(MODE,old) if old['kind']=='question' else p.match(MODE,old))
                    current_revision=policy_revision(p,policy_for(cx,POLICY,product))
                    if old['revision']!=current_revision:
                        if stale>=100:
                            remaining=True;continue
                        cx.execute('INSERT INTO review_history(review_id,snapshot,at) VALUES(?,?,?)',
                                   (old['id'],json.dumps(dict(old),ensure_ascii=False),time.time()))
                        manual=bool(old['manager_edited'] or old['approval_kind']=='manager' or old['status']=='deferred')
                        if manual:
                            cx.execute("UPDATE reviews SET status=CASE WHEN status='deferred' THEN 'deferred' ELSE 'needs_review' END,approval_hash=NULL,approval_kind=NULL,revision=NULL,model_status='stale',draft_version=draft_version+1,reason='Настройки или знания изменились; проверьте сохранённый текст',updated_at=? WHERE id=?",
                                       (time.time(),old['id']))
                            event(cx,old['id'],'scenario_changed_manual_recheck')
                        else:
                            cx.execute("UPDATE reviews SET status='new',draft=NULL,generator=NULL,fact_ids='[]',revision=NULL,approval_hash=NULL,approval_kind=NULL,model_status='not_needed',hermes_attempted=0,model_attempts=0,model_retry_at=NULL,model_error=NULL,draft_version=draft_version+1,reason='Предыдущий машинный черновик сохранён в истории; переподготовка',updated_at=? WHERE id=?",
                                       (time.time(),old['id']))
                            event(cx,old['id'],'scenario_changed_machine_reprepare')
                        stale+=1
                if stale:event(cx,None,'stale_drafts_reset',str(stale))
                if not remaining:set_setting(cx,'v4_scan_token',scan_token)
                cx.commit()
            except Exception:cx.rollback();raise
        pending=cx.execute("SELECT * FROM reviews WHERE status='new' ORDER BY updated_at,id LIMIT 100").fetchall()
        if pending:
            cx.execute('BEGIN IMMEDIATE')
            try:
                for row in pending:
                    if setting(cx,'processing_enabled','1')!='1':break
                    prepare_v4(cx,row,p,MODE,POLICY)
                    if MODE=='demo':
                        prepared=cx.execute('SELECT * FROM reviews WHERE id=?',(row['id'],)).fetchone()
                        saved=saved_for(prepared,p,prepared['revision'],ROOT/'data/saved_ai_answers_v3.json',cx)
                        if saved and prepared['status']=='needs_review':
                            cx.execute("UPDATE reviews SET draft=?,generator='Сохранённый пример ИИ',fact_ids=?,model_status='saved',reason='Сохранённый пример ИИ; требуется проверка менеджера',draft_version=draft_version+1 WHERE id=?",
                                       (saved[0],json.dumps(saved[1]),row['id']))
                            event(cx,row['id'],'saved_ai_draft')
                cx.commit()
            except Exception:cx.rollback();raise
        if setting(cx,'auto_send_enabled','0')!='1':return
        allow_ids=None
        if MODE=='sandbox':
            allow_file=DATA/'auto_ids.json'
            allow_ids=set(json.loads(allow_file.read_text())) if allow_file.exists() else set()
        for row in cx.execute("SELECT * FROM reviews WHERE status='ready_auto' ORDER BY updated_at,id LIMIT 10").fetchall():
            if setting(cx,'processing_enabled','1')!='1' or setting(cx,'auto_send_enabled','0')!='1':break
            if allow_ids is not None and row['id'] not in allow_ids:continue
            try:send_reserved(cx,row['id'],row['draft'],row['source_hash'],row['revision'],'auto_policy',audience='public' if row['kind']=='question' else None)
            except HTTPException as exc:
                if exc.status_code==409:
                    cx.execute("UPDATE reviews SET status='needs_review',approval_hash=NULL,approval_kind=NULL,reason='Разрешение на автоотправку устарело; проверьте черновик' WHERE id=? AND status='ready_auto'",(row['id'],))
                event(cx,row['id'],'auto_send_failed',str(exc.status_code))
            except Exception as exc:event(cx,row['id'],'auto_send_failed',type(exc).__name__)
    finally:cx.close()

def worker_loop():
    while not STOP.is_set():
        try:
            paths=[DATA/'sandbox.sqlite3'] if MODE=='sandbox' else sorted(DATA.glob('*.sqlite3'),key=lambda path:path.stat().st_mtime,reverse=True)
            for path in paths:
                if STOP.is_set():break
                process_db(path)
        except Exception:pass
        WAKE.wait(float(POLICY['sandbox_poll_seconds'] if MODE=='sandbox' else POLICY['demo_poll_seconds']))
        WAKE.clear()

def reserve_hermes(cx,mode):
    day=time.strftime('%Y-%m-%d',time.gmtime())
    configured=min(int(v4_config(cx)['llm_budget_per_day']),int(POLICY['manual_llm_budget_per_day']))
    if mode=='sandbox':
        cx.execute('BEGIN IMMEDIATE')
        try:
            used=int(setting(cx,'hermes_used','0')) if setting(cx,'hermes_day')==day else 0
            if used>=configured:cx.rollback();return False
            set_setting(cx,'hermes_day',day);set_setting(cx,'hermes_used',used+1);cx.commit()
            return True
        except Exception:cx.rollback();raise
    session_used=int(setting(cx,'demo_hermes_used','0')) if setting(cx,'demo_hermes_day')==day else 0
    if session_used>=min(2,configured):return False
    global_path=ROOT/'.data/demo-hermes-budget.sqlite3'
    budget=sqlite3.connect(global_path,timeout=30,isolation_level=None)
    try:
        budget.execute('CREATE TABLE IF NOT EXISTS quota(day TEXT PRIMARY KEY,used INTEGER NOT NULL)')
        budget.execute('BEGIN IMMEDIATE')
        used=budget.execute('SELECT used FROM quota WHERE day=?',(day,)).fetchone()
        global_limit=min(14,max(8,int(os.getenv('WB_DEMO_HERMES_GLOBAL_LIMIT','8'))))
        if used and used[0]>=global_limit:budget.rollback();return False
        budget.execute('INSERT INTO quota(day,used) VALUES(?,1) ON CONFLICT(day) DO UPDATE SET used=used+1',(day,))
        budget.commit()
    finally:budget.close()
    set_setting(cx,'demo_hermes_day',day);set_setting(cx,'demo_hermes_used',session_used+1)
    return True

MAX_MODEL_ATTEMPTS=3

def model_failure_kind(exc):
    """Network and provider capacity can be retried; invalid content cannot."""
    import httpx
    if isinstance(exc,httpx.HTTPStatusError):
        return 'transient' if exc.response.status_code in (408,409,429,500,502,503,504) else 'configuration'
    if isinstance(exc,(httpx.TimeoutException,httpx.TransportError,TimeoutError,OSError)):
        return 'transient'
    if isinstance(exc,RuntimeError) and 'not configured' in str(exc).lower():
        return 'configuration'
    return 'content'

def hermes_step(path,now=None,reserve=None,generate_fn=None):
    """One bounded model transition, injectable for clock and client tests."""
    now=time.time() if now is None else now
    reserve=reserve_hermes if reserve is None else reserve
    generate_fn=generate if generate_fn is None else generate_fn
    cx=connect(path)
    try:
        if setting(cx,'processing_enabled','1')!='1':return 'paused'
        row=cx.execute("""SELECT * FROM reviews WHERE status='needs_review'
            AND model_status IN ('pending','retry','limit')
            AND COALESCE(model_retry_at,0)<=? AND model_attempts<?
            AND manager_edited=0 ORDER BY updated_at,id LIMIT 1""",(now,MAX_MODEL_ATTEMPTS)).fetchone()
        if not row:return 'idle'
        p=profile(cx)
        product,_=(p.match_question(MODE,row) if row['kind']=='question' else p.match(MODE,row))
        if not product:
            cx.execute("UPDATE reviews SET model_status='unavailable',model_error='identity',reason='ИИ не запущен: товар не сопоставлен с профилем' WHERE id=?",(row['id'],))
            return 'unavailable'
        if row['revision']!=policy_revision(p,policy_for(cx,POLICY,product)):
            cx.execute("UPDATE reviews SET model_status='stale',approval_hash=NULL,reason='Правила изменились; черновик требует обновления' WHERE id=?",(row['id'],))
            return 'stale'
        if not reserve(cx,MODE):
            retry_at=(int(now)//86400+1)*86400+1
            cx.execute("UPDATE reviews SET model_status='limit',model_error='quota',model_retry_at=?,reason='Лимит ИИ исчерпан; повтор после обновления дневной квоты' WHERE id=?",(retry_at,row['id']))
            event(cx,row['id'],'hermes_quota_deferred',str(retry_at))
            return 'limit'
        cx.execute("UPDATE reviews SET model_status='running',model_error=NULL,model_retry_at=NULL,model_attempts=model_attempts+1,hermes_attempted=1,reason='ИИ готовит ответ',updated_at=? WHERE id=?",(now,row['id']))
        event(cx,row['id'],'hermes_started',str(row['model_attempts']+1))
        complaint,_=complaint_plan(cx,row,p,product)
        style=v4_config(cx,product)
        attempt=row['model_attempts']+1
    finally:cx.close()
    rid=row['id'];source=row['source_hash'];revision=row['revision'];draft_version=row['draft_version']
    try:
        text,used=generate_fn(row,p,product,row['draft'] or '',settings=style,plan=complaint)
        selection=None
        if row['campaign_id']:
            cx=connect(path)
            try:selection=select_campaign(cx,row,product,MODE)
            finally:cx.close()
        expected=json.loads(row['recommendation_ids'] or '[]')
        valid_offer=bool(selection and selection['campaign'] and selection['campaign']['id']==row['campaign_id'] and
                         selection['campaign']['version']==row['campaign_version'] and
                         [x['id'] for x in selection['selected']]==expected)
        if valid_offer:text+=upsell_text(selection)
        validate_composed(text,product,p,row['kind'],selection if valid_offer else None)
    except Exception as exc:
        kind=model_failure_kind(exc)
        retry=kind=='transient' and attempt<MAX_MODEL_ATTEMPTS
        delay=min(300,30*2**(attempt-1)) if retry else None
        state='retry' if retry else 'failed'
        reason=('ИИ временно недоступен; повтор через '+str(delay)+' секунд' if retry else
                'ИИ вернул непроверенный текст; сохранён утверждённый черновик' if kind=='content' else
                'ИИ недоступен из-за настройки; сохранён утверждённый черновик' if kind=='configuration' else
                'ИИ не ответил после '+str(MAX_MODEL_ATTEMPTS)+' попыток; сохранён утверждённый черновик')
        cx=connect(path)
        try:
            changed=cx.execute("""UPDATE reviews SET model_status=?,model_error=?,model_retry_at=?,reason=?,updated_at=?
                WHERE id=? AND source_hash=? AND revision=? AND draft_version=? AND manager_edited=0
                AND status='needs_review' AND model_status='running'""",
                (state,kind,now+delay if retry else None,reason,now,rid,source,revision,draft_version)).rowcount
            event(cx,rid,'hermes_'+kind if changed else 'hermes_discarded_after_edit',type(exc).__name__)
        finally:cx.close()
        return state if changed else 'discarded'
    cx=connect(path)
    try:
        current=profile(cx)
        current_product,_=(current.match_question(MODE,row) if row['kind']=='question' else current.match(MODE,row))
        if not current_product or revision!=policy_revision(current,policy_for(cx,POLICY,current_product)):
            event(cx,rid,'hermes_discarded_after_policy_change')
            return 'discarded'
        auto=bool(row['v4_route_auto'])
        grant=approval(text,source,revision,'public' if row['kind']=='question' else None) if auto else None
        changed=cx.execute("""UPDATE reviews SET draft=?,generator=?,fact_ids=?,model_status='ready',
            model_error=NULL,model_retry_at=NULL,reason=?,draft_version=draft_version+1,status=?,
            approval_hash=?,approval_kind=?,campaign_id=?,campaign_version=?,recommendation_ids=?,updated_at=?
            WHERE id=? AND source_hash=? AND revision=? AND draft_version=? AND manager_edited=0
            AND status='needs_review' AND model_status='running'""",
            (text,'ИИ · адаптированный ответ, утверждённая процедура' if complaint else 'Сгенерировано ИИ',
             json.dumps(used),'Проверенный ответ ИИ готов к автоотправке' if auto else 'Ответ ИИ готов; требуется решение менеджера',
             'ready_auto' if auto else 'needs_review',grant,'auto_policy' if auto else None,
             row['campaign_id'] if valid_offer else None,row['campaign_version'] if valid_offer else None,
             json.dumps(expected if valid_offer else []),now,rid,source,revision,draft_version)).rowcount
        event(cx,rid,'hermes_draft' if changed else 'hermes_discarded_after_edit')
        if changed and auto:WAKE.set()
        return 'ready' if changed else 'discarded'
    finally:cx.close()

def hermes_loop():
    window_started=time.monotonic();window_calls=0
    while not STOP.is_set():
        if time.monotonic()-window_started>=float(POLICY['sandbox_poll_seconds']):
            window_started=time.monotonic();window_calls=0
        if window_calls>=int(POLICY['manual_llm_max_per_cycle']):STOP.wait(5);continue
        paths=[DATA/'sandbox.sqlite3'] if MODE=='sandbox' else sorted(DATA.glob('*.sqlite3'),key=lambda x:x.stat().st_mtime,reverse=True)[:20]
        worked=False
        for path in paths:
            result=hermes_step(path)
            if result not in ('idle','paused'):
                worked=True
                if result in ('ready','retry','failed','discarded'):window_calls+=1
                break
        STOP.wait(2 if worked else 5)

@asynccontextmanager
async def lifespan(app):
    STOP.clear();thread=threading.Thread(target=worker_loop,name='wb-v2-worker',daemon=True);thread.start()
    model_thread=threading.Thread(target=hermes_loop,name='wb-v2-hermes',daemon=True);model_thread.start()
    yield
    STOP.set();WAKE.set();thread.join(timeout=3);model_thread.join(timeout=3)

app=FastAPI(title='ВБ-отзовик V2',docs_url=None,redoc_url=None,openapi_url=None,lifespan=lifespan)
app.mount('/static',StaticFiles(directory=ROOT/'static'),name='static')

@app.middleware('http')
async def guard(request:Request,call_next):
    sid=request.cookies.get('demo_session_v3','')
    if MODE=='demo':request.state.demo_session=sid if re.fullmatch('[0-9a-f]{32}',sid) else secrets.token_hex(16)
    else:
        auth=request.headers.get('Authorization','');expected=(ROOT/'.data/operator-key').read_text().strip()
        try:user,pw=base64.b64decode(auth.removeprefix('Basic '),validate=True).decode().split(':',1)
        except Exception:user=pw=''
        if not (auth.startswith('Basic ') and hmac.compare_digest(user,'operator') and hmac.compare_digest(pw,expected)):
            return Response(status_code=401,headers={'WWW-Authenticate':'Basic realm="WB Sandbox operator"'})
    if request.method not in ('GET','HEAD'):
        origin=request.headers.get('origin')
        if origin and origin!=str(request.base_url).rstrip('/'):
            return JSONResponse({'detail':'Origin mismatch'},status_code=403)
    response=await call_next(request)
    response.headers['Cache-Control']='no-store'
    response.headers['X-Content-Type-Options']='nosniff'
    response.headers['Content-Security-Policy']="default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' https://*.wbbasket.ru https://*.wildberries.ru; connect-src 'self'; frame-ancestors 'none'"
    if MODE=='demo' and sid!=request.state.demo_session:response.set_cookie('demo_session_v3',request.state.demo_session,httponly=True,samesite='strict')
    return response

@app.get('/')
def index():return FileResponse(ROOT/('static/v4.html' if os.getenv('WB_UI_V4')=='1' else 'static/v2.html'))

@app.get('/api/v2/state')
def state(request:Request,tab:str='attention',product_id:str='',q:str='',offset:int=0,limit:int=30,kind:str='feedback',rating:int=0,sort:str='newest'):
    if tab not in ('waiting','attention','processing','deferred','all','done','errors'):raise HTTPException(400,'Неизвестная вкладка')
    if kind not in ('feedback','question'):raise HTTPException(400,'Неизвестный тип обращения')
    if rating not in (0,1,2,3,4,5) or sort not in ('oldest','newest'):raise HTTPException(400,'Неверный фильтр')
    limit=min(max(limit,1),100);offset=max(offset,0)
    cx=db_for(request);p=profile(cx)
    try:
        rows=cx.execute('SELECT id,nm_id,remote_size,remote_article,status,model_status FROM reviews WHERE kind=?',(kind,)).fetchall()
        card_groups=p.card_groups()
        card_for={item['product_id']:group[0]['product_id'] for group in card_groups for item in group}
        card_counts={group[0]['product_id']:{'all':0,'waiting':0,'attention':0,'processing':0,'deferred':0,'done':0,'errors':0}
                     for group in card_groups}
        counts={'all':0,'waiting':0,'attention':0,'deferred':0,'done':0,'errors':0,'processing':0,'no_reply':0}
        for row in rows:
            pid=(p.match_question(MODE,row) if kind=='question' else p.match(MODE,row))[0]
            pid=card_for.get(pid['product_id']) if pid else None
            keys=['all']
            if row['status'] not in ('api_confirmed','remote_answered','no_reply_needed'):keys.append('waiting')
            if row['status']=='needs_review':keys.append('attention')
            if row['status']=='deferred':keys.append('deferred')
            if row['status'] in ('api_confirmed','remote_answered'):keys.append('done')
            if row['status'] in ('failed','outcome_unknown','remote_changed') or row['model_status']=='failed':keys.append('errors')
            if row['status'] in ('new','ready_auto','sending') or row['model_status'] in ('pending','running'):keys.append('processing')
            if row['status']=='no_reply_needed':keys.append('no_reply')
            for key in keys:
                counts[key]+=1
                if key in card_counts.get(pid,{}):card_counts[pid][key]+=1
        where=['kind=?'];args=[kind]
        if tab=='attention':where.append("status='needs_review'")
        elif tab=='processing':where.append("(status IN ('new','ready_auto','sending') OR model_status IN ('pending','running'))")
        elif tab=='waiting':where.append("status NOT IN ('api_confirmed','remote_answered','no_reply_needed')")
        elif tab=='deferred':where.append("status='deferred'")
        elif tab=='done':where.append("status IN ('api_confirmed','remote_answered')")
        elif tab=='errors':where.append("(status IN ('failed','outcome_unknown','remote_changed') OR model_status='failed')")
        if product_id:
            card_sql,card_args=card_where(p,MODE,kind,product_id)
            where.append(card_sql);args.extend(card_args)
        if q:
            where.append('(body LIKE ? OR pros LIKE ? OR cons LIKE ? OR id LIKE ?)')
            args.extend(['%'+q[:100]+'%']*4)
        if rating and kind=='feedback':where.append('rating=?');args.append(rating)
        clause='WHERE '+' AND '.join(where) if where else ''
        total=cx.execute('SELECT COUNT(*) FROM reviews '+clause,args).fetchone()[0]
        order='ORDER BY COALESCE(julianday(created_date),updated_at/86400+2440587.5) '+('ASC' if sort=='oldest' else 'DESC')+',id'
        selected=cx.execute('SELECT * FROM reviews '+clause+' '+order+' LIMIT ? OFFSET ?',(*args,limit,offset)).fetchall()
        return {'mode':MODE,'kind':kind,'counts':counts,'variants':[{'product':p.present(group[0]),
                'sizes':[str(item['size']) for item in group],
                'counts':card_counts[group[0]['product_id']]} for group in card_groups],
                'reviews':[public_row(x,p,cx) for x in selected],'total':total,'offset':offset,'limit':limit,
                'processing_enabled':setting(cx,'processing_enabled','1')=='1',
                'auto_send_enabled':setting(cx,'auto_send_enabled','0')=='1',
                'last_sync':setting(cx,'last_sync'),
                'revision':policy_revision(p,policy_for(cx,POLICY))[:12]}
    finally:cx.close()

@app.get('/api/v2/reviews/{rid}')
def review(rid:str,request:Request):
    cx=db_for(request)
    try:return public_row(get_row(cx,rid),profile(cx),cx)
    finally:cx.close()

class SettingBody(BaseModel):
    processing_enabled:bool|None=None
    auto_send_enabled:bool|None=None

@app.put('/api/v2/settings')
def update_settings(body:SettingBody,request:Request):
    if body.processing_enabled is None and body.auto_send_enabled is None:raise HTTPException(400,'Нет изменения')
    cx=db_for(request)
    try:
        if body.processing_enabled is not None:set_setting(cx,'processing_enabled',int(body.processing_enabled))
        if body.auto_send_enabled is not None:set_setting(cx,'auto_send_enabled',int(body.auto_send_enabled))
        event(cx,None,'settings',json.dumps(body.model_dump(exclude_none=True)))
        WAKE.set()
        return {'processing_enabled':setting(cx,'processing_enabled','1')=='1','auto_send_enabled':setting(cx,'auto_send_enabled','0')=='1'}
    finally:cx.close()

class SendBody(BaseModel):
    text:str=Field(min_length=2,max_length=700)
    source_hash:str
    revision:str
    audience:str|None=None
    draft_version:int|None=None

@app.post('/api/v2/reviews/{rid}/send')
def send(rid:str,body:SendBody,request:Request):
    cx=db_for(request)
    try:
        row=get_row(cx,rid)
        if row['kind']=='question' and body.draft_version is None:
            raise HTTPException(422,'Для вопроса нужна актуальная версия черновика и аудитории')
        return send_reserved(cx,rid,body.text,body.source_hash,body.revision,'manager',audience=body.audience,draft_version=body.draft_version)
    finally:cx.close()

class TemplateBody(BaseModel):
    name:str=Field(min_length=2,max_length=80)
    kind:str
    intent:str
    body:str=Field(min_length=2,max_length=700)

@app.get('/api/v2/templates')
def templates(request:Request,kind:str=''):
    if kind and kind not in ('feedback','question'):raise HTTPException(400,'Неизвестный тип')
    cx=db_for(request)
    try:return {'templates':list_templates(cx,kind or None)}
    finally:cx.close()

@app.post('/api/v2/templates')
def create_template(body:TemplateBody,request:Request):
    try:params=validate_definition(body.name,body.kind,body.intent,body.body)
    except ValueError as exc:raise HTTPException(422,str(exc))
    cx=db_for(request)
    try:
        ensure_templates(cx);tid='custom-'+secrets.token_hex(8)
        cx.execute('INSERT INTO answer_templates VALUES(?,?,?,?,?,?,0,?)',
                   (tid,body.name.strip(),body.kind,body.intent,body.body.strip(),json.dumps(params),time.time()))
        return {'id':tid,'params':params}
    finally:cx.close()

@app.put('/api/v2/templates/{tid}')
def update_template(tid:str,body:TemplateBody,request:Request):
    try:params=validate_definition(body.name,body.kind,body.intent,body.body)
    except ValueError as exc:raise HTTPException(422,str(exc))
    cx=db_for(request)
    try:
        ensure_templates(cx);old=cx.execute('SELECT is_builtin FROM answer_templates WHERE id=?',(tid,)).fetchone()
        if not old:raise HTTPException(404,'Шаблон не найден')
        if old['is_builtin']:raise HTTPException(403,'Встроенный шаблон доступен только для чтения; создайте свой')
        cx.execute('UPDATE answer_templates SET name=?,kind=?,intent=?,body=?,params=?,updated_at=? WHERE id=?',
                   (body.name.strip(),body.kind,body.intent,body.body.strip(),json.dumps(params),time.time(),tid))
        return {'id':tid,'params':params}
    finally:cx.close()

class TemplateChoice(BaseModel):
    template_id:str
    source_hash:str
    revision:str
    draft_version:int|None=None

@app.post('/api/v2/reviews/{rid}/template')
def choose_template(rid:str,body:TemplateChoice,request:Request):
    cx=db_for(request)
    try:
        ensure_templates(cx);row=get_row(cx,rid);p=profile(cx)
        matched,_=(p.match_question(MODE,row) if row['kind']=='question' else p.match(MODE,row))
        if row['source_hash']!=body.source_hash or row['revision']!=body.revision or body.revision!=policy_revision(p,policy_for(cx,POLICY,matched)):
            raise HTTPException(409,'Профиль или обращение изменились')
        template=cx.execute('SELECT * FROM answer_templates WHERE id=?',(body.template_id,)).fetchone()
        if not template:raise HTTPException(404,'Шаблон не найден')
        product,_=(p.match_question(MODE,row) if row['kind']=='question' else p.match(MODE,row))
        try:
            text,fids=render_template(dict(template),p,product,row['kind'],row['intent'])
            text=validate_reply(text,product,p,row['kind'])
        except ValueError as exc:raise HTTPException(422,str(exc))
        source='Шаблон · '+template['name']
        if body.draft_version is not None:
            cx.execute('BEGIN IMMEDIATE')
            try:
                fresh=get_row(cx,rid)
                if fresh['draft_version']!=body.draft_version or fresh['source_hash']!=row['source_hash'] or fresh['revision']!=row['revision'] or fresh['status'] not in ('needs_review','ready_auto','deferred'):
                    raise HTTPException(409,'Черновик изменился; обновите обращение')
                cx.execute("UPDATE reviews SET draft=?,generator=?,fact_ids=?,draft_version=draft_version+1,manager_edited=1,approval_hash=NULL,approval_kind=NULL,model_status='edited',status=CASE WHEN status='ready_auto' THEN 'needs_review' ELSE status END,updated_at=? WHERE id=?",
                           (text,source,json.dumps(fids),time.time(),rid))
                event(cx,rid,'template_inserted',template['id']);cx.commit()
            except Exception:cx.rollback();raise
        return {'text':text,'fact_ids':fids,'source':source,'draft_version':row['draft_version']+1 if body.draft_version is not None else row['draft_version']}
    finally:cx.close()

class DraftBody(BaseModel):
    text:str=Field(max_length=700)
    source_hash:str
    revision:str
    draft_version:int

class AudienceBody(BaseModel):
    audience:str
    source_hash:str
    revision:str
    draft_version:int

@app.put('/api/v2/reviews/{rid}/audience')
def set_audience(rid:str,body:AudienceBody,request:Request):
    if body.audience not in ('public','private'):raise HTTPException(422,'Выберите аудиторию вопроса')
    cx=db_for(request)
    try:
        cx.execute('BEGIN IMMEDIATE');row=get_row(cx,rid)
        if row['kind']!='question':raise HTTPException(400,'У отзыва нет настройки аудитории')
        if row['source_hash']!=body.source_hash or row['revision']!=body.revision or row['draft_version']!=body.draft_version or row['status'] not in ('needs_review','ready_auto','deferred'):
            raise HTTPException(409,'Вопрос изменился; обновите его')
        cx.execute("UPDATE reviews SET audience=?,approval_hash=NULL,approval_kind=NULL,draft_version=draft_version+1,status=CASE WHEN status='ready_auto' THEN 'needs_review' ELSE status END WHERE id=?",
                   (body.audience,rid))
        event(cx,rid,'audience_selected',body.audience);cx.commit()
        return {'audience':body.audience,'draft_version':body.draft_version+1,
                'api_supported':body.audience=='public'}
    except Exception:cx.rollback();raise
    finally:cx.close()

@app.put('/api/v2/reviews/{rid}/draft')
def save_draft(rid:str,body:DraftBody,request:Request):
    cx=db_for(request)
    try:
        cx.execute('BEGIN IMMEDIATE');row=get_row(cx,rid)
        if row['source_hash']!=body.source_hash or row['revision']!=body.revision or row['draft_version']!=body.draft_version or row['status'] not in ('needs_review','ready_auto','deferred'):
            raise HTTPException(409,'Черновик изменился; обновите обращение')
        cx.execute("UPDATE reviews SET draft=?,generator='Правка менеджера',fact_ids='[]',draft_version=draft_version+1,manager_edited=1,approval_hash=NULL,approval_kind=NULL,model_status='edited',status=CASE WHEN status='ready_auto' THEN 'needs_review' ELSE status END,updated_at=? WHERE id=?",
                   (body.text,time.time(),rid))
        event(cx,rid,'manager_draft');cx.commit()
        return {'draft_version':body.draft_version+1,'text':body.text}
    except Exception:cx.rollback();raise
    finally:cx.close()

class RephraseBody(BaseModel):
    text:str=Field(max_length=700)
    source_hash:str
    revision:str
    draft_version:int

@app.post('/api/v2/reviews/{rid}/rephrase')
def rephrase(rid:str,body:RephraseBody,request:Request):
    cx=db_for(request)
    try:
        row=get_row(cx,rid);p=profile(cx)
        matched,_=(p.match_question(MODE,row) if row['kind']=='question' else p.match(MODE,row))
        if row['source_hash']!=body.source_hash or row['revision']!=body.revision or body.revision!=policy_revision(p,policy_for(cx,POLICY,matched)) or row['draft_version']!=body.draft_version or row['status'] not in ('needs_review','ready_auto','deferred'):
            raise HTTPException(409,'Обращение или черновик изменились')
        if MODE=='demo':
            ids={x['id'] for x in json.loads((ROOT/'data/demo_reviews_v2.json').read_text())}
            ids.update('q:'+x['id'] for x in json.loads((ROOT/'data/demo_questions_v3.json').read_text()))
            if os.getenv('WB_V4_FIXTURES')=='1':
                ids.update(x['id'] for x in json.loads((ROOT/'data/demo_reviews_v4.json').read_text()))
            if rid not in ids:raise HTTPException(403,'В Demo ИИ доступен только для синтетических обращений')
        product,_=(p.match_question(MODE,row) if row['kind']=='question' else p.match(MODE,row))
        if not product:raise HTTPException(422,'Карточка товара не сопоставлена с профилем')
        style=v4_config(cx,product);complaint,_=complaint_plan(cx,row,p,product)
        if complaint_choose(row,p,product) and not complaint:raise HTTPException(422,'Нет утверждённой процедуры для этой претензии; откройте библиотеку сценариев')
        if not reserve_hermes(cx,MODE):raise HTTPException(429,'Дневной лимит генерации ИИ исчерпан; используйте шаблон или сохранённый черновик')
        cx.execute("UPDATE reviews SET model_status='running' WHERE id=?",(rid,));event(cx,rid,'hermes_rephrase_started')
    finally:cx.close()
    try:text,used=generate(row,p,product,body.text,settings=style,plan=complaint,editorial=True)
    except Exception as exc:
        cx=db_for(request)
        try:
            cx.execute("UPDATE reviews SET model_status='failed',model_error='content',reason='ИИ не подготовил проверенный ответ; сохранён черновик менеджера' WHERE id=? AND draft_version=?",(rid,body.draft_version))
            event(cx,rid,'hermes_rephrase_failed',type(exc).__name__)
        finally:cx.close()
        return {'status':'failed','detail':'ИИ не подготовил проверенный ответ; текущий текст сохранён','draft':body.text}
    cx=db_for(request)
    try:
        current=profile(cx)
        fresh_product,_=(current.match_question(MODE,row) if row['kind']=='question' else current.match(MODE,row))
        if not fresh_product or body.revision!=policy_revision(current,policy_for(cx,POLICY,fresh_product)):
            raise HTTPException(409,'Правила изменились во время генерации; сохранён прежний черновик')
        source='ИИ · адаптированный ответ, утверждённая процедура' if complaint else 'Сгенерировано ИИ'
        cx.execute('BEGIN IMMEDIATE')
        previous=get_row(cx,rid)
        if previous['source_hash']!=body.source_hash or previous['revision']!=body.revision or previous['draft_version']!=body.draft_version:
            raise HTTPException(409,'Черновик изменился во время генерации; ответ ИИ не применён')
        cx.execute('INSERT INTO review_history(review_id,snapshot,at) VALUES(?,?,?)',
                   (rid,json.dumps(dict(previous),ensure_ascii=False),time.time()))
        changed=cx.execute("UPDATE reviews SET draft=?,generator=?,fact_ids=?,model_status='ready',model_error=NULL,reason='Ответ ИИ готов; требуется решение менеджера',draft_version=draft_version+1,manager_edited=1,approval_hash=NULL,approval_kind=NULL,status=CASE WHEN status='ready_auto' THEN 'needs_review' ELSE status END,updated_at=? WHERE id=? AND source_hash=? AND revision=? AND draft_version=? AND status IN ('needs_review','ready_auto','deferred')",
                           (text,source,json.dumps(used),time.time(),rid,body.source_hash,body.revision,body.draft_version)).rowcount
        if not changed:raise HTTPException(409,'Черновик изменился во время генерации; ответ ИИ не применён')
        event(cx,rid,'hermes_rephrase_ready');cx.commit()
        return {'status':'ready','text':text,'source':source,'draft_version':body.draft_version+1}
    except Exception:cx.rollback();raise
    finally:cx.close()

class DeferBody(BaseModel):
    reason:str=Field(min_length=3,max_length=300)

@app.post('/api/v2/reviews/{rid}/defer')
def defer(rid:str,body:DeferBody,request:Request):
    cx=db_for(request)
    try:
        row=get_row(cx,rid)
        if row['status'] not in ('needs_review','deferred','ready_auto'):raise HTTPException(409,'Отзыв уже не в очереди')
        cx.execute("UPDATE reviews SET status='deferred',defer_reason=?,approval_hash=NULL,approval_kind=NULL,updated_at=? WHERE id=?",(body.reason,time.time(),rid))
        event(cx,rid,'defer',body.reason)
        return {'status':'deferred'}
    finally:cx.close()

@app.post('/api/v2/reviews/{rid}/reconcile')
def reconcile_review(rid:str,request:Request):
    if MODE!='sandbox':raise HTTPException(404)
    cx=db_for(request)
    try:
        row=get_row(cx,rid)
        if row['status'] not in ('sending','outcome_unknown') or not row['sent_text']:
            raise HTTPException(409,'Нет неопределённой отправки для проверки')
        try:remote=readback_with_retry(WB(),row['remote_id'] or rid,row['kind'])
        except Exception as exc:raise HTTPException(502,'Контрольное чтение не выполнено: '+type(exc).__name__)
        answer=(remote or {}).get('answer') or {}
        if answer.get('text')==row['sent_text']:
            cx.execute("UPDATE reviews SET status='api_confirmed',remote_answer=?,reason='Ответ подтверждён контрольным чтением',updated_at=? WHERE id=?",(row['sent_text'],time.time(),rid))
            observe_remote(cx,rid,remote)
            event(cx,rid,'manual_reconcile_confirmed')
            return {'status':'api_confirmed'}
        cx.execute("UPDATE reviews SET status='outcome_unknown',reason='Контрольное чтение не нашло совпадающий ответ' WHERE id=?",(rid,))
        event(cx,rid,'manual_reconcile_unknown')
        return {'status':'outcome_unknown'}
    finally:cx.close()

@app.post('/api/v2/reviews/{rid}/retry-preflight')
def retry_preflight(rid:str,request:Request):
    cx=db_for(request)
    try:
        row=get_row(cx,rid)
        reason=row['reason'] or ''
        safe_failure=reason.startswith(('Проверка перед POST не выполнена','Проверка перед отправкой не выполнена'))
        changed_failure=reason=='Отзыв в WB изменился, уже отвечен или вариант не совпал'
        if row['status']!='failed' or not (safe_failure or changed_failure) or row['send_attempts']!=1:
            raise HTTPException(409,'Повтор возможен только если POST точно не выполнялся')
        p=profile(cx)
        found,_=(p.match_question(MODE,row) if row['kind']=='question' else p.match(MODE,row))
        if not found:raise HTTPException(409,'Вариант товара больше не сопоставлен')
        if changed_failure:
            remote=readback_with_retry(WB(),row['remote_id'] or rid,row['kind'])
            expected=(normalize_question(remote_question(remote)) if row['kind']=='question' else normalize(remote_row(remote))) if remote else None
            local=(normalize_question({'id':row['remote_id'] or rid,'nm_id':row['nm_id'],'body':row['body'],'remote_article':row['remote_article'],
                                       'user_name':row['user_name'],'created_date':row['created_date']}) if row['kind']=='question' else normalize(dict(row)))
            if not remote or (remote.get('answer') or {}).get('text') or expected!=local:
                raise HTTPException(409,'Данные в WB всё ещё отличаются; синхронизируйте обращение')
        current=policy_revision(p,policy_for(cx,POLICY,found))
        cx.execute('BEGIN IMMEDIATE')
        fresh=get_row(cx,rid)
        if fresh['status']!='failed' or fresh['send_attempts']!=1 or fresh['source_hash']!=row['source_hash']:
            raise HTTPException(409,'Обращение изменилось; обновите экран')
        cx.execute("UPDATE reviews SET status='needs_review',send_attempts=0,approval_hash=NULL,approval_kind=NULL,revision=?,draft_version=draft_version+1,reason='Проверка WB перед POST не прошла; подтвердите ответ заново',updated_at=? WHERE id=?",(current,time.time(),rid))
        event(cx,rid,'preflight_requeued')
        cx.commit()
        return {'status':'needs_review'}
    except Exception:
        cx.rollback();raise
    finally:cx.close()

@app.get('/api/v2/profiles')
def profiles(request:Request):
    cx=db_for(request);p=profile(cx)
    try:return {'revision':policy_revision(p,policy_for(cx,POLICY))[:12],
            'models':[{'model_id':m['model_id'],'name':m['name']} for m in p.models.values()],
            'variants':[{'product':p.present(x),'facts':[{'field':f['field'],'value':f['value'],'source':f['source'],'status':f['status']} for f in p.facts(x['product_id']).values()],
                         'size_advice':[r['response'] for r in p.data['Советы_размера'] if r['model_id']==x['model_id'] and r['status']=='approved'],
                         'recommendations':[r['reason'] for r in p.recommendations(x['product_id'])]}
                        for x in p.products.values()]}
    finally:cx.close()

@app.get('/api/v2/profiles/file')
def profile_file(request:Request):
    private=DATA/'profiles.xlsx'
    bundled=ROOT/'data/profiles_v4.xlsx' if os.getenv('WB_UI_V4')=='1' and MODE=='demo' else ROOT/'data/profiles.xlsx'
    path=private if private.exists() else bundled
    return FileResponse(path,filename='profiles.xlsx',media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')

@app.put('/api/v2/profiles/file')
async def replace_profile(request:Request):
    if MODE!='sandbox':raise HTTPException(404,'В Demo профиль доступен только для просмотра')
    raw=await request.body()
    if len(raw)>2_000_000 or not raw.startswith(b'PK'):
        raise HTTPException(422,'Нужен XLSX размером до 2 МБ')
    temp=DATA/('profile-upload-'+secrets.token_hex(8)+'.xlsx')
    try:
        temp.write_bytes(raw);temp.chmod(0o600)
        try:new=load_profile(temp)
        except Exception as exc:raise HTTPException(422,'Профиль не прошёл проверку: '+str(exc))
        saved=ROOT/'.data/sandbox/ids.json'
        if saved.exists():
            ids=json.loads(saved.read_text())
            mapped=new.products.get(ids['product_id'])
            if not mapped or mapped.get('sandbox_nmID')!=ids['nmID']:
                raise HTTPException(422,'Сопоставление существующей тестовой карточки отсутствует или изменено')
        current=DATA/'profiles.xlsx'
        if current.exists():
            backup=DATA/('profiles-before-'+str(int(time.time()))+'-'+secrets.token_hex(4)+'.xlsx')
            shutil.copy2(current,backup);backup.chmod(0o600)
        os.replace(temp,current)
        cx=connect(DATA/'sandbox.sqlite3')
        try:
            cx.execute("UPDATE reviews SET status='new',draft=NULL,generator=NULL,fact_ids='[]',revision=NULL,approval_hash=NULL,approval_kind=NULL,hermes_attempted=0,model_status='not_needed',reason='Профиль обновлён; ответ готовится заново',updated_at=? WHERE status IN ('new','ready_auto','needs_review','deferred')",(time.time(),))
            event(cx,None,'profile_import',new.revision[:12])
        finally:cx.close()
        WAKE.set()
        return {'revision':policy_revision(new,POLICY)[:12],'detail':'Профиль проверен и установлен. Неотправленные ответы готовятся заново.'}
    finally:
        temp.unlink(missing_ok=True)

from .v4_api import router as v4_router
app.include_router(v4_router)
