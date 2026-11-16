"""Buteurs (Wilson, 16/11/2026 : « dans la bankroll ajoute le foot : resultats, buteur uniquement titulaire ») :
cotes « buteur dans le match » au moment ou les compositions OFFICIELLES sont connues, et reglement d'apres la feuille
de match ESPN.

- Cotes : The Odds API, marche player_goal_scorer_anytime, region us (DraftKings / FanDuel...), 1 credit par match,
  une seule fois par match, dans les 100 min avant le coup d'envoi et seulement si les DEUX compositions sont
  officielles (foot_sim : home_info.lineup == « officielle »). Aucune source gratuite connue (ESPN n'a pas les buteurs,
  Winamax bloque les scripts). Sans credit (quota epuise jusqu'a la recharge mensuelle), rien n'est releve : les paris
  buteur attendent.
- Reglement : buts de la feuille ESPN (keyEvents « scoringPlay », buts contre son camp exclus, penalty compte) ;
  joueur absent de la feuille (ni titulaire ni entre en jeu) -> rembourse.
- Paris en direct sur un remplacant qui entre : pas de cote en direct disponible -> non pris (pas de pari a une cote
  inventee).
"""
import difflib
import json
import unicodedata
from datetime import datetime, timedelta, timezone
from pathlib import Path

PATH = Path("data/foot_scorer_odds.json")
SPORT_KEY = {47: "soccer_epl", 87: "soccer_spain_la_liga", 54: "soccer_germany_bundesliga", 55: "soccer_italy_serie_a",
             53: "soccer_france_ligue_one", 42: "soccer_uefa_champs_league", 73: "soccer_uefa_europa_league"}
API = "https://api.the-odds-api.com/v4"
WINDOW = timedelta(minutes=100)
MIN_CREDITS = 5                 # credits laisses sur une cle (le balayage Pinnacle garde sa propre reserve)


def _norm(s):
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()
    return " ".join(s.replace("-", " ").replace(".", " ").split())


def same_player(a, b):
    """Meme joueur ? (noms ESPN / FotMob / bookmaker : accents, initiales, nom seul)."""
    a, b = _norm(a), _norm(b)
    if not a or not b: return False
    if a == b: return True
    la, lb = a.split()[-1], b.split()[-1]
    if la == lb and (a.split()[0][0] == b.split()[0][0] or len(a.split()) == 1 or len(b.split()) == 1): return True
    return difflib.SequenceMatcher(None, a, b).ratio() >= 0.88


def load():
    try:
        return json.loads(PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"games": {}, "quota": {}}


def official(g):
    return all(((g.get(s + "_info") or {}).get("lineup") == "officielle") for s in ("home", "away"))


def _event_id(sport_key, g, key):
    """Identifiant Odds API du match (liste des evenements : gratuite)."""
    import urllib.request
    url = f"{API}/sports/{sport_key}/events?apiKey={key}"
    evs = json.loads(urllib.request.urlopen(url, timeout=30).read())
    ko = datetime.fromisoformat(g["ko"].replace("Z", "+00:00"))
    sim = lambda a, b: difflib.SequenceMatcher(None, _norm(a), _norm(b)).ratio()
    best, bs = None, 1.2
    for e in evs:
        if abs(datetime.fromisoformat(e["commence_time"].replace("Z", "+00:00")) - ko) > timedelta(hours=3): continue
        sc = sim(e["home_team"], g["home"]["name"]) + sim(e["away_team"], g["away"]["name"])
        if sc > bs: best, bs = e, sc
    return best and best["id"]


def fetch(games, now=None, dry=False):
    """Releve les cotes buteur des matchs eligibles (compositions officielles, < 100 min). Renvoie les ids releves."""
    import urllib.request
    import ev_scan
    now = now or datetime.now(timezone.utc)
    st = load()
    keys = ev_scan._keys()
    done = []
    for g in games:
        sk = SPORT_KEY.get(g.get("league"))
        if not sk or str(g["id"]) in st["games"] or not official(g): continue
        ko = datetime.fromisoformat(g["ko"].replace("Z", "+00:00"))
        if not timedelta(0) < ko - now <= WINDOW: continue
        kn = next(((n, k) for n, k in keys if st["quota"].get(n, 999) > MIN_CREDITS), None)
        if not kn:
            print("  [!] buteurs : plus de credit The Odds API (recharge mensuelle)"); break
        try:
            eid = _event_id(sk, g, kn[1])
            if not eid:
                st["games"][str(g["id"])] = {"at": now.isoformat(timespec="minutes"), "none": "match introuvable"}; continue
            r = urllib.request.urlopen(f"{API}/sports/{sk}/events/{eid}/odds?apiKey={kn[1]}&regions=us"
                                       f"&markets=player_goal_scorer_anytime&oddsFormat=decimal", timeout=30)
            st["quota"][kn[0]] = int(r.headers.get("x-requests-remaining") or 0)
            data = json.loads(r.read())
        except Exception as e:   # jamais la cle dans le message
            print(f"  [!] buteurs {g['home']['name']} - {g['away']['name']} : {type(e).__name__}")
            continue
        best = {}
        for b in data.get("bookmakers") or []:
            for m in b.get("markets") or []:
                if m.get("key") != "player_goal_scorer_anytime": continue
                for o in m.get("outcomes") or []:
                    nm = o.get("description") or o.get("name")
                    if (o.get("name") in (None, "Yes", nm)) and o.get("price") and (nm not in best or o["price"] > best[nm][0]):
                        best[nm] = [o["price"], b.get("title") or b.get("key")]
        st["games"][str(g["id"])] = {"at": now.isoformat(timespec="minutes"), "event": eid, "match": f"{g['home']['name']} - {g['away']['name']}",
                                     "ko": g["ko"], "odds": best}
        done.append(str(g["id"]))
    if not dry and (done or st["games"]):
        cut = (now - timedelta(days=3)).isoformat()
        st["games"] = {k: v for k, v in st["games"].items() if (v.get("ko") or v["at"]) >= cut}
        PATH.write_text(json.dumps(st, ensure_ascii=False, indent=0), encoding="utf-8")
    return done


def price_for(gid, name):
    """[cote, bookmaker] du joueur (None si pas cote)."""
    odds = ((load()["games"].get(str(gid)) or {}).get("odds")) or {}
    return next((v for nm, v in odds.items() if same_player(nm, name)), None)


def scorer_result(res):
    """'win' / 'loss' / 'push' (joueur pas sur la feuille) / None (pas termine) d'apres ESPN."""
    from nba_boxdb import _get as eget
    d = eget(f"https://site.api.espn.com/apis/site/v2/sports/{res['espn']}/summary?event={res['espn_id']}") or {}
    state = ((((d.get("header") or {}).get("competitions") or [{}])[0].get("status") or {}).get("type") or {}).get("state")
    if state != "post": return None
    played = False
    for t in d.get("rosters") or []:
        for p in t.get("roster") or []:
            if same_player((p.get("athlete") or {}).get("displayName"), res["player"]) and (p.get("starter") or p.get("subbedIn")):
                played = True
    for k in d.get("keyEvents") or []:
        typ = str(((k.get("type") or {}).get("text")) or "")
        if not k.get("scoringPlay") or "own" in typ.lower(): continue
        if any(same_player((p.get("athlete") or {}).get("displayName"), res["player"]) for p in k.get("participants") or []):
            return "win"
    return "loss" if played else "push"
