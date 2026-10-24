// MapLibre + OpenFreeMap setup (spec section 10.1).
//
// Basemap is OpenFreeMap positron: free, no API key, no registration, no usage
// limit, OpenStreetMap data, and near-monochrome. The map must recede -- the
// colour field is the subject. A colourful basemap under a colour
// visualisation would make every hex a lie.

import maplibregl from 'maplibre-gl';
import type { Store } from './state';

export const STYLE_URL = 'https://tiles.openfreemap.org/styles/positron';

export function createMap(container: HTMLElement, store: Store): maplibregl.Map {
  const map = new maplibregl.Map({
    container,
    style: STYLE_URL,
    center: [store.state.lon, store.state.lat],
    zoom: store.state.z,
    minZoom: 0,
    maxZoom: 16,
    attributionControl: { compact: true },
    dragRotate: false,
    pitchWithRotate: false,
    renderWorldCopies: true,
  });
  map.addControl(new maplibregl.NavigationControl({ showCompass: false }), 'bottom-right');
  map.touchZoomRotate.disableRotation();

  // Camera -> state. moveend only: the hash is a shareable view, not a
  // per-frame animation log.
  map.on('moveend', () => {
    const c = map.getCenter();
    store.patch({ z: map.getZoom(), lat: c.lat, lon: c.lng });
  });
  return map;
}

/** The basemap's first symbol layer: deck.gl draws beneath it so labels stay readable. */
export function firstLabelLayerId(map: maplibregl.Map): string | undefined {
  const layers = map.getStyle()?.layers ?? [];
  return layers.find((l) => l.type === 'symbol')?.id;
}
