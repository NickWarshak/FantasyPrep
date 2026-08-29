"""Inline the player pool into the draft room's HTML.

The app ships as one self-contained file (it also runs as a published Artifact,
where runtime fetches are blocked), so the pool is baked in at build time.

Two inputs:

  espn_ppr300_cheatsheet.csv   the draft pool -- what everyone drafts from, and
                               what the bots value players by
  NicksRankings.csv            optional personal board, matched on by name; it
                               drives the "my board" strip and never changes
                               what the bots do

Kickers and defenses only exist in the ESPN sheet, so they simply carry no
personal rank. If the personal file is missing the strip hides itself and the
rest of the app is unaffected.

    python mockdraft/build.py
"""
import csv, json, pathlib, re
from collections import Counter

ROOT  = pathlib.Path(__file__).resolve().parent
CSV   = ROOT.parent / 'espn_ppr300_cheatsheet.csv'
MINE  = ROOT.parent / 'NicksRankings.csv'
TPL   = ROOT / 'template.html'
OUT   = ROOT / 'index.html'
TOKEN = '/*PLAYERS*/[]'

POS = {'QB', 'RB', 'WR', 'TE', 'K', 'DST'}

# The two sheets spell a few players differently.
ALIASES = {'nicholas singleton': 'nick singleton', 'kenneth gainwell': 'kenny gainwell'}


def norm(name):
    s = name.lower().strip()
    s = re.sub(r"[.'`\-]", '', s)
    s = re.sub(r'\s+(jr|sr|ii|iii|iv|v)$', '', s)
    s = re.sub(r'\s+', ' ', s)
    return ALIASES.get(s, s)


def load_personal():
    """name -> personal rank, or {} when there is no personal board."""
    if not MINE.exists():
        return {}
    rows = list(csv.DictReader(MINE.open()))
    return {norm(r['Name']): i + 1 for i, r in enumerate(rows)}


mine = load_personal()

players = []
for r in csv.DictReader(CSV.open()):
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
        mine.get(norm(r['Player']), 0),      # 0 = not on the personal board
    ])

players.sort(key=lambda p: p[0])
if [p[0] for p in players] != list(range(1, len(players) + 1)):
    raise SystemExit('ranks are not a contiguous 1..N sequence')

tpl = TPL.read_text(encoding='utf-8')
if TOKEN not in tpl:
    raise SystemExit(f'placeholder {TOKEN} not found in template')

OUT.write_text(tpl.replace(TOKEN, json.dumps(players, separators=(',', ':'))), encoding='utf-8')

print(f'{len(players)} players -> {OUT.name} ({OUT.stat().st_size / 1024:.0f} KB)')
print('  ' + '  '.join(f'{k}:{v}' for k, v in sorted(Counter(p[2] for p in players).items())))

if mine:
    unmatched = [p for p in players if not p[7] and p[2] not in ('K', 'DST')]
    print(f'  personal board: {sum(1 for p in players if p[7])}/{len(players)} matched'
          f' (K and DEF are not on it by design)')
    if unmatched:
        print('  NOT MATCHED: ' + ', '.join(f'{p[1]} ({p[2]}, #{p[0]})' for p in unmatched))
else:
    print(f'  no {MINE.name} found -- the personal board strip stays hidden')
