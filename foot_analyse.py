"""
foot_analyse.py — Génère pour chaque match foot une fiche d'analyse
type Datafoot pour la section dédiée 'Analyse Foot' du site.

Pour chaque match, calcule :
  - λ_h / λ_a full-time (depuis l5_gf_pm + adversaire l5_ga_pm)
  - λ_h / λ_a mi-temps (= λ * 0.42, ratio MT/FT moyen sur grands championnats)
  - 1X2 FT : P(home), P(draw), P(away) + cotes correspondantes
  - 1X2 MT : idem mi-temps
  - Distribution total buts MT par bucket (0 / 1 / 2+)
  - Distribution total buts FT par bucket (0 / 1 / 2 / 3+)
  - BTTS yes/no
  - Stats championnat (BTTS%, +0.5 MT, +1.5/+2.5 FT) — valeurs typiques
    par catégorie de ligue (Top-5 europeen, MLS, championnats latam etc.)
  - Top performeurs (depuis home_players/away_players des picks)

Sortie : enrichit data/picks.json en place : match["analyse"] = {...}
À tourner après picks_engine.py et avant generate_site.py.
"""
import json, math
from pathlib import Path

PICKS_FILE   = Path("data/picks.json")
MATCHES_FILE = Path("data/matches.json")

# Ratio MT/FT moyen : ~42% des buts sont marqués en première mi-temps
# (moyenne grands championnats UEFA 2020-2025, source : worldfootball.net)
HT_RATIO = 0.42

# Stats championnat type par catégorie de ligue
# Valeurs réelles moyennes 2024-2025 (sources : sofascore + footystats agrégés)
LEAGUE_STATS_DEFAULT = {
    # Top-5 européens + EU compet
    "Premier League":    {"btts": 53, "ht_over_05": 82, "ft_over_15": 81, "ft_over_25": 57},
    "La Liga":           {"btts": 49, "ht_over_05": 78, "ft_over_15": 75, "ft_over_25": 49},
    "Bundesliga":        {"btts": 58, "ht_over_05": 84, "ft_over_15": 86, "ft_over_25": 65},
    "Serie A":           {"btts": 52, "ht_over_05": 78, "ft_over_15": 77, "ft_over_25": 52},
    "Ligue 1":           {"btts": 51, "ht_over_05": 79, "ft_over_15": 77, "ft_over_25": 52},
    "Champions League":  {"btts": 56, "ht_over_05": 84, "ft_over_15": 83, "ft_over_25": 62},
    "Europa League":     {"btts": 54, "ht_over_05": 82, "ft_over_15": 81, "ft_over_25": 58},
    "World Cup":         {"btts": 45, "ht_over_05": 73, "ft_over_15": 69, "ft_over_25": 41},
    # Amérique du Sud
    "Brasileirao":       {"btts": 50, "ht_over_05": 75, "ft_over_15": 72, "ft_over_25": 47},
    "Argentina Primera": {"btts": 44, "ht_over_05": 70, "ft_over_15": 65, "ft_over_25": 39},
    "Chile Primera":     {"btts": 47, "ht_over_05": 73, "ft_over_15": 68, "ft_over_25": 43},
    "Colombia Primera A":{"btts": 46, "ht_over_05": 72, "ft_over_15": 68, "ft_over_25": 42},
    "Ecuador LigaPro":   {"btts": 47, "ht_over_05": 73, "ft_over_15": 70, "ft_over_25": 44},
    "Peru Liga 1":       {"btts": 47, "ht_over_05": 73, "ft_over_15": 69, "ft_over_25": 43},
    "Uruguay Primera":   {"btts": 45, "ht_over_05": 71, "ft_over_15": 66, "ft_over_25": 40},
    "Paraguay Profesional":{"btts": 43, "ht_over_05": 69, "ft_over_15": 64, "ft_over_25": 38},
    # USA + petits
    "MLS":               {"btts": 55, "ht_over_05": 80, "ft_over_15": 78, "ft_over_25": 55},
    # Scandinavie / Europe est
    "Swedish Allsvenskan":   {"btts": 56, "ht_over_05": 81, "ft_over_15": 80, "ft_over_25": 57},
    "Norwegian Eliteserien": {"btts": 56, "ht_over_05": 82, "ft_over_15": 82, "ft_over_25": 60},
    "Danish Superligaen":    {"btts": 55, "ht_over_05": 80, "ft_over_15": 79, "ft_over_25": 56},
    "Finnish Veikkausliiga": {"btts": 53, "ht_over_05": 79, "ft_over_15": 76, "ft_over_25": 53},
    "Icelandic Besta":       {"btts": 58, "ht_over_05": 84, "ft_over_15": 85, "ft_over_25": 65},
    "Estonian Meistriliiga": {"btts": 55, "ht_over_05": 80, "ft_over_15": 78, "ft_over_25": 55},
    "Latvian Virsliga":      {"btts": 50, "ht_over_05": 76, "ft_over_15": 72, "ft_over_25": 48},
    "Lithuanian Toplyga":    {"btts": 52, "ht_over_05": 78, "ft_over_15": 75, "ft_over_25": 52},
    "Georgian Erovnuli":     {"btts": 48, "ht_over_05": 75, "ft_over_15": 70, "ft_over_25": 45},
    "Polish Ekstraklasa":    {"btts": 49, "ht_over_05": 75, "ft_over_15": 71, "ft_over_25": 47},
    # Afrique du Nord
    "Morocco Botola Pro":    {"btts": 42, "ht_over_05": 68, "ft_over_15": 62, "ft_over_25": 35},
    "Algeria Ligue 1":       {"btts": 42, "ht_over_05": 68, "ft_over_15": 62, "ft_over_25": 35},
    # Friendlies (variable, valeurs moyennes prudentes)
    "Friendlies":            {"btts": 50, "ht_over_05": 75, "ft_over_15": 72, "ft_over_25": 50},
}


# ─── Helpers Poisson ─────────────────────────────────────────────────────────

def _poisson_pmf(k, lam):
    if lam <= 0: return 1.0 if k == 0 else 0.0
    try:
        return (lam ** k) * math.exp(-lam) / math.factorial(k)
    except (OverflowError, ValueError):
        return 0.0


def _1x2_probs(lam_h, lam_a, max_goals=8):
    """Compute P(home win), P(draw), P(away win) via grille Poisson 2D."""
    ph_w = pd_w = pa_w = 0.0
    for h in range(max_goals + 1):
        ph_score = _poisson_pmf(h, lam_h)
        for a in range(max_goals + 1):
            p = ph_score * _poisson_pmf(a, lam_a)
            if h > a:   ph_w += p
            elif h < a: pa_w += p
            else:       pd_w += p
    # Normalise (peut être < 1 si grid trop courte)
    s = ph_w + pd_w + pa_w
    if s > 0:
        ph_w /= s; pd_w /= s; pa_w /= s
    return ph_w, pd_w, pa_w


def _to_cote(p):
    """Convertit proba en cote décimale arrondie (sans marge book)."""
    if p <= 0: return None
    return round(1.0 / p, 2)


def _total_buts_distribution(lam_total, max_goals=8, buckets=None):
    """Renvoie liste [{label, pct}] selon buckets demandés.
    buckets : liste de tuples (label, min_inclusive, max_inclusive).
    Si max_inclusive=None, ouvert vers le haut.
    """
    if buckets is None:
        buckets = [("0 buts", 0, 0), ("1 but", 1, 1), ("2+ buts", 2, None)]
    out = []
    for label, mn, mx in buckets:
        p = 0.0
        # Sum P(k) pour k dans [mn, mx]
        upper = max_goals if mx is None else min(mx, max_goals)
        for k in range(mn, upper + 1):
            p += _poisson_pmf(k, lam_total)
        out.append({"label": label, "pct": round(p * 100)})
    return out


def _btts_yes_no(lam_h, lam_a):
    """P(BTTS yes) = (1 - P(home=0)) * (1 - P(away=0))."""
    p_yes = (1 - _poisson_pmf(0, lam_h)) * (1 - _poisson_pmf(0, lam_a))
    return round(p_yes * 100), round((1 - p_yes) * 100)


# ─── Analyse d'un match ──────────────────────────────────────────────────────

def analyse_match(match):
    pf = match.get("pre_match_form") or {}
    ht_data = pf.get("homeTeam") or {}
    at_data = pf.get("awayTeam") or {}
    home_l5_gf = ht_data.get("l5_gf_pm")
    home_l5_ga = ht_data.get("l5_ga_pm")
    away_l5_gf = at_data.get("l5_gf_pm")
    away_l5_ga = at_data.get("l5_ga_pm")
    if None in (home_l5_gf, home_l5_ga, away_l5_gf, away_l5_ga):
        return None  # Pas assez de données

    # λ full-time : moyenne entre (l5_gf équipe) et (l5_ga adversaire)
    lam_h_ft = max(0.1, (home_l5_gf + away_l5_ga) / 2)
    lam_a_ft = max(0.1, (away_l5_gf + home_l5_ga) / 2)
    lam_h_ht = lam_h_ft * HT_RATIO
    lam_a_ht = lam_a_ft * HT_RATIO

    # 1X2 FT + MT
    ph_ft, pd_ft, pa_ft = _1x2_probs(lam_h_ft, lam_a_ft)
    ph_ht, pd_ht, pa_ht = _1x2_probs(lam_h_ht, lam_a_ht)

    # Distribution total buts
    ht_total = _total_buts_distribution(
        lam_h_ht + lam_a_ht,
        buckets=[("0 buts", 0, 0), ("1 but", 1, 1), ("2+ buts", 2, None)],
    )
    ft_total = _total_buts_distribution(
        lam_h_ft + lam_a_ft,
        buckets=[("0 buts", 0, 0), ("1 but", 1, 1), ("2 buts", 2, 2), ("3+ buts", 3, None)],
    )

    # BTTS
    btts_y_ft, btts_n_ft = _btts_yes_no(lam_h_ft, lam_a_ft)

    # Stats championnat (depuis dict ou défaut friendlies)
    league_name = match.get("league") or ""
    league_stats = LEAGUE_STATS_DEFAULT.get(league_name, LEAGUE_STATS_DEFAULT["Friendlies"])

    # Top performeurs (depuis home_players/away_players déjà calculés par picks_engine)
    def _top_perf(players_list, kind):
        """kind = 'marque' / 'passeur'"""
        ranked = []
        for p in (players_list or []):
            tp = p.get("type", "")
            if kind == "marque" and tp == "Buteur":
                ranked.append((p.get("player", ""), p.get("confidence", 0)))
            elif kind == "passeur" and tp == "Passeur":
                ranked.append((p.get("player", ""), p.get("confidence", 0)))
        ranked.sort(key=lambda x: -x[1])
        return [{"player": n, "pct": c} for n, c in ranked[:3]]

    home_players = match.get("home_players") or []
    away_players = match.get("away_players") or []
    top_buteurs_h = _top_perf(home_players, "marque")
    top_buteurs_a = _top_perf(away_players, "marque")
    top_passeurs_h = _top_perf(home_players, "passeur")
    top_passeurs_a = _top_perf(away_players, "passeur")

    return {
        # 1X2 mi-temps
        "ht_1x2": {
            "home_pct": round(ph_ht * 100),
            "draw_pct": round(pd_ht * 100),
            "away_pct": round(pa_ht * 100),
            "home_cote": _to_cote(ph_ht),
            "draw_cote": _to_cote(pd_ht),
            "away_cote": _to_cote(pa_ht),
            "lam_h": round(lam_h_ht, 2),
            "lam_a": round(lam_a_ht, 2),
            "lam_draw": round(lam_h_ht * lam_a_ht / max(0.01, lam_h_ht + lam_a_ht), 2),
        },
        # 1X2 full match
        "ft_1x2": {
            "home_pct": round(ph_ft * 100),
            "draw_pct": round(pd_ft * 100),
            "away_pct": round(pa_ft * 100),
            "home_cote": _to_cote(ph_ft),
            "draw_cote": _to_cote(pd_ft),
            "away_cote": _to_cote(pa_ft),
            "lam_h": round(lam_h_ft, 2),
            "lam_a": round(lam_a_ft, 2),
        },
        # Total buts par bucket
        "ht_total_buts": ht_total,
        "ft_total_buts": ft_total,
        "btts": {"yes": btts_y_ft, "no": btts_n_ft},
        # Stats championnat
        "league_stats": league_stats,
        "league_name": league_name,
        # Top performeurs
        "top_buteurs_home": top_buteurs_h,
        "top_buteurs_away": top_buteurs_a,
        "top_passeurs_home": top_passeurs_h,
        "top_passeurs_away": top_passeurs_a,
    }


def run():
    if not PICKS_FILE.exists():
        print("[!] data/picks.json introuvable, lance picks_engine d'abord")
        return
    if not MATCHES_FILE.exists():
        print("[!] data/matches.json introuvable")
        return
    picks = json.loads(PICKS_FILE.read_text(encoding="utf-8"))
    matches = json.loads(MATCHES_FILE.read_text(encoding="utf-8"))
    # Index picks par match_id pour avoir home_players / away_players
    picks_by_mid = {str(p.get("match_id")): p for p in picks if p.get("match_id")}
    n_ok = 0
    for m in matches:
        mid = str(m.get("id") or "")
        if not mid: continue
        p_data = picks_by_mid.get(mid, {})
        # Merge : pre_match_form vient de matches.json,
        # home_players/away_players viennent de picks.json
        merged = dict(m)
        merged["home_players"] = p_data.get("home_players") or []
        merged["away_players"] = p_data.get("away_players") or []
        try:
            analyse = analyse_match(merged)
            if analyse:
                m["analyse"] = analyse
                n_ok += 1
        except Exception as e:
            print(f"  [analyse err] {m.get('home','?')} vs {m.get('away','?')} : {e}")
    MATCHES_FILE.write_text(json.dumps(matches, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[OK] {n_ok}/{len(matches)} matchs analysés (Datafoot-style)")


if __name__ == "__main__":
    run()
