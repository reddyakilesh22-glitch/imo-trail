PY ?= .venv/bin/python

setup:            ## create venv and install deps
	python3 -m venv .venv && .venv/bin/pip install -q -r requirements.txt

data:             ## regenerate synthetic sample data
	$(PY) scripts/generate_sample_data.py

demo:             ## run last week, then this week (so the WoW explainer has a baseline)
	rm -rf snapshots output
	$(PY) -m reportops run --asof 2026-11-06
	$(PY) -m reportops run --asof 2026-11-13

test:
	$(PY) -m pytest -q

.PHONY: setup data demo test
