# SplitMate -- Python standard library only, no install step.
# Usage:
#   make test     # run the unittest suite
#   make test-ui  # run the browser-code suites (needs Node.js; no packages)
#   make smoke    # one-command end-to-end proof (mock + real-mode HTTP paths)
#   make run      # start the app in mock mode on http://127.0.0.1:8000
#   make check    # test + test-ui + smoke

PYTHON ?= python3
NODE ?= node

.PHONY: help test test-ui smoke run check clean

help:
	@echo "SplitMate targets:"
	@echo "  make test     run the unittest suite"
	@echo "  make test-ui  run the browser-code suites (needs Node.js, no packages)"
	@echo "  make smoke    one-command end-to-end smoke test (offline, no accounts)"
	@echo "  make run      start the app in mock mode (http://127.0.0.1:8000)"
	@echo "  make check    test + test-ui + smoke"

test:
	$(PYTHON) -m unittest discover -s tests -t . -v

test-ui:
	$(NODE) --test tests/test_capture_ui.mjs tests/test_grid_ui.mjs

smoke:
	bash scripts/smoke.sh

run:
	$(PYTHON) app.py

check: test test-ui smoke

clean:
	rm -rf __pycache__ tests/__pycache__ splitmate/__pycache__ scripts/__pycache__
