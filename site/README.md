# XKB / The Living Archive

Standalone Traditional Chinese introduction website. Vite, the unmodified Scrollcraft engine, Lucide icons, semantic HTML, and independently composited generated images. No backend credentials or private knowledge data are required.

```sh
npm install
npm run dev -- --port 4173
npm run build
```

Deploy `dist/` to a static host. The current local preview is http://127.0.0.1:4173.

## Experience

The photographic archive hero uses independent background, subject and foreground planes. A custom scroll-driven Evidence Thread assembles source fragments into an evidence card, passes a review gate, and delivers a sourced recall. The remaining page provides expandable evidence principles and a working setup selector with keyboard navigation and command copying. The example is labeled and does not pretend to be live retrieval.

Reduced motion presents the entire lifecycle in normal reading order. The site does not require WebGL or video decoding. Google Fonts provide Manrope and Noto Sans TC, with system fallbacks.

## Assets and design

Generated originals and alpha-preserving WebP delivery images: `public/assets/`.

Generation used the built-in image tool. Its exact model cannot be selected or verified. The explicitly requested GPT Image 2.5 connector rejected all attempts due to its service-plan requirement, before creating jobs; it did not generate these images.

Creative brief, prompts, provenance, and verification report: `../scrollcraft/builds/xkb-archive/`.

## Verification

```sh
npx playwright install chromium
node verify.mjs
```

Project checks cover 1440px, 390px and 360px viewports; asset loading; hero text visibility; differential parallax; assembly completion; setup tabs; clipboard; and reduced-motion accessibility. Screenshots are under `node_modules/.cache/archive-check/`.

The installed Scrollcraft `scripts/shoot.mjs` additionally samples each scroll act, measures text contrast and checks dead scroll. Final contact sheets are under `node_modules/.cache/archive-qa/final-desktop/` and `final-mobile/`. Real iPhone/Android hardware and production deployment were not tested.
