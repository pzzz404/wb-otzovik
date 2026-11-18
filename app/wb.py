"""Narrow sandbox-only WB adapter. Never follows redirects or sends to production."""
import threading,time
from urllib.parse import urlparse
from pathlib import Path
import httpx

SECRET=Path(__file__).resolve().parents[1] / '.env'
HOST='https://feedbacks-api-sandbox.wildberries.ru'
class WBError(Exception):
    def __init__(self,status):self.status=status;super().__init__(f'WB Sandbox HTTP {status}')
class WB:
    _lock=threading.Lock()
    _last_call=0.0
    def __init__(self,secret=SECRET,client=None):
        values={}
        if secret.exists():
            for line in secret.read_text().splitlines():
                if '=' in line and not line.lstrip().startswith('#'):
                    k,v=line.split('=',1);values[k.strip()]=v.strip().strip('"\'')
        if values.get('WB_SANDBOX_BASE_URL','').rstrip('/')!=HOST:raise ValueError('Only the approved Russian sandbox host is accepted')
        if not values.get('WB_SANDBOX_TOKEN'):raise ValueError('Sandbox token unavailable')
        self.token=values['WB_SANDBOX_TOKEN'];self.client=client or httpx.Client(timeout=12,trust_env=False,follow_redirects=False)
    def request(self,method,path,**kwargs):
        if path not in ['/api/v1/feedbacks','/api/v1/feedback','/api/v1/feedbacks/answer','/api/v1/test/make/feedbacks/batch',
                        '/api/v1/questions','/api/v1/question','/api/v1/test/make/questions/batch']:raise ValueError('Path denied')
        with WB._lock:
            delay=1.05-(time.monotonic()-WB._last_call)
            if delay>0:time.sleep(delay)
            WB._last_call=time.monotonic()
        r=self.client.request(method,HOST+path,headers={'Authorization':self.token,'Content-Type':'application/json'},**kwargs)
        if r.status_code not in (200,204):raise WBError(r.status_code)
        if r.status_code==204:return None
        body=r.json()
        if body.get('error'):raise WBError('api_error')
        return body.get('data')
    def list(self,answered=False,take=100,skip=0):
        data=self.request('GET','/api/v1/feedbacks',params={'isAnswered':str(answered).lower(),'take':take,'skip':skip})
        return data.get('feedbacks',[]) if isinstance(data,dict) else []
    def get(self,rid):
        data=self.request('GET','/api/v1/feedback',params={'id':rid})
        return data if isinstance(data,dict) and data.get('id')==rid else None
    def answer(self,rid,text):
        self.request('POST','/api/v1/feedbacks/answer',json={'id':rid,'text':text})
    def make(self,nmid,texts):
        return self.request('POST','/api/v1/test/make/feedbacks/batch',json={'feedbacks':[{'nmId':nmid,'texts':texts}]})
    def list_questions(self,answered=False,take=100,skip=0):
        data=self.request('GET','/api/v1/questions',params={'isAnswered':str(answered).lower(),'take':take,'skip':skip})
        return data.get('questions',[]) if isinstance(data,dict) else []
    def get_question(self,rid):
        data=self.request('GET','/api/v1/question',params={'id':rid})
        return data if isinstance(data,dict) and data.get('id')==rid else None
    def answer_question(self,rid,text):
        self.request('PATCH','/api/v1/questions',json={'id':rid,'answer':{'text':text},'state':'wbRu'})
    def make_questions(self,nmid,texts):
        return self.request('POST','/api/v1/test/make/questions/batch',json={'questions':[{'nmId':nmid,'texts':texts}]})

def remote_row(item):
    details=item.get('productDetails') or {}
    photos=[]
    for link in item.get('photoLinks') or []:
        if isinstance(link,dict):link=link.get('fullSize') or link.get('miniSize')
        if not isinstance(link,str):continue
        u=urlparse(link)
        host=(u.hostname or '').lower()
        if u.scheme=='https' and (host.endswith('.wbbasket.ru') or host=='wbbasket.ru' or host.endswith('.wildberries.ru')) and not u.username and not u.password:
            photos.append(link)
    video=item.get('video') or {}
    video_url=video.get('link') if isinstance(video,dict) else None
    if video_url:
        u=urlparse(video_url)
        if u.scheme!='https' or not (u.hostname or '').endswith('.wbbasket.ru'):video_url=None
    return {'id':item['id'],'nm_id':details.get('nmId') or 0,'rating':item.get('productValuation') or 0,
            'body':item.get('text') or '', 'pros':item.get('pros') or '', 'cons':item.get('cons') or '',
            'remote_size':details.get('size'),'remote_article':details.get('supplierArticle'),'photo_urls':photos,
            'order_status':item.get('orderStatus'),'matching_size':item.get('matchingSize'),
            'color':item.get('color'),'video_url':video_url,
            'user_name':item.get('userName'),'created_date':item.get('createdDate'),
            'parent_feedback_id':item.get('parentFeedbackId'),'child_feedback_id':item.get('childFeedbackId'),
            'remote_state':item.get('state'),'remote_answer_state':(item.get('answer') or {}).get('state'),
            'remote_answer_editable':(item.get('answer') or {}).get('editable')}

def remote_question(item):
    details=item.get('productDetails') or {}
    return {'id':item['id'],'nm_id':details.get('nmId') or 0,'body':item.get('text') or '',
            'remote_article':details.get('supplierArticle'),'remote_state':item.get('state'),
            'user_name':item.get('userName'),'created_date':item.get('createdDate')}
