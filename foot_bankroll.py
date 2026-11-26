"""
foot_bankroll.py — Bankroll IA foot : « défi x2 » (Wilson, 26/11/2026 : « une deuxième bankroll IA spécial foot,
100 € de départ, tu te gères comme tu veux, tu paries ce que tu veux, combien tu veux ; le but est de faire x2 en
10 jours »).

Chiffré avant de construire (Monte Carlo, 200 000 défis, espérance -4 % par pari faute d'avantage démontré, ~20 paris) :
règles prudentes 0 % de chances de x2 ; ce mode défi 15-20 % de x2 et 25-30 % de finir sous 50 € ; tout miser sur une
cote de 2,0 : 48 % (pile ou face). Wilson a choisi ce mode défi, avec des défis de 10 jours enchaînés qui repartent du
solde. Ce mode ne vaut que pour cette bankroll : la Bankroll IA NBA garde ses règles prudentes.

Règles du défi :
  - chaque jour de match (jour de Paris du coup d'envoi) : au plus 2 paris simples (cote 1,80 à 3,00) et 1 combiné
    de 2-3 matchs différents (cote totale 3 à 5, coût <= 8 %), un seul pari par match ;
  - mise selon la confiance : forte 15 % (espérance >= 0 et simulation d'accord), moyenne 10 % (coût <= 3 %,
    simulation pas contre), correcte 8 % (coût <= 6 %) ; combiné 8 % ; au plus 35 % du capital engagé par jour ;
  - freins : sous 50 €, mises divisées par 2 ; objectif atteint (x2) -> défi gagné, plus aucun pari jusqu'à la fin
    des 10 jours ; à la fin, nouveau défi x2 à partir du solde (recharge à 100 € si la bankroll est à sec) ;
  - cotes DraftKings publiées par ESPN (marge retirée pour la probabilité juste, simulation foot sur les +/- 2,5 buts),
    comme les picks du jour : bankroll fictive, aucun argent réel ; la cote française estimée est ~15 % plus basse.

    st = load(); settle(st); placed = decide(st); save(st)
"""
import json
import math
from datetime import datetime, timedelta, timezone
from itertools import combinations
from pathlib import Path

PATH = Path("data/foot_bankroll.json")
START = 100.0
DAYS = 10
RULES = {
    "single_odds": (1.80, 3.00), "per_day_single": 2, "per_day_combo": 1,
    "stake": {"forte": 0.15, "moyenne": 0.10, "correcte": 0.08}, "combo_stake": 0.08,
    "cost": {"moyenne": -0.03, "correcte": -0.06},
    "leg_odds": (1.25, 2.20), "leg_p": 0.55, "leg_ev": -0.05, "combo_odds": (3.0, 5.0), "combo_cost": -0.08,
    "day_max": 0.35, "brake_below": 50.0, "brake": 0.5, "min_stake": 0.5,
    "lead_min": 25, "horizon_h": 16, "first_hour": 9,
}
SITE = "https://nadzhh.github.io/sports-picks/"

try:
    from zoneinfo import ZoneInfo
    TZ = ZoneInfo("Europe/Paris")
except Exception:
    TZ = timezone(timedelta(hours=1))


def _t(iso):
    return datetime.fromisoformat(iso.replace("Z", "+00:00"))


def day_of(iso):
    return _t(iso).astimezone(TZ).strftime("%Y-%m-%d")


def _round(x):
    return math.floor(round(x * 10, 6)) / 10


def load(path=PATH):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"start": START, "created": datetime.now(timezone.utc).isoformat(timespec="minutes"),
                "bets": [], "challenges": [], "topups": []}


def save(state, path=PATH):
    state["updated"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    tmp = Path(str(path) + ".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=0), encoding="utf-8")
    tmp.replace(path)


def equity(state):
    return (state["start"] + sum(x["amount"] for x in state.get("topups") or [])
            + sum(b.get("pnl", 0.0) for b in state["bets"] if b["status"] != "open"))


def cash(state):
    return equity(state) - sum(b["stake"] for b in state["bets"] if b["status"] == "open")


def challenge(state, now=None):
    """Défi en cours (créé / clôturé / déclaré gagné au passage). Renvoie le défi actif."""
    now = now or datetime.now(timezone.utc)
    chs = state.setdefault("challenges", [])
    ch = chs[-1] if chs else None
    eq = equity(state)
    if ch and now >= _t(ch["end_at"]) and not any(b["status"] == "open" for b in state["bets"]):
        ch.update(status="gagné" if ch.get("won_at") else "terminé", end_eq=round(eq, 2))
        ch = None
    if ch is None or ch.get("status") != "en cours":
        if eq < 1.0:   # bankroll a sec : recharge a 100 EUR, notee a part (le defi suivant repart de la)
            state.setdefault("topups", []).append({"at": now.isoformat(timespec="minutes"), "amount": round(START - eq, 2)})
            eq = equity(state)
        ch = {"n": len(chs) + 1, "start_at": now.isoformat(timespec="minutes"),
              "end_at": (now + timedelta(days=DAYS)).isoformat(timespec="minutes"),
              "start_eq": round(eq, 2), "target": round(2 * eq, 2), "status": "en cours", "best": round(eq, 2)}
        chs.append(ch)
    ch["best"] = round(max(ch.get("best", eq), eq), 2)
    if not ch.get("won_at") and eq >= ch["target"]:
        ch["won_at"] = now.isoformat(timespec="minutes")
    return ch


def _conf(x):
    """(niveau, part du capital) ou None : la confiance d'un pari simple."""
    ps = x.get("p_sim")
    if x["ev"] >= 0 and (ps is None or ps >= x["p"]):
        return "forte", RULES["stake"]["forte"]
    if x["ev"] >= RULES["cost"]["moyenne"] and (ps is None or ps >= x["p"] - 0.02):
        return "moyenne", RULES["stake"]["moyenne"]
    if x["ev"] >= RULES["cost"]["correcte"] and (ps is None or ps >= x["p"] - 0.05):
        return "correcte", RULES["stake"]["correcte"]
    return None


def candidates(now):
    import picks_du_jour as PJ
    out = []
    for x in PJ.value_candidates(now, RULES["horizon_h"], min_ev=-0.08):
        if x.get("sport") != "foot": continue
        if not timedelta(minutes=RULES["lead_min"]) <= _t(x["ko"]) - now <= timedelta(hours=RULES["horizon_h"]): continue
        out.append(x)
    return out


def _leg(x, tag):
    import picks_du_jour as PJ
    pj = {k: x.get(k) for k in ("kind", "src", "sport", "match_id", "match", "label", "price", "ko", "res", "book", "fair_src")}
    key = f'fb|{x["match_id"]}|{x["label"]}'
    return {"key": key, "ev_key": key, "pj": pj, "name": x["match"], "team": x.get("league", ""), "opp": x.get("book", ""),
            "stat": x["label"], "side": "", "line": 0, "dec": x["price"], "am": None, "price_fr": round(x["price"] * PJ.FR_US, 2),
            "p_sim": x.get("p_sim") if x.get("p_sim") is not None else x["p"], "p_mkt": x.get("p_fair") or x["p"],
            "date": x["ko"], "gid": "", "pid": "", "sport": "foot", "tag": tag}


def decide(state, now=None):
    """Place les paris du défi pour les matchs des prochaines heures. Renvoie la liste des paris placés."""
    now = now or datetime.now(timezone.utc)
    ch = challenge(state, now)
    if ch.get("won_at"): return []                       # objectif atteint : on fige jusqu'a la fin du defi
    if now.astimezone(TZ).hour < RULES["first_hour"]: return []
    eq = equity(state)
    brake = RULES["brake"] if eq < RULES["brake_below"] else 1.0
    taken = {b["legs"][i]["pj"]["match_id"] for b in state["bets"] for i in range(len(b["legs"]))}
    cands = [x for x in candidates(now) if x["match_id"] not in taken]
    placed = []
    for day in sorted({day_of(x["ko"]) for x in cands}):
        mine = [b for b in state["bets"] if b.get("day") == day]
        n_single = sum(1 for b in mine if b["kind"] == "single")
        n_combo = sum(1 for b in mine if b["kind"] == "combine")
        engaged = sum(b["stake"] for b in mine if b["status"] == "open") + sum(b["stake"] for b in placed if b["day"] == day)
        used = set()
        cs = [x for x in cands if day_of(x["ko"]) == day]

        def room(stake):
            return min(stake, RULES["day_max"] * eq - engaged, cash(state) - sum(b["stake"] for b in placed))

        # 1. simples : la meilleure confiance d'abord, puis l'esperance
        rank = {"forte": 0, "moyenne": 1, "correcte": 2}
        singles = []
        for x in cs:
            lo, hi = RULES["single_odds"]
            c = _conf(x)
            if c and lo <= x["price"] <= hi: singles.append((rank[c[0]], -x["ev"], x, c))
        for _, _, x, (conf, frac) in sorted(singles, key=lambda r: (r[0], r[1])):
            if n_single >= RULES["per_day_single"]: break
            if x["match_id"] in used: continue
            stake = _round(room(frac * brake * eq))
            if stake < RULES["min_stake"]: break
            placed.append(_bet(state, placed, [_leg(x, "simple")], stake, x["price"], x["p"], x["ev"], "single", conf, day, now))
            used.add(x["match_id"]); n_single += 1; engaged += stake
        # 2. combine : le plus probable dont la cote totale tombe entre 3 et 5
        if n_combo < RULES["per_day_combo"]:
            lo, hi = RULES["leg_odds"]
            pool = sorted((x for x in cs if x["match_id"] not in used and lo <= x["price"] <= hi and x["p"] >= RULES["leg_p"]
                           and x["ev"] >= RULES["leg_ev"] and (x.get("p_sim") is None or x["p_sim"] >= x["p"] - 0.05)),
                          key=lambda x: -x["p"])
            best_per_match = {}
            for x in pool: best_per_match.setdefault(x["match_id"], x)
            pool = list(best_per_match.values())[:10]
            best = None
            for k in (2, 3):
                for legs in combinations(pool, k):
                    d = math.prod(x["price"] for x in legs)
                    if not RULES["combo_odds"][0] <= d <= RULES["combo_odds"][1]: continue
                    p = math.prod(x["p"] for x in legs)
                    if p * d - 1 < RULES["combo_cost"]: continue   # la marge se multiplie a chaque match ajoute
                    if best is None or (p, p * d) > (best[0], best[0] * best[1]): best = (p, d, legs)
            if best:
                p, d, legs = best
                stake = _round(room(RULES["combo_stake"] * brake * eq))
                if stake >= RULES["min_stake"]:
                    placed.append(_bet(state, placed, [_leg(x, "combiné") for x in legs], stake, round(d, 3), round(p, 4),
                                       round(p * d - 1, 4), "combine", "combiné", day, now))
    state["bets"] += placed
    return placed


def _bet(state, placed, legs, stake, odds, p, ev, kind, conf, day, now):
    return {"legs": legs, "stake": stake, "odds": round(odds, 3), "p": round(p, 4), "ev": round(ev, 4), "kind": kind,
            "mode": "défi", "conf": conf, "id": f'{day}-f{len(state["bets"]) + len(placed) + 1}', "day": day, "night": day,
            "challenge": (state.get("challenges") or [{}])[-1].get("n"), "placed_at": now.isoformat(timespec="minutes"),
            "status": "open", "news": []}


def settle(state, now=None):
    """Règle les paris terminés (score ESPN, comme les picks du jour). Renvoie les paris réglés."""
    import picks_du_jour as PJ
    import nba_bankroll as BK
    now = now or datetime.now(timezone.utc)
    done = []
    for b in state["bets"]:
        if b["status"] != "open": continue
        res = []
        for l in b["legs"]:
            if l.get("result") in ("win", "loss", "void"):
                res.append(l["result"]); continue
            if now - _t(l["date"]) < timedelta(hours=2):
                res.append(None); continue
            try:
                r = PJ.settle_pick(l["pj"])
            except Exception as e:
                print(f"  [!] bankroll foot, reglement {l['stat']} : {type(e).__name__}: {e}")
                r = None
            res.append({"push": "void"}.get(r, r))
        if BK.settle_bet(b, res): done.append(b)
    if done: challenge(state, now)
    return done


# ── Telegram (canal « Bankroll IA ») ─────────────────────────────────────────

def _fr(x, d=2):
    return f"{x:.{d}f}".replace(".", ",")


def _head(state):
    ch = (state.get("challenges") or [{}])[-1]
    eq = equity(state)
    if not ch: return f"Capital {_fr(eq)} €"
    left = min(DAYS, max(0, math.ceil((_t(ch["end_at"]) - datetime.now(timezone.utc)).total_seconds() / 86400 - 0.01)))
    return (f'Défi x2 n°{ch["n"]} : <b>{_fr(eq)} €</b> / objectif {_fr(ch["target"])} € '
            f'({_fr(100 * (eq - ch["start_eq"]) / max(ch["target"] - ch["start_eq"], 0.01), 0)} % du chemin) · J-{left}')


def telegram_placed(placed, state):
    eq = equity(state)
    out = ["⚽ <b>Bankroll IA foot · défi x2 : paris à prendre</b>"]
    for b in placed:
        when = _t(b["legs"][0]["date"]).astimezone(TZ).strftime("%d/%m %H:%M")
        if b["kind"] == "combine":
            out.append(f'\n🔗 <b>Combiné</b> · cote <b>{_fr(b["odds"])}</b> · gagné {round(100 * b["p"])} %')
            out += [f'• {l["name"]} ({_t(l["date"]).astimezone(TZ).strftime("%d/%m %H:%M")}) : <b>{l["stat"]}</b> @ {_fr(l["dec"])}'
                    for l in b["legs"]]
        else:
            l = b["legs"][0]
            out.append(f'\n🎯 <b>{l["name"]}</b> ({l["team"]}, {when}) : <b>{l["stat"]}</b>')
            out.append(f'Cote {l["opp"]} <b>{_fr(b["odds"])}</b> (FR estimée {_fr(l["price_fr"])}) · gagné {round(100 * b["p"])} % · '
                       f'confiance {b["conf"]} · ' + (f'espérance +{round(100 * b["ev"])} %' if b["ev"] >= 0 else f'coût ≈ {round(-100 * b["ev"])} %'))
        out.append(f'Mise <b>{_fr(b["stake"])} €</b> ({_fr(100 * b["stake"] / eq, 0)} % de la bankroll)')
    out.append(f'\n{_head(state)} · <a href="{SITE}">détails sur le site</a>')
    return "\n".join(out)


def telegram_settled(bets, state):
    lab = {"won": "✅", "lost": "❌", "void": "↩️"}
    out = ["⚽ <b>Bankroll IA foot : paris réglés</b>"]
    for b in bets:
        legs = " + ".join(f'{l["name"]} : {l["stat"]}' for l in b["legs"])
        out.append(f'{lab.get(b["status"], "")} {legs} : {"+" if b["pnl"] > 0 else ""}{_fr(b["pnl"])} €')
    ch = (state.get("challenges") or [{}])[-1]
    if ch.get("won_at") and ch.get("status") == "en cours":
        out.append("\n🏆 <b>Objectif x2 atteint !</b> Plus aucun pari jusqu’à la fin du défi.")
    out.append(f'\n{_head(state)}')
    return "\n".join(out)
