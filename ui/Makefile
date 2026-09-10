.PHONY: init-local run check container logs

PYTHON ?= python3

init-local:
	mkdir -p runtime
	$(PYTHON) scripts/init-local-env.py

run:
	$(PYTHON) scripts/run-local.py

check:
	$(PYTHON) -m compileall -q .
	node --check static/js/app.js

container:
	docker compose up --build

logs:
	docker compose logs --tail 100 -f
