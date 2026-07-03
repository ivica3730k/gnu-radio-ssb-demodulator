#!/usr/bin/env python3
"""SSB receiver reading directly from an RTL-SDR, output to PulseAudio."""

import argparse
import logging
import threading

import osmosdr

from gnu_radio_ssb_demodulator.ssb_rx_factory import build_ssb_rx

logger = logging.getLogger("rx")
PEAK_WARNING_THRESHOLD = 0.8


class LevelLogger(threading.Thread):
    def __init__(self, peak_probe, polling_period_seconds=1.0, warning_threshold=PEAK_WARNING_THRESHOLD):
        super().__init__(daemon=True)
        self.peak_probe = peak_probe
        self.polling_period_seconds = polling_period_seconds
        self.warning_threshold = warning_threshold
        self._stop_event = threading.Event()

    def run(self):
        while not self._stop_event.wait(self.polling_period_seconds):
            peak_amplitude = self.peak_probe.read_and_reset()
            if peak_amplitude >= self.warning_threshold:
                logger.warning("peak amplitude %.3f >= %.2f - approaching clip", peak_amplitude, self.warning_threshold)
            else:
                logger.info("peak amplitude %.3f", peak_amplitude)

    def stop(self):
        self._stop_event.set()


def main():
    argument_parser = argparse.ArgumentParser(description="SSB receiver from an RTL-SDR (direct)")
    argument_parser.add_argument("--mode", choices=["usb", "lsb"], default="usb", dest="sideband_mode")
    argument_parser.add_argument("--hardware-frequency", type=int, required=True, dest="hardware_frequency_hz")
    argument_parser.add_argument("--dial-frequency", type=int, required=True, dest="dial_frequency_hz")
    argument_parser.add_argument("--bpf-low", type=int, default=200, dest="bandpass_low_hz")
    argument_parser.add_argument("--bpf-high", type=int, default=3000, dest="bandpass_high_hz")
    argument_parser.add_argument(
        "--volume", type=float, default=80.0, help="0 to 100 percent", dest="output_volume_percent"
    )
    argument_parser.add_argument("--gain", type=float, default=30.0, help="RF gain in dB", dest="rf_gain_db")
    argument_parser.add_argument("--ppm", type=int, default=14, dest="frequency_correction_ppm")
    argument_parser.add_argument("--agc", action="store_true", dest="agc_enabled")
    argument_parser.add_argument("--bias-t", action="store_true", dest="bias_tee_enabled")
    argument_parser.add_argument("--audio-output-device", default="pulse", dest="audio_output_device")
    parsed_arguments = argument_parser.parse_args()

    if not 0.0 <= parsed_arguments.output_volume_percent <= 100.0:
        argument_parser.error("--volume must be between 0 and 100")

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    global logger
    logger = logging.getLogger(f"rx.{parsed_arguments.dial_frequency_hz}")

    device_arguments = "rtl=0" + (",bias=1" if parsed_arguments.bias_tee_enabled else "")
    iq_source = osmosdr.source(args=device_arguments)
    top_block = build_ssb_rx(
        iq_source,
        parsed_arguments.sideband_mode,
        parsed_arguments.hardware_frequency_hz,
        parsed_arguments.dial_frequency_hz,
        parsed_arguments.bandpass_low_hz,
        parsed_arguments.bandpass_high_hz,
        parsed_arguments.output_volume_percent,
        parsed_arguments.rf_gain_db,
        parsed_arguments.audio_output_device,
        parsed_arguments.frequency_correction_ppm,
        parsed_arguments.agc_enabled,
    )
    top_block.start()
    level_logger = LevelLogger(top_block.peak_probe)
    level_logger.start()
    try:
        top_block.wait()
    except KeyboardInterrupt:
        pass
    finally:
        level_logger.stop()
        top_block.stop()
        top_block.wait()


if __name__ == "__main__":
    main()
