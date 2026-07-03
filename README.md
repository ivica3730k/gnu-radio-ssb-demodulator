# gnu-radio-ssb-demodulator

A headless GNU Radio SSB receiver. It reads raw I/Q directly from an RTL-SDR,
tunes a single channel out of the captured spectrum, demodulates USB or LSB, and
plays the audio to a PulseAudio sink. One process, no network, no GUI.

Designed to run one instance per channel and to feed either your speakers or a
named PulseAudio sink (e.g. for a downstream decoder).

You can also run it in JSON config mode to open multiple receiver chains from
one hardware capture.

## Signal chain

```
RTL-SDR ──▶ frequency ──▶ sideband ──▶ [AGC] ──▶ real ──▶ resample ──▶ [limiter] ──▶ volume ──▶ PulseAudio
 (I/Q)      translate     band-pass    (IF)     audio    50k→48k       (peak clip)   (AF trim)     sink
```

The hardware tunes to `--hardware-frequency` and captures 1.8 MHz of spectrum
around it. `--dial-frequency` selects the channel to receive from within that
window; the receiver shifts it to baseband, filters one sideband, and produces
audio. The dial may sit above or below the hardware frequency.

## Requirements

GNU Radio and gr-osmosdr are **system packages** — they are not installable via
pip/Poetry (they are compiled C++ with Python bindings). Install them from your
distribution first:

```bash
sudo apt install gnuradio gr-osmosdr   # Debian/Ubuntu
```

An RTL-SDR dongle with the rtl-sdr-blog driver (needed for bias-T), and a running
PulseAudio (or PipeWire with the Pulse shim) for audio output.

## Setup

```bash
poetry install
poetry run pre-commit install
```

If you use a virtualenv, create it with `--system-site-packages` so it can see
the system GNU Radio install:

```bash
python3 -m venv --system-site-packages .venv
```

## Run

```bash
poetry run gnu-radio-ssb-demodulator \
  cli \
  --rtl-index 0 \
  --hardware-frequency 7000000 \
  --dial-frequency 7074000 \
  --mode usb \
  --bpf-low 200 --bpf-high 3000 \
  --gain 10 \
  --volume 80 \
  --ppm 14 \
  --agc \
  --bias-t
```

Or run in JSON config mode (**exclusive**: no other receiver flags allowed):

```bash
poetry run gnu-radio-ssb-demodulator json --json-config-file ./config.json
```

## Options

| Command/Flag | Default | Description |
|------|---------|-------------|
| `cli` | required mode | Single receiver configured by CLI flags. |
| `json --json-config-file` | required in `json` mode | Load full config from JSON and open all configured receiver chains. |
| `--hardware-frequency` | *required* | Frequency the SDR tunes to, in Hz. |
| `--dial-frequency` | *required* | Channel to receive, in Hz. May be above or below the hardware frequency. |
| `--mode` | `usb` | Sideband: `usb` or `lsb`. |
| `--bpf-low` | `200` | Audio passband lower edge, Hz. |
| `--bpf-high` | `3000` | Audio passband upper edge, Hz. |
| `--gain` | `30` | RF/tuner gain in dB. Fixed — set once; use AGC for level, not this. |
| `--rtl-index` | `0` | RTL-SDR device index for CLI mode. |
| `--volume` | `80` | AF output level, 0–100 % of full scale. |
| `--ppm` | `14` | Crystal frequency correction, in PPM. Tune per dongle. |
| `--agc` | off | Enable AGC (see below). Omit for a flat, fixed-gain feed. |
| `--bias-t` | off | Enable the ~4.5 V bias-T for an inline LNA / active antenna. |
| `--audio-output-device` | `pulse` | PulseAudio sink name; `pulse`/`default` = the default sink. |

### JSON config format

`json --json-config-file` expects a core hardware block plus a `receivers` list.
Sample rate is fixed internally at **1,800,000** and is not configurable.

### 20m example: FT8 + FT4 + WSPR at the same time

Hardware is tuned to **13,900 kHz** (`13900000` Hz) and three receiver chains are opened:
- FT8: 14.074 MHz
- FT4: 14.080 MHz
- WSPR: 14.0956 MHz

```json
{
  "rtl_index": 0,
  "hardware_frequency": 13900000,
  "transverter_offset": 50000000,
  "gain": 10,
  "ppm": 14,
  "bias_t": true,
  "receivers": [
    {
      "mode": "USB",
      "dial_frequency": 14074000,
      "bpf_low": 200,
      "bpf_high": 3800,
      "agc": true,
      "pulse_audio_output_device": "ft8_20m",
      "volume": 80
    },
    {
      "mode": "USB",
      "dial_frequency": 14080000,
      "bpf_low": 200,
      "bpf_high": 3000,
      "agc": true,
      "pulse_audio_output_device": "ft4_20m",
      "volume": 80
    },
    {
      "mode": "USB",
      "dial_frequency": 14095600,
      "bpf_low": 1200,
      "bpf_high": 1800,
      "agc": true,
      "pulse_audio_output_device": "wspr_20m",
      "volume": 80
    }
  ]
}
```

Run it with:

```bash
poetry run gnu-radio-ssb-demodulator json --json-config-file ./config-20m.json
```

## AGC vs. gain vs. volume

These are three independent stages, and it helps to keep them straight:

- **`--gain`** is the *RF* gain at the tuner. Leave it fixed (like gqrx does). It
  sets how hard the ADC is driven; it is not your volume control.
- **`--agc`** does dynamic-range compression in the IF, after the ADC — the same
  place gqrx handles a wide S-meter range. With AGC on, weak and very strong
  signals both level to a usable output without touching RF gain. A feedforward
  limiter after it hard-clips any transient the AGC feedback loop is too slow to
  catch, so brief overshoots can't blast the output.
- **`--volume`** is the final *AF* trim, 0–100 %. It only attenuates.

For listening across a wide range of signal strengths, enable `--agc`. For a
clean, linear feed to a decoder, leave it off and rely on fixed gain.

## Audio routing

Create 3 virtual sinks (one per mode):

```bash
pactl load-module module-null-sink sink_name=ft8_20m sink_properties=device.description=ft8_20m
pactl load-module module-null-sink sink_name=ft4_20m sink_properties=device.description=ft4_20m
pactl load-module module-null-sink sink_name=wspr_20m sink_properties=device.description=wspr_20m
```

Optional: listen to each virtual sink while decoders read from it (loopback to default sink):

```bash
pactl load-module module-loopback source=ft8_20m.monitor sink=@default_sink@ latency_msec=1 adjust_time=0
pactl load-module module-loopback source=ft4_20m.monitor sink=@default_sink@ latency_msec=1 adjust_time=0
pactl load-module module-loopback source=wspr_20m.monitor sink=@default_sink@ latency_msec=1 adjust_time=0
```

## Monitoring

The receiver logs the peak audio amplitude once per second at INFO, escalating to
WARNING at ≥ 0.8 (within ~2 dB of the ±1.0 clip ceiling). Watch it to calibrate:
aim for peaks around 0.5, and treat sustained WARNINGs as a sign to back off the
level.

## Notes

- **Frequency example:** for 40 m FT8 (7.074 MHz) with no upconverter, tune the
  hardware near the band (e.g. `--hardware-frequency 7000000`) and set
  `--dial-frequency 7074000`. With a 50 MHz upconverter, add 50 MHz to both.
- **Capture width:** the dial must fall within ±900 kHz of the hardware frequency
  (the 1.8 MHz capture). Channels further apart need a separate hardware tune.
