#!/bin/bash

REDIS_HOST=localhost flask --app dev.py --debug run --port ${PORT:-8080}
