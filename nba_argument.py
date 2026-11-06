"""
nba_argument.py — Argumentaire d'un pari sur prop NBA, facon « NBA PLAY » (Wilson, 06/11/2026 : « les picks Bankroll IA
je veux qu'ils soient analyses et argumentes tels quels » ; exemple : LaMelo Ball plus de 19,5 points — Edwards absent,
Pelicans 25e en points encaisses, 89 % sur ses 9 derniers matchs contre les 10 pires defenses, 20,6 tirs tentes et
31,1 minutes, 70 % sur ses 10 derniers, sort d'un match a 34 points).

Memes rubriques, calculees sur nos donnees (feuilles de match des deux saisons, absences et effet mesure sur les
coequipiers, simulation, marche sans marge), PLUS une rubrique « a surveiller » : les faits qui vont CONTRE le pari.
Un post de ce genre choisit ses chiffres (ex. un echantillon « contre les 10 pires defenses ») ; ici ils sont affiches
quel que soit le sens, et la decision reste la probabilite et la cote (cote juste, « prends si »).
"""
import statistics
from collections import defaultdict

STAT_FR = {"PTS": "points", "REB": "rebonds", "AST": "passes", "3PM": "paniers à 3 pts", "PR": "points + rebonds",
           "PA": "points + passes", "RA": "rebonds + passes", "PRA": "points + rebonds + passes", "STL": "interceptions",
           "BLK": "contres"}
PARTS = {"PR": ("PTS", "REB"), "PA": ("PTS", "AST"), "RA": ("REB", "AST"), "PRA": ("PTS", "REB", "AST")}
_DEF = {}


def _val(row, stat):
    return sum(row.get(k, 0) for k in PARTS.get(stat, (stat,)))


def _fr(x, d=1):
    return f"{x:.{d}f}".replace(".", ",")


def _ord(n):
    return "1re" if n == 1 else f"{n}e"


def defense(season):
    """{saison: {stat: {abbr: (moyenne encaissee par match, rang 1 = defense la plus avare)}}} sur la saison."""
    if season in _DEF: return _DEF[season]
    import nba_boxdb as B
    acc = defaultdict(lambda: defaultdict(list))
    for g in B.load_season(season).values():
        for side, opp in (("home", "away"), ("away", "home")):
            rows = [r for r in (g.get(opp) or {}).get("players") or [] if not r.get("dnp") and r.get("MIN")]
            if not rows: continue
            for st in STAT_FR:
                acc[st][g[side]["abbr"]].append(sum(_val(r, st) for r in rows))
    out = {}
    for st, T in acc.items():
        m = {t: statistics.mean(v) for t, v in T.items() if len(v) >= 3}
        order = sorted(m, key=lambda t: m[t])
        out[st] = {t: (m[t], order.index(t) + 1) for t in m}
    _DEF[season] = out
    return out


def argue(g, pr, over, logs, season):
    """(puces, resume) pour la prop pr {id, name, side, stat, line, p_mkt, p_sim} du match g de nba_sim.json."""
    stat, line, name = pr["stat"], float(pr["line"]), pr["name"]
    first = name.split()[0] if name else name
    side, opp = pr["side"], ("away" if pr["side"] == "home" else "home")
    oab, oname = g[opp]["abbr"], g[opp].get("name") or g[opp]["abbr"]
    sfr = STAT_FR.get(stat, stat)
    log = logs.get(str(pr["id"])) or []
    D = defense(season) if len(defense(season).get(stat, {})) >= 20 else defense(season - 1)
    pro, contre = [], []

    # 1. Absences : chez l'adversaire (et ce qu'il encaisse sans eux), et dans sa propre equipe (effet mesure sur lui)
    out_opp = [o["name"] for o in ((g.get("injuries") or {}).get(opp) or {}).get("out") or []]
    for a in (g.get("absences") or {}).get(opp) or []:
        al = a.get("allowed") or {}
        k = "PTS" if stat in ("PTS", "PR", "PA", "PRA") else stat if stat in al else None
        if k and al.get(k) and al.get("n", 0) >= 3:
            sans, avec = al[k]
            txt = (f"{a['name']} ABSENT chez {oname} : sans lui, ils encaissent {_fr(sans)} {STAT_FR.get(k, k)} "
                   f"par match ({al['n']} matchs) contre {_fr(avec)} avec")
            (pro if (sans > avec) == over else contre).append(txt)
            break
    else:   # absence adverse sans effet mesure : argument seulement pour un « plus » (adversaire affaibli)
        if out_opp and over: pro.append(f"Absents chez {oname} : {', '.join(out_opp[:2])}")
    for a in (g.get("absences") or {}).get(side) or []:
        m = next((m for m in a.get("mates") or [] if m["name"] == name), None)
        k = stat if m and stat in m else ("PTS" if m and stat in PARTS else None)
        if m and k:
            sans, avec = m[k]
            txt = (f"Sans {a['name']} ({m['n']} matchs), {first} tourne à {_fr(sans)} {STAT_FR.get(k, k)} en "
                   f"{_fr(m['MIN'][0])} min, contre {_fr(avec)} avec lui")
            (pro if (sans > avec) == over else contre).append(txt)
            break

    # 2. Defense adverse sur cette stat
    d = (D.get(stat) or {}).get(oab)
    if d:
        val, rk = d
        txt = f"{oname} : {_ord(rk)} défense de la ligue en {sfr} encaissés ({_fr(val)} par match ; 1re = la plus avare)"
        good = (rk >= 21) if over else (rk <= 10)
        bad = (rk <= 10) if over else (rk >= 21)
        if good: pro.append(txt)
        elif bad: contre.append(txt)

    # 3. Reussite contre le meme type de defense (10 plus faibles si « plus », 10 meilleures si « moins »), 2 saisons
    tiers = []
    for dte, o, r, y in log[:60]:
        DY = defense(y).get(stat) or {}
        if o in DY and ((DY[o][1] >= 21) if over else (DY[o][1] <= 10)): tiers.append((r, _val(r, stat)))
    if len(tiers) >= 5:
        hit = sum(1 for _, v in tiers if (v > line) == over)
        avg = statistics.mean(v for _, v in tiers)
        lab = "10 pires" if over else "10 meilleures"
        txt = (f"{round(100 * hit / len(tiers))} % de réussite sur ses {len(tiers)} derniers matchs contre les {lab} défenses "
               f"en {sfr} ; moyenne de {_fr(avg)}")
        (pro if hit / len(tiers) >= 0.55 else contre).append(txt)
        sample = [r for r, _ in tiers]
    else:
        sample = [r for _, _, r, _ in log[:10]]

    # 4. Volume : tirs tentes (points), tentatives a 3 pts, minutes
    if sample:
        mins = statistics.mean(r.get("MIN", 0) for r in sample)
        if stat in ("PTS", "PR", "PA", "PRA"):
            pro.append(f"Moyenne de {_fr(statistics.mean(r.get('FGA', 0) for r in sample))} tirs tentés et {_fr(mins)} minutes "
                       "sur cet échantillon")
        elif stat == "3PM":
            pro.append(f"Moyenne de {_fr(statistics.mean(r.get('3PA', 0) for r in sample))} tirs à 3 pts tentés et {_fr(mins)} minutes")
        else:
            pro.append(f"{_fr(mins)} minutes de moyenne sur cet échantillon")
        l5 = [r.get("MIN", 0) for _, _, r, _ in log[:5]]
        if len(log) >= 15:
            base = statistics.mean(r.get("MIN", 0) for _, _, r, _ in log[5:20])
            if statistics.mean(l5) < base - 3:
                contre.append(f"Temps de jeu en baisse : {_fr(statistics.mean(l5))} min sur ses 5 derniers matchs contre {_fr(base)} avant")

    # 5. Forme : L10 et dernier match
    l10 = [_val(r, stat) for _, _, r, _ in log[:10]]
    if l10:
        hit = sum(1 for v in l10 if (v > line) == over)
        txt = f"{hit * 10} % de réussite sur ses 10 derniers matchs (moyenne {_fr(statistics.mean(l10))})"
        (pro if hit >= 6 else contre).append(txt)
        last = l10[0]
        txt = f"Sort d'un match à {last:g} {sfr}"
        if (last > line) == over: pro.append(txt)
        l5h = sum(1 for v in l10[:5] if (v > line) == over)
        if l5h <= 2: contre.append(f"Seulement {l5h}/5 sur ses 5 derniers matchs")

    # 6. Simulation et marche
    sm = next((p.get(stat) for p in g.get("players") or [] if str(p.get("id")) == str(pr["id"])), None) or {}
    pm = pr["p_mkt"] if over else 1 - pr["p_mkt"]
    ps = pr["p_sim"] if over else 1 - pr["p_sim"]
    simtxt = (f"Ma simulation : {_fr(sm['mean'])} {sfr} en moyenne, " if sm.get("mean") else "Ma simulation : ") + \
             f"{round(100 * ps)} % de chances ; le marché sans la marge : {round(100 * pm)} %"
    (pro if ps >= pm - 0.02 else contre).append(simtxt)

    ln = f"{line:g}".replace(".", ",")
    resume = (f"{name} doit faire {'plus' if over else 'moins'} de {ln} {sfr}. "
              + (f"Pour : {pro[0]}" + (f" ; {pro[1]}" if len(pro) > 1 else "") + ". " if pro else "")
              + (f"À surveiller : {contre[0]}." if contre else "Aucun fait majeur contre."))
    return pro, contre, resume


def for_pick(gid, pid, stat, line, over):
    """Argumentaire d'une prop du soir (lecture de data/nba_sim.json) : (puces pour, puces contre, resume) ou None."""
    import json
    from pathlib import Path
    import nba_props_edge as E
    import nba_boxdb as B
    try:
        sim = json.loads(Path("data/nba_sim.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    g = next((g for g in sim.get("games") or [] if str(g["gid"]) == str(gid)), None)
    if not g: return None
    pr = next((x for x in list(g.get("props") or []) + E._derived_ra(g)
               if str(x["id"]) == str(pid) and x["stat"] == stat and float(x["line"]) == float(line)), None)
    if not pr: return None
    season = B.season_year()
    if E._LOGS.get("season") != season: E._LOGS.update(season=season, logs=E.game_logs((season - 1, season)))
    return argue(g, pr, over, E._LOGS["logs"], season)


def html_block(arg):
    import html as H
    if not arg: return ""
    pro, contre, resume = arg
    e = lambda s: H.escape(str(s))
    return ('<div class="na-arg"><ul class="vd-why">' + "".join(f"<li>{e(x)}</li>" for x in pro) + "</ul>"
            + (('<div class="na-w">⚠️ À surveiller</div><ul class="vd-why">' + "".join(f"<li>{e(x)}</li>" for x in contre) + "</ul>")
               if contre else "") + f'<p class="na-r">{e(resume)}</p></div>')


def telegram(arg):
    if not arg: return ""
    pro, contre, resume = arg
    return "\n".join([f"• {x}" for x in pro] + ([f"⚠️ {x}" for x in contre]) + [f"<i>{resume}</i>"])
