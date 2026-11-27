# Cross-platform: prefer venv python if present
ifeq ($(OS),Windows_NT)
  VENV_PY := .venv/Scripts/python.exe
  VENV_PIP := .venv/Scripts/pip.exe
else
  VENV_PY := .venv/bin/python
  VENV_PIP := .venv/bin/pip
endif

PYTHON ?= $(shell if [ -x "$(VENV_PY)" ] || [ -f "$(VENV_PY)" ]; then echo "$(VENV_PY)"; else echo python; fi)

.PHONY: setup sync probe promote build dry publish test lint all

setup:
	python -m venv .venv
	$(VENV_PIP) install -r requirements.txt -e ".[dev]"

sync:
	$(PYTHON) -m tvboxgen sync

probe:
	$(PYTHON) -m tvboxgen probe

promote:
	$(PYTHON) -m tvboxgen promote

build:
	$(PYTHON) -m tvboxgen build

dry:
	$(PYTHON) -m tvboxgen build --dry-run

publish:
	$(PYTHON) -m tvboxgen publish --local

test:
	$(PYTHON) -m pytest -q

lint:
	$(PYTHON) -m ruff check src
	$(PYTHON) -m ruff format --check src

all: lint test sync probe promote build
