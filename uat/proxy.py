#!/usr/bin/env python3
"""Allowlisting HTTP egress proxy for the UAT sandbox network.

Usage: proxy.py --listen 0.0.0.0:3128 --allow api.deepseek.com:443 [--allow host:port ...]

The tester and the application under test sit on an internal Docker network
with no route out; this proxy, attached to that network and to an outside one,
is their only exit. It relays CONNECT tunnels and plain-HTTP requests to the
exact host:port pairs given with --allow by the UAT gate, and
refuses everything else, including any name that resolves to a loopback,
link-local (cloud metadata), multicast or unspecified address. Each decision
is logged on stdout, which the controller keeps as an artifact.
"""
import argparse
import ipaddress
import select
import socket
import socketserver
import sys
import urllib.parse

IDLE_SECONDS = 300
MAX_HEADER_BYTES = 65536


def log(message):
    print(message, flush=True)


def forbidden_address(address):
    ip = ipaddress.ip_address(address)
    return ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_unspecified


def endpoint(text, default_port):
    """(host, port) from 'host[:port]' or '[v6]:port'."""
    parsed = urllib.parse.urlsplit(f'//{text}')
    return (parsed.hostname or '').lower(), parsed.port or default_port


class Handler(socketserver.StreamRequestHandler):
    allowed = frozenset()
    # Unbuffered, so no bytes after the request head wait in a buffer the relay would skip.
    rbufsize = 0

    def reply(self, status, reason):
        self.wfile.write(f'HTTP/1.1 {status} {reason}\r\nContent-Length: 0\r\nConnection: close\r\n\r\n'.encode())

    def handle(self):
        head = b''
        while not head.endswith(b'\r\n\r\n'):
            line = self.rfile.readline(MAX_HEADER_BYTES)
            if not line or len(head) + len(line) > MAX_HEADER_BYTES:
                return
            head += line if line != b'\n' else b'\r\n'
        request_line, *header_lines = head.decode('latin-1').split('\r\n')
        try:
            method, target, version = request_line.split(' ')
        except ValueError:
            return self.reply(400, 'Bad Request')
        if method == 'CONNECT':
            host, port = endpoint(target, 443)
            path = None
        else:
            url = urllib.parse.urlsplit(target)
            if url.scheme != 'http' or not url.hostname:
                return self.reply(400, 'Bad Request')
            host, port = url.hostname.lower(), url.port or 80
            path = urllib.parse.urlunsplit(('', '', url.path or '/', url.query, ''))
        if (host, port) not in self.allowed:
            log(f'DENY {method} {host}:{port}')
            return self.reply(403, 'Forbidden')
        try:
            addresses = {info[4][0] for info in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)}
            if any(forbidden_address(address) for address in addresses):
                log(f'DENY {method} {host}:{port} resolves to a forbidden address')
                return self.reply(403, 'Forbidden')
            upstream = socket.create_connection((host, port), timeout=15)
        except OSError as error:
            log(f'FAIL {method} {host}:{port}: {error}')
            return self.reply(502, 'Bad Gateway')
        log(f'ALLOW {method} {host}:{port}')
        with upstream:
            if method == 'CONNECT':
                self.reply(200, 'Connection Established')
            else:
                kept = [line for line in header_lines if line and not line.lower().startswith(('proxy-', 'connection:'))]
                upstream.sendall('\r\n'.join([f'{method} {path} {version}', *kept, 'Connection: close', '', '']).encode('latin-1'))
            self.relay(upstream)

    def relay(self, upstream):
        client = self.connection
        sockets = [client, upstream]
        while True:
            readable, _, _ = select.select(sockets, [], [], IDLE_SECONDS)
            if not readable:
                return
            for source in readable:
                data = source.recv(65536)
                if not data:
                    return
                (upstream if source is client else client).sendall(data)


class Server(socketserver.ThreadingTCPServer):
    daemon_threads = True
    allow_reuse_address = True


def main():
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('--listen', default='0.0.0.0:3128')
    parser.add_argument('--allow', action='append', default=[], help='host:port to allow (repeatable)')
    options = parser.parse_args()
    Handler.allowed = frozenset(endpoint(item, 443) for item in options.allow)
    host, port = options.listen.rsplit(':', 1)
    with Server((host, int(port)), Handler) as server:
        log(f'listening on {options.listen}; allowed: {", ".join(sorted(f"{h}:{p}" for h, p in Handler.allowed)) or "nothing"}')
        server.serve_forever()


if __name__ == '__main__':
    sys.exit(main())
