# Vayl — build / test / release helpers.
#
#   make test           # offline test suite
#   make lint           # ruff
#   make build          # build the container image locally (single-arch)
#   make push           # multi-arch build + push to the registry (needs `docker login`)
#   make release        # test + lint + push, tagged with the pyproject version
#
# Override: make push IMAGE=ghcr.io/you/vayl VERSION=0.2.0

PY      ?= .venv/bin/python
IMAGE   ?= ghcr.io/ac12644/vayl
VERSION ?= $(shell grep -m1 '^version' pyproject.toml | cut -d'"' -f2)
PLATFORMS ?= linux/amd64,linux/arm64

.PHONY: test lint build push release smoke clean help

help:
	@grep -E '^[a-z].*##' $(MAKEFILE_LIST) | sed 's/:.*##/ —/' | sort

test:  ## run the offline test suite
	$(PY) -m pytest tests/ -q

lint:  ## ruff check
	$(PY) -m ruff check .

build:  ## build the image locally (single-arch, tagged :VERSION and :latest)
	docker build -t $(IMAGE):$(VERSION) -t $(IMAGE):latest .

push:  ## multi-arch build + push (run `docker login <registry>` first)
	docker buildx build --platform $(PLATFORMS) -t $(IMAGE):$(VERSION) -t $(IMAGE):latest --push .

release: test lint push  ## test + lint + multi-arch push (version from pyproject.toml)
	@echo "released $(IMAGE):$(VERSION)"

smoke:  ## build + boot the image + hit /healthz (single-arch, local)
	docker build -q -t $(IMAGE):smoke . >/dev/null
	docker rm -f vayl-smoke 2>/dev/null || true
	docker run -d --name vayl-smoke -p 8080:8080 $(IMAGE):smoke >/dev/null
	@sh -c 'for i in $$(seq 1 30); do curl -sf localhost:8080/healthz && break || sleep 1; done'
	@echo "\n  healthz OK"
	docker rm -f vayl-smoke >/dev/null

clean:  ## remove local db/key/secret artifacts
	rm -f *.db *.db.key *.db.sign.key *.wrapped *.salt *.token 2>/dev/null || true
