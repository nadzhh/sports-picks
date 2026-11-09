"""
improve_bot.py — Bot d'amelioration nocturne (Wilson, 05/11/2026 : « un bot actif sur les analyses, qui analyse tout
pour ameliorer la simulation ; le but est d'avoir une bankroll positive »).

Chaque nuit (GitHub Actions, improve.yml) :
  1. MESURE la simulation foot telle qu'en production (backtest walk-forward, memes tirages par match) sur deux saisons
     separees, et son ecart au marche (log-loss contre la cloture Pinnacle).
  2. ESSAIE des reglages voisins (un parametre a la fois, +/- un pas, a tour de role) et n'en ADOPTE qu'un seul s'il
     ameliore LES DEUX saisons (1X2 + plus/moins 2,5 + les deux marquent). Sinon rien ne change : un reglage qui ne
     gagne que sur une saison est du hasard. Chaque adoption est journalisee (historique = retour arriere possible).
  3. RECALE les probabilites affichees (foot_calib) sur les tirages du reglage en vigueur, si le gain tient en
     validation croisee (appris sur une saison, teste sur l'autre, dans les deux sens).
  4. AJUSTE les mises de chaque palier des picks d'apres la realite : CLV negative sur >= 40 paris regles -> mise
     divisee par 2 (jamais sous 0,1 u) ; CLV redevenue positive -> mise d'origine.
  5. PUBLIE son rapport (data/improve_report.json, carte du site) et un resume Telegram le dimanche.

Garde-fous : jamais de changement de code, seulement des valeurs dans data/model_params.json ; au plus un reglage
adopte par nuit ; seuils au-dessus du bruit (memes tirages pour toutes les variantes).
Usage : python improve_bot.py [--candidates 8] [--sims 1000] [--dry]
"""
import argparse, hashlib, json, math, os, subprocess, sys, time, traceback
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

PARAMS_PATH = Path("data/model_params.json")
REPORT_PATH = Path("data/improve_report.json")
SEASONS = (("2024-25", "2024-08-01", "2025-06-30"), ("2025-26", "2025-08-01", "2026-06-30"))
# Pas d'essai par parametre (autour de la valeur en vigueur) et bornes
SPACE = {"rest_w": (0.05, -0.3, 0.3), "cong_w": (0.03, -0.2, 0.2), "hl_days": (40.0, 80, 500), "k_team": (1.0, 0.5, 12), "rating_beta": (0.1, 0.0, 1.0),
         "intensity_sd": (0.1, 0.0, 0.8), "finish": (0.02, 0.85, 1.1), "lineup_pow": (0.2, 0.0, 2.0),
         "home_boost": (0.02, 0.9, 1.1), "k_cup": (4.0, 2, 40), "goal_w": (0.1, 0.0, 0.6), "k_share": (300.0, 200, 3000)}
MIN_GAIN, MIN_SUM = 0.0004, 0.0012       # gain minimal de score par saison et au total (memes tirages : bruit ~1e-4)
STAKE_MIN_N, STAKE_CUT = 40, -0.02
FOOT_MIN_ROWS = 500                     # matchs rejoues minimum par saison pour juger un reglage


def _now():
    return datetime.now(timezone.utc)


def load_params():
    try:
        return json.loads(PARAMS_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"foot": {}, "history": []}


def save_params(p):
    PARAMS_PATH.write_text(json.dumps(p, ensure_ascii=False, indent=1), encoding="utf-8")


def defaults():
    from foot_sim import P
    return dict(P)


# ─── Backtest d'une variante ─────────────────────────────────────────────────
def _label(params, season):
    h = hashlib.sha1(json.dumps(params, sort_keys=True).encode()).hexdigest()[:10]
    return f"bot_{season.replace('-', '')}_{h}"


def run_backtest(params, season, start, end, sims):
    """Lance foot_backtest dans un processus a part (memoire isolee) ; renvoie les lignes (cache sur disque)."""
    label = _label(params, season)
    out = Path(f"data/foot_db/backtest_{label}.json")
    if out.exists():
        return json.loads(out.read_text(encoding="utf-8"))["rows"]
    cmd = [sys.executable, "foot_backtest.py", "--from", start, "--to", end, "--params", json.dumps(params),
           "--sims", str(sims), "--label", label]
    r = subprocess.run(cmd, capture_output=True, text=True, env=dict(os.environ, PYTHONIOENCODING="utf-8"))
    if r.returncode or not out.exists():
        raise RuntimeError(f"backtest {label} : {r.stderr[-300:]}")
    return json.loads(out.read_text(encoding="utf-8"))["rows"]


def _ll(p, y):
    p = min(max(p, 1e-4), 1 - 1e-4)
    return -(y * math.log(p) + (1 - y) * math.log(1 - p))


def score(rows):
    """Score a minimiser : log-loss moyen 1X2 + plus/moins 2,5 + les deux marquent (tous matchs evalues)."""
    s, n = 0.0, 0
    for r in rows:
        h, a = r["score"][:2]
        y = 0 if h > a else 1 if h == a else 2
        s += -math.log(max([r["p1"], r["px"], r["p2"]][y], 1e-4)) + _ll(r["po25"], h + a > 2.5) + _ll(r["pbtts"], h > 0 and a > 0)
        n += 1
    return s / max(n, 1)


def market_gap(rows):
    """(log-loss 1X2 simulation, log-loss 1X2 Pinnacle cloture, n) sur les matchs cotes."""
    K = [r for r in rows if (r.get("mkt") or {}).get("1x2")]
    if not K: return None
    y = lambda r: 0 if r["score"][0] > r["score"][1] else 1 if r["score"][0] == r["score"][1] else 2
    ls = sum(-math.log(max([r["p1"], r["px"], r["p2"]][y(r)], 1e-4)) for r in K) / len(K)
    lm = sum(-math.log(max(r["mkt"]["1x2"][y(r)], 1e-4)) for r in K) / len(K)
    return round(ls, 4), round(lm, 4), len(K)


# ─── Choix des essais du soir ────────────────────────────────────────────────
def candidates(cur, k, start_idx):
    names = list(SPACE)
    out = []
    for i in range(len(names)):
        name = names[(start_idx + i) % len(names)]
        step, lo, hi = SPACE[name]
        for sgn in (-1, 1):
            v = round(cur.get(name, 0) + sgn * step, 4)
            if lo <= v <= hi: out.append((name, v))
        if len(out) >= k: break
    return out[:k], (start_idx + i + 1) % len(names)


# ─── Recalage (foot_calib) re-appris sur les tirages en vigueur ─────────────
def refit_calibration(rows_by_season):
    """Apprend T, D (1X2) et les echelles +2,5 / deux marquent sur une saison, teste sur l'autre, dans les deux sens ;
    renvoie les parametres appris sur les deux saisons si le gain tient dans les deux sens, sinon None."""
    import numpy as np

    def fit3(R):
        P = np.clip(np.array([[r["p1"], r["px"], r["p2"]] for r in R]), 1e-6, 1)
        Y = np.array([0 if r["score"][0] > r["score"][1] else 1 if r["score"][0] == r["score"][1] else 2 for r in R])
        def ll(t, d):
            L = t * np.log(P); L[:, 1] += d
            E = np.exp(L - L.max(1, keepdims=True)); E /= E.sum(1, keepdims=True)
            return float(-np.mean(np.log(E[np.arange(len(Y)), Y])))
        best = min(((ll(t, d), t, d) for t in np.linspace(0.8, 1.5, 36) for d in np.linspace(-0.2, 0.2, 21)))
        return best[1:], ll, ll(1.0, 0.0)

    def fitb(R, key, yf):
        p = np.clip(np.array([r[key] for r in R]), 1e-6, 1 - 1e-6); y = np.array([yf(r) for r in R], float)
        z = np.log(p / (1 - p))
        def ll(a, b):
            q = np.clip(1 / (1 + np.exp(-(a * z + b))), 1e-6, 1 - 1e-6)
            return float(-np.mean(y * np.log(q) + (1 - y) * np.log(1 - q)))
        best = min(((ll(a, b), a, b) for a in np.linspace(0.4, 1.3, 46) for b in np.linspace(-0.3, 0.3, 25)))
        return best[1:], ll, ll(1.0, 0.0)

    yo = lambda r: sum(r["score"][:2]) > 2.5
    yb = lambda r: r["score"][0] > 0 and r["score"][1] > 0
    S = list(rows_by_season.values())
    ok = True
    for tr, te in ((S[0], S[1]), (S[1], S[0])):
        (t, d), _, _ = fit3(tr); _, ll3, base3 = fit3(te)
        (a1, b1), _, _ = fitb(tr, "po25", yo); _, llo, baseo = fitb(te, "po25", yo)
        (a2, b2), _, _ = fitb(tr, "pbtts", yb); _, llb, baseb = fitb(te, "pbtts", yb)
        if not (ll3(t, d) < base3 and llo(a1, b1) < baseo and llb(a2, b2) < baseb): ok = False
    if not ok: return None
    A = S[0] + S[1]
    (t, d), _, _ = fit3(A); (a1, b1), _, _ = fitb(A, "po25", yo); (a2, b2), _, _ = fitb(A, "pbtts", yb)
    return {"T": round(float(t), 3), "D": round(float(d), 3), "O25": [round(float(a1), 3), round(float(b1), 3)],
            "BTTS": [round(float(a2), 3), round(float(b2), 3)]}


# ─── Mises par palier d'apres la realite ─────────────────────────────────────
def adjust_stakes(params):
    import picks_du_jour as PJ
    base = {"value": 1.0, "prop": 1.0, "boost": 1.0, "safe": 0.5, "best": 0.25, "fill": 0.25}
    cur = dict(base, **(params.get("stakes") or {}))
    notes = []
    for kinds, key in ((("value", "prop"), "value"), (("boost",), "boost"), (("safe",), "safe"),
                       (("best",), "best"), (("fill",), "fill")):
        s = PJ.stats(kinds)
        if not s or s.get("clv") is None or s.get("clv_n", 0) < STAKE_MIN_N: continue
        if s["clv"] < STAKE_CUT and cur[key] > 0.1:
            new = max(0.1, round(cur[key] / 2, 2)); notes.append(f"{key} : CLV {100 * s['clv']:+.1f} % sur {s['clv_n']} -> mise {cur[key]:g} -> {new:g} u")
            cur[key] = new
        elif s["clv"] >= 0 and cur[key] < base[key]:
            notes.append(f"{key} : CLV {100 * s['clv']:+.1f} % -> mise rendue a {base[key]:g} u"); cur[key] = base[key]
    if "value" in cur: cur["prop"] = cur["value"]
    params["stakes"] = {k: v for k, v in cur.items() if v != base[k]}
    return notes


# ─── Lecons de la veille (06/11/2026 : « d'une soiree a l'autre, ameliore-les selon les erreurs de la veille ») ──
# Exclusion seulement sur des paris nombreux ET repartis sur plusieurs soirs : le 05/11, 98 props de 2 matchs seulement
# (tres liees : un match a faible score fait gagner presque tous les « moins ») auraient sinon compte comme 98 preuves.
EXCL_N, EXCL_CLV, EXCL_Z, EXCL_NIGHTS = 30, -0.04, 2.0, 5


def _records():
    """Paris suivis et regles : canal Picks, paris conviction de la Bankroll IA, props du soir.
    -> [{seg, p, win, clv, ko, label, price, src}]"""
    import picks_du_jour as PJ
    out = []
    for p in PJ.load().get("picks") or []:
        if p.get("result") not in ("win", "loss"): continue
        out.append({"seg": PJ.segment(p), "p": p["p"], "win": p["result"] == "win", "ko": p["ko"], "label": p["label"],
                    "match": p["match"], "price": p["price"], "src": "canal",
                    "clv": (p["price"] * p["last_p"] - 1) if p.get("last_p") else None})
    try:
        bk = json.loads(Path("data/nba_bankroll.json").read_text(encoding="utf-8"))
        for b in bk.get("bets") or []:
            if b.get("kind") != "conv" or b["status"] not in ("won", "lost"): continue
            pj = b["legs"][0]["pj"]
            out.append({"seg": PJ.segment(pj), "p": b["p"], "win": b["status"] == "won", "ko": pj["ko"], "label": pj["label"],
                        "match": pj["match"], "price": b["odds"], "src": "bankroll", "clv": None})
    except (OSError, ValueError, KeyError):
        pass
    try:
        seen = set()   # une seule fois par joueur / stat / sens et par match (la ligne bouge dans la soiree)
        for p in json.loads(Path("data/props_edge.json").read_text(encoding="utf-8")).get("props") or []:
            if p.get("result") not in ("win", "loss"): continue
            k = (p["gid"], p["id"], p["stat"], p["over"])
            if k in seen: continue
            seen.add(k)
            seg = f'nba · {p["stat"]} {"plus" if p["over"] else "moins"}'
            out.append({"seg": seg, "p": p["p"], "win": p["result"] == "win", "ko": p["ko"], "label": f'{p["name"]} {p["stat"]} {p["line"]:g}',
                        "match": p["match"], "price": p["fair"], "src": "props", "clv": None, "gid": p["gid"]})
    except (OSError, ValueError, KeyError):
        pass
    return out


def lessons(params, now=None):
    """Bilan de la veille + suivi cumule par type de pari ; ecarte un type qui perd de facon repetee, le reintegre quand
    il redevient bon. Jamais sur un seul soir : au moins EXCL_N paris regles."""
    now = now or _now()
    R = _records()
    yday = (now - timedelta(days=1)).strftime("%Y-%m-%d")
    hier = [r for r in R if r["ko"][:10] in (yday, now.strftime("%Y-%m-%d"))]
    segs = {}
    for r in R:
        s = segs.setdefault(r["seg"], {"n": 0, "w": 0, "exp": 0.0, "var": 0.0, "clv": [], "nights": set()})
        s["n"] += 1; s["w"] += r["win"]; s["exp"] += r["p"]; s["var"] += r["p"] * (1 - r["p"]); s["nights"].add(r["ko"][:10])
        if r["clv"] is not None: s["clv"].append(r["clv"])
    ex = set(params.get("exclude") or [])
    notes = []
    for k, s in segs.items():
        if s["n"] < EXCL_N or len(s["nights"]) < EXCL_NIGHTS: continue
        clv = sum(s["clv"]) / len(s["clv"]) if len(s["clv"]) >= EXCL_N // 2 else None
        z = (s["w"] - s["exp"]) / math.sqrt(max(s["var"], 1e-9))
        bad = (clv is not None and clv <= EXCL_CLV) or z <= -EXCL_Z
        good = (clv is None or clv >= 0) and z >= 0
        if bad and k not in ex:
            ex.add(k); notes.append(f"écarté : {k} ({s['w']}/{s['n']} gagnés pour {s['exp']:.1f} attendus"
                                    + (f", CLV {100 * clv:+.1f} %" if clv is not None else "") + ")")
        elif good and k in ex:
            ex.discard(k); notes.append(f"réintégré : {k} ({s['w']}/{s['n']} gagnés pour {s['exp']:.1f} attendus)")
    params["exclude"] = sorted(ex)
    table = sorted(({"seg": k, "n": s["n"], "w": s["w"], "exp": round(s["exp"], 1),
                     "clv": round(sum(s["clv"]) / len(s["clv"]), 4) if s["clv"] else None} for k, s in segs.items()),
                   key=lambda x: -x["n"])
    props = [r for r in hier if r["src"] == "props"]
    return {"hier": [{k: r[k] for k in ("label", "match", "price", "p", "win", "src")} for r in hier if r["src"] != "props"],
            "props": {"n": len(props), "w": sum(r["win"] for r in props), "exp": round(sum(r["p"] for r in props), 1),
                      "matchs": len({r.get("gid") for r in props})},
            "segments": table[:12], "exclus": sorted(ex), "notes": notes}


def foot_round(P, rep, cur, k, sims, workers, dry):
    """Une nuit de reglage foot (modifie P et rep)."""
    cands, nxt = candidates(cur, k, P.get("next", 0))
    variants = [("base", None, cur)] + [(name, v, dict(cur, **{name: v})) for name, v in cands]
    with ThreadPoolExecutor(max_workers=workers) as pool:   # chaque (variante, saison) = un processus
        futs = {(i, s): pool.submit(run_backtest, prm, s, a, b, sims)
                for i, (_, _, prm) in enumerate(variants) for s, a, b in SEASONS}
        rows = {key: f.result() for key, f in futs.items()}
    thin = [s for s, _, _ in SEASONS if len(rows[(0, s)]) < FOOT_MIN_ROWS]
    if thin:   # base foot du CI incomplete : on ne juge rien sur un echantillon trop petit
        rep["notes"].append(f"foot : moins de {FOOT_MIN_ROWS} matchs rejoues sur {', '.join(thin)} "
                            f"({', '.join(str(len(rows[(0, s)])) for s in thin)}), aucun reglage juge cette nuit")
        return
    base_rows = {s: rows[(0, s)] for s, _, _ in SEASONS}
    base = {s: round(score(R), 5) for s, R in base_rows.items()}
    rep["base"] = base
    rep["gap"] = {s: market_gap(R) for s, R in base_rows.items()}
    results = []
    for i, (name, v, _) in enumerate(variants[1:], 1):
        sc = {s: round(score(rows[(i, s)]), 5) for s, _, _ in SEASONS}
        gains = {s: round(base[s] - sc[s], 5) for s in base}
        results.append({"param": name, "from": cur.get(name), "to": v, "score": sc, "gain": gains})
        print(f"  essai {name} {cur.get(name)} -> {v} : gains {gains}", flush=True)
    rep["tried"] = results
    P["next"] = nxt
    ok = [r for r in results if all(g >= MIN_GAIN for g in r["gain"].values()) and sum(r["gain"].values()) >= MIN_SUM]
    if ok:
        best = max(ok, key=lambda r: sum(r["gain"].values()))
        rep["adopted"] = best
        if not dry:
            P.setdefault("history", []).append({"at": rep["at"], "param": best["param"], "from": best["from"], "to": best["to"],
                                                 "gain": best["gain"], "base": base})
            P.setdefault("foot", {})[best["param"]] = best["to"]
            P.pop("foot_calib_for", None)      # le recalage est a refaire sur les nouveaux tirages (nuit suivante)
    # recalage sur les tirages du reglage en vigueur (si pas deja fait pour ce reglage)
    key = hashlib.sha1(json.dumps(cur, sort_keys=True).encode()).hexdigest()[:10]
    if not rep["adopted"] and P.get("foot_calib_for") != key:
        cal = refit_calibration(base_rows)
        if cal:
            rep["notes"].append(f"recalage re-appris (validation croisee OK) : {cal}")
            if not dry: P["foot_calib"], P["foot_calib_for"] = cal, key
        else:
            rep["notes"].append("recalage : pas de gain stable en validation croisee, valeurs gardees")
            if not dry: P["foot_calib_for"] = key


# ─── NBA (06/11/2026, Wilson : « ok pour les points 1 a 4 » -> le bot regle aussi la simulation NBA) ───────────
# Mesure : backtest « aveugle » de nba_backtest (sans fuite : effectif du match precedent, pas de ligne Vegas)
# contre les VRAIES cotes de props (ESPN BET 2025-26, DraftKings 2026-27), log-loss de P(over) simulee.
# Meme regle que le foot : un parametre a la fois, adopte seulement s'il ameliore LES DEUX echantillons.
NBA_SAMPLES = (("2025-26", 2026), ("2026-27", 2027))
NBA_SPACE = {"usage_pow": (0.05, 0.9, 1.5), "ast_pow": (0.05, 0.9, 1.5), "hl_rates": (5.0, 10, 80),
             "hl_minutes": (1.0, 2, 12), "k_usage": (30.0, 30, 400), "usage_k": (2.0, 4, 40),
             "min_sd_scale": (0.1, 0.5, 1.5), "wowy_min": (0.1, 0.0, 1.0), "mkt_min_alpha": (0.1, 0.0, 1.0),
             "blowout_slope": (0.002, 0.0, 0.02), "vol_c": (0.1, 0.0, 1.0), "shoot_c": (0.1, 0.3, 1.5)}
NBA_MIN_GAIN, NBA_MIN_SUM, NBA_MIN_PROPS = 0.0005, 0.0012, 1000
NBA_DB = Path("data/nba_db")


def nba_defaults():
    from nba_sim import P   # valeurs du code + reglages deja adoptes (cle "nba")
    return dict(P)


def _nba_stamp(year):
    """Taille des fichiers de la saison : les resultats en cache sont refaits des qu'il y a de nouveaux matchs."""
    return "-".join(str((NBA_DB / f).stat().st_size) if (NBA_DB / f).exists() else "0"
                    for f in (f"box_{year - 1}.json", f"box_{year}.json", f"props_{year}.json"))


def nba_backtest(params, year, sims):
    """[(p_sim, p_marche, over reel, stat)] sur les vraies lignes (poussees exclues), via nba_backtest.py --blind."""
    h = hashlib.sha1((json.dumps(params, sort_keys=True) + _nba_stamp(year)).encode()).hexdigest()[:10]
    label = f"bot_nba_{year}_{h}"
    out = NBA_DB / f"backtest_{label}.json"
    if not out.exists():
        cmd = [sys.executable, "nba_backtest.py", "--year", str(year), "--params", json.dumps(params),
               "--sims", str(sims), "--blind", "--label", label]
        r = subprocess.run(cmd, capture_output=True, text=True, env=dict(os.environ, PYTHONIOENCODING="utf-8"))
        if r.returncode or not out.exists():
            raise RuntimeError(f"backtest {label} : {r.stderr[-300:]}")
    D = json.loads(out.read_text(encoding="utf-8"))
    return [(r["p_sim"], r["p_mkt"], r["actual"] > r["line"], r["stat"]) for r in D.get("real") or []
            if r["actual"] != r["line"] and r.get("p_play", 1) >= 0.9]


def nba_score(rows):
    return sum(_ll(p, y) for p, _, y, *_ in rows) / max(len(rows), 1)


def nba_gap(rows):
    if not rows: return None
    return round(nba_score(rows), 4), round(sum(_ll(m, y) for _, m, y, *_ in rows) / len(rows), 4), len(rows)


def nba_refresh():
    """Cotes de props historiques (ESPN, gratuit) : saison en cours mise a jour chaque nuit (incremental),
    2025-26 recuperee une fois puis gardee dans le cache CI."""
    notes = []
    for _, y in NBA_SAMPLES:
        if not (NBA_DB / f"box_{y}.json").exists():
            notes.append(f"NBA : base des matchs {y - 1}-{str(y)[2:]} absente"); continue
        if y < NBA_SAMPLES[-1][1] and (NBA_DB / f"props_{y}.json").exists(): continue
        try:
            subprocess.run([sys.executable, "nba_props_hist.py", str(y)], capture_output=True, text=True, timeout=5400,
                           env=dict(os.environ, PYTHONIOENCODING="utf-8"))
        except Exception as e:
            notes.append(f"NBA : cotes de props {y} non mises a jour ({type(e).__name__})")
    return notes


def nba_round(P, rep, k=4, sims=1000, workers=4, dry=False):
    """Une nuit de reglage NBA ; modifie P (cle "nba", "next_nba", "history") sauf en --dry."""
    R = {"tried": [], "adopted": None, "notes": nba_refresh()}
    rep["nba"] = R
    samples = [(s, y) for s, y in NBA_SAMPLES if (NBA_DB / f"props_{y}.json").exists() and (NBA_DB / f"box_{y}.json").exists()]
    if len(samples) < 2:
        R["notes"].append("NBA : il faut les deux echantillons de vraies cotes pour juger un reglage, essai reporte")
        return R
    cur = nba_defaults()
    names = list(NBA_SPACE)
    cands, i0 = [], P.get("next_nba", 0)
    for j in range(len(names)):
        name = names[(i0 + j) % len(names)]
        step, lo, hi = NBA_SPACE[name]
        cands += [(name, round(cur[name] + sg * step, 4)) for sg in (-1, 1) if lo <= round(cur[name] + sg * step, 4) <= hi]
        if len(cands) >= k: break
    cands = cands[:k]
    variants = [("base", None, cur)] + [(n, v, dict(cur, **{n: v})) for n, v in cands]
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futs = {(i, s): pool.submit(nba_backtest, prm, y, sims) for i, (_, _, prm) in enumerate(variants) for s, y in samples}
        rows = {key: f.result() for key, f in futs.items()}
    small = [s for s, _ in samples if len(rows[(0, s)]) < NBA_MIN_PROPS]
    base = {s: round(nba_score(rows[(0, s)]), 5) for s, _ in samples}
    R["base"], R["gap"] = base, {s: nba_gap(rows[(0, s)]) for s, _ in samples}
    # calage des probabilites de props par stat (nba_prop_calib, 09/11) : appris sur la saison passee, juge sur la
    # saison en cours, garde seulement s'il bat le marche hors echantillon
    if not dry:
        try:
            import nba_prop_calib as PC
            R["calib"] = PC.refit(rows[(0, samples[0][0])], rows[(0, samples[-1][0])], rep["at"])
        except Exception as e:
            R["notes"].append(f"NBA : calage des props non refait ({type(e).__name__}: {e})"[:300])
    for i, (name, v, _) in enumerate(variants[1:], 1):
        sc = {s: round(nba_score(rows[(i, s)]), 5) for s, _ in samples}
        gains = {s: round(base[s] - sc[s], 5) for s in base}
        R["tried"].append({"param": name, "from": cur[name], "to": v, "score": sc, "gain": gains})
        print(f"  NBA essai {name} {cur[name]} -> {v} : gains {gains}", flush=True)
    P["next_nba"] = (i0 + j + 1) % len(names)
    if small:
        R["notes"].append(f"NBA : moins de {NBA_MIN_PROPS} props sur {', '.join(small)}, rien n'est adopte")
        return R
    ok = [r for r in R["tried"] if all(g >= NBA_MIN_GAIN for g in r["gain"].values()) and sum(r["gain"].values()) >= NBA_MIN_SUM]
    if ok:
        best = max(ok, key=lambda r: sum(r["gain"].values()))
        R["adopted"] = best
        if not dry:
            P.setdefault("history", []).append({"at": rep["at"], "sport": "nba", "param": best["param"], "from": best["from"],
                                                 "to": best["to"], "gain": best["gain"], "base": base})
            P.setdefault("nba", {})[best["param"]] = best["to"]
    return R


def _cleanup_nba():
    for f in NBA_DB.glob("backtest_bot_nba_*.json"):
        if time.time() - f.stat().st_mtime > 2 * 86400: f.unlink()


# ─── Nuit ────────────────────────────────────────────────────────────────────
def run(k=8, sims=1000, dry=False, workers=4, nba_k=4):
    t0 = time.time()
    P = load_params()
    cur = dict(defaults(), **(P.get("foot") or {}))
    rep = {"at": _now().isoformat(timespec="minutes"), "sims": sims, "tried": [], "adopted": None, "notes": []}
    try:
        foot_round(P, rep, cur, k, sims, workers, dry)
    except Exception as e:
        rep["notes"].append(f"foot : erreur {type(e).__name__}: {e}"[:400])
        rep["trace"] = traceback.format_exc()[-1500:]
        print(rep["trace"], flush=True)
    if nba_k:
        try:
            nba_round(P, rep, nba_k, sims, workers, dry)
        except Exception as e:
            rep.setdefault("nba", {"tried": [], "adopted": None, "notes": []})["notes"].append(f"NBA : {type(e).__name__}: {e}"[:300])
            rep["trace_nba"] = traceback.format_exc()[-1500:]
            print(rep["trace_nba"], flush=True)
    try:
        rep["notes"] += adjust_stakes(P)
    except Exception as e:
        rep["notes"].append(f"mises : {type(e).__name__}: {e}")
    try:   # objectif de Wilson : series de 10 paris positives (series10.py)
        import series10
        rep["series"] = series10.text()
    except Exception as e:
        rep["series"] = [f"series : {type(e).__name__}: {e}"]
    try:   # lecons de la veille : bilan, suivi par type de pari, exclusions / reintegrations
        rep["lecons"] = lessons(P)
        rep["notes"] += rep["lecons"]["notes"]
    except Exception as e:
        rep["notes"].append(f"lecons : {type(e).__name__}: {e}")
    try:   # post-mortem des paris regles : malchance ou erreur de lecture, probabilites annoncees vs reelles
        import postmortem
        pm = postmortem.run(write=not dry)
        rep["postmortem"] = postmortem.text(pm)
    except Exception as e:
        rep["notes"].append(f"post-mortem : {type(e).__name__}: {e}")
    rep["params"] = P.get("foot") or {}
    rep["minutes"] = round((time.time() - t0) / 60, 1)
    hist = []
    try:
        hist = json.loads(REPORT_PATH.read_text(encoding="utf-8")).get("nights") or []
    except (OSError, ValueError):
        pass
    if not dry:
        save_params(P)
        night = {k2: rep.get(k2) for k2 in ("at", "base", "gap", "adopted", "notes")}
        if rep.get("nba"): night["nba"] = {k2: rep["nba"].get(k2) for k2 in ("base", "gap", "adopted", "calib")}
        REPORT_PATH.write_text(json.dumps({"last": rep, "nights": (hist + [night])[-60:]},
                                          ensure_ascii=False, indent=1), encoding="utf-8")
    _cleanup(); _cleanup_nba()
    return rep


def _cleanup():
    """Garde les sorties de backtest du bot des 3 derniers jours seulement (cache des variantes)."""
    for f in Path("data/foot_db").glob("backtest_bot_*.json"):
        if time.time() - f.stat().st_mtime > 3 * 86400: f.unlink()


def summary(rep):
    """Resume texte (Telegram du dimanche, carte du site)."""
    g = rep.get("gap") or {}
    gap = " ; ".join(f"{s} : simu {v[0]:.4f} / Pinnacle {v[1]:.4f}" for s, v in g.items() if v)
    a = rep.get("adopted")
    lines = [f"🤖 <b>Bot d'amélioration</b> · nuit du {rep['at'][8:10]}/{rep['at'][5:7]}",
             f"{len(rep['tried'])} réglages essayés sur 2 saisons ({rep['minutes']} min).",
             (f"✅ Adopté : <b>{a['param']}</b> {a['from']} → {a['to']} (gain {sum(a['gain'].values()):.4f} sur les deux saisons)"
              if a else "Aucun réglage n'améliore les deux saisons : rien ne change (c'est voulu)."),
             f"Écart au marché (log-loss 1X2) : {gap}" if gap else "Écart au marché foot : non mesuré cette nuit."]
    lines += [f"• {n}" for n in rep.get("notes") or []]
    N = rep.get("nba") or {}
    if N.get("tried") or N.get("notes"):
        b = N.get("adopted")
        ng = " ; ".join(f"{s} : simu {v[0]:.4f} / bookmaker {v[1]:.4f} ({v[2]} props)" for s, v in (N.get("gap") or {}).items() if v)
        lines.append(f"🏀 NBA : {len(N.get('tried') or [])} réglages essayés · "
                     + (f"✅ adopté <b>{b['param']}</b> {b['from']} → {b['to']}" if b else "aucun n'améliore les deux échantillons"))
        if ng: lines.append(f"Écart au marché props (log-loss) : {ng}")
        lines += [f"• {n}" for n in N.get("notes") or []]
    L = rep.get("lecons") or {}
    if L.get("hier"):
        w = sum(r["win"] for r in L["hier"])
        lines.append(f"\nVeille : {w}/{len(L['hier'])} gagnés (attendu {sum(r['p'] for r in L['hier']):.1f}).")
        lines += [f"{'✅' if r['win'] else '❌'} {r['label']} @ {r['price']:.2f} ({round(100 * r['p'])} % annoncé)" for r in L["hier"][:8]]
    lines += [f"📋 {x}" for x in rep.get("postmortem") or []]
    lines += [f"🔟 {x}" for x in rep.get("series") or []]
    pr = L.get("props") or {}
    if pr.get("n"):
        lines.append(f"Props du soir : {pr['w']}/{pr['n']} gagnées (attendu {pr['exp']:.1f}) sur {pr['matchs']} match(s) : "
                     "très liées entre elles, ne pas en tirer de conclusion avant plusieurs soirs.")
    if L.get("exclus"):
        lines.append("Types de pari écartés : " + ", ".join(L["exclus"]))
    return "\n".join(lines)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--candidates", type=int, default=8)
    ap.add_argument("--sims", type=int, default=1000)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--dry", action="store_true")
    ap.add_argument("--nba-candidates", type=int, default=4, help="reglages NBA essayes par nuit (0 = pas de NBA)")
    ap.add_argument("--only-nba", action="store_true", help="test : seulement la partie NBA (sans ecrire)")
    ap.add_argument("--telegram", action="store_true", help="envoie le resume (le dimanche, en CI)")
    a = ap.parse_args()
    if a.only_nba:
        rep = {"at": _now().isoformat(timespec="minutes")}
        print(json.dumps(nba_round(load_params(), rep, a.nba_candidates, a.sims, a.workers, dry=True), ensure_ascii=False, indent=1))
        sys.exit(0)
    rep = run(a.candidates, a.sims, a.dry, a.workers, a.nba_candidates)
    txt = summary(rep)
    print(txt)
    if a.telegram and _now().weekday() == 6 and os.environ.get("GITHUB_ACTIONS"):
        try:
            import notify
            notify.tg_send(txt, chat_id=notify.TELEGRAM_CHAT_ID_BANKROLL)
        except Exception as e:
            print(f"[!] Telegram : {e}")
