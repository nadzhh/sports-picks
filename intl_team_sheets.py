"""
intl_team_sheets.py — Construit/met à jour une fiche d'équipe nationale.

Pour chaque équipe nationale qu'on a vue jouer (matches.json) ou qui est
dans fifa_rankings.json, on récupère ses 10 derniers matchs internationaux
via FotMob (qualifs + amicaux + tournoi en cours), on calcule :

  - gf_pm / ga_pm sur L5 et L10
  - clean_sheets, failed_to_score, btts
  - off_score / def_score : performance vs attendu Elo (ce qu'on devrait
    marquer/encaisser contre cet adversaire, déduit du gap FIFA points).
    Échelle [-1, +1]. Positif = sur-performance.
  - off_rating / def_rating : "bon" si score > +0.3, "nul" si < -0.3, sinon "moyen"
  - form : string "WDLDW" (5 derniers résultats)
  - notes_fr : observations textuelles utiles à l'algo + à l'humain

Sortie : data/intl_team_sheets.json
Consommé par : picks_engine.py (ajustement λ Poisson pour matchs WC)

Pipeline : tourne 1x/jour (cron) après les matchs résolus.
"""
import json, re, unicodedata
from datetime import datetime, timezone
from pathlib import Path

from fotmob_client import team as fm_team, league as fm_league

OUT_FILE   = Path("data/intl_team_sheets.json")
FIFA_FILE  = Path("data/fifa_rankings.json")
WC_HIST    = Path("data/wc_historical_records.json")
MATCHES    = Path("data/matches.json")

# League ID FotMob de la Coupe du Monde 2026
WC_LEAGUE_ID = 77

# Fenêtres
WINDOW_L5  = 5
WINDOW_L10 = 10

# Sensibilité du score off/def vs attendu Elo
# Si l'écart observé/attendu = 0.5 but/match → score ≈ 0.5
SCORE_SCALE = 1.0

# Compétitions ignorées (pas représentatives du niveau "équipe nationale A")
# - U17/U19/U21/U23 + W (women) traités séparément
EXCLUDE_COMP_KEYWORDS = ("U17", "U19", "U21", "U23", "Women", "W ", "Olympic", "Nations League Group")
# NB : on garde "Nations League" / "Friendlies" / "World Cup" / "Qualification" / "Euro" / "Copa America"


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _slug(name):
    """Normalise un nom d'équipe en slug ASCII."""
    if not name: return ""
    s = unicodedata.normalize("NFD", name)
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    return re.sub(r"[^a-z0-9_]", "_", s.lower()).strip("_")


def _load_json(path, default):
    if not path.exists(): return default
    try: return json.loads(path.read_text(encoding="utf-8"))
    except Exception: return default


def _save_json(path, data):
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _is_intl_competition(comp_name):
    """Garde uniquement les comp internationales seniors masc."""
    if not comp_name: return False
    for kw in EXCLUDE_COMP_KEYWORDS:
        if kw in comp_name: return False
    return True


def _parse_score(score_str):
    if not score_str: return None
    m = re.match(r"\s*(\d+)\s*-\s*(\d+)", score_str)
    if not m: return None
    return int(m.group(1)), int(m.group(2))


def _wlt(gf, ga):
    if gf > ga: return "W"
    if gf < ga: return "L"
    return "D"


# ─── Build team_ids mapping ──────────────────────────────────────────────────

def _bootstrap_team_ids():
    """Récupère le mapping slug -> fotmob_team_id pour TOUTES les équipes
    nationales pertinentes : participantes WC 2026 + équipes déjà vues dans
    matches.json (qualifs/amicaux récents) + fiches déjà persistées.
    """
    ids = {}
    # 1) Fiches existantes (persiste les IDs déjà connus)
    existing = _load_json(OUT_FILE, {})
    for slug, sheet in (existing.get("teams") or {}).items():
        if sheet.get("fotmob_team_id"):
            ids[slug] = sheet["fotmob_team_id"]

    # 2) Toutes les équipes ayant joué (ou devant jouer) un match WC 2026,
    #    fetched depuis le league endpoint FotMob. Couvre les 32 participantes
    #    dès qu'elles ont au moins un fixture programmé.
    try:
        wc_data = fm_league(WC_LEAGUE_ID, ttl=12 * 3600)
        wc_matches = ((wc_data or {}).get("overview") or {}).get("leagueOverviewMatches") or []
        for m in wc_matches:
            for side in ("home", "away"):
                t = m.get(side) or {}
                name = t.get("name"); tid = t.get("id")
                if not (name and tid): continue
                # Skip placeholders des phases finales : "1A", "2B", "3DEIJL",
                # "1F_2C", "Winner of QF 1", "Loser of SF 1", etc.
                # Heuristique : les vraies équipes commencent par une LETTRE,
                # les placeholders commencent par un chiffre ou contiennent
                # "winner"/"loser".
                low = name.lower()
                if not low or not low[0].isalpha(): continue
                if "winner" in low or "loser" in low: continue
                ids.setdefault(_slug(name), tid)
    except Exception as e:
        print(f"  [WC league bootstrap err] {e}")

    # 3) Depuis matches.json : matchs d'amicaux / qualifs récents (utile pour
    #    avoir aussi les équipes non-WC mais qui jouent en sélection)
    ms = _load_json(MATCHES, [])
    intl_kw = ("World Cup", "Nations League", "UEFA Euro", "Copa America",
               "Africa Cup", "Asian Cup", "Friendlies", "Qualification")
    for m in ms:
        league = m.get("league") or ""
        if not any(kw in league for kw in intl_kw):
            continue
        for side in ("home", "away"):
            name = m.get(side); tid = m.get(f"{side}_id")
            if name and tid:
                ids.setdefault(_slug(name), tid)

    return ids


# ─── Fetch + analyse fixtures ────────────────────────────────────────────────

def _fetch_recent_intl_fixtures(team_id, n=12):
    """Renvoie les N derniers matchs internationaux terminés (ordre du + récent au + ancien)."""
    if not team_id: return []
    try:
        data = fm_team(team_id, ttl=12 * 3600)
    except Exception as e:
        print(f"  [fm err] team {team_id}: {e}")
        return []
    if not data: return []
    fx_all = (((data.get("fixtures") or {}).get("allFixtures") or {}).get("fixtures") or [])
    finished = []
    for f in fx_all:
        st = f.get("status") or {}
        if not st.get("finished"): continue
        comp = (f.get("tournament") or {}).get("name") or ""
        if not _is_intl_competition(comp): continue
        score = _parse_score(st.get("scoreStr"))
        if not score: continue
        h = f.get("home") or {}; a = f.get("away") or {}
        is_home = (str(h.get("id") or "") == str(team_id))
        gh, ga = score
        gf, gc = (gh, ga) if is_home else (ga, gh)
        opp_name = (a if is_home else h).get("name") or ""
        finished.append({
            "date":    (st.get("utcTime") or "")[:10],
            "opp":     opp_name,
            "opp_slug": _slug(opp_name),
            "opp_id":  (a if is_home else h).get("id"),
            "venue":   "home" if is_home else "away",
            "gf":      gf, "ga": gc,
            "result":  _wlt(gf, gc),
            "comp":    comp,
        })
    finished.sort(key=lambda x: x["date"], reverse=True)
    return finished[:n]


def _expected_goals_for(team_pts, opp_pts):
    """λ attendu pour 'team' vs 'opp' à partir du gap FIFA points.
    Baseline 1.30 but/match en équipe nationale, ±0.4 but pour 100 pts d'écart.
    """
    base = 1.30
    gap = (team_pts - opp_pts) / 100.0
    return max(0.30, base + 0.40 * gap)


def _wc_pedigree(slug, wc_hist_data):
    """Calcule le pédigrée WC d'une équipe depuis ses résultats 2014/2018/2022.
    Renvoie {history: dict, score: float, label: str, summary_fr: str}."""
    records = (wc_hist_data or {}).get("records") or {}
    rec = records.get(slug)
    if not rec:
        return {
            "history": {}, "score": 0.0, "label": "inconnu", "titles_count": 0,
            "summary_fr": "Pas d'historique récent en Coupe du Monde",
        }
    scoring = wc_hist_data.get("_round_score", {})
    thresholds = wc_hist_data.get("_label_thresholds", {})
    rounds = ["2014", "2018", "2022"]
    score = sum(scoring.get(rec.get(y, "none"), 0) for y in rounds) / len(rounds)
    titles = rec.get("titles") or []
    # Bonus pour titres (max +2)
    titles_recent = [t for t in titles if t >= 1990]
    bonus = min(2.0, len(titles_recent) * 0.5)
    score = round(score + bonus, 2)

    # Label
    if score >= thresholds.get("elite", 6.0):       label = "élite mondiale"
    elif score >= thresholds.get("regular", 3.0):    label = "régulière phase finale"
    elif score >= thresholds.get("occasional", 1.5): label = "habituée du tournoi"
    elif score > 0:                                   label = "présence occasionnelle"
    else:                                             label = "novice / absent récent"

    # Summary FR
    parts = []
    label_fr = {
        "champion":"vainqueur", "final":"finaliste", "semi":"demi-finaliste",
        "quarter":"quart de finale", "round_16":"1/8", "group":"phase de poules",
        "none":"non qualifiée",
    }
    for y in ("2022", "2018", "2014"):
        r = rec.get(y, "none")
        if r != "none":
            parts.append(f"{label_fr.get(r, r)} en {y}")
    if titles_recent:
        parts.append(f"{len(titles_recent)} titre{'s' if len(titles_recent)>1 else ''} ({', '.join(str(t) for t in titles_recent)})")
    summary = " · ".join(parts) if parts else "Pas de phase finale récente"

    return {
        "history":      {y: rec.get(y, "none") for y in rounds},
        "score":        score,
        "label":        label,
        "titles_count": len(titles),
        "titles_recent": titles_recent,
        "summary_fr":   summary,
    }


def _compute_sheet(slug, team_id, fixtures, fifa_rankings, wc_hist):
    """Calcule la fiche pour 1 équipe à partir de ses fixtures + son rank FIFA."""
    own = fifa_rankings.get(slug, {}) or {}
    own_pts = own.get("points") or 1200
    own_rank = own.get("rank")

    pedigree = _wc_pedigree(slug, wc_hist)

    if not fixtures:
        return {
            "fotmob_team_id": team_id,
            "fifa_rank": own_rank,
            "fifa_points": own_pts,
            "matches_n": 0,
            "off_score": 0.0, "def_score": 0.0,
            "off_rating": "moyen", "def_rating": "moyen",
            "form": "",
            "wc_pedigree": pedigree,
            "notes_fr": [],
            "last_updated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }

    l10 = fixtures[:WINDOW_L10]
    l5  = fixtures[:WINDOW_L5]

    def _stats(arr):
        n = len(arr) or 1
        return {
            "n":              len(arr),
            "gf_pm":          round(sum(f["gf"] for f in arr) / n, 2),
            "ga_pm":          round(sum(f["ga"] for f in arr) / n, 2),
            "clean_sheets":   sum(1 for f in arr if f["ga"] == 0),
            "failed_to_score":sum(1 for f in arr if f["gf"] == 0),
            "btts":           sum(1 for f in arr if f["gf"] > 0 and f["ga"] > 0),
            "wins":           sum(1 for f in arr if f["result"] == "W"),
            "draws":          sum(1 for f in arr if f["result"] == "D"),
            "losses":         sum(1 for f in arr if f["result"] == "L"),
        }

    s10 = _stats(l10)
    s5  = _stats(l5)

    # Off / Def score : observé - attendu sur L10, avec attendu déduit du gap Elo
    # vs chaque adversaire individuellement. Shrinkage par sqrt(N/10) pour éviter
    # les conclusions hâtives quand peu de matchs.
    expected_gf_total = 0.0; expected_ga_total = 0.0
    for f in l10:
        opp_pts = (fifa_rankings.get(f["opp_slug"]) or {}).get("points")
        if not opp_pts:
            # Pas de FIFA points pour l'adversaire : on suppose 1100 (faible)
            opp_pts = 1100
        expected_gf_total += _expected_goals_for(own_pts, opp_pts)
        expected_ga_total += _expected_goals_for(opp_pts, own_pts)
    n = max(1, len(l10))
    exp_gf_pm = expected_gf_total / n
    exp_ga_pm = expected_ga_total / n
    raw_off = (s10["gf_pm"] - exp_gf_pm) / max(0.5, exp_gf_pm)  # ratio
    raw_def = (exp_ga_pm - s10["ga_pm"]) / max(0.5, exp_ga_pm)
    # Shrinkage : on multiplie par sqrt(N/10) → effet plein à 10 matchs, à 1 seul N=2 effet = 45%
    shrink = (max(1, len(l10)) / 10.0) ** 0.5
    off_score = max(-1.0, min(1.0, raw_off * SCORE_SCALE * shrink))
    def_score = max(-1.0, min(1.0, raw_def * SCORE_SCALE * shrink))

    def _rating(score):
        if score > 0.3:  return "bon"
        if score < -0.3: return "nul"
        return "moyen"

    form = "".join(f["result"] for f in l5)

    # Notes auto : signaux saillants sur la forme
    notes = []
    if s5["wins"] >= 4:
        notes.append(f"Forme exceptionnelle : {s5['wins']} victoires sur L5")
    elif s5["losses"] >= 3:
        notes.append(f"Forme inquiétante : {s5['losses']} défaites sur L5")
    if s10["clean_sheets"] >= 5:
        notes.append(f"Défense solide : {s10['clean_sheets']} clean sheets sur L10")
    if s10["failed_to_score"] >= 4:
        notes.append(f"Attaque en panne : {s10['failed_to_score']} blanchies sur L10")
    if s10["gf_pm"] >= 2.5:
        notes.append(f"Attaque prolifique : {s10['gf_pm']} buts/match sur L10")
    if s10["ga_pm"] >= 1.8:
        notes.append(f"Défense fragile : {s10['ga_pm']} buts encaissés/match sur L10")
    # Sous-perf / sur-perf brute
    diff_off = s10["gf_pm"] - exp_gf_pm
    if abs(diff_off) >= 0.6:
        sign = "+" if diff_off > 0 else ""
        notes.append(f"Attaque {sign}{round(diff_off,1)} but/match vs attendu Elo")
    diff_def = exp_ga_pm - s10["ga_pm"]
    if abs(diff_def) >= 0.6:
        sign = "+" if diff_def > 0 else ""
        notes.append(f"Défense {sign}{round(diff_def,1)} but/match vs attendu Elo")

    # Note pédigrée WC
    if pedigree["score"] >= 6.0:
        notes.append(f"Pédigrée WC élite : {pedigree['summary_fr']}")
    elif pedigree["score"] >= 3.0:
        notes.append(f"Habituée du tournoi : {pedigree['summary_fr']}")
    elif pedigree["score"] == 0:
        notes.append("Aucune phase finale WC sur les 3 dernières éditions")

    return {
        "fotmob_team_id": team_id,
        "fifa_rank":      own_rank,
        "fifa_points":    own_pts,
        "matches_n":      len(fixtures),
        "off_score":      round(off_score, 2),
        "def_score":      round(def_score, 2),
        "off_rating":     _rating(off_score),
        "def_rating":     _rating(def_score),
        "form":           form,
        "stats_l5":       s5,
        "stats_l10":      {**s10, "gf_expected_pm": round(exp_gf_pm, 2),
                                   "ga_expected_pm": round(exp_ga_pm, 2)},
        "recent":         l10,
        "wc_pedigree":    pedigree,
        "notes_fr":       notes,
        "last_updated":   datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


# ─── Main ────────────────────────────────────────────────────────────────────

def run():
    fifa = _load_json(FIFA_FILE, {})
    rankings = fifa.get("rankings") or {}
    wc_hist = _load_json(WC_HIST, {})
    team_ids = _bootstrap_team_ids()
    print(f"=== Fiches équipes nationales : {len(team_ids)} équipes connues ===")

    existing = _load_json(OUT_FILE, {})
    teams_out = existing.get("teams") or {}

    n_updated = 0
    for slug, tid in team_ids.items():
        fixtures = _fetch_recent_intl_fixtures(tid, n=WINDOW_L10 + 2)
        sheet = _compute_sheet(slug, tid, fixtures, rankings, wc_hist)
        teams_out[slug] = sheet
        n_updated += 1
        rating_str = f"off={sheet['off_rating']}({sheet['off_score']:+.2f}) def={sheet['def_rating']}({sheet['def_score']:+.2f})"
        ped = (sheet.get('wc_pedigree') or {}).get('label', '?')[:18]
        print(f"  [{slug:22s}] N={sheet['matches_n']:2d} {rating_str} form={sheet['form']} WC={ped}")

    out = {
        "_meta":   "Fiches équipes nationales : aisance off/def vs attendu Elo, forme L5/L10. Consommé par picks_engine pour ajuster λ Poisson WC.",
        "_updated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "teams":   teams_out,
    }
    _save_json(OUT_FILE, out)
    print(f"\n[OK] {n_updated} fiches mises à jour -> {OUT_FILE}")


if __name__ == "__main__":
    run()
