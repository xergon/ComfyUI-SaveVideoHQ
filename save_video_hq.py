"""SaveVideoHQ - quality-tier video save for ComfyUI.

Takes a VIDEO input and re-encodes with industry-standard preset tiers.
Every preset name encodes: codec / subsampling / bit depth / quality target.

Bit-depth fidelity:
  10-bit pix_fmts use a 16-bit RGB intermediate (uint16, rgb48le) so the
  diffusion's float precision actually reaches the encoder. 8-bit pix_fmts
  use the standard uint8 path.
"""

import os
import logging
from fractions import Fraction

import av
import folder_paths


log = logging.getLogger("SaveVideoHQ")


# All presets are intra-only (no inter-frame prediction).
# Every name is "<codec>_<sampling>_<bitdepth>_<quality>" so you know exactly what
# you're getting.  H.265 is intra-only here for professional use cases - inter-frame
# H.265 mangles particles regardless of bitrate.
PRESETS = {
    # ProRes (intra-only by design, MOV container).
    # Native specs: Proxy/LT/422/HQ are 4:2:2 10-bit; 4444/XQ are 4:4:4 12-bit (encoded via 10-bit pix_fmt).
    "ProRes_Proxy_4:2:2_10bit_(~45Mbps)":     {"codec": "prores_ks", "container": "mov", "pix_fmt": "yuv422p10le", "opts": {"profile": "0"}},
    "ProRes_LT_4:2:2_10bit_(~102Mbps)":       {"codec": "prores_ks", "container": "mov", "pix_fmt": "yuv422p10le", "opts": {"profile": "1"}},
    "ProRes_422_4:2:2_10bit_(~147Mbps)":      {"codec": "prores_ks", "container": "mov", "pix_fmt": "yuv422p10le", "opts": {"profile": "2"}},
    "ProRes_HQ_4:2:2_10bit_(~220Mbps)":       {"codec": "prores_ks", "container": "mov", "pix_fmt": "yuv422p10le", "opts": {"profile": "3"}},
    "ProRes_4444_4:4:4_12bit_(~330Mbps)":     {"codec": "prores_ks", "container": "mov", "pix_fmt": "yuv444p10le", "opts": {"profile": "4"}},
    "ProRes_4444_XQ_4:4:4_12bit_(~500Mbps)":  {"codec": "prores_ks", "container": "mov", "pix_fmt": "yuv444p10le", "opts": {"profile": "5"}},

    # H.264 All-I 8-bit - hardware-decoded by every modern GPU and most phones.
    "H264_AllI_4:2:0_8bit_CRF14":  {"codec": "libx264", "container": "mp4", "pix_fmt": "yuv420p", "opts": {"crf": "14", "preset": "slow", "profile": "high",    "g": "1", "bf": "0", "x264-params": "keyint=1:scenecut=0"}},
    "H264_AllI_4:2:2_8bit_CRF12":  {"codec": "libx264", "container": "mp4", "pix_fmt": "yuv422p", "opts": {"crf": "12", "preset": "slow", "profile": "high422", "g": "1", "bf": "0", "x264-params": "keyint=1:scenecut=0"}},
    "H264_AllI_4:4:4_8bit_CRF10":  {"codec": "libx264", "container": "mp4", "pix_fmt": "yuv444p", "opts": {"crf": "10", "preset": "slow", "profile": "high444", "g": "1", "bf": "0", "x264-params": "keyint=1:scenecut=0"}},

    # H.264 All-I 10-bit - many GPUs cannot accelerate decode (high10/high422 profiles).
    # Use these when post-production tools matter more than playback compatibility.
    "H264_AllI_4:2:0_10bit_CRF14":      {"codec": "libx264", "container": "mp4", "pix_fmt": "yuv420p10le", "opts": {"crf": "14", "preset": "slow", "profile": "high10",   "g": "1", "bf": "0", "x264-params": "keyint=1:scenecut=0"}},
    "H264_AllI_4:2:2_10bit_CRF12":      {"codec": "libx264", "container": "mp4", "pix_fmt": "yuv422p10le", "opts": {"crf": "12", "preset": "slow", "profile": "high422",  "g": "1", "bf": "0", "x264-params": "keyint=1:scenecut=0"}},
    "H264_AllI_4:4:4_10bit_CRF10":      {"codec": "libx264", "container": "mkv", "pix_fmt": "yuv444p10le", "opts": {"crf": "10", "preset": "slow", "profile": "high444",  "g": "1", "bf": "0", "x264-params": "keyint=1:scenecut=0"}},
    "H264_AllI_4:4:4_10bit_lossless":   {"codec": "libx264", "container": "mkv", "pix_fmt": "yuv444p10le", "opts": {"qp": "0", "preset": "veryslow", "profile": "high444", "g": "1", "bf": "0", "x264-params": "keyint=1:scenecut=0"}},

    # H.265 All-I - smaller files than equivalent H.264 All-I (~30-40% size savings)
    # at equivalent visual quality.  All variants are particle-tuned: tune=grain,
    # deblock=-3,-3, no SAO.  Always 10-bit profiles since x265 prefers 10-bit pipeline.
    "H265_AllI_4:2:0_8bit_CRF14":      {"codec": "libx265", "container": "mp4", "pix_fmt": "yuv420p",     "opts": {"crf": "14", "preset": "slow", "profile": "main",        "x265-params": "keyint=1:no-open-gop=1:bframes=0:tune=grain:deblock=-3,-3:no-sao=1"}},
    "H265_AllI_4:2:0_10bit_CRF14":     {"codec": "libx265", "container": "mp4", "pix_fmt": "yuv420p10le", "opts": {"crf": "14", "preset": "slow", "profile": "main10",      "x265-params": "keyint=1:no-open-gop=1:bframes=0:tune=grain:deblock=-3,-3:no-sao=1"}},
    "H265_AllI_4:2:2_10bit_CRF12":     {"codec": "libx265", "container": "mkv", "pix_fmt": "yuv422p10le", "opts": {"crf": "12", "preset": "slow", "profile": "main422-10",  "x265-params": "keyint=1:no-open-gop=1:bframes=0:tune=grain:deblock=-3,-3:no-sao=1"}},
    "H265_AllI_4:4:4_10bit_CRF10":     {"codec": "libx265", "container": "mkv", "pix_fmt": "yuv444p10le", "opts": {"crf": "10", "preset": "slow", "profile": "main444-10",  "x265-params": "keyint=1:no-open-gop=1:bframes=0:tune=grain:deblock=-3,-3:no-sao=1"}},
    "H265_AllI_4:4:4_10bit_lossless":  {"codec": "libx265", "container": "mkv", "pix_fmt": "yuv444p10le", "opts": {"preset": "veryslow", "profile": "main444-10", "x265-params": "lossless=1:keyint=1:no-open-gop=1:bframes=0"}},

    # Mathematical lossless - MKV container.
    "FFV1_lossless_4:4:4_10bit":  {"codec": "ffv1", "container": "mkv", "pix_fmt": "yuv444p10le", "opts": {"level": "3", "g": "1", "coder": "1", "context": "1"}},

    # Manual override
    "Custom":  None,
}


# Profile lookup for the Custom path
H264_PROFILE = {
    "yuv420p": "high", "yuv422p": "high422", "yuv444p": "high444",
    "yuv420p10le": "high10", "yuv422p10le": "high422", "yuv444p10le": "high444",
}
H265_PROFILE = {
    "yuv420p": "main", "yuv422p": "main422-8", "yuv444p": "main444-8",
    "yuv420p10le": "main10", "yuv422p10le": "main422-10", "yuv444p10le": "main444-10",
}


class SaveVideoHQ:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "video": ("VIDEO",),
                "filename_prefix": ("STRING", {"default": "video_hq"}),
                "preset": (
                    list(PRESETS.keys()),
                    {
                        "default": "H264_AllI_4:2:2_10bit_CRF12",
                        "tooltip": "Preset format: <codec>_AllI_<subsampling>_<bitdepth>_<quality>. All presets are intra-only (no inter-frame). Pick Custom to use the manual widgets below.",
                    },
                ),
                # Manual widgets active only when preset == Custom
                "custom_codec": (["libx264", "libx265", "prores_ks", "ffv1"], {"default": "libx264"}),
                "custom_container": (["mp4", "mkv", "mov"], {"default": "mp4"}),
                "custom_rate_control": (["crf", "bitrate", "lossless"], {"default": "crf"}),
                "custom_crf": ("INT", {"default": 12, "min": 0, "max": 51}),
                "custom_bitrate_mbps": ("INT", {"default": 80, "min": 1, "max": 2000}),
                "custom_preset_speed": (["medium", "slow", "slower", "veryslow", "placebo", "fast", "faster", "veryfast", "ultrafast"], {"default": "slow"}),
                "custom_all_intra": ("BOOLEAN", {"default": True}),
                "custom_pix_fmt": (["yuv444p10le", "yuv444p", "yuv422p10le", "yuv422p", "yuv420p10le", "yuv420p"], {"default": "yuv444p10le"}),
            }
        }

    RETURN_TYPES = ()
    OUTPUT_NODE = True
    CATEGORY = "video"
    FUNCTION = "save"
    DESCRIPTION = "Save VIDEO with intra-only preset tiers (ProRes, H.264 All-I, H.265 All-I, FFV1 lossless). All preset names spell out subsampling and bit depth. 10-bit presets use a 16-bit RGB pipeline so float precision reaches the encoder."

    def save(self, video, filename_prefix, preset,
             custom_codec, custom_container, custom_rate_control,
             custom_crf, custom_bitrate_mbps, custom_preset_speed,
             custom_all_intra, custom_pix_fmt):
        cfg = PRESETS.get(preset)
        if cfg is None:
            cfg = self._build_custom_config(
                custom_codec, custom_container, custom_rate_control,
                custom_crf, custom_bitrate_mbps, custom_preset_speed,
                custom_all_intra, custom_pix_fmt,
            )

        codec = cfg["codec"]
        container = cfg["container"]
        pix_fmt = cfg["pix_fmt"]
        opts = cfg["opts"]

        comp = video.get_components()
        frames = comp.images
        audio = comp.audio
        fps = comp.frame_rate
        if not isinstance(fps, Fraction):
            fps = Fraction(float(fps)).limit_denominator(1000)

        out_dir = folder_paths.get_output_directory()
        full_path, base_prefix, _, subfolder, _ = folder_paths.get_save_image_path(filename_prefix, out_dir)
        os.makedirs(full_path, exist_ok=True)
        existing = [f for f in os.listdir(full_path) if f.startswith(base_prefix) and f.endswith(f".{container}")]
        idx = 1 + max(
            [int(f[len(base_prefix) + 1:-(len(container) + 1)]) for f in existing
             if f[len(base_prefix) + 1:-(len(container) + 1)].isdigit()],
            default=0,
        )
        out_filename = f"{base_prefix}_{idx:05d}.{container}"
        out_path = os.path.join(full_path, out_filename)

        outc = av.open(out_path, mode="w")
        try:
            self._encode_video(outc, frames, fps, codec, opts, pix_fmt)
            if audio is not None:
                try:
                    self._encode_audio(outc, audio, container)
                except Exception as e:
                    log.warning(f"audio passthrough failed: {e}; saving video-only")
        finally:
            outc.close()

        size_mb = os.path.getsize(out_path) / 1e6
        log.info(f"SaveVideoHQ wrote {out_path} ({size_mb:.1f} MB) preset={preset}")

        # Mirror what comfy_api.latest._ui.PreviewVideo serializes to. The
        # frontend keys off the `animated: (True,)` tuple flag (note: tuple,
        # not list) to mount a video player + download link instead of an
        # image preview. This is the same shape the built-in SaveVideo uses.
        return {
            "ui": {
                "images": [{
                    "filename": out_filename,
                    "subfolder": subfolder,
                    "type": "output",
                }],
                "animated": (True,),
            }
        }

    def _build_custom_config(self, codec, container, rate_control, crf, bitrate_mbps,
                             preset_speed, all_intra, pix_fmt):
        opts = {}
        if codec in ("libx264", "libx265"):
            opts["preset"] = preset_speed
            if rate_control == "lossless":
                if codec == "libx264":
                    opts["qp"] = "0"
                else:
                    opts["x265-params"] = "lossless=1"
            elif rate_control == "bitrate":
                opts["b"] = f"{bitrate_mbps}M"
                opts["maxrate"] = f"{int(bitrate_mbps * 1.5)}M"
                opts["bufsize"] = f"{bitrate_mbps * 2}M"
            else:
                opts["crf"] = str(crf)
            if codec == "libx264":
                opts["profile"] = H264_PROFILE.get(pix_fmt, "high")
            else:
                opts["profile"] = H265_PROFILE.get(pix_fmt, "main")
            if all_intra:
                opts["g"] = "1"
                opts["bf"] = "0"
                if codec == "libx264":
                    opts["x264-params"] = "keyint=1:scenecut=0"
                else:
                    existing = opts.get("x265-params", "")
                    extra = "keyint=1:no-open-gop=1:bframes=0:tune=grain:deblock=-3,-3"
                    opts["x265-params"] = f"{existing}:{extra}" if existing else extra
        elif codec == "prores_ks":
            opts["profile"] = "5" if "444" in pix_fmt else "3"
        elif codec == "ffv1":
            opts.update({"level": "3", "coder": "1", "context": "1", "g": "1"})
        return {"codec": codec, "container": container, "pix_fmt": pix_fmt, "opts": opts}

    def _encode_video(self, outc, frames, fps, codec, opts, pix_fmt):
        n, h, w, c = frames.shape
        if c != 3:
            raise ValueError(f"expected 3 channels (RGB), got {c}")
        stream = outc.add_stream(codec, rate=fps)
        stream.width = int(w)
        stream.height = int(h)
        stream.pix_fmt = pix_fmt
        stream.time_base = Fraction(1, int(fps * 1000))
        stream.options = opts

        # 10-bit and 12-bit pix_fmts: feed PyAV uint16 RGB so the diffusion's float
        # precision actually survives. 8-bit pix_fmts: standard uint8 RGB path.
        is_high_bit = ("10le" in pix_fmt) or ("12le" in pix_fmt) or ("16le" in pix_fmt)
        if is_high_bit:
            arr = frames.cpu().numpy()
            arr = (arr * 65535.0).clip(0, 65535).astype("uint16")
            for f in arr:
                # rgb48le = 16-bit RGB little-endian; PyAV converts to target pix_fmt
                # at encode time, so we keep ~16 bits per channel until the codec
                # quantizes it down to 10 (or 12).
                frame = av.VideoFrame.from_ndarray(f, format="rgb48le")
                for packet in stream.encode(frame):
                    outc.mux(packet)
        else:
            arr = (frames.cpu().numpy() * 255.0).clip(0, 255).astype("uint8")
            for f in arr:
                frame = av.VideoFrame.from_ndarray(f, format="rgb24")
                for packet in stream.encode(frame):
                    outc.mux(packet)

        for packet in stream.encode():
            outc.mux(packet)

    def _encode_audio(self, outc, audio, container):
        wf = audio["waveform"]
        sr = int(audio["sample_rate"])
        if wf is None or wf.numel() == 0:
            return
        a_codec = "aac"
        if container == "mkv":
            a_codec = "flac"
        elif container == "mov":
            a_codec = "pcm_s16le"
        a_stream = outc.add_stream(a_codec, rate=sr)
        a_stream.layout = "stereo" if wf.shape[1] >= 2 else "mono"
        samples = wf[0].cpu().numpy()
        if samples.dtype != "float32":
            samples = samples.astype("float32")
        a_frame = av.AudioFrame.from_ndarray(samples, format="fltp", layout=a_stream.layout)
        a_frame.sample_rate = sr
        for packet in a_stream.encode(a_frame):
            outc.mux(packet)
        for packet in a_stream.encode():
            outc.mux(packet)


NODE_CLASS_MAPPINGS = {"SaveVideoHQ": SaveVideoHQ}
NODE_DISPLAY_NAME_MAPPINGS = {"SaveVideoHQ": "Save Video (High Quality)"}
