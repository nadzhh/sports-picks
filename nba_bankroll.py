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
    # part de l'ecart simu - marche gardee dans la probabilite. 0,40 au backtest (28/10) ; 0,10 mesure le
    # 04/11/2026 sur 613 selections reelles reglees (cotes DraftKings) : 0,05 et 0,15 sur chaque moitie,
    # 0 pour les passes. A 0,40 la bankroll prenait des paris d'esperance negative (-5 a -12 %).
    "lambda":     0.10,
    "max_odds":   2.60,   # au-dela, l'« avantage » historique venait de cotes d'ouverture aberrantes
    "hold":       (0.035, 0.11),   # marge du bookmaker sur la ligne : hors plage = cote suspecte, on passe
    "kelly":      0.25,   # fraction de Kelly
    # Plafonds (04/11/2026, Wilson : « tu mets ce que tu veux, 10 % si grosse confiance ») : la mise suit la
    # confiance (fraction de Kelly selon la source de l'avantage, voir CONF), ces plafonds ne servent que de garde-fou.
    "max_bet":    0.10,   # mise max d'un pari, en part du capital
    "max_game":   0.12,   # exposition max sur un meme match
    "max_night":  0.30,   # exposition max sur une soiree / journee
    "min_ev":     0.05,   # esperance minimale d'un simple
    "min_stake":  0.5,    # EUR
    "combo":      True,
    "combo_ev":   0.08,   # esperance minimale d'un combine
    "combo_max":  0.01,   # mise max d'un combine, en part du capital
    # Un pari ne s'annule pas : tant qu'une info qui peut changer le role du joueur est en suspens dans
    # son equipe (coequipier de rotation incertain, rapport officiel pas encore rempli), on attend ; la
    # veille resimule des que le statut tombe. A moins de wait_until min du match, plus rien ne tombera :
    # la derniere simulation pondere l'incertitude et on decide. (Si le joueur parie ne joue pas, rembourse.)
    "wait_min":   18,     # minutes attendues d'un coequipier incertain qui justifient d'attendre
    "wait_until": 40,     # minutes avant le coup d'envoi
}
UNCERTAIN = ("questionable", "doubtful", "day-to-day")
# Discipline : la bankroll mesure sa propre CLV (cote prise vs derniere cote avant le match, suivi
# data/nba_sim_picks.json). Sans avantage mesure, un bon parieur reduit puis arrete.
PRUDENT = (30, 0.0)    # apres 30 selections reglees, CLV moyenne < 0 -> mises divisees par 2
PAUSE = (60, -0.02)    # (plus utilise depuis le 29/10/2026 : la bankroll ne se met plus en pause)
# Jugee sur TOUTES les selections du simulateur (data/nba_sim_picks.json), pas sur les seuls paris de la
# bankroll : 29/10/2026, 176 selections, CLV -4,6 % -> les cotes obtenues valent la cloture, pas d'avantage
# demontre sur les paris ordinaires. Seule piste gardee, a petite mise et mesuree a part : les paris nes
# d'une information fraiche (statut d'un joueur du match qui change, la veille resimule avant que la
# cote bouge) — mode « info ».
INFO_KELLY, INFO_NIGHT = 0.5, 0.03   # en mode info : 1/2 de la fraction de Kelly, 3 % du capital par soiree
MAX_GAP = 0.20         # ecart simu / marche >= 20 pts : presque toujours une info manquante (5-10 en reel)
CALIB_NOTE = ("Réglages testés le 28/10/2026 sur 3 échantillons de vraies cotes (oct-nov 2025, avr-juin 2026, "
              "début 2026-27). Recalés le 04/11/2026 sur les 613 premières sélections réelles réglées : le simulateur "
              "annonçait 61 % de réussite pour 51,5 % obtenus (49,7 % selon le marché). On ne garde donc que 10 % de "
              "son écart avec le bookmaker (40 % avant) : la bankroll ne prend plus que les paris dont l’espérance "
              "reste positive après la marge. Les grosses mises vont aux value bets (cote française au-dessus de la "
              "cote juste Pinnacle) que ma simulation confirme.")


def _dec(am):
    return None if am is None else (1 + am / 100 if am > 0 else 1 + 100 / -am)


def _imp(am):
    return 100 / (am + 100) if am > 0 else -am / (-am + 100)


def _round(x):
    # aux 10 centimes (avant le 04/11/2026 : tranche de 0,50 EUR inferieure, qui ramenait presque toutes les
    # mises a 0,50 EUR quelle que soit la confiance)
    return math.floor(round(x * 10, 6)) / 10


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
        if d is None or ev < prm["min_ev"] or d > prm["max_odds"] or abs(c["p_sim"] - c["p_mkt"]) >= MAX_GAP: continue
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
        out.append({"legs": [c], "stake": stake, "odds": round(d, 3), "p": round(p, 4), "ev": round(ev, 4), "kind": "simple",
                    "conf": "faible"})   # avantage du simulateur seul : non demontre en reel
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
    ev = None
    for b in state["bets"]:
        if b["status"] != "open": continue
        if b["kind"] == "conv":    # pari conviction (picks du soir) : meme reglement que le canal Picks
            import picks_du_jour as PJ
            r = PJ.settle_pick(b["legs"][0]["pj"], box)
            n += settle_bet(b, [{"push": "void"}.get(r, r)]) if r else 0
            continue
        if b["kind"] == "value":   # value bet : resultat releve par ev_scan (scores ESPN)
            if ev is None:
                import ev_scan
                ev = ev_scan.load()["picks"]
            r = (ev.get(b["legs"][0]["ev_key"]) or {}).get("result")
            n += settle_bet(b, [{"push": "void"}.get(r, r)]) if r else 0
            continue
        n += settle_bet(b, [leg_result(leg, box) for leg in b["legs"]])
    return n


# Paris « conviction » (Wilson, 06/11/2026 : « la bankroll doit tourner ; mise selon ta confiance ; mieux vaut 1-2
# paris gagnants que 4-5 avec 3 perdants ») : chaque soir, au plus 2 paris parmi les picks du soir, places a 20 h :
#   1. d'abord ce qui a une esperance positive (value, boost rentable) : 1/4 de Kelly, au plus 10 % du capital ;
#   2. sinon les plus surs : gagne >= 60 % (probabilite recalee), cote >= 1,40, cout <= 6 %, simulation pas contre.
#      Mise selon la confiance : 1 % du capital a 60 %, jusqu'a 5 % a 80 % et plus. Ils coutent un peu (~4-6 % par
#      pari) : le bot d'amelioration baisse leur mise si leur CLV reelle reste negative (model_params « conv »).
CONV = {"per_night": 2, "p_min": 0.60, "min_odds": 1.40, "max_cost": 0.06, "lo": 0.01, "hi": 0.05}


def decide_conviction(state, picks, now=None):
    """Place les paris conviction de la soiree parmi les picks du soir (picks_du_jour.all_picks)."""
    from nba_sim_bilan import night_of
    now = now or datetime.now(timezone.utc)
    try:
        mult = (json.loads(Path("data/model_params.json").read_text(encoding="utf-8")).get("stakes") or {}).get("conv", 1.0)
    except (OSError, ValueError):
        mult = 1.0
    taken = {leg.get("key") for b in state["bets"] for leg in b["legs"]}
    eq = equity(state)
    nights = {}
    for b in state["bets"]:
        if b["kind"] == "conv": nights[b["night"]] = nights.get(b["night"], 0) + 1
    try:   # reservoir elargi : toutes les lignes et props du soir (pas seulement les 3-5 picks postes)
        import picks_du_jour as PJ
        have = {(x["match_id"], x["label"]) for x in picks}
        picks = list(picks) + [dict(x, kind=x["kind"]) for x in PJ.value_candidates(now, 24, min_ev=-CONV["max_cost"])
                               + PJ.prop_candidates(now, 24, min_ev=-CONV["max_cost"]) if (x["match_id"], x["label"]) not in have]
    except Exception as e:
        print(f"  [!] conviction : reservoir elargi indisponible ({type(e).__name__}: {e})")
    pos = sorted((x for x in picks if x["ev"] >= 0), key=lambda x: -x["ev"])
    safe = sorted((x for x in picks if x["ev"] < 0 and x["p"] >= CONV["p_min"] and x["price"] >= CONV["min_odds"]
                   and x["ev"] >= -CONV["max_cost"] and (x.get("p_sim") is None or x["p_sim"] >= x["p"] - 0.02)
                   and __import__("picks_du_jour")._hist_ok(x)),   # historique du joueur pas contre (06/11/2026)
                  key=lambda x: (-x["p"], -x["ev"]))
    placed, matches = [], set()
    for x in pos + safe:
        night = night_of(x["ko"])
        key = f'pj|{x["match_id"]}|{x["label"]}'
        if key in taken or x["match_id"] in matches or nights.get(night, 0) >= CONV["per_night"]: continue
        d, p = x["price"], x["p"]
        if x["ev"] >= 0:
            frac, conf = min(0.25 * (p * d - 1) / (d - 1), VALUE_MAX), "value"
        else:
            frac = CONV["lo"] + (CONV["hi"] - CONV["lo"]) * min(max((p - CONV["p_min"]) / 0.20, 0.0), 1.0)
            conf = "haute" if p >= 0.72 else "moyenne" if p >= 0.66 else "correcte"
        stake = _round(frac * mult * eq)
        if stake < PRM["min_stake"] or stake > cash(state) - sum(b["stake"] for b in placed): continue
        pj = {k: x.get(k) for k in ("kind", "src", "sport", "match_id", "match", "label", "price", "ko", "res", "book")}
        leg = {"key": key, "ev_key": key, "pj": pj, "name": x["match"], "team": x.get("league", ""), "opp": x.get("book", ""),
               "stat": x["label"], "side": "", "line": 0, "dec": d, "am": None, "p_sim": x.get("p_sim") or p,
               "p_mkt": x.get("p_fair") or p, "date": x["ko"], "gid": "", "pid": "", "sport": x.get("sport"), "tag": "conviction"}
        placed.append({"legs": [leg], "stake": stake, "odds": d, "p": p, "ev": x["ev"], "kind": "conv", "mode": "conviction",
                       "conf": conf, "id": f'{night}-c{len(state["bets"]) + len(placed) + 1}', "night": night,
                       "placed_at": now.isoformat(timespec="minutes"), "status": "open", "news": []})
        matches.add(x["match_id"]); nights[night] = nights.get(night, 0) + 1
    state["bets"] += placed
    return placed


VALUE_MAX = 0.10     # mise max d'un value bet (part du capital)
# Confiance d'un value bet = accord entre la cote juste Pinnacle (avantage valide) et ma propre simulation
# (foot_sim / nba_sim) : les deux d'accord -> forte (3/4 de Kelly) ; simu a moins de 5 pts -> moyenne (1/2) ;
# simu nettement contre -> faible (1/4) ; pas de simulation du match -> moyenne.
CONF = {"forte": 0.75, "moyenne": 0.5, "faible": 0.25}
# Audit du 04/11/2026 (Monte-Carlo, 90 j, 2 paris/jour) : avec un avantage annonce de +5 % mais reel nul, 3/4 de Kelly
# donne 88 % de risque de perdre 30 % depuis un plus haut (5e centile 39 EUR) ; 0,35 / 0,25 / 0,15 et 5 % max : 25 %.
# Tant que les value bets n'ont pas PROUVE leur avantage en reel (value_proven), on mise donc avec ces fractions.
CONF_UNPROVEN = {"forte": 0.35, "moyenne": 0.25, "faible": 0.15}
VALUE_MAX_UNPROVEN = 0.05
PROOF_N = 100          # value bets regles avec une cote juste de cloture Pinnacle


def value_proven(ev_picks=None):
    """(prouve ?, n, CLV moyenne, borne basse IC95) : CLV = cote prise x proba juste Pinnacle au dernier releve - 1."""
    if ev_picks is None:
        try:
            import ev_scan
            ev_picks = ev_scan.load()["picks"]
        except Exception:
            ev_picks = {}
    c = [p["price"] * p["last_fair"] - 1 for p in ev_picks.values() if p.get("result") and p.get("last_fair")]
    n = len(c)
    if n < 2: return False, n, None, None
    m = sum(c) / n
    sd = (sum((x - m) ** 2 for x in c) / (n - 1)) ** 0.5
    lo = m - 1.96 * sd / n ** 0.5
    return n >= PROOF_N and lo > 0, n, m, lo



def _ndist(x):
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def sim_prob(p):
    """Probabilite de ma simulation pour un value bet ev_scan (None si le match n'est pas simule)."""
    import difflib
    from datetime import timedelta
    try:
        ko = datetime.fromisoformat(p["commence"].replace("Z", "+00:00"))
    except (KeyError, ValueError):
        return None
    same = lambda a, b: difflib.SequenceMatcher(None, a.lower(), b.lower()).ratio()
    path = "data/foot_sim.json" if p.get("sport") == "foot" else "data/nba_sim.json"
    try:
        games = json.loads(Path(path).read_text(encoding="utf-8")).get("games") or []
    except (OSError, ValueError):
        return None
    for g in games:
        t = datetime.fromisoformat((g.get("ko") or g.get("date")).replace("Z", "+00:00"))
        if abs(t - ko) > timedelta(hours=3): continue
        if same(g["home"]["name"], p["home"]) + same(g["away"]["name"], p["away"]) < 1.3: continue
        home = p["name"] == p["home"]
        if p.get("sport") == "foot":
            q = g["p"]
            if p["market"] == "h2h":
                return q["X"] if p["name"] == "Draw" else q["1"] if home else q["2"]
            if p["market"] == "totals":
                o = q["over"].get(str(p["point"]))
                return None if o is None else (o if p["name"] == "Over" else 1 - o)
            return None
        tm = g.get("team") or {}
        if p["market"] == "h2h" and tm.get("p_home_win") is not None:
            return tm["p_home_win"] if home else 1 - tm["p_home_win"]
        if p["market"] == "totals" and tm.get("total_sd"):
            o = 1 - _ndist((p["point"] - tm["total_mean"]) / tm["total_sd"])
            return o if p["name"] == "Over" else 1 - o
        if p["market"] == "spreads" and tm.get("margin_sd"):
            m = tm["margin_mean"] if home else -tm["margin_mean"]      # marge de l'equipe pariee
            return 1 - _ndist((-p["point"] - m) / tm["margin_sd"])
        return None
    return None


def value_conf(p):
    """(niveau de confiance, probabilite de ma simulation ou None)."""
    ps = sim_prob(p)
    if ps is None: return "moyenne", None
    return ("forte" if ps >= p["fair"] else "moyenne" if ps >= p["fair"] - 0.05 else "faible"), round(ps, 4)


def decide_value(state, new):
    """Value bets (ev_scan : cote francaise au-dessus de la cote juste Pinnacle) : 1/4 de Kelly sur la
    probabilite juste, au plus VALUE_MAX du capital. Seul avantage valide (CLV positive sur deux saisons)."""
    from nba_sim_bilan import night_of
    now = datetime.now(timezone.utc).isoformat(timespec="minutes")
    taken = {leg.get("ev_key") for b in state["bets"] for leg in b["legs"]}
    placed, eq = [], equity(state)
    proven = value_proven()[0]
    fracs, vmax = (CONF, VALUE_MAX) if proven else (CONF_UNPROVEN, VALUE_MAX_UNPROVEN)
    for p in new:
        if p["key"] in taken: continue
        d, pf = p["price"], p["fair"]
        conf, ps = value_conf(p)
        k = (pf * d - 1) / (d - 1)
        day = sum(b["stake"] for b in state["bets"] + placed if b["status"] == "open" and b.get("night") == night_of(p["commence"]))
        stake = _round(min(fracs[conf] * k * eq, vmax * eq, PRM["max_night"] * eq - day))
        if stake < PRM["min_stake"] or stake > cash(state) - sum(x["stake"] for x in placed): continue
        leg = {"key": "ev|" + p["key"], "ev_key": p["key"], "name": f'{p["home"]} - {p["away"]}', "team": p["league"],
               "opp": p["book_name"], "stat": p["label"], "side": "", "line": 0, "dec": d, "am": None,
               "p_sim": ps if ps is not None else pf, "p_mkt": pf, "date": p["commence"], "gid": "", "pid": "",
               "sport": p.get("sport")}
        placed.append({"legs": [leg], "stake": stake, "odds": d, "p": pf, "ev": p["ev"], "kind": "value", "mode": "value", "conf": conf,
                       "id": f'{night_of(p["commence"])}-v{len(state["bets"]) + len(placed) + 1}', "night": night_of(p["commence"]),
                       "placed_at": now, "status": "open", "news": []})
    state["bets"] += placed
    return placed


def candidates(games, now=None):
    """Selections de la soiree a partir des matchs simules (nba_sim_run.run_game) : les picks du
    simulateur, joueur quasi certain de jouer, cote du cote parie."""
    from nba_sim_run import SIM_BIAS, PICK_MIN_PLAY
    from nba_sim_bilan import night_of
    out = []
    for g in games:
        news = [e["txt"] for e in g.get("events") or [] if e.get("at") == g.get("sim_at") and "→" in e.get("txt", "")
                and "Vegas" not in e["txt"]]   # changements de statut seulement (pas les mouvements de ligne)
        # Infos en suspens par equipe : rapport officiel pas encore rempli, coequipiers incertains
        pend = {}
        for side in ("home", "away"):
            why = []
            if ((g.get("injuries") or {}).get(side) or {}).get("src") not in (None, "officiel"):
                why.append("rapport officiel des blessures pas encore rempli")
            pend[side] = (why, [(pl["id"], pl["name"]) for pl in g.get("players") or [] if pl["side"] == side
                                and pl.get("status") in UNCERTAIN and pl.get("MIN", 0) >= PRM["wait_min"]])
        for p in g.get("props") or []:
            if not p.get("pick") or p.get("p_play", 0) < PICK_MIN_PLAY: continue
            why, unc = pend[p["side"]]
            names = [n for i, n in unc if i != p["id"]]
            pending = why + ([f"statut de {', '.join(names)} (incertain{'s' if len(names) > 1 else ''})"] if names else [])
            o = p["pick"] == "o"
            ps = min(max(p["p_sim"] + SIM_BIAS, 0.0), 1.0)
            hold = _imp(p["over"]) + _imp(p["under"]) - 1 if p.get("over") is not None and p.get("under") is not None else None
            out.append({"key": f'{g["gid"]}|{p["id"]}|{p["stat"]}|{p["line"]}|{p["pick"]}', "gid": g["gid"],
                        "night": night_of(g["date"]), "date": g["date"], "pid": p["id"], "name": p["name"],
                        "team": g[p["side"]]["abbr"], "opp": g["away" if p["side"] == "home" else "home"]["abbr"],
                        "stat": p["stat"], "line": p["line"], "side": p["pick"], "am": p["over"] if o else p["under"],
                        "p_sim": ps if o else 1 - ps, "p_mkt": p["p_mkt"] if o else 1 - p["p_mkt"], "hold": hold,
                        "pending": pending, "news": news})
    return out


# Paris « safe » (demande de Wilson, 29/10/2026) : le cote que le bookmaker ET la simulation donnent
# nettement favori, cote 1,50 minimum ; ou un combine de deux jambes tres probables (matchs differents)
# qui atteint 1,50. Petite mise fixe, suivis a part. Ils suivent la discipline (aucun en pause).
SAFE = {"min_odds": 1.50, "p_min": 0.58, "leg_p": 0.75, "stake": 0.01, "per_night": 2}
SAFE_IN_BANK = False


def safe_candidates(games):
    """Toutes les lignes principales (pas seulement les picks) : pour chaque cote d'une prop, probabilites
    simulee et du marche sans marge du cote parie, joueur quasi certain de jouer."""
    from nba_sim_run import SIM_BIAS, PICK_MIN_PLAY
    from nba_sim_bilan import night_of
    out = []
    for g in games:
        for p in g.get("props") or []:
            if p.get("p_play", 0) < PICK_MIN_PLAY or p.get("over") is None or p.get("under") is None: continue
            ps = min(max(p["p_sim"] + SIM_BIAS, 0.0), 1.0)
            for side in ("o", "u"):
                am = p["over"] if side == "o" else p["under"]
                out.append({"key": f'{g["gid"]}|{p["id"]}|{p["stat"]}|{p["line"]}|{side}', "gid": g["gid"],
                            "night": night_of(g["date"]), "date": g["date"], "pid": p["id"], "name": p["name"],
                            "team": g[p["side"]]["abbr"], "opp": g["away" if p["side"] == "home" else "home"]["abbr"],
                            "stat": p["stat"], "line": p["line"], "side": side, "am": am,
                            "p_sim": ps if side == "o" else 1 - ps, "p_mkt": p["p_mkt"] if side == "o" else 1 - p["p_mkt"],
                            "pending": [], "news": []})
    return out


def plan_safe(cands, eq, taken, n_done, prm_mult=1.0):
    """Au plus SAFE['per_night'] - n_done paris safe : simples d'abord (le plus probable), sinon un combine."""
    left = SAFE["per_night"] - n_done
    if left <= 0: return []
    stake = max(PRM["min_stake"], _round(SAFE["stake"] * eq * prm_mult))   # au moins la mise minimale
    ok = [c for c in cands if c["key"] not in taken and c["am"] is not None]
    singles = sorted((c for c in ok if _dec(c["am"]) >= SAFE["min_odds"] and min(c["p_sim"], c["p_mkt"]) >= SAFE["p_min"]),
                     key=lambda c: -min(c["p_sim"], c["p_mkt"]))
    out, players = [], set()
    for c in singles:
        if len(out) >= left: break
        if (c["gid"], c["pid"]) in players: continue
        p = min(c["p_sim"], c["p_mkt"]); d = _dec(c["am"])
        out.append({"legs": [c], "stake": stake, "odds": round(d, 3), "p": round(p, 4), "ev": round(p * d - 1, 4), "kind": "safe"})
        players.add((c["gid"], c["pid"]))
    if len(out) < left:   # combine safe : deux jambes tres probables de matchs differents
        legs = sorted((c for c in ok if min(c["p_sim"], c["p_mkt"]) >= SAFE["leg_p"] and (c["gid"], c["pid"]) not in players),
                      key=lambda c: -min(c["p_sim"], c["p_mkt"]))[:10]
        best = None
        for i in range(len(legs)):
            for j in range(i + 1, len(legs)):
                a, b = legs[i], legs[j]
                if a["gid"] == b["gid"]: continue
                d = _dec(a["am"]) * _dec(b["am"])
                if d < SAFE["min_odds"]: continue
                pr = min(a["p_sim"], a["p_mkt"]) * min(b["p_sim"], b["p_mkt"])
                if best is None or pr > best[0]: best = (pr, d, a, b)
        if best:
            pr, d, a, b = best
            out.append({"legs": [a, b], "stake": stake, "odds": round(d, 3), "p": round(pr, 4), "ev": round(pr * d - 1, 4), "kind": "safe"})
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
    for r in picks.values():
        if r.get("result") in ("win", "loss", "push") and r.get("last_p_mkt") is not None:
            clv.append(r["last_p_mkt"] * _dec(r["first_price"]) - 1)
    n, m = len(clv), (sum(clv) / len(clv) if clv else 0.0)
    # Plus de pause (choix de Wilson, 29/10/2026 : la bankroll doit tourner tous les jours) : une CLV
    # negative ne fait que diviser les mises par 2 (« prudent »)
    if n >= PRUDENT[0] and m < PRUDENT[1]: return "prudent", m, n
    return "normal", m, n


def decide(state, games):
    """Place les nouveaux paris de la veille. Renvoie la liste des paris places."""
    now = datetime.now(timezone.utc).isoformat(timespec="minutes")
    mode, clv, n_clv = discipline(state)
    state["mode"] = {"mode": mode, "clv": round(clv, 4), "n": n_clv}
    cands = candidates(games)
    if mode == "pause":   # seuls les paris nes d'une information fraiche, a petite mise
        cands = [c for c in cands if c.get("news")]
    taken = {leg["key"] for b in state["bets"] for leg in b["legs"]}
    # Attente d'information : on retire (et on affiche) les selections dont le contexte n'est pas fixe
    t_now = datetime.now(timezone.utc)
    waiting = {k: w for k, w in (state.get("waiting") or {}).items()
               if datetime.fromisoformat(w["date"].replace("Z", "+00:00")) > t_now and k not in taken}
    ready = []
    for c in cands:
        mins = (datetime.fromisoformat(c["date"].replace("Z", "+00:00")) - t_now).total_seconds() / 60
        if c["key"] in taken: continue
        if c["pending"] and mins > PRM["wait_until"]:
            p_, d_, ev_ = leg_estimate(c)
            if ev_ >= PRM["min_ev"] and d_ <= PRM["max_odds"]:
                waiting[c["key"]] = {"name": c["name"], "team": c["team"], "opp": c["opp"], "stat": c["stat"],
                                     "side": c["side"], "line": c["line"], "date": c["date"], "dec": round(d_, 2),
                                     "ev": round(ev_, 4), "why": c["pending"], "since": waiting.get(c["key"], {}).get("since", now)}
            continue
        waiting.pop(c["key"], None)
        ready.append(c)
    state["waiting"] = waiting
    cands = ready
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
        prm = dict(PRM, combo=PRM["combo"] and not combo_done and mode != "pause",
                   kelly=PRM["kelly"] * (INFO_KELLY if mode == "pause" else 0.5 if mode == "prudent" else 1))
        if mode == "pause":
            prm["max_night"] = INFO_NIGHT
        for bet in plan(cs, eq, nexp, gexp, prm, taken):
            if bet["stake"] > cash(state) - sum(x["stake"] for x in placed): continue
            for leg in bet["legs"]:
                leg["dec"] = round(_dec(leg["am"]), 3)
            bet.update(id=f'{night}-{len(state["bets"]) + len(placed) + 1}', night=night, placed_at=now, status="open",
                       mode="info" if mode == "pause" else mode,
                       news=sorted({n for leg in bet["legs"] for n in leg.get("news") or []}))
            placed.append(bet)
            taken |= {leg["key"] for leg in bet["legs"]}
            for leg in bet["legs"]: state["waiting"].pop(leg["key"], None)
    # Paris safe : plus dans la bankroll depuis le 04/11/2026 (esperance -6 % selon ma propre estimation) ;
    # ils partent a Wilson dans le recapitulatif Telegram de 20 h (notify.build_hv_digest).
    if SAFE_IN_BANK and mode != "pause":
        taken = {leg["key"] for b in state["bets"] + placed for leg in b["legs"]}
        sc = safe_candidates(games)
        for night in sorted({c["night"] for c in sc}):
            n_done = sum(1 for b in state["bets"] + placed if b.get("night") == night and b.get("kind") == "safe")
            for bet in plan_safe([c for c in sc if c["night"] == night], eq, taken, n_done, 0.5 if mode == "prudent" else 1.0):
                if bet["stake"] > cash(state) - sum(x["stake"] for x in placed): continue
                for leg in bet["legs"]:
                    leg["dec"] = round(_dec(leg["am"]), 3)
                bet.update(id=f'{night}-{len(state["bets"]) + len(placed) + 1}', night=night, placed_at=now, status="open",
                           mode=mode, news=[])
                placed.append(bet)
                taken |= {leg["key"] for leg in bet["legs"]}
    state["bets"] += placed
    return placed


# ── Telegram : les paris a prendre et leur reglement ─────────────────────────

UNIT_FR = {"AST": "passes", "3PM": "paniers à 3 pts", "STL": "interceptions", "BLK": "contres"}
SITE = "https://nadzhh.github.io/sports-picks/"


def _fr(x, d=2):
    return f"{x:.{d}f}".replace(".", ",")


def _paris_time(iso):
    try:
        from zoneinfo import ZoneInfo
        return datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(ZoneInfo("Europe/Paris")).strftime("%d/%m %H:%M")
    except Exception:
        return ""


def _leg_txt(l):
    if l.get("ev_key"):   # value bet
        return f'<b>{l["name"]}</b> ({l["team"]}, {_paris_time(l["date"])}) : {l["stat"]} chez {l["opp"]}'
    return (f'<b>{l["name"]}</b> ({l["team"]} vs {l["opp"]}, {_paris_time(l["date"])}) : '
            f'{"Over" if l["side"] == "o" else "Under"} {l["line"]:g} {UNIT_FR.get(l["stat"], l["stat"])}')


def telegram_placed(placed, state):
    """Message des paris a prendre. Cote mini : en dessous, l'esperance tombe sous le seuil (min_ev)."""
    eq = equity(state)
    out = ["🤖 <b>Bankroll IA : paris à prendre</b>"]
    for b in placed:
        mini = SAFE["min_odds"] if b["kind"] == "safe" else (1 + PRM["min_ev"]) / b["p"]
        if b["kind"] == "value":   # cote francaise au-dessus de la cote juste Pinnacle
            l = b["legs"][0]
            out.append(f'\n💎 <b>Value bet</b> · confiance {b.get("conf", "moyenne")} · {_leg_txt(l)}')
            out.append(f'Cote {l["opp"]} <b>{_fr(b["odds"])}</b> · cote juste Pinnacle <b>{_fr(1 / b["p"])}</b> · '
                       f'espérance +{round(100 * b["ev"], 1)} %')
            out.append(f'Mise <b>{_fr(b["stake"])} €</b> ({_fr(100 * b["stake"] / eq, 1)} % de la bankroll)')
            continue
        if b["kind"] == "conv":    # pari conviction (06/11/2026) : seuil = cote juste, pas le seuil +5 %
            l = b["legs"][0]
            ico = "⚽" if l.get("sport") == "foot" else "🏀"
            out.append(f'\n🎯 <b>Conviction</b> · confiance {b.get("conf", "correcte")} · {ico} {l["name"]} : <b>{l["stat"]}</b>')
            out.append(f'Cote {l.get("opp") or ""} <b>{_fr(b["odds"])}</b> · gagné {round(100 * b["p"])} % (simu {round(100 * l["p_sim"])} %) · '
                       f'cote juste <b>{_fr(1 / b["p"])}</b> : rentable si ta cote ≥ {_fr(1 / b["p"])}'
                       + (f', sinon coût ≈ {round(100 * -b["ev"])} %' if b["ev"] < 0 else f', espérance +{round(100 * b["ev"])} %'))
            out.append(f'Mise <b>{_fr(b["stake"])} €</b> ({_fr(100 * b["stake"] / eq, 1)} % de la bankroll)')
            continue
        if b["kind"] == "safe":
            out.append(f'\n🛡️ <b>Pari safe</b> (probabilité {round(100 * b["p"])} %)\n' + "\n".join("• " + _leg_txt(l) for l in b["legs"]))
        elif b["kind"] == "combine":
            out.append("\n🔗 <b>Combiné</b>\n" + "\n".join("• " + _leg_txt(l) for l in b["legs"]))
        else:
            out.append("\n🏀 " + _leg_txt(b["legs"][0]))
        out.append(f'Cote DraftKings <b>{_fr(b["odds"])}</b> · à prendre si ta cote ≥ <b>{_fr(mini)}</b>')
        out.append(f'Mise <b>{_fr(b["stake"])} €</b> ({_fr(100 * b["stake"] / eq, 1)} % de la bankroll)')
        why = f'proba retenue {round(100 * b["p"])} %, espérance {round(100 * b["ev"]):+d} %'
        if len(b["legs"]) == 1:
            l = b["legs"][0]
            why = f'simu {round(100 * l["p_sim"])} % vs bookmaker {round(100 * l["p_mkt"])} % → ' + why
        out.append(f"<i>{why}</i>")
    out.append(f'\nCapital {_fr(eq)} € · <a href="{SITE}">détails sur le site</a>')
    return "\n".join(out)


def telegram_settled(bets, state):
    lab = {"won": "✅", "lost": "❌", "void": "↩️"}
    out = ["🤖 <b>Bankroll IA : paris réglés</b>"]
    for b in bets:
        legs = " + ".join(f'{l["name"]} : {l["stat"]}' if l.get("ev_key") else f'{l["name"]} {"Over" if l["side"] == "o" else "Under"} {l["line"]:g} '
                          f'{UNIT_FR.get(l["stat"], l["stat"])}' + (f' ({l["actual"]:g})' if l.get("actual") is not None else "")
                          for l in b["legs"])
        out.append(f'{lab.get(b["status"], "")} {legs} : {"+" if b["pnl"] > 0 else ""}{_fr(b["pnl"])} €')
    eq = equity(state)
    out.append(f'\nCapital <b>{_fr(eq)} €</b> ({"+" if eq >= state["start"] else ""}'
               f'{_fr(100 * (eq / state["start"] - 1), 1)} % depuis le départ)')
    return "\n".join(out)
