# kb_curator development tasks.
#
# Default `make` target is `test` so an AI feedback loop can run `make` to
# get a deterministic pass/fail signal in under a few seconds.

.PHONY: test test-verbose test-watch lint sanity install help

PY ?= python3
PYTEST ?= $(PY) -m pytest

help:
	@echo "Targets:"
	@echo "  test         Run the unit + integration test suite (default)."
	@echo "  test-verbose Run tests with -vv and full tracebacks."
	@echo "  test-watch   Re-run tests on file change (requires pytest-watch)."
	@echo "  lint         Quick syntax check across the package."
	@echo "  sanity       End-to-end smoke test against the configured vault."
	@echo "  install      pip install -e .[dev]"

test:
	$(PYTEST)

test-verbose:
	$(PYTEST) -vv --tb=long

test-watch:
	$(PY) -m pytest_watch -- $(PYTEST_ARGS)

lint:
	$(PY) -m compileall -q kb_curator kb_curator.py

sanity:
	$(PY) kb_curator.py audit --json | $(PY) -c "import json,sys; d=json.load(sys.stdin)['summary']; print('audit:', d); sys.exit(0 if d.get('error',0)==0 else 1)"
	$(PY) kb_curator.py links check | head -3
	$(PY) kb_curator.py taxonomy show | head -5

install:
	$(PY) -m pip install -e ".[dev]"

# Default target.
.DEFAULT_GOAL := test
