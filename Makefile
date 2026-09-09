.PHONY: install dev test lint docker

install:
	python -m pip install -e '.[dev]'
	playwright install chromium

dev:
	uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

test:
	pytest -q

lint:
	ruff check .

docker:
	docker compose up --build
