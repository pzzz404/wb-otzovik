"""Seed private v2 mapping from the saved test card; no WB request."""
import json
from pathlib import Path
from openpyxl import load_workbook
from app.core import ROOT
from app.profile import load
ids=json.loads((ROOT/'.data/sandbox/ids.json').read_text())
old=load_workbook(ROOT/'data/profiles.xlsx')
for row in old['Варианты'].iter_rows(min_row=2):
    if row[0].value==ids['product_id']:
        row[3].value=ids['nmID'];row[4].value='SL-TS-01-WBTEST'
dest=ROOT/'.data/sandbox-v2/profiles.xlsx'
dest.parent.mkdir(parents=True, exist_ok=True)
if dest.exists():raise SystemExit('Private profile exists; refusing overwrite')
old.save(dest);dest.chmod(0o600)
profile=load(dest)
assert profile.products[ids['product_id']]['sandbox_nmID']==ids['nmID']
print('Private sandbox profile seeded from saved test card')
