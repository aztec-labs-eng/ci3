#!/usr/bin/env python3
"""Tests for the dashboard's rendering and auth.

  python3 rk_test.py
"""
import base64
import unittest
from unittest import mock

import rk
from rk import ansi_to_html
from rk_core import hyperlink


class FakeRedis:
    def __init__(self):
        self.d = {}

    def get(self, k):
        return self.d.get(k)

    def set(self, k, v, ex=None, nx=False):
        if not (nx and k in self.d):
            self.d[k] = v

    def incr(self, k):
        self.d[k] = int(self.d.get(k, 0)) + 1
        return self.d[k]


def basic(user, password):
    return {'Authorization': 'Basic ' + base64.b64encode(f'{user}:{password}'.encode()).decode()}


class AuthTest(unittest.TestCase):
    def setUp(self):
        for p in (mock.patch.object(rk, 'r', FakeRedis()),
                  mock.patch.object(rk, 'DASHBOARD_PASSWORD', 'right'),
                  mock.patch.object(rk, 'root', return_value='ok')):
            p.start()
            self.addCleanup(p.stop)
        self.client = rk.app.test_client()

    def get(self, headers=None, ip='1.2.3.4'):
        return self.client.get('/', headers={'X-Forwarded-For': ip, **(headers or {})}).status_code

    def test_password_required(self):
        self.assertEqual(self.get(), 401)
        self.assertEqual(self.get(basic('aztec', 'wrong')), 401)
        self.assertEqual(self.get(basic('aztec', 'right')), 200)

    def test_no_password_configured_refuses_everyone(self):
        with mock.patch.object(rk, 'DASHBOARD_PASSWORD', ''):
            self.assertEqual(self.get(basic('aztec', '')), 401)

    def test_repeated_failures_block_that_client_only(self):
        for _ in range(rk.AUTH_FAIL_LIMIT):
            self.assertEqual(self.get(basic('aztec', 'wrong')), 401)
        self.assertEqual(self.get(basic('aztec', 'right')), 429)
        self.assertEqual(self.get(basic('aztec', 'right'), ip='5.6.7.8'), 200)

    def test_requests_without_credentials_are_not_counted(self):
        for _ in range(rk.AUTH_FAIL_LIMIT + 5):
            self.assertEqual(self.get(), 401)
        self.assertEqual(self.get(basic('aztec', 'right')), 200)


class AnsiToHtmlTest(unittest.TestCase):
    def test_relative_links_keep_their_target(self):
        html = ansi_to_html(hyperlink('/section/next', 'next') + ' ' + hyperlink('/1757000000000', 'log'))
        self.assertIn('<a href="/section/next">next</a>', html)
        self.assertIn('<a href="/1757000000000">log</a>', html)

    def test_absolute_links_keep_their_target(self):
        html = ansi_to_html(hyperlink('https://github.com/x/y/pull/1', 'pr') + ' https://example.com/a')
        self.assertIn('<a href="https://github.com/x/y/pull/1">pr</a>', html)
        self.assertIn('<a href="https://example.com/a">https://example.com/a</a>', html)

    def test_unsafe_link_targets_are_dropped(self):
        html = ansi_to_html(hyperlink('javascript:alert(1)', 'x') + hyperlink('//evil.example/a', 'y'))
        self.assertNotIn('javascript:', html)
        self.assertNotIn('evil.example', html)


class BenchListingTest(unittest.TestCase):
    def test_series_are_grouped_by_repo_and_linked(self):
        series = ['acme/widgets/main', 'acme/widgets/prs', 'zeta/app/main']
        with mock.patch.object(rk, 'list_bench_dashboards', lambda: series):
            html = ansi_to_html(rk.bench_listing())
        self.assertIn('<a href="/bench/acme/widgets/main">main</a>', html)
        self.assertIn('<a href="/bench/acme/widgets/prs">prs</a>', html)
        self.assertIn('<a href="/bench/zeta/app/main">main</a>', html)
        self.assertLess(html.index('acme/widgets'), html.index('zeta/app'))

    def test_no_series_adds_nothing(self):
        with mock.patch.object(rk, 'list_bench_dashboards', lambda: []):
            self.assertEqual(rk.bench_listing(), '')


if __name__ == '__main__':
    unittest.main()
