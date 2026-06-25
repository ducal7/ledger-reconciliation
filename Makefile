PYTHON ?= python

.PHONY: data reconcile test lint all

data:
	$(PYTHON) -m recon.data

reconcile:
	$(PYTHON) -m recon.reconcile

test:
	$(PYTHON) -m pytest

lint:
	ruff check

all: data reconcile lint test
