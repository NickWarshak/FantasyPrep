"""Inline the ESPN PPR 300 cheatsheet into the draft room's HTML.

The app ships as one self-contained file (it runs as a published Artifact, where
runtime fetches are blocked), so the player pool is baked in at build time.
Re-run this after updating the cheatsheet CSV.

    python mockdraft/build.py
"""
import csv, json, pathlib

ROOT  = pathlib.Path(__file__).resolve().parent
CSV   = ROOT.parent / 'espn_ppr300_cheatsheet.csv'
TPL   = ROOT / 'template.html'
OUT   = ROOT / 'index.html'
TOKEN = '/*PLAYERS*/[]'

POS = {'QB', 'RB', 'WR', 'TE', 'K', 'DST'}

rows = list(csv.DictReader(CSV.open()))
players = []
for r in rows:
    pos = r['Position'].strip().upper()
    if pos not in POS:
        raise SystemExit(f'unexpected position {pos!r} for {r["Player"]!r}')
    players.append([
        int(r['Rank']),
        r['Player'].strip(),
        pos,
        int(r['PositionRank']),
        r['Team'].strip(),
        int(r['AuctionValue'] or 0),
        int(r['Bye'] or 0),
    ])

players.sort(key=lambda p: p[0])
if [p[0] for p in players] != list(range(1, len(players) + 1)):
    raise SystemExit('ranks are not a contiguous 1..N sequence')

tpl = TPL.read_text(encoding='utf-8')
if TOKEN not in tpl:
    raise SystemExit(f'placeholder {TOKEN} not found in template')

data = json.dumps(players, separators=(',', ':'))
OUT.write_text(tpl.replace(TOKEN, data), encoding='utf-8')

from collections import Counter
print(f'{len(players)} players -> {OUT.name} ({OUT.stat().st_size / 1024:.0f} KB)')
print('  ' + '  '.join(f'{k}:{v}' for k, v in sorted(Counter(p[2] for p in players).items())))
