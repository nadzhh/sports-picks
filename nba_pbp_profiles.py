"""
nba_pbp_profiles.py — Profils joueurs et equipes tires du play-by-play reel (ESPN, format v2).

Complete les taux « box-score » de nba_sim.NBAState (usage, %, rebonds, passes...)
avec ce que seul le play-by-play revele :
- joueur : repartition des tirs par zone et reussite par zone (retrecie vers la
  reussite moyenne de la ligue dans la zone), part de paniers assistes, types de
  tir (dunk, layup, pull-up...), positions reelles des tirs (pour l'animation),
  et profil d'occupation du terrain minute par minute (reconstitue a partir des
  remplacements) -> rotations realistes : titulaires au debut des QT1/QT3 et en fin
  de match, remplacants en milieu de quart-temps ;
- equipe : reussite concedee par zone (qualite defensive par zone).

Coordonnees ESPN : pieds, demi-terrain, panier en (25, 0).
Usage : build_profiles([2026, 2027]) -> dict (quelques secondes pour ~1 400 matchs).
"""
import math
from collections import defaultdict

import numpy as np

from nba_boxdb import load_plays, load_season

ZONES = ("RIM", "PAINT", "MID", "C3", "AB3")
ZONE_LABEL = {"RIM": "au cercle", "PAINT": "dans la raquette", "MID": "à mi-distance",
              "C3": "à 3 pts dans le corner", "AB3": "à 3 pts"}
RIM_TYPES = {"94", "95", "96", "109", "110", "111", "112", "115", "116", "117", "118", "124", "125", "126",
             "127", "128", "129", "138", "141", "142", "143", "149", "150", "151", "152", "153"}
FT_TYPES = {str(t) for t in range(97, 109)} | {"157", "165", "166"}
T_SUB, T_DREB, T_OREB, T_END_PERIOD = "584", "155", "156", "412"
MAX_XY = 25            # positions de tir gardees par joueur et par zone
K_ZONE = 25.0          # pseudo-tentatives du retrait vers la ligue (par zone)
SEASON_W = 0.6         # poids d'un match de la saison precedente
HL_GAMES = 40.0        # demi-vie (matchs du joueur) de la ponderation


def zone_of(x, y, attempted, type_id):
    if attempted == 3:
        if y is not None and y <= 9: return "C3"
        return "AB3"
    if x is None or y is None:
        return "RIM" if str(type_id) in RIM_TYPES else "MID"
    d = math.hypot(x - 25, y)
    if d < 4.5: return "RIM"
    if d < 14: return "PAINT"
    return "MID"


def _game_seconds(period, clock):
    """Secondes ecoulees depuis le debut du match (prolongations de 5 min)."""
    if period is None or clock is None: return None
    if period <= 4: return (period - 1) * 720 + (720 - clock)
    return 2880 + (period - 5) * 300 + (300 - clock)


def _on_court_minutes(plays, box_game):
    """Minutes (0..47) ou chaque joueur etait sur le terrain, reconstituees a partir des
    remplacements : un joueur qui agit (ou sort) avant d'etre entre dans une periode y
    etait au debut. Retourne {player_id: np.array(48, bool)}."""
    team_of = {}
    starters = set()
    for side in ("home", "away"):
        for p in box_game[side].get("players") or []:
            team_of[p["id"]] = box_game[side]["id"]
            if p.get("starter"): starters.add(p["id"])
    occ = defaultdict(lambda: np.zeros(48, bool))
    by_period = defaultdict(list)
    for p in plays:
        if p.get("period") and p["period"] <= 4:
            by_period[p["period"]].append(p)
    for period in range(1, 5):
        evs = by_period.get(period, [])
        if not evs: continue
        on = set(starters) if period == 1 else set()
        if period > 1:
            entered = set()
            for e in evs:
                if e["type"] == T_SUB:
                    if e.get("p2") and e["p2"] not in entered: on.add(e["p2"])
                    if e.get("p1"): entered.add(e["p1"])
                else:
                    for pid in (e.get("p1"), e.get("p2"), e.get("p3")):
                        if pid and pid in team_of and pid not in entered: on.add(pid)
        start = (period - 1) * 720
        last_t, cur = start, set(on)

        def mark(players, t0, t1):
            m0, m1 = int(t0 // 60), int(math.ceil(t1 / 60))
            for pid in players:
                occ[pid][max(m0, 0):min(m1, 48)] = True
        for e in evs:
            if e["type"] != T_SUB: continue
            t = _game_seconds(period, e.get("clock"))
            if t is None: continue
            if t - last_t >= 30: mark(cur, last_t, t)
            cur.discard(e.get("p2")); cur.add(e.get("p1"))
            last_t = t
        mark(cur, last_t, start + 720)
    return occ


def load_data(years):
    """Box-scores et play-by-play des saisons (a charger une fois pour plusieurs profils)."""
    box, plays, season_of = {}, {}, {}
    for y in years:
        box.update(load_season(y))
        pl = load_plays(y)
        plays.update(pl)
        for gid in pl: season_of[gid] = y
    return box, plays, season_of


def build_profiles(years, before=None, data=None):
    """before : date ISO exclue (profils « a la veille » pour les backtests).
    data : resultat de load_data(years) pour eviter de relire les fichiers."""
    box, plays, season_of = data or load_data(years)
    cur_season = max(years)
    if before is not None:
        # saison « en cours » a la date : la plus recente ayant deja des matchs avant elle
        played = {season_of[g] for g in plays if g in box and box[g]["date"] < before}
        cur_season = max(played) if played else min(years)
    gids = sorted((g for g in plays if g in box and (before is None or box[g]["date"] < before)),
                  key=lambda g: box[g]["date"])

    # Comptes ponderes (poids par match : saison precedente decotee, recence)
    pz = defaultdict(lambda: defaultdict(lambda: np.zeros(3)))     # pid -> zone -> [FGA, FGM, assisted]
    ptype = defaultdict(lambda: defaultdict(lambda: defaultdict(float)))
    pxy = defaultdict(lambda: defaultdict(list))
    tz = defaultdict(lambda: defaultdict(lambda: np.zeros(2)))     # equipe en defense -> zone -> [FGA, FGM]
    lz = defaultdict(lambda: np.zeros(2))
    occ_sum = defaultdict(lambda: np.zeros(48))
    occ_w = defaultdict(float)
    n_games_player = defaultdict(int)
    andone = defaultdict(lambda: np.zeros(2))                       # zone -> [makes, and-ones]
    block_by = defaultdict(float)
    blocked_zone = defaultdict(lambda: np.zeros(2))                 # zone -> [FGA, contres]

    # Recence : index du match dans l'historique de chaque joueur, du plus recent au plus ancien
    player_games = defaultdict(list)
    for gid in gids:
        for side in ("home", "away"):
            for p in box[gid][side].get("players") or []:
                if not p.get("dnp") and p.get("MIN"): player_games[p["id"]].append(gid)
    weight = {}
    for pid, gl in player_games.items():
        n = len(gl)
        for i, gid in enumerate(gl):
            age = n - 1 - i
            weight[(pid, gid)] = 0.5 ** (age / HL_GAMES) * (1.0 if season_of[gid] == cur_season else SEASON_W)

    for gid in gids:
        g = box[gid]
        team_ids = {g["home"]["id"], g["away"]["id"]}
        evs = plays[gid]
        for k, e in enumerate(evs):
            t = e["type"]
            if not e.get("shooting") or t in FT_TYPES or e.get("attempted") not in (2, 3) or not e.get("p1"):
                continue
            shooter = e["p1"]
            w = weight.get((shooter, gid), 0.0)
            if w == 0: continue
            z = zone_of(e.get("x"), e.get("y"), e.get("attempted"), t)
            made = bool(e.get("scoring"))
            rec = pz[shooter][z]
            rec[0] += w
            if made:
                rec[1] += w
                if e.get("p2"): rec[2] += w
                # and-one : lancer franc « 1 of 1 » du meme joueur a la meme seconde
                nxt = evs[k + 1:k + 4]
                andone[z] += [1, any(n["type"] == "97" and n.get("p1") == shooter and n.get("clock") == e.get("clock") for n in nxt)]
            else:
                blocked_zone[z] += [1, bool(e.get("p2"))]
                if e.get("p2"): block_by[e["p2"]] += 1
            ptype[shooter][z][t] += w
            if e.get("x") is not None and len(pxy[shooter][z]) < 400:
                pxy[shooter][z].append((e["x"], e["y"]))
            defense = next((tid for tid in team_ids if tid != e.get("team")), None)
            if defense:
                tz[defense][z] += [1, made]
            lz[z] += [1, made]
        occ = _on_court_minutes(evs, g)
        for side in ("home", "away"):
            for p in g[side].get("players") or []:
                pid = p["id"]
                if p.get("dnp") or not p.get("MIN") or pid not in occ: continue
                w = weight.get((pid, gid), 0.0)
                occ_sum[pid] += w * occ[pid]
                occ_w[pid] += w
                n_games_player[pid] += 1

    league = {z: lz[z][1] / max(lz[z][0], 1) for z in ZONES}
    league_mix = np.array([lz[z][0] for z in ZONES]); league_mix = league_mix / league_mix.sum()
    players = {}
    for pid in set(pz) | set(occ_sum):
        zones = {}
        fga_tot = sum(pz[pid][z][0] for z in ZONES)
        for z in ZONES:
            a, m, ast = pz[pid][z]
            pct = (m + K_ZONE * league[z]) / (a + K_ZONE)
            zones[z] = {
                "w": a / fga_tot if fga_tot else float(league_mix[ZONES.index(z)]),
                "pct": pct, "fga": a,
                "ast": (ast + 3 * 0.6) / (m + 3) if z != "RIM" else (ast + 3 * 0.5) / (m + 3),
                "types": sorted(ptype[pid][z].items(), key=lambda x: -x[1])[:6],
                "xy": pxy[pid][z][-MAX_XY:],
            }
        prof = occ_sum[pid] / occ_w[pid] if occ_w[pid] else None
        players[pid] = {"zones": zones, "fga": fga_tot, "occ": prof.tolist() if prof is not None else None,
                        "n_games": n_games_player[pid]}
    # Gabarits de rotation (joueurs sans historique) : titulaire type / remplacant type
    occs = [np.array(p["occ"]) for p in players.values() if p["occ"] is not None and p["n_games"] >= 5]
    starter_tpl = np.mean([o for o in occs if o[0] > 0.8], axis=0) if occs else np.full(48, 0.7)
    bench_tpl = np.mean([o for o in occs if o[0] < 0.2 and o.mean() > 0.2], axis=0) if occs else np.full(48, 0.35)
    teams = {}
    for tid, zz in tz.items():
        teams[tid] = {z: ((zz[z][1] + 60 * league[z]) / (zz[z][0] + 60)) / league[z] for z in ZONES}
    return {
        "league": league, "league_mix": dict(zip(ZONES, league_mix.tolist())),
        "and_one": {z: andone[z][1] / max(andone[z][0], 1) for z in ZONES},
        "block_rate": {z: blocked_zone[z][1] / max(blocked_zone[z][0], 1) for z in ZONES},
        "players": players, "team_def": teams, "n_games": len(gids),
        "starter_tpl": starter_tpl.tolist(), "bench_tpl": bench_tpl.tolist(),
    }


if __name__ == "__main__":
    import sys, time
    t0 = time.time()
    prof = build_profiles([int(a) for a in sys.argv[1:]] or [2026, 2027])
    print(f"{prof['n_games']} matchs, {len(prof['players'])} joueurs, {time.time() - t0:.1f}s")
    print("reussite ligue par zone :", {z: round(v, 3) for z, v in prof["league"].items()})
    print("repartition des tirs   :", {z: round(v, 3) for z, v in prof["league_mix"].items()})
    print("and-one                :", {z: round(v, 3) for z, v in prof["and_one"].items()})
    print("taux de contre (ratés) :", {z: round(v, 3) for z, v in prof["block_rate"].items()})
