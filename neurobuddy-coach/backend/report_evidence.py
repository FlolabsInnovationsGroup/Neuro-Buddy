"""Numbered, citable evidence pulled from a generated Neurobuddy HTML report.

The AI analysis must rest on facts that are actually in the report. This module
reads the report's own tables (the statistics tables, the baseline comparison
table, and the data tables printed under each figure) and turns them into a
short list of numbered facts (E1, E2, ...). Each fact keeps its metric name,
value, unit, and, when the report compares against baseline, the baseline mean,
SD, z-score, status and the direction of change.

It also attaches METRIC_EFFECTS: a hedged, literature-based summary of what a
rise or a drop of each metric family has been associated with, and the
references behind it. The external AI API receives this as report.evidence and
is instructed to cite fact ids for every statement.

Nothing here is computed from raw EEG. It only restates and summarizes numbers
the pipeline already printed, so the AI cannot be handed a value the report
does not contain.
"""
from __future__ import annotations

import math
import re
from collections import defaultdict

EVIDENCE_SCHEMA = 'neurobuddy.report-evidence/0.1'
Z_THRESHOLD = 3.0
MAX_FACTS = 1200
FRONTAL = {'Fz', 'F3', 'F4', 'Fp1', 'Fp2', 'F7', 'F8'}

REFERENCES = {
    'Polich2007': 'Polich J. (2007). Updating P300: an integrative theory of P3a and P3b. Clinical Neurophysiology, 118(10), 2128-2148.',
    'Kok2001': 'Kok A. (2001). On the utility of P3 amplitude as a measure of processing capacity. Psychophysiology, 38(3), 557-577.',
    'Cajochen1995': 'Cajochen C., Brunner D.P., Krauchi K., Graw P., Wirz-Justice A. (1995). Power density in theta/alpha frequencies of the waking EEG progressively increases during sustained wakefulness. Sleep, 18(10), 890-894.',
    'Finelli2000': 'Finelli L.A., Baumann H., Borbely A.A., Achermann P. (2000). Dual electroencephalogram markers of human sleep homeostasis: correlation between theta activity in waking and slow-wave activity in sleep. Neuroscience, 101(3), 523-529.',
    'Craig2012': 'Craig A., Tran Y., Wijesuriya N., Nguyen H. (2012). Regional brain wave activity changes associated with fatigue. Psychophysiology, 49(4), 574-582.',
    'Wascher2014': 'Wascher E., Rasch B., Sanger J., et al. (2014). Frontal theta activity reflects distinct aspects of mental fatigue. Biological Psychology, 96, 57-65.',
    'Klimesch1999': 'Klimesch W. (1999). EEG alpha and theta oscillations reflect cognitive and memory performance: a review and analysis. Brain Research Reviews, 29(2-3), 169-195.',
    'Barry2007': 'Barry R.J., Clarke A.R., Johnstone S.J., Magee C.A., Rushby J.A. (2007). EEG differences between eyes-closed and eyes-open resting conditions. Clinical Neurophysiology, 118(12), 2765-2773.',
    'AlShargie2016': 'Al-Shargie F., Kiguchi M., Badruddin N., et al. (2016). Mental stress assessment using simultaneous measurement of EEG and fNIRS. Biomedical Optics Express, 7(10), 3882-3898.',
}

# What the athlete-level areas are, in the order the analysis reports them.
AREAS = [('recovery', 'Recovery'), ('fatigue', 'Fatigue'), ('sleep', 'Sleep'),
         ('stress', 'Stress / arousal'), ('attention', 'Attention / alertness')]

# For each metric family: what a rise or a drop MAY be related to, and which direction lines up
# with each area. These are group-level research findings, not signs for an individual.
METRIC_EFFECTS = {
    'theta': {
        'label': 'Theta power (4-8 Hz)',
        'up': 'Higher theta, especially frontal, may be related to mental fatigue, longer time awake or sleepiness.',
        'down': 'Lower theta may be related to higher alertness.',
        'consistent_with': {'fatigue': 'up', 'sleep': 'up', 'recovery': 'up', 'attention': 'up'},
        'refs': ['Cajochen1995', 'Finelli2000', 'Wascher2014', 'Craig2012'],
    },
    'alpha': {
        'label': 'Alpha power (8-13 Hz)',
        'up': 'Higher alpha may be related to fatigue, relaxation or lower arousal.',
        'down': 'Lower alpha may be related to higher arousal, focused attention or stress.',
        'consistent_with': {'fatigue': 'up', 'recovery': 'up', 'stress': 'down'},
        'refs': ['Craig2012', 'Barry2007', 'AlShargie2016'],
    },
    'beta': {
        'label': 'Beta power (13-30 Hz)',
        'up': 'Higher beta may be related to arousal, mental stress, effort or muscle tension.',
        'down': 'Lower beta may be related to drowsiness or reduced vigilance.',
        'consistent_with': {'stress': 'up', 'sleep': 'down', 'attention': 'down'},
        'refs': ['AlShargie2016', 'Craig2012'],
    },
    'delta': {
        'label': 'Delta power (1-4 Hz) while awake',
        'up': 'Higher waking delta may be related to drowsiness or sleep pressure, or to eye and movement activity.',
        'down': 'Lower waking delta may be related to higher alertness.',
        'consistent_with': {'sleep': 'up'},
        'refs': ['Cajochen1995'],
    },
    'peak_alpha_frequency': {
        'label': 'Peak alpha frequency',
        'up': 'A faster alpha peak may be related to better cognitive readiness.',
        'down': 'A slower alpha peak may be related to fatigue, sleepiness or lower cognitive performance.',
        'consistent_with': {'fatigue': 'down', 'sleep': 'down', 'recovery': 'down', 'attention': 'down'},
        'refs': ['Klimesch1999'],
    },
    'alpha_theta_ratio': {
        'label': 'Alpha/theta ratio',
        'up': 'A higher alpha/theta ratio may be related to a more alert, less theta-dominant state.',
        'down': 'A lower alpha/theta ratio (relatively more theta) may be related to sleepiness or fatigue.',
        'consistent_with': {'fatigue': 'down', 'sleep': 'down', 'recovery': 'down'},
        'refs': ['Klimesch1999', 'Cajochen1995'],
    },
    'beta_alpha_ratio': {
        'label': 'Beta/alpha ratio',
        'up': 'A higher beta/alpha ratio may be related to higher arousal or stress.',
        'down': 'A lower beta/alpha ratio may be related to lower arousal, relaxation or fatigue.',
        'consistent_with': {'stress': 'up', 'fatigue': 'down', 'recovery': 'down'},
        'refs': ['AlShargie2016', 'Craig2012'],
    },
    'beta_theta_ratio': {
        'label': 'Beta/theta ratio',
        'up': 'A higher beta/theta ratio may be related to higher arousal.',
        'down': 'A lower beta/theta ratio (relatively more theta) may be related to reduced vigilance or fatigue.',
        'consistent_with': {'stress': 'up', 'fatigue': 'down', 'sleep': 'down', 'attention': 'down'},
        'refs': ['Craig2012', 'Cajochen1995'],
    },
    'p300_amplitude': {
        'label': 'P300 amplitude (target minus standard)',
        'up': 'A larger P300 may be related to more attention given to the target.',
        'down': 'A smaller P300 may be related to fatigue, sleep loss or reduced attention.',
        'consistent_with': {'fatigue': 'down', 'sleep': 'down', 'recovery': 'down', 'attention': 'down'},
        'refs': ['Polich2007', 'Kok2001'],
    },
    'p300_latency': {
        'label': 'P300 peak latency',
        'up': 'A later P300 may be related to slower processing, as seen with fatigue or sleep loss.',
        'down': 'An earlier P300 may be related to faster processing.',
        'consistent_with': {'fatigue': 'up', 'sleep': 'up', 'recovery': 'up', 'attention': 'up'},
        'refs': ['Polich2007'],
    },
    'signal': {
        'label': 'Amplitude and waveform measures (RMS, variance, kurtosis, peak-to-peak and similar)',
        'up': 'Changes may be related to recording conditions such as electrode contact, movement or muscle tension.',
        'down': 'Changes may be related to recording conditions such as electrode contact, movement or muscle tension.',
        'consistent_with': {},
        'refs': [],
    },
}


def family(metric: str) -> str | None:
    """Map a report metric name to a METRIC_EFFECTS family, or None if it is not interpreted."""
    m = metric.lower().replace(' ', '_')
    if 'p300' in m:
        if 'latency' in m:
            return 'p300_latency'
        if 'uv' in m or 'µv' in m or 'amplitude' in m:
            return 'p300_amplitude'
        return None
    if 'theta_delta_ratio' in m:
        return None
    for key in ('alpha_theta_ratio', 'beta_alpha_ratio', 'beta_theta_ratio', 'peak_alpha_frequency'):
        if key in m:
            return key
    for band in ('theta', 'alpha', 'beta', 'delta'):
        if re.search(rf'(^|[._]){band}_(relative|power|relative_pct|absolute)', m):
            return band
    if re.search(r'(^|[._])(rms|variance|standard_deviation|peak_to_peak|kurtosis|skewness|crest_factor|mad|max|min|mean|median)$', m):
        return 'signal'
    return None


def _num(text):
    try:
        value = float(str(text).strip().replace(',', ''))
    except ValueError:
        return None
    return value if math.isfinite(value) else None


def _pct(test, mean):
    if test is None or mean is None or mean == 0:
        return None
    return round((test - mean) / abs(mean) * 100, 2)


def _norm(name: str) -> str:
    last = re.split(r'[/.]', name.lower().replace('µ', 'u'))[-1]
    return re.sub(r'[^a-z0-9]', '', last)


def _cells(line: str) -> list[str]:
    return [c.strip() for c in line.split('|')[1:]]


def parse_tables(text: str) -> list[dict]:
    """Tables in extract_report() text, with the heading and figure they sit under."""
    tables, section, figure, current = [], '', None, None
    for line in text.split('\n'):
        if line.startswith('|'):
            if current is None:
                current = {'section': section, 'figure': figure, 'header': _cells(line), 'rows': []}
                tables.append(current)
            else:
                current['rows'].append(_cells(line))
            continue
        if not line.strip():
            continue  # whitespace between <tr> elements does not end a table
        current = None
        heading = re.match(r'(#+) (.*)', line)
        if heading:
            section = heading.group(2).strip()
            if len(heading.group(1)) <= 2:
                figure = None
        fig = re.match(r'\[Figure (\d+): (.*)\]$', line)
        if fig:
            figure = {'index': int(fig.group(1)), 'caption': fig.group(2)}
    return tables


def _recording(text: str) -> dict:
    lines = [l.strip() for l in text.split('\n')]
    wanted = {'File': 'file', 'Selected pipeline': 'pipeline', 'Status': 'status', 'Duration': 'duration'}
    out = {}
    for i, line in enumerate(lines[:-1]):
        if line in wanted and wanted[line] not in out:
            nxt = next((l for l in lines[i + 1:i + 3] if l), '')
            if nxt and len(nxt) < 120:
                out[wanted[line]] = nxt
    return out


def _source(table: dict) -> str:
    fig = table.get('figure')
    if fig:
        return f'Figure {fig["index"]}: {fig["caption"]}'
    return table.get('section') or 'Report table'


class _Facts:
    def __init__(self):
        self.items: list[dict] = []
        self.dropped = 0

    def add(self, **fact) -> str | None:
        if len(self.items) >= MAX_FACTS:
            self.dropped += 1
            return None
        fact = {k: v for k, v in fact.items() if v is not None}
        fact['id'] = f'E{len(self.items) + 1}'
        self.items.append(fact)
        return fact['id']


def _comparison(table: dict, facts: _Facts, out: dict, baseline_values: dict | None = None) -> None:
    """Every metric in a baseline vs testing table, split into outside and within +/-3 SD."""
    head = [h.lower().replace('_', ' ') for h in table['header']]
    col = {name: head.index(name) for name in head}

    def get(row, name):
        i = col.get(name)
        return row[i] if i is not None and i < len(row) else ''

    rows, groups = [], defaultdict(list)
    for row in table['rows']:
        metric = get(row, 'metric')
        if not metric:
            continue
        mean, test, z = _num(get(row, 'baseline mean')), _num(get(row, 'test value')), _num(get(row, 'z score'))
        direction = None
        if mean is not None and test is not None:
            direction = 'up' if test > mean else 'down' if test < mean else 'unchanged'
        item = {'metric': metric, 'unit': get(row, 'unit') or None, 'baseline_mean': mean,
                'baseline_sd': _num(get(row, 'baseline sd')), 'test_value': test, 'z': z,
                'status': get(row, 'status') or '', 'direction': direction,
                'baseline_n': _num(get(row, 'baseline n')), 'family': family(metric)}
        rows.append(item)
        channel = re.match(r'(.*?\.channel\.)([^.]+)\.(.+)$', metric)
        if channel and z is not None:
            groups[channel.group(1) + '*.' + channel.group(3)].append((channel.group(2), item))

    signature = tuple((r['metric'], r['test_value'], r['z']) for r in rows)
    if signature in out['seen']:
        return
    out['seen'].add(signature)
    outside = [r for r in rows if r['z'] is not None and abs(r['z']) > Z_THRESHOLD]
    z_scored = sum(r['z'] is not None for r in rows)
    n_values = {int(r['baseline_n']) for r in rows if r['baseline_n']}
    out['comparisons'].append({
        'source': _source(table), 'metrics_compared': len(rows), 'z_scored': z_scored,
        'not_comparable': len(rows) - z_scored,
        'above_3sd': sum(r['z'] > Z_THRESHOLD for r in outside),
        'below_3sd': sum(r['z'] < -Z_THRESHOLD for r in outside),
        'within_3sd': z_scored - len(outside),
        'outside_interpreted': sum(r['family'] in METRIC_EFFECTS and r['family'] != 'signal' for r in outside),
        'baseline_n': sorted(n_values),
    })

    def add(kind, r):
        position = None
        if r['z'] is not None:
            position = 'above +3 SD' if r['z'] > Z_THRESHOLD else 'below -3 SD' if r['z'] < -Z_THRESHOLD else 'within 3 SD'
        facts.add(kind=kind, source=_source(table), metric=r['metric'], family=r['family'], unit=r['unit'],
                  baseline_mean=r['baseline_mean'], baseline_sd=r['baseline_sd'],
                  baseline_values=(baseline_values or {}).get(r['metric']), value=r['test_value'], z=r['z'],
                  pct_change=_pct(r['test_value'], r['baseline_mean']), position=position,
                  direction=r['direction'], baseline_n=r['baseline_n'])

    by_z = sorted(outside, key=lambda r: -abs(r['z']))
    interpreted = lambda r: r['family'] in METRIC_EFFECTS and r['family'] != 'signal'
    # 1. outside +/-3 SD: EEG band, ratio and P300 metrics first, then every other metric
    for r in by_z:
        if interpreted(r):
            add('outside_3sd', r)
    for r in by_z:
        if not interpreted(r):
            add('outside_3sd_other', r)
    # 2. P300 amplitude and latency inside +/-3 SD are listed one by one
    for r in rows:
        if r['family'] in ('p300_amplitude', 'p300_latency') and r not in outside and r['z'] is not None:
            add('within_3sd', r)
    # 3. each channel metric across the head: how many channels are within, above or below
    for key, members in sorted(groups.items(), key=lambda kv: (not interpreted(kv[1][0][1]), kv[0])):
        zs = [m['z'] for _, m in members]
        if len(zs) < 2:
            continue
        ordered = sorted(zs)
        mid = len(ordered) // 2
        median_z = ordered[mid] if len(ordered) % 2 else (ordered[mid - 1] + ordered[mid]) / 2
        inside = [z for z in zs if abs(z) <= Z_THRESHOLD]
        frontal = [m['z'] for ch, m in members if ch in FRONTAL]
        facts.add(kind='channel_summary', source=_source(table), metric=key, family=members[0][1]['family'],
                  channels=len(zs), channels_within=len(inside),
                  channels_above=sum(z > Z_THRESHOLD for z in zs), channels_below=sum(z < -Z_THRESHOLD for z in zs),
                  channels_up=sum(z > 0 for z in zs), channels_down=sum(z < 0 for z in zs),
                  median_z=round(median_z, 2), within_z_range=f'{min(inside):.2f} to {max(inside):.2f}' if inside else None,
                  direction='up' if median_z > 0 else 'down' if median_z < 0 else 'unchanged',
                  frontal_median_z=round(sorted(frontal)[len(frontal) // 2], 2) if frontal else None)


def _statistics(table: dict, facts: _Facts, seen: set) -> None:
    for row in table['rows']:
        if len(row) < 2 or not row[0]:
            continue
        name, raw = row[0], row[1]
        value = _num(raw)
        key = (_norm(name), str(value if value is not None else raw))
        if key in seen:
            continue
        seen.add(key)
        fam = family(name)
        keep = fam in ('p300_amplitude', 'p300_latency') or re.search(
            r'(cohen|p uncorrected|^target$|^standard$|epochs|/ t$|n200|electrode|latency window|sleep|efficien|latency|stage|wake|n3|rem|accuracy|reaction)',
            name.lower())
        if not keep:
            continue
        facts.add(kind='statistic', source=_source(table), metric=name, family=fam,
                  value=value if value is not None else raw, unit=(row[2] if len(row) > 2 and row[2] else None))


def _band_power(table: dict, facts: _Facts) -> None:
    head = table['header']
    rows = [r for r in table['rows'] if len(r) == len(head)]
    if not rows:
        return
    for band in ('delta', 'theta', 'alpha', 'beta'):
        name = f'{band}_relative_pct'
        if name not in head:
            continue
        i = head.index(name)
        values = [(r[0], _num(r[i])) for r in rows if _num(r[i]) is not None]
        if not values:
            continue
        frontal = [v for ch, v in values if ch in FRONTAL]
        facts.add(kind='figure_data', source=_source(table), metric=f'{band} relative power (% of 1-30 Hz)', family=band,
                  unit='%', value=round(sum(v for _, v in values) / len(values), 2), channels=len(values),
                  frontal_mean=round(sum(frontal) / len(frontal), 2) if frontal else None,
                  range=f'{min(v for _, v in values):.2f} to {max(v for _, v in values):.2f}')


def _erp(table: dict, facts: _Facts) -> None:
    head = table['header']
    if 'target_minus_standard_uv' not in head:
        return
    li, di = head.index('latency_ms'), head.index('target_minus_standard_uv')
    window = [(_num(r[li]), _num(r[di])) for r in table['rows'] if len(r) > max(li, di)]
    window = [(t, v) for t, v in window if t is not None and v is not None and 250 <= t <= 500]
    if not window:
        return
    t, v = max(window, key=lambda tv: tv[1])
    facts.add(kind='figure_data', source=_source(table), metric='P300 difference-wave peak in 250-500 ms (read from plotted data)',
              family='p300_amplitude', unit='µV', value=round(v, 2), latency_ms=t)


def _quality(table: dict, facts: _Facts) -> None:
    head = table['header']
    if 'quality_grade' in head and 'quality_score_0_bad_3_good' in head:
        i = head.index('quality_grade')
        grades = defaultdict(int)
        for r in table['rows']:
            if len(r) > i:
                grades[r[i]] += 1
        facts.add(kind='data_quality', source=_source(table), metric='channel quality grades',
                  value=', '.join(f'{k}: {v}' for k, v in sorted(grades.items())), family=None)
    if 'review_recommended' in head and 'flagged_fraction' in head:
        ri, fi = head.index('review_recommended'), head.index('flagged_fraction')
        review = [r[0] for r in table['rows'] if len(r) > ri and r[ri].lower() == 'true']
        worst = max((_num(r[fi]) or 0 for r in table['rows'] if len(r) > fi), default=0)
        facts.add(kind='data_quality', source=_source(table), metric='artifact screening',
                  value=f'{len(review)} of {len(table["rows"])} channels recommended for review; largest flagged fraction {worst:.3f}',
                  family=None)


def build_evidence(text: str = '', figures: list[dict] | None = None, tables: list[dict] | None = None,
                   baseline_values: dict | None = None) -> dict:
    """Evidence from report text (HTML reports) and/or ready-made tables (structured job files)."""
    facts = _Facts()
    out = {'comparisons': [], 'seen': set()}
    seen: set = set()
    tables = list(tables or []) + parse_tables(text)
    for table in tables:
        head = [h.lower().replace('_', ' ') for h in table['header']]
        if 'z score' in head and 'metric' in head:
            _comparison(table, facts, out, baseline_values)
    for table in tables:
        head = [h.lower() for h in table['header']]
        if head[:2] in (['statistic', 'value'], ['metric', 'value']):
            _statistics(table, facts, seen)
        elif any(h.endswith('_relative_pct') for h in head):
            _band_power(table, facts)
        elif 'target_minus_standard_uv' in head:
            _erp(table, facts)
        elif 'quality_grade' in head or 'review_recommended' in head:
            _quality(table, facts)
    families = sorted({f['family'] for f in facts.items if f.get('family') in METRIC_EFFECTS})
    refs = sorted({r for fam in families for r in METRIC_EFFECTS[fam]['refs']})
    return {
        'schema_version': EVIDENCE_SCHEMA,
        'report_type': 'baseline_comparison' if out['comparisons'] else 'single_recording',
        'recording': _recording(text) if text else {},
        'z_threshold': Z_THRESHOLD,
        'comparisons': out['comparisons'],
        'facts': facts.items,
        'facts_not_included': facts.dropped,
        'figures': [{'index': f['index'], 'caption': f['caption']} for f in (figures or [])],
        'areas': [{'key': k, 'label': v} for k, v in AREAS],
        'metric_effects': {fam: METRIC_EFFECTS[fam] for fam in families},
        'references': {key: REFERENCES[key] for key in refs},
    }


def effects_table_text() -> str:
    """METRIC_EFFECTS as plain text, for the instructions sent to the API."""
    lines = []
    for fam, e in METRIC_EFFECTS.items():
        refs = ', '.join(e['refs']) or 'no specific reference'
        lines.append(f'- {e["label"]}: UP: {e["up"]} DOWN: {e["down"]} ({refs})')
    return '\n'.join(lines)
