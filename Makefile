.PHONY: test lint

IMAGE := ecoedge-ha-integration-tests

# Tests run in Docker (pinned Python 3.14 + Home Assistant from requirements_test.txt).
test:
	docker build -q -f tests/Dockerfile -t $(IMAGE) . >/dev/null
	docker run --rm -v "$(CURDIR):/repo" -w /repo $(IMAGE) python -m pytest $(ARGS)

lint:
	docker build -q -f tests/Dockerfile -t $(IMAGE) . >/dev/null
	docker run --rm -v "$(CURDIR):/repo" -w /repo $(IMAGE) python -m ruff check custom_components tests
