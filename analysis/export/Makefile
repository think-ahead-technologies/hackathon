# ABOUTME: One-command reproduction of the on-device conveyor-fault model (train -> quantize -> vela -> eval -> meta).
# ABOUTME: Uses the py3.12 export venv; modules run from this dir (flat imports).
EXPORT := $(shell pwd)
VENV   := $(EXPORT)/.venv
PY     := $(VENV)/bin/python
ANALYSIS_VENV := $(EXPORT)/../.venv

.PHONY: help venv silver train quantize vela eval meta model gate package test clean

PIPELINE := $(EXPORT)/../../pipeline
VERSION  ?= $(shell date +%Y.%m.%d)-dev   # override for a release: make package VERSION=2026.06.25-a1

help: ## list targets
	@grep -E '^[a-z-]+:.*##' $(MAKEFILE_LIST) | sed 's/:.*## /\t/' | sort

venv: ## create the py3.12 export venv + install TF/Vela
	uv venv --python 3.12 $(VENV)
	VIRTUAL_ENV=$(VENV) uv pip install -r $(EXPORT)/requirements.txt pytest

silver: ## RF-label the unlabeled fault sessions (weak supervision) -> silver_labels/
	$(PY) make_silver.py

train: ## train the classifier on all windows + LOSO-CV, save the model
	$(PY) train.py

quantize: ## int8 PTQ -> build/model_int8.tflite (+ quant.json)
	$(PY) quantize.py

vela: ## Vela compile + normalize summary into the gate's schema
	$(PY) vela_compile.py

eval: ## int8 e2e eval (rank fidelity + device threshold) -> build/eval.json
	$(PY) evaluate.py

meta: ## emit Contract A (build/model-meta.json)
	$(PY) emit_meta.py

model: train quantize vela eval meta ## full chain: trained .tflite + gate inputs + contract

gate: ## run the pipeline deployability gate against this build's vela artifact
	$(PY) $(PIPELINE)/gate.py --summary build/vela-summary.csv \
	      --policy $(PIPELINE)/vela.policy.json --artifact build/model_int8_vela.tflite

package: ## package the gated model -> build/manifest.json (Contract A + sha256 + size)
	$(PY) $(PIPELINE)/package.py --meta build/model-meta.json --version $(VERSION) --out build

test: ## TF tests (3.12 export venv) + pure tests
	$(PY) -m pytest tests/ -q

clean: ## remove build artifacts
	rm -rf $(EXPORT)/build
