"""Exports the OpenAPI schema (used by energia-web to generate TypeScript types).

Usage:
    uv run python -m app.export_openapi openapi.json   # writes the file (UTF-8)
    uv run python -m app.export_openapi                # prints to stdout
"""

import json
import sys
from typing import Any

from app.config import Settings


def build_schema() -> dict[str, Any]:
    from app.main import create_app

    # Fixed local settings so the output does not depend on the environment.
    app = create_app(Settings(_env_file=None, AMBIENTE="local"))
    return app.openapi()


def main(argv: list[str]) -> None:
    content = json.dumps(build_schema(), indent=2, ensure_ascii=False) + "\n"
    if len(argv) > 1:
        with open(argv[1], "w", encoding="utf-8", newline="\n") as fh:
            fh.write(content)
    else:
        sys.stdout.write(content)


if __name__ == "__main__":
    main(sys.argv)
