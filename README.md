# argo-manager

Small CLI wrapper around the ArgoCD CLI for multi-cluster workflows.

Install locally:

```bash
python3 -m pip install --upgrade pip
pip install .
```

Usage:

```bash
argo-manager app --help
```

Installation options (recommended)
---------------------------------

1) Install with pipx (recommended for CLI tools)

```bash
# install pipx if you don't have it
python3 -m pip install --user pipx
python3 -m pipx ensurepath

cd /path/to/argo-manager
pipx install .
```

This creates an isolated environment and exposes `argo-manager` on your PATH.

2) Per-user install (scripts go to ~/.local/bin)

```bash
cd /path/to/argo-manager
python3 -m pip install --user .
# ensure ~/.local/bin is in your PATH
```

3) Developer / editable install (virtualenv)

```bash
cd /path/to/argo-manager
python3 -m venv .venv
. .venv/bin/activate
pip install -e .
```

4) Lightweight wrapper (no install required)

Create an executable wrapper that points to the repo venv or module. Example:

```bash
sudo tee /usr/local/bin/argo-manager >/dev/null <<'EOF'
#!/bin/bash
VENV="$HOME/argocd/.venv"
if [ -x "$VENV/bin/argo-manager" ]; then
	exec "$VENV/bin/argo-manager" "$@"
else
	exec python3 -m argocd_manager.cli "$@"
fi
EOF
sudo chmod +x /usr/local/bin/argo-manager
```

Autocompletion
--------------

This project supports shell autocompletion via `argcomplete`.

Install `argcomplete` in the environment where `argo-manager` is installed (pipx venv or user/venv):

```bash
pip install argcomplete
# Enable for current session
eval "$(register-python-argcomplete argo-manager)"
# To persist for bash, add to your ~/.bashrc:
echo 'eval "$(register-python-argcomplete argo-manager)"' >> ~/.bashrc
```

Makefile helpers
----------------

There are convenience Makefile targets in the project root:

	- `make install` - install in the active Python environment (the current venv)
	- `make install-user` - install for your user (`pip install --user .`)
	- `make install-venv` - create `.venv` and install editable
	- `make install-pipx` - install using `pipx install .` (requires pipx)
	- `make completion` - prints the command to enable argcomplete for current shell
	- `make uninstall-user` - `pip uninstall --user argo-manager`

Pick whichever workflow fits your system policy (pipx is generally best for single-user CLI tools).
