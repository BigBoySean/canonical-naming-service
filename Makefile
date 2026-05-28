.PHONY: install run test coverage lint typecheck help

help:
	@echo "Targets:"
	@echo "  install    install dependencies and package in editable mode"
	@echo "  run        start the FastAPI dev server on port 8000"
	@echo "  test       run pytest"
	@echo "  coverage   run pytest with coverage report (term + missing lines)"
	@echo "  lint       run ruff check"
	@echo "  typecheck  run mypy"

install:
	pip install -e ".[dev]"

run:
	uvicorn canonical_naming.main:app --reload --port 8000

test:
	pytest

coverage:
	pytest --cov=canonical_naming --cov-report=term-missing

lint:
	ruff check src tests

typecheck:
	mypy src
