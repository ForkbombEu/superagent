"""Durable recovery for a lost UAT background-job wake-up."""
import importlib.machinery
import importlib.util
import os
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SHA = 'a' * 40


def load_recovery():
    path = os.path.join(ROOT, 'scripts', 'superagent-uat-recovery')
    loader = importlib.machinery.SourceFileLoader('superagent_uat_recovery', path)
    module = importlib.util.module_from_spec(importlib.util.spec_from_loader('superagent_uat_recovery', loader))
    loader.exec_module(module)
    return module


class RecoveryTest(unittest.TestCase):
    def setUp(self):
        self.module = load_recovery()
        self.posts = []
        self.module.load_config = lambda: ('http://gate.test', 'token')
        self.module.log = lambda _: None

    def sweep(self, ticket, run):
        def call(method, path):
            if method == 'GET':
                self.assertEqual(path, 'tickets?state=person&limit=1000')
                return {'items': [ticket]}
            self.posts.append((method, path))
            return {}
        self.module.call = call
        self.module.gate_run = lambda config, repository, number, sha: run
        self.module.sweep('owner/repository', {}, {})

    def ticket(self, summary):
        return {'id': 'ticket-1', 'title': 'ticket', 'summary': summary, 'updatedAt': 'now',
                'action': 'blocked', 'actions': {'retry': True}}

    def test_completed_uat_retries_the_waiting_ticket(self):
        self.sweep(self.ticket(f'Waiting for UAT: PR #17 at {SHA}'), {'state': 'COMPLETE', 'verdict': 'PASS'})
        self.assertEqual(self.posts, [('POST', 'tickets/ticket-1/retry')])

    def test_running_uat_does_not_retry_the_ticket(self):
        self.sweep(self.ticket(f'Waiting for UAT: PR #17 at {SHA}'), {'state': 'RUNNING', 'verdict': None})
        self.assertEqual(self.posts, [])

    def test_legacy_wait_report_resolves_its_single_open_pull_request(self):
        self.module.legacy_pr = lambda repository, sha: (17, SHA)
        self.sweep(self.ticket('The ticket is waiting on a fresh UAT run for head aaaaaaa; its worker continues when it lands.'),
                   {'state': 'COMPLETE', 'verdict': 'PASS'})
        self.assertEqual(self.posts, [('POST', 'tickets/ticket-1/retry')])

    def test_nonblocked_tickets_are_not_retried(self):
        ticket = self.ticket(f'Waiting for UAT: PR #17 at {SHA}')
        ticket['action'] = 'completed'
        self.sweep(ticket, {'state': 'COMPLETE', 'verdict': 'PASS'})
        self.assertEqual(self.posts, [])

    def test_recovery_retry_budget_is_per_ticket_and_head(self):
        ticket = self.ticket(f'Waiting for UAT: PR #17 at {SHA}')
        self.module.call = lambda method, path: {'items': [ticket]} if method == 'GET' else self.posts.append((method, path)) or {}
        self.module.gate_run = lambda config, repository, number, sha: {'state': 'COMPLETE', 'verdict': 'PASS'}
        retries = {}
        for _ in range(self.module.MAX_RETRIES + 1):
            self.module.sweep('owner/repository', retries, {})
        self.assertEqual(self.posts, [('POST', 'tickets/ticket-1/retry')] * self.module.MAX_RETRIES)


if __name__ == '__main__':
    unittest.main()
