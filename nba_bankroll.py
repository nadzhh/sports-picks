"""
nba_bankroll.py — Bankroll IA : 100 EUR virtuels geres seuls sur les props NBA du simulateur.

Philosophie (parieur discipline, pas joueur) :
  - on ne parie QUE la ou un avantage est demontre : la selection validee de nba_sim_run (passes,
    paniers a 3 pts, interceptions, contres ; ecart simu / marche >= PICK_GAP), a la premiere cote vue
    (l'avantage mesure est a l'ouverture, il fond quand la cote bouge) ;
  - la probabilite de gagner n'est PAS celle du simulateur (trop sur de lui face au marche) : on la tire
    vers celle du bookmaker (p = p_marche + LAMBDA x ecart), LAMBDA calibre sur les backtests ;
  - mise = fraction de Kelly sur cette probabilite prudente, plafonnee par pari, par match (paris
    correles) et par soiree ; arrondie a 0,50 EUR ; rien si l'esperance est trop faible ;
  - combine : au plus un par soiree, 2 selections de matchs differents, chacune avec un avantage
    net ; mise plus petite (la variance d'un combine est bien plus forte, la marge du bookmaker s'y
    multiplie). Il n'est joue que si son esperance depasse celle des simples.
Les cotes sont celles de DraftKings (publiees par ESPN) : bankroll fictive, pas d'argent reel.

    state = load(); placed = decide(state, games); settle(state); save(state)
"""
import json, math
from datetime import datetime, timezone
from pathlib import Path

BANK_PATH = Path("data/nba_bankroll.json")
START = 100.0
PRM = {
    "lambda":     0.40,   # part de l'ecart simu - marche gardee dans la probabilite (calibre : voir CALIB_NOTE)
    "max_odds":   2.60,   # au-dela, l'« avantage » historique venait de cotes d'ouverture aberrantes
    "hold":       (0.035, 0.11),   # marge du bookmaker sur la ligne : hors plage = cote suspecte, on passe
    "kelly":      0.25,   # fraction de Kelly
    "max_bet":    0.03,   # mise max d'un simple, en part du capital
    "max_game":   0.05,   # exposition max sur un meme match
    "max_night":  0.10,   # exposition max sur une soiree
    "min_ev":     0.05,   # esperance minimale d'un simple
    "min_stake":  0.5,    # EUR
    "combo":      True,
    "combo_ev":   0.08,   # esperance minimale d'un combine
    "combo_max":  0.01,   # mise max d'un combine, en part du capital
}
# Discipline : la bankroll mesure sa propre CLV (cote prise vs derniere cote avant le match, suivi
# data/nba_sim_picks.json). Sans avantage mesure, un bon parieur reduit puis arrete.
PRUDENT = (30, 0.0)    # apres 30 paris regles, CLV moyenne < 0 -> mises divisees par 2
PAUSE = (60, -0.02)    # apres 60 paris regles, CLV moyenne < -2 % -> plus de nouveaux paris
CALIB_NOTE = ("Réglages testés le 28/10/2026 sur 3 échantillons de vraies cotes (oct-nov 2025, avr-juin 2026, "
              "début 2026-27), en rejouant exactement cette règle soirée après soirée : avec les cotes d’ouverture "
              "(scénario optimiste) 100 € deviennent 642 € / 264 € / 124 € ; avec les cotes de clôture (pessimiste) "
              "79 € / 111 € / 86 €. L’avantage n’existe que si la cote est prise tôt, d’où la veille toutes les "
              "10-30 min et la règle de discipline : CLV négative après 30 paris → mises divisées par 2, "
              "sous −2 % après 60 paris → pause.")


def _dec(am):
    return None if am is None else (1 + am / 100 if am > 0 else 1 + 100 / -am)


def _imp(am):
    return 100 / (am + 100) if am > 0 else -am / (-am + 100)


def _round(x):
    return math.floor(x * 2) / 2   # 0,50 EUR


def load(path=BANK_PATH):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"start": START, "created": datetime.now(timezone.utc).isoformat(timespec="minutes"), "bets": []}


def save(state, path=BANK_PATH):
    state["updated"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    Path(path).write_text(json.dumps(state, ensure_ascii=False, indent=0), encoding="utf-8")


def equity(state):
    """Capital = depart + gains/pertes regles (les mises en cours restent comptees dans le capital)."""
    return state["start"] + sum(b.get("pnl", 0.0) for b in state["bets"] if b["status"] != "open")


def cash(state):
    return equity(state) - sum(b["stake"] for b in state["bets"] if b["status"] == "open")


def leg_estimate(c, prm=PRM):
    """Probabilite prudente et esperance d'une selection. c : {p_sim, p_mkt (du cote parie), am}."""
    p = min(max(c["p_mkt"] + prm["lambda"] * (c["p_sim"] - c["p_mkt"]), 0.01), 0.99)
    d = _dec(c["am"])
    return p, d, p * d - 1


def plan(cands, eq, night_exposure, game_exposure, prm=PRM, taken=()):
    """Paris a placer parmi les candidats (memes regles en production et en backtest).
    cands : [{key, gid, pid, stat, side, line, am, p_sim, p_mkt, ...}] ; taken : cles deja jouees.
    night_exposure : mises deja engagees sur la soiree ; game_exposure : {gid: mises engagees}.
    -> [{"legs": [cand], "stake", "odds", "p", "ev", "kind"}]"""
    rows = []
    for c in cands:
        if c["key"] in taken: continue
        p, d, ev = leg_estimate(c, prm)
        if d is None or ev < prm["min_ev"] or d > prm["max_odds"]: continue
        if c.get("hold") is not None and not prm["hold"][0] <= c["hold"] <= prm["hold"][1]: continue
        rows.append((ev, p, d, c))
    rows.sort(key=lambda r: -r[0])
    out, night, games, players = [], night_exposure, dict(game_exposure), set()
    for ev, p, d, c in rows:
        if (c["gid"], c["pid"]) in players: continue      # un pari par joueur (stats correlees)
        k = (p * d - 1) / (d - 1)
        stake = min(prm["kelly"] * k * eq, prm["max_bet"] * eq,
                    prm["max_game"] * eq - games.get(c["gid"], 0.0), prm["max_night"] * eq - night)
        stake = _round(stake)
        if stake < prm["min_stake"]: continue
        out.append({"legs": [c], "stake": stake, "odds": round(d, 3), "p": round(p, 4), "ev": round(ev, 4), "kind": "simple"})
        night += stake; games[c["gid"]] = games.get(c["gid"], 0.0) + stake; players.add((c["gid"], c["pid"]))
    if prm["combo"] and len({r[3]["gid"] for r in rows}) >= 2:
        best = None
        top = rows[:8]
        for i in range(len(top)):
            for j in range(i + 1, len(top)):
                a, b = top[i], top[j]
                if a[3]["gid"] == b[3]["gid"]: continue      # matchs differents : pas de correlation cachee
                p, d = a[1] * b[1], a[2] * b[2]
                ev = p * d - 1
                if ev >= prm["combo_ev"] and ev > max(a[0], b[0]) and (best is None or ev > best[0]):
                    best = (ev, p, d, a[3], b[3])
        if best:
            ev, p, d, a, b = best
            k = (p * d - 1) / (d - 1)
            stake = _round(min(prm["kelly"] * k * eq, prm["combo_max"] * eq, prm["max_night"] * eq - night))
            if stake >= prm["min_stake"]:
                out.append({"legs": [a, b], "stake": stake, "odds": round(d, 3), "p": round(p, 4), "ev": round(ev, 4),
                            "kind": "combine"})
    return out


def leg_result(leg, box):
    """win / loss / push / void (joueur absent) / None (pas encore joue)."""
    g = box.get(leg["gid"])
    if not g: return None
    pl = next((x for sd in ("home", "away") for x in g[sd].get("players") or [] if x["id"] == leg["pid"]), None)
    if not pl or pl.get("dnp") or not pl.get("MIN"): return "void"
    from nba_sim_run import STAT_COMPS
    v = sum(pl.get(c, 0) for c in STAT_COMPS[leg["stat"]])
    leg["actual"] = v
    if v == leg["line"]: return "push"
    return "win" if (v > leg["line"]) == (leg["side"] == "o") else "loss"


def settle_bet(b, results):
    """Regle un pari a partir des resultats de ses selections (combine : selection nulle / remboursee
    retiree et cote recalculee). Renvoie True s'il est regle."""
    if any(r is None for r in results) and "loss" not in results: return False
    now = datetime.now(timezone.utc).isoformat(timespec="minutes")
    for leg, r in zip(b["legs"], results): leg["result"] = r
    if "loss" in results:
        b["status"], b["pnl"] = "lost", -b["stake"]
    else:
        live = [leg for leg, r in zip(b["legs"], results) if r == "win"]
        if not live:
            b["status"], b["pnl"] = "void", 0.0
        else:
            odds = 1.0
            for leg in live: odds *= leg["dec"]
            b["status"], b["pnl"] = "won", round(b["stake"] * (odds - 1), 2)
    b["settled_at"] = now
    return True


def settle(state, box):
    n = 0
    for b in state["bets"]:
        if b["status"] != "open": continue
        n += settle_bet(b, [leg_result(leg, box) for leg in b["legs"]])
    return n


def candidates(games, now=None):
    """Selections de la soiree a partir des matchs simules (nba_sim_run.run_game) : les picks du
    simulateur, joueur quasi certain de jouer, cote du cote parie."""
    from nba_sim_run import SIM_BIAS, PICK_MIN_PLAY
    from nba_sim_bilan import night_of
    out = []
    for g in games:
        for p in g.get("props") or []:
            if not p.get("pick") or p.get("p_play", 0) < PICK_MIN_PLAY: continue
            o = p["pick"] == "o"
            ps = min(max(p["p_sim"] + SIM_BIAS, 0.0), 1.0)
            hold = _imp(p["over"]) + _imp(p["under"]) - 1 if p.get("over") is not None and p.get("under") is not None else None
            out.append({"key": f'{g["gid"]}|{p["id"]}|{p["stat"]}|{p["line"]}|{p["pick"]}', "gid": g["gid"],
                        "night": night_of(g["date"]), "date": g["date"], "pid": p["id"], "name": p["name"],
                        "team": g[p["side"]]["abbr"], "opp": g["away" if p["side"] == "home" else "home"]["abbr"],
                        "stat": p["stat"], "line": p["line"], "side": p["pick"], "am": p["over"] if o else p["under"],
                        "p_sim": ps if o else 1 - ps, "p_mkt": p["p_mkt"] if o else 1 - p["p_mkt"], "hold": hold})
    return out


def discipline(state, picks=None):
    """(mode, CLV moyenne, n) : « normal », « prudent » (mises / 2) ou « pause », selon la CLV des
    selections jouees et reglees (prix pris vs derniere cote sans marge avant le match)."""
    if picks is None:
        try:
            picks = json.loads(Path("data/nba_sim_picks.json").read_text(encoding="utf-8")).get("picks", {})
        except (OSError, ValueError):
            picks = {}
    clv = []
    for b in state["bets"]:
        if b["status"] == "open": continue
        for leg in b["legs"]:
            r = picks.get(leg["key"])
            if r and r.get("last_p_mkt") is not None:
                clv.append(r["last_p_mkt"] * leg["dec"] - 1)
    n, m = len(clv), (sum(clv) / len(clv) if clv else 0.0)
    if n >= PAUSE[0] and m < PAUSE[1]: return "pause", m, n
    if n >= PRUDENT[0] and m < PRUDENT[1]: return "prudent", m, n
    return "normal", m, n


def decide(state, games):
    """Place les nouveaux paris de la veille. Renvoie la liste des paris places."""
    now = datetime.now(timezone.utc).isoformat(timespec="minutes")
    mode, clv, n_clv = discipline(state)
    state["mode"] = {"mode": mode, "clv": round(clv, 4), "n": n_clv}
    if mode == "pause": return []
    cands = candidates(games)
    taken = {leg["key"] for b in state["bets"] for leg in b["legs"]}
    placed = []
    eq = equity(state)
    for night in sorted({c["night"] for c in cands}):
        cs = [c for c in cands if c["night"] == night]
        open_night = [b for b in state["bets"] if b.get("night") == night]
        nexp = sum(b["stake"] for b in open_night)
        gexp = {}
        for b in open_night:
            for leg in b["legs"]: gexp[leg["gid"]] = gexp.get(leg["gid"], 0.0) + b["stake"] / len(b["legs"])
        combo_done = any(b["kind"] == "combine" for b in open_night)
        prm = dict(PRM, combo=PRM["combo"] and not combo_done,
                   kelly=PRM["kelly"] / (2 if mode == "prudent" else 1))
        for bet in plan(cs, eq, nexp, gexp, prm, taken):
            if bet["stake"] > cash(state) - sum(x["stake"] for x in placed): continue
            for leg in bet["legs"]:
                leg["dec"] = round(_dec(leg["am"]), 3)
            bet.update(id=f'{night}-{len(state["bets"]) + len(placed) + 1}', night=night, placed_at=now, status="open")
            placed.append(bet)
            taken |= {leg["key"] for leg in bet["legs"]}
    state["bets"] += placed
    return placed
