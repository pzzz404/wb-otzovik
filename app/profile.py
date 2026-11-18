"""Validated, versioned clothing profiles; model facts with explicit variant overrides."""
import hashlib,json,re
from pathlib import Path
from openpyxl import load_workbook
from .core import ROOT

SOURCE=ROOT/'data/profiles.xlsx'
HEADERS={
 'Модели':['model_id','name','category','prohibited_promises','active','version'],
 'Варианты':['product_id','model_id','demo_nmID','sandbox_nmID','seller_article','color','size','photo','active','version'],
 'Факты':['fact_id','scope','owner_id','field','value','override_of','source','status','verified_at','version'],
 'Советы_размера':['rule_id','model_id','condition_code','condition','response','source','status','version'],
 'Сочетания':['recommendation_id','source_product_id','target_product_id','scenario','reason','source','status','version','active'],
}
FIELDS={'composition','wash','dry','iron','fit','measurement','feature','contents'}
SIZE_TOKEN=r'(?:XXS|XS|S|M|L|XL|XXL|\d{2})'

def requested_sizes(question):
    """Only explicit size labels; a buyer's body measurement is not a size."""
    text=str(question or '').upper()
    found=[]
    for match in re.finditer(r'\b(?:МЕЖДУ|РАЗМЕР(?:АМИ|ОВ|Ы|А|Е|ОМ)?)\s+('+SIZE_TOKEN+
                             r')(?:\s*(?:И|ИЛИ|/|–|-)\s*('+SIZE_TOKEN+r'))?',text):
        found.extend(value for value in match.groups() if value)
    for match in re.finditer(r'\b('+SIZE_TOKEN+r')\s*/\s*('+SIZE_TOKEN+r')\b',text):
        found.extend(match.groups())
    return list(dict.fromkeys(found))

def load(path=SOURCE):
    book=load_workbook(path,read_only=True,data_only=True)
    data={}
    for sheet,header in HEADERS.items():
        if sheet not in book:raise ValueError('Нет листа '+sheet)
        rows=book[sheet].values
        if list(next(rows))!=header:raise ValueError('Неверные столбцы: '+sheet)
        data[sheet]=[dict(zip(header,row)) for row in rows if any(v is not None for v in row)]
    model={};product={};facts={};ids={'demo':{},'sandbox':{}};rules=set();recs=set()
    for x in data['Модели']:
        if not all(x.get(k) for k in ('model_id','name','category','prohibited_promises','version')) or x['model_id'] in model:raise ValueError('Некорректная модель')
        model[x['model_id']]=x
    for x in data['Варианты']:
        if not all(x.get(k) for k in ('product_id','model_id','seller_article','color','size','photo','version')) or x['product_id'] in product or x['model_id'] not in model:raise ValueError('Некорректный вариант')
        photo=ROOT/'static/products'/str(x['photo'])
        if Path(str(x['photo'])).name!=x['photo'] or not photo.is_file():raise ValueError('Отсутствует фото точного варианта: '+str(x['product_id']))
        for mode in ids:
            nm=x.get(mode+'_nmID')
            if nm is not None:
                if not isinstance(nm,int) or nm<=0:raise ValueError('Неверный nmID: '+mode)
                same=ids[mode].setdefault(nm,[])
                if same and (any(y['size']==x['size'] for y in same) or
                             any((y['model_id'],y['seller_article'],y['color'])!=(x['model_id'],x['seller_article'],x['color']) for y in same)):
                    raise ValueError('Повторный nmID без точной размерной границы: '+mode)
                same.append(x)
        product[x['product_id']]=x
    by_field={}
    for f in data['Факты']:
        if not all(f.get(k) for k in ('fact_id','scope','owner_id','field','value','source','status','verified_at','version')) or f['fact_id'] in facts or f['field'] not in FIELDS or f['status'] not in ('approved','draft','retired'):raise ValueError('Некорректный факт')
        if f['scope']=='model':
            if f['owner_id'] not in model or f['override_of']:raise ValueError('Некорректный факт модели')
        elif f['scope']=='variant':
            if f['owner_id'] not in product:raise ValueError('Некорректный факт варианта')
        else:raise ValueError('Неизвестная область факта')
        key=f['scope'],f['owner_id'],f['field']
        if key in by_field:raise ValueError('Конфликт фактов: '+str(key))
        by_field[key]=f;facts[f['fact_id']]=f
    for f in data['Факты']:
        if f['scope']=='variant':
            x=product[f['owner_id']];inherited=by_field.get(('model',x['model_id'],f['field']))
            if inherited and f['override_of']!=inherited['fact_id']:raise ValueError('Переопределение должно ссылаться на факт модели')
            if not inherited and f['override_of']:raise ValueError('Лишняя ссылка на переопределение')
    for r in data['Советы_размера']:
        if not all(r.get(k) for k in ('rule_id','model_id','condition_code','condition','response','source','status','version')) or r['rule_id'] in rules or r['model_id'] not in model or r['status'] not in ('approved','draft','retired'):raise ValueError('Некорректное правило размера')
        rules.add(r['rule_id'])
    for r in data['Сочетания']:
        if not all(r.get(k) for k in ('recommendation_id','source_product_id','target_product_id','scenario','reason','source','status','version')) or r['recommendation_id'] in recs or r['source_product_id'] not in product or r['target_product_id'] not in product or r['source_product_id']==r['target_product_id'] or r['status'] not in ('approved','draft','retired'):raise ValueError('Некорректное сочетание')
        recs.add(r['recommendation_id'])
    revision=hashlib.sha256(json.dumps({'data':data,'policy':(ROOT/'response_policy.md').read_text()},sort_keys=True,ensure_ascii=False,default=str).encode()).hexdigest()
    return Profile(data,revision,model,product,by_field)

class Profile:
    def __init__(self,data,revision,models,products,by_field):
        self.data=data;self.revision=revision;self.models=models;self.products=products;self.by_field=by_field
    def card_groups(self):
        """Seller article and color are the review inbox unit; size stays a review fact."""
        groups={}
        for product in self.products.values():
            if product['active']:
                key=(product['model_id'],product['seller_article'],product['color'])
                groups.setdefault(key,[]).append(product)
        return list(groups.values())
    def card_members(self,product_id):
        product=self.products[product_id]
        key=(product['model_id'],product['seller_article'],product['color'])
        return [item for item in self.products.values() if item['active'] and
                (item['model_id'],item['seller_article'],item['color'])==key]
    def match(self,mode,review):
        nm=int(review['nm_id'])
        found=[x for x in self.products.values() if x['active'] and x.get(mode+'_nmID')==nm]
        if not found:return None,'Нет точного nmID в каталоге'
        raw=dict(review)
        if len(found)>1:
            found=[x for x in found if raw.get('remote_size')==str(x['size']) and raw.get('remote_article')==str(x['seller_article'])]
            if len(found)!=1:return None,'Размер или артикул варианта не подтверждён'
        x=found[0]
        if mode=='sandbox':
            if raw.get('remote_size')!=str(x['size']) or raw.get('remote_article')!=str(x['seller_article']):return None,'Вариант/артикул WB не совпадает с каталогом'
        return x,''
    def match_question(self,mode,question):
        nm=int(question['nm_id'])
        found=[x for x in self.products.values() if x['active'] and x.get(mode+'_nmID')==nm]
        if not found:return None,'Карточка вопроса не сопоставлена с профилем'
        if len(found)>1 and len({(x['model_id'],x['seller_article'],x['color']) for x in found})!=1:
            return None,'Карточка вопроса неоднозначна'
        x=found[0]
        article=dict(question).get('remote_article')
        if mode=='sandbox' and article and article!=x['seller_article']:
            return None,'Артикул карточки вопроса не совпадает с профилем'
        return x,''
    def question_facts(self,product_id):
        model_id=self.products[product_id]['model_id']
        result={}
        for (scope,owner,field),fact in self.by_field.items():
            if scope=='model' and owner==model_id and fact['status']=='approved':result[field]=fact
        # A question does not identify a purchased size. A variant override makes
        # the model value ambiguous even when the card nmID matches one variant.
        for variant in self.products.values():
            if variant['model_id']!=model_id:continue
            for (scope,owner,field),fact in self.by_field.items():
                if scope=='variant' and owner==variant['product_id']:
                    result.pop(field,None)
        return result
    def question_size_facts(self,product_id,question):
        """Measurements for explicitly named sizes of this model/color/article only."""
        card=self.products[product_id]
        named=set(requested_sizes(question))
        if not named:return {}
        result={}
        for item in self.card_members(product_id):
            size=str(item['size']).upper()
            if size not in named or item['model_id']!=card['model_id']:
                continue
            fact=self.facts(item['product_id']).get('measurement')
            if fact and fact['status']=='approved':result[size]=fact
        return result
    def facts(self,product_id):
        p=self.products[product_id];result={}
        for (scope,owner,field),fact in self.by_field.items():
            if scope=='model' and owner==p['model_id'] and fact['status']=='approved':result[field]=fact
        for (scope,owner,field),fact in self.by_field.items():
            if scope=='variant' and owner==product_id:
                if fact['status']=='approved':result[field]=fact
                elif field in result:del result[field]
        return result
    def advice(self,product_id,condition_code):
        p=self.products[product_id]
        return next((r for r in self.data['Советы_размера'] if r['model_id']==p['model_id'] and r['condition_code']==condition_code and r['status']=='approved'),None)
    def recommendations(self,product_id):
        return [r for r in self.data['Сочетания'] if r['source_product_id']==product_id and r['status']=='approved' and r['active']]
    def present(self,p):
        model=self.models[p['model_id']]
        return {'product_id':p['product_id'],'name':model['name'],'color':p['color'],'size':p['size'],'variant':f"{p['color']} / {p['size']}",'photo':'/static/products/'+p['photo'],'seller_article':p['seller_article'],'model_id':p['model_id']}
