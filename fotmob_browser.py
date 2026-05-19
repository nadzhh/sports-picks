"""
fotmob_browser.py — Fallback navigateur (Camoufox) pour matchs en conflit de slug.

Quand FotMob renvoie le mauvais match (slug partage entre LaLiga aller/retour/CL),
on charge la page dans un vrai navigateur. Le JS de FotMob lit le #fragment et
appelle /api/data/matchDetails?matchId=X avec le bon header x-mas. On intercepte
cette reponse pour recuperer les vraies donnees.
"""
import asyncio
import hashlib
import json
import time
from pathlib import Path

from camoufox.async_api import AsyncCamoufox

CACHE_DIR = Path("data/cache_fotmob_browser")
CACHE_DIR.mkdir(parents=True, exist_ok=True)

CACHE_TTL = 30 * 24 * 3600  # 30 jours (matchs finis = immuables)


def _cache_path(match_id):
    return CACHE_DIR / f"match_{match_id}.json"


def cache_get(match_id):
    p = _cache_path(match_id)
    if not p.exists(): return None
    if time.time() - p.stat().st_mtime > CACHE_TTL: return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def cache_set(match_id, data):
    try:
        _cache_path(match_id).write_text(
            json.dumps(data, ensure_ascii=False), encoding="utf-8"
        )
    except Exception:
        pass


# ─── Parsing identique a fotmob_client._fetch_match_page ──────────────────────

def extract_slim(payload):
    """Convertit le payload /api/data/matchDetails en format slim compatible match_events()."""
    if not payload:
        return None
    general = payload.get("general", {}) or {}
    header  = payload.get("header", {}) or {}
    content = payload.get("content", {}) or {}

    top_stats = {}
    try:
        groups = (
            content.get("stats", {}).get("Periods", {}).get("All", {}).get("stats", []) or []
        )
        for grp in groups:
            for st in grp.get("stats", []) or []:
                title = st.get("title")
                vals  = st.get("stats")
                if title and vals and len(vals) == 2 and title not in top_stats:
                    top_stats[title] = vals
    except Exception:
        pass

    return {
        "general":   general,
        "header":    header,
        "top_stats": top_stats,
    }


# ─── Browser singleton avec cache ────────────────────────────────────────────

class FotMobBrowser:
    """
    Singleton browser pour fetch de matchDetails specifiques.
    Re-utilise une seule page pour tous les fetches d'une session.
    """

    def __init__(self):
        self._ctx     = None
        self._browser = None
        self._page    = None
        self._warmed  = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        await self.close()

    async def _ensure_started(self):
        if self._browser is None:
            self._ctx = AsyncCamoufox(headless=True)
            self._browser = await self._ctx.__aenter__()
            self._page = await self._browser.new_page()
        if not self._warmed:
            try:
                await self._page.goto(
                    "https://www.fotmob.com/",
                    wait_until="domcontentloaded",
                    timeout=30000,
                )
                await asyncio.sleep(2)
                self._warmed = True
            except Exception as e:
                print(f"  [browser warmup err] {e}")

    async def fetch_match(self, match_id, slug_url, timeout_s=20):
        """
        Recupere les donnees du match specifique via navigation + interception API.
        Retourne dict slim ou None si echec (403, pas de donnees).
        """
        # Cache hit ?
        cached = cache_get(match_id)
        if cached is not None and cached.get("top_stats"):
            return cached

        await self._ensure_started()

        captured = []
        last_status = [None]

        async def on_response(resp):
            url = resp.url
            if "matchDetails" in url and f"matchId={match_id}" in url:
                last_status[0] = resp.status
                if resp.status == 200:
                    try:
                        data = await resp.json()
                        if data and data.get("content"):  # verif data non-vide
                            captured.append(data)
                    except Exception:
                        pass

        self._page.on("response", on_response)
        try:
            full_url = f"https://www.fotmob.com{slug_url.split('#')[0]}#{match_id}"
            try:
                await self._page.goto(
                    full_url,
                    wait_until="domcontentloaded",
                    timeout=timeout_s * 1000,
                )
            except Exception:
                pass

            for _ in range(20):
                if captured:
                    break
                await asyncio.sleep(0.5)
        finally:
            try:
                self._page.remove_listener("response", on_response)
            except Exception:
                pass

        if not captured:
            return {"_status": last_status[0] or "no_response"}  # marqueur d'echec

        slim = extract_slim(captured[0])
        if not slim.get("top_stats"):
            return {"_status": "empty_stats"}

        cache_set(match_id, slim)
        return slim

    async def close(self):
        if self._ctx:
            try:
                await self._ctx.__aexit__(None, None, None)
            except Exception:
                pass
            self._browser = None
            self._ctx     = None
            self._page    = None
            self._warmed  = False


# ─── Helper d'extraction (compatible match_events de fotmob_client) ───────────

def slim_to_match_events(slim):
    """Convertit la donnee slim (cachee) au format attendu par collect_team_l5."""
    if not slim: return None
    header = slim.get("header", {})
    teams = header.get("teams", [])
    status = header.get("status", {})
    events = header.get("events", {}) or {}
    top = slim.get("top_stats", {}) or {}

    def _pair(key):
        v = top.get(key)
        if v and len(v) == 2: return v[0], v[1]
        return None, None

    def _num(v):
        if v is None: return None
        try: return float(v)
        except: return None

    def parse_goals(goals_dict):
        out = []
        for scorer_name, goal_list in (goals_dict or {}).items():
            for g in goal_list:
                if g.get("type") != "Goal":
                    continue
                out.append({
                    "scorer":      g.get("player", {}).get("name") or scorer_name,
                    "scorer_id":   g.get("player", {}).get("id"),
                    "assist":      g.get("assistStr"),
                    "minute":      g.get("time"),
                    "ownGoal":     bool(g.get("ownGoal")),
                    "description": g.get("goalDescription"),
                })
        out.sort(key=lambda x: x.get("minute") or 0)
        return out

    ts_h, ts_a   = _pair("Total shots")
    sot_h, sot_a = _pair("Shots on target")
    xg_h, xg_a   = _pair("Expected goals (xG)")

    return {
        "score":     status.get("scoreStr"),
        "utcTime":   status.get("utcTime"),
        "home":      teams[0].get("name") if len(teams) > 0 else None,
        "home_id":   teams[0].get("id")   if len(teams) > 0 else None,
        "away":      teams[1].get("name") if len(teams) > 1 else None,
        "away_id":   teams[1].get("id")   if len(teams) > 1 else None,
        "home_goals": parse_goals(events.get("homeTeamGoals")),
        "away_goals": parse_goals(events.get("awayTeamGoals")),
        "home_shots": _num(ts_h),
        "away_shots": _num(ts_a),
        "home_sot":   _num(sot_h),
        "away_sot":   _num(sot_a),
        "home_xg":    _num(xg_h),
        "away_xg":    _num(xg_a),
        "opp_sot":    None,  # rempli par collect
        "opp_shots":  None,
        "opp_xg":     None,
    }
