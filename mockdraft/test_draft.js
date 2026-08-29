/* Headless check of the draft engine.
 *
 * Pulls the logic straight out of index.html so the tests exercise the same
 * source the page ships, stubs the DOM calls the engine makes, and runs full
 * drafts across a spread of room settings looking for invariant breaks.
 *
 *     node mockdraft/test_draft.js
 */
const fs = require('fs');
const path = require('path');

const html = fs.readFileSync(path.join(__dirname, 'index.html'), 'utf8');
const scripts = [...html.matchAll(/<script>([\s\S]*?)<\/script>/g)].map(m => m[1]);
// The last two blocks are rendering and DOM event wiring; the first two are the
// pure engine (data + roster shape, then bots + the draft loop).
const engine = scripts.slice(0, 2).join('\n');

const stub = `
  function renderAll(){} function renderClockbar(){} function renderClock(){}
  function renderPool(){} function renderRail(){} function renderRecap(){}
  function buildFilters(){}
  const document = { getElementById: () => ({ hidden: false, classList: { remove(){}, add(){}, toggle(){} }, value: '', textContent: '' }),
                     querySelector: () => null, querySelectorAll: () => [] };
  let __iv = null;
  function setInterval(){ return 1; } function clearInterval(){}
`;

const api = new Function(stub + engine + `
  return { get S(){return S}, set S(v){S=v}, get St(){return St}, set St(v){St=v},
           teamAt, totalPicks, lineup, needs, counts, available, botChoice,
           autoChoice, startDraft, makePick, rounds, SLOTS, STANDARD, P, teamName };
`)();

let failures = 0;
function check(name, cond, detail) {
  if (!cond) { failures++; console.log('  FAIL  ' + name + (detail ? '  — ' + detail : '')); }
  else console.log('  ok    ' + name);
}

/* ── Draft order ────────────────────────────────────────────────────────── */
console.log('\ndraft order');
api.S = { teams: 4, type: 'snake', slots: {}, rounds: 4, slot: 1 };
const snake = [...Array(12).keys()].map(api.teamAt).join(' ');
check('snake reverses each round', snake === '0 1 2 3 3 2 1 0 0 1 2 3', snake);

api.S.type = 'linear';
const linear = [...Array(12).keys()].map(api.teamAt).join(' ');
check('linear repeats the order', linear === '0 1 2 3 0 1 2 3 0 1 2 3', linear);

api.S.type = '3rr';
const rr = [...Array(16).keys()].map(api.teamAt).join(' ');
check('3rd round reversal flips from round 3', rr === '0 1 2 3 3 2 1 0 3 2 1 0 0 1 2 3', rr);

api.S.type = 'snake';
for (const t of [4, 10, 12, 14]) {
  api.S.teams = t;
  const seen = new Set([...Array(t * 6).keys()].map(api.teamAt));
  const perRound = {};
  for (let i = 0; i < t * 6; i++) {
    const r = Math.floor(i / t);
    (perRound[r] = perRound[r] || new Set()).add(api.teamAt(i));
  }
  const everyTeamOncePerRound = Object.values(perRound).every(s => s.size === t);
  check(t + '-team snake: every team picks once per round', everyTeamOncePerRound && seen.size === t);
}

/* ── Lineup assignment ──────────────────────────────────────────────────── */
console.log('\nlineup assignment');
api.S = { teams: 12, type: 'snake', slots: Object.assign({}, api.STANDARD), rounds: 16, slot: 1 };
const byName = {};
api.P.forEach(p => { byName[p.name] = p; });
const pick = names => names.map(n => {
  if (!byName[n]) throw new Error('no such player: ' + n);
  return byName[n].id;
});

const lu = api.lineup(pick(['Jahmyr Gibbs', 'Bijan Robinson', "Ja'Marr Chase", 'Puka Nacua']));
const filled = lu.slots.filter(s => s.p).map(s => s.lb + ':' + s.p.name.split(' ').pop());
check('two RB two WR fill RB/RB/WR/WR before flex',
  filled.join(' ') === 'RB:Gibbs RB:Robinson WR:Chase WR:Nacua', filled.join(' '));

const lu2 = api.lineup(pick(['Jahmyr Gibbs', 'Bijan Robinson', 'Chase Brown']));
const flexed = lu2.slots.find(s => s.k === 'FLEX');
check('a third RB lands in FLEX', flexed && flexed.p && flexed.p.name === 'Chase Brown',
  flexed && flexed.p ? flexed.p.name : 'empty');

// Walker is ranked 18th and Brown 19th, so FLEX takes Walker and Brown benches:
// the lineup always promotes the better player, not the earlier pick.
const lu3 = api.lineup(pick(['Jahmyr Gibbs', 'Bijan Robinson', 'Chase Brown', 'Kenneth Walker III']));
const benched = lu3.slots.filter(s => s.k === 'BN' && s.p);
check('a fourth RB benches, and the better one takes FLEX',
  benched.length === 1 && benched[0].p.name === 'Chase Brown' &&
  lu3.slots.find(s => s.k === 'FLEX').p.name === 'Kenneth Walker III',
  benched.map(b => b.p.name).join(','));

check('nobody is assigned to two slots at once',
  new Set(lu3.slots.filter(s => s.p).map(s => s.p.id)).size === lu3.slots.filter(s => s.p).length);

const n0 = api.needs([]);
check('an empty roster needs every starting slot', n0.open === 10, 'open=' + n0.open);
check('empty roster counts FLEX toward RB, WR and TE',
  n0.need.RB === 3 && n0.need.WR === 4 && n0.need.TE === 2,
  JSON.stringify(n0.need));

/* ── Full drafts ────────────────────────────────────────────────────────── */
console.log('\nfull drafts');
function runDraft(settings) {
  api.S = Object.assign({ teams: 12, type: 'snake', clock: 30, pace: 0, slot: 5,
                          slots: Object.assign({}, api.STANDARD) }, settings);
  api.S.rounds = api.rounds();
  api.startDraft();
  let guard = 0;
  while (api.St.onClock < api.totalPicks()) {
    if (++guard > 5000) throw new Error('draft failed to terminate');
    const team = api.teamAt(api.St.onClock);
    const p = team === api.S.slot - 1 ? api.autoChoice(team) : api.botChoice(team, false);
    if (!p) throw new Error('no pick available at overall ' + api.St.onClock);
    api.makePick(p.id);
  }
  return api.St;
}

const configs = [
  { label: '12-team standard snake', s: {} },
  { label: '10-team, 3rd round reversal', s: { teams: 10, type: '3rr' } },
  { label: '14-team linear', s: { teams: 14, type: 'linear' } },
  { label: '12-team superflex', s: { slots: Object.assign({}, api.STANDARD, { SFLEX: 1, BN: 5 }) } },
  { label: '8-team, no K or DEF', s: { teams: 8, slots: Object.assign({}, api.STANDARD, { K: 0, DST: 0, BN: 8 }) } },
  { label: '4-team deep bench', s: { teams: 4, slots: Object.assign({}, api.STANDARD, { BN: 12 }) } },
];

for (const cfg of configs) {
  console.log('\n  ' + cfg.label);
  let st;
  try { st = runDraft(cfg.s); }
  catch (e) { failures++; console.log('  FAIL  threw: ' + e.message); continue; }

  const S = api.S;
  check('every pick made', st.picks.length === api.totalPicks(), st.picks.length + '/' + api.totalPicks());
  check('no player drafted twice', new Set(st.picks.map(p => p.id)).size === st.picks.length);
  check('every roster is full', st.rosters.every(r => r.length === S.rounds),
    st.rosters.map(r => r.length).join(','));

  // Every team should be able to field a legal starting lineup.
  const unfilled = st.rosters.map((r, i) => [i, api.needs(r).open]).filter(x => x[1] > 0);
  check('every team fields a full starting lineup', unfilled.length === 0,
    unfilled.map(x => 'team ' + (x[0] + 1) + ' short ' + x[1]).join('; '));

  // Positional sanity: nobody hoards a scarce position.
  const over = [];
  st.rosters.forEach((r, i) => {
    const c = api.counts(r);
    const capQB = S.slots.SFLEX > 0 ? 3 : 2;
    if ((c.QB || 0) > capQB) over.push('team ' + (i + 1) + ' has ' + c.QB + ' QB');
    if ((c.TE || 0) > 2) over.push('team ' + (i + 1) + ' has ' + c.TE + ' TE');
    if ((c.K || 0) > Math.max(1, S.slots.K)) over.push('team ' + (i + 1) + ' has ' + c.K + ' K');
    if ((c.DST || 0) > Math.max(1, S.slots.DST)) over.push('team ' + (i + 1) + ' has ' + c.DST + ' DEF');
  });
  check('no team hoards a scarce position', over.length === 0, over.join('; '));

  // Kickers and defenses should stay on the board until the end.
  const late = st.picks.filter(p => ['K', 'DST'].includes(api.P[p.id].pos));
  if (late.length) {
    const earliest = Math.min(...late.map(p => Math.floor(p.overall / S.teams) + 1));
    check('K/DEF held until the late rounds', earliest >= S.rounds - 3,
      'earliest was round ' + earliest + ' of ' + S.rounds);
  }

  // The board should broadly track the sheet without being a straight readthrough.
  const first = st.picks.slice(0, S.teams).map(p => api.P[p.id].rank);
  const inOrder = first.every((r, i) => i === 0 || r > first[i - 1]);
  check('round 1 tracks the sheet but is not a pure readthrough',
    Math.max(...first) <= S.teams + 10 && !inOrder,
    'round 1 ranks: ' + first.join(','));
}

/* ── Queue behaviour ────────────────────────────────────────────────────── */
console.log('\nqueue');
api.S = Object.assign({ teams: 12, type: 'snake', clock: 30, pace: 0, slot: 1 },
                      { slots: Object.assign({}, api.STANDARD) });
api.S.rounds = api.rounds();
api.startDraft();
api.St.queue = [byName['Brock Bowers'].id, byName['Trey McBride'].id];
check('autopick takes the top of the queue',
  api.autoChoice(0).name === 'Brock Bowers', api.autoChoice(0).name);
api.St.taken[byName['Brock Bowers'].id] = true;
check('autopick skips a queued player already gone',
  api.autoChoice(0).name === 'Trey McBride', api.autoChoice(0).name);
api.St.taken[byName['Trey McBride'].id] = true;
const fell = api.autoChoice(0);
check('autopick falls back to the board with an exhausted queue', fell && fell.id >= 0,
  fell ? fell.name : 'nothing');

console.log(failures ? '\n' + failures + ' FAILING\n' : '\nall checks passed\n');
process.exit(failures ? 1 : 0);
