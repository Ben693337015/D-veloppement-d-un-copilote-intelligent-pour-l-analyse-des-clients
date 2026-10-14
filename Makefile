.PHONY: install run test lint up down migrate ingest simulate wwi adventureworks frontend frontend-install frontend-test frontend-build

install:
	pip install -r requirements.txt
	# xgboost est installé à part, avec --no-deps, pour la même raison que dans le
	# Dockerfile : son wheel PyPI déclare nvidia-nccl-cu12 (~340 Mo, utile
	# uniquement à l'entraînement multi-GPU) comme dépendance, alors que
	# XGBRegressor tourne ici en CPU (cf. app/services/forecasting_service.py).
	# Sans cette étape, `modele=xgboost` et les tests xgboost échouent avec
	# ModuleNotFoundError sur une installation faite via `make install` seul.
	pip install --no-deps xgboost==2.1.1

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

# Jeu complet ventes/stock/trésorerie (Wide World Importers) -> data/wwi/*.csv
# Pré-requis : pip install pymssql ; Docker démarré.
wwi:
	docker compose -f scripts/wwi/docker-compose.yml up -d
	python -m app.ingestion.export_wwi --telecharger

# Jeu complet prêt à importer, sans SQL Server (CSV officiels Microsoft) -> data/adventureworks/
adventureworks:
	python -m app.ingestion.export_adventureworks
