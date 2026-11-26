"""
foot_live_alerts.py — Alerte live de 2e mi-temps (Wilson, 26/11/2026 : « une équipe qui domine en 1re mais ne
conclut pas peut marquer en 2e ; pour une cote pas dégueu, attendre la 60e »).

Pas de cote en direct disponible (ESPN ne publie que les cotes d'avant-match) : la Bankroll IA ne peut donc pas
jouer ce pari (jamais de cote inventée). C'est une ALERTE pour Wilson, envoyée sur le canal Telegram « Bankroll IA » :
entre la 52e et la 68e minute, une équipe qui ne mène pas, n'a pas encore marqué et domine (tirs cadrés, tirs) ->
ma probabilité qu'elle marque d'ici la fin et la cote minimum à prendre. Une alerte par équipe et par match ;
suivie à part (data/foot_live_alerts.json) : réussite et rendement si on avait pris la cote minimum.

Modèle (simple, PAS encore vérifié) : buts restants ~ Poisson(lambda), lambda = buts attendus de l'équipe avant le
match (simulation foot ancrée sur la cote) x minutes restantes / 90 x bonus de domination (1 + 0,08 par tir cadré
d'écart, au plus x1,5). Pour 60 min jouées, à cote minimum = 1 / probabilité x 1,05.
"""
import json
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path

import foot_player_props as FP

PATH = Path("data/foot_live_alerts.json")
WIN = (52, 68)
RULE = {"sot_min": 3, "sot_ratio": 2.0, "shots_ratio": 1.5, "edge": 1.05, "boost": 0.08, "boost_max": 1.5}


def _stats(t):
    return {s.get("name"): s.get("displayValue") for s in t.get("statistics") or []}


def _f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return 0.0


def minute(e):
    st = (e.get("status") or (e.get("competitions") or [{}])[0].get("status") or {})
    try:
        return int(str(st.get("displayClock") or "0").split("'")[0].split("+")[0])
    except ValueError:
        return 0


def check(now=None):
    """Nouvelles alertes (liste de dicts) et mise a jour du fichier."""
    now = now or datetime.now(timezone.utc)
    st = FP._load(PATH, {"alerts": []})
    done = {(a["eid"], a["side"]) for a in st["alerts"]}
    sims = (FP._load("data/foot_sim.json", {}) or {}).get("games") or []
    new = []
    for slug, e in FP.events(now, -timedelta(hours=2, minutes=30), timedelta(0)):
        stt = ((e.get("status") or {}).get("type") or {})
        if stt.get("state") != "in" or (e.get("status") or {}).get("period") != 2: continue
        mn = minute(e)
        if not WIN[0] <= mn <= WIN[1]: continue
        d = FP._get(f"https://site.api.espn.com/apis/site/v2/sports/soccer/{slug}/summary?event={e['id']}")
        comp = ((d.get("header") or {}).get("competitions") or [{}])[0]
        score = {c.get("homeAway"): int(_f(c.get("score"))) for c in comp.get("competitors") or []}
        names = {c.get("homeAway"): (c.get("team") or {}).get("displayName") for c in comp.get("competitors") or []}
        box = {}
        for t in (d.get("boxscore") or {}).get("teams") or []:
            box[t.get("homeAway") or ("home" if t is (d["boxscore"]["teams"][0]) else "away")] = _stats(t)
        if set(box) != {"home", "away"}: continue
        g = FP._sim_game(e, sims)
        for side, opp in (("home", "away"), ("away", "home")):
            if (e["id"], side) in done or score.get(side, 0) > 0 or score.get(side, 0) > score.get(opp, 0): continue
            sot, sot_o = _f(box[side].get("shotsOnTarget")), _f(box[opp].get("shotsOnTarget"))
            sh, sh_o = _f(box[side].get("totalShots")), _f(box[opp].get("totalShots"))
            if sot < RULE["sot_min"] or sot < RULE["sot_ratio"] * sot_o or sh < RULE["shots_ratio"] * sh_o: continue
            gexp = ((g or {}).get("goals") or [1.35, 1.1])[0 if side == "home" else 1]
            boost = min(1 + RULE["boost"] * (sot - sot_o), RULE["boost_max"])
            lam = gexp * max(95 - mn, 1) / 90 * boost
            p = 1 - math.exp(-lam)
            a = {"eid": e["id"], "slug": slug, "side": side, "team": names.get(side), "match": f'{names.get("home")} - {names.get("away")}',
                 "league": FP.SLUGS.get(slug), "minute": mn, "score": f'{score.get("home", 0)}-{score.get("away", 0)}',
                 "sot": [int(sot), int(sot_o)], "shots": [int(sh), int(sh_o)], "poss": box[side].get("possessionPct"),
                 "xg_pre": round(gexp, 2), "p": round(p, 4), "min_odds": round(RULE["edge"] / p, 2),
                 "at": now.isoformat(timespec="minutes"), "result": None}
            new.append(a); done.add((e["id"], side))
    if new:
        st["alerts"] += new
        FP._save(PATH, st)
    return new


def settle(now=None):
    """L'équipe a-t-elle marqué après l'alerte ? Renvoie les alertes réglées."""
    st = FP._load(PATH, {"alerts": []})
    out = []
    for a in st["alerts"]:
        if a.get("result"): continue
        d = FP._get(f"https://site.api.espn.com/apis/site/v2/sports/soccer/{a['slug']}/summary?event={a['eid']}")
        comp = ((d.get("header") or {}).get("competitions") or [{}])[0]
        if ((comp.get("status") or {}).get("type") or {}).get("state") != "post": continue
        scored = False
        for k in d.get("keyEvents") or []:
            if not k.get("scoringPlay"): continue
            if not FP.same((k.get("team") or {}).get("displayName"), a["team"]): continue
            if _f((k.get("clock") or {}).get("value")) / 60 >= a["minute"] - 0.5: scored = True
        a["result"] = "win" if scored else "loss"
        out.append(a)
    if out: FP._save(PATH, st)
    return out


def stats():
    al = [a for a in FP._load(PATH, {"alerts": []})["alerts"] if a.get("result")]
    w = sum(a["result"] == "win" for a in al)
    roi = (sum((a["min_odds"] - 1) if a["result"] == "win" else -1 for a in al) / len(al)) if al else None
    return {"n": len(al), "win": w, "roi_min": roi, "p_mean": (sum(a["p"] for a in al) / len(al)) if al else None}


def telegram(new):
    out = ["⏱️ <b>Alerte live 2e mi-temps</b> <i>(pour toi, hors bankroll : pas de cote en direct pour elle)</i>"]
    for a in new:
        out.append(f'\n⚽ <b>{a["match"]}</b> ({a["league"]}) · {a["minute"]}e, {a["score"]}')
        out.append(f'<b>{a["team"]}</b> domine sans marquer : {a["sot"][0]} tirs cadrés contre {a["sot"][1]}, '
                   f'{a["shots"][0]} tirs contre {a["shots"][1]}' + (f', {str(a["poss"]).replace(".", ",")} % de possession' if a.get("poss") else ""))
        out.append(f'Pari : <b>{a["team"]} marque d’ici la fin</b> · ma probabilité {round(100 * a["p"])} % → '
                   f'à prendre si la cote est ≥ <b>{a["min_odds"]:.2f}</b>'.replace(".", ","))
    s = stats()
    out.append("\n<i>Modèle simple, pas encore vérifié" + (f' · suivi : {s["win"]}/{s["n"]} réussies' if s["n"] else "") + ".</i>")
    return "\n".join(out)


def telegram_settled(done):
    s = stats()
    lines = [f'{"✅" if a["result"] == "win" else "❌"} {a["team"]} ({a["match"]}, alerte à la {a["minute"]}e)' for a in done]
    return ("⏱️ <b>Alertes live réglées</b>\n" + "\n".join(lines)
            + (f'\nSuivi : {s["win"]}/{s["n"]} réussies, rendement à la cote minimum {100 * s["roi_min"]:+.0f} %' if s["n"] else ""))
