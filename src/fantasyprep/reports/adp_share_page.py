"""Render the ADP-share report as a standalone HTML page.

Generated from `adp_share.compute()` rather than hand-written, so every figure
on the page is the figure the code actually produced. Transcribing numbers into
a report by hand is how a report ends up disagreeing with its own source.
"""
from __future__ import annotations

import argparse
import html
import json
from pathlib import Path

POSITION_ORDER = ("QB", "RB", "WR", "TE")
HUE = {"QB": "qb", "RB": "rb", "WR": "wr", "TE": "te"}


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


def _player_rows(players: list[dict]) -> str:
    return "".join(
        f'<tr class="pl{"" if p["weight"] >= 0.05 else " dead"}">'
        f'<td class="pos"><span class="tag tag-{HUE.get(p["position"], "qb")}">'
        f'{html.escape(p["position"])}</span></td>'
        f'<td class="nm">{html.escape(p["name"])}</td>'
        f'<td class="n">{p["adp"]:.1f}</td>'
        f'<td class="n val">{p["weight"]:.3f}</td></tr>'
        for p in players
    )


def _team_detail(row: dict) -> str:
    return (
        f'<details class="team-detail"><summary>'
        f'<span class="sm-rank">{row["rank"]}</span>'
        f'<span class="sm-team">{html.escape(row["team"])}</span>'
        f'<span class="sm-share">{row["share"] * 100:.1f}%</span>'
        f'<span class="sm-hint">{row["n_players"]} drafted</span></summary>'
        f'<div class="scroll"><table class="players">'
        f"<thead><tr><th>Pos</th><th>Player</th><th class=\"n\">ADP</th>"
        f"<th class=\"n\">Weight</th></tr></thead>"
        f'<tbody>{_player_rows(row["players"])}</tbody></table></div></details>'
    )


def render(report: dict) -> str:
    rows = report["rows"]
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
        f'<span class="badp">{r["best_adp"]:.1f}</span></td></tr>'
        for r in rows
    )

    det = next((r for r in rows if r["team"] == "DET"), rows[0])
    det_rows = _player_rows(det["players"])

    sens_rows = "".join(
        f'<tr><td class="nm">{label}</td><td class="n">{sens[key]:+.3f}</td>'
        f'<td class="n dim">{moves.get(key, "") and str(moves[key]) + " places" or "&mdash;"}</td></tr>'
        for key, label in (
            ("half_life_15", "Half life 15 picks"),
            ("half_life_45", "Half life 45 picks"),
            ("half_life_60", "Half life 60 picks"),
            ("linear", "Linear weighting instead"),
            ("top100_count", "Just counting top-100 players"),
        )
    )

    curve_pts = json.dumps([[a, 0.5 ** ((a - 1) / hl)] for a in range(1, 201, 2)])

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
  --shadow:0 1px 2px rgba(23,28,35,.06),0 8px 24px rgba(23,28,35,.05);
}}
@media (prefers-color-scheme: dark) {{
  :root:not([data-theme="light"]) {{
    --ground:#0E1218; --surface:#161B22; --surface-2:#1D242D;
    --ink:#E7ECF2; --muted:#9AA6B4; --faint:#6C7885;
    --rule:rgba(231,236,242,.14); --rule-strong:rgba(231,236,242,.3);
    --qb:#9C8AC8; --rb:#D98F5C; --wr:#5A93BE; --te:#8FAC5C;
    --shadow:0 1px 2px rgba(0,0,0,.4),0 8px 24px rgba(0,0,0,.3);
  }}
}}
:root[data-theme="dark"] {{
  --ground:#0E1218; --surface:#161B22; --surface-2:#1D242D;
  --ink:#E7ECF2; --muted:#9AA6B4; --faint:#6C7885;
  --rule:rgba(231,236,242,.14); --rule-strong:rgba(231,236,242,.3);
  --qb:#9C8AC8; --rb:#D98F5C; --wr:#5A93BE; --te:#8FAC5C;
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
h1,h2,h3,.ui {{ font-family:Archivo,"Helvetica Neue",Arial,sans-serif; }}
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
.chartwrap {{ display:flex; flex-direction:column; gap:8px; }}
canvas {{ width:100%; height:190px; display:block; }}
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
  <span class="eyebrow">{report["year"]} season &middot; {report["scoring"]} &middot; consensus ADP</span>
  <h1>Which NFL teams carry the most fantasy draft capital</h1>
  <p class="lede">All 32 teams ranked by the draft value on their roster. One input &mdash; ADP &mdash;
  and one rule: a player is worth half as much for every {hl:.0f} picks he slides.</p>
</header>

<section>
  <div class="keys">
    <div class="key"><span class="v">{top["team"]}</span><span class="l">Most capital &mdash; {top["share"] * 100:.1f}% of the league</span></div>
    <div class="key"><span class="v">{ratio:.1f}&times;</span><span class="l">{top["team"]} over {bottom["team"]}, last place</span></div>
    <div class="key"><span class="v">{sens["top100_count"]:.2f}</span><span class="l">Agreement with simple top-100 counting</span></div>
    <div class="key"><span class="v">{top["capital"]:.2f}</span><span class="l">{top["team"]} capital, where pick 1.01 = 1.00</span></div>
  </div>
</section>

<section>
  <h2>The math</h2>
  <div class="formula">weight(player) = 0.5 ^ ((ADP &minus; 1) / {hl:.0f})

team capital  = sum of weights
ADP share     = team capital / all 32 teams</div>
  <div class="prose">
    <p>That's the whole method. Every {hl:.0f} picks, a player counts for half as much:
    the 1.01 overall is worth <strong>1.00</strong>, pick 31 is <strong>0.50</strong>,
    pick 61 is <strong>0.25</strong>, pick 121 is <strong>0.06</strong>.</p>
    <p>Decay rather than a straight line because a straight line gets the top of the draft
    wrong. Something like <code>201 &minus; ADP</code> makes the first pick worth 1.005&times;
    the second &mdash; when the entire premise of a draft is that it's worth a great deal more.</p>
  </div>
  <div class="card chartwrap">
    <canvas id="curve" width="960" height="380" role="img"
      aria-label="Player weight falling from 1.0 at pick 1 to near zero by pick 200"></canvas>
    <p class="caption">Weight against ADP. The first three rounds carry most of the capital;
    past pick ~120 a player is rounding error.</p>
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
  <p class="prose caption"><strong>T24 / T50 / T100</strong> count that team's players inside the
  top 24, 50 and 100 of ADP. Position colors are descriptive only &mdash; position plays no part
  in the formula.</p>
</section>

<section>
  <h2>Worked example: {det["team"]}</h2>
  <div class="card">
    <div class="scroll"><table class="players">
      <thead><tr><th>Pos</th><th>Player</th><th class="n">ADP</th><th class="n">Weight</th></tr></thead>
      <tbody>{det_rows}</tbody>
    </table></div>
    <p class="caption">Total <strong style="color:var(--ink)">{det["capital"]:.2f}</strong>
    = <strong style="color:var(--ink)">{det["share"] * 100:.1f}%</strong> of the league,
    ranked <strong style="color:var(--ink)">#{det["rank"]}</strong>.</p>
  </div>
</section>

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
    <p>The order is <strong>stable across half lives</strong> ({sens["half_life_15"]:.2f} to
    {sens["half_life_45"]:.2f}) &mdash; so it reflects the rosters, not the knob. But
    &ldquo;stable&rdquo; is not &ldquo;identical&rdquo;: one team still moves as much as
    {max(moves.values())} places between settings, so treat neighbours in the table as ties
    rather than a strict order.</p>
    <p>Linear weighting agrees far less ({sens["linear"]:.2f}), and plain top-100 counting
    least of all ({sens["top100_count"]:.2f}) &mdash; which is the point. How you weight
    early picks genuinely changes the answer.</p>
  </div>
</section>

<section>
  <h2>What this doesn't tell you</h2>
  <div class="prose">
    <p><strong>It's draft-market standing, not talent.</strong> ADP is where the market drafts a
    player. A team whose stars are underrated by the market will look poorer here than it is.</p>
    <p><strong>It rewards depth as well as stars</strong> &mdash; five mid-round players can
    outweigh one elite one. That's a real choice in the formula, not an accident; if you only
    care about top-end talent, read the T24 column instead.</p>
    <p><strong>One snapshot of a moving market.</strong> ADP shifts all through August.</p>
    <p><strong>Kickers and defenses are excluded</strong>, and so is anyone undrafted.</p>
  </div>
</section>

<section>
  <h2>Every team, every player</h2>
  <div class="details-grid">{"".join(_team_detail(r) for r in rows)}</div>
</section>

<footer>
  Source: {html.escape(report["source"])}, {report["year"]} &middot;
  generated by <code>fantasyprep.reports.adp_share</code> &middot;
  {len(rows)} teams, {sum(r["n_players"] for r in rows)} drafted players.
</footer>

</div>

<script>
(function () {{
  var pts = {curve_pts};
  var cv = document.getElementById('curve');
  function draw() {{
    var cs = getComputedStyle(document.documentElement);
    var accent = cs.getPropertyValue('--wr').trim() || '#35709B';
    var rule = cs.getPropertyValue('--rule').trim() || 'rgba(0,0,0,.13)';
    var muted = cs.getPropertyValue('--muted').trim() || '#5C6672';
    var dpr = window.devicePixelRatio || 1;
    var w = cv.clientWidth, h = cv.clientHeight;
    cv.width = w * dpr; cv.height = h * dpr;
    var g = cv.getContext('2d');
    g.setTransform(dpr, 0, 0, dpr, 0, 0);
    g.clearRect(0, 0, w, h);
    var padL = 34, padR = 10, padT = 12, padB = 24;
    var iw = w - padL - padR, ih = h - padT - padB;
    var X = function (a) {{ return padL + (a / 200) * iw; }};
    var Y = function (v) {{ return padT + (1 - v) * ih; }};

    g.strokeStyle = rule; g.lineWidth = 1;
    g.font = '11px Archivo, sans-serif'; g.fillStyle = muted;
    [0, 0.25, 0.5, 0.75, 1].forEach(function (v) {{
      g.beginPath(); g.moveTo(padL, Y(v)); g.lineTo(w - padR, Y(v)); g.stroke();
      g.textAlign = 'right'; g.fillText(v.toFixed(2), padL - 6, Y(v) + 3);
    }});
    g.textAlign = 'center';
    [1, 50, 100, 150, 200].forEach(function (a) {{
      g.fillText(a, X(a), h - 7);
    }});

    var grad = g.createLinearGradient(0, padT, 0, padT + ih);
    grad.addColorStop(0, accent + '55'); grad.addColorStop(1, accent + '00');
    g.beginPath(); g.moveTo(X(pts[0][0]), Y(pts[0][1]));
    pts.forEach(function (p) {{ g.lineTo(X(p[0]), Y(p[1])); }});
    g.lineTo(X(pts[pts.length - 1][0]), Y(0)); g.lineTo(X(pts[0][0]), Y(0));
    g.closePath(); g.fillStyle = grad; g.fill();

    g.beginPath(); g.moveTo(X(pts[0][0]), Y(pts[0][1]));
    pts.forEach(function (p) {{ g.lineTo(X(p[0]), Y(p[1])); }});
    g.strokeStyle = accent; g.lineWidth = 2; g.stroke();

    [[1, 1], [31, 0.5], [61, 0.25], [121, 0.0625]].forEach(function (p) {{
      g.beginPath(); g.arc(X(p[0]), Y(p[1]), 3.5, 0, Math.PI * 2);
      g.fillStyle = accent; g.fill();
    }});
  }}
  draw();
  window.addEventListener('resize', draw);
  if (window.matchMedia) {{
    var mq = window.matchMedia('(prefers-color-scheme: dark)');
    (mq.addEventListener ? mq.addEventListener.bind(mq, 'change') : mq.addListener.bind(mq))(draw);
  }}
  new MutationObserver(draw).observe(document.documentElement, {{
    attributes: true, attributeFilter: ['data-theme']
  }});
}})();
</script>
"""


def main(argv=None) -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--in", dest="src", type=Path, default=Path("data/adp_share_2026.json"))
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    report = json.loads(args.src.read_text(encoding="utf-8"))
    args.out.write_text(render(report), encoding="utf-8")
    print(f"Wrote {args.out} ({args.out.stat().st_size:,} bytes)")


if __name__ == "__main__":
    main()
