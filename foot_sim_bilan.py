"""
foot_sim_bilan.py — Historique du simulateur foot et bilan de chaque journee.

1. archive(games) : derniere simulation d'avant-match de chaque match (ecrasee tant qu'il n'a pas
   commence) -> data/foot_sim_history.json (versionne, publie par la veille).
2. resolve() : score, mi-temps, xG reel, cartons rouges, buteurs depuis la base foot (foot_db).
3. day_report(jour) : favori trouve ?, probabilite donnee au vrai resultat, log-loss, buts et xG
   prevus / reels, buteurs annonces qui ont marque ; insights() : pistes sur le cumul.
"""
import json, math
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

import foot_db

HIST_PATH = Path("data/foot_sim_history.json")
try:
    from zoneinfo import ZoneInfo
    TZ = ZoneInfo("Europe/Paris")
except Exception:
    TZ = timezone(timedelta(hours=1))


def day_of(iso):
    return datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(TZ).date().isoformat()


def load(path=HIST_PATH):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"games": {}}


def save(h, path=HIST_PATH):
    Path(path).write_text(json.dumps(h, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")


def archive(games, path=HIST_PATH):
    h = load(path)
    for r in games:
        old = h["games"].get(r["id"])
        if old and old.get("final"): continue
        pr = r.get("p_raw") or r["p"]
        anc = r.get("anchor") or {}
        h["games"][r["id"]] = {
            "id": r["id"], "ko": r["ko"], "day": day_of(r["ko"]), "league": r["league"], "league_name": r["league_name"],
            "home": r["home"]["name"], "away": r["away"]["name"], "sim_at": r["sim_at"],
            # p = probabilites BRUTES de la simulation (etudes de calibration) ; p_cal = celles affichees (foot_calib)
            "p": {"1": pr["1"], "X": pr["X"], "2": pr["2"], "o25": pr["over"]["2.5"], "btts": pr["btts"]},
            "p_cal": {"1": r["p"]["1"], "X": r["p"]["X"], "2": r["p"]["2"], "o25": r["p"]["over"]["2.5"], "btts": r["p"]["btts"]},
            "xg": r["xg"], "goals": r["goals"], "scores": r["scores"][:5], "typical": r["replay"]["final"] if r.get("replay") else None,
            "lineups": [r["home_info"]["lineup"], r["away_info"]["lineup"]],
            "absences": [[a["name"], a["impact"]] for side in ("home", "away") for a in (r[side + "_info"].get("absences") or [])[:3]],
            "scorers": [[s_["name"], s_["p_goal"]] for side in ("home", "away") for s_ in (r[side + "_info"].get("scorers") or [])[:3]],
            # True : simulation rejouee apres coup (etat d'avant le jour du match, composition officielle),
            # pas archivee en direct avant le coup d'envoi
            "replayed": bool(r.get("replayed")),
        }
        # premiere cote vue (celle qu'on pouvait prendre, comme les picks NBA) : paris fictifs du simulateur
        if old and old.get("odds"):
            h["games"][r["id"]]["odds"], h["games"][r["id"]]["odds_src"] = old["odds"], old.get("odds_src")
        elif anc.get("prices"):
            h["games"][r["id"]]["odds"] = anc["prices"]
            h["games"][r["id"]]["odds_src"] = (anc.get("book") or "bookmaker") + " avant le match"
        if r.get("shots"):   # tirs / tirs cadres prevus (par equipe) : mesures contre le reel au reglement
            h["games"][r["id"]]["shots"] = [r["shots"]["home"], r["shots"]["away"]]
    save(h, path)


def resolve(path=HIST_PATH, season="2026/2027"):
    """Complete les matchs termines depuis la base. Renvoie le nombre de matchs resolus."""
    h = load(path)
    todo = [g for g in h["games"].values() if not g.get("final")
            and datetime.fromisoformat(g["ko"].replace("Z", "+00:00")) < datetime.now(timezone.utc)]
    if not todo:   # cotes de cloture des journees passees (paris fictifs), meme sans nouveau resultat
        n_cl = closing_backfill(h)
        if n_cl: save(h, path)
        return n_cl
    dbs = {lid: foot_db.load(lid, season) for lid in {g["league"] for g in todo}}
    n = 0
    for g in todo:
        r = dbs.get(g["league"], {}).get(g["id"])
        if not r: continue
        names = {p[0]: p[1] for p in r["players"]}
        g["final"] = r["score"]; g["ht"] = r.get("ht")
        g["xg_real"] = [round(sum(s[6] for s in r["shots"] if s[0] == side), 2) for side in (1, 0)]
        g["reds"] = sum(1 for c in r["cards"] if c[4] in ("Red", "YellowRed"))
        g["goalscorers"] = [names.get(s[1], "") for s in r["shots"] if s[8] == "Goal" and not s[13]]
        # tirs et tirs cadres reels (cadre = arret ou but ; FotMob marque aussi « cadres » les tirs contres)
        g["shots_real"] = [[sum(1 for s in r["shots"] if s[0] == side and not s[13]),
                            sum(1 for s in r["shots"] if s[0] == side and not s[13] and ((s[11] and not s[12]) or s[8] == "Goal"))]
                           for side in (1, 0)]
        n += 1
    n_cl = closing_backfill(h)
    save(h, path)
    return n or n_cl


# ─── Paris fictifs du simulateur (Wilson, 17/11/2026 : « pour le foot, fais pareil : le bilan et le +/- u ») ───────
# Comme les picks du simulateur NBA : 1 u sur chaque cote ou la simulation SEULE (probabilites brutes, sans ancrage au
# marche) voit au moins +5 % d'esperance au prix du bookmaker ; au plus un pari 1X2 et un pari +/-2,5 buts par match.
# Banc d'essai, pas des conseils : le 29/10/2026, suivre la simulation contre Pinnacle perdait sur deux saisons.
# Prix : premiere cote DraftKings vue avant le match (ESPN) ; a defaut (journees passees), cloture Pinnacle
# (football-data.co.uk, Top 5 seulement).
BET_EV = 0.05
BET_LAB = {"1": "victoire {home}", "X": "match nul", "2": "victoire {away}", "o25": "plus de 2,5 buts", "u25": "moins de 2,5 buts"}
FD_LEAGUES = {"47": "E0", "87": "SP1", "54": "D1", "55": "I1", "53": "F1"}


def bets(g):
    """Paris fictifs d'un match : [{k, label, price, p, ev, result, units}] (result None tant que non joue)."""
    o, p = g.get("odds") or {}, g.get("p") or {}
    if not o or not p: return []
    probs = {"1": p.get("1"), "X": p.get("X"), "2": p.get("2"), "o25": p.get("o25"),
             "u25": None if p.get("o25") is None else 1 - p["o25"]}
    out = []
    for keys in (("1", "X", "2"), ("o25", "u25")):
        c = [(probs[k] * o[k] - 1, k) for k in keys if probs.get(k) and o.get(k)]
        if not c: continue
        ev, k = max(c)
        if ev < BET_EV: continue
        b = {"k": k, "label": BET_LAB[k].format(home=g["home"], away=g["away"]), "price": round(o[k], 2),
             "p": round(probs[k], 3), "ev": round(ev, 3), "result": None, "units": 0.0}
        f = g.get("final")
        if f:
            won = {"1": f[0] > f[1], "X": f[0] == f[1], "2": f[0] < f[1], "o25": sum(f) > 2.5, "u25": sum(f) < 2.5}[k]
            b["result"], b["units"] = ("win", round(o[k] - 1, 3)) if won else ("loss", -1.0)
        out.append(b)
    return out


def bets_summary(games):
    B = [b for g in games for b in bets(g) if b["result"]]
    if not B: return None
    w = sum(b["result"] == "win" for b in B)
    u = sum(b["units"] for b in B)
    return {"n": len(B), "w": w, "l": len(B) - w, "units": round(u, 2), "roi": u / len(B)}


def _closing_rows(code, season="2627", max_age_h=20):
    """Lignes football-data de la saison (rafraichies si le fichier a plus de max_age_h heures)."""
    import csv, io, time, urllib.request
    p = Path("data/foot_db/odds") / f"{code}_{season}.csv"
    if not p.exists() or time.time() - p.stat().st_mtime > max_age_h * 3600:
        try:
            raw = urllib.request.urlopen(urllib.request.Request(
                f"https://www.football-data.co.uk/mmz4281/{season}/{code}.csv", headers={"User-Agent": "Mozilla/5.0"}), timeout=30).read()
            p.parent.mkdir(parents=True, exist_ok=True)
            tmp = p.with_suffix(".part"); tmp.write_bytes(raw); tmp.replace(p)
        except Exception as e:
            print(f"  [!] cotes de cloture {code} indisponibles ({type(e).__name__})")
            if not p.exists(): return []
    return [r for r in csv.DictReader(io.StringIO(p.read_bytes().decode("utf-8-sig", "ignore"))) if r.get("HomeTeam")]


def closing_backfill(h):
    """Matchs termines du Top 5 sans cote archivee : cloture Pinnacle (football-data). Renvoie le nombre complete."""
    import difflib
    from foot_backtest import _norm
    todo = [g for g in h["games"].values() if g.get("final") and not g.get("odds") and str(g["league"]) in FD_LEAGUES]
    if not todo: return 0
    sim = lambda a, b: difflib.SequenceMatcher(None, _norm(a), _norm(b)).ratio() + (0.3 if _norm(a) in _norm(b) or _norm(b) in _norm(a) else 0)
    rows = {}
    n = 0
    for g in todo:
        code = FD_LEAGUES[str(g["league"])]
        if code not in rows: rows[code] = _closing_rows(code)
        day = datetime.fromisoformat(g["ko"].replace("Z", "+00:00")).date()
        best, bs = None, 1.2
        for r in rows[code]:
            try:
                d, mo, y = r["Date"].split("/")
                if abs((datetime(int(y) + (2000 if len(y) == 2 else 0), int(mo), int(d)).date() - day).days) > 1: continue
            except Exception:
                continue
            sc = sim(g["home"], r["HomeTeam"]) + sim(g["away"], r["AwayTeam"])
            if sc > bs: best, bs = r, sc
        if not best: continue
        o = {}
        for k, cols in (("1", ("PSCH", "AvgCH")), ("X", ("PSCD", "AvgCD")), ("2", ("PSCA", "AvgCA")),
                        ("o25", ("PC>2.5", "AvgC>2.5")), ("u25", ("PC<2.5", "AvgC<2.5"))):
            for c in cols:
                try:
                    v = float(best.get(c) or 0)
                except ValueError:
                    v = 0
                if v > 1: o[k] = v; break
        if len(o) >= 3:
            g["odds"], g["odds_src"] = o, "clôture Pinnacle (football-data)"
            n += 1
    return n


def _res(score):
    return "1" if score[0] > score[1] else ("X" if score[0] == score[1] else "2")


def _summary(games):
    done = [g for g in games if g.get("final")]
    if not done: return None
    fav = [max(("1", "X", "2"), key=lambda k: g["p"][k]) for g in done]
    hit = sum(f == _res(g["final"]) for f, g in zip(fav, done))
    ll = [-math.log(max(g["p"][_res(g["final"])], 1e-4)) for g in done]
    o = [(g["p"]["o25"], sum(g["final"]) > 2.5) for g in done]
    return {"n": len(done), "hit": hit, "p_true": sum(g["p"][_res(g["final"])] for g in done) / len(done),
            "ll": sum(ll) / len(ll), "o_hit": sum((p >= 0.5) == y for p, y in o),
            "goals_sim": sum(sum(g["goals"]) for g in done) / len(done), "goals_real": sum(sum(g["final"]) for g in done) / len(done),
            "xg_sim": sum(sum(g["xg"]) for g in done) / len(done), "xg_real": sum(sum(g["xg_real"]) for g in done) / len(done),
            "scorer_hit": sum(1 for g in done for nm, p in g.get("scorers") or [] if p >= 0.35 and nm in (g.get("goalscorers") or [])),
            "scorer_n": sum(1 for g in done for nm, p in g.get("scorers") or [] if p >= 0.35)}


def days(h=None):
    h = h or load()
    out = defaultdict(lambda: [0, 0])
    for g in h["games"].values():
        out[g["day"]][0] += 1; out[g["day"]][1] += bool(g.get("final"))
    return {d: tuple(v) for d, v in sorted(out.items(), reverse=True)}


def day_report(day, h=None):
    h = h or load()
    games = sorted((g for g in h["games"].values() if g["day"] == day), key=lambda g: g["ko"])
    return {"day": day, "games": games, "sum": _summary(games), "done": sum(bool(g.get("final")) for g in games), "total": len(games)}


def insights(h=None):
    """Pistes sur le cumul (garde-fous d'echantillon)."""
    h = h or load()
    s = _summary(list(h["games"].values()))
    if not s: return ["Pas encore de match termine dans l'historique."]
    out = []
    if s["n"] < 150:
        out.append(f"Échantillon : {s['n']} matchs. Sous ~150 matchs, l'écart au hasard reste grand : on juge d'abord "
                   "la probabilité moyenne donnée au vrai résultat (0,33 = hasard pur, ~0,38-0,40 = niveau du marché).")
    out.append(f"Probabilité moyenne donnée au résultat qui s'est produit : {s['p_true']:.2f} ; favori du simulateur "
               f"trouvé {s['hit']}/{s['n']} fois.".replace(".", ",", 1))
    gb = s["goals_sim"] - s["goals_real"]
    if s["n"] >= 30 and abs(gb) >= 0.25:
        out.append(f"Buts : le simulateur en attend {s['goals_sim']:.2f} par match pour {s['goals_real']:.2f} réels "
                   f"({'sur' if gb > 0 else 'sous'}-estimation) → recaler la finition / le niveau des attaques si ça se confirme.")
    xb = s["xg_sim"] - s["xg_real"]
    if s["n"] >= 30 and abs(xb) >= 0.25:
        out.append(f"xG : {s['xg_sim']:.2f} attendus pour {s['xg_real']:.2f} réels par match : le niveau des équipes "
                   f"est {'sur' if xb > 0 else 'sous'}-évalué en début de saison.")
    if s["scorer_n"] >= 20:
        out.append(f"Buteurs annoncés à 35 % ou plus : {s['scorer_hit']}/{s['scorer_n']} ont marqué "
                   f"({100 * s['scorer_hit'] / s['scorer_n']:.0f} %).")
    sh = [g for g in h["games"].values() if g.get("shots") and g.get("shots_real")]
    if len(sh) >= 10:   # tirs prevus vs reels, par equipe (suivi en direct depuis le 04/11/2026)
        n = 2 * len(sh)
        ps = sum(g["shots"][i][0] for g in sh for i in (0, 1)) / n; rs = sum(g["shots_real"][i][0] for g in sh for i in (0, 1)) / n
        pt = sum(g["shots"][i][1] for g in sh for i in (0, 1)) / n; rt = sum(g["shots_real"][i][1] for g in sh for i in (0, 1)) / n
        out.append(f"Tirs par équipe ({len(sh)} matchs) : {ps:.1f} prévus pour {rs:.1f} réels ; tirs cadrés {pt:.1f} prévus pour "
                   f"{rt:.1f} réels.".replace(".", ","))
    return out
