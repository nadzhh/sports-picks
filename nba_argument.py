"""
nba_argument.py — Argumentaire d'un pari sur prop NBA, facon « NBA PLAY » (Wilson, 06/11/2026 : « les picks Bankroll IA
je veux qu'ils soient analyses et argumentes tels quels » ; exemple : LaMelo Ball plus de 19,5 points — Edwards absent,
Pelicans 25e en points encaisses, 89 % sur ses 9 derniers matchs contre les 10 pires defenses, 20,6 tirs tentes et
31,1 minutes, 70 % sur ses 10 derniers, sort d'un match a 34 points).

Memes rubriques, calculees sur nos donnees (feuilles de match des deux saisons, absences et effet mesure sur les
coequipiers, simulation, marche sans marge), PLUS une rubrique « a surveiller » : les faits qui vont CONTRE le pari.
Un post de ce genre choisit ses chiffres (ex. un echantillon « contre les 10 pires defenses ») ; ici ils sont affiches
quel que soit le sens, et la decision reste la probabilite et la cote (cote juste, « prends si »).

16/11/2026 (idees 3, 4, 5 de la boite a idees) — trois rubriques d'EXPLICATION, sans effet sur les probabilites ni la
selection :
  - defense adverse PAR POSTE (G / F / C, poste des feuilles de match ESPN) : ce que l'adversaire concede par match aux
    joueurs du meme poste, rang /30 et moyenne ligue (posdef). Mesure (nba_posdef_validate.py, walk-forward 2024-25 a
    2026-27, ~20 000 cas par stat et par saison) : a defense GLOBALE connue, la part propre au poste n'explique rien de
    plus (pente PTS -0,01 ± 0,09 / +0,04 ± 0,08, REB +0,02 / +0,08 ± 0,07, AST +0,09 / +0,02 ± 0,06, 3PM +0,03 / +0,04
    ± 0,06 ; la part globale : +0,2 a +0,7). C'est donc un repere de lecture, pas un signal a brancher ;
  - confrontations directes (h2h) sur toutes les saisons en base (3), au lieu des 20 derniers matchs. Mesure walk-forward
    (2025-26 et 2026-27, >= 3 face-a-face, ~10 000 cas par stat) : seuls ~10 % de l'ecart H2H / L20 se retrouvent dans
    le match suivant (pente PTS +0,08 ± 0,04, REB +0,12, AST +0,13, 3PM +0,09), sans controle du niveau de carriere ;
  - mediane a cote des moyennes L5 / L10, et alerte quand un match extreme tire la moyenne. Mesure : la mediane L10 ne
    predit pas mieux le match suivant que la moyenne (erreur moyenne PTS 5,09 contre 5,02 ; 3PM 1,01 contre 1,02) :
    c'est une aide a la lecture.

16/11/2026 (Wilson, d'apres un post « COUP DU JOUR » qui lui plait : « oui go ») — deux rubriques empruntees a la forme :
  - « Objection principale » : le fait le plus lourd CONTRE le pari, et la reponse chiffree (ce que la simulation, qui
    l'integre ou non, en fait). Sans reponse (la simulation elle-meme est contre), on le dit ;
  - « Risque de blowout » : ecart prevu par le marche, probabilite simulee d'un ecart >= 15 points, et ce que perd un
    titulaire dans ces matchs. Mesure (3 saisons, titulaires a >= 28 min sur leurs 10 derniers matchs, 14 848 cas) :
    par rapport a un match serre (0-4 pts), minutes -1 % (5-9 pts), -4 % (10-14), -9 % (15-19), -12 % (20-24),
    -18 % (25+) ; sur l'ensemble des ecarts >= 15 : -13 % de minutes, -10 % de points (5 242 cas). nba_sim suppose
    -0,6 %/pt au-dela de 4 (plancher -30 %) : -5 % a 12 pts, -8 % a 17, -14 % a 28 -> proche, un peu court sur les
    gros ecarts (et un peu fort sur les ecarts moyens).
"""
import statistics
from collections import defaultdict

STAT_FR = {"PTS": "points", "REB": "rebonds", "AST": "passes", "3PM": "paniers à 3 pts", "PR": "points + rebonds",
           "PA": "points + passes", "RA": "rebonds + passes", "PRA": "points + rebonds + passes", "STL": "interceptions",
           "BLK": "contres"}
PARTS = {"PR": ("PTS", "REB"), "PA": ("PTS", "AST"), "RA": ("REB", "AST"), "PRA": ("PTS", "REB", "AST")}
_DEF = {}
BLOWOUT_MIN, BLOWOUT_PTS, BLOWOUT_N = 0.13, 0.10, 5242   # pertes d'un titulaire si ecart final >= 15 (mesure du 16/11)
# ordre de gravite des faits « contre » pour choisir l'objection principale
OBJ_ORDER = ("abs_mate", "sim", "abs_opp", "def", "h2h", "min", "blowout", "tiers", "form", "posdef")


def _val(row, stat):
    return sum(row.get(k, 0) for k in PARTS.get(stat, (stat,)))


def _fr(x, d=1):
    return f"{x:.{d}f}".replace(".", ",")


def _ord(n):
    return "1re" if n == 1 else f"{n}e"


def _g(x):
    """7.5 -> « 7,5 » ; 8.0 -> « 8 »."""
    return f"{float(x):g}".replace(".", ",")


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


# ─── Defense par poste (idee 3) ──────────────────────────────────────────────
POS_FR = {"G": "arrières", "F": "ailiers", "C": "pivots"}
POSDEF_STATS = ("PTS", "REB", "AST", "3PM", "PR", "PA", "RA", "PRA")
POSDEF_MIN_GAMES = 8      # matchs joues par chaque equipe avant de quitter la saison precedente
_POSDEF = {}


def pos_group(p):
    """Poste ESPN -> G / F / C (« PG », « SG » -> G ; « SF », « PF » -> F ; « F-C » -> C) ; None si inconnu."""
    s = (p or "").upper()
    if s in ("", "NA"): return None
    return "C" if "C" in s else "F" if "F" in s else "G" if "G" in s else None


def _posdef_season(year):
    """{stat: {poste: {"lg": moyenne ligue par match, "teams": {abbr: (par match, rang 1 = concede le plus)}}}, "n": min
    de matchs par equipe}. Saison reguliere seulement. Par match A TEMPS DE JEU EGAL : rythme concede par minute aux
    joueurs du poste x minutes moyennes du poste par match dans la ligue (le total brut depend sinon de la composition
    de l'adversaire du soir : 3 ailiers listes un soir, 2 le suivant)."""
    import nba_boxdb as B
    S = defaultdict(lambda: defaultdict(float))      # (equipe, poste) -> stat / MIN
    games = defaultdict(int)
    lg = defaultdict(lambda: defaultdict(float))     # poste -> stat / MIN
    for g in B.load_season(year).values():
        if g.get("type") not in (None, "regular"): continue
        for side, opp in (("home", "away"), ("away", "home")):
            rows = [r for r in (g.get(opp) or {}).get("players") or [] if not r.get("dnp") and r.get("MIN")]
            if not rows: continue
            t = g[side]["abbr"]; games[t] += 1
            for r in rows:
                P = pos_group(r.get("pos"))
                if not P: continue
                for acc in (S[(t, P)], lg[P]):
                    acc["MIN"] += float(r["MIN"])
                    for st in POSDEF_STATS: acc[st] += _val(r, st)
    if not games: return {"n": 0}
    tg = sum(games.values())
    out = {"n": min(games.values()), "teams": len(games)}
    for st in POSDEF_STATS:
        out[st] = {}
        for P in POS_FR:
            if not lg[P]["MIN"]: continue
            min_pg = lg[P]["MIN"] / tg
            vals = {t: S[(t, P)][st] / S[(t, P)]["MIN"] * min_pg for t in games if S[(t, P)]["MIN"] > 0}
            order = sorted(vals, key=lambda t: -vals[t])
            out[st][P] = {"lg": lg[P][st] / tg, "teams": {t: (vals[t], order.index(t) + 1) for t in vals}}
    return out


def posdef(season):
    """Defense par poste de la saison en cours, ou de la precedente tant que toutes les equipes n'ont pas
    POSDEF_MIN_GAMES matchs. Renvoie (table, annee utilisee)."""
    if season not in _POSDEF:
        cur = _posdef_season(season)
        if cur.get("n", 0) >= POSDEF_MIN_GAMES and cur.get("teams", 0) >= 28:
            _POSDEF[season] = (cur, season)
        else:
            _POSDEF[season] = (_posdef_season(season - 1), season - 1)
    return _POSDEF[season]


def posdef_line(oab, oname, P, stat, season):
    """(texte, rang) : « CHI concède 9,1 passes par match aux ailiers (2e plus haut total, moyenne ligue 7,4) »."""
    T, year = posdef(season)
    d = ((T.get(stat) or {}).get(P) or {})
    v = (d.get("teams") or {}).get(oab)
    if not v: return None, None
    val, rk = v
    nt = len(d["teams"])
    if rk == 1: where = "plus haut total"
    elif rk == nt: where = "plus bas total"
    elif rk <= (nt + 1) // 2: where = f"{_ord(rk)} plus haut total"
    else: where = f"{_ord(nt + 1 - rk)} plus bas total"
    when = "" if year == season else f", saison {year - 1}-{str(year)[2:]} faute de recul"
    return (f"{oname} concède {_fr(val)} {STAT_FR.get(stat, stat)} par match à l'ensemble des {POS_FR[P]} adverses "
            f"({where} de la ligue, moyenne {_fr(d['lg'])}{when})"), rk


# ─── Confrontations directes (idee 4) ────────────────────────────────────────
_H2H = {}


def _logs_all(season):
    """Feuilles de match du joueur sur toutes les saisons en base (saison en cours + 2 precedentes)."""
    if _H2H.get("season") != season:
        import nba_props_edge as E
        _H2H.update(season=season, logs=E.game_logs(tuple(range(season - 2, season + 1))))
    return _H2H["logs"]


def h2h(pid, opp, stat, line=None, over=True, season=None):
    """Matchs du joueur contre cet adversaire (toutes saisons en base, du plus recent au plus ancien) :
    {"n", "vals", "dates", "avg", "med", "hit" (si line), "seasons"} ; None si aucun match ou base absente."""
    import nba_boxdb as B
    season = season or B.season_year()
    stat = {"FG3M": "3PM"}.get(stat, stat)
    log = _logs_all(season).get(str(pid)) or []
    rows = [(d, _val(r, stat), y) for d, o, r, y in log if o == opp]
    if not rows: return None
    vals = [v for _, v, _ in rows]
    out = {"n": len(vals), "vals": vals, "dates": [d for d, _, _ in rows], "avg": statistics.mean(vals),
           "med": statistics.median(vals), "seasons": len({y for _, _, y in rows})}
    if line is not None:
        out["hit"] = sum(1 for v in vals if (v > float(line)) == over)
    return out


def h2h_text(h, opp, line, over):
    """« 3/4 au-dessus de 6,5 contre CHI, médiane 8 (2 saisons) »."""
    g = lambda x: f"{float(x):g}".replace(".", ",")
    sea = f", sur {h['seasons']} saisons" if h["seasons"] > 1 else ""
    return (f"{h['hit']}/{h['n']} {'au-dessus' if over else 'en dessous'} de {g(line)} contre {opp}, médiane "
            f"{g(h['med'])} (moyenne {_fr(h['avg'])}{sea})")


# ─── Mediane (idee 5) ────────────────────────────────────────────────────────
def mean_median(vals, line):
    """(moyenne, mediane, ecart notable ?) ; notable = |moyenne - mediane| >= 10 % de la ligne (0,5 au moins)."""
    if not vals: return None, None, False
    m, md = statistics.mean(vals), statistics.median(vals)
    return m, md, abs(m - md) >= max(0.5, 0.10 * float(line))


def argue(g, pr, over, logs, season):
    """(puces, resume) pour la prop pr {id, name, side, stat, line, p_mkt, p_sim} du match g de nba_sim.json."""
    stat, line, name = pr["stat"], float(pr["line"]), pr["name"]
    first = name.split()[0] if name else name
    side, opp = pr["side"], ("away" if pr["side"] == "home" else "home")
    oab, oname = g[opp]["abbr"], g[opp].get("name") or g[opp]["abbr"]
    sfr = STAT_FR.get(stat, stat)
    log = logs.get(str(pr["id"])) or []
    D = defense(season) if len(defense(season).get(stat, {})) >= 20 else defense(season - 1)
    pro, contre, kind = [], [], {}

    def put(good, txt, k, val=None):
        (pro if good else contre).append(txt)
        if not good: kind[txt] = (k, val)

    # 1. Absences : chez l'adversaire (et ce qu'il encaisse sans eux), et dans sa propre equipe (effet mesure sur lui)
    out_opp = [o["name"] for o in ((g.get("injuries") or {}).get(opp) or {}).get("out") or []]
    for a in (g.get("absences") or {}).get(opp) or []:
        al = a.get("allowed") or {}
        k = "PTS" if stat in ("PTS", "PR", "PA", "PRA") else stat if stat in al else None
        if k and al.get(k) and al.get("n", 0) >= 3:
            sans, avec = al[k]
            txt = (f"{a['name']} ABSENT chez {oname} : sans lui, ils encaissent {_fr(sans)} {STAT_FR.get(k, k)} "
                   f"par match ({al['n']} matchs) contre {_fr(avec)} avec")
            put((sans > avec) == over, txt, "abs_opp")
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
            put((sans > avec) == over, txt, "abs_mate", (sans, a["name"], m["n"]) if k == stat else None)
            break

    # 2. Defense adverse sur cette stat
    d = (D.get(stat) or {}).get(oab)
    if d:
        val, rk = d
        txt = f"{oname} : {_ord(rk)} défense de la ligue en {sfr} encaissés ({_fr(val)} par match ; 1re = la plus avare)"
        good = (rk >= 21) if over else (rk <= 10)
        bad = (rk <= 10) if over else (rk >= 21)
        if good: pro.append(txt)
        elif bad: put(False, txt, "def")

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
        put(hit / len(tiers) >= 0.55, txt, "tiers")
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
                put(False, f"Temps de jeu en baisse : {_fr(statistics.mean(l5))} min sur ses 5 derniers matchs contre {_fr(base)} avant", "min")

    # 4a. Risque de blowout (16/11/2026) : titulaire (>= 28 min sur ses 10 derniers), ecart >= 15 pts simule >= 30 %
    T = g.get("team") or {}
    L = g.get("lines") or {}
    m10 = [r.get("MIN", 0) for _, _, r, _ in log[:10]]
    if m10 and statistics.mean(m10) >= 28 and (T.get("p_blowout") or 0) >= 0.30:
        fav = ""
        if L.get("home_spread") not in (None, 0):
            fh = float(L["home_spread"]) < 0
            fav = (f"{g['home' if fh else 'away'].get('name') or g['home' if fh else 'away']['abbr']} favori de "
                   f"{_g(abs(float(L['home_spread'])))} points ; ")
        lost = f"{round(100 * BLOWOUT_MIN)} % de minutes" + (f" et {round(100 * BLOWOUT_PTS)} % de points" if stat in ("PTS", "PR", "PA", "PRA") else "")
        put(not over, f"Risque de blowout : {fav}ma simulation donne {round(100 * T['p_blowout'])} % de chances d'un écart de "
                      f"15 points ou plus ; dans ces matchs, un titulaire perd en moyenne {lost} par rapport à un match "
                      f"serré (3 saisons, {BLOWOUT_N // 1000} {BLOWOUT_N % 1000:03d} cas)", "blowout")

    # 4b. Defense adverse sur le POSTE du joueur (G / F / C) : affichee quand elle est dans les 5 premiers ou les 5 derniers ; elle
    # complete le rang global, qui peut masquer un trou sur un poste (rang 1 = concede le plus, a l'inverse du rang
    # global). Repere de lecture : sans pouvoir predictif mesure au-dela de la defense globale (voir en-tete).
    P = pos_group(log[0][2].get("pos")) if log else None
    if P and stat in POSDEF_STATS:
        txt, rk = posdef_line(oab, oab, P, stat, season)
        if txt and (rk <= 5 or rk >= 26):
            put((rk <= 5) == over, txt, "posdef")

    # 4c. Confrontations directes : toutes les saisons en base (et plus seulement les 20 derniers matchs)
    H = h2h(pr["id"], oab, stat, line, over, season)
    if H and H["n"] >= 2:
        put(H["hit"] / H["n"] >= 0.55, "Face à face : " + h2h_text(H, oab, line, over), "h2h")

    # 5. Forme : L10 (moyenne ET mediane) et dernier match
    l10 = [_val(r, stat) for _, _, r, _ in log[:10]]
    if l10:
        hit = sum(1 for v in l10 if (v > line) == over)
        m10, md10, gap10 = mean_median(l10, line)
        m5, md5, _ = mean_median(l10[:5], line)
        txt = (f"{round(100 * hit / len(l10))} % de réussite sur ses {len(l10)} derniers matchs (moyenne {_fr(m10)}, "
               f"médiane {_g(md10)} ; sur 5 : moyenne {_fr(m5)}, médiane {_g(md5)})")
        put(hit / len(l10) >= 0.6, txt, "form")
        if gap10:   # un match extreme tire la moyenne : la mediane dit mieux ce qu'il fait d'habitude
            up = m10 > md10
            ext = max(l10) if up else min(l10)
            txt = (f"Moyenne sur 10 ({_fr(m10)}) tirée vers le {'haut' if up else 'bas'} par un match à {_g(ext)} : "
                   f"médiane {_g(md10)}" + (f", la ligne {_g(line)} est entre les deux" if min(m10, md10) < line < max(m10, md10) else ""))
            put(up != over, txt, "form")
        last = l10[0]
        txt = f"Sort d'un match à {last:g} {sfr}"
        if (last > line) == over: pro.append(txt)
        l5h = sum(1 for v in l10[:5] if (v > line) == over)
        if l5h <= 2: put(False, f"Seulement {l5h}/5 sur ses 5 derniers matchs", "form")

    # 6. Simulation et marche
    sm = next((p.get(stat) for p in g.get("players") or [] if str(p.get("id")) == str(pr["id"])), None) or {}
    pm = pr["p_mkt"] if over else 1 - pr["p_mkt"]
    ps = pr["p_sim"] if over else 1 - pr["p_sim"]
    simtxt = (f"Ma simulation : {_fr(sm['mean'])} {sfr} en moyenne, " if sm.get("mean") else "Ma simulation : ") + \
             f"{round(100 * ps)} % de chances ; le marché sans la marge : {round(100 * pm)} %"
    put(ps >= pm - 0.02, simtxt, "sim")

    ln = f"{line:g}".replace(".", ",")
    resume = (f"{name} doit faire {'plus' if over else 'moins'} de {ln} {sfr}. "
              + (f"Pour : {pro[0]}" + (f" ; {pro[1]}" if len(pro) > 1 else "") + ". " if pro else "")
              + (objection(contre, kind, sm, ps, pm, over, sfr, stat) if contre else "Aucun fait majeur contre."))
    return pro, contre, resume


def objection(contre, kind, sm, ps, pm, over, sfr, stat):
    """« Objection principale : … Réponse : … » — le fait contre le plus lourd et ce qu'on en fait, chiffres a l'appui."""
    rank = {k: i for i, k in enumerate(OBJ_ORDER)}
    obj = min(contre, key=lambda t: (rank.get((kind.get(t) or (None,))[0], 99), contre.index(t)))
    k, val = kind.get(obj) or (None, None)
    sim = ((f"{_fr(sm['mean'])} {sfr} en moyenne, " if sm.get("mean") else "")
           + f"{round(100 * ps)} % pour le {'plus' if over else 'moins'} (marché sans marge : {round(100 * pm)} %)")
    if k == "sim":
        rep = ("aucune : ma simulation est moins optimiste que le marché, le pari tient au calage du marché (biais "
               "mesuré par stat), pas à elle. Prudence sur la mise")
    elif k == "abs_mate" and val and sm.get("mean") and abs(sm["mean"] - val[0]) >= 0.15 * val[0]:
        # la simulation integre l'absence mais reste loin de ce qu'il fait reellement sans lui : on le dit
        rep = (f"fragile : ma simulation l'intègre mais n'attend que {_fr(sm['mean'])} {sfr}, contre {_fr(val[0])} "
               f"sans {val[1]} sur {val[2]} matchs ; elle parie sur un retour vers son niveau habituel "
               f"({round(100 * ps)} % pour le {'plus' if over else 'moins'}, marché sans marge : {round(100 * pm)} %)")
    elif k == "h2h":
        try:
            import nba_h2h_weight as HW
            w = HW.weight(stat)
        except Exception:
            w = 0.10
        rep = (f"le face-à-face pèse peu : seuls ~{round(100 * w)} % de l'écart se retrouvent au match suivant (mesure sur "
               f"3 saisons) ; ma simulation : {sim}")
    elif k == "tiers":
        rep = f"échantillon trié sur un type de défense, moins fiable que son niveau global ; ma simulation : {sim}"
    elif k == "posdef":
        rep = f"la défense par poste n'ajoute rien de mesurable à la défense globale ; ma simulation : {sim}"
    else:   # absences, defense, minutes, forme, blowout : integres a la simulation
        rep = f"ma simulation l'intègre déjà : {sim}"
    return f"Objection principale : {obj}. Réponse : {rep}."


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
