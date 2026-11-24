from __future__ import annotations

from datetime import datetime
from pathlib import Path
import random
import threading
import time
import numpy as np

from oh_behave_pypi.config import (
    ExperimentConfig,
    build_trial_pool,
    format_config_lines,
    has_n_repeats,
    map_jm,
)
from oh_behave_pypi.teensy_io import TeensyController


class TaskRunner:
    def __init__(self, cfg: ExperimentConfig, teensy: TeensyController) -> None:
        self.cfg = cfg
        self.teensy = teensy
        self.lock = threading.Lock()

        self.abort_event = threading.Event()
        self.thread: threading.Thread | None = None

        self.is_running: bool = False
        self.title_text: str = "Waiting to start"
        self.active_outcome_highlight: int = 0
        self.n_resp_types: list[int] = [0, 0, 0, 0]
        self.p_hit_counts = np.zeros((2, len(cfg.sig_amps)), dtype=np.float64)
        self.perf_dirty: bool = True

    def set_idle_title_for_mode(self, run_type: int) -> None:
        if self.is_running:
            return
        titles = {
            1: "Detection Run... (ready)",
            2: "Pairing Run... (ready)",
            4: "Live Streaming... (ready)",
        }
        with self.lock:
            self.title_text = titles.get(run_type, "Waiting to start")

    def start(
        self,
        run_type: int,
        base_save_dir: str,
        run_id_prefix: str,
        get_notes_text,
    ) -> None:
        if self.is_running:
            return
        self.abort_event.clear()
        self.is_running = True
        self.thread = threading.Thread(
            target=self._run_loop,
            args=(run_type, base_save_dir, run_id_prefix, get_notes_text),
            daemon=True,
        )
        self.thread.start()

    def stop(self) -> None:
        self.abort_event.set()
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=1.5)
        self.teensy.stop_stream_logging()
        with self.lock:
            self.is_running = False
            self.active_outcome_highlight = 0
            self.title_text = "Waiting to start"

    def snapshot_ui_state(self) -> dict:
        with self.lock:
            return {
                "is_running": self.is_running,
                "title_text": self.title_text,
                "highlight": self.active_outcome_highlight,
                "counts": list(self.n_resp_types),
                "p_hit_counts": self.p_hit_counts.copy(),
                "perf_dirty": self.perf_dirty,
            }

    def clear_perf_dirty(self) -> None:
        with self.lock:
            self.perf_dirty = False

    def _sleep_interruptible(self, seconds: float) -> None:
        end_t = time.perf_counter() + seconds
        while time.perf_counter() < end_t:
            if self.abort_event.is_set() or self.teensy.app_stop_event.is_set():
                return
            time.sleep(min(0.05, max(0.001, end_t - time.perf_counter())))

    def _run_loop(
        self,
        run_type: int,
        base_save_dir: str,
        run_id_prefix: str,
        get_notes_text,
    ) -> None:
        try:
            self.teensy.send("<S,1>")
            time.sleep(0.05)

            if run_type == 4:
                with self.lock:
                    self.title_text = "live streaming, not saving..."
                while not self.abort_event.is_set() and not self.teensy.app_stop_event.is_set():
                    time.sleep(0.05)
                self.teensy.send("<S,1>")
                self.teensy.send("<S,0>")
                return

            stamp = datetime.now().strftime("%Y-%m-%d_T%H-%M-%S")
            prefix = run_id_prefix.strip() or "NA"
            run_id = f"{prefix}_{stamp}"
            save_dir = Path(base_save_dir) / run_id
            save_dir.mkdir(parents=True, exist_ok=True)

            stream_fh = open(save_dir / "data_stream.bin", "wb")
            notes_fh = open(save_dir / "data_notes.csv", "w", encoding="utf-8")

            notes_fh.write(run_id)
            for line in format_config_lines(self.cfg):
                notes_fh.write(line)
            notes_fh.flush()

            trls, ttype_dict = build_trial_pool(self.cfg)
            random.shuffle(trls)
            if self.cfg.limit_repeats:
                while has_n_repeats(trls, self.cfg.n_repeats):
                    random.shuffle(trls)

            if self.cfg.n_start_gomax > 0:
                start_stim_id = len(self.cfg.sig_amps)
                trls = ([start_stim_id] * self.cfg.n_start_gomax) + trls

            sig_amps_12bit = [
                int(round(map_jm(a, self.cfg.dac_volt_range[0], self.cfg.dac_volt_range[1], 
                                 self.cfg.dac_bit_range[0],self.cfg.dac_bit_range[1]))) for a in self.cfg.sig_amps
            ]
            opto_amp_12bit = int(round(map_jm(self.cfg.opto_amp, self.cfg.dac_volt_range[0], self.cfg.dac_volt_range[1], 
                                 self.cfg.dac_bit_range[0],self.cfg.dac_bit_range[1])))

            with self.lock:
                self.n_resp_types = [0, 0, 0, 0]
                self.p_hit_counts = np.zeros((2, len(self.cfg.sig_amps)), dtype=np.float64)
                self.perf_dirty = True

            self.teensy.start_stream_logging(stream_fh)
            self.teensy.send("<S,7>")
            time.sleep(self.cfg.tp.trigLen / 1000.0)

            notes_fh.write(f"\nRun Began at {datetime.now().strftime('%H:%M:%S')}")
            if run_type == 1:
                notes_fh.write("\nDetection Task")
                self.teensy.send("<P,12,0>")
            else:
                notes_fh.write("\nPairing Task")
                self.teensy.send("<P,12,1>")
            notes_fh.flush()

            self._sleep_interruptible(self.cfg.baseln)

            trl_idx = 0
            n_reset_cnts = 0
            reset_off = False

            while (
                trl_idx < min(self.cfg.n_trials, len(trls))
                and not self.abort_event.is_set()
                and not self.teensy.app_stop_event.is_set()
            ):
                trial_type = trls[trl_idx]
                iti_ms = int(
                    round(
                        1000.0
                        * (
                            self.cfg.iti_len[0]
                            + (self.cfg.iti_len[1] - self.cfg.iti_len[0]) * random.random()
                        )
                    )
                )

                if trial_type >= 10:
                    opto_idx = trial_type // 10
                    if opto_idx == 10:
                        opto_offset = 0
                        is_go = False
                        piezo_amp = 0
                        o_amp = opto_amp_12bit
                    else:
                        opto_offset = self.cfg.opto_times[opto_idx - 1]
                        piezo_amp = sig_amps_12bit[(trial_type % 10) - 1]
                        is_go = True
                        o_amp = opto_amp_12bit
                else:
                    if trial_type == 0:
                        is_go = False
                        o_amp = 0
                        piezo_amp = 0
                        opto_offset = 0
                    else:
                        piezo_amp = sig_amps_12bit[trial_type - 1]
                        is_go = True
                        o_amp = 0
                        opto_offset = 0

                self.teensy.send(
                    f"<W,{self.cfg.piezo_chan},{self.cfg.pulse_type},{self.cfg.pulse_len},"
                    f"{piezo_amp},{self.cfg.pulse_intrvl},{self.cfg.pulse_reps},{iti_ms}>"
                )
                self.teensy.send(
                    f"<W,{self.cfg.opto_chan},{self.cfg.opto_pulse_type},{self.cfg.opto_len},"
                    f"{o_amp},0,1,{max(0, iti_ms + opto_offset)}>"
                )

                trial_num_1based = trl_idx + 1
                desc = ttype_dict.get(trial_type, str(trial_type))
                notes_fh.write(
                    f"\n Trial {trial_num_1based} {datetime.now().strftime('%H:%M:%S')}, {desc}"
                )
                with self.lock:
                    self.title_text = f"Trial {trial_num_1based}, {desc}"

                self.teensy.trial_end_entered.clear()
                self.teensy.send("<S,2>" if is_go else "<S,3>")

                while not self.teensy.trial_end_entered.is_set():
                    if self.abort_event.is_set() or self.teensy.app_stop_event.is_set():
                        break
                    self.teensy.trial_end_entered.wait(timeout=0.05)

                if self.abort_event.is_set() or self.teensy.app_stop_event.is_set():
                    break

                outcome = self.teensy.teensy_outcome
                with self.lock:
                    self.active_outcome_highlight = outcome

                if reset_off:
                    self.teensy.send("<P,1,1>")
                    reset_off = False

                amp_matches = [i for i, a in enumerate(sig_amps_12bit) if a == piezo_amp]
                with self.lock:
                    if outcome == 1:
                        self.n_resp_types[0] += 1
                        for idx in amp_matches:
                            self.p_hit_counts[0, idx] += 1
                        n_reset_cnts = 0
                    elif outcome == 2:
                        self.n_resp_types[1] += 1
                        for idx in amp_matches:
                            self.p_hit_counts[1, idx] += 1
                        n_reset_cnts = 0
                    elif outcome == 3:
                        self.n_resp_types[2] += 1
                        n_reset_cnts = 0
                    elif outcome == 4:
                        self.n_resp_types[3] += 1
                        n_reset_cnts = 0
                    elif outcome == 5:
                        trl_idx -= 1
                        n_reset_cnts += 1
                        if n_reset_cnts >= self.cfg.n_resets and self.cfg.just_go_after_reset:
                            self.teensy.send("<P,1,0>")
                            reset_off = True
                        if n_reset_cnts >= self.cfg.n_resets:
                            n_reset_cnts = 0
                    self.perf_dirty = True

                if outcome == 4 and trial_type < 10 and self.cfg.fa_timeout_len[1] > 0:
                    lo, hi = self.cfg.fa_timeout_len
                    self._sleep_interruptible(lo + (hi - lo) * random.random())
                elif outcome == 5 and self.cfg.error_timeout_len[1] > 0:
                    lo, hi = self.cfg.error_timeout_len
                    self._sleep_interruptible(lo + (hi - lo) * random.random())

                while not self.teensy.trial_end_exited.is_set():
                    if self.abort_event.is_set() or self.teensy.app_stop_event.is_set():
                        break
                    self.teensy.trial_end_exited.wait(timeout=0.05)

                with self.lock:
                    self.active_outcome_highlight = 0
                notes_fh.write(f", Outcome = {outcome}")
                notes_fh.flush()
                trl_idx += 1

            self.teensy.send("<S,1>")
            self.teensy.send("<S,0>")
            if self.abort_event.is_set():
                notes_fh.write(f"\nRun aborted at {datetime.now().strftime('%H:%M:%S')}\n")
            else:
                with self.lock:
                    self.title_text = "Task Complete"
                notes_fh.write(f"\nRun completed at {datetime.now().strftime('%H:%M:%S')}\n")
                self._sleep_interruptible(2.0)

            notes_fh.write("\n" + get_notes_text() + "\n")
            notes_fh.close()
        finally:
            self.teensy.stop_stream_logging()
            with self.lock:
                self.is_running = False
                self.active_outcome_highlight = 0
                self.title_text = "Waiting to start"