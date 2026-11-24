"""Local stand-in for the FloLabs AI analysis API (placeholder).

Run it, then start Coach with NEUROBUDDY_AI_API_URL pointing at it:

    python tools/mock_ai_api.py                      # listens on 127.0.0.1:9100
    NEUROBUDDY_AI_API_URL=http://127.0.0.1:9100/analyze \
        .venv/bin/python -m uvicorn backend.app:app --host 127.0.0.1 --port 8765

It accepts the request described in docs/AI_ANALYSIS_API.md and answers with a
clearly labelled placeholder analysis. It performs no analysis of its own.
Replace NEUROBUDDY_AI_API_URL with the real endpoint when FloLabs supplies it.
"""
from __future__ import annotations

import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def placeholder_analysis(payload: dict) -> str:
    report = payload.get('report') or {}
    source = payload.get('source') or {}
    figures = report.get('figures') or []
    captions = '\n'.join(f'- Figure {f.get("index")}: {f.get("caption")}' for f in figures) or '- None'
    return f"""> PLACEHOLDER RESPONSE from tools/mock_ai_api.py. No AI model analyzed this report.

## Summary
The Neurobuddy request reached the placeholder API. When FloLabs supplies the real analysis API, its findings on overtraining, stress, fatigue and sleep deprivation will appear here.

## Data quality and context
- Report: {source.get('label') or source.get('filename') or 'HTML report'}
- Title: {report.get('title') or '(none)'}
- Text sent: {len(report.get('text') or ''):,} characters
- Raw HTML sent: {len(report.get('html') or ''):,} characters
- Figures sent: {len(figures)}

{captions}

## Findings
### Overtraining / under-recovery
Not analyzed (placeholder). Evidence rating: None.
### Stress / arousal
Not analyzed (placeholder). Evidence rating: None.
### Fatigue
Not analyzed (placeholder). Evidence rating: None.
### Sleep deprivation / sleepiness
Not analyzed (placeholder). Evidence rating: None.

## Limitations
This is a test response used to check the Coach integration.
"""


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
