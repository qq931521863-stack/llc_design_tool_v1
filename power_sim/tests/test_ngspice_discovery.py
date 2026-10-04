from power_sim.spice import shared


def test_loader_preserves_os_failure_diagnostics(monkeypatch):
    monkeypatch.setattr(shared, "_library_candidates", lambda: ["bad.dll", "good.dll"])
    def load(path):
        if path == "bad.dll":
            raise OSError("wrong architecture")
        return object()
    monkeypatch.setattr(shared.ct, "CDLL", load)
    diagnostics = []
    assert shared.find_ngspice_shared_library(diagnostics=diagnostics) == "good.dll"
    assert diagnostics == ["bad.dll: wrong architecture"]
    assert shared.find_ngspice_shared_library("bad.dll") is None
