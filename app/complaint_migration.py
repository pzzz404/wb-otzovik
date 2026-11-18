"""Idempotent V4 complaint-contract migration for an isolated database copy."""
import json,time
from .v2_store import event,setting,set_setting
from .v4_store import init
from .templates import ensure

MARKER='complaint_library_migration_v2'
UNSENT=('new','ready_auto','needs_review','deferred')

def candidates(cx):
    return cx.execute("SELECT * FROM reviews WHERE status IN ('ready_auto','needs_review','deferred') AND send_attempts=0 ORDER BY id").fetchall()

def counts(cx):
    rows=candidates(cx)
    manual=[r for r in rows if r['manager_edited'] or r['approval_kind']=='manager' or r['status']=='deferred']
    machine=[r for r in rows if r not in manual and (r['draft'] or r['approval_hash'])]
    sent=cx.execute("SELECT COUNT(*) FROM reviews WHERE status='api_confirmed'").fetchone()[0]
    return {'machine_to_reprepare':len(machine),'manual_to_recheck':len(manual),
            'sent_untouched':sent,'already_applied':setting(cx,MARKER)=='1'}

def apply(cx,base_profile,batch_size=100):
    if setting(cx,MARKER)=='1':return {'applied':False,**counts(cx)}
    before=counts(cx)
    init(cx,base_profile);ensure(cx)
    rows=candidates(cx)
    cx.execute('BEGIN IMMEDIATE')
    try:
        for offset in range(0,len(rows),batch_size):
            for row in rows[offset:offset+batch_size]:
                manual=row['manager_edited'] or row['approval_kind']=='manager' or row['status']=='deferred'
                if not manual and not row['draft'] and not row['approval_hash']:continue
                cx.execute('INSERT INTO review_history(review_id,snapshot,at) VALUES(?,?,?)',
                           (row['id'],json.dumps(dict(row),ensure_ascii=False),time.time()))
                if manual:
                    cx.execute("UPDATE reviews SET status=CASE WHEN status='deferred' THEN 'deferred' ELSE 'needs_review' END,approval_hash=NULL,approval_kind=NULL,revision=NULL,model_status='stale',reason='Сценарий ответа изменился; проверьте сохранённый текст',updated_at=? WHERE id=?",
                               (time.time(),row['id']))
                    event(cx,row['id'],'complaint_migration_manual_recheck')
                else:
                    cx.execute("UPDATE reviews SET status='new',draft=NULL,generator=NULL,fact_ids='[]',revision=NULL,approval_hash=NULL,approval_kind=NULL,model_status='not_needed',hermes_attempted=0,reason='Прежний машинный черновик сохранён в истории; переподготовка',draft_version=draft_version+1,updated_at=? WHERE id=?",
                               (time.time(),row['id']))
                    event(cx,row['id'],'complaint_migration_machine_reprepare')
        set_setting(cx,MARKER,'1');event(cx,None,'complaint_library_migrated',json.dumps(before,ensure_ascii=False))
        cx.commit()
    except Exception:cx.rollback();raise
    return {'applied':True,**before}
