from __future__ import annotations

from dataclasses import asdict
import queue
import struct
import threading
import time
import numpy as np
import serial
import serial.tools.list_ports

from oh_behave_pypi.config import ExperimentConfig, PARAMETER_CODES

# Wire format (26 bytes, little-endian, matching BinaryTelemetryFrame):
# sync0 (B), sync1 (B), loopNum (I), frameNum (I), stateNum (H), outcomeNum (H),
# val0 (H), val1 (H), lick (h), wheel (H), v1State (B), v2State (B), barcode (B), checksum (B)
FRAME_SIZE = 26
FRAME_STRUCT = struct.Struct("<BBIIHHHHhHBBBB")
SYNC_0 = 0xAA
SYNC_1 = 0x55


class TeensyController:
    def __init__(self, cfg: ExperimentConfig) -> None:
        self.cfg = cfg
        self.disp_len = cfg.teensy_fs * cfg.n_sec_disp
        self.x_axis = np.arange(self.disp_len, dtype=np.float64) / float(cfg.teensy_fs)

        self.ser: serial.Serial | None = None
        self.write_lock = threading.Lock()
        self.data_lock = threading.Lock()

        self.app_stop_event = threading.Event()
        self.logging_active = threading.Event()

        self.reader_thread: threading.Thread | None = None
        self.stream_queue: queue.Queue[bytes] = queue.Queue(maxsize=20_000)
        self.stream_file = None
        self.writer_thread = threading.Thread(target=self._writer_loop, daemon=True)
        self.writer_thread.start()

        self.traces = [np.full(self.disp_len, np.nan, dtype=np.float64) for _ in range(8)]
        self.last_frame_num: int | None = None

        self.teensy_state: int = 0
        self.teensy_outcome: int = 0
        self.trial_end_entered = threading.Event()
        self.trial_end_exited = threading.Event()
        self.trial_end_exited.set()

        # Optional stream health counters (helpful at 5–10 kHz)
        self.valid_frames: int = 0
        self.bad_checksums: int = 0
        self.resync_skips: int = 0
        self.last_loop_num: int | None = None
        self.dropped_loops: int = 0

    @staticmethod
    def list_ports() -> list[str]:
        return [p.device for p in serial.tools.list_ports.comports()]

    @property
    def is_connected(self) -> bool:
        return self.ser is not None and self.ser.is_open

    def connect(self, port: str) -> None:
        ser = serial.Serial(port, self.cfg.baud_rate, timeout=0.05)
        time.sleep(0.5)
        ser.reset_input_buffer()
        self.ser = ser

        with self.data_lock:
            self.last_frame_num = None
            self.last_loop_num = None
            self.valid_frames = 0
            self.bad_checksums = 0
            self.resync_skips = 0
            self.dropped_loops = 0

        self.reader_thread = threading.Thread(target=self._reader_loop, daemon=True)
        self.reader_thread.start()
        self.sync_parameters()

    def disconnect(self) -> None:
        ser = self.ser
        self.ser = None
        if self.reader_thread and self.reader_thread.is_alive():
            self.reader_thread.join(timeout=0.5)
        if ser and ser.is_open:
            try:
                ser.write(b"<S,1><S,0>")
                ser.close()
            except serial.SerialException:
                pass

    def send(self, msg: str) -> None:
        ser = self.ser
        if ser is None or not ser.is_open:
            return
        with self.write_lock:
            try:
                ser.write(msg.encode("ascii"))
            except serial.SerialException:
                pass

    def sync_parameters(self) -> None:
        for name, val in asdict(self.cfg.tp).items():
            code = PARAMETER_CODES[name]
            self.send(f"<P,{code},{int(val)}>")
            time.sleep(0.02)

    def start_stream_logging(self, file_handle) -> None:
        """Pass a binary file handle opened with open(path, 'wb')."""
        self.stop_stream_logging()
        with self.data_lock:
            self.stream_file = file_handle
        self.logging_active.set()

    def stop_stream_logging(self) -> None:
        self.logging_active.clear()
        while not self.stream_queue.empty():
            time.sleep(0.01)
        with self.data_lock:
            fh = self.stream_file
            self.stream_file = None
        if fh is not None and not fh.closed:
            try:
                fh.flush()
                fh.close()
            except OSError:
                pass

    def get_trace_snapshots(self) -> list[np.ndarray]:
        with self.data_lock:
            return [tr.copy() for tr in self.traces]

    def get_stream_stats(self) -> dict[str, int]:
        with self.data_lock:
            return {
                "valid_frames": self.valid_frames,
                "bad_checksums": self.bad_checksums,
                "resync_skips": self.resync_skips,
                "dropped_loops": self.dropped_loops,
            }

    def shutdown(self) -> None:
        self.app_stop_event.set()
        self.stop_stream_logging()
        self.disconnect()
        if self.writer_thread.is_alive():
            self.writer_thread.join(timeout=0.5)

    @staticmethod
    def _verify_xor_checksum(frame_bytes: bytes | bytearray | memoryview) -> bool:
        # XOR payload bytes [2:-1] and compare against last byte [-1]
        xor_val = 0
        for b in frame_bytes[2:-1]:
            xor_val ^= b
        return xor_val == frame_bytes[-1]

    def _reader_loop(self) -> None:
        buf = bytearray()

        while not self.app_stop_event.is_set():
            ser = self.ser
            if ser is None or not ser.is_open:
                break
            try:
                waiting = ser.in_waiting
                chunk = ser.read(waiting if waiting > 0 else 1)
                print(len(chunk))
            except serial.SerialException:
                break
            if not chunk:
                continue

            buf.extend(chunk)
            parsed_rows: list[tuple] = []
            raw_frames: list[bytes] = []
            bad_csum = 0
            skips = 0

            i = 0
            n_buf = len(buf)
            while i + FRAME_SIZE <= n_buf:
                if buf[i] != SYNC_0 or buf[i + 1] != SYNC_1:
                    i += 1
                    skips += 1
                    continue

                frame_mv = memoryview(buf)[i : i + FRAME_SIZE]
                if not self._verify_xor_checksum(frame_mv):
                    bad_csum += 1
                    # Advance past sync0 so we can resync cleanly
                    i += 1
                    continue

                fields = FRAME_STRUCT.unpack_from(frame_mv, 0)
                parsed_rows.append(fields)
                raw_frames.append(bytes(frame_mv))
                i += FRAME_SIZE

            if i > 0:
                del buf[:i]

            if not parsed_rows and bad_csum == 0 and skips == 0:
                continue

            if raw_frames and self.logging_active.is_set():
                try:
                    self.stream_queue.put_nowait(b"".join(raw_frames))
                except queue.Full:
                    pass

            arr = np.asarray(parsed_rows, dtype=np.float64) if parsed_rows else None
            n_new = arr.shape[0] if arr is not None else 0

            with self.data_lock:
                self.bad_checksums += bad_csum
                self.resync_skips += skips

                if arr is not None and n_new > 0:
                    self.valid_frames += n_new

                    # Check loopNum continuity to detect any dropped samples
                    loop_nums = arr[:, 2].astype(np.int64)
                    if self.last_loop_num is not None:
                        expected_first = self.last_loop_num + 1
                        if loop_nums[0] > expected_first:
                            self.dropped_loops += int(loop_nums[0] - expected_first)
                    diffs_inside = np.diff(loop_nums)
                    if len(diffs_inside) > 0:
                        gaps = diffs_inside[diffs_inside > 1] - 1
                        if len(gaps) > 0:
                            self.dropped_loops += int(np.sum(gaps))
                    self.last_loop_num = int(loop_nums[-1])

                    # Unpacked column indices:
                    # 0:sync0, 1:sync1, 2:loopNum, 3:frameNum, 4:stateNum, 5:outcomeNum,
                    # 6:val0, 7:val1, 8:lick, 9:wheel, 10:v1State, 11:v2State, 12:barcode, 13:checksum
                    barcode_vals = arr[:, 12]

                    frame_nums = arr[:, 3]
                    prev_f = self.last_frame_num if self.last_frame_num is not None else frame_nums[0]
                    frame_diffs = np.diff(frame_nums, prepend=prev_f)
                    self.last_frame_num = int(frame_nums[-1])
                    frames_vals = 3.0 + (frame_diffs > 0).astype(np.float64)

                    v1_vals = 5.0 + arr[:, 10]
                    v2_vals = 7.0 + arr[:, 11]
                    wheel_vals = 9.0 + (arr[:, 9] / 1024.0)
                    opto_vals = 11.0 + (arr[:, 7] / 4095.0)
                    piezo_vals = 13.0 + 5.0 * (arr[:, 6] / 4095.0)

                    licks_raw = arr[:, 8].copy()
                    licks_raw[licks_raw == 0] = np.nan
                    licks_vals = 14.0 + licks_raw

                    new_channels = [
                        barcode_vals,
                        frames_vals,
                        v1_vals,
                        v2_vals,
                        wheel_vals,
                        opto_vals,
                        piezo_vals,
                        licks_vals,
                    ]

                    for ch_idx in range(8):
                        new_vec = new_channels[ch_idx]
                        if n_new >= self.disp_len:
                            self.traces[ch_idx][:] = new_vec[-self.disp_len :]
                        else:
                            self.traces[ch_idx][:-n_new] = self.traces[ch_idx][n_new:]
                            self.traces[ch_idx][-n_new:] = new_vec

                    # State 12 = TRIALEND on Teensy[cite: 1]
                    states = arr[:, 4].astype(np.int32)
                    outcomes = arr[:, 5].astype(np.int32)
                    for st, outc in zip(states, outcomes):
                        prev_st = self.teensy_state
                        self.teensy_state = int(st)
                        if st == 12 and prev_st != 12:
                            self.teensy_outcome = int(outc)
                            self.trial_end_exited.clear()
                            self.trial_end_entered.set()
                        elif st != 12 and prev_st == 12:
                            self.trial_end_entered.clear()
                            self.trial_end_exited.set()

    def _writer_loop(self) -> None:
        while not self.app_stop_event.is_set():
            try:
                blob = self.stream_queue.get(timeout=0.05)
            except queue.Empty:
                continue
            with self.data_lock:
                fh = self.stream_file
            if fh is not None and not fh.closed:
                fh.write(blob)