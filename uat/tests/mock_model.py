#!/usr/bin/env python3
"""A scripted stand-in for the DeepSeek Messages API, for tests that run real DSH.

Usage: mock_model.py <port> <script.json> [<request log dir>]

DSH's DeepSeek provider posts Anthropic-style requests to
$DEEPSEEK_BASE_URL/v1/messages and reads server-sent events. This answers the
n-th model step of a session (n = assistant messages already in the request)
with step n of the script, so a test decides exactly which commands the tester
runs and what it finally answers, while DSH, its tools and its NDJSON stream
are the real ones:

  {"steps": [{"bash": "curl -sS -o artifacts/a.txt http://127.0.0.1:8000/"},
             {"final": "{...result JSON...}"}]}

A step may instead be {"status": 500} (an HTTP error) or carry "sleep": seconds
before answering. Steps past the end repeat the last one. The script is read
again for every request, so a test can rewrite it between runs.
"""
import json
import os
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class Handler(BaseHTTPRequestHandler):
    script_path = None
    log_dir = None
    counter = 0

    def log_message(self, *args):
        pass

    def do_GET(self):
        self.send_response(404)
        self.end_headers()

    def do_POST(self):
        request = json.loads(self.rfile.read(int(self.headers.get('content-length', 0))))
        if self.log_dir:
            Handler.counter += 1
            with open(os.path.join(self.log_dir, f'{Handler.counter:03d}.json'), 'w') as file:
                json.dump({'path': self.path, 'request': request}, file)
        with open(self.script_path) as file:
            steps = json.load(file)['steps']
        index = min(sum(message['role'] == 'assistant' for message in request['messages']), len(steps) - 1)
        step = steps[index]
        time.sleep(step.get('sleep', 0))
        if 'status' in step:
            self.send_response(step['status'])
            self.send_header('content-type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({'type': 'error', 'error': {'type': 'api_error', 'message': 'scripted'}}).encode())
            return
        if 'bash' in step:
            block = {'type': 'tool_use', 'id': f'toolu_{index}', 'name': 'bash', 'input': {}}
            arguments = {'command': step['bash'], 'description': 'Run a scripted test step'}
            delta = {'type': 'input_json_delta', 'partial_json': json.dumps(arguments)}
            stop = 'tool_use'
        else:
            block = {'type': 'text', 'text': ''}
            delta = {'type': 'text_delta', 'text': step['final']}
            stop = 'end_turn'
        self.send_response(200)
        self.send_header('content-type', 'text/event-stream')
        self.end_headers()
        usage = {'input_tokens': 10, 'output_tokens': 0}
        for event in (
            {'type': 'message_start', 'message': {'id': f'msg_{index}', 'type': 'message', 'role': 'assistant',
                                                  'model': request['model'], 'content': [], 'stop_reason': None,
                                                  'stop_sequence': None, 'usage': usage}},
            {'type': 'content_block_start', 'index': 0, 'content_block': block},
            {'type': 'content_block_delta', 'index': 0, 'delta': delta},
            {'type': 'content_block_stop', 'index': 0},
            {'type': 'message_delta', 'delta': {'stop_reason': stop, 'stop_sequence': None}, 'usage': {'output_tokens': 5}},
            {'type': 'message_stop'},
        ):
            self.wfile.write(f'event: {event["type"]}\ndata: {json.dumps(event)}\n\n'.encode())
            self.wfile.flush()


def main():
    port, Handler.script_path = int(sys.argv[1]), sys.argv[2]
    Handler.log_dir = sys.argv[3] if len(sys.argv) > 3 else None
    ThreadingHTTPServer(('0.0.0.0', port), Handler).serve_forever()


if __name__ == '__main__':
    main()
