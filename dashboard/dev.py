"""The dashboard for local development, signed in as a dev user without oauth2-proxy in front."""
import os

os.environ.setdefault('OAUTH2_PROXY_EMAIL_DOMAINS', 'localhost')

from rk import app  # noqa: E402

_wsgi_app = app.wsgi_app


def _as_dev_user(environ, start_response):
    environ['HTTP_X_AUTH_REQUEST_EMAIL'] = 'dev@localhost'
    return _wsgi_app(environ, start_response)


app.wsgi_app = _as_dev_user
