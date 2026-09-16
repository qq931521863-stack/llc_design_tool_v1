"""First-class exact-H(z) / C99 handoff page for TTPL PFC."""
from __future__ import annotations

from pathlib import Path

import numpy as np

from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)
from PySide6.QtGui import QFontDatabase

from pfc_design.control import (
    PFCControlHandoff,
    PFCControlLabAnalysis,
    assert_handoff_matches_analysis,
    build_pfc_control_handoff,
)
from power_codegen import (
    export_pfc_exact_hz_manifest,
    generate_ttpl_control_code_exact,
)


class TTPLExactHzView(QWidget):
    """Expose the analyzed current/voltage H(z) as the downstream contract."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.analysis: PFCControlLabAnalysis | None = None
        self.handoff: PFCControlHandoff | None = None

        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(7)

        header = QHBoxLayout()
        title = QLabel("TTPL Exact H(z) / C99 Handoff")
        title.setStyleSheet("font-size:16px;font-weight:650;")
        header.addWidget(title)
        hint = QLabel("Analyzed coefficients → power_sim / C99 / future ngspice · no re-discretization")
        hint.setStyleSheet("color:#667085;")
        header.addWidget(hint)
        header.addStretch(1)
        self.export_button = QPushButton("Export H(z) Manifest")
        self.codegen_button = QPushButton("Generate C99 + H(z) Contract")
        self.export_button.setEnabled(False)
        self.codegen_button.setEnabled(False)
        header.addWidget(self.export_button)
        header.addWidget(self.codegen_button)
        root.addLayout(header)

        boundary = QLabel(
            "H(z) owns the linear controller coefficients. Saturation, anti-windup and controller state semantics are separate implementation metadata and cannot be reconstructed from H(z) alone."
        )
        boundary.setWordWrap(True)
        root.addWidget(boundary)

        self.status = QLabel("Run TTPL Control / Sensing / Bode analysis first.")
        self.status.setWordWrap(True)
        root.addWidget(self.status)

        self.tabs = QTabWidget()
        self.current_text = self._text_view()
        self.voltage_text = self._text_view()
        self.contract_text = self._text_view()
        self.tabs.addTab(self.current_text, "Current H(z)")
        self.tabs.addTab(self.voltage_text, "Voltage H(z)")
        self.tabs.addTab(self.contract_text, "Implementation Contract")
        root.addWidget(self.tabs, 1)

        self.export_button.clicked.connect(self._export_manifest)
        self.codegen_button.clicked.connect(self._generate_c99)

    @staticmethod
    def _text_view() -> QPlainTextEdit:
        view = QPlainTextEdit()
        view.setReadOnly(True)
        view.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))
        return view

    @staticmethod
    def _runtime_frequency_error(analysis_tf, artifact) -> float:
        runtime = artifact.runtime_transfer()
        # Avoid DC for PI/PIF integrators and remain below 0.45*Nyquist.
        fmax = 0.45 * artifact.sample_rate_hz
        frequencies = np.geomspace(max(1.0, artifact.sample_rate_hz * 1e-5), fmax, 240)
        expected = analysis_tf.frequency_response(frequencies)
        omega = 2.0 * np.pi * frequencies / runtime.sample_rate_hz
        z = np.exp(1j * omega)
        actual = np.zeros_like(z, dtype=complex)
        denominator = np.zeros_like(z, dtype=complex)
        for index, coefficient in enumerate(runtime.b):
            actual += coefficient * z ** (-index)
        for index, coefficient in enumerate(runtime.a):
            denominator += coefficient * z ** (-index)
        actual /= denominator
        scale = np.maximum(np.abs(expected), 1e-12)
        return float(np.max(np.abs(actual - expected) / scale))

    @staticmethod
    def _controller_text(artifact, analysis_tf, loop_label: str) -> str:
        error = TTPLExactHzView._runtime_frequency_error(analysis_tf, artifact)
        b = ", ".join(f"{value:.12g}" for value in artifact.b)
        a = ", ".join(f"{value:.12g}" for value in artifact.a)
        return (
            f"{loop_label}\n"
            + "=" * 76
            + "\n"
            + f"Kind              : {artifact.kind}\n"
            + f"Sample rate       : {artifact.sample_rate_hz:.12g} Hz\n"
            + f"Sample time       : {artifact.sample_time_s:.12g} s\n"
            + f"b                 : [{b}]\n"
            + f"a                 : [{a}]\n"
            + f"Output limits     : [{artifact.output_min:.12g}, {artifact.output_max:.12g}]\n"
            + f"Difference eq.    : {artifact.difference_equation}\n"
            + f"Convention        : {artifact.coefficient_convention}\n"
            + f"power_sim rel.err : {error:.3e}\n"
            + f"Source            : {artifact.source}\n\n"
            + "NONLINEAR IMPLEMENTATION SEMANTICS\n"
            + f"Saturation/AW     : {artifact.saturation_semantics}\n"
            + f"State semantics   : {artifact.state_semantics}\n"
        )

    def set_analysis(self, analysis: PFCControlLabAnalysis) -> None:
        try:
            handoff = build_pfc_control_handoff(analysis)
            assert_handoff_matches_analysis(analysis, handoff)
            current_error = self._runtime_frequency_error(
                analysis.current_loop.controller, handoff.current
            )
            voltage_error = self._runtime_frequency_error(
                analysis.voltage_loop.controller, handoff.voltage
            )
        except Exception as exc:
            self.analysis = None
            self.handoff = None
            self.export_button.setEnabled(False)
            self.codegen_button.setEnabled(False)
            self.status.setText(f"Exact H(z) handoff failed: {exc}")
            return

        self.analysis = analysis
        self.handoff = handoff
        self.export_button.setEnabled(True)
        self.codegen_button.setEnabled(True)
        self.status.setText(
            f"PASS · analyzed H(z) frozen with no S2Z · power_sim max relative frequency-response error: "
            f"current={current_error:.3e}, voltage={voltage_error:.3e}"
        )
        self.current_text.setPlainText(
            self._controller_text(handoff.current, analysis.current_loop.controller, "CURRENT LOOP EXACT H(z)")
        )
        self.voltage_text.setPlainText(
            self._controller_text(handoff.voltage, analysis.voltage_loop.controller, "VOLTAGE LOOP EXACT H(z)")
        )
        self.contract_text.setPlainText(self._contract_text(handoff))

    @staticmethod
    def _contract_text(handoff: PFCControlHandoff) -> str:
        return (
            "TTPL CONTROLLER HANDOFF CONTRACT\n"
            + "=" * 76
            + "\n"
            + f"Topology          : {handoff.topology}\n"
            + f"Current rate      : {handoff.current.sample_rate_hz:.12g} Hz\n"
            + f"AMC rate          : {handoff.amc_rate_hz:.12g} Hz\n"
            + f"Voltage rate      : {handoff.voltage.sample_rate_hz:.12g} Hz\n"
            + f"Switching rate    : {handoff.switching_frequency_hz:.12g} Hz\n"
            + f"Duty limits       : [{handoff.duty_min:.9g}, {handoff.duty_max:.9g}]\n"
            + f"Provenance        : {handoff.provenance}\n\n"
            + "OWNERSHIP\n"
            + "  Linear controller: exact b/a in this artifact.\n"
            + "  power_sim: receives the same b/a directly; no discretization.\n"
            + "  C99: retains controller-kind-specific nonlinear state/saturation semantics and emits this exact H(z) contract for audit.\n"
            + "  Future ngspice: must consume this artifact rather than recreate the controller from Kp/Ti/pole-zero settings.\n\n"
            + "WARNING\n"
            + "  An exact H(z) does not define anti-windup, reset, saturation ordering, multi-rate scheduling, ADC timing, duty feedforward or protection behavior.\n"
        )

    def _export_manifest(self) -> None:
        if self.analysis is None:
            return
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Export TTPL Exact H(z) Manifest",
            "ttpl_exact_hz_manifest.json",
            "JSON (*.json)",
        )
        if not path:
            return
        try:
            target = export_pfc_exact_hz_manifest(self.analysis, Path(path))
        except Exception as exc:
            QMessageBox.critical(self, "Exact H(z) Export", str(exc))
            return
        QMessageBox.information(self, "Exact H(z) Export", f"Exported:\n{target}")

    def _generate_c99(self) -> None:
        if self.analysis is None:
            return
        directory = QFileDialog.getExistingDirectory(self, "Select TTPL C99 output directory")
        if not directory:
            return
        try:
            result = generate_ttpl_control_code_exact(
                self.analysis,
                Path(directory) / "ttpl_control_generated",
            )
        except Exception as exc:
            QMessageBox.critical(self, "TTPL C99 Generation", str(exc))
            return
        files = "\n".join(str(path) for path in result.files.values())
        QMessageBox.information(
            self,
            "TTPL C99 Generation",
            f"Generated exact-H(z)-audited C99 package:\n{result.directory}\n\n{files}",
        )

    def set_busy(self, busy: bool) -> None:
        enabled = (not busy) and self.analysis is not None
        self.export_button.setEnabled(enabled)
        self.codegen_button.setEnabled(enabled)


__all__ = ["TTPLExactHzView"]
