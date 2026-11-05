"""
site_today.py — Onglet « Aujourd'hui » (demande de Wilson, 05/11/2026 : « améliorer le frontend », point 2).

Constat : pour savoir quoi jouer ce soir, il fallait ouvrir au moins 4 onglets, et les picks du jour du canal
Telegram (paliers value / safe / meilleur prix, picks_du_jour.py) n'apparaissaient nulle part sur le site.
Cette page les regroupe : picks du jour par palier (recalcules a chaque mise a jour, avec les cotes du moment),
matchs des 24 prochaines heures (probabilites de la simulation, lien vers la fiche), resume de la Bankroll IA.

Comme les sections Simulation : ecrite dans today.html par la veille (nba_live.publish, a chaque publication) et par
le cron (generate_site) ; index.html en garde une copie de secours remplacee si today.html est plus recent.
"""
import html
from datetime import datetime, timedelta, timezone

import picks_du_jour as PJ

FRAGMENT = "today.html"
try:
    from zoneinfo import ZoneInfo
    TZ = ZoneInfo("Europe/Paris")
except Exception:
    TZ = timezone(timedelta(hours=1))
JOURS = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]
MOIS = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août", "septembre", "octobre", "novembre", "décembre"]


def _e(s):
    return html.escape(str(s if s is not None else ""))


def _fr(x, d=2):
    return f"{x:.{d}f}".replace(".", ",")


def _pc(x):
    return f"{round(100 * x)} %"


def _t(iso):
    return datetime.fromisoformat(str(iso).replace("Z", "+00:00"))


def _hm(iso):
    try:
        return _t(iso).astimezone(TZ).strftime("%H:%M")
    except Exception:
        return ""


def _day(iso, now):
    d = _t(iso).astimezone(TZ).date()
    n = now.astimezone(TZ).date()
    return "" if d == n else "demain " if d == n + timedelta(days=1) else f"{d:%d/%m} "


def _games():
    sims = (PJ._load("data/foot_sim.json", {}) or {}).get("games") or []
    nba = (PJ._load("data/nba_sim.json", {}) or {}).get("games") or []
    return sims, nba


def _link(x, sims, nba):
    """(onglet, identifiant) de la fiche du match d'un pick, ou None."""
    try:
        ko = _t(x["ko"])
        if x["kind"] == "prop" or x.get("src") == "prop":
            return ("nba", x["match_id"])
        home, away = (x["res"] or {}).get("home"), (x["res"] or {}).get("away")
        if x["sport"] == "foot":
            g = next((g for g in sims if abs(_t(g["ko"]) - ko) <= timedelta(hours=3)
                      and PJ._same(g["home"]["name"], home) + PJ._same(g["away"]["name"], away) >= 1.3), None)
            return ("foot", g["id"]) if g else None
        g = next((g for g in nba if abs(_t(g["date"]) - ko) <= timedelta(hours=3)
                  and PJ._same(g["home"].get("name"), home) + PJ._same(g["away"].get("name"), away) >= 1.3), None)
        return ("nba", g["gid"]) if g else None
    except Exception:
        return None


def _go(link, label="Fiche"):
    if not link: return ""
    return f'<button class="td-go" onclick="spToday(\'{link[0]}\',\'{_e(link[1])}\')">{label} →</button>'


def _pick_row(x, sims, nba):
    ico = "⚽" if x["sport"] == "foot" else "🏀"
    kind = x.get("src") if x["kind"] == "safe" else x["kind"]
    ref = (f"juste Pinnacle {_fr(1 / x['p_fair'])}" if kind in ("value", "best") else f"marché {_pc(x['p_fair'])}")
    if x.get("p_sim") is not None: ref += f" · ma simu {_pc(x['p_sim'])}"
    ev = (f'<span class="eb-acc">espérance <b>{100 * x["ev"]:+.0f} %</b></span>' if x["ev"] >= 0
          else f'<span class="eb-dim">coût attendu <b>{_fr(100 * -x["ev"], 1)} %</b></span>')
    return (f'<div class="td-pick"><div class="td-l1"><span>{ico} <b>{_e(x["match"])}</b> '
            f'<span class="eb-dim">· {_hm(x["ko"])} · {_e(x["league"])}</span></span>{_go(_link(x, sims, nba))}</div>'
            f'<div class="td-l2"><b>{_e(x["label"])}</b> <span class="td-odds">@ {_fr(x["price"])}</span> '
            f'<span class="eb-dim">{_e(x["book"])}</span></div>'
            f'<div class="td-l3">gagné {_pc(x["p"])} <span class="eb-dim">({ref})</span> · {ev}</div></div>')


def _tier_stats(kinds):
    s = PJ.stats(kinds)
    if not s: return '<p class="eb-note">Bilan affiché dès les premiers paris réglés.</p>'
    u = _fr(s["units"], 1)
    return (f'<p class="eb-note">Bilan depuis le {s["since"][8:]}/{s["since"][5:7]} : {s["w"]}-{s["n"] - s["w"]}, '
            f'{u if s["units"] < 0 else "+" + u} u (ROI {100 * s["roi"]:+.0f} %).</p>')


def _tiers(now):
    value = PJ.select(now)
    safe = PJ.safe_picks(now, taken={x["match_id"] for x in value})
    best = PJ.best_price(now, taken={x["match_id"] for x in value + safe})
    return value, safe, best


TIER_TAG = {"value": ("Value", "acc"), "prop": ("Value", "acc"), "safe": ("🛡️ Safe", "acc"), "best": ("💡 Meilleur prix", "warn")}


def picks_table(sport):
    """Carte « Picks du jour » des vues Picks foot / NBA : memes paliers que l'onglet Aujourd'hui, filtres sur le sport.
    Seules ces lignes portent data-np (notification « nouveaux picks ») : la selection brute du simulateur NBA a perdu
    25 u sur 740 picks en reel (05/11/2026) et ne doit plus etre signalee comme un pick."""
    now = datetime.now(timezone.utc)
    try:
        value, safe, best = _tiers(now)
    except Exception as e:
        print(f"  [!] picks du jour ({sport}) : {type(e).__name__}: {e}")
        return ""
    ico = "⚽" if sport == "foot" else "🏀"
    rows = ""
    for x in sorted((x for x in value + safe + best if x["sport"] == sport), key=lambda x: x["ko"]):
        lab, cls = TIER_TAG[x["kind"]]
        ev = (f'<span class="eb-acc">+{_fr(100 * x["ev"], 1)} %</span>' if x["ev"] >= 0
              else f'<span class="eb-dim">coût {_fr(100 * -x["ev"], 1)} %</span>')
        rows += (f'<tr data-np="pj|{_e(x["kind"])}|{_e(x["match_id"])}|{_e(x["label"])}" '
                 f'data-npl="{ico} {_e(x["match"])} · {_e(x["label"])} @ {_fr(x["price"])} ({lab})">'
                 f'<td class="n eb-dim">{_day(x["ko"], now)}{_hm(x["ko"])}</td><td><b>{_e(x["match"])}</b> '
                 f'<span class="eb-dim">{_e(x["league"])}</span></td><td><b>{_e(x["label"])}</b> '
                 f'<span class="eb-tag {cls}">{lab}</span></td><td>{_e(x["book"])}</td><td class="n"><b>{_fr(x["price"])}</b></td>'
                 f'<td class="n">{_pc(x["p"])}</td><td class="n">{ev}</td></tr>')
    table = (f'<div class="eb-scroll"><table class="eb-tbl eb-cards"><tr><th>Heure</th><th>Match</th><th>Pari</th>'
             f'<th>Bookmaker</th><th>Cote</th><th>Gagné</th><th>Espérance</th></tr>{rows}</table></div>' if rows else
             '<div class="eb-empty">Aucun pari ne remplit les critères pour les 24 prochaines heures. Mieux vaut ne rien '
             'jouer que jouer un pari perdant sur la durée.</div>')
    return ('<div class="eb-card"><h3 class="eb-h">🎯 Picks du jour <small>value · safe · meilleur prix, probabilités '
            'recalées sur les résultats réels</small></h3>' + table +
            '<p class="eb-note">Les mêmes que l’onglet Aujourd’hui et le canal Telegram : value = espérance positive contre la '
            'cote juste Pinnacle ; safe = cote ≥ 1,40 gagnée au moins 2 fois sur 3, marge faible ; meilleur prix = pas de la '
            'value, la marge la plus faible du jour, simulation d’accord. Seules ces lignes déclenchent la notification '
            '« nouveaux picks ».</p></div>')


def _picks_card(now, sims, nba):
    value, safe, best = _tiers(now)
    out = ('<div class="eb-card"><h3 class="eb-h">🎯 Picks du jour <small>24 prochaines heures · probabilités recalées '
           'sur les résultats réels</small></h3>')
    out += '<div class="td-tier">Value <span class="eb-tag acc">espérance positive</span></div>'
    out += ("".join(_pick_row(x, sims, nba) for x in value) if value else
            '<p class="td-none">Aucun pari ne bat la marge du bookmaker en ce moment. Mieux vaut ne rien jouer que jouer '
            'un pari perdant sur la durée.</p>')
    out += _tier_stats(("value", "prop"))
    if safe:
        out += ('<div class="td-tier">🛡️ Safe <span class="eb-tag">cote ≥ 1,40 · gagné ≥ 2 fois sur 3</span></div>'
                + "".join(_pick_row(x, sims, nba) for x in safe)
                + '<p class="eb-note">Safe ne veut pas dire sûr : ils se perdent environ 1 fois sur 3.</p>' + _tier_stats(("safe",)))
    if best:
        m = PJ.fr_margin()
        out += ('<div class="td-tier">💡 Meilleurs prix <span class="eb-tag">pas de la value : marge la plus faible</span></div>'
                + "".join(_pick_row(x, sims, nba) for x in best)
                + (f'<p class="eb-note">Un pari français moyen coûte {_fr(100 * -m, 1)} % aujourd’hui ; ceux-ci coûtent '
                   'moins, et ma simulation est d’accord.</p>' if m else "") + _tier_stats(("best",)))
    return out + ('<p class="eb-note">Recalculés à chaque mise à jour avec les cotes du moment ; le canal Telegram poste '
                  'la sélection de 20 h. Vérifie la cote chez ton bookmaker avant de jouer.</p></div>')


def _matches_card(now, sims, nba):
    rows = []
    for g in sims:
        ko = _t(g["ko"])
        if not -timedelta(hours=2) <= ko - now <= timedelta(hours=24): continue
        p = g.get("p") or {}
        pr = (f'1 {_pc(p["1"])} · X {_pc(p["X"])} · 2 {_pc(p["2"])}' if all(k in p for k in ("1", "X", "2")) else "")
        rows.append((ko, "⚽", g.get("league_name", ""), f'{g["home"]["name"]} - {g["away"]["name"]}', pr, ("foot", g["id"])))
    for g in nba:
        ko = _t(g["date"])
        if not -timedelta(hours=3) <= ko - now <= timedelta(hours=24): continue
        ph = (g.get("team") or {}).get("p_home_win")
        pr = f'{g["home"]["abbr"]} {_pc(ph)} · {g["away"]["abbr"]} {_pc(1 - ph)}' if ph is not None else ""
        rows.append((ko, "🏀", "NBA", f'{g["away"]["name"]} @ {g["home"]["name"]}', pr, ("nba", g["gid"])))
    if not rows:
        return ('<div class="eb-card"><h3 class="eb-h">📅 Matchs du jour</h3><div class="eb-empty">Aucun match suivi '
                'dans les 24 prochaines heures.</div></div>')
    body = ""
    for ko, ico, lg, name, pr, link in sorted(rows, key=lambda r: r[0]):
        live = ko <= now
        when = ('<span class="td-live">en cours</span>' if live else
                f'{_day(ko.isoformat(), now)}{ko.astimezone(TZ):%H:%M}')
        body += (f'<div class="td-m"><span class="td-when">{when}</span><span class="td-mn">{ico} <b>{_e(name)}</b>'
                 f'<span class="eb-dim"> · {_e(lg)}</span><br><span class="td-pr">{pr}</span></span>{_go(link, "Simu")}</div>')
    n_f = sum(r[1] == "⚽" for r in rows)
    return (f'<div class="eb-card td-mcard"><h3 class="eb-h">📅 Matchs du jour <small>{n_f} foot · {len(rows) - n_f} NBA · '
            f'probabilités de ma simulation</small></h3>{body}</div>')


def _bank_card():
    try:
        import nba_bankroll as BK
        import nba_bankroll_site as BS
        st = BK.load()
        eq, start = BK.equity(st), st["start"]
    except Exception:
        return ""
    open_ = [b for b in st["bets"] if b["status"] == "open"]
    done = [b for b in st["bets"] if b["status"] in ("won", "lost")]
    w = sum(b["status"] == "won" for b in done)
    rows = "".join(f'<div class="td-bet"><span>{"<br>".join(BS._leg(l) for l in b["legs"])}</span>'
                   f'<span class="td-odds">{BS._eur(b["stake"])} @ {_fr(b["odds"])}</span></div>' for b in open_)
    return ('<div class="eb-card"><h3 class="eb-h">🤖 Bankroll IA <small>mes paris, gérés seul</small></h3><div class="eb-stats">'
            + BS._stat("Capital", BS._eur(eq), f'{BS._pct(eq / start - 1, True)} depuis {BS._eur(start)}',
                       "eb-acc" if eq >= start else "eb-neg")
            + BS._stat("En jeu", BS._eur(sum(b["stake"] for b in open_)), f'{len(open_)} pari{"s" if len(open_) > 1 else ""} en cours')
            + BS._stat("Paris réglés", f'{w}-{len(done) - w}') + '</div>'
            + (f'<div class="td-bets">{rows}</div>' if rows else '<p class="td-none">Aucun pari en cours.</p>')
            + '<button class="td-go td-more" onclick="spNav(\'iabank\')">Toute la Bankroll IA →</button></div>')


def _calib_pairs():
    """(sport, probabilite annoncee, issue) : simulations archivees et reglees (foot : 1X2, plus de 2,5, les deux
    marquent ; NBA : victoire)."""
    out = []
    f = (PJ._load("data/foot_sim_history.json", {}) or {}).get("games") or {}
    for g in (f.values() if isinstance(f, dict) else f):
        fin, p = g.get("final"), g.get("p") or {}
        if not fin or not all(k in p for k in ("1", "X", "2", "o25", "btts")): continue
        h, a = fin[0], fin[1]
        out += [("foot", p["1"], h > a), ("foot", p["X"], h == a), ("foot", p["2"], h < a),
                ("foot", p["o25"], h + a > 2), ("foot", p["btts"], h > 0 and a > 0)]
        try:   # meme prevision, recalee (foot_calib, parametres appris sur les backtests : jamais vu ces matchs)
            import foot_calib
            c = foot_calib.apply({"1": p["1"], "X": p["X"], "2": p["2"], "btts": p["btts"], "over": {"2.5": p["o25"]}})
            out += [("footcal", c["1"], h > a), ("footcal", c["X"], h == a), ("footcal", c["2"], h < a),
                    ("footcal", c["over"]["2.5"], h + a > 2), ("footcal", c["btts"], h > 0 and a > 0)]
        except Exception:
            pass
    n = (PJ._load("data/nba_sim_history.json", {}) or {}).get("games") or {}
    for g in (n.values() if isinstance(n, dict) else n):
        fin, s = g.get("final"), g.get("sim") or {}
        if not fin or s.get("p_home") is None: continue
        h, a = (fin["home"], fin["away"]) if isinstance(fin, dict) else (fin[0], fin[1])
        out += [("nba", s["p_home"], h > a), ("nba", 1 - s["p_home"], a > h)]
    return out


def _bins(pairs, sport, min_n=10):
    B = []
    for lo in (0, .2, .4, .6, .8):
        L = [(p, o) for s, p, o in pairs if s == sport and lo <= p < lo + .2 + (1e-9 if lo == .8 else 0)]
        if len(L) < min_n: continue
        mp, ob = sum(p for p, _ in L) / len(L), sum(o for _, o in L) / len(L)
        B.append((mp, ob, len(L), 1.96 * (max(ob * (1 - ob), .01) / len(L)) ** .5))
    return B


def _calib_svg(series):
    W, H, P = 320, 250, 36
    X = lambda v: P + v * (W - P - 12)
    Y = lambda v: H - P + 6 - v * (H - P - 12)
    s = (f'<svg viewBox="0 0 {W} {H}" class="td-svg" role="img" aria-label="Probabilité annoncée contre fréquence observée">'
         + "".join(f'<line x1="{X(v):.1f}" y1="{Y(0):.1f}" x2="{X(v):.1f}" y2="{Y(1):.1f}" style="stroke:var(--line)"/>'
                   f'<line x1="{X(0):.1f}" y1="{Y(v):.1f}" x2="{X(1):.1f}" y2="{Y(v):.1f}" style="stroke:var(--line)"/>'
                   f'<text x="{X(v):.1f}" y="{H - 12}" class="td-ax" text-anchor="middle">{round(100 * v)}</text>'
                   f'<text x="{P - 6}" y="{Y(v) + 4:.1f}" class="td-ax" text-anchor="end">{round(100 * v)}</text>'
                   for v in (0, .2, .4, .6, .8, 1))
         + f'<line x1="{X(0)}" y1="{Y(0)}" x2="{X(1)}" y2="{Y(1)}" style="stroke:var(--mu);stroke-dasharray:4 4"/>')
    for k, (lab, col, B) in enumerate(series):
        dx = (k - (len(series) - 1) / 2) * 0.012
        pts = " ".join(f"{X(mp + dx):.1f},{Y(ob):.1f}" for mp, ob, n, ci in B)
        s += f'<polyline points="{pts}" style="fill:none;stroke:{col};stroke-width:2;opacity:.55"/>'
        for mp, ob, n, ci in B:
            s += (f'<line x1="{X(mp + dx):.1f}" y1="{Y(max(0, ob - ci)):.1f}" x2="{X(mp + dx):.1f}" y2="{Y(min(1, ob + ci)):.1f}" '
                  f'style="stroke:{col};stroke-width:1.5;opacity:.7"/><circle cx="{X(mp + dx):.1f}" cy="{Y(ob):.1f}" r="4.5" '
                  f'style="fill:{col}"><title>{lab} : annoncé {round(100 * mp)} %, observé {round(100 * ob)} % '
                  f'(± {round(100 * ci)}, {n} cas)</title></circle>')
    return s + '</svg>'


def _curve_svg(lines):
    """Gains cumules (unites) par palier, pari apres pari."""
    W, H, P = 320, 170, 34
    allv = [v for _, _, L in lines for v in L] + [0]
    lo, hi = min(allv), max(allv)
    hi, lo = (hi + 1, lo - 1) if hi == lo else (hi, lo)
    nmax = max(len(L) for _, _, L in lines)
    X = lambda i: P + i * (W - P - 10) / max(nmax, 1)
    Y = lambda v: 10 + (hi - v) * (H - P - 4) / (hi - lo)
    s = (f'<svg viewBox="0 0 {W} {H}" class="td-svg" role="img" aria-label="Gains cumulés par palier">'
         f'<line x1="{P}" y1="{Y(0):.1f}" x2="{W - 10}" y2="{Y(0):.1f}" style="stroke:var(--mu);stroke-dasharray:4 4"/>'
         f'<text x="{P - 6}" y="{Y(hi) + 4:.1f}" class="td-ax" text-anchor="end">{_fr(hi, 1)}</text>'
         f'<text x="{P - 6}" y="{Y(lo) + 4:.1f}" class="td-ax" text-anchor="end">{_fr(lo, 1)}</text>'
         f'<text x="{P - 6}" y="{Y(0) + 4:.1f}" class="td-ax" text-anchor="end">0</text>')
    for lab, col, L in lines:
        pts = " ".join(f"{X(i):.1f},{Y(v):.1f}" for i, v in enumerate([0] + L))
        s += f'<polyline points="{pts}" style="fill:none;stroke:{col};stroke-width:2.2"><title>{lab}</title></polyline>'
    return s + '</svg>'


def _bilan_card():
    TIERS = (("Value", "var(--acc)", ("value", "prop")), ("Safe", "#60a5fa", ("safe",)), ("Meilleur prix", "var(--warn)", ("best",)))
    done = sorted((p for p in PJ.load()["picks"] if p.get("result") in ("win", "loss")), key=lambda p: p["ko"])
    lines, legend = [], ""
    for lab, col, kinds in TIERS:
        cum, L = 0.0, []
        for p in done:
            if p.get("kind") in kinds: cum += p["units"]; L.append(round(cum, 2))
        s = PJ.stats(kinds)
        if L: lines.append((lab, col, L))
        legend += (f'<span class="td-lg"><i style="background:{col}"></i>{lab} : ' + (
            f'{s["w"]}-{s["n"] - s["w"]}, <b>{"+" if s["units"] >= 0 else ""}{_fr(s["units"], 1)} u</b> '
            f'(ROI {100 * s["roi"]:+.0f} %)' + (f' · CLV {100 * s["clv"]:+.1f} %'.replace(".", ",") if s.get("clv") is not None else "")
            if s else '<span class="eb-dim">pas encore de pari réglé</span>') + '</span>')
    curve = (_curve_svg(lines) if lines else
             '<p class="td-none">La courbe démarre au premier pari réglé du canal (picks postés depuis le 04/11).</p>')
    pairs = _calib_pairs()
    fb, nb, cb = _bins(pairs, "foot"), _bins(pairs, "nba"), _bins(pairs, "footcal")
    nf, nn = sum(s == "foot" for s, _, _ in pairs), sum(s == "nba" for s, _, _ in pairs)
    flags = []
    for lab, B in (("foot brut", fb), ("foot recalé", cb), ("NBA", nb)):
        for mp, ob, n, ci in B:
            if n >= 30 and abs(ob - mp) > ci:
                flags.append(f'{lab} : annoncé {round(100 * mp)} %, arrivé {round(100 * ob)} % ({n} cas) → '
                             + ("trop confiante" if ob < mp else "trop prudente"))
    calib = ""
    if fb or nb:
        calib = ('<div class="td-sub">Fiabilité de ma simulation <small>probabilité annoncée (horizontal) contre '
                 'fréquence réelle (vertical)</small></div>'
                 + '<div class="td-cal"><div class="td-cal-g">'
                 + _calib_svg([s for s in (("Foot brut", "var(--mu)", fb), ("Foot recalé", "var(--acc)", cb),
                                           ("NBA", "var(--warn)", nb)) if s[2]])
                 + '</div><div class="td-cal-t"><div class="td-lgs"><span class="td-lg"><i style="background:var(--mu)"></i>Foot brut · '
                 f'{nf} prévisions (1X2, +2,5 buts, les deux marquent)</span><span class="td-lg"><i style="background:var(--acc)">'
                 '</i>Foot recalé · ce qui est affiché depuis le 05/11</span><span class="td-lg"><i style="background:var(--warn)">'
                 f'</i>NBA · {nn} prévisions (vainqueur)</span></div>'
                 '<p class="eb-note">Sur la diagonale = fiable. Barre = marge d’erreur à 95 % (peu de matchs : elle est large). '
                 + ("Écart net : " + " ; ".join(flags) + ". " if flags else "Aucun écart net à ce jour. ")
                 + ("La version recalée, affichée depuis le 05/11, corrige cet écart (courbe verte). "
                    if any(f.startswith("foot brut") for f in flags) and not any(f.startswith("foot recalé") for f in flags) else "")
                 + 'Les picks du jour n’utilisent pas ces pourcentages bruts : ils partent de la cote juste Pinnacle, '
                 'recalée sur les résultats réels.</p></div></div>')
    return ('<div class="eb-card"><h3 class="eb-h">📈 Bilan et fiabilité <small>est-ce que ça marche ?</small></h3>'
            '<div class="td-sub">Canal Picks : gains cumulés par palier <small>1 unité par pari</small></div>'
            f'{curve}<div class="td-lgs">{legend}</div>'
            '<p class="eb-note">CLV = cote prise comparée à la dernière cote juste avant le match : positive, on bat le marché '
            '(meilleur signe à long terme que le résultat de quelques paris).</p>'
            f'{calib}</div>')


CSS = """<style>
.td-svg{width:100%;max-width:520px;height:auto;display:block;margin:8px 0 4px}
.td-ax{font:10px var(--mono);fill:var(--mu)}
.td-sub{font-weight:600;font-size:14.5px;margin:14px 0 2px}
.td-sub:first-of-type{margin-top:0}
.td-sub small{font:11.5px var(--mono);color:var(--mu);font-weight:400;margin-left:6px}
.td-lgs{display:flex;flex-direction:column;gap:4px;font-size:12.5px;color:var(--mu)}
.td-cal{display:flex;flex-wrap:wrap;gap:4px 22px;align-items:center}
.td-cal-g{flex:1 1 280px;max-width:460px;min-width:0}
.td-cal-t{flex:1 1 220px;min-width:0}
.td-lg i{display:inline-block;width:10px;height:10px;border-radius:3px;margin-right:6px;vertical-align:-1px}
.td-head{display:flex;justify-content:space-between;align-items:baseline;gap:10px;flex-wrap:wrap;margin:0 0 14px}
.td-head h2{font-size:22px;font-weight:600;margin:0;letter-spacing:-.4px}
.td-grid{display:grid;grid-template-columns:minmax(0,1.25fr) minmax(0,1fr);gap:16px;align-items:start}
.td-col{display:flex;flex-direction:column;gap:16px;min-width:0}
.td-tier{font-weight:600;font-size:15px;margin:16px 0 4px}
.td-tier:first-of-type{margin-top:0}
.td-pick{padding:10px 0;border-top:1px solid var(--line)}
.td-l1{display:flex;justify-content:space-between;align-items:center;gap:8px;font-size:13.5px}
.td-l2{font-size:15px;margin:4px 0 2px}
.td-l3{font-size:12.5px;color:var(--mu)}
.td-odds{font-family:var(--mono);font-weight:600;color:var(--acc);white-space:nowrap}
.td-none{color:var(--mu);font-size:13.5px;margin:6px 0}
.td-go{border:1px solid var(--line2);background:var(--chip);color:var(--tx);border-radius:8px;padding:5px 10px;font:600 12px var(--sans);cursor:pointer;white-space:nowrap;flex-shrink:0}
.td-go:hover{border-color:var(--acc);color:var(--acc)}
.td-more{margin-top:12px}
.td-m{display:flex;align-items:center;gap:10px;padding:9px 0;border-top:1px solid var(--line);font-size:13.5px}
.td-m:first-of-type{border-top:0}
.td-when{font-family:var(--mono);font-size:12px;color:var(--mu);min-width:62px}
.td-mn{flex:1;min-width:0}
.td-pr{font-family:var(--mono);font-size:11.5px;color:var(--mu)}
.td-live{color:var(--loss);font-weight:700}
.td-bets{margin-top:12px}
.td-bet{display:flex;justify-content:space-between;gap:10px;padding:8px 0;border-top:1px solid var(--line);font-size:13px}
@media (max-width:900px){.td-grid{grid-template-columns:1fr}}
/* ordinateur : la liste des matchs (longue les soirs de coupe d'Europe) defile dans sa colonne, a hauteur d'ecran,
   au lieu d'allonger la page et de laisser un vide sous la colonne de gauche (Wilson, 05/11/2026) */
@media (min-width:901px){.td-grid>.td-col:last-child{position:sticky;top:12px}
  .td-mcard{max-height:calc(100vh - 24px);overflow-y:auto;scrollbar-width:thin}}
</style>"""

SCRIPT = """<script>
window.spToday=function(sp,id){
  if(sp==='foot'){spNav('foot','footsim');if(window.ebPick)ebPick('foot2',id);}
  else{spNav('basket','nbasim');if(window.ebPick)ebPick('nba',id);}
  setTimeout(function(){var d=document.querySelector('[data-eb="'+(sp==='foot'?'foot2':'nba')+'"] .eb-detail.on');
    if(d)d.scrollIntoView({behavior:'smooth',block:'start'});},120);
};
</script>"""


def _safe(f):
    try:
        return f()
    except Exception as e:
        print(f"  [!] Aujourd'hui : {f.__name__} indisponible ({type(e).__name__}: {e})")
        return ""


def build_today_section(now=None):
    now = now or datetime.now(timezone.utc)
    sims, nba = _games()
    loc = now.astimezone(TZ)
    try:
        picks = _picks_card(now, sims, nba)
    except Exception as e:
        print(f"  [!] Aujourd'hui : picks du jour indisponibles ({type(e).__name__}: {e})")
        picks = '<div class="eb-card"><h3 class="eb-h">🎯 Picks du jour</h3><div class="eb-empty">Indisponibles pour le moment.</div></div>'
    stamp = now.isoformat(timespec="seconds")
    return (f'<!--today:{stamp}--><!--eb:start--><div class="eb">'
            f'<div class="td-head"><h2>{JOURS[loc.weekday()].capitalize()} {loc.day} {MOIS[loc.month - 1]}</h2>'
            f'<span class="eb-meta">mis à jour à {loc:%H:%M}</span></div>'
            f'<div class="td-grid"><div class="td-col">{picks}{_bank_card()}{_safe(_bilan_card)}</div>'
            f'<div class="td-col">{_matches_card(now, sims, nba)}</div></div></div>'
            + CSS + SCRIPT + '<!--eb:end-->')


def write_fragment(sec=None):
    with open(FRAGMENT, "w", encoding="utf-8") as f:
        f.write(sec if sec is not None else build_today_section())


def build_today_page():
    """(bloc pour index.html, contenu de today.html) : copie de secours + chargeur."""
    sec = build_today_section()
    v = sec[len("<!--today:"):sec.index("-->")]
    return f'<div id="eb-today-wrap" data-v="{_e(v)}">{sec}</div>' + LOADER, sec


LOADER = """<script>
(function(){
  var wrap=document.getElementById('eb-today-wrap');if(!wrap||window.ebTodayLive)return;
  var last=0;
  function run(el){el.querySelectorAll('script:not([type])').forEach(function(s){var n=document.createElement('script');
    n.textContent=s.textContent;s.parentNode.replaceChild(n,s);});}
  window.ebTodayLive=function(){
    last=Date.now();
    fetch('""" + FRAGMENT + """?t='+Date.now(),{cache:'no-store'}).then(function(r){if(!r.ok)throw 0;return r.text();})
      .then(function(h){var m=/^<!--today:([^>]*)-->/.exec(h);
        if(!m||m[1]<=(wrap.getAttribute('data-v')||''))return;
        wrap.innerHTML=h;wrap.setAttribute('data-v',m[1]);run(wrap);})
      .catch(function(){});
  };
  ebTodayLive();
  document.addEventListener('visibilitychange',function(){
    if(document.visibilityState==='visible'&&Date.now()-last>120000)ebTodayLive();});
})();
</script>"""
