.PHONY: install run test lint up down migrate ingest simulate frontend frontend-install frontend-test frontend-build

install:
	pip install -r requirements.txt

run:
	uvicorn app.main:app --reload

frontend-install:
	cd frontend && npm ci

frontend:
	cd frontend && npm run dev

frontend-build:
	cd frontend && npm run build

frontend-test:
	cd frontend && npx vitest run

test:
	pytest -v

lint:
	ruff check app tests

up:
	docker compose up --build

down:
	docker compose down

migrate:
	alembic upgrade head

ingest:
	python -m app.ingestion.load_online_retail --source data/raw/online_retail_II.csv

simulate:
	python -m app.ingestion.simulate_tresorerie_stocks
