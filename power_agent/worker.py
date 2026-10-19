"""Fixed calculation entry point; no caller-selected commands or file paths."""
from __future__ import annotations

import sys

from .storage import DesignStore, atomic_json, digest, read_json


def main() -> None:
    store = DesignStore(sys.argv[1])
    job_id = sys.argv[2]
    request = read_json(store.path("jobs", job_id, ".request.json"))
    if digest(request) != job_id:
        raise ValueError("Job request failed integrity check")
    try:
        from .workflow import execute
        result = execute(request["task"], store, request["design_id"], request["parameters"])
    except Exception as exc:  # noqa: BLE001 - worker boundary must preserve numerical failures
        result = {"design_id": request["design_id"], "task": request["task"],
                  "error": f"{type(exc).__name__}: {exc}"[:2000]}
    atomic_json(store.path("jobs", job_id, ".result.json"), result)


if __name__ == "__main__":
    main()
