"""Factory and graph for SSB receiver top block construction."""

import threading

import numpy as np
from gnuradio import analog, audio, blocks, filter, gr
from gnuradio.filter import firdes

SAMPLE_RATE = 1_800_000
CHANNEL_DECIMATION = 36
INTERMEDIATE_FREQUENCY_RATE = SAMPLE_RATE // CHANNEL_DECIMATION
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


class SsbRx(gr.top_block):
    def __init__(
        self,
        iq_source,
        sideband_mode,
        hardware_frequency_hz,
        dial_frequency_hz,
        bandpass_low_hz,
        bandpass_high_hz,
        output_volume_percent,
        rf_gain_db,
        audio_output_device,
        frequency_correction_ppm,
        agc_enabled,
    ):
        gr.top_block.__init__(self, "SSB RX")

        channel_offset_hz = dial_frequency_hz - hardware_frequency_hz

        self.iq_source_block = iq_source
        self.iq_source_block.set_sample_rate(SAMPLE_RATE)
        self.iq_source_block.set_center_freq(hardware_frequency_hz, 0)
        self.iq_source_block.set_gain_mode(False, 0)
        self.iq_source_block.set_gain(rf_gain_db, 0)
        self.iq_source_block.set_freq_corr(frequency_correction_ppm, 0)
        self.iq_source_block.set_iq_balance_mode(2, 0)
        self.iq_source_block.set_dc_offset_mode(2, 0)

        channel_filter_taps = firdes.low_pass(1.0, SAMPLE_RATE, 6_000, 4_000)
        self.frequency_translator = filter.freq_xlating_fir_filter_ccc(
            CHANNEL_DECIMATION, channel_filter_taps, channel_offset_hz, SAMPLE_RATE
        )

        passband_low_hz, passband_high_hz = (
            (bandpass_low_hz, bandpass_high_hz) if sideband_mode == "usb" else (-bandpass_high_hz, -bandpass_low_hz)
        )
        sideband_filter_taps = firdes.complex_band_pass(
            1.0, INTERMEDIATE_FREQUENCY_RATE, passband_low_hz, passband_high_hz, 200
        )
        self.sideband_filter = filter.fir_filter_ccc(1, sideband_filter_taps)

        if agc_enabled:
            self.automatic_gain_control = analog.agc2_cc(5.0, 1e-3, 0.5, 1.0)  # attack, decay, ref, gain
            self.automatic_gain_control.set_max_gain(120)
            self.peak_limiter = analog.rail_ff(-0.9, 0.9)
        else:
            self.automatic_gain_control = blocks.multiply_const_cc(1.0)
            self.peak_limiter = blocks.multiply_const_ff(1.0)

        self.complex_to_real = blocks.complex_to_real(1)
        self.audio_resampler = filter.rational_resampler_fff(interpolation=24, decimation=25)
        self.volume_adjustment = blocks.multiply_const_ff(output_volume_percent / 100.0)

        pulse_device_name = (
            f"pulse:{audio_output_device}" if audio_output_device not in ("", "pulse", "default") else "pulse"
        )
        self.audio_sink = audio.sink(AUDIO_SAMPLE_RATE, pulse_device_name, True)
        self.peak_probe = PeakProbe()

        self.connect(
            self.iq_source_block,
            self.frequency_translator,
            self.sideband_filter,
            self.automatic_gain_control,
            self.complex_to_real,
            self.audio_resampler,
            self.peak_limiter,
            self.volume_adjustment,
            self.audio_sink,
        )
        self.connect(self.volume_adjustment, self.peak_probe)


def build_ssb_rx(
    iq_source,
    sideband_mode,
    hardware_frequency_hz,
    dial_frequency_hz,
    bandpass_low_hz,
    bandpass_high_hz,
    output_volume_percent,
    rf_gain_db,
    audio_output_device,
    frequency_correction_ppm,
    agc_enabled,
):
    return SsbRx(
        iq_source,
        sideband_mode,
        hardware_frequency_hz,
        dial_frequency_hz,
        bandpass_low_hz,
        bandpass_high_hz,
        output_volume_percent,
        rf_gain_db,
        audio_output_device,
        frequency_correction_ppm,
        agc_enabled,
    )
