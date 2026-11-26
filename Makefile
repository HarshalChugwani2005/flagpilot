# FlagPilot tasks. Run `make install` once after cloning.

ifeq ($(OS),Windows_NT)
PYTHON := ".venv/Scripts/python.exe"
else
PYTHON := .venv/bin/python
endif

.PHONY: install run-ui run-store sim seed reset test lint

install:
	python -m venv .venv
	$(PYTHON) -m pip install -r requirements.txt

run-ui:
	$(PYTHON) -m streamlit run ui/app.py

run-store:
	$(PYTHON) -m http.server 8000 --directory demo-store

sim:
	$(PYTHON) scripts/traffic_sim.py

seed:
	$(PYTHON) scripts/seed_demo.py

reset:
	$(PYTHON) scripts/reset_demo.py

test:
	$(PYTHON) -m pytest -q

lint:
	$(PYTHON) -m ruff check .
	$(PYTHON) -m ruff format --check .
