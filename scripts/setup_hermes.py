"""Create a private Hermes profile from an existing local provider credential."""
from pathlib import Path
from getpass import getpass
import os
import argparse,secrets
parser=argparse.ArgumentParser();parser.add_argument("--credential-source",type=Path);args=parser.parse_args()
ROOT=Path(__file__).resolve().parents[1]
os.umask(0o077)
profile=ROOT/'.data/hermes';profile.mkdir(parents=True,exist_ok=True,mode=0o700)
source=args.credential_source
if (profile/'config.yaml').exists():
    raise SystemExit('Profile already exists. To update the model key, edit .data/hermes/.env')
if source:
    key = next((line.split('=', 1)[1].strip().strip('"\'') for line in source.read_text().splitlines()
                if line.startswith('OPENROUTER_API_KEY=')), '')
else:
    key = getpass('Введите API-ключ OpenRouter (ввод скрыт): ').strip()
if not key:raise SystemExit('Local OpenRouter credential unavailable')
config='''model:\n  default: openai/gpt-4.1-mini\n  provider: openrouter\n  max_tokens: 900\nplatform_toolsets:\n  api_server: []\nmemory:\n  memory_enabled: false\n  user_profile_enabled: false\n  provider: ''\nskills:\n  external_dirs: []\n  inline_shell: false\n  creation_nudge_interval: 0\ncurator:\n  enabled: false\nmcp:\n  disabled: true\nmcp_servers: {}\ncompression:\n  enabled: false\nagent:\n  max_turns: 2\n  api_max_retries: 1\ngateway:\n  api_server:\n    max_concurrent_runs: 1\n'''
files={'config.yaml':config,'.env':'OPENROUTER_API_KEY='+key+'\n','SOUL.md':'Respond only to the supplied product evidence. No tools.\n','server-key':secrets.token_urlsafe(40)}
for name,value in files.items():
 p=profile/name
 if p.exists():raise SystemExit('Private Hermes profile already exists; refusing overwrite')
 p.write_text(value);p.chmod(0o600)
print('Private Hermes profile created; credential not printed')
