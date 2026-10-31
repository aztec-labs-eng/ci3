#!/usr/bin/env python3
"""Tests for the dashboard's benchmark pages against an in-memory S3.

  python3 bench_test.py
"""
import gzip
import io
import json
import unittest

from botocore.exceptions import ClientError
from flask import Flask

import bench

BUCKET = "aztec-ci-artifacts"
SERIES = {"lastUpdate": 1, "repoUrl": "https://github.com/acme/widgets", "entries": {"Benchmarks": []}}


class FakeS3:
    def __init__(self):
        self.objects, self.gets = {}, 0

    def get_object(self, Bucket, Key):
        self.gets += 1
        if (Bucket, Key) not in self.objects:
            raise ClientError({"Error": {"Code": "NoSuchKey"}}, "GetObject")
        return {"Body": io.BytesIO(self.objects[(Bucket, Key)])}

    def get_paginator(self, name):
        s3 = self

        class Paginator:
            def paginate(self, Bucket, Prefix):
                keys = sorted(k for (b, k) in s3.objects if b == Bucket and k.startswith(Prefix))
                return [{"Contents": [{"Key": k} for k in keys[:2]]}, {"Contents": [{"Key": k} for k in keys[2:]]}]
        return Paginator()


def make_app(s3):
    app = Flask(__name__)
    app.config["TESTING"] = True
    list_dashboards = bench.register(app, lambda f: f, s3, BUCKET)
    return app.test_client(), list_dashboards


class BenchTest(unittest.TestCase):
    def setUp(self):
        self.s3 = FakeS3()
        self.body = gzip.compress(json.dumps(SERIES).encode())
        self.s3.objects[(BUCKET, "bench/acme/widgets/main/data.json")] = self.body
        self.client, self.list_dashboards = make_app(self.s3)

    def test_page_embeds_its_dashboard(self):
        resp = self.client.get("/bench/acme/widgets/main")
        self.assertEqual(resp.status_code, 200)
        html = resp.get_data(as_text=True)
        self.assertIn('const dashboard = "acme/widgets/main";', html)
        self.assertNotIn("__BENCH_DASHBOARD__", html)

    def test_timeline_embeds_its_dashboard(self):
        resp = self.client.get("/bench/acme/widgets/main/timeline?commit=abc")
        self.assertEqual(resp.status_code, 200)
        self.assertIn('const dashboard = "acme/widgets/main";', resp.get_data(as_text=True))

    def test_malformed_dashboards_are_not_found(self):
        for path in ("/bench/acme/widgets", "/bench/acme/widgets/main/extra",
                     "/bench/acme/widgets/ma%22in", "/bench/acme/widgets/main%3Cscript%3E/data.json"):
            self.assertEqual(self.client.get(path).status_code, 404, path)

    def test_data_is_passed_through_gzip_encoded(self):
        resp = self.client.get("/bench/acme/widgets/main/data.json")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.headers["Content-Encoding"], "gzip")
        self.assertEqual(resp.mimetype, "application/json")
        self.assertEqual(json.loads(gzip.decompress(resp.get_data())), SERIES)

    def test_data_is_cached_briefly(self):
        self.client.get("/bench/acme/widgets/main/data.json")
        self.client.get("/bench/acme/widgets/main/data.json")
        self.assertEqual(self.s3.gets, 1)

    def test_missing_series_is_not_found(self):
        self.assertEqual(self.client.get("/bench/acme/widgets/prs/data.json").status_code, 404)

    def test_listing_finds_series_across_pages(self):
        for key in ("bench/acme/widgets/prs/data.json", "bench/zeta/app/main/data.json",
                    "bench/acme/widgets/notes.txt", "bench/too/deep/a/b/data.json"):
            self.s3.objects[(BUCKET, key)] = b""
        self.assertEqual(self.list_dashboards(),
                         ["acme/widgets/main", "acme/widgets/prs", "zeta/app/main"])


if __name__ == "__main__":
    unittest.main()
