"""Deterministic synthetic dress inbox: 100 separate reviews for one demo card."""
import json
from datetime import datetime,timedelta,timezone
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
names=['Анна','Мария','Елена','Ольга','Ирина','Наталья','Татьяна','Светлана','Алина','Юлия','Вера','Дарья']
start=datetime(2026,11,17,6,0,tzinfo=timezone.utc)
rows=[]
for n in range(1,101):
    if n<=40:rating=5;text='Очень мягкое платье, понравилось!';pros='Приятная ткань.';cons=''
    elif n<=55:rating=5;text='Тёплое и мягкое платье, понравилось, размер подошёл.';pros='Уютное.';cons=''
    elif n<=75:rating=2;text='После первой стирки платье село.';pros='';cons='После стирки село.'
    elif n<=85:rating=5;text='Как стирать это платье?';pros='';cons=''
    elif n<=95:rating=2;text='Появились катышки после носки.';pros='';cons='Катышки.'
    else:rating=5;text='Какой размер точно подойдёт при моём росте?';pros='';cons=''
    if n==100:rating=5;text='Пять звёзд, но после стирки платье село.';cons='Платье село.'
    row={'id':f'R{n:03d}','nm_id':920101,'rating':rating,'body':text,'pros':pros,'cons':cons,
         'photo_urls':(['/static/review-photos/dress-pilling.png','/static/review-photos/dress-cuff.png'] if n==100 else
                       ['/static/review-photos/dress-pilling.png'] if n==91 else []),
         'remote_size':'M','remote_article':'SL-DR-01','user_name':names[(n-1)%len(names)],
         'created_date':(start+timedelta(minutes=4*n)).isoformat(),
         'order_status':'buyout' if n%3 else 'unknown'}
    rows.append(row)
out=ROOT/'data/demo_reviews_v4.json';out.write_text(json.dumps(rows,ensure_ascii=False,indent=2)+'\n')
print(out,len(rows))
