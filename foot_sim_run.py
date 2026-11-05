"""
foot_sim_run.py — Simule les matchs de foot a venir (Top 5 + coupes d'Europe) -> data/foot_sim.json.

Pour chaque match des 48 prochaines heures :
  - composition : officielle si publiee (~1 h avant), sinon probable (FotMob), sinon la notre (dernier
    onze de l'equipe moins les indisponibles) ; indisponibles et impact chiffre de chaque absence ;
  - 10 000 simulations minute par minute (foot_sim) : 1X2, double chance, buts, les deux marquent,
    scores exacts, mi-temps, buteurs, cartons rouges ;
  - « match type » rejoue par la video du site : le scenario le plus representatif et ses actions.
Pas de lien avec les paris ni la bankroll.

Usage : python foot_sim_run.py [--sims 10000] [--hours 48] [--no-update]
"""
import argparse, json, math, sys, time
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np

import foot_db
import foot_calib
from foot_sim import FootState, simulate, _ord, pos_group

OUT_PATH = Path("data/foot_sim.json")
CUR_SEASON = "2026/2027"
SEASON_START = "2026-07-01"
LOG_SIMS = 3000
ESPN_SLUG = {47: "eng.1", 87: "esp.1", 54: "ger.1", 55: "ita.1", 53: "fra.1", 42: "uefa.champions", 73: "uefa.europa"}
_ESPN_CACHE = {}


def espn_event(g):
    """Identifiant ESPN du match (scores en direct dans le navigateur) : meme jour, noms d'equipes proches."""
    import difflib
    from nba_boxdb import _get
    slug = ESPN_SLUG.get(g["league"])
    day = g["ko"][:10].replace("-", "")
    key = (slug, day)
    if key not in _ESPN_CACHE:
        _ESPN_CACHE[key] = (_get(f"https://site.api.espn.com/apis/site/v2/sports/soccer/{slug}/scoreboard?dates={day}") or {}).get("events") or []
    sim = lambda a, b: difflib.SequenceMatcher(None, a.lower(), b.lower()).ratio()
    best, score = None, 0.0
    for e in _ESPN_CACHE[key]:
        teams = {c.get("homeAway"): (c.get("team") or {}).get("displayName", "") for c in (e.get("competitions") or [{}])[0].get("competitors") or []}
        sc = sim(teams.get("home", ""), g["home"]["name"]) + sim(teams.get("away", ""), g["away"]["name"])
        if sc > score: best, score = e, sc
    return {"slug": slug, "id": str(best["id"])} if best and score >= 1.0 else None
LINEUP_TYPES = {"standard": "officielle", "simple": "officielle", "predicted": "probable",
                "laststarting11": "dernier onze"}


MODEL_EXT_PATH = Path("data/foot_model_ext.json")   # ecrit par foot_validate.py (backtest Top 5 base etendue vs historique)


def model_ext():
    """La base etendue (C4, coupes nationales, autres championnats...) entre-t-elle dans le modele de forces ?
    Seulement apres une validation qui montre que le Top 5 n'est pas moins bien prevu (foot_validate.py)."""
    try:
        return bool(json.loads(MODEL_EXT_PATH.read_text(encoding="utf-8")).get("enabled"))
    except (OSError, ValueError):
        return False


def build_state(params=None, ext=None):
    ext = model_ext() if ext is None else ext
    cups = None
    if ext:
        import foot_comps
        cups = foot_comps.cups()
    st = FootState(params, cups=cups)
    for r in sorted((foot_db.load_all_ext() if ext else foot_db.load_all()).values(), key=lambda r: (r["date"], r["id"])):
        st.add_match(r)
    return st


# Matchs simules : Top 5 + C1 / C3 ; avec la base etendue validee, aussi la C4, les coupes nationales et Supercoupes du
# Top 5, la Supercoupe d'Europe et les competitions de selections (demande de Wilson, 04/11/2026)
SIM_EXT = (10216, 132, 133, 247, 138, 139, 209, 8924, 141, 222, 134, 207, 74, 9806, 10195, 77)


def _comp_name(lid):
    try:
        import foot_comps
        return (foot_comps.load().get(int(lid)) or {}).get("name") or str(lid)
    except Exception:
        return str(lid)


def sim_leagues():
    return list(foot_db.LEAGUES) + ([l for l in SIM_EXT if l not in foot_db.LEAGUES] if model_ext() else [])


def upcoming(hours):
    now = datetime.now(timezone.utc)
    out = []
    for lid in sim_leagues():
        for m in foot_db.fixtures(lid, CUR_SEASON):
            stt = m.get("status") or {}
            if stt.get("started") or stt.get("finished") or stt.get("cancelled"): continue
            t = stt.get("utcTime")
            if not t: continue
            ko = datetime.fromisoformat(t.replace("Z", "+00:00"))
            if timedelta(0) < ko - now <= timedelta(hours=hours):
                out.append({"id": str(m["id"]), "league": lid, "ko": t, "round": m.get("round"),
                            "home": {"id": str(m["home"]["id"]), "name": m["home"]["name"]},
                            "away": {"id": str(m["away"]["id"]), "name": m["away"]["name"]}})
    return sorted(out, key=lambda g: g["ko"])


SHIRT_CACHE = Path("data/foot_db/shirts.json")   # dossier mis en cache par la veille (CI)
_RECS = {}


def shirt_numbers(team_id, before):
    """{joueur: numero} d'apres la feuille de match complete du dernier match joue par l'equipe (FotMob) :
    la composition « probable » d'avant-match ne donne pas les remplacants. Cache 3 jours par equipe."""
    try:
        cache = json.loads(SHIRT_CACHE.read_text(encoding="utf-8")) if SHIRT_CACHE.exists() else {}
    except ValueError:
        cache = {}
    c = cache.get(str(team_id))
    if c and time.time() - c["at"] < 3 * 86400:
        return c["nums"]
    if not _RECS: _RECS.update(foot_db.load_all_ext())
    past = [r for r in _RECS.values() if str(team_id) in (str(r["home"]["id"]), str(r["away"]["id"])) and r["date"] < before]
    nums = {}
    if past:
        last = max(past, key=lambda r: r["date"])
        try:
            lu = ((foot_db._page(last["id"]) or {}).get("content") or {}).get("lineup") or {}
            for key in ("homeTeam", "awayTeam"):
                t = lu.get(key) or {}
                if str(t.get("id")) != str(team_id): continue
                for pl in (t.get("starters") or []) + (t.get("subs") or []):
                    if pl.get("shirtNumber"): nums[str(pl.get("id"))] = str(pl["shirtNumber"])
        except Exception:
            pass
    cache[str(team_id)] = {"at": time.time(), "nums": nums}
    try:
        SHIRT_CACHE.parent.mkdir(parents=True, exist_ok=True)
        SHIRT_CACHE.write_text(json.dumps(cache), encoding="utf-8")
    except OSError:
        pass
    return nums


def lineups(st, g, day):
    """Composition et indisponibles de chaque equipe : {side: {xi, bench, type, unavailable}}."""
    try:
        pp = foot_db._page(g["id"]) or {}
    except Exception:
        pp = {}
    lu = (pp.get("content") or {}).get("lineup") or {}
    ltype = (lu.get("lineupType") or "").lower()
    out = {}
    for side, key in (("home", "homeTeam"), ("away", "awayTeam")):
        t = lu.get(key) or {}
        unav = [{"id": str(u.get("id")), "name": u.get("name"), "type": (u.get("unavailability") or {}).get("type"),
                 "back": (u.get("unavailability") or {}).get("expectedReturn")} for u in t.get("unavailable") or []]
        xi = [str(p.get("id")) for p in t.get("starters") or []]
        bench = [str(p.get("id")) for p in t.get("subs") or []]
        # noms et postes depuis FotMob : joueurs encore absents de la base (recrues, promus)
        names = {str(p.get("id")): (p.get("name"), p.get("positionId") or p.get("usualPlayingPositionId"))
                 for p in (t.get("starters") or []) + (t.get("subs") or [])}
        # Video : numero de maillot et place dans le schema (horizontalLayout : x = profondeur depuis son but,
        # y = largeur, de 0 a 1)
        info = {str(p.get("id")): {"num": str(p.get("shirtNumber") or ""),
                                   "lay": [(p.get("horizontalLayout") or {}).get("x"), (p.get("horizontalLayout") or {}).get("y")]}
                for p in (t.get("starters") or []) + (t.get("subs") or [])}
        typ = (LINEUP_TYPES.get(ltype, "probable") if xi else "")
        if not xi:   # notre composition : dernier onze de l'equipe, indisponibles retires
            tid = g[side]["id"]
            games = [x for x in st.team_games.get(tid, []) if x[0] < day]
            out_ids = {u["id"] for u in unav}
            if games:
                last_xi, last_bench = games[-1][2]
                xi = [p for p in last_xi if p not in out_ids]
                pool = Counter(pid for gm in games[-6:] for pid in gm[2][0] + gm[2][1])
                bench = [p for p, _ in pool.most_common(30) if p not in xi and p not in out_ids][:12]
                typ = "estimée"
        if xi and not bench:   # composition probable FotMob sans banc : remplacants habituels de l'equipe
            tid = g[side]["id"]
            games = [x for x in st.team_games.get(tid, []) if x[0] < day]
            out_ids = {u["id"] for u in unav}
            pool = Counter(pid for gm in games[-6:] for pid in gm[2][0] + gm[2][1])
            bench = [p for p, _ in pool.most_common(30) if p not in xi and p not in out_ids][:12]
        missing = [p for p in xi + bench if not (info.get(p) or {}).get("num")]
        if missing:   # numeros manquants (remplacants d'une compo probable, compo estimee) : dernier match joue
            try:
                nums = shirt_numbers(g[side]["id"], g["ko"])
            except Exception:
                nums = {}
            for pid in missing:
                if nums.get(pid): info[pid] = {"num": nums[pid], "lay": (info.get(pid) or {}).get("lay") or [None, None]}
        out[side] = {"xi": xi, "bench": bench, "type": typ, "unavailable": unav, "names": names, "info": info,
                     "formation": t.get("formation")}
    return out


def absence_impact(st, tid, unav, day, players_here):
    """Part de l'attaque habituelle perdue par absent (sa part d'xG x ses minutes habituelles)."""
    ref = st.typical_attack(tid, day) or 1.0
    out = []
    for u in unav:
        pr = st.player_profile(u["id"], day, tid)
        if not pr["n"]: continue
        usual = pr["share"] * pr["min_start"] * pr["start_rate"] / 90
        if usual / ref >= 0.02:
            out.append({**u, "impact": round(usual / ref, 3), "pos": pr["pos"]})
    return sorted(out, key=lambda x: -x["impact"])


def typical_index(res, gid=None):
    """Le match montre : resultat (1 / N / 2) tire au sort selon les probabilites des simulations (comme au
    basket : l'outsider apparait gagnant aussi souvent qu'il gagne ; tirage fixe par match), puis un score
    representatif de ce resultat (frequent ET proche des buts attendus), puis le scenario dont l'xG est le
    plus proche de la moyenne (sans rouge de preference). Sans gid : le resultat le plus probable."""
    H, A = res["home_goals"], res["away_goals"]
    sign = np.sign(H - A)
    if gid is None:
        k = max((1, 0, -1), key=lambda v: np.mean(sign == v))
    else:
        u = (int(gid) * 2654435761 % 2 ** 32) / 2 ** 32
        p1, px = float(np.mean(sign == 1)), float(np.mean(sign == 0))
        k = 1 if u < p1 else (0 if u < p1 + px else -1)
    # Score representatif : frequence x proximite des buts attendus. Le score le plus frequent seul donnait
    # presque toujours 1-0 (le mode d'une loi de Poisson est bas) meme pour 2,5-3 buts attendus (28/10).
    eh, ea = H.mean(), A.mean()
    cnt = Counter(zip(H[sign == k].tolist(), A[sign == k].tolist()))
    best = max(cnt, key=lambda sc: cnt[sc] * np.exp(-((sc[0] - eh) ** 2 + (sc[1] - ea) ** 2)))
    h, a = H[:LOG_SIMS], A[:LOG_SIMS]
    cand = np.nonzero((h == best[0]) & (a == best[1]))[0]
    if not len(cand): cand = np.nonzero(np.sign(h - a) == k)[0]
    z = ((res["home_xg"][cand] - res["home_xg"].mean()) ** 2 + (res["away_xg"][cand] - res["away_xg"].mean()) ** 2
         + 0.3 * (res["home_red"][cand] + res["away_red"][cand]))
    return int(cand[int(np.argmin(z))])


MAX_SUBS, MAX_WINDOWS, HT_MINUTE = 5, 3, 46   # reglement des changements (IFAB, Top 5 et coupes d'Europe)


def replay(res, inp, k, g, lu=None):
    """Actions du match type pour la video : tirs (position, xG, issue), buts, penalties, csc, rouges, remplacements,
    et les joueurs (numero, poste, place dans le schema) pour les montrer tous sur le terrain."""
    ev = []
    for minute, side, j, q, goal, kind in sorted(res["log"][k], key=lambda e: e[0]):
        pl = inp[side]["players"]
        who = pl[j]["name"] if 0 <= j < len(pl) else ""
        if kind == "jeu":
            x, y, xg, sit, typ, ontg, blk, outc = res["pool"][side][q]
            ev.append({"t": minute, "side": side, "kind": "but" if goal else ("arret" if ontg else ("contre" if blk else "rate")),
                       "who": who, "x": round(x, 1), "y": round(y, 1), "xg": round(xg, 3), "sit": sit, "type": typ})
        elif kind == "penalty":
            ev.append({"t": minute, "side": side, "kind": "but" if goal else "arret", "who": who, "x": 94.5, "y": 34.0,
                       "xg": 0.76, "sit": "Penalty", "type": ""})
        elif kind == "csc":
            ev.append({"t": minute, "side": side, "kind": "csc", "who": "", "x": 104, "y": 34, "xg": 0, "sit": "", "type": ""})
        elif kind == "rouge":
            ev.append({"t": minute, "side": side, "kind": "rouge", "who": ""})
    for side in ("home", "away"):
        t_in, t_out = res["subs"][side]
        pl = inp[side]["players"]
        outs = [(float(t_out[k, j]), j) for j in range(len(pl)) if np.isfinite(t_out[k, j])]
        ins = [(float(t_in[k, j]), j) for j in range(len(pl)) if np.isfinite(t_in[k, j]) and t_in[k, j] > 0]
        # Appariement sortant -> entrant : 5 changements au plus, poste pour poste de preference (a minute
        # proche), jamais un gardien a la place d'un joueur de champ
        used, chosen = set(), []
        for tm, jo in sorted(outs):
            if len(chosen) >= MAX_SUBS: break
            po = pl[jo]["pos"]
            cand = [(abs(ti - tm) + (0 if pl[ji]["pos"] == po else 15), ji) for ti, ji in ins
                    if ji not in used and (pl[ji]["pos"] != "GK" or po == "GK")]
            if not cand: continue
            ji = min(cand)[1]; used.add(ji)
            chosen.append([int(tm), jo, ji])
        # Reglement (Top 5 et coupes d'Europe) : 5 changements, en 3 arrets de jeu au plus ; ceux faits a la
        # mi-temps (46e) ne comptent pas. Au-dela de 3 moments, les deux plus proches sont fusionnes.
        wins = sorted({c[0] for c in chosen if c[0] > HT_MINUTE})
        while len(wins) > MAX_WINDOWS:
            i = min(range(len(wins) - 1), key=lambda k: wins[k + 1] - wins[k])
            wins[i:i + 2] = [round((wins[i] + wins[i + 1]) / 2)]
        for c in chosen:
            if c[0] > HT_MINUTE: c[0] = min(wins, key=lambda w: abs(w - c[0]))
            else: c[0] = HT_MINUTE
            ev.append({"t": c[0], "side": side, "kind": "remplacement", "who": pl[c[2]]["name"], "out": pl[c[1]]["name"]})
    ev.sort(key=lambda e: e["t"])
    score = [0, 0]
    for e in ev:
        if e["kind"] in ("but", "csc"):
            score[0 if e["side"] == "home" else 1] += 1
        e["score"] = list(score)
    teams = {}
    for side in ("home", "away"):
        info = ((lu or {}).get(side) or {}).get("info") or {}
        pls = []
        for q in inp[side]["players"]:
            i = info.get(q["id"]) or {}
            lay = i.get("lay") or [None, None]
            pls.append({"name": q["name"], "num": i.get("num", ""), "pos": q["pos"], "st": bool(q["starter"]),
                        "lay": [round(lay[0], 3), round(lay[1], 3)] if q["starter"] and None not in lay else None})
        teams[side] = {"players": pls, "formation": ((lu or {}).get(side) or {}).get("formation")}
    xh, xa = float(res["home_xg"].mean()), float(res["away_xg"].mean())
    poss = round(min(max(0.5 + 0.3 * (xh - xa) / max(xh + xa, 0.1), 0.35), 0.65), 3)   # possession du domicile (approx.)
    return {"v": 2, "events": ev, "final": score, "teams": teams, "poss": poss}


# Recalage des tirs (backtest walk-forward du 04/11/2026, 548 matchs oct. 2025 - oct. 2026, sans fuite : ajuste
# avant le 01/04/2026, valide apres). Brut : tirs cadres surestimes de 12 % (un but simule compte cadre meme si le
# tir reel tire au sort etait hors cadre), tirs du match sous-estimes. Probabilites : Platt p' = sig(a + b logit p) ;
# moyennes : facteur d'echelle. Validation : tirs / cadres par equipe calibres a 4 pts pres ; cumules : 5-9 pts
# (fiabilite moyenne, affichee comme telle).
# 04/11/2026 (2e version) : la moyenne simulee est melangee a 40 % avec les HABITUDES DE VOLUME (tirs de l'equipe et
# tirs concedes par l'adversaire, 15 derniers matchs, hors penalties) : l'xG ne voit pas les equipes qui frappent de
# loin ni les blocs bas qui laissent tirer. Backtest 759 matchs, choisi avant le 01/04/2026, valide apres : log-loss
# tirs equipe 0,508 -> 0,497, cadres 0,449 -> 0,442, total cadres 0,695 -> 0,672. Un modele de style detaille
# (contre-attaque, coups de pied arretes, distance, domination, interactions) n'apportait RIEN hors echantillon
# (+0,4 % d'erreur) : la simulation tient deja compte du rapport de force (outsider en contre : reel / simule = 1,00).
SHOT_CAL = {"team_sh": (0.075, 1.13), "team_so": (-0.129, 0.981), "tot_sh": (0.186, 1.295), "tot_so": (-0.043, 1.080),
            "scale_sh": 1.023, "scale_so": 0.887, "blend": 0.4}
SP_SIT = ("FromCorner", "SetPiece", "FreeKick", "ThrowInSetPiece")


def style_profile(team_id, before, n=15):
    """Facon de jouer sur les n derniers matchs de la base (avant `before`) : volume de tirs pour / contre, qualite
    (xG par tir), distance de tir, part de contre-attaques et de coups de pied arretes, domination (part des tirs du
    match), defense a 3. Sert au melange des tirs et a la carte « Profil tactique »."""
    if not _RECS: _RECS.update(foot_db.load_all_ext())
    tid = str(team_id)
    past = sorted((r for r in _RECS.values() if tid in (str(r["home"]["id"]), str(r["away"]["id"])) and r["date"] < before
                   and r.get("shots")), key=lambda r: r["date"])[-n:]
    if len(past) < 5: return None
    S = defaultdict(float)
    for r in past:
        side = 1 if str(r["home"]["id"]) == tid else 0
        f = [x for x in r["shots"] if x[0] == side and not x[13] and x[9] != "Penalty"]
        a = [x for x in r["shots"] if x[0] != side and not x[13] and x[9] != "Penalty"]
        so = lambda L: sum(1 for x in L if (x[11] and not x[12]) or x[8] == "Goal")
        S["sh"] += len(f); S["sha"] += len(a); S["so"] += so(f); S["soa"] += so(a)
        S["xg"] += sum(x[6] for x in f); S["xga"] += sum(x[6] for x in a)
        S["fb"] += sum(1 for x in f if x[9] == "FastBreak"); S["sp"] += sum(1 for x in f if x[9] in SP_SIT)
        S["dist"] += sum(math.hypot(105 - x[4], 34 - x[5]) for x in f)
        S["back3"] += str(r.get("formation_home" if side else "formation_away") or "").startswith("3")
    m = len(past); sh = S["sh"] or 1
    form = Counter(str(r.get("formation_home" if str(r["home"]["id"]) == tid else "formation_away") or "") for r in past)
    return {"n": m, "sh": round(S["sh"] / m, 2), "sha": round(S["sha"] / m, 2), "so": round(S["so"] / m, 2),
            "soa": round(S["soa"] / m, 2), "xg": round(S["xg"] / m, 2), "xga": round(S["xga"] / m, 2),
            "xgps": round(S["xg"] / sh, 3), "fb": round(S["fb"] / sh, 3), "sp": round(S["sp"] / sh, 3),
            "dist": round(S["dist"] / sh, 1), "dom": round(S["sh"] / ((S["sh"] + S["sha"]) or 1), 3),
            "back3": round(S["back3"] / m, 2), "formation": form.most_common(1)[0][0] if form else ""}


def _cal(p, key):
    a, b = SHOT_CAL[key]
    p = min(max(float(p), 1e-4), 1 - 1e-4)
    return round(1 / (1 + math.exp(-(a + b * math.log(p / (1 - p))))), 3)


def shot_lines(res, style=None):
    """Tirs et tirs cadres : moyennes par equipe (simulation melangee aux habitudes de volume, voir SHOT_CAL) et
    probabilites recalees de depasser 3 lignes (equipe) ou 4 / 3 lignes (match) placees autour de la moyenne."""
    style = style or {}
    raw_sh = {s: res[s + "_pshots"].sum(axis=1) for s in ("home", "away")}
    raw_so = {s: res[s + "_psot"].sum(axis=1) for s in ("home", "away")}
    sh, so, ms, mo, sim_m = {}, {}, {}, {}, {}
    for s, o in (("home", "away"), ("away", "home")):
        me, op = style.get(s), style.get(o)
        b = SHOT_CAL["blend"] if me and op else 0.0
        for raw, out, mm, key, sc, naive in ((raw_sh, sh, ms, "sh", SHOT_CAL["scale_sh"], (me["sh"] + op["sha"]) / 2 if b else 0),
                                             (raw_so, so, mo, "so", SHOT_CAL["scale_so"], (me["so"] + op["soa"]) / 2 if b else 0)):
            m0 = float(raw[s].mean()) or 1e-9
            target = (1 - b) * m0 * sc + b * naive
            out[s] = raw[s] * (target / m0)          # distribution simulee remise a l'echelle de la moyenne melangee
            mm[s] = target
        sim_m[s] = [round(float(raw_sh[s].mean()) * SHOT_CAL["scale_sh"], 1), round(float(raw_so[s].mean()) * SHOT_CAL["scale_so"], 1)]
    lines = {}
    for s in ("home", "away"):
        b = math.floor(ms[s])
        lines[s + "_sh"] = {f"{l}.5": _cal(np.mean(sh[s] > l + 0.5), "team_sh") for l in (b - 2, b, b + 2)}
        b = math.floor(mo[s])
        lines[s + "_so"] = {f"{l}.5": _cal(np.mean(so[s] > l + 0.5), "team_so") for l in (b - 1, b, b + 1)}
    ts, to = sh["home"] + sh["away"], so["home"] + so["away"]
    b = math.floor(ms["home"] + ms["away"])
    lines["tot_sh"] = {f"{l}.5": _cal(np.mean(ts > l + 0.5), "tot_sh") for l in (b - 3, b - 1, b + 1, b + 3)}
    b = math.floor(mo["home"] + mo["away"])
    lines["tot_so"] = {f"{l}.5": _cal(np.mean(to > l + 0.5), "tot_so") for l in (b - 1, b, b + 1)}
    return {"home": [round(ms["home"], 1), round(mo["home"], 1)], "away": [round(ms["away"], 1), round(mo["away"], 1)],
            "sim": sim_m, "lines": lines}


# Cartons par joueur (valide walk-forward le 04/11/2026 sur 71 962 apparitions, oct. 2025 - oct. 2026) :
# taux = (jaunes + K x taux moyen) / (minutes / 90 + K), K = 30 ; P(jaune) = 1 - exp(-taux x minutes / 90).
# Brier 0,1061 vs 0,1076 pour un taux unique ; calibre a 1-3 pts (petits temps de jeu sous-estimes ~2 pts).
CARD_K = 30.0
_CARDS = {}


def player_book(before):
    """{joueur: [minutes, jaunes, rouges, matchs, buts, tirs, cadres, minutes saison, ...]} et taux moyens,
    d'apres les matchs de la base joues avant `before` (cache par jour)."""
    day = before[:10]
    if _CARDS.get("day") == day: return _CARDS["book"], _CARDS["avg"]
    if not _RECS: _RECS.update(foot_db.load_all_ext())
    book = defaultdict(lambda: {"min": 0.0, "y": 0, "r": 0, "s_app": 0, "s_min": 0.0, "s_g": 0, "s_sh": 0, "s_so": 0,
                                "s_y": 0, "s_r": 0})
    tot = [0.0, 0, 0]
    for r in _RECS.values():
        if r["date"] >= before: continue
        cur = r["date"] >= SEASON_START   # saison en cours, quel que soit le format de saison (2026/2027, 2026...)
        cards = defaultdict(lambda: [0, 0])
        for c in r.get("cards") or []:
            if c[4] in ("Yellow", "YellowRed"): cards[c[3]][0] = 1
            if c[4] in ("Red", "YellowRed"): cards[c[3]][1] = 1
        for q in r.get("players") or []:
            m = min(q[6] or 0, 95)
            if m <= 0: continue
            b = book[q[0]]
            b["min"] += m; b["y"] += cards[q[0]][0]; b["r"] += cards[q[0]][1]
            tot[0] += m; tot[1] += cards[q[0]][0]; tot[2] += cards[q[0]][1]
            if cur:
                b["s_app"] += 1; b["s_min"] += m; b["s_y"] += cards[q[0]][0]; b["s_r"] += cards[q[0]][1]
        if cur:
            for sh in r.get("shots") or []:
                if sh[13]: continue
                b = book[sh[1]]
                b["s_sh"] += 1; b["s_so"] += bool((sh[11] and not sh[12]) or sh[8] == "Goal"); b["s_g"] += sh[8] == "Goal"
    avg = (tot[1] / (tot[0] / 90), tot[2] / (tot[0] / 90)) if tot[0] else (0.17, 0.006)
    _CARDS.update(day=day, book=book, avg=avg)
    return book, avg


def player_table(res, inp, side, before):
    """Tableau des joueurs d'une equipe : moyennes des simulations (sur les matchs ou il joue) + saison reelle."""
    book, (ay, ar) = player_book(before)
    pl = inp[side]["players"]
    mins = res[side + "_pmin"]
    out = []
    for j, q in enumerate(pl):
        m = mins[:, j]
        on = m > 0
        if on.mean() < 0.05: continue
        b = book.get(q["id"]) or {"min": 0, "y": 0, "r": 0, "s_app": 0, "s_min": 0, "s_g": 0, "s_sh": 0, "s_so": 0, "s_y": 0, "s_r": 0}
        ry = (b["y"] + CARD_K * ay) / (b["min"] / 90 + CARD_K)
        rr = (b["r"] + CARD_K * ar) / (b["min"] / 90 + CARD_K)
        g = res[side + "_pgoals"][:, j]
        out.append({"name": q["name"], "pos": q["pos"], "starter": bool(q["starter"]), "p_play": round(float(on.mean()), 3),
                    "min": round(float(m[on].mean()), 0),
                    "p_goal": round(float(np.mean(g[on] > 0)), 3), "goals": round(float(g[on].mean()), 2),
                    "shots": round(float(res[side + "_pshots"][on, j].mean()) * SHOT_CAL["scale_sh"], 2),
                    "sot": round(float(res[side + "_psot"][on, j].mean()) * SHOT_CAL["scale_so"], 2),
                    "p_yellow": round(float(np.mean(1 - np.exp(-ry * m[on] / 90))), 3),
                    "p_red": round(float(np.mean(1 - np.exp(-rr * m[on] / 90))), 4),
                    "season": {"app": b["s_app"], "min": round(b["s_min"]), "g": b["s_g"], "sh": b["s_sh"], "so": b["s_so"],
                               "y": b["s_y"], "r": b["s_r"]}})
    return sorted(out, key=lambda x: (not x["starter"], -x["min"]))


def team_form(team_id, before, n=10):
    """Forme recente (n derniers matchs de la base : championnat + coupes d'Europe suivis) : buts, xG, tirs et
    tirs cadres pour / contre par match, part de matchs a plus de 2,5 buts et ou les deux marquent. Sert
    d'arguments aux chiffres de la simulation (demande de Wilson, 04/11/2026)."""
    if not _RECS: _RECS.update(foot_db.load_all_ext())
    tid = str(team_id)
    past = sorted((r for r in _RECS.values() if tid in (str(r["home"]["id"]), str(r["away"]["id"])) and r["date"] < before
                   and r.get("score")), key=lambda r: r["date"])[-n:]
    if not past: return None
    agg = defaultdict(float)
    res = []
    for r in past:
        side = 1 if str(r["home"]["id"]) == tid else 0
        h, a = r["score"]
        gf, ga = (h, a) if side else (a, h)
        agg["gf"] += gf; agg["ga"] += ga
        agg["o25"] += (h + a) > 2.5; agg["btts"] += h > 0 and a > 0
        res.append("V" if gf > ga else "N" if gf == ga else "D")
        for s in r.get("shots") or []:
            k = "f" if s[0] == side else "a"
            if s[13]: continue                       # but contre son camp : pas un tir de l'equipe
            agg["sh_" + k] += 1
            agg["sot_" + k] += (bool(s[11]) and not s[12]) or s[8] == "Goal"   # contre != cadre
            agg["xg_" + k] += s[6] or 0
    m = len(past)
    out = {k: round(v / m, 2) for k, v in agg.items()}
    out.update(n=m, last=res[::-1][:5], o25=round(agg["o25"] / m, 2), btts=round(agg["btts"] / m, 2))
    return out


# Historique des tirs L5 / L10 / L20 (Wilson, 05/11/2026 : « comme les stats joueurs NBA, en tenant compte des
# adversaires »). Backtest du 05/11 sur 6 139 matchs-equipe (toutes competitions) : erreur moyenne sur les tirs du
# match suivant 4,38 (L5) / 4,18 (L10) / 4,08 (L20) en brut ; corriger chaque match de la force de l'adversaire
# (tirs qu'il concede) et ramener a l'adversaire du soir gagne ~3 % : meilleur = moitie brut + moitie corrige
# (4,22 / 4,03 / 3,93). Sur les tirs cadres, la correction n'apporte rien (1,92 = 1,92) : cadres en brut seulement.
_CONC = {}


def _conceded(before):
    """{equipe: [(date, tirs concedes, cadres concedes)]} et moyennes de reference (cache par jour)."""
    day = before[:10]
    if _CONC.get("day") == day: return _CONC["idx"], _CONC["ref"]
    if not _RECS: _RECS.update(foot_db.load_all_ext())
    idx, tot = defaultdict(list), [0, 0, 0]
    for r in sorted(_RECS.values(), key=lambda r: r["date"]):
        if r["date"] >= before or not r.get("shots"): continue
        for side in (1, 0):
            f = [s for s in r["shots"] if s[0] != side and not s[13]]
            so = sum(1 for s in f if (s[11] and not s[12]) or s[8] == "Goal")
            idx[str((r["home"] if side else r["away"])["id"])].append((r["date"], len(f), so))
            tot[0] += len(f); tot[1] += so; tot[2] += 1
    ref = (tot[0] / tot[2], tot[1] / tot[2]) if tot[2] else (12.8, 4.5)
    _CONC.update(day=day, idx=idx, ref=ref)
    return idx, ref


def _conc_at(idx, team, date, k=20):
    L = [x for x in idx.get(str(team), []) if x[0] < date][-k:]
    return (sum(x[1] for x in L) / len(L), sum(x[2] for x in L) / len(L)) if len(L) >= 5 else (None, None)


def shot_history(team_id, opp_id, before, n=20):
    """n derniers matchs (toutes competitions de la base) : tirs et cadres de l'equipe, force de l'adversaire de
    chaque match (tirs qu'il concedait), et tirs « ajustes » a l'adversaire du soir (moitie brut, moitie corrige)."""
    if not _RECS: _RECS.update(foot_db.load_all_ext())
    idx, (ref, refo) = _conceded(before)
    tid = str(team_id)
    past = sorted((r for r in _RECS.values() if tid in (str(r["home"]["id"]), str(r["away"]["id"])) and r["date"] < before
                   and r.get("shots") and r.get("score")), key=lambda r: r["date"])[-n:]
    if len(past) < 5: return None
    tonight = _conc_at(idx, opp_id, before)[0] or ref
    games = []
    for r in reversed(past):
        side = 1 if str(r["home"]["id"]) == tid else 0
        opp = r["away"] if side else r["home"]
        f = [s for s in r["shots"] if s[0] == side and not s[13]]
        sh, so = len(f), sum(1 for s in f if (s[11] and not s[12]) or s[8] == "Goal")
        oc = _conc_at(idx, opp["id"], r["date"])[0]
        adj = (sh + sh * tonight / oc) / 2 if oc else sh
        h, a = r["score"]
        games.append([r["date"][:10], opp.get("name") or "?", "D" if side else "E", sh, so, round(adj, 1),
                      None if oc is None else round(oc, 1), f"{h}-{a}" if side else f"{a}-{h}"])
    return {"opp_conc": round(tonight, 1), "ref": round(ref, 1), "games": games}


def shots_hist_pair(g):
    """Historique des tirs des deux equipes d'un match (carte « Historique des tirs »)."""
    try:
        return {s: shot_history(g[s]["id"], g[o]["id"], g["ko"]) for s, o in (("home", "away"), ("away", "home"))}
    except Exception as e:
        print(f"  [!] historique des tirs {g.get('id')} : {type(e).__name__}: {e}")
        return None


def run_game(st, g, n_sims):
    day = _ord(g["ko"])
    lu = lineups(st, g, day)
    for side in ("home", "away"):
        for pid, (name, pos) in lu[side]["names"].items():
            if name and not (st.player_info.get(pid) or {}).get("name"):
                st.player_info[pid] = {"name": name, "pos": pos_group(pos)}
    inp = st.project(g["home"]["id"], g["away"]["id"], g["league"], lu["home"], lu["away"], day,
                     neutral=g["league"] in (42, 73) and "final" in str(g.get("round", "")).lower())
    res = simulate(inp, n=n_sims, seed=int(g["id"]) % (2 ** 31), shots_db=st.player_shots, log_sims=LOG_SIMS)
    hg, ag = res["home_goals"], res["away_goals"]
    tot = hg + ag
    cs = Counter(zip(hg.tolist(), ag.tolist()))
    try:
        espn = espn_event(g)
    except Exception:
        espn = None
    out = {"id": g["id"], "league": g["league"], "league_name": foot_db.LEAGUES.get(g["league"]) or _comp_name(g["league"]), "ko": g["ko"], "espn": espn,
           "home": g["home"], "away": g["away"], "n_sims": n_sims,
           "sim_at": datetime.now(timezone.utc).isoformat(timespec="minutes"),
           "xg": [round(float(res["home_xg"].mean()), 2), round(float(res["away_xg"].mean()), 2)],
           "goals": [round(float(hg.mean()), 2), round(float(ag.mean()), 2)],
           "p": {"1": round(float(np.mean(hg > ag)), 4), "X": round(float(np.mean(hg == ag)), 4), "2": round(float(np.mean(hg < ag)), 4),
                 "btts": round(float(np.mean((hg > 0) & (ag > 0))), 4),
                 "over": {str(l): round(float(np.mean(tot > l)), 4) for l in (0.5, 1.5, 2.5, 3.5, 4.5)},
                 "ht": {"1": round(float(np.mean(res["home_ht"] > res["away_ht"])), 4), "X": round(float(np.mean(res["home_ht"] == res["away_ht"])), 4),
                        "2": round(float(np.mean(res["home_ht"] < res["away_ht"])), 4)},
                 "red": round(float(np.mean((res["home_red"] + res["away_red"]) > 0)), 4),
                 "clean": [round(float(np.mean(ag == 0)), 4), round(float(np.mean(hg == 0)), 4)]},
           "scores": [[int(h), int(a), round(c / n_sims, 4)] for (h, a), c in cs.most_common(12)],
           "goal_hist": [round(float(np.mean(tot == k)), 4) for k in range(7)] + [round(float(np.mean(tot >= 7)), 4)]}
    # Facon de jouer (15 derniers matchs) puis tirs et tirs cadres (simulation + habitudes de volume, SHOT_CAL)
    try:
        out["style"] = {s: style_profile(g[s]["id"], g["ko"]) for s in ("home", "away")}
    except Exception:
        out["style"] = {}
    out["shots"] = shot_lines(res, out["style"])
    try:
        out["form"] = {s: team_form(g[s]["id"], g["ko"]) for s in ("home", "away")}
    except Exception:
        pass
    out["shots_hist"] = shots_hist_pair(g)
    for side in ("home", "away"):
        pl = inp[side]["players"]
        scorers = [{"name": q["name"], "pos": q["pos"], "starter": q["starter"],
                    "p_goal": round(float(np.mean(res[side + "_pgoals"][:, j] > 0)), 4),
                    "shots": round(float(res[side + "_pshots"][:, j].mean()), 2),
                    "sot": round(float(res[side + "_psot"][:, j].mean()) * SHOT_CAL["scale_so"], 2)} for j, q in enumerate(pl)]
        out[side + "_info"] = {"lineup": lu[side]["type"], "lineup_mult": round(inp[side]["lineup_mult"], 3),
                               "npxg_base": round(inp[side]["npxg_base"], 2), "npxg": round(inp[side]["npxg"], 2),
                               "absences": absence_impact(st, g[side]["id"], lu[side]["unavailable"], day, pl),
                               "scorers": sorted([s for s in scorers if s["p_goal"] >= 0.03], key=lambda s: -s["p_goal"])[:8],
                               "xi": [q["name"] for q in pl if q["starter"]]}
        try:
            out[side + "_info"]["players"] = player_table(res, inp, side, g["ko"])
        except Exception as e:
            print(f"  [!] tableau joueurs {side} : {type(e).__name__}: {e}")
    k = typical_index(res, g["id"])
    out["replay"] = replay(res, inp, k, g, lu)
    # probabilites affichees recalees (1X2, +2,5, les deux marquent ; foot_calib, 05/11/2026), brutes dans p_raw
    foot_calib.ensure(out)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sims", type=int, default=10000)
    ap.add_argument("--hours", type=int, default=48)
    ap.add_argument("--no-update", action="store_true")
    a = ap.parse_args()
    t0 = time.time()
    if not a.no_update:
        foot_db.update(list(foot_db.LEAGUES), [CUR_SEASON])
    st = build_state()
    games = upcoming(a.hours)
    print(f"=== Simulation foot : {len(games)} matchs dans les {a.hours} h (etat en {time.time() - t0:.0f}s) ===", flush=True)
    out = []
    for g in games:
        try:
            r = run_game(st, g, a.sims)
        except Exception as e:
            print(f"  [err] {g['id']} {g['home']['name']} - {g['away']['name']} : {type(e).__name__}: {e}"); continue
        print(f"  {r['home']['name'][:18]:>18s} {r['xg'][0]:.2f} - {r['xg'][1]:.2f} {r['away']['name'][:18]:<18s} "
              f"1X2 {r['p']['1']:.2f}/{r['p']['X']:.2f}/{r['p']['2']:.2f}  +2,5 {r['p']['over']['2.5']:.2f}  "
              f"compo {r['home_info']['lineup'] or '?'}/{r['away_info']['lineup'] or '?'}  type {r['replay']['final']}", flush=True)
        out.append(r)
    OUT_PATH.write_text(json.dumps({"generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "games": out},
                                   ensure_ascii=False), encoding="utf-8")
    print(f"[OK] {OUT_PATH} ({len(out)} matchs, {time.time() - t0:.0f}s)")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
