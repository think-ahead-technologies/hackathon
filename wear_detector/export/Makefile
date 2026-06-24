# ABOUTME: One-command reproduction of the on-device wear model (train -> quantize -> vela -> meta).
# ABOUTME: Uses the py3.12 export venv; modules run from the repo root so the package imports.
ROOT := $(shell cd ../.. && pwd)
VENV := $(ROOT)/wear_detector/export/.venv
PY   := $(VENV)/bin/python
RUN  := cd $(ROOT) &&

.PHONY: help venv train quantize vela eval meta model test clean

help: ## list targets
	@grep -E '^[a-z-]+:.*##' $(MAKEFILE_LIST) | sed 's/:.*## /\t/' | sort

venv: ## create the py3.12 export venv + install TF/Vela
	uv venv --python 3.12 $(VENV)
	VIRTUAL_ENV=$(VENV) uv pip install -r $(ROOT)/wear_detector/export/requirements.txt pytest

train: ## train the AE on healthy windows, save the encoder
	$(RUN) $(PY) -m wear_detector.export.train

quantize: ## int8 PTQ -> build/model_int8.tflite (+ quant.json)
	$(RUN) $(PY) -m wear_detector.export.quantize

vela: ## Vela compile + normalize summary into the pipeline gate's schema
	$(RUN) $(PY) -m wear_detector.export.vela_compile

eval: ## end-to-end int8 eval + per-unit centroid -> build/baseline.json
	$(RUN) $(PY) -m wear_detector.export.eval_embedding

meta: ## emit Contract A (dashboard/pipeline/model-meta.json)
	$(RUN) $(PY) -m wear_detector.export.emit_meta

model: train quantize vela eval meta ## full chain: trained .tflite + gate inputs + contract

test: ## TF model tests (3.12) + pure tests (3.14 runtime venv)
	$(RUN) $(PY) -m pytest wear_detector/tests/test_model.py -q
	$(RUN) $(ROOT)/.venv/bin/python -m pytest wear_detector/tests/ -q

clean: ## remove build artifacts
	rm -rf $(ROOT)/wear_detector/export/build
