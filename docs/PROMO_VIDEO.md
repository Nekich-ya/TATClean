# Software promo videos: tools and production plan

Recommendations checked against official product pages in November 2026. Product plans, AI credits and export/licensing rules change—verify them before purchasing or uploading assets.

## Choose a stack by job

| Tool | Best fit for this project | Main trade-off |
| --- | --- | --- |
| **Adobe After Effects + Premiere** | Frame-accurate UI compositing, kinetic typography, premium tech transitions and a large plugin/template ecosystem; Premiere handles the final edit/audio | Paid subscription and a real learning curve; After Effects is a compositor/motion tool, not the ideal long-form editor |
| **DaVinci Resolve + Fusion** | Best value all-in-one editing/color/audio/node-based motion pipeline; strong free version, additional Studio/AI tools | Fusion's node workflow takes practice; some features are Studio-only |
| **Jitter + Figma** | Fast polished UI/product animations with editable imported layers; Jitter AI can create/refine editable animations/effects | Cloud workflow; plan/export/AI-credit limits apply. Image-to-video is a separate flattened-generation route, not the same as editable motion |
| **Cavalry** | Procedural 2D systems, kinetic type, looping tech patterns and repeatable variants | Not a timeline video editor or generative-AI renderer; currently free for individuals via a Canva account |
| **Blender** | Free 3D glass/device scenes, camera moves, lighting and geometry-node motion | More setup/rendering skill required; excessive 3D effects can distract from readable UI |
| **Canva Magic Animate / AI video tools** | Fast beginner-friendly social trailers, automatic motion/transition suggestions, resizes and captions | Less precise than pro layer/node tools; premium/export/asset licensing can apply |
| **Adobe Firefly + Adobe editing tools** | AI backgrounds/concept shots and assisted compositing integrated with a professional workflow | Verify commercial-use/model terms; generated shots still need editorial/color finishing |
| **Runway** | AI-generated abstract B-roll, scene/background transformations and style exploration | Not reliable for exact UI labels, real measurements or faithful product behavior; keep generated content separate from proof/demo footage |

**My picks:** premium: OBS → After Effects → Premiere. Best-value: OBS → Cavalry/Fusion → Resolve. Fastest UI-focused: Figma/Jitter (editable layers) → Resolve or Premiere. Add Runway/Firefly only where generative footage genuinely improves the story.

Use [OBS Studio](https://obsproject.com/) for Windows screen capture. Camtasia is an easier paid screen-demo option. Do not choose a macOS-only recorder as the primary Windows workflow.

## Keep UI truthful and crisp

Record the real app in a clean VM/demo account and follow [SCREENSHOTS.md](SCREENSHOTS.md). Never send unredacted captures, IP addresses, account paths, credentials, private logs or baseline files to a cloud AI tool.

For screen text/buttons, use native footage or manually constructed editable layers in Figma/Jitter/After Effects. Generative video can morph letters, invent controls or imply actions the software never performs. It is useful for abstract black/silver backgrounds and transitions—not evidence of successful optimization or a fabricated FPS result.

Before commercial use, check music/SFX/font/template/stock licenses, AI model/platform output terms, client consent and any third-party trademarks. Firefly access does not automatically give every partner model identical legal terms. Keep source project files and licenses privately archived; don't commit editing caches or large master files to this repository.

## 30–35 second storyboard

| Time | Shot | Honest on-screen copy |
| --- | --- | --- |
| 0–3s | Black/silver logo reveal and one clean sound cue | **Inspect. Apply. Revert.** |
| 3–9s | Native Dashboard reveal; subtle crop/zoom; Optimize Now | **Gaming controls—with a rollback plan** |
| 9–16s | Drive selector → read-only scan → reviewed candidates | **Clean selected disposable paths** |
| 16–23s | Granular gaming cards and checkpoint gate | **Only the settings you choose** |
| 23–28s | A genuine revert in an approved VM, or a clearly labeled UI walkthrough | **Originals saved before changes** |
| 28–35s | Final app shot and real repository/release URL once available | **Explore TAT Clean on GitHub** |

Do not claim “0.5 ms input lag,” “guaranteed lower ping,” “memory compression boost,” or a measured FPS gain: timer resolution, working-set trimming and application latency are different things. A successful Windows restore point must be genuinely verified, not edited into a false demo.

## Motion direction and delivery

- Palette: `#000000`, charcoal glass, `#FFFFFF`/`#8E8E93`; use silver accents, not neon gamer clutter.
- Use 150–250ms eased card reveals, restrained parallax, 2–4-word kinetic type beats and clean masks. Keep active control/status text readable.
- Avoid shaking/glitching the UI, repeated whip transitions or bloom that washes out dark cards.
- Capture at a readable native resolution; compose a 1920×1080 master and an independently reframed 1080×1920 social cut. Never stretch the app window.
- Use consistent 30/60 fps timelines (60 fps is useful for smooth UI demonstrations); export a high-quality master plus H.264/H.265 delivery versions with captions and a clean audio mix.
- Host the full video externally or as a release asset. Put a small reviewed thumbnail/preview in `docs/media/`, not a large video binary in Git.

## Official product references

- [After Effects](https://www.adobe.com/products/aftereffects.html)
- [DaVinci Resolve / Fusion](https://www.blackmagicdesign.com/products/davinciresolve)
- [Jitter](https://jitter.video/product) and [editable AI animations](https://jitter.video/changelog/2026-10-01-jitter-ai-opus-5-5/)
- [Cavalry](https://cavalry.scenegroup.co/)
- [Blender animation](https://www.blender.org/features/animation)
- [Canva Magic Studio](https://www.canva.com/magic/)
- [Adobe Firefly](https://www.adobe.com/products/firefly.html)
- [Runway video generation](https://runwayml.com/product/ai-video-generator)
