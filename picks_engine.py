"""
picks_engine.py — v10
- Forme récente prime sur H2H historique
- Analyse buteur enrichie (forme récente du joueur)
- Max 3 props joueurs par match (pas par équipe)
- Paris "fun" (cote >= 2.0) avec analyse contextuelle
- Reasoning explicatif (plus de "probabilité bookmaker X%")
"""

import json, os, math

def load_matches():
    with open("data/matches.json", encoding="utf-8") as f:
        return json.load(f)

def load_player_stats():
    path = "data/player_stats.json"
    if not os.path.exists(path): return {}
    with open(path, encoding="utf-8") as f:
        return json.load(f)

# ─── Helpers ────────────────────────────────────────────────────────────────

def get_form(fd, side):
    try: return fd.get(side, {}).get("form", [])
    except: return []

def win_rate(f):   return f.count("W") / len(f) if f else 0
def unbeaten(f):   return (f.count("W") + f.count("D")) / len(f) if f else 0
def loss_rate(f):  return f.count("L") / len(f) if f else 0

def _sos_weight(opp_rank):
    """Poids Strength-of-Schedule selon rank adversaire.
    Top 4 -> 1.5x · top 10 -> 1.2x · 11-15 -> 1.0x · 16-18 -> 0.8x · 19+ -> 0.6x.
    Inconnu -> 1.0x (neutre)."""
    if opp_rank is None:
        return 1.0
    if opp_rank <= 4:  return 1.5
    if opp_rank <= 10: return 1.2
    if opp_rank <= 15: return 1.0
    if opp_rank <= 18: return 0.8
    return 0.6

def sos_unbeaten(f, opp_ranks):
    """Unbeaten% pondere par la qualite des adversaires.
    Une victoire vs top-4 vaut 1.5x ; un nul vs bottom-3 vaut 0.6x.
    Si opp_ranks absent / vide -> fallback sur unbeaten() simple."""
    if not f or not opp_ranks or len(opp_ranks) != len(f):
        return unbeaten(f)
    total_w = 0.0
    pts_w = 0.0
    for res, opp_r in zip(f, opp_ranks):
        w = _sos_weight(opp_r)
        total_w += w
        if res in ("W", "D"):
            pts_w += w
    return (pts_w / total_w) if total_w else unbeaten(f)

def get_form_opp_ranks(fd, side):
    try: return fd.get(side, {}).get("form_opp_ranks", []) or []
    except: return []


# ─── Signaux squad : penaltyman, top scorers, taux penaltys ──────────────────

def team_squad_signals(team_id):
    """Lit le squad d'une equipe via fm_team et extrait :
    - penaltyman : joueur avec le + de pens marques (>=2 pour etre fiable)
    - top_scorers : top 5 buteurs saison
    - total_penalties : somme des pens marques par l'equipe (proxy : taux/m)
    - matches_played : nombre de matchs joues par l'equipe
    """
    out = {"penaltyman": None, "top_scorers": [], "total_penalties": 0,
           "matches_played": None, "team_pen_pm": None}
    if not team_id: return out
    try:
        from fotmob_client import team as fm_team
        td = fm_team(int(team_id)) or {}
    except Exception:
        return out
    groups = td.get("squad", {}).get("squad", []) or []
    all_players = []
    matches_max = 0
    for g in groups:
        if g.get("title") in ("keepers", "coach"): continue
        for p in (g.get("members") or []):
            all_players.append({
                "id":        p.get("id"),
                "name":      (p.get("name") or "").strip(),
                "goals":     p.get("goals") or 0,
                "penalties": p.get("penalties") or 0,
            })
    # Approx team matches : max apparaissant dans les membres du squad
    # (le top scorer/penaltyman est typiquement un titulaire)
    for p in all_players:
        # Pas de "apps" dans squad, on prend les matchs equipe via league
        pass
    if all_players:
        top_p = max(all_players, key=lambda x: x["penalties"])
        if top_p["penalties"] >= 2:
            out["penaltyman"] = top_p
        out["top_scorers"] = sorted([p for p in all_players if p["goals"] >= 5],
                                     key=lambda x: x["goals"], reverse=True)[:5]
        out["total_penalties"] = sum(p["penalties"] for p in all_players)
    return out


def formation_adjust(formation_str):
    """Renvoie un multiplicateur sur le λ adverse selon la formation.
    5+ defenseurs : -10% (defense fermee)
    3 defenseurs  : +10% (defense ouverte)
    4 (defaut)    : pas de changement
    """
    if not formation_str:
        return 1.0, ""
    try:
        # Format type "4-3-3", "5-3-2", "3-4-3" etc.
        parts = str(formation_str).split("-")
        n_def = int(parts[0])
        if n_def >= 5:
            return 0.90, f"defense fermee ({formation_str})"
        if n_def == 3:
            return 1.10, f"defense ouverte ({formation_str})"
        return 1.0, ""
    except Exception:
        return 1.0, ""


# ─── Tiers FIFA pour ajustement Strength-of-Schedule sur les buts ────────
# Probleme : L5 GF/GA brut ne reflete pas le niveau des opposants. Le Pays-
# Bas qui marque 1.6 b/m face a France/Allemagne n'est PAS la meme chose
# qu'Uzbekistan qui marque 1.5 b/m face a Vietnam/Bhutan. Quand ces 2 equipes
# se rencontrent, on doit ajuster les lambda Poisson :
#   - top team marque PLUS qu'a son habitude (vs defense + faible)
#   - weak team marque MOINS qu'a son habitude (vs defense + solide)
#   - top team encaisse MOINS (vs attaque + faible)
#   - weak team encaisse PLUS (vs attaque + solide)

TIER_1_TEAMS = {  # top 12 FIFA (les "monstres")
    "argentina", "france", "spain", "england", "brazil", "portugal",
    "netherlands", "belgium", "italy", "germany", "croatia", "colombia",
}
TIER_2_TEAMS = {  # top 13-30 FIFA (solides)
    "mexico", "switzerland", "uruguay", "usa", "united states", "morocco",
    "japan", "senegal", "iran", "denmark", "norway", "sweden", "wales",
    "turkiye", "turkey", "south korea", "korea republic", "egypt",
    "ivory coast", "cote d'ivoire", "ecuador", "australia", "ukraine",
    "poland", "serbia", "scotland", "austria",
}
TIER_3_TEAMS = {  # top 31-60 FIFA (mid-tier)
    "czechia", "czech republic", "tunisia", "algeria", "nigeria", "cameroon",
    "ghana", "saudi arabia", "south africa", "venezuela", "paraguay", "peru",
    "chile", "bosnia and herzegovina", "bosnia", "ireland", "hungary",
    "romania", "iraq", "qatar", "panama", "jamaica", "honduras",
    "costa rica", "cape verde", "dr congo", "mali", "burkina faso",
    "slovakia", "slovenia", "iceland", "finland", "greece", "albania",
    "north macedonia", "uzbekistan", "georgia", "azerbaijan",
    "canada", "haiti",
}
# Le reste (Bhutan, Cambodge, Andorra, San Marino, Iles Vierges, etc.) = tier 4


def team_tier(name):
    """Tier FIFA approxime de l'equipe (1 = top, 4 = micro nation)."""
    if not name: return 4
    n = str(name).strip().lower()
    if n in TIER_1_TEAMS: return 1
    if n in TIER_2_TEAMS: return 2
    if n in TIER_3_TEAMS: return 3
    return 4


def is_top_intl_team(name):
    """True si l'equipe est tier 1, 2 ou 3 (= grosse selection)."""
    return team_tier(name) <= 3


# ─── FIFA Elo + favoris CdM ─────────────────────────────────────────────
# Charge le ranking depuis data/fifa_rankings.json (top 100 nations FIFA
# avec points Elo). Permet un ajustement MUCH plus fin que les 4 tiers.
# Spain (1854) vs Cape Verde (1334) = 520 pts d'écart → λ_spain ~2.7,
# λ_cv ~0.4 au lieu de 2.04 vs 0.70 avec les tiers.

_FIFA_RANK_CACHE = {"data": None}
_INTL_SHEETS_CACHE = {"data": None}


def _load_intl_sheets():
    """Charge data/intl_team_sheets.json (fiches équipes nationales :
    off_rating, def_rating, off_score, def_score, etc.).
    Cache process-level."""
    if _INTL_SHEETS_CACHE["data"] is not None:
        return _INTL_SHEETS_CACHE["data"]
    try:
        import json as _jsh
        with open("data/intl_team_sheets.json", encoding="utf-8") as f:
            d = _jsh.load(f)
        _INTL_SHEETS_CACHE["data"] = d
        return d
    except Exception:
        _INTL_SHEETS_CACHE["data"] = {"teams": {}}
        return _INTL_SHEETS_CACHE["data"]


def get_intl_sheet(team_name):
    """Renvoie la fiche de l'équipe nationale ou None."""
    if not team_name: return None
    import unicodedata, re
    s = unicodedata.normalize("NFD", team_name.strip())
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    slug = re.sub(r"[^a-z0-9_]", "_", s.lower()).strip("_")
    return ((_load_intl_sheets().get("teams") or {}).get(slug)) or None


def intl_sheet_lambda_adjustment(team_off_score, team_def_score, opp_off_score, opp_def_score):
    """Applique l'ajustement λ en fonction des fiches équipe.
    Renvoie (factor_for_team_attack, factor_for_team_concedes).
    Max ±15% par dimension → ±30% combiné (cap).

    Logique :
      - λ_team_attack ↑ si team off bon, ↓ si team off nul
      - λ_team_attack ↓ si opp def bon, ↑ si opp def nul
    """
    SENSI = 0.15
    factor_atk = (1.0 + SENSI * (team_off_score or 0.0)) * (1.0 - SENSI * (opp_def_score or 0.0))
    factor_conc = (1.0 + SENSI * (opp_off_score or 0.0)) * (1.0 - SENSI * (team_def_score or 0.0))
    # Cap [0.6, 1.4] pour éviter explosions
    factor_atk = max(0.6, min(1.4, factor_atk))
    factor_conc = max(0.6, min(1.4, factor_conc))
    return factor_atk, factor_conc

def _load_fifa_rankings():
    if _FIFA_RANK_CACHE["data"] is not None:
        return _FIFA_RANK_CACHE["data"]
    try:
        import json as _jrank
        with open("data/fifa_rankings.json", encoding="utf-8") as f:
            d = _jrank.load(f)
        _FIFA_RANK_CACHE["data"] = d
        return d
    except Exception as e:
        print(f"  [fifa rankings load err] {e}")
        _FIFA_RANK_CACHE["data"] = {"rankings": {}, "_tournament_favorites": {}, "_uefa_powerhouses": []}
        return _FIFA_RANK_CACHE["data"]


def get_fifa_data(team_name):
    """Renvoie {'rank': int, 'points': int} ou None si pas dans le ranking."""
    if not team_name: return None
    d = _load_fifa_rankings()
    rk = d.get("rankings", {}).get(team_name.strip().lower())
    return rk


def get_fifa_data_or_default(team_name):
    """Comme get_fifa_data mais avec un fallback estimé pour les minnows
    pas dans notre liste (Curaçao, Bhutan, San Marino…) → tier 4 ~1000 pts.

    Évite que ces équipes soient traitées comme "neutre" face à un top et
    sous-estiment les buts du favori (cas Allemagne 7-1 Curaçao).
    """
    fd = get_fifa_data(team_name)
    if fd is not None:
        return fd, False  # vrai ranking
    t = team_tier(team_name)
    # Estime des pts en fonction du tier (4 = micro, valeur basse mais > 0)
    pts_estimated = {1: 1700, 2: 1550, 3: 1400, 4: 1000}[t]
    return {"rank": 130 if t == 4 else 100, "points": pts_estimated}, True


# ─── Contexte CdM : round, mismatch, motivation ──────────────────────────

def _normalize_round_label(label):
    """'1' / 'Group A R1' / 'Matchday 1' → 1, etc. None si non group stage."""
    if label is None: return None
    s = str(label).strip().lower()
    # Knockout phases : on ne boost pas
    for ko in ("final", "semi", "quarter", "round of 16", "round of 32",
              "playoff", "knockout"):
        if ko in s: return None
    # Cherche un chiffre 1/2/3 (matchday group)
    import re as _re
    m = _re.search(r"\b([123])\b", s)
    if m: return int(m.group(1))
    return None


def wc_context_boost(team_name, opp_name, round_num=None, league_id=None):
    """Boost λ ADDITIF (en buts) pour contexte CdM 2026.

    Renvoie (boost_team, boost_opp). Cumulé avec tier_strength_adjustment.

    Logique humaine :
      1. WC R1 = motivation peak. Tous les top ont envie de bien démarrer.
      2. Steamroller : gros favori (Elo gap ≥ 400) vs minnow → le favori
         cherche à prendre le large tôt (différence de but qualif). +0.5 λ
      3. Steamroller XL : gap ≥ 600 → +0.8 λ pour le favori, λ minnow -= 0.2
    """
    if league_id != WC_LEAGUE_ID:
        return 0.0, 0.0
    fd_t, _ = get_fifa_data_or_default(team_name)
    fd_o, _ = get_fifa_data_or_default(opp_name)
    elo_gap = fd_t["points"] - fd_o["points"]
    boost_team = 0.0
    boost_opp  = 0.0
    # R1 motivation (premier match de poule)
    if round_num == 1 and is_wc_favorite(team_name):
        boost_team += 0.20  # +0.20 but : envie de pas mal démarrer
    # Steamroller : gros favori vs minnow
    if elo_gap >= 400:
        boost_team += 0.50
        boost_opp  -= 0.10
    if elo_gap >= 600:
        boost_team += 0.30  # cumulé avec le précédent → +0.80 total
        boost_opp  -= 0.15  # cumulé → -0.25
    # Si team est dans les UEFA powerhouses ET R1 → encore +0.10
    if round_num == 1 and is_uefa_powerhouse(team_name):
        boost_team += 0.10
    return boost_team, boost_opp


def wc_context_descr(team_name, opp_name, round_num=None, league_id=None):
    """Renvoie une description humaine du contexte appliqué. '' si rien."""
    if league_id != WC_LEAGUE_ID:
        return ""
    fd_t, est_t = get_fifa_data_or_default(team_name)
    fd_o, est_o = get_fifa_data_or_default(opp_name)
    elo_gap = fd_t["points"] - fd_o["points"]
    parts = []
    if round_num == 1:
        parts.append(f"🎯 Premier match de poule : motivation peak pour {team_name}, envie de bien démarrer.")
    if elo_gap >= 600:
        parts.append(f"🚂 Gros mismatch (Elo gap +{elo_gap}) : {team_name} cherche à creuser tôt pour la diff de buts qualif.")
    elif elo_gap >= 400:
        parts.append(f"🚂 Mismatch significatif (Elo gap +{elo_gap}) : {team_name} pousse pour prendre le large.")
    if est_o:
        parts.append(f"⚠️ {opp_name} hors top 100 FIFA — fallback Elo estimé, défense souvent perméable vs top.")
    return " ".join(parts)


def is_wc_favorite(team_name):
    """True si l'equipe fait partie des favoris CdM 2026."""
    if not team_name: return False
    d = _load_fifa_rankings()
    favs = d.get("_tournament_favorites", {}).get("world_cup_2026", [])
    return team_name.strip().lower() in favs


def is_uefa_powerhouse(team_name):
    if not team_name: return False
    d = _load_fifa_rankings()
    return team_name.strip().lower() in d.get("_uefa_powerhouses", [])


def fifa_elo_expected(rating_team, rating_opp):
    """Win probability ELO classique : 1 / (1 + 10^((R_opp - R_team)/400))."""
    return 1 / (1 + 10 ** ((rating_opp - rating_team) / 400))


def tier_strength_adjustment(team_name, opp_name):
    """Renvoie (mult_attack, mult_defense_concede) pour TEAM vs OPP.

    Nouvelle implémentation basée sur l'Elo FIFA réel :
    - Diff de 100 pts ≈ +0.4 but d'avantage pour le favori (~équivalent
      à 1 tier dans l'ancien système, mais plus fin).
    - Spain (1854) vs Cape Verde (1334) = 520 pts → mult attack 1.93,
      mult defense 0.39 → λ_spain ~2.7, λ_cv ~0.4.
    - Si l'équipe est un favori CdM 2026 face à une non-favorite : +5%
      attack bonus (motivation / habitude des grandes compets).
    - Si pas dans le ranking : fallback sur l'ancien système 4-tiers
      (Bhutan, San Marino, etc. — tier 4 par défaut).
    """
    fd_team = get_fifa_data(team_name)
    fd_opp  = get_fifa_data(opp_name)

    if fd_team and fd_opp:
        gap = fd_team["points"] - fd_opp["points"]   # > 0 si team plus fort
        # Chaque 100 pts ELO ≈ 0.40 but d'avantage. Mais plutôt que multiplier
        # directement, on applique un facteur multiplicatif sur les λ qui
        # tient compte aussi de la qualité absolue (un top contre top = pas
        # d'avantage net même si gap 50).
        # Mult attack = 1 + gap / 500 (clamp [0.30, 2.20])
        mult_attack = max(0.30, min(2.20, 1 + gap / 500))
        mult_defense_concede = max(0.30, min(2.20, 1 - gap / 500))
        # Bonus favori CdM si team est dans la liste ET opp n'y est pas
        if is_wc_favorite(team_name) and not is_wc_favorite(opp_name):
            mult_attack *= 1.07
            mult_defense_concede *= 0.93
        return mult_attack, mult_defense_concede

    # Fallback : ancien système tiers (équipes hors du top 100 FIFA)
    t_team = team_tier(team_name)
    t_opp  = team_tier(opp_name)
    diff = t_opp - t_team
    mult_attack = max(0.4, min(1.7, 1 + diff * 0.18))
    mult_defense_concede = max(0.4, min(1.7, 1 - diff * 0.18))
    return mult_attack, mult_defense_concede

def get_pos(fd, s):
    pos = (fd or {}).get(s, {}).get("position", None)
    return pos if pos is not None else 20
def get_rat(fd, s):
    try:
        v = (fd or {}).get(s, {}).get("avgRating", None)
        return float(v) if v is not None else 6.5
    except: return 6.5

# ─── World Cup 2026 : score exact via Poisson grid ──────────────────────────
WC_LEAGUE_ID = 77  # FIFA World Cup, identifiant interne

def predict_exact_score(lam_h, lam_a, max_g=5):
    """Renvoie ((home, away), prob, top3) le score le plus probable + 3 alts.

    Calcule P(home=i) x P(away=j) sur une grille [0..max_g] x [0..max_g]
    via PMF de Poisson independantes.
    """
    import math as _m
    def _pmf(k, lam):
        return (lam ** k) * _m.exp(-lam) / _m.factorial(k)
    grid = []
    for i in range(max_g + 1):
        ph = _pmf(i, lam_h)
        for j in range(max_g + 1):
            pa = _pmf(j, lam_a)
            grid.append(((i, j), ph * pa))
    grid.sort(key=lambda x: x[1], reverse=True)
    best_score, best_p = grid[0]
    top3 = grid[:3]
    return best_score, best_p, top3


def parse_h2h(h):
    try:
        d = h.get("teamDuel", {})
        hw = int(d.get("homeWins", 0) or 0)
        dr = int(d.get("draws", 0) or 0)
        aw = int(d.get("awayWins", 0) or 0)
        return hw, dr, aw
    except: return 0,0,0

def is_cup_league(league_id):
    """Détecte si c'est une coupe européenne."""
    return league_id in {7, 679, 17015}

def home_away_context(recent, is_home):
    """
    Retourne les stats pertinentes selon si l'équipe joue à domicile ou extérieur.
    Préfère les stats home/away spécifiques si disponibles (>=3 matchs).
    """
    if not recent:
        return {}
    h_gf = recent.get("home_gf_pm", 0)
    h_ga = recent.get("home_ga_pm", 0)
    a_gf = recent.get("away_gf_pm", 0)
    a_ga = recent.get("away_ga_pm", 0)
    h_n  = recent.get("home_n", 0)
    a_n  = recent.get("away_n", 0)

    if is_home and h_n >= 3:
        return {"gf": h_gf, "ga": h_ga, "n": h_n, "src": "domicile"}
    elif not is_home and a_n >= 3:
        return {"gf": a_gf, "ga": a_ga, "n": a_n, "src": "extérieur"}
    else:
        return {"gf": recent.get("goals_for_pm",0), "ga": recent.get("goals_ag_pm",0),
                "n": recent.get("n_matches",0), "src": "L10"}

def cup_context(recent, cup_league=False):
    """
    Pour les matchs européens, retourne les stats en coupe si disponibles.
    Minimum 5 matchs pour être statistiquement fiable.
    """
    if not recent or not cup_league:
        return {}
    cup_gf = recent.get("cup_gf_pm", 0)
    cup_n  = recent.get("cup_n", 0)
    if cup_n >= 5:  # minimum 5 matchs en coupe
        return {"gf": cup_gf, "ga": recent.get("cup_ga_pm",0), "n": cup_n}
    return {}

def knockout_pressure_bonus(league_id, form):
    """
    En phase KO (demi, quart...) les équipes à domicile ont un bonus de motivation.
    Retourne un bonus 0-10% sur les probabilités offensives.
    """
    if not is_cup_league(league_id):
        return 0
    # En méforme globale mais match KO à domicile → boost motivation
    w_rate = form.count("W") / len(form) if form else 0
    # Même une équipe en méforme peut se surpasser en KO à domicile
    return 0.08  # +8% boost motivation KO

def opponent_quality_adjust(form_details):
    """
    Ajuste la valeur de la forme selon la qualité des adversaires récents.
    Si les victoires sont contre des équipes faibles → dévalue la série.
    Utilise les match_details pour détecter les adversaires.
    """
    # Pas implémentable sans classement adversaires dans details
    # → retourne 1.0 (neutre) pour l'instant
    return 1.0

def poisson_at_least(lam, k):
    """P(X >= k) pour X ~ Poisson(lam) — probabilité réelle."""
    if lam <= 0: return 0
    prob_less = sum(math.exp(-lam) * lam**i / math.factorial(i) for i in range(k))
    return round((1 - prob_less) * 100, 1)

def goals_analysis(recent, season_ts, team_name):
    """
    Retourne un dict d'analyse des buts pour un texte de reasoning.
    Combine forme récente et stats saison.
    """
    r_gf    = recent.get("goals_for_pm", 0)   if recent else 0
    r_ga    = recent.get("goals_ag_pm", 0)    if recent else 0
    r_tot   = recent.get("total_goals_pm", 0) if recent else 0
    r_btts  = recent.get("btts_rate", 0)      if recent else 0
    r_btts_c= recent.get("btts_count", 0)     if recent else 0
    r_n     = recent.get("btts_n", 0)         if recent else 0
    s_gf    = (season_ts or {}).get("goals_pm", 0)
    s_ga    = (season_ts or {}).get("conceded_pm", 0)
    return {
        "gf": r_gf or s_gf, "ga": r_ga or s_ga,
        "total": r_tot or (r_gf+r_ga) or (s_gf+s_ga),
        "btts_rate": r_btts, "btts_count": r_btts_c, "btts_n": r_n,
        "has_recent": bool(r_gf),
        "src": "L10" if r_gf else "saison",
        "s_gf": s_gf, "s_ga": s_ga,
    }

def frac2dec(s):
    try:
        if "/" in str(s):
            n, d = str(s).split("/")
            return round(int(n)/int(d)+1, 2)
        return round(float(s)+1, 2)
    except: return None

def get_mkt(odds, name, cg=None):
    try:
        for m in odds.get("markets", []):
            if m.get("marketName") == name:
                if cg is None or str(m.get("choiceGroup","")) == str(cg):
                    return m
    except: pass
    return None

def get_odds(mkt, choice):
    if not mkt: return None
    for c in mkt.get("choices", []):
        if c.get("name") == choice:
            # Format Sofascore : fractionalValue (str "5/4")
            fv = c.get("fractionalValue")
            if fv:
                d = frac2dec(fv)
                if d: return d
            # Format Odds API fallback (foot_wc_odds) : cote décimale directe
            cote = c.get("cote")
            if cote:
                try: return float(cote)
                except Exception: pass
            # Fallback : side-based matching (utile pour 1X2 où name = home_team_name)
            if isinstance(c.get("name"), str) and choice == c.get("side"):
                cote = c.get("cote")
                if cote:
                    try: return float(cote)
                    except Exception: pass
    # Pour 1X2 via foot_wc_odds : choices.name = "France"/"Draw"/"Senegal"
    # mais get_odds est appelé avec choice="1"/"X"/"2". On match via side.
    if mkt and choice in ("1", "X", "2"):
        side_map = {"1": "home", "X": "draw", "2": "away"}
        target_side = side_map[choice]
        for c in mkt.get("choices", []):
            if c.get("side") == target_side:
                cote = c.get("cote")
                if cote:
                    try: return float(cote)
                    except Exception: pass
    return None

def prob(cote):
    return round(100/cote, 1) if cote and cote > 1 else 0

def form_summary(f):
    """Résumé lisible de la forme : ex '4V 1N 0D sur 5 matchs'"""
    if not f: return ""
    w, dn, l = f.count("W"), f.count("D"), f.count("L")
    return f"{w}V/{dn}N/{l}D sur {len(f)} matchs"

def recent_form_score(f, n=5):
    """Score 0-100 basé sur les N derniers matchs (forme récente)."""
    if not f: return 50
    recent = f[-n:]
    return round((recent.count("W") * 3 + recent.count("D")) / (len(recent) * 3) * 100)

def form_trend(f):
    """Compare les 3 derniers vs les 3 précédents pour détecter une tendance."""
    if len(f) < 6: return "stable"
    recent = f[-3:]
    older  = f[-6:-3]
    r_score = recent.count("W") * 3 + recent.count("D")
    o_score = older.count("W") * 3 + older.count("D")
    if r_score > o_score + 2: return "en grande forme"
    if r_score > o_score:     return "en hausse"
    if r_score < o_score - 2: return "en méforme"
    if r_score < o_score:     return "en légère baisse"
    return "stable"

# ─── Scoring contextuel défense adverse ─────────────────────────────────────

def defense_weakness(opp_pos, opp_rat, opp_conceded_pm=0):
    """Score 0-1 de faiblesse défensive adverse."""
    pos_s  = min(1.0, opp_pos / 20)
    rat_s  = max(0, (7.5 - opp_rat) / 2.0)
    conc_s = min(1.0, opp_conceded_pm / 3.0) if opp_conceded_pm else 0
    return round(pos_s * 0.4 + rat_s * 0.3 + conc_s * 0.3, 3)

def defense_label(w):
    if w >= 0.7: return "défense très vulnérable"
    if w >= 0.5: return "défense fragile"
    if w >= 0.3: return "défense correcte"
    return "défense solide"

# ─── Props joueurs ───────────────────────────────────────────────────────────

def _lookup_player_cote(odds_for_player, market_key):
    """Helper : retourne (cote, book, all_books_list) pour le marche demande."""
    if not odds_for_player: return None, None, []
    m = odds_for_player.get(market_key)
    if not m: return None, None, []
    cote = m.get("over")  # "Yes" pour anytime, "Over X.5" pour assists/shots
    book = m.get("book")
    books_list = [b for b in (m.get("books") or []) if b.get("side") == "over"]
    return cote, book, books_list


def _match_odds_name(target, odds_keys):
    """Match fuzzy d'un nom de joueur entre stats scraper et The Odds API."""
    import unicodedata
    def _clean(s):
        s = unicodedata.normalize("NFD", s or "")
        return "".join(c for c in s if unicodedata.category(c) != "Mn").lower().strip()
    t = _clean(target)
    if not t or not odds_keys: return None
    # 1) exact
    for k in odds_keys:
        if _clean(k) == t: return k
    # 2) substring (utile pour "Ollie Watkins" vs "Watkins")
    for k in odds_keys:
        kc = _clean(k)
        if t in kc or kc in t: return k
    # 3) lastname
    t_last = t.split()[-1] if t else ""
    if t_last:
        for k in odds_keys:
            k_last = _clean(k).split()[-1]
            if k_last and k_last == t_last: return k
    return None


def player_picks_contextual(players, opp_pos, opp_rat, opp_conceded_pm=0, btts_prob=50, min_apps=5, match_odds=None):
    """
    Analyse contextuelle enrichie avec forme récente joueur.
    Forme récente (xG, buts récents) prime sur les stats saison brutes.

    match_odds : dict {player_name: {market_key: {over, under, book, books, line}}}
                 issu de foot_odds.json pour ce match. Permet d'attacher la
                 VRAIE cote bookmaker aux picks (au lieu de cote=None).
    """
    if not players: return []
    _buteur_data_local = []  # capture proba calibree par joueur (pour DC Buteur)

    weakness  = defense_weakness(opp_pos, opp_rat, opp_conceded_pm)
    def_label = defense_label(weakness)
    picks     = []
    odds_keys = list((match_odds or {}).keys())

    for p in players:
        apps   = p.get("appearances", 0)
        is_sub = p.get("is_sub", False)
        if apps < min_apps: continue

        name    = p.get("shortName", p.get("name",""))
        pos     = p.get("position","")
        goals   = p.get("goals", 0)
        assists = p.get("assists", 0)
        shots   = p.get("shots", 0)
        gpm     = p.get("goals_pm", 0)
        apm     = p.get("assists_pm", 0)
        gapm    = p.get("g_a_pm", 0)
        xgpm    = p.get("xG_pm", 0)
        xapm    = p.get("xA_pm", 0)
        minutes = p.get("minutes", 0)

        sub_penalty = 0.75 if is_sub else 1.0
        sub_tag     = " ⚠️ peut-être pas titulaire" if is_sub else ""
        pos_mult    = {"F": 1.3, "M": 1.0, "D": 0.55}.get(pos, 0.8)
        ctx_bonus   = weakness * 0.35

        # ── Analyse forme récente buteur ────────────────────────────
        # Efficacité sur la saison
        shot_eff = goals / shots if shots > 0 else 0
        xg_total = xgpm * apps
        # Sur ou sous-performance par rapport au xG
        xg_conv_ratio = (goals / xg_total) if xg_total > 0.5 else 1.0

        # Contexte buts concédés adversaire
        opp_conc_txt = ""
        if opp_conceded_pm >= 1.8:
            opp_conc_txt = f", face à une défense qui concède {opp_conceded_pm:.1f} buts/match"
        elif opp_conceded_pm >= 1.3:
            opp_conc_txt = f", défense adverse poreuse ({opp_conceded_pm:.1f} buts concédés/match)"

        # ── Buteur anytime ──────────────────────────────────────────
        if gpm >= 0.15:
            # Calibration Poisson : P(marque) = 1 - e^(-lambda)
            lam_base  = (xgpm + gpm) / 2 if xgpm > 0 else gpm
            p_base    = poisson_at_least(lam_base, 1)

            # Tags efficacité
            if xg_conv_ratio > 1.2:
                eff_tag = f" · finisseur efficace ({round(shot_eff*100)}% de conv.)"
            elif xg_conv_ratio < 0.7 and goals > 3:
                eff_tag = f" · sous-performe son xG (attention)"
            elif shot_eff >= 0.18:
                eff_tag = f" · bonne efficacité ({round(shot_eff*100)}% conv.)"
            else:
                eff_tag = ""

            # Ajustement contextuel — plafond réaliste 68%
            ctx_mult = 1 + ctx_bonus * 0.30
            ctx_mult *= {"F":1.15,"M":1.0,"D":0.7}.get(pos, 0.9)
            ctx_mult *= sub_penalty
            # Contexte sur lambda : faiblesse défense + buts concédés adversaire
            # Plus la défense est mauvaise, plus le lambda augmente
            adj = 1 + ctx_bonus * 0.50 + (opp_conceded_pm / 10 if opp_conceded_pm else 0)
            lam_ctx  = lam_base * adj
            lam_ctx *= {"F":1.15,"M":1.0,"D":0.7}.get(pos, 0.9)
            lam_ctx *= sub_penalty
            ctx_conf = round(poisson_at_least(lam_ctx, 1))

            # Reasoning explicatif
            reasoning = (
                f"{goals} buts en {apps} matchs cette saison ({round(gpm*100,1)}% de chance/match)"
                f"{eff_tag} · xG moyen {xgpm:.2f}/match"
                f"{opp_conc_txt} · {def_label}{sub_tag}"
            )

            # Buteur : analyse historique (34 picks, WR 35%, ROI -25%, cote moy 2.96)
            # -> algo emet trop large. On exige conf >= 50% (au lieu de 35%) ET
            #    cote bookmaker >= 2.4 si dispo (sinon on garde sur conf seule).
            #    Plus shrinkage pour aligner predicted vs observed.
            ctx_conf_cal = _foot_calibrate(ctx_conf)
            # Capture proba calibree par joueur (utilise par DC Buteur en aval)
            _buteur_data_local.append({
                "player":     name,
                "position":   pos,
                "is_sub":     is_sub,
                "conf_cal":   ctx_conf_cal if ctx_conf_cal is not None else 0,
                "raw_conf":   ctx_conf,
                "gpm":        gpm, "xgpm": xgpm, "goals": goals, "apps": apps,
                "reasoning":  reasoning,
            })
            if ctx_conf_cal is not None and ctx_conf_cal >= 50:
                # Recherche cote reelle "anytime scorer"
                odds_key = _match_odds_name(name, odds_keys)
                player_odds = (match_odds or {}).get(odds_key) if odds_key else None
                cote, book, books_list = _lookup_player_cote(player_odds, "anytime_scorer")
                # Filtre cote : si on connait la cote book ET qu'elle est < 2.4,
                # le profit attendu est trop maigre vu notre WR reel observe (35%).
                if cote is not None and cote < 2.4:
                    pass  # skip pick
                else:
                    picks.append({
                        "player": name, "position": pos, "is_sub": is_sub,
                        "type": "Buteur", "label": f"{name} marque",
                        "cote": cote, "book": book, "books": books_list,
                        "confidence": round(ctx_conf_cal),
                        "reasoning": reasoning,
                        "context": {"weakness": weakness},
                        "stats": {"goals": goals, "apps": apps, "gpm": gpm, "xgpm": xgpm}
                    })

        # ── Passeur (passe décisive uniquement) ─────────────────────
        # Règle user : on distingue clairement Buteur vs Passeur (2 paris
        # bien différents). Le pari "but OU passe" (Joueur décisif) est
        # supprimé car il mélangeait les 2 et n'avait pas de marché bookmaker
        # dédié. Maintenant : "Passeur" = anytime assist UNIQUEMENT (cote
        # book = market "player_assists").
        if apm >= 0.15:
            lam_ass  = (xapm + apm) / 2 if xapm > 0 else apm
            lam_ass_ctx = lam_ass * (1 + weakness * 0.15) * sub_penalty
            ctx_conf    = round(poisson_at_least(lam_ass_ctx, 1))
            if ctx_conf >= 30:
                odds_key = _match_odds_name(name, odds_keys)
                player_odds = (match_odds or {}).get(odds_key) if odds_key else None
                cote, book, books_list = _lookup_player_cote(player_odds, "assists")
                picks.append({
                    "player": name, "position": pos, "is_sub": is_sub,
                    "type": "Passeur", "label": f"{name} délivre une passe décisive",
                    "cote": cote, "book": book, "books": books_list,
                    "confidence": ctx_conf,
                    "reasoning": (f"{assists} passes décisives en {apps} matchs ({round(apm*100,1)}% de chance/match)"
                                  f" · xA moyen {xapm:.2f}/match · {def_label}{sub_tag}"),
                    "context": {"weakness": weakness},
                    "stats": {"assists": assists, "apps": apps, "apm": apm, "xapm": xapm}
                })

    # Trier par confiance
    picks.sort(key=lambda x: x["confidence"], reverse=True)

    # ── Sélection : on autorise Buteur ET Passeur sur le même joueur ─────
    # (règle user : "soit l'un soit l'autre soit les 2 si vraiment tu penses
    # que le joueur peut marquer ET faire une passe"). On filtre juste sur
    # la confidence minimum par type pour ne garder que les paris à valeur.
    from collections import defaultdict
    by_player = defaultdict(list)
    for pk in picks:
        by_player[pk.get("player", "")].append(pk)

    final = []
    for pname, ppicks in by_player.items():
        if not pname:
            final.extend(ppicks)
            continue
        types = {pk["type"]: pk for pk in ppicks}
        buteur  = types.get("Buteur")
        passeur = types.get("Passeur")
        # Buteur : conf >= 50% pour être proposé
        if buteur and buteur.get("confidence", 0) >= 50:
            final.append(buteur)
        # Passeur : conf >= 45% pour être proposé (indépendamment du Buteur)
        if passeur and passeur.get("confidence", 0) >= 45:
            final.append(passeur)

    final.sort(key=lambda x: x["confidence"], reverse=True)

    # ── Double Chance Buteur : appairer 2 joueurs (l'un OU l'autre marque) ──
    # Market populaire (Betclic/Bwin) avec cotes interessantes (1.40-1.80 pour
    # 2 top buteurs meme equipe). P(A ou B) = 1 - (1-pA)(1-pB) avec independance
    # approximee (legere sous-estimation due a la correlation positive).
    # On utilise _buteur_data_local (proba calibree par joueur, capturee meme si
    # individuellement sous le seuil d'emit).
    if len(_buteur_data_local) >= 2:
        _buteur_data_local.sort(key=lambda x: x.get("conf_cal", 0), reverse=True)
        a, b = _buteur_data_local[0], _buteur_data_local[1]
        pa = float(a.get("conf_cal", 0)) / 100.0
        pb = float(b.get("conf_cal", 0)) / 100.0
        p_or = 1 - (1 - pa) * (1 - pb)
        conf_combined = round(p_or * 100)
        # Seuil 65% : correspond cote min ~1.54 (cote book typique 1.50-1.80)
        # Hard cap conf à 70% : au-dessus, cote_min = 1/0.70 = 1.43 (limite
        # acceptable). Si 2 joueurs à 80%+ chacun → on plafonne pour ne pas
        # afficher des cotes ridicules (1.11) et respecter la règle utilisateur
        # "minimum 1.40 sur picks buteur".
        if conf_combined > 70:
            conf_combined = 70
            p_or = 0.70
        should_emit_dc = False
        label = None
        cote_min = None
        if conf_combined >= 65:
            cote_min = _fair_cote(conf_combined)
            # Skip si cote_min < 1.40 (safety net, ne devrait pas arriver vu
            # le cap conf à 70% au-dessus mais on garde la garde explicite)
            if cote_min is not None and cote_min >= 1.40:
                label = f"{a['player']} ou {b['player']} marque"
                should_emit_dc = True
        if should_emit_dc:
            reasoning = (
                f"📈 P({a['player']} marque) ≈ {round(pa*100)}% · "
                f"P({b['player']} marque) ≈ {round(pb*100)}%\n"
                f"🎯 P(au moins un marque) = {conf_combined}% (calculée par 1 - (1-pA)(1-pB))\n"
                f"💎 Cote min équilibre = {cote_min} - chercher ≥{cote_min} chez le bookmaker"
            )
            final.append({
                "player":      f"{a['player']} / {b['player']}",
                "position":    "",
                "is_sub":      False,
                "type":        "Double Chance Buteur",
                "label":       label,
                "cote":        None,
                "book":        None,
                "books":       [],
                "confidence":  conf_combined,
                "cote_min":    cote_min,
                "reasoning":   reasoning,
                "context":     {},
                "stats":       {
                    "p_a":      round(pa, 3),
                    "p_b":      round(pb, 3),
                    "p_or":     round(p_or, 3),
                    "player_a": a["player"],
                    "player_b": b["player"],
                },
            })

    return final

# ─── Analyse équipe ──────────────────────────────────────────────────────────

def analyze_match(match, pstats_all, player_odds_all=None):
    form = match.get("pre_match_form") or {}
    h2h  = match.get("h2h") or {}
    odds = match.get("match_odds") or {}
    home = match["home"]
    away = match["away"]
    mid  = str(match["id"])
    # Odds joueurs reelles pour CE match (anytime_scorer, assists, shots_on_target).
    match_player_odds = (player_odds_all or {}).get(mid, {})

    hf = get_form(form,"homeTeam"); af = get_form(form,"awayTeam")
    hp = get_pos(form,"homeTeam");  ap = get_pos(form,"awayTeam")
    hr = get_rat(form,"homeTeam");  ar = get_rat(form,"awayTeam")
    hw, dn, aw = parse_h2h(h2h); h2ht = hw+dn+aw

    # Forme récente (score 0-100 basé sur les 5 derniers)
    h_form_score = recent_form_score(hf)
    a_form_score = recent_form_score(af)
    h_trend = form_trend(hf)
    a_trend = form_trend(af)

    # Stats récentes et saison (disponibles si scraper_players_today a tourné)
    pstats       = pstats_all.get(mid, {})
    home_ts_data = pstats.get("home_team_stats", {})
    away_ts_data = pstats.get("away_team_stats", {})
    home_rec     = pstats.get("home_recent", {})
    away_rec     = pstats.get("away_recent", {})
    h2h_shots_d  = pstats.get("h2h_shots", {})

    h_goals = goals_analysis(home_rec, home_ts_data, home)
    a_goals = goals_analysis(away_rec, away_ts_data, away)

    # ── Contexte domicile/extérieur ──────────────────────────────────────────
    league_id   = match.get("league_id", 0) or 0
    is_cup      = is_cup_league(league_id)
    h_ha_ctx    = home_away_context(home_rec, is_home=True)   # stats dom de l'équipe dom
    a_ha_ctx    = home_away_context(away_rec, is_home=False)  # stats ext de l'équipe ext
    h_cup_ctx   = cup_context(home_rec, is_cup)
    a_cup_ctx   = cup_context(away_rec, is_cup)
    ko_bonus_h  = knockout_pressure_bonus(league_id, hf) if hf else 0
    ko_bonus_a  = knockout_pressure_bonus(league_id, af) if af else 0

    # Buts attendus avec contexte home/away (plus précis que la moyenne globale)
    h_gf_ctx = h_cup_ctx.get("gf") or h_ha_ctx.get("gf") or h_goals.get("gf", 0)
    a_gf_ctx = a_cup_ctx.get("gf") or a_ha_ctx.get("gf") or a_goals.get("gf", 0)
    h_ga_ctx = h_cup_ctx.get("ga") or h_ha_ctx.get("ga") or h_goals.get("ga", 0)
    a_ga_ctx = a_cup_ctx.get("ga") or a_ha_ctx.get("ga") or a_goals.get("ga", 0)
    h_gf_src = h_cup_ctx.get("n","") and "LDC" or h_ha_ctx.get("src","L10")
    a_gf_src = a_cup_ctx.get("n","") and "LDC" or a_ha_ctx.get("src","L10")

    ft  = get_mkt(odds,"Full time")
    dc  = get_mkt(odds,"Double chance")
    btt = get_mkt(odds,"Both teams to score")
    fts = get_mkt(odds,"First team to score")
    crn = get_mkt(odds,"Corners 2-Way","9.5")
    crd = get_mkt(odds,"Cards in match","3.5")
    dnb = get_mkt(odds,"Draw no bet")

    c1=get_odds(ft,"1"); c2=get_odds(ft,"2"); cx=get_odds(ft,"X")
    c1x=get_odds(dc,"1X"); cx2=get_odds(dc,"X2")
    cy=get_odds(btt,"Yes"); cn=get_odds(btt,"No")
    btts_p = prob(cy)

    candidates = []

    def _classify_tier(conf, cote=None, is_fun=False):
        """Classifie un pick en safe / ok / fun.

        Regles :
        - 'safe' : conf >= 70 et cote <= 1.85 (forte proba, cote raisonnable)
        - 'fun'  : is_fun=True OU conf < 55 OU cote >= 2.6 (long shot)
        - 'ok'   : entre les deux (le ventre mou - value picks)
        """
        if is_fun:
            return "fun"
        if conf is None:
            return "ok"
        if conf >= 70 and (cote is None or cote <= 1.85):
            return "safe"
        if conf < 55 or (cote is not None and cote >= 2.6):
            return "fun"
        return "ok"

    def add(direction, type_, label, cote, conf, reasoning, form_data=None, is_fun=False):
        candidates.append({
            "direction": direction, "type": type_, "label": label,
            "cote": cote, "confidence": conf, "reasoning": reasoning,
            "stats": {"form": form_data or []},
            "is_fun": is_fun,
            "tier": _classify_tier(conf, cote, is_fun),
        })

    # ── 1X2 — forme récente pondérée davantage que H2H ────────────────────
    # Poids : forme récente 55%, classement 20%, H2H 15%, rating 10%
    # Pour les amicaux internationaux (league_id 114) : on accepte quand on a
    # un L5 complet (>=4 matchs) des 2 cotes - le team endpoint fallback fournit
    # le L5 TCC (toutes competitions confondues). Skip uniquement quand L5 trop
    # courte (Gibraltar vs BVI : peu de matchs records).
    IS_INTL_FRIENDLY = (league_id == 114)
    h_opp_ranks_pre = get_form_opp_ranks(form, "homeTeam")
    a_opp_ranks_pre = get_form_opp_ranks(form, "awayTeam")
    h_sos_ok = sum(1 for r in h_opp_ranks_pre if r is not None) >= 3
    a_sos_ok = sum(1 for r in a_opp_ranks_pre if r is not None) >= 3
    h_l5_ok = len(hf) >= 4
    a_l5_ok = len(af) >= 4

    # ── Filtre amicaux "petites equipes" ─────────────────────────────────
    # Pour les Friendlies (league_id 114), au moins UNE des 2 equipes doit
    # etre une grosse selection internationale (top 35 FIFA). Sinon on skip
    # tous les picks - les matchs comme Ouganda vs Madagascar generent du
    # bruit (data sparse, signal faible).
    HAS_TOP_TEAM = (not IS_INTL_FRIENDLY) or is_top_intl_team(home) or is_top_intl_team(away)
    if IS_INTL_FRIENDLY and not HAS_TOP_TEAM:
        # Pas de pick, on retourne tot pour ces matchs sans interet
        return [], [], [], []

    if IS_INTL_FRIENDLY and h_l5_ok and a_l5_ok:
        # ── Amicaux : strategie multi-marche basee sur la forme + buts L5 ─
        # Plutot que des DC sur favoris ecrasants (cote 1.10-1.15 = ridicule),
        # on propose des picks plus interessants :
        #   - 1X2 si asymetrie nette (delta form >= 18)
        #   - "Favori marque 1.5+" sur asymetrie + attaque solide (Poisson)
        #   - Over/Under 2.5 selon attendu Poisson total
        import math as _math
        # Delta forme L5 brut + bonus tier (l'ecart de niveau FIFA pondere).
        # Sans bonus tier, Pays-Bas vs Uzbekistan donnait delta<0 (Uzb a + W
        # face a equipes faibles) -> aucun pick. Avec bonus tier (+15/tier),
        # le delta reflete vraiment l'asymetrie de niveau.
        delta_raw = h_form_score - a_form_score
        t_h = team_tier(home); t_a = team_tier(away)
        tier_bonus = (t_a - t_h) * 15  # positif si home meilleur tier
        delta = delta_raw + tier_bonus
        h_team = form.get("homeTeam") or {}
        a_team = form.get("awayTeam") or {}
        h_gf = h_team.get("l5_gf_pm")
        h_ga = h_team.get("l5_ga_pm")
        a_gf = a_team.get("l5_gf_pm")
        a_ga = a_team.get("l5_ga_pm")

        tier_descr = ""
        if t_h != t_a:
            tier_descr = f" · ecart de tier FIFA {t_h}vs{t_a}"

        if delta >= 18:
            conf = round(min(85, 55 + delta * 0.6))
            add("home_win","Forme superieure",f"{home} gagne",c1,conf,
                f"{home} : {form_summary(hf)} vs {away} : {form_summary(af)} "
                f"(L5 TCC{tier_descr})",hf)
        elif delta <= -18:
            conf = round(min(85, 55 + abs(delta) * 0.6))
            add("away_win","Forme superieure",f"{away} gagne",c2,conf,
                f"{away} : {form_summary(af)} vs {home} : {form_summary(hf)} "
                f"(L5 TCC{tier_descr})",af)

        if h_gf is not None and h_ga is not None and a_gf is not None and a_ga is not None:
            # Ajustement SoS sur les buts via les tiers FIFA
            h_atk, h_def = tier_strength_adjustment(home, away)
            a_atk, a_def = tier_strength_adjustment(away, home)
            adj_h_gf = h_gf * h_atk
            adj_h_ga = h_ga * h_def
            adj_a_gf = a_gf * a_atk
            adj_a_ga = a_ga * a_def
            lambda_h = max(0.2, (adj_h_gf + adj_a_ga) / 2)
            lambda_a = max(0.2, (adj_a_gf + adj_h_ga) / 2)
            lambda_total = lambda_h + lambda_a

            def _p_at_least_2(lam):
                return 1 - _math.exp(-lam) * (1 + lam)

            # "Favori marque 1.5+" pour asymetrie + attaque forte
            if delta >= 8 and lambda_h >= 1.3:
                p2 = _p_at_least_2(lambda_h)
                conf = round(min(78, p2 * 100 + 5))
                if conf >= 52:
                    add("home_over_15", "Buteur equipe",
                        f"{home} marque plus de 1.5 buts", None, conf,
                        f"{home} attendu a ~{lambda_h:.1f} buts "
                        f"(marque {h_gf:.1f}/m sur L5, {away} encaisse {a_ga:.1f}/m) - "
                        f"P(2+ buts) ~{round(p2*100)}%", hf)
            if delta <= -8 and lambda_a >= 1.3:
                p2 = _p_at_least_2(lambda_a)
                conf = round(min(78, p2 * 100 + 5))
                if conf >= 52:
                    add("away_over_15", "Buteur equipe",
                        f"{away} marque plus de 1.5 buts", None, conf,
                        f"{away} attendu a ~{lambda_a:.1f} buts "
                        f"(marque {a_gf:.1f}/m sur L5, {home} encaisse {h_ga:.1f}/m) - "
                        f"P(2+ buts) ~{round(p2*100)}%", af)

            # Over/Under 2.5 buts si l'attendu total est franchement loin de 2.5
            p0 = _math.exp(-lambda_total)
            p1 = p0 * lambda_total
            p2t = p1 * lambda_total / 2
            p_over25 = 1 - p0 - p1 - p2t
            p_under25 = 1 - p_over25
            if p_over25 >= 0.58:
                conf = round(min(78, p_over25 * 100))
                add("over25", "Total buts",
                    f"Plus de 2.5 buts", None, conf,
                    f"Attendu {lambda_total:.1f} buts au total "
                    f"({home} ~{lambda_h:.1f}, {away} ~{lambda_a:.1f}) - "
                    f"P(>2.5) ~{round(p_over25*100)}%", hf)
            elif p_under25 >= 0.58:
                conf = round(min(78, p_under25 * 100))
                add("under25", "Total buts",
                    f"Moins de 2.5 buts", None, conf,
                    f"Attendu seulement {lambda_total:.1f} buts au total "
                    f"({home} ~{lambda_h:.1f}, {away} ~{lambda_a:.1f}) - "
                    f"P(<2.5) ~{round(p_under25*100)}%", hf)

            # BTTS Oui : les 2 attaques ~1.0+ buts attendus
            p_h_score = 1 - _math.exp(-lambda_h)
            p_a_score = 1 - _math.exp(-lambda_a)
            p_btts_yes = p_h_score * p_a_score
            p_btts_no = 1 - p_btts_yes
            # On n'ajoute pas si on a deja un Over (les 2 sont correles)
            already_over = any(c.get("direction") == "over25" for c in candidates)
            already_under = any(c.get("direction") == "under25" for c in candidates)
            if not already_over and p_btts_yes >= 0.58 and lambda_h >= 1.0 and lambda_a >= 1.0:
                conf = round(min(75, p_btts_yes * 100))
                add("btts_yes", "BTTS",
                    f"Les 2 equipes marquent", None, conf,
                    f"P({home} marque) {round(p_h_score*100)}% x P({away} marque) {round(p_a_score*100)}% "
                    f"= ~{round(p_btts_yes*100)}%", hf)
            elif not already_under and p_btts_no >= 0.62:
                conf = round(min(75, p_btts_no * 100))
                add("btts_no", "BTTS",
                    f"Au moins 1 equipe ne marque pas", None, conf,
                    f"Attaque faible d'un cote : {home} ~{lambda_h:.1f} buts, {away} ~{lambda_a:.1f} buts - "
                    f"P(BTTS Non) ~{round(p_btts_no*100)}%", hf)
    else:
        # ── 1X2 standard : forme + classement + H2H + rating Sofa ─────────────
        if hf:
            form_c = h_form_score * 0.55
            pos_c  = max(0, (20-hp)/20*20) if hp < 20 else 0
            h2h_c  = (hw/h2ht*15) if h2ht else 7
            rat_c  = min(10, (hr-6.5)*15) if hr > 6.5 else 0
            conf   = round(min(94, form_c + pos_c + h2h_c + rat_c))
            if conf >= 58:
                trend_txt = f" ({h_trend})" if h_trend != "stable" else ""
                add("home_win","Victoire domicile",f"{home} gagne",c1,conf,
                    f"{home} : {form_summary(hf)}{trend_txt} · #{hp} au classement · "
                    f"H2H: {hw}V/{dn}N/{aw}D · Note Sofa: {hr:.1f}/10",hf)

        if af:
            form_c = a_form_score * 0.55
            pos_c  = max(0, (20-ap)/20*20) if ap < 20 else 0
            h2h_c  = (aw/h2ht*15) if h2ht else 7
            rat_c  = min(10, (ar-6.5)*15) if ar > 6.5 else 0
            conf   = round(min(94, form_c + pos_c + h2h_c + rat_c))
            if conf >= 58:
                trend_txt = f" ({a_trend})" if a_trend != "stable" else ""
                add("away_win","Victoire extérieur",f"{away} gagne",c2,conf,
                    f"{away} : {form_summary(af)}{trend_txt} · #{ap} au classement · "
                    f"H2H: {aw}V/{dn}N/{hw}D · Note Sofa: {ar:.1f}/10",af)

    # ── Favori net ─────────────────────────────────────────────────────────
    # Skip "Favori net" pour amicaux internationaux (pas de classement fiable)
    if hf and af and league_id != 114:
        pd = ap - hp
        if abs(pd) >= 6:
            fav    = home if pd > 0 else away
            fav_f  = hf if pd > 0 else af
            fav_pos= hp if pd > 0 else ap
            fav_rat= hr if pd > 0 else ar
            und_pos= ap if pd > 0 else hp
            cote_f = c1 if pd > 0 else c2
            dir_   = "home_win" if pd > 0 else "away_win"
            fav_trend = h_trend if pd > 0 else a_trend

            conf = round(min(90, 50 + abs(pd)*1.4 + max(0,fav_rat-6.5)*6))
            if conf >= 65:
                trend_txt = f", {fav_trend}" if fav_trend != "stable" else ""
                ex = next((c for c in candidates if c["direction"]==dir_), None)
                if not ex or ex["confidence"] < conf:
                    if ex: candidates.remove(ex)
                    add(dir_,"Favori net",f"{fav} gagne",cote_f,conf,
                        f"{abs(pd)} places d'écart au classement (#{fav_pos} vs #{und_pos})"
                        f"{trend_txt} · {form_summary(fav_f)} · Note Sofa: {fav_rat:.1f}/10",fav_f)

    # ── Double chance — une seule (la meilleure) ────────────────────────────
    # Analyse historique (20 picks, WR 65%, ROI -4.3%, cote moy 1.42, break-even 70.6%)
    # -> seuil 70 trop bas (cote serre, marginalement non rentable).
    # Nouveau seuil : conf >= 80 ET cote >= 1.45 pour assurer +EV.
    #
    # IMPORTANT : pour les amicaux internationaux (Friendlies = league_id 114),
    # le "L5 invaincu" est inutilisable :
    # - Adversaires de niveau tres heterogene (rang FIFA 30 vs 150)
    # - Petit echantillon (4-6 matchs sur 12 mois)
    # - Aucun classement disponible pour ponderer
    # On SKIP DC pour les amicaux internationaux.
    dc_cands = []
    # Strength-of-Schedule : pondere le L5 selon le rang des adversaires
    h_opp_ranks = get_form_opp_ranks(form, "homeTeam")
    a_opp_ranks = get_form_opp_ranks(form, "awayTeam")
    # Pour les amicaux : DC autorise si L5 >=4 matchs des 2 cotes (TCC).
    # Si SoS dispo on l'utilise en bonus (cf ub_sos plus bas).
    can_dc_h = (not IS_INTL_FRIENDLY) or (h_l5_ok and a_l5_ok)
    can_dc_a = (not IS_INTL_FRIENDLY) or (h_l5_ok and a_l5_ok)
    if can_dc_h and hf:
        ub_raw = unbeaten(hf)
        ub_sos = sos_unbeaten(hf, h_opp_ranks)
        # On utilise SoS si on a au moins 3 ranks valides, sinon unbeaten brut
        n_valid = sum(1 for r in h_opp_ranks if r is not None)
        ub = ub_sos if n_valid >= 3 else ub_raw
        h2h_ = (hw+dn)/h2ht if h2ht else 0.5
        rank_advantage_h = 0
        if hp and ap:
            rank_advantage_h = max(0, min(15, (ap - hp) * 1.0))
        conf = round(ub * 40 + h_form_score * 0.3 + h2h_ * 20 + rank_advantage_h * 1.5)
        conf_cal = _foot_calibrate(conf)
        if conf_cal is not None and conf_cal >= 80 and (c1x is None or c1x >= 1.45):
            trend_txt = f", {h_trend}" if h_trend not in ("stable","en légère baisse") else ""
            sos_tag = ""
            if n_valid >= 3 and abs(ub_sos - ub_raw) >= 0.05:
                sos_tag = " (pondéré qualité adv.)"
            reasoning = (
                f"{home} invaincu {round(ub*100)}%{sos_tag} sur {len(hf)} derniers matchs{trend_txt}"
                f" · H2H: {hw+dn}/{h2ht} matchs sans défaite"
            )
            if hp and ap:
                reasoning += f" · Classement: #{hp} vs #{ap}"
            dc_cands.append(("home_dc","Double chance",f"{home} ou Nul (1X)",c1x,round(conf_cal),
                reasoning, hf))
    if can_dc_a and af:
        ub_raw = unbeaten(af)
        ub_sos = sos_unbeaten(af, a_opp_ranks)
        n_valid = sum(1 for r in a_opp_ranks if r is not None)
        ub = ub_sos if n_valid >= 3 else ub_raw
        h2h_ = (aw+dn)/h2ht if h2ht else 0.5
        rank_advantage_a = 0
        if hp and ap:
            rank_advantage_a = max(0, min(15, (hp - ap) * 1.0))
        conf = round(ub * 40 + a_form_score * 0.3 + h2h_ * 20 + rank_advantage_a * 1.5)
        conf_cal = _foot_calibrate(conf)
        if conf_cal is not None and conf_cal >= 80 and (cx2 is None or cx2 >= 1.45):
            trend_txt = f", {a_trend}" if a_trend not in ("stable","en légère baisse") else ""
            sos_tag = ""
            if n_valid >= 3 and abs(ub_sos - ub_raw) >= 0.05:
                sos_tag = " (pondéré qualité adv.)"
            reasoning = (
                f"{away} invaincu {round(ub*100)}%{sos_tag} sur {len(af)} derniers matchs{trend_txt}"
                f" · H2H: {aw+dn}/{h2ht} matchs sans défaite"
            )
            if hp and ap:
                reasoning += f" · Classement: #{ap} (away) vs #{hp} (home)"
            dc_cands.append(("away_dc","Double chance",f"Nul ou {away} (X2)",cx2,round(conf_cal),
                reasoning, af))
    if dc_cands:
        add(*max(dc_cands, key=lambda x: x[4]))

    # ── BTTS — reasoning explicatif ─────────────────────────────────────────
    if btt:
        py, pn = prob(cy), prob(cn)

        import math as _math

        # Probabilité de scorer via Poisson + fréquence BTTS observée
        h_gf  = h_gf_ctx or h_goals.get("gf", 0)
        a_gf  = a_gf_ctx or a_goals.get("gf", 0)
        h_score_prob = round((1 - _math.exp(-h_gf)) * 100) if h_gf > 0 else None
        a_score_prob = round((1 - _math.exp(-a_gf)) * 100) if a_gf > 0 else None

        h_btts_c = home_rec.get("btts_count", None)
        h_btts_n = home_rec.get("btts_n", 0)
        a_btts_c = away_rec.get("btts_count", None)
        a_btts_n = away_rec.get("btts_n", 0)

        src_h = h_goals.get("src","saison")
        src_a = a_goals.get("src","saison")

        def btts_block(team_name, gf, ga, btts_c, btts_n, score_prob, src):
            """Génère le bloc d'analyse BTTS pour une équipe."""
            parts = []
            if gf:
                parts.append(f"{team_name}: {gf:.1f} buts marqués/{src}, {ga:.1f} concédés/{src}")
            if score_prob:
                parts.append(f"marque dans ~{score_prob}% des matchs")
            if btts_c is not None and btts_n > 0:
                rate = round(btts_c/btts_n*100)
                parts.append(f"BTTS {btts_c}/{btts_n} derniers matchs ({rate}%)")
            return " · ".join(parts)

        h_block = btts_block(home, h_gf, h_goals.get("ga",0), h_btts_c, h_btts_n, h_score_prob, src_h)
        a_block = btts_block(away, a_gf, a_goals.get("ga",0), a_btts_c, a_btts_n, a_score_prob, src_a)

        if py >= 55:
            reasoning_btts = " | ".join(filter(None, [h_block, a_block]))
            if not reasoning_btts:
                reasoning_btts = "Deux équipes offensives, BTTS probable"
            add("btts_yes","BTTS","Les deux équipes marquent",cy,min(90,round(py+3)),reasoning_btts)

        elif pn >= 60:
            # Identifie l'équipe la moins offensive
            weaker     = home if (h_gf or 0) < (a_gf or 0) else away
            weaker_blk = h_block if weaker == home else a_block
            stronger_blk = a_block if weaker == home else h_block
            reasoning_btts = (f"Risque que {weaker} ne marque pas | {weaker_blk} | {stronger_blk}")
            if not weaker_blk:
                reasoning_btts = f"L'une des deux équipes (#{hp} vs #{ap}) risque de ne pas marquer"
            add("btts_no","BTTS Non","Au moins une équipe ne marque pas",cn,min(88,round(pn+2)),reasoning_btts)

    # ── Over/Under buts — reasoning explicatif ─────────────────────────────
    for thr in ["1.5","2.5","3.5"]:
        mkt = get_mkt(odds,"Match goals",thr)
        if not mkt: continue
        co=get_odds(mkt,"Over"); cu=get_odds(mkt,"Under")
        po,pu=prob(co),prob(cu)
        min_p={"1.5":75,"2.5":60,"3.5":68}.get(thr,60)

        # ── Estimation buts via données réelles + Poisson ────────────────────────
        h_gf_s  = h_goals.get("s_gf", 0)
        a_gf_s  = a_goals.get("s_gf", 0)
        h_ga_s  = h_goals.get("s_ga", 0)
        a_ga_s  = a_goals.get("s_ga", 0)
        # Priorité : contexte home/away/cup > L10 > saison
        h_gf_r  = h_gf_ctx or home_rec.get("goals_for_pm", 0)
        a_gf_r  = a_gf_ctx or away_rec.get("goals_for_pm", 0)
        h_ga_r  = h_ga_ctx or home_rec.get("goals_ag_pm", 0)
        a_ga_r  = a_ga_ctx or away_rec.get("goals_ag_pm", 0)
        h_btts  = home_rec.get("btts_rate", 0) / 100 if home_rec.get("btts_n", 0) >= 5 else None
        a_btts  = away_rec.get("btts_rate", 0) / 100 if away_rec.get("btts_n", 0) >= 5 else None
        h_tot_r = home_rec.get("total_goals_pm", 0)
        a_tot_r = away_rec.get("total_goals_pm", 0)
        src_hr  = "L10" if h_gf_r else "saison"
        src_ar  = "L10" if a_gf_r else "saison"

        h_att = h_gf_r or h_gf_s
        a_def = a_ga_r or a_ga_s
        a_att = a_gf_r or a_gf_s
        h_def = h_ga_r or h_ga_s
        # Buts attendus : moyenne attaque/défense croisée
        h_exp = round((h_att + a_def) / 2, 2) if (h_att and a_def) else (h_att or 0)
        a_exp = round((a_att + h_def) / 2, 2) if (a_att and h_def) else (a_att or 0)
        total_exp = round(h_exp + a_exp, 1) if (h_exp or a_exp) else None

        # Probabilité Poisson depuis nos données (priorité sur cote bookmaker)
        thr_float = float(thr)
        k = int(thr_float) + 1  # nombre de buts nécessaires
        if total_exp and total_exp > 0:
            p_data = poisson_at_least(total_exp, k)
        else:
            p_data = None

        # Confiance = moyenne pondérée : données réelles 60% + bookmaker 40%
        if p_data is not None:
            conf_over  = round(p_data * 0.60 + prob(co) * 0.40)
            conf_under = round((100 - p_data) * 0.60 + prob(cu) * 0.40)
        else:
            conf_over  = round(prob(co))
            conf_under = round(prob(cu))

        # BTTS comme signal supplémentaire pour Over 2.5
        if thr == "2.5" and h_btts is not None and a_btts is not None:
            btts_signal = (h_btts + a_btts) / 2
            if btts_signal > 0.65:
                conf_over  = round(min(conf_over + 5, 92))
            elif btts_signal < 0.40:
                conf_under = round(min(conf_under + 5, 88))

        if conf_over >= 55 and co:
            if total_exp:
                cup_note = f" · contexte LDC ({h_gf_src}/{a_gf_src})" if is_cup else ""
                r = (
                    f"{home}: {h_att:.1f} buts/{src_hr} · défense {away} concède {a_def:.1f}/{src_ar} → ~{h_exp:.1f} attendus · "
                    f"{away}: {a_att:.1f} buts/{src_ar} · défense {home} concède {h_def:.1f}/{src_hr} → ~{a_exp:.1f} attendus · "
                    f"Total: ~{total_exp} buts (P={p_data}%{cup_note})"
                )
            else:
                r = f"Deux équipes offensives, match orienté vers les buts"
            add(f"over{thr.replace('.','')}", f"Over {thr} buts", f"Plus de {thr} buts", co, conf_over, r)

        elif conf_under >= 60 and cu and thr in ["2.5","3.5"]:
            if total_exp:
                r = (
                    f"Défenses solides — {home} concède {h_def:.1f}/{src_hr}, {away} concède {a_def:.1f}/{src_ar} · "
                    f"Total attendu: ~{total_exp} buts (P Under={100-p_data if p_data else '?'}%)"
                )
            else:
                r = f"{home} (#{hp}) et {away} (#{ap}) — défenses solides, match fermé attendu"
            add(f"under{thr.replace('.','')}", f"Under {thr} buts", f"Moins de {thr} buts", cu, conf_under, r)

    # ── Première équipe à marquer ───────────────────────────────────────────
    if fts and hf:
        c_home = get_odds(fts, home); c_away = get_odds(fts, away)
        ph = prob(c_home); pa = prob(c_away)

        # Stats offensives pour FTS
        h_off = h_gf_ctx or h_goals.get("gf", 0)
        a_off = a_gf_ctx or a_goals.get("gf", 0)
        h_def = h_ga_ctx or h_goals.get("ga", 0)
        a_def = a_ga_ctx or a_goals.get("ga", 0)

        if ph >= 52 and ph > pa:
            r = (
                f"{home} marque {h_off:.1f} buts/match ({h_gf_src}) · "
                f"défense {away} concède {a_def:.1f}/match · "
                f"cote bookmaker → {round(ph)}% de probabilité"
            )
            add("fts_home","1ère équipe à marquer",f"{home} marque en premier",c_home,
                min(80,round(ph+2)),r)
        elif pa >= 52 and pa > ph:
            r = (
                f"{away} marque {a_off:.1f} buts/match ({a_gf_src}) · "
                f"défense {home} concède {h_def:.1f}/match · "
                f"cote bookmaker → {round(pa)}% de probabilité"
            )
            add("fts_away","1ère équipe à marquer",f"{away} marque en premier",c_away,
                min(80,round(pa+2)),r)

    # ── Corners ────────────────────────────────────────────────────────────
    if crn:
        co=get_odds(crn,"Over"); cu=get_odds(crn,"Under")
        po,pu=prob(co),prob(cu)
        if po >= 60:
            add("corners_over","Corners","Plus de 9.5 corners",co,min(83,round(po+3)),
                f"{home} et {away} deux équipes actives sur les phases offensives, "
                f"volume de corners élevé attendu")
        elif pu >= 63:
            add("corners_under","Corners","Moins de 9.5 corners",cu,min(83,round(pu+2)),
                f"Match attendu fermé et peu de transitions offensives — peu de corners")

    # ── Cartons ────────────────────────────────────────────────────────────
    if crd:
        co=get_odds(crd,"Over"); cu=get_odds(crd,"Under")
        po,pu=prob(co),prob(cu)
        if po >= 60:
            add("cards_over","Cartons","Plus de 3.5 cartons",co,min(81,round(po+2)),
                f"Rencontre à forts enjeux ou rivalité historique — match tendu, arbitrage attendu strict")
        elif pu >= 63:
            add("cards_under","Cartons","Moins de 3.5 cartons",cu,min(81,round(pu+1)),
                f"Match propre attendu — deux équipes disciplinées sans historique d'incidents")

    # ── Déduplication picks équipe ──────────────────────────────────────────
    seen = {}
    for pk in sorted(candidates, key=lambda x: x["confidence"], reverse=True):
        k = pk.get("direction", pk["label"])
        if k not in seen: seen[k] = pk
    raw_picks = list(seen.values())

    # ── Cohérence Over/Under buts : évite les contradictions ─────────────────
    directions = {p.get("direction","") for p in raw_picks}
    filtered = []
    for pk in raw_picks:
        d = pk.get("direction","")
        # Over 1.5 incompatible avec Under 2.5 ou Under 3.5
        if d == "over15" and ("under25" in directions or "under35" in directions):
            continue
        # Over 1.5 redondant si Over 2.5 existe
        if d == "over15" and "over25" in directions:
            continue
        # Over 2.5 incompatible avec Under 3.5 (quasi-contradiction)
        if d == "over25" and "under35" in directions:
            o = next((p for p in raw_picks if p.get("direction")=="over25"), None)
            u = next((p for p in raw_picks if p.get("direction")=="under35"), None)
            if o and u and u["confidence"] > o["confidence"]:
                continue  # garde under35, supprime over25
        # Under 3.5 redondant si Under 2.5 plus restrictif existe
        if d == "under35" and "under25" in directions:
            continue
        filtered.append(pk)

    # ── Cohérence 1X2 / DC : un match a UN seul resultat. On ne peut pas ─────
    # proposer "Freiburg gagne" + "Aston Villa gagne" + "Freiburg ou Nul". On
    # garde le pick le + confiant et on ne conserve que les autres COMPATIBLES.
    #
    # Sets de "outcomes gagnants" pour chaque direction (H=home, D=draw, A=away):
    OUTCOME_SETS = {
        "home_win": {"H"},
        "away_win": {"A"},
        "draw":     {"D"},
        "home_dc":  {"H", "D"},   # 1X
        "away_dc":  {"D", "A"},   # X2
        "no_draw":  {"H", "A"},   # 12
    }
    onextwo_picks = [p for p in filtered if p.get("direction") in OUTCOME_SETS]
    other_picks   = [p for p in filtered if p.get("direction") not in OUTCOME_SETS]

    # Greedy : tri par confidence desc, on garde un pick si compatible
    # (intersection non vide) avec TOUS les deja-gardes.
    onextwo_picks.sort(key=lambda x: x["confidence"], reverse=True)
    kept_1x2 = []
    for pk in onextwo_picks:
        my_outcomes = OUTCOME_SETS[pk["direction"]]
        ok = all(OUTCOME_SETS[k["direction"]] & my_outcomes for k in kept_1x2)
        if ok:
            kept_1x2.append(pk)

    team_picks = sorted(kept_1x2 + other_picks, key=lambda x: x["confidence"], reverse=True)[:5]

    # ── Paris fun (cote >= 2.0, analyse sérieuse) ──────────────────────────
    fun_picks = []

    # Victoire extérieure surprise (outsider en forme)
    if af and c2 and c2 >= 2.0:
        a_recent = recent_form_score(af)
        a_trend_  = form_trend(af)
        if a_recent >= 60 and a_trend_ in ("en hausse","en grande forme"):
            if win_rate(af[-5:]) >= 0.4:
                conf = round(min(72, a_recent * 0.6 + (aw/h2ht*20 if h2ht else 10)))
                if conf >= 45:
                    fun_picks.append({
                        "direction": "fun_away_win", "type": "🎲 Paris fun",
                        "label": f"{away} gagne à l'extérieur",
                        "cote": c2, "confidence": conf, "is_fun": True,
                        "stats": {"form": af},
                        "reasoning": (f"{away} est {a_trend_} ({form_summary(af[-5:])}) "
                                      f"et peut créer la surprise à {round(c2,2)} — "
                                      f"outsider mais en confiance")
                    })

    # Nul entre deux équipes proches
    if hf and af and cx and cx >= 2.0:
        pd = abs(ap - hp)
        if pd <= 4 and abs(h_form_score - a_form_score) <= 15:
            conf = round(min(65, 40 + int(dn or 0)/int(h2ht or 1)*25 if h2ht else 45))
            if conf >= 40:
                fun_picks.append({
                    "direction": "fun_draw", "type": "🎲 Paris fun",
                    "label": "Match nul",
                    "cote": cx, "confidence": conf, "is_fun": True,
                    "stats": {"form": hf},
                    "reasoning": (f"Deux équipes au niveau similaire (#{hp} vs #{ap}), "
                                  f"forme équilibrée des deux côtés — nul logique à {round(cx,2)}")
                })

    # Over 3.5 buts si deux équipes très offensives
    o35_mkt = get_mkt(odds,"Match goals","3.5")
    if o35_mkt:
        co35 = get_odds(o35_mkt,"Over")
        if co35 and co35 >= 2.0:
            both_off = (loss_rate(hf) < 0.3 if hf else False) and (loss_rate(af) < 0.3 if af else False)
            if both_off and btts_p >= 60:
                fun_picks.append({
                    "direction": "fun_over35", "type": "🎲 Paris fun",
                    "label": "Plus de 3.5 buts",
                    "cote": co35, "confidence": 52, "is_fun": True,
                    "stats": {},
                    "reasoning": (f"Deux attaques prolifiques ({home} et {away} marquent régulièrement), "
                                  f"BTTS probable — match à buts à {round(co35,2)}")
                })

    # Trier les fun picks par confiance et garder max 2
    fun_picks.sort(key=lambda x: x["confidence"], reverse=True)
    fun_picks = fun_picks[:2]

    # ── Coupe du Monde 2026 : marchés multiples dérivés de la grille Poisson
    # Au lieu de générer UNIQUEMENT le score exact (fun, cote 4-8), on dérive
    # de la même paire (lam_h, lam_a) plusieurs marchés à confiance variable :
    #   - Vainqueur 1X2 (home / draw / away)
    #   - Double chance (1X, 12, X2)
    #   - Over/Under 1.5, 2.5, 3.5
    #   - BTTS (les 2 équipes marquent)
    #   - Score exact #1
    # Chaque marché passe via le seuil 50% de confiance (ou est exclu).
    if league_id == WC_LEAGUE_ID:
        h_l5_gf = (form.get("homeTeam") or {}).get("l5_gf_pm")
        h_l5_ga = (form.get("homeTeam") or {}).get("l5_ga_pm")
        a_l5_gf = (form.get("awayTeam") or {}).get("l5_gf_pm")
        a_l5_ga = (form.get("awayTeam") or {}).get("l5_ga_pm")
        if h_l5_gf is not None and a_l5_ga is not None and a_l5_gf is not None and h_l5_ga is not None:
            h_atk_m, h_def_m = tier_strength_adjustment(home, away)
            a_atk_m, a_def_m = tier_strength_adjustment(away, home)
            lam_h_wc = max(0.2, ((h_l5_gf * h_atk_m) + (a_l5_ga * a_def_m)) / 2)
            lam_a_wc = max(0.2, ((a_l5_gf * a_atk_m) + (h_l5_ga * h_def_m)) / 2)

            # ─── CONTEXT BOOSTERS CdM : R1, mismatch, motivation ─────────
            # Détecte le round depuis match.json (fotmob expose
            # league_round_name 1/2/3 pour la phase de poules).
            round_num = _normalize_round_label(
                match.get("league_round_name") or match.get("round_name")
                or (form.get("leagueRoundName"))
            )
            # Côté home : son boost personnel + malus que lui inflige away
            h_self_boost, h_opp_malus = wc_context_boost(home, away, round_num, league_id)
            a_self_boost, a_opp_malus = wc_context_boost(away, home, round_num, league_id)
            # Applique : λ_team += son_boost + malus_que_lui_inflige_l_opp
            lam_h_wc = max(0.10, lam_h_wc + h_self_boost + a_opp_malus)
            lam_a_wc = max(0.10, lam_a_wc + a_self_boost + h_opp_malus)

            # ─── FICHES ÉQUIPES NATIONALES (intl_team_sheets.json) ────────
            # Ajustement empirique basé sur perf récente vs attendu Elo.
            # Ex : Spain off_score -0.20 + Cape Verde def_score +0.57 →
            # réduit λ Spain (-3% * -8.55% ≈ -11%) car Spain sous-performe
            # en attaque ET Cape Verde sur-performe en défense.
            h_sheet = get_intl_sheet(home) or {}
            a_sheet = get_intl_sheet(away) or {}
            sheet_descr_h = ""; sheet_descr_a = ""
            if h_sheet or a_sheet:
                h_off = h_sheet.get("off_score", 0.0) if h_sheet else 0.0
                h_def = h_sheet.get("def_score", 0.0) if h_sheet else 0.0
                a_off = a_sheet.get("off_score", 0.0) if a_sheet else 0.0
                a_def = a_sheet.get("def_score", 0.0) if a_sheet else 0.0
                f_h_atk, f_h_conc = intl_sheet_lambda_adjustment(h_off, h_def, a_off, a_def)
                f_a_atk, f_a_conc = intl_sheet_lambda_adjustment(a_off, a_def, h_off, h_def)
                # λ_team_attack utilise f_team_atk + on borne
                lam_h_wc = max(0.10, lam_h_wc * f_h_atk)
                lam_a_wc = max(0.10, lam_a_wc * f_a_atk)
                # Description pour reasoning
                def _label(score, kind):
                    if kind == "off":
                        if score > 0.3: return "off solide"
                        if score < -0.3: return "off en panne"
                        return "off moyen"
                    else:
                        if score > 0.3: return "def solide"
                        if score < -0.3: return "def fragile"
                        return "def moyenne"
                if h_sheet:
                    h_ped = h_sheet.get("wc_pedigree", {}) or {}
                    ped_str = ""
                    if h_ped.get("label") and h_ped.get("label") not in ("inconnu", "novice / absent récent"):
                        ped_str = f" · 🏆 {h_ped.get('label')}"
                    elif h_ped.get("label") == "novice / absent récent":
                        ped_str = " · 🏆 novice WC"
                    sheet_descr_h = (f"📋 Fiche {home} : {_label(h_off,'off')} ({h_off:+.2f})"
                                     f" · {_label(h_def,'def')} ({h_def:+.2f})"
                                     f" · forme {h_sheet.get('form','')}{ped_str}")
                    if h_ped.get("summary_fr") and h_ped.get("score", 0) >= 3.0:
                        sheet_descr_h += f"\n   ↳ Historique : {h_ped['summary_fr']}"
                if a_sheet:
                    a_ped = a_sheet.get("wc_pedigree", {}) or {}
                    ped_str = ""
                    if a_ped.get("label") and a_ped.get("label") not in ("inconnu", "novice / absent récent"):
                        ped_str = f" · 🏆 {a_ped.get('label')}"
                    elif a_ped.get("label") == "novice / absent récent":
                        ped_str = " · 🏆 novice WC"
                    sheet_descr_a = (f"📋 Fiche {away} : {_label(a_off,'off')} ({a_off:+.2f})"
                                     f" · {_label(a_def,'def')} ({a_def:+.2f})"
                                     f" · forme {a_sheet.get('form','')}{ped_str}")
                    if a_ped.get("summary_fr") and a_ped.get("score", 0) >= 3.0:
                        sheet_descr_a += f"\n   ↳ Historique : {a_ped['summary_fr']}"

                # Petit booster pédigrée : équipe élite (score ≥6) en phase
                # éliminatoire seulement (jamais en R1 où les surprises sont
                # nombreuses). Modeste : ±5% sur λ_attaque.
                # NB : on n'applique PAS au R1 — historique WC ne prédit pas
                # bien le 1er match d'une équipe (Spain 0-0 vs Cap Vert hier).
                if round_num and round_num > 1:
                    h_pscore = (h_sheet.get("wc_pedigree") or {}).get("score", 0)
                    a_pscore = (a_sheet.get("wc_pedigree") or {}).get("score", 0)
                    if h_pscore - a_pscore >= 3:
                        lam_h_wc *= 1.05
                    elif a_pscore - h_pscore >= 3:
                        lam_a_wc *= 1.05

            # ─── IMPORTANCE MATCH (wc_match_importance.json) ──────────────
            # Pour matchs 3 de poule : si déjà qualifié → rotation (λ ×0.85)
            # si doit gagner → joue à fond (λ ×1.05), si éliminé → libre (λ ×0.92)
            # 1er match du tournoi → cadenassé (λ ×0.95)
            importance_descr = ""
            try:
                import json as _jimp
                with open("data/wc_match_importance.json", encoding="utf-8") as _imp_f:
                    _imp = _jimp.load(_imp_f)
                _imp_teams = (_imp.get("teams") or {})
                import unicodedata as _udimp, re as _reimp
                def _slug_imp(nm):
                    s = _udimp.normalize("NFD", nm or "")
                    s = "".join(c for c in s if _udimp.category(c) != "Mn")
                    return _reimp.sub(r"[^a-z0-9_]", "_", s.lower()).strip("_")
                _h_imp = _imp_teams.get(_slug_imp(home), {})
                _a_imp = _imp_teams.get(_slug_imp(away), {})
                _h_mod = _h_imp.get("importance_modifier", 1.0)
                _a_mod = _a_imp.get("importance_modifier", 1.0)
                if _h_mod != 1.0:
                    lam_h_wc *= _h_mod
                if _a_mod != 1.0:
                    lam_a_wc *= _a_mod
                parts_imp = []
                if _h_imp.get("status_fr"):
                    parts_imp.append(f"{home} : {_h_imp['status_fr']}" +
                                     (f" (λ ×{_h_mod:.2f})" if _h_mod != 1.0 else ""))
                if _a_imp.get("status_fr"):
                    parts_imp.append(f"{away} : {_a_imp['status_fr']}" +
                                     (f" (λ ×{_a_mod:.2f})" if _a_mod != 1.0 else ""))
                if parts_imp:
                    importance_descr = "🎯 Importance : " + " · ".join(parts_imp)
            except Exception:
                pass

            # ─── MÉTÉO / ALTITUDE / STADE (foot_match_context) ────────────
            # Ajustement λ_total selon conditions :
            #  - pluie forte (>5mm) : -10% (jeu moins fluide)
            #  - vent fort (>30 km/h) : -7% (passes imprécises)
            #  - chaleur extrême (>32°C) : -10% (rythme baisse)
            #  - altitude > 1500m : +5% (ballons trompeurs)
            # Stade climatisé annule pluie/vent.
            ctx_match = match.get("context") or {}
            wx = ctx_match.get("weather") or {}
            std = ctx_match.get("stadium") or {}
            wx_factor = 1.0
            wx_descr_parts = []
            climatized = bool(std.get("climatized"))
            if not climatized:
                prec = wx.get("precipitation_sum_mm") or 0
                wind = wx.get("wind_max_kmh") or 0
                tmax = wx.get("temp_max") or 0
                if prec >= 5:
                    wx_factor *= 0.90; wx_descr_parts.append(f"pluie {prec}mm (-10%)")
                if wind >= 30:
                    wx_factor *= 0.93; wx_descr_parts.append(f"vent {wind}km/h (-7%)")
                if tmax >= 32:
                    wx_factor *= 0.90; wx_descr_parts.append(f"chaleur {tmax}°C (-10%)")
            alt = std.get("altitude_m") or 0
            if alt >= 1500:
                wx_factor *= 1.05; wx_descr_parts.append(f"altitude {alt}m (+5%)")
            if wx_factor != 1.0:
                lam_h_wc *= wx_factor
                lam_a_wc *= wx_factor
            wx_descr = ""
            if wx_descr_parts:
                wx_descr = "🌦️ Conditions : " + " · ".join(wx_descr_parts)
            elif wx.get("summary_fr"):
                wx_descr = f"🌦️ {wx.get('summary_fr')}" + (f" · stade fermé" if climatized else "")

            ctx_descr_h = wc_context_descr(home, away, round_num, league_id)
            ctx_descr_a = wc_context_descr(away, home, round_num, league_id)
            # ─── ABSENCES BUTEURS CLÉS (lineup.unavailable) ────────────────
            # Refresh lineup explicitement pour WC (besoin de la compo
            # probable/officielle à T-90 min pour appliquer les malus
            # d'absences sur les TOP buteurs).
            try:
                from fotmob_client import match_lineup as _ml
                _ln = _ml(match.get("_page_url") or match.get("page_url"), ttl=30*60)
            except Exception:
                _ln = None
            absent_descr_h = ""; absent_descr_a = ""
            if _ln:
                _h_tid = match.get("home_id"); _a_tid = match.get("away_id")
                _h_sig = team_squad_signals(_h_tid) if _h_tid else {"top_scorers": []}
                _a_sig = team_squad_signals(_a_tid) if _a_tid else {"top_scorers": []}
                def _wc_unavail(side_key):
                    t = (_ln.get(side_key) or {})
                    out = set()
                    for u in (t.get("unavailable") or []):
                        nm = (u.get("name") or "").strip().lower()
                        if nm and "doubt" not in (u.get("return") or "").lower():
                            out.add(nm)
                    return out
                _h_unav = _wc_unavail("home")
                _a_unav = _wc_unavail("away")
                def _wc_absence_malus(sig, unav):
                    if not sig.get("top_scorers"): return 1.0, []
                    total_g = sum(p["goals"] for p in sig["top_scorers"])
                    if total_g == 0: return 1.0, []
                    absent_share = 0.0; absent_names = []
                    for p in sig["top_scorers"]:
                        if p["name"].lower() in unav:
                            absent_share += p["goals"] / total_g
                            absent_names.append(p["name"])
                    reduction = min(0.30, absent_share * 0.5)
                    return (1.0 - reduction), absent_names
                _h_factor, _h_absents = _wc_absence_malus(_h_sig, _h_unav)
                _a_factor, _a_absents = _wc_absence_malus(_a_sig, _a_unav)
                lam_h_wc *= _h_factor
                lam_a_wc *= _a_factor
                if _h_absents:
                    absent_descr_h = (f"⚠️ {home} : absent(s) buteur(s) clé(s) — "
                                      f"{', '.join(_h_absents[:3])} ({int((1-_h_factor)*100)}% λ)")
                if _a_absents:
                    absent_descr_a = (f"⚠️ {away} : absent(s) buteur(s) clé(s) — "
                                      f"{', '.join(_a_absents[:3])} ({int((1-_a_factor)*100)}% λ)")
                # Statut lineup (predicted / standard) à exposer
                lineup_type = (_ln.get("type") or "").lower()
                if lineup_type == "standard":
                    lineup_descr = "✅ Compo OFFICIELLE"
                elif lineup_type == "predicted":
                    lineup_descr = "⚠️ Compo PROBABLE (officielle à T-60min)"
                else:
                    lineup_descr = ""
            else:
                lineup_descr = ""

            ctx_descr_combined = "\n".join(d for d in [
                ctx_descr_h, ctx_descr_a,
                sheet_descr_h, sheet_descr_a,
                importance_descr,
                wx_descr, lineup_descr,
                absent_descr_h, absent_descr_a,
            ] if d)

            # ANALYSE NIVEAU (FIFA / favoris / Elo) à exposer dans les reasonings
            _fh = get_fifa_data(home)
            _fa = get_fifa_data(away)
            h_rank = _fh["rank"] if _fh else "?"
            a_rank = _fa["rank"] if _fa else "?"
            h_pts  = _fh["points"] if _fh else None
            a_pts  = _fa["points"] if _fa else None
            elo_gap = (h_pts - a_pts) if (h_pts is not None and a_pts is not None) else None
            elo_p_home = fifa_elo_expected(h_pts, a_pts) if (h_pts and a_pts) else None
            level_descr = ""
            if elo_gap is not None:
                if abs(elo_gap) >= 400:
                    level_descr = (
                        f"📊 Écart FIFA ÉNORME : {home} #{h_rank} ({h_pts} pts) vs {away} #{a_rank} ({a_pts} pts) "
                        f"— gap Elo {elo_gap:+d} pts → P(vainqueur favori) ≈ {(max(elo_p_home, 1-elo_p_home))*100:.0f}%."
                    )
                elif abs(elo_gap) >= 200:
                    level_descr = (
                        f"📊 Écart FIFA significatif : {home} #{h_rank} vs {away} #{a_rank} "
                        f"(gap {elo_gap:+d} pts, P favori ≈ {(max(elo_p_home, 1-elo_p_home))*100:.0f}%)."
                    )
                else:
                    level_descr = (
                        f"📊 Niveau équilibré : {home} #{h_rank} vs {away} #{a_rank} (gap {elo_gap:+d} pts)."
                    )
            # Annotation favoris
            fav_tags = []
            if is_wc_favorite(home): fav_tags.append(f"⭐ {home} favori CdM 2026")
            if is_wc_favorite(away): fav_tags.append(f"⭐ {away} favori CdM 2026")
            if is_uefa_powerhouse(home) and not is_wc_favorite(home): fav_tags.append(f"🇪🇺 {home} UEFA powerhouse")
            if is_uefa_powerhouse(away) and not is_wc_favorite(away): fav_tags.append(f"🇪🇺 {away} UEFA powerhouse")
            fav_descr = " · ".join(fav_tags)

            # Forme L10 (utilisable depuis match["pre_match_form"])
            h_form_arr = (form.get("homeTeam") or {}).get("form") or []
            a_form_arr = (form.get("awayTeam") or {}).get("form") or []
            def _form_summary(f):
                if not f: return "?"
                w = f.count("W"); d = f.count("D"); l = f.count("L")
                return f"{w}V/{d}N/{l}D sur {len(f)}"
            h_form_str = _form_summary(h_form_arr[:10])
            a_form_str = _form_summary(a_form_arr[:10])
            form_descr = f"L10 : {home} {h_form_str} · {away} {a_form_str}"

            level_block = (level_descr + "\n" + (fav_descr + "\n" if fav_descr else "") + form_descr).strip()

            # Helpers Poisson
            import math as _mwc
            def _pmf(k, lam):
                return (lam ** k) * _mwc.exp(-lam) / _mwc.factorial(k)

            # Grille complète 0-7 pour 1X2 / Over+Under / BTTS / score exact
            MAX_G = 7
            grid_full = {}
            for i in range(MAX_G + 1):
                ph = _pmf(i, lam_h_wc)
                for j in range(MAX_G + 1):
                    pa = _pmf(j, lam_a_wc)
                    grid_full[(i, j)] = ph * pa

            p_home_win = sum(p for (i, j), p in grid_full.items() if i > j)
            p_draw     = sum(p for (i, j), p in grid_full.items() if i == j)
            p_away_win = sum(p for (i, j), p in grid_full.items() if i < j)
            p_btts     = (1 - _mwc.exp(-lam_h_wc)) * (1 - _mwc.exp(-lam_a_wc))
            # Totals
            lam_total = lam_h_wc + lam_a_wc
            def _p_total_under(k):
                return sum(_pmf(i, lam_total) for i in range(k + 1))
            p_under_15 = _p_total_under(1)  # P(total <= 1)
            p_over_15  = 1 - p_under_15
            p_under_25 = _p_total_under(2)
            p_over_25  = 1 - p_under_25
            p_under_35 = _p_total_under(3)
            p_over_35  = 1 - p_under_35

            # Cote estimée = 1/p (sans marge bookmaker). Pour info utilisateur.
            def _est_cote(p):
                if p <= 0: return None
                return round(1 / p, 2)

            # Helper : ajoute un pick s'il dépasse le seuil de confiance
            wc_candidates = [
                # 1X2 (vainqueur)
                {
                    "direction": "wc_home_win", "type": "🏆 Vainqueur WC",
                    "label": f"{home} gagne",
                    "p": p_home_win, "min_conf": 50,
                    "reasoning": (
                        f"🏆 Modèle Poisson CdM (tier FIFA ajusté)\n"
                        f"λ {home} = {lam_h_wc:.2f} buts attendus, λ {away} = {lam_a_wc:.2f}\n"
                        f"P({home} gagne) = {p_home_win*100:.1f}% · P(nul) = {p_draw*100:.1f}% · P({away}) = {p_away_win*100:.1f}%"
                    ),
                },
                {
                    "direction": "wc_away_win", "type": "🏆 Vainqueur WC",
                    "label": f"{away} gagne",
                    "p": p_away_win, "min_conf": 50,
                    "reasoning": (
                        f"🏆 Modèle Poisson CdM (tier FIFA ajusté)\n"
                        f"λ {home} = {lam_h_wc:.2f}, λ {away} = {lam_a_wc:.2f}\n"
                        f"P({home}) = {p_home_win*100:.1f}% · P(nul) = {p_draw*100:.1f}% · P({away} gagne) = {p_away_win*100:.1f}%"
                    ),
                },
                {
                    "direction": "wc_draw", "type": "🏆 Vainqueur WC",
                    "label": "Match nul",
                    "p": p_draw, "min_conf": 32,  # nul rare, seuil + bas
                    "reasoning": (
                        f"🏆 Modèle Poisson CdM\n"
                        f"P(nul) = {p_draw*100:.1f}% — équipes équilibrées (λ {lam_h_wc:.2f} vs {lam_a_wc:.2f})"
                    ),
                },
                # Double chance
                {
                    "direction": "wc_dc_1x", "type": "🏆 Double chance WC",
                    "label": f"{home} ou nul (1X)",
                    "p": p_home_win + p_draw, "min_conf": 65,
                    "reasoning": (
                        f"🛡️ Double chance : {home} gagne ({p_home_win*100:.1f}%) OU match nul ({p_draw*100:.1f}%)\n"
                        f"Combiné = {(p_home_win + p_draw)*100:.1f}% — option safe contre {away}"
                    ),
                },
                {
                    "direction": "wc_dc_x2", "type": "🏆 Double chance WC",
                    "label": f"{away} ou nul (X2)",
                    "p": p_away_win + p_draw, "min_conf": 65,
                    "reasoning": (
                        f"🛡️ Double chance : {away} gagne ({p_away_win*100:.1f}%) OU match nul ({p_draw*100:.1f}%)\n"
                        f"Combiné = {(p_away_win + p_draw)*100:.1f}%"
                    ),
                },
                # Totals
                {
                    "direction": "wc_over_15", "type": "⚽ Total buts WC",
                    "label": "Plus de 1.5 buts",
                    "p": p_over_15, "min_conf": 70,
                    "reasoning": (
                        f"⚽ Modèle Poisson : λ total = {lam_total:.2f} buts attendus\n"
                        f"P(plus de 1.5 buts) = {p_over_15*100:.1f}%"
                    ),
                },
                {
                    "direction": "wc_under_15", "type": "⚽ Total buts WC",
                    "label": "Moins de 1.5 buts",
                    "p": p_under_15, "min_conf": 50,
                    "reasoning": (
                        f"⚽ Modèle Poisson : λ total = {lam_total:.2f}\n"
                        f"P(moins de 1.5 buts) = {p_under_15*100:.1f}% — match potentiellement fermé"
                    ),
                },
                {
                    "direction": "wc_over_25", "type": "⚽ Total buts WC",
                    "label": "Plus de 2.5 buts",
                    "p": p_over_25, "min_conf": 55,
                    "reasoning": (
                        f"⚽ Modèle Poisson : λ total = {lam_total:.2f}\n"
                        f"P(plus de 2.5 buts) = {p_over_25*100:.1f}%"
                    ),
                },
                {
                    "direction": "wc_under_25", "type": "⚽ Total buts WC",
                    "label": "Moins de 2.5 buts",
                    "p": p_under_25, "min_conf": 55,
                    "reasoning": (
                        f"⚽ Modèle Poisson : λ total = {lam_total:.2f}\n"
                        f"P(moins de 2.5 buts) = {p_under_25*100:.1f}%"
                    ),
                },
                {
                    "direction": "wc_over_35", "type": "⚽ Total buts WC",
                    "label": "Plus de 3.5 buts",
                    "p": p_over_35, "min_conf": 50,
                    "reasoning": (
                        f"⚽ Modèle Poisson : λ total = {lam_total:.2f}\n"
                        f"P(plus de 3.5 buts) = {p_over_35*100:.1f}% — match ouvert prévu"
                    ),
                },
                # BTTS
                {
                    "direction": "wc_btts_yes", "type": "⚽ BTTS WC",
                    "label": "Les 2 équipes marquent",
                    "p": p_btts, "min_conf": 55,
                    "reasoning": (
                        f"⚽ BTTS Poisson : P({home} ≥ 1) × P({away} ≥ 1) = "
                        f"{(1-_mwc.exp(-lam_h_wc))*100:.0f}% × {(1-_mwc.exp(-lam_a_wc))*100:.0f}% = "
                        f"{p_btts*100:.1f}%"
                    ),
                },
                {
                    "direction": "wc_btts_no", "type": "⚽ BTTS WC",
                    "label": "Une équipe ne marque pas (BTTS NO)",
                    "p": 1 - p_btts, "min_conf": 55,
                    "reasoning": (
                        f"⚽ BTTS NO : P(au moins une équipe blanchie) = {(1-p_btts)*100:.1f}%\n"
                        f"λ faibles côté {away} ({lam_a_wc:.2f}) ou {home} ({lam_h_wc:.2f})"
                    ),
                },
                # Double chance 12 : pas de nul (un des deux gagne)
                {
                    "direction": "wc_dc_12", "type": "🏆 Double chance WC",
                    "label": f"{home} ou {away} (12, pas de nul)",
                    "p": p_home_win + p_away_win, "min_conf": 75,
                    "reasoning": (
                        f"🛡️ Double chance 12 : {home} ({p_home_win*100:.1f}%) OU {away} ({p_away_win*100:.1f}%)\n"
                        f"Combiné = {(p_home_win + p_away_win)*100:.1f}% — couvre tout sauf le nul"
                    ),
                },
                # Équipes marquent N+ buts (utile pour gros favoris)
                {
                    "direction": "wc_home_scores_1", "type": "⚽ Buteur équipe WC",
                    "label": f"{home} marque (≥1 but)",
                    "p": 1 - _mwc.exp(-lam_h_wc), "min_conf": 75,
                    "reasoning": (
                        f"⚽ P({home} marque ≥1) = {(1-_mwc.exp(-lam_h_wc))*100:.1f}% — λ_{home} = {lam_h_wc:.2f}"
                    ),
                },
                {
                    "direction": "wc_home_scores_2", "type": "⚽ Buteur équipe WC",
                    "label": f"{home} marque 2+ buts",
                    "p": 1 - _mwc.exp(-lam_h_wc) - lam_h_wc * _mwc.exp(-lam_h_wc),
                    "min_conf": 55,
                    "reasoning": (
                        f"⚽ P({home} marque 2+) = {(1 - _mwc.exp(-lam_h_wc) - lam_h_wc*_mwc.exp(-lam_h_wc))*100:.1f}% (λ = {lam_h_wc:.2f})"
                    ),
                },
                {
                    "direction": "wc_home_scores_3", "type": "⚽ Buteur équipe WC",
                    "label": f"{home} marque 3+ buts",
                    "p": 1 - sum(_pmf(i, lam_h_wc) for i in range(3)),
                    "min_conf": 45,
                    "reasoning": (
                        f"⚽ P({home} marque 3+) = {(1-sum(_pmf(i, lam_h_wc) for i in range(3)))*100:.1f}% — gros favori en mode steamroller"
                    ),
                },
                {
                    "direction": "wc_away_scores_1", "type": "⚽ Buteur équipe WC",
                    "label": f"{away} marque (≥1 but)",
                    "p": 1 - _mwc.exp(-lam_a_wc), "min_conf": 75,
                    "reasoning": (
                        f"⚽ P({away} marque ≥1) = {(1-_mwc.exp(-lam_a_wc))*100:.1f}% — λ_{away} = {lam_a_wc:.2f}"
                    ),
                },
                {
                    "direction": "wc_away_scores_2", "type": "⚽ Buteur équipe WC",
                    "label": f"{away} marque 2+ buts",
                    "p": 1 - _mwc.exp(-lam_a_wc) - lam_a_wc * _mwc.exp(-lam_a_wc),
                    "min_conf": 50,
                    "reasoning": (
                        f"⚽ P({away} marque 2+) = {(1-_mwc.exp(-lam_a_wc) - lam_a_wc*_mwc.exp(-lam_a_wc))*100:.1f}% (λ = {lam_a_wc:.2f})"
                    ),
                },
                # Équipes blanchies (utile pour favoris qui défendent bien)
                {
                    "direction": "wc_away_no_score", "type": "🛡️ Clean sheet WC",
                    "label": f"{away} ne marque pas",
                    "p": _mwc.exp(-lam_a_wc), "min_conf": 55,
                    "reasoning": (
                        f"🛡️ P({away} blanchi) = {_mwc.exp(-lam_a_wc)*100:.1f}% — défense {home} dominante (λ_{away} = {lam_a_wc:.2f})"
                    ),
                },
                {
                    "direction": "wc_home_no_score", "type": "🛡️ Clean sheet WC",
                    "label": f"{home} ne marque pas",
                    "p": _mwc.exp(-lam_h_wc), "min_conf": 55,
                    "reasoning": (
                        f"🛡️ P({home} blanchi) = {_mwc.exp(-lam_h_wc)*100:.1f}% (λ_{home} = {lam_h_wc:.2f})"
                    ),
                },
            ]
            # ─── SÉLECTION HUMAINE : on filtre/score/diversifie ───────────
            # On a 17 candidats potentiels. Au lieu de tous les exposer, on
            # se comporte comme un analyste : 3-4 picks pertinents max,
            # diversifiés par famille de marché, mélangeant 1 safe + 1 value +
            # 1 longshot/score si pertinent.

            # Mapping direction -> famille de marché
            FAMILY = {
                "wc_home_win": "1X2", "wc_away_win": "1X2", "wc_draw": "1X2",
                "wc_dc_1x": "DC", "wc_dc_x2": "DC", "wc_dc_12": "DC",
                "wc_over_15": "TOTAL", "wc_under_15": "TOTAL",
                "wc_over_25": "TOTAL", "wc_under_25": "TOTAL",
                "wc_over_35": "TOTAL",
                "wc_btts_yes": "BTTS", "wc_btts_no": "BTTS",
                "wc_home_scores_1": "BUT_TEAM", "wc_home_scores_2": "BUT_TEAM",
                "wc_home_scores_3": "BUT_TEAM",
                "wc_away_scores_1": "BUT_TEAM", "wc_away_scores_2": "BUT_TEAM",
                "wc_away_no_score": "CLEAN_SHEET", "wc_home_no_score": "CLEAN_SHEET",
            }

            def _tier_pick(cote, conf):
                """Catégorise : safe (cote bas+conf haute), value (mid), fun (cote haut)."""
                if cote is None: return "unknown"
                if cote <= 1.25 and conf >= 80: return "safe"
                if cote >= 2.50 or conf <= 35:  return "fun"
                return "value"

            def _interest_score(cand):
                """Score d'intérêt : favorise les value picks (cote 1.4-2.0),
                pénalise les ultra-safe (cote < 1.10 = pas de juice) et les
                ultra-fun (cote > 4 sauf score exact spécial). Conf x cote.
                """
                p = cand["p"]
                cote_est = _est_cote(p)
                if cote_est is None: return 0
                conf = p * 100
                # Sweet spot : cote 1.4-2.0 → EV*1.3 bonus
                if 1.4 <= cote_est <= 2.0: bonus = 1.3
                elif 1.25 <= cote_est <= 2.5: bonus = 1.1
                elif cote_est <= 1.15: bonus = 0.6  # juice trop faible
                else: bonus = 0.8
                return conf * bonus

            # Filtre seuils, puis trie par interest_score
            kept_candidates = []
            for cand in wc_candidates:
                p = cand["p"]
                conf = round(p * 100)
                if conf < cand["min_conf"]:
                    continue
                cand["_conf"] = conf
                cand["_cote"] = _est_cote(p)
                cand["_family"] = FAMILY.get(cand["direction"], "OTHER")
                cand["_score"] = _interest_score(cand)
                cand["_tier"]  = _tier_pick(cand["_cote"], conf)
                kept_candidates.append(cand)

            # Tri par score descendant
            kept_candidates.sort(key=lambda c: c["_score"], reverse=True)

            # Sélection : MAX 3 picks "autres" (résultat/buteur/total/BTTS...) par match,
            # MAX 1 par famille, MAX 1 safe et MAX 1 fun parmi ces 3.
            # Le score exact est ajouté EN PLUS et géré séparément (4 picks max total).
            # Contraintes garanties pour TOUS les matchs WC :
            #   - 1 score exact (toujours)
            #   - AU MOINS 1 autre pick (même en relaxant min_conf si nécessaire)
            selected_for_match = []
            families_used = set()
            tiers_used = []

            for cand in kept_candidates:
                fam = cand["_family"]
                tier = cand["_tier"]
                if len(selected_for_match) >= 3:
                    break
                if fam in families_used:
                    continue
                if tier == "safe" and tiers_used.count("safe") >= 1:
                    continue
                if tier == "fun" and tiers_used.count("fun") >= 1:
                    continue
                selected_for_match.append(cand)
                families_used.add(fam)
                tiers_used.append(tier)

            # GARANTIE : au moins 1 pick "autre" doit être sélectionné pour chaque
            # match WC. Si aucun n'a passé les seuils min_conf, on prend le meilleur
            # candidat sur _interest_score parmi tous les wc_candidates (sans seuil).
            if not selected_for_match:
                all_wc = list(wc_candidates)
                for cand in all_wc:
                    if "_conf" not in cand:
                        p = cand["p"]
                        cand["_conf"] = round(p * 100)
                        cand["_cote"] = _est_cote(p)
                        cand["_family"] = FAMILY.get(cand["direction"], "OTHER")
                        cand["_score"] = _interest_score(cand)
                        cand["_tier"]  = _tier_pick(cand["_cote"], cand["_conf"])
                all_wc.sort(key=lambda c: c["_score"], reverse=True)
                if all_wc:
                    selected_for_match.append(all_wc[0])
                    families_used.add(all_wc[0]["_family"])
                    tiers_used.append(all_wc[0]["_tier"])

            # Routing : cote_min < 2.0 → team_picks · cote_min ≥ 2.0 → fun_picks
            # User : "cote fun obligé d'être à +2". Mes cotes sont des cote_min
            # (estimation fair value du modèle), pas des cotes bookmaker réelles
            # — on ne pollue pas le badge `cote`, on utilise `cote_min` qui
            # affiche "✅ Prendre uniquement si cote ≥ X" côté UI.
            # Tier assignment basé sur cote_min :
            #   - cote_min ≤ 1.85 + conf ≥ 70 → safe
            #   - cote_min ≥ 2.60 OU conf < 55 → fun
            #   - sinon → ok
            def _assign_tier(cote_min, conf):
                if cote_min is None: return "ok"
                if cote_min >= 2.60 or conf < 55: return "fun"
                if cote_min <= 1.85 and conf >= 70: return "safe"
                return "ok"

            # Lookup cote bookmaker pour les picks WC depuis match_odds
            # (alimenté par foot_wc_odds.py / The Odds API en fallback).
            ft_wc  = get_mkt(odds, "Full time")
            btt_wc = get_mkt(odds, "Both teams to score")
            ou_wc  = get_mkt(odds, "Goals Over/Under (2.5)")
            def _wc_book_cote(direction):
                """Renvoie cote bookmaker WC pour une direction wc_*, ou None."""
                d = (direction or "").lower()
                if d == "wc_home_win":  return get_odds(ft_wc, "1")
                if d == "wc_away_win":  return get_odds(ft_wc, "2")
                if d == "wc_draw":      return get_odds(ft_wc, "X")
                if d == "wc_btts_yes":  return get_odds(btt_wc, "Yes")
                if d == "wc_btts_no":   return get_odds(btt_wc, "No")
                if d == "wc_over_25":   return get_odds(ou_wc, "Over 2.5")
                if d == "wc_under_25":  return get_odds(ou_wc, "Under 2.5")
                return None

            for cand in selected_for_match:
                full_reasoning = cand["reasoning"]
                if ctx_descr_combined:
                    full_reasoning = f"{ctx_descr_combined}\n{full_reasoning}"
                if level_block:
                    full_reasoning = f"{level_block}\n{full_reasoning}"
                cote_min = cand["_cote"]
                conf = cand["_conf"]
                final_tier = _assign_tier(cote_min, conf)
                cote_book = _wc_book_cote(cand["direction"])
                pick_dict = {
                    "direction": cand["direction"],
                    "type":      cand["type"],
                    "label":     cand["label"],
                    "cote":      cote_book,   # cote bookmaker (None si pas dispo)
                    "cote_min":  cote_min,    # cote minimum recommandée (1/p)
                    "confidence": conf,
                    "tier":      final_tier,
                    "stats":     {"lam_h": round(lam_h_wc, 2), "lam_a": round(lam_a_wc, 2),
                                  "h_fifa_rank": h_rank, "a_fifa_rank": a_rank,
                                  "elo_gap": elo_gap, "wc_round": round_num,
                                  "family": cand["_family"]},
                    "reasoning": full_reasoning,
                }
                # Routing fun (cote estimée >= 2.0) vs team (< 2.0)
                if cote_min is not None and cote_min >= 2.0:
                    pick_dict["is_fun"] = True
                    fun_picks.append(pick_dict)
                else:
                    team_picks.append(pick_dict)

            # Score exact #1 : TOUJOURS inclus pour les matchs WC, peu importe
            # le seuil. C'est le pari fun signature du canal Telegram (cote 4-12
            # typique) — on garantit qu'il est présent pour chaque match.
            (sh, sa), p_best, top3 = predict_exact_score(lam_h_wc, lam_a_wc)
            alts = " · ".join(f"{i}-{j} ({round(p*100,1)}%)" for (i,j), p in top3)
            include_score = True
            if include_score:
                score_reasoning_base = (
                    f"🏆 Coupe du Monde 2026 - score exact (Poisson grid, ajusté Elo FIFA)\n"
                    f"λ {home} = {lam_h_wc:.2f} buts, λ {away} = {lam_a_wc:.2f} buts\n"
                    f"Top 3 : {alts}\n"
                    f"⚠️ Pari fun (cote élevée typique 4-8) - mise modeste"
                )
                if level_block:
                    score_reasoning_base = f"{level_block}\n{score_reasoning_base}"
                fun_picks.append({
                    "direction": f"wc_score_{sh}_{sa}",
                    "type":      "🏆 Score exact WC",
                    "label":     f"Score exact : {home} {sh}-{sa} {away}",
                    "cote_min":  round(1 / max(0.01, p_best), 2),
                    "confidence": round(p_best * 100),
                    "tier":      "fun",
                    "is_fun":    True,
                    "stats":     {"lam_h": round(lam_h_wc, 2), "lam_a": round(lam_a_wc, 2),
                                  "h_fifa_rank": h_rank, "a_fifa_rank": a_rank, "elo_gap": elo_gap},
                    "reasoning": score_reasoning_base,
                    "exact_score": [sh, sa],
                    "wc_top3":     [[list(s), round(p*100,1)] for s, p in top3],
                })

    # ── Props joueurs ───────────────────────────────────────────────────────
    home_players = pstats.get("home", [])
    away_players = pstats.get("away", [])
    home_recent  = home_rec
    away_recent  = away_rec

    # Filtre joueurs absents/blesses (depuis lineup.unavailable)
    lineup = pstats.get("lineup") or {}

    # ── Picks buteur AMICAUX : utilise la lineup probable ────────────────────
    # Pour les selections nationales, on n'a pas de player_stats de
    # championnat (ils jouent dans des clubs differents). On utilise les
    # seasonGoals/seasonAppearances integrees dans la lineup pour calculer
    # P(joueur marque) via Poisson individuel.
    # ── Signaux squad universels (utilises pour amicaux ET championnats) ───
    # Calcule penaltyman + top scorers pour les 2 equipes, et applique
    # formation/absences/penalties partout ou pertinent.
    h_team_id = match.get("home_id")
    a_team_id = match.get("away_id")
    h_sig = team_squad_signals(h_team_id)
    a_sig = team_squad_signals(a_team_id)

    def _unavailable_names(side):
        t = lineup.get(side) or {}
        out_a = set()
        for u in (t.get("unavailable") or []):
            nm = (u.get("name") or "").strip().lower()
            if nm:
                ret = (u.get("return") or "").lower()
                if "doubt" not in ret:
                    out_a.add(nm)
        return out_a

    h_unavailable = _unavailable_names("home") if lineup else set()
    a_unavailable = _unavailable_names("away") if lineup else set()

    # Picks buteur "compo-based" : activé pour amicaux internationaux ET
    # pour la Coupe du Monde (les 2 utilisent la lineup + nat_stats joueur).
    # Pour WC, on hausse le seuil de confidence pour éviter le noise R1
    # (peu de buts en phase de poules, beaucoup d'équipes défensives).
    IS_INTL_NAT_MATCH = IS_INTL_FRIENDLY or (league_id == WC_LEAGUE_ID)
    if IS_INTL_NAT_MATCH and lineup:
        import math as _math_p
        h_team_data = form.get("homeTeam") or {}
        a_team_data = form.get("awayTeam") or {}
        h_gf_l5 = h_team_data.get("l5_gf_pm")
        h_ga_l5 = h_team_data.get("l5_ga_pm")
        a_gf_l5 = a_team_data.get("l5_gf_pm")
        a_ga_l5 = a_team_data.get("l5_ga_pm")

        # ── Ajustement TIER (Strength-of-Schedule sur les buts) ─────────────
        # Pays-Bas (tier 1) qui marque 1.6/m face a France/Allemagne != Uzbekistan
        # (tier 3) qui marque 1.5/m face a Vietnam. Quand ils se rencontrent,
        # on adjuste les L5 stats par le tier ecart.
        h_atk_mult, h_def_mult = tier_strength_adjustment(home, away)
        a_atk_mult, a_def_mult = tier_strength_adjustment(away, home)
        adj_h_gf = (h_gf_l5 or 1.5) * h_atk_mult
        adj_h_ga = (h_ga_l5 or 1.5) * h_def_mult
        adj_a_gf = (a_gf_l5 or 1.5) * a_atk_mult
        adj_a_ga = (a_ga_l5 or 1.5) * a_def_mult

        # Attendu buts par equipe ce match (sur stats ajustees)
        lam_h_match = max(0.2, (adj_h_gf + adj_a_ga) / 2)
        lam_a_match = max(0.2, (adj_a_gf + adj_h_ga) / 2)

        t_h = team_tier(home)
        t_a = team_tier(away)
        tier_descr = f"tier {home}={t_h} vs {away}={t_a}"

        # ── Ajustement formation : +/- 10% sur λ adverse selon la formation ──
        h_form_mult, h_form_descr = formation_adjust((lineup.get("home") or {}).get("formation"))
        a_form_mult, a_form_descr = formation_adjust((lineup.get("away") or {}).get("formation"))
        # La formation home affecte le λ adverse (away)
        lam_a_match *= h_form_mult
        lam_h_match *= a_form_mult

        def _adjust_lambda_for_absences(lam_team, sig, unavailable_names):
            """Reduit λ si top scorers manquent. Penalty max : -30%."""
            if not sig.get("top_scorers"): return lam_team, []
            total_goals = sum(p["goals"] for p in sig["top_scorers"])
            if total_goals == 0: return lam_team, []
            absent_share = 0
            absent_names = []
            for p in sig["top_scorers"]:
                if p["name"].lower() in unavailable_names:
                    absent_share += p["goals"] / total_goals
                    absent_names.append(p["name"])
            reduction = min(0.3, absent_share * 0.5)
            return lam_team * (1 - reduction), absent_names

        lam_h_match, h_absent_stars = _adjust_lambda_for_absences(lam_h_match, h_sig, h_unavailable)
        lam_a_match, a_absent_stars = _adjust_lambda_for_absences(lam_a_match, a_sig, a_unavailable)

        # Pour les amicaux internationaux, on enrichit avec les stats CLUB
        # des joueurs (via fotmob_client.player()). Les seasonGoals/Apps de la
        # lineup sont les stats NAT TEAM (souvent 0-3 buts en 5 apps), donc
        # tres peu predictifs. Le club_stats (LaLiga, PL, Serie A) + L10
        # donnent un signal vraiment exploitable.
        try:
            from fotmob_client import player as fm_player
        except Exception:
            fm_player = None

        def _player_lambda(starter):
            """Renvoie (lambda_per_match, source, descr).

            IMPORTANT : pour un match en SELECTION, les stats nat team sont
            BIEN plus predictives que les stats club. Marcus Thuram cartonne
            a l'Inter (0.7 G/m club) mais marque jamais en EDF (0.20 G/m nat).
            Pour France vs X, il faut utiliser 0.20 pas 0.7.

            Formule :
            - w_nat = 0.70 si nat_n >= 5  (priorise nat)
            - w_nat = 0.55 si nat_n in [3, 5]
            - w_nat = 0    si nat_n < 3   (echantillon trop petit)
            + boost recence : but(s) dans les 3 derniers nat -> +10% / but
            """
            pid = starter.get("id")
            ln_nat_g = starter.get("goals")
            ln_nat_a = starter.get("apps")

            club_lam = None
            club_src = None
            club_descr = None
            nat_lam = None
            nat_n = 0
            nat_src = None
            nat_descr = None
            last3_goals = 0

            if fm_player and pid:
                try:
                    pdata = fm_player(pid) or {}
                except Exception:
                    pdata = {}
                l10 = pdata.get("l10") or {}
                club = pdata.get("club_stats") or {}
                ns = pdata.get("nat_stats") or {}

                # Club lambda
                if l10.get("n", 0) >= 5 and l10.get("goals_pm") is not None:
                    club_lam = l10["goals_pm"]
                    club_src = f"L10 {club.get('league','club')}"
                    club_descr = f"L10 club : {l10['goals']}G/{l10['n']}m"
                elif club.get("matches") and club.get("goals") is not None:
                    g, m = club["goals"], club["matches"]
                    if m >= 5:
                        club_lam = g / m
                        club_src = f"Saison {club.get('league','club')}"
                        club_descr = f"Saison club : {g}G/{m}m"

                # Nat lambda via fm_player.nat_stats (carriere recente)
                if ns.get("n", 0) >= 3 and ns.get("goals_pm") is not None:
                    nat_lam = ns["goals_pm"]
                    nat_n = ns["n"]
                    nat_src = f"nat {ns.get('team','')}"
                    nat_descr = f"selection {ns['goals']}G/{ns['n']}m ({nat_lam:.2f}/m)"
                    last3_goals = ns.get("last3_goals", 0) or 0

            # Fallback nat stats depuis la lineup (saison nat courante)
            if nat_lam is None and ln_nat_g is not None and ln_nat_a and ln_nat_a >= 3:
                nat_lam = ln_nat_g / ln_nat_a
                nat_n = ln_nat_a
                nat_src = "nat (lineup)"
                nat_descr = f"selection {ln_nat_g}G/{ln_nat_a}m"

            # Drought penalty + meta nat (minutes/m, dernier match)
            drought_1 = False
            drought_2 = False
            nat_meta = ""
            if fm_player and pid:
                try:
                    pdata2 = fm_player(pid) or {}
                    ns2 = pdata2.get("nat_stats") or {}
                    drought_1 = bool(ns2.get("drought_1_match"))
                    drought_2 = bool(ns2.get("drought_2_matches"))
                    # Meta : minutes moyennes + dernier match nat
                    mins_pm = ns2.get("minutes_pm")
                    lm_mins = ns2.get("last_match_mins") or 0
                    lm_g    = ns2.get("last_match_g") or 0
                    lm_a    = ns2.get("last_match_a") or 0
                    bits = []
                    if mins_pm and mins_pm >= 60:
                        bits.append(f"titu nat ({int(mins_pm)}min/m)")
                    elif mins_pm and mins_pm >= 30:
                        bits.append(f"rotatif nat ({int(mins_pm)}min/m)")
                    if lm_mins:
                        evt = f"{lm_g}G+{lm_a}A" if (lm_g or lm_a) else "0G+0A"
                        bits.append(f"last match {lm_mins}min ({evt})")
                    if bits:
                        nat_meta = " | " + " · ".join(bits)
                except Exception:
                    pass

            if club_lam is not None and nat_lam is not None:
                # POIDS NAT ELEVE : pour selection, le passe nat prime sur club
                w_nat = 0.70 if nat_n >= 5 else 0.55
                final_lam = club_lam * (1 - w_nat) + nat_lam * w_nat
                recency_note = ""
                if last3_goals >= 1:
                    boost = 1.0 + 0.10 * min(2, last3_goals)
                    final_lam *= boost
                    recency_note = f", 🔥 {last3_goals}G/3 derniers nat (+{int((boost-1)*100)}%)"
                if drought_2:
                    final_lam *= 0.40
                    recency_note += " · ⚠️ 2 derniers matchs nat 60+min 0G+0A (-60%)"
                elif drought_1:
                    final_lam *= 0.55
                    recency_note += " · ⚠️ dernier match nat 60+min 0G+0A (-45%)"
                src = f"nat-weighted (club {int((1-w_nat)*100)}%/nat {int(w_nat*100)}%)"
                descr = f"{nat_descr} · {club_descr}{recency_note}{nat_meta}"
                return final_lam, src, descr
            if nat_lam is not None and nat_n >= 5:
                if last3_goals >= 1:
                    nat_lam *= 1.0 + 0.10 * min(2, last3_goals)
                if drought_2: nat_lam *= 0.40
                elif drought_1: nat_lam *= 0.55
                return nat_lam, nat_src, nat_descr
            if club_lam is not None:
                # Pas de data nat -> on amorti le club car le passe club ne
                # se transpose pas tel quel en selection (different role,
                # tactique, partenaires, niveau d'opposition).
                amortized = club_lam * 0.65
                return (amortized, f"{club_src} amorti -35%",
                        f"{club_descr} (amorti car pas de stats nat dispo)")
            return (None, None, None)

        def _friendly_player_picks(side, team_name, lam_team_match, side_absent, sig):
            picks_out = []
            t = lineup.get(side) or {}
            pman = (sig or {}).get("penaltyman")
            pman_name = (pman or {}).get("name", "").lower() if pman else ""
            pman_pen = (pman or {}).get("penalties", 0) if pman else 0
            for s in (t.get("starters") or []):
                name = s.get("name") or ""
                pos_id = s.get("pos_id") or 0
                if not name: continue
                if pos_id in (1,): continue  # skip gardien
                if name.lower() in side_absent: continue
                lam_player, source, descr = _player_lambda(s)
                if lam_player is None or lam_player < 0.15:
                    continue
                # Boost si penaltyman attitre (>=2 penalties tires cette saison) :
                # un buteur designe sur penalty a ~15% de chance de marquer un
                # penalty / match en plus d'un but normal -> boost λ de 15%.
                is_penaltyman = (name.lower() == pman_name and pman_pen >= 2)
                if is_penaltyman:
                    lam_player = lam_player * 1.15
                    descr = f"{descr} + penaltyman ({pman_pen} pens cette saison)"
                # Ajuste λ selon attendu de l'equipe ce match
                # (calibration : equipe joue typiquement ~1.5 buts/m en club)
                lam_p_match = lam_player * (lam_team_match / 1.5)
                p_scores = 1 - _math_p.exp(-lam_p_match)
                p_scores_2 = 1 - _math_p.exp(-lam_p_match) * (1 + lam_p_match)

                # Statut compo : si predicted (et pas confirmed), on prefixe
                # le reasoning avec un warning et on baisse la conf de 5pts.
                lineup_type = (lineup.get("type") or "").lower()
                is_predicted = lineup_type in ("predicted", "")  # vide = predicted par defaut
                lineup_warn = ""
                if is_predicted:
                    lineup_warn = "⚠️ Compo PROBABLE (a confirmer 1h avant). "
                conf_penalty = 5 if is_predicted else 0

                conf_scores = round(p_scores * 100) - conf_penalty
                # Seuil 45 pour amicaux nat team (les EDF marquent moins
                # qu'une attaque club ; un Mbappé à 50% est déjà très solide)
                if conf_scores >= 45:
                    pen_tag = " (penaltyman)" if is_penaltyman else ""
                    picks_out.append({
                        "kind": "marque",
                        "name": name,
                        "side": side,
                        "team": team_name,
                        "lam":  lam_p_match,
                        "conf": min(82, conf_scores),
                        "p":    p_scores,
                        "lineup_status": "predicted" if is_predicted else "confirmed",
                        "reasoning": (
                            f"{lineup_warn}{name}{pen_tag} : {descr}. "
                            f"λ ajusté à {lam_p_match:.2f} ({team_name} attendu {lam_team_match:.1f} buts) "
                            f"→ P(marque) ~{conf_scores}%."
                        ),
                    })
                conf_double = round(p_scores_2 * 100) - conf_penalty
                if conf_double >= 22 and lam_p_match >= 0.65:
                    picks_out.append({
                        "kind": "double_buteur",
                        "name": name,
                        "side": side,
                        "team": team_name,
                        "lam":  lam_p_match,
                        "conf": min(60, conf_double + 10),
                        "p":    p_scores_2,
                        "lineup_status": "predicted" if is_predicted else "confirmed",
                        "reasoning": (
                            f"{lineup_warn}{name} : {descr}. "
                            f"Attendu ~{lam_p_match:.1f} buts ce match - P(2+) ~{conf_double}% (cote elevee)."
                        ),
                    })

                # ── Pick PASSEUR (passe décisive uniquement) ────────────────
                # Règle user : distinction claire Buteur vs Passeur (2 paris
                # bien différents). λ Passeur = uniquement les ASSISTS, pas
                # G+A combinés. Marché bookmaker = player_assists.
                pid_pas = s.get("id")
                lam_passeur = None
                pas_descr = None
                if fm_player and pid_pas:
                    try:
                        pdata2 = fm_player(pid_pas) or {}
                    except Exception:
                        pdata2 = {}
                    ns_pas = pdata2.get("nat_stats") or {}
                    l10_pas = pdata2.get("l10") or {}
                    if ns_pas.get("n", 0) >= 3:
                        # On utilise les assists UNIQUEMENT (par-match)
                        nat_a_pm = ns_pas.get("assists_pm", 0) or 0
                        # Pour le club : assists/match L10
                        club_a_pm = l10_pas.get("assists_pm") or 0
                        w_nat_pas = 0.80 if ns_pas.get("n", 0) >= 5 else 0.6
                        lam_passeur = club_a_pm * (1 - w_nat_pas) + nat_a_pm * w_nat_pas
                        # Drought penalty (pas d'assists récentes)
                        if ns_pas.get("drought_2_matches"):
                            lam_passeur *= 0.50
                        pas_descr = (f"selection {ns_pas.get('assists',0)}A/{ns_pas.get('n',0)}m "
                                     f"({nat_a_pm:.2f} passes/m nat)")
                if lam_passeur and lam_passeur >= 0.20:
                    lam_pas_match = lam_passeur * (lam_team_match / 1.5)
                    p_passeur = 1 - _math_p.exp(-lam_pas_match)
                    conf_pas = round(p_passeur * 100) - conf_penalty
                    # Seuil 35 pour passeur (cote ~2.85 = tier fun OK)
                    if conf_pas >= 35:
                        picks_out.append({
                            "kind": "passeur",
                            "name": name,
                            "side": side,
                            "team": team_name,
                            "lam":  lam_pas_match,
                            "conf": min(75, conf_pas),
                            "p":    p_passeur,
                            "lineup_status": "predicted" if is_predicted else "confirmed",
                            "reasoning": (
                                f"{lineup_warn}{name} délivre une passe décisive : {pas_descr}. "
                                f"P(passe décisive) ~{conf_pas}%."
                            ),
                        })
            return picks_out

        h_friendly_picks = _friendly_player_picks("home", home, lam_h_match, h_unavailable, h_sig)
        a_friendly_picks = _friendly_player_picks("away", away, lam_a_match, a_unavailable, a_sig)
        # On stocke pour merger plus bas avec home_players/away_players
        # (qui sont vides pour les nat teams)
        friendly_picks = sorted(h_friendly_picks + a_friendly_picks,
                                key=lambda x: x["conf"], reverse=True)[:4]

        # ── Pick team : "1+ penalty marque dans le match" ───────────────────
        # Si une des 2 equipes a un taux de penaltys/match eleve ET un
        # penaltyman fiable titulaire, on propose ce pick.
        for sig, side_starters_team, side_label in [
            (h_sig, lineup.get("home"), home),
            (a_sig, lineup.get("away"), away),
        ]:
            pman = sig.get("penaltyman")
            if not pman: continue
            # Verifie que le penaltyman est titulaire (sinon perte de signal)
            starters_names = {(s.get("name") or "").lower()
                              for s in ((side_starters_team or {}).get("starters") or [])}
            if pman["name"].lower() not in starters_names: continue
            # Heuristique : >=3 pens marques sur la saison = haute probabilite
            # qu'il y ait des penaltys siffles. P(>=1 penalty marque) ~50-65%.
            if pman["penalties"] >= 4:
                conf = min(72, 55 + pman["penalties"] * 2)
                add("team_penalty", "Penalty",
                    f"1+ penalty marque dans le match", None, conf,
                    f"{side_label} a marque {pman['penalties']} penaltys cette saison "
                    f"(penaltyman titulaire : {pman['name']}). Forte probabilite "
                    f"qu'un penalty soit siffle.", hf)
                break  # on en propose un seul max
    else:
        friendly_picks = []
    def _names_unavailable(side):
        t = lineup.get(side) or {}
        out_absent = set()
        out_doubt  = set()
        for u in (t.get("unavailable") or []):
            name = (u.get("name") or "").strip().lower()
            if not name: continue
            ret = (u.get("return") or "").lower()
            # Doubtful = incertain mais peut jouer
            if "doubt" in ret:
                out_doubt.add(name)
            else:
                out_absent.add(name)
        return out_absent, out_doubt

    h_absent, h_doubt = _names_unavailable("home")
    a_absent, a_doubt = _names_unavailable("away")

    def _is_absent(player, side_absent):
        n = (player.get("name") or player.get("shortName") or "").strip().lower()
        if not n: return False
        if n in side_absent: return True
        # Match partiel (au cas ou les noms different legerement)
        for ab in side_absent:
            if ab and ab in n: return True
            if n and n in ab: return True
        return False

    # Annote les doubtful (penalite confiance) et exclut les absents
    home_players_filt = []
    for p in home_players:
        if _is_absent(p, h_absent):
            continue
        if _is_absent(p, h_doubt):
            p = dict(p); p["_doubtful"] = True
        home_players_filt.append(p)
    away_players_filt = []
    for p in away_players:
        if _is_absent(p, a_absent):
            continue
        if _is_absent(p, a_doubt):
            p = dict(p); p["_doubtful"] = True
        away_players_filt.append(p)

    home_conceded = away_ts_data.get("conceded_pm", 0) if away_ts_data else 0
    away_conceded = home_ts_data.get("conceded_pm", 0) if home_ts_data else 0

    home_pp_raw = player_picks_contextual(home_players_filt, ap, ar, home_conceded, btts_p, match_odds=match_player_odds)
    away_pp_raw = player_picks_contextual(away_players_filt, hp, hr, away_conceded, btts_p, match_odds=match_player_odds)

    # Marquer les picks de joueurs doubtful (confidence x0.85)
    for pk in home_pp_raw + away_pp_raw:
        pname = (pk.get("player") or "").strip().lower()
        if pname in h_doubt or pname in a_doubt:
            pk["confidence"] = round(pk["confidence"] * 0.85)
            pk["reasoning"] = f"⚠️ {pname.title()} INCERTAIN · " + pk.get("reasoning", "")

    # Boost penaltyman + reduction pour star absent (championnats et amicaux)
    def _is_penaltyman_pick(pk, sig):
        pman = (sig or {}).get("penaltyman")
        if not pman: return False
        pname = (pk.get("player") or "").strip().lower()
        if pname != pman["name"].lower(): return False
        return pman["penalties"] >= 2
    for pk in home_pp_raw:
        if _is_penaltyman_pick(pk, h_sig) and pk.get("type") in ("Buteur","Joueur décisif"):
            pk["confidence"] = min(95, round(pk["confidence"] * 1.10))
            pk["reasoning"] = f"⚽ Penaltyman · " + pk.get("reasoning", "")
    for pk in away_pp_raw:
        if _is_penaltyman_pick(pk, a_sig) and pk.get("type") in ("Buteur","Joueur décisif"):
            pk["confidence"] = min(95, round(pk["confidence"] * 1.10))
            pk["reasoning"] = f"⚽ Penaltyman · " + pk.get("reasoning", "")

    # ── Max 3 props joueurs PAR MATCH (pas par équipe) ──────────────────────
    # Mélange les deux listes, trie par confiance, garde les 3 meilleurs
    all_pp = []
    for pk in home_pp_raw: pk["team"] = "home"; all_pp.append(pk)
    for pk in away_pp_raw: pk["team"] = "away"; all_pp.append(pk)
    all_pp.sort(key=lambda x: x["confidence"], reverse=True)

    # Assure diversité : max 2 du même joueur, max 2 de la même équipe
    final_pp, seen_player_types, team_count = [], set(), {"home":0,"away":0}
    for pk in all_pp:
        pkey = f"{pk['player']}_{pk['type']}"
        team = pk.get("team","home")
        if pkey in seen_player_types: continue
        if team_count[team] >= 2: continue
        seen_player_types.add(pkey)
        team_count[team] += 1
        final_pp.append(pk)
        if len(final_pp) >= 3: break

    home_pp = [p for p in final_pp if p.get("team") == "home"]
    away_pp = [p for p in final_pp if p.get("team") == "away"]

    # ── Garantie min 1 prop joueur par match ──────────────────────────────────
    if not final_pp:
        # Prend le meilleur attaquant/milieu sans seuil
        candidates_fallback = []
        for side_key, plist in [("home", home_players), ("away", away_players)]:
            for p in plist:
                if p.get("position") in ("F","M") and p.get("appearances",0) >= 3:
                    candidates_fallback.append((side_key, p))
        candidates_fallback.sort(
            key=lambda x: x[1].get("xG_pm",0)*0.6 + x[1].get("goals_pm",0)*0.4,
            reverse=True
        )
        if candidates_fallback:
            side_key, bp = candidates_fallback[0]
            name   = bp.get("shortName", bp.get("name",""))
            gpm    = bp.get("goals_pm", 0)
            xgpm   = bp.get("xG_pm", 0)
            apps   = bp.get("appearances", 0)
            goals  = bp.get("goals", 0)
            pos    = bp.get("position","")
            is_sub = bp.get("is_sub", False)
            opp_p  = ap if side_key=="home" else hp
            opp_r  = ar if side_key=="home" else hr
            opp_c  = away_conceded if side_key=="home" else home_conceded
            weak   = defense_weakness(opp_p, opp_r, opp_c)
            lam    = (xgpm+gpm)/2 if xgpm else gpm
            lam_c  = lam * (1+weak*0.5) * {"F":1.15,"M":1.0}.get(pos,0.9)
            conf   = max(20, round(poisson_at_least(lam_c, 1)))
            # Lookup cote anytime_scorer reelle (meme pour le fallback)
            _odds_keys = list((match_player_odds or {}).keys())
            _odds_key = _match_odds_name(name, _odds_keys)
            _player_odds = (match_player_odds or {}).get(_odds_key) if _odds_key else None
            _cote, _book, _books_list = _lookup_player_cote(_player_odds, "anytime_scorer")
            fb_pk  = {
                "player": name, "position": pos, "is_sub": is_sub,
                "type": "Buteur", "label": f"{name} marque",
                "team": side_key, "cote": _cote, "book": _book, "books": _books_list,
                "confidence": conf,
                "reasoning": f"{goals}G en {apps} matchs ({round(gpm*100,1)}%/match) · xG: {xgpm:.2f}/match",
                "context": {"weakness": weak}, "stats": {"goals": goals, "apps": apps}
            }
            if side_key == "home":
                home_pp = [fb_pk]
            else:
                away_pp = [fb_pk]

    # ── Tirs équipe (TOP-5 europeens + UEFA uniquement) ─────────────────────
    # Les stats tirs/SoT ne sont pas fiables pour les championnats secondaires
    # ou hors Europe (FotMob ne les expose pas, ou de maniere incomplete).
    # Symptome user : "Boston River vs Liverpool FC (Uruguay Primera) -> ? tirs cadres".
    # Pour ces ligues on emet uniquement : Resultat/DC, Buteur/DC Buteur, Over/Under buts.
    SHOTS_RELIABLE_LEAGUES = {
        17,    # Premier League
        8,     # La Liga
        35,    # Bundesliga
        23,    # Serie A
        34,    # Ligue 1
        7,     # Champions League
        679,   # Europa League
        17015, # Conference League
    }
    if league_id in SHOTS_RELIABLE_LEAGUES:
        shots_props = team_shots_props(home_ts_data, away_ts_data, home_rec, away_rec,
                                       h2h_shots_d, home, away, hp, ap, match_odds=odds,
                                       home_form=hf, away_form=af)
        team_picks.extend(shots_props)

    # ── Inject friendly player picks (lineup-based) ─────────────────────────
    # Pour les amicaux, on construit les picks buteur via lineup.starters
    # (utilise les seasonGoals du club du joueur) au lieu des stats championnat.
    # Seuil cote minimum pour les picks buteur/joueur décisif : 1.40.
    # Règle utilisateur : "pour un buteur même double chance c'est minimum
    # une cote à 1.40, en dessous tu ne proposes pas". Les picks avec cote
    # estimée < 1.40 sont skippés (l'edge est trop faible pour valoir la mise).
    MIN_COTE_BUTEUR = 1.40
    if friendly_picks:
        # Préchauffage du lookup des cotes anytime_scorer book (data/foot_player_odds.json
        # via player_odds_all → match_player_odds). On lookup la vraie cote
        # bookmaker pour chaque joueur, et c'est ELLE qui est filtrée sur 1.40
        # (la cote_min calculée par le modèle est gardée comme fair value
        # de référence mais n'est plus utilisée pour le filtre).
        _po_keys = list((match_player_odds or {}).keys())
        for fp in friendly_picks:
            cote_est = round(1 / max(0.01, fp["p"]), 2) if fp["p"] > 0 else None
            # Lookup cote bookmaker depuis foot_player_odds.json
            # Chaque kind utilise SON marché propre :
            #   - "marque"        → anytime_scorer (marquer ≥ 1 but)
            #   - "double_buteur" → scorer_2_plus  (marquer ≥ 2 buts, cote ~6-10)
            # Sinon cote_book reste None et on affiche cote_min (estimation modèle).
            cote_book = None
            book = None
            if fp["kind"] == "marque":
                _ok = _match_odds_name(fp["name"], _po_keys)
                if _ok:
                    _player_odds = (match_player_odds or {}).get(_ok) or {}
                    _scorer = _player_odds.get("anytime_scorer") or {}
                    cote_book = _scorer.get("over")
                    book = _scorer.get("book")
            elif fp["kind"] == "double_buteur":
                _ok = _match_odds_name(fp["name"], _po_keys)
                if _ok:
                    _player_odds = (match_player_odds or {}).get(_ok) or {}
                    _scorer2 = _player_odds.get("scorer_2_plus") or {}
                    cote_book = _scorer2.get("over")
                    book = _scorer2.get("book")
            elif fp["kind"] == "passeur":
                _ok = _match_odds_name(fp["name"], _po_keys)
                if _ok:
                    _player_odds = (match_player_odds or {}).get(_ok) or {}
                    _ass = _player_odds.get("assists") or {}
                    cote_book = _ass.get("over")
                    book = _ass.get("book")
            # Filtre cote min : utilise la cote book si dispo, sinon cote_est
            # Si cote book < 1.40, skip
            cote_for_filter = cote_book if cote_book is not None else cote_est
            if cote_for_filter is not None and cote_for_filter < MIN_COTE_BUTEUR:
                continue
            if fp["kind"] == "marque":
                label = f"{fp['name']} marque"
                ptype = "Buteur"
            elif fp["kind"] == "passeur":
                label = f"{fp['name']} délivre une passe décisive"
                ptype = "Passeur"
            else:  # double_buteur
                label = f"{fp['name']} marque 2+ buts"
                ptype = "Double buteur"
            pick = {
                "player":     fp["name"],
                "position":   "F",
                "is_sub":     fp.get("lineup_status") == "predicted",
                "type":       ptype,
                "label":      label,
                "cote":       cote_book,    # vraie cote book si dispo, sinon None
                "cote_min":   cote_est,     # estimation fair value du modèle (= 1/proba)
                "book":       book,
                "books":      [],
                "confidence": int(fp["conf"]),
                "reasoning":  fp["reasoning"],
                "context":    {"source": "lineup", "lam": round(fp["lam"], 2),
                               "lineup_status": fp.get("lineup_status", "predicted")},
                "stats":      {"goals": None, "apps": None, "gpm": round(fp["lam"], 3),
                               "xgpm": None},
                "team":       fp["side"],
                "lineup_status": fp.get("lineup_status", "predicted"),
            }
            if fp["side"] == "home":
                home_pp.append(pick)
            else:
                away_pp.append(pick)
        # Tri par confiance
        home_pp.sort(key=lambda x: x.get("confidence", 0), reverse=True)
        away_pp.sort(key=lambda x: x.get("confidence", 0), reverse=True)

    # ── Tag tier (safe/ok/fun) sur tous les picks finaux ────────────────────
    def _tag_tier(lst):
        for p in lst or []:
            if "tier" in p: continue
            p["tier"] = _classify_tier(p.get("confidence"), p.get("cote"),
                                       p.get("is_fun", False))
    _tag_tier(team_picks)
    _tag_tier(home_pp)
    _tag_tier(away_pp)
    _tag_tier(fun_picks)

    # ── Calibration empirique : annote WR observée par bucket conf ──────────
    # Lit data/learning_stats.json (calibration foot/foot_wc), trouve le bucket
    # du pick et ajoute une note dans le reasoning si déviation significative
    # (≥ 10pp d'écart entre claim et observé sur N ≥ 20 picks).
    try:
        import json as _jcal
        with open("data/learning_stats.json", encoding="utf-8") as _cf:
            _calib = _jcal.load(_cf)
    except Exception:
        _calib = None
    if _calib:
        # Pour WC : utilise foot_wc si bucket assez riche, sinon foot global.
        # En début de tournoi, peu de samples WC → on retombe sur foot global.
        is_wc_match = (league_id == WC_LEAGUE_ID)
        cal_foot    = (_calib.get("calibration") or {}).get("foot") or {}
        cal_wc      = (_calib.get("calibration") or {}).get("foot_wc") or {}
        cal_table   = cal_foot
        if is_wc_match:
            # Si le bucket WC a ≥ 20 samples, on prend la version WC, sinon foot
            cal_table = {b: (cal_wc.get(b) if (cal_wc.get(b, {}).get("n", 0) >= 20)
                              else cal_foot.get(b, {}))
                         for b in set(list(cal_foot.keys()) + list(cal_wc.keys()))}
        def _bucket_for(conf):
            if conf is None: return None
            if conf < 40: return "<40"
            if conf < 50: return "40-49"
            if conf < 60: return "50-59"
            if conf < 70: return "60-69"
            if conf < 80: return "70-79"
            if conf < 90: return "80-89"
            return "90+"
        def _tag_calibration(lst):
            for p in lst or []:
                b = _bucket_for(p.get("confidence"))
                if not b: continue
                t = cal_table.get(b)
                if not t or t.get("n", 0) < 20: continue
                obs = t.get("wr_observed", 0)
                exp = t.get("expected_wr", 0)
                delta = obs - exp
                if abs(delta) >= 10:
                    note = (f"📉 Calibration : bucket {b}% historiquement à WR {obs:.0f}% "
                            f"(claim {exp:.0f}%, n={t['n']}) — sur-estimé de {abs(delta):.0f}pp")
                    p["reasoning"] = (p.get("reasoning") or "") + "\n" + note
                    p["calibration_delta"] = round(delta, 1)
        _tag_calibration(team_picks)
        _tag_calibration(home_pp)
        _tag_calibration(away_pp)
        _tag_calibration(fun_picks)

    # ── Edge : compare conf_modele a la cote book quand dispo ───────────────
    # edge_pp = (P_modele - P_book) * 100. > 0 = value pick, < 0 = arnaque.
    def _tag_edge(lst):
        for p in lst or []:
            cote = p.get("cote")
            conf = p.get("confidence")
            if cote and conf:
                try:
                    p_book = 1.0 / float(cote)
                    edge_pp = round((conf / 100.0 - p_book) * 100, 1)
                    p["edge_pp"] = edge_pp
                except Exception:
                    pass
    _tag_edge(team_picks)
    _tag_edge(home_pp)
    _tag_edge(away_pp)
    _tag_edge(fun_picks)

    # ── Marque les TOP 5 historises (meme score que _save_to_history) ─────
    # Aligné avec HIST_MIN_COTE = 1.30 et HIST_MAX_PER_MATCH = 5 en sortie.
    HIST_MIN_COTE_DISPLAY = 1.30
    HIST_MAX_PER_MATCH_DISPLAY = 5

    def _eff_cote(pk):
        c = pk.get("cote")
        if c and c > 0: return float(c)
        cmin = pk.get("cote_min")
        if cmin and cmin > 0: return float(cmin)
        conf = pk.get("confidence") or 0
        if conf <= 0: return None
        return round((1.0 / (conf/100.0)) * 0.92, 2)

    def _hist_score(pk):
        edge = pk.get("edge_pp") or 0
        conf = pk.get("confidence") or 0
        tier_bonus = {"safe": 12, "ok": 6, "fun": 0}.get(pk.get("tier", ""), 0)
        return edge * 1.5 + conf * 0.3 + tier_bonus

    all_pool = (
        [(p, "team")    for p in team_picks] +
        [(p, "player")  for p in home_pp] +
        [(p, "player")  for p in away_pp] +
        [(p, "fun")     for p in fun_picks]
    )
    # Eligibilite : meme regle que _filter_for_history — un pick passe si
    # sa cote connue (réelle OU modèle) >= seuil, ou s'il n'a aucune cote
    # connue (pas de cote_min ni cote réelle → on ne bloque pas).
    def _is_elig_display(pk):
        c    = pk.get("cote")
        cmin = pk.get("cote_min")
        if c and c > 0:
            return c >= HIST_MIN_COTE_DISPLAY
        if cmin and cmin > 0:
            return cmin >= HIST_MIN_COTE_DISPLAY
        return True  # pas de cote connue → on garde
    elig = [(p, c) for p, c in all_pool if _is_elig_display(p)]
    elig.sort(key=lambda x: _hist_score(x[0]), reverse=True)
    top3_ids = set(id(p) for p, _ in elig[:HIST_MAX_PER_MATCH_DISPLAY])
    for p, _ in all_pool:
        p["history_top3"] = id(p) in top3_ids

    # ── Dédoublonnage : un même match peut générer 2 picks pour la même
    # direction logique (branche standard 'home_win' + branche WC 'wc_home_win').
    # Sur Telegram et le site, l'user voit 2 picks "France gagne" différents.
    # On dédoublonne en gardant celui avec la confidence la plus haute.
    DIR_CANONICAL = {
        "wc_home_win": "h_win",   "home_win":   "h_win",
        "wc_away_win": "a_win",   "away_win":   "a_win",
        "wc_draw":     "draw",    "draw":       "draw",
        "wc_dc_1x":    "dc_1x",   "home_dc":    "dc_1x",  "dc_1x": "dc_1x",
        "wc_dc_x2":    "dc_x2",   "away_dc":    "dc_x2",  "dc_x2": "dc_x2",
        "wc_dc_12":    "dc_12",   "no_draw":    "dc_12",  "12":    "dc_12",
        "wc_over_15":  "o15",     "over_15":    "o15",    "over15": "o15",
        "wc_under_15": "u15",     "under_15":   "u15",
        "wc_over_25":  "o25",     "over_25":    "o25",    "over25": "o25",
        "wc_under_25": "u25",     "under_25":   "u25",
        "wc_over_35":  "o35",     "over_35":    "o35",    "over35": "o35",
        "wc_btts_yes": "btts_y",  "btts_yes":   "btts_y", "btts":  "btts_y",
        "wc_btts_no":  "btts_n",  "btts_no":    "btts_n",
    }
    def _dedup_by_direction(picks):
        by_canonical = {}
        passthrough = []
        for pk in picks:
            d = (pk.get("direction") or "").lower()
            canon = DIR_CANONICAL.get(d)
            if canon is None:
                # Direction non gérée par dédup (ex: home_scores_2, score exact, etc.) → garde
                passthrough.append(pk)
                continue
            cur = by_canonical.get(canon)
            if cur is None:
                by_canonical[canon] = pk
            else:
                # Garde celui avec la conf la plus haute (en cas d'égalité, préfère
                # le wc_ qui a un reasoning plus riche)
                cur_conf = cur.get("confidence") or 0
                new_conf = pk.get("confidence") or 0
                cur_wc = (cur.get("direction") or "").startswith("wc_")
                new_wc = (pk.get("direction") or "").startswith("wc_")
                if new_conf > cur_conf or (new_conf == cur_conf and new_wc and not cur_wc):
                    by_canonical[canon] = pk
        return list(by_canonical.values()) + passthrough

    team_picks = _dedup_by_direction(team_picks)
    fun_picks  = _dedup_by_direction(fun_picks)

    # ── Cohérence Score exact vs totals Plus/Moins ──────────────────────────
    # Si on émet "Plus de 1.5 buts" / "Plus de 2.5 buts" en team pick et que
    # le score exact propose un total qui contredit, on remplace le score
    # exact par le plus probable qui RESPECTE le seuil.
    has_over15 = any((p.get("direction") or "").lower() in ("over_15","wc_over_15") for p in team_picks)
    has_over25 = any((p.get("direction") or "").lower() in ("over_25","wc_over_25") for p in team_picks)
    has_under15 = any((p.get("direction") or "").lower() in ("under_15","wc_under_15") for p in team_picks)
    has_under25 = any((p.get("direction") or "").lower() in ("under_25","wc_under_25") for p in team_picks)
    min_total_required = None
    max_total_allowed  = None
    if has_over25: min_total_required = 3
    elif has_over15: min_total_required = 2
    if has_under15: max_total_allowed = 1
    elif has_under25: max_total_allowed = 2

    def _score_total(pk):
        es = pk.get("exact_score")
        if es and len(es) == 2:
            try: return int(es[0]) + int(es[1])
            except: pass
        # Fallback : parse direction wc_score_H_A
        d = (pk.get("direction") or "").lower()
        if d.startswith("wc_score_"):
            parts = d.split("_")
            try: return int(parts[2]) + int(parts[3])
            except: pass
        return None

    if min_total_required is not None or max_total_allowed is not None:
        kept_fun = []
        for pk in fun_picks:
            d = (pk.get("direction") or "").lower()
            if d.startswith("wc_score_"):
                tot = _score_total(pk)
                if tot is None:
                    kept_fun.append(pk); continue
                if min_total_required is not None and tot < min_total_required:
                    # Cherche un meilleur score exact dans wc_top3
                    top3 = pk.get("wc_top3") or []
                    found = False
                    for (sh, sa), p_pct in [((t[0][0], t[0][1]), t[1]) for t in top3]:
                        if sh + sa >= min_total_required:
                            pk["exact_score"] = [sh, sa]
                            pk["direction"] = f"wc_score_{sh}_{sa}"
                            import re
                            m_lab = re.match(r"(.*?:\s*)(\S+)\s+(\d+-\d+)\s+(\S.*)", pk.get("label",""))
                            if m_lab:
                                pk["label"] = f"{m_lab.group(1)}{m_lab.group(2)} {sh}-{sa} {m_lab.group(4)}"
                            # p_pct est déjà en pourcentage (0-100) dans wc_top3
                            pk["confidence"] = max(5, round(p_pct))
                            p_proba = max(0.01, p_pct / 100.0)
                            pk["cote_min"] = round(1 / p_proba, 2)
                            found = True
                            break
                    if not found:
                        # Pas d'alternative cohérente → skip ce score exact
                        continue
                if max_total_allowed is not None and tot > max_total_allowed:
                    # Pareil dans l'autre sens : cherche score ≤ max
                    top3 = pk.get("wc_top3") or []
                    found = False
                    for (sh, sa), p_pct in [((t[0][0], t[0][1]), t[1]) for t in top3]:
                        if sh + sa <= max_total_allowed:
                            pk["exact_score"] = [sh, sa]
                            pk["direction"] = f"wc_score_{sh}_{sa}"
                            import re
                            m_lab = re.match(r"(.*?:\s*)(\S+)\s+(\d+-\d+)\s+(\S.*)", pk.get("label",""))
                            if m_lab:
                                pk["label"] = f"{m_lab.group(1)}{m_lab.group(2)} {sh}-{sa} {m_lab.group(4)}"
                            pk["confidence"] = max(5, round(p_pct))
                            p_proba = max(0.01, p_pct / 100.0)
                            pk["cote_min"] = round(1 / p_proba, 2)
                            found = True
                            break
                    if not found:
                        continue
            kept_fun.append(pk)
        fun_picks = kept_fun

    # ── Cohérence Match nul (fun) vs DC 12 "pas de nul" ─────────────────────
    # Si on a DC 12 (Iraq ou Norway, pas de nul) à conf ≥ 70% → ne pas
    # proposer "Match nul" en fun (contradictoire).
    has_dc12_strong = any(
        ((p.get("direction") or "").lower() in ("no_draw","12","wc_dc_12"))
        and (p.get("confidence") or 0) >= 70
        for p in team_picks
    )
    if has_dc12_strong:
        fun_picks = [p for p in fun_picks
                     if (p.get("direction") or "").lower() not in ("draw","wc_draw")]

    return team_picks, home_pp, away_pp, fun_picks

# ─── Tirs équipe ─────────────────────────────────────────────────────────────

def _weighted_avg(l5, l10, season, w5=0.50, w10=0.30, ws=0.20):
    """Moyenne ponderee L5/L10/Saison. Skip valeurs None."""
    vals = []
    if l5     is not None: vals.append((float(l5),     w5))
    if l10    is not None: vals.append((float(l10),    w10))
    if season is not None: vals.append((float(season), ws))
    if not vals: return None
    total_w = sum(w for _, w in vals)
    return sum(v * w for v, w in vals) / total_w


def _poisson_over(lam, k):
    """P(X > k) pour X ~ Poisson(lam). k peut etre fractionnaire (line 20.5)."""
    if lam is None or lam <= 0: return 0
    import math
    threshold = int(k)
    # P(X >= threshold + 1) = 1 - P(X <= threshold)
    cum = sum(math.exp(-lam) * lam**i / math.factorial(i) for i in range(threshold + 1))
    return round((1 - cum) * 100, 1)


def _generate_lines(expected, n_lines=3, spread=3.0):
    """Legacy: lignes centrees autour de expected. Pour compat tirs cadres internes."""
    if not expected or expected <= 0: return []
    def to_half(v): return round(v * 2) / 2 - (0 if (round(v * 2) % 2 == 1) else 0.5)
    if n_lines == 3:
        return sorted({to_half(expected - spread), to_half(expected), to_half(expected + spread)})
    return sorted({to_half(expected - spread + i * (2 * spread / (n_lines - 1))) for i in range(n_lines)})


# Plages de lignes autorisees pour les picks tirs (user spec).
# Hors plage = on n'emet PAS de pick (eviter les Plus de 19.5/Moins de 30.5
# qui correspondent a des cotes trop basses ou trop hautes pour avoir de la
# value reelle). Les bookmakers proposent souvent ces lignes mais notre
# modele est trop bruite aux extremes.
SHOTS_LINE_RANGES = {
    "total_shots":  (24.5, 29.5),
    "home_shots":   (10.5, 16.5),
    "away_shots":   (10.5, 16.5),
    "total_sot":    (7.5,  10.5),
    "home_sot":     (2.5,  6.5),
    "away_sot":     (2.5,  6.5),
}

# Lignes heuristiques (utilisees quand pas de cote bookmaker reelle dispo)
# Restreintes aux plages SHOTS_LINE_RANGES pour eviter les picks aberrants.
BOOKMAKER_LINES = {
    "total_shots":  [24.5, 25.5, 26.5, 27.5, 28.5, 29.5],
    "home_shots":   [10.5, 11.5, 12.5, 13.5, 14.5, 15.5, 16.5],
    "away_shots":   [10.5, 11.5, 12.5, 13.5, 14.5, 15.5, 16.5],
    "total_sot":    [7.5, 8.5, 9.5, 10.5],
    "home_sot":     [2.5, 3.5, 4.5, 5.5, 6.5],
    "away_sot":     [2.5, 3.5, 4.5, 5.5, 6.5],
}


def _filter_shot_lines_in_range(lines, market_key):
    """Filtre une liste de lignes pour ne garder que celles dans SHOTS_LINE_RANGES.

    Sert pour les lignes reelles bookmaker (FotMob) ET les lignes heuristiques :
    on n'emet jamais de pick sur une ligne hors plage, quelle que soit la source.
    """
    rng = SHOTS_LINE_RANGES.get(market_key)
    if not rng:
        return lines
    lo, hi = rng
    return [ln for ln in lines if lo <= ln <= hi]


def _fair_cote(probability):
    """
    Cote fair / break-even / seuil-valeur:
    - Si tu joues au-dessus = +EV
    - Si tu joues en-dessous = -EV (bookmaker te gruge)
    """
    if not probability or probability <= 0 or probability >= 100:
        return None
    return round(1 / (probability / 100), 2)


# ── CALIBRATION FOOT (basee sur analyse historique 207 picks) ────────────────
# Le modele foot est trop confiant sur les buckets hauts :
#   80-89% conf -> 68% WR (gap -16pp), 60-69% conf -> 48% WR (gap -16pp)
# Sweet spot 70-79% conf est OK (78% WR, +4pp). Solution : shrinkage doux.
FOOT_CAL_BASELINE = 60   # WR moyen foot observe
FOOT_CAL_ALPHA    = 0.75 # final = 0.75 * model + 0.25 * 60 (shrinkage doux)


def _foot_calibrate(conf):
    """Applique shrinkage doux pour compenser l'overconfidence du modele."""
    if conf is None: return None
    return round(FOOT_CAL_ALPHA * float(conf) + (1 - FOOT_CAL_ALPHA) * FOOT_CAL_BASELINE, 1)


def _value_tier(cote_min):
    """
    Note la qualite d'une opportunite selon le cote_min (= cote requise pour +EV):
    - Plus la cote_min est haute, plus c'est facile a trouver chez un bookmaker
    """
    if not cote_min: return None
    if cote_min >= 1.45: return ("🎯", "Belle value", "#22c55e")    # green - facile a beat
    if cote_min >= 1.30: return ("💎", "Value correcte", "#84cc16") # lime - jouable
    if cote_min >= 1.22: return ("⚠️", "Value serrée", "#f59e0b")  # amber - bookmaker souvent en dessous
    return ("🚫", "Quasi-impossible", "#94a3b8")  # gray - presque toujours -EV


# Sweet spot: conf 60-80% donne cote_min 1.25-1.67
# - >80% conf donne cote_min <1.25 = quasi-impossible a beat (bookmaker offre toujours moins)
# - <60% pas fiable
SWEET_SPOT_CONF = (60, 80)
MIN_CONF        = 60
MIN_COTE_MIN    = 1.20    # filtre: skip si cote_min < 1.20 (vraiment trop dur)


def _pick_line(expected, lines, line_label_prefix, kind, min_conf=MIN_CONF, sweet_spot=SWEET_SPOT_CONF):
    """
    Cherche LA ligne donnant une cote_min raisonnable (1.30+).
    Privilegie zones 60-75% confidence = cote_min 1.33-1.67 = vraies opportunites de value.
    """
    if not expected or expected <= 0 or not lines: return None
    sweet_lo, sweet_hi = sweet_spot
    sweet_candidates = []
    fallback = None
    fallback_dist = 0

    def _make_pick(line, direction, conf):
        cote_min = _fair_cote(conf)
        if cote_min is None or cote_min < MIN_COTE_MIN:
            return None  # filtre: trop pres certitude, no value
        label = (f"Plus de {line} {line_label_prefix}" if direction == "over"
                 else f"Moins de {line} {line_label_prefix}")
        return {
            "line": line, "direction": direction,
            "label": label,
            "confidence": round(conf), "kind": kind,
            "cote_min": cote_min,
            "value": _value_tier(cote_min),
        }

    for line in lines:
        p_over = _poisson_over(expected, line)
        # OVER
        if sweet_lo <= p_over <= sweet_hi:
            pick = _make_pick(line, "over", p_over)
            if pick: sweet_candidates.append(pick)
        elif p_over >= min_conf:
            pick = _make_pick(line, "over", p_over)
            if pick:
                dist = p_over - 50
                if dist > fallback_dist:
                    fallback = pick
                    fallback_dist = dist
        # UNDER
        p_under = 100 - p_over
        if sweet_lo <= p_under <= sweet_hi:
            pick = _make_pick(line, "under", p_under)
            if pick: sweet_candidates.append(pick)
        elif p_under >= min_conf:
            pick = _make_pick(line, "under", p_under)
            if pick:
                dist = p_under - 50
                if dist > fallback_dist:
                    fallback = pick
                    fallback_dist = dist

    if sweet_candidates:
        # Choix: la conf la plus haute DANS la sweet spot (meilleur edge potentiel)
        return max(sweet_candidates, key=lambda x: x["confidence"])
    return fallback


def _extract_bookmaker_shot_lines(match_odds):
    """
    Extrait les lignes bookmaker reelles pour tirs/tirs cadres depuis match_odds.
    """
    out = {
        "total_shots": [], "total_sot": [],
        "home_shots":  [], "away_shots": [],
    }
    if not match_odds:
        return out
    markets = match_odds.get("markets", [])
    for mk in markets:
        name = mk.get("marketName", "")
        cg = mk.get("choiceGroup")
        if not cg: continue
        try:
            line = float(cg)
        except:
            continue
        over_cote = under_cote = None
        for c in mk.get("choices", []):
            cn = c.get("name", "")
            try:
                dec = round(float(c.get("fractionalValue", 0)) + 1, 2)
            except:
                continue
            if cn.lower() == "over":   over_cote = dec
            elif cn.lower() == "under": under_cote = dec
        if over_cote and under_cote:
            if name == "Total shots":
                out["total_shots"].append((line, over_cote, under_cote))
            elif name == "Total shots on target":
                out["total_sot"].append((line, over_cote, under_cote))
            elif name == "Away team total shots":
                out["away_shots"].append((line, over_cote, under_cote))
            elif name == "Home team total shots":
                out["home_shots"].append((line, over_cote, under_cote))
    return out


def team_shots_props(home_ts, away_ts, home_recent, away_recent, h2h_shots, home_name, away_name, home_pos=10, away_pos=10, match_odds=None, home_form=None, away_form=None):
    """
    Genere des picks tirs/SoT.
    - Utilise les splits dom/ext (Betis home -> shots_home, Elche away -> shots_away)
    - Fallback sur overall (L5/L10/saison) si splits trop petits ou indisponibles.
    """
    props = []
    hr = home_recent or {}; ar = away_recent or {}

    # ── Stats contextuelles (selon role dans CE match) ─────────────────────
    def ctx_or_overall(side_dict, ctx_key, ctx_n_key, overall_l5, overall_l10, overall_season,
                       min_split_n=4):
        """
        Prefere la stat contextuelle (ex: shots_home) si echantillon >= min_split_n.
        Sinon blend 60% split + 40% overall pour compenser le petit echantillon.
        Sinon fallback complet sur overall.
        """
        ctx_val = side_dict.get(ctx_key)
        ctx_n   = side_dict.get(ctx_n_key, 0)
        overall = _weighted_avg(
            side_dict.get(overall_l5),
            side_dict.get(overall_l10),
            side_dict.get(overall_season),
        )
        if ctx_val is None:
            return overall, "overall"
        if ctx_n >= min_split_n + 2:
            # Echantillon suffisant: 80% split + 20% overall (pour reduire variance)
            if overall is None: return ctx_val, f"dom/ext ({ctx_n}m)"
            return ctx_val * 0.8 + overall * 0.2, f"dom/ext ({ctx_n}m)"
        if ctx_n >= min_split_n:
            # Echantillon limite: blend 60/40
            if overall is None: return ctx_val, f"dom/ext faible ({ctx_n}m)"
            return ctx_val * 0.6 + overall * 0.4, f"dom/ext blend ({ctx_n}m)"
        return overall, "overall (split trop petit)"

    # Home team joue a domicile -> stats "home"
    # Away team joue a l'exterieur -> stats "away"
    # Adversaire concede: home_team concede a domicile -> opp_shots_home
    #                    away_team concede a l'exterieur -> opp_shots_away

    # ── Stats PONDEREES par qualite adversaire (priorite) ─────────────────
    # Si dispo, on utilise la moyenne L10 ponderee selon similitude opp_rank avec aujourd'hui
    h_shots_w = hr.get("shots_weighted")
    a_shots_w = ar.get("shots_weighted")

    if h_shots_w is not None:
        h_shots_off = h_shots_w
        h_off_src = f"pondere vs opp #{hr.get('target_opp_rank')}"
    else:
        h_shots_off, h_off_src = ctx_or_overall(hr, "shots_home", "shots_home_n",
                                                "shots_l5", "shots_l10", "shots_season")
    if a_shots_w is not None:
        a_shots_off = a_shots_w
        a_off_src = f"pondere vs opp #{ar.get('target_opp_rank')}"
    else:
        a_shots_off, a_off_src = ctx_or_overall(ar, "shots_away", "shots_away_n",
                                                "shots_l5", "shots_l10", "shots_season")

    h_shots_def, h_def_src = ctx_or_overall(hr, "opp_shots_home", "shots_home_n",
                                            "opp_shots_l5", "opp_shots_l10", None)
    a_shots_def, a_def_src = ctx_or_overall(ar, "opp_shots_away", "shots_away_n",
                                            "opp_shots_l5", "opp_shots_l10", None)

    h_sot_off, _   = ctx_or_overall(hr, "sot_home", "shots_home_n",
                                    "sot_l5", "sot_l10", "sot_season")
    a_sot_off, _   = ctx_or_overall(ar, "sot_away", "shots_away_n",
                                    "sot_l5", "sot_l10", "sot_season")
    h_sot_def, _   = ctx_or_overall(hr, "opp_sot_home", "shots_home_n",
                                    "opp_sot_l5", "opp_sot_l10", None)
    a_sot_def, _   = ctx_or_overall(ar, "opp_sot_away", "shots_away_n",
                                    "opp_sot_l5", "opp_sot_l10", None)

    # Attendu = moyenne attaque (contextuelle) + defense adverse (contextuelle)
    def _expect(off, opp_def):
        if off is None and opp_def is None: return None
        if off is None: return opp_def
        if opp_def is None: return off
        return (off + opp_def) / 2

    exp_h_shots = _expect(h_shots_off, a_shots_def)
    exp_a_shots = _expect(a_shots_off, h_shots_def)
    exp_h_sot   = _expect(h_sot_off,   a_sot_def)
    exp_a_sot   = _expect(a_sot_off,   h_sot_def)

    # Source pour reasoning
    src_info = f"{home_name} (att {h_off_src}) · {away_name} (att {a_off_src})"

    # Ajustement classement (favori prend plus de tirs)
    pd = away_pos - home_pos
    if abs(pd) > 3 and exp_h_shots and exp_a_shots:
        adj = min(0.20, abs(pd) * 0.012)
        if pd > 3:  # home favori
            exp_h_shots *= (1 + adj); exp_a_shots *= max(0.80, 1 - adj)
            if exp_h_sot: exp_h_sot *= (1 + adj * 0.7)
            if exp_a_sot: exp_a_sot *= max(0.80, 1 - adj * 0.7)
            rank_ctx = f"{home_name} favori (#{home_pos} vs #{away_pos})"
        else:        # away favori
            exp_a_shots *= (1 + adj); exp_h_shots *= max(0.80, 1 - adj)
            if exp_a_sot: exp_a_sot *= (1 + adj * 0.7)
            if exp_h_sot: exp_h_sot *= max(0.80, 1 - adj * 0.7)
            rank_ctx = f"{away_name} favori (#{away_pos} vs #{home_pos})"
    else:
        rank_ctx = "équipes proches au classement"

    # H2H blend si dispo
    h2h_avg = h2h_shots.get("avg_total_shots", 0)
    h2h_n   = h2h_shots.get("n_matches", 0)

    exp_total_shots = (exp_h_shots or 0) + (exp_a_shots or 0)
    if h2h_avg and h2h_n >= 3 and exp_total_shots:
        exp_total_shots = exp_total_shots * 0.7 + h2h_avg * 0.3

    exp_total_sot = (exp_h_sot or 0) + (exp_a_sot or 0)

    if not exp_total_shots: return props

    # ── Helper pour reasoning detaille (stats brutes + ajustements + forme) ──
    def _build_shots_reasoning(stat_label="tirs"):
        """
        Construit le reasoning multi-ligne pour les picks tirs.
        Format : stats brutes L10/L5 -> defenses adverses -> attendu match -> forme.
        L'utilisateur veut voir les VRAIES stats pour pouvoir verifier.
        """
        # Choisit les bonnes cles selon le stat affiche : tirs total vs tirs cadres (SOT)
        if stat_label == "tirs cadrés" or stat_label == "tirs cadres":
            h_l10 = hr.get("sot_l10"); h_l5 = hr.get("sot_l5")
            a_l10 = ar.get("sot_l10"); a_l5 = ar.get("sot_l5")
        else:
            h_l10 = hr.get("shots_l10"); h_l5 = hr.get("shots_l5")
            a_l10 = ar.get("shots_l10"); a_l5 = ar.get("shots_l5")
        # Ligne 1 : stats brutes (L5/L10 = TCC quand >=3 matchs TCC, sinon championnat-seul)
        h_src = hr.get("shots_source", "championnat")
        a_src = ar.get("shots_source", "championnat")
        h_tag = " TCC" if h_src == "tcc" else ""
        a_tag = " TCC" if a_src == "tcc" else ""
        def _stat_str(l10, l5, tag=""):
            if l10 is None and l5 is None: return "?"
            parts = []
            if l10 is not None: parts.append(f"L10{tag} {l10:.1f}")
            if l5 is not None: parts.append(f"L5{tag} {l5:.1f}")
            return " (".join(parts) + (")" if l5 is not None else "")
        l1 = f"📊 Brut : {home_name} {_stat_str(h_l10, h_l5, h_tag)} {stat_label}/m · {away_name} {_stat_str(a_l10, a_l5, a_tag)} {stat_label}/m"
        # Ligne 2 : defenses adverses
        h_def_v = h_shots_def if stat_label == "tirs" else h_sot_def
        a_def_v = a_shots_def if stat_label == "tirs" else a_sot_def
        l2_parts = []
        if h_def_v is not None: l2_parts.append(f"{home_name} concède {h_def_v:.1f}/m")
        if a_def_v is not None: l2_parts.append(f"{away_name} concède {a_def_v:.1f}/m")
        l2 = "🛡️ Défenses : " + " · ".join(l2_parts) if l2_parts else ""
        # Ligne 3 : attendu match
        exp_h = exp_h_shots if stat_label == "tirs" else exp_h_sot
        exp_a = exp_a_shots if stat_label == "tirs" else exp_a_sot
        exp_t = (exp_h or 0) + (exp_a or 0)
        l3 = f"🎯 Attendu : {home_name} ~{exp_h:.1f} · {away_name} ~{exp_a:.1f} → total ~{exp_t:.1f}"
        # Ligne 4 : contexte rank + forme si dispo
        l4_parts = [rank_ctx]
        if home_form:
            l4_parts.append(f"{home_name} forme : {'-'.join(home_form[:5])}")
        if away_form:
            l4_parts.append(f"{away_name} forme : {'-'.join(away_form[:5])}")
        l4 = "⚖️ " + " · ".join(l4_parts)
        return "\n".join(filter(None, [l1, l2, l3, l4]))

    # ── Lignes bookmaker realistes (centrees sur l'esperance) ────────────────
    # Le bookmaker propose typiquement 3 lignes autour de l'esperance.
    # On evite les lignes irrealistes qu'aucun bookmaker n'offre.
    def _build_reasoning(off, opp_def, expected, side_name, ctx_label=""):
        off_s = f"{round(off,1)}" if off else "?"
        def_s = f"{round(opp_def,1)}" if opp_def else "?"
        exp_s = f"{round(expected,1)}" if expected else "?"
        ctx_suffix = f" [{ctx_label}]" if ctx_label else ""
        return f"{side_name} prend {off_s}/m{ctx_suffix} · adv. concède {def_s}/m → attendu ~{exp_s} ({rank_ctx})"

    # ── Tous les candidats (max 2 picks finaux) ──────────────────────────
    candidates = []

    def _add(p, pick_type, expected_val, reasoning_text, priority):
        if not p: return
        candidates.append({
            "direction": f"{pick_type.lower().replace(' ','_')}_{p['direction']}_{p['line']}",
            "type": pick_type,
            "label": p["label"],
            "cote": p.get("cote"),  # vrai cote bookmaker si dispo
            "cote_min": p["cote_min"],
            "value": p.get("value"),
            "confidence": p["confidence"],
            "edge": p.get("edge"),
            "reasoning": reasoning_text,
            "stats": {"expected": round(expected_val, 1)},
            "priority": priority,
        })

    # ── Recupere lignes bookmaker reelles si dispo ────────────────────────
    bm_lines = _extract_bookmaker_shot_lines(match_odds)
    # Applique le filtre de plage (SHOTS_LINE_RANGES) sur les lignes reelles aussi :
    # meme si le bookmaker propose "Plus de 19.5 tirs", on ne joue pas dans cette
    # zone (cote trop basse, model trop bruite aux extremes).
    for _mk in ("total_shots", "home_shots", "away_shots", "total_sot", "home_sot", "away_sot"):
        if bm_lines.get(_mk):
            _rng = SHOTS_LINE_RANGES.get(_mk)
            if _rng:
                _lo, _hi = _rng
                bm_lines[_mk] = [(ln, o, u) for (ln, o, u) in bm_lines[_mk] if _lo <= ln <= _hi]

    def _best_real_line(expected, bm_data, kind):
        """
        Pour chaque ligne bookmaker, calcule l'edge reel (my_prob * cote - 1).
        Retourne le meilleur over/under avec edge positif.
        """
        if not expected or not bm_data: return None
        best = None
        for line, over_cote, under_cote in bm_data:
            p_over = _poisson_over(expected, line) / 100
            # Over edge = my_prob * cote - 1 (>0 = +EV)
            over_edge = p_over * over_cote - 1
            under_edge = (1 - p_over) * under_cote - 1
            # Prefere positif et eleve. Ignore les picks sous 60% confidence
            if over_edge > 0 and p_over >= 0.60:
                conf = round(p_over * 100)
                if not best or over_edge > best["edge"]:
                    best = {
                        "line": line, "direction": "over",
                        "label": f"Plus de {line} {kind}",
                        "confidence": conf, "cote": over_cote,
                        "cote_min": round(1/p_over, 2),
                        "edge": round(over_edge * 100, 1),
                        "value": ("🎯", f"Edge +{round(over_edge*100,1)}%", "#22c55e"),
                    }
            if under_edge > 0 and (1-p_over) >= 0.60:
                conf = round((1-p_over) * 100)
                if not best or under_edge > best["edge"]:
                    best = {
                        "line": line, "direction": "under",
                        "label": f"Moins de {line} {kind}",
                        "confidence": conf, "cote": under_cote,
                        "cote_min": round(1/(1-p_over), 2),
                        "edge": round(under_edge * 100, 1),
                        "value": ("🎯", f"Edge +{round(under_edge*100,1)}%", "#22c55e"),
                    }
        return best

    # Total tirs - prefer real bookmaker lines
    p_real = _best_real_line(exp_total_shots, bm_lines.get("total_shots"), "tirs total")
    if p_real:
        candidates.append({
            "direction": f"shots_{p_real['direction']}_{p_real['line']}",
            "type": "Tirs (total match)",
            "label": p_real["label"],
            "cote": p_real["cote"],
            "cote_min": p_real["cote_min"],
            "value": p_real["value"],
            "confidence": p_real["confidence"],
            "edge": p_real["edge"],
            "reasoning": _build_shots_reasoning("tirs") + f"\n💰 Ligne book {p_real['line']} @ {p_real['cote']} → edge +{p_real['edge']}%",
            "stats": {"expected_total": round(exp_total_shots, 1)},
            "priority": 1,
        })
    else:
        # Fallback hardcoded lines (pas de cotes reelles dispo)
        p = _pick_line(exp_total_shots, BOOKMAKER_LINES["total_shots"], "tirs total", "Tirs total")
        if p:
            candidates.append({
                "direction": f"shots_{p['direction']}_{p['line']}",
                "type": "Tirs (total match)",
                "label": p["label"],
                "cote": None, "cote_min": p["cote_min"], "value": p.get("value"),
                "confidence": p["confidence"],
                "reasoning": _build_shots_reasoning("tirs"),
                "stats": {"expected_total": round(exp_total_shots, 1)},
                "priority": 1,
            })

    # Tirs equipe - vraies cotes si dispo
    if exp_h_shots:
        p_real_h = _best_real_line(exp_h_shots, bm_lines.get("home_shots"), f"tirs {home_name}")
        if p_real_h:
            candidates.append({
                "direction": f"shots_home_{p_real_h['direction']}_{p_real_h['line']}",
                "type": f"Tirs ({home_name})",
                "label": p_real_h["label"],
                "cote": p_real_h["cote"], "cote_min": p_real_h["cote_min"],
                "value": p_real_h["value"], "confidence": p_real_h["confidence"],
                "edge": p_real_h["edge"],
                "reasoning": _build_reasoning(h_shots_off, a_shots_def, exp_h_shots, home_name, h_off_src)
                             + f" | bookmaker {p_real_h['line']} @ {p_real_h['cote']} → edge +{p_real_h['edge']}%",
                "stats": {"expected": round(exp_h_shots, 1)},
                "priority": 2,
            })
        else:
            p = _pick_line(exp_h_shots, BOOKMAKER_LINES["home_shots"], f"tirs {home_name}", "Tirs équipe")
            _add(p, f"Tirs ({home_name})", exp_h_shots,
                 _build_reasoning(h_shots_off, a_shots_def, exp_h_shots, home_name, h_off_src), 2)
    if exp_a_shots:
        p_real_a = _best_real_line(exp_a_shots, bm_lines.get("away_shots"), f"tirs {away_name}")
        if p_real_a:
            candidates.append({
                "direction": f"shots_away_{p_real_a['direction']}_{p_real_a['line']}",
                "type": f"Tirs ({away_name})",
                "label": p_real_a["label"],
                "cote": p_real_a["cote"], "cote_min": p_real_a["cote_min"],
                "value": p_real_a["value"], "confidence": p_real_a["confidence"],
                "edge": p_real_a["edge"],
                "reasoning": _build_reasoning(a_shots_off, h_shots_def, exp_a_shots, away_name, a_off_src)
                             + f" | bookmaker {p_real_a['line']} @ {p_real_a['cote']} → edge +{p_real_a['edge']}%",
                "stats": {"expected": round(exp_a_shots, 1)},
                "priority": 2,
            })
        else:
            p = _pick_line(exp_a_shots, BOOKMAKER_LINES["away_shots"], f"tirs {away_name}", "Tirs équipe")
            _add(p, f"Tirs ({away_name})", exp_a_shots,
                 _build_reasoning(a_shots_off, h_shots_def, exp_a_shots, away_name, a_off_src), 2)

    # Tirs cadres total - prefer real bookmaker lines
    if exp_total_sot:
        p_real_sot = _best_real_line(exp_total_sot, bm_lines.get("total_sot"), "tirs cadrés total")
        if p_real_sot:
            candidates.append({
                "direction": f"sot_{p_real_sot['direction']}_{p_real_sot['line']}",
                "type": "Tirs cadrés (total)",
                "label": p_real_sot["label"],
                "cote": p_real_sot["cote"], "cote_min": p_real_sot["cote_min"],
                "value": p_real_sot["value"],
                "confidence": p_real_sot["confidence"],
                "edge": p_real_sot["edge"],
                "reasoning": _build_shots_reasoning("tirs cadrés") + f"\n💰 Ligne book {p_real_sot['line']} @ {p_real_sot['cote']} → edge +{p_real_sot['edge']}%",
                "stats": {"expected_total": round(exp_total_sot, 1)},
                "priority": 3,
            })
        else:
            p = _pick_line(exp_total_sot, BOOKMAKER_LINES["total_sot"], "tirs cadrés total", "Tirs cadrés total")
            if p:
                candidates.append({
                    "direction": f"sot_{p['direction']}_{p['line']}",
                    "type": "Tirs cadrés (total)",
                    "label": p["label"],
                    "cote": None, "cote_min": p["cote_min"], "value": p.get("value"),
                    "confidence": p["confidence"],
                    "reasoning": _build_shots_reasoning("tirs cadrés"),
                    "stats": {"expected_total": round(exp_total_sot, 1)},
                    "priority": 3,
                })
    if exp_h_sot:
        p = _pick_line(exp_h_sot, BOOKMAKER_LINES["home_sot"], f"tirs cadrés {home_name}", "Tirs cadrés équipe")
        _add(p, f"Tirs cadrés ({home_name})", exp_h_sot,
             _build_reasoning(h_sot_off, a_sot_def, exp_h_sot, home_name, h_off_src), 4)
    if exp_a_sot:
        p = _pick_line(exp_a_sot, BOOKMAKER_LINES["away_sot"], f"tirs cadrés {away_name}", "Tirs cadrés équipe")
        _add(p, f"Tirs cadrés ({away_name})", exp_a_sot,
             _build_reasoning(a_sot_off, h_sot_def, exp_a_sot, away_name, a_off_src), 4)

    if not candidates:
        return props

    # Tri: confidence desc (privilege le pick le plus marque)
    candidates.sort(key=lambda c: (-c["confidence"], c["priority"]))

    # MAX 2 picks final : prefere 1 "total" + 1 "equipe" si dispo, sinon top 2 par confiance
    selected = []
    has_total = False
    has_team = False
    for c in candidates:
        is_total = "(total" in c["type"]
        if is_total and not has_total and len(selected) < 2:
            selected.append(c); has_total = True
        elif (not is_total) and not has_team and len(selected) < 2:
            selected.append(c); has_team = True
        if len(selected) >= 2: break

    # Si on n'a que 1 pick mais d'autres candidats forts existent, prendre le 2eme top
    if len(selected) < 2 and len(candidates) > 1:
        for c in candidates:
            if c not in selected:
                selected.append(c); break

    for c in selected:
        c.pop("priority", None)

    props.extend(selected)
    return props

# ─── Main ────────────────────────────────────────────────────────────────────

def run():
    matches      = load_matches()
    player_stats = load_player_stats()
    output       = []

    # Cotes joueurs reelles (anytime_scorer, assists, shots_on_target) generees
    # par foot_odds.py. Mapping {match_id: {player_name: {market: {data}}}}.
    player_odds_all = {}
    try:
        import json as _json
        po = _json.load(open("data/foot_player_odds.json", encoding="utf-8"))
        # Strip meta key
        player_odds_all = {k: v for k, v in po.items() if not k.startswith("_")}
        if player_odds_all:
            n_players = sum(len(v) for v in player_odds_all.values())
            print(f"  [foot_odds] {len(player_odds_all)} matchs avec {n_players} joueurs cotes")
    except Exception:
        pass

    # Enrichissement match avec contexte (stade, météo) — pour Coupe du Monde
    # surtout, mais bénéfique partout (api FotMob donne le venue lat/lon).
    try:
        import foot_match_context as _ctx
        n_enriched = 0
        for _m in matches:
            try:
                _ctx.enrich_match(_m)
                if _m.get("context"):
                    n_enriched += 1
            except Exception as _e:
                print(f"  [ctx err] match {_m.get('id')}: {_e}")
        if n_enriched:
            print(f"  [ctx] {n_enriched}/{len(matches)} matchs enrichis (stade + météo)")
    except ImportError:
        pass

    for match in matches:
        team_picks, home_pp, away_pp, fun_picks = analyze_match(match, player_stats, player_odds_all)
        # IMPORTANT : on inclut TOUS les matchs scrapes, meme sans picks, pour
        # que l'user voie qu'on les a bien recuperes (transparence). Les matchs
        # sans pick auront top_pick=None et seront badges "Pas de pick" dans l'UI.
        top = team_picks[0] if team_picks else (home_pp[0] if home_pp else away_pp[0] if away_pp else None)
        output.append({
            "league":       match["league"],
            "home":         match["home"],
            "away":         match["away"],
            "start_ts":     match.get("start_ts"),
            "match_id":     match["id"],
            "page_url":     match.get("_page_url"),
            "picks":        team_picks,
            "home_players": home_pp,
            "away_players": away_pp,
            "fun_picks":    fun_picks,
            "top_pick":     top,
            "no_picks":     not (team_picks or home_pp or away_pp),
            # Contexte enrichi (stade + météo + texte) si dispo
            "context":      match.get("context"),
        })

    # Tri : matchs avec picks (confiance desc), puis matchs sans picks (par heure)
    output.sort(key=lambda x: (
        0 if x.get("no_picks") else 1,                                # matchs AVEC picks en premier
        x["top_pick"]["confidence"] if x.get("top_pick") else 0       # puis par confiance
    ), reverse=True)

    with open("data/picks.json","w",encoding="utf-8") as f:
        json.dump(output,f,ensure_ascii=False,indent=2)

    print(f"✅ {len(output)} matchs → data/picks.json")
    for m in output[:5]:
        tp  = m.get("top_pick")
        nb  = len(m["home_players"]) + len(m["away_players"])
        fun = len(m.get("fun_picks",[]))
        if not tp:
            print(f"  {m['home']} vs {m['away']} → (no pick) · {nb} props joueurs · {fun} fun picks")
            continue
        c   = f" @ {tp['cote']:.2f}" if tp.get("cote") else ""
        print(f"  {m['home']} vs {m['away']} → {tp['label']} ({tp['confidence']}%){c} · {nb} props joueurs · {fun} fun picks")

    _save_to_history(output)
    return output


HISTORY_FREEZE_HOURS_FOOT = 18      # snapshot picks dans la fenetre 18h avant kickoff (au lieu de 3h trop tardif)
HISTORY_RECOVER_HOURS_FOOT = 24     # rattrape aussi les picks dont le KO est passe
                                    # depuis moins de 24h (couvre les trous de cron)


def _save_to_history(matches):
    """Sauvegarde les picks foot dans data/picks_history.json.

    Strategie : on snapshot les picks dans la fenetre :
    - [-24h, kickoff] : couvre les pannes de cron pendant la freeze window
    - [0h, +3h avant kickoff] : snapshot final juste avant le coup d'envoi

    On REMPLACE uniquement les PENDING existants par les picks frais (les
    picks deja resolus WIN/LOSS ne sont jamais ecrases).
    """
    from pathlib import Path
    from datetime import datetime, timezone
    hist_path = Path("data/picks_history.json")
    history = {"picks": []}
    if hist_path.exists():
        try: history = json.loads(hist_path.read_text(encoding="utf-8"))
        except Exception: history = {"picks": []}
    today = datetime.now().strftime("%Y-%m-%d")
    now_utc = datetime.now(tz=timezone.utc)

    # Determine quels matchs sont a snapshoter dans l'historique :
    # - Fenetre standard : kickoff a moins de FREEZE_HOURS dans le futur
    # - Fenetre de rattrapage : kickoff il y a moins de RECOVER_HOURS
    #   (couvre les trous de cron : si le cron n'a pas tourne pendant la
    #   freeze window, on capture quand meme le pick au prochain run).
    freezable_ids = set()
    for m in matches:
        ts = m.get("start_ts")
        if not ts: continue
        try:
            ko = datetime.fromtimestamp(int(ts), tz=timezone.utc)
            hours_to_ko = (ko - now_utc).total_seconds() / 3600
            if -HISTORY_RECOVER_HOURS_FOOT <= hours_to_ko <= HISTORY_FREEZE_HOURS_FOOT:
                freezable_ids.add(m.get("match_id"))
        except Exception:
            pass

    # Drop tous les PENDING des matchs qu'on va re-snapshoter
    kept = []
    n_dropped = 0
    for p in history.get("picks", []):
        if p.get("result") in (None, "PENDING") and p.get("match_id") in freezable_ids:
            n_dropped += 1
            continue
        kept.append(p)
    history["picks"] = kept

    existing_ids = {p.get("id") for p in history.get("picks", [])}
    n_added = 0

    # ── Filtre historique : cote >= 1.30 + top 5 par match max ──────────
    # User original : 'cote 1.60 min (pas 1.15)' - mais avec les nouveaux
    # picks Poisson safe (Belgium marque 2+ buts cote_min 1.25, Cape Verde
    # ne marque pas 1.25, etc.) on filtrait TOUS les picks safe. Compromis :
    # seuil à 1.30 (rejette les ultra-safe 1.15 anytime_scorer mais accepte
    # les Poisson-derived safe picks > 1.25).
    HIST_MIN_COTE = 1.30
    HIST_MAX_PER_MATCH = 5   # 3 -> 5 pour exposer les 4-5 picks WC

    def _effective_cote(pk):
        """Cote effective pour le filtre historique :
        1. cote book réelle si dispo
        2. cote_min du modèle (fair value 1/p sans marge)
        3. Heuristique (1/conf)*0.92 en dernier recours
        """
        c = pk.get("cote")
        if c and c > 0: return float(c)
        cmin = pk.get("cote_min")
        if cmin and cmin > 0: return float(cmin)
        conf = pk.get("confidence") or 0
        if conf <= 0: return None
        return round((1.0 / (conf/100.0)) * 0.92, 2)

    def _pick_score(pk):
        """Score pour ranker les picks : edge + bonus tier + conf normalisee.
        L'edge prime sur la conf brute (value picks plutot que safe picks
        a cote ridicule)."""
        edge = pk.get("edge_pp") or 0
        conf = pk.get("confidence") or 0
        tier_bonus = {"safe": 12, "ok": 6, "fun": 0}.get(pk.get("tier", ""), 0)
        return edge * 1.5 + conf * 0.3 + tier_bonus

    def _filter_for_history(all_picks):
        """Garde les top N picks. Logique : un pick PASSE si :
          - Il a une cote réelle (bookmaker) >= seuil, OU
          - Il a une cote_min (modèle) >= seuil, OU
          - Il n'a NI cote NI cote_min (on ne peut pas juger → on garde)
        Filtre seulement les picks dont la cote est CONNUE et < seuil
        (= les picks sûrement à juice trop faible).
        EXCLUT systématiquement les picks "score exact" : leur WR est trop
        bas (≤20%) et fausse les stats globales du tracking historique.
        """
        scored = []
        for pk in all_picks:
            # Exclure les score exact (pollue les stats WR/ROI)
            direction = (pk.get("direction") or "").lower()
            label_lc  = (pk.get("label") or "").lower()
            if direction.startswith("wc_score_") or "score exact" in label_lc:
                continue
            c    = pk.get("cote")
            cmin = pk.get("cote_min")
            if c and c > 0:
                if c < HIST_MIN_COTE: continue
            elif cmin and cmin > 0:
                if cmin < HIST_MIN_COTE: continue
            scored.append((pk, _pick_score(pk)))
        scored.sort(key=lambda x: x[1], reverse=True)
        return [x[0] for x in scored[:HIST_MAX_PER_MATCH]]

    for m in matches:
        match_id = m.get("match_id")
        if match_id not in freezable_ids:
            continue  # trop loin du kickoff, pas dans l'historique
        page_url = m.get("page_url") or m.get("_page_url")
        matchup = f"{m.get('home','?')} vs {m.get('away','?')}"
        league = m.get("league", "")
        ts = m.get("start_ts")
        try:
            date = datetime.fromtimestamp(int(ts), tz=timezone.utc).strftime("%Y-%m-%d") if ts else today
        except Exception:
            date = today

        # On consolide team + players + fun pour ranker uniformement
        team_pool = [dict(p, _cat="team", _side=None) for p in m.get("picks", [])]
        home_pool = [dict(p, _cat="player", _side="home") for p in m.get("home_players", [])]
        away_pool = [dict(p, _cat="player", _side="away") for p in m.get("away_players", [])]
        fun_pool  = [dict(p, _cat="fun", _side=None) for p in m.get("fun_picks", [])]
        all_pool = team_pool + home_pool + away_pool + fun_pool
        kept = _filter_for_history(all_pool)

        for pk in kept:
            cat = pk.pop("_cat")
            side = pk.pop("_side", None)
            if cat == "team":
                pid = f"{date}_{match_id}_team_{pk.get('direction','?')}"
            elif cat == "player":
                pid = f"{date}_{match_id}_player_{pk.get('player','?')}_{pk.get('type','?')}"
            else:
                pid = f"{date}_{match_id}_fun_{pk.get('type','?')}_{pk.get('label','?')[:30]}"
            if pid in existing_ids: continue
            e = dict(pk); e.update({"id": pid, "date": date, "match_id": match_id, "page_url": page_url,
                                     "matchup": matchup, "league": league, "category": cat,
                                     "result": "PENDING", "actual": None, "resolved_at": None})
            if side: e["side"] = side
            history["picks"].append(e); existing_ids.add(pid); n_added += 1

    hist_path.write_text(json.dumps(history, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[history] {n_added} picks foot ajoutes (-{n_dropped} anciens remplaces, freeze {HISTORY_FREEZE_HOURS_FOOT}h)")


if __name__ == "__main__":
    run()