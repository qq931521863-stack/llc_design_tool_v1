"""One bounded child process per local MCP server, with persisted results."""
from __future__ import annotations

import atexit
import hashlib
import os
import subprocess
import sys
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

from .reporting import render_html
from .storage import DesignStore, atomic_json, atomic_text, digest, read_json

TERMINAL = {"COMPLETED", "FAILED", "CANCELLED", "TIMED_OUT"}


class JobManager:
    def __init__(self, store: DesignStore):
        self.store = store
        self._lock = threading.RLock()
        self._active = {}
        atexit.register(self.close)

    def submit(self, task: str, design_id: str, parameters: dict, timeout_seconds: int = 300) -> dict:
        if task not in ("analysis", "optimization", "corners"):
            raise ValueError("Unsupported agent task")
        if type(timeout_seconds) is not int or not 1 <= timeout_seconds <= 600:
            raise ValueError("timeout_seconds must be an integer in 1..600")
        self.store.load(design_id)
        with self._lock:
            if self._active:
                raise ValueError("A calculation is active; poll or cancel it before submitting another")
            request = {"task": task, "design_id": design_id, "parameters": parameters,
                       "timeout_seconds": timeout_seconds, "nonce": uuid.uuid4().hex}
            job_id = digest(request)
            atomic_json(self.store.path("jobs", job_id, ".request.json"), request)
            metadata = {"job_id": job_id, "design_id": design_id, "task": task,
                        "state": "RUNNING", "submitted_at": datetime.now(timezone.utc).isoformat(),
                        "timeout_seconds": timeout_seconds, "verified_for_hardware": False}
            atomic_json(self.store.path("jobs", job_id), metadata)
            env = {**os.environ, "OPENBLAS_NUM_THREADS": "1", "OMP_NUM_THREADS": "1",
                   "MPLCONFIGDIR": str(self.store.root / "cache" / "matplotlib"),
                   "XDG_CACHE_HOME": str(self.store.root / "cache")}
            log_path = self.store.path("jobs", job_id, ".log")
            try:
                with log_path.open("w") as log:
                    process = subprocess.Popen([sys.executable, "-m", "power_agent.worker",
                        str(self.store.root), job_id], cwd=Path(__file__).resolve().parent.parent,
                        env=env, stdin=subprocess.DEVNULL, stdout=log, stderr=log)
            except OSError as exc:
                metadata.update(state="FAILED", error=str(exc))
                atomic_json(self.store.path("jobs", job_id), metadata)
                return metadata
            record = {"process": process, "cancelled": False}
            self._active[job_id] = record
            threading.Thread(target=self._watch, args=(job_id, record, metadata), daemon=True).start()
            return dict(metadata)

    def _watch(self, job_id: str, record: dict, metadata: dict) -> None:
        process = record["process"]
        timed_out = False
        try:
            process.wait(timeout=metadata["timeout_seconds"])
        except subprocess.TimeoutExpired:
            timed_out = True
            process.kill()
            process.wait()
        with self._lock:
            if record["cancelled"]:
                metadata["state"] = "CANCELLED"
            elif timed_out:
                metadata["state"] = "TIMED_OUT"
            else:
                try:
                    request = read_json(self.store.path("jobs", job_id, ".request.json"))
                    if digest(request) != job_id:
                        raise ValueError("Job request failed integrity check")
                    result = read_json(self.store.path("jobs", job_id, ".result.json"))
                    if result.get("design_id") != metadata["design_id"] or result.get("task") != metadata["task"]:
                        raise ValueError("Calculation result identity mismatch")
                    if process.returncode or "error" in result:
                        raise ValueError(result.get("error", f"Worker exited {process.returncode}"))
                    self.store.load(metadata["design_id"])
                    metadata.update(state="COMPLETED", result_sha256=digest(result),
                                    design_status=result["design_status"])
                except (OSError, ValueError, KeyError, TypeError) as exc:
                    metadata.update(state="FAILED", error=str(exc)[:2000])
            metadata["finished_at"] = datetime.now(timezone.utc).isoformat()
            try:
                atomic_json(self.store.path("jobs", job_id), metadata)
            finally:
                self._active.pop(job_id, None)

    def get(self, job_id: str, include_result: bool = False) -> dict:
        with self._lock:
            metadata = read_json(self.store.path("jobs", job_id))
            if metadata["state"] == "RUNNING" and job_id not in self._active:
                return {**metadata, "state": "UNMANAGED",
                        "message": "Owned by another/ended server session; this session cannot cancel it"}
        if metadata["state"] == "COMPLETED":
            result = read_json(self.store.path("jobs", job_id, ".result.json"))
            if digest(result) != metadata["result_sha256"]:
                raise ValueError("Stored result failed integrity check")
            metadata["result_path"] = str(self.store.path("jobs", job_id, ".result.json"))
            if include_result:
                metadata["result"] = result
        return metadata

    def cancel(self, job_id: str) -> dict:
        with self._lock:
            record = self._active.get(job_id)
            if record and record["process"].poll() is None:
                record["cancelled"] = True
                record["process"].kill()
        return self.get(job_id)

    def export(self, job_id: str, format: str = "json") -> dict:
        if format not in ("json", "html"):
            raise ValueError("Export format must be json or html")
        metadata = self.get(job_id, include_result=True)
        if metadata["state"] != "COMPLETED":
            raise ValueError("Only completed calculations may be exported")
        snapshot = read_json(self.store.path("designs", metadata["design_id"]))
        if digest(snapshot) != metadata["design_id"]:
            raise ValueError("Design snapshot failed integrity check")
        document = {"schema": "power-design-toolkit/agent-report-v1", "job": metadata,
                    "design": snapshot, "hardware_status": "NOT_RUN"}
        path = self.store.path("exports", job_id, "." + format)
        if format == "json":
            atomic_json(path, document)
        else:
            atomic_text(path, render_html(document))
        return {"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "job_id": job_id, "design_id": metadata["design_id"], "verified_for_hardware": False}

    def close(self) -> None:
        with self._lock:
            for record in self._active.values():
                if record["process"].poll() is None:
                    record["cancelled"] = True
                    record["process"].kill()


_managers: dict[str, JobManager] = {}
_manager_lock = threading.Lock()


def manager() -> JobManager:
    store = DesignStore()
    key = str(store.root)
    with _manager_lock:
        if key not in _managers:
            _managers[key] = JobManager(store)
        return _managers[key]
