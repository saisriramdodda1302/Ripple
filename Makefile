.PHONY: db up down install init index-evals evals test lint

db:
	docker compose up -d db

up:
	docker compose up --build

down:
	docker compose down

install:
	pip install -e ".[dev]"

init:
	ripple init-db

index-evals:
	ripple index https://github.com/psf/requests
	ripple index https://github.com/pallets/click

evals:
	ripple evals

test:
	pytest -q

lint:
	ruff check ripple evals tests
