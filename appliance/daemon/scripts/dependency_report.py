"""Emit installed Python dependency and license metadata as JSON."""

from __future__ import annotations

import json
from importlib.metadata import distributions


def main() -> None:
    packages = []
    for distribution in distributions():
        metadata = distribution.metadata
        packages.append(
            {
                "name": metadata.get("Name", distribution.name),
                "version": distribution.version,
                "license": metadata.get("License-Expression")
                or metadata.get("License")
                or "UNKNOWN",
            }
        )
    packages.sort(key=lambda package: package["name"].casefold())
    print(json.dumps(packages, indent=2))


if __name__ == "__main__":
    main()
