"""Reject codec support claims without matching decoded and played evidence."""

from copy import deepcopy
import math
import unittest
from unittest.mock import patch

from src.persona.media_qualification import validate_media


CODECS = ["h264", "vp8", "vp9", "av1", "aac", "opus"]
MANIFEST = {
    "id": "native-codecs-v1",
    "fixtures": [
        {"id": codec, "kind": "video" if index < 4 else "audio",
         "sha256": str(index) * 64, "content_type": f'{"video" if index < 4 else "audio"}/test; codecs="{codec}"',
         "bitrate": 32000, "framerate": 8}
        for index, codec in enumerate(CODECS)
    ],
}
CONFIG = {"fixture_set": "native-codecs-v1", "codecs": CODECS,
          "scope": "native-decode-playback", "physical_input": "not_verified",
          "physical_output": "not_verified", "webrtc": "not_verified"}


def signal(sample_rate, length, frequency=1000, phase=0.4):
    samples = [0.125 * math.sin(2 * math.pi * frequency * i / sample_rate + phase)
               for i in range(length)]
    return {"length": length, "samples": samples[:256], "min": min(samples), "max": max(samples),
            "mean": sum(samples) / length, "rms": math.sqrt(sum(x*x for x in samples) / length)}


def observations(sample_rate=48000):
    result = {"fixtureSet": "native-codecs-v1", "done": True, "trusted": True,
              "sampleRate": sample_rate, "runningState": "running", "closedState": "closed",
              "physicalInput": "not_verified", "physicalOutput": "not_verified", "webRTC": "not_verified",
              "codecs": {}}
    for fixture in MANIFEST["fixtures"]:
        codec = {"sha256": fixture["sha256"], "contentType": fixture["content_type"],
                 "kind": fixture["kind"], "canPlayType": "probably",
                 "decodingInfo": {"supported": True, "smooth": False, "powerEfficient": False},
                 "duration": 2, "readyState": 4, "timeBefore": 0, "timeAfter": 0.3,
                 "ended": True, "endTime": 2,
                 "events": [{"event": "loadedmetadata", "time": 0},
                            {"event": "playing", "time": 0.1}],
                 "decodingConfig": {"type": "file"}}
        if fixture["kind"] == "video":
            codec["decodingConfig"]["video"] = {
                "contentType": fixture["content_type"], "width": 64, "height": 64,
                "bitrate": fixture["bitrate"], "framerate": fixture["framerate"]}
            codec.update(width=64, height=64, totalVideoFrames=16, droppedVideoFrames=0)
            for key, rgba in (("red", [255, 0, 0, 255]), ("green", [0, 255, 0, 255])):
                codec[key] = {"width": 64, "height": 64,
                              **{metric: list(rgba) for metric in ("min", "max", "mean", "center")}}
        else:
            codec["decodingConfig"]["audio"] = {"contentType": fixture["content_type"],
                "channels": "1", "bitrate": fixture["bitrate"], "samplerate": 48000}
            codec["outputGain"] = 0
            codec["decoded"] = {"sampleRate": sample_rate, "channels": 1, "length": sample_rate * 2,
                                "duration": 2, "signal": signal(sample_rate, 4096)}
            codec["playback"] = {"sampleRate": sample_rate, "fftSize": 2048,
                                 "signal": signal(sample_rate, 2048, phase=1.2),
                                 "peakBin": round(1000 * 2048 / sample_rate), "peakDb": -25}
        result["codecs"][fixture["id"]] = codec
    return result


class MediaQualificationTests(unittest.TestCase):
    def setUp(self):
        self.manifest_patch = patch("src.persona.media_qualification.media_manifest", return_value=MANIFEST)
        self.manifest_patch.start()
        self.addCleanup(self.manifest_patch.stop)
        self.valid = observations()

    def assert_codec_fails(self, observed, codec):
        passed, failures = validate_media(CONFIG, observed, 48000)
        self.assertFalse(passed)
        self.assertIn(codec, failures)

    def test_real_signal_and_rgba_evidence_passes_at_both_native_rates(self):
        for rate in (44100, 48000):
            with self.subTest(rate=rate):
                self.assertEqual(validate_media(CONFIG, observations(rate), rate), (True, []))

    def test_supported_query_cannot_hide_wrong_video_pixels(self):
        for codec in CODECS[:4]:
            for color in ("red", "green"):
                with self.subTest(codec=codec, color=color):
                    observed = deepcopy(self.valid)
                    observed["codecs"][codec][color]["center"] = [0, 0, 0, 255]
                    self.assert_codec_fails(observed, codec)

    def test_supported_query_and_forged_summaries_cannot_hide_silent_or_wrong_frequency_audio(self):
        for codec in CODECS[4:]:
            for stage in ("decoded", "playback"):
                for frequency in (0, 500, 2000):
                    with self.subTest(codec=codec, stage=stage, frequency=frequency):
                        observed = deepcopy(self.valid)
                        # Keep plausible summary statistics and frequency-bin claims.
                        observed["codecs"][codec][stage]["signal"]["samples"] = (
                            [0.0] * 256 if frequency == 0 else signal(48000, 256, frequency)["samples"])
                        self.assert_codec_fails(observed, codec)

    def test_missing_codec_never_qualifies_complete_suite(self):
        for codec in CODECS:
            with self.subTest(codec=codec):
                observed = deepcopy(self.valid)
                del observed["codecs"][codec]
                self.assert_codec_fails(observed, codec)

    def test_cleanup_failures_reject_global_and_codec_evidence(self):
        for field in ("cleanupError", "error"):
            observed = deepcopy(self.valid)
            observed[field] = "cleanup failed"
            self.assertFalse(validate_media(CONFIG, observed, 48000)[0])
            for codec in CODECS:
                with self.subTest(field=field, codec=codec):
                    observed = deepcopy(self.valid)
                    observed["codecs"][codec][field] = "context remained active"
                    self.assert_codec_fails(observed, codec)
        observed = deepcopy(self.valid)
        observed["closedState"] = "running"
        self.assertFalse(validate_media(CONFIG, observed, 48000)[0])

    def test_unfinished_playback_or_stationary_clock_fails(self):
        for codec in CODECS:
            for key, value in (("ended", False), ("endTime", 0.5), ("timeAfter", 0),
                               ("timeAfter", -1), ("timeBefore", float("nan"))):
                with self.subTest(codec=codec, key=key, value=value):
                    observed = deepcopy(self.valid)
                    observed["codecs"][codec][key] = value
                    self.assert_codec_fails(observed, codec)

    def test_malformed_audio_samples_and_video_measurements_fail_without_crashing(self):
        for samples in (None, "not samples", [], [0] * 255, [True] * 256,
                        [float("nan")] * 256, [float("inf")] * 256, [None] * 256):
            for stage in ("decoded", "playback"):
                with self.subTest(samples=repr(samples)[:30], stage=stage):
                    observed = deepcopy(self.valid)
                    observed["codecs"]["aac"][stage]["signal"]["samples"] = samples
                    self.assert_codec_fails(observed, "aac")
        for rgba in (None, [], [255, 0, 0], [True, 0, 0, 255], [float("nan"), 0, 0, 255]):
            observed = deepcopy(self.valid)
            observed["codecs"]["h264"]["red"]["mean"] = rgba
            self.assert_codec_fails(observed, "h264")

    def test_media_event_trace_rejects_errors_and_malformed_records(self):
        invalid_events = (
            None,
            "playing",
            [],
            [{}],
            [{"event": "playing"}],
            [{"event": "playing", "time": -1}],
            [{"event": "playing", "time": float("nan")}],
            [{"event": "playing", "time": True}],
            [{"event": 7, "time": 0}],
            [{"event": "error", "time": 0}],
        )
        for events in invalid_events:
            with self.subTest(events=repr(events)):
                observed = deepcopy(self.valid)
                observed["codecs"]["aac"]["events"] = events
                self.assert_codec_fails(observed, "aac")

    def test_waiting_and_stalled_events_are_diagnostic_only(self):
        observed = deepcopy(self.valid)
        observed["codecs"]["opus"]["events"].extend([
            {"event": "waiting", "time": 0.4},
            {"event": "stalled", "time": 0.5},
        ])
        self.assertEqual(validate_media(CONFIG, observed, 48000), (True, []))

    def test_false_support_untrusted_input_and_fixture_mismatch_fail(self):
        for key, value in (("trusted", False), ("done", False), ("fixtureSet", "other"),
                           ("physicalOutput", "verified")):
            observed = deepcopy(self.valid)
            observed[key] = value
            self.assertFalse(validate_media(CONFIG, observed, 48000)[0])
        for codec in CODECS:
            for field, value in (("supported", False), ("smooth", "true"), ("powerEfficient", None)):
                observed = deepcopy(self.valid)
                observed["codecs"][codec]["decodingInfo"][field] = value
                self.assert_codec_fails(observed, codec)
            observed = deepcopy(self.valid)
            observed["codecs"][codec]["sha256"] = "wrong fixture"
            self.assert_codec_fails(observed, codec)
        self.assertEqual(validate_media(CONFIG, self.valid, True), (False, CODECS))


if __name__ == "__main__":
    unittest.main()
