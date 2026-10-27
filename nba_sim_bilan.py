"""
nba_sim_bilan.py — Historique du simulateur NBA et bilan de chaque soiree.

1. archive(games) : a chaque passage du cron, garde la DERNIERE simulation d'avant-match de chaque
   match (score attendu, minutes et stats projetees par joueur) -> data/nba_sim_history.json
   (versionne, comme data/nba_sim_picks.json : la CI commite data/*.json).
2. resolve(year) : une fois le match joue, ajoute le score final et les vraies stats (base box-score).
3. night_report(night) : bilan d'une soiree — picks (resultat, CLV, cause de chaque pick perdu),
   matchs (simu vs Vegas vs reel), erreurs de minutes — et pistes d'amelioration sur le cumul.

Usage : python nba_sim_bilan.py [2026-10-27]   # bilan texte (defaut : derniere soiree terminee)
"""
import json, math, sys
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

HIST_PATH = Path("data/nba_sim_history.json")
PICKS_PATH = Path("data/nba_sim_picks.json")   # = nba_sim_run.PICKS_PATH
STATS = ("MIN", "PTS", "REB", "AST", "3PM", "STL", "BLK")
DETAIL_DAYS = 60       # au-dela, on ne garde que le resume du match (taille du fichier versionne)
MIN_MISS = 6           # ecart de minutes (prevu / joue) qui suffit a expliquer un pick perdu
STAT_FR = {"AST": "passes", "3PM": "paniers à 3 pts", "STL": "interceptions", "BLK": "contres",
           "PTS": "points", "REB": "rebonds"}


def night_of(iso):
    """Soiree NBA (date americaine) d'un coup d'envoi UTC : les matchs de 03:00Z sont ceux de la veille."""
    return (datetime.fromisoformat(iso.replace("Z", "+00:00")) - timedelta(hours=6)).date().isoformat()


def _load(path, default):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def load_history(path=HIST_PATH):
    return _load(path, {"games": {}})


def load_picks(path=PICKS_PATH):
    return _load(path, {"picks": {}}).get("picks", {})


def _save(hist, path=HIST_PATH):
    Path(path).write_text(json.dumps(hist, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")


def archive(games, path=HIST_PATH):
    """Derniere simulation d'avant-match de chaque match (ecrasee a chaque passage tant qu'il n'a pas commence)."""
    hist = load_history(path)
    now = datetime.now(timezone.utc).isoformat(timespec="minutes")
    for r in games:
        old = hist["games"].get(r["gid"])
        if old and old.get("final"): continue
        t, L = r["team"], r.get("lines") or {}
        hist["games"][r["gid"]] = {
            "date": r["date"], "night": night_of(r["date"]), "sim_at": now,
            "home": r["home"]["abbr"], "away": r["away"]["abbr"],
            "sim": {"home": t["home_mean"], "away": t["away_mean"], "p_home": t["p_home_win"]},
            "vegas": {"total": L.get("total"), "spread": L.get("home_spread")},
            # id -> [nom, cote (home/away), proba de jouer, minutes, PTS, REB, AST, 3PM, STL, BLK] (moyennes s'il joue)
            "players": {p["id"]: [p["name"], p["side"], p["p_play"], p["MIN"]]
                        + [(p.get(s) or {}).get("mean") for s in STATS[1:]]
                        for p in r["players"] if p["p_play"] >= 0.2}}
    _save(hist, path)


def resolve(year, path=HIST_PATH):
    """Score final et vraies stats des matchs archives qui sont termines."""
    from nba_boxdb import load_season
    hist = load_history(path)
    todo = [gid for gid, h in hist["games"].items() if not h.get("final")]
    if todo:
        box = {**load_season(year - 1), **load_season(year)}
        for gid in todo:
            g = box.get(gid)
            if not g: continue
            h = hist["games"][gid]
            h["final"] = {"home": g["home"]["pts"], "away": g["away"]["pts"]}
            h["actual"] = {p["id"]: [p.get(s, 0) for s in STATS] for sd in ("home", "away")
                           for p in g[sd].get("players") or [] if p.get("MIN") and not p.get("dnp")}
            # joueurs qui ont joue sans etre prevus : leur nom n'est pas dans la simulation
            h["names"] = {p["id"]: [p["name"], sd] for sd in ("home", "away")
                          for p in g[sd].get("players") or []
                          if p.get("MIN") and not p.get("dnp") and p["id"] not in h.get("players", {})}
    # Allege les vieilles soirees : le resume suffit, le detail joueur ne sert plus au bilan
    cut = (date.today() - timedelta(days=DETAIL_DAYS)).isoformat()
    for h in hist["games"].values():
        if h.get("final") and h["night"] < cut and "players" in h:
            for k in ("players", "actual", "names"): h.pop(k, None)
    _save(hist, path)
    return sum(1 for gid in todo if hist["games"][gid].get("final"))


# ── Bilan ────────────────────────────────────────────────────────────────────

def _dec(am):
    return None if am is None else (1 + am / 100 if am > 0 else 1 + 100 / -am)


def _pick_detail(r, h):
    """Pick enrichi : minutes et stat projetees / reelles, CLV, cause s'il est perdu."""
    d = dict(r)
    pl = (h or {}).get("players", {}).get(r["id"])
    act = (h or {}).get("actual", {}).get(r["id"])
    i = STATS.index(r["stat"]) if r["stat"] in STATS else None
    d["proj_min"] = pl[3] if pl else None
    d["proj"] = pl[4 + i - 1] if pl and i else None
    d["act_min"] = act[0] if act else None
    lp = r.get("last_p_mkt")
    d["clv"] = lp * _dec(r["first_price"]) - 1 if lp is not None else None
    d["moved"] = (lp - r["first_p_mkt"]) if lp is not None and r.get("v") == 2 else None
    d["cause"], d["cause_txt"] = "", ""
    res = r.get("result")
    if res == "void":
        d["cause"], d["cause_txt"] = "void", "n'a pas joué : pari remboursé"
    elif res == "loss":
        over, a, line = r["side"] == "o", r.get("actual"), r["line"]
        pm, am = d["proj_min"], d["act_min"]
        if pm and am is not None and ((am - pm >= MIN_MISS and not over) or (pm - am >= MIN_MISS and over)):
            d["cause"] = "minutes"
            d["cause_txt"] = f"minutes : {am:.0f} min jouées pour {pm:.0f} prévues"
        elif a is not None and abs(a - line) <= 1:
            d["cause"], d["cause_txt"] = "near", f"perdu d'un rien : {a:g} pour une ligne à {line:g}"
        else:
            d["cause"] = "rate"
            proj = f" (projeté {d['proj']:.1f} en {pm:.0f} min)" if d["proj"] is not None and pm else ""
            d["cause_txt"] = f"rendement : {a:g} en {am:.0f} min{proj}" if am is not None else "rendement"
    return d


def _summary(picks):
    done = [p for p in picks if p.get("result") in ("win", "loss", "push")]
    w = sum(p["result"] == "win" for p in done); l = sum(p["result"] == "loss" for p in done)
    units = sum(_dec(p["first_price"]) - 1 if p["result"] == "win" else -1 if p["result"] == "loss" else 0 for p in done)
    clv = [p["clv"] for p in picks if p.get("clv") is not None and p.get("result") != "void"]
    # sens du mouvement, parmi les cotes qui ont bouge (une cote immobile ne dit rien)
    moved = [p["moved"] for p in picks if p.get("moved") is not None and abs(p["moved"]) > 0.005
             and p.get("result") != "void"]
    by = defaultdict(lambda: [0, 0])
    for p in done:
        if p["result"] in ("win", "loss"):
            for k in (p["stat"], "over" if p["side"] == "o" else "under"):
                by[k][p["result"] == "loss"] += 1
    return {"n": len(done), "w": w, "l": l, "p": len(done) - w - l,
            "void": sum(p.get("result") == "void" for p in picks),
            "pending": sum(not p.get("result") for p in picks),
            "units": units, "roi": units / len(done) if done else None,
            "clv": sum(clv) / len(clv) if clv else None,
            "with_us": sum(m > 0 for m in moved) / len(moved) if moved else None, "n_moved": len(moved),
            "causes": {c: sum(p["cause"] == c for p in picks) for c in ("minutes", "near", "rate")},
            "by": dict(by)}


def _games(hist, night=None):
    return {gid: h for gid, h in hist["games"].items() if (night is None or h["night"] == night)}


def _game_rows(games):
    rows = []
    for gid, h in sorted(games.items(), key=lambda x: x[1]["date"]):
        f, s, v = h.get("final"), h["sim"], h.get("vegas") or {}
        row = {"gid": gid, "home": h["home"], "away": h["away"], "sim": s, "final": f, "vegas": v}
        if f:
            row["winner_ok"] = (s["p_home"] >= 0.5) == (f["home"] > f["away"])
            row["vegas_ok"] = None if v.get("spread") is None else (v["spread"] <= 0) == (f["home"] > f["away"])
            row["err_total"] = s["home"] + s["away"] - f["home"] - f["away"]
            row["err_total_vegas"] = None if not v.get("total") else v["total"] - f["home"] - f["away"]
        rows.append(row)
    return rows


def _minutes(games):
    """Erreurs de minutes (joueurs prevus a 10+ min ou qui ont joue 10+ min) et biais par stat."""
    errs, misses, bias = [], [], defaultdict(list)
    for h in games.values():
        if not h.get("final") or "players" not in h: continue
        act, pls = h.get("actual", {}), h["players"]
        for pid, pl in pls.items():
            a = act.get(pid)
            am = a[0] if a else 0
            pm = pl[3] * pl[2]   # minutes attendues, absence comprise
            if max(pm, am) < 10: continue
            errs.append(am - pm)
            if abs(am - pm) >= 10:
                misses.append({"name": pl[0], "team": h[pl[1]], "proj": pm, "act": am, "dnp": not a})
            if a and am >= 10:
                for j, s in enumerate(STATS[1:], 1):
                    if pl[3 + j] is not None:
                        bias[s].append((a[j] - pl[3 + j] * am / max(pl[3], 1), a[j]))   # a minutes egales
        for pid, (name, sd) in (h.get("names") or {}).items():
            am = act[pid][0]
            if am >= 10:
                errs.append(am); misses.append({"name": name, "team": h[sd], "proj": 0, "act": am, "dnp": False})
    misses.sort(key=lambda m: -abs(m["act"] - m["proj"]))
    return {"n": len(errs), "mae": sum(map(abs, errs)) / len(errs) if errs else None, "misses": misses,
            "bias": {s: (sum(x for x, _ in v) / len(v), sum(y for _, y in v) / len(v), len(v)) for s, v in bias.items()}}


def nights(hist=None):
    hist = hist or load_history()
    out = defaultdict(lambda: [0, 0])
    for h in hist["games"].values():
        out[h["night"]][0] += 1
        out[h["night"]][1] += bool(h.get("final"))
    return {n: tuple(v) for n, v in sorted(out.items(), reverse=True)}


def night_report(night, hist=None, picks=None):
    hist = hist or load_history()
    picks = picks if picks is not None else load_picks()
    games = _games(hist, night)
    pk = [_pick_detail(r, hist["games"].get(r["gid"])) for r in picks.values() if night_of(r["date"]) == night]
    pk.sort(key=lambda p: (p["date"], p["team"], p["name"]))
    return {"night": night, "games": _game_rows(games), "picks": pk, "sum": _summary(pk), "min": _minutes(games),
            "done": sum(bool(h.get("final")) for h in games.values()), "total": len(games)}


def cumulative(hist=None, picks=None):
    hist = hist or load_history()
    picks = picks if picks is not None else load_picks()
    pk = [_pick_detail(r, hist["games"].get(r["gid"])) for r in picks.values()]
    games = {gid: h for gid, h in hist["games"].items() if h.get("final")}
    return {"picks": pk, "sum": _summary(pk), "games": _game_rows(games), "min": _minutes(games),
            "nights": len({h["night"] for h in games.values()})}


def _f(x, d=1):
    return f"{x:.{d}f}".replace(".", ",")


def insights(rep, cum):
    """Pistes d'amelioration : ce que disent la soiree et surtout le cumul (avec garde-fous d'echantillon)."""
    out, s, c = [], rep["sum"], cum["sum"]
    n = c["n"]
    if n < 200:
        out.append(f"Échantillon : {n} pick{'s' if n > 1 else ''} réglé{'s' if n > 1 else ''} depuis le début. "
                   f"Sur 100 picks, le hasard seul fait varier le résultat d'environ ±{math.sqrt(100):.0f} unités : "
                   "le gain ou la perte d'une soirée ne prouve rien. Le juge rapide, c'est la CLV (le marché va-t-il dans notre sens ?).")
    if c["with_us"] is not None and c["n_moved"] >= 10:
        ok = c["with_us"] >= 0.6
        out.append(f"Mouvement des cotes : le marché est allé dans notre sens sur {100 * c['with_us']:.0f} % des picks "
                   f"({c['n_moved']} cotes qui ont bougé ; 74-80 % au backtest). "
                   + ("Le signal fonctionne : continuer, en prenant les cotes le plus tôt possible." if ok else
                      "Moins bien qu'au backtest : si ça reste sous 60 % après 100 picks, relever l'écart minimal "
                      "(PICK_GAP 0,08 → 0,10) ou retirer la stat la plus faible."))
    lost = s["l"]
    if lost:
        cm = s["causes"]
        parts = [f"{cm['minutes']} à cause des minutes", f"{cm['near']} d'une seule unité", f"{cm['rate']} sur le rendement"]
        out.append(f"Picks perdus ce soir ({lost}) : " + ", ".join(p for p in parts if not p.startswith("0 ")) + ".")
    tot_lost = c["l"]
    if tot_lost >= 8 and c["causes"]["minutes"] / tot_lost >= 0.35:
        out.append(f"Minutes : {c['causes']['minutes']} des {tot_lost} picks perdus depuis le début viennent d'un temps de jeu "
                   "mal prévu. C'est la priorité : modèle de minutes (rotation des remplaçants, retours de blessure, "
                   "matchs à sens unique où les titulaires sortent tôt).")
    m = cum["min"]
    if m["mae"] is not None and m["n"] >= 40:
        out.append(f"Précision des minutes (cumul) : erreur moyenne {_f(m['mae'])} min par joueur sur {m['n']} joueurs ; "
                   f"{len(m['misses'])} écarts de 10 min ou plus.")
    for k, lab in (("over", "Overs"), ("under", "Unders")):
        w, l = c["by"].get(k, (0, 0))
        if w + l >= 25 and w / (w + l) < 0.47:
            out.append(f"{lab} : {w}-{l} ({100 * w / (w + l):.0f} %). Si la tendance tient, revoir la correction "
                       f"de biais (SIM_BIAS = 0,025) dans ce sens.")
    for st in ("3PM", "AST", "STL", "BLK"):
        w, l = c["by"].get(st, (0, 0))
        if w + l >= 30 and w / (w + l) < 0.45:
            out.append(f"{STAT_FR[st].capitalize()} : {w}-{l} ({100 * w / (w + l):.0f} %) sur {w + l} picks : "
                       "candidate au retrait de la sélection si la CLV est aussi négative.")
    for st, (b, mean, k) in sorted(m["bias"].items()):
        if k >= 150 and mean > 0 and abs(b) / mean >= 0.08:
            out.append(f"Biais {STAT_FR.get(st, st)} : à minutes égales, le simulateur {'sous' if b > 0 else 'sur'}-estime "
                       f"de {100 * abs(b) / mean:.0f} % ({k} joueurs) → recalage à étudier (valider sur les 2 périodes de backtest).")
    g = [r for r in cum["games"] if r.get("final")]
    gv = [r for r in g if r.get("err_total_vegas") is not None]
    if len(gv) >= 15:
        es = sum(abs(r["err_total"]) for r in gv) / len(gv); ev = sum(abs(r["err_total_vegas"]) for r in gv) / len(gv)
        out.append(f"Scores ({len(gv)} matchs) : erreur moyenne sur le total {_f(es)} pts pour le simulateur, "
                   f"{_f(ev)} pour Vegas" + (" : normal, le simulateur s'aligne en partie sur Vegas." if es >= ev else "."))
    if not out:
        out.append("Pas encore assez de matchs joués pour en tirer une piste.")
    return out


def text_report(night=None):
    hist, picks = load_history(), load_picks()
    ns = nights(hist)
    if night is None:
        night = next((n for n, (t, d) in ns.items() if d), None)
    if not night:
        return "Aucune soirée terminée dans l'historique."
    rep, cum = night_report(night, hist, picks), cumulative(hist, picks)
    s = rep["sum"]
    L = [f"=== Bilan de la soirée NBA du {night} ({rep['done']}/{rep['total']} matchs terminés) ===",
         f"Picks : {s['w']}-{s['l']}" + (f"-{s['p']}" if s["p"] else "") + f" · {s['units']:+.2f} u"
         + (f" (ROI {100 * s['roi']:+.1f} %)" if s["roi"] is not None else "")
         + (f" · CLV {100 * s['clv']:+.1f} %" if s["clv"] is not None else "")
         + (f" · {s['void']} remboursé(s)" if s["void"] else "") + (f" · {s['pending']} en attente" if s["pending"] else "")]
    for p in rep["picks"]:
        res = {"win": "GAGNE", "loss": "PERDU", "push": "nul", "void": "rembourse"}.get(p.get("result"), "attente")
        L.append(f"  {res:9s} {p['name']:24s} {p['team']:4s} {'Over' if p['side'] == 'o' else 'Under'} {p['line']:g} {p['stat']:3s}"
                 f" @ {_dec(p['first_price']):.2f}  reel {p.get('actual', '-')!s:>3s}"
                 + (f"  min {p['proj_min']:.0f}->{p['act_min']:.0f}" if p["proj_min"] and p["act_min"] is not None else "")
                 + (f"  [{p['cause_txt']}]" if p["cause_txt"] else ""))
    L.append("Matchs :")
    for g in rep["games"]:
        f = g["final"]
        L.append(f"  {g['away']:>4s} @ {g['home']:<4s} simu {g['sim']['away']:.0f}-{g['sim']['home']:.0f}"
                 + (f"  final {f['away']}-{f['home']}  vainqueur {'OK' if g['winner_ok'] else 'raté'}" if f else "  (à venir)"))
    if rep["min"]["misses"]:
        L.append("Plus gros écarts de minutes :")
        for m in rep["min"]["misses"][:8]:
            L.append(f"  {m['name']} ({m['team']}) : prévu {m['proj']:.0f} min, joué {m['act']:.0f}" + (" (absent)" if m["dnp"] else ""))
    L.append("Pistes :")
    L += [f"  - {t}" for t in insights(rep, cum)]
    return "\n".join(L)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    print(text_report(sys.argv[1] if len(sys.argv) > 1 else None))
