"""
nba_props_edge.py — « Props du soir » NBA facon PropsEdge / PrizePicks (Wilson, 05/11/2026 : « analyse pareil que ce
post : DeAnthony Melton plus de 6 reb+pas, les books a 5,5, environ -119 sans le jus, 5 sur 8 cette saison, mediane de
8 sur ses 10 derniers »).

Pour chaque prop DraftKings du soir (via ESPN, gratuit ; stats simples et combinees : PTS, REB, AST, 3PM, PTS+REB,
PTS+AST, REB+AST, PTS+REB+AST) :
  - probabilite SANS LA MARGE du bookmaker a sa ligne (p_mkt), corrigee de 10 % de l'ecart de ma simulation
    (poids valide sur 613 selections reelles, comme les picks du jour) -> cote juste ;
  - historique du joueur : saison, face a l'adversaire du soir (2 saisons), L5, L10, L20, moyenne et mediane ;
  - « prends si ta cote >= X » : X = cote juste + 3 % (seuil de rentabilite avec marge de securite).
Pourquoi ce seuil : le pick du post est rentable parce que PrizePicks paie un prix FIXE (~-118 par jambe) au-dessus de
la probabilite ; chez Betclic / Winamax, c'est rentable seulement si leur cote depasse la cote juste.
Selection (5 au plus) : cote du cote choisi au moins 1,60, historique ET marche dans le meme sens (L10 et saison >= 60 %,
mediane du bon cote de la ligne), simulation pas contre. L'historique seul ne prouve rien (le marche le connait) :
il sert a eviter les paris que tout contredit. Suivi reel (data/props_edge.json) : resultat et CLV.
"""
import json, statistics
from datetime import datetime, timedelta, timezone
from pathlib import Path

PATH = Path("data/props_edge.json")
LABEL = {"PTS": "points", "REB": "rebonds", "AST": "passes", "3PM": "paniers à 3 pts", "PR": "points + rebonds",
         "PA": "points + passes", "RA": "rebonds + passes", "PRA": "points + rebonds + passes", "STL": "interceptions",
         "BLK": "contres"}
SHORT = {"PR": "PTS+REB", "PA": "PTS+AST", "RA": "REB+AST", "PRA": "PTS+REB+AST"}
W_PROP, EDGE, MIN_ODDS, MAX_SHOW = 0.10, 0.03, 1.60, 5


def _t(iso):
    return datetime.fromisoformat(str(iso).replace("Z", "+00:00"))


def _dec(am):
    return 1 + (am / 100 if am > 0 else 100 / -am)


def _am(p):
    """Cote americaine equivalente a une probabilite (pour la lecture « -119 » comme dans le post)."""
    return round(-100 * p / (1 - p)) if p >= 0.5 else round(100 * (1 - p) / p)


def _val(row, stat):
    if stat in ("PTS", "REB", "AST", "3PM", "STL", "BLK"): return row.get(stat, 0)
    return sum(row.get(k, 0) for k in {"PR": ("PTS", "REB"), "PA": ("PTS", "AST"), "RA": ("REB", "AST"),
                                       "PRA": ("PTS", "REB", "AST")}[stat])


def game_logs(years):
    """{athlete_id: [(date, adversaire, ligne de stats), ...] du plus recent au plus ancien} (matchs joues)."""
    import nba_boxdb as B
    logs = {}
    for y in years:
        for g in B.load_season(y).values():
            if g.get("type") not in (None, "regular", "postseason", "playoffs"): continue
            for side, opp in (("home", "away"), ("away", "home")):
                for r in (g.get(side) or {}).get("players") or []:
                    if r.get("dnp") or not r.get("MIN"): continue
                    logs.setdefault(r["id"], []).append((g["date"], g[opp]["abbr"], r, y))
    for v in logs.values(): v.sort(key=lambda x: x[0], reverse=True)
    return logs


def history(log, stat, line, over, opp, season):
    vals = [(d, o, _val(r, stat), y) for d, o, r, y in log]
    hit = lambda L: (sum(1 for x in L if (x[2] > line) == over), len(L))
    cur = [x for x in vals if x[3] == season]
    return {"season": hit(cur), "h2h": hit([x for x in vals if x[1] == opp]), "l5": hit(vals[:5]),
            "l10": hit(vals[:10]), "l20": hit(vals[:20]),
            "avg": round(statistics.mean(x[2] for x in vals[:10]), 1) if vals else None,
            "med": statistics.median([x[2] for x in vals[:10]]) if vals else None,
            "bars": [[d[5:], o, v] for d, o, v, _ in vals[:10]][::-1]}


def candidates(now=None, hours=24):
    now = now or datetime.now(timezone.utc)
    try:
        sim = json.loads(Path("data/nba_sim.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    import nba_boxdb as B
    season = B.season_year()
    logs = game_logs((season - 1, season))
    out = []
    for g in sim.get("games") or []:
        ko = _t(g["date"])
        if not timedelta(0) < ko - now <= timedelta(hours=hours): continue
        for pr in g.get("props") or []:
            if pr.get("p_play", 0) < 0.9 or pr.get("over") is None or pr.get("under") is None: continue
            if pr["stat"] not in LABEL: continue
            log = logs.get(str(pr["id"])) or []
            if len(log) < 10: continue
            opp = g["away"]["abbr"] if pr["side"] == "home" else g["home"]["abbr"]
            for over in (True, False):
                pm = pr["p_mkt"] if over else 1 - pr["p_mkt"]
                ps = pr["p_sim"] if over else 1 - pr["p_sim"]
                p = pm + W_PROP * (ps - pm)
                fair = 1 / p
                if fair * (1 + EDGE) < MIN_ODDS: continue
                h = history(log, pr["stat"], pr["line"], over, opp, season)
                rate = lambda k: h[k][0] / h[k][1] if h[k][1] else None
                ok_hist = (rate("l10") or 0) >= 0.6 and (rate("season") or 0) >= 0.6 and h["med"] is not None \
                    and ((h["med"] > pr["line"]) == over)
                if not ok_hist or ps < pm - 0.02: continue
                out.append({"gid": g["gid"], "ko": g["date"], "match": f'{g["away"]["abbr"]} @ {g["home"]["abbr"]}',
                            "opp": opp, "id": str(pr["id"]), "name": pr["name"], "stat": pr["stat"], "line": pr["line"],
                            "over": over, "p_mkt": round(pm, 4), "p_sim": round(ps, 4), "p": round(p, 4),
                            "fair": round(fair, 2), "take": round(fair * (1 + EDGE), 2), "am_novig": _am(pm),
                            "dk": pr["over"] if over else pr["under"], "hist": h})
    # les plus probables d'abord, a historique egal ; un seul prop par joueur
    out.sort(key=lambda x: (-x["p"], -(x["hist"]["l10"][0] / max(x["hist"]["l10"][1], 1))))
    seen, top = set(), []
    for x in out:
        if x["id"] in seen: continue
        seen.add(x["id"]); top.append(x)
        if len(top) >= MAX_SHOW: break
    return top


# ─── Suivi reel ──────────────────────────────────────────────────────────────
def record(props, now=None):
    now = now or datetime.now(timezone.utc)
    try:
        st = json.loads(PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        st = {"props": []}
    have = {(p["gid"], p["id"], p["stat"], p["line"], p["over"]) for p in st["props"]}
    for x in props:
        k = (x["gid"], x["id"], x["stat"], x["line"], x["over"])
        if k in have: continue
        st["props"].append({kk: x[kk] for kk in ("gid", "ko", "match", "id", "name", "stat", "line", "over", "p_mkt", "p", "fair", "take")}
                           | {"at": now.isoformat(timespec="minutes")})
    PATH.write_text(json.dumps(st, ensure_ascii=False, indent=0), encoding="utf-8")


def resolve():
    """Regle les props suivis d'apres les feuilles de match (stat reelle vs ligne)."""
    try:
        st = json.loads(PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return 0
    import nba_boxdb as B
    db = {}
    for y in (B.season_year() - 1, B.season_year()): db.update(B.load_season(y))
    n = 0
    for p in st["props"]:
        if p.get("result"): continue
        g = db.get(str(p["gid"]))
        if not g: continue
        row = next((r for s in ("home", "away") for r in (g.get(s) or {}).get("players") or [] if r.get("id") == p["id"]), None)
        if row is None: continue
        if row.get("dnp") or not row.get("MIN"):
            p["result"] = "void"
        else:
            v = _val(row, p["stat"]); p["actual"] = v
            p["result"] = "push" if v == p["line"] else ("win" if (v > p["line"]) == p["over"] else "loss")
        n += 1
    if n: PATH.write_text(json.dumps(st, ensure_ascii=False, indent=0), encoding="utf-8")
    return n


def stats():
    try:
        P = json.loads(PATH.read_text(encoding="utf-8"))["props"]
    except (OSError, ValueError, KeyError):
        return None
    done = [p for p in P if p.get("result") in ("win", "loss")]
    if not done: return None
    w = sum(p["result"] == "win" for p in done)
    exp = sum(p["p"] for p in done)
    return {"n": len(done), "w": w, "expected": round(exp, 1),
            "roi_at_take": round(sum((p["take"] - 1) if p["result"] == "win" else -1 for p in done) / len(done), 4)}


# ─── Affichage ───────────────────────────────────────────────────────────────
def _e(s):
    import html
    return html.escape(str(s if s is not None else ""))


def _fr(x, d=2):
    return f"{x:.{d}f}".replace(".", ",")


def card_html(x):
    h, line = x["hist"], x["line"]
    side = "Plus" if x["over"] else "Moins"
    top = max([v for _, _, v in h["bars"]] + [line]) * 1.2 or 1
    bars = "".join(
        f'<div class="pe-b {"ok" if (v > line) == x["over"] else "ko"}" title="{_e(d)} · {_e(o)} · {v:g}"><em>{v:g}</em>'
        f'<i style="height:{100 * v / top:.1f}%"></i><small>{_e(o)}</small></div>' for d, o, v in h["bars"])
    chip = lambda lab, k: (f'<div class="pe-c"><small>{lab}</small><b>{h[k][0]}/{h[k][1]}</b>'
                           f'<span>{round(100 * h[k][0] / h[k][1]) if h[k][1] else 0} %</span></div>')
    stat = SHORT.get(x["stat"], LABEL[x["stat"]])
    return (f'<div class="pe-card"><div class="pe-hd"><div><b class="pe-n">{_e(x["name"])}</b>'
            f'<span class="eb-dim"> · {_e(x["match"])} · {datetime.fromisoformat(x["ko"].replace("Z", "+00:00")).astimezone(_tz()):%H:%M}</span></div>'
            f'<span class="pe-p">{_am(x["p"]):+d}</span></div>'
            f'<div class="pe-line">{side} de <b>{line:g}</b> {_e(LABEL[x["stat"]])}'
            + (f' <span class="eb-dim">({_e(stat)})</span>' if x["stat"] in SHORT else "") + '</div>'
            f'<div class="pe-plot"><div class="pe-ln" style="bottom:{100 * line / top:.1f}%"><span>{line:g}</span></div>{bars}</div>'
            f'<div class="pe-chips">{chip("Saison", "season")}{chip("H2H", "h2h")}{chip("L5", "l5")}{chip("L10", "l10")}{chip("L20", "l20")}</div>'
            f'<div class="pe-ft"><span>Moyenne L10 <b>{_fr(h["avg"], 1)}</b></span><span>Médiane L10 <b>{h["med"]:g}</b></span></div>'
            f'<p class="pe-why">DraftKings : {x["dk"]:+d} ; <b>{x["am_novig"]:+d} sans la marge</b> ({round(100 * x["p_mkt"])} %), '
            f'ma simu {round(100 * x["p_sim"])} % → <b>cote juste {_fr(x["fair"])}</b>. '
            f'<b class="eb-acc">Prends si ta cote ≥ {_fr(x["take"])}</b> (Betclic, Winamax…), sinon passe.</p></div>')


def _tz():
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo("Europe/Paris")
    except Exception:
        return timezone(timedelta(hours=1))


CSS = """<style>
.pe-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:12px}
.pe-card{background:var(--panel2,#141b16);border:1px solid var(--line);border-radius:12px;padding:14px}
.pe-hd{display:flex;justify-content:space-between;align-items:flex-start;gap:8px}
.pe-n{font-size:15.5px}
.pe-p{font:700 12px var(--mono);color:#c084fc;background:rgba(192,132,252,.12);border-radius:6px;padding:3px 7px}
.pe-line{font-size:14px;margin:6px 0 10px}
.pe-plot{position:relative;display:flex;align-items:flex-end;gap:3px;height:120px;border-bottom:1px solid var(--line);padding-right:30px}
.pe-b{flex:1;min-width:0;height:100%;display:flex;flex-direction:column;justify-content:flex-end;align-items:center}
.pe-b em{font:10px var(--mono);font-style:normal;color:var(--mu);margin-bottom:2px}
.pe-b i{display:block;width:100%;border-radius:4px 4px 0 0;min-height:2px}
.pe-b.ok i{background:var(--acc)} .pe-b.ko i{background:var(--loss);opacity:.8}
.pe-b small{font:9px var(--mono);color:var(--mu);margin-top:3px;position:absolute;bottom:-15px}
.pe-b{position:relative}
.pe-ln{position:absolute;left:0;right:0;border-top:2px dashed #e5e7eb;opacity:.7}
.pe-ln span{position:absolute;right:0;top:-9px;font:600 10.5px var(--mono);background:var(--panel2,#141b16);padding:0 3px}
.pe-chips{display:grid;grid-template-columns:repeat(5,1fr);gap:4px;margin-top:22px}
.pe-c{background:var(--chip);border-radius:8px;padding:5px 2px;text-align:center;display:flex;flex-direction:column}
.pe-c small{font:10px var(--mono);color:var(--mu)} .pe-c b{font-size:13px} .pe-c span{font:10.5px var(--mono);color:var(--acc)}
.pe-ft{display:flex;justify-content:space-between;font-size:12.5px;color:var(--mu);margin-top:8px}
.pe-why{font-size:12.5px;line-height:1.5;margin:8px 0 0;color:var(--tx)}
</style>"""


def section_html(now=None):
    """Carte « Props du soir » (onglet Aujourd'hui et Basket > Picks)."""
    try:
        P = candidates(now)
    except Exception as e:
        print(f"  [!] props du soir : {type(e).__name__}: {e}")
        return ""
    s = stats()
    track = (f'Suivi réel : {s["w"]}/{s["n"]} gagnés (attendu {_fr(s["expected"], 1)}) ; aux cotes « prends si », ROI '
             f'{100 * s["roi_at_take"]:+.0f} %.'.replace(".", ",", 0) if s else "Suivi réel : chaque prop affiché est réglé sur la feuille de match.")
    body = (f'<div class="pe-grid">{"".join(card_html(x) for x in P)}</div>' if P else
            '<p class="td-none">Aucun prop ce soir où le marché, ma simulation et l’historique vont dans le même sens à une '
            'cote d’au moins 1,60.</p>')
    return (CSS + '<div class="eb-card"><h3 class="eb-h">📊 Props du soir <small>façon PropsEdge · marché sans marge + '
            'historique + ma simu</small></h3>' + body +
            '<p class="eb-note">Cote juste = probabilité DraftKings sans sa marge, corrigée de 10 % de l’écart de ma simulation '
            '(réglage validé sur 613 sélections réelles). L’historique (saison, face à l’adversaire, L5, L10, L20) sert de '
            'garde-fou : le marché le connaît déjà, il ne crée pas d’avantage à lui seul. Le pari est rentable seulement si ton '
            f'bookmaker paie au moins la cote « prends si » (cote juste + 3 %). {track}</p></div>')
