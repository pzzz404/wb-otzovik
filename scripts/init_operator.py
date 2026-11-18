from pathlib import Path
import secrets
root=Path(__file__).resolve().parents[1];p=root/'.data/operator-key';p.parent.mkdir(parents=True,exist_ok=True)
if p.exists():raise SystemExit('Operator key already exists; refusing overwrite')
p.write_text(secrets.token_urlsafe(32));p.chmod(0o600)
print('Private operator key created')
