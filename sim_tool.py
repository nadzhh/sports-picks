"""
sim_tool.py — simulations de scenario a la demande pour le chat (lancees par le relais chat_bot.py, JAMAIS par Claude :
il n'a pas de terminal). Demande de Wilson (04/11/2026) : choisir un match -> analyse approfondie -> simuler le match
ou un joueur, y compris « et si X ne joue pas ? ».

Requete (ligne « SIMULER: {...} » ecrite par Claude, validee ici) :
  {"sport": "nba" | "foot", "match": "<gid ESPN NBA | id FotMob>", "n": 10000, "absents": ["Nom", ...]}
Chaque scenario est compare a la simulation de reference (meme graine) : on lit l'EFFET du changement, pas le bruit.
Plus de simulations ne rend pas le modele plus juste (a 10 000, l'erreur de tirage est deja ~0,5 pt) : n <= N_MAX.
"""
import difflib, json
from datetime import datetime, timedelta, timezone

N_MAX, N_DEF = 20000, 10000
_STATE = {}


def _same(a, b):
    return difflib.SequenceMatcher(None, (a or "").lower(), (b or "").lower()).ratio()


def _pct(x): return f"{100 * x:.0f} %"


# ─── NBA ─────────────────────────────────────────────────────────────────────
def _nba_state():
    import nba_sim_run as R
    if "nba" not in _STATE:
        _STATE["nba"] = R.build_state(R.season_year(datetime.now(timezone.utc).date()))
    return _STATE["nba"]


def nba(gid, n=N_DEF, absents=()):
    import numpy as np
    import nba_sim_run as R
    from nba_sim import simulate
    st = _nba_state()
    today = datetime.now(timezone.utc).date()
    g = next((x for d in (today, today + timedelta(days=1), today - timedelta(days=1)) for x in R.upcoming_games(d)
              if str(x["gid"]) == str(gid)), None)
    if not g: return f"Match NBA {gid} introuvable (pas dans les matchs à venir)."
    found = []

    def run(drop):
        orig = R._roster

        def roster(tid, status, reason):
            r, s, out = orig(tid, status, reason)
            keep = []
            for p in r:
                if any(_same(p.get("name"), a) >= 0.85 for a in drop):
                    out = list(out) + [{"id": p["id"], "name": p["name"]}]; found.append(p["name"])
                else:
                    keep.append(p)
            return keep, s, out
        R._roster = roster
        orig_lines = R._lines
        if absents:   # la ligne Vegas suppose les statuts du soir : pour un scenario, simulation sans calage Vegas
            R._lines = lambda *a_, **k_: None
        try:
            inp, lines, _ = R.build_inputs(st, g)
        finally:
            R._roster = orig; R._lines = orig_lines
        return inp, simulate(inp, n=n, seed=int(g["gid"]) % (2 ** 31))

    base_inp, base = run(())
    sc_inp, sc = run(tuple(absents)) if absents else (base_inp, base)
    missing = [a for a in absents if not any(_same(f, a) >= 0.85 for f in found)]
    gp = R.fetch_game_props(g["gid"])
    lines = R.props_main_lines(gp) if gp else {}
    h, a = g["home"].get("abbreviation"), g["away"].get("abbreviation")

    def team(res):
        hp, ap = res["home"]["PTS"].sum(axis=1), res["away"]["PTS"].sum(axis=1)
        return float(hp.mean()), float(ap.mean()), float((hp > ap).mean())

    bh, ba, bp = team(base); sh, sa, sp = team(sc)
    out = [f"Simulation {a} @ {h} : {n} matchs" + (f", scénario sans {', '.join(found)}" if found else "") + "."
           + (" Référence et scénario simulés SANS calage sur la ligne Vegas (elle suppose les statuts actuels) : "
              "comparer les écarts, pas les niveaux. LIMITE : le niveau d'équipe du modèle NBA vient de ses résultats "
              "d'équipe, pas de la valeur de chaque joueur ; l'effet d'une absence sur le SCORE est sous-estimé (en temps "
              "normal la ligne Vegas le corrige). La partie fiable est la redistribution des minutes et des stats entre "
              "coéquipiers." if absents else "")]
    if missing: out.append(f"(Joueur(s) non trouvé(s) dans l'effectif : {', '.join(missing)}.)")
    out.append(f"Score moyen {h} {sh:.1f} - {sa:.1f} {a} (référence {bh:.1f} - {ba:.1f}) ; {h} gagne {_pct(sp)} (référence {_pct(bp)}).")
    rows = []
    for s in ("home", "away"):
        bidx = {p["id"]: i for i, p in enumerate(base_inp[s]["players"])}
        for i, p in enumerate(sc_inp[s]["players"]):
            on = sc[s]["MIN"][:, i] > 0
            if on.mean() < 0.3: continue
            stat = lambda res, k, j, m: float(sum(res[s][c][:, j] for c in R.STAT_COMPS[k])[m].mean())
            j = bidx.get(p["id"])
            bon = base[s]["MIN"][:, j] > 0 if j is not None else None
            cells = []
            for k in ("MIN", "PTS", "REB", "AST", "3PM", "STL", "BLK", "PRA"):
                v = float(sc[s]["MIN"][on, i].mean()) if k == "MIN" else stat(sc, k, i, on)
                ref = (float(base[s]["MIN"][bon, j].mean()) if k == "MIN" else stat(base, k, j, bon)) if j is not None and bon.sum() else None
                cells.append(f"{k} {v:.1f}" + (f" ({v - ref:+.1f})" if ref is not None and abs(v - ref) >= 0.3 and absents else ""))
            props = []
            for (aid, k), m in lines.items():
                if aid != p["id"] or k not in R.STAT_COMPS: continue
                x = sum(sc[s][c][:, i] for c in R.STAT_COMPS[k])[on]
                props.append(f"{k} {m['line']:g} : plus {_pct(float((x > m['line']).mean()))} (marché {_pct(m['p_over_novig'])})")
            rows.append(f"- {p['name']} ({h if s == 'home' else a}, joue {_pct(float(on.mean()))}) : " + ", ".join(cells)
                        + (" | lignes : " + " ; ".join(props) if props else ""))
    out.append("Joueurs (moyennes quand il joue, écart vs référence entre parenthèses) :\n" + "\n".join(rows))
    return "\n".join(out)


# ─── Foot ────────────────────────────────────────────────────────────────────
def _foot_state():
    import foot_sim_run as F
    if "foot" not in _STATE:
        _STATE["foot"] = F.build_state()
    return _STATE["foot"]


def foot(match_id, n=N_DEF, absents=()):
    import foot_sim_run as F
    st = _foot_state()
    g = next((x for x in F.upcoming(96) if str(x["id"]) == str(match_id)), None)
    if not g: return f"Match foot {match_id} introuvable (pas dans les 4 prochains jours simulés)."
    found = []
    orig = F.lineups

    def drop_lineups(st_, g_, day):
        lu = orig(st_, g_, day)
        for side in ("home", "away"):
            L = lu[side]
            for pid, (name, pos) in list(L["names"].items()):
                if any(_same(name, a) >= 0.85 for a in absents):
                    found.append(name)
                    if pid in L["xi"]:
                        L["xi"].remove(pid)
                        sub = next((b for b in L["bench"] if (L["names"].get(b) or ("", ""))[1] == pos), L["bench"][0] if L["bench"] else None)
                        if sub: L["bench"].remove(sub); L["xi"].append(sub)
                    elif pid in L["bench"]:
                        L["bench"].remove(pid)
        return lu
    base = F.run_game(st, g, n)
    if absents:
        F.lineups = drop_lineups
        try:
            sc = F.run_game(st, g, n)
        finally:
            F.lineups = orig
    else:
        sc = base
    missing = [x for x in absents if not any(_same(f, x) >= 0.85 for f in found)]
    hn, an = g["home"]["name"], g["away"]["name"]
    p, q = sc["p"], base["p"]
    d = lambda k, a_, b_: f" (référence {_pct(b_)})" if absents else ""
    out = [f"Simulation {hn} - {an} : {n} matchs" + (f", scénario sans {', '.join(found)}" if found else "") + "."]
    if missing: out.append(f"(Joueur(s) non trouvé(s) dans les compositions : {', '.join(missing)}.)")
    out.append(f"1 {_pct(p['1'])}{d(0, p['1'], q['1'])} / N {_pct(p['X'])}{d(0, p['X'], q['X'])} / 2 {_pct(p['2'])}{d(0, p['2'], q['2'])} ; "
               f"buts attendus {sc['goals'][0]:.2f}-{sc['goals'][1]:.2f} (référence {base['goals'][0]:.2f}-{base['goals'][1]:.2f}) ; "
               f"+2,5 {_pct(p['over']['2.5'])} ; les deux marquent {_pct(p['btts'])}.")
    sh = sc.get("shots") or {}
    if sh: out.append(f"Tirs {sh['home'][0]}-{sh['away'][0]} (cadrés {sh['home'][1]}-{sh['away'][1]}).")
    for side, nm in (("home", hn), ("away", an)):
        pl = (sc.get(side + "_info") or {}).get("players") or []
        out.append(f"{nm} : " + " ; ".join(f"{x['name']} marque {_pct(x['p_goal'])}, {x['shots']:.1f} tirs, jaune {_pct(x['p_yellow'])}"
                                           for x in sorted(pl, key=lambda x: -x["p_goal"])[:6]))
    return "\n".join(out)


def _base_ok(sport):
    from pathlib import Path
    d = Path("data/nba_db" if sport == "nba" else "data/foot_db")
    return d.is_dir() and sum(1 for _ in d.glob("*.json*")) >= 2


def run(req):
    """Execute une requete validee ; renvoie un texte (jamais d'exception vers le relais)."""
    try:
        if not _base_ok(str(req.get("sport", "")).lower()):
            return "Base de matchs indisponible dans le relais pour l'instant (il redémarre) : simulation impossible, réessaie dans quelques minutes."
        sport = str(req.get("sport", "")).lower()
        n = max(1000, min(int(req.get("n") or N_DEF), N_MAX))
        absents = [str(a)[:60] for a in (req.get("absents") or [])][:4]
        mid = str(req.get("match", ""))[:20]
        if not mid.isdigit(): return "Identifiant de match invalide."
        if sport == "nba": return nba(mid, n, absents)
        if sport == "foot": return foot(mid, n, absents)
        return "Sport inconnu (nba ou foot)."
    except Exception as e:
        return f"La simulation a échoué : {type(e).__name__}: {str(e)[:200]}"


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8")
    print(run(json.loads(sys.argv[1])))
