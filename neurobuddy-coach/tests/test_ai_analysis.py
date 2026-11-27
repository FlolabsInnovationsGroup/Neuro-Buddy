"""AI analysis of generated HTML reports (Advanced Analysis drop zone).

The external analysis API is a placeholder until FloLabs supplies it, so a
local fake server stands in for it here.
"""
import base64
import importlib
import json
import sys
import threading
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

PNG = base64.b64encode(bytes.fromhex(
    '89504e470d0a1a0a0000000d4948445200000001000000010806000000'
    '1f15c4890000000d49444154789c6360000002000154a24f5f0000000049454e44ae426082')).decode()

REPORT = f"""<!doctype html><html><head><title>Neuro-buddy V2 EEG Report</title>
<style>.x{{color:red}}</style><script>window.secret = "do not send";</script></head><body>
<h1>Neuro-buddy V2 EEG Analysis</h1><p>Research output only.</p>
<table><tr><th>Metric</th><th>Value</th></tr><tr><td>p300_difference_peak_uv</td><td>11.6</td></tr></table>
<img alt="Oddball ERP target vs standard" src="data:image/png;base64,{PNG}">
<canvas aria-label="heatmap"></canvas>
</body></html>"""

ANALYSIS = '## Summary\nNo strong evidence of fatigue in this synthetic report.'


class FakeAPI(BaseHTTPRequestHandler):
    requests = []
    reply = (200, {'analysis_markdown': ANALYSIS, 'model': 'flolabs-test'})

    def log_message(self, *args):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers['content-length'])))
        FakeAPI.requests.append({'path': self.path, 'headers': {k.lower(): v for k, v in self.headers.items()}, 'body': body})
        status, payload = FakeAPI.reply
        data = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header('content-type', 'application/json')
        self.send_header('content-length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def serve(handler):
    server = ThreadingHTTPServer(('127.0.0.1', 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f'http://127.0.0.1:{server.server_address[1]}/analyze'


@pytest.fixture()
def fake_api():
    FakeAPI.requests = []
    FakeAPI.reply = (200, {'analysis_markdown': ANALYSIS, 'model': 'flolabs-test'})
    server, url = serve(FakeAPI)
    yield url
    server.shutdown()


def boot(tmp_path, monkeypatch, url=None, key=None):
    monkeypatch.setenv('NEUROBUDDY_DATA_DIR', str(tmp_path / 'data'))
    for name, value in (('NEUROBUDDY_AI_API_URL', url), ('NEUROBUDDY_AI_API_KEY', key)):
        if value:
            monkeypatch.setenv(name, value)
        else:
            monkeypatch.delenv(name, raising=False)
    for name in ('backend.app', 'backend.shelves'):
        sys.modules.pop(name, None)
    app = importlib.import_module('backend.app')
    job_id = str(uuid4())
    team_id, athlete_id = str(uuid4()), str(uuid4())
    with app.database() as db:
        db.execute('INSERT INTO teams VALUES(?,?,?)', (team_id, 'Atlas Sprint Lab', app.now()))
        db.execute('INSERT INTO athletes VALUES(?,?,?,?)', (athlete_id, team_id, 'Athlete Rivera', app.now()))
        db.execute('''INSERT INTO jobs(id,mode,target_id,test_recording_id,created_at,updated_at,status,error,result,
                      result_dir,team_id,athlete_id) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)''',
                   (job_id, 'single', 'shelf', None, app.now(), app.now(), 'completed', None,
                    json.dumps({'mode': 'single'}), job_id, team_id, athlete_id))
    (app.REPORTS / job_id).mkdir(parents=True)
    (app.REPORTS / job_id / 'report.html').write_text(REPORT, encoding='utf-8')
    return TestClient(app.app), app, job_id


def test_extract_report_keeps_text_tables_and_figures_but_not_scripts():
    from backend import ai_analysis
    out = ai_analysis.extract_report(REPORT)
    assert out['title'] == 'Neuro-buddy V2 EEG Report'
    assert '# Neuro-buddy V2 EEG Analysis' in out['text']
    assert '| p300_difference_peak_uv | 11.6' in out['text']
    assert 'do not send' not in out['text'] and 'color:red' not in out['text']
    assert [img['alt'] for img in out['images']] == ['Oddball ERP target vs standard']
    assert out['images'][0]['media_type'] == 'image/png'


def test_missing_api_url_fails_gracefully(tmp_path, monkeypatch):
    client, _, job_id = boot(tmp_path, monkeypatch)
    assert client.get('/api/v2/ai-analysis/status').json()['configured'] is False
    assert client.get('/api/v2/health').json()['ai_connected'] is False
    response = client.post('/api/v2/ai-analysis/report', json={'job_id': job_id, 'path': 'report.html'})
    assert response.status_code == 503
    assert 'NEUROBUDDY_AI_API_URL' in response.json()['detail']


def test_report_paths_cannot_escape_the_job_folder(tmp_path, monkeypatch, fake_api):
    client, app, job_id = boot(tmp_path, monkeypatch, url=fake_api)
    (app.STORAGE / 'outside.html').write_text(REPORT, encoding='utf-8')
    for path in ('../outside.html', '../../outside.html', 'missing.html', 'report.json'):
        response = client.post('/api/v2/ai-analysis/report', json={'job_id': job_id, 'path': path})
        assert response.status_code == 404, path
    assert FakeAPI.requests == []


def test_advanced_index_lists_only_longitudinal_reports(tmp_path, monkeypatch):
    """AI Processing offers longitudinal reports only, not single EEG or baseline/testing pages."""
    client, app, job_id = boot(tmp_path, monkeypatch)
    athlete = client.get('/api/v2/shelf-advanced').json()['teams'][0]['athletes'][0]
    assert athlete['reports'][0]['html_reports'] == []  # a single EEG run has no longitudinal report
    with app.database() as db:
        db.execute("UPDATE jobs SET mode='athlete' WHERE id=?", (job_id,))
    folder = app.REPORTS / job_id
    for rel in ('athlete_report.html', 'comparative_analysis.html', 'test/report.html',
                'baseline/longitudinal.html', 'baseline/01_a/report.html'):
        (folder / rel).parent.mkdir(parents=True, exist_ok=True)
        (folder / rel).write_text(REPORT, encoding='utf-8')
    athlete = client.get('/api/v2/shelf-advanced').json()['teams'][0]['athletes'][0]
    assert athlete['reports'][0]['html_reports'] == [
        {'job_id': job_id, 'path': 'athlete_report.html', 'label': 'Longitudinal athlete report'}]


def test_job_report_is_sent_to_the_api_and_saved(tmp_path, monkeypatch, fake_api):
    client, _, job_id = boot(tmp_path, monkeypatch, url=fake_api, key='secret-key')
    status = client.get('/api/v2/ai-analysis/status').json()
    assert status['configured'] is True and 'secret-key' not in json.dumps(status)
    before = client.get('/api/v2/ai-analysis/report', params={'job_id': job_id, 'path': 'report.html'})
    assert before.status_code == 200 and before.json() == {'saved': False}

    response = client.post('/api/v2/ai-analysis/report', json={'job_id': job_id, 'path': 'report.html'})
    assert response.status_code == 200, response.text
    result = response.json()
    assert result['analysis_markdown'] == ANALYSIS
    assert result['model'] == 'flolabs-test'
    assert result['source']['athlete_name'] == 'Athlete Rivera'
    assert result['figures_sent'] == 1
    assert 'secret-key' not in json.dumps(result)

    sent = FakeAPI.requests[-1]
    assert sent['path'] == '/analyze'
    assert sent['headers']['authorization'] == 'Bearer secret-key'
    body = sent['body']
    assert body['schema_version'] == 'neurobuddy.ai-analysis-request/0.1'
    for topic in ('recovery', 'fatigue', 'sleep', 'stress', 'attention'):
        assert topic in body['instructions'].lower()
    assert body['report']['html'] == REPORT
    assert '| p300_difference_peak_uv | 11.6' in body['report']['text']
    assert 'do not send' not in body['report']['text']
    assert body['report']['figures'] == [{'index': 1, 'caption': 'Oddball ERP target vs standard',
                                          'media_type': 'image/png', 'data_base64': PNG}]

    saved = client.get('/api/v2/ai-analysis/report', params={'job_id': job_id, 'path': 'report.html'}).json()
    assert saved['saved'] is True and saved['analysis_markdown'] == ANALYSIS


def test_dropped_html_file_is_analyzed(tmp_path, monkeypatch, fake_api):
    client, _, _ = boot(tmp_path, monkeypatch, url=fake_api)
    response = client.post('/api/v2/ai-analysis/upload',
                           files={'file': ('athlete_report.html', REPORT.encode(), 'text/html')})
    assert response.status_code == 200, response.text
    assert response.json()['source'] == {'kind': 'uploaded_file', 'filename': 'athlete_report.html', 'label': 'athlete_report.html'}
    assert 'authorization' not in FakeAPI.requests[-1]['headers']
    bad = client.post('/api/v2/ai-analysis/upload', files={'file': ('notes.txt', b'hello', 'text/plain')})
    assert bad.status_code == 422


@pytest.mark.parametrize('reply,status,phrase', [
    ((401, {'error': 'bad key'}), 502, 'credentials'),
    ((429, {'error': 'slow down'}), 429, 'rate limiting'),
    ((500, {'error': 'boom'}), 502, 'HTTP 500'),
    ((200, {'something': 'else'}), 502, 'analysis_markdown'),
])
def test_api_errors_are_reported_clearly(tmp_path, monkeypatch, fake_api, reply, status, phrase):
    FakeAPI.reply = reply
    client, _, job_id = boot(tmp_path, monkeypatch, url=fake_api)
    response = client.post('/api/v2/ai-analysis/report', json={'job_id': job_id, 'path': 'report.html'})
    assert response.status_code == status
    assert phrase in response.json()['detail']


def test_unreachable_api_is_reported(tmp_path, monkeypatch):
    client, _, job_id = boot(tmp_path, monkeypatch, url='http://127.0.0.1:9/analyze')
    response = client.post('/api/v2/ai-analysis/report', json={'job_id': job_id, 'path': 'report.html'})
    assert response.status_code == 502
    assert 'Could not reach' in response.json()['detail']


def test_oversized_report_is_refused_not_truncated(tmp_path, monkeypatch, fake_api):
    monkeypatch.setenv('NEUROBUDDY_AI_MAX_CHARS', '50')
    client, _, job_id = boot(tmp_path, monkeypatch, url=fake_api)
    response = client.post('/api/v2/ai-analysis/report', json={'job_id': job_id, 'path': 'report.html'})
    assert response.status_code == 413
    assert FakeAPI.requests == []


def test_placeholder_mock_api_answers_the_real_request(tmp_path, monkeypatch):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
    import mock_ai_api
    server, url = serve(mock_ai_api.Handler)
    try:
        client, _, job_id = boot(tmp_path, monkeypatch, url=url)
        response = client.post('/api/v2/ai-analysis/report', json={'job_id': job_id, 'path': 'report.html'})
        assert response.status_code == 200, response.text
        assert 'PLACEHOLDER RESPONSE' in response.json()['analysis_markdown']
        assert 'Figure 1: Oddball ERP target vs standard' in response.json()['analysis_markdown']
    finally:
        server.shutdown()


COMPARISON = """<html><head><title>Neuro-buddy Athlete Comparative Report</title></head><body>
<h1>Neuro-buddy Athlete Comparative Report</h1><h2>Longitudinal file</h2><table>
<tr><th>Metric</th><th>Unit</th><th>Baseline Mean</th><th>Baseline Sd</th><th>Test Value</th><th>Z Score</th><th>Flag</th><th>Status</th><th>Baseline N</th></tr>
<tr><td>specialized.oddball.erp.p300_difference_peak_uv</td><td>µV</td><td>13.2</td><td>0.12</td><td>11.6</td><td>-13.3</td><td></td><td>Abnormal</td><td>3</td></tr>
<tr><td>specialized.oddball.erp.p300_difference_peak_latency_ms</td><td>ms</td><td>304</td><td>20</td><td>288</td><td>-0.8</td><td></td><td>Within range</td><td>3</td></tr>
""" + ''.join(f"<tr><td>shared.channel.{ch}.theta_relative</td><td>ratio</td><td>0.030</td><td>0.001</td><td>{v}</td><td>{z}</td><td></td><td>{s}</td><td>3</td></tr>"
             for ch, v, z, s in [('Fz', '0.034', '4.0', 'Abnormal'), ('F3', '0.032', '2.0', 'Within range'),
                                 ('F4', '0.032', '2.0', 'Within range'), ('Pz', '0.031', '1.0', 'Within range')]) + """
<tr><td>shared.channel.Fz.rms</td><td>V</td><td>1e-05</td><td>1e-08</td><td>1.01e-05</td><td>10</td><td></td><td>Abnormal</td><td>3</td></tr>
<tr><td>shared.channel.Fz.dominant_frequency</td><td>Hz</td><td>10</td><td>0</td><td>10</td><td>N/A</td><td></td><td>Not comparable</td><td>3</td></tr>
</table></body></html>"""


def test_evidence_splits_outside_and_within_3sd_with_z_scores():
    from backend import ai_analysis, report_evidence
    ev = report_evidence.build_evidence(ai_analysis.extract_report(COMPARISON)['text'])
    assert ev['report_type'] == 'baseline_comparison'
    comp = ev['comparisons'][0]
    assert (comp['metrics_compared'], comp['not_comparable'], comp['above_3sd'], comp['below_3sd'], comp['within_3sd']) == (8, 1, 2, 1, 4)
    assert 'expected_outside_by_chance' not in comp
    facts = {f['metric']: f for f in ev['facts']}
    p300 = facts['specialized.oddball.erp.p300_difference_peak_uv']
    assert (p300['kind'], p300['position'], p300['direction'], p300['family'], p300['z']) == ('outside_3sd', 'below -3 SD', 'down', 'p300_amplitude', -13.3)
    assert p300['pct_change'] == pytest.approx(-12.12, abs=0.01)
    latency = facts['specialized.oddball.erp.p300_difference_peak_latency_ms']
    assert (latency['kind'], latency['position']) == ('within_3sd', 'within 3 SD')
    assert facts['shared.channel.Fz.theta_relative']['position'] == 'above +3 SD'
    summary = facts['shared.channel.*.theta_relative']
    assert (summary['kind'], summary['channels'], summary['channels_within'], summary['channels_above'], summary['median_z']) == ('channel_summary', 4, 3, 1, 2.0)
    assert facts['shared.channel.Fz.rms']['kind'] == 'outside_3sd_other'
    assert 'may be related to fatigue' in ev['metric_effects']['p300_amplitude']['down']
    assert [a['key'] for a in ev['areas']] == ['recovery', 'fatigue', 'sleep', 'stress', 'attention']
    assert [f['id'] for f in ev['facts']] == [f'E{i}' for i in range(1, len(ev['facts']) + 1)]


def test_instructions_ask_for_area_sections_with_values_then_implications():
    from backend import ai_analysis
    text = ai_analysis.SYSTEM_PROMPT
    assert '### Values' in text and '### Implications' in text and 'What it measures' in text
    for status in ('"Out of range (elevated)"', '"Out of range (low)"', '"In range"'):
        assert status in text
    assert 'Always give the z-score' in text and 'may be related to' in text
    assert 'Do not write about what the metrics cannot show' in text
    for area in ('## Sleep', '## Fatigue', '## Recovery', '## Stress / arousal', '## Attention / alertness'):
        assert area in text
    assert 'Not assessable' not in text and '{effects}' not in text
    # Overtraining appears only as something to monitor in the recommendations, never as a disclaimer.
    assert 'overtraining' not in text.split('## Recommendations')[0].lower()
    recs = text.split('## Recommendations')[1].split('## Suggested follow-up')[0]
    assert 'Evidence | Finding | May be related to | Recommendation' in recs
    for practice in ('Training:', 'Recovery and sleep:', 'Stress management:', 'overtraining', 'breathing'):
        assert practice in recs
    for effect in __import__('backend.report_evidence', fromlist=['x']).METRIC_EFFECTS.values():
        assert 'cannot' not in (effect['up'] + effect['down']) and 'no established' not in (effect['up'] + effect['down']).lower()


def test_request_carries_evidence_and_mock_writes_both_sections():
    from backend import ai_analysis
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
    import mock_ai_api
    extracted = ai_analysis.extract_report(COMPARISON)
    payload = ai_analysis.build_request(COMPARISON, extracted, {'label': 'test'})
    assert payload['report']['evidence']['facts'][0]['metric'] == 'specialized.oddball.erp.p300_difference_peak_uv'
    md = mock_ai_api.placeholder_analysis(payload)
    assert 'PLACEHOLDER RESPONSE' in md and 'No AI model' in md
    assert '## Outside ±3 SD (elevated or low)' in md and '## Within ±3 SD (normal)' in md
    assert '| E1 | oddball p300 difference peak uv | 13.2 ± 0.12 | 11.6 | **-13.30** below | fatigue, sleep loss or reduced attention |' in md
    assert 'all channels: theta relative | 3 of 4' in md
    assert 'cannot' not in md and 'Not assessable' not in md and 'by chance' not in md


LONGITUDINAL = {'task_subtype': 'oddball', 'baseline_recording_count': 3, 'method': 'mean and sample SD',
                'metrics': [{'metric': 'specialized.oddball.erp.p300_difference_peak_uv', 'baseline_values': [13.1, 13.2, 13.3]}]}
CSV_HEADER = 'metric,unit,baseline_mean,baseline_sd,test_value,z_score,flag,status,baseline_n,lower_bound,upper_bound,cv_percent,reason\n'


def boot_athlete(tmp_path, monkeypatch, url):
    client, app, job_id = boot(tmp_path, monkeypatch, url=url)
    with app.database() as db:
        db.execute("UPDATE jobs SET mode='athlete' WHERE id=?", (job_id,))
        athlete_id, team_id = db.execute('SELECT athlete_id, team_id FROM jobs WHERE id=?', (job_id,)).fetchone()
    folder = app.REPORTS / job_id
    (folder / 'comparative_analysis.csv').write_text(CSV_HEADER +
        'specialized.oddball.erp.p300_difference_peak_uv,µV,13.2,0.1,11.6,-16,,Abnormal,3,,,,\n'
        'shared.channel.Fz.theta_relative,ratio,0.03,0.001,0.031,1.0,,Within range,3,,,,\n'
        'shared.channel.Pz.theta_relative,ratio,0.03,0.001,0.0305,0.5,,Within range,3,,,,\n', encoding='utf-8')
    (folder / 'baseline').mkdir()
    (folder / 'baseline' / 'longitudinal.json').write_text(json.dumps(LONGITUDINAL), encoding='utf-8')
    return client, athlete_id, team_id


def test_dropped_athlete_sends_baselines_and_testing_comparison(tmp_path, monkeypatch, fake_api):
    client, athlete_id, team_id = boot_athlete(tmp_path, monkeypatch, fake_api)
    assert client.get('/api/v2/ai-analysis/subject', params={'type': 'athlete', 'id': athlete_id}).json() == {'saved': False}
    response = client.post('/api/v2/ai-analysis/subject', json={'type': 'athlete', 'id': athlete_id})
    assert response.status_code == 200, response.text
    assert response.json()['source']['label'] == 'Athlete Rivera (athlete)'
    subject = FakeAPI.requests[-1]['body']['subject']
    assert subject['type'] == 'athlete' and subject['team_name'] == 'Atlas Sprint Lab'
    ev = subject['athletes'][0]['evidence']
    p300 = ev['facts'][0]
    assert (p300['z'], p300['position'], p300['baseline_values']) == (-16.0, 'below -3 SD', [13.1, 13.2, 13.3])
    assert ev['recording']['baseline_sessions'] == 3
    assert ev['comparisons'][0]['within_3sd'] == 2
    assert client.get('/api/v2/ai-analysis/subject', params={'type': 'athlete', 'id': athlete_id}).json()['saved'] is True

    team = client.post('/api/v2/ai-analysis/subject', json={'type': 'team', 'id': team_id})
    assert team.status_code == 200
    assert [a['athlete_name'] for a in FakeAPI.requests[-1]['body']['subject']['athletes']] == ['Athlete Rivera']


def test_subject_without_comparison_is_reported(tmp_path, monkeypatch, fake_api):
    client, app, job_id = boot(tmp_path, monkeypatch, url=fake_api)
    with app.database() as db:
        athlete_id = db.execute('SELECT athlete_id FROM jobs WHERE id=?', (job_id,)).fetchone()[0]
    response = client.post('/api/v2/ai-analysis/subject', json={'type': 'athlete', 'id': athlete_id})
    assert response.status_code == 404 and 'baseline vs testing' in response.json()['detail']
    assert FakeAPI.requests == []
    assert client.post('/api/v2/ai-analysis/subject', json={'type': 'coach', 'id': athlete_id}).status_code == 422


def test_mock_handles_a_team_subject(tmp_path, monkeypatch):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
    import mock_ai_api
    from backend import ai_analysis
    ev = ai_analysis.report_evidence.build_evidence(ai_analysis.extract_report(COMPARISON)['text'])
    md = mock_ai_api.placeholder_analysis({'subject': {'type': 'team', 'athletes': [
        {'athlete_name': 'A One', 'evidence': ev}, {'athlete_name': 'B Two', 'evidence': ev}]}})
    assert '# A One' in md and '# B Two' in md and '[B Two:E1]' in md


def test_anthropic_provider_status_and_request(monkeypatch):
    """NEUROBUDDY_AI_PROVIDER=anthropic sends the report to Claude, without the raw HTML."""
    from backend import ai_analysis
    monkeypatch.setenv('NEUROBUDDY_AI_PROVIDER', 'anthropic')
    monkeypatch.delenv('NEUROBUDDY_AI_API_URL', raising=False)
    monkeypatch.delenv('NEUROBUDDY_AI_API_KEY', raising=False)
    assert ai_analysis.status()['configured'] is False
    monkeypatch.setenv('NEUROBUDDY_AI_API_KEY', 'sk-test')
    status = ai_analysis.status()
    assert status['configured'] is True and status['provider'] == 'Anthropic Claude API'
    assert 'sk-test' not in json.dumps(status)

    sent = {}

    class Block:
        type = 'text'
        text = ANALYSIS

    class Message:
        stop_reason = 'end_turn'
        model = 'claude-opus-5-5'
        id = 'msg_test'
        content = [Block()]

    class Stream:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def get_final_message(self):
            return Message()

    class FakeClient:
        def __init__(self, **kwargs):
            sent['client'] = kwargs
            self.beta = type('B', (), {'messages': type('M', (), {'stream': staticmethod(lambda **kw: sent.update(request=kw) or Stream())})()})()

    import anthropic
    monkeypatch.setattr(anthropic, 'Anthropic', FakeClient)
    result = ai_analysis.analyze_html(REPORT, {'kind': 'uploaded_file', 'filename': 'r.html', 'label': 'r.html'})
    assert result['analysis_markdown'] == ANALYSIS and result['model'] == 'claude-opus-5-5'
    req = sent['request']
    assert sent['client']['api_key'] == 'sk-test'
    assert req['model'] == 'claude-opus-5-5' and req['system'] == ai_analysis.SYSTEM_PROMPT
    content = req['messages'][0]['content']
    assert 'do not send' not in content[0]['text'] and '"html"' not in content[0]['text']
    assert 'p300_difference_peak_uv' in content[0]['text']
    assert content[-1] == {'type': 'image', 'source': {'type': 'base64', 'media_type': 'image/png', 'data': PNG}}


# ---------------------------------------------------------------- team of several athletes

def comparison_folder(folder, p300_test):
    folder.mkdir(parents=True, exist_ok=True)
    (folder / 'comparative_analysis.csv').write_text(CSV_HEADER +
        f'specialized.oddball.erp.p300_difference_peak_uv,µV,13.2,0.1,{p300_test},-16,,Abnormal,3,,,,\n'
        'shared.channel.Fz.theta_relative,ratio,0.03,0.001,0.031,1.0,,Within range,3,,,,\n', encoding='utf-8')
    (folder / 'baseline').mkdir(exist_ok=True)
    (folder / 'baseline' / 'longitudinal.json').write_text(json.dumps(LONGITUDINAL), encoding='utf-8')
    (folder / 'athlete_report.html').write_text('<html><body><h1>Neuro-buddy Athlete Comparative Report</h1>'
                                                + 'x' * 200000 + '</body></html>', encoding='utf-8')


def boot_team(tmp_path, monkeypatch, url, names=('Ana Lopez', 'Ben Cole', 'Cy Park'), without=('Dee Ray',)):
    """A team batch job with one longitudinal report per athlete, plus athletes that have none."""
    client, app, _ = boot(tmp_path, monkeypatch, url=url)
    with app.database() as db:
        team_id = db.execute('SELECT id FROM teams').fetchone()[0]
        db.execute('DELETE FROM jobs')
        db.execute('DELETE FROM athletes')
        ids = {}
        for name in list(names) + list(without):
            ids[name] = str(uuid4())
            db.execute('INSERT INTO athletes VALUES(?,?,?,?)', (ids[name], team_id, name, app.now()))
        job_id = str(uuid4())
        members = [{'athlete_id': ids[n], 'athlete_name': n, 'status': 'completed',
                    'relative_report': f'athletes/{ids[n]}/athlete_report.html', 'result': {'mode': 'athlete'}} for n in names]
        members += [{'athlete_id': ids[n], 'athlete_name': n, 'status': 'skipped', 'message': 'no baselines'} for n in without]
        db.execute('INSERT INTO jobs(id,mode,target_id,test_recording_id,created_at,updated_at,status,error,result,'
                   'result_dir,team_id,athlete_id) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                   (job_id, 'team', team_id, None, app.now(), app.now(), 'completed', None,
                    json.dumps({'mode': 'team', 'members': members}), job_id, team_id, None))
    for i, name in enumerate(names):
        comparison_folder(app.REPORTS / job_id / 'athletes' / ids[name], 11.0 + i)
    return client, app, team_id, job_id, ids


def test_team_sends_every_athletes_longitudinal_report(tmp_path, monkeypatch, fake_api):
    client, app, team_id, job_id, ids = boot_team(tmp_path, monkeypatch, fake_api)
    advanced = client.get('/api/v2/shelf-advanced').json()['teams'][0]
    assert sorted(a['name'] for a in advanced['athletes']) == ['Ana Lopez', 'Ben Cole', 'Cy Park']
    for athlete in advanced['athletes']:
        assert [h['path'] for r in athlete['reports'] for h in r['html_reports']] == [
            f'athletes/{athlete["id"]}/athlete_report.html']

    response = client.post('/api/v2/ai-analysis/subject', json={'type': 'team', 'id': team_id})
    assert response.status_code == 200, response.text
    result = response.json()
    # One request per athlete, each carrying that athlete's own longitudinal statistics.
    assert all(len(r['body']['subject']['athletes']) == 1 for r in FakeAPI.requests)
    sent = sorted((r['body']['subject']['athletes'][0]['athlete_name'],
                   r['body']['subject']['athletes'][0]['evidence']['facts'][0]['value'])
                  for r in FakeAPI.requests)
    assert sent == [('Ana Lopez', 11.0), ('Ben Cole', 12.0), ('Cy Park', 13.0)]
    assert result['athletes_sent'] == 3 and result['athletes_failed'] == []
    assert result['athletes_without_comparison'] == ['Dee Ray']
    md = result['analysis_markdown']
    assert [line for line in md.splitlines() if line.startswith('# ')] == ['# Ana Lopez', '# Ben Cole', '# Cy Park']
    assert 'No longitudinal report yet for: Dee Ray' in md
    assert result['source']['label'] == 'Atlas Sprint Lab (team, 3 athlete(s))'
    saved = client.get('/api/v2/ai-analysis/subject', params={'type': 'team', 'id': team_id}).json()
    assert saved['saved'] is True and saved['athletes_sent'] == 3
    # One separate, complete AI report per athlete, each also saved as that athlete's own result.
    reports = result['reports']
    assert [r['athlete_name'] for r in reports] == ['Ana Lopez', 'Ben Cole', 'Cy Park']
    for r in reports:
        assert r['analysis_markdown'] and r['created_at'] and r['disclaimer'] and r['athletes_sent'] == 1
        assert r['source']['kind'] == 'team_member' and r['source']['label'] == f"{r['athlete_name']} (Atlas Sprint Lab)"
        assert r['source']['id'] == ids[r['athlete_name']]
        own = client.get('/api/v2/ai-analysis/subject', params={'type': 'athlete', 'id': ids[r['athlete_name']]}).json()
        assert own['saved'] is True and own['analysis_markdown'] == r['analysis_markdown']


def test_team_keeps_the_athletes_that_succeed(tmp_path, monkeypatch, fake_api):
    client, app, team_id, _, _ = boot_team(tmp_path, monkeypatch, fake_api)
    from backend import ai_analysis

    def flaky(payload):
        name = payload['subject']['athletes'][0]['athlete_name']
        if name == 'Ben Cole':
            raise ai_analysis.AIRequestFailed('The AI analysis API returned HTTP 500.')
        return {'analysis_markdown': f'# {name}\n\n## Summary\nok', 'model': 'm', 'truncated': False}
    monkeypatch.setattr(ai_analysis, 'call_api', flaky)
    result = client.post('/api/v2/ai-analysis/subject', json={'type': 'team', 'id': team_id}).json()
    assert result['athletes_analyzed'] == ['Ana Lopez', 'Cy Park']
    assert result['athletes_failed'] == [{'athlete_name': 'Ben Cole', 'error': 'The AI analysis API returned HTTP 500.'}]
    assert result['analysis_markdown'].count('# Ana Lopez') == 1  # the model's own heading is not doubled
    assert 'Ben Cole was not analyzed' in result['analysis_markdown']

    def down(payload):
        raise ai_analysis.AIRequestFailed('down', 502)
    monkeypatch.setattr(ai_analysis, 'call_api', down)
    failed = client.post('/api/v2/ai-analysis/subject', json={'type': 'team', 'id': team_id})
    assert failed.status_code == 502 and failed.json()['detail'] == 'down'


def test_team_size_does_not_limit_the_request(tmp_path, monkeypatch, fake_api):
    """The size limit applies per athlete, so a large team is not refused."""
    client, app, team_id, _, _ = boot_team(tmp_path, monkeypatch, fake_api, names=[f'Athlete {i}' for i in range(8)], without=())
    from backend import ai_analysis
    with app.database() as db:
        subject, _ = app._subject('team', team_id)
    whole, one = len(json.dumps(subject)), max(len(json.dumps(a)) for a in subject['athletes'])
    monkeypatch.setenv('NEUROBUDDY_AI_MAX_CHARS', str((whole + one) // 2 if whole > 2 * one else one * 2))
    assert int(ai_analysis.settings()['max_chars']) < whole
    response = client.post('/api/v2/ai-analysis/subject', json={'type': 'team', 'id': team_id})
    assert response.status_code == 200, response.text
    assert response.json()['athletes_sent'] == 8 and len(FakeAPI.requests) == 8


def test_dropped_longitudinal_report_sends_its_statistics_not_the_html(tmp_path, monkeypatch, fake_api):
    client, app, team_id, job_id, ids = boot_team(tmp_path, monkeypatch, fake_api)
    # The page's text is over this limit (it used to answer 413); its statistics are well under it.
    monkeypatch.setenv('NEUROBUDDY_AI_MAX_CHARS', '100000')
    path = f'athletes/{ids["Ben Cole"]}/athlete_report.html'
    response = client.post('/api/v2/ai-analysis/report', json={'job_id': job_id, 'path': path})
    assert response.status_code == 200, response.text
    body = FakeAPI.requests[-1]['body']
    assert 'report' not in body
    assert [a['athlete_name'] for a in body['subject']['athletes']] == ['Ben Cole']
    assert body['subject']['team_name'] == 'Atlas Sprint Lab'
    assert body['subject']['athletes'][0]['evidence']['facts'][0]['value'] == 12.0
    assert response.json()['source']['label'] == 'Ben Cole · Longitudinal report'
    saved = client.get('/api/v2/ai-analysis/report', params={'job_id': job_id, 'path': path}).json()
    assert saved['saved'] is True


def test_mock_writes_recommendations_tied_to_each_out_of_range_finding(tmp_path, monkeypatch):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
    import mock_ai_api
    from backend import ai_analysis
    folder = tmp_path / 'one'
    comparison_folder(folder, 11.6)
    ev = ai_analysis.athlete_evidence(folder)
    md = mock_ai_api.placeholder_analysis({'subject': {'type': 'athlete', 'athletes': [{'athlete_name': 'A', 'evidence': ev}]}})
    recs = md.split('## Recommendations')[1].split('## Suggested follow-up')[0]
    assert '| Evidence | Finding | May be related to | Recommendation |' in recs
    assert '| E1 | oddball p300 difference peak uv down (z -16.00) |' in recs
    assert 'lighter or shorter session' in recs and 'sleep extension' in recs
