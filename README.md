# ComfyUI-SaveVideoHQ

A ComfyUI custom node for saving `VIDEO` outputs at professional quality tiers — **ProRes ladder**, **All-Intra H.264 / H.265**, and **mathematically lossless FFV1** — with an honest 10-bit pipeline that preserves the diffusion model's float precision instead of throwing it away in an `uint8` cast.

Built for video diffusion workflows (LTX-Video, Wan, Hunyuan, etc.) where particle-heavy or gradient-heavy output gets mangled by ComfyUI's default `SaveVideo` and the `tune=film` defaults of most encoders.

![Save Video HQ node](docs/preset_dropdown.png)

## Why this exists

ComfyUI's built-in `SaveVideo` has no rate-control / GOP / pix_fmt knobs. `VHS_VideoCombine` exposes basic controls but doesn't ship sane "I want a ProRes HQ master" or "All-I H.264 4:4:4" presets, and uses a `uint8` RGB pipeline that quantizes 10-bit pix_fmts to 8 bits before encoding.

This node:

- Ships **19 named professional presets** (ProRes Proxy → 4444 XQ, H.264 / H.265 All-I in 4:2:0 / 4:2:2 / 4:4:4, FFV1 lossless) with consistent naming `<codec>_AllI_<subsampling>_<bitdepth>_<quality>`
- Uses a **16-bit RGB intermediate** (`uint16`, `rgb48le`) when the target pix_fmt is 10-bit so float precision actually reaches the encoder
- Tunes H.265 with `tune=grain:deblock=-3,-3:no-sao` so particles and high-frequency detail aren't smeared by HEVC's aggressive default filters
- Is **intra-only** for every preset — inter-frame compression mangles particles regardless of bitrate; All-Intra is the only sane choice for diffusion output going into post-production
- Has a **Custom** preset for full manual control of codec / CRF / bitrate / GOP / preset speed / pix_fmt

## Install

### Via ComfyUI Manager (recommended once registered)

1. Open ComfyUI Manager
2. Click "Install Custom Nodes"
3. Search "Save Video HQ"
4. Install, restart ComfyUI

### Via git clone

```bash
cd ComfyUI/custom_nodes
git clone https://github.com/xergon/ComfyUI-SaveVideoHQ
cd ComfyUI-SaveVideoHQ
pip install -r requirements.txt
```

Restart ComfyUI.

### Manual single-file install

Drop `save_video_hq.py` into `ComfyUI/custom_nodes/`. ComfyUI bundles `av` (PyAV) so no extra pip install is needed in most installs.

## Usage

Right-click the canvas → **Add Node → video → Save Video (High Quality)**, then connect a `VIDEO` output (from `CreateVideo`, `LTX-2.3 FLF2V`, etc.) to the node's `video` input.

```
[your video diffusion graph]
        |
     VIDEO
        |
        v
+------------------------------+
| Save Video (High Quality)   |
|                              |
| preset = ProRes_HQ_4:2:2_... |
| filename_prefix = render     |
+------------------------------+
        |
        v
   ComfyUI/output/render_00001.mov
```

## Presets

All presets are **All-Intra** (every frame a keyframe). Inter-frame prediction is disabled because it mangles particle / high-frequency content regardless of bitrate.

### ProRes (intra-only by design, MOV container)

| Preset | Subsampling | Bit depth | ~Bitrate | Use case |
|---|---|---|---|---|
| `ProRes_Proxy_4:2:2_10bit_(~45Mbps)` | 4:2:2 | 10-bit | 45 Mbps | Preview / proxy edit |
| `ProRes_LT_4:2:2_10bit_(~102Mbps)` | 4:2:2 | 10-bit | 102 Mbps | Light edit |
| `ProRes_422_4:2:2_10bit_(~147Mbps)` | 4:2:2 | 10-bit | 147 Mbps | Standard edit |
| `ProRes_HQ_4:2:2_10bit_(~220Mbps)` | 4:2:2 | 10-bit | 220 Mbps | Broadcast HQ master |
| `ProRes_4444_4:4:4_12bit_(~330Mbps)` | 4:4:4 | 12-bit | 330 Mbps | Feature / alpha-channel edit |
| `ProRes_4444_XQ_4:4:4_12bit_(~500Mbps)` | 4:4:4 | 12-bit | 500 Mbps | VFX master |

### H.264 All-I, 8-bit (broad GPU decode acceleration)

| Preset | Subsampling | CRF |
|---|---|---|
| `H264_AllI_4:2:0_8bit_CRF14` | 4:2:0 | 14 |
| `H264_AllI_4:2:2_8bit_CRF12` | 4:2:2 | 12 |
| `H264_AllI_4:4:4_8bit_CRF10` | 4:4:4 | 10 |

### H.264 All-I, 10-bit (some GPUs require software decode)

| Preset | Subsampling | CRF |
|---|---|---|
| `H264_AllI_4:2:0_10bit_CRF14` | 4:2:0 | 14 |
| `H264_AllI_4:2:2_10bit_CRF12` | 4:2:2 | 12 |
| `H264_AllI_4:4:4_10bit_CRF10` | 4:4:4 | 10 |
| `H264_AllI_4:4:4_10bit_lossless` | 4:4:4 | qp=0 (lossless) |

### H.265 All-I (smaller files than H.264 at equivalent quality, ~30-40% size reduction)

All H.265 presets use particle-friendly flags: `tune=grain:deblock=-3,-3:no-sao=1`.

| Preset | Subsampling | Bit depth | CRF |
|---|---|---|---|
| `H265_AllI_4:2:0_8bit_CRF14` | 4:2:0 | 8-bit | 14 |
| `H265_AllI_4:2:0_10bit_CRF14` | 4:2:0 | 10-bit | 14 |
| `H265_AllI_4:2:2_10bit_CRF12` | 4:2:2 | 10-bit | 12 |
| `H265_AllI_4:4:4_10bit_CRF10` | 4:4:4 | 10-bit | 10 |
| `H265_AllI_4:4:4_10bit_lossless` | 4:4:4 | 10-bit | lossless=1 |

### Lossless

| Preset | Codec | Container |
|---|---|---|
| `FFV1_lossless_4:4:4_10bit` | FFV1 | MKV |

### Custom

Pick `Custom` to expose manual controls:

- `custom_codec`: libx264 / libx265 / prores_ks / ffv1
- `custom_container`: mp4 / mkv / mov
- `custom_rate_control`: crf / bitrate / lossless
- `custom_crf`: 0-51 (lower = better quality)
- `custom_bitrate_mbps`: 1-2000 (used when rate_control=bitrate)
- `custom_preset_speed`: medium / slow / slower / veryslow / placebo / fast / faster / veryfast / ultrafast
- `custom_all_intra`: true (GOP=1) / false
- `custom_pix_fmt`: yuv420p / yuv422p / yuv444p (8-bit) or yuv420p10le / yuv422p10le / yuv444p10le (10-bit)

## Bit depth fidelity

Most ComfyUI video nodes do `(frames * 255).astype(uint8)` before encoding — meaning a 10-bit pix_fmt receives only 8 bits of source data. This wastes file size and produces no quality improvement over 8-bit.

`SaveVideoHQ` detects 10-bit / 12-bit pix_fmts and switches to a 16-bit RGB pipeline:

```python
arr = (frames.cpu().numpy() * 65535.0).clip(0, 65535).astype("uint16")
frame = av.VideoFrame.from_ndarray(arr_per_frame, format="rgb48le")
```

The encoder receives 16 bits per channel (more than the diffusion model's BF16 effective precision) and quantizes down to the target 10-bit / 12-bit pix_fmt.

You see this in real output as: less banding in skies / smoke / gradients, cleaner color blending for translucent particles, fewer posterization steps in dark areas.

## Why intra-only for diffusion video

Inter-frame compression (P-frames, B-frames) predicts each frame from neighbours and stores residuals. For high-frequency content (particles, grain, sparkles) prediction fails frame-to-frame, residuals are large, and the codec spends bits poorly *or* the deblocking / SAO filters smear the failed predictions.

Particle-heavy diffusion output looks "fizzy", smeary, or temporally unstable when saved with default H.264 / H.265 inter-frame settings — even at very high bitrates. All-Intra removes this entire failure mode at the cost of ~3-5x larger files.

## H.264 vs H.265 for particles (short version)

- **H.265 inter-frame**: avoid for particles. Default deblock + SAO + motion compensation combine to smear high-frequency detail regardless of bitrate.
- **H.264 inter-frame**: better than H.265 for particles thanks to mature `psy-rd` defaults and gentler deblocking, but still inferior to All-I.
- **All-Intra (H.264 or H.265)**: roughly equivalent quality at same bitrate; H.265 produces ~30-40% smaller files at equivalent visual quality.

## Acknowledgements

Created by [@xergon](https://github.com/xergon) for high-quality video diffusion workflows.

## License

MIT — see [LICENSE](LICENSE).
