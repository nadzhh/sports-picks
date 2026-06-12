"""
foot_match_context.py - Enrichit un match foot avec contexte étendu :

  - 🌡️ Météo (open-meteo.com, gratuit, sans clé)
  - 🏟️ Stade (table hardcodée pour CdM 2026, altitude, capacité, climatisé)
  - 📊 L5 détaillé (calculé depuis l'historique fixtures du match)
  - 💡 Texte de contexte automatique (template assemblé)

Utilisé pour la section "🔍 ANALYSE APPROFONDIE" de la carte foot, surtout
pour la Coupe du Monde 2026.

Tout en cache disque (météo 6h, stade infini) pour minimiser les calls.
"""
import json, urllib.request, urllib.error
from datetime import datetime, timedelta, timezone
from pathlib import Path

DATA       = Path("data")
DATA.mkdir(exist_ok=True)
STADIUMS   = DATA / "stadiums_wc2026.json"
WEATHER_CACHE = DATA / "cache_weather.json"
FOTMOB_VENUE_CACHE = DATA / "cache_fotmob_venue.json"

WEATHER_TTL = 6 * 3600
FOTMOB_VENUE_TTL = 30 * 24 * 3600  # le stade ne change pas

UA = "Mozilla/5.0 (sport-picks/1.0)"


def _load_fotmob_venue_cache():
    if not FOTMOB_VENUE_CACHE.exists(): return {}
    try:
        return json.loads(FOTMOB_VENUE_CACHE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_fotmob_venue_cache(data):
    try:
        FOTMOB_VENUE_CACHE.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


def fetch_fotmob_venue(match_id):
    """Récupère venue + lat/lon + capacité depuis FotMob matchDetails."""
    if not match_id: return None
    cache = _load_fotmob_venue_cache()
    key = str(match_id)
    import time as _t
    entry = cache.get(key)
    if entry and (_t.time() - entry.get("_ts", 0)) < FOTMOB_VENUE_TTL:
        return entry.get("data")
    url = f"https://www.fotmob.com/api/data/matchDetails?matchId={match_id}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 Chrome/131.0", "Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=15) as r:
            data = json.loads(r.read())
    except Exception as e:
        print(f"  [fotmob venue err] match {match_id}: {e}")
        return None
    try:
        s = data["content"]["matchFacts"]["infoBox"].get("Stadium") or {}
    except Exception:
        s = {}
    if not s.get("name"): return None
    out = {
        "name":     s.get("name"),
        "city":     s.get("city"),
        "country":  s.get("country"),
        "lat":      s.get("lat"),
        "lon":      s.get("long") or s.get("lon"),
        "capacity": s.get("capacity"),
        "surface":  s.get("surface"),
    }
    cache[key] = {"_ts": int(_t.time()), "data": out}
    _save_fotmob_venue_cache(cache)
    return out


def _load_stadiums():
    if not STADIUMS.exists(): return {}
    try:
        return json.loads(STADIUMS.read_text(encoding="utf-8")).get("stadiums", {})
    except Exception:
        return {}


def _load_weather_cache():
    if not WEATHER_CACHE.exists(): return {}
    try:
        return json.loads(WEATHER_CACHE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_weather_cache(data):
    try:
        WEATHER_CACHE.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


def lookup_stadium(venue_name):
    """Retourne le dict stade pour un nom de venue (matching tolérant)."""
    if not venue_name: return None
    stadiums = _load_stadiums()
    name_lc = venue_name.lower()
    # Match exact d'abord
    for sname, s in stadiums.items():
        if sname.lower() == name_lc:
            return {"name": sname, **s}
    # Match partiel (mot-clé)
    for sname, s in stadiums.items():
        snorm = sname.lower().replace("'", "").replace("&", "")
        vnorm = name_lc.replace("'", "").replace("&", "")
        # Si le nom du stade complet apparaît dans la venue (ou inverse)
        if snorm in vnorm or vnorm in snorm:
            return {"name": sname, **s}
        # Match sur ville
        if s.get("city","").lower().split()[0] in vnorm:
            # Risque de faux positif (Boston Garden vs Gillette Boston)
            # Donc on garde plutôt pour les villes uniques (Mexico, Vancouver)
            if s.get("city","").lower() in vnorm:
                return {"name": sname, **s}
    return None


def fetch_weather(lat, lon, when_iso):
    """Récupère la météo pour un point + date donnée (open-meteo.com gratuit).

    when_iso : ISO date du match. On récupère le jour entier (max/min/precipitation).

    Retourne dict { temp_max, temp_min, humidity, precipitation_sum, wind_max,
    weather_code, summary_fr } ou None si echec.
    """
    if not (lat and lon and when_iso): return None
    try:
        dt = datetime.fromisoformat(when_iso.replace("Z","+00:00"))
        date_str = dt.strftime("%Y-%m-%d")
    except Exception:
        return None
    # Cache key
    cache_key = f"{lat:.3f}|{lon:.3f}|{date_str}"
    cache = _load_weather_cache()
    entry = cache.get(cache_key)
    import time as _t
    if entry and (_t.time() - entry.get("_ts", 0)) < WEATHER_TTL:
        return entry.get("data")
    # API : open-meteo forecast (gratuit, sans clé, jusqu'à 16 jours)
    url = (
        f"https://api.open-meteo.com/v1/forecast"
        f"?latitude={lat}&longitude={lon}"
        f"&daily=temperature_2m_max,temperature_2m_min,precipitation_sum,wind_speed_10m_max,weather_code"
        f"&hourly=relative_humidity_2m"
        f"&timezone=auto"
        f"&start_date={date_str}&end_date={date_str}"
    )
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=15) as r:
            data = json.loads(r.read())
    except Exception as e:
        print(f"  [weather err] {lat:.2f},{lon:.2f} {date_str}: {e}")
        return None
    daily = data.get("daily", {})
    hourly = data.get("hourly", {})
    if not daily.get("temperature_2m_max"):
        return None
    tmax = (daily.get("temperature_2m_max") or [None])[0]
    tmin = (daily.get("temperature_2m_min") or [None])[0]
    psum = (daily.get("precipitation_sum") or [None])[0]
    wmax = (daily.get("wind_speed_10m_max") or [None])[0]
    wcode = (daily.get("weather_code") or [None])[0]
    # Humidité : moyenne des heures de l'après-midi (12h-20h)
    h_times = hourly.get("time") or []
    h_hum   = hourly.get("relative_humidity_2m") or []
    afternoon_hums = []
    for i, t in enumerate(h_times):
        try:
            hour = int(t.split("T")[1].split(":")[0])
            if 12 <= hour <= 20:
                afternoon_hums.append(h_hum[i])
        except Exception:
            continue
    humidity = round(sum(afternoon_hums) / len(afternoon_hums)) if afternoon_hums else None
    out = {
        "temp_max": tmax,
        "temp_min": tmin,
        "humidity": humidity,
        "precipitation_sum_mm": psum,
        "wind_max_kmh": wmax,
        "weather_code": wcode,
        "summary_fr": _summarize_weather(tmax, tmin, humidity, psum, wcode),
    }
    cache[cache_key] = {"_ts": int(_t.time()), "data": out}
    _save_weather_cache(cache)
    return out


# WMO weather codes -> emoji + label FR
WCODE = {
    0: ("☀️", "ciel dégagé"),
    1: ("🌤️", "principalement clair"),
    2: ("⛅", "partiellement nuageux"),
    3: ("☁️", "couvert"),
    45: ("🌫️", "brouillard"),
    48: ("🌫️", "brouillard givrant"),
    51: ("🌦️", "bruine légère"),
    53: ("🌦️", "bruine modérée"),
    55: ("🌧️", "bruine dense"),
    61: ("🌦️", "pluie légère"),
    63: ("🌧️", "pluie modérée"),
    65: ("🌧️", "pluie forte"),
    71: ("🌨️", "neige légère"),
    73: ("🌨️", "neige modérée"),
    75: ("❄️", "neige forte"),
    80: ("🌦️", "averses légères"),
    81: ("🌦️", "averses modérées"),
    82: ("⛈️", "averses violentes"),
    95: ("⛈️", "orage"),
    96: ("⛈️", "orage + grêle légère"),
    99: ("⛈️", "orage + grêle forte"),
}


def _summarize_weather(tmax, tmin, hum, psum, wcode):
    icon, label = WCODE.get(wcode or -1, ("🌤️", "conditions variables"))
    parts = [f"{icon} {label}"]
    if tmax is not None:
        if tmin is not None and tmin < tmax - 3:
            parts.append(f"{int(tmin)}–{int(tmax)}°C")
        else:
            parts.append(f"{int(tmax)}°C")
    if hum is not None:
        parts.append(f"humidité {hum}%")
    if psum is not None and psum >= 1:
        parts.append(f"précipitations {psum:.1f}mm")
    return " · ".join(parts)


def build_context_text(stadium, weather, match):
    """Assemble un paragraphe contexte automatique à partir des données."""
    parts = []
    if weather:
        parts.append(f"Conditions : {weather.get('summary_fr','?')}.")
    if stadium:
        bits = [f"Stade : {stadium.get('name','?')}"]
        if stadium.get("altitude_m"):
            bits.append(f"altitude {stadium['altitude_m']}m")
        if stadium.get("capacity"):
            bits.append(f"{stadium['capacity']:,} places".replace(",", " "))
        if stadium.get("climatized") is True:
            bits.append("climatisé")
        elif stadium.get("type") == "indoor":
            bits.append("indoor")
        else:
            bits.append("plein air")
        parts.append(" · ".join(bits) + ".")
        if stadium.get("context_fr"):
            parts.append(stadium["context_fr"])
    # Impact altitude
    if stadium and stadium.get("altitude_m", 0) >= 1500:
        parts.append(f"⚠️ Altitude {stadium['altitude_m']}m : impact endurance significatif pour l'équipe non-acclimatée.")
    # Impact pluie
    if weather and (weather.get("precipitation_sum_mm") or 0) >= 5:
        parts.append("⚠️ Pluies prévues : terrain potentiellement lourd, jeu direct privilégié, plus d'erreurs techniques.")
    if weather and (weather.get("temp_max") or 0) >= 32 and stadium and not stadium.get("climatized"):
        parts.append("⚠️ Forte chaleur (>32°C) en plein air : impact endurance, plus de pauses hydratation.")
    if weather and (weather.get("wind_max_kmh") or 0) >= 30:
        parts.append("⚠️ Vent fort prévu : jeu aérien moins fiable, balles longues imprévisibles.")
    return " ".join(parts)


def enrich_match(match):
    """Ajoute les champs context.weather, context.stadium, context.text au match.

    Strategy : on essaie d'abord FotMob matchDetails (donne venue + lat/lon),
    puis on fusionne avec notre table hardcodée (qui ajoute altitude, type
    indoor/outdoor, climatized, context_fr).

    Modifie le dict en place et le retourne.
    """
    # 1) FotMob venue (lat/lon précis)
    mid = match.get("id") or match.get("match_id")
    fm_venue = fetch_fotmob_venue(mid) if mid else None
    # 2) Lookup hardcodé (altitude/climatized/context_fr) — fallback ou enrichissement
    venue_name = (fm_venue or {}).get("name") or match.get("venue") or ""
    hc_stadium = lookup_stadium(venue_name) if venue_name else None
    # Match aussi sur ville si FotMob "Stadium" name ne matche pas notre table
    if not hc_stadium and fm_venue and fm_venue.get("city"):
        for sname, s in _load_stadiums().items():
            if s.get("city","").lower().split()[0] in (fm_venue["city"] or "").lower():
                hc_stadium = {"name": sname, **s}
                break
    # Fusion : FotMob comme base + enrichissement de notre table
    stadium = None
    if fm_venue or hc_stadium:
        stadium = {**(fm_venue or {}), **{k:v for k,v in (hc_stadium or {}).items() if v is not None}}
    # Fallback : on a un FotMob venue mais aucun match dans la hardcoded table
    # On reste avec ce qu'on a (lat/lon suffisent pour la météo).
    weather = None
    lat = (stadium or {}).get("lat")
    lon = (stadium or {}).get("lon")
    if lat and lon:
        start_iso = match.get("start_iso") or match.get("date") or ""
        if not start_iso and match.get("start_ts"):
            try:
                start_iso = datetime.fromtimestamp(match["start_ts"], tz=timezone.utc).isoformat()
            except Exception:
                pass
        if start_iso:
            weather = fetch_weather(lat, lon, start_iso)
    if not (stadium or weather):
        return match
    context = {
        "stadium": stadium,
        "weather": weather,
        "text":    build_context_text(stadium, weather, match),
    }
    match["context"] = context
    return match


if __name__ == "__main__":
    # Smoke test : Azteca le 11 juin 2026
    s = lookup_stadium("Estadio Azteca")
    print("Stadium:", s)
    if s:
        w = fetch_weather(s["lat"], s["lon"], "2026-06-11T21:00:00+00:00")
        print("Weather:", w)
        print("Context text:", build_context_text(s, w, {}))
