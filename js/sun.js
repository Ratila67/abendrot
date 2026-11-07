// Position et heures du soleil (algorithme SunCalc, précision ~1 min)
const rad = Math.PI / 180, dayMs = 864e5, J1970 = 2440588, J2000 = 2451545, J0 = 0.0009, E = rad * 23.4397;
const toDays = d => d.valueOf() / dayMs - 0.5 + J1970 - J2000;

function ecliptic(ds) {
  const M = rad * (357.5291 + 0.98560028 * ds);
  const C = rad * (1.9148 * Math.sin(M) + 0.02 * Math.sin(2 * M) + 0.0003 * Math.sin(3 * M));
  const L = M + C + rad * 102.9372 + Math.PI;
  return { M, L, dec: Math.asin(Math.sin(E) * Math.sin(L)) };
}

/** Heure où le soleil descend sous la hauteur h (degrés) le jour de `date`. */
export function sunTime(date, lat, lng, h) {
  const lw = rad * -lng, phi = rad * lat, d = toDays(date);
  const n = Math.round(d - J0 - lw / (2 * Math.PI)), ds = J0 + lw / (2 * Math.PI) + n;
  const { M, L, dec } = ecliptic(ds);
  const cw = (Math.sin(h * rad) - Math.sin(phi) * Math.sin(dec)) / (Math.cos(phi) * Math.cos(dec));
  if (cw < -1 || cw > 1) return null;
  const a = J0 + (Math.acos(cw) + lw) / (2 * Math.PI) + n;
  return new Date((J2000 + a + 0.0053 * Math.sin(M) - 0.0069 * Math.sin(2 * L) + 0.5 - J1970) * dayMs);
}

/** Azimut (degrés depuis le nord, sens horaire) et hauteur du soleil à un instant donné. */
export function sunPosition(date, lat, lng) {
  const lw = rad * -lng, phi = rad * lat, d = toDays(date);
  const { L, dec } = ecliptic(d);
  const ra = Math.atan2(Math.sin(L) * Math.cos(E), Math.cos(L));
  const H = rad * (280.16 + 360.9856235 * d) - lw - ra;
  const az = Math.atan2(Math.sin(H), Math.cos(H) * Math.sin(phi) - Math.tan(dec) * Math.cos(phi));
  const alt = Math.asin(Math.sin(phi) * Math.sin(dec) + Math.cos(phi) * Math.cos(dec) * Math.cos(H));
  return { azimuth: (az / rad + 180 + 360) % 360, altitude: alt / rad };
}

/** Coucher « effectif » : altitude de l'observateur (dip) + masque de relief à l'ouest. */
export function effectiveSunset(date, lat, lng, altM = 0, horizonDeg = 0) {
  const dip = 2.076 * Math.sqrt(Math.max(altM, 0)) / 60;
  return sunTime(date, lat, lng, -0.833 - dip + horizonDeg);
}
