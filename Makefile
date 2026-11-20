.PHONY: install test lint demo up down logs initdb

install:
	uv pip install -e ".[dev]"

test:
	.venv/bin/pytest -q

lint:
	.venv/bin/ruff check app tests scripts

demo:
	.venv/bin/python scripts/demo.py

up:
	docker compose up --build -d

down:
	docker compose down -v

logs:
	docker compose logs -f api worker beat

initdb:
	.venv/bin/python -m app.cli initdb