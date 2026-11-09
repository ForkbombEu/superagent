#!/usr/bin/env python3
"""Sample application under test: a tiny records API.

POST /records {"name": "<non-empty>"} -> 201 {"id": n, "name": ...}; anything else -> 400.
GET /records/<id> -> 200 record or 404. GET /health -> 200.
With UAT_FORCE_500=1 (or a file named force-500 next to this script), POST
/records answers 500: the bug a UAT run must catch.
"""
import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

RECORDS = {}
HERE = os.path.dirname(os.path.abspath(__file__))


class Handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        print(f'{self.command} {self.path} -> {args[1] if len(args) > 1 else ""}', flush=True)

    def answer(self, status, body):
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header('content-type', 'application/json')
        self.send_header('content-length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path == '/health':
            return self.answer(200, {'status': 'ok'})
        if self.path.startswith('/records/') and self.path[9:].isdigit():
            record = RECORDS.get(int(self.path[9:]))
            return self.answer(200, record) if record else self.answer(404, {'error': 'not found'})
        self.answer(404, {'error': 'not found'})

    def do_POST(self):
        if self.path != '/records':
            return self.answer(404, {'error': 'not found'})
        if os.environ.get('UAT_FORCE_500') == '1' or os.path.exists(os.path.join(HERE, 'force-500')):
            return self.answer(500, {'error': 'internal error'})
        try:
            body = json.loads(self.rfile.read(int(self.headers.get('content-length', 0))))
            name = body['name']
            if not isinstance(name, str) or not name.strip():
                raise ValueError
        except (ValueError, KeyError, TypeError):
            return self.answer(400, {'error': 'name is required'})
        record = {'id': len(RECORDS) + 1, 'name': name}
        RECORDS[record['id']] = record
        self.answer(201, record)


if __name__ == '__main__':
    ThreadingHTTPServer(('127.0.0.1', int(sys.argv[1]) if len(sys.argv) > 1 else 3000), Handler).serve_forever()
