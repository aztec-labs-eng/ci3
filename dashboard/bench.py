"""Benchmark series on the dashboard.

ci3's bench_upload keeps one series per repository and dashboard section in S3, at
bench/<org>/<repo>/<section>/data.json: gzip-encoded JSON in the window.BENCHMARK_DATA shape of
github-action-benchmark. The sections are the CI-run sections (the default branch, "prs", ...).

  GET /bench/<org>/<repo>/<section>            the charts.
  GET /bench/<org>/<repo>/<section>/timeline   the memory timeline of one commit's stacked chart.
  GET /bench/<org>/<repo>/<section>/data.json  the series, passed through still gzip-encoded.
"""
import json
import re
import threading
import time
from pathlib import Path

from botocore.exceptions import ClientError
from flask import Response, abort

S3_PREFIX = "bench/"
S3_SUFFIX = "/data.json"
# org/repo/section. Also what keeps the dashboard safe to embed in the pages' script.
DASHBOARD = re.compile(r"[A-Za-z0-9._-]+/[A-Za-z0-9._-]+/[A-Za-z0-9._-]+")
DATA_TTL = 30
LISTING_TTL = 300

_PAGES = Path(__file__).resolve().parent / "bench"


def _page(name, dashboard):
    html = (_PAGES / name).read_text()
    return html.replace('"__BENCH_DASHBOARD__"', json.dumps(dashboard))


def _check(dashboard):
    if not DASHBOARD.fullmatch(dashboard):
        abort(404)


def register(app, protect, s3, bucket):
    """Adds the /bench routes. Returns list_dashboards(): the org/repo/section series in S3."""
    lock = threading.Lock()
    data_cache = {}  # dashboard -> (fetched at, gzip body)
    listing_cache = {"at": 0.0, "dashboards": []}

    def route(rule):
        def deco(fn):
            return app.route(rule)(protect(fn))
        return deco

    @route("/bench/<path:dashboard>/data.json")
    def bench_data(dashboard):
        _check(dashboard)
        now = time.time()
        with lock:
            cached = data_cache.get(dashboard)
        if cached and now - cached[0] < DATA_TTL:
            body = cached[1]
        else:
            try:
                obj = s3.get_object(Bucket=bucket, Key=S3_PREFIX + dashboard + S3_SUFFIX)
            except ClientError as e:
                if e.response["Error"]["Code"] in ("NoSuchKey", "404"):
                    abort(404)
                raise
            body = obj["Body"].read()
            with lock:
                data_cache[dashboard] = (now, body)
        return Response(body, mimetype="application/json",
                        headers={"Content-Encoding": "gzip", "Cache-Control": f"max-age={DATA_TTL}"})

    @route("/bench/<path:dashboard>/timeline")
    def bench_timeline(dashboard):
        _check(dashboard)
        return _page("timeline.html", dashboard)

    @route("/bench/<path:dashboard>")
    def bench_page(dashboard):
        _check(dashboard)
        return _page("index.html", dashboard)

    def list_dashboards():
        now = time.time()
        with lock:
            if now - listing_cache["at"] < LISTING_TTL:
                return listing_cache["dashboards"]
        found = []
        for page in s3.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix=S3_PREFIX):
            for obj in page.get("Contents", []):
                key = obj["Key"]
                if key.endswith(S3_SUFFIX):
                    dashboard = key[len(S3_PREFIX):-len(S3_SUFFIX)]
                    if DASHBOARD.fullmatch(dashboard):
                        found.append(dashboard)
        found.sort()
        with lock:
            listing_cache.update(at=now, dashboards=found)
        return found

    return list_dashboards
