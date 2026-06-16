"""
wc_match_importance.py — Calcule l'importance d'un match WC pour chaque équipe.

À partir des résultats des matchs WC déjà joués (via FotMob league endpoint),
calcule pour chaque équipe :
  - matches_played : nb de matchs déjà joués dans le tournoi
  - pts : points accumulés (3 pour V, 1 pour N, 0 pour D)
  - status : "must_win" / "qualif_done" / "eliminated" / "in_play"
  - importance_modifier : facteur λ à appliquer (1.0 = neutre)
      * must_win en match 3       → 1.05 (joue à fond)
      * qualif_done en match 3    → 0.85 (rotation, gestion)
      * eliminated en match 3     → 0.92 (joue libre mais sans pression)
      * 1er match du tournoi      → 0.95 (matchs cadenassés, vu hier)

Sortie : data/wc_match_importance.json
Consommé par : picks_engine.py (ajustement λ Poisson WC)
"""
import json, re, unicodedata
from datetime import datetime, timezone
from pathlib import Path

from fotmob_client import league as fm_league

OUT_FILE     = Path("data/wc_match_importance.json")
WC_LEAGUE_ID = 77


def _slug(name):
    if not name: return ""
    s = unicodedata.normalize("NFD", name)
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    return re.sub(r"[^a-z0-9_]", "_", s.lower()).strip("_")


def _parse_score(s):
    if not s: return None
    m = re.match(r"\s*(\d+)\s*-\s*(\d+)", s)
    if not m: return None
    return int(m.group(1)), int(m.group(2))


def run():
    try:
        data = fm_league(WC_LEAGUE_ID, ttl=2 * 3600)
    except Exception as e:
        print(f"[X] FotMob WC fetch err: {e}")
        return
    matches = ((data or {}).get("overview") or {}).get("leagueOverviewMatches") or []
    # Pour chaque équipe : compte matchs joués + points
    teams = {}
    for m in matches:
        st = m.get("status") or {}
        if not st.get("finished"): continue
        h = m.get("home") or {}; a = m.get("away") or {}
        h_name = h.get("name"); a_name = a.get("name")
        if not (h_name and a_name): continue
        # Skip placeholders
        for nm in (h_name, a_name):
            if not nm or not nm[0].isalpha(): continue
            if "winner" in nm.lower() or "loser" in nm.lower(): continue
        score = _parse_score(st.get("scoreStr"))
        if not score: continue
        gh, ga = score
        h_slug = _slug(h_name); a_slug = _slug(a_name)
        for slug, name in [(h_slug, h_name), (a_slug, a_name)]:
            if slug not in teams:
                teams[slug] = {"name": name, "matches_played": 0, "pts": 0,
                               "gf": 0, "ga": 0, "wins": 0, "draws": 0, "losses": 0}
        if gh > ga:
            teams[h_slug]["pts"] += 3; teams[h_slug]["wins"] += 1
            teams[a_slug]["losses"] += 1
        elif gh < ga:
            teams[a_slug]["pts"] += 3; teams[a_slug]["wins"] += 1
            teams[h_slug]["losses"] += 1
        else:
            teams[h_slug]["pts"] += 1; teams[h_slug]["draws"] += 1
            teams[a_slug]["pts"] += 1; teams[a_slug]["draws"] += 1
        teams[h_slug]["gf"] += gh; teams[h_slug]["ga"] += ga
        teams[h_slug]["matches_played"] += 1
        teams[a_slug]["gf"] += ga; teams[a_slug]["ga"] += gh
        teams[a_slug]["matches_played"] += 1

    # Statut + importance modifier
    for slug, t in teams.items():
        n = t["matches_played"]
        pts = t["pts"]
        if n == 0:
            t["status"] = "not_started"
            t["importance_modifier"] = 0.95  # 1er match toujours plus cadenassé
            t["status_fr"] = "1er match du tournoi"
        elif n == 1:
            t["status"] = "in_play"
            t["importance_modifier"] = 1.00
            t["status_fr"] = f"1 match joué ({pts}pts)"
        elif n == 2:
            # Avant match 3 : déjà qualif ? éliminé ? doit gagner ?
            # Heuristique simple sur les points (varie selon groupe mais souvent juste)
            if pts >= 6:
                t["status"] = "qualif_done"
                t["importance_modifier"] = 0.85  # rotation probable
                t["status_fr"] = f"qualifié·e (6+ pts), rotation probable"
            elif pts >= 3:
                t["status"] = "must_win"
                t["importance_modifier"] = 1.05  # joue à fond
                t["status_fr"] = f"3 pts, doit gagner pour assurer"
            else:
                t["status"] = "eliminated_likely"
                t["importance_modifier"] = 0.92  # joue libre, sans pression
                t["status_fr"] = f"0-1 pts, élimination probable"
        else:
            t["status"] = "knockout"
            t["importance_modifier"] = 1.00
            t["status_fr"] = "phase éliminatoire"

    out = {
        "_meta":    "Importance / statut des équipes WC 2026 pour ajustement λ Poisson.",
        "_updated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "teams":    teams,
    }
    OUT_FILE.parent.mkdir(exist_ok=True)
    OUT_FILE.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[OK] WC importance : {len(teams)} équipes -> {OUT_FILE}")
    for slug, t in sorted(teams.items(), key=lambda x: (-x[1]["matches_played"], -x[1]["pts"])):
        print(f"  [{slug:20s}] {t['matches_played']}m {t['pts']}pts {t['status']:18s} mod={t['importance_modifier']:.2f}")


if __name__ == "__main__":
    run()
