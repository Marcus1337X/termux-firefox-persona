# Native codec fixtures

These six files are repository-owned, synthetic media used only for the
`native-codecs-v1` browser decode/playback probe. No external media is used.
The video files are video-only: 64x64, yuv420p, 8 fps, 2 seconds; frames from
0 through 0.999 seconds are RGB red (`255,0,0`) and frames from 1 through
1.999 seconds are RGB lime (`0,255,0`). H.264, VP9, and AV1 carry BT.709
metadata after an explicit BT.601-to-BT.709 conversion. VP8 retains the
native BT.601-like source values and carries SMPTE 170M metadata because the
VP8 bitstream color-space definition uses that YUV interpretation ([RFC 6386,
section 9.2](https://www.rfc-editor.org/rfc/rfc6386.html#section-9.2)). The
audio files are audio-only: mono, 48 kHz, 2 seconds, 1 kHz sine at FFmpeg's
bit-exact 1/8 amplitude.

The fixture manifest is the source of the MIME, codec-string, stream bitrate,
and SHA-256 values. The media server serves only files listed there.

## Reproduction

The inherited Termux process had `LD_LIBRARY_PATH` set to the Codex CLI
directory first. Its `libc++_shared.so` has SHA-256
`430f7cde7c1a88042bb9e39ae1c1f4b9f5819f3bd95acd1453d2e02c63ba1870`, while
the prefix library has SHA-256
`e09c2f45cf4cf8ae574f94b6c2650d99ead0d332d5396f6613f062a2d2d73540`.
With the inherited order, the Android linker failed while loading
`libplacebo.so` because of the `libc++_shared.so` symbol
`_ZNSt6__ndk127__from_chars_floating_pointIfE...`. A child process with
`LD_LIBRARY_PATH=$TP/lib` before invoking FFmpeg succeeds, while the
Codex-CLI directory first reproduces the failure. This is a partial loader
observation; it does not claim a complete ELF dependency analysis or infer
why the two libraries differ internally.

The fixtures were generated with that per-process prefix path. A private
download is not required to reproduce the working path and no package was
installed or upgraded. For provenance, the matching package URL was
`https://is.mirror.flokinet.net/termux/termux-main/pool/main/libc/libc++/libc++_29_aarch64.deb`.
The installed `libc++` package and its private extraction both report version
29, and their files are byte-identical (size 1,374,336; SHA-256
`e09c2f45cf4cf8ae574f94b6c2650d99ead0d332d5396f6613f062a2d2d73540`).

```sh
TP=/data/data/com.termux/files/usr
OUT=src/persona/assets/media
VF709='color=c=0xff0000:size=64x64:rate=8:duration=1[r];color=c=0x00ff00:size=64x64:rate=8:duration=1[g];[r][g]concat=n=2:v=1:a=0,scale=in_color_matrix=bt601:out_color_matrix=bt709,format=yuv420p,setparams=colorspace=bt709:color_primaries=bt709:color_trc=bt709:range=tv[v]'
VF601='color=c=0xff0000:size=64x64:rate=8:duration=1[r];color=c=0x00ff00:size=64x64:rate=8:duration=1[g];[r][g]concat=n=2:v=1:a=0,format=yuv420p,setparams=colorspace=smpte170m:color_primaries=smpte170m:color_trc=smpte170m:range=tv[v]'
FF="env LD_LIBRARY_PATH=$TP/lib $TP/bin/ffmpeg"

$FF -hide_banner -y -filter_complex "$VF709" -map '[v]' -an \
  -c:v libx264 -preset ultrafast -threads 1 -g 8 -keyint_min 8 \
  -pix_fmt yuv420p -colorspace bt709 -color_primaries bt709 \
  -color_trc bt709 -color_range tv -movflags +faststart "$OUT/h264.mp4"
$FF -hide_banner -y -filter_complex "$VF601" -map '[v]' -an \
  -c:v libvpx -deadline good -cpu-used 8 -threads 1 -g 8 -crf 4 -b:v 0 \
  -pix_fmt yuv420p -colorspace smpte170m -color_primaries smpte170m \
  -color_trc smpte170m -color_range tv "$OUT/vp8.webm"
$FF -hide_banner -y -filter_complex "$VF709" -map '[v]' -an \
  -c:v libvpx-vp9 -deadline good -cpu-used 8 -threads 1 -g 8 -crf 20 -b:v 0 \
  -pix_fmt yuv420p -colorspace bt709 -color_primaries bt709 \
  -color_trc bt709 -color_range tv "$OUT/vp9.webm"
$FF -hide_banner -y -filter_complex "$VF709" -map '[v]' -an \
  -c:v libaom-av1 -cpu-used 8 -threads 1 -g 8 -crf 30 -b:v 0 -row-mt 0 \
  -pix_fmt yuv420p -colorspace bt709 -color_primaries bt709 \
  -color_trc bt709 -color_range tv "$OUT/av1.webm"
$FF -hide_banner -y -f lavfi -i 'sine=frequency=1000:sample_rate=48000:duration=2' \
  -vn -c:a aac -b:a 96k -ar 48000 -ac 1 "$OUT/aac.m4a"
$FF -hide_banner -y -f lavfi -i 'sine=frequency=1000:sample_rate=48000:duration=2' \
  -vn -c:a libopus -b:a 96k -ar 48000 -ac 1 "$OUT/opus.webm"
```

The generator used FFmpeg 8.1.2 with libx264, libvpx 1.16.0, libaom-av1
3.14.1, native AAC, and libopus. The private download was the exact URL
above; its archive SHA-256 was
`bb9f12113c137aa0e8513bb51cc49fe77a5ce3ca39ab9e92c57d228ecdf00222`.
FFprobe confirmed BT.709 matrix/primaries/transfer and TV-range metadata on
H.264, VP9, and AV1, and SMPTE 170M equivalents on VP8. For an explicit
FFmpeg RGB check, use
`colorspace=iall=bt709:all=bt709:irange=tv:range=tv` before `format=rgb24`
for the BT.709 files; their sampled centers are approximately red
`(252,0,0)` and lime `(0,254,0)`. VP8's default decoded centers are
approximately red `(254,0,0)` and lime `(0,255,1)`. Decoded AAC and Opus had
non-silent 1 kHz waveforms.
