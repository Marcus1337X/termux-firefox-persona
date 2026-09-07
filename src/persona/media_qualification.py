"""Check measured codec output, independently of browser support claims."""

from __future__ import annotations

import math
from collections.abc import Mapping

from .media import media_manifest


def _map(value):
    return value if isinstance(value, Mapping) else {}


def _number(value):
    return type(value) in (int, float) and math.isfinite(value)


def _near(value, expected, tolerance):
    return _number(value) and abs(value - expected) <= tolerance


def _events_valid(value):
    """Validate the media event trace without rejecting recoverable stalls."""
    if not isinstance(value, list) or not value:
        return False
    for event in value:
        if not isinstance(event, Mapping):
            return False
        name = event.get("event")
        timestamp = event.get("time")
        if (not isinstance(name, str) or not name
                or not _number(timestamp) or timestamp < 0
                or name == "error"):
            return False
    return True


def _color(value, expected):
    value = _map(value)
    if value.get("width") != 64 or value.get("height") != 64:
        return False
    for key in ("min", "max", "mean", "center"):
        samples = value.get(key)
        if not isinstance(samples, list) or len(samples) != 4:
            return False
        if not all(_number(x) and 0 <= x <= 255 and abs(x - target) <= 15
                   for x, target in zip(samples, expected)):
            return False
    return True


def _signal(value, sample_rate, length):
    value = _map(value)
    samples = value.get("samples")
    if (value.get("length") != length or not isinstance(samples, list) or len(samples) != 256
            or not all(_number(x) and abs(x) < 0.18 for x in samples)):
        return False
    if (not _near(value.get("rms"), 0.125 / math.sqrt(2), 0.015)
            or not _near(value.get("min"), -0.125, 0.025)
            or not _near(value.get("max"), 0.125, 0.025)
            or not _near(value.get("mean"), 0, 0.008)):
        return False
    # Fit the known 1 kHz signal with an arbitrary phase.  Lossy decoders and
    # resampling need a numerical tolerance, but constant or wrong-frequency
    # samples cannot pass using forged summary statistics.
    sine = [math.sin(2 * math.pi * 1000 * i / sample_rate) for i in range(len(samples))]
    cosine = [math.cos(2 * math.pi * 1000 * i / sample_rate) for i in range(len(samples))]
    ss = sum(x*x for x in sine)
    cc = sum(x*x for x in cosine)
    sc = sum(x*y for x,y in zip(sine, cosine))
    ys = sum(x*y for x,y in zip(samples, sine))
    yc = sum(x*y for x,y in zip(samples, cosine))
    determinant = ss*cc-sc*sc
    a, b = (ys*cc-yc*sc)/determinant, (yc*ss-ys*sc)/determinant
    amplitude = math.hypot(a, b)
    error = math.sqrt(sum((x-a*s-b*c)**2 for x,s,c in zip(samples,sine,cosine))/len(samples))
    return 0.10 <= amplitude <= 0.15 and error <= 0.01


def _codec(fixture, observed, sample_rate):
    observed = _map(observed)
    decoding = _map(observed.get("decodingInfo"))
    if (observed.get("error") or observed.get("cleanupError")
            or not _events_valid(observed.get("events"))
            or observed.get("sha256") != fixture["sha256"]
            or observed.get("contentType") != fixture["content_type"]
            or observed.get("kind") != fixture["kind"]
            or observed.get("canPlayType") not in ("maybe", "probably")
            or decoding.get("supported") is not True
            or type(decoding.get("smooth")) is not bool
            or type(decoding.get("powerEfficient")) is not bool
            or not _near(observed.get("duration"), 2, 0.1)
            or observed.get("readyState") not in (2, 3, 4)
            or not _number(observed.get("timeBefore")) or not _number(observed.get("timeAfter"))
            or observed["timeBefore"] < 0 or observed["timeAfter"] - observed["timeBefore"] < 0.1
            or observed.get("ended") is not True or not _near(observed.get("endTime"), 2, 0.1)):
        return False
    config = {"type": "file"}
    if fixture["kind"] == "video":
        config["video"] = {"contentType": fixture["content_type"], "width": 64, "height": 64,
                           "bitrate": fixture["bitrate"], "framerate": fixture["framerate"]}
        return (observed.get("decodingConfig") == config
                and observed.get("width") == 64 and observed.get("height") == 64
                and _color(observed.get("red"), [255, 0, 0, 255])
                and _color(observed.get("green"), [0, 255, 0, 255])
                and type(observed.get("totalVideoFrames")) is int and observed["totalVideoFrames"] >= 2
                and type(observed.get("droppedVideoFrames")) is int
                and 0 <= observed["droppedVideoFrames"] <= observed["totalVideoFrames"])
    config["audio"] = {"contentType": fixture["content_type"], "channels": "1",
                       "bitrate": fixture["bitrate"], "samplerate": 48000}
    decoded, playback = _map(observed.get("decoded")), _map(observed.get("playback"))
    return (observed.get("decodingConfig") == config
            and observed.get("outputGain") == 0
            and decoded.get("sampleRate") == sample_rate and decoded.get("channels") == 1
            and _near(decoded.get("length"), sample_rate * 2, sample_rate * 0.1)
            and _near(decoded.get("duration"), 2, 0.1)
            and _signal(decoded.get("signal"), sample_rate, 4096)
            and playback.get("sampleRate") == sample_rate and playback.get("fftSize") == 2048
            and _signal(playback.get("signal"), sample_rate, 2048)
            and _near(playback.get("peakBin"), round(1000 * 2048 / sample_rate), 1)
            and _number(playback.get("peakDb")) and -45 < playback["peakDb"] < -15)


def validate_media(config, observed, sample_rate):
    """Return a pass flag and failed fixture IDs for the finite media suite."""
    manifest = media_manifest()
    config, observed = _map(config), _map(observed)
    fixtures = manifest["fixtures"]
    ids = [fixture["id"] for fixture in fixtures]
    if type(sample_rate) is not int or sample_rate not in (44100, 48000):
        return False, ids
    observed_codecs = _map(observed.get("codecs"))
    failures = [fixture["id"] for fixture in fixtures
                if not _codec(fixture, observed_codecs.get(fixture["id"]), sample_rate)]
    common = (config.get("fixture_set") == manifest["id"] and config.get("codecs") == ids
              and observed.get("fixtureSet") == manifest["id"]
              and observed.get("done") is True and observed.get("trusted") is True
              and observed.get("sampleRate") == sample_rate and sample_rate in (44100, 48000)
              and observed.get("runningState") == "running" and observed.get("closedState") == "closed"
              and observed.get("physicalInput") == "not_verified"
              and observed.get("physicalOutput") == "not_verified" and observed.get("webRTC") == "not_verified"
              and not observed.get("error") and not observed.get("cleanupError")
              and set(observed_codecs) == set(ids))
    return bool(common and not failures), failures
