"""Silent, gesture-driven native AudioContext diagnostics in the probe tab."""

REALTIME_AUDIO_SETUP = r'''(() => {
  const report = window.__tbpRealtimeAudio = {done:false, physicalOutput:'not_verified',
    inputDevices:'not_verified', workers:'notapplicable'};
  const button = document.createElement('button');
  button.id = '__tbp_audio_start'; button.textContent = 'Run audio probe';
  document.body.appendChild(button);
  let context = null, oscillator = null, amplitude = null, analyser = null, mute = null;
  async function cleanup() {
    try { if (oscillator) oscillator.stop(); } catch (_) {}
    for (const node of [oscillator, amplitude, analyser, mute]) {
      try { if (node) node.disconnect(); } catch (_) {}
    }
    try { if (context && context.state !== 'closed') await bounded(context.close()); }
    catch (e) { report.cleanupError = String(e); }
    report.closedState = context ? context.state : null;
    button.remove();
  }
  window.__tbpAudioCleanup = cleanup;
  const wait = ms => new Promise(resolve => setTimeout(resolve, ms));
  async function bounded(promise) {
    let timer;
    try { return await Promise.race([promise, new Promise((_,reject) => {
      timer = setTimeout(() => reject(new Error('AudioContext operation timeout')), 3000);
    })]); } finally { clearTimeout(timer); }
  }
  try {
    context = new AudioContext();
    report.sampleRate = context.sampleRate;
    report.initialState = context.state;
    report.maxChannelCount = context.destination.maxChannelCount;
  } catch (e) { report.error = String(e); report.done = true; return report; }
  button.addEventListener('click', async event => {
    button.disabled = true;
    report.trusted = event.isTrusted;
    try {
      if (!event.isTrusted) throw new Error('Trusted user activation required');
      await bounded(context.resume()); report.runningState = context.state;
      oscillator = context.createOscillator(); oscillator.type = 'sine';
      oscillator.frequency.value = context.sampleRate / 32;
      amplitude = context.createGain(); amplitude.gain.value = 0.25;
      analyser = context.createAnalyser(); analyser.fftSize = 2048;
      analyser.smoothingTimeConstant = 0;
      mute = context.createGain(); mute.gain.value = 0;
      oscillator.connect(amplitude).connect(analyser).connect(mute).connect(context.destination);
      report.outputGain = mute.gain.value;
      oscillator.start();
      report.timeBefore = context.currentTime;
      await wait(180);
      report.timeAfter = context.currentTime;
      const wave = new Float32Array(analyser.fftSize);
      const bytes = new Uint8Array(analyser.fftSize);
      const spectrum = new Float32Array(analyser.frequencyBinCount);
      analyser.getFloatTimeDomainData(wave); analyser.getByteTimeDomainData(bytes);
      analyser.getFloatFrequencyData(spectrum);
      let min = Infinity, max = -Infinity, squareSum = 0, peakBin = -1, peakDb = -Infinity;
      for (const value of wave) { min = Math.min(min,value); max = Math.max(max,value); squareSum += value*value; }
      for (let i=0;i<spectrum.length;i++) if (spectrum[i] > peakDb) { peakDb=spectrum[i]; peakBin=i; }
      report.analyser = {fftSize:analyser.fftSize, frequencyBinCount:analyser.frequencyBinCount,
        frequency:oscillator.frequency.value, length:wave.length, min, max,
        rms:Math.sqrt(squareSum/wave.length), first64:Array.from(wave.slice(0,64)),
        byteFirst64:Array.from(bytes.slice(0,64)), peakBin,
        peakDb:Number.isFinite(peakDb) ? peakDb : null};
      report.baseLatency = context.baseLatency;
      report.outputLatency = context.outputLatency === undefined ? null : context.outputLatency;
      await bounded(context.suspend()); report.suspendedState = context.state;
      report.suspendedTimeBefore = context.currentTime;
      await wait(50); report.suspendedTimeAfter = context.currentTime;
      await bounded(context.resume()); report.resumedState = context.state;
    } catch (e) { report.error = String(e); }
    finally { await cleanup(); report.done = true; }
  }, {once:true});
  return {ready:true, target:'#__tbp_audio_start'};
})()'''
