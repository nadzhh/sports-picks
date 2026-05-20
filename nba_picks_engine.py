"""
nba_picks_engine.py — Algorithme picks joueurs NBA (v2 — pace + vegas + B2B)

Modele par joueur :
1. Filtre les "garbage games" (MIN < 15) du sample L20
2. Moyenne ponderee L5 (50%) + L10 (30%) + Saison (20%) sur stats brutes
3. Multiplicateurs contextuels :
   - pace_mult       = (team_pace + opp_pace) / (2 * league_avg_pace)
   - vegas_mult      = team_total / season_PPG   (sur PTS et combos)
   - rest_mult       = 0.96 si B2B (-4% sur volume), 1.0 sinon
4. Sigma : stdev L20 raw, floor a 15% du mean (anti-overconfidence)
5. Filtre direction : REB/AST/FG3M = over only
6. Si vraie ligne bookmaker dispo : edge >= 3% post-vig pour validation
7. Mix high-conf + mid-conf pour varier les cotes
"""
import json, math, os, sys
from datetime import datetime, timedelta
from statistics import mean, stdev

os.makedirs("data", exist_ok=True)

LEAGUE_AVG_PACE_DEFAULT = 99.0   # fallback si stat manquante
LEAGUE_AVG_PPG_DEFAULT  = 113.0  # fallback team PPG NBA moderne


# Lignes par defaut (fallback si pas d'odds API).
# PRA seul est par 5 (convention bookmakers).
BOOKMAKER_LINES = {
    "PTS":  [8.5, 10.5, 12.5, 14.5, 16.5, 18.5, 20.5, 22.5, 24.5, 26.5, 28.5, 30.5, 32.5, 34.5],
    "REB":  [3.5, 4.5, 5.5, 6.5, 7.5, 8.5, 9.5, 10.5, 11.5, 12.5],
    "AST":  [2.5, 3.5, 4.5, 5.5, 6.5, 7.5, 8.5, 9.5, 10.5, 11.5],
    "FG3M": [0.5, 1.5, 2.5, 3.5, 4.5, 5.5],
    "PRA":  [14.5, 19.5, 24.5, 29.5, 34.5, 39.5, 44.5, 49.5, 54.5],
    "PR":   [12.5, 14.5, 16.5, 18.5, 20.5, 22.5, 25.5, 28.5, 32.5, 35.5, 38.5],
    "PA":   [12.5, 14.5, 16.5, 18.5, 20.5, 22.5, 25.5, 28.5, 32.5, 35.5, 38.5],
}

# Directions autorisees par prop : REB/AST/FG3M sont over-only chez les bookmakers
ALLOWED_DIRECTIONS = {
    "PTS":  ("over", "under"),
    "REB":  ("over",),
    "AST":  ("over",),
    "FG3M": ("over",),
    "PRA":  ("over", "under"),
    "PR":   ("over", "under"),
    "PA":   ("over", "under"),
}

# Sweet spot heuristique (fallback si pas de book reel)
SWEET_LOW, SWEET_HIGH = 60, 78
MIN_CONF = 60
MAX_PICKS_PER_PLAYER = 2

# Filtre qualite : nombre de picks par equipe est VARIABLE selon la qualite des
# signaux alignes. On garde tout ce qui passe le quality_score min, plus un
# hard cap pour eviter le spam (10/equipe max).
QUALITY_SCORE_MIN_WITH_ODDS    = 50   # avec odds reelles (edge calcule)
QUALITY_SCORE_MIN_HEURISTIC    = 30   # sans odds (mode degrade)
HARD_CAP_PER_TEAM = 10


def _quality_score(p):
    """
    Score qualite d'un pick basee sur l'alignement de signaux multiples.
    Un pick a 64% conf mais avec edge solide + trend hot + def_arg + hit L20 OK
    doit etre garde. Inversement un pick 80% conf sans support contextuel est
    filtre.

    Composantes (toutes additives) :
      - Edge bookmaker (pondere x1.5) : c'est l'EV reelle, le signal le + fort
      - Bonus confidence > 50%
      - Bonus hit rate L20 > 50% (durabilite vs sample)
      - Bonus trend hot (delta L10-L20 positif)
      - Bonus def_argument (matchup favorable)
      - Penalite rotation_warning (deja filtre upstream, double secu)
    """
    edge = p.get("edge") or 0
    conf = p.get("confidence", 0)
    hit_l20 = p.get("hit_l20_pct", p.get("hit_pct", 0))
    td = p.get("trend_delta", 0)
    has_def_arg = bool(p.get("def_argument"))
    has_rotation_warn = bool(p.get("rotation_warning"))

    score = 0.0
    score += edge * 1.5                          # edge le + important
    score += max(0, conf - 50) * 0.5             # bonus au-dessus 50% conf
    score += max(0, hit_l20 - 50) * 0.5          # bonus au-dessus 50% L20
    score += max(0, td) * 0.4                    # bonus trend hot uniquement
    if has_def_arg:        score += 12           # matchup favorable
    if has_rotation_warn:  score -= 25           # joueur bench -> penalise fort
    return score


def _normal_cdf(x, mu, sigma):
    """P(X <= x) pour X ~ Normal(mu, sigma)."""
    if sigma <= 0: return 0.5 if x < mu else 1.0
    z = (x - mu) / (sigma * math.sqrt(2))
    return 0.5 * (1 + math.erf(z))


def _weighted_avg(l5_avg, l10_avg, season_avg, w5=0.50, w10=0.30, ws=0.20):
    """Moyenne ponderee L5 / L10 / Saison."""
    vals = []
    if l5_avg is not None:     vals.append((float(l5_avg), w5))
    if l10_avg is not None:    vals.append((float(l10_avg), w10))
    if season_avg is not None: vals.append((float(season_avg), ws))
    if not vals: return None
    total = sum(w for _, w in vals)
    return sum(v * w for v, w in vals) / total


def _extract_values(games, key):
    """Extrait les valeurs d'un stat (PTS, REB...) depuis une liste de games."""
    return [g.get(key, 0) or 0 for g in games if g]


def _compose_pra(g):
    return (g.get("PTS",0) or 0) + (g.get("REB",0) or 0) + (g.get("AST",0) or 0)
def _compose_pr(g):
    return (g.get("PTS",0) or 0) + (g.get("REB",0) or 0)
def _compose_pa(g):
    return (g.get("PTS",0) or 0) + (g.get("AST",0) or 0)


def _fair_cote(prob_pct):
    if not prob_pct or prob_pct <= 0 or prob_pct >= 100: return None
    return round(100 / prob_pct, 2)


def _value_tier(cote_min):
    if not cote_min: return None
    if cote_min >= 1.50: return ("🎯", "Bonne value", "#22c55e")
    if cote_min >= 1.35: return ("💎", "Value correcte", "#84cc16")
    if cote_min >= 1.25: return ("⚠️", "Value serrée", "#f59e0b")
    return None


# Mapping prop -> cle dans opp_def_allowed
PROP_TO_DEF_KEY = {
    "PTS":  "opp_pts",
    "REB":  "opp_reb",
    "AST":  "opp_ast",
    "FG3M": "opp_fg3m",
    # Pour combos, on utilise la composante principale (PTS) car points dominent les combos
    "PRA":  "opp_pts",
    "PR":   "opp_pts",
    "PA":   "opp_pts",
}
PROP_TO_DEF_LABEL = {
    "PTS":  "points",
    "REB":  "rebonds",
    "AST":  "passes",
    "FG3M": "3-points",
    "PRA":  "points",
    "PR":   "points",
    "PA":   "points",
}


def _compute_multipliers(match_ctx, prop_key, is_home):
    """
    Calcule les multiplicateurs contextuels appliques a mu pour un prop.
    - pace_mult : pour stats de volume (PTS, REB, AST, FG3M, et combos)
    - vegas_mult: pour PTS et combos contenant des points (PTS, PRA, PR, PA)
    - def_mult  : faille defensive de l'adversaire pour ce stat (DVP-lite)
    - rest_mult : B2B haircut sur volume
    Retourne (mult, breakdown_dict) - breakdown utile pour le reasoning + UI.
    """
    if not match_ctx: return 1.0, {}
    mult = 1.0
    bd = {}

    # ─── Pace mult ───────────────────────────────────────────────────────────
    home_pace = match_ctx.get("home_pace") or LEAGUE_AVG_PACE_DEFAULT
    away_pace = match_ctx.get("away_pace") or LEAGUE_AVG_PACE_DEFAULT
    lg_pace   = match_ctx.get("league_avg_pace") or LEAGUE_AVG_PACE_DEFAULT
    game_pace = (home_pace + away_pace) / 2
    pace_mult = game_pace / lg_pace if lg_pace else 1.0
    pace_mult = max(0.90, min(1.10, pace_mult))
    if prop_key in ("PTS", "REB", "AST", "FG3M", "PRA", "PR", "PA"):
        mult *= pace_mult
        bd["pace"] = round(pace_mult, 3)

    # ─── Vegas mult (team total vs season PPG) ──────────────────────────────
    team_total  = (match_ctx.get("home_total") if is_home else match_ctx.get("away_total")) or 0
    team_ppg    = (match_ctx.get("home_ppg")   if is_home else match_ctx.get("away_ppg"))   or LEAGUE_AVG_PPG_DEFAULT
    if team_total and team_ppg:
        vegas_mult = team_total / team_ppg
        vegas_mult = max(0.88, min(1.12, vegas_mult))
        if prop_key in ("PTS", "PRA", "PR", "PA"):
            mult *= vegas_mult
            bd["vegas"] = round(vegas_mult, 3)

    # ─── Defense mult (faille defensive de l'adversaire) ─────────────────────
    # Si player home -> opp = away. Si player away -> opp = home.
    opp_def    = match_ctx.get("away_def_allowed") if is_home else match_ctx.get("home_def_allowed")
    league_def = match_ctx.get("league_def_avg") or {}
    def_key    = PROP_TO_DEF_KEY.get(prop_key)
    if opp_def and league_def and def_key:
        opp_val = opp_def.get(def_key) or 0
        avg_val = league_def.get(def_key) or 0
        if opp_val > 0 and avg_val > 0:
            def_mult = opp_val / avg_val
            def_mult = max(0.92, min(1.08, def_mult))
            mult *= def_mult
            bd["def"] = round(def_mult, 3)
            # Rang de defense pour le stat (1 = pire defense)
            rank_key = f"rank_{def_key}"
            rank = opp_def.get(rank_key)
            if rank:
                bd["def_rank"] = rank  # 1..30, 1 = encaisse le + (faille)
                bd["def_stat"] = PROP_TO_DEF_LABEL.get(prop_key, "")

    # ─── Rest / B2B mult ─────────────────────────────────────────────────────
    if match_ctx.get("is_b2b"):
        rest_mult = 0.96
        mult *= rest_mult
        bd["b2b"] = rest_mult

    # ─── Blowout mult (spread eleve = garbage time pour les starters) ───────
    # Quand |spread| >= 10 le coach sort ses starters tot, -6% sur volume.
    # On regarde le spread du cote du joueur.
    team_spread = match_ctx.get("home_spread") if is_home else match_ctx.get("away_spread")
    if team_spread is not None and abs(team_spread) >= 10:
        blowout_mult = 0.94
        if prop_key in ("PTS", "REB", "AST", "FG3M", "PRA", "PR", "PA"):
            mult *= blowout_mult
            bd["blowout"] = blowout_mult

    return mult, bd


def _detect_b2b(last_game_date_str, upcoming_game_date_str):
    """Detect back-to-back : moins de 36h entre fin du dernier match et le prochain."""
    if not last_game_date_str or not upcoming_game_date_str: return False
    try:
        last = datetime.strptime(last_game_date_str, "%Y-%m-%d")
        upc  = datetime.strptime(upcoming_game_date_str[:10], "%Y-%m-%d")
        return (upc - last).days <= 1
    except Exception:
        return False


def player_props(player, ctx=None, real_lines=None, match_ctx=None):
    """
    Genere les picks pour un joueur.
    match_ctx : dict avec home_pace, away_pace, league_avg_pace, home_total, away_total,
                home_ppg, away_ppg, game_date, is_b2b, is_home (oui le joueur joue a domicile).
    """
    name = player.get("name", "?")
    season = player.get("season_avg", {})
    raw_games = player.get("l10_games", []) or []   # 20 derniers matchs bruts
    real_lines = real_lines or {}
    match_ctx = match_ctx or {}
    is_home = bool(match_ctx.get("is_home"))

    # ─── Detection rotation reduite (joueur passe en bench / sortie equipe) ─
    # Si 4+ des 10 derniers VRAIS matchs ont MIN<15, le joueur n'est plus dans
    # la rotation (cas Harrison Barnes en mai 2026). On flag pour avertir
    # l'utilisateur et bloquer les alertes high-value, MAIS on garde tous les
    # matchs dans l'analyse (sinon biais positif sur L10/L20).
    last10_raw = raw_games[:10]
    low_min_count = sum(1 for g in last10_raw if (g.get("MIN") or 0) < 15)
    rotation_warning = None
    if last10_raw and low_min_count >= 4:
        recent_mins = [int(g.get("MIN") or 0) for g in last10_raw[:5]]
        rotation_warning = (
            f"Rotation reduite : {low_min_count}/10 derniers matchs <15 min "
            f"(derniers 5 : {'/'.join(str(m) for m in recent_mins)} min)"
        )

    # ─── Filtre minimum : seulement les VRAIS DNP (MIN=0, blesse/scratch) ──
    # On garde TOUS les autres matchs (bench, garbage, etc.) pour avoir un L10/L20
    # honnete representant la production actuelle du joueur.
    games = [g for g in raw_games if (g.get("MIN") or 0) > 0]
    if not games: return []

    # ─── B2B detection (utilise la date du game le + recent du joueur) ──────
    if not match_ctx.get("is_b2b") and games:
        last_date = games[0].get("date")
        match_ctx["is_b2b"] = _detect_b2b(last_date, match_ctx.get("game_date"))

    # L20 sample, L10 et L5 fenetres
    l20 = games[:20]
    l10 = games[:10]
    l5  = games[:5]

    def stats_for(extractor, season_value, prop_key=None):
        l5_vals  = [extractor(g) for g in l5]
        l10_vals = [extractor(g) for g in l10]
        l20_vals = [extractor(g) for g in l20]
        l5_mean  = mean(l5_vals) if l5_vals else None
        l10_mean = mean(l10_vals) if l10_vals else None
        # Sigma : stdev sur L20 (plus stable), floor 15% du mean (anti-overconfidence)
        try:
            sigma = stdev(l20_vals) if len(l20_vals) >= 3 else (max(2.0, (l10_mean or 0) * 0.4))
        except Exception:
            sigma = max(2.0, (l10_mean or 0) * 0.4)
        if l10_mean: sigma = max(sigma, abs(l10_mean) * 0.15)
        sigma = max(sigma, 0.5)  # floor absolu

        mu_base = _weighted_avg(l5_mean, l10_mean, season_value)
        # Ajuste mu par les multiplicateurs contextuels
        if mu_base is not None and prop_key:
            mult, _ = _compute_multipliers(match_ctx, prop_key, is_home)
            mu = mu_base * mult
            # Sigma s'ajuste proportionnellement (variance scale avec volume)
            sigma = sigma * (0.5 + 0.5 * mult)  # damp un peu
        else:
            mu = mu_base
        # Hit rate sur L20 brut (non-ajuste) pour blending
        return mu, sigma, l5_mean, l10_mean, l20_vals

    pts_mu, pts_sd, pts_l5, pts_l10, pts_vals  = stats_for(lambda g: g.get("PTS",0) or 0,  season.get("PTS"),  "PTS")
    reb_mu, reb_sd, reb_l5, reb_l10, reb_vals  = stats_for(lambda g: g.get("REB",0) or 0,  season.get("REB"),  "REB")
    ast_mu, ast_sd, ast_l5, ast_l10, ast_vals  = stats_for(lambda g: g.get("AST",0) or 0,  season.get("AST"),  "AST")
    fg3_mu, fg3_sd, fg3_l5, fg3_l10, fg3_vals  = stats_for(lambda g: g.get("FG3M",0) or 0, season.get("FG3M"), "FG3M")
    pra_season = (season.get("PTS",0) or 0) + (season.get("REB",0) or 0) + (season.get("AST",0) or 0)
    pra_mu, pra_sd, pra_l5, pra_l10, pra_vals = stats_for(_compose_pra, pra_season, "PRA")
    pr_season  = (season.get("PTS",0) or 0) + (season.get("REB",0) or 0)
    pr_mu,  pr_sd,  pr_l5,  pr_l10,  pr_vals  = stats_for(_compose_pr,  pr_season,  "PR")
    pa_season  = (season.get("PTS",0) or 0) + (season.get("AST",0) or 0)
    pa_mu,  pa_sd,  pa_l5,  pa_l10,  pa_vals  = stats_for(_compose_pa,  pa_season,  "PA")

    candidates = []

    def _hit_rates(vals, line, direction):
        """
        Retourne (hits_l10, n_l10, hits_l20, n_l20, trend_delta_pp).
        trend_delta = L10% - L20%.
        > +10pp = momentum positif (hot streak)
        < -10pp = momentum negatif (cold streak)
        """
        if not vals: return 0, 0, 0, 0, 0
        def _h(arr):
            if direction == "over":
                return sum(1 for v in arr if v > line)
            else:
                return sum(1 for v in arr if v < line)
        l10_arr = vals[:10]
        l20_arr = vals[:20]
        h10, n10 = _h(l10_arr), len(l10_arr)
        h20, n20 = _h(l20_arr), len(l20_arr)
        pct10 = (h10 / n10 * 100) if n10 else 0
        pct20 = (h20 / n20 * 100) if n20 else 0
        return h10, n10, h20, n20, round(pct10 - pct20, 1)

    # Conserve l'ancien helper pour compat
    def _hit_rate(vals, line, direction):
        h10, n10, _, _, _ = _hit_rates(vals, line, direction)
        return h10, n10

    # Strict mode : si on a au moins 1 vraie ligne pour ce joueur,
    # on n'autorise QUE les props que le bookmaker quote (pas de fallback heuristique).
    has_any_real = bool(real_lines)

    def gen_picks(prop_key, label_str, mu, sigma, lines, season_v, l5_v, l10_v, vals):
        if mu is None or sigma is None or sigma <= 0:
            return
        allowed = ALLOWED_DIRECTIONS.get(prop_key, ("over", "under"))

        # Si on a une vraie ligne bookmaker, on scanne TOUTES les alt-lines pour
        # trouver celle avec le meilleur edge. Sinon : fallback heuristique.
        real = real_lines.get(prop_key)
        use_real = real and real.get("line") is not None
        alt_lines_data = (real or {}).get("all_lines") or []
        if use_real:
            if alt_lines_data:
                lines_to_check = [L["line"] for L in alt_lines_data]
            else:
                lines_to_check = [real["line"]]
        elif has_any_real:
            return  # bookmaker n'a pas ce prop pour ce joueur -> skip
        else:
            lines_to_check = lines

        for line in lines_to_check:
            p_over_pct = round((1 - _normal_cdf(line, mu, sigma)) * 100, 1)
            p_under_pct = round(100 - p_over_pct, 1)

            for direction, p_pct in [("over", p_over_pct), ("under", p_under_pct)]:
                if direction not in allowed:
                    continue
                # Si vraie ligne : 53-87% conf ET edge >= 3% post-vig
                # Sinon : filtre par sweet-spot heuristique
                if use_real:
                    if not (53 <= p_pct <= 87): continue
                else:
                    if not (SWEET_LOW <= p_pct <= SWEET_HIGH): continue
                cm = _fair_cote(p_pct)
                # Multi-book : pour CETTE ligne specifique, on trouve les meilleurs cotes
                # par direction parmi tous les books qui la quotent
                real_cote = None
                best_book = None
                all_books_this_line = []
                if use_real and alt_lines_data:
                    # Recupere tous les entries alt_lines pour cette ligne specifique
                    entries_for_line = [L for L in alt_lines_data if L.get("line") == line]
                    all_books_this_line = entries_for_line  # liste de {line,over,under,book}
                    # Pick la meilleure cote pour cette direction (la PLUS GRANDE = plus payante)
                    candidates_with_cote = [(L.get(direction), L.get("book")) for L in entries_for_line if L.get(direction)]
                    if candidates_with_cote:
                        candidates_with_cote.sort(key=lambda x: x[0], reverse=True)
                        real_cote, best_book = candidates_with_cote[0]
                elif use_real:
                    real_cote = real.get(direction)
                    best_book = real.get("book")
                # Edge : ecart entre cote du book et fair cote (fair = 100/p_pct)
                edge = None
                if use_real and real_cote and cm:
                    edge = round((real_cote - cm) / cm * 100, 1)
                    if edge < 3:
                        continue
                tier = _value_tier(cm) if not use_real else ("🎯", "Real DK/FD", "#22c55e")
                if not tier: continue
                h10, n10, h20, n20, trend_delta = _hit_rates(vals, line, direction)
                pct_l10 = round(h10 / n10 * 100) if n10 else 0
                pct_l20 = round(h20 / n20 * 100) if n20 else 0
                # Trend label
                if   trend_delta >=  10: trend, trend_icon = "hot",    "📈"
                elif trend_delta <= -10: trend, trend_icon = "cold",   "📉"
                else:                    trend, trend_icon = "stable", ""
                prefix = "plus de" if direction == "over" else "moins de"
                _, mult_bd = _compute_multipliers(match_ctx, prop_key, is_home)
                ctx_parts = []
                if "pace"  in mult_bd: ctx_parts.append(f"pace×{mult_bd['pace']}")
                if "vegas" in mult_bd: ctx_parts.append(f"vegas×{mult_bd['vegas']}")
                if "def"   in mult_bd: ctx_parts.append(f"def×{mult_bd['def']}")
                if "b2b"   in mult_bd: ctx_parts.append(f"B2B×{mult_bd['b2b']}")
                ctx_str = (" · " + " · ".join(ctx_parts)) if ctx_parts else ""

                # Argument defensif fort : phrase humaine pour l'UI
                def_argument = ""
                if "def_rank" in mult_bd:
                    rank = mult_bd["def_rank"]
                    stat_label = mult_bd.get("def_stat", "")
                    opp_team_name = match_ctx.get("away_team") if is_home else match_ctx.get("home_team")
                    # Faille forte : top 8 (rang 1-8 = encaisse le +)
                    # Force forte : bottom 8 (rang 23-30 = encaisse le -)
                    if rank <= 8:
                        def_argument = f"Adversaire encaisse beaucoup de {stat_label} (#{rank}/30)"
                    elif rank >= 23:
                        def_argument = f"Adversaire solide vs {stat_label} (#{rank}/30)"
                # Liste tous les books proposant cette ligne+direction (pour display)
                books_for_pick = []
                if use_real and all_books_this_line:
                    for L in all_books_this_line:
                        if L.get(direction):
                            books_for_pick.append({"book": L["book"], "cote": L[direction]})
                    # Tri par cote decroissante (meilleure d'abord)
                    books_for_pick.sort(key=lambda b: b["cote"], reverse=True)
                candidates.append({
                    "prop": prop_key,
                    "label": f"{name} {prefix} {line} {label_str}",
                    "line": line,
                    "direction": direction,
                    "confidence": round(p_pct),
                    "cote_min": cm,
                    "real_cote": real_cote,
                    "book": best_book if use_real else None,
                    "books": books_for_pick,  # liste {book, cote} pour display multi-book
                    "edge": edge,
                    "is_real_line": bool(use_real),
                    "value": tier,
                    "hit_rate": f"{h10}/{n10}",
                    "hit_pct":  pct_l10,
                    "hit_l10":     f"{h10}/{n10}",
                    "hit_l10_pct": pct_l10,
                    "hit_l20":     f"{h20}/{n20}",
                    "hit_l20_pct": pct_l20,
                    "trend":       trend,
                    "trend_delta": trend_delta,
                    "trend_icon":  trend_icon,
                    "context": mult_bd,
                    "def_argument": def_argument,
                    "rotation_warning": rotation_warning,  # flag si joueur passe bench
                    "stats": {"mu": round(mu, 1), "sigma": round(sigma, 1),
                              "L5": round(l5_v or 0, 1), "L10": round(l10_v or 0, 1),
                              "Saison": round(season_v or 0, 1)},
                    "reasoning": f"L5 {round(l5_v or 0,1)} · L10 {round(l10_v or 0,1)} · Saison {round(season_v or 0,1)} → attendu {round(mu,1)}{ctx_str}",
                })

    gen_picks("PTS",  "points",       pts_mu, pts_sd, BOOKMAKER_LINES["PTS"],  season.get("PTS"), pts_l5, pts_l10, pts_vals)
    gen_picks("REB",  "rebonds",      reb_mu, reb_sd, BOOKMAKER_LINES["REB"],  season.get("REB"), reb_l5, reb_l10, reb_vals)
    gen_picks("AST",  "passes",       ast_mu, ast_sd, BOOKMAKER_LINES["AST"],  season.get("AST"), ast_l5, ast_l10, ast_vals)
    gen_picks("FG3M", "3-points",     fg3_mu, fg3_sd, BOOKMAKER_LINES["FG3M"], season.get("FG3M"), fg3_l5, fg3_l10, fg3_vals)
    gen_picks("PRA",  "PRA",          pra_mu, pra_sd, BOOKMAKER_LINES["PRA"],  pra_season, pra_l5, pra_l10, pra_vals)
    gen_picks("PR",   "pts + reb",    pr_mu,  pr_sd,  BOOKMAKER_LINES["PR"],   pr_season,  pr_l5,  pr_l10,  pr_vals)
    gen_picks("PA",   "pts + ast",    pa_mu,  pa_sd,  BOOKMAKER_LINES["PA"],   pa_season,  pa_l5,  pa_l10,  pa_vals)

    # Tri par CONFIDENCE desc (les + forts en premier) + hit_pct
    # Score = confidence + bonus hit_rate (pondère par % de hits)
    def _cand_score(p):
        td = max(-30, min(30, p.get("trend_delta", 0)))
        return p["confidence"] + p.get("hit_l20_pct", 0) * 0.2 + td * 0.3
    candidates.sort(key=_cand_score, reverse=True)
    # Diversification : 1 pick max par prop_type, et on tente d'avoir 1 high + 1 mid
    high = [c for c in candidates if c["confidence"] >= 70]
    mid  = [c for c in candidates if c["confidence"] <  70]
    selected, seen_props = [], set()
    # 1 best pick (high si dispo, sinon mid)
    for c in (high or mid):
        if c["prop"] in seen_props: continue
        selected.append(c); seen_props.add(c["prop"]); break
    # 1 mid pick sur prop differente (pour avoir cote >1.50)
    for c in mid:
        if c["prop"] in seen_props: continue
        selected.append(c); seen_props.add(c["prop"]); break
    # On remplit jusqu'a MAX_PICKS_PER_PLAYER avec n'importe quoi
    for c in candidates:
        if c["prop"] in seen_props: continue
        selected.append(c); seen_props.add(c["prop"])
        if len(selected) >= MAX_PICKS_PER_PLAYER: break
    return selected[:MAX_PICKS_PER_PLAYER + 1]  # tolere 1 de plus pour le mix team-level


def _match_player_name(name, odds_players):
    """Match fuzzy d'un nom de joueur entre stats.nba.com et The Odds API."""
    if not name or not odds_players: return None
    nlow = name.lower().strip()
    if nlow in {k.lower() for k in odds_players}:
        return next(k for k in odds_players if k.lower() == nlow)
    # Tente sans diacritiques / prefixes (Jr., II, etc.)
    import unicodedata
    def _clean(s):
        s = unicodedata.normalize("NFD", s)
        s = "".join(c for c in s if unicodedata.category(c) != "Mn")
        return s.lower().replace(".", "").replace("-", " ").replace(" jr", "").replace(" ii", "").replace(" iii", "").strip()
    nclean = _clean(name)
    for k in odds_players:
        if _clean(k) == nclean: return k
    # Match partiel sur lastname
    n_last = nclean.split()[-1] if nclean else ""
    for k in odds_players:
        k_last = _clean(k).split()[-1] if _clean(k) else ""
        if n_last and n_last == k_last:
            return k
    return None


def analyze_match(match_data, odds_for_game=None, game_lines=None):
    """Pour 1 match NBA, genere picks joueurs (home + away).
    Si odds_for_game fourni (mode bookmaker reel), on skip les joueurs non listes par DK.
    Si game_lines fourni : contient Vegas total + spread pour scaler les projections."""
    home_team = match_data.get("home_team", "?")
    away_team = match_data.get("away_team", "?")
    home_players = match_data.get("home_players", [])
    away_players = match_data.get("away_players", [])
    odds_for_game = odds_for_game or {}
    game_lines = game_lines or {}
    bookmaker_mode = bool(odds_for_game)  # True si on a des odds reelles pour ce match

    # ─── Match context (pace, vegas total, game date, def stats, spread) ────
    base_ctx = {
        "home_team":       home_team,
        "away_team":       away_team,
        "home_pace":       match_data.get("home_pace"),
        "away_pace":       match_data.get("away_pace"),
        "league_avg_pace": match_data.get("league_avg_pace") or LEAGUE_AVG_PACE_DEFAULT,
        "home_ppg":        match_data.get("home_ppg"),
        "away_ppg":        match_data.get("away_ppg"),
        "home_total":      game_lines.get("home_total"),
        "away_total":      game_lines.get("away_total"),
        "game_total":      game_lines.get("game_total"),
        "home_spread":     game_lines.get("home_spread"),  # pour blowout mult
        "away_spread":     game_lines.get("away_spread"),
        "game_date":       match_data.get("game_date") or "",
        "home_def_allowed": match_data.get("home_def_allowed", {}),
        "away_def_allowed": match_data.get("away_def_allowed", {}),
        "league_def_avg":   match_data.get("league_def_avg", {}),
    }

    def _get_real_lines(player_name):
        if not odds_for_game: return None
        match = _match_player_name(player_name, odds_for_game)
        return odds_for_game.get(match) if match else None

    home_picks = []
    away_picks = []
    for p in home_players:
        rl = _get_real_lines(p.get("name"))
        if bookmaker_mode and not rl: continue
        ctx = dict(base_ctx); ctx["is_home"] = True
        picks = player_props(p, ctx={"is_home": True}, real_lines=rl, match_ctx=ctx)
        for pk in picks:
            pk["player"] = p.get("name")
            pk["team"] = home_team
            pk["side"] = "home"
        home_picks.extend(picks)
    for p in away_players:
        rl = _get_real_lines(p.get("name"))
        if bookmaker_mode and not rl: continue
        ctx = dict(base_ctx); ctx["is_home"] = False
        picks = player_props(p, ctx={"is_home": False}, real_lines=rl, match_ctx=ctx)
        for pk in picks:
            pk["player"] = p.get("name")
            pk["team"] = away_team
            pk["side"] = "away"
        away_picks.extend(picks)

    def _filter_by_quality(picks, max_per_player=1, hard_cap=HARD_CAP_PER_TEAM):
        """
        Selection par QUALITY_SCORE. Seuil adaptatif :
        - Mode bookmaker (>=1 pick avec is_real_line=True) -> QUALITY_SCORE_MIN_WITH_ODDS=50
        - Mode heuristique (aucun pick avec odds reelles) -> QUALITY_SCORE_MIN_HEURISTIC=30
          (quota odds API epuise par ex.)
        """
        has_real_odds = any(p.get("is_real_line") for p in picks)
        threshold = QUALITY_SCORE_MIN_WITH_ODDS if has_real_odds else QUALITY_SCORE_MIN_HEURISTIC
        eligible = [p for p in picks if _quality_score(p) >= threshold]
        eligible.sort(key=_quality_score, reverse=True)

        selected, per_player = [], {}
        # 1er passage : max 1 pick par joueur
        for p in eligible:
            if len(selected) >= hard_cap: break
            pname = p.get("player", "?")
            if per_player.get(pname, 0) >= max_per_player: continue
            selected.append(p)
            per_player[pname] = per_player.get(pname, 0) + 1
        # 2eme passage : tolere 2 par joueur si on a pas atteint le hard cap
        if len(selected) < hard_cap:
            for p in eligible:
                if p in selected: continue
                pname = p.get("player", "?")
                if per_player.get(pname, 0) >= 2: continue
                selected.append(p)
                per_player[pname] = per_player.get(pname, 0) + 1
                if len(selected) >= hard_cap: break
        return selected

    home_picks = _filter_by_quality(home_picks)
    away_picks = _filter_by_quality(away_picks)

    # ── Injury filter Tank01 (ne se declenche que si RAPIDAPI_KEY dispo) ──
    # Pour chaque pick survivant, on check le statut blessure du joueur.
    # - Out / Doubtful -> on DROP le pick (joueur ne joue pas)
    # - Day-To-Day -> on flag injury_warning mais on garde
    # API call par joueur unique (cache 12h Tank01), max ~10-20 calls/run.
    try:
        from nba_tank01 import is_player_out
    except ImportError:
        is_player_out = None

    def _apply_injury_filter(picks):
        if not is_player_out: return picks
        out = []
        checked = {}  # cache local par run : 1 call par joueur unique
        for p in picks:
            pname = p.get("player", "")
            if not pname:
                out.append(p); continue
            if pname not in checked:
                try:
                    checked[pname] = is_player_out(pname)
                except Exception:
                    checked[pname] = (False, "", "")
            is_out, status, ret_date = checked[pname]
            if is_out:
                print(f"  [INJURY OUT] skip pick {pname} ({status})")
                continue
            if status:  # Day-To-Day, Questionable etc.
                p["injury_warning"] = f"{status}" + (f" (return {ret_date})" if ret_date else "")
            out.append(p)
        return out

    home_picks = _apply_injury_filter(home_picks)
    away_picks = _apply_injury_filter(away_picks)
    return home_picks, away_picks


def run():
    try:
        ps = json.load(open("data/nba_player_stats.json", encoding="utf-8"))
        games = json.load(open("data/nba_matches.json", encoding="utf-8"))
    except Exception as e:
        print(f"[X] Charger NBA data: {e}")
        return {}

    # Charge les odds bookmaker (optionnel)
    odds = {}
    try:
        odds = json.load(open("data/nba_odds.json", encoding="utf-8"))
        if odds:
            n_props = sum(len(p) for g in odds.values() for p in g.values())
            print(f"  [odds] {len(odds)} matchs avec {n_props} props bookmaker")
        else:
            print(f"  [odds] aucune odds reelle dispo, fallback lignes heuristiques")
    except Exception:
        print(f"  [odds] data/nba_odds.json absent - lignes heuristiques")

    # Charge les game lines (Vegas totals + spreads pour vegas_mult)
    glines = {}
    try:
        glines = json.load(open("data/nba_game_lines.json", encoding="utf-8"))
        print(f"  [game_lines] {len(glines)} matchs avec total/spread Vegas")
    except Exception:
        print(f"  [game_lines] data/nba_game_lines.json absent - pas de vegas mult")

    out = {}
    for g in games:
        gid = g["game_id"]
        match_data = ps.get(str(gid)) or ps.get(gid)
        if not match_data: continue
        odds_for_game = odds.get(str(gid)) or odds.get(gid) or {}
        gline = glines.get(str(gid)) or glines.get(gid) or {}
        # Propage game_date dans match_data
        if g.get("date") and not match_data.get("game_date"):
            match_data["game_date"] = g["date"]
        home_picks, away_picks = analyze_match(match_data, odds_for_game=odds_for_game, game_lines=gline)
        out[str(gid)] = {
            "game_id":    gid,
            "home_team":  g["home"],
            "away_team":  g["away"],
            "date":       g.get("date"),
            "status":     g.get("status"),
            "home_picks": home_picks,
            "away_picks": away_picks,
        }
        print(f"  {g['away']} @ {g['home']}: {len(home_picks)} home picks · {len(away_picks)} away picks")

    with open("data/nba_picks.json", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"\n[OK] {len(out)} matchs -> data/nba_picks.json")

    # Sauvegarde dans l'historique (status PENDING - resolu plus tard par nba_resolver.py)
    _save_to_history(out)
    return out


def _save_to_history(picks_data):
    """
    Ajoute les picks du jour dans data/nba_picks_history.json (sans ecraser les existants).
    Format flat list : chaque pick a un `id` unique pour permettre la deduplication.
    """
    from pathlib import Path
    hist_path = Path("data/nba_picks_history.json")
    if hist_path.exists():
        try:
            history = json.loads(hist_path.read_text(encoding="utf-8"))
        except Exception:
            history = {"picks": []}
    else:
        history = {"picks": []}

    existing_ids = {p.get("id") for p in history.get("picks", [])}
    today = datetime.now().strftime("%Y-%m-%d")
    n_added = 0

    def _extract_game_date(game_data, fallback):
        """Retourne YYYY-MM-DD a partir de la date NBA (gameEt / gameTimeUTC), sinon fallback."""
        raw = game_data.get("date") or ""
        if isinstance(raw, str) and len(raw) >= 10:
            # Format ISO : "2026-05-19T20:00:00..." ou similaire
            candidate = raw[:10]
            # Validation rapide
            if candidate.count("-") == 2 and candidate[4] == "-" and candidate[7] == "-":
                return candidate
        return fallback

    for gid, game in picks_data.items():
        matchup = f"{game.get('away_team','?')} @ {game.get('home_team','?')}"
        date = _extract_game_date(game, today)
        for pick in (game.get("home_picks", []) + game.get("away_picks", [])):
            pick_id = f"{date}_{gid}_{pick.get('player','?')}_{pick.get('prop','?')}_{pick.get('direction','?')}_{pick.get('line','?')}"
            if pick_id in existing_ids: continue
            entry = dict(pick)
            entry["id"] = pick_id
            entry["date"] = date
            entry["game_id"] = gid
            entry["matchup"] = matchup
            entry["result"] = "PENDING"
            entry["actual"] = None
            entry["resolved_at"] = None
            history["picks"].append(entry)
            existing_ids.add(pick_id)
            n_added += 1

    hist_path.write_text(json.dumps(history, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[history] {n_added} picks ajoutes (total: {len(history['picks'])})")


if __name__ == "__main__":
    run()
