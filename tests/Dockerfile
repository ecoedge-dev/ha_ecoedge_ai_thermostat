# Test runner for the integration (local Docker, same as CI's Python).
#   make test
FROM python:3.14-slim@sha256:35caba83af2fe6da9a5d922f9b0156f21d1246a48278c58a08bd253303ba42e8
WORKDIR /repo
COPY requirements_test.txt .
RUN pip install --no-cache-dir -r requirements_test.txt
