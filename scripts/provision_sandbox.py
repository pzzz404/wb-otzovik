"""Create one synthetic project card/review in WB Russian sandbox, with readback."""
import json,time
from pathlib import Path
import httpx
from openpyxl import load_workbook
from app.core import ROOT
from app.wb import WB,remote_row
wb = WB()
base = 'https://content-api-sandbox.wildberries.ru'
headers = {'Authorization': wb.token, 'Content-Type': 'application/json'}
state=ROOT/'.data/sandbox/ids.json';state.parent.mkdir(parents=True,exist_ok=True)
if state.exists():raise SystemExit('Already provisioned; refusing duplicate creation')
with httpx.Client(timeout=20,trust_env=False,follow_redirects=False) as client:
    bar=client.post(base+'/content/v2/barcodes',headers=headers,json={'count':1})
    print('barcode HTTP',bar.status_code)
    if bar.status_code!=200:raise SystemExit('Barcode creation blocked')
    data=bar.json().get('data') or []
    if not data:raise SystemExit('No barcode returned')
    sku=data[0]
    payload=[{'subjectID':192,'variants':[{'vendorCode':'SL-TS-01-WBTEST','title':'Футболка Роса тестовая','description':'Тестовая карточка проекта ВБ-отзовик. Молочная футболка, размер M.','brand':'Северная линия','dimensions':{'length':20,'width':15,'height':3,'weightBrutto':0.3},'characteristics':[],'sizes':[{'techSize':'M','wbSize':'46','skus':[sku]}],'documents':{'excludeDocuments':True}}]}]
    time.sleep(1.1)
    made=client.post(base+'/content/v2/cards/upload',headers=headers,json=payload)
    print('card upload HTTP',made.status_code,'error',made.json().get('error') if made.status_code==200 else 'unknown')
    if made.status_code!=200 or made.json().get('error'):raise SystemExit('Card upload blocked')
    found=[]
    for attempt in range(5):
        time.sleep(1.1)
        listed=client.post(base+'/content/v2/get/cards/list',headers=headers,json={'settings':{'sort':{'ascending':True},'cursor':{'limit':100},'filter':{'withPhoto':-1}}})
        if listed.status_code!=200:raise SystemExit(f'Card readback HTTP {listed.status_code}')
        found=[x for x in listed.json().get('cards') or [] if x.get('vendorCode')=='SL-TS-01-WBTEST']
        if found:break
    if len(found)!=1:raise SystemExit('Card not found by vendorCode after upload')
    nm=found[0]['nmID'];print('card readback confirmed',nm)
book=load_workbook(ROOT/'data/catalog.xlsx');sheet=book['Товары']
for row in sheet.iter_rows(min_row=2):
    if row[0].value=='TS-01':
        row[2].value=nm
        row[3].value='SL-TS-01-WBTEST'
private=ROOT/'.data/sandbox/catalog.xlsx';book.save(private);private.chmod(0o600)
created=wb.make(nm,['Спасибо, футболка понравилась!'])
ids=[x for x in created or [] if x.get('nmId')==nm]
feedback_ids=ids[0].get('feedbackIds') if ids else []
if len(feedback_ids)!=1:raise SystemExit('Feedback ID not returned; no answer attempted')
item=wb.get(feedback_ids[0])
if not item or remote_row(item)['nm_id']!=nm or item.get('answer'):raise SystemExit('Feedback readback failed; no answer attempted')
state.write_text(json.dumps({'product_id':'TS-01','nmID':nm,'feedback_id':feedback_ids[0]},indent=2));state.chmod(0o600)
print('feedback readback confirmed',feedback_ids[0])
