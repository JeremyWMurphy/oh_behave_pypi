import sys
from PySide6.QtWidgets import QApplication

from oh_behave_pypi.config import ExperimentConfig, TeensyParams
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
        
        # Override specific experiment parameters:
        serial_port = "/dev/ttyACM0",
        n_trials = 300,
        sig_amps = [0.5, 1.0, 2.0, 4.0],

        # Override specific hardware/state-machine timing parameters:
        tp = TeensyParams(
            enforceEarlyLick = 0,  
            valveLen = 350,        
            respLen = 700,         
        ),
    )

    app = QApplication(sys.argv)
    teensy = TeensyController(cfg)
    win = OhBehaveWindow(cfg, teensy)
    win.show()
    sys.exit(app.exec())

if __name__ == "__main__":
    main()