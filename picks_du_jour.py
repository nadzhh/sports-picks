"""
picks_du_jour.py — le message unique du canal Telegram « Picks » (20 h, heure de Paris) : autant de picks que les criteres en retiennent (12 au plus pour la lisibilite), seulement
ceux dont l'esperance reste positive avec une probabilite RECALEE sur les resultats reels (demande de Wilson,
04/11/2026 : « que les meilleurs picks » ; 2 canaux : Picks pour lui, Bankroll IA a part).

Pourquoi recaler : la confiance brute des modeles ment (simulateur NBA : annonce 61 %, reel 52 % ; picks « high value »
NBA : edge annonce 20-50 %, ROI reel -12,6 % sur 72). Probabilite retenue :
  - foot et NBA, lignes de match : cote juste Pinnacle (marge retiree) ; plus / moins 2,5 buts : + 0,25 x l'ecart de
    ma simulation (backtest PL 2025-26 vs cloture Pinnacle : poids optimal 0,35-0,40 sur le +2,5, 0 sur le 1X2) ;
  - props NBA : marche sans marge + 0,10 x l'ecart du simulateur (613 selections reelles), jamais si l'ecart >= 15 pts
    (ROI -13 % : info manquante) ; cote DraftKings (a verifier chez le bookmaker francais).
Esperance = probabilite retenue x meilleure cote - 1 ; seuil +2 % (cote francaise vs Pinnacle) ou +3 % (props).
Chaque pick poste est enregistre (data/telegram_picks.json) puis regle : le canal est juge sur son vrai ROI.

Palier « Meilleur prix » (Wilson, 05/11/2026 : les cotes francaises sont nettement sous Pinnacle, mediane -9,5 % sur
91 issues mesurees -> presque jamais de value) : autant de paris que les criteres en retiennent, la ou la meilleure cote francaise est a moins
de 3 % de la cote juste Pinnacle ET ou ma simulation donne au moins la probabilite de Pinnacle. Esperance encore
legerement NEGATIVE, annoncee comme telle : on paie ~1-3 % de marge au lieu de ~9,5 %. Suivi a part du palier value.
"""
import difflib, json
from datetime import datetime, timedelta, timezone
from pathlib import Path

PATH = Path("data/telegram_picks.json")
# 05/11/2026 (Wilson : « ne te bloque pas a 3 paris, c'est a toi de gerer ») : pas de quota fixe, ce sont les criteres
# qui decident (esperance, accord de la simulation, un pari par match). MAX_PICKS ne sert qu'a garder le message
# Telegram lisible et sous sa limite de 4 096 caracteres (environ 250 caracteres par pari), tous paliers confondus.
MAX_PICKS = 12
WINDOW_H = 24
MIN_EV_VALUE, MIN_EV_PROP = 0.02, 0.03
W_O25, W_PROP, MAX_GAP_PROP = 0.25, 0.10, 0.15
BEST_MIN_EV = -0.03   # palier « Meilleur prix »
# Palier « Safe » (Wilson, 05/11/2026 : « des paris a partir de 1,40, si pour toi c'est vraiment safe ») : cote >= 1,40,
# probabilite RECALEE >= 65 % (perdu au plus ~1 fois sur 3), simulation d'accord (>= 65 %) quand elle existe, et cout
# <= 5 % (mesure du 05/11 : un favori a 1,40-1,60 coute ~9 % chez les bookmakers FR, ce n'est plus « safe » pour le capital).
SAFE_MIN_ODDS, SAFE_MIN_P, SAFE_MIN_EV = 1.40, 0.65, -0.05
# « 5 picks du soir » (Wilson, 05/11/2026 : « au moins 3-5 picks par soir de match ») : apres value / boosts / safe /
# meilleur prix, complement avec les paris les MOINS CHERS (cout affiche), mise reduite. Mises en unites par palier :
# value et boosts rentables 1 u ; safe 0,5 u ; meilleur prix et complement 0,25 u (perte attendue ~2-3 u / mois au lieu
# de ~6 u a mise pleine : mesure du 05/11, top 5 du soir a -4,9 % en moyenne).
TARGET_PICKS, FILL_MIN_EV = 5, -0.08   # 05/11 : les cotes FR coutent ~11 % ; a -6 %, 2 picks seulement un soir de Ligue Europa
# Paliers a petite mise (meilleur prix, complement) : cote >= 1,50 (Wilson, 05/11 : « 0,25 u a 1,09 ? inutile » :
# gain de 0,02 u pour un cout de 8 %). Le palier safe garde son seuil de 1,40.
SMALL_MIN_ODDS = 1.50
STAKE = {"value": 1.0, "prop": 1.0, "boost": 1.0, "safe": 0.5, "best": 0.25, "fill": 0.25}
try:   # mises ajustees par improve_bot : un palier dont la CLV reelle est negative voit sa mise baisser
    STAKE.update(json.loads(Path("data/model_params.json").read_text(encoding="utf-8")).get("stakes") or {})
except (OSError, ValueError):
    pass
# Cotes US -> France (Wilson, 09/11/2026 : « les cotes FR sont plus basses, 1,80 book US > 1,60 book FR ») : aucune
# source automatique des lignes FR de props (The Odds API region fr : 0 book sur les props NBA ; flux Kambi refuse),
# donc le prix FR d'une prop DraftKings est ESTIME a 89 % du prix US (meme ordre que la marge FR mesuree le 05/11 sur
# le 1X2 : -11 %). La selection ne change pas (Wilson : « on reste tel quel ») ; l'estimation est affichee et le bilan
# du canal est aussi compte a ce prix.
FR_US = 0.89
US_BOOKS = ("DraftKings", "FanDuel")
MAX_ODDS_WIN = 3.0   # victoires / nul : au-dela, retirer la marge a la proportionnelle surestime l'outsider (meme regle qu'ev_scan)
UNIT = {"AST": "passes", "3PM": "paniers à 3 pts", "STL": "interceptions", "BLK": "contres", "PTS": "points",
        "REB": "rebonds"}
JOURS = ["lun.", "mar.", "mer.", "jeu.", "ven.", "sam.", "dim."]


def fr_est(x):
    """Prix estime chez un bookmaker francais d'un pick cote par un book US (None si deja une cote FR)."""
    return round(max(1.01, x["price"] * FR_US), 2) if x.get("book") in US_BOOKS else None


def _px(x):
    """« @ 2,02 DraftKings (≈ 1,80 en France : coût ≈ 15 %) » pour le message Telegram."""
    s = f"<b>@ {_fr(x['price'])}</b> {x['book']}"
    fr = x.get("price_fr") or fr_est(x)
    if fr:
        ev = x["p"] * fr - 1
        s += f" (≈ {_fr(fr)} en France : " + (f"espérance {100 * ev:+.0f} %" if ev >= 0 else f"coût ≈ {100 * -ev:.0f} %") + ")"
    return s


def _cl(x):
    return "coût au prix US" if fr_est(x) else "coût attendu"


def _load(p, d):
    try:
        return json.loads(Path(p).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return d


def _t(iso):
    return datetime.fromisoformat(str(iso).replace("Z", "+00:00"))


def _paris(dt):
    try:
        from zoneinfo import ZoneInfo
        return dt.astimezone(ZoneInfo("Europe/Paris"))
    except Exception:
        return dt + timedelta(hours=1)


def _hm(dt):
    d = _paris(dt)
    return f"{JOURS[d.weekday()]} {d:%H:%M}"


def _dec(am):
    return None if am is None else 1 + am / 100 if am > 0 else 1 + 100 / -am


def _same(a, b):
    return difflib.SequenceMatcher(None, (a or "").lower(), (b or "").lower()).ratio()


def _fr(x, d=2):
    return f"{x:.{d}f}".replace(".", ",")


# ─── Types de pari (apprentissage de la veille, 06/11/2026) ──────────────────
def segment(x):
    """Type de pari pour le suivi cumule : « foot · 1X2 », « foot · total plus », « nba · 3PM moins », « boost »..."""
    if x.get("src") == "boost" or x.get("kind") == "boost": return "boost"
    lab = (x.get("label") or "").lower()
    res = x.get("res") or {}
    if res.get("stat"):
        return f'nba · {res["stat"]} {"plus" if res.get("side") == "o" else "moins"}'
    sport = x.get("sport") or "?"
    if lab.startswith("victoire") or lab == "match nul": return f"{sport} · 1X2"
    if lab.startswith(("plus de", "moins de")):
        return f'{sport} · total {"plus" if lab.startswith("plus") else "moins"}'
    return f"{sport} · autre"


def excluded():
    """Types de pari ecartes par le bot d'amelioration (perte repetee sur 30+ paris : CLV <= -4 % ou gains nettement
    sous les probabilites annoncees). Reintegres des qu'ils redeviennent bons."""
    try:
        return set(json.loads(Path("data/model_params.json").read_text(encoding="utf-8")).get("exclude") or [])
    except (OSError, ValueError):
        return set()


def _keep(out):
    ex = excluded()
    return [x for x in out if segment(x) not in ex] if ex else out


# ─── Candidats ───────────────────────────────────────────────────────────────
def value_candidates(now, hours=WINDOW_H, min_ev=MIN_EV_VALUE):
    """Lignes de match (foot + NBA) : meilleure cote francaise vs probabilite recalee (Pinnacle [+ simu sur le +2,5])."""
    fair = (_load("data/ev_picks.json", {}) or {}).get("fair") or {}
    sims = (_load("data/foot_sim.json", {}) or {}).get("games") or []
    nba = (_load("data/nba_sim.json", {}) or {}).get("games") or []
    out = []
    for eid, v in fair.items():
        try:
            ko = _t(v["commence"])
        except (KeyError, ValueError):
            continue
        if not timedelta(0) < ko - now <= timedelta(hours=hours): continue
        sim, gn, mid = None, None, eid
        if v.get("sport") == "foot":
            sim = next((g for g in sims if abs(_t(g["ko"]) - ko) <= timedelta(hours=3)
                        and _same(g["home"]["name"], v["home"]) + _same(g["away"]["name"], v["away"]) >= 1.3), None)
        elif v.get("sport") == "nba":
            gn = next((g for g in nba if abs(_t(g["date"]) - ko) <= timedelta(hours=3)
                       and _same(g["home"].get("name"), v["home"]) + _same(g["away"].get("name"), v["away"]) >= 1.3), None)
            # meme identifiant que les props du match (gid NBA) : un seul pari par match, tous paliers (05/11/2026)
            if gn: mid = gn["gid"]
        for lab, fair_odds in v.get("lines") or []:
            fr = (v.get("fr") or {}).get(lab)
            if not fr or not fair_odds: continue
            if (lab.startswith("Victoire") or lab == "Match nul") and fr[0] > MAX_ODDS_WIN: continue
            p_fair = 1 / fair_odds
            p, p_sim = p_fair, None
            if sim:
                q = sim["p"]                       # recalee (foot_calib) : affichage et accord de la simulation
                qr = sim.get("p_raw") or q         # brute : melange du +2,5 (poids W_O25 valide sur la simulation brute)
                p_sim = {"Match nul": q["X"], f"Victoire {v['home']}": q["1"], f"Victoire {v['away']}": q["2"]}.get(lab)
                for pre, over in (("Plus de ", True), ("Moins de ", False)):   # toutes les lignes x,5 simulees (0,5 a 4,5)
                    if lab.startswith(pre) and lab.endswith(" buts"):
                        o = q["over"].get(lab[len(pre):-5])
                        if o is not None: p_sim = o if over else 1 - o
                if p_sim is not None and "2.5 buts" in lab:
                    o_raw = qr["over"].get("2.5")
                    ps_raw = p_sim if o_raw is None else (o_raw if lab.startswith("Plus") else 1 - o_raw)
                    p = p_fair + W_O25 * (ps_raw - p_fair)
            elif v.get("sport") == "nba" and lab.startswith("Victoire"):   # accord du simulateur NBA (palier Meilleur prix)
                ph = ((gn or {}).get("team") or {}).get("p_home_win")
                if ph is not None: p_sim = ph if lab == f"Victoire {v['home']}" else 1 - ph
            ev = p * fr[0] - 1
            if ev < min_ev: continue
            out.append({"kind": "value", "sport": v.get("sport"), "match_id": mid, "match": f"{v['home']} - {v['away']}",
                        "league": v.get("league", ""), "ko": v["commence"], "label": lab, "price": fr[0], "book": fr[1],
                        "p": round(p, 4), "p_fair": round(p_fair, 4), "p_sim": None if p_sim is None else round(p_sim, 4),
                        "ev": round(ev, 4), "res": {"espn": v.get("espn"), "home": v["home"], "away": v["away"],
                                                    "commence": v["commence"], "key": (v.get("keys") or {}).get(lab)}})
    return _keep(out)


def prop_candidates(now, hours=WINDOW_H, min_ev=MIN_EV_PROP):
    """Props NBA (toutes les lignes cotees) : probabilite = marche sans marge + 0,10 x ecart du simulateur."""
    out = []
    for g in (_load("data/nba_sim.json", {}) or {}).get("games") or []:
        ko = _t(g["date"])
        if not timedelta(0) < ko - now <= timedelta(hours=hours): continue
        for pr in g.get("props") or []:
            if pr.get("p_play", 0) < 0.9 or pr.get("over") is None or pr.get("under") is None: continue
            for side in ("o", "u"):
                ps = pr["p_sim"] if side == "o" else 1 - pr["p_sim"]
                pm = pr["p_mkt"] if side == "o" else 1 - pr["p_mkt"]
                if abs(ps - pm) >= MAX_GAP_PROP: continue
                d = _dec(pr["over"] if side == "o" else pr["under"])
                p = pm + W_PROP * (ps - pm)
                ev = p * d - 1
                if ev < min_ev: continue
                out.append({"kind": "prop", "sport": "nba", "match_id": g["gid"],
                            "match": f"{g['away']['abbr']} @ {g['home']['abbr']}", "league": "NBA", "ko": g["date"],
                            "label": f"{pr['name']} {'plus' if side == 'o' else 'moins'} de {pr['line']:g} {UNIT.get(pr['stat'], pr['stat'])}",
                            "price": round(d, 2), "book": "DraftKings", "p": round(p, 4), "p_fair": round(pm, 4),
                            "p_sim": round(ps, 4), "ev": round(ev, 4), "safe": p >= 0.58 and d >= 1.5,
                            "res": {"gid": g["gid"], "pid": pr["id"], "stat": pr["stat"], "line": pr["line"], "side": side}})
    return _keep(out)


def fr_margin():
    """Ecart median entre la meilleure cote francaise et la cote juste Pinnacle, toutes issues (mesure du jour)."""
    fair = (_load("data/ev_picks.json", {}) or {}).get("fair") or {}
    x = sorted(fr[0] * p - 1 for v in fair.values() for lab, fo in v.get("lines") or []
               for fr in [(v.get("fr") or {}).get(lab)] for p in [1 / fo if fo else 0] if fr and fo)
    return x[len(x) // 2] if x else None


def safe_picks(now, taken=(), hours=WINDOW_H):
    """Palier « Safe » : voir SAFE_*. Lignes de match et props NBA, un pari par match, les plus probables d'abord."""
    c = [dict(x, kind="safe", src=x["kind"]) for x in value_candidates(now, hours, min_ev=SAFE_MIN_EV)
         + prop_candidates(now, hours, min_ev=SAFE_MIN_EV)
         if x["price"] >= SAFE_MIN_ODDS and x["p"] >= SAFE_MIN_P and (x.get("p_sim") is None or x["p_sim"] >= SAFE_MIN_P)]
    out, seen = [], set(taken)
    for x in sorted(c, key=lambda x: (-x["p"], -x["ev"])):
        if x["match_id"] in seen: continue
        out.append(x); seen.add(x["match_id"])
        if len(out) + len(taken) >= MAX_PICKS: break
    return sorted(out, key=lambda x: x["ko"])


def best_price(now, taken=(), hours=WINDOW_H):
    """Palier « Meilleur prix » : esperance entre -3 % et le seuil value, simulation au moins aussi confiante que
    Pinnacle, un pari par match, dans la place laissee par les picks value (MAX_PICKS au total)."""
    c = [dict(x, kind="best") for x in value_candidates(now, hours, min_ev=BEST_MIN_EV)
         if x["ev"] < MIN_EV_VALUE and x.get("p_sim") is not None and x["p_sim"] >= x["p_fair"]
         and x["price"] >= SMALL_MIN_ODDS]
    out, seen = [], set(taken)
    for x in sorted(c, key=lambda x: -x["ev"]):
        if x["match_id"] in seen: continue
        out.append(x); seen.add(x["match_id"])
        if len(out) + len(taken) >= MAX_PICKS: break
    return sorted(out, key=lambda x: x["ko"])


def boost_picks(now, taken=()):
    """Cotes boostees Winamax rentables (boosts.py : prix juste cale sur Pinnacle, esperance >= +5 %)."""
    try:
        import boosts as BO
    except Exception:
        return []
    out = []
    for b in BO.picks(now):
        if (b.get("eid") or f"boost|{b['id']}") in taken: continue
        out.append(_boost_pick(b, "boost"))
    return out


def _boost_pick(b, kind):
    return {"kind": kind, "src": "boost", "sport": b["sport"], "match_id": b.get("eid") or f"boost|{b['id']}", "match": b["match"],
            "league": "Winamax · cote boostée" + (f" (mise max {b['max_stake']} €)" if b.get("max_stake") else ""),
            "ko": b["ko"], "label": b["label"], "price": b["odds"], "book": "Winamax", "p": b["p"], "p_fair": b["p"],
            "p_sim": None, "ev": b["ev"], "max_stake": b.get("max_stake"), "res": {"boost": b}}


def _hist_ok(x):
    """Contexte (Wilson, 06/11 : « le contexte doit primer ») : un pari sur prop NBA ne doit pas etre contredit par
    l'historique du joueur (L10 >= 50 % du bon cote, mediane du bon cote). Ex. du 06/11 : Christie moins de 9,5 pts
    avec 4/10 et une mediane de 11 sortait en complement."""
    r = x.get("res") or {}
    if not r.get("stat") or not r.get("pid"): return True
    try:
        import nba_props_edge as E
        return E.hist_ok(r["pid"], r["stat"], r["line"], r.get("side") == "o")
    except Exception:
        return True


def fill(now, taken=(), have=0, hours=WINDOW_H):
    """Complement jusqu'a TARGET_PICKS (au plus un pari par match) : les paris les moins chers du moment, boosts
    compris, esperance >= FILL_MIN_EV. Jamais plus de picks que de matchs dans la fenetre."""
    vc = value_candidates(now, hours, min_ev=FILL_MIN_EV) + prop_candidates(now, hours, min_ev=FILL_MIN_EV)
    n_matches = len({x["match_id"] for x in value_candidates(now, hours, min_ev=-1) + prop_candidates(now, hours, min_ev=-1)})
    target = min(TARGET_PICKS, n_matches) - have
    if target <= 0: return []
    # jamais un pari que ma simulation contredit nettement (> 5 points sous la probabilite retenue)
    c = [dict(x, kind="fill", src=x["kind"]) for x in vc
         if x["price"] >= SMALL_MIN_ODDS and (x.get("p_sim") is None or x["p_sim"] >= x["p"] - 0.05) and _hist_ok(x)]
    try:
        import boosts as BO
        c += [_boost_pick(b, "fill") for b in BO.current(now) if b.get("ev") is not None and b["ev"] >= FILL_MIN_EV
              and b["odds"] >= SMALL_MIN_ODDS]
    except Exception:
        pass
    out, seen = [], set(taken)
    for x in sorted(c, key=lambda x: -x["ev"]):
        if x["match_id"] in seen: continue
        out.append(x); seen.add(x["match_id"])
        if len(out) >= target: break
    return sorted(out, key=lambda x: x["ko"])


def select(now, hours=WINDOW_H):
    """Les meilleurs : esperance decroissante, un pick par match, MAX_PICKS au plus."""
    c = sorted(value_candidates(now, hours) + prop_candidates(now, hours), key=lambda x: -x["ev"])
    out, seen = [], set()
    for x in c:
        if x["match_id"] in seen: continue
        out.append(x); seen.add(x["match_id"])
        if len(out) >= MAX_PICKS: break
    return sorted(out, key=lambda x: x["ko"])


# ─── Suivi des picks postes ──────────────────────────────────────────────────
def load():
    return _load(PATH, {"picks": []})


def record(picks, now):
    st = load()
    day = _paris(now).strftime("%Y-%m-%d")
    have = {(p["day"], p["match_id"], p["label"]) for p in st["picks"]}
    for x in picks:
        if (day, x["match_id"], x["label"]) not in have:
            st["picks"].append(dict(x, day=day, posted_at=now.isoformat(timespec="minutes")))
    PATH.write_text(json.dumps(st, ensure_ascii=False, indent=0), encoding="utf-8")


def settle_pick(p, box=None):
    """Resultat d'un pick termine ('win' / 'loss' / 'push' / None si pas encore connu) : value / meilleur prix /
    complement -> score ESPN ; prop -> feuille de match ; boost -> score et mi-temps. Partage avec la Bankroll IA."""
    kind = p.get("src") if p["kind"] in ("safe", "fill") else p["kind"]
    if kind == "boost" or p.get("src") == "boost":
        import boosts as BO
        return BO.settle((p.get("res") or {}).get("boost") or {})
    if kind in ("value", "best") and (p.get("res") or {}).get("key") and p["res"].get("espn"):
        import ev_scan
        m, nm, pt = p["res"]["key"]
        out = ev_scan._espn_result(dict(p["res"], market=m, name=nm, point=pt))
        return out[0] if out else None
    if kind == "prop" and box is not None:
        import nba_bankroll
        r = nba_bankroll.leg_result(dict(p["res"]), box)
        return {"void": "push"}.get(r, r)
    return None


def resolve(box=None, now=None):
    """Regle les picks postes termines (settle_pick)."""
    now = now or datetime.now(timezone.utc)
    st = load()
    n = 0
    for p in st["picks"]:
        if p.get("result") or now - _t(p["ko"]) < timedelta(hours=3): continue
        try:
            r = settle_pick(p, box)
        except Exception as e:
            print(f"  [!] reglement pick {p.get('label')} : {type(e).__name__}: {e}")
            r = None
        if r:
            p["result"] = r
            k = p.get("stake", 1.0)       # unites misees (paliers a mise reduite, 05/11/2026)
            p["units"] = round(k * (p["price"] - 1), 3) if r == "win" else -k if r == "loss" else 0.0
            if p.get("price_fr"):   # meme pari au prix francais estime (cote US x FR_US)
                p["units_fr"] = round(k * (p["price_fr"] - 1), 3) if r == "win" else -k if r == "loss" else 0.0
            n += 1
    if n: PATH.write_text(json.dumps(st, ensure_ascii=False, indent=0), encoding="utf-8")
    return n


def update_close(now=None):
    """Derniere probabilite sans marge vue avant le match pour chaque pick poste encore ouvert (CLV = cote prise x
    cette probabilite - 1, meme methode que le labo des strategies). Appele a chaque cycle de la veille (05/11/2026)."""
    now = now or datetime.now(timezone.utc)
    st = load()
    todo = [p for p in st["picks"] if not p.get("result") and _t(p["ko"]) > now]
    if not todo: return 0
    cur = {(x["match_id"], x["label"]): x["p_fair"]
           for x in value_candidates(now, 48, min_ev=-9) + prop_candidates(now, 48, min_ev=-9)}
    n = 0
    for p in todo:
        q = cur.get((p["match_id"], p["label"]))
        if q and q != p.get("last_p"): p["last_p"] = q; n += 1
    if n: PATH.write_text(json.dumps(st, ensure_ascii=False, indent=0), encoding="utf-8")
    return n


def stats(kinds=("value", "prop")):
    done = [p for p in load()["picks"] if p.get("result") in ("win", "loss") and p.get("kind") in kinds]
    if not done: return None
    w = sum(p["result"] == "win" for p in done)
    u = sum(p["units"] for p in done)
    staked = sum(p.get("stake", 1.0) for p in done) or 1
    # au prix francais : prix FR estime pour les picks cotes par un book US (estime aussi pour les anciens picks)
    def _ufr(p):
        if "units_fr" in p: return p["units_fr"]
        fr = fr_est(p)
        if not fr or p["result"] != "win": return p["units"]
        return round(p.get("stake", 1.0) * (fr - 1), 3)
    u_fr = sum(_ufr(p) for p in done)
    clv = [p["price"] * p["last_p"] - 1 for p in done if p.get("last_p")]
    return {"n": len(done), "w": w, "units": round(u, 2), "roi": u / staked, "since": min(p["day"] for p in done),
            "units_fr": round(u_fr, 2), "roi_fr": u_fr / staked,
            "clv": sum(clv) / len(clv) if clv else None, "clv_n": len(clv)}


# ─── Message ─────────────────────────────────────────────────────────────────
def message(picks, now):
    head = f"🎯 <b>Picks du jour</b> · {_paris(now):%d/%m}"
    s = stats()
    foot = (f"\n\n<i>Suivi du canal depuis le {s['since'][8:]}/{s['since'][5:7]} : {s['w']}-{s['n'] - s['w']}, "
            f"{s['units']:+.1f} u (ROI {100 * s['roi']:+.0f} %)"
            + (f", au prix français estimé {s['units_fr']:+.1f} u (ROI {100 * s['roi_fr']:+.0f} %)" if s["units_fr"] != s["units"] else "")
            + ".</i>" if s else
            "\n\n<i>Chaque pick posté est suivi : le bilan du canal s'affichera ici.</i>")
    best = [x for x in picks if x["kind"] == "best"]
    safe = [x for x in picks if x["kind"] == "safe"]
    boost = [x for x in picks if x["kind"] == "boost"]
    filler = [x for x in picks if x["kind"] == "fill"]
    picks = [x for x in picks if x["kind"] not in ("best", "safe", "boost", "fill")]

    def mise(x):
        k = x.get("stake", 1.0)
        return f" · mise <b>{(f'{k:g}').replace('.', ',')} u</b>"
    if not picks and not best and not safe and not boost and not filler:
        return (head + "\n\nAucun pari ne bat la marge du bookmaker pour les 24 prochaines heures. "
                "Je préfère ne rien poster que poster un pari perdant sur la durée." + foot)
    lines = [head, (f"{len(picks)} pick{'s' if len(picks) > 1 else ''}, classés par heure · probabilités recalées sur les résultats réels"
                    if picks else "Aucun pari ne bat la marge du bookmaker aujourd'hui (pas de value).")]
    for i, x in enumerate(picks, 1):
        ico = "⚽" if x["sport"] == "foot" else "🏀"
        mini = 1.0 / x["p"] * (1 + (MIN_EV_VALUE if x["kind"] == "value" else MIN_EV_PROP))
        why = (f"cote juste Pinnacle {_fr(1 / x['p_fair'])}" if x["kind"] == "value" else f"marché {round(100 * x['p_fair'])} %")
        if x.get("p_sim") is not None:
            why += f" · ma simu {round(100 * x['p_sim'])} %"
        tag = " 🛡️" if x.get("safe") else ""
        lines.append(f"\n{i}. {ico} <b>{x['match']}</b> · {_hm(_t(x['ko']))} · <i>{x['league']}</i>\n"
                     f"• <b>{x['label']}</b>{tag} — {_px(x)} (à prendre si ≥ {_fr(mini)})\n"
                     f"• proba retenue {round(100 * x['p'])} % ({why}) · espérance <b>{100 * x['ev']:+.0f} %</b>" + mise(x))
    if boost:
        lines.append("\n⚡ <b>Cotes boostées rentables</b> — boost au-dessus de sa cote juste (modèle calé sur Pinnacle, "
                     "vérifié sur 2 548 matchs), espérance ≥ +5 %.")
        for x in boost:
            lines.append(f"⚽ <b>{x['match']}</b> · {_hm(_t(x['ko']))}\n"
                         f"• <b>{x['label']}</b> — <b>@ {_fr(x['price'])}</b> Winamax (juste {_fr(1 / x['p'])})"
                         f" · espérance <b>{100 * x['ev']:+.0f} %</b>" + mise(x)
                         + (f" (Winamax plafonne à {x['max_stake']} €)" if x.get("max_stake") else ""))
    if safe:
        lines.append("\n🛡️ <b>Paris safe</b> — cote ≥ 1,40, gagnés au moins 2 fois sur 3 selon la probabilité recalée, "
                     "simulation d'accord, et la marge du bookmaker reste faible. Safe ne veut pas dire sûr : ils se perdent "
                     "environ 1 fois sur 3.")
        for x in safe:
            ico = "⚽" if x["sport"] == "foot" else "🏀"
            ref = f"juste Pinnacle {_fr(1 / x['p_fair'])}" if x["src"] == "value" else f"marché {round(100 * x['p_fair'])} %"
            lines.append(f"{ico} <b>{x['match']}</b> · {_hm(_t(x['ko']))}\n"
                         f"• <b>{x['label']}</b> — {_px(x)} · gagné {round(100 * x['p'])} % ({ref}"
                         + (f", ma simu {round(100 * x['p_sim'])} %" if x.get("p_sim") is not None else "") + ")"
                         + (f" · espérance <b>{100 * x['ev']:+.0f} %</b>" if x["ev"] >= 0 else f" · {_cl(x)} {_fr(100 * -x['ev'], 1)} %")
                         + mise(x))
        ss = stats(("safe",))
        if ss:
            lines.append(f"<i>Palier safe depuis le {ss['since'][8:]}/{ss['since'][5:7]} : {ss['w']}-{ss['n'] - ss['w']}, "
                         f"{ss['units']:+.1f} u (ROI {100 * ss['roi']:+.0f} %).</i>")
    if best:
        m = fr_margin()
        lines.append("\n💡 <b>Meilleurs prix du jour</b> — pas de la value : les paris où la marge du bookmaker est la plus "
                     "faible, simulation d'accord" + (f" (un pari français moyen coûte {_fr(100 * -m, 1)} % aujourd'hui)" if m else "") + ".")
        for x in best:
            ico = "⚽" if x["sport"] == "foot" else "🏀"
            lines.append(f"{ico} <b>{x['match']}</b> · {_hm(_t(x['ko']))}\n"
                         f"• <b>{x['label']}</b> — {_px(x)} · juste Pinnacle {_fr(1 / x['p_fair'])}"
                         f" · ma simu {round(100 * x['p_sim'])} % · coût attendu <b>{_fr(100 * -x['ev'], 1)} %</b> de la mise"
                         + mise(x))
        sb = stats(("best",))
        if sb:
            lines.append(f"<i>Palier meilleur prix depuis le {sb['since'][8:]}/{sb['since'][5:7]} : {sb['w']}-{sb['n'] - sb['w']}, "
                         f"{sb['units']:+.1f} u (ROI {100 * sb['roi']:+.0f} %).</i>")
    if filler:
        lines.append("\n➕ <b>Complément du soir</b> — pas de la value : les paris les moins chers du moment, à mise réduite "
                     "pour limiter le coût.")
        for x in filler:
            ico = "⚽" if x["sport"] == "foot" else "🏀"
            lines.append(f"{ico} <b>{x['match']}</b> · {_hm(_t(x['ko']))}\n"
                         f"• <b>{x['label']}</b> — {_px(x)} · gagné {round(100 * x['p'])} %"
                         f" · {_cl(x)} <b>{_fr(100 * max(-x['ev'], 0), 1)} %</b>" + mise(x))
        sf = stats(("fill",))
        if sf:
            lines.append(f"<i>Complément depuis le {sf['since'][8:]}/{sf['since'][5:7]} : {sf['w']}-{sf['n'] - sf['w']}, "
                         f"{sf['units']:+.1f} u (ROI {100 * sf['roi']:+.0f} %).</i>")
    try:   # props NBA facon PropsEdge : a prendre seulement si la cote francaise atteint le seuil
        import nba_props_edge as PE
        pe = PE.candidates(now)[:3]
    except Exception:
        pe = []
    if pe:
        lines.append("\n📊 <b>Props du soir</b> — marché sans marge + historique + ma simu ; rentable seulement si ta cote atteint le seuil.")
        for x in pe:
            h = x["hist"]
            lines.append(f"🏀 <b>{x['name']}</b> ({x['match']}) · {'Plus' if x['over'] else 'Moins'} de {x['line']:g} {PE.LABEL[x['stat']]}\n"
                         f"• L10 {h['l10'][0]}/{h['l10'][1]} · saison {h['season'][0]}/{h['season'][1]} · médiane {h['med']:g} · "
                         f"sans la marge {x['am_novig']:+d} → <b>prends si ≥ {_fr(x['take'])}</b>")
    return "\n".join(lines) + foot


def build(now=None):
    now = now or datetime.now(timezone.utc)
    picks = all_picks(now)
    return message(picks, now), picks


def all_picks(now=None):
    """La selection du soir, tous paliers, avec la mise de chacun (meme liste pour Telegram et le site)."""
    now = now or datetime.now(timezone.utc)
    picks = select(now)
    picks += boost_picks(now, taken={x["match_id"] for x in picks})
    picks += safe_picks(now, taken={x["match_id"] for x in picks})
    picks += best_price(now, taken={x["match_id"] for x in picks})
    picks += fill(now, taken={x["match_id"] for x in picks}, have=len(picks))
    for x in picks:
        x["stake"] = STAKE.get(x["kind"], 0.25)
        if fr_est(x): x["price_fr"] = fr_est(x)
    return picks


if __name__ == "__main__":
    import re, sys
    sys.stdout.reconfigure(encoding="utf-8")
    txt, pk = build()
    print(re.sub(r"<[^>]+>", "", txt))
    print(f"\n[{len(value_candidates(datetime.now(timezone.utc)))} value, {len(prop_candidates(datetime.now(timezone.utc)))} props au-dessus du seuil]")
