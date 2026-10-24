// The drawn layer: one soft circle per grid cell, overlapping into a field.
//
// The cells are H3 hexagons (see cells.ts), but they are *drawn* as circles of
// the same area, with a radial falloff and a radius slightly larger than the
// hexagon, so neighbouring cells bleed into one another and the result reads
// as a continuous mood field rather than a honeycomb. Overlap is the point: a
// city is a smear, not a tiling.
//
// The circles are interleaved into the basemap *beneath* its boundary, road
// and label layers, so country, state and city lines stay legible on top of
// the colour.

import { ScatterplotLayer } from '@deck.gl/layers';
import { MapboxOverlay } from '@deck.gl/mapbox';
import maplibregl from 'maplibre-gl';
import { oklabToRgba255 } from './colour';
import { alphaFor, diffRampLab, filterToBounds, type HexRow } from './compose';
import { firstOverlayBeforeId } from './map';
import { cellRadiusM } from './cells';

export {
  ResolutionPicker,
  alphaFor,
  composeDifference,
  composeFrame,
  resForZoom,
  rowStats,
  type HexRow,
} from './compose';
export { parentOf } from './cells';

/** How far a cell's circle spills past its own area. Above 1 the circles
 *  overlap, which is what makes the field continuous. A hexagon's corners
 *  reach about 1.1x its equal-area circle, so this is a real spill. */
export const OVERLAP = 1.45;

export function filterToViewport(rows: HexRow[], map: maplibregl.Map): HexRow[] {
  const b = map.getBounds();
  return filterToBounds(rows, {
    south: b.getSouth(),
    north: b.getNorth(),
    west: b.getWest(),
    east: b.getEast(),
  });
}

function diffColour(dE: number, alpha: number): [number, number, number, number] {
  const lab = diffRampLab(dE);
  return oklabToRgba255(lab.L, lab.a, lab.b, alpha);
}

/** A ScatterplotLayer whose discs fade out towards their rim instead of
 *  ending at a hard edge, so overlapping cells blend. deck.gl gives the
 *  fragment shader `geometry.uv` as the unit position within the disc. */
class SoftCircleLayer extends ScatterplotLayer<HexRow> {
  static layerName = 'SoftCircleLayer';

  getShaders() {
    const shaders = super.getShaders();
    return {
      ...shaders,
      inject: {
        ...(shaders.inject ?? {}),
        'fs:DECKGL_FILTER_COLOR': `
          float d = length(geometry.uv);
          // Flat core, long soft shoulder: the cell's own area stays its own
          // colour, and only the spill fades.
          color.a *= 1.0 - smoothstep(0.35, 1.0, d);
        `,
      },
    };
  }
}

export interface GridOptions {
  reducedMotion: boolean;
}

export class Grid {
  readonly overlay: MapboxOverlay;
  private rows: HexRow[] = [];
  private nRef = 1;
  private res = 0;
  private mode: 'colour' | 'difference' = 'colour';
  private version = 0;
  private frameId = '';

  constructor(
    private map: maplibregl.Map,
    private opts: GridOptions,
    private onHover: (row: HexRow | null, x: number, y: number) => void,
  ) {
    this.overlay = new MapboxOverlay({
      interleaved: true, // colour under the basemap's boundaries and labels
      layers: [],
    });
  }

  setData(
    rows: HexRow[],
    nRef: number,
    res: number,
    frameId: string,
    mode: 'colour' | 'difference',
  ): void {
    this.rows = rows;
    this.nRef = nRef;
    this.res = res;
    this.frameId = frameId;
    this.mode = mode;
    this.version += 1;
    this.render();
  }

  private render(): void {
    const nRef = this.nRef;
    const mode = this.mode;
    const radius = cellRadiusM(this.res) * OVERLAP;
    const layer = new SoftCircleLayer({
      id: 'listening',
      data: this.rows,
      getPosition: (d) => [d.lon, d.lat],
      getRadius: radius,
      radiusUnits: 'meters',
      // Keep cells visible when zoomed far out, where a level-0 cell is still
      // only a few pixels, without letting them swell into blobs up close.
      radiusMinPixels: 2,
      radiusMaxPixels: 2000,
      stroked: false,
      filled: true,
      antialiasing: true,
      getFillColor: (d) =>
        mode === 'difference'
          ? diffColour(d.dE ?? 0, alphaFor(d.n, nRef))
          : oklabToRgba255(d.L, d.a, d.b, alphaFor(d.n, nRef)),
      pickable: true,
      // Cross-fade frame to frame rather than flicker; disabled under
      // prefers-reduced-motion.
      transitions: this.opts.reducedMotion ? {} : { getFillColor: 220, getRadius: 220 },
      updateTriggers: { getFillColor: [this.version, this.frameId, mode] },
      onHover: ({ object, x, y }) => this.onHover((object as HexRow) ?? null, x, y),
      // `beforeId` is a @deck.gl/mapbox interleaved-overlay prop, so it is not
      // on the layer's own prop type; spreading keeps the rest type-checked.
      ...({ beforeId: firstOverlayBeforeId(this.map) } as { beforeId?: string }),
    });
    this.overlay.setProps({ layers: [layer] });
  }
}
