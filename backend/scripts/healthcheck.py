"""Standalone health check script for Docker HEALTHCHECK.

Usage:
    python -m backend.scripts.healthcheck

Exits 0 if the backend /health endpoint returns HTTP 200, else 1.
"""

import sys
import urllib.request


def main() -> int:
    port = 9176
    url = f"http://localhost:{port}/health"
    try:
        with urllib.request.urlopen(url, timeout=5) as resp:
            if resp.status == 200:
                return 0
    except Exception:
        pass
    return 1


if __name__ == "__main__":
    sys.exit(main())
