#!/usr/bin/env python3
"""SSB receiver reading directly from an RTL-SDR, output to PulseAudio."""
import argparse
import logging
import threading

import numpy as np
from gnuradio import gr, blocks, filter, audio, analog
from gnuradio.filter import firdes
import osmosdr


log = logging.getLogger("rx")

SAMP_RATE = 1_800_000
DECIM = 36
IF_RATE = SAMP_RATE // DECIM
AUDIO_RATE = 48_000
PEAK_WARN = 0.8


class PeakProbe(gr.sync_block):
    def __init__(self):
        gr.sync_block.__init__(self, name="peak_probe",
                               in_sig=[np.float32], out_sig=None)
        self._peak = 0.0
        self._lock = threading.Lock()

    def work(self, input_items, output_items):
        x = input_items[0]
        if len(x):
            m = float(np.max(np.abs(x)))
            with self._lock:
                if m > self._peak:
                    self._peak = m
        return len(x)

    def read_and_reset(self):
        with self._lock:
            p, self._peak = self._peak, 0.0
        return p


class LevelLogger(threading.Thread):
    def __init__(self, probe, period=1.0, warn=PEAK_WARN):
        super().__init__(daemon=True)
        self.probe, self.period, self.warn = probe, period, warn
        self._stop = threading.Event()

    def run(self):
        while not self._stop.wait(self.period):
            peak = self.probe.read_and_reset()
            if peak >= self.warn:
                log.warning("peak amplitude %.3f >= %.2f - approaching clip", peak, self.warn)
            else:
                log.info("peak amplitude %.3f", peak)

    def stop(self):
        self._stop.set()


class ssb_rx(gr.top_block):
    def __init__(self, mode, hw_freq, dial_freq, bpf_low, bpf_high, volume,
                 gain, bias, audio_dev, ppm, agc):
        gr.top_block.__init__(self, "SSB RX")

        offset = dial_freq - hw_freq

        dev_args = "rtl=0" + (",bias=1" if bias else "")
        self.src = osmosdr.source(args=dev_args)
        self.src.set_sample_rate(SAMP_RATE)
        self.src.set_center_freq(hw_freq, 0)
        self.src.set_gain_mode(False, 0)
        self.src.set_gain(gain, 0)
        self.src.set_freq_corr(ppm, 0)
        self.src.set_iq_balance_mode(2, 0)
        self.src.set_dc_offset_mode(2, 0)

        chan_taps = firdes.low_pass(1.0, SAMP_RATE, 6_000, 4_000)
        self.xlate = filter.freq_xlating_fir_filter_ccc(
            DECIM, chan_taps, offset, SAMP_RATE)

        lo, hi = (bpf_low, bpf_high) if mode == "usb" else (-bpf_high, -bpf_low)
        sb_taps = firdes.complex_band_pass(1.0, IF_RATE, lo, hi, 200)
        self.sbfilt = filter.fir_filter_ccc(1, sb_taps)

        if agc:
            self.agc = analog.agc2_cc(5.0, 1e-3, 0.5, 1.0)  # attack, decay, ref, gain
            self.agc.set_max_gain(120)
            self.limit = analog.rail_ff(-0.9, 0.9)
        else:
            self.agc = blocks.multiply_const_cc(1.0)
            self.limit = blocks.multiply_const_ff(1.0)

        self.c2r = blocks.complex_to_real(1)
        self.resamp = filter.rational_resampler_fff(interpolation=24, decimation=25)
        self.vol = blocks.multiply_const_ff(volume / 100.0)

        dev = f"pulse:{audio_dev}" if audio_dev not in ("", "pulse", "default") else "pulse"
        self.sink = audio.sink(AUDIO_RATE, dev, True)
        self.peak = PeakProbe()

        self.connect(self.src, self.xlate, self.sbfilt, self.agc,
                     self.c2r, self.resamp, self.limit, self.vol, self.sink)
        self.connect(self.vol, self.peak)


def main():
    ap = argparse.ArgumentParser(description="SSB receiver from an RTL-SDR (direct)")
    ap.add_argument("--mode", choices=["usb", "lsb"], default="usb")
    ap.add_argument("--hardware-frequency", type=int, required=True, dest="hw_freq")
    ap.add_argument("--dial-frequency", type=int, required=True, dest="dial_freq")
    ap.add_argument("--bpf-low", type=int, default=200)
    ap.add_argument("--bpf-high", type=int, default=3000)
    ap.add_argument("--volume", type=float, default=80.0, help="0 to 100 percent")
    ap.add_argument("--gain", type=float, default=30.0, help="RF gain in dB")
    ap.add_argument("--ppm", type=int, default=14)
    ap.add_argument("--agc", action="store_true")
    ap.add_argument("--bias-t", action="store_true", dest="bias")
    ap.add_argument("--audio-output-device", default="pulse", dest="audio_dev")
    args = ap.parse_args()

    if not 0.0 <= args.volume <= 100.0:
        ap.error("--volume must be between 0 and 100")

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    global log
    log = logging.getLogger(f"rx.{args.dial_freq}")

    tb = ssb_rx(args.mode, args.hw_freq, args.dial_freq,
                args.bpf_low, args.bpf_high, args.volume, args.gain, args.bias,
                args.audio_dev, args.ppm, args.agc)
    tb.start()
    lvl = LevelLogger(tb.peak)
    lvl.start()
    try:
        tb.wait()
    except KeyboardInterrupt:
        pass
    finally:
        lvl.stop()
        tb.stop()
        tb.wait()


if __name__ == "__main__":
    main()