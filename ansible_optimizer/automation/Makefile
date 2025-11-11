PYTHON ?= python3.13
SCRIPT := $(CURDIR)/run_validations.py

.PHONY: validate

validate:
	@if [ -z "$(MANIFEST)" ]; then \
		echo "Usage: make validate MANIFEST=<path/to/manifest.yaml> [NAME=<label>]"; \
		exit 64; \
	fi
	@CMD="$(PYTHON) \"$(SCRIPT)\" \"$$MANIFEST\""; \
	if [ -n "$(NAME)" ]; then \
		CMD="$$CMD --name \"$(NAME)\""; \
	fi; \
	if [ -n "$(KUBECONFORM_ARGS)" ]; then \
		CMD="$$CMD --kubconform-args '$(KUBECONFORM_ARGS)'"; \
	fi; \
	if [ -n "$(KUBE_LINTER_ARGS)" ]; then \
		CMD="$$CMD --kube-linter-args '$(KUBE_LINTER_ARGS)'"; \
	fi; \
	if [ -n "$(KUBECONFORM_BIN)" ]; then \
		CMD="$$CMD --kubconform-bin \"$(KUBECONFORM_BIN)\""; \
	fi; \
	if [ -n "$(KUBE_LINTER_BIN)" ]; then \
		CMD="$$CMD --kube-linter-bin \"$(KUBE_LINTER_BIN)\""; \
	fi; \
	eval "$$CMD"
