# No build step and no runtime dependencies: these are shortcuts for the
# commands in the README, not a build system.

PYTHON ?= python

.PHONY: help setup lint test test-all cycle experiments verify hmi io-map plcopen rusty crosscheck clean

help:
	@echo "make setup        install pytest and ruff (nothing else is needed)"
	@echo "make lint         ruff over src, scripts and tests"
	@echo "make test         unit tests, a few seconds"
	@echo "make test-all     everything, including the end-to-end cycles"
	@echo "make cycle        run one CIP cycle and print the batch record"
	@echo "make experiments  the full sweep: results/, figures and batch records"
	@echo "make verify       re-run one committed cell and check it reproduces"
	@echo "make hmi          Modbus TCP on :5020 and the operator page on :8080"
	@echo "make io-map       regenerate docs/io_map.md from the ST declarations"
	@echo "make plcopen      regenerate export/plcopen/cip_skid.xml"
	@echo "make rusty        compile plc/ with RuSTy (Linux; see scripts/rusty)"
	@echo "make crosscheck   the same scenarios on the interpreter and the RuSTy build"

setup:
	$(PYTHON) -m pip install -r requirements-dev.txt

lint:
	$(PYTHON) -m ruff check src scripts tests

test:
	$(PYTHON) -m pytest -q -m "not slow"

test-all:
	$(PYTHON) -m pytest -q

cycle:
	$(PYTHON) scripts/run_cycle.py --recipe adaptive --soil medium

experiments:
	$(PYTHON) scripts/run_experiments.py

verify:
	$(PYTHON) scripts/verify_results.py --recipe adaptive --scenario medium

hmi:
	$(PYTHON) scripts/serve_hmi.py --speed 20 --autostart

io-map:
	$(PYTHON) scripts/render_io_map.py

plcopen:
	$(PYTHON) scripts/export_plcopen.py

# RUSTY_STDLIB is the directory scripts/rusty/build_stdlib.sh wrote to.
RUSTY_STDLIB ?= $(HOME)/rusty/stdlib

rusty:
	scripts/rusty/compile_plc.sh $(RUSTY_STDLIB) build/rusty

crosscheck:
	$(PYTHON) scripts/crosscheck_compiled.py

clean:
	rm -rf .pytest_cache .ruff_cache
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
