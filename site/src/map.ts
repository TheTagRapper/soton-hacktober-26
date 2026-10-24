// MapLibre + OpenFreeMap setup (spec section 10.1).
//
// Basemap is OpenFreeMap positron: free, no API key, no registration, no usage
// limit, OpenStreetMap data, and near-monochrome. The map must recede -- the
// colour field is the subject. A colourful basemap under a colour
// visualisation would make every hex a lie.

import maplibregl from 'maplibre-gl';
import type { Basemap, Store } from './state';

// Two near-monochrome OpenFreeMap styles: free, no API key, no registration,
// no usage limit, OpenStreetMap data. The basemap must recede -- the colour
// field is the subject -- so neither carries colour of its own, and both keep
// their boundaries, roads and labels drawn *over* the colour.
//
// Dark is the default: thirteen saturated hues read far better against it than
// against white, and the map this palette comes from is itself on black. The
// light style is one click away for anyone who wants to read the geography
// first.
export const STYLES: Record<Basemap, string> = {
  dark: 'https://tiles.openfreemap.org/styles/dark',
  light: 'https://tiles.openfreemap.org/styles/positron',
};

export const STYLE_URL = STYLES.dark;

export function createMap(container: HTMLElement, store: Store): maplibregl.Map {
  const map = new maplibregl.Map({
    container,
    style: STYLES[store.state.base],
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

/** Where the colour field is inserted into the basemap's layer stack.
 *
 * Everything that carries geographic *information* -- country and state
 * boundaries, coastlines, roads, and every label from country down to town --
 * must draw on top of the colour, or the map stops being a map. So the overlay
 * goes immediately below the first boundary or label layer, and above the
 * landcover and water fills that are only there to be recognisable. */
export function firstOverlayBeforeId(map: maplibregl.Map): string | undefined {
  const layers = map.getStyle()?.layers ?? [];
  const wanted = /boundar|admin|place|label|poi|road|street|transport|bridge|tunnel/i;
  const byId = layers.find((l) => wanted.test(l.id));
  if (byId) return byId.id;
  return layers.find((l) => l.type === 'symbol' || l.type === 'line')?.id;
}
