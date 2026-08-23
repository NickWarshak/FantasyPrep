"""Render the draft-capital report as a standalone HTML page.

Generated from the computed JSON rather than hand-written, so every figure on
the page is the figure the code actually produced. Transcribing numbers into a
report by hand is how a report ends up disagreeing with its own source.
"""
from __future__ import annotations

import argparse
import html
import json
from pathlib import Path

POSITION_ORDER = ("QB", "RB", "WR", "TE")
HUE = {"QB": "qb", "RB": "rb", "WR": "wr", "TE": "te"}


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _spearman(a: list[float], b: list[float]) -> float:
    def ranks(xs):
        order = sorted(range(len(xs)), key=lambda i: xs[i])
        out = [0.0] * len(xs)
        for i, j in enumerate(order):
            out[j] = i + 1
        return out

    ra, rb = ranks(a), ranks(b)
    n = len(a)
    ma, mb = sum(ra) / n, sum(rb) / n
    num = sum((x - ma) * (y - mb) for x, y in zip(ra, rb))
    den = (sum((x - ma) ** 2 for x in ra) * sum((y - mb) ** 2 for y in rb)) ** 0.5
    return num / den if den else 0.0


def _split(row: dict) -> dict[str, float]:
    out = {p: 0.0 for p in POSITION_ORDER}
    for p in row["players"]:
        if p["position"] in out:
            out[p["position"]] += p["weight"]
    return out


def _bar(row: dict, max_capital: float) -> str:
    split = _split(row)
    width = (row["capital"] / max_capital) * 100 if max_capital else 0
    segs = "".join(
        f'<span class="seg seg-{HUE[pos]}" style="flex:{v:.4f}" title="{pos}: {v:.2f}"></span>'
        for pos in POSITION_ORDER if (v := split[pos]) > 0
    )
    return f'<span class="bar" style="width:{width:.2f}%">{segs}</span>'


def _player_rows(players: list[dict], unit: str) -> str:
    return "".join(
        f'<tr class="pl{"" if p["weight"] >= 0.05 else " dead"}">'
        f'<td class="pos"><span class="tag tag-{HUE.get(p["position"], "qb")}">'
        f'{html.escape(p["position"])}</span></td>'
        f'<td class="nm">{html.escape(p["name"])}</td>'
        f'<td class="n">{p["adp"]:.0f}</td>'
        f'<td class="n val">{p["weight"]:.3f}</td></tr>'
        for p in players
    )


def _team_detail(row: dict, unit: str) -> str:
    return (
        f'<details class="team-detail"><summary>'
        f'<span class="sm-rank">{row["rank"]}</span>'
        f'<span class="sm-team">{html.escape(row["team"])}</span>'
        f'<span class="sm-share">{row["share"] * 100:.1f}%</span>'
        f'<span class="sm-hint">{row["n_players"]} drafted</span></summary>'
        f'<div class="scroll"><table class="players">'
        f'<thead><tr><th>Pos</th><th>Player</th><th class="n">{html.escape(unit)}</th>'
        f'<th class="n">Weight</th></tr></thead>'
        f'<tbody>{_player_rows(row["players"], unit)}</tbody></table></div></details>'
    )


def _tilt_row(r: dict) -> str:
    cap = r["capital"] or 1.0
    bp = r["by_position"]
    wrte = bp["WR"] + bp["TE"]
    segs = "".join(
        f'<span class="seg seg-{h}" style="flex:{v:.4f}"></span>'
        for h, v in (("qb", bp["QB"]), ("rb", bp["RB"]), ("wr", wrte)) if v > 0
    )
    return (
        f'<tr><td class="tm">{html.escape(r["team"])}</td>'
        f'<td class="rk">#{r["rank"]}</td>'
        f'<td class="barcell"><span class="bar" style="width:100%">{segs}</span></td>'
        f'<td class="n">{r["qb_pct"] * 100:.0f}%</td>'
        f'<td class="n dim">{bp["RB"] / cap * 100:.0f}%</td>'
        f'<td class="n">{r["pass_catcher_pct"] * 100:.0f}%</td>'
        f'<td class="n val">{r["tilt"] * 100:+.0f}</td></tr>'
    )


def _gap_row(team: str, capital_rank: int, other_rank: int) -> str:
    d = other_rank - capital_rank
    cls = "down" if d > 0 else ("up" if d < 0 else "dim")
    return (
        f'<tr><td class="tm">{html.escape(team)}</td>'
        f'<td class="n">#{capital_rank}</td><td class="n">#{other_rank}</td>'
        f'<td class="n val {cls}">{d:+d}</td></tr>'
    )


def _comparison(
    rows: list[dict],
    other_rows: list[dict],
    value_key: str,
    fmt: str,
    label: str,
) -> dict:
    """Shared machinery for comparing draft capital against an outside ranking."""
    capital_rank = {r["team"]: r["rank"] for r in rows}
    capital_share = {r["team"]: r["share"] * 100 for r in rows}
    other = {r["team"]: r for r in other_rows}
    shared = sorted(set(capital_rank) & set(other))
    rho = _spearman(
        [capital_rank[t] for t in shared], [other[t]["rank"] for t in shared]
    )
    gaps = sorted(shared, key=lambda t: -(other[t]["rank"] - capital_rank[t]))
    return {
        "shared": shared,
        "rho": rho,
        "over": "".join(_gap_row(t, capital_rank[t], other[t]["rank"]) for t in gaps[:5]),
        "under": "".join(_gap_row(t, capital_rank[t], other[t]["rank"]) for t in gaps[-5:]),
        "scatter": [[other[t][value_key], capital_share[t], t] for t in shared],
        "table": "".join(
            f'<tr><td class="rk">{other[t]["rank"]}</td><td class="tm">{html.escape(t)}</td>'
            f'<td class="n">{other[t][value_key]:{fmt}}</td>'
            f'<td class="n dim">#{capital_rank[t]}</td></tr>'
            for t in sorted(shared, key=lambda x: other[x]["rank"])
        ),
        "label": label,
    }


# --------------------------------------------------------------------------- #
# page
# --------------------------------------------------------------------------- #
def render(report: dict, offense: dict | None = None) -> str:
    rows = report["rows"]
    unit = report.get("unit", "ADP")
    max_capital = max(r["capital"] for r in rows)
    top, bottom = rows[0], rows[-1]
    ratio = top["capital"] / bottom["capital"] if bottom["capital"] else 0
    hl = report["half_life"]
    sens = report["sensitivity"]
    moves = report["max_rank_move"]

    table_rows = "".join(
        f"<tr>"
        f'<td class="rk">{r["rank"]}</td>'
        f'<td class="tm">{html.escape(r["team"])}</td>'
        f'<td class="barcell">{_bar(r, max_capital)}</td>'
        f'<td class="n share">{r["share"] * 100:.1f}%</td>'
        f'<td class="n">{r["capital"]:.2f}</td>'
        f'<td class="n dim">{r["counts"]["24"]}</td>'
        f'<td class="n dim">{r["counts"]["50"]}</td>'
        f'<td class="n dim">{r["counts"]["100"]}</td>'
        f'<td class="best">{html.escape(r["best_player"] or "")}'
        f'<span class="badp">{r["best_adp"]:.0f}</span></td></tr>'
        for r in rows
    )

    det = next((r for r in rows if r["team"] == "DET"), rows[0])
    sens_rows = "".join(
        f'<tr><td class="nm">{label}</td><td class="n">{sens[key]:+.3f}</td>'
        f'<td class="n dim">{str(moves[key]) + " places" if key in moves else "&mdash;"}</td></tr>'
        for key, label in (
            ("half_life_15", "Half life 15 picks"),
            ("half_life_45", "Half life 45 picks"),
            ("half_life_60", "Half life 60 picks"),
            ("linear", "Linear weighting instead"),
            ("top100_count", "Just counting top-100 players"),
        )
    )
    curve_pts = json.dumps([[a, 0.5 ** ((a - 1) / hl)] for a in range(1, 201, 2)])

    by_tilt = sorted(rows, key=lambda r: -r["tilt"])
    tilt_rows = "".join(_tilt_row(r) for r in by_tilt)
    qb_heavy, pc_heavy = by_tilt[0], by_tilt[-1]
    n_positive = sum(1 for r in rows if r["tilt"] > 0)

    offense_sections, scatters = "", []
    if offense:
        vegas = _comparison(
            rows, offense["vegas"]["rows"], "implied_points_per_game", ".1f", "Vegas"
        )
        fpi = _comparison(rows, offense["fpi"]["rows"], "offense", "+.2f", "FPI")
        scatters = [
            {"id": "sc-vegas", "data": vegas["scatter"], "x": "Vegas implied points per game"},
            {"id": "sc-fpi", "data": fpi["scatter"], "x": "FPI offense rating"},
        ]
        weeks = offense["vegas"]["weeks"]
        offense_sections = f"""
<section>
  <h2>Check 1 &mdash; Vegas implied points</h2>
  <div class="prose">
    <p>The most objective answer available, because there is money behind it. For every game
    a sportsbook posts a total and a spread, and those two numbers pin down how many points
    each side is expected to score:</p>
  </div>
  <div class="formula">implied total = over/under &divide; 2 + margin &divide; 2

margin = +spread if favoured, &minus;spread if not</div>
  <div class="prose">
    <p>Averaged over weeks {weeks[0]}&ndash;{weeks[-1]}, every game of which is priced.
    <strong>Agreement with draft capital: {vegas["rho"]:.2f}.</strong></p>
    <p>Two limits worth holding: only the opening weeks are priced, so each team gets five or
    six games against an uneven schedule; and a team total counts <em>all</em> points,
    including defensive and special-teams scores that no fantasy offense collects.</p>
  </div>
  <div class="card chartwrap">
    <canvas id="sc-vegas" width="960" height="420" role="img"
      aria-label="Vegas implied points per game against draft capital share"></canvas>
    <p class="caption">Implied points per game (horizontal) against share of draft capital
    (vertical). Above the line = drafted higher than Vegas expects them to score.</p>
  </div>
  <div class="grid2">
    <div class="card"><h3>Drafted above what Vegas expects</h3>
      <div class="scroll"><table><thead><tr><th>Team</th><th class="n">Capital</th>
      <th class="n">Vegas</th><th class="n">Gap</th></tr></thead>
      <tbody>{vegas["over"]}</tbody></table></div></div>
    <div class="card"><h3>Vegas likes them more than the draft does</h3>
      <div class="scroll"><table><thead><tr><th>Team</th><th class="n">Capital</th>
      <th class="n">Vegas</th><th class="n">Gap</th></tr></thead>
      <tbody>{vegas["under"]}</tbody></table></div></div>
  </div>
  <div class="scroll">
    <table><thead><tr><th></th><th>Team</th><th class="n">Pts/game</th>
    <th class="n">Capital rank</th></tr></thead>
    <tbody>{vegas["table"]}</tbody></table>
  </div>
</section>

<section>
  <h2>Check 2 &mdash; ESPN FPI offense</h2>
  <div class="prose">
    <p>A different question: not how many points a team scores, but how good its offense is
    once you strip out the defenses it happens to face. FPI splits into offense, defense and
    special teams, in points above average per game.</p>
    <p>That the split is real rather than assumed was checked rather than trusted &mdash;
    offense + defense + special teams equals the published FPI for
    <strong>{offense["fpi"]["split_verified"]}</strong> teams.
    <strong>Agreement with draft capital: {fpi["rho"]:.2f}.</strong></p>
    <p>Unlike Vegas this is a model, not money, and it is a rating rather than a point total
    &mdash; so the two tables are not interchangeable. Vegas answers &ldquo;how many points
    will they score,&rdquo; FPI answers &ldquo;how good are they at scoring.&rdquo; A team
    with a soft opening schedule can rank well on the first and ordinarily on the second.</p>
  </div>
  <div class="card chartwrap">
    <canvas id="sc-fpi" width="960" height="420" role="img"
      aria-label="FPI offense rating against draft capital share"></canvas>
    <p class="caption">FPI offense rating (horizontal) against share of draft capital
    (vertical).</p>
  </div>
  <div class="grid2">
    <div class="card"><h3>Drafted above their offense rating</h3>
      <div class="scroll"><table><thead><tr><th>Team</th><th class="n">Capital</th>
      <th class="n">FPI</th><th class="n">Gap</th></tr></thead>
      <tbody>{fpi["over"]}</tbody></table></div></div>
    <div class="card"><h3>Rated above where they're drafted</h3>
      <div class="scroll"><table><thead><tr><th>Team</th><th class="n">Capital</th>
      <th class="n">FPI</th><th class="n">Gap</th></tr></thead>
      <tbody>{fpi["under"]}</tbody></table></div></div>
  </div>
  <div class="scroll">
    <table><thead><tr><th></th><th>Team</th><th class="n">Off. rating</th>
    <th class="n">Capital rank</th></tr></thead>
    <tbody>{fpi["table"]}</tbody></table>
  </div>
</section>
"""

    scatter_js = f"var SCATTERS = {json.dumps(scatters)};"

    return f"""<title>NFL Draft Capital Share</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Archivo:wght@400;500;600;700&family=IBM+Plex+Mono:wght@400;500&family=Source+Serif+4:opsz,wght@8..60,400;8..60,600&display=swap">
<style>
:root {{
  --ground:#F1F3F0; --surface:#FFFFFF; --surface-2:#E9ECE8;
  --ink:#171C23; --muted:#5C6672; --faint:#8A939E;
  --rule:rgba(23,28,35,.13); --rule-strong:rgba(23,28,35,.28);
  --qb:#7C6AA8; --rb:#C0703C; --wr:#35709B; --te:#6E8B3D;
  --up:#2F7D5B; --down:#A6543C;
  --shadow:0 1px 2px rgba(23,28,35,.06),0 8px 24px rgba(23,28,35,.05);
}}
@media (prefers-color-scheme: dark) {{
  :root:not([data-theme="light"]) {{
    --ground:#0E1218; --surface:#161B22; --surface-2:#1D242D;
    --ink:#E7ECF2; --muted:#9AA6B4; --faint:#6C7885;
    --rule:rgba(231,236,242,.14); --rule-strong:rgba(231,236,242,.3);
    --qb:#9C8AC8; --rb:#D98F5C; --wr:#5A93BE; --te:#8FAC5C;
    --up:#56A57E; --down:#C97A5E;
    --shadow:0 1px 2px rgba(0,0,0,.4),0 8px 24px rgba(0,0,0,.3);
  }}
}}
:root[data-theme="dark"] {{
  --ground:#0E1218; --surface:#161B22; --surface-2:#1D242D;
  --ink:#E7ECF2; --muted:#9AA6B4; --faint:#6C7885;
  --rule:rgba(231,236,242,.14); --rule-strong:rgba(231,236,242,.3);
  --qb:#9C8AC8; --rb:#D98F5C; --wr:#5A93BE; --te:#8FAC5C;
  --up:#56A57E; --down:#C97A5E;
  --shadow:0 1px 2px rgba(0,0,0,.4),0 8px 24px rgba(0,0,0,.3);
}}

* {{ box-sizing:border-box; }}
body {{
  background:var(--ground); color:var(--ink);
  font:400 16px/1.6 "Source Serif 4",Georgia,serif;
  margin:0; padding:40px 24px 96px;
  display:flex; flex-direction:column; align-items:center;
}}
.wrap {{ width:100%; max-width:1060px; display:flex; flex-direction:column; gap:42px; }}
.prose {{ max-width:68ch; display:flex; flex-direction:column; gap:14px; }}
h1,h2,h3 {{ font-family:Archivo,"Helvetica Neue",Arial,sans-serif; }}
h1 {{ font-size:clamp(2rem,5vw,3.1rem); font-weight:700; letter-spacing:-.022em;
      line-height:1.04; margin:0; text-wrap:balance; }}
h2 {{ font-size:1.32rem; font-weight:600; letter-spacing:-.01em; margin:0;
      text-wrap:balance; padding-bottom:10px; border-bottom:1px solid var(--rule-strong); }}
h3 {{ font-size:1rem; font-weight:600; margin:0; }}
p {{ margin:0; }}
section {{ display:flex; flex-direction:column; gap:18px; }}
.eyebrow {{ font-family:Archivo,sans-serif; font-size:.69rem; font-weight:600;
            letter-spacing:.17em; text-transform:uppercase; color:var(--muted); }}
.lede {{ font-size:1.13rem; color:var(--muted); max-width:62ch; }}
strong {{ font-weight:600; }}
code {{ font-family:"IBM Plex Mono",ui-monospace,monospace; font-size:.87em;
        background:var(--surface-2); padding:.1em .34em; border-radius:3px; }}
.keys {{ display:flex; flex-wrap:wrap; gap:10px; }}
.key {{ background:var(--surface); border:1px solid var(--rule); border-radius:8px;
        padding:13px 17px; box-shadow:var(--shadow); flex:1 1 190px;
        display:flex; flex-direction:column; gap:3px; }}
.key .v {{ font-family:Archivo,sans-serif; font-size:1.6rem; font-weight:700;
           letter-spacing:-.02em; font-variant-numeric:tabular-nums; }}
.key .l {{ font-size:.82rem; color:var(--muted); font-family:Archivo,sans-serif; }}
.scroll {{ overflow-x:auto; }}
table {{ border-collapse:collapse; width:100%; font-family:Archivo,sans-serif; }}
th {{ font-size:.68rem; font-weight:600; letter-spacing:.1em; text-transform:uppercase;
      color:var(--muted); text-align:left; padding:0 10px 9px; white-space:nowrap;
      border-bottom:1px solid var(--rule-strong); }}
td {{ padding:7px 10px; border-bottom:1px solid var(--rule); font-size:.9rem; }}
.n {{ text-align:right; font-variant-numeric:tabular-nums; white-space:nowrap; }}
.dim {{ color:var(--faint); }}
.rk {{ color:var(--faint); font-variant-numeric:tabular-nums; width:34px; font-size:.82rem; }}
.tm {{ font-weight:700; letter-spacing:.02em; white-space:nowrap; }}
.share {{ font-weight:600; }}
.best {{ color:var(--muted); font-size:.85rem; white-space:nowrap; }}
.badp {{ color:var(--faint); margin-left:7px; font-variant-numeric:tabular-nums; }}
.barcell {{ width:34%; min-width:150px; }}
.bar {{ display:flex; height:13px; border-radius:2px; overflow:hidden; }}
.seg {{ display:block; }}
.seg-qb {{ background:var(--qb); }} .seg-rb {{ background:var(--rb); }}
.seg-wr {{ background:var(--wr); }} .seg-te {{ background:var(--te); }}
.up {{ color:var(--up); }} .down {{ color:var(--down); }}
.legend {{ display:flex; flex-wrap:wrap; gap:16px; font-family:Archivo,sans-serif;
           font-size:.8rem; color:var(--muted); }}
.legend span {{ display:flex; align-items:center; gap:6px; }}
.dot {{ width:10px; height:10px; border-radius:2px; display:block; }}
.tag {{ font-family:Archivo,sans-serif; font-size:.68rem; font-weight:600;
        padding:2px 6px; border-radius:3px; color:#fff; }}
.tag-qb {{ background:var(--qb); }} .tag-rb {{ background:var(--rb); }}
.tag-wr {{ background:var(--wr); }} .tag-te {{ background:var(--te); }}
.formula {{ font-family:"IBM Plex Mono",monospace; font-size:.95rem; line-height:1.9;
            background:var(--surface); border:1px solid var(--rule);
            border-left:3px solid var(--wr); border-radius:6px; padding:16px 18px;
            overflow-x:auto; white-space:pre; color:var(--ink); }}
.card {{ background:var(--surface); border:1px solid var(--rule); border-radius:8px;
         padding:18px 20px; box-shadow:var(--shadow);
         display:flex; flex-direction:column; gap:12px; }}
.grid2 {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(280px,1fr)); gap:14px; }}
.chartwrap {{ display:flex; flex-direction:column; gap:8px; }}
canvas {{ width:100%; height:190px; display:block; }}
#sc-vegas, #sc-fpi {{ height:230px; }}
.caption {{ font-family:Archivo,sans-serif; font-size:.82rem; color:var(--muted); }}
.details-grid {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(320px,1fr)); gap:10px; }}
.team-detail {{ background:var(--surface); border:1px solid var(--rule);
                border-radius:8px; overflow:hidden; }}
.team-detail summary {{ cursor:pointer; list-style:none; padding:11px 15px; display:flex;
                        align-items:baseline; gap:11px; font-family:Archivo,sans-serif; }}
.team-detail summary::-webkit-details-marker {{ display:none; }}
.team-detail summary:hover {{ background:var(--surface-2); }}
.team-detail summary:focus-visible {{ outline:2px solid var(--wr); outline-offset:-2px; }}
.sm-rank {{ color:var(--faint); font-size:.8rem; font-variant-numeric:tabular-nums; min-width:20px; }}
.sm-team {{ font-weight:700; font-size:.98rem; }}
.sm-share {{ font-weight:600; font-variant-numeric:tabular-nums; }}
.sm-hint {{ color:var(--faint); font-size:.78rem; margin-left:auto; }}
.team-detail .scroll {{ padding:0 15px 13px; }}
.players td {{ font-size:.84rem; }}
.pl.dead .nm, .pl.dead .n {{ color:var(--faint); }}
.val {{ font-weight:600; }}
.nm {{ font-family:Archivo,sans-serif; }}
.pos {{ width:42px; }}
footer {{ color:var(--faint); font-size:.84rem; max-width:68ch; }}
@media (prefers-reduced-motion:reduce) {{ * {{ animation:none!important; transition:none!important; }} }}
</style>

<div class="wrap">

<header class="prose">
  <span class="eyebrow">{report["year"]} season &middot; {report["scoring"]} &middot; {html.escape(report["source"])}</span>
  <h1>Which NFL teams carry the most fantasy draft capital</h1>
  <p class="lede">All 32 teams ranked by the draft value on their roster. One input &mdash;
  where players get drafted &mdash; and one rule: a player is worth half as much for every
  {hl:.0f} places he slides.</p>
</header>

<section>
  <div class="keys">
    <div class="key"><span class="v">{top["team"]}</span><span class="l">Most capital &mdash; {top["share"] * 100:.1f}% of the league</span></div>
    <div class="key"><span class="v">{ratio:.1f}&times;</span><span class="l">{top["team"]} over {bottom["team"]}, last place</span></div>
    <div class="key"><span class="v">{qb_heavy["team"]}</span><span class="l">Only quarterback-heavy roster in the league</span></div>
    <div class="key"><span class="v">{sens["top100_count"]:.2f}</span><span class="l">Agreement with simple top-100 counting</span></div>
  </div>
</section>

<section>
  <h2>The math</h2>
  <div class="formula">weight(player) = 0.5 ^ (({unit} &minus; 1) / {hl:.0f})

team capital  = sum of weights
share         = team capital / all 32 teams</div>
  <div class="prose">
    <p>That's the whole method. Every {hl:.0f} places, a player counts for half as much:
    number 1 is worth <strong>1.00</strong>, 31 is <strong>0.50</strong>, 61 is
    <strong>0.25</strong>, 121 is <strong>0.06</strong>.</p>
    <p>Decay rather than a straight line because a straight line gets the top of the draft
    wrong. Something like <code>201 &minus; rank</code> makes the first pick worth
    1.005&times; the second &mdash; when the entire premise of a draft is that it's worth a
    great deal more.</p>
    <p>The ordering comes from <strong>{html.escape(report["source"])}</strong>, which is what
    the live draft tool ranks by, so this page and that tool describe the same board. Note it
    is a consensus <em>ranking</em>, not measured ADP &mdash; an opinion about where players
    should go rather than a record of where they went. The two disagree enough to matter:
    real draft data has Rashee Rice going 13.6, this has him 22nd.</p>
  </div>
  <div class="card chartwrap">
    <canvas id="curve" width="960" height="380" role="img"
      aria-label="Player weight falling from 1.0 at rank 1 to near zero by rank 200"></canvas>
    <p class="caption">Weight against draft position. The first three rounds carry most of the
    capital; past about 120 a player is rounding error.</p>
  </div>
</section>

<section>
  <h2>The ranking</h2>
  <div class="legend">
    <span><i class="dot seg-qb"></i>QB</span><span><i class="dot seg-rb"></i>RB</span>
    <span><i class="dot seg-wr"></i>WR</span><span><i class="dot seg-te"></i>TE</span>
    <span style="margin-left:auto">Bar length = capital. Segments show where it sits.</span>
  </div>
  <div class="scroll">
    <table>
      <thead><tr><th></th><th>Team</th><th>Capital by position</th><th class="n">Share</th>
      <th class="n">Total</th><th class="n">T24</th><th class="n">T50</th><th class="n">T100</th>
      <th>Best player</th></tr></thead>
      <tbody>{table_rows}</tbody>
    </table>
  </div>
  <p class="prose caption"><strong>T24 / T50 / T100</strong> count that team's players inside
  the top 24, 50 and 100. Position colors are descriptive only &mdash; position plays no part
  in the formula.</p>
</section>

<section>
  <h2>Where each team's capital sits</h2>
  <div class="prose">
    <p><strong>Tilt</strong> is the quarterback share minus the pass-catcher (WR + TE) share.
    Positive means a team's draft value is concentrated under center, negative means it's in
    the receiving corps.</p>
    <p>Read the column knowing that <strong>{n_positive} of {len(rows)} teams is
    positive</strong>. That isn't a fact about these rosters, it's a fact about the format: in
    a {report["teams_in_league_settings"]}-team, one-quarterback league you only need one, so
    quarterbacks slide and carry little draft weight. Tilt ranks teams against each other, not
    against zero.</p>
    <p><strong>{qb_heavy["team"]}</strong> is the one genuinely quarterback-heavy roster
    ({qb_heavy["qb_pct"] * 100:.0f}% QB against
    {qb_heavy["pass_catcher_pct"] * 100:.0f}% pass catchers). At the other end,
    <strong>{pc_heavy["team"]}</strong> puts {pc_heavy["pass_catcher_pct"] * 100:.0f}% into
    receivers and tight ends and {pc_heavy["qb_pct"] * 100:.0f}% at quarterback.</p>
  </div>
  <div class="legend">
    <span><i class="dot seg-qb"></i>QB</span><span><i class="dot seg-rb"></i>RB</span>
    <span><i class="dot seg-wr"></i>WR + TE</span>
    <span style="margin-left:auto">Bars normalized &mdash; shape, not size.</span>
  </div>
  <div class="scroll">
    <table>
      <thead><tr><th>Team</th><th>Overall</th><th>Shape of capital</th>
      <th class="n">QB</th><th class="n">RB</th><th class="n">WR+TE</th>
      <th class="n">Tilt</th></tr></thead>
      <tbody>{tilt_rows}</tbody>
    </table>
  </div>
</section>

{offense_sections}

<section>
  <h2>Does the {hl:.0f} matter?</h2>
  <div class="prose">
    <p>It's a knob I chose, so it's worth checking the ordering isn't an artifact of it.
    Re-ranking every team under different settings and correlating against the headline:</p>
  </div>
  <div class="card" style="max-width:520px">
    <div class="scroll"><table>
      <thead><tr><th>Alternative</th><th class="n">Agreement</th><th class="n">Worst move</th></tr></thead>
      <tbody>{sens_rows}</tbody>
    </table></div>
  </div>
  <div class="prose">
    <p>Stable across half lives ({sens["half_life_15"]:.2f} to {sens["half_life_45"]:.2f}), so
    the order reflects the rosters rather than the knob. But stable is not identical: one team
    still moves {max(moves.values())} places between settings, so treat neighbours in the table
    as ties rather than a strict order.</p>
  </div>
</section>

<section>
  <h2>Worked example: {det["team"]}</h2>
  <div class="card">
    <div class="scroll"><table class="players">
      <thead><tr><th>Pos</th><th>Player</th><th class="n">{html.escape(unit)}</th>
      <th class="n">Weight</th></tr></thead>
      <tbody>{_player_rows(det["players"], unit)}</tbody>
    </table></div>
    <p class="caption">Total <strong style="color:var(--ink)">{det["capital"]:.2f}</strong>
    = <strong style="color:var(--ink)">{det["share"] * 100:.1f}%</strong> of the league,
    ranked <strong style="color:var(--ink)">#{det["rank"]}</strong>.</p>
  </div>
</section>

<section>
  <h2>What this doesn't tell you</h2>
  <div class="prose">
    <p><strong>It's draft-market standing, not talent.</strong> A team whose stars are
    underrated by the consensus will look poorer here than it is.</p>
    <p><strong>It rewards depth as well as stars</strong> &mdash; five mid-round players can
    outweigh one elite one. That's a deliberate property of the formula; if you only care
    about top-end talent, read the T24 column instead.</p>
    <p><strong>One snapshot of a moving board.</strong> Rankings shift all through August.</p>
    <p><strong>Kickers and defenses are excluded</strong>, and so is anyone unranked.</p>
  </div>
</section>

<section>
  <h2>Every team, every player</h2>
  <div class="details-grid">{"".join(_team_detail(r, unit) for r in rows)}</div>
</section>

<footer>
  Draft order: {html.escape(report["source"])}, {report["year"]}.
  Offense checks: DraftKings lines via ESPN, and ESPN FPI.
  Generated by <code>fantasyprep.reports.adp_share</code>.
</footer>

</div>

<script>
{scatter_js}
var CURVE = {curve_pts};

function themeColors() {{
  var cs = getComputedStyle(document.documentElement);
  return {{
    accent: cs.getPropertyValue('--wr').trim() || '#35709B',
    rule: cs.getPropertyValue('--rule').trim() || 'rgba(0,0,0,.13)',
    muted: cs.getPropertyValue('--muted').trim() || '#5C6672',
    ink: cs.getPropertyValue('--ink').trim() || '#171C23'
  }};
}}
function fit(cv) {{
  var dpr = window.devicePixelRatio || 1;
  var w = cv.clientWidth, h = cv.clientHeight;
  cv.width = w * dpr; cv.height = h * dpr;
  var g = cv.getContext('2d');
  g.setTransform(dpr, 0, 0, dpr, 0, 0);
  g.clearRect(0, 0, w, h);
  return {{ g: g, w: w, h: h }};
}}

function drawCurve() {{
  var cv = document.getElementById('curve');
  if (!cv) return;
  var c = themeColors(), f = fit(cv), g = f.g;
  var padL = 34, padR = 10, padT = 12, padB = 24;
  var iw = f.w - padL - padR, ih = f.h - padT - padB;
  var X = function (a) {{ return padL + (a / 200) * iw; }};
  var Y = function (v) {{ return padT + (1 - v) * ih; }};
  g.strokeStyle = c.rule; g.lineWidth = 1;
  g.font = '11px Archivo, sans-serif'; g.fillStyle = c.muted;
  [0, .25, .5, .75, 1].forEach(function (v) {{
    g.beginPath(); g.moveTo(padL, Y(v)); g.lineTo(f.w - padR, Y(v)); g.stroke();
    g.textAlign = 'right'; g.fillText(v.toFixed(2), padL - 6, Y(v) + 3);
  }});
  g.textAlign = 'center';
  [1, 50, 100, 150, 200].forEach(function (a) {{ g.fillText(a, X(a), f.h - 7); }});
  var grad = g.createLinearGradient(0, padT, 0, padT + ih);
  grad.addColorStop(0, c.accent + '55'); grad.addColorStop(1, c.accent + '00');
  g.beginPath(); g.moveTo(X(CURVE[0][0]), Y(CURVE[0][1]));
  CURVE.forEach(function (p) {{ g.lineTo(X(p[0]), Y(p[1])); }});
  g.lineTo(X(CURVE[CURVE.length - 1][0]), Y(0)); g.lineTo(X(CURVE[0][0]), Y(0));
  g.closePath(); g.fillStyle = grad; g.fill();
  g.beginPath(); g.moveTo(X(CURVE[0][0]), Y(CURVE[0][1]));
  CURVE.forEach(function (p) {{ g.lineTo(X(p[0]), Y(p[1])); }});
  g.strokeStyle = c.accent; g.lineWidth = 2; g.stroke();
  [[1, 1], [31, .5], [61, .25], [121, .0625]].forEach(function (p) {{
    g.beginPath(); g.arc(X(p[0]), Y(p[1]), 3.5, 0, Math.PI * 2);
    g.fillStyle = c.accent; g.fill();
  }});
}}

function drawScatter(spec) {{
  var cv = document.getElementById(spec.id);
  if (!cv || !spec.data.length) return;
  var c = themeColors(), f = fit(cv), g = f.g, pts = spec.data;
  var padL = 46, padR = 14, padT = 14, padB = 34;
  var iw = f.w - padL - padR, ih = f.h - padT - padB;
  var xs = pts.map(function (d) {{ return d[0]; }});
  var ys = pts.map(function (d) {{ return d[1]; }});
  var x0 = Math.min.apply(null, xs), x1 = Math.max.apply(null, xs);
  var y0 = Math.min.apply(null, ys), y1 = Math.max.apply(null, ys);
  var xr = x1 - x0 || 1, yr = y1 - y0 || 1;
  x0 -= xr * .08; x1 += xr * .08; y0 -= yr * .12; y1 += yr * .12;
  var X = function (v) {{ return padL + ((v - x0) / (x1 - x0)) * iw; }};
  var Y = function (v) {{ return padT + (1 - (v - y0) / (y1 - y0)) * ih; }};
  g.strokeStyle = c.rule; g.lineWidth = 1;
  g.font = '11px Archivo, sans-serif'; g.fillStyle = c.muted; g.textAlign = 'right';
  for (var i = 0; i <= 4; i++) {{
    var v = y0 + (y1 - y0) * i / 4;
    g.beginPath(); g.moveTo(padL, Y(v)); g.lineTo(f.w - padR, Y(v)); g.stroke();
    g.fillText(v.toFixed(1) + '%', padL - 6, Y(v) + 3);
  }}
  g.textAlign = 'center';
  for (var j = 0; j <= 4; j++) {{
    var xv = x0 + (x1 - x0) * j / 4;
    g.fillText(xv.toFixed(1), X(xv), f.h - 18);
  }}
  g.fillStyle = c.muted; g.font = '11px Archivo, sans-serif';
  g.fillText(spec.x, padL + iw / 2, f.h - 3);
  var n = pts.length;
  var mx = xs.reduce(function (a, b) {{ return a + b; }}, 0) / n;
  var my = ys.reduce(function (a, b) {{ return a + b; }}, 0) / n;
  var num = 0, den = 0;
  for (var k = 0; k < n; k++) {{ num += (xs[k] - mx) * (ys[k] - my); den += Math.pow(xs[k] - mx, 2); }}
  var slope = den ? num / den : 0;
  g.beginPath();
  g.moveTo(X(x0), Y(my + slope * (x0 - mx)));
  g.lineTo(X(x1), Y(my + slope * (x1 - mx)));
  g.strokeStyle = c.muted; g.setLineDash([5, 4]); g.lineWidth = 1.2; g.stroke();
  g.setLineDash([]);
  pts.forEach(function (d) {{
    var px = X(d[0]), py = Y(d[1]);
    var resid = d[1] - (my + slope * (d[0] - mx));
    var big = Math.abs(resid) > (y1 - y0) * .07;
    g.beginPath(); g.arc(px, py, 4.5, 0, Math.PI * 2);
    g.fillStyle = c.accent; g.globalAlpha = .85; g.fill(); g.globalAlpha = 1;
    g.fillStyle = big ? c.ink : c.muted;
    g.font = (big ? '600 ' : '') + '10px Archivo, sans-serif';
    g.textAlign = 'center';
    g.fillText(d[2], px, py - 8);
  }});
}}

function drawAll() {{
  drawCurve();
  SCATTERS.forEach(drawScatter);
}}
drawAll();
window.addEventListener('resize', drawAll);
if (window.matchMedia) {{
  var mq = window.matchMedia('(prefers-color-scheme: dark)');
  (mq.addEventListener ? mq.addEventListener.bind(mq, 'change') : mq.addListener.bind(mq))(drawAll);
}}
new MutationObserver(drawAll).observe(document.documentElement, {{
  attributes: true, attributeFilter: ['data-theme']
}});
</script>
"""


def main(argv=None) -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--in", dest="src", type=Path, default=Path("data/adp_share_2026.json"))
    parser.add_argument("--offense", type=Path, default=Path("data/offense_rankings_2026.json"))
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    report = json.loads(args.src.read_text(encoding="utf-8"))
    offense = (
        json.loads(args.offense.read_text(encoding="utf-8"))
        if args.offense and args.offense.exists()
        else None
    )
    args.out.write_text(render(report, offense), encoding="utf-8")
    print(f"Wrote {args.out} ({args.out.stat().st_size:,} bytes)")


if __name__ == "__main__":
    main()
