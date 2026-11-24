"""Local stand-in for the FloLabs AI analysis API (placeholder).

Run it, then start Coach with NEUROBUDDY_AI_API_URL pointing at it:

    python tools/mock_ai_api.py                      # listens on 127.0.0.1:9100
    NEUROBUDDY_AI_API_URL=http://127.0.0.1:9100/analyze \
        .venv/bin/python -m uvicorn backend.app:app --host 127.0.0.1 --port 8765

It accepts the request described in docs/AI_ANALYSIS_API.md and answers with a
clearly labelled placeholder. No AI model is involved: the answer is a fixed,
rule-based example built only from the numbered facts Coach sends in
report.evidence and the literature map in report.evidence.metric_effects. It
shows the shape and grounding the real analysis must have (every statement
cites the fact it rests on). Replace NEUROBUDDY_AI_API_URL with the real
endpoint when FloLabs supplies it.
"""
from __future__ import annotations

import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

STATES = [('stress', 'Stress / arousal'), ('fatigue', 'Fatigue'), ('sleepiness', 'Sleep deprivation / sleepiness')]
BAND_FAMILIES = {'theta', 'alpha', 'beta', 'delta', 'alpha_theta_ratio', 'beta_alpha_ratio', 'beta_theta_ratio',
                 'theta_delta_ratio', 'peak_alpha_frequency'}


def _n(value) -> str:
    if isinstance(value, (int, float)):
        if value == int(value) and abs(value) < 1e6:
            return str(int(value))
        return f'{value:.4g}'
    return str(value)


def _short(metric: str) -> str:
    return metric.replace('shared.channel.', '').replace('specialized.oddball.erp.', 'oddball ').replace('*.', 'all channels: ')


def _pattern_consistent(f: dict) -> bool:
    n = f.get('channels') or 0
    same = f.get('channels_up', 0) if f.get('direction') == 'up' else f.get('channels_down', 0)
    return n >= 4 and abs(f.get('median_z') or 0) >= 1 and same >= 0.75 * n


def _cite(facts) -> str:
    return '[' + ', '.join(f['id'] for f in facts) + ']'


def _chance_level(ev: dict) -> bool:
    comps = [c for c in ev.get('comparisons') or [] if c.get('expected_flags_by_chance')]
    return bool(comps) and all(c['flagged_elevated'] + c['flagged_low'] <= 1.5 * c['expected_flags_by_chance'] for c in comps)


def _comparison_findings(ev: dict) -> tuple[list[str], dict, list[str]]:
    chance_level = _chance_level(ev)
    effects = ev.get('metric_effects') or {}
    facts = ev.get('facts') or []
    changes = [f for f in facts if f.get('kind') in ('flagged_change', 'baseline_comparison') and f.get('family') in effects]
    patterns = [f for f in facts if f.get('kind') == 'channel_pattern' and f.get('family') in effects]

    rows = []
    for f in changes:
        e = effects[f['family']]
        meaning = e['up'] if f.get('direction') == 'up' else e['down'] if f.get('direction') == 'down' else 'No change.'
        refs = ', '.join(e.get('refs') or []) or 'no specific reference'
        rows.append(f"| {f['id']} | {_short(f['metric'])} | {_n(f.get('baseline_mean'))} -> {_n(f.get('value'))} {f.get('unit') or ''} | "
                    f"z {_n(f.get('z'))}, {_n(f.get('pct_change'))}% | {f.get('direction')} | {meaning} ({refs}) |")

    ratings, sections = {}, []
    for key, title in STATES:
        pro, con, pro_pat, con_pat = [], [], [], []
        for f in changes:
            want = effects[f['family']].get('consistent_with', {}).get(key)
            flagged = f.get('z') is not None and abs(f['z']) > ev.get('z_threshold', 3)
            if want and flagged:
                (pro if f.get('direction') == want else con).append(f)
        for f in patterns:
            want = effects[f['family']].get('consistent_with', {}).get(key)
            if want and _pattern_consistent(f):
                (pro_pat if f.get('direction') == want else con_pat).append(f)
        p300 = [f for f in pro if f['family'].startswith('p300')]
        bands = [f for f in pro if f['family'] in BAND_FAMILIES]
        if not pro and not pro_pat:
            rating = 'None'
        elif not pro_pat and len(pro) <= len(con) + len(con_pat):
            rating = 'None'  # as much evidence points the other way
        elif pro_pat or (p300 and not chance_level):
            rating = 'Moderate'
        else:
            rating = 'Weak'
        ratings[key] = rating
        lines = [f'### {title}']
        if pro:
            lines.append(f'- Evidence for: ' + '; '.join(
                f"{_short(f['metric'])} {f['direction']} (z {_n(f.get('z'))}, {_n(f.get('pct_change'))}%) [{f['id']}]" for f in pro) + '.')
        if pro_pat:
            lines.append('- Consistent across channels: ' + '; '.join(
                f"{_short(f['metric'])} {f['direction']} in {f.get('channels_up') if f['direction'] == 'up' else f.get('channels_down')} of {f.get('channels')} channels, median z {_n(f.get('median_z'))} [{f['id']}]"
                for f in pro_pat) + '.')
        if con or con_pat:
            lines.append('- Evidence against: ' + '; '.join(
                f"{_short(f['metric'])} {f['direction']} [{f['id']}]" for f in con + con_pat) + '.')
        if bands and not pro_pat:
            lines.append(f'- The band changes are isolated channels without a matching pattern across the head {_cite(bands)}, which weakens them.')
        if not pro and not pro_pat:
            lines.append('- Not assessable from this report: no flagged metric or consistent channel pattern points this way.')
        elif rating == 'None':
            lines.append('- Not supported: at least as many flagged changes point the opposite way.')
        elif rating == 'Weak' and chance_level:
            lines.append('- Rated Weak because the number of flags in this report is no higher than chance would produce with this baseline size.')
        lines.append(f'- Evidence rating: **{rating}** (rule-based placeholder rating).')
        sections.append('\n'.join(lines))
    return rows, ratings, sections


def _single_findings(ev: dict) -> tuple[list[str], dict, list[str]]:
    facts = ev.get('facts') or []
    effects = ev.get('metric_effects') or {}
    rows = [f"| {f['id']} | {_short(f['metric'])} ({f.get('source')}) | {_n(f.get('value'))} {f.get('unit') or ''} | no baseline | n/a | "
            f"{effects[f['family']]['label']}: needs a baseline to judge a rise or drop |"
            for f in facts if f.get('family') in effects and f.get('family') != 'signal']
    sections = []
    for key, title in STATES:
        rel = [f for f in facts if f.get('family') in effects and key in (effects[f['family']].get('consistent_with') or {})]
        text = (f'- Relevant values in this recording: {_cite(rel)}. A single recording has no baseline, so a rise or drop '
                'cannot be judged and these values are not interpreted.') if rel else '- Not assessable from this report.'
        sections.append(f'### {title}\n{text}\n- Evidence rating: **None**.')
    return rows, {k: 'None' for k, _ in STATES}, sections


def placeholder_analysis(payload: dict) -> str:
    report = payload.get('report') or {}
    source = payload.get('source') or {}
    ev = report.get('evidence') or {}
    facts = ev.get('facts') or []
    figures = report.get('figures') or []
    rec = ev.get('recording') or {}
    comps = ev.get('comparisons') or []
    comparison = ev.get('report_type') == 'baseline_comparison'
    rows, ratings, sections = (_comparison_findings if comparison else _single_findings)(ev)
    quality = [f for f in facts if f.get('kind') == 'data_quality']
    by_id = {f['id']: f for f in facts}

    context = [f"- Report: {source.get('label') or source.get('filename') or 'HTML report'} ({report.get('title') or 'untitled'})."]
    if rec:
        context.append('- Recording: ' + ', '.join(f'{k} {v}' for k, v in rec.items()) + '.')
    for c in comps:
        flagged = c['flagged_elevated'] + c['flagged_low']
        line = (f"- {c['metrics_compared']} metrics compared with baseline (N = {', '.join(map(str, c['baseline_n'])) or '?'}); "
                f"{c['flagged_elevated']} elevated and {c['flagged_low']} low at |z| > {_n(ev.get('z_threshold', 3))} "
                f"({flagged} of {c['z_scored']} z-scored), of which {c['flagged_interpretable']} are EEG band, ratio or P300 metrics.")
        if c.get('expected_flags_by_chance') is not None:
            line += (f" With a baseline SD estimated from {min(c['baseline_n'])} sessions, about {c['expected_flags_by_chance']} flags "
                     f"({_n(100 * c['expected_flag_rate_by_chance'])}%) are expected from chance alone, so the flag count by itself "
                     + ('does not show a real change.' if flagged <= 1.5 * c['expected_flags_by_chance'] else 'is above chance.'))
        context.append(line)
    big = [f for f in facts if f.get('z') is not None and abs(f['z']) > 10 and f.get('pct_change') is not None and abs(f['pct_change']) < 15]
    if big:
        context.append('- Very large z-scores come from small percent changes: ' + '; '.join(
            f"{_short(f['metric'])} z {_n(f['z'])} for {_n(f['pct_change'])}% [{f['id']}]" for f in big[:3]) + '.')
    for f in quality:
        context.append(f"- {f['metric'].capitalize()}: {f['value']} [{f['id']}].")
    context.append(f'- Figures sent: {len(figures)}.')
    context += [f"  - Figure {f.get('index')}: {f.get('caption')}" for f in figures]

    p3 = [f for f in facts if (f.get('family') or '').startswith('p300')]
    if comparison:
        amp = [f for f in p3 if f['family'] == 'p300_amplitude' and f.get('direction') == 'down' and abs(f.get('z') or 0) > 3]
        lat = [f for f in p3 if f['family'] == 'p300_latency']
        summary = [f"Across {sum(c['metrics_compared'] for c in comps)} compared metrics, the strongest interpretable change is "
                   + (f"a smaller P300 than baseline {_cite(amp)}, which research associates with fatigue, sleep loss or reduced attention."
                      if amp else 'not in the P300.')]
        if lat:
            l = lat[0]
            summary.append(f"P300 latency moved {l.get('direction')} ({_n(l.get('baseline_mean'))} -> {_n(l.get('value'))} ms, z {_n(l.get('z'))}) [{l['id']}], "
                           + ('so timing does not add support for fatigue.' if l.get('direction') != 'up' or abs(l.get('z') or 0) <= 3 else 'which adds support for slower processing.'))
        if _chance_level(ev):
            summary.append('The total number of flags is about what chance alone produces with this few baselines '
                           f"({', '.join(str(n) for c in comps for n in c['baseline_n'])} sessions), and the spectral flags are scattered single "
                           'channels rather than a head-wide shift, so overall confidence is low.')
        else:
            summary.append('More metrics are flagged than chance alone would produce, so the pattern deserves a closer look, '
                           'but treat single-channel spectral flags with caution.')
    else:
        summary = ['This is a single recording with no baseline, so it can describe values but cannot show a rise or drop.']
        d = next((f for f in facts if 'cohen' in str(f.get('metric', '')).lower()), None)
        peak = next((f for f in p3 if f['family'] == 'p300_amplitude' and 'peak' in f['metric'].lower()), None)
        if d and peak:
            summary.append(f"A clear target P300 was elicited ({_n(peak['value'])} µV peak, Cohen's d {_n(d['value'])}) [{peak['id']}, {d['id']}].")
        summary.append('Compare it with baseline sessions to judge fatigue, stress or sleep loss.')

    under = ratings.get('fatigue', 'None')
    refs = ev.get('references') or {}
    out = [
        '> PLACEHOLDER RESPONSE from tools/mock_ai_api.py. No AI model analyzed this report. This is a rule-based example '
        'built only from the numbered facts Coach extracted from the report, to show how the real analysis must cite its evidence.',
        '', '## Summary', ' '.join(summary), '',
        '## Data quality and context', *context, '',
        '## Flagged metrics: direction and meaning',
        '| Evidence | Metric | Baseline -> Test | Change (z, %) | Direction | What this direction has been associated with |',
        '|---|---|---|---|---|---|', *(rows or ['| - | No interpretable metric | | | | |']), '',
        '## Findings', '### Overtraining / under-recovery',
        f"- {ev.get('overtraining_note') or 'No established EEG signature.'} (Meeusen2013)",
        f'- Under-recovery is only as supported as the fatigue finding below (rating {under}).',
        f'- Evidence rating: **{"Weak" if under != "None" else "None"}** (rule-based placeholder rating).', '',
        *sections, '### Other observations',
        f'- {len([f for f in facts if f.get("kind") == "flagged_signal_descriptor"])} amplitude or waveform descriptors were also flagged; '
        'these mostly reflect recording conditions rather than a training state.', '',
        '## Limitations',
        '- Placeholder: thresholds and ratings are fixed rules, not expert or AI judgement.',
        '- Associations are group-level and non-specific; none is a marker for an individual athlete.',
        '- Synthetic or demonstration recordings cannot support physiological conclusions.', '',
        '## Suggested follow-up',
        '- Add baseline sessions (at least 5) so the baseline SD is stable.',
        '- Record eyes-open and eyes-closed rest at the same time of day, and log sleep, caffeine and training load.',
        '- Pair EEG with HRV, a wellness questionnaire and a performance test before acting on any change.', '',
        '## References', *(f'- {k}: {v}' for k, v in refs.items()),
    ]
    if not by_id:
        out.insert(2, '_The request carried no report.evidence facts, so nothing could be cited._')
    return '\n'.join(out)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        print('[mock-ai-api]', fmt % args)

    def do_POST(self):
        length = int(self.headers.get('content-length') or 0)
        try:
            payload = json.loads(self.rfile.read(length) or b'{}')
        except ValueError:
            self._send(400, {'error': 'Body must be JSON.'})
            return
        if payload.get('schema_version') != 'neurobuddy.ai-analysis-request/0.1':
            self._send(400, {'error': 'Unknown schema_version.'})
            return
        self._send(200, {'analysis_markdown': placeholder_analysis(payload), 'model': 'placeholder', 'id': 'mock-1'})

    def _send(self, status: int, body: dict):
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header('content-type', 'application/json')
        self.send_header('content-length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=9100)
    args = parser.parse_args()
    print(f'Placeholder AI analysis API on http://{args.host}:{args.port}/analyze')
    ThreadingHTTPServer((args.host, args.port), Handler).serve_forever()


if __name__ == '__main__':
    main()
