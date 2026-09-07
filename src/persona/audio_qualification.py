"""Strict validation for the Persona audio diagnostics."""

from __future__ import annotations

import math
from typing import Any, Mapping, Sequence


_DEFAULT_SAMPLE_RATE = 44100
_ALLOWED_SAMPLE_RATES = frozenset({44100, 48000})
_CHANNELS = 2
_FRAMES = 2048
_GAIN = 0.5
_INPUT = (0.25, -0.5)
_OUTPUT = (0.125, -0.25)
_EPSILON = 1e-5


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _finite_number(value: Any) -> bool:
    return type(value) in (int, float) and math.isfinite(value)


def _number(value: Any) -> float | None:
    if not _finite_number(value):
        return None
    return float(value)


def _sample_rate(config: Mapping[str, Any]) -> int | None:
    value = config.get("sample_rate", config.get("sampleRate", _DEFAULT_SAMPLE_RATE))
    if type(value) is not int or value not in _ALLOWED_SAMPLE_RATES:
        return None
    return value


def _near(value: Any, expected: float, tolerance: float = _EPSILON) -> bool:
    actual = _number(value)
    return actual is not None and abs(actual - expected) <= tolerance


def _hash(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 8
        and all(char in "0123456789abcdef" for char in value.lower())
    )


def _channel_hash(channel: Mapping[str, Any]) -> Any:
    return channel.get("sampleHash", channel.get("hash"))


def _first_samples(value: Any, expected: float) -> bool:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return False
    return len(value) == 8 and all(_near(sample, expected, 1e-4) for sample in value)


def _channel_valid(channel: Any, index: int) -> bool:
    channel = _mapping(channel)
    expected = _OUTPUT[index]
    expected_sum = expected * _FRAMES
    max_error = _number(channel.get("maxAbsError"))
    minimum = _number(channel.get("min"))
    maximum = _number(channel.get("max"))
    total = _number(channel.get("sum"))
    return (
        channel.get("length") == _FRAMES
        and minimum is not None and maximum is not None and total is not None
        and abs(minimum - expected) <= 1e-4
        and abs(maximum - expected) <= 1e-4
        and abs(total - expected_sum) <= 1e-2
        and _first_samples(channel.get("first8samples"), expected)
        and max_error is not None and 0 <= max_error <= 1e-4
        and _hash(_channel_hash(channel))
    )


def _render_valid(render: Any, sample_rate: int) -> bool:
    render = _mapping(render)
    context = _mapping(render.get("context"))
    buffer = _mapping(render.get("renderBuffer"))
    channels = render.get("channels")
    if not isinstance(channels, Sequence) or isinstance(channels, (str, bytes)):
        return False
    return (
        context.get("sampleRate") == sample_rate
        and context.get("length") == _FRAMES
        and context.get("numberOfChannels") == _CHANNELS
        and buffer.get("sampleRate") == sample_rate
        and buffer.get("length") == _FRAMES
        and buffer.get("numberOfChannels") == _CHANNELS
        and render.get("stateAfterRender") == "closed"
        and render.get("stateAfterClose") == "closed"
        and not render.get("cleanupError")
        and len(channels) == _CHANNELS
        and all(_channel_valid(channel, index) for index, channel in enumerate(channels))
    )


def _renders_same(first: Mapping[str, Any], second: Mapping[str, Any]) -> bool:
    first_channels, second_channels = first.get("channels"), second.get("channels")
    if not isinstance(first_channels, Sequence) or not isinstance(second_channels, Sequence):
        return False
    if len(first_channels) != _CHANNELS or len(second_channels) != _CHANNELS:
        return False
    for left, right in zip(first_channels, second_channels):
        left, right = _mapping(left), _mapping(right)
        if _channel_hash(left) != _channel_hash(right):
            return False
        for key in ("length", "min", "max", "sum", "maxAbsError", "first8samples"):
            if left.get(key) != right.get(key):
                return False
    return True


def validate_offline(config: Mapping[str, Any] | None, observed: Mapping[str, Any] | None) -> bool:
    """Validate actual OfflineAudioContext evidence, independent of flags."""
    config = _mapping(config)
    observed = _mapping(observed)
    sample_rate = _sample_rate(config)
    if sample_rate is None:
        return False
    if observed.get("scope", "rendernative") != "rendernative":
        return False
    if (observed.get("physicalInput", "not_verified") != "not_verified"
            or observed.get("physicalOutput", "not_verified") != "not_verified"):
        return False
    # Accept a diagnostic wrapper while always validating the two actual runs.
    actual = _mapping(observed.get("actual", observed))
    first, second = actual.get("first"), actual.get("second")
    if not _render_valid(first, sample_rate) or not _render_valid(second, sample_rate):
        return False
    return _renders_same(_mapping(first), _mapping(second))


def _waveform_valid(analyser: Mapping[str, Any], sample_rate: int) -> bool:
    wave = analyser.get("first64")
    byte_wave = analyser.get("byteFirst64")
    if (not isinstance(wave, Sequence) or isinstance(wave, (str, bytes))
            or not isinstance(byte_wave, Sequence) or isinstance(byte_wave, (str, bytes))
            or len(wave) < 64 or len(byte_wave) < 64):
        return False
    # Require the browser's numeric JSON values before any coercion.  Besides
    # keeping malformed evidence a clean validation failure, this prevents
    # numeric strings from bypassing the native Float32 sample contract.
    if not all(_finite_number(value) for value in wave[:64]):
        return False
    samples = [float(value) for value in wave[:64]]
    if not all(type(value) is int and 0 <= value <= 255 for value in byte_wave[:64]):
        return False
    # 44100/48000 and the configured oscillator are both one cycle per 32
    # samples.  Derive all these checks from the samples themselves rather
    # than trusting claimed min/max/RMS/peak fields.
    if any(abs(value) > 0.251 for value in samples):
        return False
    if abs(sum(samples) / len(samples)) >= 0.002:
        return False
    local_rms = math.sqrt(sum(value * value for value in samples) / len(samples))
    if abs(local_rms - (0.25 / math.sqrt(2))) >= 0.002:
        return False
    if abs(min(samples) + 0.25) >= 0.003 or abs(max(samples) - 0.25) >= 0.003:
        return False
    if any(abs(samples[index] - samples[index + 32]) > 0.003 for index in range(32)):
        return False
    cosine = math.cos(2 * math.pi / 32)
    if any(abs(samples[index + 2] - 2 * cosine * samples[index + 1] + samples[index]) >= 0.003
           for index in range(62)):
        return False
    for value, byte in zip(wave[:64], byte_wave[:64]):
        quantized = max(0, min(255, math.floor(128 * (1 + float(value)))))
        if abs(int(byte) - quantized) > 1:
            return False
    return True


def validate_realtime(config: Mapping[str, Any] | None, observed: Mapping[str, Any] | None) -> bool:
    """Validate trusted realtime AudioContext/analyser evidence."""
    config = _mapping(config)
    observed = _mapping(observed)
    sample_rate = _sample_rate(config)
    if sample_rate is None:
        return False
    analyser = _mapping(observed.get("analyser"))
    base_latency = _number(observed.get("baseLatency"))
    output_latency = observed.get("outputLatency")
    physical_input = observed.get("physicalInput", observed.get("inputDevices", "not_verified"))
    physical_output = observed.get("physicalOutput", "not_verified")
    output_latency_ok = output_latency is None or (
        _finite_number(output_latency) and float(output_latency) >= 0
    )
    minimum = _number(analyser.get("min"))
    maximum = _number(analyser.get("max"))
    rms = _number(analyser.get("rms"))
    peak_db = _number(analyser.get("peakDb"))
    return (
        observed.get("done") is True
        and observed.get("scope", "rendernative") == "rendernative"
        and physical_input == "not_verified"
        and physical_output == "not_verified"
        and observed.get("trusted") is True
        and observed.get("sampleRate") == sample_rate
        and observed.get("initialState") in {"suspended", "running"}
        and observed.get("runningState") == "running"
        and observed.get("suspendedState") == "suspended"
        and observed.get("resumedState") == "running"
        and observed.get("closedState") == "closed"
        and _finite_number(observed.get("maxChannelCount"))
        and observed.get("maxChannelCount") >= _CHANNELS
        and observed.get("outputGain") == 0
        and _number(observed.get("timeAfter")) is not None
        and _number(observed.get("timeBefore")) is not None
        and float(observed["timeAfter"]) > float(observed["timeBefore"])
        and _number(observed.get("suspendedTimeBefore")) is not None
        and _number(observed.get("suspendedTimeAfter")) is not None
        and observed.get("suspendedTimeBefore") == observed.get("suspendedTimeAfter")
        and analyser.get("fftSize") == 2048
        and analyser.get("frequencyBinCount") == 1024
        and analyser.get("length") == 2048
        and _near(analyser.get("frequency"), sample_rate / 32, 1e-6)
        and minimum is not None and maximum is not None
        and abs(minimum + 0.25) <= 0.03 and abs(maximum - 0.25) <= 0.03
        and rms is not None and abs(rms - 0.1767767) <= 0.03
        and analyser.get("peakBin") == 64
        and peak_db is not None and -40 <= peak_db <= -10
        and _waveform_valid(analyser, sample_rate)
        and base_latency is not None and base_latency >= 0
        and output_latency_ok
        and not observed.get("cleanupError")
    )


__all__ = ["validate_offline", "validate_realtime"]
