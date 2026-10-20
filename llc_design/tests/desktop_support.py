"""Test-only isolation for desktop workflows, including standalone probes."""
from __future__ import annotations

from contextlib import contextmanager
import sys

_HOST_MODULES = ("llc_design.gui.main_window", "pfc_design.gui.main_window")


def _ignore_update_check(*args, **kwargs):
    """No workers, dialogs, timers or network operations belong to this fake."""
    return None


@contextmanager
def offline_update_checks():
    """Replace only the updater entry point, never native Qt timer callbacks.

    Patch aliases already imported during collection; later imports bind the
    fake directly. The updater's toolbar lambda also resolves this same fake.
    Keep the context active until owned widgets and queued callbacks are gone.
    """
    from llc_design.gui import updater

    original = updater.check_for_updates
    previous = {name: sys.modules[name].check_for_updates
                for name in _HOST_MODULES if name in sys.modules}
    updater.check_for_updates = _ignore_update_check
    for name in previous:
        sys.modules[name].check_for_updates = _ignore_update_check
    try:
        yield original
    finally:
        updater.check_for_updates = original
        for name in _HOST_MODULES:
            module = sys.modules.get(name)
            if module is not None:
                module.check_for_updates = previous.get(name, original)


def load_root_test_fixtures():
    """Load source-only pytest infrastructure without importing its checkout.

    Installed-wheel qualification must explicitly supply an archived file and
    its expected digest. The archive directory never becomes an import path.
    """
    import hashlib
    import importlib.util
    import os
    from pathlib import Path

    product_root = Path(__file__).resolve().parents[2]
    archived = os.environ.get("PDT_ARCHIVED_ROOT_CONFTEST")
    path = Path(archived).resolve() if archived else product_root / "conftest.py"
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if archived:
        expected = os.environ.get("PDT_ARCHIVED_ROOT_CONFTEST_SHA256")
        assert expected and digest == expected, "Archived root fixture digest mismatch"
    spec = importlib.util.spec_from_file_location("pdt_source_only_root_fixtures", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module._test_fixture_identity = {
        "origin": str(path), "sha256": digest,
        "kind": "archived-source-only" if archived else "checkout-source-only",
    }
    record_test_fixture_proof(module)
    return module


def record_test_fixture_proof(fixtures):
    """Assert child import isolation and retain source-only fixture provenance."""
    import hashlib
    import json
    import os
    from pathlib import Path

    product_root = Path(__file__).resolve().parents[2]
    products = {"llc_design", "pfc_design", "dab_design", "power_stage_design",
                "power_control_tools", "power_codegen", "power_sim", "power_agent",
                "webapp", "backend", "engineering_data"}
    origins = {name: str(Path(module.__file__).resolve())
               for name, module in tuple(sys.modules.items())
               if name.split(".")[0] in products and getattr(module, "__file__", None)}
    assert all(Path(path).is_relative_to(product_root) for path in origins.values()), origins
    support = Path(__file__).resolve()
    support_files = {name: {"origin": str(path),
                            "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
                     for name in ("desktop_support.py", "root_fixture_plugin.py")
                     for path in (support.with_name(name),)}
    archived = os.environ.get("PDT_ARCHIVED_ROOT_CONFTEST")
    if archived:
        archive_root = Path(archived).resolve().parent
        assert not product_root.is_relative_to(archive_root)
        assert archive_root not in {Path(path).resolve() for path in sys.path if path}
        for identity in support_files.values():
            path = Path(identity["origin"])
            archived_support = archive_root / path.relative_to(product_root)
            assert path.read_bytes() == archived_support.read_bytes(), "Test support archive bytes differ"
    proof = {"pid": os.getpid(), "root_fixture": fixtures._test_fixture_identity,
             "product_root": str(product_root), "product_origins": origins,
             "test_support_files": support_files}
    destination = os.environ.get("PDT_TEST_FIXTURE_PROOF")
    if destination:
        with open(destination, "a", encoding="utf-8") as stream:
            stream.write(json.dumps(proof, sort_keys=True) + "\n")
    return proof
