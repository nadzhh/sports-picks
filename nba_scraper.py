"""
nba_scraper.py — Recupere games NBA J/J+1, rosters, stats joueurs (saison + L10)

Produit:
- data/nba_matches.json     : matchs upcoming avec teams
- data/nba_player_stats.json: stats par match {match_id: {home_players: [...], away_players: [...]}}

Chaque joueur retourne:
- name, position
- season_avg: {PTS, REB, AST, FG3M, MIN, GP, ...}
- l10_games: [{date, opp, PTS, REB, AST, FG3M, MIN, ...}]
"""
import json, os, sys
from datetime import date, datetime, timedelta
from nba_client import games_on_date, team_player_averages, player_recent_form, team_advanced_map, team_opponent_map

CURRENT_SEASON = "2025-26"

os.makedirs("data", exist_ok=True)


def _fmt_date_for_nba(d):
    """YYYY-MM-DD -> MM/DD/YYYY (format NBA pour scoreboard)."""
    return d


def _player_summary(p_avg, l10_games, season=CURRENT_SEASON):
    """Compose le record joueur (season avg + L5 + L10)."""
    name = p_avg.get("PLAYER_NAME", "?")
    pid = p_avg.get("PLAYER_ID")
    pos = ""  # PLAYER_POSITION peut etre dans une autre endpoint

    # Season avg
    season_avg = {
        "GP":     p_avg.get("GP", 0),
        "MIN":    p_avg.get("MIN", 0),
        "PTS":    p_avg.get("PTS", 0),
        "REB":    p_avg.get("REB", 0),
        "AST":    p_avg.get("AST", 0),
        "FG3M":   p_avg.get("FG3M", 0),
        "FGM":    p_avg.get("FGM", 0),
        "FGA":    p_avg.get("FGA", 0),
        "STL":    p_avg.get("STL", 0),
        "BLK":    p_avg.get("BLK", 0),
        "TOV":    p_avg.get("TOV", 0),
        "FG_PCT": p_avg.get("FG_PCT", 0),
    }

    # L5 / L10
    games = []
    for g in l10_games:
        try: d_str = g.get("GAME_DATE", "")
        except: continue
        # Parse date
        try:
            d_obj = datetime.strptime(d_str, "%b %d, %Y")
            d_iso = d_obj.strftime("%Y-%m-%d")
        except: d_iso = d_str

        # Matchup parsing (e.g., "OKC @ LAL" or "OKC vs. LAL")
        matchup = g.get("MATCHUP", "")
        is_home = "vs." in matchup
        # Opponent extraction
        opp = ""
        if "@" in matchup:
            opp = matchup.split("@")[-1].strip()
        elif "vs." in matchup:
            opp = matchup.split("vs.")[-1].strip()

        games.append({
            "date":   d_iso,
            "opp":    opp,
            "is_home": is_home,
            "result": g.get("WL", ""),
            "MIN":    g.get("MIN", 0),
            "PTS":    g.get("PTS", 0),
            "REB":    g.get("REB", 0),
            "AST":    g.get("AST", 0),
            "FG3M":   g.get("FG3M", 0),
            "FGM":    g.get("FGM", 0),
            "FGA":    g.get("FGA", 0),
            "STL":    g.get("STL", 0),
            "BLK":    g.get("BLK", 0),
            "TOV":    g.get("TOV", 0),
        })

    return {
        "id":         pid,
        "name":       name,
        "position":   pos,
        "season_avg": season_avg,
        "l10_games":  games[:20],   # nom historique mais on stocke 20 (L20 pour stabilite stats)
    }


def fetch_team_player_data(team_id, season=CURRENT_SEASON, top_n=10):
    """
    Pour une equipe, retourne les top N joueurs (par minutes) avec
    leurs season avgs + leurs L20 derniers matchs.
    """
    avgs = team_player_averages(team_id, season=season)
    # Filtre joueurs avec >= 5 matchs et minutes >= 12
    avgs = [p for p in avgs if (p.get("GP") or 0) >= 5 and (p.get("MIN") or 0) >= 12]
    avgs.sort(key=lambda p: p.get("MIN", 0), reverse=True)
    top = avgs[:top_n]

    out = []
    for p in top:
        pid = p.get("PLAYER_ID")
        l20 = []
        if pid:
            try:
                l20 = player_recent_form(pid, season=season, n=20)
            except Exception as e:
                print(f"  [gamelog err] {p.get('PLAYER_NAME')}: {e}")
        out.append(_player_summary(p, l20))
    return out


def main():
    print("=== NBA scraper ===")
    today = date.today()
    dates = [today.isoformat(), (today + timedelta(days=1)).isoformat()]

    all_games = []
    for d in dates:
        games = games_on_date(d)
        for g in games:
            # Filtre uniquement matchs pas encore joues (status_code 1 = pre)
            if g.get("status_code") in (1, 2):  # 1=pre, 2=live
                all_games.append(g)
        print(f"  {d}: {len(games)} games ({'pre/live' if any(x.get('status_code') in (1,2) for x in games) else 'tous finis'})")

    if not all_games:
        # En periode quiet, on prend quand meme la liste meme finis pour ne pas vide
        for d in dates:
            for g in games_on_date(d):
                all_games.append(g)
        if not all_games:
            print("\n[!] Aucun match NBA. nba_matches.json restera vide.")
            with open("data/nba_matches.json", "w", encoding="utf-8") as f:
                json.dump([], f)
            with open("data/nba_player_stats.json", "w", encoding="utf-8") as f:
                json.dump({}, f)
            return

    print(f"\n[1/2] {len(all_games)} matchs a traiter")

    # Stats avancees par equipe (pace, off/def rating) - 1 seul fetch pour toute la saison
    try:
        team_adv = team_advanced_map(season=CURRENT_SEASON)
        paces = [v.get("pace", 0) for v in team_adv.values() if v.get("pace", 0) > 0]
        league_avg_pace = sum(paces) / len(paces) if paces else 99.0
        print(f"    [team adv] {len(team_adv)} equipes, pace moyen ligue = {league_avg_pace:.1f}")
    except Exception as e:
        print(f"    [team adv err] {e}")
        team_adv, league_avg_pace = {}, 99.0

    # Stats opponent (failles defensives par stat)
    try:
        team_opp = team_opponent_map(season=CURRENT_SEASON)
        # Moyennes ligue par stat encaissee
        def _avg(field):
            vals = [v.get(field, 0) for v in team_opp.values() if v.get(field, 0) > 0]
            return sum(vals) / len(vals) if vals else 0
        league_def = {
            "opp_pts":  _avg("opp_pts"),
            "opp_reb":  _avg("opp_reb"),
            "opp_ast":  _avg("opp_ast"),
            "opp_fg3m": _avg("opp_fg3m"),
        }
        print(f"    [team opp] {len(team_opp)} equipes - ligue avg: PTS encaissees={league_def['opp_pts']:.1f} · REB={league_def['opp_reb']:.1f} · AST={league_def['opp_ast']:.1f} · 3PM={league_def['opp_fg3m']:.1f}")
    except Exception as e:
        print(f"    [team opp err] {e}")
        team_opp, league_def = {}, {"opp_pts":113,"opp_reb":44,"opp_ast":26,"opp_fg3m":13}

    player_stats = {}
    for g in all_games:
        gid = g["game_id"]
        print(f"\n  {g['away_city']} {g['away']} @ {g['home_city']} {g['home']}")

        # Players des 2 equipes
        home_players = fetch_team_player_data(g["home_id"])
        print(f"    Home roster top: {len(home_players)} joueurs")
        away_players = fetch_team_player_data(g["away_id"])
        print(f"    Away roster top: {len(away_players)} joueurs")

        # Contexte equipe (pace, ratings, opp def stats)
        # BR maps sont keyees par team name court (ESPN style) : "Knicks", "Thunder"...
        home_ctx = team_adv.get(g["home"], {}) or team_adv.get(g["home_id"], {})
        away_ctx = team_adv.get(g["away"], {}) or team_adv.get(g["away_id"], {})
        home_def = team_opp.get(g["home"], {}) or team_opp.get(g["home_id"], {})
        away_def = team_opp.get(g["away"], {}) or team_opp.get(g["away_id"], {})

        player_stats[gid] = {
            "home_team":    g["home"],
            "away_team":    g["away"],
            "home_id":      g["home_id"],
            "away_id":      g["away_id"],
            "home_players": home_players,
            "away_players": away_players,
            "home_pace":    home_ctx.get("pace", league_avg_pace),
            "away_pace":    away_ctx.get("pace", league_avg_pace),
            "home_off_rtg": home_ctx.get("off_rating", 0),
            "home_def_rtg": home_ctx.get("def_rating", 0),
            "away_off_rtg": away_ctx.get("off_rating", 0),
            "away_def_rtg": away_ctx.get("def_rating", 0),
            "home_ppg":     home_ctx.get("ppg", 110),
            "away_ppg":     away_ctx.get("ppg", 110),
            "league_avg_pace": league_avg_pace,
            "game_date":    g.get("date", ""),
            # Defense allowed per stat (failles defensives)
            "home_def_allowed": home_def,
            "away_def_allowed": away_def,
            "league_def_avg":   league_def,
        }

    # Sauvegardes
    with open("data/nba_matches.json", "w", encoding="utf-8") as f:
        json.dump(all_games, f, ensure_ascii=False, indent=2)
    with open("data/nba_player_stats.json", "w", encoding="utf-8") as f:
        json.dump(player_stats, f, ensure_ascii=False, indent=2)

    print(f"\n[OK] {len(all_games)} matchs · {sum(len(v.get('home_players',[]))+len(v.get('away_players',[])) for v in player_stats.values())} joueurs")
    print(f"     data/nba_matches.json + data/nba_player_stats.json")


if __name__ == "__main__":
    main()
