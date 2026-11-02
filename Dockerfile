FROM python:3.11-slim

WORKDIR /app

# git: slim-data is installed from its GitHub repo (pyproject.toml). Pango and a font: the report PDF (D-123).
RUN apt-get update && apt-get install -y --no-install-recommends git libpango-1.0-0 libpangoft2-1.0-0 \
    fonts-dejavu-core && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml ./
COPY slim_lims ./slim_lims
RUN pip install --no-cache-dir .

EXPOSE 8000

# Apply the portal's and slim-data's migrations, then serve. Proxy headers are trusted so url_for() builds
# https:// links behind Caddy / Render.
CMD ["sh", "-c", "python -m slim_lims.cli migrate && uvicorn --factory slim_lims.main:create_app --host 0.0.0.0 --port ${PORT:-8000} --proxy-headers --forwarded-allow-ips='*'"]
