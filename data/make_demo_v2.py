"""Freeze a synthetic inbox with repeated reviews per exact variant."""
import json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
rows=[]
def add(id,nm,rating,text,photos=None,pros='',cons=''):
 rows.append({'id':id,'nm_id':nm,'rating':rating,'body':text,'pros':pros,'cons':cons,'photo_urls':photos or []})
white=[
 (5,'Отличная футболка, спасибо!'),
 (5,'Как стирать молочную футболку?'),
 (2,'Не понравилось качество ткани.'),
 (1,'На футболке пятно, приложила фото.'),
 (5,'Пять звёзд, но размер оказался мал.'),
 (5,'Какой состав у молочной футболки?'),
 (5,'Хочу комплект, что к ней подобрать?'),
 (4,'Можно не гладить эту футболку?'),
 (5,'Какой полуобхват груди изделия M?'),
 (4,'Удобная, но шов разошёлся.'),
 (5,'Спасибо, покупка понравилась.'),
 (5,'Приятная футболка, спасибо.'),
 (5,'Как сушить после стирки?'),
 (2,'Ужасное качество, не советую.'),
 (5,'Очень хорошая базовая вещь.'),
 (5,'Ну спасибо, прекрасное качество — на второй день дырка.'),
 (4,'Мне нравится цвет, спасибо.'),
 (5,'Какой размер мне точно подойдёт при росте 172 и весе 68?'),
 (5,'Отлично, всё понравилось.'),
 (5,'Посоветуйте размер больше, если я между размерами.'),
]
for i,(rating,text) in enumerate(white,1):
 photos=['/static/review-photos/cream-front.png','/static/review-photos/cream-close.png','/static/review-photos/cream-side.png'] if i==4 else None
 add(f'W{i:02}',910001,rating,text,photos)
black=[(5,'Как стирать чёрную футболку?'),(5,'Какой состав чёрного варианта?'),(5,'Хорошая футболка, спасибо.'),(2,'Чёрный цвет после стирки стал тусклым.'),(4,'Смотрите фото: шов распустился.')]
for i,(rating,text) in enumerate(black,1):
 photos=['/static/review-photos/black-seam.png'] if i==5 else None
 add(f'B{i:02}',910006,rating,text,photos)
other=[('L01',910002,5,'Как стирать синий лонгслив?'),('L02',910002,2,'Не понравилось, ткань после стирки села.'),('S01',910003,5,'Как гладить белую рубашку?'),('S02',910003,5,'Хорошая рубашка, спасибо!'),('P01',910004,5,'Как гладить брюки?'),('P02',910004,5,'Какой полуобхват талии 46?'),('C01',910005,5,'Как стирать шерстяной кардиган?'),('C02',910005,5,'Можно не гладить кардиган?'),('C03',910005,2,'Не понравилось: на кардигане появились катышки.'),('C04',910005,5,'Кардиган понравился, спасибо!')]
for id,nm,rating,text in other:add(id,nm,rating,text)
assert len([r for r in rows if r['nm_id']==910001])==20
assert len([r for r in rows if r['nm_id']==910006])==5
(ROOT/'data/demo_reviews_v2.json').write_text(json.dumps(rows,ensure_ascii=False,indent=2)+'\n')
print('Frozen synthetic reviews:',len(rows),'(milk 20, black 5)')
