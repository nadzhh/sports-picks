"""
props_edge_track.py — Suivi réel des cartes « Props du soir » façon PropsEdge / PrizePicks (nba_props_edge.py).

Idée validée par Wilson (16/11/2026) : mesurer la fiabilité des props NBA affichées, en séparant les props DraftKings
des stats combinées DÉDUITES (REB+AST « RA », ligne reconstruite à partir des lignes rebonds et passes, façon PrizePicks),
comme c'est déjà fait pour les picks du simulateur (data/nba_sim_picks.json).

Fichier data/props_edge_track.json (une ligne JSON par carte, diffs git lisibles) :
  {"v": 1, "props": [ {
     "key": "gid|id|stat|o/u", "type": "dk" | "derived", "gid", "ko", "match", "opp", "id", "name", "stat",
     "side": "o"/"u", "line", "p" (probabilité annoncée), "p_mkt", "p_sim", "fair" (cote juste), "take" (prends si),
     "dk" (cote DraftKings américaine, absente pour une ligne déduite), "derived" ({"REB": l, "AST": l} ou null),
     "first_seen", "rank" (meilleur rang vu, 1-3 = Telegram, 1-10 = affichées d'emblée), "n_seen",
     "last_seen", "last_line", "last_p", "last_dk"   (dernière version vue avant le match, ~ clôture),
     "actual" (valeur réalisée), "result": "win" | "loss" | "push" | "void" (joueur absent), "src"
  } ] }
Une carte = une fois par match / joueur / stat / sens (la ligne bouge dans la soirée : la PREMIÈRE version est celle
annoncée et réglée, la dernière est gardée à part).

Appels : nba_live.py (veille CI) -> record(PE.candidates()) à chaque cycle, resolve() avec la bankroll ;
nba_sim_site.py -> bilan_html() sous la carte « Props du soir » (Basket > Picks).
Reprise : au premier passage, les cartes déjà suivies dans data/props_edge.json (depuis le 05/11/2026, sans cote DK)
sont reprises avec leurs résultats (src « reprise »).

  python props_edge_track.py            reprise si besoin + règlement + bilan en console
  python props_edge_track.py --html f   écrit aussi le bilan HTML dans f (aperçu)
"""
import json, math, sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

PATH = Path("data/props_edge_track.json")
LEGACY = Path("data/props_edge.json")
COMPS = {"PTS": ("PTS",), "REB": ("REB",), "AST": ("AST",), "3PM": ("3PM",), "STL": ("STL",), "BLK": ("BLK",),
         "PR": ("PTS", "REB"), "PA": ("PTS", "AST"), "RA": ("REB", "AST"), "PRA": ("PTS", "REB", "AST")}
LABEL = {"PTS": "Points", "REB": "Rebonds", "AST": "Passes", "3PM": "Paniers à 3 pts", "STL": "Interceptions",
         "BLK": "Contres", "PR": "PTS+REB", "PA": "PTS+AST", "RA": "REB+AST", "PRA": "PTS+REB+AST"}
TYPE_LABEL = {"dk": "Props DraftKings", "derived": "Combinées déduites (REB+AST)"}
SETTLE_AFTER = timedelta(hours=3)   # un match NBA dure ~2 h 30 : pas de règlement avant
MIN_N = 30                          # en dessous, la marge du hasard est trop large pour conclure


def _t(iso):
    return datetime.fromisoformat(str(iso).replace("Z", "+00:00"))


def _key(gid, pid, stat, over):
    return f"{gid}|{pid}|{stat}|{'o' if over else 'u'}"


def load():
    try:
        return json.loads(PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"v": 1, "props": []}


def save(st):
    """Une carte par ligne : le fichier est commité à chaque cycle de la veille, diffs courts."""
    rows = ",\n".join(json.dumps(r, ensure_ascii=False, separators=(",", ":")) for r in st["props"])
    PATH.write_text('{"v":1,"props":[\n' + rows + "\n]}\n", encoding="utf-8")


def import_legacy(st):
    """Reprend les cartes déjà suivies par nba_props_edge.record (data/props_edge.json, depuis le 05/11/2026).
    Pas de cote DK ni de rang à l'époque ; type déduit de la stat (REB+AST n'existe pas chez DraftKings via ESPN :
    toutes les RA sont des lignes déduites)."""
    try:
        old = json.loads(LEGACY.read_text(encoding="utf-8")).get("props") or []
    except (OSError, ValueError):
        return 0
    have = {r["key"] for r in st["props"]}
    n = 0
    for p in old:
        k = _key(p["gid"], p["id"], p["stat"], p["over"])
        if k in have: continue
        have.add(k)
        st["props"].append({"key": k, "type": "derived" if p["stat"] == "RA" else "dk", "gid": str(p["gid"]),
                            "ko": p["ko"], "match": p.get("match"), "id": str(p["id"]), "name": p["name"],
                            "stat": p["stat"], "side": "o" if p["over"] else "u", "line": p["line"], "p": p["p"],
                            "p_mkt": p.get("p_mkt"), "fair": p.get("fair"), "take": p.get("take"),
                            "first_seen": p.get("at"), "src": "reprise"}
                           | ({"actual": p["actual"]} if "actual" in p else {})
                           | ({"result": p["result"]} if p.get("result") else {}))
        n += 1
    return n


def record(cards, now=None):
    """Enregistre les cartes publiées (liste triée de nba_props_edge.candidates : rang 1-3 = Telegram, 1-10 = affichées
    d'emblée sur le site, les autres sous « Voir les autres props »). Ne change rien à la sélection."""
    now = now or datetime.now(timezone.utc)
    st = load()
    if not st["props"]: import_legacy(st)
    idx = {r["key"]: r for r in st["props"]}
    stamp = now.isoformat(timespec="minutes")
    for i, x in enumerate(cards or []):
        k = _key(x["gid"], x["id"], x["stat"], x["over"])
        r = idx.get(k)
        if r is None:
            r = {"key": k, "type": "derived" if x.get("derived") else "dk", "gid": str(x["gid"]), "ko": x["ko"],
                 "match": x.get("match"), "opp": x.get("opp"), "id": str(x["id"]), "name": x["name"], "stat": x["stat"],
                 "side": "o" if x["over"] else "u", "line": x["line"], "p": x["p"], "p_mkt": x.get("p_mkt"),
                 "p_sim": x.get("p_sim"), "fair": x.get("fair"), "take": x.get("take"),
                 "dk": None if x.get("derived") else x.get("dk"), "derived": x.get("derived"),
                 "first_seen": stamp, "rank": i + 1, "n_seen": 0, "src": "live"}
            st["props"].append(r); idx[k] = r
        if r.get("result") or now >= _t(r["ko"]): continue
        if r.get("src") == "reprise":   # carte reprise encore a venir : complete ce que l'ancien suivi ne notait pas
            for kk, vv in (("opp", x.get("opp")), ("p_sim", x.get("p_sim")), ("derived", x.get("derived")),
                           ("dk", None if x.get("derived") else x.get("dk"))):
                if r.get(kk) is None: r[kk] = vv
            r.setdefault("rank", i + 1)
        r["rank"] = min(r.get("rank") or 99, i + 1)
        r["n_seen"] = (r.get("n_seen") or 0) + 1
        r.update(last_seen=stamp, last_line=x["line"], last_p=x["p"], last_dk=None if x.get("derived") else x.get("dk"))
    save(st)
    return len(st["props"])


def _box(years):
    import nba_boxdb as B
    db = {}
    for y in years: db.update(B.load_season(y))
    return db


def resolve(now=None, box=None):
    """Règle les cartes d'après les feuilles de match (data/nba_db, comme nba_props_edge.resolve et le suivi du
    simulateur) : valeur réalisée vs ligne annoncée ; joueur absent de la feuille ou sans minute -> annulé (void).
    Si la feuille manque, reprend le résultat déjà réglé par nba_props_edge (même carte, même ligne)."""
    now = now or datetime.now(timezone.utc)
    st = load()
    if not st["props"]: import_legacy(st)
    if box is None:
        import nba_boxdb as B
        y = B.season_year()
        box = _box((y - 1, y))
    try:
        legacy = {_key(p["gid"], p["id"], p["stat"], p["over"]): p
                  for p in json.loads(LEGACY.read_text(encoding="utf-8")).get("props") or [] if p.get("result")}
    except (OSError, ValueError):
        legacy = {}
    n = 0
    for r in st["props"]:
        if r.get("result") or now < _t(r["ko"]) + SETTLE_AFTER: continue
        g = box.get(str(r["gid"]))
        if g:
            rows = [x for s in ("home", "away") for x in (g.get(s) or {}).get("players") or []]
            if not rows: continue
            row = next((x for x in rows if str(x.get("id")) == str(r["id"])), None)
            if row is None or row.get("dnp") or not row.get("MIN"):
                r["result"] = "void"; r.pop("actual", None)
            else:
                v = sum(row.get(c, 0) for c in COMPS[r["stat"]])
                r["actual"] = v
                r["result"] = "push" if v == r["line"] else ("win" if (v > r["line"]) == (r["side"] == "o") else "loss")
            r["settled"] = now.isoformat(timespec="minutes"); n += 1
        else:
            p = legacy.get(r["key"])
            if p and float(p["line"]) == float(r["line"]):
                r["result"] = p["result"]
                if "actual" in p: r["actual"] = p["actual"]
                r["settled"] = now.isoformat(timespec="minutes"); r["settled_by"] = "props_edge.json"; n += 1
    if n: save(st)
    return n


# ─── Bilan ───────────────────────────────────────────────────────────────────
def _group(rows):
    """Réussite réelle vs annoncée ; marge du hasard = ±2 écarts-types de la réussite si les probabilités annoncées
    étaient justes (somme de Bernoulli : sigma = racine(somme p(1-p)) / n)."""
    done = [r for r in rows if r.get("result") in ("win", "loss")]
    n = len(done)
    if not n: return None
    w = sum(r["result"] == "win" for r in done)
    p_ann = sum(r["p"] for r in done) / n
    sd = math.sqrt(sum(r["p"] * (1 - r["p"]) for r in done)) / n
    real = w / n
    roi = sum((r["take"] - 1) if r["result"] == "win" else -1 for r in done if r.get("take")) / max(
        sum(1 for r in done if r.get("take")), 1)
    dk = [r for r in done if r.get("dk")]
    roi_dk = (sum(((r["dk"] / 100 if r["dk"] > 0 else 100 / -r["dk"]) if r["result"] == "win" else -1) for r in dk)
              / len(dk)) if dk else None
    z = (real - p_ann) / sd if sd else 0
    return {"n": n, "w": w, "real": real, "ann": p_ann, "sd": sd, "z": z, "roi_take": roi, "roi_dk": roi_dk,
            "n_dk": len(dk), "void": sum(r.get("result") == "void" for r in rows),
            "push": sum(r.get("result") == "push" for r in rows)}


def bilan(st=None):
    st = st or load()
    P = st["props"]
    out = [("Toutes les cartes", _group(P), "all")]
    for t in ("dk", "derived"):
        out.append((TYPE_LABEL[t], _group([r for r in P if r["type"] == t]), "type"))
    top3 = [r for r in P if (r.get("rank") or 99) <= 3]
    if any(r.get("result") in ("win", "loss") for r in top3):
        out.append(("Top 3 du moment (≈ Telegram, depuis la reprise)", _group(top3), "type"))
    stats = sorted({r["stat"] for r in P}, key=lambda s: -sum(1 for r in P if r["stat"] == s))
    for s in stats:
        for t in ("dk", "derived"):
            rows = [r for r in P if r["stat"] == s and r["type"] == t]
            if rows: out.append((LABEL.get(s, s) + (" (déduite)" if t == "derived" else ""), _group(rows), "stat"))
    pending = sum(1 for r in P if not r.get("result"))
    return [x for x in out if x[1]], pending


def _pc(x, d=1):
    return f"{100 * x:.{d}f}".replace(".", ",") + " %"


def _sg(x, d=1):
    return ("+" if x >= 0 else "−") + f"{abs(100 * x):.{d}f}".replace(".", ",") + " %"


def _verdict(g):
    if g["n"] < MIN_N:
        return '<span class="eb-dim">trop peu pour conclure</span>'
    if abs(g["z"]) <= 2:
        return "conforme (écart dans le hasard)"
    return ('<b class="eb-acc">mieux qu’annoncé</b>' if g["z"] > 0 else '<b class="eb-neg">surestimé</b>')


def bilan_html():
    """Carte « Suivi réel des props » (sous « Props du soir », Basket > Picks)."""
    try:
        st = load()
        if not st["props"]: return ""
        G, pending = bilan(st)
    except Exception as e:
        print(f"  [!] suivi des props : {type(e).__name__}: {e}")
        return ""
    if not G: return ""
    import html
    e = lambda s: html.escape(str(s))
    rows = []
    for lab, g, kind in G:
        lo, hi = max(g["ann"] - 2 * g["sd"], 0), min(g["ann"] + 2 * g["sd"], 1)
        style = ' style="font-weight:600"' if kind in ("all", "type") else ""
        dk = (f'{_sg(g["roi_dk"])} <span class="eb-dim">({g["n_dk"]})</span>' if g["roi_dk"] is not None else "—")
        rows.append(f'<tr{style}><td>{"&nbsp;&nbsp;" if kind == "stat" else ""}{e(lab)}</td><td>{g["n"]}</td>'
                    f'<td>{g["w"]}-{g["n"] - g["w"]}</td><td><b>{_pc(g["real"])}</b></td><td>{_pc(g["ann"])}</td>'
                    f'<td>{_pc(lo, 0)} – {_pc(hi, 0)} <span class="eb-dim">(±{_pc(2 * g["sd"], 0)})</span></td>'
                    f'<td>{_verdict(g)}</td><td>{_sg(g["roi_take"])}</td>'
                    f'<td>{dk}</td></tr>')
    tot = G[0][1]
    first = min((r.get("first_seen") or "9") for r in st["props"])[:10]
    last = sorted((r for r in st["props"] if r.get("result") in ("win", "loss", "push", "void")),
                  key=lambda r: (r["ko"], r["name"]), reverse=True)[:15]
    res = {"win": '<b class="eb-acc">gagné</b>', "loss": '<b class="eb-neg">perdu</b>', "push": "remboursé",
           "void": '<span class="eb-dim">annulé (absent)</span>'}
    recent = "".join(
        f'<tr><td>{_t(r["ko"]).strftime("%d/%m")}</td><td>{e(r["name"])}</td><td>{"Plus" if r["side"] == "o" else "Moins"} de '
        f'{str(r["line"]).replace(".", ",")} {e(LABEL.get(r["stat"], r["stat"]))}{" <span class=eb-dim>(déduite)</span>" if r["type"] == "derived" else ""}</td>'
        f'<td>{_pc(r["p"], 0)}</td><td>{r["actual"] if "actual" in r else "—"}</td><td>{res[r["result"]]}</td></tr>'
        for r in last)
    return ('<div class="eb-card"><h3 class="eb-h">📈 Suivi réel des props <small>façon PropsEdge · DraftKings vs '
            'combinées déduites</small></h3>'
            f'<p class="eb-note">Depuis le {first[8:10]}/{first[5:7]} : <b>{tot["n"]}</b> cartes réglées, réussite '
            f'<b>{_pc(tot["real"])}</b> pour <b>{_pc(tot["ann"])}</b> annoncés ({tot["void"]} annulées, joueur absent ; '
            f'{pending} en attente). Chaque carte publiée est comptée une fois, à sa première ligne.</p>'
            '<div class="eb-scroll"><table class="eb-tbl"><tr><th>Groupe</th><th>Réglés</th><th>G-P</th><th>Réussite réelle</th>'
            '<th>Annoncée</th><th>Fourchette du hasard</th><th>Verdict</th><th>ROI « prends si »</th><th>ROI cote DK</th></tr>'
            + "".join(rows) + '</table></div>'
            '<p class="eb-note">Lecture : si mes probabilités sont justes, la réussite réelle tombe 95 fois sur 100 dans la '
            'fourchette du hasard (annoncée ± 2 écarts-types). Avec 20 paris à 60 %, cette fourchette fait ±22 points : un 15/20 ou un 9/20 ne prouve '
            f'rien. En dessous de {MIN_N} paris, pas de verdict. Les cartes d’un même joueur le même soir sont liées : la '
            'vraie fourchette est un peu plus large, un écart juste à la limite reste fragile. ROI « prends si » = gain moyen si chaque pari avait été pris '
            'exactement au seuil affiché (cote juste + 3 %, + 5 % pour les déduites) ; ROI cote DK = à la cote DraftKings '
            'notée à la première apparition (enregistrée depuis le 16/11 ; marge du bookmaker incluse, d’où un ROI '
            'attendu négatif).</p>'
            + (f'<details class="eb-det"><summary>Les {len(last)} derniers réglés</summary><div class="eb-scroll">'
               '<table class="eb-tbl"><tr><th>Date</th><th>Joueur</th><th>Pari</th><th>Annoncé</th><th>Réalisé</th>'
               f'<th>Résultat</th></tr>{recent}</table></div></details>' if last else "")
            + '</div>')


def _console(st):
    G, pending = bilan(st)
    print(f"  {len(st['props'])} cartes suivies, {pending} en attente")
    for lab, g, _ in G:
        # console Windows (cp1252) : ASCII seulement
        lab = lab.encode("ascii", "replace").decode()
        print(f"  {lab:<48} n={g['n']:>4}  {g['w']:>4}-{g['n'] - g['w']:<4} reel {100 * g['real']:5.1f} %  annonce "
              f"{100 * g['ann']:5.1f} %  +/-2 sd {200 * g['sd']:4.1f}  z={g['z']:+.1f}  ROI@take {100 * g['roi_take']:+.1f} %")


if __name__ == "__main__":
    st = load()
    if not st["props"]:
        print(f"  reprise de {LEGACY} : {import_legacy(st)} cartes"); save(st)
    print(f"  reglement : {resolve()} carte(s)")
    _console(load())
    if "--html" in sys.argv:
        out = Path(sys.argv[sys.argv.index("--html") + 1])
        out.write_text(bilan_html(), encoding="utf-8"); print(f"  bilan HTML : {out}")
