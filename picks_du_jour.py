"""
picks_du_jour.py — le message unique du canal Telegram « Picks » (20 h, heure de Paris) : au plus 5 picks, seulement
ceux dont l'esperance reste positive avec une probabilite RECALEE sur les resultats reels (demande de Wilson,
04/11/2026 : « que les meilleurs picks » ; 2 canaux : Picks pour lui, Bankroll IA a part).

Pourquoi recaler : la confiance brute des modeles ment (simulateur NBA : annonce 61 %, reel 52 % ; picks « high value »
NBA : edge annonce 20-50 %, ROI reel -12,6 % sur 72). Probabilite retenue :
  - foot et NBA, lignes de match : cote juste Pinnacle (marge retiree) ; plus / moins 2,5 buts : + 0,25 x l'ecart de
    ma simulation (backtest PL 2025-26 vs cloture Pinnacle : poids optimal 0,35-0,40 sur le +2,5, 0 sur le 1X2) ;
  - props NBA : marche sans marge + 0,10 x l'ecart du simulateur (613 selections reelles), jamais si l'ecart >= 15 pts
    (ROI -13 % : info manquante) ; cote DraftKings (a verifier chez le bookmaker francais).
Esperance = probabilite retenue x meilleure cote - 1 ; seuil +2 % (cote francaise vs Pinnacle) ou +3 % (props).
Chaque pick poste est enregistre (data/telegram_picks.json) puis regle : le canal est juge sur son vrai ROI.
"""
import difflib, json
from datetime import datetime, timedelta, timezone
from pathlib import Path

PATH = Path("data/telegram_picks.json")
MAX_PICKS = 5
WINDOW_H = 24
MIN_EV_VALUE, MIN_EV_PROP = 0.02, 0.03
W_O25, W_PROP, MAX_GAP_PROP = 0.25, 0.10, 0.15
MAX_ODDS_WIN = 3.0   # victoires / nul : au-dela, retirer la marge a la proportionnelle surestime l'outsider (meme regle qu'ev_scan)
UNIT = {"AST": "passes", "3PM": "paniers à 3 pts", "STL": "interceptions", "BLK": "contres", "PTS": "points",
        "REB": "rebonds"}
JOURS = ["lun.", "mar.", "mer.", "jeu.", "ven.", "sam.", "dim."]


def _load(p, d):
    try:
        return json.loads(Path(p).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return d


def _t(iso):
    return datetime.fromisoformat(str(iso).replace("Z", "+00:00"))


def _paris(dt):
    try:
        from zoneinfo import ZoneInfo
        return dt.astimezone(ZoneInfo("Europe/Paris"))
    except Exception:
        return dt + timedelta(hours=1)


def _hm(dt):
    d = _paris(dt)
    return f"{JOURS[d.weekday()]} {d:%H:%M}"


def _dec(am):
    return None if am is None else 1 + am / 100 if am > 0 else 1 + 100 / -am


def _same(a, b):
    return difflib.SequenceMatcher(None, (a or "").lower(), (b or "").lower()).ratio()


def _fr(x, d=2):
    return f"{x:.{d}f}".replace(".", ",")


# ─── Candidats ───────────────────────────────────────────────────────────────
def value_candidates(now, hours=WINDOW_H):
    """Lignes de match (foot + NBA) : meilleure cote francaise vs probabilite recalee (Pinnacle [+ simu sur le +2,5])."""
    fair = (_load("data/ev_picks.json", {}) or {}).get("fair") or {}
    sims = (_load("data/foot_sim.json", {}) or {}).get("games") or []
    out = []
    for eid, v in fair.items():
        try:
            ko = _t(v["commence"])
        except (KeyError, ValueError):
            continue
        if not timedelta(0) < ko - now <= timedelta(hours=hours): continue
        sim = None
        if v.get("sport") == "foot":
            sim = next((g for g in sims if abs(_t(g["ko"]) - ko) <= timedelta(hours=3)
                        and _same(g["home"]["name"], v["home"]) + _same(g["away"]["name"], v["away"]) >= 1.3), None)
        for lab, fair_odds in v.get("lines") or []:
            fr = (v.get("fr") or {}).get(lab)
            if not fr or not fair_odds: continue
            if (lab.startswith("Victoire") or lab == "Match nul") and fr[0] > MAX_ODDS_WIN: continue
            p_fair = 1 / fair_odds
            p, p_sim = p_fair, None
            if sim:
                q = sim["p"]
                p_sim = {"Match nul": q["X"], f"Victoire {v['home']}": q["1"], f"Victoire {v['away']}": q["2"],
                         "Plus de 2.5 buts": q["over"]["2.5"], "Moins de 2.5 buts": 1 - q["over"]["2.5"]}.get(lab)
                if p_sim is not None and "2.5 buts" in lab:
                    p = p_fair + W_O25 * (p_sim - p_fair)
            ev = p * fr[0] - 1
            if ev < MIN_EV_VALUE: continue
            out.append({"kind": "value", "sport": v.get("sport"), "match_id": eid, "match": f"{v['home']} - {v['away']}",
                        "league": v.get("league", ""), "ko": v["commence"], "label": lab, "price": fr[0], "book": fr[1],
                        "p": round(p, 4), "p_fair": round(p_fair, 4), "p_sim": None if p_sim is None else round(p_sim, 4),
                        "ev": round(ev, 4), "res": {"espn": v.get("espn"), "home": v["home"], "away": v["away"],
                                                    "commence": v["commence"], "key": (v.get("keys") or {}).get(lab)}})
    return out


def prop_candidates(now, hours=WINDOW_H):
    """Props NBA (toutes les lignes cotees) : probabilite = marche sans marge + 0,10 x ecart du simulateur."""
    out = []
    for g in (_load("data/nba_sim.json", {}) or {}).get("games") or []:
        ko = _t(g["date"])
        if not timedelta(0) < ko - now <= timedelta(hours=hours): continue
        for pr in g.get("props") or []:
            if pr.get("p_play", 0) < 0.9 or pr.get("over") is None or pr.get("under") is None: continue
            for side in ("o", "u"):
                ps = pr["p_sim"] if side == "o" else 1 - pr["p_sim"]
                pm = pr["p_mkt"] if side == "o" else 1 - pr["p_mkt"]
                if abs(ps - pm) >= MAX_GAP_PROP: continue
                d = _dec(pr["over"] if side == "o" else pr["under"])
                p = pm + W_PROP * (ps - pm)
                ev = p * d - 1
                if ev < MIN_EV_PROP: continue
                out.append({"kind": "prop", "sport": "nba", "match_id": g["gid"],
                            "match": f"{g['away']['abbr']} @ {g['home']['abbr']}", "league": "NBA", "ko": g["date"],
                            "label": f"{pr['name']} {'plus' if side == 'o' else 'moins'} de {pr['line']:g} {UNIT.get(pr['stat'], pr['stat'])}",
                            "price": round(d, 2), "book": "DraftKings", "p": round(p, 4), "p_fair": round(pm, 4),
                            "p_sim": round(ps, 4), "ev": round(ev, 4), "safe": p >= 0.58 and d >= 1.5,
                            "res": {"gid": g["gid"], "pid": pr["id"], "stat": pr["stat"], "line": pr["line"], "side": side}})
    return out


def select(now, hours=WINDOW_H):
    """Les meilleurs : esperance decroissante, un pick par match, MAX_PICKS au plus."""
    c = sorted(value_candidates(now, hours) + prop_candidates(now, hours), key=lambda x: -x["ev"])
    out, seen = [], set()
    for x in c:
        if x["match_id"] in seen: continue
        out.append(x); seen.add(x["match_id"])
        if len(out) >= MAX_PICKS: break
    return sorted(out, key=lambda x: x["ko"])


# ─── Suivi des picks postes ──────────────────────────────────────────────────
def load():
    return _load(PATH, {"picks": []})


def record(picks, now):
    st = load()
    day = _paris(now).strftime("%Y-%m-%d")
    have = {(p["day"], p["match_id"], p["label"]) for p in st["picks"]}
    for x in picks:
        if (day, x["match_id"], x["label"]) not in have:
            st["picks"].append(dict(x, day=day, posted_at=now.isoformat(timespec="minutes")))
    PATH.write_text(json.dumps(st, ensure_ascii=False, indent=0), encoding="utf-8")


def resolve(box=None, now=None):
    """Regle les picks postes termines : value -> score ESPN (ev_scan._espn_result) ; prop -> feuille de match."""
    now = now or datetime.now(timezone.utc)
    st = load()
    n = 0
    for p in st["picks"]:
        if p.get("result") or now - _t(p["ko"]) < timedelta(hours=3): continue
        r = None
        try:
            if p["kind"] == "value" and (p["res"] or {}).get("key") and p["res"].get("espn"):
                import ev_scan
                m, nm, pt = p["res"]["key"]
                out = ev_scan._espn_result(dict(p["res"], market=m, name=nm, point=pt))
                r = out[0] if out else None
            elif p["kind"] == "prop" and box is not None:
                import nba_bankroll
                r = nba_bankroll.leg_result(dict(p["res"]), box)
                r = {"void": "push"}.get(r, r)
        except Exception as e:
            print(f"  [!] reglement pick {p.get('label')} : {type(e).__name__}: {e}")
        if r:
            p["result"] = r
            p["units"] = round(p["price"] - 1, 3) if r == "win" else -1.0 if r == "loss" else 0.0
            n += 1
    if n: PATH.write_text(json.dumps(st, ensure_ascii=False, indent=0), encoding="utf-8")
    return n


def stats():
    done = [p for p in load()["picks"] if p.get("result") in ("win", "loss")]
    if not done: return None
    w = sum(p["result"] == "win" for p in done)
    u = sum(p["units"] for p in done)
    return {"n": len(done), "w": w, "units": round(u, 2), "roi": u / len(done), "since": min(p["day"] for p in done)}


# ─── Message ─────────────────────────────────────────────────────────────────
def message(picks, now):
    head = f"🎯 <b>Picks du jour</b> · {_paris(now):%d/%m}"
    s = stats()
    foot = (f"\n\n<i>Suivi du canal depuis le {s['since'][8:]}/{s['since'][5:7]} : {s['w']}-{s['n'] - s['w']}, "
            f"{s['units']:+.1f} u (ROI {100 * s['roi']:+.0f} %).</i>" if s else
            "\n\n<i>Chaque pick posté est suivi : le bilan du canal s'affichera ici.</i>")
    if not picks:
        return (head + "\n\nAucun pari ne bat la marge du bookmaker pour les 24 prochaines heures. "
                "Je préfère ne rien poster que poster un pari perdant sur la durée." + foot)
    lines = [head, f"{len(picks)} pick{'s' if len(picks) > 1 else ''}, classés par heure · probabilités recalées sur les résultats réels"]
    for i, x in enumerate(picks, 1):
        ico = "⚽" if x["sport"] == "foot" else "🏀"
        mini = 1.0 / x["p"] * (1 + (MIN_EV_VALUE if x["kind"] == "value" else MIN_EV_PROP))
        why = (f"cote juste Pinnacle {_fr(1 / x['p_fair'])}" if x["kind"] == "value" else f"marché {round(100 * x['p_fair'])} %")
        if x.get("p_sim") is not None:
            why += f" · ma simu {round(100 * x['p_sim'])} %"
        tag = " 🛡️" if x.get("safe") else ""
        lines.append(f"\n{i}. {ico} <b>{x['match']}</b> · {_hm(_t(x['ko']))} · <i>{x['league']}</i>\n"
                     f"• <b>{x['label']}</b>{tag} — <b>@ {_fr(x['price'])}</b> {x['book']} (à prendre si ≥ {_fr(mini)})\n"
                     f"• proba retenue {round(100 * x['p'])} % ({why}) · espérance <b>{100 * x['ev']:+.0f} %</b>")
    return "\n".join(lines) + foot


def build(now=None):
    now = now or datetime.now(timezone.utc)
    picks = select(now)
    return message(picks, now), picks


if __name__ == "__main__":
    import re, sys
    sys.stdout.reconfigure(encoding="utf-8")
    txt, pk = build()
    print(re.sub(r"<[^>]+>", "", txt))
    print(f"\n[{len(value_candidates(datetime.now(timezone.utc)))} value, {len(prop_candidates(datetime.now(timezone.utc)))} props au-dessus du seuil]")
