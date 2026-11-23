from __future__ import annotations

import time

from pathlib import Path
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QTabWidget,
    QTextEdit,
    QWidget,
)
import pyqtgraph as pg

from oh_behave_pypi.config import ExperimentConfig
from oh_behave_pypi.task_runner import TaskRunner
from oh_behave_pypi.teensy_io import TeensyController


class OhBehaveWindow(QMainWindow):
    def __init__(self, cfg: ExperimentConfig, teensy: TeensyController) -> None:
        super().__init__()
        self.cfg = cfg
        self.teensy = teensy
        self.runner = TaskRunner(cfg, teensy)
        self.selected_run_type = 1

        self.setWindowTitle("oh_behave — Teensy 4.1 Behavioral Controller")
        self.resize(1380, 780)

        self._apply_dark_theme()
        self._build_ui()
        self._populate_ports()

        self.ui_timer = QTimer(self)
        self.ui_timer.setInterval(33)
        self.ui_timer.timeout.connect(self._refresh_ui)
        self.ui_timer.start()

    def _apply_dark_theme(self) -> None:
        self.setStyleSheet(
            """
            QMainWindow, QWidget {
                background-color: #000000;
                color: #FFFFFF;
                font-family: 'DejaVu Sans', 'Segoe UI', sans-serif;
                font-size: 12px;
            }
            QPushButton {
                background-color: #111111;
                color: #FFFFFF;
                border: 1px solid #444444;
                padding: 5px 10px;
                border-radius: 3px;
            }
            QPushButton:hover {
                background-color: #222222;
                border-color: #75f5f3;
            }
            QPushButton:disabled {
                color: #666666;
                border-color: #222222;
            }
            QLineEdit, QComboBox {
                background-color: #0d0d0d;
                color: #FFFFFF;
                border: 1px solid #444444;
                padding: 4px 6px;
            }
            QTextEdit {
                background-color: #050505;
                color: #00FF00;
                border: 1px solid #333333;
                font-family: monospace;
            }
            QTabWidget::pane {
                border: 1px solid #333333;
            }
            QTabBar::tab {
                background: #111111;
                color: #CCCCCC;
                padding: 6px 14px;
            }
            QTabBar::tab:selected {
                background: #222222;
                color: #FFFFFF;
            }
            """
        )

    def _build_ui(self) -> None:
        tabs = QTabWidget()
        self.setCentralWidget(tabs)

        data_tab = QWidget()
        tabs.addTab(data_tab, "Data")

        grid = QGridLayout(data_tab)
        grid.setSpacing(6)

        conn_box = QHBoxLayout()
        conn_box.addWidget(QLabel("Port:"))
        self.port_combo = QComboBox()
        self.port_combo.setMinimumWidth(120)
        conn_box.addWidget(self.port_combo)

        refresh_btn = QPushButton("↻")
        refresh_btn.setMaximumWidth(30)
        refresh_btn.clicked.connect(self._populate_ports)
        conn_box.addWidget(refresh_btn)

        self.connect_btn = QPushButton("Connect")
        self.connect_btn.clicked.connect(self.toggle_serial)
        conn_box.addWidget(self.connect_btn)
        grid.addLayout(conn_box, 0, 0, 1, 4)

        id_box = QHBoxLayout()
        id_box.addWidget(QLabel("Run ID:"))
        self.id_edit = QLineEdit("NA")
        id_box.addWidget(self.id_edit)
        grid.addLayout(id_box, 1, 0, 1, 4)

        path_btn = QPushButton("Set Path")
        path_btn.clicked.connect(self._choose_save_path)
        grid.addWidget(path_btn, 0, 4, 1, 1)

        self.path_label = QLabel(str(Path.home() / "Desktop"))
        self.path_label.setStyleSheet("font-size: 14px; color: #FFFFFF;")
        grid.addWidget(self.path_label, 0, 5, 1, 15)

        outcome_box = QHBoxLayout()
        self.outcome_labels: dict[int, QLabel] = {}
        outcome_specs = [(1, "Hit"), (2, "Miss"), (3, "CW"), (4, "FA"), (5, "EL")]
        for idx, (code, name) in enumerate(outcome_specs):
            lbl = QLabel(name)
            lbl.setAlignment(Qt.AlignCenter)
            lbl.setStyleSheet("font-size: 26px; font-weight: bold; color: #808080;")
            self.outcome_labels[code] = lbl
            outcome_box.addWidget(lbl)
            if idx < len(outcome_specs) - 1:
                sep = QLabel("|")
                sep.setAlignment(Qt.AlignCenter)
                sep.setStyleSheet("font-size: 26px; color: #FFFFFF;")
                outcome_box.addWidget(sep)
        grid.addLayout(outcome_box, 1, 5, 1, 10)

        run_type_box = QHBoxLayout()
        run_type_box.addWidget(QLabel("Run Type:"))
        self.detect_btn = QPushButton("Detect")
        self.pair_btn = QPushButton("Pair")
        self.live_btn = QPushButton("Live")
        self.detect_btn.clicked.connect(lambda: self._select_run_type(1))
        self.pair_btn.clicked.connect(lambda: self._select_run_type(2))
        self.live_btn.clicked.connect(lambda: self._select_run_type(4))
        run_type_box.addWidget(self.detect_btn)
        run_type_box.addWidget(self.pair_btn)
        run_type_box.addWidget(self.live_btn)
        grid.addLayout(run_type_box, 2, 0, 1, 4)
        self._select_run_type(1)

        flow_box = QHBoxLayout()
        self.begin_btn = QPushButton("Begin")
        self.end_btn = QPushButton("End")
        self.end_btn.setEnabled(False)
        self.begin_btn.clicked.connect(self.start_run)
        self.end_btn.clicked.connect(self.stop_run)
        flow_box.addWidget(self.begin_btn)
        flow_box.addWidget(self.end_btn)
        grid.addLayout(flow_box, 3, 0, 1, 4)

        self.notes_edit = QTextEdit()
        self.notes_edit.setPlainText("Notes Here ...")
        grid.addWidget(self.notes_edit, 4, 0, 4, 4)

        grid.addWidget(QLabel("Reward Port:"), 8, 0, 1, 2)
        rew_btn = QPushButton("Give Reward")
        rew_btn.clicked.connect(lambda: self.teensy.send("<S,8>"))
        grid.addWidget(rew_btn, 8, 2, 1, 2)

        grid.addWidget(QLabel("Valve 1:"), 9, 0, 1, 2)
        v1_open = QPushButton("Open")
        v1_close = QPushButton("Close")
        v1_open.clicked.connect(lambda: self.teensy.send("<S,5>"))
        v1_close.clicked.connect(lambda: self.teensy.send("<S,6>"))
        grid.addWidget(v1_open, 9, 2, 1, 1)
        grid.addWidget(v1_close, 9, 3, 1, 1)

        grid.addWidget(QLabel("Valve 2:"), 10, 0, 1, 2)
        v2_open = QPushButton("Open")
        v2_close = QPushButton("Close")
        v2_open.clicked.connect(lambda: self.teensy.send("<S,13>"))
        v2_close.clicked.connect(lambda: self.teensy.send("<S,14>"))
        grid.addWidget(v2_open, 10, 2, 1, 1)
        grid.addWidget(v2_close, 10, 3, 1, 1)

        pg.setConfigOptions(antialias=False)
        self.main_plot = pg.PlotWidget()
        self.main_plot.setBackground("#000000")
        self.main_plot.setTitle("Waiting to start", color="#FFFFFF", size="14pt")
        self.main_plot.setYRange(-1, 15, padding=0)
        self.main_plot.setXRange(0, self.cfg.n_sec_disp, padding=0)
        self.main_plot.setLabel("bottom", "SECS")
        self.main_plot.getAxis("left").setTicks(
            [
                [
                    (0, "Bar Code"),
                    (3, "Frames"),
                    (5, "Reward Valve"),
                    (7, "Remove Valve"),
                    (9, "Wheel"),
                    (11, "Opto"),
                    (13, "Piezo"),
                    (14, "Licks"),
                ]
            ]
        )

        palette = ["#75f5f3", "#75f5a7", "#f49234", "#5af434", "#f674e6", "#e8f028"]
        self.curves = [
            self.main_plot.plot(pen=pg.mkPen(palette[4], width=1.2)),
            self.main_plot.plot(pen=pg.mkPen(palette[0], width=1.2)),
            self.main_plot.plot(pen=pg.mkPen(palette[1], width=1.2)),
            self.main_plot.plot(pen=pg.mkPen(palette[2], width=1.2)),
            self.main_plot.plot(pen=pg.mkPen(palette[3], width=1.2)),
            self.main_plot.plot(pen=pg.mkPen(palette[4], width=1.2)),
            self.main_plot.plot(pen=pg.mkPen(palette[1], width=1.2)),
            self.main_plot.plot(
                pen=None,
                symbol="o",
                symbolSize=4,
                symbolBrush=palette[5],
                symbolPen=None,
            ),
        ]
        grid.addWidget(self.main_plot, 2, 4, 9, 11)

        self.perf_plot = pg.PlotWidget()
        self.perf_plot.setBackground("#000000")
        self.perf_plot.setTitle("Performance", color="#FFFFFF", size="12pt")
        self.perf_plot.setYRange(0.3, 4.7, padding=0)
        self.perf_plot.setXRange(0, 10, padding=0)
        self.perf_plot.getAxis("left").setTicks(
            [[(1, "Hits"), (2, "Misses"), (3, "CWs"), (4, "FAs")]]
        )
        bar_brushes = [
            QColor(17, 138, 178),
            QColor(6, 214, 160),
            QColor(255, 209, 102),
            QColor(239, 71, 111),
        ]
        self.perf_bars = pg.BarGraphItem(
            x0=[0, 0, 0, 0],
            y=[1, 2, 3, 4],
            height=0.6,
            width=[0, 0, 0, 0],
            brushes=bar_brushes,
        )
        self.perf_plot.addItem(self.perf_bars)
        grid.addWidget(self.perf_plot, 1, 15, 4, 5)

        self.psych_plot = pg.PlotWidget()
        self.psych_plot.setBackground("#000000")
        self.psych_plot.setLabel("bottom", "Stimulus Amplitude")
        self.psych_plot.setLabel("left", "P(hit)")
        self.psych_plot.setYRange(0, 1.05, padding=0)
        x_ticks = [0.0] + list(self.cfg.sig_amps)
        self.psych_plot.setXRange(0, max(x_ticks) if max(x_ticks) > 0 else 1.0, padding=0.05)
        self.psych_plot.getAxis("bottom").setTicks([[(v, str(v)) for v in x_ticks]])
        self.psych_curve = self.psych_plot.plot(
            x_ticks,
            [0.0] * len(x_ticks),
            pen=pg.mkPen(QColor(131, 56, 236), width=2),
            symbol="o",
            symbolSize=8,
            symbolBrush=QColor(131, 56, 236),
        )
        grid.addWidget(self.psych_plot, 5, 15, 5, 5)

        quit_btn = QPushButton("Quit")
        quit_btn.setStyleSheet("font-size: 16px; font-weight: bold;")
        quit_btn.clicked.connect(self.close)
        grid.addWidget(quit_btn, 10, 18, 1, 2)

        for col in range(4, 15):
            grid.setColumnStretch(col, 2)
        for col in range(15, 20):
            grid.setColumnStretch(col, 1)

    def _populate_ports(self) -> None:
        current = self.port_combo.currentText()
        self.port_combo.clear()
        ports = self.teensy.list_ports()
        self.port_combo.addItems(ports)
        if self.cfg.serial_port and self.cfg.serial_port in ports:
            self.port_combo.setCurrentText(self.cfg.serial_port)
        elif current in ports:
            self.port_combo.setCurrentText(current)

    def toggle_serial(self) -> None:
        if self.teensy.is_connected:
            self.stop_run()
            self.teensy.disconnect()
            self.connect_btn.setText("Connect")
            self.port_combo.setEnabled(True)
        else:
            port = self.port_combo.currentText().strip()
            if not port:
                QMessageBox.warning(self, "No Serial Port", "No serial port selected.")
                return
            try:
                self.teensy.connect(port)
            except Exception as exc:
                QMessageBox.critical(self, "Serial Connection Failed", str(exc))
                return
            self.connect_btn.setText("Disconnect")
            self.port_combo.setEnabled(False)
            time.sleep(0.2)
            print("Stream stats:", self.teensy.get_stream_stats())

    def _choose_save_path(self) -> None:
        folder = QFileDialog.getExistingDirectory(
            self, "Select Output Directory", self.path_label.text()
        )
        if folder:
            self.path_label.setText(folder)

    def _select_run_type(self, run_type: int) -> None:
        self.selected_run_type = run_type
        active_style = "background-color: #1f4e5f; border-color: #75f5f3; font-weight: bold;"
        self.detect_btn.setStyleSheet(active_style if run_type == 1 else "")
        self.pair_btn.setStyleSheet(active_style if run_type == 2 else "")
        self.live_btn.setStyleSheet(active_style if run_type == 4 else "")
        self.runner.set_idle_title_for_mode(run_type)

    def start_run(self) -> None:
        if not self.teensy.is_connected:
            QMessageBox.warning(self, "Not Connected", "Connect to the Teensy serial port first.")
            return
        self.begin_btn.setEnabled(False)
        self.end_btn.setEnabled(True)
        self.detect_btn.setEnabled(False)
        self.pair_btn.setEnabled(False)
        self.live_btn.setEnabled(False)
        self.runner.start(
            run_type=self.selected_run_type,
            base_save_dir=self.path_label.text(),
            run_id_prefix=self.id_edit.text(),
            get_notes_text=self.notes_edit.toPlainText,
        )

    def stop_run(self) -> None:
        self.runner.stop()
        self._set_idle_buttons()

    def _set_idle_buttons(self) -> None:
        self.begin_btn.setEnabled(True)
        self.end_btn.setEnabled(False)
        self.detect_btn.setEnabled(True)
        self.pair_btn.setEnabled(True)
        self.live_btn.setEnabled(True)

    def _refresh_ui(self) -> None:
        state = self.runner.snapshot_ui_state()

        if state["is_running"]:
            snapshots = self.teensy.get_trace_snapshots()
            for idx, curve in enumerate(self.curves):
                curve.setData(self.teensy.x_axis, snapshots[idx], connect="finite")
        else:
            for curve in self.curves:
                curve.setData([], [])

        if not state["is_running"] and self.end_btn.isEnabled():
            self._set_idle_buttons()

        self.main_plot.setTitle(state["title_text"], color="#FFFFFF", size="14pt")

        hi = state["highlight"]
        for code, lbl in self.outcome_labels.items():
            color = "#00FFFF" if code == hi else "#808080"
            lbl.setStyleSheet(f"font-size: 26px; font-weight: bold; color: {color};")

        if state["perf_dirty"]:
            self.runner.clear_perf_dirty()
            counts = state["counts"]
            p_hit_counts = state["p_hit_counts"]

            self.perf_bars.setOpts(width=counts)
            self.perf_plot.setXRange(0, max(counts) + 1.0, padding=0)

            fa_denom = counts[3] + counts[2]
            fa_rate = (counts[3] / fa_denom) if fa_denom > 0 else 0.0

            hit_rates = []
            for j in range(len(self.cfg.sig_amps)):
                denom = p_hit_counts[0, j] + p_hit_counts[1, j]
                hit_rates.append((p_hit_counts[0, j] / denom) if denom > 0 else 0.0)

            self.psych_curve.setData([0.0] + list(self.cfg.sig_amps), [fa_rate] + hit_rates)

    def closeEvent(self, event) -> None:
        self.ui_timer.stop()
        self.runner.stop()
        self.teensy.shutdown()
        event.accept()