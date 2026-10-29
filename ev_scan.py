"""
ev_scan.py — Value bets « +EV » : cotes des bookmakers francais au-dessus de la probabilite juste de Pinnacle.

Methode des parieurs professionnels : la probabilite « juste » est celle de Pinnacle (bookmaker le plus
efficace), marge retiree. On parie quand Winamax / Betclic / Unibet / PMU paient plus que cette
probabilite (esperance >= MIN_EV). Le simulateur n'intervient pas : c'est une erreur de PRIX.

Constat en production (29/10/2026) : les cotes francaises sont 5 a 14 % SOUS la cote juste Pinnacle
(marge et fiscalite francaises) -> occasion +EV exceptionnelle sur les marches principaux ; le scan sert de
sentinelle. Il garde surtout les COTES JUSTES (st["fair"]) affichees sur le site pour juger une cote
boostee Winamax / Betclic (invisible de l'API) avec le calculateur.

Validation (football-data.co.uk, cotes relevees au meme moment chez Pinnacle et chez les bookmakers
« soft », Top 5, 29/10/2026) : plus/moins 2,5 buts, EV >= 2 % : CLV +6,0 % (2024-25, 65 paris, ROI +20 %)
et +8,6 % (2025-26, 25 paris, ROI +1 %) -> avantage reel mais RARE. 1X2 : quasi aucune occasion sous
la cote 3 ; au-dela, outsiders a forte variance (exclus).

Quota The Odds API (offre gratuite, 500 credits / mois / cle) : la liste des matchs est gratuite ; les
cotes coutent 1 credit par marche et par sport -> on ne paie que les sports avec un match dans les 36 h.

    picks = scan()      # -> nouvelles occasions (et mise a jour des cotes de cloture / resultats)
Usage : python ev_scan.py [--dry]
"""
import argparse, json, sys, time, urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

import config

API = "https://api.the-odds-api.com/v4"
PATH = Path("data/ev_picks.json")
BOOKS_FR = {"winamax_fr": "Winamax", "betclic_fr": "Betclic", "unibet_fr": "Unibet", "pmu_fr": "PMU"}
SPORTS = {   # cle Odds API -> (sport, nom, marches, slug ESPN pour les resultats)
    "soccer_epl": ("foot", "Premier League", "h2h,totals", "soccer/eng.1"),
    "soccer_spain_la_liga": ("foot", "LaLiga", "h2h,totals", "soccer/esp.1"),
    "soccer_germany_bundesliga": ("foot", "Bundesliga", "h2h,totals", "soccer/ger.1"),
    "soccer_italy_serie_a": ("foot", "Serie A", "h2h,totals", "soccer/ita.1"),
    "soccer_france_ligue_one": ("foot", "Ligue 1", "h2h,totals", "soccer/fra.1"),
    "soccer_uefa_champs_league": ("foot", "Ligue des champions", "h2h,totals", "soccer/uefa.champions"),
    "soccer_uefa_europa_league": ("foot", "Ligue Europa", "h2h,totals", "soccer/uefa.europa"),
    "basketball_nba": ("nba", "NBA", "h2h,spreads,totals", "basketball/nba"),
}
MIN_EV = 0.02
MAX_ODDS_H2H = 3.0          # outsiders : variance enorme, avantage non demontre
HORIZON = timedelta(hours=36)
RESERVE = 40                # credits gardes en reserve par cle


def _keys():
    """[(nom, cle)] : le fichier versionne ne garde que le NOM de la cle (k3, k2, k1), jamais la cle."""
    return [(n, k) for n, k in (("k3", config.ODDS_API_KEY3), ("k2", config.ODDS_API_KEY2), ("k1", config.ODDS_API_KEY)) if k]


def _get(path, params, key):
    q = "&".join(f"{k}={v}" for k, v in params.items())
    r = urllib.request.urlopen(f"{API}{path}?apiKey={key}&{q}", timeout=30)
    return json.loads(r.read()), int(r.headers.get("x-requests-remaining") or 0)


def load():
    try:
        return json.loads(PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"picks": {}, "quota": {}}


def save(st):
    PATH.write_text(json.dumps(st, ensure_ascii=False, indent=0), encoding="utf-8")


def _fair(outcomes):
    """Probabilites sans marge (methode multiplicative) : {nom|point: p}."""
    inv = {(o["name"], o.get("point")): 1 / o["price"] for o in outcomes if o.get("price")}
    t = sum(inv.values())
    return {k: v / t for k, v in inv.items()} if t else {}


def _label(sport, market, name, point, home, away):
    if market == "h2h":
        return "Match nul" if name == "Draw" else f"Victoire {name}"
    if market == "totals":
        return f"{'Plus' if name == 'Over' else 'Moins'} de {point:g} {'buts' if sport == 'foot' else 'points'}"
    if market == "spreads":
        return f"{name} {point:+g}"
    return f"{name} {point or ''}"


def opportunities(ev):
    """Occasions +EV d'un evenement (cotes Odds API, region eu)."""
    books = {b["key"]: b for b in ev.get("bookmakers") or []}
    pin = books.get("pinnacle")
    if not pin: return []
    sport = SPORTS[ev["sport_key"]][0]
    out = []
    for pm in pin["markets"]:
        fair = _fair(pm["outcomes"])
        for bk, nm in BOOKS_FR.items():
            b = books.get(bk)
            if not b: continue
            m = next((x for x in b["markets"] if x["key"] == pm["key"]), None)
            if not m: continue
            for o in m["outcomes"]:
                p = fair.get((o["name"], o.get("point")))
                if not p: continue           # meme ligne exigee (point identique a Pinnacle)
                if pm["key"] == "h2h" and o["price"] > MAX_ODDS_H2H: continue
                evv = p * o["price"] - 1
                if evv >= MIN_EV:
                    out.append({"market": pm["key"], "name": o["name"], "point": o.get("point"), "book": bk, "book_name": nm,
                                "price": o["price"], "fair": round(p, 4), "ev": round(evv, 4),
                                "label": _label(sport, pm["key"], o["name"], o.get("point"), ev["home_team"], ev["away_team"])})
    # une seule ligne par issue : la meilleure cote francaise
    best = {}
    for o in out:
        k = (o["market"], o["name"], o["point"])
        if k not in best or o["price"] > best[k]["price"]: best[k] = o
    return list(best.values())


def scan(dry=False):
    st = load()
    now = datetime.now(timezone.utc)
    keys = _keys()
    new, n_ev = [], 0
    for skey, (sport, title, markets, slug) in SPORTS.items():
        try:
            events, _ = _get(f"/sports/{skey}/events", {}, keys[0][1])     # gratuit
        except Exception as e:
            print(f"  [!] {skey} : {e}"); continue
        soon = [e for e in events if timedelta(0) < datetime.fromisoformat(e["commence_time"].replace("Z", "+00:00")) - now <= HORIZON]
        if not soon: continue
        cost = len(markets.split(","))
        kn = next(((n, k) for n, k in keys if st["quota"].get(n, 999) - cost > RESERVE), None)
        if not kn:
            print("  [!] quota Odds API epuise : scan interrompu"); break
        try:
            data, rem = _get(f"/sports/{skey}/odds", {"regions": "eu", "markets": markets, "oddsFormat": "decimal"}, kn[1])
        except Exception as e:
            print(f"  [!] cotes {skey} : {e}"); continue
        st["quota"][kn[0]] = rem
        for ev in data:
            t = datetime.fromisoformat(ev["commence_time"].replace("Z", "+00:00"))
            if not timedelta(0) < t - now <= HORIZON: continue
            pin0 = next((b for b in ev.get("bookmakers") or [] if b["key"] == "pinnacle"), None)
            if pin0:   # cotes justes (sans marge) pour le calculateur du site
                st.setdefault("fair", {})[ev["id"]] = {
                    "sport": sport, "league": title, "home": ev["home_team"], "away": ev["away_team"], "commence": ev["commence_time"],
                    "at": now.isoformat(timespec="minutes"),
                    "lines": [[_label(sport, pm["key"], nm, pt, ev["home_team"], ev["away_team"]), round(1 / pf, 3)]
                              for pm in pin0["markets"] for (nm, pt), pf in _fair(pm["outcomes"]).items()]}
            # cote juste Pinnacle a jour pour les picks deja ouverts (-> CLV a la derniere mise a jour)
            pin = next((b for b in ev.get("bookmakers") or [] if b["key"] == "pinnacle"), None)
            if pin:
                for pm in pin["markets"]:
                    fair = _fair(pm["outcomes"])
                    for pk, p in st["picks"].items():
                        if p["event"] == ev["id"] and p["market"] == pm["key"] and not p.get("result"):
                            f = fair.get((p["name"], p["point"]))
                            if f: p["last_fair"], p["last_seen"] = round(f, 4), now.isoformat(timespec="minutes")
            for o in opportunities(ev):
                n_ev += 1
                k = f'{ev["id"]}|{o["market"]}|{o["name"]}|{o["point"]}'
                if k in st["picks"]: continue
                p = dict(o, key=k, event=ev["id"], sport=sport, league=title, espn=slug, commence=ev["commence_time"],
                         home=ev["home_team"], away=ev["away_team"], first_seen=now.isoformat(timespec="minutes"))
                st["picks"][k] = p; new.append(p)
        print(f"  {title:20s} {len(soon):3d} matchs < 36 h · credits restants {rem}", flush=True)
    st["scanned_at"] = now.isoformat(timespec="minutes")
    st["fair"] = {k: v for k, v in (st.get("fair") or {}).items()
                  if datetime.fromisoformat(v["commence"].replace("Z", "+00:00")) > now}
    n_res = resolve(st)
    if not dry: save(st)
    print(f"[ev] {n_ev} occasion(s) en cours, {len(new)} nouvelle(s), {n_res} resultat(s)")
    return new


def _espn_result(p):
    """Gagne / perdu / rembourse d'apres le score ESPN (None si pas termine)."""
    from nba_boxdb import _get as eget
    import difflib
    day = datetime.fromisoformat(p["commence"].replace("Z", "+00:00"))
    for d in {day.strftime("%Y%m%d"), (day - timedelta(hours=6)).strftime("%Y%m%d")}:
        sb = eget(f"https://site.api.espn.com/apis/site/v2/sports/{p['espn']}/scoreboard?dates={d}") or {}
        for e in sb.get("events") or []:
            c = e["competitions"][0]
            if (c["status"]["type"] or {}).get("state") != "post": continue
            sides = {x["homeAway"]: x for x in c["competitors"]}
            nm = lambda x: x["team"]["displayName"]
            sim = difflib.SequenceMatcher(None, nm(sides["home"]).lower(), p["home"].lower()).ratio() + \
                difflib.SequenceMatcher(None, nm(sides["away"]).lower(), p["away"].lower()).ratio()
            if sim < 1.3: continue
            h, a = float(sides["home"]["score"]), float(sides["away"]["score"])
            if p["market"] == "h2h":
                win = (p["name"] == "Draw" and h == a) or (p["name"] == p["home"] and h > a) or (p["name"] == p["away"] and a > h)
                return "win" if win else "loss", [h, a]
            if p["market"] == "totals":
                tot = h + a
                if tot == p["point"]: return "push", [h, a]
                return ("win" if (tot > p["point"]) == (p["name"] == "Over") else "loss"), [h, a]
            if p["market"] == "spreads":
                m = (h - a) if p["name"] == p["home"] else (a - h)
                if m + p["point"] == 0: return "push", [h, a]
                return ("win" if m + p["point"] > 0 else "loss"), [h, a]
    return None, None


def resolve(st):
    n = 0
    now = datetime.now(timezone.utc)
    for p in st["picks"].values():
        if p.get("result"): continue
        if now - datetime.fromisoformat(p["commence"].replace("Z", "+00:00")) < timedelta(hours=2.5): continue
        try:
            res, score = _espn_result(p)
        except Exception:
            res = None
        if res:
            p["result"], p["score"] = res, score; n += 1
    return n


def due(min_hours=11):
    """Un balayage a-t-il eu lieu dans les min_hours dernieres heures ? (quota : ~2 balayages par jour)"""
    t = load().get("scanned_at")
    return not t or datetime.now(timezone.utc) - datetime.fromisoformat(t) >= timedelta(hours=min_hours)


def telegram_msg(new):
    lines = ["💎 <b>Value bets : cote française au-dessus de la cote juste</b>"]
    for p in new:
        lines.append(f'\n{"⚽" if p["sport"] == "foot" else "🏀"} <b>{p["home"]} - {p["away"]}</b> ({p["league"]})\n'
                     f'{p["label"]} chez <b>{p["book_name"]}</b> à <b>{p["price"]:.2f}</b> · cote juste {1 / p["fair"]:.2f} · '
                     f'espérance +{100 * p["ev"]:.1f} %')
    return "\n".join(lines)


def summary(st=None):
    st = st or load()
    done = [p for p in st["picks"].values() if p.get("result") in ("win", "loss", "push")]
    units = sum(p["price"] - 1 if p["result"] == "win" else (-1 if p["result"] == "loss" else 0) for p in done)
    clv = [p["last_fair"] * p["price"] - 1 for p in st["picks"].values() if p.get("last_fair")]
    return {"n": len(done), "w": sum(p["result"] == "win" for p in done), "l": sum(p["result"] == "loss" for p in done),
            "units": units, "roi": units / len(done) if done else None, "clv": sum(clv) / len(clv) if clv else None,
            "n_clv": len(clv), "open": sum(1 for p in st["picks"].values() if not p.get("result"))}


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true")
    a = ap.parse_args()
    for p in scan(a.dry):
        print(f'  + {p["league"]} {p["home"]} - {p["away"]} : {p["label"]} @ {p["price"]} ({p["book_name"]}) '
              f'juste {1 / p["fair"]:.2f}, EV {100 * p["ev"]:+.1f} %')
