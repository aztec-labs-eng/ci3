#!/bin/bash

REDIS_HOST=localhost DASHBOARD_PASSWORD=${DASHBOARD_PASSWORD:-dev} flask --app rk.py --debug run --port ${PORT:-8080}
