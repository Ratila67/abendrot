"""Archive chaque jour la prévision de ce soir (J0) et de demain (J1) pour chaque spot.

C'est la moitié « features » du dataset : ce que l'app savait AVANT le coucher.
Usage : python -m truth.snapshot   (SUNSETHUE_KEY optionnelle)
"""
import datetime as dt

from . import weather
from .common import DATA, FORECAST_FIELDS, TZ, append_csv, env, read_csv, sunset_spots
from .sun import effective_sunset


def main(now=None):
    now = now or dt.datetime.now(dt.timezone.utc)
    today = now.astimezone(TZ).date()
    spots = sunset_spots()
    path = DATA / "forecasts.csv"
    seen = {(r["snapshot_date"], r["date"], r["spot_id"]) for r in read_csv(path)}
    series, plain = weather.forecast([(s["lat"], s["lon"], s["alt"]) for s in spots])
    key = env("SUNSETHUE_KEY")
    rows = []
    for s, ser in zip(spots, series):
        aod = weather.aerosols(s["lat"], s["lon"])
        for lead in (0, 1):
            day = today + dt.timedelta(days=lead)
            if (today.isoformat(), day.isoformat(), s["spot_id"]) in seen:
                continue
            sset = effective_sunset(day, s["lat"], s["lon"], s["alt"], s["horizon"], TZ)
            i = weather.hour_index(ser, sset)
            if i < 0:
                continue
            g = lambda o, k: (o.get(k) or [None] * (i + 1))[i]
            heur, inv = weather.heuristic(ser, plain, i, s["alt"])
            t850, pt = g(ser, "temperature_850hPa"), g(plain, "temperature_2m")
            ai = weather.hour_index(aod, sset) if aod else -1
            vis = g(ser, "visibility")
            rows.append({
                "snapshot_date": today.isoformat(), "date": day.isoformat(), "spot_id": s["spot_id"], "lead_day": lead,
                "sunset_utc": sset.isoformat(timespec="minutes"), "model": ser["model"][i],
                "cloud_low": g(ser, "cloud_cover_low"), "cloud_mid": g(ser, "cloud_cover_mid"),
                "cloud_high": g(ser, "cloud_cover_high"), "precip": g(ser, "precipitation"),
                "visibility_km": None if vis is None else round(vis / 1000, 1), "gust": g(ser, "wind_gusts_10m"),
                "t850": t850, "plain_t2m": pt, "plain_low": g(plain, "cloud_cover_low"),
                "t850_minus_plain": None if t850 is None or pt is None else round(t850 - pt, 1),
                "inversion": int(inv), "heuristic": heur,
                "aod": aod["aerosol_optical_depth"][ai] if ai >= 0 else None,
                "sunsethue": weather.sunsethue(s["lat"], s["lon"], day.isoformat(), key), "alt": s["alt"]})
    append_csv(path, rows, FORECAST_FIELDS)
    print(f"{len(rows)} prévision(s) archivée(s) pour {today}")


if __name__ == "__main__":
    main()
