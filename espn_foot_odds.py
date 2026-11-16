"""Cotes DraftKings du foot via ESPN (gratuit) : 1X2 et total de buts du Top 5, de la Ligue des champions et de la
Ligue Europa.

Pourquoi (16/11/2026) : le quota The Odds API etait epuise (11 + 3 credits, recharge vers le 3-4/12) ; sans cote juste
Pinnacle, la simulation foot n'est plus ancree au marche (retour du cas Lens-OM) et la Bankroll IA ne peut pas parier
le foot. ESPN expose gratuitement les cotes DraftKings des matchs (core API, fournisseur 100). On en retire la marge
(methode multiplicative) et on ecrit des entrees AU MEME FORMAT que ev_scan (st["fair"]) dans data/espn_foot_odds.json :
  - foot_sim_run.market_fair les utilise quand Pinnacle manque (ancrage) ;
  - picks_du_jour.value_candidates les lit en plus (paris foot du canal et de la Bankroll IA), cote DraftKings
    (prix FR estime a 85 % comme les props) ; reglement par le score ESPN comme les value bets.
DraftKings garde ~5-7 % de marge sur le foot : moins precis que Pinnacle, mais bien meilleur que la simulation seule
(09/11 : log-loss 1X2 simu 0,969 vs marche 0,928).
"""
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

PATH = Path("data/espn_foot_odds.json")
SLUGS = {"eng.1": "Premier League", "esp.1": "LaLiga", "ger.1": "Bundesliga", "ita.1": "Serie A", "fra.1": "Ligue 1",
         "uefa.champions": "Ligue des champions", "uefa.europa": "Ligue Europa"}
CORE = "https://sports.core.api.espn.com/v2/sports/soccer/leagues/{slug}/events/{eid}/competitions/{eid}/odds"
HORIZON = timedelta(hours=48)
EVERY = timedelta(hours=2)        # rafraichissement (gratuit, mais on reste poli avec ESPN)


def _dec(am):
    try:
        am = float(am)
    except (TypeError, ValueError):
        return None
    if am == 0: return None
    return round(1 + am / 100, 3) if am > 0 else round(1 + 100 / -am, 3)


def _get(url):
    from nba_boxdb import _get as eget
    return eget(url) or {}


def load():
    try:
        return json.loads(PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"at": None, "fair": {}}


def due(now=None):
    now = now or datetime.now(timezone.utc)
    at = load().get("at")
    return not at or now - datetime.fromisoformat(at) >= EVERY


def entry(slug, e, now):
    """Entree au format ev_scan["fair"] pour un evenement ESPN (None sans cotes DraftKings completes)."""
    c = (e.get("competitions") or [{}])[0]
    sides = {x.get("homeAway"): (x.get("team") or {}).get("displayName", "") for x in c.get("competitors") or []}
    home, away = sides.get("home"), sides.get("away")
    if not home or not away: return None
    od = _get(CORE.format(slug=slug, eid=e["id"]) + "?lang=en&region=us")
    dk = next((it for it in od.get("items") or [] if str((it.get("provider") or {}).get("id")) == "100"), None)
    if not dk: return None
    h, a = _dec((dk.get("homeTeamOdds") or {}).get("moneyLine")), _dec((dk.get("awayTeamOdds") or {}).get("moneyLine"))
    d = _dec((dk.get("drawOdds") or {}).get("moneyLine"))
    if not (h and a and d): return None
    lines, fr, keys = [], {}, {}
    s = 1 / h + 1 / d + 1 / a
    for lab, price, key in ((f"Victoire {home}", h, ["h2h", home, None]), ("Match nul", d, ["h2h", "Draw", None]),
                            (f"Victoire {away}", a, ["h2h", away, None])):
        lines.append([lab, round(1 / ((1 / price) / s), 3)]); fr[lab] = [price, "DraftKings"]; keys[lab] = key
    ou, o, u = dk.get("overUnder"), _dec(dk.get("overOdds")), _dec(dk.get("underOdds"))
    if ou and o and u:
        t = 1 / o + 1 / u
        for lab, price, nm in ((f"Plus de {ou:g} buts", o, "Over"), (f"Moins de {ou:g} buts", u, "Under")):
            lines.append([lab, round(1 / ((1 / price) / t), 3)]); fr[lab] = [price, "DraftKings"]; keys[lab] = ["totals", nm, float(ou)]
    return {"sport": "foot", "league": SLUGS[slug], "home": home, "away": away, "commence": e["date"],
            "at": now.isoformat(timespec="minutes"), "lines": lines, "fr": fr, "keys": keys, "espn": f"soccer/{slug}",
            "espn_id": str(e["id"]), "src": "DraftKings (ESPN)"}


def scan(now=None, dry=False):
    """Releve les cotes DraftKings des matchs dans les 48 h. Renvoie le nombre de matchs cotes."""
    now = now or datetime.now(timezone.utc)
    st = load()
    fair = {k: v for k, v in (st.get("fair") or {}).items()
            if datetime.fromisoformat(v["commence"].replace("Z", "+00:00")) > now}
    n = 0
    for slug in SLUGS:
        for k in range(3):
            day = (now + timedelta(days=k)).strftime("%Y%m%d")
            for e in (_get(f"https://site.api.espn.com/apis/site/v2/sports/soccer/{slug}/scoreboard?dates={day}").get("events") or []):
                try:
                    ko = datetime.fromisoformat(e["date"].replace("Z", "+00:00"))
                except (KeyError, ValueError):
                    continue
                if not timedelta(0) < ko - now <= HORIZON: continue
                if ((e.get("status") or {}).get("type") or {}).get("state") not in (None, "pre"): continue
                try:
                    v = entry(slug, e, now)
                except Exception as ex:
                    print(f"  [!] cotes ESPN {slug} {e.get('id')} : {type(ex).__name__}: {ex}")
                    v = None
                if v:
                    fair["espn|" + v["espn_id"]] = v; n += 1
    st = {"at": now.isoformat(timespec="minutes"), "fair": fair}
    if not dry: PATH.write_text(json.dumps(st, ensure_ascii=False, indent=0), encoding="utf-8")
    print(f"[espn foot] {n} match(s) cote(s) DraftKings dans les 48 h")
    return n


def fair_entries():
    """Entrees ESPN encore a venir (pour value_candidates / market_fair)."""
    now = datetime.now(timezone.utc)
    return {k: v for k, v in (load().get("fair") or {}).items()
            if datetime.fromisoformat(v["commence"].replace("Z", "+00:00")) > now}


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8")
    scan(dry="--dry" in sys.argv)
    for v in list(fair_entries().values())[:5]:
        print(v["league"], v["home"], "-", v["away"], v["commence"], v["lines"])
