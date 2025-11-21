#!/usr/bin/env python3

import sys
import subprocess
import os
import json
import argparse
from typing import Dict, List, Optional, Tuple
import tempfile
import difflib
from datetime import datetime
import time
from enum import Enum
import logging

# Configuration
CONFIG_FILE = os.path.expanduser("~/.argocd_urls.json")
LOG_FILE = os.path.expanduser("~/.argocd_manager.log")

DEFAULT_CONFIG = {
    "default-prod": "argocd login argocd.k8s.default.com --sso --skip-test-tls --grpc-web --insecure",
    "default-acc": "argocd login argocd.k8s-acc.default.com --sso --skip-test-tls --grpc-web --insecure"
}

# ANSI Color codes
class Colors:
    HEADER = '\033[95m'
    OKBLUE = '\033[94m'
    OKCYAN = '\033[96m'
    OKGREEN = '\033[92m'
    WARNING = '\033[93m'
    FAIL = '\033[91m'
    ENDC = '\033[0m'
    BOLD = '\033[1m'
    UNDERLINE = '\033[4m'

    @staticmethod
    def disable():
        Colors.HEADER = ''
        Colors.OKBLUE = ''
        Colors.OKCYAN = ''
        Colors.OKGREEN = ''
        Colors.WARNING = ''
        Colors.FAIL = ''
        Colors.ENDC = ''
        Colors.BOLD = ''
        Colors.UNDERLINE = ''

class SyncStatus(Enum):
    SYNCED = "Synced"
    OUT_OF_SYNC = "OutOfSync"
    UNKNOWN = "Unknown"

class HealthStatus(Enum):
    HEALTHY = "Healthy"
    PROGRESSING = "Progressing"
    DEGRADED = "Degraded"
    SUSPENDED = "Suspended"
    MISSING = "Missing"
    UNKNOWN = "Unknown"

class ArgoCDError(Exception):
    """Base exception for ArgoCD operations"""
    pass

class ConnectionError(ArgoCDError):
    """Raised when connection to ArgoCD fails"""
    pass

class CommandExecutionError(ArgoCDError):
    """Raised when ArgoCD command execution fails"""
    pass

class ConfigurationError(ArgoCDError):
    """Raised when configuration is invalid"""
    pass

def setup_logging(verbose: bool = False):
    """Setup logging configuration"""
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=[
            logging.FileHandler(LOG_FILE),
            logging.StreamHandler() if verbose else logging.NullHandler()
        ]
    )

def print_success(msg: str):
    """Print success message in green"""
    print(f"{Colors.OKGREEN}✓ {msg}{Colors.ENDC}")

def print_error(msg: str):
    """Print error message in red"""
    print(f"{Colors.FAIL}✗ {msg}{Colors.ENDC}", file=sys.stderr)

def print_warning(msg: str):
    """Print warning message in yellow"""
    print(f"{Colors.WARNING}⚠ {msg}{Colors.ENDC}")

def print_info(msg: str):
    """Print info message in cyan"""
    print(f"{Colors.OKCYAN}ℹ {msg}{Colors.ENDC}")

def print_header(msg: str):
    """Print header message"""
    print(f"\n{Colors.BOLD}{Colors.HEADER}{msg}{Colors.ENDC}")
    print(f"{Colors.HEADER}{'=' * len(msg)}{Colors.ENDC}\n")

def fuzzy_match(query: str, choices: List[str], threshold: float = 0.6) -> Optional[str]:
    """Find the best fuzzy match for a query in choices"""
    if query in choices:
        return query
    
    best_match = None
    best_ratio = 0.0
    
    for choice in choices:
        ratio = difflib.SequenceMatcher(None, query.lower(), choice.lower()).ratio()
        if ratio > best_ratio and ratio >= threshold:
            best_ratio = ratio
            best_match = choice
    
    return best_match

def confirm_action(prompt: str, default: bool = False) -> bool:
    """Ask user for confirmation"""
    choices = "Y/n" if default else "y/N"
    response = input(f"{prompt} [{choices}]: ").strip().lower()
    
    if not response:
        return default
    
    return response in ['y', 'yes']

def retry_on_failure(func, max_attempts: int = 3, delay: int = 2):
    """Retry a function on failure"""
    logger = logging.getLogger(__name__)
    
    for attempt in range(max_attempts):
        try:
            return func()
        except Exception as e:
            logger.warning(f"Attempt {attempt + 1}/{max_attempts} failed: {e}")
            if attempt < max_attempts - 1:
                print_warning(f"Retrying in {delay} seconds...")
                time.sleep(delay)
            else:
                raise
    
class ArgoCDManager:
    def __init__(self, verbose: bool = False, no_color: bool = False):
        self.logger = logging.getLogger(__name__)
        self.config = self.load_config()
        self.verbose = verbose
        
        if no_color:
            Colors.disable()
    
    def load_config(self) -> Dict:
        """Load the configuration file or create a default one if it doesn't exist."""
        try:
            if not os.path.exists(CONFIG_FILE):
                with open(CONFIG_FILE, 'w') as f:
                    json.dump(DEFAULT_CONFIG, f, indent=2)
                print_info(f"Created config file at {CONFIG_FILE}")
                return DEFAULT_CONFIG

            with open(CONFIG_FILE, 'r') as f:
                config = json.load(f)
                
            if not config:
                raise ConfigurationError("Configuration file is empty")
            
            return config
        except json.JSONDecodeError as e:
            raise ConfigurationError(f"Invalid JSON in config file: {e}")
        except Exception as e:
            raise ConfigurationError(f"Failed to load config: {e}")

    def save_config(self, config: Dict):
        """Save the configuration to file."""
        try:
            with open(CONFIG_FILE, 'w') as f:
                json.dump(config, f, indent=2)
            self.logger.info(f"Configuration saved to {CONFIG_FILE}")
        except Exception as e:
            raise ConfigurationError(f"Failed to save config: {e}")

    def export_config(self, output_file: str):
        """Export configuration to a file"""
        try:
            with open(output_file, 'w') as f:
                json.dump(self.config, f, indent=2)
            print_success(f"Configuration exported to {output_file}")
        except Exception as e:
            print_error(f"Failed to export config: {e}")

    def import_config(self, input_file: str, merge: bool = False):
        """Import configuration from a file"""
        try:
            with open(input_file, 'r') as f:
                new_config = json.load(f)
            
            if merge:
                self.config.update(new_config)
                print_info("Merging configurations...")
            else:
                self.config = new_config
                print_info("Replacing configuration...")
            
            self.save_config(self.config)
            print_success(f"Configuration imported from {input_file}")
        except Exception as e:
            print_error(f"Failed to import config: {e}")

    def list_connections(self, detailed: bool = False):
        """List all available ArgoCD connections."""
        if not self.config:
            print_warning("No ArgoCD connections configured")
            return
        
        print_header("Available ArgoCD Connections")
        
        for idx, (name, command) in enumerate(self.config.items(), 1):
            if detailed:
                print(f"{Colors.BOLD}{idx}. {name}{Colors.ENDC}")
                print(f"   Command: {Colors.OKCYAN}{command}{Colors.ENDC}")
            else:
                print(f"  {Colors.OKGREEN}{idx}.{Colors.ENDC} {Colors.BOLD}{name}{Colors.ENDC}")

    def add_connection(self, name: str, command: str):
        """Add a new ArgoCD connection."""
        if not name or not command:
            raise ConfigurationError("Connection name and command cannot be empty")
        
        if name in self.config:
            if not confirm_action(f"Connection '{name}' already exists. Overwrite?"):
                print_info("Operation cancelled")
                return
        
        self.config[name] = command
        self.save_config(self.config)
        print_success(f"Added ArgoCD connection: {name}")

    def remove_connection(self, name: str):
        """Remove an ArgoCD connection."""
        if name not in self.config:
            # Try fuzzy matching
            match = fuzzy_match(name, list(self.config.keys()))
            if match:
                print_warning(f"Connection '{name}' not found. Did you mean '{match}'?")
                if confirm_action(f"Remove '{match}' instead?"):
                    name = match
                else:
                    return
            else:
                print_error(f"Connection '{name}' not found")
                return
        
        if not confirm_action(f"Remove connection '{name}'?"):
            print_info("Operation cancelled")
            return
        
        del self.config[name]
        self.save_config(self.config)
        print_success(f"Removed ArgoCD connection: {name}")

    def connect(self, name: str) -> int:
        """Connect to a specific ArgoCD instance."""
        if name not in self.config:
            match = fuzzy_match(name, list(self.config.keys()))
            if match:
                print_warning(f"Connection '{name}' not found. Using '{match}' instead")
                name = match
            else:
                print_error(f"ArgoCD connection '{name}' not found")
                self.list_connections()
                return 1

        command = self.config[name].split()
        print_info(f"Connecting to {name}...")
        
        try:
            result = subprocess.call(command)
            if result == 0:
                print_success(f"Successfully connected to {name}")
            else:
                print_error(f"Connection failed with exit code {result}")
            return result
        except Exception as e:
            raise ConnectionError(f"Failed to connect: {e}")

    def validate_cluster(self, cluster_name: str) -> str:
        """Validate cluster name and return the correct name (with fuzzy matching)"""
        if cluster_name not in self.config:
            match = fuzzy_match(cluster_name, list(self.config.keys()))
            if match:
                print_warning(f"Cluster '{cluster_name}' not found. Using '{match}' instead")
                return match
            else:
                raise ConfigurationError(f"Cluster '{cluster_name}' not found")
        return cluster_name

    def execute_argocd_command(self, cluster_name: str, argocd_args: List[str], 
                               timeout: int = 30) -> Optional[str]:
        """Execute an ArgoCD command against a specific cluster."""
        cluster_name = self.validate_cluster(cluster_name)
        
        login_cmd = self.config[cluster_name]
        login_parts = login_cmd.split()
        
        server_url = None
        connection_args = []
        
        i = 0
        while i < len(login_parts):
            part = login_parts[i]
            if part == 'login':
                i += 1
                if i < len(login_parts) and not login_parts[i].startswith('--'):
                    server_url = login_parts[i]
                    i += 1
                while i < len(login_parts):
                    connection_args.append(login_parts[i])
                    i += 1
                break
            else:
                i += 1

        cmd = ['argocd']
        cmd.extend(argocd_args)
        
        if server_url:
            cmd.extend(['--server', server_url])
        cmd.extend(connection_args)

        self.logger.debug(f"Executing command: {' '.join(cmd)}")

        try:
            result = subprocess.run(
                cmd, 
                capture_output=True, 
                text=True, 
                check=True,
                timeout=timeout
            )
            return result.stdout
        except subprocess.TimeoutExpired:
            raise CommandExecutionError(f"Command timed out after {timeout} seconds")
        except subprocess.CalledProcessError as e:
            error_msg = e.stderr.strip() if e.stderr else "Unknown error"
            # Mask potential sensitive data in errors
            error_msg = error_msg.replace(server_url or "", "<server>")
            raise CommandExecutionError(f"Command failed: {error_msg}")
        except Exception as e:
            raise CommandExecutionError(f"Unexpected error: {e}")

    def parse_json_output(self, output: Optional[str], error_context: str) -> Optional[Dict]:
        """Parse JSON output with error handling"""
        if not output:
            return None
        
        output = output.strip()
        if not output:
            return None
        
        try:
            return json.loads(output)
        except json.JSONDecodeError as e:
            self.logger.error(f"Failed to parse JSON for {error_context}: {e}")
            print_error(f"Invalid JSON response for {error_context}")
            return None

    def list_projects(self, cluster_name: str) -> Optional[List[Dict]]:
        """List all projects in a cluster."""
        try:
            output = self.execute_argocd_command(cluster_name, ['proj', 'list', '--output', 'json'])
            return self.parse_json_output(output, "project list")
        except CommandExecutionError as e:
            print_error(f"Failed to list projects: {e}")
            return None

    def get_project_status(self, cluster_name: str, project_name: str) -> Optional[Dict]:
        """Get detailed status of a project."""
        try:
            output = self.execute_argocd_command(
                cluster_name, 
                ['proj', 'get', project_name, '--output', 'json']
            )
            return self.parse_json_output(output, f"project {project_name}")
        except CommandExecutionError as e:
            print_error(f"Failed to get project status: {e}")
            return None

    def list_applications(self, cluster_name: str, project_name: Optional[str] = None) -> Optional[List[Dict]]:
        """List applications in a cluster or specific project."""
        try:
            cmd = ['app', 'list', '--output', 'json']
            if project_name:
                cmd.extend(['--project', project_name])
            
            output = self.execute_argocd_command(cluster_name, cmd)
            return self.parse_json_output(output, "application list")
        except CommandExecutionError as e:
            print_error(f"Failed to list applications: {e}")
            return None

    def get_application_status(self, cluster_name: str, app_name: str) -> Optional[Dict]:
        """Get detailed status of an application."""
        try:
            output = self.execute_argocd_command(
                cluster_name, 
                ['app', 'get', app_name, '--output', 'json']
            )
            return self.parse_json_output(output, f"application {app_name}")
        except CommandExecutionError as e:
            print_error(f"Failed to get application status: {e}")
            return None

    def get_application_diff(self, cluster_name: str, app_name: str) -> Optional[str]:
        """Get diff of an application."""
        try:
            return self.execute_argocd_command(cluster_name, ['app', 'diff', app_name])
        except CommandExecutionError as e:
            print_error(f"Failed to get application diff: {e}")
            return None

    def sync_application(self, cluster_name: str, app_name: str, 
                        dry_run: bool = False, prune: bool = False) -> bool:
        """Sync an application."""
        try:
            if dry_run:
                print_info(f"DRY RUN: Would sync application '{app_name}'")
                diff = self.get_application_diff(cluster_name, app_name)
                if diff:
                    print(diff)
                return True
            
            if not confirm_action(f"Sync application '{app_name}'?", default=True):
                print_info("Sync cancelled")
                return False
            
            cmd = ['app', 'sync', app_name]
            if prune:
                cmd.append('--prune')
            
            print_info(f"Syncing {app_name}...")
            output = self.execute_argocd_command(cluster_name, cmd, timeout=300)
            
            if output:
                print_success(f"Successfully synced {app_name}")
                if self.verbose:
                    print(output)
                return True
            return False
        except CommandExecutionError as e:
            print_error(f"Failed to sync application: {e}")
            return False

    def sync_multiple_applications(self, cluster_name: str, app_names: List[str], 
                                   dry_run: bool = False, prune: bool = False) -> Tuple[int, int]:
        """Sync multiple applications. Returns (success_count, total_count)"""
        if dry_run:
            print_info("DRY RUN MODE - No actual changes will be made")
        
        if not dry_run and not confirm_action(f"Sync {len(app_names)} applications?"):
            print_info("Operation cancelled")
            return (0, len(app_names))
        
        success_count = 0
        for i, app_name in enumerate(app_names, 1):
            print_info(f"[{i}/{len(app_names)}] Processing {app_name}")
            if self.sync_application(cluster_name, app_name, dry_run=dry_run, prune=prune):
                success_count += 1
            time.sleep(1)  # Rate limiting
        
        return (success_count, len(app_names))

    def get_status_color(self, status: str, is_health: bool = False) -> str:
        """Get color for status"""
        if is_health:
            health_map = {
                "Healthy": Colors.OKGREEN,
                "Progressing": Colors.OKBLUE,
                "Degraded": Colors.FAIL,
                "Suspended": Colors.WARNING,
                "Missing": Colors.FAIL,
            }
            return health_map.get(status, Colors.ENDC)
        else:
            sync_map = {
                "Synced": Colors.OKGREEN,
                "OutOfSync": Colors.WARNING,
            }
            return sync_map.get(status, Colors.ENDC)

    def show_project_apps_status(self, cluster_name: str, project_name: str, 
                                watch: bool = False):
        """Show status of all applications in a project."""
        def display():
            apps = self.list_applications(cluster_name, project_name)
            if not apps:
                print_warning(f"No applications found in project '{project_name}'")
                return
            
            if watch:
                os.system('clear' if os.name == 'posix' else 'cls')
            
            print_header(f"Applications in '{project_name}' on '{cluster_name}'")
            print(f"Last updated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
            
            # Table header
            print(f"{Colors.BOLD}{'NAME':<35} {'NAMESPACE':<20} {'SYNC':<12} {'HEALTH':<12}{Colors.ENDC}")
            print("-" * 80)
            
            out_of_sync = []
            degraded = []
            
            for app in apps:
                name = app.get('metadata', {}).get('name', 'N/A')
                namespace = app.get('spec', {}).get('destination', {}).get('namespace', 'N/A')
                sync_status = app.get('status', {}).get('sync', {}).get('status', 'Unknown')
                health_status = app.get('status', {}).get('health', {}).get('status', 'Unknown')
                
                sync_color = self.get_status_color(sync_status, is_health=False)
                health_color = self.get_status_color(health_status, is_health=True)
                
                print(f"{name:<35} {namespace:<20} {sync_color}{sync_status:<12}{Colors.ENDC} {health_color}{health_status:<12}{Colors.ENDC}")
                
                if sync_status == "OutOfSync":
                    out_of_sync.append(name)
                if health_status in ["Degraded", "Missing"]:
                    degraded.append(name)
            
            print()
            print(f"Total: {len(apps)} | ", end="")
            if out_of_sync:
                print(f"{Colors.WARNING}Out of Sync: {len(out_of_sync)}{Colors.ENDC} | ", end="")
            if degraded:
                print(f"{Colors.FAIL}Degraded: {len(degraded)}{Colors.ENDC}", end="")
            print()
            
            if out_of_sync and not watch:
                print(f"\n{Colors.WARNING}Out of sync applications:{Colors.ENDC}")
                for app in out_of_sync:
                    print(f"  - {app}")
            
            if degraded and not watch:
                print(f"\n{Colors.FAIL}Degraded applications:{Colors.ENDC}")
                for app in degraded:
                    print(f"  - {app}")
        
        if watch:
            try:
                while True:
                    display()
                    time.sleep(5)
            except KeyboardInterrupt:
                print("\n\nWatch mode stopped")
        else:
            display()

    def visualize_diff(self, cluster_name: str, app_name: str):
        """Visualize the diff of an application."""
        diff_output = self.get_application_diff(cluster_name, app_name)
        if diff_output:
            print_header(f"Diff for '{app_name}'")
            print(diff_output)
        else:
            print_info(f"No differences found for '{app_name}'")

    def search_applications(self, cluster_name: str, query: str, project: Optional[str] = None):
        """Search for applications by name"""
        apps = self.list_applications(cluster_name, project)
        if not apps:
            return
        
        matches = [app for app in apps if query.lower() in app.get('metadata', {}).get('name', '').lower()]
        
        if not matches:
            print_warning(f"No applications found matching '{query}'")
            return
        
        print_header(f"Applications matching '{query}'")
        for app in matches:
            name = app.get('metadata', {}).get('name', 'N/A')
            sync_status = app.get('status', {}).get('sync', {}).get('status', 'Unknown')
            health_status = app.get('status', {}).get('health', {}).get('status', 'Unknown')
            print(f"  {Colors.BOLD}{name}{Colors.ENDC} - Sync: {sync_status}, Health: {health_status}")

def main():
    parser = argparse.ArgumentParser(
        description='ArgoCD Manager - Professional CLI for managing ArgoCD clusters',
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    
    parser.add_argument('-v', '--verbose', action='store_true', help='Enable verbose logging')
    parser.add_argument('--no-color', action='store_true', help='Disable colored output')
    
    subparsers = parser.add_subparsers(dest='command', help='Available commands')

    # List connections
    list_parser = subparsers.add_parser('list', help='List all ArgoCD connections')
    list_parser.add_argument('-d', '--detailed', action='store_true', help='Show detailed information')

    # Add connection
    add_parser = subparsers.add_parser('add', help='Add a new ArgoCD connection')
    add_parser.add_argument('name', help='Name of the connection')
    add_parser.add_argument('command', help='ArgoCD login command', nargs=argparse.REMAINDER)

    # Remove connection
    remove_parser = subparsers.add_parser('remove', help='Remove an ArgoCD connection')
    remove_parser.add_argument('name', help='Name of the connection to remove')

    # Connect
    connect_parser = subparsers.add_parser('connect', help='Connect to an ArgoCD instance')
    connect_parser.add_argument('name', help='Name of the connection')

    # Export/Import
    export_parser = subparsers.add_parser('export', help='Export configuration')
    export_parser.add_argument('file', help='Output file path')

    import_parser = subparsers.add_parser('import', help='Import configuration')
    import_parser.add_argument('file', help='Input file path')
    import_parser.add_argument('--merge', action='store_true', help='Merge with existing config')

    # Project operations
    proj_parser = subparsers.add_parser('proj', help='Project operations')
    proj_subparsers = proj_parser.add_subparsers(dest='proj_command', help='Project commands')

    list_proj_parser = proj_subparsers.add_parser('list', help='List projects in a cluster')
    list_proj_parser.add_argument('cluster', help='Cluster name')

    get_proj_parser = proj_subparsers.add_parser('get', help='Get project status')
    get_proj_parser.add_argument('cluster', help='Cluster name')
    get_proj_parser.add_argument('project', help='Project name')

    apps_proj_parser = proj_subparsers.add_parser('apps', help='Show applications in a project')
    apps_proj_parser.add_argument('cluster', help='Cluster name')
    apps_proj_parser.add_argument('project', help='Project name')
    apps_proj_parser.add_argument('-w', '--watch', action='store_true', help='Watch mode (refresh every 5s)')

    # Application operations
    app_parser = subparsers.add_parser('app', help='Application operations')
    app_subparsers = app_parser.add_subparsers(dest='app_command', help='Application commands')

    list_app_parser = app_subparsers.add_parser('list', help='List applications')
    list_app_parser.add_argument('cluster', help='Cluster name')
    list_app_parser.add_argument('-p', '--project', help='Filter by project')

    get_app_parser = app_subparsers.add_parser('get', help='Get application status')
    get_app_parser.add_argument('cluster', help='Cluster name')
    get_app_parser.add_argument('app', help='Application name')

    diff_app_parser = app_subparsers.add_parser('diff', help='Show application diff')
    diff_app_parser.add_argument('cluster', help='Cluster name')
    diff_app_parser.add_argument('app', help='Application name')

    sync_app_parser = app_subparsers.add_parser('sync', help='Sync application')
    sync_app_parser.add_argument('cluster', help='Cluster name')
    sync_app_parser.add_argument('app', help='Application name')
    sync_app_parser.add_argument('--dry-run', action='store_true', help='Show what would be synced')
    sync_app_parser.add_argument('--prune', action='store_true', help='Prune resources')

    sync_multi_parser = app_subparsers.add_parser('sync-multi', help='Sync multiple applications')
    sync_multi_parser.add_argument('cluster', help='Cluster name')
    sync_multi_parser.add_argument('apps', nargs='+', help='Application names')
    sync_multi_parser.add_argument('--dry-run', action='store_true', help='Show what would be synced')
    sync_multi_parser.add_argument('--prune', action='store_true', help='Prune resources')

    search_app_parser = app_subparsers.add_parser('search', help='Search applications')
    search_app_parser.add_argument('cluster', help='Cluster name')
    search_app_parser.add_argument('query', help='Search query')
    search_app_parser.add_argument('-p', '--project', help='Filter by project')

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return

    setup_logging(args.verbose)
    
    try:
        manager = ArgoCDManager(verbose=args.verbose, no_color=args.no_color)

        if args.command == 'list':
            manager.list_connections(detailed=args.detailed)
        elif args.command == 'add':
            command_str = ' '.join(args.command)
            manager.add_connection(args.name, command_str)
        elif args.command == 'remove':
            manager.remove_connection(args.name)
        elif args.command == 'connect':
            sys.exit(manager.connect(args.name))
        elif args.command == 'export':
            manager.export_config(args.file)
        elif args.command == 'import':
            manager.import_config(args.file, merge=args.merge)
        elif args.command == 'proj':
            if args.proj_command == 'list':
                projects = manager.list_projects(args.cluster)
                if projects:
                    print_header(f"Projects in '{args.cluster}'")
                    for proj in projects:
                        name = proj.get('metadata', {}).get('name', 'N/A')
                        print(f"  {Colors.OKGREEN}•{Colors.ENDC} {name}")
            elif args.proj_command == 'get':
                proj_status = manager.get_project_status(args.cluster, args.project)
                if proj_status:
                    print(json.dumps(proj_status, indent=2))
            elif args.proj_command == 'apps':
                manager.show_project_apps_status(args.cluster, args.project, watch=args.watch)
        elif args.command == 'app':
            if args.app_command == 'list':
                apps = manager.list_applications(args.cluster, args.project)
                if apps:
                    print_header(f"Applications in '{args.cluster}'")
                    for app in apps:
                        name = app.get('metadata', {}).get('name', 'N/A')
                        sync = app.get('status', {}).get('sync', {}).get('status', 'Unknown')
                        health = app.get('status', {}).get('health', {}).get('status', 'Unknown')
                        print(f"  {Colors.BOLD}{name}{Colors.ENDC} - Sync: {sync}, Health: {health}")
            elif args.app_command == 'get':
                app_status = manager.get_application_status(args.cluster, args.app)
                if app_status:
                    print(json.dumps(app_status, indent=2))
            elif args.app_command == 'diff':
                manager.visualize_diff(args.cluster, args.app)
            elif args.app_command == 'sync':
                manager.sync_application(args.cluster, args.app, 
                                        dry_run=args.dry_run, prune=args.prune)
            elif args.app_command == 'sync-multi':
                success, total = manager.sync_multiple_applications(
                    args.cluster, args.apps, 
                    dry_run=args.dry_run, prune=args.prune
                )
                print()
                if success == total:
                    print_success(f"All {total} applications synced successfully")
                else:
                    print_warning(f"Synced {success}/{total} applications")
            elif args.app_command == 'search':
                manager.search_applications(args.cluster, args.query, args.project)
    
    except KeyboardInterrupt:
        print("\n\nOperation interrupted by user")
        sys.exit(130)
    except (ArgoCDError, ConfigurationError, ConnectionError, CommandExecutionError) as e:
        print_error(str(e))
        sys.exit(1)
    except Exception as e:
        print_error(f"Unexpected error: {e}")
        if args.verbose:
            import traceback
            traceback.print_exc()
        sys.exit(1)

from argocd_manager.cli import run


def main():
    run()


if __name__ == "__main__":
    main()