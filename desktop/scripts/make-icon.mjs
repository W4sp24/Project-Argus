/**
 * build/icon.svg -> build/icon.ico (+ icon.png for the splash).
 *
 * sharp renders SVG through librsvg, so the radial gradients survive; png-to-ico
 * packs all seven sizes into one multi-size .ico (~360KB, dominated by the
 * 256px entry — the soft glow doesn't compress well).
 *
 * Deliberately not ImageMagick: it isn't on windows-latest by default, and its
 * SVG renderer drops gradients without an Inkscape delegate. Deliberately not
 * svgexport/svg2img: they pull Puppeteer, i.e. a second Chromium download.
 *
 * Wired as a prebuild step so the .ico is always regenerated and never goes
 * stale in git.
 */

import fs from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import sharp from "sharp";
import pngToIco from "png-to-ico";

const here = path.dirname(fileURLToPath(import.meta.url));
const buildDir = path.join(here, "..", "build");

// 16-48 are used in the taskbar, Explorer and Alt-Tab; 256 is the large icon
// view and what NSIS shows in the installer.
const SIZES = [16, 24, 32, 48, 64, 128, 256];

const svg = await fs.readFile(path.join(buildDir, "icon.svg"));

const pngs = await Promise.all(
  SIZES.map((size) =>
    sharp(svg, { density: 384 })
      .resize(size, size, { fit: "contain", background: { r: 0, g: 0, b: 0, alpha: 0 } })
      .png()
      .toBuffer(),
  ),
);

await fs.writeFile(path.join(buildDir, "icon.ico"), await pngToIco(pngs));
await fs.writeFile(path.join(buildDir, "icon.png"), pngs.at(-1));

const { size } = await fs.stat(path.join(buildDir, "icon.ico"));
console.log(`icon.ico written (${SIZES.join(", ")}px) - ${(size / 1024).toFixed(1)} KB`);
