import os
import json
from typing import Dict

DEFAULT_CONFIG = {
    "paywell-prod": "argocd login argocd.k8s.pay-well.sk --sso --skip-test-tls --grpc-web --insecure",
    "paywell-acc": "argocd login argocd.k8s-acc.pay-well.sk --sso --skip-test-tls --grpc-web --insecure",
}

DEFAULT_CONFIG_PATH = os.path.expanduser("~/.argocd_urls.json")


class ConfigurationError(Exception):
    pass


def load_config(path: str = DEFAULT_CONFIG_PATH) -> Dict:
    try:
        if not os.path.exists(path):
            with open(path, 'w') as f:
                json.dump(DEFAULT_CONFIG, f, indent=2)
            return DEFAULT_CONFIG.copy()

        with open(path, 'r') as f:
            cfg = json.load(f)

        if not cfg:
            raise ConfigurationError("Configuration file is empty")

        return cfg
    except json.JSONDecodeError as e:
        raise ConfigurationError(f"Invalid JSON in config file: {e}")
    except Exception as e:
        raise ConfigurationError(f"Failed to load config: {e}")


def save_config(config: Dict, path: str = DEFAULT_CONFIG_PATH):
    try:
        with open(path, 'w') as f:
            json.dump(config, f, indent=2)
    except Exception as e:
        raise ConfigurationError(f"Failed to save config: {e}")
