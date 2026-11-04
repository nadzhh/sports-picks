"""
chat_context.py — briefing chiffre donne a Claude avec chaque message du chat (chat_bot.py).

Wilson veut des reponses avec les VRAIES cotes, le basket ET le foot, les simulateurs et les analyses, et un
assistant qui s'ameliore avec les resultats (04/11/2026). Plutot que de laisser Claude fouiller des JSON de
plusieurs centaines de Ko, le relais lui prepare ici :
  1. la fiabilite MESUREE de chaque source (recalculee a chaque question a partir des resultats reels :
     c'est ce qui « s'auto-enrichit » au fil des soirees) ;
  2. la NBA des 30 prochaines heures : lignes DraftKings, score simule, absences, selection du simulateur,
     picks high value (cotes reelles FanDuel / DraftKings), props safe ;
  3. le foot des 36 prochaines heures : probabilites simulees, meilleure cote francaise et cote juste
     Pinnacle (ev_scan), forme recente, picks avec cote reelle ;
  4. les value bets ouverts et la Bankroll IA.

    python chat_context.py      # affiche le briefing (controle)
"""
import difflib, json
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

try:
    from zoneinfo import ZoneInfo
    PARIS = ZoneInfo("Europe/Paris")
except Exception:
    PARIS = timezone(timedelta(hours=1))

JOURS = ["lun.", "mar.", "mer.", "jeu.", "ven.", "sam.", "dim."]
UNIT = {"AST": "passes", "3PM": "paniers à 3 pts", "STL": "interceptions", "BLK": "contres", "PTS": "points",
        "REB": "rebonds", "PRA": "pts+reb+pas"}


def _load(p, default=None):
    try:
        return json.loads(Path(p).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def _t(iso):
    return datetime.fromisoformat(str(iso).replace("Z", "+00:00"))


def _hm(dt):
    d = dt.astimezone(PARIS)
    return f"{JOURS[d.weekday()]} {d:%H:%M}"


def _dec(am):
    if am is None: return None
    return round(1 + am / 100 if am > 0 else 1 + 100 / -am, 2)


def _pc(x):
    return f"{100 * x:.0f} %"


def _same(a, b):
    return difflib.SequenceMatcher(None, (a or "").lower(), (b or "").lower()).ratio()


# ─── 1. Fiabilite mesuree ────────────────────────────────────────────────────
def reliability(now):
    out = ["## 1. Fiabilité mesurée des sources (résultats réels, recalculés à l'instant)"]
    # Selection du simulateur NBA
    P = [p for p in ((_load("data/nba_sim_picks.json", {}) or {}).get("picks") or {}).values()
         if p.get("result") in ("win", "loss")]
    if P:
        w = sum(p["result"] == "win" for p in P)
        u = sum(_dec(p["first_price"]) - 1 if p["result"] == "win" else -1 for p in P)
        ps = sum(p["first_p_sim"] for p in P) / len(P); pm = sum(p["first_p_mkt"] for p in P) / len(P)
        clv = [p["last_p_mkt"] * _dec(p["first_price"]) - 1 for p in P if p.get("last_p_mkt") is not None]
        out.append(f"- Sélection du simulateur NBA (props passes / 3 pts / interceptions / contres, 1re cote DraftKings) : "
                   f"{w}-{len(P) - w}, {u:+.1f} u sur {len(P)} ({100 * u / len(P):+.1f} %) ; le simulateur annonçait {_pc(ps)}, "
                   f"réussite réelle {_pc(w / len(P))}, marché sans marge {_pc(pm)}"
                   + (f" ; CLV {100 * sum(clv) / len(clv):+.1f} %" if clv else "") + " → le simulateur seul ne bat pas la marge.")
        g = defaultdict(list)
        for p in P: g[(p["stat"], p["side"])].append(p)
        rows = []
        for (s, sd), L in sorted(g.items(), key=lambda kv: -len(kv[1])):
            if len(L) < 15: continue
            ww = sum(p["result"] == "win" for p in L)
            uu = sum(_dec(p["first_price"]) - 1 if p["result"] == "win" else -1 for p in L)
            rows.append(f"{'Over' if sd == 'o' else 'Under'} {UNIT.get(s, s)} {ww}-{len(L) - ww} ({100 * uu / len(L):+.0f} %)")
        if rows: out.append("  Par marché : " + " · ".join(rows))
    # High value NBA (ancien moteur, criteres du recap Telegram)
    try:
        import notify
        H = [p for p in (_load("data/nba_picks_history.json", {}) or {}).get("picks") or []
             if p.get("result") in ("WIN", "LOSS") and p.get("real_cote") and notify._is_high_value_nba(p)
             and (p.get("date") or "") >= (now - timedelta(days=45)).strftime("%Y-%m-%d")]
        if H:
            w = sum(p["result"] == "WIN" for p in H)
            u = sum(p["real_cote"] - 1 if p["result"] == "WIN" else -1 for p in H)
            out.append(f"- Picks NBA « high value » (moteur props, cotes réelles FanDuel / DraftKings, 45 j) : "
                       f"{w}-{len(H) - w}, {u:+.1f} u sur {len(H)} ({100 * u / len(H):+.1f} %).")
        F = []
        for p in (list((_load("data/picks_history.json", {}) or {}).values()) or [[]])[0]:
            if p.get("result") not in ("WIN", "LOSS") or (p.get("date") or "") < "2026-10-01": continue
            if p.get("is_fun"):
                c, src = notify._effective_cote(p)
                if (p.get("direction") or "").startswith("wc_score_") or not c or c < 2.0: continue
            elif not notify._is_push_eligible_team(p)[0]: continue
            c, src = notify._effective_cote(p)
            if src == "book" and c: F.append((p, c))
        if F:
            w = sum(p["result"] == "WIN" for p, _ in F)
            u = sum(c - 1 if p["result"] == "WIN" else -1 for p, c in F)
            out.append(f"- Picks foot « high value » (critères du récap Telegram, cote bookmaker réelle, depuis le 01/10) : "
                       f"{w}-{len(F) - w}, {u:+.1f} u sur {len(F)} ({100 * u / len(F):+.1f} %).")
    except Exception:
        pass
    # Calibration des marches foot (moteur picks)
    cal = (_load("data/calibration.json", {}) or {}).get("marches") or {}
    rows = [f"{k.split('/', 1)[-1]} {v['wr']:.0f} % ({v['n']})" + (f" cote moy. {v['cote_moyenne']:.2f}" if v.get("cote_moyenne") else "")
            for k, v in sorted(cal.items(), key=lambda kv: -kv[1]["n"]) if k.startswith("foot/") and v["n"] >= 40][:8]
    if rows:
        out.append("- Moteur picks foot, réussite réelle par marché (n) : " + " · ".join(rows)
                   + ". ATTENTION : sa « confiance » 90 %+ ne gagne qu'environ 52 % → ne jamais la citer comme une probabilité.")
    # Simulateur foot
    try:
        import foot_sim_bilan
        s = foot_sim_bilan._summary(list(foot_sim_bilan.load()["games"].values()))
        if s:
            out.append(f"- Simulateur foot ({s['n']} matchs) : probabilité moyenne donnée au vrai résultat {s['p_true']:.2f} "
                       f"(0,33 hasard, ~0,38-0,40 niveau marché) ; buts {s['goals_sim']:.2f} simulés / {s['goals_real']:.2f} réels.")
    except Exception:
        pass
    # Canal Telegram « Picks » (au plus 5 picks / jour, probabilite recalee ; picks_du_jour)
    try:
        import picks_du_jour
        s_ = picks_du_jour.stats()
        out.append(f"- Canal Telegram « Picks » (mes picks postés à Wilson depuis le {s_['since']}) : {s_['w']}-{s_['n'] - s_['w']}, "
                   f"{s_['units']:+.1f} u ({100 * s_['roi']:+.1f} %)." if s_ else
                   "- Canal Telegram « Picks » : pas encore de pick posté réglé (filtre : espérance > 0 en probabilité recalée).")
    except Exception:
        pass
    # Value bets
    ev = (_load("data/ev_picks.json", {}) or {}).get("picks") or {}
    R = [p for p in ev.values() if p.get("result") in ("win", "loss")]
    if R:
        w = sum(p["result"] == "win" for p in R)
        u = sum(p["price"] - 1 if p["result"] == "win" else -1 for p in R)
        out.append(f"- Value bets (cote FR > cote juste Pinnacle) : {w}-{len(R) - w}, {u:+.1f} u sur {len(R)}.")
    else:
        out.append("- Value bets (cote FR > cote juste Pinnacle) : seul avantage validé sur l'historique (CLV +6 à +9 % "
                   "sur les plus/moins 2,5 buts, 2 saisons) ; pas encore de résultat réel suivi.")
    return out


# ─── 2. NBA ──────────────────────────────────────────────────────────────────
def nba(now, hours=30):
    games = [g for g in (_load("data/nba_sim.json", {}) or {}).get("games") or []
             if timedelta(hours=-3) <= _t(g["date"]) - now <= timedelta(hours=hours)]
    if not games: return ["## 2. NBA : aucun match dans les 30 prochaines heures."]
    old = list((_load("data/nba_picks.json", {}) or {}).values())
    try:
        import notify
        hv_ok = notify._is_high_value_nba
    except Exception:
        hv_ok = lambda p: False
    out = [f"## 2. NBA (matchs des {hours} prochaines heures, heures de Paris ; cotes décimales)"]
    for g in sorted(games, key=lambda g: g["date"]):
        h, a, ln, tm = g["home"], g["away"], g.get("lines") or {}, g.get("team") or {}
        out.append(f"\n### {a['abbr']} @ {h['abbr']} ({a['name']} chez {h['name']}) · {_hm(_t(g['date']))}")
        if ln:
            out.append(f"- {ln.get('book', 'DraftKings')} : {h['abbr']} {ln.get('home_spread'):+g} · total {ln.get('total')} · "
                       f"vainqueur {h['abbr']} @ {_dec(ln.get('home_ml'))} / {a['abbr']} @ {_dec(ln.get('away_ml'))}")
        if tm:
            out.append(f"- Simulation : {h['abbr']} {tm.get('home_mean')} - {tm.get('away_mean')} {a['abbr']} (total {tm.get('total_mean')}), "
                       f"{h['abbr']} gagne {_pc(tm.get('p_home_win', 0))} ; forme {h['abbr']} {''.join(h.get('form') or [])} "
                       f"({h.get('record', '')}), {a['abbr']} {''.join(a.get('form') or [])} ({a.get('record', '')})")
        inj = g.get("injuries") or {}
        for sd, ab in (("home", h["abbr"]), ("away", a["abbr"])):
            x = inj.get(sd) or {}
            outs = [p["name"] for p in x.get("out") or []]
            dbt = [f"{p['name']} ({p.get('status', '?')})" for p in x.get("doubt") or []]
            if outs or dbt:
                out.append(f"- Absences {ab} (rapport {x.get('src') or '?'}) : " + "; ".join(
                    t for t in (", ".join(outs), f"incertains : {', '.join(dbt)}" if dbt else "") if t))
        sel = []
        for p in g.get("props") or []:
            if not p.get("pick"): continue
            o = p["pick"] == "o"
            ps, pm = (p["p_sim"], p["p_mkt"]) if o else (1 - p["p_sim"], 1 - p["p_mkt"])
            sel.append((ps - pm, f"{p['name']} {'Over' if o else 'Under'} {p['line']:g} {UNIT.get(p['stat'], p['stat'])} @ "
                                 f"{_dec(p['over'] if o else p['under'])} (simu {_pc(ps)}, marché {_pc(pm)})"))
        if sel:
            out.append("- Sélection du simulateur (fiabilité faible, voir §1) : " + " ; ".join(t for _, t in sorted(sel, reverse=True)[:6]))
        og = next((x for x in old if x.get("home_team", "").lower() in h["name"].lower()
                   and x.get("away_team", "").lower() in a["name"].lower()), None)
        hv = [p for p in ((og or {}).get("home_picks") or []) + ((og or {}).get("away_picks") or []) if hv_ok(p) and p.get("real_cote")]
        if hv:
            out.append("- High value (moteur props, critères Telegram) : " + " ; ".join(
                f"{p['label']} @ {p['real_cote']} {str(p.get('book', '')).upper()} (confiance {p.get('confidence')} %, "
                f"edge {p.get('edge')} %, L10 {p.get('hit_l10', '?')}, L20 {p.get('hit_l20', '?')})" for p in hv[:6]))
    try:
        import notify
        sb = notify._safe_block(now, hours)
        if sb:
            import re
            out.append("\n### Props safe du soir (pour Wilson)\n" + re.sub(r"<[^>]+>", "", sb))
    except Exception:
        pass
    return out


# ─── 3. Foot ─────────────────────────────────────────────────────────────────
def _fair_for(fair, home, away, ko):
    for v in fair.values():
        try:
            if abs(_t(v["commence"]) - ko) > timedelta(hours=3): continue
        except (KeyError, ValueError):
            continue
        if _same(v["home"], home) + _same(v["away"], away) >= 1.3:
            return v
    return None


def foot(now, hours=36):
    sim = [g for g in (_load("data/foot_sim.json", {}) or {}).get("games") or []
           if timedelta(hours=-2) <= _t(g["ko"]) - now <= timedelta(hours=hours)]
    picks = [m for m in _load("data/picks.json", []) or []
             if m.get("start_ts") and timedelta(hours=-2) <= datetime.fromtimestamp(int(m["start_ts"]), timezone.utc) - now <= timedelta(hours=hours)]
    fair = (_load("data/ev_picks.json", {}) or {}).get("fair") or {}
    try:
        import notify
        eff = notify._effective_cote
    except Exception:
        eff = lambda p: (None, None)
    out = [f"## 3. Foot (matchs des {hours} prochaines heures)"]
    used = set()
    for g in sorted(sim, key=lambda g: g["ko"]):
        ko, hn, an, p = _t(g["ko"]), g["home"]["name"], g["away"]["name"], g["p"]
        out.append(f"\n### {hn} - {an} · {g.get('league_name', '')} · {_hm(ko)} (simulé)")
        out.append(f"- Simulation 10 000 matchs : 1 {_pc(p['1'])} / N {_pc(p['X'])} / 2 {_pc(p['2'])} ; buts attendus "
                   f"{g['goals'][0]:.2f}-{g['goals'][1]:.2f} (xG {g['xg'][0]:.2f}-{g['xg'][1]:.2f}) ; +2,5 {_pc(p['over']['2.5'])} ; "
                   f"les deux marquent {_pc(p['btts'])}" + (f" ; tirs {g['shots']['home'][0]}-{g['shots']['away'][0]} (cadrés {g['shots']['home'][1]}-{g['shots']['away'][1]})" if g.get("shots") else ""))
        L = (g.get("shots") or {}).get("lines") or {}
        if L:
            nm = {"home_sh": f"tirs {hn}", "home_so": f"cadrés {hn}", "away_sh": f"tirs {an}", "away_so": f"cadrés {an}",
                  "tot_sh": "tirs du match (fiabilité moyenne)", "tot_so": "cadrés du match (fiabilité moyenne)"}
            out.append("- Lignes de tirs (proba recalée sur backtest 548 matchs, cote juste) : " + " ; ".join(
                f"{nm[k]} " + ", ".join(f"+{l} {_pc(p_)} @{1 / max(p_, .01):.2f}" for l, p_ in v.items()) for k, v in L.items()))
        for sd in ("home", "away"):
            i = g.get(sd + "_info") or {}
            ab = [f"{x['name']} (−{100 * x['impact']:.0f} % attaque)" for x in i.get("absences") or [] if x.get("impact", 0) >= 0.05]
            f = (g.get("form") or {}).get(sd)
            bits = []
            if f: bits.append(f"forme {''.join(f.get('last') or [])}, {f['gf']:.1f} marqués / {f['ga']:.1f} encaissés, xG {f.get('xg_f', 0):.2f} / {f.get('xg_a', 0):.2f}, +2,5 dans {round(f['o25'] * f['n'])}/{f['n']}")
            if ab: bits.append("absents : " + ", ".join(ab))
            bits.append(f"composition {i.get('lineup', '?')}")
            sp_ = (g.get("style") or {}).get(sd)
            if sp_:
                try:
                    from foot_sim_site import _style_tags
                    bits.append(f"style ({sp_['formation']}) : " + (", ".join(_style_tags(sp_)) or "sans trait marquant")
                                + f", tire {sp_['sh']:.1f} / concède {sp_['sha']:.1f}")
                except Exception:
                    pass
            out.append(f"- {g[sd]['name']} : " + " ; ".join(bits))
        v = _fair_for(fair, hn, an, ko)
        if v:
            fr = v.get("fr") or {}
            out.append("- Cotes (meilleure FR / cote juste Pinnacle) : " + " · ".join(
                f"{lab} {fr[lab][0]:.2f} {fr[lab][1]} / {odd:.2f}" if lab in fr else f"{lab} juste {odd:.2f}" for lab, odd in v["lines"][:8]))
        m = next((m for m in picks if _same(m["home"], hn) + _same(m["away"], an) >= 1.3), None)
        if m:
            used.add(id(m))
            pk = [(x, *eff(x)) for x in (m.get("picks") or []) + (m.get("fun_picks") or [])]
            booked = [f"{x['label']} @ {c:.2f}" + (f" (edge {x['edge_pp']:+.0f} pts)" if x.get("edge_pp") is not None else "")
                      for x, c, src in pk if src == "book" and c]
            if booked: out.append("- Cotes bookmaker relevées (moteur picks) : " + " · ".join(booked[:8]))
    rest = []
    for m in picks:
        if id(m) in used: continue
        pk = [(x, *eff(x)) for x in (m.get("picks") or []) + (m.get("fun_picks") or [])]
        booked = [f"{x['label']} @ {c:.2f}" + (f" (edge {x['edge_pp']:+.0f} pts)" if x.get("edge_pp") is not None else "")
                  for x, c, src in pk if src == "book" and c]
        if booked:
            ko = datetime.fromtimestamp(int(m["start_ts"]), timezone.utc)
            rest.append(f"- {m['home']} - {m['away']} ({m.get('league', '')}, {_hm(ko)}) : " + " · ".join(booked[:5]))
    if rest:
        out.append("\n### Autres matchs foot avec cote bookmaker (non simulés, moteur picks seul)")
        out += rest[:25]
    return out


# ─── 4. Value bets et bankroll ───────────────────────────────────────────────
def money(now):
    out = ["## 4. Value bets ouverts et Bankroll IA"]
    ev = (_load("data/ev_picks.json", {}) or {}).get("picks") or {}
    opn = [p for p in ev.values() if not p.get("result") and _t(p["commence"]) > now - timedelta(hours=3)]
    for p in sorted(opn, key=lambda p: p["commence"])[:12]:
        out.append(f"- Value bet : {p['home']} - {p['away']} ({p['league']}, {_hm(_t(p['commence']))}) : {p['label']} "
                   f"@ {p['price']} {p['book_name']}, cote juste {1 / p['fair']:.2f}, espérance {100 * p['ev']:+.1f} %")
    if not opn: out.append("- Aucun value bet ouvert (cotes FR rarement au-dessus de la cote juste : marge 5-14 %).")
    b = _load("data/nba_bankroll.json", {}) or {}
    if b.get("bets"):
        eq = b["start"] + sum(x.get("pnl", 0.0) for x in b["bets"] if x["status"] != "open")
        o = [x for x in b["bets"] if x["status"] == "open"]
        out.append(f"- Bankroll IA (ma bankroll, autonome) : capital {eq:.2f} € (départ {b['start']:.0f}), {len(o)} pari(s) en cours"
                   + "".join(f"\n  · {x['kind']} {x.get('conf', '')} : " + " + ".join(f"{l['name']} {l.get('stat', '')} "
                                                                            f"{'Over' if l.get('side') == 'o' else 'Under' if l.get('side') == 'u' else ''} {l.get('line', '') or ''}".strip()
                                                                            for l in x["legs"]) + f" @ {x['odds']}, mise {x['stake']} €" for x in o[:10]))
    return out


def briefing(now=None):
    now = now or datetime.now(timezone.utc)
    parts = []
    for f in (reliability, nba, foot, money):
        try:
            parts.append("\n".join(f(now)))
        except Exception as e:
            parts.append(f"## ({f.__name__} indisponible : {type(e).__name__})")
    return f"# Briefing SportsPicks · {_hm(now)} (heure de Paris)\n\n" + "\n\n".join(parts)


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8")
    b = briefing()
    print(b)
    print(f"\n[{len(b)} caractères]")
