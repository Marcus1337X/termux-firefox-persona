"""Browser-local OfflineAudioContext qualification expression.

The expression in this module is deliberately self-contained.  It does not
replace any browser API or use realtime audio; a caller evaluates it in the
target page context and receives structured evidence about two independent
offline renders.
"""

from __future__ import annotations

import json
from typing import Any, Mapping


_AUDIO_CONFIG = {
    "sampleRate": 44100,
    "numberOfChannels": 2,
    "length": 2048,
    "gain": 0.5,
    "input": [0.25, -0.5],
    "tolerance": 1e-6,
}


def audio_probe_expression(config: Mapping[str, Any] | None = None) -> str:
    """Return a browser-local async expression for deterministic audio evidence.

    The signal topology and dimensions are fixed by the qualification policy.
    ``config`` is accepted for forward-compatible metadata, but policy values
    cannot be changed by a caller.
    """

    requested = dict(_AUDIO_CONFIG)
    if config:
        configured_rate = config.get("sample_rate", config.get("sampleRate"))
        if configured_rate in (44100, 48000):
            requested["sampleRate"] = configured_rate
            requested["sample_rate"] = configured_rate
        requested["channels"] = 2
        requested["frames"] = 2048
        # Keep the public argument useful for callers that attach metadata,
        # while refusing to turn this probe into a tunable fingerprint.
        requested.update({
            key: config[key] for key in config
            if key not in requested
        })
    requested["sample_rate"] = requested["sampleRate"]
    requested["channels"] = requested["numberOfChannels"]
    requested["frames"] = requested["length"]
    encoded = json.dumps(requested, ensure_ascii=False, separators=(",", ":"))
    return f"""(async () => {{
  const requested = {encoded};
  const AudioCtor = globalThis.OfflineAudioContext || globalThis.webkitOfflineAudioContext;
  const base = {{
    operation: 'offline-buffer-gain',
    scope: 'rendernative',
    physicalInput: 'not_verified',
    physicalOutput: 'not_verified',
    requested,
    actual: {{ first: null, second: null }},
    checks: {{ supported: false, structure: false, output: false, deterministic: false,
               cleanup: false }},
    supported: false,
    status: 'unsupported',
    error: null
  }};
  const errorText = value => value && value.message ? String(value.message) : String(value);
  const errorObject = (kind, value, stage) => ({{
    kind, stage, message: errorText(value)
  }});
  if (typeof AudioCtor !== 'function') {{
    base.error = errorObject('unsupported', 'OfflineAudioContext unavailable', 'constructor');
    return base;
  }}

  function sampleHash(data) {{
    // Hash the exact float32 bit pattern, avoiding locale- or rounding-based
    // string conversions in the determinism check.
    const bits = new DataView(new ArrayBuffer(4));
    let hash = 2166136261;
    for (let i = 0; i < data.length; i++) {{
      bits.setFloat32(0, data[i], true);
      let word = bits.getUint32(0, true);
      for (let shift = 0; shift < 32; shift += 8) {{
        hash ^= (word >>> shift) & 255;
        hash = Math.imul(hash, 16777619);
      }}
    }}
    return (hash >>> 0).toString(16).padStart(8, '0');
  }}

  function channelEvidence(buffer, channel, expected) {{
    const data = buffer.getChannelData(channel);
    let minimum = Infinity, maximum = -Infinity, sum = 0, maxAbsError = 0;
    for (let i = 0; i < data.length; i++) {{
      const value = data[i];
      minimum = Math.min(minimum, value);
      maximum = Math.max(maximum, value);
      sum += value;
      maxAbsError = Math.max(maxAbsError, Math.abs(value - expected));
    }}
    return {{
      length: data.length,
      min: minimum,
      max: maximum,
      sum,
      first8samples: Array.from(data.slice(0, 8)),
      maxAbsError,
      expected,
      sampleHash: sampleHash(data),
      hash: sampleHash(data)
    }};
  }}

  async function renderOnce() {{
    let context = null, source = null, gain = null, rendered = null;
    const evidence = {{ context: null, renderBuffer: null, channels: null, stateBeforeRender: null,
                        stateAfterRender: null, stateAfterClose: null,
                        closeSupported: false, cleanupError: null }};
    try {{
      context = new AudioCtor(requested.numberOfChannels, requested.length,
                              requested.sampleRate);
      evidence.context = {{
        sampleRate: context.sampleRate,
        length: context.length,
        // OfflineAudioContext does not expose numberOfChannels directly;
        // the destination channel count is the context's output channel count.
        numberOfChannels: context.destination.channelCount,
        destinationChannelCount: context.destination.channelCount
      }};
      evidence.stateBeforeRender = context.state;
      const input = context.createBuffer(requested.numberOfChannels, requested.length,
                                         requested.sampleRate);
      input.getChannelData(0).fill(requested.input[0]);
      input.getChannelData(1).fill(requested.input[1]);
      source = context.createBufferSource();
      source.buffer = input;
      gain = context.createGain();
      gain.gain.value = requested.gain;
      source.connect(gain);
      gain.connect(context.destination);
      source.start(0);
      rendered = await context.startRendering();
      evidence.stateAfterRender = context.state;
      evidence.renderBuffer = {{
        sampleRate: rendered.sampleRate,
        length: rendered.length,
        numberOfChannels: rendered.numberOfChannels
      }};
      evidence.channels = [
        channelEvidence(rendered, 0, requested.input[0] * requested.gain),
        channelEvidence(rendered, 1, requested.input[1] * requested.gain)
      ];
      return evidence;
    }} catch (error) {{
      evidence.error = errorObject('error', error, 'render');
      const wrapped = new Error(errorText(error));
      wrapped.audioEvidence = evidence;
      throw wrapped;
    }} finally {{
      try {{ if (source && typeof source.stop === 'function') source.stop(); }}
      catch (error) {{ evidence.cleanupError = errorObject('cleanup', error, 'source.stop'); }}
      try {{ if (source && typeof source.disconnect === 'function') source.disconnect(); }}
      catch (error) {{ evidence.cleanupError = errorObject('cleanup', error, 'source.disconnect'); }}
      try {{ if (gain && typeof gain.disconnect === 'function') gain.disconnect(); }}
      catch (error) {{ evidence.cleanupError = errorObject('cleanup', error, 'gain.disconnect'); }}
      if (context) {{
        if (typeof context.close === 'function') {{
          evidence.closeSupported = true;
          try {{ await context.close(); }}
          catch (error) {{ evidence.cleanupError = errorObject('cleanup', error, 'context.close'); }}
        }}
        evidence.stateAfterClose = context.state;
      }}
    }}
  }}

  let first, second;
  try {{
    first = await renderOnce();
    base.actual.first = first;
    second = await renderOnce();
    base.actual.second = second;
  }} catch (error) {{
    if (error && error.audioEvidence) {{
      if (!base.actual.first) base.actual.first = error.audioEvidence;
      else if (!base.actual.second) base.actual.second = error.audioEvidence;
    }}
    base.error = errorObject('error', error, 'render');
    base.status = 'error';
    return base;
  }}

  function structureValid(evidence) {{
    return !!evidence && !!evidence.context
      && !!evidence.renderBuffer
      && evidence.context.sampleRate === requested.sampleRate
      && evidence.context.length === requested.length
      && evidence.context.numberOfChannels === requested.numberOfChannels
      && evidence.renderBuffer.sampleRate === requested.sampleRate
      && evidence.renderBuffer.length === requested.length
      && evidence.renderBuffer.numberOfChannels === requested.numberOfChannels
      && evidence.stateAfterClose === 'closed'
      && Array.isArray(evidence.channels) && evidence.channels.length === 2
      && evidence.channels.every(channel => channel.length === requested.length);
  }}
  function outputValid(evidence) {{
    return structureValid(evidence) && evidence.channels.every(channel =>
      Number.isFinite(channel.min) && Number.isFinite(channel.max)
      && Number.isFinite(channel.sum) && Number.isFinite(channel.maxAbsError)
      && channel.maxAbsError <= requested.tolerance
      && channel.first8samples.length === 8
      && channel.first8samples.every(value => Number.isFinite(value)));
  }}
  const structure = structureValid(first) && structureValid(second);
  const output = outputValid(first) && outputValid(second);
  const deterministic = structure && output
    && first.channels.every((channel, index) =>
      channel.sampleHash === second.channels[index].sampleHash
      && channel.length === second.channels[index].length
      && channel.min === second.channels[index].min
      && channel.max === second.channels[index].max
      && channel.sum === second.channels[index].sum
      && channel.first8samples.every((value, i) => value === second.channels[index].first8samples[i]));
  // OfflineAudioContext commonly reaches closed naturally after rendering and
  // does not implement close().  A missing close method is therefore not a
  // cleanup failure; a non-closed state or recorded cleanup error is.
  const cleanup = [first, second].every(evidence =>
    evidence.stateAfterClose === 'closed' && !evidence.cleanupError);
  base.supported = true;
  base.status = structure && output && deterministic && cleanup ? 'supported' : 'partial';
  base.checks = {{supported: true, structure, output, deterministic, cleanup}};
  if (base.status !== 'supported')
    base.error = errorObject('validation', 'offline audio evidence failed', 'checks');
  return base;
}})()"""


AUDIO_PROBE = audio_probe_expression()


__all__ = ["AUDIO_PROBE", "audio_probe_expression"]
