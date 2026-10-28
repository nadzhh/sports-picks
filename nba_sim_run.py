"""
nba_sim_run.py — Simule les matchs NBA du jour et du lendemain (production).

1. Met a jour la base box-score (nba_boxdb, incremental) et reconstruit l'etat du modele.
2. Pour chaque match a venir : effectif ESPN, statuts du rapport officiel NBA des blessures
   (nba_injury_report, toutes les 15 min ; ESPN si l'equipe n'a pas encore declare) -> absents
   retires, incertains ponderes par leur probabilite de jouer mesuree (STATUS_PLAY), lignes Vegas
   et props DraftKings publies par ESPN (gratuits, pas de credits The Odds API).
3. 10 000 simulations par match -> data/nba_db/nba_sim.json : score attendu et sa dispersion,
   % de victoire, scenario le plus typique, distributions joueurs, et pour chaque prop
   la probabilite simulee face a celle du bookmaker (marge retiree).
4. 5 000 simulations possession par possession (nba_poss_sim) -> « match type » rejoue
   par le lecteur video du site : data/replays/<game_id>.json (nba_replay).

Usage : python nba_sim_run.py [--sims 10000] [--date 2026-10-27]
        (en production, la veille nba_live.py appelle prepare / sim_games / save match par match)
"""
import argparse, json, os, time
from collections import defaultdict
from functools import lru_cache
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import numpy as np

import nba_injury_report as IR
import nba_sim_bilan
from nba_boxdb import BASE, _get, _lines, load_season, season_year, update_season
from nba_client import team_roster
from nba_props_hist import fetch_game_props, props_main_lines
from nba_sim import NBAState, simulate
from nba_pbp_profiles import build_profiles
from nba_poss_sim import pick_typical, simulate as poss_simulate
from nba_replay import build_replay

# Versionne : la veille nba_live.py resimule match par match et repart de la derniere version
OUT_PATH = "data/nba_sim.json"
STAT_COMPS = {"PTS": ("PTS",), "REB": ("REB",), "AST": ("AST",), "3PM": ("3PM",), "PRA": ("PTS", "REB", "AST"),
              "PR": ("PTS", "REB"), "PA": ("PTS", "AST"), "RA": ("REB", "AST"), "STL": ("STL",), "BLK": ("BLK",)}
REPLAY_DIR = Path("data/replays")   # versionne : publie avec le site, charge a la demande par le lecteur
REPLAY_SIMS = 5000
REPLAY_KEEP_DAYS = 3
# Probabilite de jouer selon le statut officiel, mesuree sur la saison 2025-26 : rapports de 13 h
# et 18 h (ET) de chaque jour de match confrontes aux feuilles de match (26 353 lignes, hors G League).
# A joue : Out 1 %, Doubtful 0-1 %, Questionable 52-55 %, Probable 91-92 %, Available 86-89 %
# (joueur liste qui revient), contre ~97 % pour un joueur de rotation non liste -> facteurs ci-dessous.
# « day-to-day » (ESPN, quand l'equipe n'a pas encore rempli le rapport officiel) : non mesure.
STATUS_PLAY = {"questionable": 0.57, "probable": 0.94, "available": 0.9, "day-to-day": 0.5}
STATUS_OUT = ("out", "suspension", "doubtful")
STATUS_BACK = ("available", "probable", "questionable")   # annonce par son equipe : pas « inactif »
MIN_AVAILABILITY = 0.1

# Selection de props. Regle validee en aveugle (nba_backtest.py --blind : effectif du match
# precedent, sans lignes Vegas) sur 3 echantillons : ESPN BET oct-nov 2025 (2 853 props),
# ESPN BET avr-juin 2026 (1 243), DraftKings 2026-27 (548). Le simulateur annonce le sens
# du mouvement des cotes dans 74-80 % des cas : ROI a la cote d'OUVERTURE +10 a +20 %,
# a la cote de CLOTURE -1 a -7 %. L'avantage vient donc de prendre la cote tot.
# Points et combines (PRA, P+R, P+A) : nuls ou negatifs ; rebonds : instables -> exclus.
PICK_STATS = ("3PM", "AST", "STL", "BLK")
PICK_GAP = 0.08        # ecart minimal simu corrigee - marche sans marge
SIM_BIAS = 0.025       # le simu sous-estime l'over de 2-3 pts en moyenne (mesure sur les 3 echantillons)
PICK_MIN_PLAY = 0.9    # joueurs incertains exclus
PICKS_PATH = Path("data/nba_sim_picks.json")   # versionne : suivi reel des picks proposes


def build_state(year):
    st = NBAState()
    games = []
    for y in (year - 1, year):
        games += [(gid, g, y) for gid, g in load_season(y).items()]
    games.sort(key=lambda x: (x[1]["date"], x[0]))
    st.results = {}   # team_id -> [(date, saison, gagne)] pour la forme et le bilan
    for gid, g, y in games:
        st.add_game(gid, g, y)
        h, a = g["home"], g["away"]
        for t, o in ((h, a), (a, h)):
            st.results.setdefault(t["id"], []).append((g["date"], y, t["pts"] > o["pts"]))
    st.season = year
    return st


def _form(st, team_id, n=5):
    """5 derniers resultats (V/D, du plus ancien au plus recent) et bilan de la saison en cours."""
    res = st.results.get(team_id, [])
    cur = [w for _, y, w in res if y == st.season]
    return {"form": ["V" if w else "D" for _, _, w in res[-n:]],
            "record": f"{sum(cur)}-{len(cur) - sum(cur)}"}


def _buckets(total, margin, h_abbr, a_abbr):
    """Total de points par tranches de 10 et ecarts les plus probables (par tranches de 5)."""
    edges = [-np.inf, 200, 210, 220, 230, 240, 250, 260, 270, np.inf]
    labels = ["<200", "200", "210", "220", "230", "240", "250", "260", "270+"]
    cnt = np.histogram(total, bins=edges)[0] / len(total)
    tot = [{"label": l, "p": round(float(c), 4)} for l, c in zip(labels, cnt)]
    mg = []
    for lo, hi, lab in ((1, 5, "1-5"), (6, 10, "6-10"), (11, 15, "11-15"), (16, 999, "16+")):
        mg.append({"team": h_abbr, "range": lab, "p": round(float(((margin >= lo) & (margin <= hi)).mean()), 4)})
        mg.append({"team": a_abbr, "range": lab, "p": round(float(((-margin >= lo) & (-margin <= hi)).mean()), 4)})
    mg.sort(key=lambda x: -x["p"])
    return tot, mg


def upcoming_games(day):
    out = []
    # veille comprise : a 01:00 UTC, les matchs de la soiree americaine sont dates de la veille
    for d in (day - timedelta(days=1), day, day + timedelta(days=1)):
        sb = _get(f"{BASE}/scoreboard?dates={d:%Y%m%d}") or {}
        for ev in sb.get("events") or []:
            if ((ev.get("status") or {}).get("type") or {}).get("state") != "pre": continue
            comp = (ev.get("competitions") or [{}])[0]
            sides = {c["homeAway"]: c["team"] for c in comp.get("competitors") or []}
            out.append({"gid": str(ev["id"]), "date": ev.get("date", ""), "home": sides.get("home", {}),
                        "away": sides.get("away", {})})
    return out


def _availability(summary):
    """{athlete_id: statut} depuis le bloc injuries du summary ESPN."""
    out = {}
    for t in (summary or {}).get("injuries") or []:
        for i in t.get("injuries") or []:
            aid = str((i.get("athlete") or {}).get("id", ""))
            if aid: out[aid] = (i.get("status") or "").lower()
    return out


def _et_day(iso):
    """Date du match a New York (« 10/28/2026 »), comme dans le rapport officiel."""
    dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    try:
        from zoneinfo import ZoneInfo
        return dt.astimezone(ZoneInfo("America/New_York")).strftime("%m/%d/%Y")
    except Exception:
        return (dt - timedelta(hours=4)).strftime("%m/%d/%Y")


def statuses(g, report, summary=None):
    """Statuts des deux effectifs : {cote: {"src", "status": {id: statut}, "reason": {id: raison}}}.
    Rapport officiel NBA si l'equipe l'a rempli (il doit lister TOUT joueur non disponible :
    absent du rapport = disponible), sinon blessures ESPN du summary du match."""
    if summary is None:
        summary = _get(f"{BASE}/summary?event={g['gid']}") or {}
    espn = _availability(summary)
    out = {}
    for side in ("home", "away"):
        tid = str(g[side].get("id"))
        roster = [{"id": p["PLAYER_ID"], "name": p["PLAYER_NAME"]} for p in team_roster(tid, ttl=12 * 3600)]
        entry = IR.team_entry(report, g[side].get("displayName", ""), _et_day(g["date"]))
        if entry and entry["submitted"]:
            extra = []
            off = IR.status_map(entry, roster, extra)
            out[side] = {"src": "officiel", "status": {k: v[0] for k, v in off.items()},
                         "reason": {k: v[1] for k, v in off.items()},
                         "extra": [{"name": p["name"], "status": p["status"].lower(), "reason": p["reason"]}
                                   for p in extra]}
        else:
            ids = {p["id"] for p in roster}
            out[side] = {"src": "ESPN", "status": {k: v for k, v in espn.items() if k in ids}, "reason": {}}
    return out


def signature(st):
    """Empreinte des statuts : une difference declenche une nouvelle simulation (nba_live)."""
    return "|".join(f"{k}:{v}" for side in ("home", "away") for k, v in sorted(st[side]["status"].items()))


def _roster(team_id, injuries, reasons=None):
    roster, statuses, out = [], {}, []
    for p in team_roster(team_id, ttl=12 * 3600):
        st = injuries.get(p["PLAYER_ID"], "")
        if st in STATUS_OUT:
            out.append({"id": p["PLAYER_ID"], "name": p["PLAYER_NAME"], "status": st,
                        "reason": (reasons or {}).get(p["PLAYER_ID"], "")}); continue
        roster.append({"id": p["PLAYER_ID"], "name": p["PLAYER_NAME"], "pos": p.get("POSITION", "")})
        if st: statuses[p["PLAYER_ID"]] = st
    return roster, statuses, out


def _typical(res, inp):
    """Index du tirage le plus proche du « match median » (score + stats des titulaires)."""
    hp, ap = res["home"]["PTS"].sum(axis=1), res["away"]["PTS"].sum(axis=1)
    z = ((hp - np.median(hp)) / hp.std()) ** 2 + ((ap - np.median(ap)) / ap.std()) ** 2
    # Le score doit coller au score median : seuls les tirages a +/- 2 pts sont candidats
    near = (np.abs(hp - np.median(hp)) <= 2) & (np.abs(ap - np.median(ap)) <= 2)
    z = z + np.where(near, 0.0, 100.0)
    for s in ("home", "away"):
        top = np.argsort([-p["m_mu"] * p["p_play"] for p in inp[s]["players"]])[:5]
        for i in top:
            for key in ("PTS", "REB", "AST"):
                x = res[s][key][:, i]
                if x.std() > 0: z += ((x - np.median(x)) / x.std()) ** 2 / 15
    return int(np.argmin(z))


def _dec(am):
    return 1 + am / 100.0 if am > 0 else 1 + 100.0 / -am


def build_inputs(st, g, report=None):
    """Entrees du simulateur pour un match a venir : effectifs ESPN moins les absents (rapport
    officiel NBA, sinon ESPN), incertains ponderes, inactifs de longue duree, lignes Vegas.
    -> (inp, lines, jerseys) ; inp["injuries"] = statuts utilises (affichage, veille)."""
    summary = _get(f"{BASE}/summary?event={g['gid']}") or {}
    sts = statuses(g, report, summary)
    h_id, a_id = str(g["home"].get("id")), str(g["away"].get("id"))
    h_roster, h_st, h_out = _roster(h_id, sts["home"]["status"], sts["home"]["reason"])
    a_roster, a_st, a_out = _roster(a_id, sts["away"]["status"], sts["away"]["reason"])
    lines = _lines(summary, h_id) if summary else None
    inp = st.project(h_id, a_id, h_roster, a_roster, lines)
    inp["home"]["out"], inp["away"]["out"] = h_out, a_out
    inp["injuries"] = sts
    for s, statuses_, tid in (("home", h_st, h_id), ("away", a_st, a_id)):
        for p in inp[s]["players"]:
            if statuses_.get(p["id"]) in STATUS_PLAY:
                p["p_play"] *= STATUS_PLAY[statuses_[p["id"]]]
                p["status"] = statuses_[p["id"]]
            # Liste par son equipe au rapport officiel comme disponible / probable / incertain (hors G
            # League) : il revient de blessure. Sans ce test, ses matchs manques le faisaient passer pour
            # inactif et sa probabilite de jouer etait divisee par 10.
            if (sts[s]["src"] == "officiel" and statuses_.get(p["id"]) in STATUS_BACK
                    and "G League" not in sts[s]["reason"].get(p["id"], "")):
                continue
            # Absent de TOUTES les feuilles de match recentes sans etre declare blesse :
            # inactif (blessure longue, G-League). Une absence isolee ne compte pas.
            if st.availability(p["id"], tid) == 0:
                p["p_play"] *= MIN_AVAILABILITY
            # Sur la feuille mais sans jouer aux 3 derniers matchs (choix de l'entraineur) :
            # hors rotation. Aligne la production sur le backtest valide (--blind : joueurs
            # ayant joue le match precedent), sinon ces joueurs gardaient leurs minutes de
            # la saison passee et sous-projetaient tout l'effectif (biais vers les unders).
            elif st.rotation_share(p["id"], tid) == 0:
                p["p_play"] *= MIN_AVAILABILITY
    jerseys = {p["PLAYER_ID"]: p.get("JERSEY", "") for tid in (h_id, a_id) for p in team_roster(tid, ttl=12 * 3600)}
    return inp, lines, jerseys


def _ml_prob(ml):
    if ml is None: return None
    ml = float(ml)
    return 100 / (ml + 100) if ml > 0 else -ml / (-ml + 100)


def run_replay(g, inp, lines, jerseys, profiles):
    """5 000 matchs possession par possession -> deroule du match type (data/replays/<gid>.json)."""
    seed = int(g["gid"]) % (2 ** 31)
    res = poss_simulate(inp, profiles, n=REPLAY_SIMS, seed=seed)
    k = pick_typical(res, inp)
    ph, pa = _ml_prob((lines or {}).get("home_ml")), _ml_prob((lines or {}).get("away_ml"))
    team = lambda t: {"abbr": t.get("abbreviation"), "name": t.get("displayName"), "short": t.get("name") or t.get("abbreviation"),
                      "color": t.get("color"), "alt": t.get("alternateColor")}
    meta = {"home": team(g["home"]), "away": team(g["away"]), "date": g["date"], "lines": lines,
            "books_p_home": round(ph / (ph + pa), 3) if ph and pa else None}
    rp = build_replay(res, k, inp, profiles, jerseys, meta, seed=seed)
    REPLAY_DIR.mkdir(parents=True, exist_ok=True)
    path = REPLAY_DIR / f"{g['gid']}.json"
    path.write_text(json.dumps(rp, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    sm = rp["summary"]
    return {"path": path.as_posix(), "final": rp["final"], "p_home": sm["p_home"], "avg": sm["avg"],
            "p_over": sm["p_over"], "n": sm["n"], "k": sm["k"]}


def _clean_replays():
    if not REPLAY_DIR.exists(): return
    limit = time.time() - REPLAY_KEEP_DAYS * 86400
    for f in REPLAY_DIR.glob("*.json"):
        if f.stat().st_mtime < limit: f.unlink()


LOG_STATS = ("PTS", "REB", "AST", "3PM", "STL", "BLK")


@lru_cache(maxsize=2)
def _logs(year):
    """Feuilles de match reelles (saison et precedente) pour l'argumentaire des picks :
    {joueur: [match...]}, {equipe: [stats concedees par match...]}, moyenne ligue concedee."""
    players, allowed = defaultdict(list), defaultdict(list)
    for y in (year - 1, year):
        for gid, g in sorted(load_season(y).items(), key=lambda x: (x[1]["date"], x[0])):
            for s, o in (("home", "away"), ("away", "home")):
                tot = dict.fromkeys(LOG_STATS, 0.0)
                for p in g[s].get("players") or []:
                    if p.get("dnp") or not p.get("MIN"): continue
                    for c in LOG_STATS: tot[c] += p.get(c) or 0
                    players[p["id"]].append({"date": g["date"], "season": y, "team": g[s]["id"], "MIN": p["MIN"],
                                             **{c: p.get(c) or 0 for c in LOG_STATS}})
                allowed[g[o]["id"]].append(tot)
    league = {c: float(np.mean([np.mean([r[c] for r in rs[-15:]]) for rs in allowed.values() if rs])) for c in LOG_STATS}
    return players, allowed, league


def _why(pid, stat, line, x, minutes, team_id, opp_id, opp_abbr, out, year):
    """Faits qui justifient un pick (le texte est redige par nba_sim_site)."""
    logs, allowed, league = _logs(year)
    comps = STAT_COMPS[stat]
    val = lambda r: sum(r[c] for c in comps)
    hist = logs.get(pid) or []
    cur = [r for r in hist if r["season"] == year]
    prev = [r for r in hist if r["season"] == year - 1]
    agg = lambda rs: {"n": len(rs), "avg": round(float(np.mean([val(r) for r in rs])), 2),
                      "min": round(float(np.mean([r["MIN"] for r in rs])), 1)} if rs else None
    last10 = hist[-10:]
    opp = allowed.get(opp_id, [])[-15:]
    absent = []
    for o in out:   # absents qui comptaient vraiment cette saison
        rs = [r for r in logs.get(o["id"], []) if r["season"] == year and r["team"] == team_id]
        if rs and np.mean([r["MIN"] for r in rs]) >= 18: absent.append(o["name"])
    return {"proj": round(float(x.mean()), 2), "med": float(np.median(x)), "min": round(minutes, 1),
            "cur": agg(cur), "last": [val(r) for r in cur[-5:]][::-1], "prev": agg(prev),
            "l10_over": sum(val(r) > line for r in last10), "l10_n": len(last10),
            "opp": {"abbr": opp_abbr, "allowed": round(float(np.mean([sum(r[c] for c in comps) for r in opp])), 2),
                    "league": round(sum(league[c] for c in comps), 2)} if opp else None,
            "out": absent}


def run_game(st, g, n_sims, profiles=None, report=None, keep_replay=None):
    """keep_replay : match type deja calcule a reprendre tel quel (la veille ne refait la video,
    le calcul le plus long, que si les effectifs changent ou pour la derniere simulation)."""
    inp, lines, jerseys = build_inputs(st, g, report)
    replay = keep_replay
    if profiles is not None and keep_replay is None:
        try:
            replay = run_replay(g, inp, lines, jerseys, profiles)
        except Exception as e:
            print(f"  [!] match type {g['gid']} : {type(e).__name__}: {e}")
    h_id, a_id = inp["home"]["id"], inp["away"]["id"]
    res = simulate(inp, n=n_sims, seed=int(g["gid"]) % (2 ** 31))
    hp, ap = res["home"]["PTS"].sum(axis=1), res["away"]["PTS"].sum(axis=1)
    k = _typical(res, inp)
    tot_hist, margin_top = _buckets(hp + ap, hp - ap, g["home"].get("abbreviation"), g["away"].get("abbreviation"))

    players, typical_box = [], []
    for s in ("home", "away"):
        for i, p in enumerate(inp[s]["players"]):
            on = res[s]["MIN"][:, i] > 0
            if on.mean() < 0.05: continue
            d = {"id": p["id"], "name": p["name"], "side": s, "p_play": round(float(on.mean()), 3),
                 "pos": p.get("pos", ""), "starter": bool(p.get("starter_rate", 0) >= 0.5),
                 "status": p.get("status", ""), "n_season": p["n_season"]}
            for stat, comps in STAT_COMPS.items():
                x = sum(res[s][c][:, i] for c in comps)[on]
                d[stat] = {"mean": round(float(x.mean()), 2),
                           "q": [float(v) for v in np.percentile(x, [10, 25, 50, 75, 90])]}
            d["MIN"] = round(float(res[s]["MIN"][on, i].mean()), 1)
            players.append(d)
            if res[s]["MIN"][k, i] > 0:
                typical_box.append({"id": p["id"], "name": p["name"], "side": s,
                                    **{key: int(round(float(res[s][key][k, i]))) for key in ("MIN", "PTS", "REB", "AST", "3PM")}})

    props = []
    gp = fetch_game_props(g["gid"])
    idx = {p["id"]: (s, i) for s in ("home", "away") for i, p in enumerate(inp[s]["players"])}
    for (aid, stat), m in props_main_lines(gp).items():
        if aid not in idx or stat not in STAT_COMPS: continue
        s, i = idx[aid]
        on = res[s]["MIN"][:, i] > 0
        if on.sum() < 200: continue
        x = sum(res[s][c][:, i] for c in STAT_COMPS[stat])[on]
        p_over = float((x > m["line"]).mean())
        p_play = float(on.mean())
        gap = p_over + SIM_BIAS - m["p_over_novig"]
        pick = ("o" if gap > 0 else "u") if (stat in PICK_STATS and abs(gap) >= PICK_GAP
                                             and p_play >= PICK_MIN_PLAY) else ""
        why = None
        if pick:
            o = "away" if s == "home" else "home"
            why = _why(aid, stat, m["line"], x, float(res[s]["MIN"][on, i].mean()), inp[s]["id"], inp[o]["id"],
                       g[o].get("abbreviation"), inp[s].get("out") or [], season_year(datetime.now(timezone.utc).date()))
        props.append({"id": aid, "name": inp[s]["players"][i]["name"], "side": s, "stat": stat, "line": m["line"],
                      "why": why, "over": m["over"], "under": m["under"], "p_sim": round(p_over, 4),
                      "p_mkt": round(m["p_over_novig"], 4), "p_play": round(p_play, 3),
                      "over_open": m.get("over_open"), "under_open": m.get("under_open"), "pick": pick,
                      "ev_over": round(p_over * _dec(m["over"]) - 1, 4),
                      "ev_under": round((1 - p_over) * _dec(m["under"]) - 1, 4)})

    return {
        "gid": g["gid"], "date": g["date"], "sim_at": datetime.now(timezone.utc).isoformat(timespec="minutes"),
        "report_at": (report or {}).get("at"), "sig": signature(inp["injuries"]),
        "injuries": {s: {"src": inp["injuries"][s]["src"], "out": inp[s]["out"],
                         "extra": inp["injuries"][s].get("extra") or [],
                         "doubt": [{"id": p["id"], "name": p["name"], "status": p["status"],
                                    "reason": inp["injuries"][s]["reason"].get(p["id"], ""), "p_play": p["p_play"]}
                                   for p in inp[s]["players"] if p.get("status")]} for s in ("home", "away")},
        "home": {"id": h_id, "abbr": g["home"].get("abbreviation"), "name": g["home"].get("displayName"),
                 "color": g["home"].get("color"), "alt": g["home"].get("alternateColor"), **_form(st, h_id)},
        "away": {"id": a_id, "abbr": g["away"].get("abbreviation"), "name": g["away"].get("displayName"),
                 "color": g["away"].get("color"), "alt": g["away"].get("alternateColor"), **_form(st, a_id)},
        "lines": lines, "book": (gp or {}).get("book"), "replay": replay,
        "team": {"home_mean": round(float(hp.mean()), 1), "away_mean": round(float(ap.mean()), 1),
                 "total_mean": round(float((hp + ap).mean()), 1), "total_sd": round(float((hp + ap).std()), 1),
                 "margin_mean": round(float((hp - ap).mean()), 1), "margin_sd": round(float((hp - ap).std()), 1),
                 "p_home_win": round(float((hp > ap).mean()), 3), "p_ot": round(float((res["n_ot"] > 0).mean()), 3),
                 "p_close": round(float((np.abs(hp - ap) <= 5).mean()), 3),
                 "total_hist": tot_hist, "margin_top": margin_top,
                 "p_blowout": round(float((np.abs(hp - ap) >= 15).mean()), 3),
                 # Histogramme de l'ecart final (dom - ext), classes de 2 pts de -38 a +38
                 # (les ecarts au-dela de 38 pts sont hors graphique, pas entasses dans la derniere classe)
                 "margin_hist": {"start": -38, "step": 2, "p": [round(float(x), 4) for x in np.histogram(
                     hp - ap, bins=np.arange(-38, 40, 2))[0] / len(hp)]},
                 "home_q": np.percentile(hp, [10, 50, 90]).tolist(), "away_q": np.percentile(ap, [10, 50, 90]).tolist(),
                 "total_q": np.percentile(hp + ap, [10, 50, 90]).tolist(),
                 "margin_q": np.percentile(hp - ap, [10, 50, 90]).tolist(),
                 "p_home_cover": round(float((hp - ap + lines["home_spread"] > 0).mean()), 3)
                 if lines and lines.get("home_spread") is not None else None,
                 "p_over_total": round(float((hp + ap > lines["total"]).mean()), 3)
                 if lines and lines.get("total") else None,
                 "model_only": [round(float(x), 1) for x in inp["model"]]},
        "typical": {"home": int(hp[k]), "away": int(ap[k]), "ot": int(res["n_ot"][k]), "box": typical_box},
        "players": players, "props": props, "n_sims": n_sims,
    }


def _novig(over, under):
    po, pu = _ml_prob(over), _ml_prob(under)
    return po / (po + pu) if po and pu else None


def update_picks(games, year):
    """Suivi reel des picks : premiere cote vue (celle qu'on pouvait prendre), derniere cote
    avant le match (~cloture, pour la CLV), puis resultat depuis la base box-score.
    Un pick reste compte meme s'il disparait ensuite de la selection (il a ete propose)."""
    try:
        book = json.loads(PICKS_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        book = {"picks": {}}
    picks = book["picks"]
    for r in picks.values():   # v1 (27/10) : probas de la 1re cote stockees cote over meme pour un under
        if r.get("v") != 2:
            if r["side"] == "u":
                r["first_p_sim"], r["first_p_mkt"] = round(1 - r["first_p_sim"], 4), round(1 - r["first_p_mkt"], 4)
            r["v"] = 2
    now = datetime.now(timezone.utc).isoformat(timespec="minutes")
    live = {}
    for g in games:
        teams = {"home": g["home"]["abbr"], "away": g["away"]["abbr"]}
        for p in g.get("props") or []:
            key = f'{g["gid"]}|{p["id"]}|{p["stat"]}|{p["line"]}'
            live[key] = p
            for side in ("o", "u"):
                k = f"{key}|{side}"
                if p.get("pick") == side and k not in picks:
                    price = p["over"] if side == "o" else p["under"]
                    ps, pm = p["p_sim"] + SIM_BIAS, p["p_mkt"]
                    picks[k] = {"gid": g["gid"], "date": g["date"], "id": p["id"], "name": p["name"],
                                "team": teams[p["side"]], "opp": teams["away" if p["side"] == "home" else "home"],
                                "stat": p["stat"], "line": p["line"], "side": side,
                                "first_price": price, "first_seen": now,
                                # probas du cote du pick (comme last_p_mkt)
                                "first_p_sim": round(ps if side == "o" else 1 - ps, 4),
                                "first_p_mkt": round(pm if side == "o" else 1 - pm, 4), "v": 2,
                                "open_price": p["over_open"] if side == "o" else p["under_open"]}
    box = load_season(year)
    for k, r in picks.items():
        if r.get("result"): continue
        p = live.get(k.rsplit("|", 1)[0])
        if p:   # match encore a venir : derniere cote vue
            r["last_price"] = p["over"] if r["side"] == "o" else p["under"]
            pm = p["p_mkt"] if r["side"] == "o" else 1 - p["p_mkt"]
            r["last_p_mkt"], r["last_seen"], r["active"] = round(pm, 4), now, p.get("pick") == r["side"]
            continue
        g = box.get(r["gid"])
        if not g: continue
        pl = next((x for sd in ("home", "away") for x in g[sd].get("players") or [] if x["id"] == r["id"]), None)
        if not pl or pl.get("dnp") or not pl.get("MIN"):
            r["result"] = "void"; continue
        v = sum(pl.get(c, 0) for c in STAT_COMPS[r["stat"]])
        r["actual"] = v
        r["result"] = "push" if v == r["line"] else ("win" if (v > r["line"]) == (r["side"] == "o") else "loss")
    PICKS_PATH.write_text(json.dumps(book, ensure_ascii=False, indent=0), encoding="utf-8")
    done = [r for r in picks.values() if r.get("result") in ("win", "loss")]
    print(f"  [picks] {len(picks)} au total, {sum(1 for r in picks.values() if not r.get('result'))} en attente, "
          f"{sum(r['result'] == 'win' for r in done)}-{sum(r['result'] == 'loss' for r in done)} resolus")


def prepare(day, update=True):
    """Base box-score a jour + etat du modele + profils video. -> (year, st, profiles)"""
    year = season_year(day)
    if update:
        if not load_season(year - 1): update_season(year - 1)
        update_season(year)
    st = build_state(year)
    try:
        profiles = build_profiles([year - 1, year])
    except Exception as e:
        print(f"  [!] profils play-by-play indisponibles ({e}) : pas de match type video")
        profiles = None
    return year, st, profiles


def latest_report():
    try:
        rep = IR.latest()
        print(f"  [blessures] rapport officiel NBA du {rep['at']} (ET), {len(rep['teams'])} equipes" if rep
              else "  [blessures] rapport officiel introuvable : statuts ESPN")
        return rep
    except Exception as e:
        print(f"  [!] rapport officiel des blessures : {type(e).__name__}: {e} -> statuts ESPN")
        return None


def sim_games(st, games, n_sims, profiles, report, keep_replays=None):
    out = []
    for g in games:
        try:
            r = run_game(st, g, n_sims, profiles, report, (keep_replays or {}).get(g["gid"]))
        except Exception as e:
            print(f"  [err] {g['gid']}: {type(e).__name__}: {e}"); continue
        t = r["team"]
        print(f"  {r['away']['abbr']:>4s} {t['away_mean']:5.1f} @ {r['home']['abbr']:<4s} {t['home_mean']:5.1f}"
              f"  dom {100 * t['p_home_win']:4.1f}%  typique {r['typical']['away']}-{r['typical']['home']}"
              f"  props {len(r['props'])}"
              + (f"  video {r['replay']['final'][1]}-{r['replay']['final'][0]}" if r.get("replay") else ""))
        out.append(r)
    return out


def load_out():
    try:
        with open(OUT_PATH, encoding="utf-8") as f:
            return json.load(f).get("games") or []
    except (OSError, ValueError):
        return []


def save(games, fresh, year):
    """games : tous les matchs a afficher ; fresh : ceux simules a l'instant (suivi des picks,
    historique). Renvoie le nombre de matchs termines ajoutes a l'historique."""
    try:
        update_picks(fresh, year)
    except Exception as e:
        print(f"  [!] suivi des picks : {type(e).__name__}: {e}")
    n = 0
    try:   # historique : derniere simulation d'avant-match + resultat, pour le bilan de chaque soiree
        nba_sim_bilan.archive(fresh)
        n = nba_sim_bilan.resolve(year)
        print(f"  [bilan] {n} match(s) termine(s) ajoute(s) a l'historique")
    except Exception as e:
        print(f"  [!] historique du simulateur : {type(e).__name__}: {e}")
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump({"generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                   "games": sorted(games, key=lambda g: g.get("date", ""))}, f, ensure_ascii=False)
    return n


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sims", type=int, default=10000)
    ap.add_argument("--date")
    ap.add_argument("--no-update", action="store_true", help="ne met pas la base a jour (tests)")
    a = ap.parse_args()
    day = date.fromisoformat(a.date) if a.date else datetime.now(timezone.utc).date()
    t0 = time.time()
    year, st, profiles = prepare(day, not a.no_update)
    _clean_replays()
    games = upcoming_games(day)
    print(f"=== NBA simulation : {len(games)} matchs a venir (etat construit en {time.time() - t0:.0f}s) ===")
    out = sim_games(st, games, a.sims, profiles, latest_report())
    save(out, out, year)
    print(f"[OK] {OUT_PATH} ({len(out)} matchs, {time.time() - t0:.0f}s)")


if __name__ == "__main__":
    main()
