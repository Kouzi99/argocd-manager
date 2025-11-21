install:
	pip install --upgrade pip && pip install .

install-user:
	python3 -m pip install --user .

install-venv:
	python3 -m venv .venv && . .venv/bin/activate && pip install -e .

install-pipx:
	pipx install .

completion:
	@echo "Run this to enable autocompletion for the current session:"
	@echo "  eval \"$(register-python-argcomplete argo-manager)\""

uninstall-user:
	python3 -m pip uninstall -y argo-manager || true

test:
	python3 -m py_compile argo-manager.py
