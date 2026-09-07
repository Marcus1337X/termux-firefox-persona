"""Pure validation tests for the browser-local audio diagnostics."""

from __future__ import annotations

import copy
import math
import unittest

from src.persona.audio_qualification import validate_offline, validate_realtime


class PersonaAudioQualificationTests(unittest.TestCase):
    @staticmethod
    def _offline() -> dict:
        channels = []
        for expected, total, digest in (
            (0.125, 256.0, "deadbeef"), (-0.25, -512.0, "cafebabe")
        ):
            channels.append({
                "length": 2048, "min": expected, "max": expected, "sum": total,
                "first8samples": [expected] * 8, "maxAbsError": 0.0,
                "expected": expected, "sampleHash": digest,
            })
        render = {
            "context": {"sampleRate": 44100, "length": 2048,
                        "numberOfChannels": 2},
            "renderBuffer": {"sampleRate": 44100, "length": 2048,
                             "numberOfChannels": 2},
            "stateAfterRender": "closed", "stateAfterClose": "closed", "cleanupError": None,
            "channels": channels,
        }
        return {"status": "error", "checks": {"output": False},
                "actual": {"first": copy.deepcopy(render),
                           "second": copy.deepcopy(render)}}

    @staticmethod
    def _realtime() -> dict:
        wave = [0.25 * math.sin(2 * math.pi * index / 32) for index in range(64)]
        byte_wave = [max(0, min(255, math.floor(128 * (1 + value)))) for value in wave]
        return {
            "done": True, "trusted": True, "sampleRate": 44100,
            "initialState": "suspended", "runningState": "running",
            "suspendedState": "suspended", "resumedState": "running",
            "closedState": "closed", "maxChannelCount": 2, "outputGain": 0,
            "timeBefore": 1.0, "timeAfter": 1.2,
            "suspendedTimeBefore": 1.2, "suspendedTimeAfter": 1.2,
            "baseLatency": 0.01, "outputLatency": None, "cleanupError": None,
            "analyser": {
                "fftSize": 2048, "frequencyBinCount": 1024,
                "frequency": 44100 / 32, "length": 2048,
                "min": -0.25, "max": 0.25, "rms": 0.1767767,
                "first64": wave, "byteFirst64": byte_wave,
                "peakBin": 64, "peakDb": -20,
            },
        }

    def test_offline_checks_actual_two_renderings(self):
        observed = self._offline()
        self.assertTrue(validate_offline({"sample_rate": 44100}, observed))
        observed["actual"]["first"]["channels"][0]["maxAbsError"] = 0.5
        self.assertFalse(validate_offline({"sample_rate": 44100}, observed))

    def test_offline_rejects_cleanup_and_structure_failures(self):
        observed = self._offline()
        observed["actual"]["second"]["stateAfterClose"] = "running"
        self.assertFalse(validate_offline({"sample_rate": 44100}, observed))
        observed = self._offline()
        observed["actual"]["first"]["cleanupError"] = {"stage": "disconnect"}
        self.assertFalse(validate_offline({"sample_rate": 44100}, observed))

    def test_offline_does_not_trust_status_or_check_flags(self):
        observed = self._offline()
        observed["status"] = "unsupported"
        observed["checks"] = {"supported": False, "output": False}
        self.assertTrue(validate_offline({"sample_rate": 44100}, observed))

    def test_realtime_checks_waveform_state_and_latency(self):
        observed = self._realtime()
        self.assertTrue(validate_realtime({"sample_rate": 44100}, observed))
        broken = copy.deepcopy(observed)
        broken["outputGain"] = 0.25
        self.assertFalse(validate_realtime({"sample_rate": 44100}, broken))
        broken = copy.deepcopy(observed)
        broken["analyser"]["byteFirst64"][10] += 20
        self.assertFalse(validate_realtime({"sample_rate": 44100}, broken))

    def test_realtime_rejects_constant_waveform_with_forged_summary(self):
        observed = self._realtime()
        observed["analyser"]["first64"] = [0.25] * 64
        observed["analyser"]["byteFirst64"] = [160] * 64
        self.assertFalse(validate_realtime({"sample_rate": 44100}, observed))

    def test_realtime_rejects_malformed_waveform_samples(self):
        observed = self._realtime()
        observed["analyser"]["first64"][10] = "0.25"
        self.assertFalse(validate_realtime({"sample_rate": 44100}, observed))
        observed = self._realtime()
        observed["analyser"]["first64"][10] = None
        self.assertFalse(validate_realtime({"sample_rate": 44100}, observed))

    def test_realtime_requires_cleanup_and_base_latency_but_allows_missing_output_latency(self):
        observed = self._realtime()
        observed["outputLatency"] = None
        self.assertTrue(validate_realtime({"sample_rate": 44100}, observed))
        observed["baseLatency"] = None
        self.assertFalse(validate_realtime({"sample_rate": 44100}, observed))
        observed = self._realtime()
        observed["cleanupError"] = "disconnect failed"
        self.assertFalse(validate_realtime({"sample_rate": 44100}, observed))


if __name__ == "__main__":
    unittest.main()
