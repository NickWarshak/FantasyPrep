"""Renders the data-assembled by report.gather_report_data() into a single
self-contained HTML file. Kept separate from report.py so the (fairly
heavy) HTML/SVG generation only loads when actually building the report,
not when other code imports the data-assembly functions.
"""
from __future__ import annotations

import html
import statistics

POSITIONS = ("QB", "RB", "WR", "TE")

# Dataviz skill's validated default categorical order (unmodified hex values).
POSITION_COLOR = {
    "QB": ("#2a78d6", "#3987e5"),  # slot 1 blue
    "RB": ("#eb6834", "#d95926"),  # slot 2 orange
    "WR": ("#1baf7a", "#199e70"),  # slot 3 aqua
    "TE": ("#eda100", "#c98500"),  # slot 4 yellow
}

STATUS = {
    "good": "#0ca30c",
    "warning": "#fab219",
    "serious": "#ec835a",
    "critical": "#d03b3b",
}


def _esc(s) -> str:
    return html.escape(str(s))


def _fmt(n: float, decimals: int = 1) -> str:
    return f"{n:,.{decimals}f}"


# ---------------------------------------------------------------------------
# SVG chart builders
# ---------------------------------------------------------------------------

def _scatter_facet(points: list[tuple[float, float, str]], color_light: str, color_dark: str, axis_max: float) -> str:
    """points: list of (x, y, tooltip_label). Diagonal reference line at x=y."""
    W, H, PAD = 220, 220, 28
    plot = W - PAD * 2

    def sx(v):
        return PAD + (v / axis_max) * plot

    def sy(v):
        return H - PAD - (v / axis_max) * plot

    dots = []
    for x, y, label in points:
        cx, cy = sx(max(0, x)), sy(max(0, y))
        dots.append(f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="2.6" class="scatter-dot"><title>{_esc(label)}</title></circle>')

    diag = f'<line x1="{PAD}" y1="{H-PAD}" x2="{PAD+plot}" y2="{PAD}" class="ref-line"/>'
    ticks = []
    for frac in (0, 0.5, 1.0):
        v = axis_max * frac
        x, y = sx(v), H - PAD
        ticks.append(f'<text x="{x:.0f}" y="{y+14:.0f}" class="axis-tick" text-anchor="middle">{v:.0f}</text>')
        yv = sy(v)
        ticks.append(f'<text x="{PAD-6}" y="{yv+3:.0f}" class="axis-tick" text-anchor="end">{v:.0f}</text>')

    return (
        f'<svg viewBox="0 0 {W} {H}" class="viz-root" style="--series:{color_light};--series-dark:{color_dark}" '
        f'xmlns="http://www.w3.org/2000/svg">'
        f'<line x1="{PAD}" y1="{H-PAD}" x2="{W-PAD//2}" y2="{H-PAD}" class="axis-line"/>'
        f'<line x1="{PAD}" y1="{H-PAD}" x2="{PAD}" y2="{PAD//2}" class="axis-line"/>'
        f'{diag}{"".join(ticks)}{"".join(dots)}</svg>'
    )


def _band_chart(buckets: list[dict], color_light: str, color_dark: str, y_max: float) -> str:
    """Draft rank (x) vs point outcome (y): P25-P75 band, median line, sample dots."""
    W, H, PAD_L, PAD_B, PAD_T, PAD_R = 520, 240, 44, 30, 14, 14
    plot_w = W - PAD_L - PAD_R
    plot_h = H - PAD_T - PAD_B
    if not buckets:
        return '<p class="chart-empty">No data.</p>'
    x_max = buckets[-1]["rank_end"]

    def sx(rank):
        return PAD_L + (rank / x_max) * plot_w

    def sy(v):
        return PAD_T + plot_h - (max(0, v) / y_max) * plot_h

    band_path_top = []
    band_path_bottom = []
    median_pts = []
    dots = []
    for b in buckets:
        mid = (b["rank_start"] + b["rank_end"]) / 2
        x = sx(mid)
        band_path_top.append((x, sy(b["p75"])))
        band_path_bottom.append((x, sy(b["p25"])))
        median_pts.append((x, sy(b["median"])))
        for v in b["outcomes"]:
            dots.append(f'<circle cx="{x:.1f}" cy="{sy(v):.1f}" r="1.8" class="sample-dot"/>')

    band_d = "M " + " L ".join(f"{x:.1f},{y:.1f}" for x, y in band_path_top)
    band_d += " L " + " L ".join(f"{x:.1f},{y:.1f}" for x, y in reversed(band_path_bottom)) + " Z"
    median_d = "M " + " L ".join(f"{x:.1f},{y:.1f}" for x, y in median_pts)

    y_ticks = []
    for frac in (0, 0.25, 0.5, 0.75, 1.0):
        v = y_max * frac
        yv = sy(v)
        y_ticks.append(f'<line x1="{PAD_L}" y1="{yv:.0f}" x2="{W-PAD_R}" y2="{yv:.0f}" class="grid-line"/>')
        y_ticks.append(f'<text x="{PAD_L-8}" y="{yv+3:.0f}" class="axis-tick" text-anchor="end">{v:.0f}</text>')

    x_ticks = []
    for b in buckets:
        if b["bucket"] % 3 == 0:
            mid = (b["rank_start"] + b["rank_end"]) / 2
            x_ticks.append(f'<text x="{sx(mid):.0f}" y="{H-PAD_B+16}" class="axis-tick" text-anchor="middle">{b["rank_start"]}</text>')

    return (
        f'<svg viewBox="0 0 {W} {H}" class="viz-root" style="--series:{color_light};--series-dark:{color_dark}" '
        f'xmlns="http://www.w3.org/2000/svg">'
        f'{"".join(y_ticks)}'
        f'<path d="{band_d}" class="band-fill"/>'
        f'{"".join(dots)}'
        f'<path d="{median_d}" class="median-line" fill="none"/>'
        f'<line x1="{PAD_L}" y1="{H-PAD_B}" x2="{W-PAD_R}" y2="{H-PAD_B}" class="axis-line"/>'
        f'{"".join(x_ticks)}'
        f'<text x="{W/2:.0f}" y="{H-2}" class="axis-label" text-anchor="middle">Draft rank at position</text>'
        f'</svg>'
    )


def _bar_chart(buckets: list[dict], color_light: str, color_dark: str) -> str:
    W, H, PAD_L, PAD_B, PAD_T, PAD_R = 520, 130, 44, 26, 10, 14
    plot_w = W - PAD_L - PAD_R
    plot_h = H - PAD_T - PAD_B
    if not buckets:
        return '<p class="chart-empty">No data.</p>'
    n = len(buckets)
    y_max = max(b["n"] for b in buckets) or 1
    bar_w = plot_w / n * 0.7
    gap = plot_w / n

    bars = []
    for i, b in enumerate(buckets):
        x = PAD_L + i * gap + (gap - bar_w) / 2
        bar_h = (b["n"] / y_max) * plot_h
        y = PAD_T + plot_h - bar_h
        bars.append(
            f'<rect x="{x:.1f}" y="{y:.1f}" width="{bar_w:.1f}" height="{bar_h:.1f}" rx="2" class="bar">'
            f'<title>Rank {b["rank_start"]}-{b["rank_end"]}: {b["n"]} samples</title></rect>'
        )

    return (
        f'<svg viewBox="0 0 {W} {H}" class="viz-root" style="--series:{color_light};--series-dark:{color_dark}" '
        f'xmlns="http://www.w3.org/2000/svg">'
        f'<line x1="{PAD_L}" y1="{PAD_T+plot_h:.0f}" x2="{W-PAD_R}" y2="{PAD_T+plot_h:.0f}" class="axis-line"/>'
        f'<text x="{PAD_L-8}" y="{PAD_T+4}" class="axis-tick" text-anchor="end">{y_max}</text>'
        f'<text x="{PAD_L-8}" y="{PAD_T+plot_h+3:.0f}" class="axis-tick" text-anchor="end">0</text>'
        f'{"".join(bars)}</svg>'
    )


# ---------------------------------------------------------------------------
# Section builders
# ---------------------------------------------------------------------------

def _stat_tile(label: str, value: str, status: str | None = None) -> str:
    cls = f"stat-tile status-{status}" if status else "stat-tile"
    return f'<div class="{cls}"><div class="stat-value">{value}</div><div class="stat-label">{_esc(label)}</div></div>'


def _actuals_section(data: dict) -> str:
    s = data["actuals_summary"]
    rows = data["actuals_rows"]
    by_pos = {pos: [] for pos in POSITIONS}
    for r in rows:
        if r.position in by_pos:
            by_pos[r.position].append((r.nflverse_points, r.our_points, f"{r.name} ({r.year}): {r.our_points} vs {r.nflverse_points}"))

    all_vals = [v for r in rows for v in (r.our_points, r.nflverse_points)]
    axis_max = max(all_vals) * 1.05 if all_vals else 100

    facets = []
    for pos in POSITIONS:
        cl, cd = POSITION_COLOR[pos]
        facets.append(
            f'<div class="facet"><div class="facet-title" style="--series:{cl};--series-dark:{cd}">{pos}</div>'
            f'{_scatter_facet(by_pos[pos], cl, cd, axis_max)}</div>'
        )

    outliers = [r for r in rows if r.is_outlier]
    outlier_rows = "".join(
        f'<tr><td>{_esc(r.name)}</td><td>{r.position}</td><td>{r.year}</td>'
        f'<td class="num">{r.our_points}</td><td class="num">{r.nflverse_points}</td>'
        f'<td class="num delta-{"critical" if abs(r.delta)>5 else "warning"}">{r.delta:+.2f}</td></tr>'
        for r in sorted(outliers, key=lambda r: -abs(r.delta))
    )
    outlier_table = (
        f'<table class="data-table"><thead><tr><th>Player</th><th>Pos</th><th>Year</th>'
        f'<th>Ours</th><th>nflverse</th><th>Delta</th></tr></thead><tbody>{outlier_rows}</tbody></table>'
        if outliers else '<p class="all-clear">No outliers beyond ±2.0 pts across 4,056 player-seasons.</p>'
    )

    return f'''
    <section class="report-section">
      <h2>Historical Actuals Cross-Check</h2>
      <p class="section-lede">Our computed fantasy points ({data["historical_years"][0]}-{data["historical_years"][-1]}) against
      nflverse's own <code>fantasy_points_ppr</code> column, under matching full-PPR settings. A near-exact match is expected;
      this is what caught the missing special-teams-TD credit.</p>
      <div class="tile-row">
        {_stat_tile("player-seasons checked", f'{s["n"]:,}')}
        {_stat_tile("mean abs. delta", f'{s["mean_abs_delta"]:.3f} pts', "good")}
        {_stat_tile("median abs. delta", f'{s["median_abs_delta"]:.3f} pts', "good")}
        {_stat_tile("max abs. delta", f'{s["max_abs_delta"]:.2f} pts', "good" if s["max_abs_delta"] < 2 else "warning")}
      </div>
      <div class="facet-grid">{"".join(facets)}</div>
      <p class="chart-caption">Each dot is one player-season: x = nflverse's total, y = ours. Diagonal = perfect agreement.</p>
      <h3>Outliers (&gt;2.0 pt delta)</h3>
      {outlier_table}
    </section>'''


def _espn_section(data: dict) -> str:
    s = data["espn_summary"]
    rows = data["espn_rows"]
    anomalies = data["espn_anomalies"]

    by_pos = {pos: [] for pos in POSITIONS}
    for r in rows:
        best = getattr(r, r.best_fit_label)
        if r.position in by_pos:
            by_pos[r.position].append((r.applied_total, best, f"{r.name}: {best:.1f} vs {r.applied_total:.1f} ({r.best_fit_label})"))

    all_vals = [v for r in rows for v in (r.applied_total, getattr(r, r.best_fit_label))]
    axis_max = max(all_vals) * 1.05 if all_vals else 100

    facets = []
    for pos in POSITIONS:
        cl, cd = POSITION_COLOR[pos]
        facets.append(
            f'<div class="facet"><div class="facet-title" style="--series:{cl};--series-dark:{cd}">{pos}</div>'
            f'{_scatter_facet(by_pos[pos], cl, cd, axis_max)}</div>'
        )

    outliers = [r for r in rows if r.is_outlier]
    outlier_rows = "".join(
        f'<tr><td>{_esc(r.name)}</td><td>{r.position}</td>'
        f'<td class="num">{getattr(r, r.best_fit_label):.1f}</td><td class="num">{r.applied_total:.1f}</td>'
        f'<td>{r.best_fit_label.replace("_", " ")}</td>'
        f'<td class="num delta-critical">{r.best_fit_delta:+.1f}</td></tr>'
        for r in sorted(outliers, key=lambda r: -abs(r.best_fit_delta))
    )
    outlier_table = (
        f'<table class="data-table"><thead><tr><th>Player</th><th>Pos</th><th>Ours (best fit)</th>'
        f'<th>ESPN applied</th><th>Best-fit format</th><th>Delta</th></tr></thead><tbody>{outlier_rows}</tbody></table>'
        if outliers else '<p class="all-clear">No outliers beyond the threshold.</p>'
    )

    anomaly_rows = "".join(
        f'<tr><td>{_esc(a.name)}</td><td>{a.position}</td>'
        f'<td class="num">{a.applied_average:.1f}</td><td class="num">{a.applied_total:.1f}</td></tr>'
        for a in anomalies
    )
    anomaly_table = (
        f'<table class="data-table"><thead><tr><th>Player</th><th>Pos</th>'
        f'<th>ESPN applied avg/game</th><th>ESPN applied total</th></tr></thead><tbody>{anomaly_rows}</tbody></table>'
        if anomalies else '<p class="all-clear">None excluded.</p>'
    )

    fit_counts = {}
    for r in rows:
        fit_counts[r.best_fit_label] = fit_counts.get(r.best_fit_label, 0) + 1
    fit_summary = ", ".join(f'{v} {k.replace("_"," ")}' for k, v in sorted(fit_counts.items(), key=lambda kv: -kv[1]))

    return f'''
    <section class="report-section">
      <h2>ESPN Projection Cross-Check</h2>
      <p class="section-lede">Our extracted {data["current_year"]} season projections against ESPN's own
      <code>appliedTotal</code> field, compared under three scoring variants (full/half/standard PPR) since ESPN's
      default view doesn't match our league's settings exactly &mdash; the best-fitting variant is used.
      Best fit across all checked players: {fit_summary}.</p>
      <div class="tile-row">
        {_stat_tile("players checked", str(s["n"]))}
        {_stat_tile("real outliers", str(s["n_outliers"]), "good" if s["n_outliers"] == 0 else "warning")}
        {_stat_tile("ESPN data anomalies excluded", str(s["n_anomalies"]), "serious")}
      </div>
      <div class="facet-grid">{"".join(facets)}</div>
      <p class="chart-caption">x = ESPN's applied total, y = ours under the best-fitting scoring variant.</p>
      <h3>Outliers</h3>
      {outlier_table}
      <h3>Excluded: ESPN data anomalies</h3>
      <p class="section-lede">These players' own <code>appliedAverage</code> from ESPN implies an impossible per-game
      score (all but one are QBs &mdash; a pattern worth noting, not something on our end to fix). Set aside rather
      than compared.</p>
      {anomaly_table}
    </section>'''


def _outcomes_section(data: dict) -> str:
    bucket_data = data["bucket_data"]
    y_max = max(b["p75"] for pos in POSITIONS for b in bucket_data[pos]) * 1.15

    band_charts = []
    bar_charts = []
    for pos in POSITIONS:
        cl, cd = POSITION_COLOR[pos]
        buckets = bucket_data[pos]
        n_total = sum(b["n"] for b in buckets)
        band_charts.append(
            f'<div class="facet-wide"><div class="facet-title" style="--series:{cl};--series-dark:{cd}">'
            f'{pos} <span class="facet-sub">({n_total} player-seasons across {len(buckets)} rank tiers)</span></div>'
            f'{_band_chart(buckets, cl, cd, y_max)}</div>'
        )
        bar_charts.append(
            f'<div class="facet-wide"><div class="facet-title" style="--series:{cl};--series-dark:{cd}">{pos}</div>'
            f'{_bar_chart(buckets, cl, cd)}</div>'
        )

    return f'''
    <section class="report-section">
      <h2>Outcome Range by Draft Position</h2>
      <p class="section-lede">What actually happened, historically, to players taken at each position rank
      ({data["historical_years"][0]}-{data["historical_years"][-1]}). Band = 25th-75th percentile, line = median,
      dots = every individual player-season &mdash; thin bands mean thin data, not certainty.</p>
      {"".join(band_charts)}
      <h3>Sample depth per rank tier</h3>
      <p class="section-lede">How many real player-seasons back each tier above. This is the honest denominator
      behind every band and dot.</p>
      <div class="bar-grid">{"".join(bar_charts)}</div>
    </section>'''


def render_report(data: dict) -> str:
    css = _CSS
    body = f'''
    <header class="report-header">
      <p class="eyebrow">Data Integrity Review &middot; {data["current_year"]}</p>
      <h1>Is the data right?</h1>
      <p class="report-intro">Before touching the outcome model, the historical-year range, or recency weighting &mdash;
      does the data these decisions rest on actually hold up. Two independent cross-checks, and the real outcome
      distributions underneath the simulator, laid out to look at rather than trust.</p>
    </header>
    {_actuals_section(data)}
    {_espn_section(data)}
    {_outcomes_section(data)}
    <footer class="report-footer">
      <p>Generated from {data["historical_years"][0]}-{data["historical_years"][-1]} nfl_data_py seasonal data and
      live {data["current_year"]} ESPN projections. Regenerate after any change to scoring rules, historical year
      range, or bucket design.</p>
    </footer>'''
    return f'''<!doctype html>
<title>Data Integrity Review</title>
<style>{css}</style>
{body}'''


_CSS = '''
:root {
  color-scheme: light;
  --page: #F5F6F8;
  --surface: #FFFFFF;
  --ink: #161A22;
  --ink-2: #4B5566;
  --ink-3: #8993A4;
  --border: rgba(22,26,34,0.10);
  --grid: #E4E7EC;
  --accent: #B7791F;
  --good: #0ca30c;
  --good-bg: rgba(12,163,12,0.10);
  --warning: #b8790a;
  --warning-bg: rgba(250,178,25,0.16);
  --serious: #c1552f;
  --serious-bg: rgba(236,131,90,0.14);
  --critical: #d03b3b;
  --critical-bg: rgba(208,59,59,0.10);
}
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) {
    color-scheme: dark;
    --page: #0E1116;
    --surface: #161B22;
    --ink: #F2F4F7;
    --ink-2: #B7C0CC;
    --ink-3: #7C8797;
    --border: rgba(255,255,255,0.10);
    --grid: #2A3038;
    --accent: #E0A23D;
    --good: #0ca30c;
    --good-bg: rgba(12,163,12,0.16);
    --warning: #fab219;
    --warning-bg: rgba(250,178,25,0.14);
    --serious: #ec835a;
    --serious-bg: rgba(236,131,90,0.16);
    --critical: #e66767;
    --critical-bg: rgba(230,103,103,0.14);
  }
}
:root[data-theme="dark"] {
  color-scheme: dark;
  --page: #0E1116;
  --surface: #161B22;
  --ink: #F2F4F7;
  --ink-2: #B7C0CC;
  --ink-3: #7C8797;
  --border: rgba(255,255,255,0.10);
  --grid: #2A3038;
  --accent: #E0A23D;
  --good: #0ca30c;
  --good-bg: rgba(12,163,12,0.16);
  --warning: #fab219;
  --warning-bg: rgba(250,178,25,0.14);
  --serious: #ec835a;
  --serious-bg: rgba(236,131,90,0.16);
  --critical: #e66767;
  --critical-bg: rgba(230,103,103,0.14);
}

* { box-sizing: border-box; }
body {
  background: var(--page);
  color: var(--ink);
  font-family: system-ui, -apple-system, "Segoe UI", sans-serif;
  margin: 0;
  padding: 2.5rem 1.25rem 5rem;
}
.report-header, .report-section, .report-footer {
  max-width: 980px;
  margin: 0 auto;
}
h1, h2, h3 {
  font-family: Georgia, "Iowan Old Style", "Times New Roman", serif;
  text-wrap: balance;
  font-weight: 600;
  color: var(--ink);
}
h1 { font-size: 2.1rem; margin: 0.2rem 0 0.9rem; }
h2 { font-size: 1.4rem; margin: 0 0 0.5rem; border-top: 1px solid var(--border); padding-top: 2rem; }
h3 { font-size: 1.05rem; margin: 1.8rem 0 0.6rem; color: var(--ink-2); }
.eyebrow {
  text-transform: uppercase;
  letter-spacing: 0.08em;
  font-size: 0.75rem;
  color: var(--accent);
  font-weight: 600;
  margin: 0;
}
.report-intro, .section-lede {
  color: var(--ink-2);
  max-width: 65ch;
  line-height: 1.55;
}
.report-intro { font-size: 1.02rem; }
.section-lede { font-size: 0.92rem; }
code {
  font-family: "SF Mono", "Cascadia Code", Consolas, "Roboto Mono", monospace;
  background: var(--grid);
  padding: 0.1em 0.35em;
  border-radius: 4px;
  font-size: 0.88em;
}

.report-section { margin-bottom: 1rem; }

.tile-row {
  display: flex;
  gap: 0.75rem;
  flex-wrap: wrap;
  margin: 1rem 0 1.5rem;
}
.stat-tile {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 10px;
  padding: 0.9rem 1.1rem;
  min-width: 150px;
  flex: 1 1 150px;
}
.stat-value {
  font-family: "SF Mono", "Cascadia Code", Consolas, "Roboto Mono", monospace;
  font-variant-numeric: tabular-nums;
  font-size: 1.5rem;
  font-weight: 600;
}
.stat-label {
  color: var(--ink-3);
  font-size: 0.78rem;
  text-transform: uppercase;
  letter-spacing: 0.04em;
  margin-top: 0.2rem;
}
.stat-tile.status-good .stat-value { color: var(--good); }
.stat-tile.status-warning .stat-value { color: var(--warning); }
.stat-tile.status-serious .stat-value { color: var(--serious); }
.stat-tile.status-critical .stat-value { color: var(--critical); }

.facet-grid {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 0.75rem;
  overflow-x: auto;
}
.facet, .facet-wide {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 10px;
  padding: 0.7rem 0.7rem 0.5rem;
}
.facet-wide { margin-bottom: 0.75rem; overflow-x: auto; }
.facet-title {
  font-family: "SF Mono", "Cascadia Code", Consolas, "Roboto Mono", monospace;
  font-weight: 600;
  font-size: 0.82rem;
  color: var(--series);
  margin-bottom: 0.3rem;
}
@media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) .facet-title { color: var(--series-dark); } }
:root[data-theme="dark"] .facet-title { color: var(--series-dark); }
.facet-sub { color: var(--ink-3); font-weight: 400; }

.chart-caption {
  color: var(--ink-3);
  font-size: 0.8rem;
  margin: 0.5rem 0 0;
}
.chart-empty { color: var(--ink-3); font-size: 0.85rem; }

.viz-root { width: 100%; height: auto; display: block; }
.axis-line { stroke: var(--ink-3); stroke-width: 1; }
.axis-tick { font-size: 8px; fill: var(--ink-3); font-family: "SF Mono", Consolas, monospace; }
.axis-label { font-size: 9px; fill: var(--ink-3); }
.grid-line { stroke: var(--grid); stroke-width: 1; }
.ref-line { stroke: var(--ink-3); stroke-width: 1; stroke-dasharray: 2 2; opacity: 0.6; }
.scatter-dot { fill: var(--series); opacity: 0.55; }
@media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) .scatter-dot { fill: var(--series-dark); } }
:root[data-theme="dark"] .scatter-dot { fill: var(--series-dark); }
.sample-dot { fill: var(--series); opacity: 0.35; }
@media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) .sample-dot { fill: var(--series-dark); } }
:root[data-theme="dark"] .sample-dot { fill: var(--series-dark); }
.median-line { stroke: var(--series); stroke-width: 2; stroke-linecap: round; }
@media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) .median-line { stroke: var(--series-dark); } }
:root[data-theme="dark"] .median-line { stroke: var(--series-dark); }
.band-fill { fill: var(--series); opacity: 0.16; }
@media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) .band-fill { fill: var(--series-dark); } }
:root[data-theme="dark"] .band-fill { fill: var(--series-dark); }
.bar { fill: var(--series); }
@media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) .bar { fill: var(--series-dark); } }
:root[data-theme="dark"] .bar { fill: var(--series-dark); }

.bar-grid { display: grid; grid-template-columns: repeat(2, 1fr); gap: 0.75rem; }

.data-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 0.85rem;
  margin: 0.5rem 0 1rem;
}
.data-table th, .data-table td {
  text-align: left;
  padding: 0.45rem 0.6rem;
  border-bottom: 1px solid var(--border);
}
.data-table th {
  color: var(--ink-3);
  font-weight: 600;
  text-transform: uppercase;
  font-size: 0.72rem;
  letter-spacing: 0.03em;
}
.data-table td.num {
  font-family: "SF Mono", "Cascadia Code", Consolas, "Roboto Mono", monospace;
  font-variant-numeric: tabular-nums;
  text-align: right;
}
.delta-warning { color: var(--warning); }
.delta-critical { color: var(--critical); }

.all-clear {
  background: var(--good-bg);
  color: var(--good);
  border-radius: 8px;
  padding: 0.6rem 0.9rem;
  font-size: 0.88rem;
  display: inline-block;
}

.report-footer {
  margin-top: 2.5rem;
  padding-top: 1.2rem;
  border-top: 1px solid var(--border);
  color: var(--ink-3);
  font-size: 0.8rem;
}

@media (max-width: 800px) {
  .facet-grid { grid-template-columns: repeat(2, 1fr); }
  .bar-grid { grid-template-columns: 1fr; }
}
'''
