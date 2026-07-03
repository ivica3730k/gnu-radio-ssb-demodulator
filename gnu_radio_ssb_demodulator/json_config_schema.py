from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field, model_validator
from typing_extensions import Self


class ModeEnum(str, Enum):
    """Enumeration for the mode of operation."""

    USB = "USB"
    LSB = "LSB"


class ReceiverConfigSchema(BaseModel):
    mode: ModeEnum
    dial_frequency: int
    bpf_low: int
    bpf_high: int
    agc: bool
    pulse_audio_output_device: str
    volume: int = Field(..., ge=0, le=100)

    def validate_dial_frequency_in_sample_rate_range(self, sample_rate: int, hardware_frequency: int) -> None:
        """Validate that the dial frequency is within the sample rate range."""
        half_capture_hz = sample_rate // 2
        minimum_dial_hz = hardware_frequency - half_capture_hz
        maximum_dial_hz = hardware_frequency + half_capture_hz
        if not (minimum_dial_hz <= self.dial_frequency <= maximum_dial_hz):
            raise ValueError(
                f"Dial frequency {self.dial_frequency} is out of range [{minimum_dial_hz}, {maximum_dial_hz}] "
                f"for hardware frequency {hardware_frequency} and sample rate {sample_rate}."
            )


class CoreConfigSchema(BaseModel):
    rtl_index: int = Field(default=0, ge=0)
    hardware_frequency: int
    transverter_offset: int = 0
    sample_rate: Literal[1_800_000] = 1_800_000
    gain: int
    ppm: int
    bias_t: bool
    receivers: list[ReceiverConfigSchema]

    @model_validator(mode="before")
    @classmethod
    def apply_transverter_offset_to_frequencies(cls, data):
        if not isinstance(data, dict):
            return data

        transverter_offset = data.get("transverter_offset", 0)
        if not isinstance(transverter_offset, int) or transverter_offset == 0:
            return data

        normalized_data = dict(data)
        hardware_frequency = normalized_data.get("hardware_frequency")
        if isinstance(hardware_frequency, int):
            normalized_data["hardware_frequency"] = hardware_frequency + transverter_offset

        receivers = normalized_data.get("receivers", [])
        if isinstance(receivers, list):
            normalized_receivers = []
            for receiver in receivers:
                if not isinstance(receiver, dict):
                    normalized_receivers.append(receiver)
                    continue
                normalized_receiver = dict(receiver)
                # Transverter offset shifts tuned frequencies, not audio band-pass edges.
                for frequency_field in ("dial_frequency",):
                    field_value = normalized_receiver.get(frequency_field)
                    if isinstance(field_value, int):
                        normalized_receiver[frequency_field] = field_value + transverter_offset
                normalized_receivers.append(normalized_receiver)
            normalized_data["receivers"] = normalized_receivers

        return normalized_data

    @model_validator(mode="after")
    def validate_all_receivers(self) -> Self:
        for receiver in self.receivers:
            receiver.validate_dial_frequency_in_sample_rate_range(
                sample_rate=self.sample_rate,
                hardware_frequency=self.hardware_frequency,
            )
        return self
