"""Application launcher for the independent power-design workspaces."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QToolBar,
    QVBoxLayout,
)

from dab_design.gui.main_window import DABMainWindow
from llc_design import __version__
from llc_design.core.spec import LLCDesignSpec
from llc_design.gui import theme
from llc_design.gui.closed_loop_install import install_closed_loop_verification
from llc_design.gui.contact import add_toolbar_contacts
from llc_design.gui.device_library_install import install_device_library
from llc_design.gui.help import show_help
from llc_design.gui.main_window import LLCMainWindow
from llc_design.gui.lifetime import dispose_dialog, dispose_widgets
from llc_design.gui.solution_map_install import install_llc_solution_map
from llc_design.gui.system_modeling import (
    SystemModelingDesignDialog,
    apply_definition_to_llc_window,
    apply_definition_to_ttpl_window,
    install_guided_context_actions,
)
from llc_design.i18n import t
from pfc_design.gui.main_window import PFCMainWindow
from pfc_design.gui.solution_map_install import install_ttpl_solution_map
from power_control_tools.gui.fra_advanced import install_advanced_fra_actions
from power_control_tools.gui.fra_loop_designer import FRALoopDesignerWindow
from power_control_tools.gui.main_window import ControlToolsMainWindow
from power_control_tools.system_definition import SystemTopology
from power_stage_design.gui import PowerStageMainWindow


class _LauncherCard(QFrame):
    """CAE-style entry card used by the workspace selector."""

    def __init__(
        self,
        title: str,
        description: str,
        *,
        primary: bool = False,
        horizontal: bool = False,
        cta: str = "",
        object_name: str = "",
        parent=None,
    ) -> None:
        super().__init__(parent)
        palette = theme.active_theme()
        if object_name:
            self.setObjectName(object_name)
        self.setFrameShape(QFrame.Shape.StyledPanel)
        border = palette.accent if primary else palette.border_card
        border_w = 3 if primary else 1
        self.setStyleSheet(
            f"QFrame#{object_name or 'launcher_card'} {{"
            f"background:{palette.surface}; border:{border_w}px solid {border};"
            f"border-radius:14px;}}"
            f"QFrame#{object_name or 'launcher_card'}:hover {{"
            f"border-color:{palette.accent}; background:{palette.hover};}}"
        )
        root = QHBoxLayout(self) if horizontal else QVBoxLayout(self)
        root.setContentsMargins(16, 12, 16, 12)
        root.setSpacing(6)
        text_layout = QVBoxLayout() if horizontal else root
        if horizontal:
            text_layout.setSpacing(6)
            root.addLayout(text_layout, 1)
        title_label = QLabel(title)
        title_label.setWordWrap(True)
        title_label.setStyleSheet(
            f"font-size:{22 if primary else 16}px; font-weight:700;"
            f"color:{palette.text_strong}; border:none; background:transparent;"
        )
        text_layout.addWidget(title_label)
        desc = QLabel(description)
        desc.setWordWrap(True)
        desc.setStyleSheet(
            f"font-size:13px; color:{palette.text_muted}; border:none; background:transparent;"
        )
        text_layout.addWidget(desc)
        if not horizontal:
            root.addStretch(1)
        self.button = QPushButton(cta or ("Start" if primary else t("进入")))
        self.button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.button.setMinimumHeight(36 if primary else 32)
        self.button.setStyleSheet(
            "QPushButton {"
            f"font-size:14px; font-weight:650; border-radius:8px; padding:6px 14px;"
            f"background:{palette.accent if primary else palette.surface_alt};"
            f"color:{'#ffffff' if primary else palette.text_strong};"
            f"border:1px solid {palette.accent if primary else palette.border_input};"
            "}"
            f"QPushButton:hover {{background:{palette.pressed if primary else palette.hover};}}"
        )
        if horizontal:
            root.addWidget(self.button)
            self.setMinimumHeight(116)
        else:
            row = QHBoxLayout()
            row.addStretch(1)
            row.addWidget(self.button)
            root.addLayout(row)
            self.setMinimumHeight(148)


class WorkspaceSelectionDialog(QDialog):
    """Initial function selector shown before an engineering workspace."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.selected_workspace: str | None = None
        self.setWindowTitle(t("电源设计工具箱 — 选择设计功能"))
        self.setMinimumSize(1024, 620)
        self.setWindowState(self.windowState() | Qt.WindowState.WindowMaximized)
        self.setWindowFlag(Qt.WindowType.WindowContextHelpButtonHint, False)
        self.setStyleSheet(theme.launcher_stylesheet(theme.active_theme()))

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 16, 20, 16)
        root.setSpacing(10)

        title = QLabel(t("电源设计工具箱") + f" · V{__version__}")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setStyleSheet("font-size: 25px; font-weight: 650; padding: 4px;")
        root.addWidget(title)

        subtitle = QLabel(
            t("选择功率级、专用拓扑或控制分析工作区；切换工作区时保留当前设计。")
        )
        subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        subtitle.setWordWrap(True)
        subtitle.setStyleSheet("font-size: 13px;")
        root.addWidget(subtitle)

        psd = _LauncherCard(
            t("功率级设计 / Power Stage Designer"),
            t("Buck 降压 · Boost 升压 · Buck-Boost 升降压 · 全桥 / 移相全桥 · 更多拓扑\n"
              "功率级参数、原理图与电压电流波形、器件应力、模拟环路及工程工具箱"),
            primary=True,
            horizontal=True,
            cta=t("进入功率级设计"),
            object_name="power_stage_designer_card",
        )
        psd.button.setObjectName("power_stage_designer_button")
        psd.button.setDefault(True)
        psd.button.clicked.connect(lambda: self._select("power_stage"))
        root.addWidget(psd)

        guided = _LauncherCard(
            t("系统建模与设计 / Guided System Design"),
            t("从功率级、采样、ADC、PWM 到完整闭环的逐步建模"),
            cta=t("开始系统建模"),
            object_name="guided_system_design_card",
        )
        # Keep findChild(QPushButton, ...) working for existing smoke tests.
        guided.button.setObjectName("guided_system_design_button")
        guided.button.clicked.connect(lambda: self._select("system_modeling"))
        grid = QGridLayout()
        grid.setHorizontalSpacing(16)
        grid.setVerticalSpacing(10)

        llc = _LauncherCard(
            t("进入 LLC 设计（Expert）"),
            t("谐振腔、磁性器件、损耗、开关波形、小信号与数字电压环"),
            cta=t("进入 LLC"),
            object_name="llc_expert_card",
        )
        pfc = _LauncherCard(
            t("进入 PFC 设计（Expert）"),
            t("单相 TTPL + 三相 Vienna：硬件设计、控制、采样链、Bode、AC/开关波形与 PF/THD"),
            cta=t("进入 PFC"),
            object_name="pfc_expert_card",
        )
        control = _LauncherCard(
            t("进入 Control Tools"),
            t("S2Z、数字滤波器、Bode、Step/Impulse、P/Z、SOS 与 C99 float32_t 导出"),
            cta=t("进入 Control Tools"),
            object_name="control_tools_card",
        )
        fra = _LauncherCard(
            t("进入 FRA Loop Designer"),
            t("Bode100 / SIMPLIS / Generic：Equivalent Plant、Auto Design、Model ID、稳定性与 C99"),
            cta=t("进入 FRA Loop Designer"),
            object_name="fra_designer_card",
        )
        dab = _LauncherCard(
            "DAB TPS 设计",
            "三移相控制、负载扫描、L/Cp 波形、电流与电容应力、换流电流及 PWM 计数",
            cta="进入 DAB TPS", object_name="dab_expert_card",
        )
        dab.button.clicked.connect(lambda: self._select("dab"))
        llc.button.clicked.connect(lambda: self._select("llc"))
        pfc.button.clicked.connect(lambda: self._select("pfc"))
        control.button.clicked.connect(lambda: self._select("control"))
        fra.button.clicked.connect(lambda: self._select("fra"))
        grid.addWidget(guided, 0, 0)
        grid.addWidget(llc, 0, 1)
        grid.addWidget(pfc, 0, 2)
        grid.addWidget(control, 1, 0)
        grid.addWidget(fra, 1, 1)
        grid.addWidget(dab, 1, 2)
        for column in range(3):
            grid.setColumnStretch(column, 1)
        root.addLayout(grid, 1)

        cancel = QPushButton(t("退出"))
        cancel.setObjectName("launcher_exit_button")
        cancel.clicked.connect(self.reject)
        help_button = QPushButton(t("使用说明 / 帮助 (F1)"))
        help_button.setToolTip(t("工作区选择与使用说明"))
        help_button.clicked.connect(lambda: show_help(self, "selector"))
        footer = QHBoxLayout()
        contacts = QToolBar(self)
        contacts.setObjectName("launcher_contact_toolbar")
        contacts.setStyleSheet("QToolBar { border: 0; background: transparent; }")
        contacts.setMovable(False)
        add_toolbar_contacts(contacts)
        footer.addWidget(contacts, 1)
        for button in (help_button, cancel):
            button.setStyleSheet("font-size:13px; padding:6px 14px; border-radius:6px;")
        footer.addWidget(help_button)
        footer.addWidget(cancel)
        root.addLayout(footer)
        shortcut = QShortcut(QKeySequence(QKeySequence.StandardKey.HelpContents), self)
        shortcut.activated.connect(lambda: show_help(self, "selector"))

    def _select(self, workspace: str) -> None:
        self.selected_workspace = workspace
        self.accept()


class WorkspaceApplicationController:
    """Own top-level windows and switch without destroying user state."""

    def __init__(self, initial_spec: LLCDesignSpec, *, power_stage_window=None) -> None:
        self._disposed = False
        try:
            self.llc_window = LLCMainWindow(initial_spec)
            install_device_library(self.llc_window)
            install_closed_loop_verification(self.llc_window)
            install_llc_solution_map(self.llc_window)
            self.pfc_window = PFCMainWindow()
            self.dab_window = DABMainWindow()
            install_ttpl_solution_map(self.pfc_window)
            self.control_window = ControlToolsMainWindow()
            self.fra_window = FRALoopDesignerWindow()
            install_advanced_fra_actions(self.fra_window)
            self.active_workspace: str | None = None
            self.llc_window.workspace_switch_requested.connect(self._handle_request)
            self.pfc_window.workspace_switch_requested.connect(self._handle_request)
            self.control_window.workspace_switch_requested.connect(self._handle_request)
            self.fra_window.workspace_switch_requested.connect(self._handle_request)
            self.dab_window.workspace_switch_requested.connect(self._handle_request)
            self.control_window.digital_design_invalidated.connect(self.llc_window.invalidate_external_control_design)
            self.control_window.digital_design_updated.connect(self.llc_window.set_external_control_design)
            self.control_window.digital_design_updated.connect(
                lambda digital, label="": self.llc_window.refresh_closed_loop_controller()
            )
            install_guided_context_actions(
                self.llc_window,
                lambda: self._edit_guided_definition("llc"),
            )
            install_guided_context_actions(
                self.pfc_window,
                lambda: self._edit_guided_definition("pfc"),
            )
            # Do not change an injected owner's callbacks until every new
            # workspace and installer has successfully been constructed.
            self.power_stage_window = PowerStageMainWindow() if power_stage_window is None else power_stage_window
            self.power_stage_window.workspace_switch_requested.connect(self._handle_request)
            self.power_stage_window.open_existing_llc = self._show_psd_llc
        except BaseException:
            # Construction can fail after some native roots exist. An injected
            # PSD owner predates this controller and must survive that failure.
            dispose_widgets(window for window in self._windows()
                            if window is not power_stage_window)
            raise
        app = QApplication.instance()
        if app is not None:
            app.aboutToQuit.connect(self.dispose)

    def _windows(self):
        return tuple(getattr(self, name) for name in (
            "llc_window", "pfc_window", "dab_window", "control_window",
            "fra_window", "power_stage_window",
        ) if hasattr(self, name))

    def dispose(self) -> None:
        """Destroy cached owners only when the application is finished."""
        if self._disposed:
            return
        dispose_widgets(self._windows())
        self._disposed = True
        app = QApplication.instance()
        if app is not None:
            app.aboutToQuit.disconnect(self.dispose)

    def start(self) -> bool:
        dialog = WorkspaceSelectionDialog()
        try:
            accepted = dialog.exec() == QDialog.DialogCode.Accepted
            workspace = dialog.selected_workspace
        finally:
            dispose_dialog(dialog)
        if not accepted or workspace is None:
            return False
        if workspace == "system_modeling":
            return self._run_system_modeling(previous=None)
        self.show_workspace(workspace)
        return True

    def _hide_all(self) -> None:
        self.llc_window.hide()
        self.pfc_window.hide()
        self.control_window.hide()
        self.fra_window.hide()
        self.dab_window.hide()
        self.power_stage_window.hide()

    def show_workspace(self, workspace: str) -> None:
        if self._disposed:
            raise RuntimeError("Workspace controller is disposed")
        if workspace == "system_modeling":
            self._run_system_modeling(previous=self.active_workspace)
            return
        if workspace not in {"llc", "pfc", "control", "fra", "dab", "power_stage"}:
            raise ValueError(f"unsupported workspace: {workspace}")
        self._hide_all()
        target = {
            "llc": self.llc_window,
            "pfc": self.pfc_window,
            "control": self.control_window,
            "fra": self.fra_window,
            "dab": self.dab_window,
            "power_stage": self.power_stage_window,
        }[workspace]
        self.active_workspace = workspace
        target.showMaximized()
        target.raise_()
        target.activateWindow()

    def _run_system_modeling(
        self,
        previous: str | None,
        *,
        seed_definition=None,
    ) -> bool:
        """Run V9.3 guided definition, then hand off to maintained engines."""
        self._hide_all()
        wizard = SystemModelingDesignDialog(initial_definition=seed_definition)
        try:
            accepted = wizard.exec() == QDialog.DialogCode.Accepted
            definition = wizard.definition
        finally:
            dispose_dialog(wizard)
        if not accepted or definition is None:
            if previous is not None:
                self.show_workspace(previous)
            return previous is not None

        if definition.topology == SystemTopology.LLC:
            apply_definition_to_llc_window(self.llc_window, definition)
            self.show_workspace("llc")
            self.llc_window.run_design()
            return True
        if definition.topology == SystemTopology.TTPL_PFC:
            config = apply_definition_to_ttpl_window(self.pfc_window, definition)
            self.pfc_window.subtabs.setCurrentIndex(0)
            self.show_workspace("pfc")
            self.pfc_window.run_ttpl_analysis(config)
            return True
        raise NotImplementedError(f"unsupported V9.3 guided topology: {definition.topology.value}")

    def _edit_guided_definition(self, workspace: str) -> None:
        window = self.llc_window if workspace == "llc" else self.pfc_window
        seed = getattr(window, "guided_system_definition", None)
        self._run_system_modeling(previous=workspace, seed_definition=seed)

    def _show_psd_llc(self, topology: str) -> None:
        if topology not in {"T20","T21"}:
            raise ValueError("Expected LLC half/full bridge selection")
        # Use the original LLC fields/invalidations and solver, not another tank model.
        self.llc_window.topology.setCurrentIndex(1 if topology=="T20" else 0)
        self.show_workspace("llc")

    def _handle_request(self, workspace: str) -> None:
        if workspace == "home":
            self._show_selector_again()
        else:
            self.show_workspace(workspace)

    def _show_selector_again(self) -> None:
        previous = self.active_workspace
        self._hide_all()
        dialog = WorkspaceSelectionDialog()
        try:
            accepted = dialog.exec() == QDialog.DialogCode.Accepted
            workspace = dialog.selected_workspace
        finally:
            dispose_dialog(dialog)
        if accepted and workspace:
            if workspace == "system_modeling":
                self._run_system_modeling(previous=previous)
            else:
                self.show_workspace(workspace)
        elif previous is not None:
            self.show_workspace(previous)


__all__ = ["WorkspaceApplicationController", "WorkspaceSelectionDialog"]
