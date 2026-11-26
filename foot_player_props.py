"""
foot_player_props.py — Paris joueurs foot de la Bankroll IA foot (Wilson, 26/11/2026 : « buteurs titulaires,
passes décisives », mises normales du défi).

Source des cotes : DraftKings publiées gratuitement par ESPN (core API, propBets du fournisseur 100). Vérifié le
26/11/2026 : « Anytime Goalscorer » et « Assists Milestones » sont mis à jour quelques jours avant le match (fiables) ;
les marchés mi-temps / les deux équipes marquent / buts par équipe y ont des cotes vieilles de 2-3 semaines et sans
oui/non : écartés. Pas de « joueur décisif », pas de buteur à la mi-temps, pas de cote en direct.

Seulement les TITULAIRES, une fois les compositions officielles publiées par ESPN (~1 h avant le match).
Probabilités :
  - buteur : la simulation foot (data/foot_sim.json, joueurs « scorers », p_goal ; matchs ancrés sur la cote) ;
  - passeur : modèle simple, pas encore vérifié — passes décisives par 90 min de la saison (ESPN, championnat du
    match puis grands championnats), rapprochées d'une moyenne par poste (5 matchs équivalents), x minutes attendues,
    x buts attendus de son équipe ce soir / sa moyenne de buts ; P(1+) = 1 - exp(-lambda).
La cote ne donne qu'un côté (pas de « non ») : la marge ne se retire pas ; on suppose ~10 %, et on ne garde que la
moitié de l'écart entre le modèle et cette probabilité de marché (prudence, avantage non prouvé).
"""
import difflib
import json
import math
import re
import unicodedata
from datetime import datetime, timedelta, timezone
from pathlib import Path

PATH = Path("data/espn_foot_props.json")
CACHE = Path("data/cache_espn_foot_athletes.json")      # cache_* : jamais publie
SLUGS = {"eng.1": "Premier League", "esp.1": "LaLiga", "ger.1": "Bundesliga", "ita.1": "Serie A", "fra.1": "Ligue 1",
         "uefa.champions": "Ligue des champions", "uefa.europa": "Ligue Europa"}
DOMESTIC = ("eng.1", "esp.1", "ger.1", "ita.1", "fra.1")
MARGIN, LAMBDA = 0.10, 0.5
AST_PRIOR = {"F": 0.13, "M": 0.12, "D": 0.06, "G": 0.0}      # passes decisives par 90 min, par poste
AST_K = 450.0                                                 # minutes equivalentes de la moyenne de poste
WINDOW = (timedelta(minutes=10), timedelta(minutes=120))      # cotes et compositions : de 2 h a 10 min avant


def _get(url):
    from nba_boxdb import _get as eget
    return eget(url) or {}


def _norm(s):
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z ]", "", s).strip()


def same(a, b):
    a, b = _norm(a), _norm(b)
    if not a or not b: return False
    if a == b or a.split()[-1] == b.split()[-1] and a[0] == b[0]: return True
    return difflib.SequenceMatcher(None, a, b).ratio() >= 0.85


def _t(iso):
    return datetime.fromisoformat(iso.replace("Z", "+00:00"))


def _load(p, default):
    try:
        return json.loads(Path(p).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def _save(p, d):
    tmp = Path(str(p) + ".tmp")
    tmp.write_text(json.dumps(d, ensure_ascii=False, indent=0), encoding="utf-8")
    tmp.replace(p)


def events(now, lo=WINDOW[0], hi=WINDOW[1]):
    """[(slug, event)] des grands matchs dont le coup d'envoi tombe dans [now+lo, now+hi]."""
    out = []
    for day in sorted({(now + d).astimezone(timezone(timedelta(hours=-5))).strftime("%Y%m%d") for d in (lo, hi)}):
        for slug in SLUGS:
            for e in _get(f"https://site.api.espn.com/apis/site/v2/sports/soccer/{slug}/scoreboard?dates={day}").get("events") or []:
                try:
                    ko = _t(e["date"])
                except (KeyError, ValueError):
                    continue
                if lo <= ko - now <= hi and not any(x[1]["id"] == e["id"] for x in out): out.append((slug, e))
    return out


def busy(now=None):
    """Vrai si un grand match commence dans les 2 h ou se joue (cycles de 10 min de la veille : compositions,
    alerte live de 2e mi-temps). Lu sur les cotes ESPN deja relevees (aucun appel reseau)."""
    now = now or datetime.now(timezone.utc)
    fair = (_load("data/espn_foot_odds.json", {}) or {}).get("fair") or {}
    return any(-timedelta(hours=2) <= _t(v["commence"]) - now <= timedelta(hours=2) for v in fair.values() if v.get("commence"))


def _pos(name):
    """Poste ESPN (« Left Winger », « Center Back »…) -> G / D / M / F."""
    n = (name or "").lower()
    if "goalkeeper" in n: return "G"
    if "back" in n or "defender" in n: return "D"
    if "forward" in n or "winger" in n or "striker" in n: return "F"
    return "M"


def lineups(summary):
    """{athlete_id: {name, side ('home'/'away'), starter, pos}} si les compositions sont publiees, sinon {}."""
    comp = ((summary.get("header") or {}).get("competitions") or [{}])[0]
    side_of = {str((c.get("team") or {}).get("id")): c.get("homeAway") for c in comp.get("competitors") or []}
    out = {}
    for t in summary.get("rosters") or []:
        side = side_of.get(str((t.get("team") or {}).get("id"))) or t.get("homeAway")
        for p in t.get("roster") or []:
            a = p.get("athlete") or {}
            out[str(a.get("id"))] = {"name": a.get("displayName"), "side": side, "starter": bool(p.get("starter")),
                                     "pos": _pos((p.get("position") or {}).get("name"))}
    n = sum(1 for v in out.values() if v["starter"])
    return out if n >= 20 else {}


def prices(slug, eid):
    """{athlete_id: {"goal": cote, "ast": cote}} (buteur a tout moment ; 1+ passe decisive)."""
    out, page = {}, 1
    while page <= 3:
        d = _get(f"https://sports.core.api.espn.com/v2/sports/soccer/leagues/{slug}/events/{eid}/competitions/{eid}"
                 f"/odds/100/propBets?limit=1000&page={page}")
        for x in d.get("items") or []:
            typ = (x.get("type") or {}).get("name")
            ref = ((x.get("athlete") or {}).get("$ref") or "")
            m = re.search(r"/athletes/(\d+)", ref)
            cur = x.get("current") or {}
            price = ((cur.get("over") or {}).get("decimal"))
            if not m or not price: continue
            if typ == "Anytime Goalscorer":
                out.setdefault(m.group(1), {})["goal"] = float(price)
            elif typ == "Assists Milestones" and ((cur.get("target") or {}).get("value")) == 1.0:
                out.setdefault(m.group(1), {})["ast"] = float(price)
        if page >= (d.get("pageCount") or 1): break
        page += 1
    return out


def _season(aid, slug, cache):
    """(minutes, passes decisives, buts) de la saison : championnat du match, sinon grands championnats."""
    key = f"{aid}|{slug}"
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if (cache.get(key) or {}).get("day") == today: return cache[key]["v"]
    best = (0.0, 0.0, 0.0)
    for lg in (slug,) + tuple(s for s in DOMESTIC if s != slug):
        d = _get(f"https://sports.core.api.espn.com/v2/sports/soccer/leagues/{lg}/seasons/{_season_year()}/types/1"
                 f"/athletes/{aid}/statistics")
        st = {s.get("name"): s.get("value") or 0 for c in ((d.get("splits") or {}).get("categories") or [])
              for s in c.get("stats") or []}
        v = (float(st.get("minutes") or 0), float(st.get("goalAssists") or 0), float(st.get("totalGoals") or 0))
        if v[0] > best[0]: best = v
        if best[0] >= 450: break
    cache[key] = {"day": today, "v": best}
    return best


def _season_year(now=None):
    now = now or datetime.now(timezone.utc)
    return now.year if now.month >= 7 else now.year - 1


def _sim_game(e, sims):
    ko = _t(e["date"])
    comp = (e.get("competitions") or [{}])[0]
    names = {c.get("homeAway"): (c.get("team") or {}).get("displayName") for c in comp.get("competitors") or []}
    for g in sims:
        if abs(_t(g["ko"]) - ko) > timedelta(hours=3): continue
        if (difflib.SequenceMatcher(None, _norm(g["home"]["name"]), _norm(names.get("home"))).ratio()
                + difflib.SequenceMatcher(None, _norm(g["away"]["name"]), _norm(names.get("away"))).ratio()) >= 1.3:
            return g
    return None


def p_assist(pl, season, team_goals_exp, sim_min=None):
    mins, ast, _ = season
    prior = AST_PRIOR.get(pl["pos"], 0.1)
    rate90 = (ast + prior * AST_K / 90) / ((mins + AST_K) / 90)
    exp_min = min(sim_min if sim_min else 78.0, 95.0)
    lam = rate90 * exp_min / 90 * min(max(team_goals_exp / 1.45, 0.5), 1.8)
    return 1 - math.exp(-lam)


def scan(now=None):
    """Releve les cotes joueurs des matchs dont la composition est publiee ; renvoie les candidats de la bankroll."""
    now = now or datetime.now(timezone.utc)
    sims = (_load("data/foot_sim.json", {}) or {}).get("games") or []
    cache = _load(CACHE, {})
    store = _load(PATH, {"events": {}})
    cands = []
    for slug, e in events(now):
        eid = e["id"]
        summ = _get(f"https://site.api.espn.com/apis/site/v2/sports/soccer/{slug}/summary?event={eid}")
        lu = lineups(summ)
        if not lu: continue
        pr = prices(slug, eid)
        if not pr: continue
        comp = (e.get("competitions") or [{}])[0]
        teams = {c.get("homeAway"): (c.get("team") or {}).get("displayName") for c in comp.get("competitors") or []}
        g = _sim_game(e, sims)
        match = f'{teams.get("home")} - {teams.get("away")}'
        rows = {}
        for aid, odds in pr.items():
            pl = lu.get(aid)
            if not pl or not pl["starter"]: continue
            info = (g or {}).get(f'{pl["side"]}_info') or {}
            simp = next((p for p in info.get("players") or [] if same(p.get("name"), pl["name"])), None)
            row = {"name": pl["name"], "side": pl["side"], "team": teams.get(pl["side"]), "pos": pl["pos"], **odds}
            if "goal" in odds and simp and simp.get("p_goal") is not None:
                row["p_goal"] = round(float(simp["p_goal"]), 4)
            if "ast" in odds:
                gexp = (g or {}).get("goals") or [1.45, 1.45]
                row["p_ast"] = round(p_assist(pl, _season(aid, slug, cache), gexp[0 if pl["side"] == "home" else 1],
                                              (simp or {}).get("min")), 4)
            rows[aid] = row
            for mk, lab in (("goal", f'{pl["name"]} buteur'), ("ast", f'{pl["name"]} 1+ passe décisive')):
                pm = row.get("p_" + mk)
                if mk not in odds or pm is None: continue
                price = odds[mk]
                p_mkt = min((1 / price) * (1 - MARGIN), 0.95)
                p = p_mkt + LAMBDA * (pm - p_mkt)
                cands.append({"kind": "player", "sport": "foot", "match_id": f"espn|{eid}", "match": match,
                              "league": SLUGS[slug], "ko": e["date"], "label": lab, "price": price, "book": "DraftKings",
                              "p": round(p, 4), "p_fair": round(p_mkt, 4), "p_sim": round(pm, 4), "ev": round(p * price - 1, 4),
                              "fair_src": "DraftKings (une seule cote, marge supposée 10 %)", "market": mk,
                              "res": {"espn": f"soccer/{slug}", "espn_id": eid, "player": pl["name"], "aid": aid,
                                      "team": teams.get(pl["side"]), "market": mk}})
        store["events"][eid] = {"at": now.isoformat(timespec="minutes"), "slug": slug, "match": match, "ko": e["date"],
                                "players": rows}
    store["events"] = {k: v for k, v in store["events"].items() if now - _t(v["ko"]) < timedelta(days=3)}
    _save(PATH, store); _save(CACHE, cache)
    return cands


def settle(res):
    """'win' / 'loss' / 'push' (joueur pas entre en jeu) / None (match pas termine)."""
    d = _get(f"https://site.api.espn.com/apis/site/v2/sports/{res['espn']}/summary?event={res['espn_id']}")
    state = ((((d.get("header") or {}).get("competitions") or [{}])[0].get("status") or {}).get("type") or {}).get("state")
    if state != "post": return None
    me = None
    for t in d.get("rosters") or []:
        for p in t.get("roster") or []:
            a = p.get("athlete") or {}
            if str(a.get("id")) == str(res.get("aid")) or same(a.get("displayName"), res["player"]):
                me = p
    if not me or not (me.get("starter") or me.get("subbedIn")): return "push"
    if res.get("market") == "ast":
        st = {s.get("name"): s.get("value") for s in me.get("stats") or []}
        return "win" if (st.get("goalAssists") or 0) >= 1 else "loss"
    for k in d.get("keyEvents") or []:
        typ = str(((k.get("type") or {}).get("text")) or "")
        if not k.get("scoringPlay") or "own" in typ.lower(): continue
        parts = k.get("participants") or []
        if parts and same((parts[0].get("athlete") or {}).get("displayName"), res["player"]):   # [0] = buteur, [1] = passeur
            return "win"
    return "loss"
