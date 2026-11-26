# FlagPilot tasks. Run `make install` once after cloning.

ifeq ($(OS),Windows_NT)
PYTHON := ".venv/Scripts/python.exe"
else
PYTHON := .venv/bin/python
endif

.PHONY: install run-ui run-store sim seed reset test test-integration lint

install:
	python -m venv .venv
	$(PYTHON) -m pip install -r requirements.txt

run-ui:
	$(PYTHON) -m streamlit run ui/app.py

run-store:
	$(PYTHON) scripts/serve_store.py --port 8000

sim:
	$(PYTHON) scripts/traffic_sim.py $(ARGS)

seed:
	$(PYTHON) scripts/seed_demo.py

reset:
	$(PYTHON) scripts/reset_demo.py

test:
	$(PYTHON) -m pytest -q -m "not integration"
	node --test tests/test_demo_store.mjs

test-integration:
	$(PYTHON) -m pytest -v -m integration

lint:
	$(PYTHON) -m ruff check .
	$(PYTHON) -m ruff format --check .
