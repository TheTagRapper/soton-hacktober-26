// Run the real site/src/colour.ts against the shared fixture and print what it
// computes, so test_oklab.py can compare the two implementations of the Cowen
// blend. Node 24 strips TypeScript types natively, so this needs no build step.
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

const here = path.dirname(fileURLToPath(import.meta.url));
const mod = await import(path.join(here, '..', 'site', 'src', 'colour.ts'));
const vectors = JSON.parse(
  readFileSync(path.join(here, 'fixtures', 'oklab_vectors.json'), 'utf8'),
);

const out = vectors.map((v) => {
  const lab = mod.blendEmotions(v.in);
  if (lab === null) return { oklch: null, oklab: null, hex: null };
  const [L, C, h] = mod.oklabToOklch(lab.L, lab.a, lab.b);
  return { oklch: { L, C, h }, oklab: lab, hex: mod.oklabToHex(lab.L, lab.a, lab.b) };
});
process.stdout.write(JSON.stringify(out));
