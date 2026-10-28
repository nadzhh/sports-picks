"""
nba_sim_site.py — Onglet « Simulation NBA » : liste des matchs a gauche, fiche du match a droite.

Donnees : data/nba_sim.json (nba_sim_run.py / veille nba_live.py, 10 000 simulations par match).
Publication : la section est ecrite dans nba_sim.html (mis a jour par la veille sans toucher a
index.html) ; index.html en garde une copie de secours et la remplace par nba_sim.html si plus recent.
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
    TZ_PARIS, TZ_NY = ZoneInfo("Europe/Paris"), ZoneInfo("America/New_York")
except Exception:
    TZ_PARIS, TZ_NY = timezone(timedelta(hours=1)), timezone(timedelta(hours=-4))

SIM_PATH = "data/nba_sim.json"
FRAGMENT = "nba_sim.html"
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
    if g.get("events"):
        chips += f'<span class="eb-chip hl" title="Informations reçues depuis la première simulation"><em>Infos</em>{len(g["events"])}</span>'
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


# ── Infos d'avant-match : blessures, heure de simulation, journal de la veille ──

STATUS_FR = {"out": "absent", "doubtful": "douteux", "questionable": "incertain", "probable": "probable",
             "available": "de retour", "day-to-day": "au jour le jour", "suspension": "suspendu"}


def _hm(iso, tz=None):
    """Heure de Paris d'un horodatage UTC (iso avec fuseau) ou d'une heure de New York (iso naif + TZ_NY)."""
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        if dt.tzinfo is None: dt = dt.replace(tzinfo=tz or timezone.utc)
        return f"{dt.astimezone(TZ_PARIS):%H:%M}"
    except Exception:
        return ""


def _reason(r):
    return _e((r or "").replace("Injury/Illness - ", "").replace("G League - ", "G League : "))


def _infos(g):
    inj = g.get("injuries")
    if not inj: return ""
    src = (f'rapport officiel NBA de {_hm(g["report_at"], TZ_NY)}' if g.get("report_at")
           and "officiel" in (inj["home"]["src"], inj["away"]["src"]) else "ESPN")
    head = (f'simulé à {_hm(g["sim_at"])} · blessures : {src}' if g.get("sim_at") else f"blessures : {src}")
    cols = ""
    for side in ("away", "home"):
        t, d = inj[side], g[side]
        # absents de l'effectif simule + joueurs du rapport hors effectif ESPN (two-way...)
        allout = (t.get("out") or []) + [x for x in t.get("extra") or [] if x["status"] in ("out", "doubtful")]
        outs = [o for o in allout if "G League" not in (o.get("reason") or "")]
        gl = len(allout) - len(outs)
        lines = []
        if outs:
            lines.append("<b>Absents</b> : " + ", ".join(
                f'{_e(o["name"])}' + (f' <span class="eb-dim">({_reason(o.get("reason"))})</span>' if o.get("reason") else "")
                for o in outs))
        for p in t.get("doubt") or []:
            lines.append(f'<b>{_e(p["name"])}</b> : {STATUS_FR.get(p["status"], _e(p["status"]))}, '
                         f'joue dans {_pct(p["p_play"], 0)} des simulations'
                         + (f' <span class="eb-dim">({_reason(p.get("reason"))})</span>' if p.get("reason") else ""))
        if gl: lines.append(f'<span class="eb-dim">{gl} joueur{"s" if gl > 1 else ""} en G League</span>')
        body = "".join(f"<li>{x}</li>" for x in lines) or '<li class="eb-dim">Aucun absent déclaré.</li>'
        cols += (f'<div><div class="eb-meta">{_e(d.get("abbr"))} · {"rapport officiel" if t["src"] == "officiel" else "ESPN (rapport officiel pas encore rempli)"}</div>'
                 f'<ul class="eb-inj-l">{body}</ul></div>')
    ev = "".join(f'<li><span class="eb-dim">{_hm(e["at"])}</span> {_e(e["txt"])}</li>' for e in reversed(g.get("events") or []))
    ev_html = (f'<div class="eb-meta eb-ev-t">Journal de la veille (chaque ligne a relancé la simulation)</div><ul class="eb-ev">{ev}</ul>'
               if ev else "")
    return (f'<div class="eb-card"><h3 class="eb-h">Infos d’avant-match <small>{head}</small></h3>'
            f'<div class="eb-inj">{cols}</div>{ev_html}</div>')


# ── Historique : bilan de chaque soiree (nba_sim_bilan) ──────────────────────

MOIS = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août", "septembre", "octobre",
        "novembre", "décembre"]
JOURS_L = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]
RES_TAG = {"win": ("acc", "Gagné"), "loss": ("neg", "Perdu"), "push": ("", "Nul"), "void": ("", "Remboursé")}


def _sgn(x, d=1):
    return f"{x:+.{d}f}".replace(".", ",").replace("-", "−")


def _u(x):
    return _sgn(x) + " u"


def _record(s):
    return f'{s["w"]}-{s["l"]}' + (f'-{s["p"]}' if s["p"] else "")


def _night_item(rep):
    n, s = rep["night"], rep["sum"]
    d = datetime.strptime(n, "%Y-%m-%d")
    title = f'{_record(s)} · {_u(s["units"])}' if s["n"] else f'{rep["total"]} match{"s" if rep["total"] > 1 else ""}'
    chips = f'<span class="eb-chip"><em>Matchs</em>{rep["done"]}/{rep["total"]}</span>'
    if s["with_us"] is not None:
        chips += f'<span class="eb-chip"><em>Cotes</em>{100 * s["with_us"]:.0f} % pour nous</span>'
    return (f'<button class="eb-item" data-id="bilan-{n}" onclick="ebPick(\'nba\',\'bilan-{n}\')">'
            f'<div class="eb-item-top"><span>Bilan</span><span>{JOURS[d.weekday()]} {d:%d/%m}</span></div>'
            f'<div class="eb-item-t">{title}</div><div class="eb-chips">{chips}</div></button>')


def _stat(label, value, sub="", cls=""):
    return (f'<div class="eb-stat"><span class="eb-kpi-l">{label}</span><span class="eb-stat-v {cls}">{value}</span>'
            + (f'<span class="eb-kpi-s">{sub}</span>' if sub else "") + '</div>')


def _night_facts(rep):
    """« Ce qui s'est passe » : resultats, cotes, causes des pertes, matchs, minutes."""
    s, items = rep["sum"], []
    if s["n"]:
        by = s["by"]
        parts = [f'{UNIT.get(k, k)} {by[k][0]}-{by[k][1]}' for k in PICK_STATS if k in by]
        ou = [f'{lab} {by[k][0]}-{by[k][1]}' for k, lab in (("over", "overs"), ("under", "unders")) if k in by]
        items.append(f'<b>{s["w"]} gagnés, {s["l"]} perdus</b>' + (f', {s["p"]} nul{"s" if s["p"] > 1 else ""}' if s["p"] else "")
                     + f' : <b>{_u(s["units"])}</b> à la première cote proposée (1 u par pick). '
                     f'Par stat : {", ".join(parts)} · {", ".join(ou)}.')
    if s["void"]:
        items.append(f'{s["void"]} pick{"s" if s["void"] > 1 else ""} remboursé{"s" if s["void"] > 1 else ""} : joueur absent.')
    if s["pending"]:
        items.append(f'{s["pending"]} pick{"s" if s["pending"] > 1 else ""} en attente (match pas terminé ou base pas encore à jour).')
    if s["with_us"] is not None:
        items.append(f'Cotes : le marché est allé dans notre sens sur <b>{100 * s["with_us"]:.0f} %</b> des {s["n_moved"]} cotes '
                     "qui ont bougé avant le match (74-80 % au backtest ; c'est l'indicateur le plus fiable à court terme).")
    if s["l"]:
        c = s["causes"]
        parts = [(c["minutes"], "temps de jeu mal prévu"), (c["near"], "perdus d'une seule unité"), (c["rate"], "rendement")]
        items.append("Picks perdus : " + ", ".join(f"<b>{k}</b> {t}" for k, t in parts if k) + ".")
    g = [r for r in rep["games"] if r.get("final")]
    if g:
        ok = sum(r["winner_ok"] for r in g)
        gv = [r for r in g if r.get("vegas_ok") is not None]
        gt = [r for r in g if r.get("err_total_vegas") is not None]
        items.append(f'Vainqueur trouvé dans <b>{ok} match{"s" if ok > 1 else ""} sur {len(g)}</b>'
                     + (f' (favori Vegas : {sum(r["vegas_ok"] for r in gv)} sur {len(gv)})' if gv else "") + ". "
                     f'Erreur moyenne sur le total de points : {_n(sum(abs(r["err_total"]) for r in g) / len(g))} pts'
                     + (f' (Vegas : {_n(sum(abs(r["err_total_vegas"]) for r in gt) / len(gt))} pts)' if gt else "") + ".")
    m = rep["min"]
    if m["mae"] is not None:
        top = ", ".join(f'{_e(x["name"])} ({_e(x["team"])}) {x["proj"]:.0f} → {x["act"]:.0f} min'
                        + (" (absent)" if x["dnp"] else "") for x in m["misses"][:4])
        items.append(f'Minutes : erreur moyenne de {_n(m["mae"])} min par joueur ({m["n"]} joueurs)'
                     + (f". Plus gros écarts (prévu → joué) : {top}." if top else "."))
    return '<ul class="eb-why-l">' + "".join(f"<li>{t}</li>" for t in items) + "</ul>"


def _night_picks(rep):
    rows = ""
    for n, p in enumerate(rep["picks"]):
        over = p["side"] == "o"
        cls, lab = RES_TAG.get(p.get("result"), ("", "En attente"))
        price, last = _dec(p["first_price"]), _dec(p.get("last_price"))
        pm, am = p["proj_min"], p["act_min"]
        mins = f'{pm:.0f} → {am:.0f}' if pm and am is not None else (f'{pm:.0f} → –' if pm else "–")
        act = p.get("actual")
        rk = {"win": 3, "push": 2, "void": 1, "loss": 0}.get(p.get("result"), -1)
        rows += (f'<tr class="eb-prop" data-i="{n}"><td data-v="{_e(p["name"])}">{_e(p["name"])} '
                 f'<span class="eb-dim">{_e(p["team"])}</span></td>'
                 f'<td data-v="{_e(p["stat"])}"><b>{"Over" if over else "Under"} {p["line"]:g}</b> {_e(UNIT.get(p["stat"], p["stat"]))}</td>'
                 f'<td class="n" data-v="{price:.3f}"><b>{price:.2f}</b>'
                 + (f'<span class="eb-dim"> → {last:.2f}</span>' if last and abs(last - price) >= 0.02 else "") + "</td>"
                 f'<td class="n" data-v="{act if act is not None else -1}">{act if act is not None else "–"}</td>'
                 f'<td class="n" data-v="{(am or 0) - (pm or 0):.1f}">{mins}</td>'
                 f'<td data-v="{rk}"><span class="eb-tag {cls}">{lab}</span></td>'
                 f'<td class="eb-cause">{_e(p["cause_txt"])}</td></tr>')
    if not rows:
        return '<div class="eb-empty">Aucun pick proposé pour cette soirée.</div>'
    head = "".join(f'<th class="eb-sort" onclick="ebSort(this)" title="Trier">{h}</th>'
                   for h in ("Joueur", "Pari", "Cote prise", "Réel", "Minutes", "Résultat")) + "<th>Explication</th>"
    return f'<div class="eb-scroll"><table class="eb-tbl" data-all="1"><tr>{head}</tr>{rows}</table></div>'


def _night_games(rep):
    rows = ""
    for r in rep["games"]:
        s, f, v = r["sim"], r.get("final"), r.get("vegas") or {}
        fav = r["home"] if s["p_home"] >= 0.5 else r["away"]
        vg = ""
        if v.get("spread") is not None:
            vg = f'{_e(r["home"] if v["spread"] <= 0 else r["away"])} −{abs(v["spread"]):g}'
            if v.get("total"): vg += f' · O/U {v["total"]:g}'
        final = f'<b>{f["away"]}-{f["home"]}</b>' if f else "–"
        res = (f'<span class="eb-tag {"acc" if r["winner_ok"] else "neg"}">{"trouvé" if r["winner_ok"] else "raté"}</span>'
               if f else '<span class="eb-tag">à venir</span>')
        rows += (f'<tr><td><b>{_e(r["away"])} @ {_e(r["home"])}</b></td>'
                 f'<td class="n">{s["away"]:.0f}-{s["home"]:.0f} <span class="eb-dim">({_e(fav)} '
                 f'{_pct(max(s["p_home"], 1 - s["p_home"]), 0)})</span></td>'
                 f'<td class="n">{vg or "–"}</td><td class="n">{final}</td><td>{res}</td></tr>')
    return ('<div class="eb-scroll"><table class="eb-tbl"><tr><th>Match</th><th>Simulateur</th><th>Vegas</th>'
            f'<th>Final</th><th>Vainqueur</th></tr>{rows}</table></div>')


def _night_detail(rep, cum, insights):
    n, s = rep["night"], rep["sum"]
    d = datetime.strptime(n, "%Y-%m-%d")
    many = rep["total"] > 1
    state = (f'{rep["done"]}/{rep["total"]} matchs terminés' if rep["done"] < rep["total"]
             else f'{rep["total"]} match{"s" if many else ""} terminé{"s" if many else ""}')
    stats = ""
    if s["n"]:
        stats += _stat("Picks", _record(s), f'{s["n"]} réglés' + (f' · {s["void"]} remb.' if s["void"] else ""))
        stats += _stat("Résultat", _u(s["units"]), f'ROI {_sgn(100 * s["roi"])} %', "eb-acc" if s["units"] >= 0 else "eb-neg")
    if s["with_us"] is not None:
        stats += _stat("Cotes dans notre sens", f'{100 * s["with_us"]:.0f} %',
                       f'{s["n_moved"]} cotes ont bougé · backtest 74-80 %', "eb-acc" if s["with_us"] >= 0.6 else "eb-neg")
    if s["clv"] is not None:
        stats += _stat("CLV", f'{_sgn(100 * s["clv"])} %', "cote prise vs clôture sans marge",
                       "eb-acc" if s["clv"] >= 0 else "eb-neg")
    c = cum["sum"]
    cum_txt = ""
    if c["n"]:
        cum_txt = (f'Depuis le début ({cum["nights"]} soirée{"s" if cum["nights"] > 1 else ""}) : <b>{_record(c)}</b> · '
                   f'<b class="{"eb-acc" if c["units"] >= 0 else "eb-neg"}">{_u(c["units"])}</b> · ROI {_sgn(100 * c["roi"])} %')
    return (f'<section class="eb-detail" data-id="bilan-{n}">'
            f'<div class="eb-card"><div class="eb-meta">Bilan · soirée NBA du {JOURS_L[d.weekday()]} {d.day} '
            f'{MOIS[d.month - 1]} · {state}</div>'
            "<h3 class=\"eb-h eb-bil-t\">Ce que le simulateur avait prévu, ce qui s'est passé</h3>"
            + (f'<div class="eb-stats">{stats}</div>' if stats else "") + "</div>"
            f"<div class=\"eb-card\"><h3 class=\"eb-h\">Ce qui s'est passé</h3>{_night_facts(rep)}</div>"
            '<div class="eb-card"><h3 class="eb-h">Picks de la soirée <small>cote prise → dernière cote avant le match · '
            f'minutes prévues → jouées</small></h3>{_night_picks(rep)}</div>'
            f'<div class="eb-card"><h3 class="eb-h">Matchs</h3>{_night_games(rep)}</div>'
            "<div class=\"eb-card\"><h3 class=\"eb-h\">Comment l'améliorer</h3>"
            '<ul class="eb-why-l">' + "".join(f"<li>{t}</li>" for t in insights) + "</ul>"
            + (f'<p class="eb-note">{cum_txt}</p>' if cum_txt else "") + "</div></section>")


def _history(max_nights=10):
    """(items de la liste, fiches, nombre) des dernieres soirees dont au moins un match est termine."""
    try:
        import nba_sim_bilan as B
        hist, picks = B.load_history(), B.load_picks()
        cum = B.cumulative(hist, picks)
        reps = [B.night_report(n, hist, picks) for n, (t, d) in B.nights(hist).items() if d][:max_nights]
        if not reps and hist["games"]:   # rien de termine : on annonce le premier bilan
            first = datetime.strptime(min(h["night"] for h in hist["games"].values()), "%Y-%m-%d")
            return (f'<div class="eb-card eb-meta">Premier bilan (soirée du {first:%d/%m}) ici dès la fin des matchs : '
                    "picks, ce qui s'est passé et comment améliorer le simulateur.</div>", "", 0)
        return ("".join(_night_item(r) for r in reps),
                "".join(_night_detail(r, cum, B.insights(r, cum)) for r in reps), len(reps))
    except Exception as e:
        print(f"  [!] historique NBA : {type(e).__name__}: {e}")
        return "", "", 0


def build_nba_sim_section(sim=None):
    sim = sim if sim is not None else load_sim()
    games = sorted((sim or {}).get("games") or [], key=lambda g: g.get("date", ""))
    h_items, h_details, n_nights = _history()
    if not games and not n_nights:
        return '<!--eb:start--><div class="eb"><div class="eb-card eb-empty">Aucune simulation NBA disponible.</div></div><!--eb:end-->'
    now = datetime.now(TZ_PARIS)
    ids = [_e(g["gid"]) for g in games]
    items = "".join(_item(g, gid, now, i == 0) for i, (g, gid) in enumerate(zip(games, ids)))
    picks = load_picks()
    details = "".join(
        f'<section class="eb-detail{" on" if i == 0 else ""}" data-id="{gid}">'
        f'{_header(g, now)}{_selection(g, picks)}{_infos(g)}{_infobar(g)}{_kpis(g)}{_video(g, gid)}{_hist(g)}{_tabs(g, gid)}</section>'
        for i, (g, gid) in enumerate(zip(games, ids)))
    if not games:   # pas de match a venir : le bilan le plus recent est affiche
        h_items = h_items.replace('class="eb-item"', 'class="eb-item on"', 1)
        h_details = h_details.replace('class="eb-detail"', 'class="eb-detail on"', 1)
    side = f'<div class="eb-label">Matchs NBA à venir · {len(games)}</div><div class="eb-list">{items}</div>' if games else ""
    if h_items:
        side += f'<div class="eb-label eb-label-2">Historique · bilan des soirées</div><div class="eb-list">{h_items}</div>'
    return (f'<!--nba-sim:{_e((sim or {}).get("generated_at", ""))}-->'
            '<!--eb:start--><div class="eb" data-eb="nba">' + '<div class="eb-layout">'
            f'<aside class="eb-side">{side}</aside><div class="eb-main">{details}{h_details}</div></div></div>'
            + EB_SCRIPT + PROPS_SCRIPT + _player_script() + '<!--eb:end-->')


def build_nba_sim_page():
    """(bloc pour index.html, contenu de nba_sim.html). Le bloc embarque la section du moment (secours)
    et un chargeur qui la remplace par nba_sim.html des qu'une version plus recente est publiee par
    la veille (nba_live.py) ; la page se remet a jour quand on revient sur l'onglet."""
    sec = build_nba_sim_section()
    v = sec[len("<!--nba-sim:"):sec.index("-->")] if sec.startswith("<!--nba-sim:") else ""
    return f'<div id="eb-nba-wrap" data-v="{v}">{sec}</div>' + LOADER_SCRIPT, sec


def write_fragment(sec=None):
    with open(FRAGMENT, "w", encoding="utf-8") as f:
        f.write(sec if sec is not None else build_nba_sim_section())


LOADER_SCRIPT = """<script>
(function(){
  var wrap=document.getElementById('eb-nba-wrap');if(!wrap||window.ebNbaLive)return;
  var last=0;
  function run(el){el.querySelectorAll('script').forEach(function(s){var n=document.createElement('script');
    Array.prototype.forEach.call(s.attributes,function(a){n.setAttribute(a.name,a.value);});
    n.textContent=s.textContent;s.parentNode.replaceChild(n,s);});}
  window.ebNbaLive=function(){
    last=Date.now();
    fetch('""" + FRAGMENT + """?t='+Date.now(),{cache:'no-store'}).then(function(r){if(!r.ok)throw 0;return r.text();})
      .then(function(h){
        var m=/^<!--nba-sim:([^>]*)-->/.exec(h);if(!m||m[1]<=(wrap.getAttribute('data-v')||''))return;
        var on=wrap.querySelector('.eb-item.on'),id=on&&on.getAttribute('data-id');
        wrap.innerHTML=h;wrap.setAttribute('data-v',m[1]);run(wrap);
        if(id&&wrap.querySelector('.eb-item[data-id="'+id+'"]')&&window.ebPick)ebPick('nba',id);
      }).catch(function(){});
  };
  ebNbaLive();
  document.addEventListener('visibilitychange',function(){
    if(document.visibilityState==='visible'&&Date.now()-last>120000)ebNbaLive();});
})();
</script>"""


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
