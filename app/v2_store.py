"""Isolated V2 review state. SQLite is the authority for queue and pause controls."""
import json,sqlite3,time,threading
from pathlib import Path
from .core import digest

_migration_lock=threading.Lock()

def connect(path:Path):
    with _migration_lock:
        return _connect(path)

def _connect(path:Path):
    path.parent.mkdir(parents=True,exist_ok=True)
    cx=sqlite3.connect(path,timeout=30,isolation_level=None,check_same_thread=False)
    cx.row_factory=sqlite3.Row
    cx.execute('PRAGMA journal_mode=WAL')
    cx.execute('PRAGMA busy_timeout=30000')
    cx.execute('''CREATE TABLE IF NOT EXISTS reviews(
      id TEXT PRIMARY KEY, nm_id INTEGER NOT NULL, rating INTEGER NOT NULL,
      body TEXT NOT NULL, pros TEXT NOT NULL, cons TEXT NOT NULL,
      photo_urls TEXT NOT NULL DEFAULT '[]', remote_size TEXT, remote_article TEXT,
      source_hash TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'new',
      intent TEXT, reason TEXT, draft TEXT, generator TEXT, fact_ids TEXT NOT NULL DEFAULT '[]',
      revision TEXT, approval_hash TEXT, approval_kind TEXT, send_attempts INTEGER NOT NULL DEFAULT 0,
      sent_text TEXT, remote_answer TEXT, defer_reason TEXT, updated_at REAL NOT NULL)''')
    columns={x['name'] for x in cx.execute('PRAGMA table_info(reviews)')}
    if 'hermes_attempted' not in columns:cx.execute('ALTER TABLE reviews ADD COLUMN hermes_attempted INTEGER NOT NULL DEFAULT 0')
    if 'model_status' not in columns:cx.execute("ALTER TABLE reviews ADD COLUMN model_status TEXT NOT NULL DEFAULT 'not_needed'")
    if 'model_attempts' not in columns:cx.execute('ALTER TABLE reviews ADD COLUMN model_attempts INTEGER NOT NULL DEFAULT 0')
    if 'model_retry_at' not in columns:cx.execute('ALTER TABLE reviews ADD COLUMN model_retry_at REAL')
    if 'model_error' not in columns:cx.execute('ALTER TABLE reviews ADD COLUMN model_error TEXT')
    if 'kind' not in columns:cx.execute("ALTER TABLE reviews ADD COLUMN kind TEXT NOT NULL DEFAULT 'feedback'")
    if 'remote_id' not in columns:cx.execute('ALTER TABLE reviews ADD COLUMN remote_id TEXT')
    if 'remote_state' not in columns:cx.execute('ALTER TABLE reviews ADD COLUMN remote_state TEXT')
    if 'draft_version' not in columns:cx.execute('ALTER TABLE reviews ADD COLUMN draft_version INTEGER NOT NULL DEFAULT 0')
    if 'manager_edited' not in columns:cx.execute('ALTER TABLE reviews ADD COLUMN manager_edited INTEGER NOT NULL DEFAULT 0')
    if 'audience' not in columns:cx.execute('ALTER TABLE reviews ADD COLUMN audience TEXT')
    for name in ('order_status','matching_size','color','video_url','parent_feedback_id','child_feedback_id','remote_answer_state'):
        if name not in columns:cx.execute('ALTER TABLE reviews ADD COLUMN '+name+' TEXT')
    if 'remote_answer_editable' not in columns:cx.execute('ALTER TABLE reviews ADD COLUMN remote_answer_editable INTEGER')
    if 'source_changed' not in columns:cx.execute('ALTER TABLE reviews ADD COLUMN source_changed INTEGER NOT NULL DEFAULT 0')
    for name,definition in (
        ('scenario_id','TEXT'),('scenario_version','INTEGER'),('campaign_id','TEXT'),
        ('campaign_version','INTEGER'),('recommendation_ids',"TEXT NOT NULL DEFAULT '[]'"),
        ('v4_route_auto','INTEGER NOT NULL DEFAULT 0'),('user_name','TEXT'),('created_date','TEXT'),
        ('ingested_at','REAL'),('v4_backlog_prepared','INTEGER NOT NULL DEFAULT 0')):
        if name not in columns:cx.execute('ALTER TABLE reviews ADD COLUMN '+name+' '+definition)
    cx.execute('UPDATE reviews SET ingested_at=updated_at WHERE ingested_at IS NULL')
    cx.execute("UPDATE reviews SET remote_id=id WHERE remote_id IS NULL AND kind='feedback'")
    cx.execute('''CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY,value TEXT NOT NULL)''')
    cx.execute('''CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY AUTOINCREMENT,
      review_id TEXT, action TEXT NOT NULL, detail TEXT, at REAL NOT NULL)''')
    cx.execute('''CREATE TABLE IF NOT EXISTS review_history(id INTEGER PRIMARY KEY AUTOINCREMENT,
      review_id TEXT NOT NULL, snapshot TEXT NOT NULL, at REAL NOT NULL)''')
    return cx

def setting(cx,key,default=None):
    x=cx.execute('SELECT value FROM settings WHERE key=?',(key,)).fetchone()
    return x['value'] if x else default

def set_setting(cx,key,value):
    cx.execute('INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',(key,str(value)))

def event(cx,rid,action,detail=''):
    cx.execute('INSERT INTO events(review_id,action,detail,at) VALUES(?,?,?,?)',(rid,action,detail,time.time()))

def normalize(row):
    x={k:row.get(k) for k in ('id','nm_id','rating','body','pros','cons','remote_size','remote_article')}
    x['id']=str(x['id']);x['nm_id']=int(x['nm_id']);x['rating']=int(x['rating'])
    for k in ('body','pros','cons'):x[k]=str(x[k] or '')
    photos=row.get('photo_urls') or []
    if isinstance(photos,str):
        try:photos=json.loads(photos)
        except ValueError:photos=[]
    x['photo_urls']=[str(v) for v in photos if isinstance(v,str)]
    for k in ('order_status','matching_size','color','video_url','parent_feedback_id','child_feedback_id','user_name','created_date'):
        if row.get(k) is not None:x[k]=row[k]
    return x

def normalize_question(row):
    result={'id':'q:'+str(row['id']),'nm_id':int(row.get('nm_id') or 0),
            'body':str(row.get('body') or ''),'remote_article':row.get('remote_article')}
    for k in ('user_name','created_date'):
        if row.get(k) is not None:result[k]=row[k]
    return result

def import_review(cx,row):
    if str(row['id']).startswith('q:'):raise ValueError('Feedback ID overlaps the question namespace')
    x=normalize(row);source_hash=digest(x)
    old=cx.execute('SELECT * FROM reviews WHERE id=?',(x['id'],)).fetchone()
    if old and old['source_hash']==source_hash:return False
    if old and old['status'] in ('sending','api_confirmed','outcome_unknown'):
        cx.execute('INSERT INTO review_history(review_id,snapshot,at) VALUES(?,?,?)',
                   (x['id'],json.dumps(dict(old),ensure_ascii=False),time.time()))
        cx.execute('''UPDATE reviews SET nm_id=?,rating=?,body=?,pros=?,cons=?,photo_urls=?,
            remote_size=?,remote_article=?,source_hash=?,order_status=?,matching_size=?,color=?,
            video_url=?,parent_feedback_id=?,child_feedback_id=?,user_name=?,created_date=?,source_changed=1,ingested_at=?,v4_backlog_prepared=0,updated_at=? WHERE id=?''',
            (x['nm_id'],x['rating'],x['body'],x['pros'],x['cons'],json.dumps(x['photo_urls']),
             x['remote_size'],x['remote_article'],source_hash,x.get('order_status'),x.get('matching_size'),x.get('color'),
             x.get('video_url'),x.get('parent_feedback_id'),x.get('child_feedback_id'),x.get('user_name'),x.get('created_date'),time.time(),time.time(),x['id']))
        event(cx,x['id'],'source_changed_after_send','Prior answer retained; new send blocked')
        return True
    cx.execute('''INSERT INTO reviews(id,nm_id,rating,body,pros,cons,photo_urls,remote_size,remote_article,source_hash,status,updated_at,kind,remote_id,remote_state,ingested_at)
       VALUES(?,?,?,?,?,?,?,?,?,?,'new',?,'feedback',?,?,?) ON CONFLICT(id) DO UPDATE SET
       nm_id=excluded.nm_id,rating=excluded.rating,body=excluded.body,pros=excluded.pros,
       cons=excluded.cons,photo_urls=excluded.photo_urls,remote_size=excluded.remote_size,
       remote_article=excluded.remote_article,source_hash=excluded.source_hash,status='new',
       draft=NULL,generator=NULL,fact_ids='[]',revision=NULL,approval_hash=NULL,
       approval_kind=NULL,defer_reason=NULL,hermes_attempted=0,model_status='not_needed',model_attempts=0,model_retry_at=NULL,model_error=NULL,manager_edited=0,draft_version=draft_version+1,updated_at=excluded.updated_at,
       remote_state=excluded.remote_state,ingested_at=excluded.ingested_at,v4_backlog_prepared=0''',
       (x['id'],x['nm_id'],x['rating'],x['body'],x['pros'],x['cons'],json.dumps(x['photo_urls']),
        x['remote_size'],x['remote_article'],source_hash,time.time(),x['id'],row.get('remote_state'),time.time()))
    event(cx,x['id'],'import','new or changed source')
    cx.execute('''UPDATE reviews SET order_status=?,matching_size=?,color=?,video_url=?,
        parent_feedback_id=?,child_feedback_id=?,user_name=?,created_date=?,source_changed=0 WHERE id=?''',
        (x.get('order_status'),x.get('matching_size'),x.get('color'),x.get('video_url'),x.get('parent_feedback_id'),x.get('child_feedback_id'),x.get('user_name'),x.get('created_date'),x['id']))
    return True

def import_question(cx,row):
    x=normalize_question(row);source_hash=digest(x)
    old=cx.execute('SELECT source_hash,status FROM reviews WHERE id=?',(x['id'],)).fetchone()
    if old and old['source_hash']==source_hash:return False
    if old and old['status'] in ('sending','api_confirmed','outcome_unknown'):
        event(cx,x['id'],'source_changed_after_send','Question kept for reconciliation')
        return False
    cx.execute('''INSERT INTO reviews(id,nm_id,rating,body,pros,cons,photo_urls,remote_article,source_hash,status,updated_at,kind,remote_id,remote_state,ingested_at)
       VALUES(?,?,0,?,'','','[]',? ,?,'new',?,'question',?,?,?) ON CONFLICT(id) DO UPDATE SET
       nm_id=excluded.nm_id,body=excluded.body,remote_article=excluded.remote_article,
       source_hash=excluded.source_hash,status='new',draft=NULL,generator=NULL,fact_ids='[]',
       revision=NULL,approval_hash=NULL,approval_kind=NULL,defer_reason=NULL,
       hermes_attempted=0,model_status='not_needed',model_attempts=0,model_retry_at=NULL,model_error=NULL,manager_edited=0,audience=NULL,draft_version=draft_version+1,
       remote_state=excluded.remote_state,updated_at=excluded.updated_at,ingested_at=excluded.ingested_at,v4_backlog_prepared=0''',
       (x['id'],x['nm_id'],x['body'],x['remote_article'],source_hash,time.time(),str(row['id']),row.get('remote_state'),time.time()))
    event(cx,x['id'],'import','new or changed question')
    cx.execute('UPDATE reviews SET user_name=?,created_date=? WHERE id=?',(x.get('user_name'),x.get('created_date'),x['id']))
    return True

def observe_remote(cx,internal_id,item):
    row=cx.execute('SELECT status,kind,remote_answer FROM reviews WHERE id=?',(internal_id,)).fetchone()
    if not row:return
    answer=item.get('answer') or {}
    text=answer.get('text') if isinstance(answer,dict) else None
    state=item.get('state')
    answer_state=answer.get('state') if isinstance(answer,dict) else None
    editable=answer.get('editable') if isinstance(answer,dict) else None
    cx.execute('''UPDATE reviews SET remote_state=?,remote_answer=?,remote_answer_state=?,
       remote_answer_editable=? WHERE id=?''',
       (state,text,answer_state,None if editable is None else int(bool(editable)),internal_id))
    if text and row['status'] not in ('sending','api_confirmed','outcome_unknown'):
        cx.execute("UPDATE reviews SET status='remote_answered',draft=NULL,approval_hash=NULL WHERE id=?",(internal_id,))
    elif not text and row['status']=='remote_answered':
        cx.execute("UPDATE reviews SET status='new',draft=NULL,approval_hash=NULL,model_status='not_needed',manager_edited=0,draft_version=draft_version+1 WHERE id=?",(internal_id,))
        event(cx,internal_id,'remote_answer_disappeared')
    elif not text and row['status']=='api_confirmed' and row['kind']=='question' and row['remote_answer']:
        cx.execute("UPDATE reviews SET status='remote_changed',reason='WB больше не возвращает прежний ответ; проверьте вопрос в кабинете' WHERE id=?",(internal_id,))
        event(cx,internal_id,'remote_answer_disappeared_after_local_send')
