# The only entry point a human needs (spec section 11).
#
# uv is not installed in this environment, so setup uses the stdlib venv and
# pip against the same pyproject.toml; swap PY/PIP for `uv run` if you have it.

SHELL := /bin/bash
PY    := .venv/bin/python
PIP   := .venv/bin/pip
VENV  := .venv
export PYTHONPATH := .

-include .env
export

LLAMA_BIN        ?= $(HOME)/llama.cpp/build/bin/llama-server
LLAMA_MODEL_PATH ?= $(HOME)/models/gemma-4-E4B_q4_0-it.gguf
LLAMA_HOST       ?= http://127.0.0.1:8080
LLAMA_THREADS    ?= 14
LLAMA_PARALLEL   ?= 6

.PHONY: all setup fetch fetch-npr schema normalise colour baseline rollup export \
        audit test serve-model stop-model dev build clean

all: fetch schema normalise colour rollup export audit build

setup:
	test -d $(VENV) || python3 -m venv $(VENV)
	$(PIP) install -q --upgrade pip
	$(PIP) install -q -e '.[dev]'
	cd site && npm ci || (cd site && npm install)
	@test -f "$(LLAMA_MODEL_PATH)" \
	  && echo "[setup] model present: $(LLAMA_MODEL_PATH)" \
	  || echo "[setup] MISSING model $(LLAMA_MODEL_PATH) — see README section 'The colour model'"
	@test -x "$(LLAMA_BIN)" \
	  && echo "[setup] llama-server present: $(LLAMA_BIN)" \
	  || echo "[setup] MISSING llama-server at $(LLAMA_BIN)"

fetch:
	$(PY) -m pipeline.fetch mmtd

fetch-npr:
	$(PY) -m pipeline.fetch npr

schema:
	$(PY) -m pipeline.schema
	$(PY) -c "from pipeline import ingest_npr; ingest_npr.dump_schema()"

normalise:
	$(PY) -m pipeline.normalise

# Resumable: prints a live rate and ETA, safe to interrupt, never rewrites the
# cache in place. data/cache/track_colour.jsonl is the most valuable artefact
# in the repository.
colour:
	$(PY) -m pipeline.colour_model

baseline rollup export audit: build-offline
build-offline:
	$(PY) -m pipeline.build

# llama.cpp server with the judgement schema's decoding budget (section 6.2).
serve-model:
	nohup $(LLAMA_BIN) -m "$(LLAMA_MODEL_PATH)" \
	  --host 127.0.0.1 --port 8080 \
	  --ctx-size $$(( 2048 * $(LLAMA_PARALLEL) )) \
	  --parallel $(LLAMA_PARALLEL) --threads $(LLAMA_THREADS) \
	  --no-warmup --jinja > data/work/llama-server.log 2>&1 & \
	  echo "[serve-model] starting; tail data/work/llama-server.log"

stop-model:
	-pkill -f "llama-server -m $(LLAMA_MODEL_PATH)" && echo "[stop-model] stopped"

test:
	$(PY) -m pytest tests -q

dev:
	cd site && npm run dev

build:
	cd site && npm run build

clean:
	rm -rf data/work/* site/dist
