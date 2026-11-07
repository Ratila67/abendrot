"""Portage Python de js/sun.js (SunCalc) : mêmes heures que l'app."""
import datetime as dt
import math

RAD = math.pi / 180
J1970, J2000, J0, E = 2440588, 2451545, 0.0009, RAD * 23.4397


def _days(t):
    return t.timestamp() / 86400 - 0.5 + J1970 - J2000


def sun_time(when, lat, lng, h):
    lw, phi, d = RAD * -lng, RAD * lat, _days(when)
    n = round(d - J0 - lw / (2 * math.pi))
    ds = J0 + lw / (2 * math.pi) + n
    M = RAD * (357.5291 + 0.98560028 * ds)
    C = RAD * (1.9148 * math.sin(M) + 0.02 * math.sin(2 * M) + 0.0003 * math.sin(3 * M))
    L = M + C + RAD * 102.9372 + math.pi
    dec = math.asin(math.sin(E) * math.sin(L))
    cw = (math.sin(h * RAD) - math.sin(phi) * math.sin(dec)) / (math.cos(phi) * math.cos(dec))
    if cw < -1 or cw > 1:
        return None
    a = J0 + (math.acos(cw) + lw) / (2 * math.pi) + n
    j = J2000 + a + 0.0053 * math.sin(M) - 0.0069 * math.sin(2 * L)
    return dt.datetime.fromtimestamp((j + 0.5 - J1970) * 86400, tz=dt.timezone.utc)


def effective_sunset(day, lat, lon, alt, horizon, tz):
    """Coucher au spot : altitude de l'observateur + masque de relief, comme dans l'app."""
    noon = dt.datetime(day.year, day.month, day.day, 12, tzinfo=tz)
    dip = 2.076 * math.sqrt(max(alt, 0)) / 60
    return sun_time(noon, lat, lon, -0.833 - dip + (horizon or 0))
