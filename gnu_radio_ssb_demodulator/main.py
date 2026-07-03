#!/usr/bin/env python3
"""SSB receiver reading directly from an RTL-SDR, output to PulseAudio."""

import argparse
import json
import logging
import threading
from pathlib import Path
from typing import Sequence

import osmosdr

from gnu_radio_ssb_demodulator.helpers import DEFAULT_SAMPLE_RATE, Demodulator, ReceiverHardware
from gnu_radio_ssb_demodulator.json_config_schema import CoreConfigSchema

PEAK_WARNING_THRESHOLD = 0.8


def volume_percent(value: str) -> float:
    parsed_value = float(value)
    if not 0.0 <= parsed_value <= 100.0:
        raise argparse.ArgumentTypeError("--volume must be between 0 and 100")
    return parsed_value


class LevelLogger(threading.Thread):
    def __init__(
        self, peak_probe, receiver_logger, polling_period_seconds=1.0, warning_threshold=PEAK_WARNING_THRESHOLD
    ):
        super().__init__(daemon=True)
        self.peak_probe = peak_probe
        self.receiver_logger = receiver_logger
        self.polling_period_seconds = polling_period_seconds
        self.warning_threshold = warning_threshold
        self._stop_event = threading.Event()

    def run(self):
        while not self._stop_event.wait(self.polling_period_seconds):
            peak_amplitude = self.peak_probe.read_and_reset()
            if peak_amplitude >= self.warning_threshold:
                self.receiver_logger.warning(
                    "peak amplitude %.3f >= %.2f - approaching clip", peak_amplitude, self.warning_threshold
                )
            else:
                self.receiver_logger.info("peak amplitude %.3f", peak_amplitude)

    def stop(self):
        self._stop_event.set()


def main(argv: Sequence[str] | None = None):
    argument_parser = argparse.ArgumentParser(description="SSB receiver from an RTL-SDR (direct)")
    subparsers = argument_parser.add_subparsers(dest="command", required=True)

    cli_parser = subparsers.add_parser("cli", help="Run a single receiver from CLI flags")
    cli_parser.add_argument("--mode", choices=["usb", "lsb"], default="usb", dest="sideband_mode")
    cli_parser.add_argument("--hardware-frequency", type=int, required=True, dest="hardware_frequency_hz")
    cli_parser.add_argument("--dial-frequency", type=int, required=True, dest="dial_frequency_hz")
    cli_parser.add_argument("--bpf-low", type=int, default=200, dest="bandpass_low_hz")
    cli_parser.add_argument("--bpf-high", type=int, default=3000, dest="bandpass_high_hz")
    cli_parser.add_argument(
        "--volume",
        type=volume_percent,
        default=80.0,
        help="0 to 100 percent",
        dest="output_volume_percent",
    )
    cli_parser.add_argument("--gain", type=float, default=30.0, help="RF gain in dB", dest="rf_gain_db")
    cli_parser.add_argument("--ppm", type=int, default=14, dest="frequency_correction_ppm")
    cli_parser.add_argument("--rtl-index", type=int, default=0, dest="rtl_index")
    cli_parser.add_argument("--agc", action="store_true", dest="agc_enabled")
    cli_parser.add_argument("--bias-t", action="store_true", dest="bias_tee_enabled")
    cli_parser.add_argument("--audio-output-device", default="pulse", dest="audio_output_device")

    json_parser = subparsers.add_parser("json", help="Run all receivers from a JSON config file")
    json_parser.add_argument("--json-config-file", required=True, dest="json_config_file")

    parsed_arguments = argument_parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )

    if parsed_arguments.command == "json":
        raw_json = Path(parsed_arguments.json_config_file).read_text(encoding="utf-8")
        core_config = CoreConfigSchema.model_validate(json.loads(raw_json))
        if not core_config.receivers:
            raise ValueError("JSON config must include at least one receiver")

        device_arguments = f"rtl={core_config.rtl_index}" + (",bias=1" if core_config.bias_t else "")
        iq_source = osmosdr.source(args=device_arguments)
        top_block = ReceiverHardware(
            iq_source=iq_source,
            hardware_frequency_hz=core_config.hardware_frequency,
            sample_rate_hz=DEFAULT_SAMPLE_RATE,
            rf_gain_db=core_config.gain,
            frequency_correction_ppm=core_config.ppm,
            bias_tee_enabled=core_config.bias_t,
        )
        for receiver in core_config.receivers:
            demodulator = Demodulator(
                sideband_mode=receiver.mode.value.lower(),
                sample_rate_hz=DEFAULT_SAMPLE_RATE,
                channel_offset_hz=receiver.dial_frequency - core_config.hardware_frequency,
                bandpass_low_hz=receiver.bpf_low,
                bandpass_high_hz=receiver.bpf_high,
                output_volume_percent=receiver.volume,
                audio_output_device=receiver.pulse_audio_output_device,
                agc_enabled=receiver.agc,
            )
            demodulator.attach_to(top_block)
            receiver_logger = logging.getLogger(f"rx.{receiver.dial_frequency}")
            LevelLogger(demodulator.peak_probe, receiver_logger).start()

        top_block.start()
        try:
            top_block.wait()
        except KeyboardInterrupt:
            pass
        return

    device_arguments = f"rtl={parsed_arguments.rtl_index}" + (",bias=1" if parsed_arguments.bias_tee_enabled else "")
    iq_source = osmosdr.source(args=device_arguments)
    top_block = ReceiverHardware(
        iq_source=iq_source,
        hardware_frequency_hz=parsed_arguments.hardware_frequency_hz,
        sample_rate_hz=DEFAULT_SAMPLE_RATE,
        rf_gain_db=parsed_arguments.rf_gain_db,
        frequency_correction_ppm=parsed_arguments.frequency_correction_ppm,
        bias_tee_enabled=parsed_arguments.bias_tee_enabled,
    )
    demodulator = Demodulator(
        sideband_mode=parsed_arguments.sideband_mode,
        sample_rate_hz=DEFAULT_SAMPLE_RATE,
        channel_offset_hz=parsed_arguments.dial_frequency_hz - parsed_arguments.hardware_frequency_hz,
        bandpass_low_hz=parsed_arguments.bandpass_low_hz,
        bandpass_high_hz=parsed_arguments.bandpass_high_hz,
        output_volume_percent=parsed_arguments.output_volume_percent,
        audio_output_device=parsed_arguments.audio_output_device,
        agc_enabled=parsed_arguments.agc_enabled,
    )
    demodulator.attach_to(top_block)
    receiver_logger = logging.getLogger(f"rx.{parsed_arguments.dial_frequency_hz}")
    top_block.start()
    LevelLogger(demodulator.peak_probe, receiver_logger).start()
    try:
        top_block.wait()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
