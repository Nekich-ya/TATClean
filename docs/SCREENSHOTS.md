# Public screenshot and UI-demo guide

## Privacy gate first

Existing `artifacts/ui-*.png` are **development captures, not approved marketing assets**. They can contain real adapter addresses, network names, process lists, account/file paths, machine details or log output. Do not stage/upload them automatically.

Prefer a clean Windows VM or a demo profile with synthetic, clearly labeled data. Before publication:

- Hide/redact adapter IP/MAC addresses, GUIDs and private VPN/network names.
- Hide account names, user/profile paths, machine names and private process/application details.
- Clear/hide the visible activity log and review every tooltip, dialog and diagnostic output.
- Never include tokens, signing certificates/private keys, baseline SQLite files or real credential/configuration values.
- Use opaque redaction or re-capture with demo data, not weak blur. Review the **exported** image/video, not just the editor preview.
- Do not fabricate successful restore-point creation, security checks or measured FPS/input-lag gains. Label mock/demo data where used.

## Capture style

1. Use a consistent app version, window size and Windows scale factor. Capture roughly **1440–1600px wide** at its native aspect ratio; do not stretch text into 16:9.
2. Keep the app's `#000000` background, charcoal cards and silver/white accents. Remove desktop/taskbar clutter where practical.
3. Retain readable labels and real controls. Add a subtle border/shadow only in the surrounding presentation frame; don't obscure status text with excessive glow.
4. Capture Dashboard, Gaming & Latency, Deep Cleaner and RAM & Services. Include a real preview state or a clearly labeled demo when showcasing cleanup.
5. Export crisp PNG (or WebP if appropriate) with sensible compression. Avoid large GIFs for static UI; use a short video link or a small preview GIF for motion.

## File placement

```text
artifacts/                      Private development captures; ignored
docs/media/raw/                 Private working captures; ignored
docs/media/dashboard.png        Reviewed public hero image
docs/media/gaming-and-latency.png
docs/media/deep-cleaner.png
docs/media/ram-and-services.png
docs/media/overview.gif          Optional small, reviewed motion teaser
```

Only add images after manual privacy/accuracy review. The README already contains a commented gallery block; uncomment it once the exact files exist. No missing-image placeholders are shown in the public README.

Recommended placement:

1. **Hero:** Dashboard immediately after the intro/navigation.
2. **Expandable gallery:** Gaming, Cleaner and RAM views in a `<details>` block.
3. **Feature proof:** one concise caption per image (for example, “Only selected tweaks are applied; originals are saved first”).
4. **Demo:** a thumbnail linking to a hosted video near installation—not a huge video binary in Git.

Example after the real images are added:

```html
<p align="center">
  <img src="docs/media/dashboard.png" alt="TAT Clean dashboard" width="100%">
</p>
```

Stage only reviewed files:

```powershell
git add -- docs/media/dashboard.png docs/media/gaming-and-latency.png docs/media/deep-cleaner.png docs/media/ram-and-services.png README.md
git diff --cached --name-status
.\.venv\Scripts\python.exe tools\check_publication.py
```

After reviewing every exported media file, affirm that separate manual gate explicitly:

```powershell
.\.venv\Scripts\python.exe tools\check_publication.py --media-reviewed
```

Text-based secret scanners do not inspect image pixels. View each staged/public asset yourself, and never commit `docs/media/raw/` or copy the contents of `artifacts/` wholesale.
