import sys
from PySide6.QtWidgets import QApplication

from oh_behave_pypi.config import ExperimentConfig
from oh_behave_pypi.teensy_io import TeensyController
from oh_behave_pypi.ui_window import OhBehaveWindow

import warnings

warnings.filterwarnings(
    "ignore",
    message="All-NaN slice encountered",
    category=RuntimeWarning,
)

def main() -> None:
    cfg = ExperimentConfig(
        teensy_fs=10000,
        # serial_port="/dev/ttyACM0",  # Optional override on Raspberry Pi
    )
    app = QApplication(sys.argv)
    teensy = TeensyController(cfg)
    win = OhBehaveWindow(cfg, teensy)
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()