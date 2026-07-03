# gnu-radio-ssb-demodulator

A headless GNU Radio SSB receiver. It reads raw I/Q directly from an RTL-SDR,
tunes a single channel out of the captured spectrum, demodulates USB or LSB, and
plays the audio to a PulseAudio sink. One process, no network, no GUI.

Designed to run one instance per channel and to feed either your speakers or a
named PulseAudio sink (e.g. for a downstream decoder).

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

## Options

| Flag | Default | Description |
|------|---------|-------------|
| `--hardware-frequency` | *required* | Frequency the SDR tunes to, in Hz. |
| `--dial-frequency` | *required* | Channel to receive, in Hz. May be above or below the hardware frequency. |
| `--mode` | `usb` | Sideband: `usb` or `lsb`. |
| `--bpf-low` | `200` | Audio passband lower edge, Hz. |
| `--bpf-high` | `3000` | Audio passband upper edge, Hz. |
| `--gain` | `30` | RF/tuner gain in dB. Fixed — set once; use AGC for level, not this. |
| `--volume` | `80` | AF output level, 0–100 % of full scale. |
| `--ppm` | `14` | Crystal frequency correction, in PPM. Tune per dongle. |
| `--agc` | off | Enable AGC (see below). Omit for a flat, fixed-gain feed. |
| `--bias-t` | off | Enable the ~4.5 V bias-T for an inline LNA / active antenna. |
| `--audio-output-device` | `pulse` | PulseAudio sink name; `pulse`/`default` = the default sink. |

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

By default audio goes to the system default sink (your speakers). To route to a
named PulseAudio sink instead — e.g. a null sink a decoder reads from:

```bash
pactl load-module module-null-sink sink_name=ft8 sink_properties=device.description=ft8
poetry run gnu-radio-ssb-demodulator ... --audio-output-device ft8
```

To listen to a named sink while it's also feeding something else, bridge its
monitor to your speakers:

```bash
pactl load-module module-loopback source=ft8.monitor sink=@DEFAULT_SINK@ latency_msec=1 adjust_time=0
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
- **`[R82XX] PLL not locked!`** at startup is a benign tuner message near the
  low edge of the R820T's range; ignore it if audio flows.
  