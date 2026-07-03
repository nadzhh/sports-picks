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
import json, math, re
from pathlib import Path

PICKS_FILE   = Path("data/picks.json")
MATCHES_FILE = Path("data/matches.json")
TEAM_STATS_FILE = Path("data/foot_team_season_stats.json")

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
    # Asie (Chinese Super League 2024-2025 : ~2.85 buts/match, BTTS ~48%)
    "Chinese Super League":  {"btts": 48, "ht_over_05": 74, "ft_over_15": 70, "ft_over_25": 46},
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


# ─── Stats saison par équipe (depuis FotMob fixtures) ────────────────────────

def _parse_score(s):
    if not s: return None, None
    m = re.match(r"\s*(\d+)\s*-\s*(\d+)", s)
    if not m: return None, None
    return int(m.group(1)), int(m.group(2))


def fetch_team_season_stats(team_id):
    """Récupère les fixtures saison d'une équipe et compute stats.
    Renvoie dict avec stats globales + splits home/away."""
    try:
        from fotmob_client import team as fm_team
    except ImportError:
        return None
    if not team_id: return None
    try:
        data = fm_team(team_id, ttl=12 * 3600)
    except Exception:
        return None
    if not data: return None
    fixtures = (((data.get("fixtures") or {}).get("allFixtures") or {}).get("fixtures") or [])
    # Stats agrégées
    overall = {"n": 0, "wins": 0, "draws": 0, "losses": 0,
               "btts": 0, "no_btts": 0,
               "over_15": 0, "over_25": 0, "over_35": 0,
               "under_15": 0, "under_25": 0, "under_35": 0,
               "gf_total": 0, "ga_total": 0}
    home_stats = {**overall}
    away_stats = {**overall}
    overall = {k: 0 for k in overall}

    for f in fixtures:
        st = f.get("status") or {}
        if not st.get("finished"): continue
        score = _parse_score(st.get("scoreStr"))
        if not score: continue
        gh, ga = score
        h_id = str((f.get("home") or {}).get("id") or "")
        a_id = str((f.get("away") or {}).get("id") or "")
        is_home = str(team_id) == h_id
        is_away = str(team_id) == a_id
        if not (is_home or is_away): continue

        gf = gh if is_home else ga
        gc = ga if is_home else gh
        total = gh + ga

        # Pour cette équipe : gf = buts marqués, gc = buts encaissés
        target = home_stats if is_home else away_stats
        for bucket in (overall, target):
            bucket["n"] += 1
            bucket["gf_total"] += gf
            bucket["ga_total"] += gc
            if gf > gc: bucket["wins"] += 1
            elif gf < gc: bucket["losses"] += 1
            else: bucket["draws"] += 1
            if gf > 0 and gc > 0: bucket["btts"] += 1
            else: bucket["no_btts"] += 1
            if total >= 2: bucket["over_15"] += 1
            else: bucket["under_15"] += 1
            if total >= 3: bucket["over_25"] += 1
            else: bucket["under_25"] += 1
            if total >= 4: bucket["over_35"] += 1
            else: bucket["under_35"] += 1

    def _pct_dict(bucket):
        n = max(1, bucket["n"])
        return {
            "n":         bucket["n"],
            "wins_pct":  round(bucket["wins"] / n * 100),
            "draws_pct": round(bucket["draws"] / n * 100),
            "losses_pct":round(bucket["losses"] / n * 100),
            "btts_pct":  round(bucket["btts"] / n * 100),
            "over_15":   round(bucket["over_15"] / n * 100),
            "over_25":   round(bucket["over_25"] / n * 100),
            "over_35":   round(bucket["over_35"] / n * 100),
            "under_15":  round(bucket["under_15"] / n * 100),
            "under_25":  round(bucket["under_25"] / n * 100),
            "under_35":  round(bucket["under_35"] / n * 100),
            "gf_pm":     round(bucket["gf_total"] / n, 2),
            "ga_pm":     round(bucket["ga_total"] / n, 2),
        }

    return {
        "overall": _pct_dict(overall),
        "home":    _pct_dict(home_stats),
        "away":    _pct_dict(away_stats),
    }


def fetch_all_team_stats(matches):
    """Pour chaque équipe unique des matchs, fetch stats saison.
    Cache dans data/foot_team_season_stats.json."""
    cache = {}
    if TEAM_STATS_FILE.exists():
        try: cache = json.loads(TEAM_STATS_FILE.read_text(encoding="utf-8"))
        except: cache = {}

    team_ids = set()
    for m in matches:
        for k in ("home_id", "away_id"):
            tid = m.get(k)
            if tid: team_ids.add(str(tid))

    print(f"  [team stats] {len(team_ids)} équipes à analyser")
    for tid in team_ids:
        if tid in cache: continue
        stats = fetch_team_season_stats(tid)
        if stats: cache[tid] = stats

    TEAM_STATS_FILE.parent.mkdir(exist_ok=True)
    TEAM_STATS_FILE.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  [team stats] {len(cache)} équipes en cache")
    return cache


# ─── Value Bets : moyenne 3 sources × cote bookmaker ────────────────────────

def _get_book_cote(markets, market_name, choice_name=None, side=None):
    """Cherche la cote book pour un marché. Renvoie (cote, book) ou (None, None).
    Match par marketName + (choice_name OU side)."""
    for mk in (markets or []):
        if mk.get("marketName") != market_name:
            continue
        for c in mk.get("choices", []):
            if choice_name and c.get("name") == choice_name:
                return c.get("cote"), c.get("book")
            if side and c.get("side") == side:
                return c.get("cote"), c.get("book")
    return None, None


def _compute_value_bets(match, league_stats, ph_ft, pd_ft, pa_ft,
                         lam_h_ft, lam_a_ft, btts_y_pct,
                         home_l5_gf, home_l5_ga, away_l5_gf, away_l5_ga):
    """Calcule pour chaque marché disponible :
       value = avg(P_ligue, P_modele, P_form_L5) × cote_book × 100

       P_ligue       : stats championnat hardcodées (BTTS, +0.5 MT, +1.5/2.5 FT)
                       Pour 1X2 : on prend P_avantage_home générique 45% / 27% nul / 28% away
       P_modele      : notre Poisson (ph_ft, pd_ft, pa_ft, btts_y_pct, ft_total_buts)
       P_form_L5     : basé sur la forme L5 (l5_gf_pm/l5_ga_pm des 2 équipes)

       Renvoie liste [{marche, cote, p_ligue, p_modele, p_form, p_avg, value, source_book}]
       triée par value DESC.
    """
    markets = (match.get("match_odds") or {}).get("markets") or []
    if not markets: return []

    home_name = match.get("home", "?")
    away_name = match.get("away", "?")
    bets = []

    # ── Compute P_form basé sur L5 ──
    # P_btts_form : 1 - exp(-l5_gf_home) * exp(-l5_ga_away) approximation
    # Pour rester simple : moyennes des l5
    expected_h = (home_l5_gf + away_l5_ga) / 2
    expected_a = (away_l5_gf + home_l5_ga) / 2
    # P(home marque) basé sur form
    p_h_marque = 1 - math.exp(-expected_h)
    p_a_marque = 1 - math.exp(-expected_a)
    p_btts_form = p_h_marque * p_a_marque
    p_over_25_form = 1 - sum(_poisson_pmf(k, expected_h + expected_a) for k in (0, 1, 2))
    p_over_15_form = 1 - sum(_poisson_pmf(k, expected_h + expected_a) for k in (0, 1))
    p_under_25_form = 1 - p_over_25_form
    p_under_15_form = 1 - p_over_15_form
    # 1X2 form : avantage home + classement L5
    # Simple : si expected_h > expected_a, home plus probable
    p_h_form = max(0.15, min(0.75, 0.40 + (expected_h - expected_a) * 0.15))
    p_a_form = max(0.15, min(0.75, 0.30 + (expected_a - expected_h) * 0.15))
    p_d_form = max(0.10, 1 - p_h_form - p_a_form)

    def _value_pct(p_avg, cote):
        if not cote or not p_avg: return None
        return round(p_avg * float(cote) * 100, 1)

    # ── 1X2 ──
    home_cote, home_book = _get_book_cote(markets, "Full time", side="home")
    if home_cote:
        p_lig = 0.45  # avantage home moyen tous championnats
        p_mod = ph_ft
        p_form = p_h_form
        p_avg = (p_lig + p_mod + p_form) / 3
        bets.append({
            "marche": f"{home_name} gagne (1X2)",
            "cote": float(home_cote),
            "book": home_book,
            "p_ligue": round(p_lig * 100),
            "p_modele": round(p_mod * 100),
            "p_form": round(p_form * 100),
            "p_avg": round(p_avg * 100),
            "value": _value_pct(p_avg, home_cote),
        })
    draw_cote, draw_book = _get_book_cote(markets, "Full time", choice_name="Draw", side="draw")
    if draw_cote:
        p_lig = 0.27
        p_mod = pd_ft
        p_form = p_d_form
        p_avg = (p_lig + p_mod + p_form) / 3
        bets.append({
            "marche": "Match nul (1X2)",
            "cote": float(draw_cote), "book": draw_book,
            "p_ligue": round(p_lig * 100), "p_modele": round(p_mod * 100),
            "p_form": round(p_form * 100), "p_avg": round(p_avg * 100),
            "value": _value_pct(p_avg, draw_cote),
        })
    away_cote, away_book = _get_book_cote(markets, "Full time", side="away")
    if away_cote:
        p_lig = 0.28
        p_mod = pa_ft
        p_form = p_a_form
        p_avg = (p_lig + p_mod + p_form) / 3
        bets.append({
            "marche": f"{away_name} gagne (1X2)",
            "cote": float(away_cote), "book": away_book,
            "p_ligue": round(p_lig * 100), "p_modele": round(p_mod * 100),
            "p_form": round(p_form * 100), "p_avg": round(p_avg * 100),
            "value": _value_pct(p_avg, away_cote),
        })

    # ── BTTS ──
    btts_yes_cote, btts_book = _get_book_cote(markets, "Both teams to score", choice_name="Yes")
    if btts_yes_cote:
        p_lig = league_stats.get("btts", 50) / 100.0
        p_mod = btts_y_pct / 100.0
        p_form = p_btts_form
        p_avg = (p_lig + p_mod + p_form) / 3
        bets.append({
            "marche": "Les 2 équipes marquent (BTTS Oui)",
            "cote": float(btts_yes_cote), "book": btts_book,
            "p_ligue": round(p_lig * 100), "p_modele": round(p_mod * 100),
            "p_form": round(p_form * 100), "p_avg": round(p_avg * 100),
            "value": _value_pct(p_avg, btts_yes_cote),
        })
    btts_no_cote, btts_no_book = _get_book_cote(markets, "Both teams to score", choice_name="No")
    if btts_no_cote:
        p_lig = 1 - league_stats.get("btts", 50) / 100.0
        p_mod = 1 - btts_y_pct / 100.0
        p_form = 1 - p_btts_form
        p_avg = (p_lig + p_mod + p_form) / 3
        bets.append({
            "marche": "BTTS Non",
            "cote": float(btts_no_cote), "book": btts_no_book,
            "p_ligue": round(p_lig * 100), "p_modele": round(p_mod * 100),
            "p_form": round(p_form * 100), "p_avg": round(p_avg * 100),
            "value": _value_pct(p_avg, btts_no_cote),
        })

    # ── Over/Under 2.5 ──
    over25_cote, ou_book = _get_book_cote(markets, "Goals Over/Under (2.5)", choice_name="Over 2.5")
    if over25_cote:
        p_lig = league_stats.get("ft_over_25", 50) / 100.0
        # P_modele : depuis ft_total_buts (somme buckets >=3)
        # On utilise le calcul direct
        p_mod = 1 - sum(_poisson_pmf(k, lam_h_ft + lam_a_ft) for k in (0, 1, 2))
        p_form = p_over_25_form
        p_avg = (p_lig + p_mod + p_form) / 3
        bets.append({
            "marche": "Plus de 2.5 buts",
            "cote": float(over25_cote), "book": ou_book,
            "p_ligue": round(p_lig * 100), "p_modele": round(p_mod * 100),
            "p_form": round(p_form * 100), "p_avg": round(p_avg * 100),
            "value": _value_pct(p_avg, over25_cote),
        })
    under25_cote, ou_no_book = _get_book_cote(markets, "Goals Over/Under (2.5)", choice_name="Under 2.5")
    if under25_cote:
        p_lig = 1 - league_stats.get("ft_over_25", 50) / 100.0
        p_mod = sum(_poisson_pmf(k, lam_h_ft + lam_a_ft) for k in (0, 1, 2))
        p_form = p_under_25_form
        p_avg = (p_lig + p_mod + p_form) / 3
        bets.append({
            "marche": "Moins de 2.5 buts",
            "cote": float(under25_cote), "book": ou_no_book,
            "p_ligue": round(p_lig * 100), "p_modele": round(p_mod * 100),
            "p_form": round(p_form * 100), "p_avg": round(p_avg * 100),
            "value": _value_pct(p_avg, under25_cote),
        })

    # Tri par value DESC (ne garde QUE les bets avec value calculée)
    bets = [b for b in bets if b.get("value") is not None]
    bets.sort(key=lambda x: -x["value"])
    return bets


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

    # ── Value Bets : moyenne 3 sources × cote bookmaker ──
    # Sources : P_ligue (stats championnat) + P_modele (Poisson) + P_form (L5)
    # value = avg(3 sources) × cote_book × 100
    # > 105 = value positif, < 95 = à éviter
    value_bets = _compute_value_bets(match, league_stats,
                                      ph_ft, pd_ft, pa_ft,
                                      lam_h_ft, lam_a_ft,
                                      btts_y_ft,
                                      home_l5_gf, home_l5_ga,
                                      away_l5_gf, away_l5_ga)

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
        # Value bets (triés par value DESC)
        "value_bets": value_bets,
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

    # Fetch stats saison de toutes les équipes (avec cache disque)
    team_stats_cache = fetch_all_team_stats(matches)

    n_ok = 0
    for m in matches:
        mid = str(m.get("id") or "")
        if not mid: continue
        p_data = picks_by_mid.get(mid, {})
        merged = dict(m)
        merged["home_players"] = p_data.get("home_players") or []
        merged["away_players"] = p_data.get("away_players") or []
        # Injecte stats saison home + away
        merged["home_season_stats"] = team_stats_cache.get(str(m.get("home_id")) or "")
        merged["away_season_stats"] = team_stats_cache.get(str(m.get("away_id")) or "")
        try:
            analyse = analyse_match(merged)
            if analyse:
                m["analyse"] = analyse
                m["home_season_stats"] = merged["home_season_stats"]
                m["away_season_stats"] = merged["away_season_stats"]
                n_ok += 1
        except Exception as e:
            print(f"  [analyse err] {m.get('home','?')} vs {m.get('away','?')} : {e}")
    MATCHES_FILE.write_text(json.dumps(matches, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[OK] {n_ok}/{len(matches)} matchs analysés (Datafoot-style)")


if __name__ == "__main__":
    run()
