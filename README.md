# gnu-radio-ssb-demodulator

GNU Radio SSB receiver that reads IQ directly from an RTL-SDR device and outputs audio through PulseAudio.

## Setup

```bash
poetry install
poetry run pre-commit install
```

## Run

```bash
python receiver.py \
  --mode usb \
  --hardware-frequency 7100000 \
  --dial-frequency 7102000 \
  --gain 30 \
  --volume 80
```

