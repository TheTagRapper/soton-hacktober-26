// H3 cell geometry, client side. Mirrors pipeline/spatial.py.
//
// Cell ids arrive in the Arrow shards as the H3 index's unsigned 64-bit
// integer form; h3-js wants the hex string, which is just that integer in
// base 16.
//
// The cells are hexagons, but they are *drawn* as circles of the same area
// with a soft edge (see grid.ts), so neighbours overlap into a continuous
// field instead of a honeycomb.

import { cellToLatLng, cellToParent, getHexagonAreaAvg, getResolution } from 'h3-js';

export const BASE_RES = 8;

export const cellToHex = (cell: bigint): string => cell.toString(16);

export function cellCentre(cell: bigint): [number, number] {
  const [lat, lng] = cellToLatLng(cellToHex(cell));
  return [lat, lng];
}

export function parentOf(cellHex: string, res: number): string {
  return getResolution(cellHex) <= res ? cellHex : cellToParent(cellHex, res);
}

export function cellAreaM2(res: number): number {
  return getHexagonAreaAvg(res, 'm2');
}

/** Radius of a circle with the same area as an average cell at this
 *  resolution, so the painted area matches the area the cell represents. */
export function cellRadiusM(res: number): number {
  return Math.sqrt(cellAreaM2(res) / Math.PI);
}

// Spec section 7.2, LOCKED.
export const ZOOM_TO_RES: number[] = [
  // z: 0  1  2  3  4  5  6  7  8  9 10 11 12 13 14 15 16+
  1, 1, 1, 2, 2, 3, 4, 5, 5, 6, 6, 7, 7, 8, 8, 8, 8,
];

export const resForZoom = (z: number): number =>
  ZOOM_TO_RES[Math.max(0, Math.min(16, Math.floor(z)))];
