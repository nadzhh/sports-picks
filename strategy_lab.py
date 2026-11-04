"""
strategy_lab.py — « bots » de strategie : chaque soir, chaque strategie choisit ses 10 paris dans le meme lot de paris
possibles, au meme moment (20 h, heure de Paris), mise fictive 1 unite ; tout est regle et compare (demande de Wilson,
04/11/2026 : « sortir la meilleure analyse / algorithme avec le plus gros taux de reussite / fiabilite »).

Regles du contradicteur (methode Expert / Contradicteur) :
  - classement au ROI et a la CLV, pas au seul taux de reussite (80 % a cote 1,20 perd de l'argent) ;
  - une strategie « au hasard » sert de temoin ;
  - test multiple : avec N strategies, l'une paraitra bonne par chance -> « prouvee » seulement si l'intervalle de
    confiance du ROI corrige de Bonferroni (z = 2,69 pour 12) exclut 0, sur au moins MIN_N paris.
Fichier : data/strategy_lab.json ; carte « Labo des strategies » (nba_bankroll_site) et briefing du chat.
"""
import hashlib, json, math, random
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

PATH = Path("data/strategy_lab.json")
PER_NIGHT, DECIDE_HOUR, WINDOW_H, MIN_N = 10, 20, 24, 100
W_PROP = 0.10
PICK_STATS = ("AST", "3PM", "STL", "BLK")
UNIT = {"AST": "passes", "3PM": "paniers à 3 pts", "STL": "interceptions", "BLK": "contres", "PTS": "points",
        "REB": "rebonds", "PRA": "pts+reb+pas", "PR": "pts+reb", "PA": "pts+pas", "RA": "reb+pas"}


def _load(p, d):
    try: return json.loads(Path(p).read_text(encoding="utf-8"))
    except (OSError, ValueError): return d


def _t(iso): return datetime.fromisoformat(str(iso).replace("Z", "+00:00"))


def _dec(am): return None if am is None else 1 + am / 100 if am > 0 else 1 + 100 / -am


def _paris(dt):
    try:
        from zoneinfo import ZoneInfo
        return dt.astimezone(ZoneInfo("Europe/Paris"))
    except Exception:
        return dt + timedelta(hours=1)


# ─── Lot de paris possibles (identique pour toutes les strategies) ───────────
def nba_pool(now, hours=WINDOW_H):
    out = []
    for g in (_load("data/nba_sim.json", {}) or {}).get("games") or []:
        if not timedelta(0) < _t(g["date"]) - now <= timedelta(hours=hours): continue
        pl = {p["id"]: p for p in g.get("players") or []}
        side_of = {p["id"]: p["side"] for p in g.get("players") or []}
        uplift = {}   # joueur -> {stat: (sans, avec, n)} d'apres les absents de son equipe (WOWY)
        for sd in ("home", "away"):
            for a in (g.get("absences") or {}).get(sd) or []:
                for m in a.get("mates") or []:
                    uplift.setdefault((sd, m["name"]), {})
                    for st in ("PTS", "REB", "AST", "3PM", "MIN"):
                        if m.get(st): uplift[(sd, m["name"])][st] = (m[st][0], m[st][1], m.get("n", 0))
        for pr in g.get("props") or []:
            if pr.get("p_play", 0) < 0.9 or pr.get("over") is None or pr.get("under") is None: continue
            P = pl.get(pr["id"]) or {}
            sd = side_of.get(pr["id"], pr.get("side"))
            for side in ("o", "u"):
                ps = pr["p_sim"] if side == "o" else 1 - pr["p_sim"]
                pm = pr["p_mkt"] if side == "o" else 1 - pr["p_mkt"]
                d = _dec(pr["over"] if side == "o" else pr["under"])
                out.append({"src": "nba", "gid": g["gid"], "ko": g["date"], "pid": pr["id"], "name": pr["name"],
                            "match": f"{g['away']['abbr']} @ {g['home']['abbr']}", "stat": pr["stat"], "line": pr["line"],
                            "side": side, "price": round(d, 3), "book": "DraftKings", "p_sim": ps, "p_mkt": pm,
                            "gap": ps - pm, "sel": pr.get("pick") == side, "min": P.get("MIN"),
                            "min_season": (P.get("season") or {}).get("MIN"), "upl": uplift.get((sd, pr["name"]), {}),
                            "key": f'{g["gid"]}|{pr["id"]}|{pr["stat"]}|{pr["line"]}|{side}'})
    return out


def value_pool(now, hours=WINDOW_H):
    try:
        import picks_du_jour as PJ
        old = PJ.MIN_EV_VALUE
        PJ.MIN_EV_VALUE = -1.0                # toutes les lignes cotees (chaque strategie trie a sa facon)
        try:
            c = PJ.value_candidates(now, hours)
        finally:
            PJ.MIN_EV_VALUE = old
    except Exception:
        return []
    return [dict(x, src="value", key=f'{x["match_id"]}|{x["label"]}', gid=x["match_id"]) for x in c]


def hv_pool(now, hours=WINDOW_H):
    try:
        import notify
    except Exception:
        return []
    out = []
    for gid, g in ((_load("data/nba_picks.json", {}) or {}).items()):
        try:
            ko = _t(g.get("date"))
        except (TypeError, ValueError):
            continue
        if not timedelta(0) < ko - now <= timedelta(hours=hours): continue
        for pk in (g.get("home_picks") or []) + (g.get("away_picks") or []):
            if pk.get("real_cote") and notify._is_high_value_nba(pk):
                out.append({"src": "hv", "gid": gid, "ko": g["date"], "name": pk.get("label"), "match": f'{g.get("away_team")} @ {g.get("home_team")}',
                            "label": pk.get("label"), "price": pk["real_cote"], "book": str(pk.get("book", "")).upper(),
                            "conf": pk.get("confidence", 0), "edge": pk.get("edge") or 0, "key": f'hv|{gid}|{pk.get("label")}',
                            "res": {"prop": pk.get("prop"), "line": pk.get("line"), "direction": pk.get("direction"),
                                    "player": pk.get("player") or pk.get("label")}})
    return out


# ─── Strategies : (cle, nom, explication, fonction lot -> paris tries par conviction) ─
def _per_player(rows, n=PER_NIGHT):
    out, seen = [], set()
    for r in rows:
        k = (r["gid"], r.get("pid") or r.get("name"))
        if k in seen: continue
        out.append(r); seen.add(k)
        if len(out) >= n: break
    return out


def _seed(day):
    return int(hashlib.md5(day.encode()).hexdigest()[:8], 16)


def s_random(P, day):
    nba = list(P["nba"])
    random.Random(_seed(day)).shuffle(nba)
    return _per_player(nba)


def s_market_fav(P, day):
    return _per_player(sorted((r for r in P["nba"] if r["price"] >= 1.5), key=lambda r: -r["p_mkt"]))


def s_sim_raw(P, day):
    return _per_player(sorted((r for r in P["nba"] if r["stat"] in PICK_STATS and r["gap"] > 0), key=lambda r: -r["gap"]))


def s_sim_cal(P, day):
    ev = lambda r: (r["p_mkt"] + W_PROP * (r["p_sim"] - r["p_mkt"])) * r["price"] - 1
    return _per_player(sorted((r for r in P["nba"] if abs(r["gap"]) < 0.15), key=lambda r: -ev(r)))


def s_gap_mid(P, day):
    return _per_player(sorted((r for r in P["nba"] if r["stat"] in PICK_STATS and 0.05 <= r["gap"] < 0.15), key=lambda r: -r["gap"]))


def s_unders(P, day):
    return _per_player(sorted((r for r in P["nba"] if r["side"] == "u" and r["stat"] in ("AST", "3PM") and r["gap"] > 0.03),
                              key=lambda r: -r["gap"]))


def s_minutes(P, day):
    """Plus de temps de jeu que d'habitude (absences, role) et le simulateur d'accord : over sur ses stats."""
    rows = [r for r in P["nba"] if r["side"] == "o" and r.get("min") and r.get("min_season") and r["gap"] > 0
            and r["min"] - r["min_season"] >= 3]
    return _per_player(sorted(rows, key=lambda r: -(r["min"] - r["min_season"])))


def s_absences(P, day):
    """Coequipier absent : le joueur produit nettement plus sans lui (5 matchs mini, WOWY) et la simu va dans ce sens."""
    rows = []
    for r in P["nba"]:
        u = r["upl"].get(r["stat"]) if r["stat"] in ("PTS", "REB", "AST", "3PM") else None
        if not u or u[2] < 5 or u[1] <= 0: continue
        up = u[0] / u[1] - 1
        if (up >= 0.15 and r["side"] == "o" and r["gap"] > 0) or (up <= -0.15 and r["side"] == "u" and r["gap"] > 0):
            rows.append(dict(r, _s=abs(up)))
    return _per_player(sorted(rows, key=lambda r: -r["_s"]))


def s_consensus(P, day):
    """Simulateur ET marche du meme cote (proba simu et marche >= 55 %), cote >= 1,6 : le pari « qui fait l'unanimite »."""
    rows = [r for r in P["nba"] if r["p_sim"] >= 0.55 and r["p_mkt"] >= 0.52 and r["price"] >= 1.6]
    return _per_player(sorted(rows, key=lambda r: -(r["p_sim"] + r["p_mkt"])))


def s_safe(P, day):
    rows = [r for r in P["nba"] if r["price"] >= 1.5 and min(r["p_sim"], r["p_mkt"]) >= 0.58]
    return _per_player(sorted(rows, key=lambda r: -min(r["p_sim"], r["p_mkt"])))


def s_high_value(P, day):
    return _per_player(sorted(P["hv"], key=lambda r: -r["conf"]))


def s_value(P, day):
    rows = [r for r in P["value"] if r["ev"] > 0 and not ((r["label"].startswith("Victoire") or r["label"] == "Match nul") and r["price"] > 3.0)]
    return _per_player(sorted(rows, key=lambda r: -r["ev"]))


def s_foot_sim(P, day):
    """Plus / moins 2,5 buts ou le simulateur foot s'ecarte le plus de la cote juste Pinnacle, du cote du simulateur."""
    rows = [r for r in P["value"] if r.get("p_sim") is not None and "2.5 buts" in r["label"] and r["p_sim"] > r["p_fair"]]
    return _per_player(sorted(rows, key=lambda r: -(r["p_sim"] - r["p_fair"])))


STRATS = [
    ("hasard", "Témoin : au hasard", "10 props NBA tirées au sort : ce que vaut « ne rien savoir ».", s_random),
    ("marche", "Favoris du marché", "Les props NBA que le bookmaker donne les plus probables, cote ≥ 1,50.", s_market_fav),
    ("simu_brut", "Simulateur brut", "Le plus gros écart simulateur / bookmaker (passes, 3 pts, interceptions, contres).", s_sim_raw),
    ("simu_recale", "Simulateur recalé", "Espérance avec 10 % de l'écart du simulateur (poids mesuré en réel), écarts < 15 pts.", s_sim_cal),
    ("ecart_modere", "Écart modéré", "Écart simulateur / bookmaker entre 5 et 15 pts seulement (les gros écarts = info manquante).", s_gap_mid),
    ("unders", "Unders passes / 3 pts", "Seulement les « moins de » sur les passes et les 3 pts, quand le simulateur est d'accord.", s_unders),
    ("minutes", "Temps de jeu en hausse", "Joueurs avec 3+ min de plus que leur moyenne ce soir, over avec le simulateur.", s_minutes),
    ("absences", "Effet des absences", "Le joueur produit 15 %+ de plus (ou de moins) sans un coéquipier absent ce soir, simulateur d'accord.", s_absences),
    ("consensus", "Consensus simu + marché", "Simulateur ≥ 55 % et marché ≥ 52 % du même côté, cote ≥ 1,60.", s_consensus),
    ("safe", "Safe", "Simulateur et bookmaker ≥ 58 %, cote ≥ 1,50.", s_safe),
    ("high_value", "High value (moteur props)", "Les picks « high value » de l'ancien moteur, cotes FanDuel / DraftKings.", s_high_value),
    ("value", "Value bets Pinnacle", "Cote française au-dessus de la cote juste Pinnacle (foot et NBA, victoires ≤ 3,00).", s_value),
    ("foot_simu", "Simulateur foot +/- 2,5", "Plus / moins 2,5 buts où le simulateur foot s'écarte le plus de Pinnacle.", s_foot_sim),
]
Z_ADJ = 2.69   # Bonferroni ~12 strategies (0,05 / 12, bilateral)


# ─── Decision du soir, suivi des cotes, reglement ────────────────────────────
def load():
    return _load(PATH, {"bets": [], "days": []})


def save(st):
    PATH.write_text(json.dumps(st, ensure_ascii=False, indent=0), encoding="utf-8")


def decide(now=None):
    """Une fois par jour a partir de DECIDE_HOUR (Paris) : chaque strategie choisit ses paris. Renvoie le nombre."""
    now = now or datetime.now(timezone.utc)
    loc = _paris(now)
    day = loc.strftime("%Y-%m-%d")
    st = load()
    if loc.hour < DECIDE_HOUR or day in st["days"]: return 0
    P = {"nba": nba_pool(now), "value": value_pool(now), "hv": hv_pool(now)}
    n = 0
    for key, name, _, fn in STRATS:
        try:
            picks = fn(P, day)
        except Exception as e:
            print(f"  [!] strategie {key} : {type(e).__name__}: {e}"); continue
        for r in picks:
            st["bets"].append({"day": day, "strat": key, "src": r["src"], "key": r["key"], "gid": r["gid"], "ko": r["ko"],
                               "match": r.get("match"), "label": r.get("label") or f'{r["name"]} {"plus" if r.get("side") == "o" else "moins"} de {r.get("line")} {UNIT.get(r.get("stat"), r.get("stat"))}',
                               "price": r["price"], "book": r.get("book"), "p_mkt": r.get("p_mkt") or r.get("p_fair"),
                               "res": r.get("res") or {k: r.get(k) for k in ("gid", "pid", "stat", "line", "side")},
                               "last_p": r.get("p_mkt") or r.get("p_fair")})
            n += 1
    st["days"].append(day)
    save(st)
    return n


def update_prices(now=None):
    """Derniere probabilite sans marge vue avant le match (CLV) pour les paris NBA et value encore ouverts."""
    now = now or datetime.now(timezone.utc)
    st = load()
    if not st["bets"]: return 0
    nba = {r["key"]: r for r in nba_pool(now - timedelta(hours=WINDOW_H), hours=2 * WINDOW_H)}
    val = {r["key"]: r for r in value_pool(now - timedelta(hours=WINDOW_H), hours=2 * WINDOW_H)}
    n = 0
    for b in st["bets"]:
        if b.get("result") or _t(b["ko"]) <= now: continue
        r = nba.get(b["key"]) or val.get(b["key"])
        if r:
            p = r.get("p_mkt") if r["src"] == "nba" else r.get("p_fair")
            if p and p != b.get("last_p"): b["last_p"] = p; n += 1
    if n: save(st)
    return n


def resolve(box=None, now=None):
    now = now or datetime.now(timezone.utc)
    st = load(); n = 0
    hist = None
    for b in st["bets"]:
        if b.get("result") or now - _t(b["ko"]) < timedelta(hours=3): continue
        r = None
        try:
            if b["src"] == "nba" and box is not None:
                import nba_bankroll
                r = nba_bankroll.leg_result(dict(b["res"]), box)
                r = "push" if r == "void" else r          # joueur absent : rembourse
            elif b["src"] == "value" and (b["res"] or {}).get("key"):
                import ev_scan
                m, nm, pt = b["res"]["key"]
                out = ev_scan._espn_result(dict(b["res"], market=m, name=nm, point=pt))
                r = out[0] if out else None
            elif b["src"] == "hv":
                if hist is None:
                    hist = {(p.get("date"), p.get("label")): p.get("result") for p in (_load("data/nba_picks_history.json", {}) or {}).get("picks") or []}
                x = next((v for (d, l), v in hist.items() if l == b["label"] and d and abs((_t(d + "T12:00Z") - _t(b["ko"])).days) <= 1), None)
                r = {"WIN": "win", "LOSS": "loss", "PUSH": "push"}.get(x)
        except Exception as e:
            print(f"  [!] reglement labo {b.get('label')} : {type(e).__name__}: {e}")
        if r:
            b["result"] = r
            b["units"] = round(b["price"] - 1, 3) if r == "win" else -1.0 if r == "loss" else 0.0
            n += 1
    if n: save(st)
    return n


def leaderboard():
    st = load()
    by = defaultdict(list)
    for b in st["bets"]:
        if b.get("result") in ("win", "loss"): by[b["strat"]].append(b)
    rows = []
    for key, name, desc, _ in STRATS:
        L = by.get(key, [])
        n = len(L)
        open_n = sum(1 for b in st["bets"] if b["strat"] == key and not b.get("result"))
        if not n:
            rows.append({"key": key, "name": name, "desc": desc, "n": 0, "open": open_n}); continue
        u = [b["units"] for b in L]
        m = sum(u) / n
        sd = (sum((x - m) ** 2 for x in u) / max(n - 1, 1)) ** 0.5
        se = sd / n ** 0.5
        clv = [b["price"] * b["last_p"] - 1 for b in L if b.get("last_p")]
        status = ("prouvée" if n >= MIN_N and m - Z_ADJ * se > 0 else "perdante" if n >= MIN_N and m + Z_ADJ * se < 0
                  else "à confirmer")
        rows.append({"key": key, "name": name, "desc": desc, "n": n, "open": open_n, "hit": sum(b["result"] == "win" for b in L) / n,
                     "odds": sum(b["price"] for b in L) / n, "roi": m, "lo": m - 1.96 * se, "hi": m + 1.96 * se,
                     "clv": (sum(clv) / len(clv)) if clv else None, "status": status,
                     "since": min(b["day"] for b in L)})
    rows.sort(key=lambda r: (-(r.get("lo") if r.get("n") else -9), r["key"]))
    return rows


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8")
    now = datetime.now(timezone.utc)
    P = {"nba": nba_pool(now), "value": value_pool(now), "hv": hv_pool(now)}
    print(f"lot : {len(P['nba'])} props NBA, {len(P['value'])} lignes de match cotées, {len(P['hv'])} high value")
    day = _paris(now).strftime("%Y-%m-%d")
    for key, name, _, fn in STRATS:
        picks = fn(P, day)
        print(f"\n{name} ({len(picks)}) :")
        for r in picks[:3]:
            print("   ", r.get("match"), "|", r.get("label") or f'{r["name"]} {r["side"]} {r["line"]} {r["stat"]}', "@", r["price"])
