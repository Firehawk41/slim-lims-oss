FROM python:3.11-slim

WORKDIR /app

# git: slim-data is installed from its GitHub repo (pyproject.toml).
RUN apt-get update && apt-get install -y --no-install-recommends git && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml ./
COPY slim_lab_portal ./slim_lab_portal
RUN pip install --no-cache-dir .

EXPOSE 8000

# Apply the portal's and slim-data's migrations, then serve. Proxy headers are trusted so url_for() builds
# https:// links behind Caddy / Render.
CMD ["sh", "-c", "python -m slim_lab_portal.cli migrate && uvicorn --factory slim_lab_portal.main:create_app --host 0.0.0.0 --port ${PORT:-8000} --proxy-headers --forwarded-allow-ips='*'"]
