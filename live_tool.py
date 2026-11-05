"""
live_tool.py — etat d'un match EN COURS et projections sur le temps restant, pour l'« Analyse live » du chat
(demande de Wilson, 04/11/2026 : « quand un match a commence, recuperer les infos du debut de match et proposer des
idees : buteurs, tirs, vainqueur, buts... »). Appele par le relais (chat_bot.py), jamais par Claude.

FOOT (valide, backtest 759 matchs arretes a 30', 45', 60', 75') : buts restants ~ Poisson(niveau d'avant-match x temps
restant x etat du score x cartons rouges). L'xG observe en debut de match prevoit MOINS bien la suite que le niveau
d'avant-match (log-loss 1X2 a 30' : 0,939 vs 1,415 avec l'xG observe seul) -> il est affiche, pas utilise.
NBA (NON valide faute d'historique en cours de match) : stats actuelles + minutes restantes x rendement d'avant-match
(fautes et match plie reduisent les minutes) ; victoire : marge actuelle + marge attendue sur le temps restant.
"""
import json, math
from datetime import datetime, timezone
from pathlib import Path

RED_SELF, RED_OPP, TRAIL, LEAD = 0.69, 1.61, 1.06, 0.965


def _load(p, d):
    try: return json.loads(Path(p).read_text(encoding="utf-8"))
    except (OSError, ValueError): return d


def _pc(x): return f"{100 * x:.0f} %"


def _pois(l, k): return math.exp(-l) * l ** k / math.factorial(k)


def _p_over(mean, line):
    """P(X > line) pour X ~ Poisson(mean) (compteurs : buts, tirs, passes...)."""
    k = int(math.floor(line))
    return 1 - sum(_pois(mean, i) for i in range(k + 1)) if mean > 0 else 0.0


# ─── FOOT ─────────────────────────────────────────────────────────────────────
def _minute(pp):
    h = (pp.get("header") or {}).get("status") or {}
    import re
    lt = h.get("liveTime") or {}
    m = re.match(r"\s*(\d+):(\d+)", lt.get("long") or "")          # « 19:39 » (le champ court contient des caracteres invisibles)
    if m: return min(int(m.group(1)) + int(m.group(2)) / 60, 125), h
    d = re.findall(r"\d+", lt.get("short") or "")                    # « 45+2 » -> 47
    if d: return min(sum(int(x) for x in d[:2]), 125), h
    return (90 if h.get("finished") else 45 if (h.get("halfs") or {}).get("firstHalfEnded") else 0), h


def foot(mid):
    import foot_db
    pp = foot_db._page(mid, fresh=True)          # page a jour (sinon cache CDN de 10 min)
    if not pp: return "Page FotMob du match introuvable."
    t, h = _minute(pp)
    if not h.get("started"): return "Le match n'a pas encore commencé : utilise l'analyse d'avant-match."
    if h.get("finished"):
        return (f"MATCH TERMINÉ · score final {h.get('scoreStr', '?')} ({(h.get('reason') or {}).get('long', 'fin du match')}). "
                "Plus aucun pari live possible : analyse d'après-match seulement.")
    gm = next((g for g in (_load("data/foot_sim.json", {}) or {}).get("games") or [] if str(g["id"]) == str(mid)), None)
    if not gm: return "Pas de simulation d'avant-match pour ce match (hors Top 5 / coupes simulées) : projection impossible."
    rec = foot_db.extract(pp, gm["league"], "live")
    hn, an = gm["home"]["name"], gm["away"]["name"]
    g = {1: 0, 0: 0}; xg = {1: 0.0, 0: 0.0}; sh = {1: 0, 0: 0}; so = {1: 0, 0: 0}; red = {1: 0, 0: 0}
    by_player = {}
    for s in rec.get("shots") or []:
        side = s[0]
        if s[8] == "Goal": g[side] += 1
        if s[13]: continue
        xg[side] += s[6] or 0; sh[side] += 1; so[side] += bool((s[11] and not s[12]) or s[8] == "Goal")
        b = by_player.setdefault(s[1], [0, 0, 0]); b[0] += 1; b[1] += bool((s[11] and not s[12]) or s[8] == "Goal"); b[2] += s[8] == "Goal"
    sc = (rec.get("score") or [g[1], g[0]])
    g = {1: sc[0], 0: sc[1]}
    for c in rec.get("cards") or []:
        if c[4] in ("Red", "YellowRed"): red[c[2]] += 1
    fin = bool(h.get("finished"))
    rem = 0.0 if fin else max(93 - t, 0) / 93
    lam = {}
    for side, k in ((1, 0), (0, 1)):
        d = g[side] - g[1 - side]
        l = gm["goals"][k] * rem * (TRAIL if d < 0 else LEAD if d > 0 else 1.0) * (RED_SELF ** red[side]) * (RED_OPP ** red[1 - side])
        lam[side] = max(l, 0.0)
    ph = [_pois(lam[1], i) for i in range(10)]; pa = [_pois(lam[0], j) for j in range(10)]
    p1 = px = p2 = pbt = 0.0; over = {}
    for i in range(10):
        for j in range(10):
            q = ph[i] * pa[j]; fh, fa = g[1] + i, g[0] + j
            p1 += q * (fh > fa); px += q * (fh == fa); p2 += q * (fh < fa); pbt += q * (fh > 0 and fa > 0)
            for L in (0.5, 1.5, 2.5, 3.5, 4.5, 5.5):
                over[L] = over.get(L, 0) + q * (fh + fa > L)
    tot = lam[1] + lam[0]
    nxt = (lam[1] / tot if tot else 0, lam[0] / tot if tot else 0, math.exp(-tot))
    out = [f"MATCH EN COURS · {hn} {g[1]}-{g[0]} {an} · {t:.0f}e minute"
           + (f" · rouges {hn} {red[1]} / {an} {red[0]}" if red[1] or red[0] else ""),
           f"Jusqu'ici : tirs {sh[1]}-{sh[0]} (cadrés {so[1]}-{so[0]}), xG {xg[1]:.2f}-{xg[0]:.2f} "
           f"(avant-match : buts attendus {gm['goals'][0]:.2f}-{gm['goals'][1]:.2f} sur 90 min). "
           "L'xG observé est indicatif : sur 759 matchs, il prévoit moins bien la suite que le niveau d'avant-match.",
           f"Buts encore attendus : {hn} {lam[1]:.2f}, {an} {lam[0]:.2f} (temps restant x niveau d'avant-match x état du score x rouges).",
           f"Probabilités finales : 1 {_pc(p1)} / N {_pc(px)} / 2 {_pc(p2)} · les deux marquent {_pc(pbt)} · "
           + " · ".join(f"plus de {L:g} {_pc(v)}" for L, v in over.items() if 0.02 < v < 0.98),
           f"Prochain but : {hn} {_pc(nxt[0] * (1 - nxt[2]))} / {an} {_pc(nxt[1] * (1 - nxt[2]))} / aucun {_pc(nxt[2])}"]
    # tirs : actuels + rythme d'avant-match sur le temps restant
    pre = gm.get("shots") or {}
    if pre.get("home"):
        for side, k, nm in ((1, "home", hn), (0, "away", an)):
            mshot = sh[side] + pre[k][0] * rem; mso = so[side] + pre[k][1] * rem
            out.append(f"Tirs {nm} : {sh[side]} → {mshot:.1f} attendus au total (cadrés {so[side]} → {mso:.1f}) ; "
                       + ", ".join(f"plus de {L:g} tirs {_pc(_p_over(pre[k][0] * rem, L - sh[side]) if L > sh[side] else 1)}"
                                   for L in (math.floor(mshot) - 1.5, math.floor(mshot) + 0.5, math.floor(mshot) + 2.5) if L > 0))
    # buteurs encore sur le terrain
    names = {p[0]: p[1] for p in rec.get("players") or []}
    off = {p[0] for p in rec.get("players") or [] if p[7]}                 # sortis (raison de sortie renseignee)
    on_pitch = {p[0] for p in rec.get("players") or [] if p[5] is not None and p[0] not in off}   # [id, nom, cote, titulaire, poste, entree, sortie, raison, note]
    sc_lines = []
    for sd, side in (("home", 1), ("away", 0)):
        info = gm.get(sd + "_info") or {}
        team_pre = max(gm["goals"][0 if side == 1 else 1], 0.05)
        for pl in info.get("players") or []:
            pid = next((i for i, n in names.items() if n == pl["name"]), None)
            if pid is None or pid not in on_pitch: continue
            lam_i = -math.log(max(1 - pl["p_goal"], 1e-6)) * (lam[side] / (team_pre * rem) if rem else 0) * rem
            b = by_player.get(pid, [0, 0, 0])
            sc_lines.append((1 - math.exp(-lam_i), f"{pl['name']} ({hn if side == 1 else an}) : marque encore {_pc(1 - math.exp(-lam_i))}"
                             f" · déjà {b[2]} but(s), {b[0]} tir(s), {b[1]} cadré(s)"))
    if sc_lines:
        out.append("Buteurs encore sur le terrain (probabilité de marquer d'ici la fin) : "
                   + " ; ".join(t_ for _, t_ in sorted(sc_lines, key=lambda x: -x[0])[:8]))
    cards = [f"{c[0]}' {names.get(c[3], '?')} ({c[4]})" for c in rec.get("cards") or []]
    if cards: out.append("Cartons : " + ", ".join(cards))
    return "\n".join(out)


# ─── NBA ──────────────────────────────────────────────────────────────────────
def nba(gid):
    from nba_boxdb import _get, BASE, _players
    s = _get(f"{BASE}/summary?event={gid}") or {}
    comp = ((s.get("header") or {}).get("competitions") or [{}])[0]
    stt = comp.get("status") or {}
    state = (stt.get("type") or {}).get("state")
    if state == "pre": return "Le match n'a pas encore commencé : utilise l'analyse d'avant-match."
    period, clock = int(stt.get("period") or 0), stt.get("displayClock") or "0:00"
    try:
        mm, ss = clock.split(":"); cmin = int(mm) + int(float(ss)) / 60
    except ValueError:
        cmin = 0.0
    rem_game = 0.0 if state == "post" else max((4 - period) * 12 + cmin, 0) if period <= 4 else cmin
    gm = next((g for g in (_load("data/nba_sim.json", {}) or {}).get("games") or [] if str(g["gid"]) == str(gid)), None)
    if not gm: return "Pas de simulation d'avant-match pour ce match : projection impossible."
    sc = {c.get("homeAway"): int(c.get("score") or 0) for c in comp.get("competitors") or []}
    h, a = gm["home"]["abbr"], gm["away"]["abbr"]
    tm = gm.get("team") or {}
    frac = rem_game / 48
    margin_now = sc.get("home", 0) - sc.get("away", 0)
    m_exp = margin_now + (tm.get("margin_mean") or 0) * frac
    sd = (tm.get("margin_sd") or 13) * math.sqrt(max(frac, 1e-6))
    p_home = 0.5 * (1 + math.erf(m_exp / (sd * math.sqrt(2)))) if rem_game > 0 else float(margin_now > 0)
    tot_exp = sc.get("home", 0) + sc.get("away", 0) + (tm.get("total_mean") or 0) * frac
    out = [f"{'MATCH TERMINÉ' if state == 'post' else 'MATCH EN COURS'} · {a} {sc.get('away', 0)} @ {h} {sc.get('home', 0)}"
           + ("" if state == "post" else f" · {period}e quart-temps, {clock} à jouer"),
           f"Projection : {h} gagne {_pc(p_home)} (marge finale attendue {m_exp:+.1f}) · total final attendu {tot_exp:.1f} "
           f"(ligne d'avant-match {(gm.get('lines') or {}).get('total')}). Modèle live NBA NON validé : indicatif."]
    sim = {p["id"]: p for p in gm.get("players") or []}
    props = {}
    for pr in gm.get("props") or []: props.setdefault(pr["id"], []).append(pr)
    blowout = abs(margin_now) >= 20 and period >= 4
    rows = []
    for team in (s.get("boxscore") or {}).get("players") or []:
        ab = (team.get("team") or {}).get("abbreviation")
        for p in _players(team):
            if p.get("dnp"): continue
            q = sim.get(p["id"])
            if not q: continue
            share = (q.get("MIN") or 0) / 48
            f = 0.5 if p.get("PF", 0) >= 5 else 0.75 if (p.get("PF", 0) >= 4 and period <= 3) else 1.0
            if blowout and q.get("starter"): f *= 0.5
            rem_i = share * rem_game * f
            line = []
            for st in ("PTS", "REB", "AST", "3PM", "STL", "BLK"):
                rate = (q.get(st) or {}).get("mean", 0) / max(q.get("MIN") or 1, 1)
                now = p.get(st, 0); fut = rate * rem_i
                for pr in props.get(p["id"], []):
                    if pr["stat"] != st: continue
                    need = pr["line"] - now
                    pov = 1.0 if need < 0 else _p_over(fut, need)
                    line.append(f"{st} {now} → {now + fut:.1f} (ligne {pr['line']:g} : plus {_pc(pov)}, d'avant-match {_pc(pr['p_sim'])})")
            if line:
                rows.append(f"- {p['name']} ({ab}, {p.get('MIN', 0):.0f} min jouées, {p.get('PF', 0)} fautes, encore ~{rem_i:.0f} min) : " + " ; ".join(line))
    if rows: out.append("Joueurs (stat actuelle → projection finale) :\n" + "\n".join(rows[:24]))
    return "\n".join(out)


def run(sport, mid):
    try:
        if not str(mid).isdigit(): return "Identifiant de match invalide."
        if sport == "foot": return foot(mid)
        if sport == "nba": return nba(mid)
        return "Sport inconnu."
    except Exception as e:
        return f"Données live indisponibles : {type(e).__name__}: {str(e)[:200]}"


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8")
    print(run(sys.argv[1], sys.argv[2]))
