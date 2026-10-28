from flask import Flask, render_template_string, request, Response, redirect
from flask_compress import Compress
from flask_httpauth import HTTPBasicAuth
import boto3
from botocore.config import Config
from botocore.exceptions import ClientError
import gzip
import json
import os
import re
import requests
import shlex
import threading
import time as _time
import uuid
from ansi2html import Ansi2HTMLConverter
from pathlib import Path

# Import core rendering logic
from rk_core import (
    YELLOW, BLUE, GREEN, RED, PURPLE, BOLD, RESET,
    LINK_OPEN, hyperlink, r, get_section_data, get_list_as_string
)
S3_LOGS_BUCKET = os.getenv('S3_LOGS_BUCKET', 'aztec-ci-artifacts')
S3_LOGS_PREFIX = os.getenv('S3_LOGS_PREFIX', 'logs')

_s3 = boto3.client('s3', region_name='us-east-2', config=Config(connect_timeout=10, read_timeout=60, retries={'max_attempts': 3}))
DASHBOARD_PASSWORD = os.getenv('DASHBOARD_PASSWORD', '')

app = Flask(__name__)
Compress(app)
auth = HTTPBasicAuth()

# Conditional auth decorator - only require auth if password is set
def optional_auth(f):
    if DASHBOARD_PASSWORD:
        return auth.login_required(f)
    return f


def read_from_s3(key):
    """Read log from S3 (fallback when Redis and disk both miss)."""
    try:
        prefix = key[:4]
        s3_key = f"{S3_LOGS_PREFIX}/{prefix}/{key}.log.gz"
        obj = _s3.get_object(Bucket=S3_LOGS_BUCKET, Key=s3_key)
        return gzip.decompress(obj['Body'].read()).decode('utf-8', errors='replace')
    except ClientError as e:
        if e.response['Error']['Code'] != 'NoSuchKey':
            print(f"S3 error reading {key}: {e}")
    except Exception as e:
        print(f"Error reading from S3: {e}")
    return None

def read_breakdown_from_s3(runtime, flow_name, sha):
    """Read benchmark breakdown from S3."""
    breakdown_name = f"{runtime}-{flow_name}-{sha}"
    s3_prefix = f"{S3_LOGS_PREFIX}/bench/bb-breakdown"

    # Exact match
    try:
        s3_key = f"{s3_prefix}/{breakdown_name}.log.gz"
        obj = _s3.get_object(Bucket=S3_LOGS_BUCKET, Key=s3_key)
        return gzip.decompress(obj['Body'].read()).decode('utf-8', errors='replace')
    except ClientError as e:
        if e.response['Error']['Code'] != 'NoSuchKey':
            print(f"S3 error reading breakdown {breakdown_name}: {e}")
    except Exception as e:
        print(f"Error reading breakdown from S3: {e}")

    # Prefix search (SHA may be a prefix)
    try:
        list_prefix = f"{s3_prefix}/{breakdown_name}"
        paginator = _s3.get_paginator('list_objects_v2')
        for page in paginator.paginate(Bucket=S3_LOGS_BUCKET, Prefix=list_prefix):
            for obj in page.get('Contents', []):
                key = obj['Key']
                if key.endswith('.log.gz'):
                    try:
                        resp = _s3.get_object(Bucket=S3_LOGS_BUCKET, Key=key)
                        return gzip.decompress(resp['Body'].read()).decode('utf-8', errors='replace')
                    except Exception:
                        pass
    except Exception as e:
        print(f"S3 prefix search failed for breakdown {breakdown_name}: {e}")

    return None

@auth.verify_password
def verify_password(username, password):
    if username == "aztec" and password == DASHBOARD_PASSWORD:
        return username
    return None

_github_status_cache = {"status": None, "ts": 0}
_github_status_lock = threading.Lock()

def convert_to_ocs8(text):
    # Replace URLs not already part of an OCS8 link using negative lookbehind.
    pattern = r'(?<!\x1b\]8;;)(https?://[\w_.\-/:?=&%#+@~]+)'
    def replace_link(match):
        url = match.group(0)
        return hyperlink(url, url)
    return re.sub(pattern, replace_link, text)

# ansi2html >= 1.9.4 rewrites any link target without a URL scheme to "#", which would break every
# same-site link. Route them through a placeholder origin and strip it once converted.
_SAME_SITE_ORIGIN = 'http://same-site.invalid'
_SAME_SITE_LINK = re.compile(r'\x1b\]8;;(?=/(?!/))')

def ansi_to_html(text):
    text = convert_to_ocs8(text)
    text = _SAME_SITE_LINK.sub(lambda _: LINK_OPEN + _SAME_SITE_ORIGIN, text)
    conv = Ansi2HTMLConverter(inline=True)
    html = conv.convert(text, full=False)
    return html.replace(f'href="{_SAME_SITE_ORIGIN}/', 'href="/')

def update_status(offset: int, filter_str: str, filter_prop: str) -> None:
    ga_status = get_github_actions_status()
    return (
        f"{BOLD}{BLUE}{hyperlink('/', 'AZTEC LABS CI SYSTEM')}{RESET}: "
        f"(offset: {offset}) (filter: {filter_str or 'unset'}) (filter_prop: {filter_prop or 'unset'} [status,name,author,msg]) ({ga_status})\n"
    )

def get_github_actions_status():
    # Cache for 60 seconds
    import time
    now = time.time()
    ga_url='https://githubstatus.com'
    with _github_status_lock:
        if _github_status_cache["status"] and now - _github_status_cache["ts"] < 60:
            return _github_status_cache["status"]
        try:
            resp = requests.get("https://www.githubstatus.com/api/v2/components.json", timeout=2)
            resp.raise_for_status()
            data = resp.json()
            for comp in data.get("components", []):
                if comp.get("name", "").lower() == "actions":
                    status = comp.get("status", "")
                    if status in ("operational", "none"):
                        result = f"Github Actions: {GREEN}{hyperlink(ga_url, 'NOMINAL')}{RESET}"
                    else:
                        result = f"Github Actions: {RED}{hyperlink(ga_url, 'DEGRADED')}{RESET}"
                    _github_status_cache["status"] = result
                    _github_status_cache["ts"] = now
                    return result
        except Exception:
            result = f"Github Actions: {YELLOW}UNKNOWN{RESET}"
            _github_status_cache["status"] = result
            _github_status_cache["ts"] = now
            return result

def list_dashboards() -> list[str]:
    """Live dashboards from the self-registering `ci-run-sections` index.

    Each member is an "<org>/<repo>/<section>" string written by log_ci_run. A
    section ZSET carries a sliding 90-day TTL, so one that has expired (no runs in
    90 days) is retired: we drop it from the index on read. O(sections), no scan.
    """
    live = []
    for raw in r.smembers('ci-run-sections'):
        name = raw.decode() if isinstance(raw, bytes) else raw
        if r.exists('ci-run-' + name):
            live.append(name)
        else:
            r.srem('ci-run-sections', raw)
    return sorted(live)


def _split_dashboard(d: str) -> tuple[str, str]:
    """'org/repo/section' -> ('org/repo', 'section'); legacy unscoped -> ('', d)."""
    parts = d.split('/')
    if len(parts) >= 3:
        return '/'.join(parts[:-1]), parts[-1]
    return '', d


def root() -> str:
    out = update_status(0, '', '') + "\nCI runs:\n\n"

    grouped: dict[str, list[tuple[str, str]]] = {}
    for d in list_dashboards():
        repo, section = _split_dashboard(d)
        grouped.setdefault(repo, []).append((section, d))

    if not grouped:
        out += f"{YELLOW}(no active dashboards yet){RESET}\n"
    for repo in sorted(grouped):
        if repo:
            out += f"{BOLD}{repo}{RESET}\n"
        for section, d in sorted(grouped[repo]):
            out += f"  {YELLOW}{hyperlink('/section/' + d, section)}{RESET}\n"
        out += "\n"

    out += (
        f"Benchmarks:\n"
        f"\n{YELLOW}"
        f"{hyperlink('/chonk-breakdowns', 'chonk breakdowns')}\n"
        f"{RESET}"
    )
    return out

def section_view(section: str) -> str:
    offset = int(request.args.get('offset', 0))
    limit = int(request.args.get('limit', 50))
    filter_str = request.args.get('filter', default='', type=str)
    filter_prop = request.args.get('filter_prop', default='', type=str)

    lines = update_status(offset, filter_str, filter_prop)
    lines += "\n"
    lines += f"Last {limit} ci runs on {section}:\n\n"
    lines += get_section_data(section, offset, limit, filter_str, filter_prop)
    return lines

TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <title>ACI &#183; {{ filter_str|default('') }}</title>
    <style>
        body {
            background-color: black;
            color: #cccccc;
            font-family: monospace;
            padding: 10px;
        }
        .output {
            white-space: pre;
        }
        a { color: inherit; text-decoration: none; }
        ::-webkit-scrollbar {
            width: 6px;
            height: 6px;
        }
        ::-webkit-scrollbar-track {
            background: #000000;
        }
        ::-webkit-scrollbar-thumb {
            background: #444;
            border-radius: 4px;
        }
        ::-webkit-scrollbar-thumb:hover {
            background: #555;
        }
        ::-webkit-scrollbar:horizontal {
            display: none;
        }
    </style>
    <script>
        var follow = "{{ follow|default('bottom') }}";
        if (location.pathname === '/') {
            setInterval(() => {
                if (document.visibilityState === 'visible' && window.getSelection().toString() === '') {
                    fetch(location.href)
                        .then(response => {
                            if (!response.ok) throw new Error(response.status);
                            return response.text();
                        })
                        .then(html => {
                            const parser = new DOMParser();
                            const newDoc = parser.parseFromString(html, 'text/html');
                            document.body.innerHTML = newDoc.body.innerHTML;
                        })
                        .catch(() => {});
                }
            }, 5000);
        } else {
            if ('scrollRestoration' in history) {
                history.scrollRestoration = 'manual';
            }

            const key = "scrollPosition:" + window.location.href;

            window.addEventListener('beforeunload', () => {
                sessionStorage.setItem(key, window.scrollY);
            });

            window.addEventListener('load', () => {
                let navType = '';
                if (performance.getEntriesByType("navigation").length > 0) {
                    navType = performance.getEntriesByType("navigation")[0].type;
                } else {
                    navType = performance.navigation && performance.navigation.type === 1 ? 'reload' : 'navigate';
                }
                if (navType === 'navigate' || navType === 'reload') {
                    if (follow === 'bottom') {
                        window.scrollTo(0, document.body.scrollHeight);
                    }
                } else {
                    const pos = sessionStorage.getItem(key);
                    if (pos !== null) {
                        window.scrollTo(0, parseInt(pos, 10));
                    }
                }
            });

            const refresh_func = () => {
                if (follow === 'bottom') {
                    if (document.visibilityState === 'visible' && window.innerHeight + window.scrollY >= document.body.offsetHeight && window.getSelection().toString() === '') {
                        const startTime = Date.now();
                        fetch(location.href)
                            .then(response => {
                                if (!response.ok) throw new Error(response.status);
                                return response.text();
                            })
                            .then(html => {
                                const parser = new DOMParser();
                                const newDoc = parser.parseFromString(html, 'text/html');
                                const contentText = newDoc.body.textContent || '';
                                const isError = contentText.includes('Key not found') ||
                                               contentText.includes('Failed to decompress') ||
                                               contentText.includes('List is empty or key not found') ||
                                               contentText.includes('Error retrieving list');
                                document.body.innerHTML = newDoc.body.innerHTML;
                                window.scrollTo(window.scrollX, document.body.scrollHeight);
                                return isError;
                            })
                            .catch(() => false)
                            .then(isError => {
                                const responseTime = Date.now() - startTime;
                                if (document.body.scrollHeight < 500000 && responseTime <= 3000 && !isError) {
                                    setTimeout(refresh_func, 5000);
                                }
                            });
                    } else {
                        if (document.body.scrollHeight < 500000) {
                            setTimeout(refresh_func, 5000);
                        }
                    }
                } else {
                    if (document.visibilityState === 'visible' && window.scrollY === 0 && window.getSelection().toString() === '') {
                        const startTime = Date.now();
                        fetch(location.href)
                            .then(response => {
                                if (!response.ok) throw new Error(response.status);
                                return response.text();
                            })
                            .then(html => {
                                const parser = new DOMParser();
                                const newDoc = parser.parseFromString(html, 'text/html');
                                const contentText = newDoc.body.textContent || '';
                                const isError = contentText.includes('Key not found') ||
                                               contentText.includes('Failed to decompress') ||
                                               contentText.includes('List is empty or key not found') ||
                                               contentText.includes('Error retrieving list');
                                document.body.innerHTML = newDoc.body.innerHTML;
                                return isError;
                            })
                            .catch(() => false)
                            .then(isError => {
                                const responseTime = Date.now() - startTime;
                                if (document.body.scrollHeight < 500000 && responseTime <= 3000 && !isError) {
                                    setTimeout(refresh_func, 5000);
                                }
                            });
                    } else {
                        if (document.body.scrollHeight < 500000) {
                            setTimeout(refresh_func, 5000);
                        }
                    }
                }
            }
            setTimeout(refresh_func, 5000);
        }
    </script>
</head>
<body>
    <div class="output">{{ value|safe }}</div>
</body>
</html>
"""

@app.route('/')
@optional_auth
def show_root():
    return render_template_string(
        TEMPLATE,
        value=ansi_to_html(root()),
        filter_str='',
        filter_prop=''
    )

# <path:section> so org/repo/section dashboards (with slashes) route correctly —
# same reason as /list/<path:key> below (WSGI decodes %2F to / before routing).
@app.route('/section/<path:section>')
@optional_auth
def show_section(section):
    return render_template_string(
        TEMPLATE,
        value=ansi_to_html(section_view(section)),
        filter_str=request.args.get('filter', default='', type=str),
        filter_prop=request.args.get('filter_prop', default='', type=str),
        follow='top'
    )

# <path:key> (not <key>) so branch-qualified history keys like
# `history_<hash>_merge-train/spartan` route correctly. WSGI decodes %2F to /
# in PATH_INFO before Flask routes, so percent-encoding can't rescue a plain
# <key> converter — only <path:key> matches segments containing /.
@app.route('/list/<path:key>')
@optional_auth
def get_list(key):
    value = get_list_as_string(key)
    follow = request.args.get('follow', 'top')
    return render_template_string(TEMPLATE, value=ansi_to_html(value), follow=follow, filter_str='', filter_prop='')

@app.route('/chonk-breakdowns')
@optional_auth
def chonk_breakdowns():
    """Serve the chonk breakdowns viewer page."""
    breakdown_html_path = Path('chonk-breakdowns/breakdown-viewer.html')
    if breakdown_html_path.exists():
        with breakdown_html_path.open('r') as f:
            return f.read()
    else:
        return "Breakdown viewer not found", 404

@app.route('/api/breakdown/flows')
@optional_auth
def list_available_flows():
    """API endpoint to list available breakdown flows from S3, filtered by runtime and SHA."""
    runtime = request.args.get('runtime')
    sha = request.args.get('sha')
    flows = set()
    s3_prefix = f"{S3_LOGS_PREFIX}/bench/bb-breakdown/"

    try:
        paginator = _s3.get_paginator('list_objects_v2')
        for page in paginator.paginate(Bucket=S3_LOGS_BUCKET, Prefix=s3_prefix):
            for obj in page.get('Contents', []):
                filename = obj['Key'][len(s3_prefix):]
                if not filename.endswith('.log.gz'):
                    continue
                parts = filename.replace('.log.gz', '').split('-', 1)
                if len(parts) == 2:
                    file_runtime = parts[0]
                    rest = parts[1]
                    last_dash = rest.rfind('-')
                    if last_dash != -1:
                        flow_name = rest[:last_dash]
                        file_sha = rest[last_dash + 1:]

                        if runtime and file_runtime != runtime:
                            continue
                        if sha and not file_sha.startswith(sha):
                            continue

                        flows.add(flow_name)
    except Exception as e:
        print(f"Error listing flows from S3: {e}")

    return Response(json.dumps(sorted(list(flows))), mimetype='application/json')

@app.route('/api/breakdown/<runtime>/<flow_name>/<sha>')
@optional_auth
def get_breakdown(runtime, flow_name, sha):
    """API endpoint to fetch breakdown JSON."""
    breakdown_data = read_breakdown_from_s3(runtime, flow_name, sha)
    if breakdown_data:
        return Response(breakdown_data, mimetype='application/json')

    return Response('{"error": "Breakdown not found"}', mimetype='application/json', status=404)

# The ci3 server API (ci3/CI3_SERVER_API.md in aztec-packages): what the ci3 scripts talk to.
import ci3_api
ci3_api.register(app, optional_auth, _s3, S3_LOGS_BUCKET, S3_LOGS_PREFIX, DASHBOARD_PASSWORD)

@app.route('/<key>')
@optional_auth
def get_value(key):
    # Check if raw text format is requested
    raw_text = key.endswith('.txt')
    if raw_text:
        key = key[:-4]  # Remove .txt extension

    try:
        value = r.get(key)
    except Exception:
        value = None
    if value is None:
        value = read_from_s3(key)
    if value is None:
        value = "Key not found"
    elif isinstance(value, bytes):
        # Redis returns raw bytes — decompress if gzip.
        try:
            if value.startswith(b"\x1f\x8b"):
                value = gzip.decompress(value).decode()
            else:
                value = value.decode()
        except Exception:
            value = "Failed to decompress"

    # Return raw text if .txt extension was used
    if raw_text:
        return Response(value, mimetype='text/plain')

    return render_template_string(TEMPLATE, value=ansi_to_html(value), filter_str='', filter_prop='')

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8080)
