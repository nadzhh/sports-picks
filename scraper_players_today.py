"""
scraper_players_today.py — Stats joueurs + team stats SAISON 2025/26 via FotMob

Pour chaque ligue active aujourd'hui:
  - charge ~6 fichiers stat equipe (goals_pm, shots_pm, xG, corners, ...)
  - charge ~7 fichiers stat joueur (goals, assists, shots, xG, xA, mins, rating)
  - indexe par team_id / player_id

Pour chaque match:
  - assemble home_recent / away_recent (forme + goals + shots + btts)
  - liste de joueurs offensifs (top du championnat) par team
  - h2h_shots calcule depuis les rencontres precedentes
"""
import asyncio, json, os, re, sys
from fotmob_client import league as fm_league, stat as fm_stat, match_events, match_lineup, match_h2h, team as fm_team, session_calls, reset_session
from config import FOTMOB_LEAGUES, CUP_LEAGUES, INTERNAL_LEAGUE_IDS

os.makedirs("data", exist_ok=True)


# ─── Stat files a charger par ligue ──────────────────────────────────────────

# Convention FotMob: certains stat files donnent deja la moyenne par match,
# d'autres donnent le total saison. On documente lequel est lequel.
TEAM_STAT_FILES = {
    # filename                  -> (key, is_per_match)
    "goals_team_match":            ("goals_pm",    True),
    "goals_conceded_team_match":   ("conceded_pm", True),
    "ontarget_scoring_att_team":   ("sot_pm",      True),
    "expected_goals_team":         ("xg_pm",       False),  # total saison -> diviser
    "expected_goals_conceded_team":("xg_conc_pm",  False),
    "corner_taken_team":           ("corners_pm",  False),
    "big_chance_team":             ("big_chance",  False),
    "clean_sheet_team":            ("clean_sheet", False),  # count, sera divise pour rate
    "possession_percentage_team":  ("possession",  True),
}

# Player stats: stat_key -> (filename, value_field)
PLAYER_STAT_FILES = {
    "goals":         "goals",
    "assists":       "goal_assist",
    "rating":        "rating",
    "mins":          "mins_played",
    "xG":            "expected_goals",
    "xA":            "expected_assists",
    "shots_p90":     "total_scoring_att",
    "sot_p90":       "ontarget_scoring_att",
}


def _stat_index(d):
    """Retourne {team_id_or_player_id: {value, matches, ...}} depuis un stat file."""
    if not d:
        return {}
    tl = d.get("TopLists", []) or []
    if not tl:
        return {}
    sl = tl[0].get("StatList", []) or []
    out = {}
    for s in sl:
        # Team file: TeamId. Player file: ParticiantId (with team info)
        key = s.get("ParticiantId") if s.get("ParticiantId") else s.get("TeamId")
        if not key: continue
        out[key] = {
            "value":    s.get("StatValue"),
            "sub":      s.get("SubStatValue"),
            "matches":  s.get("MatchesPlayed"),
            "minutes":  s.get("MinutesPlayed"),
            "rank":     s.get("Rank"),
            "team_id":  s.get("TeamId"),
            "name":     s.get("ParticipantName"),
        }
    return out


def collect_league_stats(fm_lid, fm_sid):
    """
    Pour une ligue, charge tous les stat files team + player.
    Retourne (team_stats[team_id], player_stats[player_id]).
    """
    team_data = {}   # team_id -> {goals_pm, conceded_pm, ...}
    player_data = {} # player_id -> {goals, assists, ..., team_id, name}

    for fname, (skey, is_per_match) in TEAM_STAT_FILES.items():
        idx = _stat_index(fm_stat(fm_lid, fm_sid, fname))
        for tid, s in idx.items():
            team_data.setdefault(tid, {"team_id": tid, "name": s.get("name"), "matches": s.get("matches")})
            val = s.get("value")
            n = s.get("matches") or 0
            # Si stat est un total saison, on calcule la moyenne
            if not is_per_match and val is not None and n > 0:
                try: val = round(float(val) / n, 2)
                except: pass
            team_data[tid][skey] = val

    for skey, fname in PLAYER_STAT_FILES.items():
        idx = _stat_index(fm_stat(fm_lid, fm_sid, fname))
        for pid, s in idx.items():
            player_data.setdefault(pid, {
                "id": pid, "name": s.get("name"), "team_id": s.get("team_id"),
                "minutes": s.get("minutes"), "appearances": s.get("matches"),
            })
            player_data[pid][skey] = s.get("value")
            # Track meta from each file
            if s.get("minutes") and not player_data[pid].get("minutes"):
                player_data[pid]["minutes"] = s.get("minutes")
            if s.get("matches") and not player_data[pid].get("appearances"):
                player_data[pid]["appearances"] = s.get("matches")

    return team_data, player_data


def _f(v):
    try: return float(v)
    except: return 0


def _parse_score(score_str):
    """'2 - 1' -> (2,1) ou None."""
    if not score_str: return None
    m = re.match(r"\s*(\d+)\s*-\s*(\d+)", score_str)
    if not m: return None
    return int(m.group(1)), int(m.group(2))


def compute_fixture_stats(league_data, team_id_str, n_recent=10):
    """
    Calcule les vraies stats par-equipe a partir des scores des fixtures.
    Retourne dict avec: n_matches, home_n, away_n, gf/ga splits, btts global + L10.
    """
    all_matches = (league_data or {}).get("fixtures", {}).get("allMatches", []) or []
    played = []
    for m in all_matches:
        if not m.get("status", {}).get("finished"):
            continue
        h_id = str(m.get("home", {}).get("id"))
        a_id = str(m.get("away", {}).get("id"))
        if team_id_str not in (h_id, a_id):
            continue
        score = _parse_score(m.get("status", {}).get("scoreStr"))
        if not score:
            continue
        gh, ga = score
        is_home = (team_id_str == h_id)
        gf = gh if is_home else ga
        gc = ga if is_home else gh
        played.append({
            "utc": m.get("status", {}).get("utcTime", ""),
            "is_home": is_home,
            "gf": gf, "gc": gc,
            "btts": gf > 0 and gc > 0,
            "clean_sheet": gc == 0,
            "failed_to_score": gf == 0,
        })

    played.sort(key=lambda x: x["utc"], reverse=True)
    last_n = played[:n_recent]

    def avg(arr, k):
        if not arr: return 0
        return round(sum(x[k] for x in arr) / len(arr), 2)

    homes = [x for x in played if x["is_home"]]
    aways = [x for x in played if not x["is_home"]]

    return {
        "n_matches":   len(played),
        "goals_for_pm": avg(played, "gf"),
        "goals_ag_pm":  avg(played, "gc"),
        "total_goals_pm": round(avg(played, "gf") + avg(played, "gc"), 2),
        "home_n":      len(homes),
        "home_gf_pm":  avg(homes, "gf"),
        "home_ga_pm":  avg(homes, "gc"),
        "away_n":      len(aways),
        "away_gf_pm":  avg(aways, "gf"),
        "away_ga_pm":  avg(aways, "gc"),
        # BTTS global
        "btts_count":  sum(1 for x in played if x["btts"]),
        "btts_n":      len(played),
        "btts_rate":   round(sum(1 for x in played if x["btts"]) / len(played) * 100, 1) if played else 0,
        # BTTS L10
        "btts_l10_count": sum(1 for x in last_n if x["btts"]),
        "btts_l10_n":     len(last_n),
        "btts_l10_rate":  round(sum(1 for x in last_n if x["btts"]) / len(last_n) * 100, 1) if last_n else 0,
        # L10 averages
        "l10_n":        len(last_n),
        "l10_gf_pm":    avg(last_n, "gf"),
        "l10_ga_pm":    avg(last_n, "gc"),
        "l10_clean_sheets": sum(1 for x in last_n if x["clean_sheet"]),
        "l10_failed_to_score": sum(1 for x in last_n if x["failed_to_score"]),
    }


# ─── Recent form / team metrics ──────────────────────────────────────────────

def build_team_recent(team_metrics, fixture_metrics, l10_matches, form_list, position, l10_all_comp=None, target_opp_rank=None):
    """
    Construit home_recent/away_recent au format picks_engine.
    - team_metrics: stat files FotMob (saison: SoT, xG, corners, ...)
    - fixture_metrics: depuis scores reels (gf/ga splits, BTTS)
    - l10_matches: liste des 10 derniers matchs avec stats (tirs/SoT/xG par match)
    """
    if not team_metrics and not fixture_metrics:
        return {}
    fm = fixture_metrics or {}
    tm = team_metrics or {}
    l10 = l10_matches or []
    l5  = l10[:5]

    # Stats agregees L5 et L10 (tirs/SoT/xG) - non pondere
    s5  = aggregate_match_stats(l5)
    s10 = aggregate_match_stats(l10)
    # Split home/away (sur L10 pour plus d'echantillon)
    s10_home = aggregate_match_stats(l10, side_filter="home")
    s10_away = aggregate_match_stats(l10, side_filter="away")
    # PONDERE par qualite adversaire (rank similaire a aujourd'hui)
    s10_weighted = aggregate_match_stats(l10, target_opp_rank=target_opp_rank) if target_opp_rank else s10
    # Toutes competitions
    all_comp = l10_all_comp or []
    s5_all  = aggregate_match_stats(all_comp[:5])
    s10_all = aggregate_match_stats(all_comp)
    g5_all  = aggregate_goals_from_matches(all_comp[:5])
    g10_all = aggregate_goals_from_matches(all_comp)
    # Buts agreges L5/L10
    g5  = aggregate_goals_from_matches(l5)
    g10 = aggregate_goals_from_matches(l10)

    # ── REGLE TCC (Toutes Competitions Confondues) ───────────────────────────
    # Pour les stats RECENTES (L5/L10), on prefere TCC pour eviter le biais
    # "competition unique" : un match de coupe joue entre deux matchs de championnat
    # est plus representatif de la forme que ne pas le compter.
    # Fallback sur championnat-seul si TCC a moins de min_n matchs (echantillon trop maigre).
    _MIN_TCC_N = 3
    def _pick_tcc(all_val, all_n, lg_val, min_n=_MIN_TCC_N):
        if all_val is not None and (all_n or 0) >= min_n:
            return all_val
        return lg_val

    n_all_5  = s5_all.get("n", 0)
    n_all_10 = s10_all.get("n", 0)
    n_g_all_5  = g5_all.get("n", 0)
    n_g_all_10 = g10_all.get("n", 0)

    # Saison
    season_gf  = fm.get("goals_for_pm", _f(tm.get("goals_pm")))
    season_ga  = fm.get("goals_ag_pm",  _f(tm.get("conceded_pm")))
    season_sot = _f(tm.get("sot_pm"))    # FotMob: SoT/match saison (reel)
    season_xg  = _f(tm.get("xg_pm"))     # FotMob: xG/match saison (reel)

    # Tirs totaux: pas de stat saison FotMob -> on prend L10 comme reference saison
    season_shots = s10.get("shots_pm")

    return {
        "form": form_list[-10:] if form_list else [],
        "position": position,
        "avgRating": None,

        # ── BUTS (compat picks_engine) ─────────────────────────────────────
        "goals_for_pm":   season_gf,
        "goals_ag_pm":    season_ga,
        "total_goals_pm": fm.get("total_goals_pm", 0),

        # Tri-period buts (TCC prefere si >=3 matchs, sinon championnat seul)
        "goals_for_l5":  _pick_tcc(g5_all.get("goals_for_pm"),  n_g_all_5,  g5.get("goals_for_pm")),
        "goals_for_l10": _pick_tcc(g10_all.get("goals_for_pm"), n_g_all_10, g10.get("goals_for_pm")),
        "goals_ag_l5":   _pick_tcc(g5_all.get("goals_ag_pm"),   n_g_all_5,  g5.get("goals_ag_pm")),
        "goals_ag_l10":  _pick_tcc(g10_all.get("goals_ag_pm"),  n_g_all_10, g10.get("goals_ag_pm")),
        # Conserve aussi versions championnat-seul (pour comparaison/debug si besoin)
        "goals_for_l5_lg":  g5.get("goals_for_pm"),
        "goals_for_l10_lg": g10.get("goals_for_pm"),
        "goals_ag_l5_lg":   g5.get("goals_ag_pm"),
        "goals_ag_l10_lg":  g10.get("goals_ag_pm"),
        "goals_for_season":  season_gf,
        "goals_ag_season":   season_ga,

        # BTTS
        "btts_rate":  fm.get("btts_l10_rate", 0),
        "btts_count": fm.get("btts_l10_count", 0),
        "btts_n":     fm.get("btts_l10_n", 0),

        # Splits home/away
        "home_gf_pm": fm.get("home_gf_pm", 0),
        "home_ga_pm": fm.get("home_ga_pm", 0),
        "home_n":     fm.get("home_n", 0),
        "away_gf_pm": fm.get("away_gf_pm", 0),
        "away_ga_pm": fm.get("away_ga_pm", 0),
        "away_n":     fm.get("away_n", 0),
        "n_matches":  fm.get("n_matches", 0),

        # L10 (compat picks_engine ancien)
        "l10_gf_pm": fm.get("l10_gf_pm", 0),
        "l10_ga_pm": fm.get("l10_ga_pm", 0),
        "l10_n":     fm.get("l10_n", 0),
        "cup_gf_pm": 0, "cup_ga_pm": 0, "cup_n": 0,

        # ── TIRS / SoT / xG : tri-period (TCC prefere) ───────────────────
        "shots_pm":       season_shots or s10.get("shots_pm"),  # compat
        "shots_l5":       _pick_tcc(s5_all.get("shots_pm"),  n_all_5,  s5.get("shots_pm")),
        "shots_l10":      _pick_tcc(s10_all.get("shots_pm"), n_all_10, s10.get("shots_pm")),
        "shots_season":   None,  # FotMob ne donne pas total shots/match saison
        "opp_shots_l5":   _pick_tcc(s5_all.get("opp_shots_pm"),  n_all_5,  s5.get("opp_shots_pm")),
        "opp_shots_l10":  _pick_tcc(s10_all.get("opp_shots_pm"), n_all_10, s10.get("opp_shots_pm")),
        # Versions championnat-seul (acces explicite)
        "shots_l5_lg":    s5.get("shots_pm"),
        "shots_l10_lg":   s10.get("shots_pm"),
        "opp_shots_l5_lg":  s5.get("opp_shots_pm"),
        "opp_shots_l10_lg": s10.get("opp_shots_pm"),

        "sot_pm":         season_sot,  # compat
        "sot_l5":         _pick_tcc(s5_all.get("sot_pm"),  n_all_5,  s5.get("sot_pm")),
        "sot_l10":        _pick_tcc(s10_all.get("sot_pm"), n_all_10, s10.get("sot_pm")),
        "sot_season":     season_sot,
        "opp_sot_l5":     _pick_tcc(s5_all.get("opp_sot_pm"),  n_all_5,  s5.get("opp_sot_pm")),
        "opp_sot_l10":    _pick_tcc(s10_all.get("opp_sot_pm"), n_all_10, s10.get("opp_sot_pm")),
        "sot_l5_lg":      s5.get("sot_pm"),
        "sot_l10_lg":     s10.get("sot_pm"),
        "opp_sot_l5_lg":  s5.get("opp_sot_pm"),
        "opp_sot_l10_lg": s10.get("opp_sot_pm"),

        "xg_pm":          season_xg,  # compat
        "xg_l5":          _pick_tcc(s5_all.get("xg_pm"),  n_all_5,  s5.get("xg_pm")),
        "xg_l10":         _pick_tcc(s10_all.get("xg_pm"), n_all_10, s10.get("xg_pm")),
        "xg_season":      season_xg,
        "opp_xg_l5":      _pick_tcc(s5_all.get("opp_xg_pm"),  n_all_5,  s5.get("opp_xg_pm")),
        "opp_xg_l10":     _pick_tcc(s10_all.get("opp_xg_pm"), n_all_10, s10.get("opp_xg_pm")),
        "xg_l5_lg":       s5.get("xg_pm"),
        "xg_l10_lg":      s10.get("xg_pm"),

        # Sample size : indique l'echantillon effectivement utilise (TCC si choisi, sinon championnat)
        "shots_n_l5":     n_all_5  if (s5_all.get("shots_pm")  is not None and n_all_5  >= _MIN_TCC_N) else s5.get("n", 0),
        "shots_n_l10":    n_all_10 if (s10_all.get("shots_pm") is not None and n_all_10 >= _MIN_TCC_N) else s10.get("n", 0),
        "shots_n_l5_lg":  s5.get("n", 0),
        "shots_n_l10_lg": s10.get("n", 0),
        "shots_source":   "tcc" if (s10_all.get("shots_pm") is not None and n_all_10 >= _MIN_TCC_N) else "championnat",

        # ── PONDERES par qualite adversaire (target_opp_rank = adv. d'aujourd'hui) ─
        "shots_weighted":  s10_weighted.get("shots_pm"),
        "sot_weighted":    s10_weighted.get("sot_pm"),
        "xg_weighted":     s10_weighted.get("xg_pm"),
        "opp_shots_weighted":  s10_weighted.get("opp_shots_pm"),
        "opp_sot_weighted":    s10_weighted.get("opp_sot_pm"),
        "avg_opp_rank_l10":    s10.get("avg_opp_rank"),
        "target_opp_rank":     target_opp_rank,

        # ── TOUTES COMPETITIONS (championnat + CL/coupes) ───────────────────
        "all_comp_n_l5":         s5_all.get("n", 0),
        "all_comp_n_l10":        s10_all.get("n", 0),
        "shots_l5_all":          s5_all.get("shots_pm"),
        "shots_l10_all":         s10_all.get("shots_pm"),
        "sot_l5_all":            s5_all.get("sot_pm"),
        "sot_l10_all":           s10_all.get("sot_pm"),
        "xg_l5_all":             s5_all.get("xg_pm"),
        "xg_l10_all":            s10_all.get("xg_pm"),
        "goals_for_l5_all":      g5_all.get("goals_for_pm"),
        "goals_for_l10_all":     g10_all.get("goals_for_pm"),
        "goals_ag_l5_all":       g5_all.get("goals_ag_pm"),
        "goals_ag_l10_all":      g10_all.get("goals_ag_pm"),
        "opp_shots_l5_all":      s5_all.get("opp_shots_pm"),
        "opp_shots_l10_all":     s10_all.get("opp_shots_pm"),
        "opp_sot_l5_all":        s5_all.get("opp_sot_pm"),
        "opp_sot_l10_all":       s10_all.get("opp_sot_pm"),
        "opp_xg_l5_all":         s5_all.get("opp_xg_pm"),
        "opp_xg_l10_all":        s10_all.get("opp_xg_pm"),

        # ── Splits Domicile / Exterieur (depuis L10) ────────────────────────
        "shots_home":     s10_home.get("shots_pm"),
        "shots_home_n":   s10_home.get("n", 0),
        "shots_away":     s10_away.get("shots_pm"),
        "shots_away_n":   s10_away.get("n", 0),
        "sot_home":       s10_home.get("sot_pm"),
        "sot_away":       s10_away.get("sot_pm"),
        "xg_home":        s10_home.get("xg_pm"),
        "xg_away":        s10_away.get("xg_pm"),
        "opp_shots_home": s10_home.get("opp_shots_pm"),
        "opp_shots_away": s10_away.get("opp_shots_pm"),

        "shots_trend": "",

        # Bonus saison
        "xg_conc_pm":    _f(tm.get("xg_conc_pm")),
        "corners_pm":    _f(tm.get("corners_pm")),
        "big_chance_pm": _f(tm.get("big_chance")),
        "possession_pct":_f(tm.get("possession")),
        "clean_sheet_pm":_f(tm.get("clean_sheet")),
    }


def build_team_summary(team_metrics):
    if not team_metrics:
        return {}
    sot = _f(team_metrics.get("sot_pm"))
    return {
        "goals_pm":     _f(team_metrics.get("goals_pm")),
        "conceded_pm":  _f(team_metrics.get("conceded_pm")),
        "shots_pm":     sot * 2.5 if sot else 0,
        "sot_pm":       sot,
    }


# ─── Player records ──────────────────────────────────────────────────────────

def build_player_record(p):
    """Transforme stats brutes FotMob -> format picks_engine."""
    name = p.get("name") or ""
    apps = p.get("appearances") or 0
    if apps < 1:
        return None
    mins = p.get("minutes") or 0
    g = _f(p.get("goals"))
    a = _f(p.get("assists"))
    xg = _f(p.get("xG"))
    xa = _f(p.get("xA"))
    shots90 = _f(p.get("shots_p90"))
    sot90 = _f(p.get("sot_p90"))
    rating = p.get("rating")

    # Position depuis Positions? FotMob donne un code numerique (115=ATT, etc.)
    # On par defaut F si shots90 elevee, sinon M
    pos = "F" if shots90 > 1.5 or g > 5 else "M"

    return {
        "id": p.get("id"),
        "name": name,
        "shortName": name,
        "position": pos,
        "team_id": p.get("team_id"),
        "appearances": apps,
        "is_sub": False,
        "minutes": mins,
        "goals": int(g),
        "assists": int(a),
        "shots":     round(shots90 * apps, 0) if shots90 else 0,
        "shots_on":  round(sot90 * apps, 0) if sot90 else 0,
        "goals_pm":   round(g / apps, 3) if apps else 0,
        "assists_pm": round(a / apps, 3) if apps else 0,
        "g_a_pm":     round((g + a) / apps, 3) if apps else 0,
        "shots_pm":   round(shots90, 3),
        "sot_pm":     round(sot90, 3),
        "xG_pm":      round(xg / apps, 3) if apps and xg else 0,
        "xA_pm":      round(xa / apps, 3) if apps and xa else 0,
        "rating":     rating,
    }


# ─── H2H shots (compute from finished h2h fixtures in league_data) ───────────

def compute_h2h_shots_from_fotmob(league_data, home_id_str, away_id_str):
    """
    On ne peut pas avoir les shots par match sans matchDetails (403).
    On laisse 0 pour l'instant. Avec un upgrade x-mas header on pourrait debloquer.
    """
    return {"avg_total_shots": 0, "n_matches": 0}


# ─── L5: 5 derniers matchs d'une equipe avec scorers/assists ──────────────────

def _strip_assist(s):
    """'assist by Dani Olmo' -> 'Dani Olmo'."""
    if not s: return None
    return re.sub(r"^assist by\s+", "", s, flags=re.I).strip() or None


def collect_team_l5_all_comp(team_id, n=10, skip_friendlies=True):
    """
    Recupere les n derniers matchs TOUTES COMPETITIONS de l'equipe via team.overviewFixtures.
    Inclut: championnat, Coupe d'Europe (CL/EL/EC), coupes domestiques.
    Exclut: matchs amicaux (par defaut).
    """
    td = fm_team(team_id)
    if not td: return []
    ovf = td.get("overview", {}).get("overviewFixtures", []) or []

    EXCLUDED = {"Club Friendlies"} if skip_friendlies else set()
    team_id_str = str(team_id)

    played = []
    for m in ovf:
        if not m.get("status", {}).get("finished"):
            continue
        tname = m.get("tournament", {}).get("name", "")
        if tname in EXCLUDED:
            continue
        played.append(m)

    played.sort(key=lambda x: x.get("status", {}).get("utcTime", ""), reverse=True)
    recent = played[:n]

    out = []
    for m in recent:
        page_url = m.get("pageUrl") or ""
        h_id = str(m.get("home", {}).get("id"))
        is_home = (team_id_str == h_id)
        opponent = m.get("away", {}).get("name") if is_home else m.get("home", {}).get("name")
        score_str = m.get("status", {}).get("scoreStr") or ""
        tournament = m.get("tournament", {}).get("name", "")
        fixture_utc = m.get("status", {}).get("utcTime") or ""
        fixture_date = fixture_utc[:10]

        evt = match_events(page_url) if page_url else None
        evt_valid = bool(evt and (evt.get("utcTime") or "")[:10] == fixture_date)

        gf = ga = None
        sm = re.match(r"\s*(\d+)\s*-\s*(\d+)", score_str)
        if sm:
            gh, gaw = int(sm.group(1)), int(sm.group(2))
            gf = gh if is_home else gaw
            ga = gaw if is_home else gh

        result = ""
        if gf is not None and ga is not None:
            result = "W" if gf > ga else ("D" if gf == ga else "L")

        team_shots = opp_shots = team_sot = opp_sot = team_xg = opp_xg = None
        if evt_valid:
            evt_home_id = str(evt.get("home_id"))
            if evt_home_id == team_id_str:
                team_shots = evt.get("home_shots"); opp_shots = evt.get("away_shots")
                team_sot   = evt.get("home_sot");   opp_sot   = evt.get("away_sot")
                team_xg    = evt.get("home_xg");    opp_xg    = evt.get("away_xg")
            else:
                team_shots = evt.get("away_shots"); opp_shots = evt.get("home_shots")
                team_sot   = evt.get("away_sot");   opp_sot   = evt.get("home_sot")
                team_xg    = evt.get("away_xg");    opp_xg    = evt.get("home_xg")

        out.append({
            "date": m.get("status", {}).get("utcTime"),
            "opponent": opponent,
            "is_home": is_home,
            "score": score_str,
            "gf": gf, "ga": ga, "result": result,
            "tournament": tournament,
            "data_valid": evt_valid,
            "match_id":   m.get("id"),
            "page_url":   page_url,
            "team_id_str": team_id_str,
            "team_shots": team_shots, "opp_shots": opp_shots,
            "team_sot": team_sot, "opp_sot": opp_sot,
            "team_xg": team_xg, "opp_xg": opp_xg,
        })
    return out


def _extract_standings_map(league_data):
    """Extrait {team_id_str: rank} depuis league_data standings."""
    standings_map = {}
    table_root = (league_data or {}).get("table", []) or []
    if table_root:
        try:
            table = table_root[0].get("data", {}).get("table", {}).get("all", [])
            if not table and table_root[0].get("data", {}).get("tables"):
                table = []
                for t in table_root[0]["data"]["tables"]:
                    table.extend(t.get("table", {}).get("all", []) or [])
            for t in table:
                tid = t.get("id")
                if tid: standings_map[str(tid)] = t.get("idx")
        except Exception:
            pass
    return standings_map


def collect_team_l5(league_data, team_id_str, n=5):
    """
    Pour une equipe donnee, recupere les details des n derniers matchs joues.
    Retourne liste [{date, opponent, opp_rank, is_home, score, gf, ga, result,
                     team_shots, opp_shots, team_sot, opp_sot, team_xg, opp_xg,
                     goals: [...]}].
    """
    all_matches = league_data.get("fixtures", {}).get("allMatches", []) or []
    standings_map = _extract_standings_map(league_data)
    played = []
    for m in all_matches:
        if not m.get("status", {}).get("finished"):
            continue
        h_id = str(m.get("home", {}).get("id"))
        a_id = str(m.get("away", {}).get("id"))
        if team_id_str not in (h_id, a_id):
            continue
        played.append(m)
    played.sort(key=lambda x: x.get("status", {}).get("utcTime", ""), reverse=True)
    recent = played[:n]

    out = []
    for m in recent:
        page_url = m.get("pageUrl") or ""
        h_id = str(m.get("home", {}).get("id"))
        a_id = str(m.get("away", {}).get("id"))  # ← FIX: re-extraire dans la boucle
        is_home = (team_id_str == h_id)
        opponent = m.get("away", {}).get("name") if is_home else m.get("home", {}).get("name")
        score_str = m.get("status", {}).get("scoreStr") or ""
        fixture_utc = m.get("status", {}).get("utcTime") or ""
        fixture_date = fixture_utc[:10]

        evt = match_events(page_url) if page_url else None

        evt_valid = False
        if evt:
            evt_date = (evt.get("utcTime") or "")[:10]
            if evt_date == fixture_date:
                evt_valid = True

        # Score numerique
        gf = ga = None
        sm = re.match(r"\s*(\d+)\s*-\s*(\d+)", score_str)
        if sm:
            gh, gaw = int(sm.group(1)), int(sm.group(2))
            gf = gh if is_home else gaw
            ga = gaw if is_home else gh

        result = ""
        if gf is not None and ga is not None:
            result = "W" if gf > ga else ("D" if gf == ga else "L")

        team_goals = []
        opp_goals = []
        team_shots = opp_shots = None
        team_sot   = opp_sot   = None
        team_xg    = opp_xg    = None
        if evt_valid:  # On utilise les donnees du match SEULEMENT si date matche
            evt_home_id = str(evt.get("home_id"))
            if evt_home_id == team_id_str:
                team_goals = evt.get("home_goals", []) or []
                opp_goals  = evt.get("away_goals", []) or []
                team_shots = evt.get("home_shots"); opp_shots = evt.get("away_shots")
                team_sot   = evt.get("home_sot");   opp_sot   = evt.get("away_sot")
                team_xg    = evt.get("home_xg");    opp_xg    = evt.get("away_xg")
            else:
                team_goals = evt.get("away_goals", []) or []
                opp_goals  = evt.get("home_goals", []) or []
                team_shots = evt.get("away_shots"); opp_shots = evt.get("home_shots")
                team_sot   = evt.get("away_sot");   opp_sot   = evt.get("home_sot")
                team_xg    = evt.get("away_xg");    opp_xg    = evt.get("home_xg")

        # Rang adversaire (depuis standings)
        opp_id_str = a_id if is_home else h_id
        opp_rank = standings_map.get(opp_id_str)

        out.append({
            "date":      fixture_utc,
            "opponent":  opponent,
            "opp_rank":  opp_rank,
            "opp_id":    opp_id_str,
            "is_home":   is_home,
            "score":     score_str,
            "gf":        gf,
            "ga":        ga,
            "result":    result,
            "data_valid": evt_valid,
            "match_id":  m.get("id"),
            "page_url":  page_url,
            "team_id_str": team_id_str,
            "team_shots": team_shots, "opp_shots": opp_shots,
            "team_sot":   team_sot,   "opp_sot":   opp_sot,
            "team_xg":    team_xg,    "opp_xg":    opp_xg,
            "goals":     [
                {
                    "scorer":  g.get("scorer"),
                    "assist":  _strip_assist(g.get("assist")),
                    "minute":  g.get("minute"),
                    "ownGoal": g.get("ownGoal"),
                } for g in team_goals
            ],
            "opp_goals": [
                {
                    "scorer":  g.get("scorer"),
                    "assist":  _strip_assist(g.get("assist")),
                    "minute":  g.get("minute"),
                    "ownGoal": g.get("ownGoal"),
                } for g in opp_goals
            ],
        })
    return out


def _avg(arr, dec=1):
    return round(sum(arr) / len(arr), dec) if arr else None


def aggregate_match_stats(match_list, side_filter=None, target_opp_rank=None):
    """
    Aggrege tirs/SoT/xG depuis une liste de matchs.
    side_filter: None / 'home' / 'away'
    target_opp_rank: si fourni, pondere les matchs en fonction de la similitude
                    entre opp_rank du match L5 et target_opp_rank (adversaire du jour).
    Retourne dict {shots_pm, sot_pm, xg_pm, opp_shots_pm, opp_sot_pm, opp_xg_pm, n, avg_opp_rank}.
    """
    filtered = match_list
    if side_filter == "home":
        filtered = [m for m in match_list if m.get("is_home")]
    elif side_filter == "away":
        filtered = [m for m in match_list if not m.get("is_home")]

    # Moyenne ponderee si target_opp_rank fourni
    def w_avg(values, ranks, target_rank, dec=1):
        if not values: return None
        if target_rank is None or not ranks or all(r is None for r in ranks):
            return _avg(values, dec)
        # Poids: plus le rank du match est proche du target, plus il compte
        weights = []
        for r in ranks:
            if r is None: w = 0.5
            else:
                # Difference de rang: 0 = max poids, plus loin = moins de poids
                diff = abs(r - target_rank)
                w = max(0.3, 1.0 - diff * 0.05)
            weights.append(w)
        tot_w = sum(weights)
        return round(sum(v*w for v,w in zip(values, weights)) / tot_w, dec) if tot_w else None

    shots     = [m.get("team_shots") for m in filtered if m.get("team_shots") is not None]
    opp_shots = [m.get("opp_shots")  for m in filtered if m.get("opp_shots")  is not None]
    sot       = [m.get("team_sot")   for m in filtered if m.get("team_sot")   is not None]
    opp_sot   = [m.get("opp_sot")    for m in filtered if m.get("opp_sot")    is not None]
    xg        = [m.get("team_xg")    for m in filtered if m.get("team_xg")    is not None]
    opp_xg    = [m.get("opp_xg")     for m in filtered if m.get("opp_xg")     is not None]
    # Ranks de chaque match (pour ponderation)
    ranks     = [m.get("opp_rank")   for m in filtered if m.get("team_shots") is not None]
    opp_ranks = [m.get("opp_rank")   for m in filtered if m.get("opp_shots")  is not None]
    sot_ranks = [m.get("opp_rank")   for m in filtered if m.get("team_sot")   is not None]
    xg_ranks  = [m.get("opp_rank")   for m in filtered if m.get("team_xg")    is not None]
    all_ranks = [m.get("opp_rank")   for m in filtered if m.get("opp_rank") is not None]

    return {
        "shots_pm":      w_avg(shots,     ranks,     target_opp_rank),
        "opp_shots_pm":  w_avg(opp_shots, opp_ranks, target_opp_rank),
        "sot_pm":        w_avg(sot,       sot_ranks, target_opp_rank),
        "opp_sot_pm":    w_avg(opp_sot,   opp_ranks, target_opp_rank),
        "xg_pm":         w_avg(xg,        xg_ranks,  target_opp_rank, 2),
        "opp_xg_pm":     w_avg(opp_xg,    opp_ranks, target_opp_rank, 2),
        "n":             len(shots),
        "avg_opp_rank":  round(sum(all_ranks)/len(all_ranks),1) if all_ranks else None,
        # Raw (non-pondere) pour reference
        "shots_pm_raw":  _avg(shots),
        "sot_pm_raw":    _avg(sot),
    }


def aggregate_goals_from_matches(match_list):
    """Aggrege buts marques/concedes depuis une liste de matchs (depuis scoreStr)."""
    gf = [m.get("gf") for m in match_list if m.get("gf") is not None]
    ga = [m.get("ga") for m in match_list if m.get("ga") is not None]
    return {
        "goals_for_pm": _avg(gf, 2),
        "goals_ag_pm":  _avg(ga, 2),
        "n": len(gf),
    }


async def patch_invalid_matches(all_l10_lists):
    """
    Trouve les matchs ou data_valid=False et fetch leurs vraies donnees via Camoufox.
    all_l10_lists: liste de listes [l10_for_team_A, l10_for_team_B, ...]
    Modifie les entries in-place.

    Cap a 30 matchs max pour eviter de bloquer le cron quand des leagues
    massives (Friendlies internationaux, qualifs WC avec amicaux) generent
    des centaines de "conflits". Au-dela, on accepte les donnees imparfaites.
    Plus skip si CAMOUFOX_DISABLE env var est set (GH Actions).
    """
    import os as _os
    if _os.environ.get("CAMOUFOX_DISABLE") == "1":
        print(f"\n[browser fallback] CAMOUFOX_DISABLE=1 -> skip")
        return 0
    from fotmob_browser import FotMobBrowser, slim_to_match_events

    # Collecte tous les matchs invalides (deduplique par match_id)
    invalid_by_id = {}
    for l10 in all_l10_lists:
        for entry in l10:
            if not entry.get("data_valid") and entry.get("match_id") and entry.get("page_url"):
                mid = entry["match_id"]
                if mid not in invalid_by_id:
                    invalid_by_id[mid] = {
                        "match_id": mid,
                        "page_url": entry["page_url"],
                        "entries": [],
                    }
                invalid_by_id[mid]["entries"].append(entry)

    if not invalid_by_id:
        return 0

    # Cap pour eviter le hang sur Friendlies & cie (118 matchs vu en prod)
    MAX_BROWSER_PATCH = 30
    if len(invalid_by_id) > MAX_BROWSER_PATCH:
        print(f"\n[browser fallback] {len(invalid_by_id)} matchs invalides -> cap a {MAX_BROWSER_PATCH} pour eviter le hang")
        # Garde les premiers (la plupart sont des amicaux internationaux secondaires)
        invalid_by_id = dict(list(invalid_by_id.items())[:MAX_BROWSER_PATCH])
    print(f"\n[browser fallback] {len(invalid_by_id)} match(s) en conflit a fetch via Camoufox...")

    fetched = 0
    failed = 0
    async with FotMobBrowser() as browser:
        for mid, info in invalid_by_id.items():
            slim = await browser.fetch_match(mid, info["page_url"])
            if not slim or slim.get("_status"):
                failed += 1
                print(f"  [X] matchId={mid} - {slim.get('_status', 'no_data') if slim else 'no_resp'}")
                continue
            evt = slim_to_match_events(slim)
            if not evt:
                failed += 1
                continue

            # Patche toutes les entries qui pointent vers ce match_id
            for entry in info["entries"]:
                team_id_str = entry.get("team_id_str") or ""
                evt_home_id = str(evt.get("home_id"))
                if evt_home_id == team_id_str:
                    entry["team_shots"] = evt.get("home_shots")
                    entry["opp_shots"]  = evt.get("away_shots")
                    entry["team_sot"]   = evt.get("home_sot")
                    entry["opp_sot"]    = evt.get("away_sot")
                    entry["team_xg"]    = evt.get("home_xg")
                    entry["opp_xg"]     = evt.get("away_xg")
                else:
                    entry["team_shots"] = evt.get("away_shots")
                    entry["opp_shots"]  = evt.get("home_shots")
                    entry["team_sot"]   = evt.get("away_sot")
                    entry["opp_sot"]    = evt.get("home_sot")
                    entry["team_xg"]    = evt.get("away_xg")
                    entry["opp_xg"]     = evt.get("home_xg")
                entry["data_valid"] = True
                entry["data_source"] = "browser"
            fetched += 1
            print(f"  [OK] matchId={mid} (tirs={entry.get('team_shots')})")
    if failed:
        print(f"  -> {fetched} OK / {failed} echec (FotMob restreint l'API matchDetails pour ces matchs)")
    return fetched


def collect_h2h_details(page_url, home_id, away_id, n=5):
    """
    Recupere les n derniers h2h avec stats (score + tirs + tirs cadres).
    Pour chaque h2h match, on fetch la page pour les stats si dispo.
    """
    h2h_matches = match_h2h(page_url)
    if not h2h_matches:
        return []

    home_id_str = str(home_id)
    away_id_str = str(away_id)

    # Filtre matchs finis + tri par date desc
    finished = []
    for m in h2h_matches:
        if not m.get("status", {}).get("finished"):
            continue
        # Verifie que c'est bien un match entre les 2 equipes
        h_id = str(m.get("home", {}).get("id", ""))
        a_id = str(m.get("away", {}).get("id", ""))
        if home_id_str not in (h_id, a_id) or away_id_str not in (h_id, a_id):
            continue
        finished.append(m)
    finished.sort(key=lambda x: x.get("status", {}).get("utcTime", ""), reverse=True)
    recent = finished[:n]

    out = []
    for m in recent:
        match_url = m.get("matchUrl") or ""
        h = m.get("home", {})
        a = m.get("away", {})
        h_id_m = str(h.get("id", ""))
        score_str = m.get("status", {}).get("scoreStr", "")
        date_iso = m.get("status", {}).get("utcTime", "")
        league = m.get("league", {}).get("name", "")

        # Score numerique
        gh = ga = None
        sm = re.match(r"\s*(\d+)\s*-\s*(\d+)", score_str)
        if sm:
            gh, ga = int(sm.group(1)), int(sm.group(2))

        # Score du point de vue home_team (l'equipe a domicile dans le match d'aujourd'hui)
        home_was_home_in_h2h = (h_id_m == home_id_str)
        home_gf = gh if home_was_home_in_h2h else ga
        home_ga = ga if home_was_home_in_h2h else gh

        # Resultat du point de vue home_team
        result = ""
        if home_gf is not None and home_ga is not None:
            result = "W" if home_gf > home_ga else ("D" if home_gf == home_ga else "L")

        # Fetch stats detaillees du match (tirs/SoT)
        evt = match_events(match_url) if match_url else None
        evt_valid = bool(evt and (evt.get("utcTime") or "")[:10] == date_iso[:10])
        h_shots = a_shots = h_sot = a_sot = None
        if evt_valid:
            evt_h_id = str(evt.get("home_id", ""))
            if evt_h_id == home_id_str:
                h_shots = evt.get("home_shots"); a_shots = evt.get("away_shots")
                h_sot   = evt.get("home_sot");   a_sot   = evt.get("away_sot")
            else:
                h_shots = evt.get("away_shots"); a_shots = evt.get("home_shots")
                h_sot   = evt.get("away_sot");   a_sot   = evt.get("home_sot")

        # Extrait match_id du matchUrl pour eventuel browser fallback
        mid_h2h = None
        if "#" in match_url:
            try: mid_h2h = int(match_url.split("#")[1])
            except: pass

        out.append({
            "date":   date_iso,
            "league": league,
            "home_team": h.get("name"),
            "away_team": a.get("name"),
            "home_was_home": home_was_home_in_h2h,
            "score":  score_str,
            "home_gf": home_gf,
            "home_ga": home_ga,
            "result_for_home": result,
            "home_shots": h_shots,
            "away_shots": a_shots,
            "home_sot":   h_sot,
            "away_sot":   a_sot,
            "data_valid": evt_valid,
            # Champs requis par patch_invalid_matches:
            "match_id":   mid_h2h,
            "page_url":   match_url.split("#")[0] if match_url else "",
            "team_id_str": home_id_str,  # POV de l'equipe-home-d-aujourd-hui
            # Pour patch: noms des champs alignés avec L5
            "team_shots": h_shots, "opp_shots": a_shots,
            "team_sot":   h_sot,   "opp_sot":   a_sot,
            "team_xg":    None,    "opp_xg":    None,
        })
    return out


def compute_decisive_players(match_list):
    """
    Pour une liste de matchs (avec goals), compte les contributions decisives
    par joueur (buts + passes deçisives).
    Retourne liste triee desc: [{name, goals, assists, decisive_matches, n_matches}].
    """
    stats = {}  # player_name -> {goals, assists, matches_decisive_set}
    n_matches = len(match_list)
    for idx, m in enumerate(match_list):
        decisive_in_this = set()
        for g in m.get("goals", []) or []:
            scorer = g.get("scorer")
            assist = g.get("assist")
            own = g.get("ownGoal")
            if scorer and not own:
                stats.setdefault(scorer, {"goals": 0, "assists": 0, "matches": set()})
                stats[scorer]["goals"] += 1
                stats[scorer]["matches"].add(idx)
            if assist:
                stats.setdefault(assist, {"goals": 0, "assists": 0, "matches": set()})
                stats[assist]["assists"] += 1
                stats[assist]["matches"].add(idx)

    out = []
    for name, s in stats.items():
        out.append({
            "name":     name,
            "goals":    s["goals"],
            "assists":  s["assists"],
            "decisive": s["goals"] + s["assists"],
            "matches_decisive": len(s["matches"]),
            "n_matches": n_matches,
        })
    out.sort(key=lambda x: (x["decisive"], x["matches_decisive"]), reverse=True)
    return out


def trend_arrow(l5, l10, season, threshold=0.10):
    """
    Retourne un indicateur de tendance.
    Compare l5 vs (l10 ou season) avec un seuil de variation.
    """
    if l5 is None: return ""
    ref = l10 if l10 is not None else season
    if ref is None or ref == 0:
        return ""
    diff = (l5 - ref) / ref
    if diff > threshold:    return "↑"
    if diff < -threshold:   return "↓"
    return "→"


# ─── MAIN ────────────────────────────────────────────────────────────────────

def main():
    reset_session()
    print(f"=== FotMob players scraper ===")

    try:
        matches = json.load(open("data/matches.json", encoding="utf-8"))
    except Exception as e:
        print(f"[X] matches.json: {e}")
        sys.exit(1)

    if not matches:
        print("[X] matches.json vide")
        sys.exit(1)

    # 1. Quelles ligues sont en jeu (FotMob IDs)
    leagues_in_play = set()
    for m in matches:
        fm_lid = m.get("_fotmob_lid")
        if fm_lid:
            leagues_in_play.add(fm_lid)

    # 2. Pour chaque ligue, charge season_id puis tous les stats
    #    PARALLELISE (8 workers) : gain ~6-8x sur la phase de chargement.
    print(f"\n[1/3] Chargement stats team+player ({len(leagues_in_play)} ligues)...")
    league_stats = {}  # fm_lid -> {team_data, player_data}
    league_data_cache = {}  # fm_lid -> raw league data (for season_id, fixtures)

    from concurrent.futures import ThreadPoolExecutor

    def _load_league(fm_lid):
        ld = fm_league(fm_lid)
        sid = None
        pl = (ld or {}).get("stats", {}).get("players", [])
        if pl and pl[0].get("fetchAllUrl"):
            m_re = re.search(r"/season/(\d+)/", pl[0]["fetchAllUrl"])
            if m_re: sid = m_re.group(1)
        if not sid:
            return fm_lid, ld, None, None
        team_data, player_data = collect_league_stats(fm_lid, sid)
        return fm_lid, ld, team_data, player_data

    with ThreadPoolExecutor(max_workers=8) as ex:
        results = list(ex.map(_load_league, leagues_in_play))

    for fm_lid, ld, team_data, player_data in results:
        league_data_cache[fm_lid] = ld
        if team_data is None:
            print(f"  [!] season_id introuvable pour ligue {fm_lid}")
            continue
        name = next((n for n, info in FOTMOB_LEAGUES.items() if info["id"] == fm_lid), str(fm_lid))
        print(f"  - {name}")
        league_stats[fm_lid] = {"team": team_data, "player": player_data}

    # ─── PHASE A: collecte L10 brut (championnat + toutes comp) ───────────
    print(f"\n[2/4] Collecte L10 par equipe (championnat + toutes comp)...")
    match_data = []  # [(m, fm_lid, h_l10, a_l10, h_l10_all, a_l10_all), ...]
    for m in matches:
        fm_lid = m.get("_fotmob_lid")
        home_id = m.get("home_id")
        away_id = m.get("away_id")
        page_url = m.get("_page_url")
        league_data = league_data_cache.get(fm_lid, {})

        h_l10 = collect_team_l5(league_data, str(home_id), n=10)
        a_l10 = collect_team_l5(league_data, str(away_id), n=10)
        h_l10_all = collect_team_l5_all_comp(home_id, n=10)
        a_l10_all = collect_team_l5_all_comp(away_id, n=10)
        # H2H details collectes ici aussi (pour pouvoir patcher avant les agregations)
        h2h_d = []
        if page_url:
            try: h2h_d = collect_h2h_details(page_url, home_id, away_id, n=5)
            except Exception as e: print(f"  [h2h err] {e}")

        match_data.append((m, fm_lid, h_l10, a_l10, h_l10_all, a_l10_all, h2h_d))

    # ─── PHASE B: fix matchs en conflit slug via browser headless ─────────
    print(f"\n[3/4] Patch matchs invalides via navigateur (L10 + h2h)...")
    all_lists = []
    for _, _, h_l10, a_l10, h_l10_all, a_l10_all, h2h_d in match_data:
        all_lists.extend([h_l10, a_l10, h_l10_all, a_l10_all, h2h_d])

    try:
        n_fixed = asyncio.run(patch_invalid_matches(all_lists))
        if n_fixed:
            print(f"  -> {n_fixed} matchs corriges via browser")
    except Exception as e:
        print(f"  [browser fallback echoue] {e}")

    # Apres patch, re-syncroniser les champs h2h-specifiques (home_shots/away_shots)
    # depuis team_shots/opp_shots (patch_invalid_matches met a jour team_*/opp_*)
    for _, _, _, _, _, _, h2h_d in match_data:
        for e in h2h_d:
            if e.get("data_valid"):
                e["home_shots"] = e.get("team_shots")
                e["away_shots"] = e.get("opp_shots")
                e["home_sot"]   = e.get("team_sot")
                e["away_sot"]   = e.get("opp_sot")

    # ─── PHASE C: aggregations + ecriture ────────────────────────────────
    print(f"\n[4/4] Assemblage player_stats par match...")
    out = {}
    for m, fm_lid, h_l10, a_l10, h_l10_all, a_l10_all, h2h_d in match_data:
        mid = str(m["id"])
        home_id = m.get("home_id"); away_id = m.get("away_id")
        try:
            home_id_int = int(home_id); away_id_int = int(away_id)
        except: continue

        lstats = league_stats.get(fm_lid, {})
        team_idx = lstats.get("team", {})
        player_idx = lstats.get("player", {})

        h_tm = team_idx.get(home_id_int, {})
        a_tm = team_idx.get(away_id_int, {})

        league_data = league_data_cache.get(fm_lid, {})
        h_fix = compute_fixture_stats(league_data, str(home_id))
        a_fix = compute_fixture_stats(league_data, str(away_id))

        h_l5 = h_l10[:5]
        a_l5 = a_l10[:5]

        pmf = m.get("pre_match_form") or {}
        h_form = pmf.get("homeTeam", {}).get("form", [])
        a_form = pmf.get("awayTeam", {}).get("form", [])
        h_pos  = pmf.get("homeTeam", {}).get("position")
        a_pos  = pmf.get("awayTeam", {}).get("position")

        # Rang de chaque equipe (pour ponderation: target_opp_rank = adv. dans CE match)
        h_target_opp_rank = a_pos  # Pour Home, target = rang de Away
        a_target_opp_rank = h_pos  # Pour Away, target = rang de Home

        h_recent = build_team_recent(h_tm, h_fix, h_l10, h_form, h_pos, h_l10_all, target_opp_rank=h_target_opp_rank)
        a_recent = build_team_recent(a_tm, a_fix, a_l10, a_form, a_pos, a_l10_all, target_opp_rank=a_target_opp_rank)
        h_team = build_team_summary(h_tm)
        a_team = build_team_summary(a_tm)

        home_players = []
        away_players = []
        for pid, p in player_idx.items():
            tid = p.get("team_id")
            rec = build_player_record(p)
            if not rec: continue
            if tid == home_id_int: home_players.append(rec)
            elif tid == away_id_int: away_players.append(rec)
        home_players.sort(key=lambda x: x.get("g_a_pm", 0), reverse=True)
        away_players.sort(key=lambda x: x.get("g_a_pm", 0), reverse=True)

        h2h_shots = compute_h2h_shots_from_fotmob(league_data, str(home_id), str(away_id))

        h_dec_l5  = compute_decisive_players(h_l5)
        h_dec_l10 = compute_decisive_players(h_l10)
        a_dec_l5  = compute_decisive_players(a_l5)
        a_dec_l10 = compute_decisive_players(a_l10)

        # Lineup (h2h_details deja collecté en phase A)
        lineup = None
        page_url = m.get("_page_url")
        if page_url:
            try:
                lineup = match_lineup(page_url)
            except Exception as e:
                print(f"  [lineup err] {e}")
        h2h_details = h2h_d  # Reutilise les details deja collectes + patches

        out[mid] = {
            "home": home_players, "away": away_players,
            "home_team_stats": h_team, "away_team_stats": a_team,
            "home_recent": h_recent, "away_recent": a_recent,
            "home_l5": h_l5, "away_l5": a_l5,
            "home_decisive_l5":  h_dec_l5[:5],  "home_decisive_l10": h_dec_l10[:5],
            "away_decisive_l5":  a_dec_l5[:5],  "away_decisive_l10": a_dec_l10[:5],
            "h2h_shots": h2h_shots,
            "lineup":    lineup,
            "h2h_details": h2h_details,
        }
        print(f"  {m['home']} vs {m['away']}: "
              f"{len(home_players)}+{len(away_players)} joueurs | "
              f"L5 valid: {sum(1 for x in h_l5 if x.get('data_valid'))}/5+"
              f"{sum(1 for x in a_l5 if x.get('data_valid'))}/5")

    with open("data/player_stats.json", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)

    print(f"\n[OK] {len(out)} matchs -> data/player_stats.json")
    print(f"     FotMob calls (session): {session_calls()}")


if __name__ == "__main__":
    main()
