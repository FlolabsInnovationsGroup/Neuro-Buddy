"""Local stand-in for the FloLabs AI analysis API (placeholder).

Run it, then start Coach with NEUROBUDDY_AI_API_URL pointing at it:

    python tools/mock_ai_api.py                      # listens on 127.0.0.1:9100
    NEUROBUDDY_AI_API_URL=http://127.0.0.1:9100/analyze \
        .venv/bin/python -m uvicorn backend.app:app --host 127.0.0.1 --port 8765

It accepts the request described in docs/AI_ANALYSIS_API.md and answers with a
clearly labelled placeholder. No AI model is involved: the answer is a fixed,
rule-based example built only from the numbered facts Coach sends in
report.evidence (or subject.athletes[].evidence) and the literature map in report.evidence.metric_effects. It
shows the shape and grounding the real analysis must have (every statement
cites the fact it rests on). Replace NEUROBUDDY_AI_API_URL with the real
endpoint when FloLabs supplies it.
"""
from __future__ import annotations

import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

AREAS = [('recovery', 'Recovery'), ('fatigue', 'Fatigue'), ('sleep', 'Sleep'),
         ('stress', 'Stress / arousal'), ('attention', 'Attention / alertness')]
OTHER = 'Listed with its z-score; not linked to a specific area.'


def _n(value) -> str:
    if isinstance(value, (int, float)):
        if value == int(value) and abs(value) < 1e6:
            return str(int(value))
        return f'{value:.4g}'
    return str(value)


def _n3(value) -> str:
    return f'{value:.3g}' if isinstance(value, (int, float)) else str(value)


def _z(value) -> str:
    return f'{value:+.2f}' if isinstance(value, (int, float)) else 'n/a'


def _short(metric: str) -> str:
    name = (metric.replace('shared.channel.', '').replace('shared.quality.', 'quality ')
            .replace('specialized.oddball.erp.', 'oddball ').replace('*.', 'all channels: '))
    return name.replace('.', ' ').replace('_', ' ')


def _meaning(f: dict, effects: dict) -> str:
    e = effects.get(f.get('family') or '')
    if not e:
        return OTHER
    return e['up'] if f.get('direction') == 'up' else e['down'] if f.get('direction') == 'down' else OTHER


def _short_meaning(f: dict, effects: dict) -> str:
    """'fatigue, sleep loss or reduced attention' from the full sentence, for the side-by-side tables."""
    import re
    text = _meaning(f, effects)
    m = re.search(r'may be related to (.*?)(?: such as .*)?\.$', text)
    return m.group(1) if m else 'no specific link'


def _consistent_pattern(f: dict) -> bool:
    n = f.get('channels') or 0
    same = f.get('channels_up', 0) if f.get('direction') == 'up' else f.get('channels_down', 0)
    return n >= 4 and abs(f.get('median_z') or 0) >= 1 and same >= 0.75 * n


def _comparison_sections(ev: dict, prefix: str = '') -> list[str]:
    effects = ev.get('metric_effects') or {}
    facts = ev.get('facts') or []
    cite = lambda f: f"[{prefix}{f['id']}]"
    outside = [f for f in facts if f.get('kind') in ('outside_3sd', 'outside_3sd_other')]
    interp = [f for f in outside if f.get('kind') == 'outside_3sd']
    within_p3 = [f for f in facts if f.get('kind') == 'within_3sd']
    summaries = [f for f in facts if f.get('kind') == 'channel_summary']
    comps = ev.get('comparisons') or []
    rec = ev.get('recording') or {}

    # Summary
    p3_out = [f for f in interp if (f.get('family') or '').startswith('p300')]
    head_wide = [f for f in summaries if f.get('family') in effects and f.get('family') != 'signal' and _consistent_pattern(f)]
    summary = []
    for c in comps:
        summary.append(f"The testing session was compared with {', '.join(map(str, c['baseline_n'])) or '?'} baseline sessions on "
                       f"{c['z_scored']} metrics: {c['above_3sd']} are above +3 SD, {c['below_3sd']} are below -3 SD and "
                       f"{c['within_3sd']} are within 3 SD of the baseline mean.")
    if p3_out:
        f = p3_out[0]
        summary.append(f"The largest EEG change is {_short(f['metric'])} at z {_z(f['z'])} ({f['position']}, "
                       f"{_n(f.get('baseline_mean'))} to {_n(f.get('value'))} {f.get('unit') or ''}) {cite(f)}. {_meaning(f, effects)}")
    elif interp:
        f = interp[0]
        summary.append(f"The largest EEG change is {_short(f['metric'])} at z {_z(f['z'])} {cite(f)}. {_meaning(f, effects)}")
    if head_wide:
        summary.append('Changes shared across most channels: ' + '; '.join(
            f"{_short(f['metric'])} {f['direction']} (median z {_z(f['median_z'])}) {cite(f)}" for f in head_wide) + '.')
    else:
        summary.append('The band-power changes outside 3 SD sit in single channels rather than across the head.')

    context = []
    if rec:
        context.append('Recording: ' + ', '.join(f'{k} {v}' for k, v in rec.items()) + '.')

    out_rows = []
    for f in outside:
        sd = f" ± {_n3(f.get('baseline_sd'))}" if f.get('baseline_sd') is not None else ''
        arrow = 'above' if (f.get('z') or 0) > 0 else 'below'
        out_rows.append(f"| {prefix}{f['id']} | {_short(f['metric'])} | {_n3(f.get('baseline_mean'))}{sd} | "
                        f"{_n3(f.get('value'))} | **{_z(f.get('z'))}** {arrow} | {_short_meaning(f, effects)} |")
    in_rows = []
    for f in within_p3:
        in_rows.append(f"| {prefix}{f['id']} | {_short(f['metric'])} | 1 of 1 | {_z(f.get('z'))} | "
                       f"{_n3(f.get('baseline_mean'))} to {_n3(f.get('value'))} {f.get('unit') or ''} |")
    for f in summaries:
        note = []
        if f.get('channels_above'):
            note.append(f"{f['channels_above']} above +3 SD")
        if f.get('channels_below'):
            note.append(f"{f['channels_below']} below -3 SD")
        if _consistent_pattern(f):
            note.append(f"{f['direction']} in most channels")
        in_rows.append(f"| {prefix}{f['id']} | {_short(f['metric'])} | {f.get('channels_within')} of {f.get('channels')} | "
                       f"{f.get('within_z_range') or 'n/a'} (median {_z(f.get('median_z'))}) | {', '.join(note)} |")

    areas = []
    for key, title in AREAS:
        lines = [f'### {title}']
        match = [f for f in interp if (effects.get(f.get('family')) or {}).get('consistent_with', {}).get(key) == f.get('direction')]
        trend = [f for f in summaries if f.get('family') in effects and _consistent_pattern(f)
                 and effects[f['family']].get('consistent_with', {}).get(key) == f.get('direction')]
        if match:
            p3 = [f for f in match if f['family'].startswith('p300')]
            spread = 'Includes the P300, ' if p3 else ''
            lines.append(f'- {spread}{len(match)} metric(s) outside 3 SD line up with this area:')
            lines += [f"  - {_short(f['metric'])}: z {_z(f['z'])}, {f['position']} {cite(f)}. {_meaning(f, effects)}" for f in match[:8]]
            if len(match) > 8:
                lines.append(f"  - and {len(match) - 8} more: " + ', '.join(cite(f) for f in match[8:]))
        else:
            lines.append('- No metrics outside 3 SD line up with this area.')
        if trend:
            lines.append('- Within-range trends across channels: ' + '; '.join(
                f"{_short(f['metric'])} {f['direction']} (median z {_z(f['median_z'])}) {cite(f)}" for f in trend) + '.')
        areas.append('\n'.join(lines))

    return [
        '## Summary', ' '.join(summary), *([''] + context if context else []), '',
        '## Outside ±3 SD (elevated or low)',
        '| Evidence | Metric | Baseline mean ± SD | Testing | z vs ±3 SD | May be related to |',
        '|---|---|---|---|---|---|', *(out_rows or ['| - | None | | | | |']), '',
        '## Within ±3 SD (normal)',
        '| Evidence | Metric | Channels within | z range | Note |',
        '|---|---|---|---|---|', *(in_rows or ['| - | None | | | |']), '',
        '## What the results may be related to', *areas, '',
    ]


def _single_sections(ev: dict) -> list[str]:
    facts = ev.get('facts') or []
    effects = ev.get('metric_effects') or {}
    rec = ev.get('recording') or {}
    rows = [f"| {f['id']} | {_short(str(f['metric']))} ({f.get('source')}) | {_n(f.get('value'))} {f.get('unit') or ''} |"
            for f in facts if f.get('kind') in ('statistic', 'figure_data', 'data_quality')]
    d = next((f for f in facts if 'cohen' in str(f.get('metric', '')).lower()), None)
    peak = next((f for f in facts if f.get('family') == 'p300_amplitude' and 'peak' in str(f['metric']).lower()), None)
    summary = ['This report is a single recording, so it holds values without z-scores.']
    if d and peak:
        summary.append(f"A clear target P300 was elicited ({_n(peak['value'])} µV peak, Cohen's d {_n(d['value'])}) [{peak['id']}, {d['id']}].")
    summary.append('Drop the athlete into AI Processing to compare these values with the baseline sessions and get z-scores.')
    return [
        '## Summary', ' '.join(summary), '',
        *(['Recording: ' + ', '.join(f'{k} {v}' for k, v in rec.items()) + '.', ''] if rec else []),
        '## Values in this recording', '| Evidence | Metric | Value |', '|---|---|---|', *rows, '',
    ]


# Recommendation per out-of-range area, from what the finding may be related to.
PRACTICES = {
    'fatigue': 'consider a lighter or shorter session or an extra rest day, and log training load and RPE',
    'sleep': 'consider sleep extension, regular sleep and wake times, and caffeine timing',
    'recovery': 'consider active recovery, a deload week if it persists, and HRV and resting heart rate checks',
    'stress': 'consider paced breathing or HRV biofeedback, a relaxation routine, and less non-training stress',
    'attention': 'consider shorter, well-rested skill sessions with breaks and a pre-session routine',
}


def _recommendations(ev: dict, prefix: str = '') -> list[str]:
    effects = ev.get('metric_effects') or {}
    facts = [f for f in (ev.get('facts') or []) if f.get('kind') == 'outside_3sd']
    facts.sort(key=lambda f: -abs(f.get('z') or 0))
    rows = []
    for f in facts:
        areas = [a for a, d in ((effects.get(f.get('family') or '') or {}).get('consistent_with') or {}).items()
                 if d == f.get('direction')]
        advice = '; '.join(PRACTICES[a] for a in areas if a in PRACTICES) or 'keep the current plan and re-test to confirm'
        rows.append(f"| {prefix}{f['id']} | {_short(f['metric'])} {f.get('direction')} (z {_z(f.get('z'))}) "
                    f"| {_short_meaning(f, effects)} | {advice} |")
    lines = ['## Recommendations']
    if rows:
        lines += ['| Evidence | Finding | May be related to | Recommendation |', '|---|---|---|---|', *rows, '',
                  '- Over the next one to two weeks, consider adjusting load to the findings above and re-testing at the same time of day.']
    else:
        lines += ['No value is outside ±3 SD. Consider keeping the current plan and the usual sleep and recovery routine.']
    return lines + ['']


def placeholder_analysis(payload: dict) -> str:
    banner = ('> PLACEHOLDER RESPONSE from tools/mock_ai_api.py. No AI model analyzed this report. This is a rule-based '
              'example built only from the numbered facts Coach extracted, to show the layout and citation style of the real analysis.')
    out, refs = [banner, ''], {}
    subject = payload.get('subject')
    if subject:
        athletes = subject.get('athletes') or []
        team = len(athletes) > 1 or subject.get('type') == 'team'
        for a in athletes:
            ev = a.get('evidence') or {}
            refs.update(ev.get('references') or {})
            if team:
                out += [f"# {a.get('athlete_name')}", '']
            out += _comparison_sections(ev, prefix=f"{a.get('athlete_name')}:" if team else '')
            out += _recommendations(ev, prefix=f"{a.get('athlete_name')}:" if team else '')
        if subject.get('athletes_without_comparison'):
            out += ['Athletes without a completed baseline vs testing comparison: ' + ', '.join(subject['athletes_without_comparison']) + '.', '']
    else:
        report = payload.get('report') or {}
        ev = report.get('evidence') or {}
        refs.update(ev.get('references') or {})
        out += _comparison_sections(ev) if ev.get('report_type') == 'baseline_comparison' else _single_sections(ev)
        if ev.get('report_type') == 'baseline_comparison':
            out += _recommendations(ev)
        figures = report.get('figures') or []
        if figures:
            out += ['Figures sent: ' + '; '.join(f"Figure {f.get('index')}: {f.get('caption')}" for f in figures) + '.', '']
    out += [
        '## Suggested follow-up',
        '- Add baseline sessions so the baseline SD is more stable.',
        '- Record at the same time of day, and log sleep, caffeine and training load next to each session.',
        '- Pair EEG with HRV, a wellness questionnaire and a performance test.', '',
        '## References', *(f'- {k}: {v}' for k, v in sorted(refs.items())),
    ]
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
