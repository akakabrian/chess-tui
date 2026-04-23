VENV := .venv
PY   := $(VENV)/bin/python
PIP  := $(VENV)/bin/pip

# System engine paths (Debian/Ubuntu defaults). Override with e.g.
#   make STOCKFISH=/opt/stockfish/stockfish run
STOCKFISH ?= $(shell command -v stockfish || echo /usr/games/stockfish)
GNUCHESS  ?= $(shell command -v gnuchess  || echo /usr/games/gnuchess)
BOOK      ?= /usr/share/games/gnuchess/book.bin

.PHONY: all bootstrap venv run run-vs run-puzzle test test-only perf clean deps engine-check book-check

all: bootstrap venv

# "bootstrap" = install external engines + opening book. We don't vendor
# Stockfish source — it's a ~20 MB standalone binary that apt/brew ship.
# Documenting both paths here rather than pulling 150 MB of source.
bootstrap: deps engine-check book-check

deps:
	@echo "==> installing stockfish, gnuchess, gnuchess-book (apt)"
	@if command -v apt-get >/dev/null 2>&1; then \
	  sudo apt-get install -y stockfish gnuchess gnuchess-book; \
	elif command -v brew >/dev/null 2>&1; then \
	  brew install stockfish gnu-chess; \
	  echo "NOTE: brew gnu-chess has no polyglot book shipped — see DECISIONS.md §book"; \
	else \
	  echo "unknown package manager — install stockfish + gnuchess manually"; exit 1; \
	fi

engine-check:
	@echo "==> stockfish : $(STOCKFISH)"
	@test -x "$(STOCKFISH)" || (echo "  MISSING — 'make deps' or override STOCKFISH=..." && exit 1)
	@echo "==> gnuchess  : $(GNUCHESS)  (optional)"
	@test -x "$(GNUCHESS)" || echo "  MISSING — optional second engine"

book-check:
	@echo "==> polyglot  : $(BOOK)"
	@test -f "$(BOOK)" && echo "  OK ($$(stat -c%s "$(BOOK)" 2>/dev/null || stat -f%z "$(BOOK)") bytes)" || echo "  MISSING — opening explorer will be empty"

venv: $(VENV)/bin/python
$(VENV)/bin/python:
	python3 -m venv $(VENV)
	$(PIP) install -e .

run: venv
	$(PY) play.py

run-vs: venv
	$(PY) play.py --mode engine

run-puzzle: venv
	$(PY) play.py --mode puzzle

# Full test suite — TUI scenarios + agent API + perf micro.
test: venv
	$(PY) -m tests.qa
	$(PY) -m tests.api_qa
	$(PY) -m tests.perf

# Fast subset: python -m tests.qa PATTERN
test-only: venv
	$(PY) -m tests.qa $(PAT)

perf: venv
	$(PY) -m tests.perf

clean:
	rm -rf $(VENV) *.egg-info chess_tui/__pycache__ tests/__pycache__
	rm -f tests/out/*.svg
