"""Native decoding and silent media playback against generated fixtures."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re


ASSET_ROOT = Path(__file__).parent / "assets" / "media"


def media_manifest() -> dict:
    manifest = json.loads((ASSET_ROOT / "manifest.json").read_text())
    expected = {"h264": ("h264.mp4", "video/mp4", "video"),
                "vp8": ("vp8.webm", "video/webm", "video"),
                "vp9": ("vp9.webm", "video/webm", "video"),
                "av1": ("av1.webm", "video/webm", "video"),
                "aac": ("aac.m4a", "audio/mp4", "audio"),
                "opus": ("opus.webm", "audio/webm", "audio")}
    codecs = {"h264": "avc1.42c00a", "vp8": "vp8", "vp9": "vp09.00.10.08",
              "av1": "av01.0.00M.08", "aac": "mp4a.40.2", "opus": "opus"}
    fixtures = manifest.get("fixtures") if isinstance(manifest, dict) else None
    if (not isinstance(manifest, dict) or manifest.get("schema_version") != 1 or manifest.get("id") != "native-codecs-v1"
            or not isinstance(fixtures, list)
            or [f.get("id") if isinstance(f, dict) else None for f in fixtures] != list(expected)):
        raise ValueError("Media fixture manifest does not match the finite suite")
    for fixture in fixtures:
        if ((fixture.get("filename"), fixture.get("mime"), fixture.get("kind")) != expected[fixture["id"]]
                or fixture.get("content_type") != f'{fixture["mime"]}; codecs="{codecs[fixture["id"]]}"'
                or type(fixture.get("bitrate")) is not int or fixture["bitrate"] <= 0
                or (fixture["kind"] == "video" and
                    (type(fixture.get("framerate")) is not int or fixture["framerate"] != 8))
                or not isinstance(fixture.get("sha256"), str)
                or not re.fullmatch("[0-9a-f]{64}", fixture["sha256"])):
            raise ValueError("Invalid media fixture metadata")
        path = ASSET_ROOT / fixture["filename"]
        if path.is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest() != fixture["sha256"]:
            raise ValueError("Media fixture content does not match its manifest")
    return manifest


def media_setup_expression() -> str:
    return MEDIA_SETUP.replace("__TBP_MEDIA_MANIFEST__", json.dumps(media_manifest()))


MEDIA_SETUP = r'''(() => {
  const manifest = __TBP_MEDIA_MANIFEST__;
  const report = window.__tbpMedia = {done:false, fixtureSet:manifest.id, codecs:{},
    physicalInput:'not_verified', physicalOutput:'not_verified', webRTC:'not_verified'};
  const button = document.createElement('button');
  button.id = '__tbp_media_start'; button.textContent = 'Run media probe';
  document.body.appendChild(button);
  let context = null, active = null, source = null, analyser = null, mute = null;
  const wait = ms => new Promise(resolve => setTimeout(resolve,ms));
  async function bounded(promise, ms=6000) {
    let timer;
    try { return await Promise.race([promise,new Promise((_,reject)=>{
      timer=setTimeout(()=>reject(new Error('Media operation timeout')),ms);
    })]); } finally { clearTimeout(timer); }
  }
  async function until(test, ms=6000) {
    const deadline=Date.now()+ms;
    while (!test()) {
      if (active && active.error) throw new Error('MediaError '+active.error.code+': '+active.error.message);
      if (Date.now()>deadline) throw new Error('Media observation timeout');
      await wait(20);
    }
  }
  async function seek(time) {
    const element=active;
    let listener;
    const event=new Promise(resolve=>{
      listener=resolve;element.addEventListener('seeked',listener,{once:true});
    });
    try {element.currentTime=time;await bounded(event);}
    finally {element.removeEventListener('seeked',listener);}
    await bounded(new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
  }
  function clearElement() {
    for (const node of [source,analyser,mute]) if (node) node.disconnect();
    source=null; analyser=null; mute=null;
    if (active) {
      active.pause(); active.removeAttribute('src'); active.load(); active.remove(); active=null;
    }
  }
  async function cleanup() {
    try { clearElement(); } catch(e) { report.cleanupError=String(e); }
    try { if (context && context.state!=='closed') await bounded(context.close()); }
    catch(e) { report.cleanupError=String(e); }
    report.closedState=context ? context.state : null;
    button.remove();
  }
  window.__tbpMediaCleanup=cleanup;
  function sampleStats(data, count=256) {
    let min=Infinity,max=-Infinity,square=0,sum=0;
    for (const x of data) { min=Math.min(min,x);max=Math.max(max,x);square+=x*x;sum+=x; }
    return {length:data.length,min,max,mean:sum/data.length,rms:Math.sqrt(square/data.length),
      samples:Array.from(data.slice(0,count))};
  }
  function pixels(video) {
    const canvas=document.createElement('canvas'); canvas.width=64;canvas.height=64;
    const ctx=canvas.getContext('2d'); ctx.drawImage(video,0,0,64,64);
    const rgba=ctx.getImageData(0,0,64,64).data;
    const min=[255,255,255,255],max=[0,0,0,0],sum=[0,0,0,0];
    for(let i=0;i<rgba.length;i++) {const c=i%4;min[c]=Math.min(min[c],rgba[i]);max[c]=Math.max(max[c],rgba[i]);sum[c]+=rgba[i];}
    return {width:canvas.width,height:canvas.height,min,max,mean:sum.map(x=>x/4096),
      center:Array.from(ctx.getImageData(32,32,1,1).data)};
  }
  async function probeFixture(fixture) {
    const result={sha256:fixture.sha256,contentType:fixture.content_type,kind:fixture.kind,events:[]};
    report.codecs[fixture.id]=result;
    try {
      active=document.createElement(fixture.kind); active.preload='auto';
      for(const event of ['loadedmetadata','canplay','playing','waiting','stalled','seeked','ended','error']) {
        const element=active;
        element.addEventListener(event,()=>result.events.push({event,time:element.currentTime}));
      }
      if(fixture.kind==='video') {active.muted=true;active.width=64;active.height=64;}
      document.body.appendChild(active);
      result.canPlayType=active.canPlayType(fixture.content_type);
      const config={type:'file'};
      if(fixture.kind==='video') config.video={contentType:fixture.content_type,width:64,height:64,
        bitrate:fixture.bitrate,framerate:fixture.framerate};
      else config.audio={contentType:fixture.content_type,channels:'1',bitrate:fixture.bitrate,samplerate:48000};
      result.decodingConfig=config;
      try {result.decodingInfo=await bounded(navigator.mediaCapabilities.decodingInfo(config));}
      catch(e) {result.decodingInfo={error:String(e)};}
      const url='/__tbp_media/'+fixture.filename;
      active.src=url; active.load();
      await until(()=>active.readyState>=2);
      result.duration=active.duration; result.readyState=active.readyState;
      if(fixture.kind==='video') {
        result.width=active.videoWidth; result.height=active.videoHeight;
        await bounded(active.play());
        result.timeBefore=active.currentTime;
        await until(()=>active.currentTime>=0.2);
        result.timeAfter=active.currentTime;
        active.pause(); await seek(0.25);
        result.red=pixels(active);
        await seek(1.25);
        result.green=pixels(active);
        await bounded(active.play()); await until(()=>active.ended);
        result.ended=active.ended; result.endTime=active.currentTime;
        const quality=active.getVideoPlaybackQuality();
        result.totalVideoFrames=quality.totalVideoFrames;
        result.droppedVideoFrames=quality.droppedVideoFrames;
      } else {
        const response=await bounded(fetch(url));
        if(!response.ok) throw new Error('Media fetch HTTP '+response.status);
        const decoded=await bounded(context.decodeAudioData(await response.arrayBuffer()));
        result.decoded={sampleRate:decoded.sampleRate,channels:decoded.numberOfChannels,
          length:decoded.length,duration:decoded.duration,
          signal:sampleStats(decoded.getChannelData(0).slice(Math.floor(decoded.sampleRate*0.25),
            Math.floor(decoded.sampleRate*0.25)+4096))};
        source=context.createMediaElementSource(active); analyser=context.createAnalyser();
        analyser.fftSize=2048;analyser.smoothingTimeConstant=0;
        mute=context.createGain();mute.gain.value=0;
        source.connect(analyser).connect(mute).connect(context.destination);
        result.outputGain=mute.gain.value;
        await bounded(active.play());result.timeBefore=active.currentTime;
        await until(()=>active.currentTime>=0.3);
        result.timeAfter=active.currentTime;
        const data=new Float32Array(analyser.fftSize),spectrum=new Float32Array(analyser.frequencyBinCount);
        analyser.getFloatTimeDomainData(data);analyser.getFloatFrequencyData(spectrum);
        let peakBin=-1,peakDb=-Infinity;
        for(let i=0;i<spectrum.length;i++) if(spectrum[i]>peakDb) {peakBin=i;peakDb=spectrum[i];}
        result.playback={sampleRate:context.sampleRate,fftSize:analyser.fftSize,
          signal:sampleStats(data),peakBin,peakDb:Number.isFinite(peakDb)?peakDb:null};
        await until(()=>active.ended);result.ended=active.ended;result.endTime=active.currentTime;
      }
    } catch(e) {result.error=String(e);}
    finally {try {clearElement();}catch(e){result.cleanupError=String(e);}}
  }
  button.addEventListener('click',async event=>{
    button.disabled=true;report.trusted=event.isTrusted;
    try {
      if(!event.isTrusted) throw new Error('Trusted media activation required');
      context=new AudioContext();await bounded(context.resume());
      report.sampleRate=context.sampleRate;report.runningState=context.state;
      for(const fixture of manifest.fixtures) await probeFixture(fixture);
    } catch(e) {report.error=String(e);}
    finally {await cleanup();report.done=true;}
  },{once:true});
  return {ready:true,target:'#__tbp_media_start'};
})()'''
