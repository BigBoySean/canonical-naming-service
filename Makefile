.PHONY: install run test lint typecheck help

help:
	@echo "Targets:"
	@echo "  install    install dependencies and package in editable mode"
	@echo "  run        start the FastAPI dev server on port 8000"
	@echo "  test       run pytest"
	@echo "  lint       run ruff check"
	@echo "  typecheck  run mypy"

install:
	pip install -e ".[dev]"

run:
	uvicorn canonical_naming.main:app --reload --port 8000

test:
	pytest

lint:
	ruff check src tests

typecheck:
	mypy src
