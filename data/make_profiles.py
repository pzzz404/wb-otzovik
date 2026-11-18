"""Create the editable v2 clothing profile workbook from fictional owner-approved examples."""
from pathlib import Path
from openpyxl import Workbook
ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'data/profiles.xlsx'
models=[
 ('TEE','Футболка «Роса»','Одежда','Не обещать посадку, отсутствие усадки или совпадение оттенков'),
 ('LONG','Лонгслив «Линия»','Одежда','Не выводить уход из состава; не обещать отсутствие линьки'),
 ('SHIRT','Рубашка «Лист»','Одежда','Не обещать непрозрачность или отсутствие смятия'),
 ('PANTS','Брюки «Берег»','Одежда','Не гарантировать размер по росту и весу'),
 ('CARD','Кардиган «Тихо»','Одежда','Не обещать отсутствие катышков или усадки'),
]
variants=[
 ('TS-01','TEE',910001,None,'SL-TS-01','молочный','M','tee-cream-m.png'),
 ('TS-01B','TEE',910006,None,'SL-TS-01-B','чёрный','M','tee-black-m.png'),
 ('LS-02','LONG',910002,None,'SL-LS-02','синий','L','longsleeve-navy-l.png'),
 ('SH-03','SHIRT',910003,None,'SL-SH-03','белый','M','shirt-white-m.png'),
 ('PT-04','PANTS',910004,None,'SL-PT-04','графит','46','trousers-graphite-46.png'),
 ('CD-05','CARD',910005,None,'SL-CD-05','серый','M','cardigan-grey-m.png'),
]
# id, scope, owner, field, value, override_of
facts=[
 ('M01','model','TEE','fit','Прямая посадка; замер выбирают по таблице размеров',''),
 ('M02','model','TEE','wash','Машинная стирка при температуре не выше 30 °C по ярлыку',''),
 ('M03','model','TEE','dry','Сушить на воздухе, без барабанной сушки',''),
 ('M04','model','TEE','iron','Гладить при температуре не выше 110 °C',''),
 ('V01','variant','TS-01','composition','Молочный / M: 95% хлопок, 5% эластан',''),
 ('V02','variant','TS-01','measurement','Полуобхват груди изделия M — 52 см',''),
 ('V03','variant','TS-01B','composition','Чёрный / M: 100% хлопок',''),
 ('V04','variant','TS-01B','wash','Чёрный вариант: вывернуть наизнанку и стирать при температуре не выше 30 °C','M02'),
 ('V05','variant','TS-01B','measurement','Полуобхват груди изделия M — 51 см',''),
 ('M05','model','LONG','fit','Прилегающая посадка',''),
 ('M06','model','LONG','wash','Бережная машинная стирка при температуре не выше 30 °C',''),
 ('M07','model','LONG','dry','Сушить в расправленном виде',''),
 ('M08','model','LONG','iron','Гладить при температуре не выше 110 °C',''),
 ('V06','variant','LS-02','composition','Синий / L: 100% хлопок',''),
 ('V07','variant','LS-02','measurement','Длина рукава изделия L — 63 см',''),
 ('M09','model','SHIRT','fit','Свободная посадка',''),
 ('M10','model','SHIRT','wash','Машинная стирка при температуре не выше 40 °C',''),
 ('M11','model','SHIRT','dry','Сушить на плечиках',''),
 ('M12','model','SHIRT','iron','Гладить при средней температуре до 150 °C',''),
 ('V08','variant','SH-03','composition','Белый / M: 70% хлопок, 30% лён',''),
 ('V09','variant','SH-03','contents','В комплекте одна рубашка',''),
 ('M13','model','PANTS','fit','Прямая посадка',''),
 ('M14','model','PANTS','wash','Машинная стирка при температуре не выше 30 °C',''),
 ('M15','model','PANTS','dry','Сушить на воздухе',''),
 ('V10','variant','PT-04','composition','Графит / 46: 98% хлопок, 2% эластан',''),
 ('V11','variant','PT-04','measurement','Полуобхват талии изделия 46 — 39 см',''),
 ('M16','model','CARD','fit','Свободная посадка',''),
 ('M17','model','CARD','wash','Ручная деликатная стирка при температуре не выше 30 °C по ярлыку',''),
 ('M18','model','CARD','dry','Сушить горизонтально в расправленном виде',''),
 ('M19','model','CARD','iron','Гладить через ткань при низкой температуре',''),
 ('V12','variant','CD-05','composition','Серый / M: 60% шерсть мериноса, 40% хлопок',''),
 ('V13','variant','CD-05','contents','В комплекте один кардиган',''),
]
advice=[('S01','TEE','between_sizes','Если покупатель находится между двумя размерами и предпочитает более свободную посадку','Можно рассмотреть следующий размер по таблице замеров; посадку по росту и весу не гарантируем'),('S02','CARD','loose_fit','Если покупатель просит более свободную посадку и сравнил свои замеры с таблицей','Можно рассмотреть размер больше; итоговую посадку не гарантируем')]
pairs=[('R01','TS-01','PT-04','Запрос дополнения к комплекту','Утверждённое сочетание молочного и графитового'),('R02','TS-01B','PT-04','Запрос дополнения к комплекту','Утверждённое сочетание чёрного и графитового'),('R03','SH-03','CD-05','Запрос дополнения к комплекту','Утверждённое сочетание рубашки и кардигана')]
w=Workbook();m=w.active;m.title='Модели';m.append(['model_id','name','category','prohibited_promises','active','version'])
for row in models:m.append([*row,True,1])
v=w.create_sheet('Варианты');v.append(['product_id','model_id','demo_nmID','sandbox_nmID','seller_article','color','size','photo','active','version'])
for row in variants:v.append([*row,True,1])
f=w.create_sheet('Факты');f.append(['fact_id','scope','owner_id','field','value','override_of','source','status','verified_at','version'])
for row in facts:f.append([*row,'Вымышленная утверждённая карточка бренда «Северная линия»','approved','2026-11-16',1])
a=w.create_sheet('Советы_размера');a.append(['rule_id','model_id','condition_code','condition','response','source','status','version'])
for row in advice:a.append([*row,'Вымышленная размерная политика «Северная линия»','approved',1])
r=w.create_sheet('Сочетания');r.append(['recommendation_id','source_product_id','target_product_id','scenario','reason','source','status','version','active'])
for row in pairs:r.append([*row,'Вымышленная матрица сочетаний «Северная линия»','approved',1,True])
w.save(OUT)
print('Profiles:',len(models),'models,',len(variants),'variants,',len(facts),'facts')
