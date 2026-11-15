.PHONY: up down logs build

up:
	@bash run.sh

down:
	docker compose down

logs:
	docker compose logs -f

build:
	docker compose build
