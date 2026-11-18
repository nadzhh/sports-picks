"""
nba_results.py — Basket > Résultats : matchs NBA termines depuis moins de 24 h (Wilson, 18/11/2026 : « voir les
matchs qui se sont joues il y a moins de 24 h avec les scores ; si je clique, les scores par quart-temps, les
details, les stats joueurs par rapport aux props : Doncic, la prop etait a 34,5 pts, il a mis 35 -> ok »).

Sources (aucun appel a l'Odds API) :
  - base des feuilles de match (nba_boxdb, tenue a jour par la veille) : score, quarts-temps, stats d'equipe et joueurs ;
  - lignes DraftKings d'avant-match conservees par ESPN (nba_props_hist.fetch_game_props, 1 appel par match termine,
    une seule fois) + probabilite de notre simulation relevee dans data/nba_sim.json avant le match ;
  - nos paris regles : picks du jour (data/telegram_picks.json), selection du simulateur (data/nba_sim_picks.json),
    props du soir (data/props_edge_track.json) ; simulation du score (data/nba_sim_history.json).

refresh() (reseau, appele par la veille nba_live) tient data/nba_results.json ; view() (sans reseau) rend la vue.
"""
import html as H
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

PATH = Path("data/nba_results.json")
WINDOW = timedelta(hours=24)                         # fin de match il y a moins de 24 h
GAME_LEN = timedelta(hours=2, minutes=30)            # duree d'un match (fin estimee)
KEEP = timedelta(days=4)
MAX_TRIES = 4
LABEL = {"PTS": "Points", "REB": "Rebonds", "AST": "Passes", "3PM": "Paniers à 3 pts", "PRA": "Pts+Reb+Pas",
         "PR": "Pts+Reb", "PA": "Pts+Pas", "RA": "Reb+Pas", "STL": "Interceptions", "BLK": "Contres", "TO": "Pertes"}
COMP = {"PTS": ("PTS",), "REB": ("REB",), "AST": ("AST",), "3PM": ("3PM",), "STL": ("STL",), "BLK": ("BLK",),
        "TO": ("TO",), "PRA": ("PTS", "REB", "AST"), "PR": ("PTS", "REB"), "PA": ("PTS", "AST"), "RA": ("REB", "AST")}
ORDER = ("PTS", "REB", "AST", "PRA", "PR", "PA", "RA", "3PM", "STL", "BLK", "TO")
SIMCOL = {"PTS": 4, "REB": 5, "AST": 6, "3PM": 7, "STL": 8, "BLK": 9}   # nba_sim_history players[id]
JOURS = ("Lundi", "Mardi", "Mercredi", "Jeudi", "Vendredi", "Samedi", "Dimanche")


def _e(s):
    return H.escape(str(s if s is not None else ""))


def _fr(x, d=1):
    return f"{x:.{d}f}".replace(".", ",")


def _t(iso):
    return datetime.fromisoformat(str(iso).replace("Z", "+00:00"))


def _load(p, default):
    try:
        return json.loads(Path(p).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def _tz():
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo("Europe/Paris")
    except Exception:
        return timezone(timedelta(hours=1))


def load():
    return _load(PATH, {"games": {}})


def _save(st):
    tmp = str(PATH) + ".tmp"
    Path(tmp).write_text(json.dumps(st, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    os.replace(tmp, PATH)


def _ko(gid, g, hist):
    """Coup d'envoi (UTC) : simulation archivee, sinon date US de la feuille de match + 1 h UTC le lendemain."""
    h = hist.get(gid) or {}
    if h.get("date"): return _t(h["date"])
    d = datetime.strptime(g["date"][:10], "%Y-%m-%d").replace(tzinfo=timezone.utc)
    return d + timedelta(days=1, hours=1)


def finished(now=None, db=None, hist=None):
    """[(gid, feuille, coup d'envoi)] des matchs termines depuis moins de 24 h, du plus recent au plus ancien ;
    a defaut (journee sans match), ceux de la derniere soiree jouee."""
    import nba_boxdb as B
    now = now or datetime.now(timezone.utc)
    if db is None:
        y = B.season_year()
        db = {**B.load_season(y - 1), **B.load_season(y)} if now.month in (10,) else B.load_season(y)
    hist = hist if hist is not None else (_load("data/nba_sim_history.json", {}).get("games") or {})
    rows = [(gid, g, _ko(gid, g, hist)) for gid, g in db.items()
            if g.get("type") in (None, "regular", "postseason", "playoffs") and g.get("home", {}).get("players")]
    out = [r for r in rows if timedelta(0) <= now - (r[2] + GAME_LEN) <= WINDOW]
    if not out and rows:
        last = max(r[1]["date"][:10] for r in rows)
        out = [r for r in rows if r[1]["date"][:10] == last]
    return sorted(out, key=lambda r: (r[2], r[0]), reverse=True)


def refresh(now=None):
    """Releve les lignes du soir (simulation) et recupere les lignes DraftKings des matchs termines. True si change."""
    now = now or datetime.now(timezone.utc)
    st = load()
    G = st.setdefault("games", {})
    changed = False
    # 1. avant le match : probabilite de notre simulation pour chaque prop affichee (data/nba_sim.json)
    for g in (_load("data/nba_sim.json", {}).get("games") or []):
        if not g.get("props"): continue
        try:
            if _t(g["date"]) <= now: continue          # commence : on garde le dernier releve d'avant-match
        except (KeyError, ValueError):
            continue
        sim = {f'{p["id"]}|{p["stat"]}': [p["line"], p.get("over"), p.get("under"), p.get("p_sim")]
               for p in g["props"] if p.get("p_sim") is not None}
        e = G.setdefault(str(g["gid"]), {})
        if e.get("sim") != sim:
            e.update(sim=sim, ko=g["date"]); changed = True
    # 2. apres le match : toutes les lignes principales DraftKings conservees par ESPN (une fois)
    try:
        todo = finished(now)
    except Exception as ex:
        print(f"  [!] resultats NBA : {type(ex).__name__}: {ex}"); todo = []
    for gid, g, ko in todo:
        e = G.setdefault(gid, {})
        if e.get("lines") or e.get("tries", 0) >= MAX_TRIES: continue
        e["ko"] = e.get("ko") or ko.strftime("%Y-%m-%dT%H:%MZ")
        e["tries"] = e.get("tries", 0) + 1; changed = True
        try:
            from nba_props_hist import fetch_game_props, props_main_lines
            gp = fetch_game_props(gid)
            m = props_main_lines(gp) if gp else {}
        except Exception as ex:
            print(f"  [!] lignes {gid} : {type(ex).__name__}: {ex}"); continue
        if m:
            e["book"] = gp.get("book", "")
            e["lines"] = {f"{aid}|{s}": [v["line"], v["over"], v["under"]] for (aid, s), v in m.items()}
            print(f"  [resultats] {g['away']['abbr']} @ {g['home']['abbr']} : {len(m)} lignes {e['book']}")
    # 3. menage
    for gid in [k for k, e in G.items() if e.get("ko") and now - _t(e["ko"]) > KEEP]:
        del G[gid]; changed = True
    if changed: _save(st)
    return changed


# ── Rendu ───────────────────────────────────────────────────────────────────

def _val(r, stat):
    return sum((r.get(k) or 0) for k in COMP[stat])


def _our_bets(gid):
    """{(pid, stat, side): [(origine, detail)]} et paris hors props, pour ce match."""
    props, other = {}, []
    for x in (_load("data/telegram_picks.json", {}).get("picks") or []):
        if x.get("sport") != "nba" or str(x.get("match_id")) != gid: continue
        r = x.get("res") or {}
        tag = {"value": "Value", "prop": "Value", "safe": "Safe", "boost": "Boost", "best": "Meilleur prix",
               "fill": "Complément", "coup": "Bon coup"}.get(x.get("kind"), x.get("kind", ""))
        item = ("🎯 Pick du jour · " + tag, x)
        if r.get("pid") and r.get("stat"):
            props.setdefault((str(r["pid"]), r["stat"], r.get("side")), []).append(item)
        else:
            other.append(item)
    for b in (_load("data/nba_bankroll.json", {}).get("bets") or []):   # Bankroll IA : jambes sur ce match
        for leg in b.get("legs") or []:
            pj = leg.get("pj") or {}
            if str(pj.get("match_id") or leg.get("gid") or "") != gid: continue
            r = pj.get("res") or {}
            x = {"label": pj.get("label") or leg.get("stat", ""), "price": leg.get("dec"), "result": leg.get("result"),
                 "line": r.get("line"), "name": None, "actual": leg.get("actual"),
                 "stake": b.get("stake"), "combo": len(b.get("legs") or []) > 1}
            item = ("💰 Bankroll IA" + (" · combiné" if x["combo"] else ""), x)
            if r.get("pid") and r.get("stat"):
                props.setdefault((str(r["pid"]), r["stat"], r.get("side")), []).append(item)
            else:
                other.append(item)
    for x in (_load("data/nba_sim_picks.json", {}).get("picks") or {}).values():
        if str(x.get("gid")) == gid:
            props.setdefault((str(x["id"]), x["stat"], x.get("side")), []).append(("🤖 Sélection de la simu", x))
    for x in (_load("data/props_edge_track.json", {}).get("props") or []):
        if str(x.get("gid")) == gid:
            props.setdefault((str(x["id"]), x["stat"], x.get("side")), []).append(("📊 Props du soir", x))
    return props, other


def _res_tag(res):
    if res == "win": return '<span class="eb-tag acc">✅ gagné</span>'
    if res == "loss": return '<span class="eb-tag neg">❌ perdu</span>'
    if res == "push": return '<span class="eb-tag">remboursé</span>'
    return '<span class="eb-tag">en attente</span>'


def _quarters(g):
    a, h = g["away"], g["home"]
    n = max(len(a.get("quarters") or []), len(h.get("quarters") or []))
    heads = "".join(f"<th class='num'>{'QT' + str(i + 1) if i < 4 else 'Prol.' + (str(i - 3) if n > 5 else '')}</th>"
                    for i in range(n))
    row = lambda t, o: (f'<tr><td><b>{_e(t["abbr"])}</b> <span class="eb-dim">{_e(t.get("name", ""))}</span></td>'
                        + "".join(f'<td class="n num">{q}</td>' for q in (t.get("quarters") or []))
                        + f'<td class="n num"><b class="{"eb-acc" if t["pts"] > o["pts"] else ""}">{t["pts"]}</b></td></tr>')
    return (f'<div class="eb-card"><h3 class="eb-h">Score par quart-temps</h3><div class="eb-scroll"><table class="eb-tbl">'
            f'<tr><th>Équipe</th>{heads}<th class="num">Total</th></tr>{row(a, h)}{row(h, a)}</table></div></div>')


def _sim_vs_real(g, h):
    """Simulation du score et lignes Vegas confrontees au resultat."""
    a, ho = g["away"], g["home"]
    lines = []
    if h.get("sim"):
        s = h["sim"]; ph = s.get("p_home") or 0.5
        fav = ho if ph >= 0.5 else a
        win = ho if ho["pts"] > a["pts"] else a
        lines.append(f'Ma simulation : <b>{_e(a["abbr"])} {_fr(s["away"], 0)} – {_fr(s["home"], 0)} {_e(ho["abbr"])}</b>, '
                     f'{_e(fav["abbr"])} gagnant à {round(100 * max(ph, 1 - ph))} % → '
                     + ('<b class="eb-acc">bon vainqueur</b>' if fav is win else '<b class="eb-neg">raté</b>')
                     + f' ; total simulé {_fr(s["home"] + s["away"], 1)}, réel {a["pts"] + ho["pts"]}.')
    L = g.get("lines") or {}
    sp = L.get("home_spread") if L.get("home_spread") is not None else (h.get("vegas") or {}).get("spread")
    tot = L.get("total") if L.get("total") is not None else (h.get("vegas") or {}).get("total")
    if sp is not None:
        m = ho["pts"] - a["pts"] + sp
        who = (f'{_e(ho["abbr"])} couvre' if m > 0 else f'{_e(a["abbr"])} couvre' if m < 0 else "égalité")
        lines.append(f'Handicap {L.get("book") or "Vegas"} : {_e(ho["abbr"])} {sp:+g} → <b>{who}</b> '
                     f'(écart réel {ho["pts"] - a["pts"]:+d} pour {_e(ho["abbr"])}).')
    if tot is not None:
        t = a["pts"] + ho["pts"]
        lines.append(f'Total {tot:g} → <b>{"plus" if t > tot else "moins" if t < tot else "pile"}</b> ({t} points).')
    if not lines: return ""
    return ('<div class="eb-card"><h3 class="eb-h">Simulation et marché face au résultat</h3>'
            + "".join(f'<p class="nr-p">{x}</p>' for x in lines) + '</div>')


def _our_card(g, props, other, players):
    rows = ""
    real = lambda o: o.startswith("🎯") or o.startswith("💰")    # paris reellement joues
    flat = sorted(((k, o, x) for k, its in props.items() for o, x in its if real(o)),
                  key=lambda r: (0 if r[1].startswith("🎯") else 1, r[0][0], r[0][1]))
    track = {}
    for its in props.values():
        for o, x in its:
            if not real(o): track.setdefault(o, [0, 0])[0 if x.get("result") == "win" else 1] += x.get("result") in ("win", "loss")
    for (pid, stat, side), origin, x in flat:
        p = players.get(pid)
        if True:
            line = x.get("line") if x.get("line") is not None else (x.get("res") or {}).get("line")
            act = _val(p, stat) if p and stat in COMP and not p.get("dnp") else x.get("actual")
            name = x.get("name") or (p or {}).get("name") or x.get("label", "")
            lab = (f'{_e(name)} {"plus" if side == "o" else "moins"} de {line:g} {_e(LABEL.get(stat, stat).lower())}'
                   if line is not None else _e(x.get("label", "")))
            rows += (f'<tr><td>{_e(origin)}</td><td><b>{lab}</b></td>'
                     f'<td class="n num">{"" if act is None else _fr(act, 0)}</td><td>{_res_tag(x.get("result"))}</td></tr>')
    for origin, x in other:
        rows += (f'<tr><td>{_e(origin)}</td><td><b>{_e(x.get("label", ""))}</b> <span class="eb-dim">@ {_fr(x.get("price") or 0, 2)}</span></td>'
                 f'<td></td><td>{_res_tag(x.get("result"))}</td></tr>')
    suivi = " · ".join(f'{_e(o)} <b>{w}-{l}</b>' for o, (w, l) in sorted(track.items()) if w + l)
    suivi = (f'<p class="eb-note">Suivis sans mise : {suivi}. Ils sont repérés dans le tableau des props ci-dessous.</p>'
             if suivi else "")
    if not rows and not suivi: return ""
    table = ('<div class="eb-scroll"><table class="eb-tbl nr-wrap"><tr><th>Origine</th><th>Pari</th><th class="num">Réel</th>'
             '<th>Résultat</th></tr>' + rows + '</table></div>' if rows else
             '<p class="nr-p eb-dim">Aucun pick du jour ni pari de la Bankroll IA sur ce match.</p>')
    return '<div class="eb-card"><h3 class="eb-h">Nos paris sur ce match</h3>' + table + suivi + '</div>'


def _props_card(gid, g, e, h, props):
    lines = (e or {}).get("lines") or {}
    sim = (e or {}).get("sim") or {}
    if not lines and not sim:
        return ('<div class="eb-card"><h3 class="eb-h">Props du match</h3><p class="nr-p eb-dim">Lignes DraftKings pas '
                'encore récupérées (reprise au prochain passage de la veille).</p></div>')
    src = lines or {k: v[:3] for k, v in sim.items()}
    proj = h.get("players") or {}
    rows, stats_seen, n_over, n = "", [], 0, 0
    for side in ("away", "home"):
        T = g[side]
        pl = sorted((p for p in T.get("players") or [] if not p.get("dnp")), key=lambda p: -(p.get("MIN") or 0))
        for p in pl:
            first = True
            for stat in ORDER:
                v = src.get(f'{p["id"]}|{stat}')
                if not v: continue
                line, over, under = v[0], v[1], v[2]
                act = _val(p, stat)
                n += 1; n_over += act > line
                if stat not in stats_seen: stats_seen.append(stat)
                ps = sim.get(f'{p["id"]}|{stat}')
                p_sim = ps[3] if ps and ps[0] == line else None
                pj = proj.get(p["id"])
                mean = (sum(pj[SIMCOL[k]] or 0 for k in COMP[stat]) if pj and all(k in SIMCOL for k in COMP[stat]) else None)
                ours = "".join(f' <span class="eb-tag {"acc" if x.get("result") == "win" else "neg" if x.get("result") == "loss" else ""}" '
                               f'title="{_e(o)}">{_e(o.split(" · ")[0].split(" ")[0])} {"plus" if sd == "o" else "moins"}</span>'
                               for (pid, s, sd), its in props.items() if pid == p["id"] and s == stat for o, x in its)
                d = act - line
                res = (f'<span class="nr-ok">✅ Plus</span> <span class="eb-dim">(+{_fr(d, 1)})</span>' if d > 0 else
                       f'<span class="nr-ko">Moins</span> <span class="eb-dim">({_fr(d, 1)})</span>')
                odds = (f'{over:+d} / {under:+d}' if isinstance(over, int) and isinstance(under, int) else "")
                simtxt = ((f'{round(100 * p_sim)} % plus' if p_sim is not None else "")
                          + (f'{" · " if p_sim is not None else ""}proj. {_fr(mean, 1)}' if mean is not None else ""))
                who = (f'<b>{_e(p["name"])}</b> <span class="eb-dim">{_e(T["abbr"])} · {_fr(p.get("MIN") or 0, 0)} min</span>')
                rows += (f'<tr data-s="{stat}"><td class="{"" if first else "nr-rep"}">{who}</td>'
                         f'<td>{_e(LABEL[stat])}{ours}</td><td class="n num">{line:g}</td><td class="n num"><b>{act}</b></td>'
                         f'<td>{res}</td><td class="n eb-dim nr-x">{odds}</td><td class="n eb-dim nr-x">{simtxt}</td></tr>')
                first = False
    if not rows: return ""
    chips = ('<div class="nr-f"><button class="on" onclick="nrF(this,\'\')">Toutes</button>'
             + "".join(f'<button onclick="nrF(this,\'{s}\')">{_e(LABEL[s])}</button>' for s in ORDER if s in stats_seen) + '</div>')
    book = (e or {}).get("book") or "DraftKings"
    return (f'<div class="eb-card"><h3 class="eb-h">Props du match <small>{n} lignes {_e(book)} d’avant-match · '
            f'{n_over} passées au-dessus ({round(100 * n_over / n)} %)</small></h3>{chips}'
            '<div class="eb-scroll"><table class="eb-tbl nr-props"><tr><th>Joueur</th><th>Stat</th><th class="num">Ligne</th>'
            '<th class="num">Réel</th><th>Résultat</th><th class="nr-x">Cotes plus / moins</th><th class="nr-x">Ma simu</th></tr>'
            + rows + '</table></div><p class="eb-note">✅ Plus = la stat a dépassé la ligne. Les étiquettes 🎯, 🤖 et 📊 '
            'marquent nos paris (pick du jour, sélection de la simu, props du soir) : vertes si gagnés.</p></div>')


def _box(T, O):
    head = ('<tr><th>Joueur</th><th class="num">Min</th><th class="num">Pts</th><th class="num">Reb</th><th class="num">Pas</th>'
            '<th class="num">Tirs</th><th class="num">3 pts</th><th class="num">LF</th><th class="num">Int</th>'
            '<th class="num">Ctr</th><th class="num">BP</th><th class="num">+/-</th></tr>')
    rows, dnp = "", []
    pl = sorted(T.get("players") or [], key=lambda p: (not p.get("starter"), -(p.get("MIN") or 0)))
    for p in pl:
        if p.get("dnp"):
            dnp.append(f'{_e(p["name"])}{" (" + _e(p.get("reason")) + ")" if p.get("reason") else ""}'); continue
        rows += (f'<tr><td>{"<b>" if p.get("starter") else ""}{_e(p["name"])}{"</b>" if p.get("starter") else ""} '
                 f'<span class="eb-dim">{_e(p.get("pos", ""))}</span></td><td class="n num">{_fr(p.get("MIN") or 0, 0)}</td>'
                 f'<td class="n num"><b>{p.get("PTS", 0)}</b></td><td class="n num">{p.get("REB", 0)}</td><td class="n num">{p.get("AST", 0)}</td>'
                 f'<td class="n num">{p.get("FGM", 0)}/{p.get("FGA", 0)}</td><td class="n num">{p.get("3PM", 0)}/{p.get("3PA", 0)}</td>'
                 f'<td class="n num">{p.get("FTM", 0)}/{p.get("FTA", 0)}</td><td class="n num">{p.get("STL", 0)}</td>'
                 f'<td class="n num">{p.get("BLK", 0)}</td><td class="n num">{p.get("TO", 0)}</td><td class="n num">{p.get("PM", 0):+d}</td></tr>')
    s = T.get("stats") or {}
    if s:
        rows += (f'<tr class="nr-tot"><td>Équipe</td><td></td><td class="n num"><b>{T["pts"]}</b></td>'
                 f'<td class="n num">{s.get("OREB", 0) + s.get("DREB", 0)}</td><td class="n num">{s.get("AST", 0)}</td>'
                 f'<td class="n num">{s.get("FGM", 0)}/{s.get("FGA", 0)}</td><td class="n num">{s.get("3PM", 0)}/{s.get("3PA", 0)}</td>'
                 f'<td class="n num">{s.get("FTM", 0)}/{s.get("FTA", 0)}</td><td class="n num">{s.get("STL", 0)}</td>'
                 f'<td class="n num">{s.get("BLK", 0)}</td><td class="n num">{s.get("TO", 0)}</td><td></td></tr>')
    return (f'<div class="eb-card"><h3 class="eb-h">{_e(T["abbr"])} <small>{_e(T.get("name", ""))} · {T["pts"]} points · '
            f'titulaires en gras</small></h3><div class="eb-scroll"><table class="eb-tbl">{head}{rows}</table></div>'
            + (f'<p class="eb-note">N’ont pas joué : {", ".join(dnp)}.</p>' if dnp else "") + '</div>')


def _when(ko):
    d = ko.astimezone(_tz())
    return f'{JOURS[d.weekday()]} {d:%d/%m} · {d:%H:%M}'


def _item(gid, g, ko, first, n_bets):
    a, h = g["away"], g["home"]
    aw = a["pts"] > h["pts"]
    chips = (f'<span class="eb-chip"><em>Nos paris</em>{n_bets[0]}-{n_bets[1]}</span>' if sum(n_bets) else "")
    return (f'<button class="eb-item{" on" if first else ""}" data-id="r{gid}" onclick="ebPick(\'nbares\',\'r{gid}\')">'
            f'<div class="eb-item-top"><span>NBA · terminé</span><span>{_when(ko)}</span></div>'
            f'<div class="eb-item-t">{"<b>" if aw else ""}{_e(a["abbr"])} {a["pts"]}{"</b>" if aw else ""} – '
            f'{"" if aw else "<b>"}{h["pts"]} {_e(h["abbr"])}{"" if aw else "</b>"}</div>'
            f'<div class="eb-chips">{chips}</div></button>')


def stamp(now=None):
    """Version des resultats (ajoutee a celle du fragment NBA) : le chargeur de index.html remplace une page qui
    embarque des resultats plus anciens meme quand la simulation n'a pas change."""
    now = now or datetime.now(timezone.utc)
    games = finished(now)
    if not games: return ""
    st = load().get("games") or {}
    return f'{max(k for _, _, k in games):%Y-%m-%dT%H:%M}#{sum(1 for gid, _, _ in games if (st.get(gid) or {}).get("lines")):02d}'


def view(now=None):
    """Contenu de la vue Basket > Résultats (liste a gauche, fiche du match a droite)."""
    now = now or datetime.now(timezone.utc)
    hist = _load("data/nba_sim_history.json", {}).get("games") or {}
    games = finished(now, hist=hist)
    if not games:
        return '<div class="eb-card eb-empty">Aucun match NBA terminé ces dernières 24 heures.</div>'
    st = load().get("games") or {}
    items = details = ""
    for i, (gid, g, ko) in enumerate(games):
        props, other = _our_bets(gid)
        players = {p["id"]: p for side in ("home", "away") for p in g[side].get("players") or []}
        res = ([x.get("result") for its in props.values() for o, x in its if o[:1] in ("🎯", "💰")]
               + [x.get("result") for _, x in other])
        nb = (res.count("win"), res.count("loss"))
        items += _item(gid, g, ko, i == 0, nb)
        a, h = g["away"], g["home"]
        details += (f'<section class="eb-detail{" on" if i == 0 else ""}" data-id="r{gid}">'
                    f'<div class="eb-card nr-hd"><div class="eb-meta">NBA · {_when(ko)} · terminé'
                    + (f' après {len(a.get("quarters") or []) - 4} prolongation(s)' if len(a.get("quarters") or []) > 4 else "")
                    + f'</div><div class="nr-sc"><span class="{"nr-w" if a["pts"] > h["pts"] else ""}">{_e(a.get("name") or a["abbr"])} '
                    f'<b>{a["pts"]}</b></span><span class="eb-dim">–</span><span class="{"nr-w" if h["pts"] > a["pts"] else ""}">'
                    f'<b>{h["pts"]}</b> {_e(h.get("name") or h["abbr"])}</span></div></div>'
                    + _quarters(g) + _sim_vs_real(g, hist.get(gid) or {}) + _our_card(g, props, other, players)
                    + _props_card(gid, g, st.get(gid), hist.get(gid) or {}, props)
                    + _box(a, h) + _box(h, a) + '</section>')
    return (f'<div class="eb-layout"><aside class="eb-side"><div class="eb-label">Matchs terminés · {len(games)}</div>'
            f'<div class="eb-list">{items}</div></aside><div class="eb-main">{details}</div></div>' + CSS)


CSS = """<style>
.nr-sc{display:flex;gap:14px;align-items:baseline;flex-wrap:wrap;font-size:20px;margin-top:6px}
.nr-sc b{font-size:28px}.nr-sc .nr-w{color:var(--acc)}
.nr-p{margin:6px 0;font-size:13.5px;line-height:1.5}
.nr-ok{color:var(--acc);font-weight:700}.nr-ko{color:var(--loss,#f87171);font-weight:700}
.nr-f{display:flex;flex-wrap:wrap;gap:4px;margin:4px 0 6px}
.nr-f button{background:var(--chip,rgba(148,163,184,.1));border:0;color:var(--mu);border-radius:7px;padding:4px 9px;cursor:pointer;font:inherit;font-size:12px}
.nr-f button.on{background:rgba(149,224,74,.15);color:var(--tx);font-weight:700}
.nr-props:not(.flt) td.nr-rep>*{visibility:hidden}
.nr-wrap td{white-space:normal}
@media (max-width:640px){.nr-props td,.nr-props th{padding:7px 5px}.nr-props .nr-x{display:none}
  .nr-props td:first-child{white-space:normal;min-width:96px}.nr-props td:first-child .eb-dim{display:block;font-size:11px}
  .nr-props td:nth-child(2){white-space:normal}.nr-props td:nth-child(5){font-size:12.5px}
  .nr-sc{font-size:16px}.nr-sc b{font-size:24px}}
.nr-tot td{font-weight:600;border-top:2px solid var(--line)!important}
</style><script>
window.nrF=window.nrF||function(b,s){var c=b.closest('.eb-card');
  c.querySelectorAll('.nr-f button').forEach(function(x){x.classList.toggle('on',x===b);});
  var t=c.querySelector('.nr-props');if(t)t.classList.toggle('flt',!!s);
  c.querySelectorAll('tr[data-s]').forEach(function(r){r.style.display=(!s||r.getAttribute('data-s')===s)?'':'none';});};
</script>"""


if __name__ == "__main__":
    print("change" if refresh() else "rien de nouveau")
