.PHONY: install run test lint openapi

install:
	pip install -r requirements-dev.txt

run:
	uvicorn app.main:app --reload --port 8080

test:
	pytest -q

lint:
	ruff check app tests

openapi:
	python -c "import json,app.main as m; print(json.dumps(m.app.openapi(), ensure_ascii=False, indent=2))" > openapi.json
	@echo "Đã ghi openapi.json"
