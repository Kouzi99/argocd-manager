import os
import subprocess
import json
import time
from typing import Dict, List, Optional, Tuple
from datetime import datetime
import difflib
import logging

from .config import load_config, save_config, ConfigurationError
from .colors import Colors
import shlex


class ArgoCDError(Exception):
    pass


class ConnectionError(ArgoCDError):
    pass


class CommandExecutionError(ArgoCDError):
    pass


def fuzzy_match(query: str, choices: List[str], threshold: float = 0.6) -> Optional[str]:
    if query in choices:
        return query
    best = None
    best_ratio = 0.0
    for c in choices:
        ratio = difflib.SequenceMatcher(None, query.lower(), c.lower()).ratio()
        if ratio > best_ratio and ratio >= threshold:
            best_ratio = ratio
            best = c
    return best


def confirm_action(prompt: str, default: bool = False, color: Optional[str] = None) -> bool:
    choices = "Y/n" if default else "y/N"
    if color:
        full_prompt = f"{color}{prompt}{Colors.ENDC} [{choices}]: "
    else:
        full_prompt = f"{prompt} [{choices}]: "
    response = input(full_prompt).strip().lower()
    if not response:
        return default
    return response in ['y', 'yes']


class ArgoCDManager:
    def __init__(self, verbose: bool = False, no_color: bool = False, config_path: Optional[str] = None, allow_patch: bool = False):
        self.logger = logging.getLogger(__name__)
        if config_path:
            self.config = load_config(config_path)
        else:
            self.config = load_config()
        self.verbose = verbose
        # Whether to allow kubectl patch fallback when `argocd app set` fails.
        # Disabled by default for safety; can be enabled via CLI flag --allow-patch
        self.allow_patch = allow_patch
        # Track production clusters the user has confirmed this session to avoid repeated prompts
        self._confirmed_production = set()
        if no_color:
            Colors.disable()

    # Config operations
    def export_config(self, output_file: str):
        try:
            with open(output_file, 'w') as f:
                json.dump(self.config, f, indent=2)
            print(f"{Colors.OKGREEN}Exported to {output_file}{Colors.ENDC}")
        except Exception as e:
            print(f"{Colors.FAIL}Failed to export: {e}{Colors.ENDC}")

    def import_config(self, input_file: str, merge: bool = False):
        try:
            with open(input_file, 'r') as f:
                new_cfg = json.load(f)
            if merge:
                self.config.update(new_cfg)
            else:
                self.config = new_cfg
            save_config(self.config)
            print(f"{Colors.OKGREEN}Imported config from {input_file}{Colors.ENDC}")
        except Exception as e:
            print(f"{Colors.FAIL}Failed to import: {e}{Colors.ENDC}")

    def list_connections(self, detailed: bool = False):
        if not self.config:
            print(f"{Colors.WARNING}No connections configured{Colors.ENDC}")
            return
        print(f"\n{Colors.BOLD}{Colors.HEADER}Available ArgoCD Connections{Colors.ENDC}\n")
        for i, (name, cmd) in enumerate(self.config.items(), 1):
            if detailed:
                print(f"{Colors.BOLD}{i}. {name}{Colors.ENDC}\n   Command: {Colors.OKCYAN}{cmd}{Colors.ENDC}")
            else:
                print(f"  {Colors.OKGREEN}{i}.{Colors.ENDC} {Colors.BOLD}{name}{Colors.ENDC}")

    def add_connection(self, name: str, command: str):
        if not name or not command:
            raise ConfigurationError("Name and command cannot be empty")
        if name in self.config:
            if not confirm_action(f"Connection '{name}' exists. Overwrite?"):
                print("Cancelled")
                return
        self.config[name] = command
        save_config(self.config)
        print(f"{Colors.OKGREEN}Added connection {name}{Colors.ENDC}")

    def remove_connection(self, name: str):
        if name not in self.config:
            match = fuzzy_match(name, list(self.config.keys()))
            if match:
                print(f"{Colors.WARNING}Not found. Did you mean '{match}'?{Colors.ENDC}")
                if confirm_action(f"Remove '{match}' instead?"):
                    name = match
                else:
                    return
            else:
                print(f"{Colors.FAIL}Not found{Colors.ENDC}")
                return
        if not confirm_action(f"Remove '{name}'?"):
            print("Cancelled")
            return
        del self.config[name]
        save_config(self.config)
        print(f"{Colors.OKGREEN}Removed {name}{Colors.ENDC}")

    # Execution helpers
    def validate_cluster(self, cluster_name: str) -> str:
        # Exact match
        if cluster_name in self.config:
            return cluster_name

        # Case-insensitive exact match
        lower_map = {k.lower(): k for k in self.config.keys()}
        if cluster_name.lower() in lower_map:
            matched = lower_map[cluster_name.lower()]
            print(f"{Colors.WARNING}Using '{matched}' (case-insensitive) instead of '{cluster_name}'{Colors.ENDC}")
            return matched

        # Fuzzy match (case-insensitive by design)
        match = fuzzy_match(cluster_name, list(self.config.keys()))
        if match:
            print(f"{Colors.WARNING}Using '{match}' instead of '{cluster_name}'{Colors.ENDC}")
            return match

        raise ConfigurationError(f"Cluster '{cluster_name}' not found")

    def execute_argocd_command(self, cluster_name: str, argocd_args: List[str], timeout: int = 30, quiet: bool = False) -> Optional[str]:
        cluster_name = self.validate_cluster(cluster_name)
        login_cmd = self.config[cluster_name]
        parts = login_cmd.split()
        server_url = None
        connection_args = []

        # Only these flags are safe to forward to argocd subcommands (login-only flags like
        # --sso should NOT be forwarded).
        ALLOWED_GLOBAL_FLAGS = {"--grpc-web", "--insecure", "--auth-token", "--port-forward", "--plaintext"}

        i = 0
        while i < len(parts):
            part = parts[i]
            if part == 'login':
                i += 1
                if i < len(parts) and not parts[i].startswith('--'):
                    server_url = parts[i]
                    i += 1
                while i < len(parts):
                    part = parts[i]
                    # forward only allowed global flags; if a flag expects a value (like --auth-token),
                    # include the value as well
                    if part in ALLOWED_GLOBAL_FLAGS:
                        connection_args.append(part)
                        # If this flag takes a value (auth-token), attach next token when present
                        if part == '--auth-token' and i + 1 < len(parts) and not parts[i + 1].startswith('--'):
                            connection_args.append(parts[i + 1])
                            i += 1
                    # ignore login-only flags such as --sso, --skip-test-tls
                    i += 1
                break
            i += 1

        cmd = ['argocd'] + argocd_args
        if server_url:
            cmd.extend(['--server', server_url])
        cmd.extend(connection_args)

        self.logger.debug('Executing: %s', ' '.join(cmd))

        try:
            # If connecting to a production-like cluster, ask for confirmation
            if 'prod' in cluster_name.lower() and cluster_name not in self._confirmed_production:
                resp = confirm_action(f"Cluster '{cluster_name}' looks like production. Continue connecting?", default=False, color=Colors.FAIL)
                if not resp:
                    raise CommandExecutionError('User aborted connection to production cluster')
                # remember confirmation for this run so we don't re-prompt
                self._confirmed_production.add(cluster_name)

            res = subprocess.run(cmd, capture_output=True, text=True, check=True, timeout=timeout)
            return res.stdout
        except subprocess.CalledProcessError as e:
            # Some argocd commands (notably `argocd app diff`) return a non-zero
            # exit code while printing useful output to stdout (the diff). In that
            # case prefer to return stdout so callers can display the diff instead
            # of classifying it as an error. If there's no stdout, fall back to
            # stderr or a generic message.
            stdout = (e.stdout or '').strip()
            stderr = (e.stderr or '').strip()
            if stdout:
                return stdout
            err = stderr if stderr else 'Unknown error'
            err = err.replace(server_url or '', '<server>')
            raise CommandExecutionError(err)
        except subprocess.TimeoutExpired:
            raise CommandExecutionError(f"Command timed out after {timeout} seconds")
        except Exception as e:
            raise CommandExecutionError(str(e))

    def _handle_oidc_login(self, cluster_name: str, quiet: bool = False) -> bool:
        """Offer to run the stored login command for cluster_name or wait for manual login.

        Returns True if user chose to try again (after login), False otherwise.
        """
        login_cmd = self.config.get(cluster_name)
        if not login_cmd:
            print(f"{Colors.FAIL}No login command available for {cluster_name}{Colors.ENDC}")
            return False

        if not quiet and not self.verbose:
            # only print the stored login note when not quiet (or when verbose is explicitly set)
            print(f"{Colors.WARNING}Authentication required for '{cluster_name}'.{Colors.ENDC}")
            print(f"Stored login command: {Colors.OKCYAN}{login_cmd}{Colors.ENDC}")

        # Auto-run the stored login command and poll for session validity
        parts = shlex.split(login_cmd)
        try:
            if not quiet and not self.verbose:
                print(f"{Colors.OKCYAN}Auto-running stored login command...{Colors.ENDC}")
            # capture output to avoid noisy messages; print only when verbose
            res = subprocess.run(parts, capture_output=True, text=True)
            if self.verbose:
                if res.stdout:
                    print(res.stdout)
                if res.stderr:
                    print(res.stderr)
        except Exception as e:
            print(f"{Colors.FAIL}Failed to run login command: {e}{Colors.ENDC}")
            return False

        # Auto-poll to verify session
        if not quiet and not self.verbose:
            print(f"{Colors.OKCYAN}Waiting for authentication to complete...{Colors.ENDC}")
        poll_attempts = 15
        poll_delay = 2
        for attempt in range(poll_attempts):
            try:
                # try a lightweight call to verify auth; short timeout
                self.execute_argocd_command(cluster_name, ['proj', 'list', '--output', 'json'], timeout=5, quiet=quiet)
                if not quiet and not self.verbose:
                    print(f"{Colors.OKGREEN}Authentication verified for '{cluster_name}'{Colors.ENDC}")
                # remember user's confirmation for production clusters so we don't re-prompt repeatedly
                if 'prod' in cluster_name.lower():
                    self._confirmed_production.add(cluster_name)
                return True
            except CommandExecutionError as e:
                err = str(e)
                # still unauthenticated -> keep polling
                if ('Unauthenticated' in err or 'invalid session' in err or
                        'rpc error: code = Unauthenticated' in err):
                    time.sleep(poll_delay)
                    continue
                # other errors -> stop polling
                if not quiet:
                    print(f"{Colors.WARNING}Login ran but verification failed: {e}{Colors.ENDC}")
                return False

        print(f"{Colors.WARNING}Timed out waiting for authentication for '{cluster_name}'{Colors.ENDC}")
        return False

    # Application/project helpers
    def list_projects(self, cluster_name: str):
        cmd = ['proj', 'list', '--output', 'json']
        for attempt in range(2):
            try:
                out = self.execute_argocd_command(cluster_name, cmd)
                return json.loads(out) if out else None
            except CommandExecutionError as e:
                err = str(e)
                low = err.lower()
                # detect a variety of auth-related error strings (including oauth2 invalid_grant)
                if any(k in low for k in ('unauthenticated', 'invalid session', 'rpc error: code = unauthenticated', 'invalid_grant', 'invalid refresh token', 'oauth2')):
                    if attempt == 0 and self._handle_oidc_login(cluster_name):
                        continue
                print(f"{Colors.FAIL}Failed: {e}{Colors.ENDC}")
                return None

    def get_project_status(self, cluster_name: str, project_name: str):
        try:
            out = self.execute_argocd_command(cluster_name, ['proj', 'get', project_name, '--output', 'json'])
            return json.loads(out) if out else None
        except CommandExecutionError as e:
            print(f"{Colors.FAIL}Failed: {e}{Colors.ENDC}")
            return None

    def list_applications(self, cluster_name: str, project_name: Optional[str] = None):
        cmd = ['app', 'list', '--output', 'json']
        if project_name:
            cmd.extend(['--project', project_name])
        for attempt in range(2):
            try:
                out = self.execute_argocd_command(cluster_name, cmd)
                return json.loads(out) if out else None
            except CommandExecutionError as e:
                err = str(e)
                low = err.lower()
                if any(k in low for k in ('unauthenticated', 'invalid session', 'rpc error: code = unauthenticated', 'invalid_grant', 'invalid refresh token', 'oauth2')):
                    if attempt == 0 and self._handle_oidc_login(cluster_name):
                        continue
                print(f"{Colors.FAIL}Failed: {e}{Colors.ENDC}")
                return None

    def get_application_status(self, cluster_name: str, app_name: str):
        return self._get_application_status(cluster_name, app_name, quiet=False)

    def _get_application_status(self, cluster_name: str, app_name: str, quiet: bool = False):
        cmd = ['app', 'get', app_name, '--output', 'json']
        for attempt in range(2):
            try:
                out = self.execute_argocd_command(cluster_name, cmd, quiet=quiet)
                return json.loads(out) if out else None
            except CommandExecutionError as e:
                err = str(e)
                low = err.lower()
                if any(k in low for k in ('unauthenticated', 'invalid session', 'rpc error: code = unauthenticated', 'invalid_grant', 'invalid refresh token', 'oauth2')):
                    if attempt == 0 and self._handle_oidc_login(cluster_name, quiet=quiet):
                        continue
                print(f"{Colors.FAIL}Failed: {e}{Colors.ENDC}")
                return None

    def get_application_diff(self, cluster_name: str, app_name: str):
        return self._get_application_diff(cluster_name, app_name, quiet=False)

    def _get_application_diff(self, cluster_name: str, app_name: str, quiet: bool = False):
        cmd = ['app', 'diff', app_name]
        for attempt in range(2):
            try:
                return self.execute_argocd_command(cluster_name, cmd, quiet=quiet)
            except CommandExecutionError as e:
                err = str(e)
                low = err.lower()
                if any(k in low for k in ('unauthenticated', 'invalid session', 'rpc error: code = unauthenticated', 'invalid_grant', 'invalid refresh token', 'oauth2')):
                    if attempt == 0 and self._handle_oidc_login(cluster_name, quiet=quiet):
                        continue
                if not quiet:
                    print(f"{Colors.FAIL}Failed: {e}{Colors.ENDC}")
                return None

    def sync_application(self, cluster_name: str, app_name: str, dry_run: bool = False, prune: bool = False) -> bool:
        if dry_run:
            print(f"{Colors.OKCYAN}DRY RUN: Would sync {app_name}{Colors.ENDC}")
            diff = self.get_application_diff(cluster_name, app_name)
            if diff:
                print(diff)
            return True
        if not confirm_action(f"Sync '{app_name}'?", default=True):
            print("Cancelled")
            return False

        cmd = ['app', 'sync', app_name]
        if prune:
            cmd.append('--prune')

        # Attempt sync, but if we hit auth errors, try an automatic login once and retry.
        for attempt in range(2):
            try:
                out = self.execute_argocd_command(cluster_name, cmd, timeout=300)
                print(f"{Colors.OKGREEN}Synced {app_name}{Colors.ENDC}")
                if self.verbose and out:
                    print(out)
                return True
            except CommandExecutionError as e:
                err = str(e).lower()
                if attempt == 0 and any(k in err for k in ('unauthenticated', 'invalid session', 'rpc error: code = unauthenticated', 'invalid_grant', 'invalid refresh token', 'oauth2')):
                    # try to run stored login flow, then retry
                    if self._handle_oidc_login(cluster_name):
                        continue
                print(f"{Colors.FAIL}Failed to sync: {e}{Colors.ENDC}")
                return False

    def sync_multiple_applications(self, cluster_name: str, app_names: List[str], dry_run: bool = False, prune: bool = False) -> Tuple[int, int]:
        if dry_run:
            print(f"{Colors.OKCYAN}DRY RUN MODE{Colors.ENDC}")
        if not dry_run and not confirm_action(f"Sync {len(app_names)} applications?"):
            print("Cancelled")
            return (0, len(app_names))
        success = 0
        for i, app in enumerate(app_names, 1):
            print(f"[{i}/{len(app_names)}] Processing {app}")
            if self.sync_application(cluster_name, app, dry_run=dry_run, prune=prune):
                success += 1
            time.sleep(1)
        return (success, len(app_names))

    def set_application_target_revision(self, cluster_name: str, app_name: str, revision: str, repo: Optional[str] = None, source_index: Optional[int] = None, dry_run: bool = False) -> bool:
        """Set the application's target revision.

        Prefer using `argocd app set` when possible; fall back to kubectl patch if needed.
        If `repo` is provided, pass it to argocd to target the correct source. If the
        application has multiple sources, use `source_index` to patch the correct entry.
        """
        cluster_name = self.validate_cluster(cluster_name)

        # Fetch application status first (this will trigger auto-login if needed)
        app_status = self.get_application_status(cluster_name, app_name)
        if not app_status:
            print(f"{Colors.FAIL}Unable to fetch application status for {app_name}{Colors.ENDC}")
            return False

        spec = app_status.get('spec', {})
        sources = []
        if 'sources' in spec and isinstance(spec['sources'], list):
            sources = spec['sources']
        elif 'source' in spec and isinstance(spec['source'], dict):
            sources = [spec['source']]

        # If repo provided and multiple sources, try to resolve index by matching repoURL
        resolved_index = source_index
        if repo and sources and len(sources) > 1 and resolved_index is None:
            for i, s in enumerate(sources):
                repourl = s.get('repoURL') or s.get('helm', {}).get('repo')
                if repourl and repourl.rstrip('/') == repo.rstrip('/'):
                    resolved_index = i
                    break

        if len(sources) > 1 and resolved_index is None:
            # Interactive chooser: list sources with useful fields and ask user to pick
            print(f"\n{Colors.BOLD}Application has multiple sources. Choose which source to update for '{app_name}' on '{cluster_name}':{Colors.ENDC}")
            for i, s in enumerate(sources):
                repourl = s.get('repoURL') or s.get('helm', {}).get('repo') or '<no-repo>'
                tgt = s.get('targetRevision') or s.get('ref') or '<none>'
                chart = s.get('chart') or s.get('path') or '<no-chart>'
                print(f"  [{i}] repo: {repourl} | chart/path: {chart} | targetRevision: {tgt}")

            # Prompt user to select an index or skip
            while True:
                try:
                    choice = input(f"Select source index to update for cluster '{cluster_name}' (enter number, or 's' to skip): ").strip()
                except EOFError:
                    print("\nNo input available; skipping")
                    return False
                if not choice or choice.lower() in ('s', 'skip', 'n', 'no'):
                    print('Skipping')
                    return False
                if choice.isdigit():
                    ci = int(choice)
                    if 0 <= ci < len(sources):
                        resolved_index = ci
                        break
                print('Invalid selection. Please enter a valid index or "s" to skip.')

        # Build argocd app set command
        cmd = ['app', 'set', app_name, '--revision', revision]
        if repo:
            cmd.extend(['--repo', repo])
        if resolved_index is not None:
            # argocd uses 1-based source position
            cmd.extend(['--source-position', str(resolved_index + 1)])

        if dry_run:
            print(f"DRY RUN: argocd {' '.join(cmd)} --server <server-from-config>")
            return True

        # Try using argocd app set first
        try:
            out = self.execute_argocd_command(cluster_name, cmd, timeout=30)
            if self.verbose and out:
                print(out)
            return True
        except CommandExecutionError as e:
            # If argocd app set fails, do NOT fall back to kubectl patch unless explicitly allowed.
            print(f"{Colors.FAIL}argocd app set failed: {e}.{Colors.ENDC}")
            if not self.allow_patch:
                print(f"{Colors.WARNING}kubectl patch fallback is disabled. To enable, run with --allow-patch.{Colors.ENDC}")
                return False

        # Fallback: use kubectl patch against the Application CRD in the app's namespace (only if allowed)
        kube_ns = app_status.get('metadata', {}).get('namespace') or 'argocd'

        # Prepare JSON patch depending on whether we target a specific source index
        try:
            if resolved_index is not None:
                patch_obj = [{'op': 'replace', 'path': f'/spec/sources/{resolved_index}/targetRevision', 'value': revision}]
                patch_str = json.dumps(patch_obj)
                patch_cmd = ['kubectl', '-n', kube_ns, 'patch', 'applications.argoproj.io', app_name, '--type=json', '-p', patch_str]
            else:
                # merge patch for spec.source
                patch_obj = {'spec': {'source': {'targetRevision': revision}}}
                patch_str = json.dumps(patch_obj)
                patch_cmd = ['kubectl', '-n', kube_ns, 'patch', 'applications.argoproj.io', app_name, '--type', 'merge', '-p', patch_str]

            res = subprocess.run(patch_cmd, capture_output=True, text=True, check=True)
            if res.stdout:
                print(res.stdout)
            return True
        except subprocess.CalledProcessError as e:
            # Try fallback resource name (older clusters may accept 'applications')
            try:
                if resolved_index is not None:
                    patch_cmd[3] = 'applications'
                else:
                    patch_cmd[3] = 'applications'
                res = subprocess.run(patch_cmd, capture_output=True, text=True, check=True)
                if res.stdout:
                    print(res.stdout)
                return True
            except subprocess.CalledProcessError:
                print(f"{Colors.FAIL}kubectl patch failed: {e.stderr or e.stdout}{Colors.ENDC}")
                return False

    def show_project_apps_status(self, cluster_name: str, project_name: str, watch: bool = False):
        def display():
            apps = self.list_applications(cluster_name, project_name)
            if not apps:
                print(f"{Colors.WARNING}No applications found{Colors.ENDC}")
                return
            if watch:
                os.system('clear' if os.name == 'posix' else 'cls')
            print(f"\n{Colors.BOLD}{Colors.HEADER}Applications in '{project_name}' on '{cluster_name}'{Colors.ENDC}\n")
            print(f"Last updated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
            print(f"{Colors.BOLD}{'NAME':<35} {'NAMESPACE':<20} {'SYNC':<12} {'HEALTH':<12}{Colors.ENDC}")
            print('-' * 80)
            out_of_sync = []
            degraded = []
            for app in apps:
                name = app.get('metadata', {}).get('name', 'N/A')
                namespace = app.get('spec', {}).get('destination', {}).get('namespace', 'N/A')
                sync_status = app.get('status', {}).get('sync', {}).get('status', 'Unknown')
                health_status = app.get('status', {}).get('health', {}).get('status', 'Unknown')
                sync_color = Colors.OKGREEN if sync_status == 'Synced' else Colors.WARNING
                health_color = Colors.OKGREEN if health_status == 'Healthy' else Colors.FAIL
                print(f"{name:<35} {namespace:<20} {sync_color}{sync_status:<12}{Colors.ENDC} {health_color}{health_status:<12}{Colors.ENDC}")
                if sync_status == 'OutOfSync':
                    out_of_sync.append(name)
                if health_status in ['Degraded', 'Missing']:
                    degraded.append(name)
            print('\n')
            print(f"Total: {len(apps)} | ", end='')
            if out_of_sync:
                print(f"{Colors.WARNING}Out of Sync: {len(out_of_sync)}{Colors.ENDC} | ", end='')
            if degraded:
                print(f"{Colors.FAIL}Degraded: {len(degraded)}{Colors.ENDC}", end='')
            print()
        if watch:
            try:
                while True:
                    display()
                    time.sleep(5)
            except KeyboardInterrupt:
                print('\nWatch stopped')
        else:
            display()

    def visualize_diff(self, cluster_name: str, app_name: str):
        # Fetch application status so we can show target revision(s)
        app_status = self.get_application_status(cluster_name, app_name)
        diff_output = self.get_application_diff(cluster_name, app_name)
        if not diff_output:
            print(f"{Colors.OKCYAN}No differences found for '{app_name}'{Colors.ENDC}")
            return

        # Print target revisions (repo + targetRevision) when available
        if app_status:
            spec = app_status.get('spec', {})
            sources = []
            if 'sources' in spec and isinstance(spec['sources'], list):
                sources = spec['sources']
            elif 'source' in spec and isinstance(spec['source'], dict):
                sources = [spec['source']]

            targets = []
            for s in sources:
                repo = s.get('repoURL') or s.get('helm', {}).get('repo') or s.get('chart')
                rev = s.get('targetRevision') or s.get('targetRevision') or s.get('ref') or ''
                if repo:
                    if rev:
                        targets.append(f"{repo} @ {rev}")
                    else:
                        targets.append(f"{repo}")
                else:
                    # fallback: show a compact representation
                    try:
                        compact = json.dumps(s)
                    except Exception:
                        compact = str(s)
                    targets.append(compact)

            if targets:
                print(f"\n{Colors.BOLD}Target revisions:{Colors.ENDC}")
                for t in targets:
                    print(f"  - {t}")

        # Colorize unified diff lines for readability
        print(f"\n{Colors.BOLD}{Colors.HEADER}Diff for '{app_name}'{Colors.ENDC}\n")
        for line in diff_output.splitlines():
            if line.startswith('+++') or line.startswith('---'):
                # file header
                print(f"{Colors.OKBLUE}{line}{Colors.ENDC}")
            elif line.startswith('@@'):
                print(f"{Colors.OKCYAN}{line}{Colors.ENDC}")
            elif line.startswith('+') and not line.startswith('+++'):
                print(f"{Colors.OKGREEN}{line}{Colors.ENDC}")
            elif line.startswith('-') and not line.startswith('---'):
                print(f"{Colors.FAIL}{line}{Colors.ENDC}")
            else:
                print(line)

    # Display helpers
    def print_application_summary(self, app_status: Dict):
        spec = app_status.get('spec', {})
        status = app_status.get('status', {})
        dest = spec.get('destination', {})
        print(f"\n{Colors.BOLD}{app_status.get('metadata', {}).get('name', 'N/A')}{Colors.ENDC}")
        print(f"Destination: {dest.get('server')} -> namespace {dest.get('namespace')}")
        print(f"Sync status: {status.get('sync', {}).get('status')}")
        print(f"Health status: {status.get('health', {}).get('status')}")
        print(f"Reconciled at: {status.get('reconciledAt')}")
        # Show target revisions from spec.sources and sync.revisions
        sources = spec.get('sources', [])
        if sources:
            print('\nTarget revisions:')
            for s in sources:
                repo = s.get('repoURL') or s.get('chart') or '<unknown>'
                rev = s.get('targetRevision') or s.get('version') or ''
                print(f"  - {repo} @ {rev}")

        images = status.get('summary', {}).get('images', [])
        sync_revs = status.get('sync', {}).get('revisions', [])
        if sync_revs:
            print('\nSync revisions:')
            for r in sync_revs:
                print('  -', r)
        if images:
            print('Images:')
            for img in images:
                print('  -', img)

    def print_application_table(self, app_status: Dict):
        # Print a table of resources: NAME | NAMESPACE | KIND | SYNC | HEALTH
        resources = app_status.get('status', {}).get('resources', [])
        if not resources:
            print(f"{Colors.WARNING}No resources available{Colors.ENDC}")
            return

        # Header (add TARGET for targetRevision(s) and REV for app-level sync revisions)
        header = f"{Colors.BOLD}{'NAME':<28} {'NAMESPACE':<16} {'KIND':<14} {'SYNC':<8} {'HEALTH':<8} {'TARGET':<20} {'REV':<20}{Colors.ENDC}"
        print('\n' + header)
        print('-' * 130)
        app_revs = ','.join(app_status.get('status', {}).get('sync', {}).get('revisions', []) or [])
        # collect targetRevision values from spec.sources
        spec_sources = app_status.get('spec', {}).get('sources', [])
        target_revs = []
        for s in spec_sources:
            tr = s.get('targetRevision')
            if tr and tr not in target_revs:
                target_revs.append(tr)
        target_str = ','.join(target_revs)
        for r in resources:
            name = r.get('name', '')
            ns = r.get('namespace', '')
            kind = r.get('kind', '')
            sync = r.get('status', '')
            health = r.get('health', {}).get('status', '') if isinstance(r.get('health'), dict) else ''
            sync_color = Colors.OKGREEN if sync == 'Synced' else Colors.WARNING if sync == 'OutOfSync' else Colors.ENDC
            health_color = Colors.OKGREEN if health == 'Healthy' else Colors.FAIL if health in ['Degraded', 'Missing'] else Colors.ENDC
            print(f"{name:<28} {ns:<16} {kind:<14} {sync_color}{sync:<8}{Colors.ENDC} {health_color}{health:<8}{Colors.ENDC} {target_str:<20} {app_revs:<20}")

    def search_applications(self, cluster_name: str, query: str, project: Optional[str] = None):
        apps = self.list_applications(cluster_name, project)
        if not apps:
            return
        matches = [a for a in apps if query.lower() in a.get('metadata', {}).get('name', '').lower()]
        if not matches:
            print(f"{Colors.WARNING}No matches{Colors.ENDC}")
            return
        print(f"\n{Colors.BOLD}{Colors.HEADER}Applications matching '{query}'{Colors.ENDC}\n")
        for app in matches:
            name = app.get('metadata', {}).get('name', 'N/A')
            sync = app.get('status', {}).get('sync', {}).get('status', 'Unknown')
            health = app.get('status', {}).get('health', {}).get('status', 'Unknown')
            print(f"  {Colors.BOLD}{name}{Colors.ENDC} - Sync: {sync}, Health: {health}")
