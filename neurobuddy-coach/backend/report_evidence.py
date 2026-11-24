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
MAX_FACTS = 160
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
    'Meeusen2013': 'Meeusen R., Duclos M., Foster C., et al. (2013). Prevention, diagnosis, and treatment of the overtraining syndrome: joint consensus statement of the ECSS and the ACSM. Medicine & Science in Sports & Exercise, 45(1), 186-205.',
}

# For each metric family: what a rise or a drop has been associated with, and which
# direction is consistent with each state. These are non-specific group-level
# findings, not markers for an individual.
METRIC_EFFECTS = {
    'theta': {
        'label': 'Theta power (4-8 Hz)',
        'up': 'Higher waking theta, especially frontal, has been associated with time awake, sleepiness and mental fatigue.',
        'down': 'Lower theta has no specific state association; it can follow better alertness or simply recording differences.',
        'consistent_with': {'fatigue': 'up', 'sleepiness': 'up'},
        'refs': ['Cajochen1995', 'Finelli2000', 'Wascher2014', 'Craig2012'],
    },
    'alpha': {
        'label': 'Alpha power (8-13 Hz)',
        'up': 'Higher alpha has been reported with fatigue and lower arousal (eyes-open data); in eyes-closed rest it is the normal relaxed state.',
        'down': 'Lower alpha (alpha blocking) accompanies higher arousal, attention or stress, and also opening the eyes or drowsy alpha drop-out.',
        'consistent_with': {'fatigue': 'up', 'stress': 'down'},
        'refs': ['Craig2012', 'Barry2007', 'AlShargie2016'],
    },
    'beta': {
        'label': 'Beta power (13-30 Hz)',
        'up': 'Higher beta has been associated with arousal, mental stress and effort; muscle tension also raises beta and is a common confound.',
        'down': 'Lower beta has been reported with drowsiness and reduced vigilance.',
        'consistent_with': {'stress': 'up', 'sleepiness': 'down'},
        'refs': ['AlShargie2016', 'Craig2012'],
    },
    'delta': {
        'label': 'Delta power (1-4 Hz) in waking EEG',
        'up': 'Higher waking delta can reflect drowsiness or sleep pressure, but eye movement and movement artifact also land in this band.',
        'down': 'Lower delta has no specific state association.',
        'consistent_with': {'sleepiness': 'up'},
        'refs': ['Cajochen1995'],
    },
    'peak_alpha_frequency': {
        'label': 'Peak alpha frequency',
        'up': 'A faster alpha peak has been linked with better cognitive readiness.',
        'down': 'A slower alpha peak has been associated with fatigue, sleepiness and lower cognitive performance.',
        'consistent_with': {'fatigue': 'down', 'sleepiness': 'down'},
        'refs': ['Klimesch1999'],
    },
    'alpha_theta_ratio': {
        'label': 'Alpha/theta ratio',
        'up': 'More alpha relative to theta points away from the theta-dominant pattern seen with sleepiness.',
        'down': 'A falling alpha/theta ratio (relatively more theta) is consistent with sleepiness or fatigue.',
        'consistent_with': {'fatigue': 'down', 'sleepiness': 'down'},
        'refs': ['Klimesch1999', 'Cajochen1995'],
    },
    'theta_delta_ratio': {
        'label': 'Theta/delta ratio',
        'up': 'No established state association on its own.',
        'down': 'No established state association on its own.',
        'consistent_with': {},
        'refs': [],
    },
    'beta_alpha_ratio': {
        'label': 'Beta/alpha ratio',
        'up': 'A rising beta/alpha ratio is consistent with higher arousal or stress.',
        'down': 'A falling beta/alpha ratio is consistent with lower arousal, relaxation or fatigue.',
        'consistent_with': {'stress': 'up', 'fatigue': 'down'},
        'refs': ['AlShargie2016', 'Craig2012'],
    },
    'beta_theta_ratio': {
        'label': 'Beta/theta ratio',
        'up': 'A rising beta/theta ratio is consistent with higher arousal.',
        'down': 'A falling beta/theta ratio (relatively more theta) is consistent with reduced vigilance or fatigue.',
        'consistent_with': {'stress': 'up', 'fatigue': 'down', 'sleepiness': 'down'},
        'refs': ['Craig2012', 'Cajochen1995'],
    },
    'p300_amplitude': {
        'label': 'P300 amplitude (target minus standard)',
        'up': 'A larger P300 is usually read as more attentional resources allocated to the target.',
        'down': 'A smaller P300 has been associated with fatigue, sleep loss and reduced attention.',
        'consistent_with': {'fatigue': 'down', 'sleepiness': 'down'},
        'refs': ['Polich2007', 'Kok2001'],
    },
    'p300_latency': {
        'label': 'P300 peak latency',
        'up': 'A later P300 has been associated with slower stimulus evaluation, as seen with fatigue or sleep loss.',
        'down': 'An earlier P300 is usually read as faster stimulus evaluation.',
        'consistent_with': {'fatigue': 'up', 'sleepiness': 'up'},
        'refs': ['Polich2007'],
    },
    'signal': {
        'label': 'Amplitude and waveform descriptors (RMS, variance, kurtosis, peak-to-peak and similar)',
        'up': 'Mostly reflect recording conditions (impedance, movement, muscle, gain), not a training state.',
        'down': 'Mostly reflect recording conditions, not a training state.',
        'consistent_with': {},
        'refs': [],
    },
}
OVERTRAINING_NOTE = ('There is no established EEG signature of overtraining. Its diagnosis rests on a sustained performance '
                     'decrement with other causes excluded, so EEG changes can at most be consistent with under-recovery.')
OVERTRAINING_REFS = ['Meeusen2013']


def family(metric: str) -> str | None:
    """Map a report metric name to a METRIC_EFFECTS family, or None if it is not interpreted."""
    m = metric.lower().replace(' ', '_')
    if 'p300' in m:
        if 'latency' in m:
            return 'p300_latency'
        if 'uv' in m or 'µv' in m or 'amplitude' in m:
            return 'p300_amplitude'
        return None
    for key in ('alpha_theta_ratio', 'theta_delta_ratio', 'beta_alpha_ratio', 'beta_theta_ratio', 'peak_alpha_frequency'):
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


def chance_flag_rate(baseline_n: int, threshold: float = Z_THRESHOLD) -> float | None:
    """Share of |z| > threshold expected by chance when z uses the sample SD of n baselines.

    With x independent of the n baseline values, z / sqrt(1 + 1/n) follows Student's t
    with n - 1 degrees of freedom, so small baselines flag far more than the 0.27 %
    a known SD would give.
    """
    if not baseline_n or baseline_n < 2:
        return None
    df = baseline_n - 1
    t = threshold / math.sqrt(1 + 1 / baseline_n)
    const = math.exp(math.lgamma((df + 1) / 2) - math.lgamma(df / 2)) / math.sqrt(df * math.pi)
    steps, upper = 20000, 60.0 + 40 * t
    h = (upper - t) / steps
    pdf = lambda x: const * (1 + x * x / df) ** (-(df + 1) / 2)
    area = pdf(t) + pdf(upper) + sum((4 if i % 2 else 2) * pdf(t + i * h) for i in range(1, steps))
    tail = area * h / 3
    if df <= 2:  # heavy tail beyond the integration limit: P(T > u) for t with 1 or 2 df
        tail += (0.5 - math.atan(upper) / math.pi) if df == 1 else 0.5 * (1 - upper / math.sqrt(upper * upper + 2))
    return round(min(1.0, 2 * tail), 4)


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


def _comparison(table: dict, facts: _Facts, out: dict) -> None:
    head = [h.lower() for h in table['header']]
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
        status = get(row, 'status') or ''
        direction = None
        if mean is not None and test is not None:
            direction = 'up' if test > mean else 'down' if test < mean else 'unchanged'
        item = {'metric': metric, 'unit': get(row, 'unit') or None, 'baseline_mean': mean,
                'baseline_sd': _num(get(row, 'baseline sd')), 'test_value': test, 'z': z, 'status': status,
                'direction': direction, 'baseline_n': _num(get(row, 'baseline n')), 'family': family(metric)}
        rows.append(item)
        channel = re.match(r'(.*?\.channel\.)([^.]+)\.(.+)$', metric)
        key = (channel.group(1) + '*.' + channel.group(3)) if channel else None
        if key and item['family'] and item['family'] != 'signal' and z is not None:
            groups[key].append((channel.group(2), item))

    signature = tuple((r['metric'], r['test_value'], r['z']) for r in rows)
    if signature in out['seen']:
        return
    out['seen'].add(signature)
    flagged = [r for r in rows if r['z'] is not None and abs(r['z']) > Z_THRESHOLD]
    n_values = {int(r['baseline_n']) for r in rows if r['baseline_n']}
    out['comparisons'].append({
        'source': _source(table), 'metrics_compared': len(rows),
        'z_scored': sum(r['z'] is not None for r in rows),
        'not_comparable': sum(r['z'] is None for r in rows),
        'flagged_elevated': sum(r['z'] > Z_THRESHOLD for r in flagged),
        'flagged_low': sum(r['z'] < -Z_THRESHOLD for r in flagged),
        'flagged_interpretable': sum(bool(r['family']) and r['family'] != 'signal' for r in flagged),
        'baseline_n': sorted(n_values),
        'expected_flag_rate_by_chance': chance_flag_rate(min(n_values)) if n_values else None,
        'expected_flags_by_chance': (round(chance_flag_rate(min(n_values)) * sum(r['z'] is not None for r in rows))
                                     if n_values and chance_flag_rate(min(n_values)) is not None else None),
    })

    def add(kind, r):
        facts.add(kind=kind, source=_source(table), metric=r['metric'], family=r['family'], unit=r['unit'],
                  baseline_mean=r['baseline_mean'], baseline_sd=r['baseline_sd'], value=r['test_value'], z=r['z'],
                  pct_change=_pct(r['test_value'], r['baseline_mean']), status=r['status'], direction=r['direction'],
                  baseline_n=r['baseline_n'])

    by_z = sorted(flagged, key=lambda r: -abs(r['z']))
    # 1. flagged metrics with a literature interpretation (EEG bands, ratios, P300)
    for r in by_z:
        if r['family'] and r['family'] != 'signal':
            add('flagged_change', r)
    # 2. P300 amplitude and latency are always reported, flagged or not
    for r in rows:
        if r['family'] in ('p300_amplitude', 'p300_latency') and r not in flagged:
            add('baseline_comparison', r)
    # 3. the same metric across channels: is the change consistent?
    for key, members in sorted(groups.items()):
        zs = [m['z'] for _, m in members]
        if len(zs) < 2:
            continue
        up, down = sum(z > 0 for z in zs), sum(z < 0 for z in zs)
        top_ch, top = max(members, key=lambda cm: abs(cm[1]['z']))
        frontal = [m['z'] for ch, m in members if ch in FRONTAL]
        pcts = [p for p in (_pct(m['test_value'], m['baseline_mean']) for _, m in members) if p is not None]
        mean_z = sum(zs) / len(zs)
        ordered = sorted(zs)
        mid = len(ordered) // 2
        median_z = ordered[mid] if len(ordered) % 2 else (ordered[mid - 1] + ordered[mid]) / 2
        facts.add(kind='channel_pattern', source=_source(table), metric=key, family=members[0][1]['family'],
                  channels=len(zs), median_z=round(median_z, 2), mean_z=round(mean_z, 2), channels_up=up, channels_down=down,
                  channels_flagged=sum(abs(z) > Z_THRESHOLD for z in zs),
                  mean_pct_change=round(sum(pcts) / len(pcts), 2) if pcts else None,
                  direction='up' if median_z > 0 else 'down' if median_z < 0 else 'unchanged',
                  frontal_mean_z=round(sum(frontal) / len(frontal), 2) if frontal else None,
                  largest=f'{top_ch} z={top["z"]:.2f}')
    # 4. flagged amplitude, waveform and quality descriptors: the largest few only
    other = [r for r in by_z if not r['family'] or r['family'] == 'signal']
    for r in other[:12]:
        add('flagged_signal_descriptor', r)


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


def build_evidence(text: str, figures: list[dict] | None = None) -> dict:
    facts = _Facts()
    out = {'comparisons': [], 'seen': set()}
    seen: set = set()
    tables = parse_tables(text)
    for table in tables:
        head = [h.lower() for h in table['header']]
        if 'z score' in head and 'metric' in head:
            _comparison(table, facts, out)
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
    families = sorted({f['family'] for f in facts.items if f.get('family')})
    refs = sorted({r for fam in families for r in METRIC_EFFECTS[fam]['refs']} | set(OVERTRAINING_REFS))
    return {
        'schema_version': EVIDENCE_SCHEMA,
        'report_type': 'baseline_comparison' if out['comparisons'] else 'single_recording',
        'recording': _recording(text),
        'z_threshold': Z_THRESHOLD,
        'comparisons': out['comparisons'],
        'facts': facts.items,
        'facts_not_included': facts.dropped,
        'figures': [{'index': f['index'], 'caption': f['caption']} for f in (figures or [])],
        'metric_effects': {fam: METRIC_EFFECTS[fam] for fam in families},
        'overtraining_note': OVERTRAINING_NOTE,
        'references': {key: REFERENCES[key] for key in refs},
    }


def effects_table_text() -> str:
    """METRIC_EFFECTS as plain text, for the instructions sent to the API."""
    lines = []
    for fam, e in METRIC_EFFECTS.items():
        refs = ', '.join(e['refs']) or 'no specific reference'
        lines.append(f'- {e["label"]}: UP: {e["up"]} DOWN: {e["down"]} ({refs})')
    lines.append(f'- Overtraining: {OVERTRAINING_NOTE} (Meeusen2013)')
    return '\n'.join(lines)
