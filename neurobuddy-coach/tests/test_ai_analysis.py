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


def test_advanced_index_lists_draggable_html_reports(tmp_path, monkeypatch):
    client, _, job_id = boot(tmp_path, monkeypatch)
    athlete = client.get('/api/v2/shelf-advanced').json()['teams'][0]['athletes'][0]
    assert athlete['reports'][0]['html_reports'] == [{'job_id': job_id, 'path': 'report.html', 'label': 'Single EEG report'}]


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
    for topic in ('overtraining', 'stress', 'fatigue', 'sleep deprivation'):
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
