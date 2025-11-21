import argparse
import json
import os
import textwrap
import shutil
from .logger import setup_logging
from .manager import ArgoCDManager
from .colors import Colors
try:
    import argcomplete  # optional dependency for shell autocompletion
except Exception:
    argcomplete = None


def build_parser() -> argparse.ArgumentParser:
    examples = (
        "Examples:\n"
        "  argo-manager list\n"
        "  argo-manager app get <cluster> <app> --format summary\n"
        "  argo-manager app set-target <cluster> <app> --revision <revision> --index <n> --show-diff\n"
        "  argo-manager app overview <cluster-pattern> <app> --show-diff\n"
        "\nTo enable shell autocompletion (bash):\n"
        "  # install argcomplete in your environment: pip install argcomplete\n"
        "  # then run: eval \"$(register-python-argcomplete argo-manager)\"\n"
    )
    parser = argparse.ArgumentParser(description='ArgoCD Manager', epilog=examples, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('-v', '--verbose', action='store_true')
    parser.add_argument('--no-color', action='store_true')
    parser.add_argument('--allow-patch', action='store_true', help='Allow falling back to kubectl patch when argocd app set fails')
    parser.add_argument('--editor', help='Editor to use for editing config (overrides $EDITOR)')
    subparsers = parser.add_subparsers(dest='command')

    list_parser = subparsers.add_parser('list')
    list_parser.add_argument('-d', '--detailed', action='store_true')

    edit_parser = subparsers.add_parser('edit', help='Edit saved ArgoCD connections in an editor')
    edit_parser.add_argument('--editor', help='Editor to use (overrides $EDITOR and --editor top-level)')

    add_parser = subparsers.add_parser('add')
    add_parser.add_argument('name')
    add_parser.add_argument('cmd', nargs=argparse.REMAINDER)

    remove_parser = subparsers.add_parser('remove')
    remove_parser.add_argument('name')

    connect_parser = subparsers.add_parser('connect')
    connect_parser.add_argument('name')

    export_parser = subparsers.add_parser('export')
    export_parser.add_argument('file')

    import_parser = subparsers.add_parser('import')
    import_parser.add_argument('file')
    import_parser.add_argument('--merge', action='store_true')

    proj_parser = subparsers.add_parser('proj')
    proj_sub = proj_parser.add_subparsers(dest='proj_command')
    p_list = proj_sub.add_parser('list')
    p_list.add_argument('cluster')
    p_get = proj_sub.add_parser('get')
    p_get.add_argument('cluster')
    p_get.add_argument('project')
    p_apps = proj_sub.add_parser('apps')
    p_apps.add_argument('cluster')
    p_apps.add_argument('project')
    p_apps.add_argument('-w', '--watch', action='store_true')

    app_parser = subparsers.add_parser('app')
    app_sub = app_parser.add_subparsers(dest='app_command')
    a_list = app_sub.add_parser('list')
    a_list.add_argument('cluster')
    a_list.add_argument('-p', '--project')
    a_get = app_sub.add_parser('get')
    a_get.add_argument('cluster')
    a_get.add_argument('app')
    a_get.add_argument('--format', choices=['summary', 'table', 'json'], default='summary', help='Output format')
    a_get_multi = app_sub.add_parser('get-multi', help='Get application status from multiple clusters')
    a_get_multi.add_argument('clusters', nargs='+', help='Cluster names')
    a_get_multi.add_argument('app')
    a_get_multi.add_argument('--format', choices=['summary', 'table', 'json'], default='summary', help='Output format')
    a_diff = app_sub.add_parser('diff')
    a_diff.add_argument('cluster')
    a_diff.add_argument('app')
    a_overview = app_sub.add_parser('overview', help='Show compact overview (target,status,diff) across clusters')
    a_overview.add_argument('clusters', nargs='+', help='Cluster names or glob patterns')
    a_overview.add_argument('app')
    a_overview.add_argument('--show-diff', action='store_true', help='Inline show diffs when present')
    a_overview.add_argument('--targets-full', action='store_true', help='Show full target URLs in the table')
    a_sync = app_sub.add_parser('sync')
    a_sync.add_argument('cluster')
    a_sync.add_argument('app')
    a_sync.add_argument('--dry-run', action='store_true')
    a_sync.add_argument('--prune', action='store_true')
    a_sync_multi = app_sub.add_parser('sync-multi')
    a_sync_multi.add_argument('cluster')
    a_sync_multi.add_argument('apps', nargs='+')
    a_sync_multi.add_argument('--dry-run', action='store_true')
    a_sync_multi.add_argument('--prune', action='store_true')
    a_search = app_sub.add_parser('search')
    a_search.add_argument('cluster')
    a_search.add_argument('query')
    a_search.add_argument('-p', '--project')
    a_set_target = app_sub.add_parser('set-target', help='Set target revision for an application across clusters')
    a_set_target.add_argument('clusters', nargs='+', help='Cluster names or glob patterns (eg Monet*)')
    a_set_target.add_argument('app')
    a_set_target.add_argument('--revision', required=True, help='Target revision to set (branch, tag, or ref)')
    a_set_target.add_argument('--repo', help='Repository URL to target (optional)')
    a_set_target.add_argument('--index', type=int, help='If the Application has multiple sources, set targetRevision for this source index (0-based)')
    a_set_target.add_argument('--dry-run', action='store_true', help='Show commands without applying')
    a_set_target.add_argument('--sync', action='store_true', help='After setting the revision, show diffs and optionally sync (prompts per-cluster)')
    a_set_target.add_argument('--show-diff', action='store_true', help='Show diff after setting the revision (no sync)')

    return parser


def run(argv=None):
    parser = build_parser()
    # Enable argcomplete if available
    if argcomplete is not None:
        try:
            argcomplete.autocomplete(parser)
        except Exception:
            pass

    args = parser.parse_args(argv)
    setup_logging(args.verbose)
    manager = ArgoCDManager(verbose=args.verbose, no_color=args.no_color, allow_patch=getattr(args, 'allow_patch', False))

    if not args.command:
        parser.print_help()
        return

    # Editor override precedence: command-line edit parser > top-level --editor > $EDITOR

    if args.command == 'list':
        manager.list_connections(detailed=args.detailed)
    elif args.command == 'add':
        cmd = ' '.join(args.cmd)
        manager.add_connection(args.name, cmd)
    elif args.command == 'remove':
        manager.remove_connection(args.name)
    elif args.command == 'export':
        manager.export_config(args.file)
    elif args.command == 'import':
        manager.import_config(args.file, merge=args.merge)
    elif args.command == 'edit':
        # Editor precedence: --editor on subcommand > top-level --editor > $EDITOR > vi
        editor = getattr(args, 'editor', None) or getattr(args, 'editor', None) or os.environ.get('EDITOR') or 'vi'
        from .config import DEFAULT_CONFIG_PATH
        config_path = DEFAULT_CONFIG_PATH
        # Launch editor
        import subprocess
        subprocess.call([editor, config_path])
    elif args.command == 'proj':
        if args.proj_command == 'list':
            projs = manager.list_projects(args.cluster)
            if projs:
                print('\nProjects:')
                for p in projs:
                    print(' -', p.get('metadata', {}).get('name'))
        elif args.proj_command == 'get':
            status = manager.get_project_status(args.cluster, args.project)
            if status:
                print(json.dumps(status, indent=2))
        elif args.proj_command == 'apps':
            manager.show_project_apps_status(args.cluster, args.project, watch=args.watch)
    elif args.command == 'app':
        if args.app_command == 'list':
            apps = manager.list_applications(args.cluster, args.project)
            if apps:
                for a in apps:
                    print(a.get('metadata', {}).get('name'))
        elif args.app_command == 'get':
            status = manager.get_application_status(args.cluster, args.app)
            if not status:
                return
            if args.format == 'json':
                print(json.dumps(status, indent=2))
            elif args.format == 'summary':
                manager.print_application_summary(status)
            elif args.format == 'table':
                manager.print_application_table(status)
        elif args.app_command == 'get-multi':
            clusters = args.clusters
            app = args.app
            if args.format == 'json':
                result = {}
                for c in clusters:
                    result[c] = manager.get_application_status(c, app)
                print(json.dumps(result, indent=2))
            elif args.format == 'summary':
                for c in clusters:
                    print(f"\nCluster: {c}")
                    status = manager.get_application_status(c, app)
                    if status:
                        manager.print_application_summary(status)
                    else:
                        print(f"{Colors.WARNING}No data for {c}{Colors.ENDC}")
            elif args.format == 'table':
                for c in clusters:
                    print(f"\nCluster: {c}")
                    status = manager.get_application_status(c, app)
                    if status:
                        manager.print_application_table(status)
                    else:
                        print(f"{Colors.WARNING}No data for {c}{Colors.ENDC}")
        elif args.app_command == 'diff':
            manager.visualize_diff(args.cluster, args.app)
        elif args.app_command == 'overview':
            import fnmatch
            raw_patterns = args.clusters
            app = args.app
            show_diff = args.show_diff
            targets_full = getattr(args, 'targets_full', False)

            available = list(manager.config.keys())
            clusters = []
            for p in raw_patterns:
                pl = p.lower()
                matches = [c for c in available if fnmatch.fnmatch(c.lower(), pl)]
                if not matches:
                    print(f"{Colors.WARNING}Pattern '{p}' did not match any configured clusters{Colors.ENDC}")
                else:
                    clusters.extend(matches)

            seen = set()
            clusters = [c for c in clusters if not (c in seen or seen.add(c))]
            if not clusters:
                print(f"{Colors.FAIL}No clusters to operate on.{Colors.ENDC}")
                return

            # Collect data quietly first to avoid interleaved login output.
            import io
            import contextlib

            rows = []  # tuples: (cluster, target_str, sync_status, health_status, diff_out)
            auth_messages = []

            for c in clusters:
                # Try to fetch quietly (suppress printed login messages)
                status = manager._get_application_status(c, app, quiet=True)
                if status is None:
                    # likely needs authentication or failed; run login flow but capture output
                    buf_out = io.StringIO()
                    buf_err = io.StringIO()
                    with contextlib.redirect_stdout(buf_out), contextlib.redirect_stderr(buf_err):
                        try:
                            manager._handle_oidc_login(c, quiet=False)
                        except Exception:
                            pass
                    combined = buf_out.getvalue().strip()
                    errtxt = buf_err.getvalue().strip()
                    if combined or errtxt:
                        auth_messages.append((c, combined + ("\n" + errtxt if errtxt else "")))
                    # try fetching again quietly
                    status = manager._get_application_status(c, app, quiet=True)

                # collect target/diff info even if status is None
                if not status:
                    rows.append((c, '<no-data>', '-', '-', None))
                    continue
                # collect target revisions
                spec = status.get('spec', {})
                sources = []
                if 'sources' in spec and isinstance(spec['sources'], list):
                    sources = spec['sources']
                elif 'source' in spec and isinstance(spec['source'], dict):
                    sources = [spec['source']]
                targets = []
                for s in sources:
                    repo = s.get('repoURL') or s.get('helm', {}).get('repo') or s.get('chart') or s.get('path')
                    rev = s.get('targetRevision') or s.get('ref') or ''
                    if repo:
                        targets.append(f"{repo}@{rev}" if rev else repo)
                    else:
                        targets.append(rev or '<unknown>')

                # keep targets as a list of repo@rev strings (one per source)
                target_list = targets
                sync_status = status.get('status', {}).get('sync', {}).get('status', '-')
                health_status = status.get('status', {}).get('health', {}).get('status', '-')

                # Truncate target string for table but keep a readable preview.
                # When verbose, show the full target to be clearer.
                # Colorize sync and health statuses
                sync_color = Colors.WARNING if sync_status == 'OutOfSync' else (Colors.OKGREEN if sync_status == 'Synced' else Colors.ENDC)
                health_color = Colors.OKGREEN if health_status == 'Healthy' else (Colors.WARNING if health_status == 'Degraded' else (Colors.FAIL if health_status == 'Missing' else Colors.ENDC))

                # check diff presence (fast): fetch quietly
                diff_out = manager._get_application_diff(c, app, quiet=True)

                rows.append((c, target_list, sync_status, health_status, diff_out))
            # Compute target column width to fit the full target strings, but respect terminal width
            if rows:
                # compute maximum length of individual target entries (repo@rev)
                max_target_len = 0
                for (_, tlist, _, _, _) in rows:
                    for t in (tlist or []):
                        if len(t) > max_target_len:
                            max_target_len = len(t)
            else:
                max_target_len = 40

            # Determine terminal width and compute available space for the target column
            term_width = shutil.get_terminal_size((120, 20)).columns
            fixed_cols = 20 + 1 + 12 + 1 + 10 + 1 + 6  # cluster + spaces + sync + health + diff
            # Allow a small margin
            avail = max(20, term_width - fixed_cols - 4)

            # Respect a reasonable maximum to avoid extremely wide tables; allow long targets but cap at 200
            target_col_w = min(max(20, max_target_len), 200, avail)

            # Build and print compact table header
            print(f"\n{Colors.BOLD}{'CLUSTER':<20} {'TARGET(s)':<{target_col_w}} {'SYNC':<12} {'HEALTH':<10} {'DIFF':<6}{Colors.ENDC}")
            print('-' * (20 + 1 + target_col_w + 1 + 12 + 1 + 10 + 1 + 6))

            # Render rows with wrapped TARGET(s) cell so the table behaves like a true table
            for (c, target_list, sync_status, health_status, diff_out) in rows:
                # Build lines for the target cell: one entry per source; wrap long URLs per-entry
                lines = []
                if not target_list:
                    lines = ['']
                else:
                    for entry in target_list:
                        if not entry:
                            continue
                        # split into repo and revision (last '@') so revision can be shown on its own line
                        repo_part, sep, rev_part = entry.rpartition('@')
                        if sep:
                            repo = repo_part
                            rev = rev_part
                        else:
                            repo = entry
                            rev = None

                        # wrap the repo part if needed
                        if len(repo) <= target_col_w:
                            lines.append(repo)
                        else:
                            wrapped_repo = textwrap.wrap(repo, width=target_col_w, break_long_words=True, break_on_hyphens=False)
                            if wrapped_repo:
                                lines.extend(wrapped_repo)
                            else:
                                lines.append(repo)

                        # add a dedicated revision line like '@main' for clarity
                        if rev:
                            rev_line = f"@{rev}"
                            lines.append(rev_line)
                diff_flag = 'Yes' if diff_out else 'No'
                sync_color = Colors.WARNING if sync_status == 'OutOfSync' else (Colors.OKGREEN if sync_status == 'Synced' else Colors.ENDC)
                health_color = Colors.OKGREEN if health_status == 'Healthy' else (Colors.WARNING if health_status == 'Degraded' else (Colors.FAIL if health_status == 'Missing' else Colors.ENDC))

                # Print first line with cluster and status columns
                first = lines[0]
                print(f"{c:<20} {first:<{target_col_w}} {sync_color}{sync_status:<12}{Colors.ENDC} {health_color}{health_status:<10}{Colors.ENDC} {diff_flag:<6}")

                # Print continuation lines for wrapped TARGET(s) (blank for other columns)
                for cont in lines[1:]:
                    print(f"{'':<20} {cont:<{target_col_w}} {'':<12} {'':<10} {'':<6}")

            # After printing the table, print any auth/login messages (so they don't interleave with the table)
            if auth_messages:
                for cl, msg in auth_messages:
                    if msg:
                        print(msg)

            # If requested, show diffs after the table and login messages
            if show_diff:
                for (c, _target_list, sync_status, health_status, diff_out) in rows:
                    if diff_out:
                        print('\n')
                        manager.visualize_diff(c, app)
                        print('\n')
        elif args.app_command == 'set-target':
            import fnmatch

            raw_patterns = args.clusters
            app = args.app
            rev = args.revision
            repo = args.repo
            idx = args.index
            dry = args.dry_run
            do_sync = args.sync

            # Expand cluster globs against saved config
            available = list(manager.config.keys())
            clusters = []
            for p in raw_patterns:
                # case-insensitive matching: compare lowercase
                pl = p.lower()
                matches = [c for c in available if fnmatch.fnmatch(c.lower(), pl)]
                if not matches:
                    print(f"{Colors.WARNING}Pattern '{p}' did not match any configured clusters{Colors.ENDC}")
                else:
                    clusters.extend(matches)

            # Deduplicate while preserving order
            seen = set()
            clusters = [c for c in clusters if not (c in seen or seen.add(c))]

            if not clusters:
                print(f"{Colors.FAIL}No clusters to operate on.{Colors.ENDC}")
                return

            succeeded = 0
            for c in clusters:
                print(f"[{c}] Setting target revision to '{rev}' for '{app}'")
                ok = manager.set_application_target_revision(c, app, rev, repo=repo, source_index=idx, dry_run=dry)
                if not ok:
                    print(f"{Colors.FAIL}[{c}] Failed to set target revision for {app}{Colors.ENDC}")
                    continue
                succeeded += 1

                # If requested, show diff immediately (no sync). If --sync was passed, retain existing sync flow.
                if getattr(args, 'show_diff', False):
                    print(f"\n[{c}] Showing diff after update:\n")
                    manager.visualize_diff(c, app)

                # If requested, show diff and ask to sync
                if do_sync:
                    # If this was a dry-run, we can't show the true diff because the
                    # revision hasn't actually been applied. Offer to perform the real
                    # apply now so the diff can be shown and sync confirmed.
                    from .manager import confirm_action
                    if dry:
                        resp = confirm_action(f"You ran with --dry-run. Apply the change now for cluster '{c}' so diff can be shown and sync can be confirmed?", default=False)
                        if not resp:
                            print(f"Skipping diff/sync for {app} on {c} (dry-run)")
                            continue
                        # perform the actual apply now
                        print(f"Applying change for real on {c}...")
                        ok_apply = manager.set_application_target_revision(c, app, rev, repo=repo, source_index=idx, dry_run=False)
                        if not ok_apply:
                            print(f"{Colors.FAIL}[{c}] Failed to apply change; skipping diff/sync{Colors.ENDC}")
                            continue

                    print(f"\n[{c}] Showing diff after update:\n")
                    manager.visualize_diff(c, app)
                    # Ask user to confirm sync
                    if confirm_action(f"Sync '{app}' on '{c}' now?", default=False):
                        manager.sync_application(c, app, dry_run=False, prune=False)
                    else:
                        print(f"Skipping sync for {app} on {c}")

            print(f"Done: {succeeded}/{len(clusters)} succeeded")
        elif args.app_command == 'sync':
            # Support glob patterns for cluster names (e.g., paywell-*) so users can target multiple clusters
            import fnmatch
            cluster_arg = args.cluster
            if any(ch in cluster_arg for ch in ['*', '?']):
                available = list(manager.config.keys())
                matches = [c for c in available if fnmatch.fnmatch(c.lower(), cluster_arg.lower())]
                if not matches:
                    print(f"{Colors.WARNING}Pattern '{cluster_arg}' did not match any configured clusters{Colors.ENDC}")
                else:
                    for c in matches:
                        manager.sync_application(c, args.app, dry_run=args.dry_run, prune=args.prune)
            else:
                manager.sync_application(args.cluster, args.app, dry_run=args.dry_run, prune=args.prune)
        elif args.app_command == 'sync-multi':
            s, t = manager.sync_multiple_applications(args.cluster, args.apps, dry_run=args.dry_run, prune=args.prune)
            if s == t:
                print(f"All {t} synced")
            else:
                print(f"Synced {s}/{t}")
        elif args.app_command == 'search':
            manager.search_applications(args.cluster, args.query, args.project)
