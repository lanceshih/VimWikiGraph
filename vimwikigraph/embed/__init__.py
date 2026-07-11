import tomllib
from pathlib import Path


def load_config(path="config.toml") -> dict:
    with open(path, "rb") as f:
        cfg = tomllib.load(f)
    # expand ~ in all path values
    for section in cfg.values():
        if isinstance(section, dict):
            for k, v in section.items():
                if isinstance(v, str) and v.startswith("~"):
                    section[k] = str(Path(v).expanduser())
    return cfg
