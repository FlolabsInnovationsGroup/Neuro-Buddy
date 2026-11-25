"""The optional shared-password gate used for hosted deployments."""
import base64
import importlib
import sys

from fastapi.testclient import TestClient


def _load(tmp_path, monkeypatch, password):
    monkeypatch.setenv('NEUROBUDDY_DATA_DIR', str(tmp_path / 'db'))
    if password is None:
        monkeypatch.delenv('NEUROBUDDY_ACCESS_PASSWORD', raising=False)
    else:
        monkeypatch.setenv('NEUROBUDDY_ACCESS_PASSWORD', password)
    sys.modules.pop('backend.app', None)
    return TestClient(importlib.import_module('backend.app').app)


def _basic(user, password):
    return {'Authorization': 'Basic ' + base64.b64encode(f'{user}:{password}'.encode()).decode()}


def test_no_password_keeps_local_behaviour(tmp_path, monkeypatch):
    cli = _load(tmp_path, monkeypatch, None)
    assert cli.get('/').status_code == 200
    assert cli.get('/api/v2/library').status_code == 200


def test_password_gate(tmp_path, monkeypatch):
    cli = _load(tmp_path, monkeypatch, 's3cret')
    assert cli.get('/api/v2/health').status_code == 200
    for path in ('/', '/api/v2/library', '/v2/app.js', '/api/v2/jobs'):
        r = cli.get(path)
        assert r.status_code == 401, path
        assert r.headers['www-authenticate'].startswith('Basic')
    assert cli.get('/api/v2/library', headers=_basic('coach', 'wrong')).status_code == 401
    assert cli.get('/api/v2/library', headers={'Authorization': 'Basic !!!'}).status_code == 401
    assert cli.get('/api/v2/library', headers=_basic('anyone', 's3cret')).status_code == 200
    assert cli.post('/api/v2/teams', json={'name': 'T'}).status_code == 401
    assert cli.post('/api/v2/teams', json={'name': 'T'}, headers=_basic('c', 's3cret')).status_code == 201
    sys.modules.pop('backend.app', None)
