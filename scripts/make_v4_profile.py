"""Add synthetic approved clothing examples to a copy of the V3 XLSX profile."""
import argparse
from pathlib import Path
from openpyxl import load_workbook
from app.core import ROOT

parser=argparse.ArgumentParser()
parser.add_argument('--source',type=Path,default=ROOT/'data/profiles.xlsx')
parser.add_argument('--out',type=Path,default=ROOT/'.data/dev-v4/profiles.xlsx')
args=parser.parse_args()
if args.source.resolve()==args.out.resolve():raise SystemExit('Use a distinct output; keep the source untouched')
book=load_workbook(args.source)
models=book['Модели'];variants=book['Варианты'];facts=book['Факты']
model_ids={row[0] for row in list(models.values)[1:]}
variant_ids={row[0] for row in list(variants.values)[1:]}
fact_ids={row[0] for row in list(facts.values)[1:]}
for row in [
 ('DRESS','Платье «Тепло»','Одежда','Не гарантировать отсутствие катышков, усадки и точную посадку',True,1),
 ('SUIT','Костюм «Облако»','Одежда','Не гарантировать совместимость частей другого костюма',True,1),
]:
 if row[0] not in model_ids:models.append(row)
for row in [
 ('DR-01','DRESS',920101,None,'SL-DR-01','серый','M','dress-grey-m.png',True,1),
 ('SU-01','SUIT',920102,None,'SL-SU-01','кремовый','M','suit-cream-m.png',True,1),
 ('SU-02','SUIT',920103,None,'SL-SU-02','графит','M','suit-charcoal-m.png',True,1),
]:
 if row[0] not in variant_ids:variants.append(row)
for row in [
 ('D01','model','DRESS','composition','80% шерсть, 20% полиамид',None),
 ('D02','model','DRESS','wash','Ручная стирка до 30 °C, без отжима',None),
 ('D03','model','DRESS','dry','Сушить горизонтально в расправленном виде',None),
 ('D04','model','DRESS','fit','Прямой силуэт; сверять замеры с таблицей',None),
 ('D05','model','DRESS','feature','Натуральная шерсть в составе; тепло и комфорт',None),
 ('S01-V4','model','SUIT','fit','Свободная посадка; части сочетаются только внутри этой модели',None),
 ('S02-V4','model','SUIT','wash','Деликатная стирка до 30 °C по ярлыку',None),
 ('S03-V4','variant','SU-01','composition','Кремовый вариант: 70% хлопок, 30% полиэстер',None),
 ('S04-V4','variant','SU-02','composition','Графитовый вариант: 70% хлопок, 30% полиэстер',None),
]:
 if row[0] not in fact_ids:
  facts.append([*row,'Синтетическая утверждённая карточка для Demo','approved','2026-11-17',1])
args.out.parent.mkdir(parents=True,exist_ok=True);book.save(args.out)
print(args.out)
