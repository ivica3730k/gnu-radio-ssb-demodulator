"""Factory and graph for SSB receiver top block construction."""

import math
import threading

import numpy as np
from gnuradio import analog, audio, blocks, filter, gr
from gnuradio.filter import firdes

DEFAULT_SAMPLE_RATE = 1_800_000
TARGET_INTERMEDIATE_FREQUENCY_RATE = 50_000
AUDIO_SAMPLE_RATE = 48_000


class PeakProbe(gr.sync_block):
    def __init__(self):
        gr.sync_block.__init__(self, name="peak_probe", in_sig=[np.float32], out_sig=None)
        self._peak = 0.0
        self._lock = threading.Lock()

    def work(self, input_items, output_items):
        input_samples = input_items[0]
        if len(input_samples):
            current_peak = float(np.max(np.abs(input_samples)))
            with self._lock:
                if current_peak > self._peak:
                    self._peak = current_peak
        return len(input_samples)

    def read_and_reset(self):
        with self._lock:
            peak_value, self._peak = self._peak, 0.0
        return peak_value


class ReceiverHardware(gr.top_block):
    def __init__(
        self,
        iq_source,
        hardware_frequency_hz,
        sample_rate_hz,
        rf_gain_db,
        frequency_correction_ppm,
        bias_tee_enabled,
    ):
        gr.top_block.__init__(self, "SSB RX")

        self.sample_rate_hz = sample_rate_hz
        self.channel_decimation = max(1, round(self.sample_rate_hz / TARGET_INTERMEDIATE_FREQUENCY_RATE))
        self.intermediate_frequency_rate = self.sample_rate_hz / self.channel_decimation
        self.iq_source_block = iq_source
        self.iq_source_block.set_sample_rate(self.sample_rate_hz)
        self.iq_source_block.set_center_freq(hardware_frequency_hz, 0)
        self.iq_source_block.set_gain_mode(False, 0)
        self.iq_source_block.set_gain(rf_gain_db, 0)
        self.iq_source_block.set_freq_corr(frequency_correction_ppm, 0)
        self.iq_source_block.set_iq_balance_mode(2, 0)
        self.iq_source_block.set_dc_offset_mode(2, 0)
        if hasattr(self.iq_source_block, "set_bias"):
            self.iq_source_block.set_bias(bool(bias_tee_enabled), 0)


class Demodulator:
    def __init__(
        self,
        sideband_mode,
        sample_rate_hz,
        channel_offset_hz,
        bandpass_low_hz,
        bandpass_high_hz,
        output_volume_percent,
        audio_output_device,
        agc_enabled,
    ):
        channel_decimation = max(1, round(sample_rate_hz / TARGET_INTERMEDIATE_FREQUENCY_RATE))
        intermediate_frequency_rate = sample_rate_hz / channel_decimation
        channel_filter_taps = firdes.low_pass(1.0, sample_rate_hz, 6_000, 4_000)
        self.frequency_translator = filter.freq_xlating_fir_filter_ccc(
            channel_decimation,
            channel_filter_taps,
            channel_offset_hz,
            sample_rate_hz,
        )

        passband_low_hz, passband_high_hz = (
            (bandpass_low_hz, bandpass_high_hz)
            if sideband_mode.lower() == "usb"
            else (-bandpass_high_hz, -bandpass_low_hz)
        )
        sideband_filter_taps = firdes.complex_band_pass(
            1.0, intermediate_frequency_rate, passband_low_hz, passband_high_hz, 200
        )
        self.sideband_filter = filter.fir_filter_ccc(1, sideband_filter_taps)

        if agc_enabled:
            self.automatic_gain_control = analog.agc2_cc(5.0, 1e-3, 0.5, 1.0)  # attack, decay, ref, gain
            self.automatic_gain_control.set_max_gain(120)
            self.peak_limiter = analog.rail_ff(-0.9, 0.9)
        else:
            self.automatic_gain_control = blocks.multiply_const_cc(1.0)
            self.peak_limiter = blocks.multiply_const_ff(1.0)

        interpolation, decimation = _build_resampler_ratio(sample_rate_hz, channel_decimation)
        self.complex_to_real = blocks.complex_to_real(1)
        self.audio_resampler = filter.rational_resampler_fff(interpolation=interpolation, decimation=decimation)
        self.volume_adjustment = blocks.multiply_const_ff(output_volume_percent / 100.0)
        pulse_device_name = (
            f"pulse:{audio_output_device}" if audio_output_device not in ("", "pulse", "default") else "pulse"
        )
        self.audio_sink = audio.sink(AUDIO_SAMPLE_RATE, pulse_device_name, True)
        self.peak_probe = PeakProbe()

    def attach_to(self, receiver_hardware: ReceiverHardware):
        receiver_hardware.connect(
            receiver_hardware.iq_source_block,
            self.frequency_translator,
            self.sideband_filter,
            self.automatic_gain_control,
            self.complex_to_real,
            self.audio_resampler,
            self.peak_limiter,
            self.volume_adjustment,
            self.audio_sink,
        )
        receiver_hardware.connect(self.volume_adjustment, self.peak_probe)


def _build_resampler_ratio(sample_rate_hz, channel_decimation):
    numerator = AUDIO_SAMPLE_RATE * channel_decimation
    denominator = sample_rate_hz
    common_divisor = math.gcd(numerator, denominator)
    return numerator // common_divisor, denominator // common_divisor
