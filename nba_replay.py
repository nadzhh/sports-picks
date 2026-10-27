"""
nba_replay.py — Deroule du « match type » pour le lecteur video du site.

A partir du journal de nba_poss_sim (toutes les actions de toutes les simulations),
reconstruit le match choisi par pick_typical sous forme d'evenements lisibles :
changements, tirs (position reelle tiree des tirs du joueur, type de tir selon ses
habitudes), contres, passes, rebonds, pertes de balle, fautes, lancers, fins de
periode, avec le score et la probabilite de victoire en direct.

Chaque evenement porte une priorite : la version condensee (~3 min) montre les
actions marquantes et la fin de match, et fait defiler le reste en accelere.
"""
import math

import numpy as np

from nba_pbp_profiles import ZONES
from nba_poss_sim import K_FT, K_IFOUL, K_NONE, K_SHOT, K_TO, CTX_DREB, CTX_OREB, CTX_STEAL

SHOT_FR = {
    "92": "tir en suspension", "93": "bras roulé", "94": "claquette", "95": "lay-up", "96": "dunk",
    "109": "lay-up en course", "110": "lay-up en pénétration", "111": "alley-oop", "112": "lay-up à revers",
    "113": "tir en course", "114": "tir en pivot", "115": "dunk en pénétration", "116": "dunk lancé",
    "117": "dunk à revers", "118": "alley-oop dunk", "119": "bras roulé en pénétration", "120": "bras roulé en pivot",
    "121": "fadeaway", "122": "tir avec la planche", "124": "finger roll", "125": "lay-up après rebond",
    "126": "lay-up à revers", "127": "lay-up à revers", "128": "finger roll", "129": "finger roll",
    "130": "floater", "131": "pull-up", "132": "step-back", "133": "pull-up avec la planche", "137": "fadeaway en pivot",
    "138": "dunk après rebond", "141": "lay-up sur coupe", "144": "floater", "146": "pull-up en course",
    "149": "alley-oop dunk", "150": "tip dunk", "151": "dunk sur coupe", "152": "dunk à revers", "153": "dunk à revers",
}
ZONE_DEFAULT = {"RIM": ("95", "110", "96"), "PAINT": ("130", "93", "114"), "MID": ("92", "131", "121"),
                "C3": ("92",), "AB3": ("92", "131", "132")}
ZONE_XY = {"RIM": (25, 2), "PAINT": (25, 9), "MID": (12, 14), "C3": (2, 5), "AB3": (25, 26)}
TO_FR = [("mauvaise passe", 0.42), ("perte de balle", 0.30), ("marcher", 0.08), ("passage en force", 0.10),
         ("sortie de terrain", 0.06), ("violation des 24 secondes", 0.04)]
CTX_FR = {0: "demi-terrain", CTX_DREB: "transition", CTX_STEAL: "contre-attaque", CTX_OREB: "seconde chance"}
PERIOD_FR = {1: "1er quart-temps", 2: "2e quart-temps", 3: "3e quart-temps", 4: "4e quart-temps"}


def _last(name):
    parts = str(name).split()
    if len(parts) >= 2 and parts[-1].rstrip(".") in ("Jr", "Sr", "II", "III", "IV"):
        return parts[-2]
    return parts[-1] if parts else str(name)


def _wp(margin_home, t_elapsed, mu, sigma):
    """Probabilite de victoire domicile en cours de match (marche aleatoire gaussienne)."""
    frac = max(0.0, (2880.0 - t_elapsed) / 2880.0)
    if frac <= 0:
        return 1.0 if margin_home > 0 else (0.0 if margin_home < 0 else 0.5)
    z = (margin_home + mu * frac) / (sigma * math.sqrt(frac) + 0.6)
    return 0.5 * (1 + math.erf(z / math.sqrt(2)))


def _clock(per, t, p_end):
    rem = max(0.0, p_end - t)
    return round(rem, 1)


def build_replay(res, k, inp, profiles, jerseys, meta, seed=0):
    """Deroule du match k. meta : {home:{abbr,name,color,alt}, away:{...}, date, lines, books_p_home}."""
    rng = np.random.default_rng(seed)
    L = res["log"]
    teams = res["teams"]
    nh = res["n_home"]
    ids = teams[0].ids + teams[1].ids
    names = teams[0].names + teams[1].names
    players = [{"id": pid, "name": nm, "short": _last(nm), "num": str(jerseys.get(pid, "") or ""),
                "team": 0 if j < nh else 1} for j, (pid, nm) in enumerate(zip(ids, names))]
    sc_all = res["score"].astype(float)
    mu = float((sc_all[:, 0] - sc_all[:, 1]).mean())
    sigma = float((sc_all[:, 0] - sc_all[:, 1]).std())
    prof = profiles["players"]

    def shot_detail(j, zone):
        pr = prof.get(ids[j], {}).get("zones", {}).get(zone, {})
        types = pr.get("types") or []
        if types:
            tids, w = zip(*types)
            tid = str(rng.choice(tids, p=np.array(w) / sum(w)))
        else:
            tid = str(rng.choice(ZONE_DEFAULT[zone]))
        xy = pr.get("xy") or []
        if xy:
            x, y = xy[int(rng.integers(len(xy)))]
        else:
            x, y = ZONE_XY[zone]
            x += rng.normal(0, 3); y += rng.normal(0, 2)
            if zone == "C3" and rng.random() < 0.5: x = 50 - x
        label = SHOT_FR.get(tid, "tir en suspension")
        if zone in ("C3", "AB3") and tid in ("95", "96", "110", "94"): label = "tir en suspension"
        return label, (round(float(x), 1), round(float(y), 1))

    steps = np.nonzero(L["act"][:, k])[0]
    events = []
    prev_lu = None
    score = [0, 0]
    per_seen = 1

    def push(ev, t, per, p_end, prio=0):
        ev.update({"q": int(per), "c": _clock(per, t, p_end), "s": list(score),
                   "wp": round(_wp(score[0] - score[1], t, mu, sigma), 3), "pr": prio})
        events.append(ev)

    starters = sorted(int(x) for x in list(L["lu_h"][steps[0], k]) + list(L["lu_a"][steps[0], k]))
    for s in steps:
        per = int(L["per"][s, k]); t0 = float(L["t"][s, k]); d = float(L["d"][s, k])
        p_end = per * 720.0 if per <= 4 else 2880.0 + (per - 4) * 300.0
        if per != per_seen:
            push({"k": "end", "txt": f"Fin du {PERIOD_FR.get(per_seen, 'prolongation')}"}, p_end - (720 if per <= 4 else 300), per_seen,
                 p_end - (720 if per <= 4 else 300), prio=3)
            per_seen = per
        lu = sorted(int(x) for x in list(L["lu_h"][s, k]) + list(L["lu_a"][s, k]))
        if prev_lu is not None and lu != prev_lu:
            p_in = [j for j in lu if j not in prev_lu]
            p_out = [j for j in prev_lu if j not in lu]
            txt = "Changement : " + ", ".join(players[j]["short"] for j in p_in) + " remplace " + \
                  ", ".join(players[j]["short"] for j in p_out)
            push({"k": "sub", "in": p_in, "out": p_out, "txt": txt}, t0, per, p_end)
        prev_lu = lu
        off = int(L["off"][s, k]); kind = int(L["kind"][s, k]); ctx = int(L["ctx"][s, k])
        team_name = meta["home" if off == 0 else "away"]["short"]
        poss = {"team": off, "ctx": CTX_FR.get(ctx, "demi-terrain")}
        t_end = t0 + d
        if kind == K_NONE:
            continue
        if kind == K_TO:
            to = int(L["to"][s, k]); stl = int(L["stl"][s, k])
            if stl >= 0:
                txt = f"{players[stl]['short']} intercepte le ballon de {players[to]['short']}"
                push({"k": "to", "p": to, "stl": stl, "txt": txt, "badge": "INTERCEPTION", **poss}, t_end, per, p_end, prio=2)
            else:
                lab = TO_FR[int(rng.choice(len(TO_FR), p=[w for _, w in TO_FR]))][0]
                push({"k": "to", "p": to, "txt": f"Perte de balle de {players[to]['short']} ({lab})", **poss}, t_end, per, p_end)
            continue
        nsf = int(L["nsf"][s, k])
        ftw = int(L["ftw"][s, k]); nft = int(L["nft"][s, k]); ftm = int(L["ftm"][s, k])
        fouler = int(L["fouler"][s, k])
        if nsf >= 0:
            push({"k": "foul", "p": nsf, "txt": f"Faute de {players[nsf]['short']}", **poss}, t0 + d * 0.5, per, p_end)
        if kind in (K_FT, K_IFOUL) or (kind == K_SHOT and not L["shooting"][s, k]):
            how = "faute volontaire" if kind == K_IFOUL else ("faute sur tir" if kind == K_FT else "faute, lancers (bonus)")
            if kind != K_SHOT or nsf < 0:
                push({"k": "foul", "p": fouler, "on": ftw, "txt": f"{how.capitalize()} de {players[fouler]['short']} sur {players[ftw]['short']}", **poss},
                     t_end, per, p_end, prio=1 if kind == K_IFOUL else 0)
        if L["shooting"][s, k]:
            sh = int(L["sh"][s, k]); zone = ZONES[int(L["zone"][s, k])]; made = bool(L["made"][s, k])
            val = int(L["sh_val"][s, k]); ast = int(L["ast"][s, k]); blk = int(L["blk"][s, k])
            heave = bool(L["heave"][s, k])
            label, xy = shot_detail(sh, zone)
            if heave: label, xy = "tir du milieu de terrain", (25.0, 60.0)
            where = " à 3 pts" if val == 3 else ""
            if made:
                score[off] += val
                txt = f"{players[sh]['short']} marque{where} ({label})" + (f", passe de {players[ast]['short']}" if ast >= 0 else "")
                badge = "+3" if val == 3 else ("DUNK" if "dunk" in label else "+2")
                prio = 2 + (val == 3) + ("dunk" in label or "alley" in label)
            elif blk >= 0:
                txt = f"{players[blk]['short']} contre {players[sh]['short']} ({label})"
                badge, prio = "CONTRE", 3
            else:
                txt = f"{players[sh]['short']} manque un {label}{where}"
                badge, prio = None, 0
            push({"k": "shot", "p": sh, "z": zone, "made": made, "v": val, "ast": ast if ast >= 0 else None,
                  "blk": blk if blk >= 0 else None, "xy": xy, "lab": label, "txt": txt, "badge": badge, **poss},
                 t_end, per, p_end, prio=prio)
            if made and nft == 1 and ftw == sh:
                push({"k": "foul", "p": fouler, "on": sh, "txt": f"Et la faute ! {players[fouler]['short']} sur {players[sh]['short']}",
                      "badge": "AND-ONE", **poss}, t_end, per, p_end, prio=3)
        if nft > 0:
            makes = [True] * ftm + [False] * (nft - ftm)
            rng.shuffle(makes)
            if not makes[-1] and ftm == nft - 1:   # le rebond suit le dernier lancer rate
                pass
            for i, ok in enumerate(makes):
                score[off] += int(ok)
                push({"k": "ft", "p": ftw, "made": ok, "i": i + 1, "n": nft,
                      "txt": f"{players[ftw]['short']} {'réussit' if ok else 'manque'} le lancer franc {i + 1}/{nft}", **poss},
                     t_end, per, p_end)
        oreb = int(L["oreb"][s, k]); dreb = int(L["dreb"][s, k])
        if oreb >= 0:
            push({"k": "reb", "p": oreb, "o": True, "txt": f"Rebond offensif de {players[oreb]['short']}", **poss}, t_end, per, p_end)
        elif dreb >= 0:
            push({"k": "reb", "p": dreb, "o": False, "txt": f"Rebond défensif de {players[dreb]['short']}", **poss}, t_end, per, p_end)
        # Coherence : le score journalise fait foi
        score = [int(L["sc"][s, k][0]), int(L["sc"][s, k][1])]
        events[-1]["s"] = list(score)
    final_per = int(L["per"][steps[-1], k])
    push({"k": "end", "txt": "Fin du match"}, 9e9, final_per, 9e9, prio=5)
    events[-1]["c"] = 0.0
    events[-1]["wp"] = 1.0 if score[0] > score[1] else 0.0

    # Condense : actions marquantes + 2 dernieres minutes du QT4 et prolongations en entier
    for ev in events:
        late = ev["q"] >= 5 or (ev["q"] == 4 and ev["c"] <= 120)
        lead_change = False
        ev["hl"] = bool(ev["pr"] >= 2 or late)
    for i in range(1, len(events)):
        a, b = events[i - 1]["s"], events[i]["s"]
        if (a[0] - a[1]) * (b[0] - b[1]) < 0:
            events[i]["hl"] = True
            events[i]["lc"] = True

    sc = res["score"]
    hp, ap = sc[:, 0], sc[:, 1]
    lines = meta.get("lines") or {}
    summary = {
        "n": int(len(hp)), "k": int(k) + 1,
        "p_home": round(float((hp > ap).mean()), 4), "avg": [round(float(hp.mean()), 1), round(float(ap.mean()), 1)],
        "total_line": lines.get("total"), "p_over": round(float((hp + ap > lines["total"]).mean()), 4) if lines.get("total") else None,
        "books_p_home": meta.get("books_p_home"),
    }
    return {"v": 1, "meta": {k2: meta[k2] for k2 in ("home", "away", "date") if k2 in meta},
            "players": players, "starters": starters, "events": events, "summary": summary,
            "final": [int(score[0]), int(score[1])]}
