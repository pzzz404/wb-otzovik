"""Local isolated app entry point; select demo or sandbox with WB_MODE."""
import os
from pathlib import Path
import uvicorn
ROOT=Path(__file__).resolve().parent
mode=os.environ.get('WB_MODE','demo')
if mode not in ('demo','sandbox'):raise SystemExit('Only demo and sandbox are supported')
if mode=='demo':
    os.environ.setdefault('WB_UI_V4','1')
    os.environ.setdefault('WB_V4_FIXTURES','1')
hermes_key=ROOT/'.data/hermes/server-key'
if hermes_key.exists():os.environ['HERMES_API_KEY']=hermes_key.read_text().strip()
os.environ['HERMES_URL']='http://127.0.0.1:8667'
os.environ.setdefault('WB_DATA_DIR',str(ROOT/'.data'/(mode+'-v2')))
port=int(os.environ.get('WB_PORT','8767' if mode=='demo' else '8768'))
if not 1024<=port<=65535:raise SystemExit('Invalid local port')
bind=os.getenv('WB_BIND','127.0.0.1')
if mode=='sandbox' and bind!='127.0.0.1':raise SystemExit('Sandbox web interface must remain on loopback')
uvicorn.run('app.v2:app',host=bind,port=port,log_level='warning')
