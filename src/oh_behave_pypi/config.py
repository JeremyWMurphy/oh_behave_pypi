from __future__ import annotations

from dataclasses import asdict, dataclass, field
import numpy as np

@dataclass
class TeensyParams:
    enforceEarlyLick: int = 1
    lickMax: int = 1
    waitForNextFrame: int = 0
    contingentStim: int = 0
    trigLen: int = 200
    respLen: int = 3000
    valveLen: int = 500
    consumeLen: int = 1000
    outLen: int = 1000
    removeLen: int = 3000


@dataclass
class ExperimentConfig:
    # Teensy comms & display
    teensy_fs: int = 2000
    baud_rate: int = 115200
    serial_port: str = ""
    n_sec_disp: int = 10

    # Experiment structure
    baseln: float = 5.0
    n_trials: int = 500

    # Piezo waveform parameters
    piezo_chan: int = 0
    pulse_type: int = 5  # 0=whale, 1=square, 2=rampup, 3=rampdown, 4=pyramid, 5=gauss
    pulse_len: int = 100  # ms
    pulse_intrvl: int = 100  # ms
    pulse_reps: int = 10

    # Trial distribution & ITI
    iti_len: tuple[float, float] = (3.0, 3.0)
    prcnt_go_p_alone: float = 1.0
    prcnt_go_p_opto: float = 1.0
    prcnt_opto: float = 1.0
    sig_amps: list[float] = field(default_factory=lambda: [2.0])
    prcnt_amps: list[float] = field(default_factory=lambda: [1.0])
    opto_times: list[int] = field(default_factory=lambda: [-200, -75, -50, -25])

    n_start_gomax: int = 0
    limit_repeats: bool = False
    n_repeats: int = 3
    n_resets: float = float("inf")
    just_go_after_reset: bool = False

    # Timeouts
    error_timeout_len: tuple[float, float] = (0.0, 0.0)
    fa_timeout_len: tuple[float, float] = (0.0, 0.0)

    # Opto parameters
    opto_chan: int = 1
    opto_amp: float = 2.38
    opto_pulse_type: int = 5
    opto_len: int = 50  # ms

    tp: TeensyParams = field(default_factory=TeensyParams)


PARAMETER_CODES: dict[str, int] = {
    "enforceEarlyLick": 1,
    "lickMax": 2,
    "waitForNextFrame": 3,
    "contingentStim": 4,
    "trigLen": 5,
    "respLen": 6,
    "valveLen": 7,
    "consumeLen": 8,
    "outLen": 10,
    "removeLen": 11,
}


def format_config_lines(cfg: ExperimentConfig) -> list[str]:
    lines: list[str] = []
    for k, v in asdict(cfg).items():
        if k != "tp":
            lines.append(f"\n{k}: {v}")
    for k, v in asdict(cfg.tp).items():
        lines.append(f"\n{k}: {v}")
    return lines


def map_jm(x: float, in_min: float, in_max: float, out_min: float, out_max: float) -> float:
    return (x - in_min) * (out_max - out_min) / (in_max - in_min) + out_min


def has_n_repeats(trls: list[int], n: int) -> bool:
    """Returns True if any consecutive run in trls has length >= n."""
    if not trls or n <= 1:
        return bool(trls)
    run_len = 1
    for i in range(1, len(trls)):
        if trls[i] == trls[i - 1]:
            run_len += 1
            if run_len >= n:
                return True
        else:
            run_len = 1
    return False


def build_trial_pool(cfg: ExperimentConfig) -> tuple[list[int], dict[int, str]]:
    trls: list[int] = []
    ttype_dict: dict[int, str] = {}

    n_opto_trials = int(np.ceil(cfg.n_trials * cfg.prcnt_opto))
    for i, opto_t in enumerate(cfg.opto_times, start=1):
        for j, amp in enumerate(cfg.sig_amps, start=1):
            code = (i * 10) + j
            ttype_dict[code] = f"o:{opto_t},p:{amp}"
            n_tmp = int(
                np.ceil(
                    ((n_opto_trials * cfg.prcnt_go_p_opto) * cfg.prcnt_amps[j - 1])
                    / len(cfg.opto_times)
                )
            )
            trls.extend([code] * n_tmp)

    ttype_dict[100] = "opto alone"
    n_opto_alone = int(np.ceil(cfg.n_trials * (1.0 - cfg.prcnt_go_p_opto) * cfg.prcnt_opto))
    trls.extend([100] * n_opto_alone)

    n_nopto_trls = int(np.ceil(cfg.n_trials * (1.0 - cfg.prcnt_opto)))
    for i, amp in enumerate(cfg.sig_amps, start=1):
        ttype_dict[i] = f"p alone:{amp}"
        n_tmp = int(np.ceil((n_nopto_trls * cfg.prcnt_go_p_alone) * cfg.prcnt_amps[i - 1]))
        trls.extend([i] * n_tmp)

    ttype_dict[0] = "full catch"
    n_catch = int(
        np.ceil(cfg.n_trials * (1.0 - cfg.prcnt_go_p_alone) * (1.0 - cfg.prcnt_opto))
    )
    trls.extend([0] * n_catch)

    return trls, ttype_dict