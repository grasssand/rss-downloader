set shell := ["pwsh.exe", "-c"]

sync:
    uv sync --group dev

run:
    uv run rss-downloader -w

run-cli:
    uv run rss-downloader

test:
    uv run pytest --cov=src/rss_downloader --cov-report=html

test-cli:
    uv run pytest --cov=src/rss_downloader
