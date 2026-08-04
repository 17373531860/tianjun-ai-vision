"""USB 4G 短信独立测试工具的 PyQt5 界面。"""

from __future__ import annotations

import sys
from collections.abc import Callable
from datetime import datetime

from PyQt5 import QtCore, QtGui, QtWidgets
from serial.tools import list_ports

from .modem import DiagnosticReport
from .sms_service import SmsBatchResult, SmsService, SmsServiceConfig
from .utils import SmsValidationError, choose_encoding, mask_phone, parse_recipients


DEFAULT_TEST_MESSAGE = "【天军AI视觉】USB 4G 短信告警通道测试，请确认收到。"


class OperationThread(QtCore.QThread):
    """在后台线程运行一次串口任务，避免 AT 超时冻结 GUI。

    Context: 由 Qt 主线程创建；``operation`` 在 QThread 中执行且可阻塞串口，
             只能通过 signals 更新 UI，不能直接访问 QWidget。
    """

    completed = QtCore.pyqtSignal(object)
    failed = QtCore.pyqtSignal(str)
    log = QtCore.pyqtSignal(str)

    def __init__(self, operation: Callable[[Callable[[str], None]], object]) -> None:
        super().__init__()
        self._operation = operation

    def run(self) -> None:
        try:
            result = self._operation(self.log.emit)
        except Exception as exc:
            self.failed.emit(str(exc))
        else:
            self.completed.emit(result)


class SmsTestWindow(QtWidgets.QMainWindow):
    """提供 COM 诊断和真实测试短信发送，不依赖主程序。"""

    def __init__(self) -> None:
        super().__init__()
        self._task: OperationThread | None = None
        self._task_kind = ""
        self.setWindowTitle("天军 AI 视觉 · USB 4G 短信测试工具（第一阶段）")
        self.setMinimumSize(920, 720)
        self.resize(1020, 820)
        self._build_ui()
        self._apply_style()
        self.refresh_ports()

    def _build_ui(self) -> None:
        root = QtWidgets.QWidget()
        self.setCentralWidget(root)
        layout = QtWidgets.QVBoxLayout(root)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(12)

        title = QtWidgets.QLabel("USB 4G 模块短信告警 · 独立硬件验证")
        title.setObjectName("title")
        subtitle = QtWidgets.QLabel(
            "通用可选通知能力（默认关闭）。本工具只验证短信专用 COM，不连接主程序、灯塔或 MES。"
        )
        subtitle.setWordWrap(True)
        subtitle.setObjectName("subtitle")
        layout.addWidget(title)
        layout.addWidget(subtitle)

        device_group = QtWidgets.QGroupBox("1. 短信模块")
        device_grid = QtWidgets.QGridLayout(device_group)
        self.port_combo = QtWidgets.QComboBox()
        self.port_combo.setEditable(True)
        self.port_combo.setInsertPolicy(QtWidgets.QComboBox.NoInsert)
        self.port_combo.setMinimumWidth(360)
        self.refresh_button = QtWidgets.QPushButton("刷新串口")
        self.refresh_button.clicked.connect(self.refresh_ports)
        self.baud_combo = QtWidgets.QComboBox()
        self.baud_combo.setEditable(True)
        self.baud_combo.addItems(["115200", "9600", "57600", "38400", "19200"])
        self.baud_combo.setCurrentText("115200")
        self.encoding_combo = QtWidgets.QComboBox()
        self.encoding_combo.addItem("自动（中文用 UCS2，推荐）", "auto")
        self.encoding_combo.addItem("固定 UCS2", "ucs2")
        self.encoding_combo.addItem("固定 GSM / ASCII", "gsm")
        self.encoding_combo.currentIndexChanged.connect(self._update_message_counter)
        self.retry_spin = QtWidgets.QSpinBox()
        self.retry_spin.setRange(0, 3)
        self.retry_spin.setValue(1)
        self.retry_spin.setSuffix(" 次")
        device_grid.addWidget(QtWidgets.QLabel("短信 COM 口"), 0, 0)
        device_grid.addWidget(self.port_combo, 0, 1)
        device_grid.addWidget(self.refresh_button, 0, 2)
        device_grid.addWidget(QtWidgets.QLabel("波特率"), 1, 0)
        device_grid.addWidget(self.baud_combo, 1, 1)
        device_grid.addWidget(QtWidgets.QLabel("失败重试"), 1, 2)
        device_grid.addWidget(self.retry_spin, 1, 3)
        device_grid.addWidget(QtWidgets.QLabel("短信编码"), 2, 0)
        device_grid.addWidget(self.encoding_combo, 2, 1, 1, 3)
        device_grid.setColumnStretch(1, 1)
        layout.addWidget(device_group)

        sms_group = QtWidgets.QGroupBox("2. 接收人与测试内容")
        sms_grid = QtWidgets.QGridLayout(sms_group)
        self.phone_edit = QtWidgets.QPlainTextEdit()
        self.phone_edit.setPlaceholderText(
            "例如：13800138000；多个号码用逗号、分号或换行分隔"
        )
        self.phone_edit.setMaximumHeight(64)
        self.message_edit = QtWidgets.QPlainTextEdit(DEFAULT_TEST_MESSAGE)
        self.message_edit.setMinimumHeight(105)
        self.message_edit.textChanged.connect(self._update_message_counter)
        self.counter_label = QtWidgets.QLabel()
        self.counter_label.setAlignment(QtCore.Qt.AlignRight)
        sms_grid.addWidget(QtWidgets.QLabel("接收手机号"), 0, 0)
        sms_grid.addWidget(self.phone_edit, 0, 1)
        sms_grid.addWidget(QtWidgets.QLabel("短信内容"), 1, 0, QtCore.Qt.AlignTop)
        sms_grid.addWidget(self.message_edit, 1, 1)
        sms_grid.addWidget(self.counter_label, 2, 1)
        layout.addWidget(sms_group)

        action_layout = QtWidgets.QHBoxLayout()
        self.diagnose_button = QtWidgets.QPushButton("检测模块与网络")
        self.diagnose_button.setObjectName("secondaryAction")
        self.diagnose_button.clicked.connect(self.start_diagnosis)
        self.cost_checkbox = QtWidgets.QCheckBox(
            "我已确认 SIM 开通短信；测试发送可能产生运营商资费"
        )
        self.send_button = QtWidgets.QPushButton("发送测试短信")
        self.send_button.setObjectName("primaryAction")
        self.send_button.clicked.connect(self.start_send)
        action_layout.addWidget(self.diagnose_button)
        action_layout.addSpacing(12)
        action_layout.addWidget(self.cost_checkbox, 1)
        action_layout.addWidget(self.send_button)
        layout.addLayout(action_layout)

        self.status_label = QtWidgets.QLabel(
            "就绪：请先选择短信模块 COM，建议先执行“检测模块与网络”"
        )
        self.status_label.setObjectName("statusInfo")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        log_group = QtWidgets.QGroupBox("3. AT 日志与结果（手机号、短信正文已隐藏）")
        log_layout = QtWidgets.QVBoxLayout(log_group)
        self.log_edit = QtWidgets.QPlainTextEdit()
        self.log_edit.setReadOnly(True)
        self.log_edit.setMinimumHeight(220)
        self.log_edit.setFont(QtGui.QFont("Consolas", 9))
        clear_button = QtWidgets.QPushButton("清空日志")
        clear_button.clicked.connect(self.log_edit.clear)
        log_layout.addWidget(self.log_edit)
        log_layout.addWidget(clear_button, alignment=QtCore.Qt.AlignRight)
        layout.addWidget(log_group, 1)

        footnote = QtWidgets.QLabel(
            "提示：软件只能确认模块已把短信提交给运营商；纯流量卡、未开短信的物联网卡、欠费或运营商拦截仍会导致无法送达。"
        )
        footnote.setObjectName("footnote")
        footnote.setWordWrap(True)
        layout.addWidget(footnote)
        self._update_message_counter()

    def _apply_style(self) -> None:
        self.setStyleSheet(
            """
            QWidget { font-family: "Microsoft YaHei UI"; font-size: 13px; color: #1f2937; }
            QMainWindow { background: #f4f7fb; }
            QLabel#title { font-size: 22px; font-weight: 700; color: #0f3d66; }
            QLabel#subtitle { color: #52657a; padding-bottom: 4px; }
            QGroupBox { background: white; border: 1px solid #d8e1ea; border-radius: 8px;
                        margin-top: 10px; padding: 14px 10px 10px 10px; font-weight: 600; }
            QGroupBox::title { subcontrol-origin: margin; left: 12px; padding: 0 5px; }
            QLineEdit, QComboBox, QPlainTextEdit, QSpinBox {
                background: white; border: 1px solid #bdc9d6; border-radius: 5px; padding: 6px;
            }
            QLineEdit:focus, QComboBox:focus, QPlainTextEdit:focus { border-color: #1677c8; }
            QPushButton { min-height: 30px; padding: 4px 14px; border-radius: 5px;
                          border: 1px solid #b7c5d3; background: #f8fafc; }
            QPushButton:hover { background: #eef5fb; }
            QPushButton:disabled { color: #9aa5b1; background: #edf0f3; }
            QPushButton#primaryAction { color: white; background: #1477c9; border-color: #1477c9; font-weight: 600; }
            QPushButton#primaryAction:hover { background: #0f65ad; }
            QPushButton#secondaryAction { color: #0f5d9c; border-color: #2c82c9; font-weight: 600; }
            QLabel#statusInfo { background: #eaf4ff; border: 1px solid #b9daf5; border-radius: 6px;
                                color: #145b8f; padding: 9px; }
            QLabel#footnote { color: #8a5a00; background: #fff8e7; border-radius: 5px; padding: 7px; }
            """
        )

    @QtCore.pyqtSlot()
    def refresh_ports(self) -> None:
        """刷新 Windows COM 列表并保留当前手工输入。"""

        current = self._selected_port()
        ports = sorted(list_ports.comports(), key=lambda item: item.device)
        self.port_combo.blockSignals(True)
        self.port_combo.clear()
        for item in ports:
            description = item.description or "串口设备"
            self.port_combo.addItem(f"{item.device} — {description}", item.device)
        if current:
            index = self.port_combo.findData(current)
            if index >= 0:
                self.port_combo.setCurrentIndex(index)
            else:
                self.port_combo.setEditText(current)
        elif not ports:
            self.port_combo.setEditText("")
        self.port_combo.blockSignals(False)
        self._append_log(
            f"发现 {len(ports)} 个串口"
            if ports
            else "未发现串口；请插入 USB 4G 模块后刷新"
        )

    @QtCore.pyqtSlot()
    def start_diagnosis(self) -> None:
        """后台运行模块诊断。"""

        try:
            config = self._build_config(enabled=False)
        except ValueError as exc:
            self._show_input_error(str(exc))
            return

        def operation(logger: Callable[[str], None]) -> DiagnosticReport:
            return SmsService(config, logger=logger).diagnose_modem()

        self._start_task("diagnose", operation, "正在检测模块、SIM、网络和短信能力……")

    @QtCore.pyqtSlot()
    def start_send(self) -> None:
        """校验资费确认后，在后台发送一条真实测试短信。"""

        if not self.cost_checkbox.isChecked():
            self._show_input_error("请先勾选已确认 SIM 开通短信及可能产生资费")
            return
        try:
            recipients = parse_recipients(self.phone_edit.toPlainText())
            message = self.message_edit.toPlainText().strip()
            config = self._build_config(enabled=True, recipients=recipients)
        except (ValueError, SmsValidationError) as exc:
            self._show_input_error(str(exc))
            return

        masked = "、".join(mask_phone(phone) for phone in recipients)
        self._append_log(f"准备发送到：{masked}")

        def operation(logger: Callable[[str], None]) -> SmsBatchResult:
            service = SmsService(config, logger=logger)
            return service.send_test_sms(recipients, message)

        self._start_task("send", operation, "正在提交短信；每个号码最多等待约 60 秒……")

    def _start_task(
        self,
        kind: str,
        operation: Callable[[Callable[[str], None]], object],
        status: str,
    ) -> None:
        if self._task is not None and self._task.isRunning():
            return
        self._task_kind = kind
        self._set_busy(True)
        self._set_status(status, "info")
        self._append_log(status)
        task = OperationThread(operation)
        task.log.connect(self._append_log)
        task.completed.connect(self._on_task_completed)
        task.failed.connect(self._on_task_failed)
        task.finished.connect(self._on_task_finished)
        self._task = task
        task.start()

    @QtCore.pyqtSlot(object)
    def _on_task_completed(self, result: object) -> None:
        if isinstance(result, DiagnosticReport):
            icons = {"ok": "[通过]", "warning": "[提示]", "error": "[失败]"}
            for item in result.items:
                self._append_log(
                    f"{icons.get(item.level, '[信息]')} {item.name}：{item.detail}"
                )
            if result.success:
                self._set_status("模块诊断通过：可以继续发送真实测试短信", "success")
            else:
                self._set_status("模块诊断未通过：请按日志处理后再发送", "error")
            return

        if isinstance(result, SmsBatchResult):
            for item in result.results:
                state = "成功" if item.success else "失败"
                reference = (
                    f"，消息引用 {item.message_reference}"
                    if item.message_reference
                    else ""
                )
                self._append_log(
                    f"[{state}] {mask_phone(item.phone)}，尝试 {item.attempts} 次{reference}：{item.detail}"
                )
            if result.success:
                self._set_status(
                    f"短信已全部提交运营商：{result.succeeded}/{len(result.results)}。请到接收手机确认送达",
                    "success",
                )
            else:
                self._set_status(
                    f"短信发送未全部成功：{result.succeeded}/{len(result.results)}，请查看 AT 日志",
                    "error",
                )

    @QtCore.pyqtSlot(str)
    def _on_task_failed(self, message: str) -> None:
        self._append_log(f"[异常] {message}")
        self._set_status(f"操作失败：{message}", "error")
        QtWidgets.QMessageBox.critical(self, "操作失败", message)

    @QtCore.pyqtSlot()
    def _on_task_finished(self) -> None:
        self._set_busy(False)
        self._task = None
        self._task_kind = ""

    def _build_config(
        self,
        *,
        enabled: bool,
        recipients: tuple[str, ...] = (),
    ) -> SmsServiceConfig:
        port = self._selected_port()
        if not port:
            raise ValueError("请选择或输入短信模块 COM 口")
        try:
            baudrate = int(self.baud_combo.currentText().strip())
        except ValueError as exc:
            raise ValueError("波特率必须是整数") from exc
        if not 300 <= baudrate <= 4_000_000:
            raise ValueError("波特率超出支持范围 300~4000000")
        return SmsServiceConfig(
            enabled=enabled,
            port=port,
            baudrate=baudrate,
            recipients=recipients,
            encoding=str(self.encoding_combo.currentData()),
            retries=self.retry_spin.value(),
        )

    def _selected_port(self) -> str:
        data = self.port_combo.currentData()
        if data and self.port_combo.currentText().startswith(str(data)):
            return str(data).strip()
        text = self.port_combo.currentText().strip()
        return text.split(" — ", 1)[0].strip()

    @QtCore.pyqtSlot()
    def _update_message_counter(self) -> None:
        message = self.message_edit.toPlainText()
        requested = (
            str(self.encoding_combo.currentData())
            if hasattr(self, "encoding_combo")
            else "auto"
        )
        try:
            selected = choose_encoding(message, requested)
        except SmsValidationError:
            selected = "auto"
        length = (
            len(message) if selected == "gsm" else len(message.encode("utf-16-be")) // 2
        )
        maximum = 160 if selected == "gsm" else 70
        self.counter_label.setText(
            f"当前 {length}/{maximum}（{selected.upper()} 单条短信）"
        )
        self.counter_label.setStyleSheet(
            "color: #b42318;" if length > maximum else "color: #66788a;"
        )

    @QtCore.pyqtSlot(str)
    def _append_log(self, message: str) -> None:
        stamp = datetime.now().strftime("%H:%M:%S")
        self.log_edit.appendPlainText(f"[{stamp}] {message}")
        cursor = self.log_edit.textCursor()
        cursor.movePosition(QtGui.QTextCursor.End)
        self.log_edit.setTextCursor(cursor)

    def _set_busy(self, busy: bool) -> None:
        self.refresh_button.setDisabled(busy)
        self.diagnose_button.setDisabled(busy)
        self.send_button.setDisabled(busy)
        self.port_combo.setDisabled(busy)
        self.baud_combo.setDisabled(busy)
        self.encoding_combo.setDisabled(busy)
        self.retry_spin.setDisabled(busy)
        self.phone_edit.setDisabled(busy)
        self.message_edit.setDisabled(busy)
        self.cost_checkbox.setDisabled(busy)

    def _set_status(self, text: str, level: str) -> None:
        colors = {
            "info": ("#eaf4ff", "#b9daf5", "#145b8f"),
            "success": ("#eaf8ee", "#abdcb9", "#176b36"),
            "error": ("#fff0f0", "#f0b8b8", "#a11f1f"),
        }
        background, border, color = colors[level]
        self.status_label.setText(text)
        self.status_label.setStyleSheet(
            f"background:{background}; border:1px solid {border}; border-radius:6px; color:{color}; padding:9px;"
        )

    def _show_input_error(self, message: str) -> None:
        self._set_status(message, "error")
        QtWidgets.QMessageBox.warning(self, "输入检查", message)

    def closeEvent(self, event: QtGui.QCloseEvent) -> None:  # noqa: N802 - Qt API 命名
        if self._task is not None and self._task.isRunning():
            QtWidgets.QMessageBox.information(
                self, "任务执行中", "请等待当前串口任务结束后再关闭"
            )
            event.ignore()
            return
        event.accept()


def run_gui(*, smoke_quit_ms: int | None = None) -> int:
    """创建应用并运行主窗口；``smoke_quit_ms`` 仅供离线 GUI 冒烟。"""

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    app.setApplicationName("天军 AI 视觉 USB 4G 短信测试工具")
    app.setStyle("Fusion")
    window = SmsTestWindow()
    window.show()
    if smoke_quit_ms is not None:
        QtCore.QTimer.singleShot(max(50, smoke_quit_ms), app.quit)
    return app.exec_()
