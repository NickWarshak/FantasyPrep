"""Blend three ranking sources into one DK-uploadable list: Nick's Rankings.

Weights: DraftKings pre-draft (ADP) 50%, Angle redraft 25%, fantasy_rankings 25%.

The DK file is the player universe (1568 players) because its IDs are what the
DraftKings upload keys on. The other two lists are truncated (top 150 / top 199),
so a player missing from one of them is imputed as

    max(cutoff + 1, dk_rank)

That reads absence as information only where absence is informative. Inside the
top 150 it is a real penalty -- two other rankers looked at that range and left
the player out. Past the cutoff it collapses to the DK rank, so a DK-500 player
is not dragged up to ~200 by imputing "just off the list" for the short sources,
which would scramble the deep tail where DK is the only signal we have.
"""
import csv, re

DK   = 'DkPreDraftRankings.csv'
ANG  = 'angle_ranks_redraft_top150.csv'
FANT = 'fantasy_rankings.csv'
W_DK, W_ANG, W_FANT = 0.50, 0.25, 0.25

ALIASES = {'kenneth gainwell': 'kenny gainwell'}

def norm(s):
    s = s.lower().strip()
    s = re.sub(r"[.'`\-]", '', s)
    s = re.sub(r'\s+(jr|sr|ii|iii|iv|v)$', '', s)
    s = re.sub(r'\s+', ' ', s)
    return ALIASES.get(s, s)

dk = list(csv.DictReader(open(DK)))
dk_rank = {norm(r['Name']): i + 1 for i, r in enumerate(dk)}

def load(path, col):
    rows = list(csv.DictReader(open(path)))
    return {norm(r[col]): i + 1 for i, r in enumerate(rows)}, len(rows)

ang,  ang_cut  = load(ANG,  'Player')
fant, fant_cut = load(FANT, 'Player')

for name, src in (('angle', ang), ('fantasy', fant)):
    unmatched = [k for k in src if k not in dk_rank]
    if unmatched:
        raise SystemExit(f'{name}: unmatched players {unmatched}')

out = []
for i, r in enumerate(dk):
    key, d = norm(r['Name']), i + 1
    a = ang.get(key,  max(ang_cut + 1, d))
    f = fant.get(key, max(fant_cut + 1, d))
    out.append({
        'ID': r['ID'], 'Name': r['Name'], 'Position': r['Position'],
        'Team': r['Team'], 'dk': d, 'angle': a, 'fantasy': f,
        'in_angle': key in ang, 'in_fantasy': key in fant,
        'score': W_DK * d + W_ANG * a + W_FANT * f,
    })

out.sort(key=lambda x: (x['score'], x['dk']))

with open('NicksRankings.csv', 'w', newline='') as fh:
    w = csv.writer(fh)
    w.writerow(['ID', 'Name', 'Position', 'Team'])
    for p in out:
        w.writerow([p['ID'], p['Name'], p['Position'], p['Team']])

with open('NicksRankings_detail.csv', 'w', newline='') as fh:
    w = csv.writer(fh)
    w.writerow(['NickRank', 'Name', 'Position', 'Team', 'Score',
                'DK_rank', 'Angle_rank', 'Fantasy_rank', 'Sources', 'DK_move'])
    for i, p in enumerate(out, 1):
        n = 1 + p['in_angle'] + p['in_fantasy']
        w.writerow([i, p['Name'], p['Position'], p['Team'], round(p['score'], 2),
                    p['dk'], p['angle'] if p['in_angle'] else '',
                    p['fantasy'] if p['in_fantasy'] else '', n, p['dk'] - i])

print(f'{len(out)} players -> NicksRankings.csv, NicksRankings_detail.csv')
print(f'in all three: {sum(1 for p in out if p["in_angle"] and p["in_fantasy"])}')
