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
        h["games"][r["id"]] = {
            "id": r["id"], "ko": r["ko"], "day": day_of(r["ko"]), "league": r["league"], "league_name": r["league_name"],
            "home": r["home"]["name"], "away": r["away"]["name"], "sim_at": r["sim_at"],
            "p": {"1": r["p"]["1"], "X": r["p"]["X"], "2": r["p"]["2"], "o25": r["p"]["over"]["2.5"], "btts": r["p"]["btts"]},
            "xg": r["xg"], "goals": r["goals"], "scores": r["scores"][:5], "typical": r["replay"]["final"] if r.get("replay") else None,
            "lineups": [r["home_info"]["lineup"], r["away_info"]["lineup"]],
            "absences": [[a["name"], a["impact"]] for side in ("home", "away") for a in (r[side + "_info"].get("absences") or [])[:3]],
            "scorers": [[s_["name"], s_["p_goal"]] for side in ("home", "away") for s_ in (r[side + "_info"].get("scorers") or [])[:3]],
        }
    save(h, path)


def resolve(path=HIST_PATH, season="2026/2027"):
    """Complete les matchs termines depuis la base. Renvoie le nombre de matchs resolus."""
    h = load(path)
    todo = [g for g in h["games"].values() if not g.get("final")
            and datetime.fromisoformat(g["ko"].replace("Z", "+00:00")) < datetime.now(timezone.utc)]
    if not todo: return 0
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
        n += 1
    save(h, path)
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
    return out
