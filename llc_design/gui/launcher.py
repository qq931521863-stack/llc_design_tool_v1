"""Application launcher for the independent power-design workspaces."""

from __future__ import annotations

import textwrap

from PySide6.QtCore import Qt
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QDialog,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
)

from llc_design.core.spec import LLCDesignSpec
from llc_design.gui import theme
from llc_design.gui.help import show_help
from llc_design.i18n import t
from llc_design.gui.main_window import LLCMainWindow
from llc_design.gui.closed_loop_install import install_closed_loop_verification
from llc_design.gui.device_library_install import install_device_library
from llc_design.gui.system_modeling import (
    SystemModelingDesignDialog,
    apply_definition_to_llc_window,
    apply_definition_to_ttpl_window,
)
from pfc_design.gui.main_window import PFCMainWindow
from power_control_tools.gui.fra_advanced import install_advanced_fra_actions
from power_control_tools.gui.fra_loop_designer import FRALoopDesignerWindow
from power_control_tools.gui.main_window import ControlToolsMainWindow
from power_control_tools.system_definition import SystemTopology


class WorkspaceSelectionDialog(QDialog):
    """Initial function selector shown before an engineering workspace."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.selected_workspace: str | None = None
        self.setWindowTitle(t("电源设计工具箱 — 选择设计功能"))
        self.setMinimumSize(1180, 820)
        self.setWindowFlag(Qt.WindowType.WindowContextHelpButtonHint, False)
        self.setStyleSheet(theme.launcher_stylesheet(theme.active_theme()))

        root = QVBoxLayout(self)
        title = QLabel(t("请选择进入的设计工作区"))
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setStyleSheet("font-size: 25px; font-weight: 650; padding: 14px;")
        root.addWidget(title)

        subtitle = QLabel(
            t("V9.3 新增“系统建模与设计”：先逐步定义功率级、采样、ADC/PWM 与数字时序，再进入现有强分析引擎；")
            + t("Expert 用户仍可直接进入 LLC、PFC、Control Tools 或 FRA 工作区。")
        )
        subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        subtitle.setWordWrap(True)
        subtitle.setStyleSheet("font-size: 14px; padding: 2px 30px 14px 30px;")
        root.addWidget(subtitle)

        choices = QGridLayout()
        choices.setHorizontalSpacing(24)
        choices.setVerticalSpacing(16)

        system_button = self._choice_button(
            t("系统建模与设计 / Guided System Design"),
            t("V9.3 主入口：拓扑 → 功率级 → 采样/滤波 → ADC/PWM/延时 → 控制器意图 → 系统复核 → 完整分析"),
            minimum_height=145,
        )
        system_button.setObjectName("guided_system_design_button")
        system_button.setStyleSheet(
            system_button.styleSheet()
            + f"QPushButton#guided_system_design_button {{border: 3px solid {theme.active_theme().accent};}}"
        )
        system_button.clicked.connect(lambda: self._select("system_modeling"))
        choices.addWidget(system_button, 0, 0, 1, 2)

        llc_button = self._choice_button(
            t("进入 LLC 设计（Expert）"),
            t("谐振腔、磁性器件、损耗、开关波形、小信号与数字电压环"),
            minimum_height=140,
        )
        pfc_button = self._choice_button(
            t("进入 PFC 设计（Expert）"),
            t("单相 TTPL + 三相 Vienna：硬件设计、控制、采样链、Bode、AC/开关波形与 PF/THD"),
            minimum_height=140,
        )
        control_button = self._choice_button(
            t("进入 Control Tools"),
            t("S2Z、数字滤波器、Bode、Step/Impulse、P/Z、SOS 与 C99 float32_t 导出"),
            minimum_height=140,
        )
        fra_button = self._choice_button(
            t("进入 FRA Loop Designer"),
            t("Bode100 / SIMPLIS / Generic：Equivalent Plant、Auto Design、Model ID、稳定性与 C99"),
            minimum_height=140,
        )
        llc_button.clicked.connect(lambda: self._select("llc"))
        pfc_button.clicked.connect(lambda: self._select("pfc"))
        control_button.clicked.connect(lambda: self._select("control"))
        fra_button.clicked.connect(lambda: self._select("fra"))
        choices.addWidget(llc_button, 1, 0)
        choices.addWidget(pfc_button, 1, 1)
        choices.addWidget(control_button, 2, 0)
        choices.addWidget(fra_button, 2, 1)
        root.addLayout(choices, 1)

        cancel = QPushButton(t("退出"))
        cancel.clicked.connect(self.reject)
        help_button = QPushButton(t("使用说明 / 帮助 (F1)"))
        help_button.setToolTip(t("系统建模向导与四个 Expert 工作区分别做什么、如何选择"))
        help_button.clicked.connect(lambda: show_help(self, "selector"))
        footer = QHBoxLayout()
        footer.addStretch(1)
        footer.addWidget(help_button)
        footer.addWidget(cancel)
        footer.addStretch(1)
        root.addLayout(footer)
        shortcut = QShortcut(QKeySequence(QKeySequence.StandardKey.HelpContents), self)
        shortcut.activated.connect(lambda: show_help(self, "selector"))

    @staticmethod
    def _choice_button(title: str, description: str, *, minimum_height: int = 170) -> QPushButton:
        """Create a launcher card whose localized description cannot overflow."""

        palette = theme.active_theme()
        wrapped_description = "\n".join(
            textwrap.wrap(
                description,
                width=45,
                break_long_words=True,
                break_on_hyphens=False,
            )
        )
        button = QPushButton(f"{title}\n\n{wrapped_description}")
        button.setMinimumSize(500, minimum_height)
        button.setStyleSheet(
            "QPushButton {"
            f"font-size: 16px; font-weight: 600; text-align: center;"
            f"padding: 20px; border: 2px solid {palette.border_input}; border-radius: 10px;"
            f"background: {palette.surface_alt}; color: {palette.text_strong};"
            "}"
            f"QPushButton:hover {{background: {palette.hover}; border-color: {palette.accent};}}"
            f"QPushButton:pressed {{background: {palette.pressed};}}"
        )
        return button

    def _select(self, workspace: str) -> None:
        self.selected_workspace = workspace
        self.accept()


class WorkspaceApplicationController:
    """Own top-level windows and switch without destroying user state."""

    def __init__(self, initial_spec: LLCDesignSpec) -> None:
        self.llc_window = LLCMainWindow(initial_spec)
        install_device_library(self.llc_window)
        install_closed_loop_verification(self.llc_window)
        self.pfc_window = PFCMainWindow()
        self.control_window = ControlToolsMainWindow()
        self.fra_window = FRALoopDesignerWindow()
        install_advanced_fra_actions(self.fra_window)
        self.active_workspace: str | None = None
        self.llc_window.workspace_switch_requested.connect(self._handle_request)
        self.pfc_window.workspace_switch_requested.connect(self._handle_request)
        self.control_window.workspace_switch_requested.connect(self._handle_request)
        self.fra_window.workspace_switch_requested.connect(self._handle_request)
        self.control_window.digital_design_updated.connect(self.llc_window.set_external_control_design)
        self.control_window.digital_design_updated.connect(
            lambda digital, label="": self.llc_window.refresh_closed_loop_controller()
        )

    def start(self) -> bool:
        dialog = WorkspaceSelectionDialog()
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return False
        if dialog.selected_workspace is None:
            return False
        if dialog.selected_workspace == "system_modeling":
            return self._run_system_modeling(previous=None)
        self.show_workspace(dialog.selected_workspace)
        return True

    def _hide_all(self) -> None:
        self.llc_window.hide()
        self.pfc_window.hide()
        self.control_window.hide()
        self.fra_window.hide()

    def show_workspace(self, workspace: str) -> None:
        if workspace == "system_modeling":
            self._run_system_modeling(previous=self.active_workspace)
            return
        if workspace not in {"llc", "pfc", "control", "fra"}:
            raise ValueError(f"unsupported workspace: {workspace}")
        self._hide_all()
        target = {
            "llc": self.llc_window,
            "pfc": self.pfc_window,
            "control": self.control_window,
            "fra": self.fra_window,
        }[workspace]
        self.active_workspace = workspace
        target.showMaximized()
        target.raise_()
        target.activateWindow()

    def _run_system_modeling(self, previous: str | None) -> bool:
        """Run V9.3 guided definition, then hand off to maintained engines."""
        self._hide_all()
        wizard = SystemModelingDesignDialog()
        if wizard.exec() != QDialog.DialogCode.Accepted or wizard.definition is None:
            if previous is not None:
                self.show_workspace(previous)
            return previous is not None

        definition = wizard.definition
        if definition.topology == SystemTopology.LLC:
            apply_definition_to_llc_window(self.llc_window, definition)
            self.show_workspace("llc")
            # Reuse the mature system analyzer; the wizard never reimplements
            # the LLC equations.  Digital-loop analysis remains available on
            # its existing page with the guided sampling/PWM fields populated.
            self.llc_window.run_design()
            return True
        if definition.topology == SystemTopology.TTPL_PFC:
            config = apply_definition_to_ttpl_window(self.pfc_window, definition)
            self.pfc_window.subtabs.setCurrentIndex(0)
            self.show_workspace("pfc")
            # TTPL already has a single complete analysis entry that builds
            # Bode + line cycle + switching and hands exact H(z) downstream.
            self.pfc_window.run_ttpl_analysis(config)
            return True
        raise NotImplementedError(f"unsupported V9.3 guided topology: {definition.topology.value}")

    def _handle_request(self, workspace: str) -> None:
        if workspace == "home":
            self._show_selector_again()
        else:
            self.show_workspace(workspace)

    def _show_selector_again(self) -> None:
        previous = self.active_workspace
        self._hide_all()
        dialog = WorkspaceSelectionDialog()
        if dialog.exec() == QDialog.DialogCode.Accepted and dialog.selected_workspace:
            if dialog.selected_workspace == "system_modeling":
                self._run_system_modeling(previous=previous)
            else:
                self.show_workspace(dialog.selected_workspace)
        elif previous is not None:
            self.show_workspace(previous)


__all__ = ["WorkspaceApplicationController", "WorkspaceSelectionDialog"]
