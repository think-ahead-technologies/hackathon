# ABOUTME: Developer entrypoints for the edge ops dashboard stack.
# ABOUTME: `make` (or `make help`) lists targets; `make up` runs the full demo.

COMPOSE      := docker compose
FAKE         := $(COMPOSE) --profile fake
TEST_IMAGE   := python:3.12-slim
VECTOR_IMAGE := timberio/vector:0.50.0-debian

.DEFAULT_GOAL := help

## help: list available targets
.PHONY: help
help:
	@grep -E '^## ' $(MAKEFILE_LIST) | sed 's/## /  /' | sort

## up: build + start the full stack with synthetic telemetry (the demo default)
.PHONY: up
up:
	$(FAKE) up --build -d
	@echo "Grafana   http://localhost:3000"
	@echo "Label UI  http://localhost:8080"
	@echo "Prometheus http://localhost:9090"

## up-real: start the stack WITHOUT fakegen (point a real device at NATS)
.PHONY: up-real
up-real:
	$(COMPOSE) up --build -d

## serial-shim: run the device→fabric shim on the HOST (macOS path; needs SERIAL_PORT=/dev/tty.usbmodemXXXX)
.PHONY: serial-shim
serial-shim:
	@test -n "$(SERIAL_PORT)" || { echo "set SERIAL_PORT — find it with: ls /dev/tty.usbmodem* (macOS) or ls /dev/ttyACM* (Linux)"; exit 1; }
	@test -d serial-shim/.venv || python3 -m venv serial-shim/.venv
	@serial-shim/.venv/bin/pip install -q -r serial-shim/requirements.txt
	NATS_URL=$(or $(NATS_URL),nats://localhost:4222) LINE=$(or $(LINE),line1) \
		SERIAL_PORT=$(SERIAL_PORT) SERIAL_BAUD=$(or $(SERIAL_BAUD),115200) \
		OTEL_EXPORTER_OTLP_ENDPOINT=$(or $(OTEL_ENDPOINT),http://localhost:4318) \
		serial-shim/.venv/bin/python -u serial-shim/shim.py

## down: stop and remove the stack (data is not persisted)
.PHONY: down
down:
	$(FAKE) down

## restart: rebuild and restart only the app services (bridge + fakegen)
.PHONY: restart
restart:
	$(FAKE) up -d --build bridge fakegen

## build: build the bridge + fakegen images without starting anything
.PHONY: build
build:
	$(FAKE) build

## test: run the bridge + Vector + pipeline-gate + serial-shim unit tests
.PHONY: test
test: test-bridge test-vector test-pipeline test-serial-shim

## test-bridge: run the bridge pure-logic unit tests in the target python image
.PHONY: test-bridge
test-bridge:
	docker run --rm -v "$(CURDIR)/bridge":/app -w /app $(TEST_IMAGE) \
		sh -c "pip install -q -r requirements.txt && python -m pytest -q"

## test-vector: run the data-minimization policy unit tests (vector test)
.PHONY: test-vector
test-vector:
	docker run --rm -v "$(CURDIR)/vector":/etc/vector $(VECTOR_IMAGE) \
		test /etc/vector/vector.yaml /etc/vector/vector.test.yaml

## test-pipeline: run the deployability-gate unit tests
.PHONY: test-pipeline
test-pipeline:
	docker run --rm -v "$(CURDIR)/pipeline":/app -w /app $(TEST_IMAGE) \
		sh -c "pip install -q -r requirements.txt pytest && python -m pytest -q"

## test-serial-shim: run the serial→NATS shim pure-logic unit tests
.PHONY: test-serial-shim
test-serial-shim:
	docker run --rm -v "$(CURDIR)/serial-shim":/app -w /app $(TEST_IMAGE) \
		sh -c "pip install -q -r requirements.txt pytest && python -m pytest -q"

## logs: follow logs for all services (use `make logs S=bridge` for one)
.PHONY: logs
logs:
	$(FAKE) logs -f $(S)

## ps: show service status
.PHONY: ps
ps:
	$(FAKE) ps

## psql: open a psql shell on Timescale
.PHONY: psql
psql:
	$(COMPOSE) exec timescale psql -U postgres

## verify: smoke-check the live loop and the observability pipeline
.PHONY: verify
verify:
	@echo "== open alerts =="
	@curl -s localhost:8000/alerts || true
	@echo "\n== p95 latency by hop (Prometheus) =="
	@curl -s 'http://localhost:9090/api/v1/query' --data-urlencode \
		'query=histogram_quantile(0.95, sum by (span_name, le) (rate(spanmetrics_duration_milliseconds_bucket{span_name!="edge.pipeline"}[1m])))' \
		| python3 -c "import sys,json; [print(f\"  {r['metric'].get('span_name'):<16} {float(r['value'][1]):.1f} ms\") for r in json.load(sys.stdin)['data']['result']]" || true
	@echo "== egress B/s by classification (Prometheus) =="
	@curl -s 'http://localhost:9090/api/v1/query' --data-urlencode \
		'query=sum by (data_classification) (rate(egress_bytes_total[1m]))' \
		| python3 -c "import sys,json; [print(f\"  {r['metric'].get('data_classification'):<12} {float(r['value'][1]):.2f} B/s\") for r in json.load(sys.stdin)['data']['result']]" || true

## label: send a demo label for cnc-7 (LABEL='imbalance' to override)
.PHONY: label
label:
	curl -s -X POST localhost:8000/label -H 'Content-Type: application/json' \
		-d '{"line":"line1","container_id":"cnc-7","label":"$(or $(LABEL),bearing wear)"}'
	@echo

## deploy: publish a Contract C model deploy (VERSION=v2 to set one, else auto-increments)
.PHONY: deploy
deploy:
	curl -s -X POST localhost:8000/deploy -H 'Content-Type: application/json' \
		-d '{"line":"line1","model_version":"$(VERSION)"}'
	@echo

## perturb: drive cnc-7 anomalous on cue (demo control)
.PHONY: perturb
perturb:
	curl -s -X POST localhost:8000/control -H 'Content-Type: application/json' -d '{"cmd":"perturb"}'
	@echo

## heal: return the line to healthy on cue (demo control)
.PHONY: heal
heal:
	curl -s -X POST localhost:8000/control -H 'Content-Type: application/json' -d '{"cmd":"heal"}'
	@echo

## reset: clean slate for a fresh demo run (down + up: fresh DB, metrics, traces)
.PHONY: reset
reset:
	$(MAKE) down
	$(MAKE) up

## smoke: bring up and assert the whole loop end-to-end (pre-demo confidence check)
.PHONY: smoke
smoke:
	./scripts/smoke.sh

# --- model pipeline (Vela gate -> sign -> GitOps promote) ----------------

MODEL_VERSION ?= pdm-anomaly@2026.06.15-a3f1
# Tools pinned in containers (stable flags; reproducible regardless of host versions).
COSIGN_IMAGE ?= gcr.io/projectsigstore/cosign:v2.5.3
ORAS_IMAGE   ?= ghcr.io/oras-project/oras:v1.2.3
# REGISTRY: internal registry addr (reached over the compose network).
# NET: compose network so the oras/cosign containers resolve zot.
REGISTRY     ?= zot:5000
NET          ?= dashboard_default
LINE         ?= line1
export COSIGN_PASSWORD ?=
COSIGN_RUN := docker run --rm -e COSIGN_PASSWORD -v "$(CURDIR)/pipeline":/work -w /work $(COSIGN_IMAGE)

# Split model_id@tag into the OCI ref (repo from id, tag from version).
MODEL_ID  := $(firstword $(subst @, ,$(MODEL_VERSION)))
MODEL_TAG := $(lastword $(subst @, ,$(MODEL_VERSION)))
MODEL_REF := $(REGISTRY)/models/$(MODEL_ID):$(MODEL_TAG)

## keygen: one-time setup — cosign keypair (gitignored) + zot TLS cert. Run before the first `make up`.
.PHONY: keygen
keygen:
	$(COSIGN_RUN) generate-key-pair
	@mkdir -p pipeline/certs
	@test -f pipeline/certs/zot.crt || openssl req -x509 -newkey rsa:2048 -nodes \
		-keyout pipeline/certs/zot.key -out pipeline/certs/zot.crt -days 365 -subj "/CN=zot"
	@echo "ready: cosign keypair (pipeline/cosign.{key,pub}) + zot TLS cert (pipeline/certs/)"

## gate: run the deployability gate on the sample Vela summary (MODEL=bad to watch it fail)
.PHONY: gate
gate:
	python3 pipeline/gate.py \
		--summary pipeline/samples/vela-summary$(if $(filter bad,$(MODEL)),-bad).csv \
		--policy pipeline/vela.policy.json \
		$(if $(filter bad,$(MODEL)),,--artifact pipeline/build/model_int8_vela.tflite)

## package: build manifest + sha256, sign for device + registry, push to zot. Needs the stack up.
.PHONY: package
package:
	python3 pipeline/package.py --meta pipeline/model-meta.json --version "$(MODEL_VERSION)" --out pipeline/build
	@echo "device sig: cosign sign-blob over the manifest -> raw ECDSA-P256 for on-device PSA verify"
	docker run --rm -e COSIGN_PASSWORD -v "$(CURDIR)/pipeline":/work $(COSIGN_IMAGE) \
		sign-blob --yes --tlog-upload=false --key /work/cosign.key \
		--output-signature /work/build/manifest.sig.b64 /work/build/manifest.json
	python3 pipeline/der2raw.py --in pipeline/build/manifest.sig.b64 --out pipeline/build/manifest.sig
	@echo "push + sign $(MODEL_REF)"
	docker run --rm --network $(NET) -v "$(CURDIR)/pipeline/build":/work -w /work $(ORAS_IMAGE) \
		push --insecure $(MODEL_REF) model_int8_vela.tflite:application/octet-stream \
		manifest.json:application/json manifest.sig:application/octet-stream
	docker run --rm --network $(NET) -e COSIGN_PASSWORD -v "$(CURDIR)/pipeline":/work $(COSIGN_IMAGE) \
		sign --yes --allow-insecure-registry --tlog-upload=false --key /work/cosign.key $(MODEL_REF)

## promote: reconcile desired -> running line (verify registry signature, publish Contract C). Needs the stack up.
.PHONY: promote
promote:
	python3 pipeline/promote.py

## deploy-artifact: pull the desired model from the registry and stream it to devices as
## Contract C frames over NATS (the gateway bridge: registry = truth, NATS = device transport).
.PHONY: deploy-artifact
deploy-artifact:
	rm -rf pipeline/pull && mkdir -p pipeline/pull
	docker run --rm --network $(NET) -v "$(CURDIR)/pipeline/pull":/pull -w /pull $(ORAS_IMAGE) \
		pull --insecure $(MODEL_REF) -o /pull
	docker run --rm --network $(NET) -v "$(CURDIR)/pipeline":/work -w /work python:3.12-slim \
		sh -c "pip install -q nats-py==2.7.2 nkeys==0.2.1 && BUILD_DIR=pull NATS_URL=nats://nats:4222 LINE=$(LINE) python publish_artifact.py"

# --- device identity / NATS auth (CRA secure-by-default) -----------------

## provision: issue a unique nkey per device/service and render the NATS auth config (nats/)
.PHONY: provision
provision:
	docker run --rm -v "$(CURDIR)/pipeline":/app -v "$(CURDIR)/nats":/nats -w /app $(TEST_IMAGE) \
		sh -c "pip install -q -r requirements.txt && python provision.py --fleet fleet.json --out /nats"

## verify-auth: prove the provisioned config — authorized pub works, cross-device + anonymous denied
.PHONY: verify-auth
verify-auth:
	./scripts/verify-auth.sh

## nats-secure: run only the NATS broker under per-identity nkey auth (needs `make provision` first)
.PHONY: nats-secure
nats-secure:
	$(COMPOSE) -f docker-compose.yml -f docker-compose.secure.yml up -d nats

## up-secure: bring up the full stack under per-identity nkey auth (needs `make provision` first)
.PHONY: up-secure
up-secure:
	@test -f nats/creds/vector.nk.pub || { echo "run 'make provision' first"; exit 1; }
	VECTOR_NATS_SEED="$$(cat nats/creds/vector.nk)" \
	VECTOR_NATS_NKEY="$$(cat nats/creds/vector.nk.pub)" \
	$(COMPOSE) -f docker-compose.yml -f docker-compose.secure.yml --profile fake up --build -d
	@echo "secured stack up — auth on. Grafana http://localhost:3000"

## clean: tear down and remove local python caches
.PHONY: clean
clean: down
	rm -rf bridge/__pycache__ bridge/.pytest_cache pipeline/__pycache__ pipeline/.pytest_cache
