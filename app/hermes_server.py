"""Isolated loopback Hermes API, using the installed code without changing it."""
import asyncio,os,signal,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
PROFILE=ROOT/'.data/hermes'
os.umask(0o077)
os.environ['HERMES_HOME']=str(PROFILE)
os.environ['API_SERVER_KEY']=(PROFILE/'server-key').read_text().strip()
os.environ['HERMES_MAX_ITERATIONS']='2'
os.environ['HERMES_MAX_TOKENS']='900'
os.environ['NO_PROXY']=os.environ['no_proxy']='localhost,127.0.0.1,::1'
os.chdir(PROFILE)
from dotenv import load_dotenv
load_dotenv(PROFILE/'.env',override=True)
from gateway.config import PlatformConfig
from gateway.platforms.api_server import APIServerAdapter
class NoToolAdapter(APIServerAdapter):
 def _ensure_session_db(self):return None
 def _create_agent(self,*args,**kwargs):
  agent=super()._create_agent(*args,**kwargs)
  if agent.valid_tool_names or agent.tools or agent._memory_enabled or agent._user_profile_enabled or agent._memory_manager:
   raise RuntimeError('Unsafe Hermes capabilities')
  agent.max_iterations=2
  agent._persist_session=lambda *a,**k:None
  agent._save_trajectory=lambda *a,**k:None
  agent._session_db=None
  agent._spawn_background_review=lambda *a,**k:None
  agent.client=agent.client.with_options(max_retries=0)
  original=agent.client.chat.completions.create
  calls=0
  def once(*a,**k):
   nonlocal calls
   calls+=1
   if calls>1:raise RuntimeError('Second generation denied')
   k['temperature']=0
   return original(*a,**k)
  agent.client.chat.completions.create=once
  return agent
async def main():
 adapter=NoToolAdapter(PlatformConfig(enabled=True,extra={'host':'127.0.0.1','port':8667}))
 if not await adapter.connect():raise RuntimeError('Hermes API could not start')
 stop=asyncio.Event()
 for sig in (signal.SIGINT,signal.SIGTERM):asyncio.get_running_loop().add_signal_handler(sig,stop.set)
 await stop.wait();await adapter.disconnect()
if __name__=='__main__':asyncio.run(main())
