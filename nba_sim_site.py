"""
nba_sim_site.py — Onglet « Simulation NBA » : liste des matchs a gauche, fiche du match a droite.

Donnees : data/nba_db/nba_sim.json (nba_sim_run.py, 10 000 simulations par match).
Styles : composants .eb-* de site_theme.py ; JS de selection partage avec
foot_analyse_site.py. Visibilite par classes uniquement (optimize_html convertit
les styles en ligne en classes).
"""
import html
import json
from datetime import datetime, timedelta, timezone

from foot_analyse_site import SCRIPT as EB_SCRIPT

try:
    from zoneinfo import ZoneInfo
    TZ_PARIS = ZoneInfo("Europe/Paris")
except Exception:
    TZ_PARIS = timezone(timedelta(hours=1))

SIM_PATH = "data/nba_db/nba_sim.json"
JOURS = ["Lun.", "Mar.", "Mer.", "Jeu.", "Ven.", "Sam.", "Dim."]
STAT_LABELS = {"PTS": "Points", "REB": "Rebonds", "AST": "Passes", "3PM": "Paniers à 3 pts", "PRA": "Pts + Reb + Pas",
               "PR": "Pts + Reb", "PA": "Pts + Pas", "RA": "Reb + Pas", "STL": "Interceptions", "BLK": "Contres"}
STAT_SHORT = {"PTS": "Pts", "REB": "Reb", "AST": "Pas", "3PM": "3pts", "PRA": "PRA", "PR": "P+R", "PA": "P+A",
              "RA": "R+A", "STL": "Int", "BLK": "Ctr"}
# Bilan des backtests contre les vraies lignes (nba_backtest.py, labels m_U / late_U / blind_*)
BACKTEST_NOTE = ("Sur la ligne de clôture, le simulateur ne bat pas le bookmaker. Sa force : repérer les cotes "
                 "d’ouverture mal placées sur les 3 pts, passes, interceptions et contres (voir la sélection).")
PICKS_PATH = "data/nba_sim_picks.json"
PICK_STATS = ("3PM", "AST", "STL", "BLK")   # = nba_sim_run.PICK_STATS
SIM_BIAS = 0.025                            # = nba_sim_run.SIM_BIAS
PROPS_SHOWN = 10
SUSPECT_GAP = 0.20


def load_sim(path=SIM_PATH):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _e(s):
    return html.escape(str(s if s is not None else ""))


def _pct(x, d=1):
    return f"{100 * x:.{d}f}".replace(".", ",") + " %"


def _when(iso, now):
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(TZ_PARIS)
    except Exception:
        return ""
    if dt.date() == now.date(): day = "Auj."
    elif dt.date() == now.date() + timedelta(days=1): day = "Dem."
    else: day = JOURS[dt.weekday()]
    return f"{day} {dt:%H:%M}"


def _dec(ml):
    if ml is None: return None
    ml = float(ml)
    return 1 + ml / 100 if ml > 0 else 1 + 100 / -ml


def _am(o):
    return "" if o is None else (f"+{o}" if o > 0 else f"−{-o}")


def _nick(team):
    n = team.get("name") or team.get("abbr") or ""
    return n.split()[-1] if n else ""


def _vegas_line(g):
    L = g.get("lines") or {}
    if L.get("home_spread") is None: return None
    sp = L["home_spread"]
    return (g["home"], sp) if sp <= 0 else (g["away"], -sp)


# ── Liste de gauche ──────────────────────────────────────────────────────────

def _item(g, gid, now, first):
    L = g.get("lines") or {}
    chips = ""
    for team, key in ((g["away"], "away_ml"), (g["home"], "home_ml")):
        d = _dec(L.get(key))
        if d: chips += f'<span class="eb-chip"><em>{_e(team["abbr"])}</em>{d:.2f}</span>'
    if L.get("total"):
        chips += f'<span class="eb-chip"><em>O/U</em>{L["total"]}</span>'
    return (f'<button class="eb-item{" on" if first else ""}" data-id="{gid}" onclick="ebPick(\'nba\',\'{gid}\')">'
            f'<div class="eb-item-top"><span>NBA</span><span>{_when(g.get("date", ""), now)}</span></div>'
            f'<div class="eb-item-t">{_e(_nick(g["away"]))} @ {_e(_nick(g["home"]))}</div>'
            f'<div class="eb-chips">{chips}</div></button>')


# ── Fiche du match ───────────────────────────────────────────────────────────

def _form(team):
    cells = "".join(f'<i class="{"v" if r == "V" else "d"}">{r}</i>' for r in team.get("form") or [])
    return f'<div class="eb-form" title="5 derniers matchs, du plus ancien au plus récent">{cells}</div>' if cells else ""


def _kpi(label, p, sub=""):
    return (f'<div class="eb-card eb-kpi"><span class="eb-kpi-l">{label}</span>'
            f'<span class="eb-kpi-v">{_pct(p)}</span><span class="eb-bar"><i style="--w:{100 * p:.1f}%"></i></span>'
            + (f'<span class="eb-kpi-s">{sub}</span>' if sub else "") + '</div>')


def _header(g, now):
    t, h, a, L = g["team"], g["home"], g["away"], g.get("lines") or {}
    vl = _vegas_line(g)
    vegas = (f' · {_e(g.get("book") or "DraftKings")} : {_e(vl[0]["abbr"])} {vl[1]:+.1f} · O/U {L["total"]}'
             if vl and L.get("total") else "")

    def side(team, pts):
        return (f'<div class="eb-team"><span class="eb-team-n">{_e(_nick(team))}</span>{_form(team)}'
                f'<span class="eb-team-sub">Points attendus : <b>{pts:.1f}</b> · bilan <b>{_e(team.get("record", "–"))}</b></span></div>')
    return (f'<div class="eb-card"><div class="eb-meta">NBA · {_when(g.get("date", ""), now)}{vegas}</div>'
            f'<div class="eb-vs">{side(a, t["away_mean"])}<span class="eb-vs-x">@</span>{side(h, t["home_mean"])}</div></div>')


def _infobar(g):
    n = f'{g.get("n_sims", 10000):,}'.replace(",", " ")
    return ('<div class="eb-card"><div class="eb-ctrl">'
            f'<span class="eb-meta"><b class="eb-acc">{n}</b> simulations · rythme, adresse, minutes, blessures et lignes Vegas</span>'
            f'<span class="eb-ctrl-info">{BACKTEST_NOTE}</span></div></div>')


def _kpis(g):
    t, h, a, L = g["team"], g["home"], g["away"], g.get("lines") or {}
    p = t["p_home_win"]

    def odds(key, prob):
        fair = f"juste {1 / max(prob, 1e-3):.2f}"
        return f"cote {_dec(L.get(key)):.2f} · {fair}" if L.get(key) is not None else f"cote {fair}"
    row1 = (f'<div class="eb-g3">{_kpi("Victoire " + _e(_nick(a)), 1 - p, odds("away_ml", 1 - p))}'
            f'{_kpi("Match serré (écart ≤ 5 pts)", t.get("p_close", 0), "prolongation " + _pct(t["p_ot"]))}'
            f'{_kpi("Victoire " + _e(_nick(h)), p, odds("home_ml", p))}</div>')
    vl = _vegas_line(g)
    if t.get("p_over_total") is not None:
        over = _kpi(f'Plus de {L["total"]} pts', t["p_over_total"], f'total projeté {t["total_mean"]:.1f}')
    else:
        over = _kpi("Écart ≥ 15 pts", t.get("p_blowout", 0), f'total projeté {t["total_mean"]:.1f}')
    if vl and t.get("p_home_cover") is not None:
        pc = t["p_home_cover"] if vl[0] is h else 1 - t["p_home_cover"]
        cover = _kpi(f'{_e(_nick(vl[0]))} {vl[1]:+.1f} couvert', pc, f'écart projeté {abs(t["margin_mean"]):.1f} pts')
    else:
        cover = _kpi("Écart ≥ 15 pts", t.get("p_blowout", 0))
    tiles = "".join(f'<div class="eb-tile"><b>{_e(x["team"])} {x["range"]}</b><small>{_pct(x["p"])}</small></div>'
                    for x in (t.get("margin_top") or [])[:6])
    margins = (f'<div class="eb-card eb-kpi"><span class="eb-kpi-l">Écarts les plus probables</span>'
               f'<div class="eb-tiles">{tiles}</div></div>')
    return row1 + f'<div class="eb-g3">{over}{cover}{margins}</div>'


def _hist(g):
    t, L = g["team"], g.get("lines") or {}
    hist = t.get("total_hist") or []
    if not hist: return ""
    top = max(x["p"] for x in hist) or 1
    line = L.get("total")

    def has_line(lab):
        if line is None: return False
        lo = -1e9 if lab == "<200" else float(lab.rstrip("+"))
        hi = 200 if lab == "<200" else (1e9 if lab.endswith("+") else lo + 10)
        return lo <= line < hi
    bars = "".join(f'<div class="eb-hb{" mark" if has_line(x["label"]) else ""}"><em>{_pct(x["p"])}</em>'
                   f'<i style="--h:{100 * x["p"] / top:.1f}%"></i></div>' for x in hist)
    xs = "".join(f'<span>{_e(x["label"])}</span>' for x in hist)
    note = f' <small>hachuré = tranche de la ligne O/U {line}</small>' if line else ""
    return (f'<div class="eb-card"><h3 class="eb-h">Distribution du total de points{note}</h3>'
            f'<div class="eb-hist">{bars}</div><div class="eb-hx">{xs}</div></div>')


def _rb(q, scale, line=None):
    pos = lambda v: max(0.0, min(100.0, 100.0 * v / scale))
    out = (f'<span class="eb-rb" style="--a:{pos(q[0]):.1f}%;--b:{100 - pos(q[4]):.1f}%;--c:{pos(q[1]):.1f}%;'
           f'--d:{100 - pos(q[3]):.1f}%;--m:{pos(q[2]):.1f}%"><i class="o"></i><i class="in"></i><i class="m"></i>')
    if line is not None:
        out += f'<i class="l" style="--x:{pos(line):.1f}%" title="Ligne DraftKings {line}"></i>'
    return out + "</span>"


def _players(g):
    lines = {(p["id"], p["stat"]): p["line"] for p in g.get("props") or []}
    scale = max([p["PTS"]["q"][4] for p in g["players"]] + [30]) * 1.05
    rows = ""
    for side in ("away", "home"):
        team = g[side]
        ps = sorted([p for p in g["players"] if p["side"] == side and p["p_play"] >= 0.3 and p["MIN"] >= 8],
                    key=lambda p: -p["MIN"] * p["p_play"])
        starters = [p for p in ps if p.get("starter")][:5]
        if len(starters) < 5: starters = ps[:5]
        for label, group in (("5 majeur", starters), ("Banc", [p for p in ps if p not in starters])):
            if not group: continue
            rows += f'<tr class="sec"><td colspan="7">{_e(team.get("name"))} · {label}</td></tr>'
            for p in group:
                tag = ""
                if p.get("status"): tag = '<span class="eb-tag warn">incertain</span>'
                elif p["p_play"] < 0.9: tag = f'<span class="eb-tag">joue {_pct(p["p_play"], 0)}</span>'
                if p.get("n_season", 0) < 2: tag += '<span class="eb-tag" title="Projection moins fiable">peu de données</span>'
                rows += (f'<tr><td>{_e(p["name"])}{tag}</td><td class="n">{p["MIN"]:.0f}</td>'
                         f'<td class="n"><b>{p["PTS"]["mean"]:.1f}</b>{_rb(p["PTS"]["q"], scale, lines.get((p["id"], "PTS")))}</td>'
                         f'<td class="n">{p["REB"]["mean"]:.1f}</td><td class="n">{p["AST"]["mean"]:.1f}</td>'
                         f'<td class="n">{p["3PM"]["mean"]:.1f}</td><td class="n">{p["PRA"]["mean"]:.1f}</td></tr>')
    return ('<p class="eb-note eb-note-top">Moyennes sur les matchs où le joueur joue · barre : 8 matchs sur 10 (fin), '
            '1 sur 2 (épais), trait blanc = médiane, pointillés = ligne DraftKings</p>'
            '<div class="eb-scroll"><table class="eb-tbl"><tr><th>Joueur</th><th>Min</th><th>Points</th><th>Reb</th>'
            f'<th>Pas</th><th>3pts</th><th>PRA</th></tr>{rows}</table></div>')


def _props(g, gid):
    props = g.get("props") or []
    if not props:
        return '<div class="eb-empty">Lignes DraftKings pas encore publiées (en général la veille au soir).</div>'
    gap = lambda p: abs(p["p_sim"] - p["p_mkt"])
    rows = sorted(props, key=lambda p: (gap(p) >= SUSPECT_GAP, -gap(p)))
    stats = [s for s in STAT_LABELS if any(p["stat"] == s for p in rows)]
    seg = (f'<div class="eb-seg"><button class="on" onclick="ebProps(\'{gid}\',\'ALL\',this)">Tous</button>'
           + "".join(f'<button onclick="ebProps(\'{gid}\',\'{s}\',this)">{STAT_SHORT[s]}</button>' for s in stats) + '</div>')
    body = ""
    for i, p in enumerate(rows):
        d = p["p_sim"] - p["p_mkt"]
        over = d >= 0
        sus = gap(p) >= SUSPECT_GAP and p["stat"] not in PICK_STATS
        cls = "eb-dim" if sus else ("eb-acc" if gap(p) >= 0.08 else "")
        hide = " eb-off" if i >= PROPS_SHOWN else ""
        flag = ('<span class="eb-tag" title="Écart trop grand pour être crédible : le bookmaker intègre sûrement '
                'une info absente du modèle">suspect</span>') if sus else ""
        if p.get("pick"):
            flag += '<span class="eb-tag acc" title="Retenu par la sélection du simulateur">sélection</span>'
        price = p["over"] if over else p["under"]
        # data-v : valeur de tri de chaque colonne (ebSort)
        body += (f'<tr class="eb-prop{hide}" data-stat="{p["stat"]}" data-i="{i}"><td data-v="{_e(p["name"])}">{_e(p["name"])}</td>'
                 f'<td data-v="{_e(STAT_LABELS.get(p["stat"], p["stat"]))}">{_e(STAT_LABELS.get(p["stat"], p["stat"]))}</td>'
                 f'<td class="n" data-v="{p["line"]}"><b>{p["line"]}</b></td>'
                 f'<td class="n" data-v="{p["p_sim"]:.4f}"><span class="eb-pp"><i style="--w:{100 * p["p_sim"]:.1f}%"></i></span>{_pct(p["p_sim"], 0)}</td>'
                 f'<td class="n" data-v="{p["p_mkt"]:.4f}"><span class="eb-pp bk"><i style="--w:{100 * p["p_mkt"]:.1f}%"></i></span>{_pct(p["p_mkt"], 0)}</td>'
                 f'<td class="n {cls}" data-v="{abs(d):.4f}">{"+" if d >= 0 else "−"}{abs(100 * d):.0f} pts{flag}</td>'
                 f'<td class="n" data-v="{"Over" if over else "Under"}{_dec(price):06.3f}">{"Over" if over else "Under"} '
                 f'<span class="eb-dim">{_am(price)}</span></td></tr>')
    more = (f'<button class="eb-more" onclick="ebPropsMore(\'{gid}\',this)">Voir les {len(rows)} props</button>'
            if len(rows) > PROPS_SHOWN else "")
    head = "".join(f'<th class="eb-sort" onclick="ebSort(this)" title="Trier">{h}</th>'
                   for h in ("Joueur", "Marché", "Ligne", "Over simu", "Over book", "Écart", "Côté simu"))
    return (f'{seg}<div class="eb-scroll"><table class="eb-tbl" id="eb-props-{gid}"><tr>{head}</tr>{body}</table></div>{more}'
            '<p class="eb-note">Probabilité de dépasser la ligne, marge du bookmaker retirée. Écarts ≥ 20 pts (« suspect ») '
            'en fin de liste : ils signalent presque toujours une info que le modèle n’a pas (minutes, nouveau rôle, blessure).</p>')


def _typical(g):
    ty, h, a = g["typical"], g["home"], g["away"]
    box = ty.get("box") or []
    rows = ""
    for side, team in (("away", a), ("home", h)):
        rows += f'<tr class="sec"><td colspan="6">{_e(team.get("name"))} · {ty[side]} pts</td></tr>'
        for b in sorted([b for b in box if b["side"] == side], key=lambda b: -b["PTS"])[:5]:
            rows += (f'<tr><td>{_e(b["name"])}</td><td class="n">{b["MIN"]}</td><td class="n"><b>{b["PTS"]}</b></td>'
                     f'<td class="n">{b["REB"]}</td><td class="n">{b["AST"]}</td><td class="n">{b["3PM"]}</td></tr>')
    ot = " · après prolongation" if ty.get("ot") else ""
    return (f'<div class="eb-vs eb-vs-typ"><div class="eb-team"><span class="eb-kpi-v">{ty["away"]}</span>'
            f'<span class="eb-team-sub">{_e(_nick(a))}</span></div><span class="eb-vs-x">–</span>'
            f'<div class="eb-team"><span class="eb-kpi-v">{ty["home"]}</span><span class="eb-team-sub">{_e(_nick(h))}</span></div></div>'
            f'<div class="eb-scroll"><table class="eb-tbl"><tr><th>Joueur</th><th>Min</th><th>Pts</th><th>Reb</th><th>Pas</th>'
            f'<th>3pts</th></tr>{rows}</table></div>'
            f'<p class="eb-note">Le tirage le plus proche du match médian{ot} : un scénario plausible pour se projeter, pas une prédiction.</p>')


def _tabs(g, gid):
    n_props = len(g.get("props") or [])
    return ('<div class="eb-card"><div class="eb-seg">'
            '<button class="on" onclick="ebSeg(this,\'pl\')">Joueurs</button>'
            f'<button onclick="ebSeg(this,\'pr\')">Props DraftKings · {n_props}</button></div>'
            f'<div class="eb-pane on" data-pane="pl">{_players(g)}</div>'
            f'<div class="eb-pane" data-pane="pr">{_props(g, gid)}</div></div>')


# ── Selection du simulateur ─────────────────────────────────────────────────

def load_picks(path=PICKS_PATH):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f).get("picks") or {}
    except (OSError, ValueError):
        return {}


UNIT = {"AST": "passes", "3PM": "paniers à 3 pts", "STL": "interceptions", "BLK": "contres"}


def _n(v, d=1):
    return f"{v:.{d}f}".replace(".", ",")


def _why_text(p):
    """Argumentaire d'un pick, redige a partir des faits calcules par nba_sim_run._why."""
    w = p.get("why")
    if not w:
        return ""
    over = p["pick"] == "o"
    unit, line = UNIT.get(p["stat"], STAT_LABELS.get(p["stat"], p["stat"]).lower()), _n(p["line"])
    ps = min(max(p["p_sim"] + SIM_BIAS, 0), 1)
    ps, pm = (ps, p["p_mkt"]) if over else (1 - ps, 1 - p["p_mkt"])
    items = [f'Le simulateur projette <b>{_n(w["proj"])} {unit}</b> (médiane {_n(w["med"], 0)}) en {w["min"]:.0f} min : '
             f'{"plus" if over else "moins"} de {line} dans <b>{_pct(ps, 0)}</b> des 10 000 matchs simulés, '
             f'contre {_pct(pm, 0)} selon le bookmaker.']
    cur, prev = w.get("cur"), w.get("prev")
    if cur:
        last = ", ".join(_n(v, 0) for v in w.get("last") or [])
        items.append(f'Cette saison : <b>{_n(cur["avg"])}</b> par match en {cur["min"]:.0f} min '
                     f'({cur["n"]} match{"s" if cur["n"] > 1 else ""}{" : " + last if last else ""}, du plus récent au plus ancien).')
    if w.get("l10_n") and w["l10_n"] > (cur or {}).get("n", 0):
        k = w["l10_over"] if over else w["l10_n"] - w["l10_over"]
        items.append(f'Sur ses {w["l10_n"]} derniers matchs : <b>{k}/{w["l10_n"]}</b> '
                     f'{"au-dessus" if over else "en dessous"} de {line}.')
    if prev:
        txt = f'Saison passée : {_n(prev["avg"])} en {prev["min"]:.0f} min'
        if cur and abs(cur["min"] - prev["min"]) >= 4:
            txt += (f', temps de jeu en {"hausse" if cur["min"] > prev["min"] else "baisse"} '
                    f'de {abs(cur["min"] - prev["min"]):.0f} min cette saison')
        items.append(txt + ".")
    n_games = (cur or {}).get("n", 0) + (prev or {}).get("n", 0)
    if n_games < 10:
        items.append(f'<span class="eb-warn">Prudence : seulement {n_games} match{"s" if n_games > 1 else ""} '
                     f'en {"deux saisons" if prev else "un an"}, la projection repose en partie sur des moyennes de poste.</span>')
    o = w.get("opp")
    if o and o["league"]:
        d = o["allowed"] / o["league"] - 1
        if abs(d) >= 0.05:
            good = (d > 0) == over
            items.append(f'Adversaire : {_e(o["abbr"])} concède {_n(o["allowed"])} {unit} par match sur ses 15 derniers '
                         f'({"+" if d > 0 else "−"}{abs(100 * d):.0f} % par rapport à la moyenne de la ligue, '
                         f'{"favorable" if good else "défavorable"} au pari).')
        else:
            items.append(f'Adversaire : {_e(o["abbr"])} dans la moyenne de la ligue '
                         f'({_n(o["allowed"])} {unit} concédés par match).')
    if w.get("out"):
        items.append(f'Absents dans son équipe : {_e(", ".join(w["out"]))}. '
                     'Le simulateur redistribue leurs minutes et leurs ballons.')
    price = p["over"] if over else p["under"]
    opn = p.get("over_open") if over else p.get("under_open")
    if opn is not None and _dec(opn) and abs(_dec(opn) - _dec(price)) >= 0.02:
        moved = _dec(price) < _dec(opn)
        items.append(f'Cote passée de {_dec(opn):.2f} à {_dec(price):.2f} depuis l’ouverture : '
                     + ("le marché va déjà dans ce sens, une partie de l’avantage est déjà prise." if moved
                        else "le marché va dans l’autre sens, prudence."))
    return '<ul class="eb-why-l">' + "".join(f"<li>{t}</li>" for t in items) + "</ul>"


def _pick_rows(items):
    """Une ligne par pick (triee par ecart decroissant) + sa ligne « Pourquoi ? » (repliee sauf la premiere)."""
    rows = ""
    for n, p in enumerate(sorted(items, key=lambda p: -abs(p["p_sim"] + SIM_BIAS - p["p_mkt"]))):
        over = p["pick"] == "o"
        price = p["over"] if over else p["under"]
        opn = p.get("over_open") if over else p.get("under_open")
        ps = min(max(p["p_sim"] + SIM_BIAS, 0), 1)
        ps, pm = (ps, p["p_mkt"]) if over else (1 - ps, 1 - p["p_mkt"])
        moved = ""
        if opn is not None and _dec(opn) and abs(_dec(opn) - _dec(price)) >= 0.02:
            moved = f'<span class="eb-dim"> (ouv. {_dec(opn):.2f})</span>'
        why = _why_text(p)
        rows += (f'<tr class="eb-prop eb-pick{" open" if n == 0 and why else ""}" data-stat="{p["stat"]}" data-i="{n}"'
                 f'{" onclick=\"ebWhy(this)\"" if why else ""}><td data-v="{_e(p["name"])}">{_e(p["name"])}</td>'
                 f'<td data-v="{_e(STAT_LABELS.get(p["stat"], p["stat"]))}"><b>{"Over" if over else "Under"} {p["line"]}</b> '
                 f'{_e(STAT_LABELS.get(p["stat"], p["stat"]))}</td>'
                 f'<td class="n" data-v="{_dec(price):.3f}"><b>{_dec(price):.2f}</b>{moved}</td>'
                 f'<td class="n" data-v="{ps:.4f}">{_pct(ps, 0)}</td><td class="n" data-v="{pm:.4f}">{_pct(pm, 0)}</td>'
                 f'<td class="n eb-acc" data-v="{ps - pm:.4f}">+{100 * (ps - pm):.0f} pts</td>'
                 f'<td class="eb-why-b">{"Pourquoi ?" if why else ""}</td></tr>')
        if why:
            rows += f'<tr class="eb-why{"" if n == 0 else " eb-off"}"><td colspan="7">{why}</td></tr>'
    return rows


def _pick_head():
    cols = ("Joueur", "Pari", "Cote", "Simu", "Book", "Écart")
    return "".join(f'<th class="eb-sort" onclick="ebSort(this)" title="Trier">{h}</th>' for h in cols) + "<th></th>"


PICK_NOTE = ("Règle testée en aveugle sur 3 saisons de vraies cotes (ESPN BET 2025-26, DraftKings 2026-27) : "
             "le simulateur annonce le sens du mouvement de la cote 3 fois sur 4. À la cote d’ouverture : "
             "+10 à +20 % de rendement ; à la cote de clôture : −1 à −7 %. <b>L’avantage vient de prendre la cote tôt</b> : "
             "si elle a déjà bougé dans le sens du pari (« ouv. »), il a en partie fondu. Mise fixe et modeste.")


def _track(picks):
    done = [r for r in picks.values() if r.get("result") in ("win", "loss", "push")]
    if not done:
        return '<span class="eb-meta">Suivi réel démarré le 27/10 : premiers résultats après les matchs de ce soir.</span>'
    w = sum(r["result"] == "win" for r in done); l = sum(r["result"] == "loss" for r in done)
    profit = sum((_dec(r["first_price"]) - 1) if r["result"] == "win" else -1 if r["result"] == "loss" else 0 for r in done)
    clv = [r["last_p_mkt"] * _dec(r["first_price"]) - 1 for r in done if r.get("last_p_mkt") is not None]
    clv_txt = f' · CLV {100 * sum(clv) / len(clv):+.1f} %' if clv else ""
    return (f'<span class="eb-meta">Suivi réel depuis le 27/10, tous matchs : <b>{w}-{l}</b> · '
            f'<b class="{"eb-acc" if profit >= 0 else "eb-neg"}">{profit:+.1f} u</b> '
            f'({100 * profit / len(done):+.1f} % sur {len(done)} picks, 1 u par pick, première cote proposée){clv_txt}</span>')


def _selection(g, picks):
    items = [p for p in g.get("props") or [] if p.get("pick")]
    if not items:
        msg = ("Aucune prop retenue pour ce match : pas d’écart assez net sur les 3 pts, passes, interceptions et contres."
               if g.get("props") else "Lignes DraftKings pas encore publiées (en général la veille au soir) : "
               "la sélection apparaîtra à leur sortie.")
        return f'<div class="eb-card eb-sel"><h3 class="eb-h">Sélection du simulateur</h3><div class="eb-empty">{msg}</div></div>'
    return ('<div class="eb-card eb-sel"><div class="eb-ctrl"><h3 class="eb-h eb-h-nomb">Sélection du simulateur '
            f'<small>{len(items)} props · cliquer une ligne pour l’argumentaire</small></h3>{_track(picks)}</div>'
            f'<div class="eb-scroll"><table class="eb-tbl" data-all="1"><tr>{_pick_head()}</tr>'
            f'{_pick_rows(items)}</table></div><p class="eb-note">{PICK_NOTE}</p></div>')


def _video(g, gid):
    """Carte « match type en video » : le deroule (data/replays/<gid>.json, nba_replay.py)
    est charge a la demande et rejoue par le lecteur canvas (nba_replay_player.js)."""
    rp = g.get("replay")
    if not rp:
        return ""
    h, a = g["home"], g["away"]
    fin = rp["final"]
    name = f'match-type-{a["abbr"]}-{h["abbr"]}-{(g.get("date") or "")[:10]}'
    n = f'{rp["n"]:,}'.replace(",", " ")
    k = f'{rp["k"]:,}'.replace(",", " ")
    return ('<div class="eb-card eb-video">'
            '<div class="eb-video-head"><div>'
            '<h3 class="eb-h eb-h-nomb">Le match type en vidéo <small>~3 min · condensé</small></h3>'
            f'<div class="eb-meta">{n} matchs simulés possession par possession · simulation n° {k} · '
            f'{_e(_nick(a))} <b class="eb-acc">{fin[1]}-{fin[0]}</b> {_e(_nick(h))}</div></div>'
            f'<button class="eb-btn" onclick="ebVideo(this,\'{_e(rp["path"])}\',\'{_e(name)}\')">▶ Regarder</button></div>'
            '<div class="eb-video-host"></div>'
            '<p class="eb-note">Le vainqueur choisi par la majorité des simulations, un score proche de la médiane, '
            'chaque joueur proche de sa moyenne. Bouton « Télécharger MP4 » sous la vidéo.</p></div>')


def _player_script():
    """Lecteur video (nba_replay_player.js) + chargement a la demande du deroule."""
    try:
        with open("nba_replay_player.js", encoding="utf-8") as f:
            js = f.read()
    except OSError:
        return ""
    return "<script>" + js + "</script>" + VIDEO_SCRIPT


VIDEO_SCRIPT = """<script>
window.ebVideo=function(btn,path,name){
  var host=btn.closest('.eb-video').querySelector('.eb-video-host');
  if(host.getAttribute('data-ready'))return;
  btn.disabled=true;btn.textContent='Chargement…';
  fetch(path,{cache:'no-cache'}).then(function(r){if(!r.ok)throw new Error('HTTP '+r.status);return r.json();})
    .then(function(d){host.setAttribute('data-ready','1');SPReplay.open(host,d,name);btn.classList.add('eb-off');
      var p=host.querySelector('[data-a=play]');if(p)p.click();})
    .catch(function(e){btn.disabled=false;btn.textContent='▶ Regarder';host.innerHTML='<p class="eb-note">Vidéo indisponible ('+e.message+').</p>';});
};
</script>"""


def build_nba_sim_section(sim=None):
    sim = sim if sim is not None else load_sim()
    if not sim or not sim.get("games"):
        return '<!--eb:start--><div class="eb"><div class="eb-card eb-empty">Aucune simulation NBA disponible.</div></div><!--eb:end-->'
    now = datetime.now(TZ_PARIS)
    games = sorted(sim["games"], key=lambda g: g.get("date", ""))
    ids = [_e(g["gid"]) for g in games]
    items = "".join(_item(g, gid, now, i == 0) for i, (g, gid) in enumerate(zip(games, ids)))
    picks = load_picks()
    details = "".join(
        f'<section class="eb-detail{" on" if i == 0 else ""}" data-id="{gid}">'
        f'{_header(g, now)}{_selection(g, picks)}{_infobar(g)}{_kpis(g)}{_video(g, gid)}{_hist(g)}{_tabs(g, gid)}</section>'
        for i, (g, gid) in enumerate(zip(games, ids)))
    return ('<!--eb:start--><div class="eb" data-eb="nba">' + '<div class="eb-layout">'
            f'<aside class="eb-side"><div class="eb-label">Matchs NBA à venir · {len(games)}</div>'
            f'<div class="eb-list">{items}</div></aside><div class="eb-main">{details}</div></div></div>'
            + EB_SCRIPT + PROPS_SCRIPT + _player_script() + '<!--eb:end-->')


PROPS_SCRIPT = """<script>
(function(){
  if(window.ebProps)return;
  // Visibilite : filtre de stat (data-stat) + limite de lignes tant que « Voir les N props » n'a pas ete clique
  function apply(t){
    var stat=t.getAttribute('data-stat')||'ALL',all=t.getAttribute('data-all')==='1',shown=0;
    t.querySelectorAll('tr.eb-prop').forEach(function(r){
      var ok=stat==='ALL'||r.getAttribute('data-stat')===stat;
      var vis=ok&&(all||stat!=='ALL'||shown<""" + str(PROPS_SHOWN) + """);
      r.classList.toggle('eb-off',!vis);if(vis)shown++;
    });
  }
  window.ebProps=function(id,stat,btn){
    var t=document.getElementById('eb-props-'+id);if(!t)return;
    btn.parentNode.querySelectorAll('button').forEach(function(b){b.classList.toggle('on',b===btn);});
    t.setAttribute('data-stat',stat);apply(t);
  };
  window.ebPropsMore=function(id,btn){
    var t=document.getElementById('eb-props-'+id);if(!t)return;
    t.setAttribute('data-all','1');apply(t);btn.classList.add('eb-off');
  };
  window.ebWhy=function(tr){var n=tr.nextElementSibling;if(!n||!n.classList.contains('eb-why'))return;
    n.classList.toggle('eb-off');tr.classList.toggle('open',!n.classList.contains('eb-off'));};
  // Tri par colonne : 1er clic decroissant pour les nombres (croissant pour le texte), 2e clic inverse
  window.ebSort=function(th){
    var t=th.closest('table'),idx=Array.prototype.indexOf.call(th.parentNode.children,th);
    var rows=Array.prototype.slice.call(t.querySelectorAll('tr.eb-prop'));if(!rows.length)return;
    var val=function(r){var v=r.children[idx].getAttribute('data-v')||'';return /^-?[0-9.]+$/.test(v)?parseFloat(v):v;};
    var num=typeof val(rows[0])==='number';
    var dir=th.getAttribute('data-dir')==='desc'?'asc':th.getAttribute('data-dir')==='asc'?'desc':(num?'desc':'asc');
    th.parentNode.querySelectorAll('th').forEach(function(h){h.removeAttribute('data-dir');});
    th.setAttribute('data-dir',dir);
    var k=dir==='asc'?1:-1;
    rows.sort(function(a,b){var x=val(a),y=val(b);
      var c=typeof x==='number'&&typeof y==='number'?x-y:String(x).localeCompare(String(y),'fr');
      return c?c*k:(+a.getAttribute('data-i'))-(+b.getAttribute('data-i'));});
    var why=rows.map(function(r){var n=r.nextElementSibling;return n&&n.classList.contains('eb-why')?n:null;});
    var body=rows[0].parentNode;rows.forEach(function(r,i){body.appendChild(r);if(why[i])body.appendChild(why[i]);});
    apply(t);
  };
})();
</script>"""
