# Brand assets

Home Assistant (>= 2026.3) serves a custom integration's brand images from this
`brand/` directory (via the local brands-proxy API), taking priority over the
`home-assistant/brands` CDN — no PR to that repository is needed.

Files here — the maintainer's own SG Smart 3.0 icon (a white house with "3.0"
on red), supplied as a 1024 px image and scaled down with its white corners
made transparent so it sits cleanly on a dark theme:

- `icon.png` (256×256) / `icon@2x.png` (512×512) — the square integration icon.
- `logo.png` / `logo@2x.png` — the logo shown on the integration page (a copy of
  the square icon; the project has no separate wordmark).

Until 0.12.0 these were an original mesh-of-lights motif drawn by
`scripts/make_icon.py`. That script was removed with the new icon: running it
would have silently overwritten the chosen one.
