.PHONY: check lint types test

check: lint types test

lint:
	uv run ruff check .

types:
	uv run mypy src

test:
	uv run pytest --cov=discogs --cov-report=term-missing --cov-fail-under=90
