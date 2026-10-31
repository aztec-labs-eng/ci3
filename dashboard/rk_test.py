#!/usr/bin/env python3
"""Tests for the dashboard's ANSI to HTML rendering.

  python3 rk_test.py
"""
import unittest
from unittest import mock

import rk
from rk import ansi_to_html
from rk_core import hyperlink


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
