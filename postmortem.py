"""
postmortem.py — Post-mortem des paris (06/11/2026, Wilson : « des bots qui apprennent des erreurs de la veille »).

Pour chaque pari regle (Bankroll IA, canal Picks), une cause, en separant la MALCHANCE (bonne lecture, mauvais sort :
perdu d'un rien, xG favorables, carton rouge) de l'ERREUR DE LECTURE (minutes mal prevues, rendement loin de la
projection, xG contraires, marche parti contre nous). Puis, sur 30 jours :
  - repartition des causes des paris perdus ;
  - probabilite annoncee contre reussite reelle par source (si on annonce 60 % et qu'on gagne 50 %, on se trompe).
Sources : data/nba_bankroll.json, data/telegram_picks.json, data/nba_sim_history.json (minutes et stats projetees /
reelles), data/foot_sim_history.json (xG reels, cartons rouges). Appele chaque nuit par improve_bot (rapport + carte du
site) ; aucune decision automatique ici : les exclusions restent celles d'improve_bot.lessons (CLV), mesurees.
Usage : python postmortem.py
"""
import difflib, json, math, sys, unicodedata
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

OUT = Path("data/postmortem.json")
DAYS = 30
MIN_MISS = 6            # minutes d'ecart (dans le mauvais sens) qui expliquent une prop perdue
BLOWOUT = 20            # ecart final : titulaires sortis tot
NEAR = {"STL": 0.5, "BLK": 0.5, "3PM": 0.5, "REB": 1.0, "AST": 1.0}   # « perdu d'un rien » : une unite (1,5 pt sinon)
NBA_STATS = ("MIN", "PTS", "REB", "AST", "3PM", "STL", "BLK")
COMBO = {"PRA": ("PTS", "REB", "AST"), "PR": ("PTS", "REB"), "PA": ("PTS", "AST"), "RA": ("REB", "AST")}
LUCK = {"near", "xg", "rouge"}
CAUSE_FR = {"minutes": "minutes mal prévues", "rate": "rendement sous la projection", "near": "perdu d'un rien",
            "xg": "malchance (xG favorables)", "rouge": "carton rouge", "lecture": "lecture contredite par le match (xG)",
            "market": "marché parti contre nous", "other": "sans détail"}


def _load(p, d):
    try:
        return json.loads(Path(p).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return d


def _norm(s):
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()
    return " ".join(s.replace("-", " ").split())


def _val(arr, stat, base):
    """Valeur d'une stat (ou d'un combine) dans un tableau [MIN, PTS, REB, ...] (base = indice de MIN)."""
    comps = COMBO.get(stat, (stat,))
    if any(c not in NBA_STATS for c in comps): return None
    try:
        return sum(arr[base + NBA_STATS.index(c)] for c in comps)
    except (TypeError, IndexError):
        return None


# ─── Diagnostic d'un pari ────────────────────────────────────────────────────
def nba_prop(gid, pid, stat, line, side, hist):
    """(cause, texte) d'une prop NBA perdue."""
    h = (hist.get("games") or {}).get(str(gid)) or {}
    pl, act = (h.get("players") or {}).get(str(pid)), (h.get("actual") or {}).get(str(pid))
    if not act: return "other", "match ou feuille de stats introuvable"
    am, a = act[0], _val(act, stat, 0)
    pm = pl[3] if pl else None
    proj = _val(pl, stat, 3) if pl else None
    f = h.get("final") or {}
    margin = abs(f.get("home", 0) - f.get("away", 0)) if f else 0
    over = side == "o"
    if pm and ((pm - am >= MIN_MISS and over) or (am - pm >= MIN_MISS and not over)):
        why = f" (match plié de {margin} pts)" if margin >= BLOWOUT and am < pm else ""
        return "minutes", f"{am:.0f} min jouées pour {pm:.0f} prévues{why}"
    if a is not None and abs(a - line) <= NEAR.get(stat, 1.5):
        return "near", f"{a:g} pour une ligne à {line:g}"
    if a is not None and proj is not None and pm:
        return "rate", f"{a:g} en {am:.0f} min (projeté {proj * am / max(pm, 1):.1f} à ce temps de jeu)"
    return "rate", f"{a:g} en {am:.0f} min" if a is not None else "rendement"


def _foot_game(fh, home, away, day):
    best, sc = None, 0.0
    for g in (fh.get("games") or {}).values() if isinstance(fh.get("games"), dict) else fh.get("games") or []:
        if not g.get("final") or abs((datetime.fromisoformat(g["day"]) - datetime.fromisoformat(day[:10])).days) > 1: continue
        s = (difflib.SequenceMatcher(None, _norm(home), _norm(g["home"])).ratio()
             + difflib.SequenceMatcher(None, _norm(away), _norm(g["away"])).ratio()) / 2
        if s > sc: best, sc = g, s
    return best if sc >= 0.6 else None


def foot_bet(label, home, away, day, fh):
    """(cause, texte) d'un pari foot perdu (1X2, plus/moins de 2,5, les deux marquent)."""
    g = _foot_game(fh, home, away, day)
    if not g: return "other", "match introuvable dans l'historique"
    xh, xa = g.get("xg_real") or [None, None]
    fin = g["final"]
    red = f", {g['reds']} carton(s) rouge(s)" if g.get("reds") else ""
    if xh is None: return "other", f"score {fin[0]}-{fin[1]}{red}"
    sc = f"score {fin[0]}-{fin[1]}, xG {xh:.1f}-{xa:.1f}{red}"
    L = _norm(label)
    if "2.5" in L or "2,5" in L:
        good = (xh + xa >= 2.6) if L.startswith("plus") else (xh + xa <= 2.4)
    elif "deux" in L or "btts" in L:
        good = (min(xh, xa) >= 0.9) if "non" not in L else (min(xh, xa) <= 0.7)
    elif "nul" in L:
        good = abs(xh - xa) <= 0.3
    elif _norm(home) and _norm(home).split()[0] in L:
        good = xh - xa >= 0.4
    elif _norm(away) and _norm(away).split()[0] in L:
        good = xa - xh >= 0.4
    else:
        return "other", sc
    if good: return "xg", sc
    if g.get("reds"): return "rouge", sc
    return "lecture", sc


# ─── Collecte ────────────────────────────────────────────────────────────────
def collect(now=None):
    now = now or datetime.now(timezone.utc)
    cut = (now - timedelta(days=DAYS)).strftime("%Y-%m-%d")
    hist, fh = _load("data/nba_sim_history.json", {}), _load("data/foot_sim_history.json", {})
    rows = []
    for b in _load("data/nba_bankroll.json", {}).get("bets") or []:
        if b.get("status") not in ("won", "lost") or (b.get("night") or "") < cut: continue
        legs = b.get("legs") or []
        row = {"src": "Bankroll IA", "night": b.get("night"), "p": b.get("p"), "price": b.get("odds"),
               "win": b["status"] == "won", "kind": b.get("kind"), "stake": b.get("stake"),
               "label": " + ".join(f"{l.get('name', '')} {'+' if l.get('side') == 'o' else '-'}{l.get('line', '')} {l.get('stat', '')}".strip()
                                   for l in legs)[:120]}
        if not row["win"]:
            lost = [l for l in legs if l.get("result") == "loss"] or legs
            l = lost[0] if lost else {}
            if l.get("pid") and l.get("stat"):
                row["cause"], row["txt"] = nba_prop(l.get("gid"), l["pid"], l["stat"], l.get("line"), l.get("side"), hist)
            else:
                row["cause"], row["txt"] = "other", ""
        rows.append(row)
    T = _load("data/telegram_picks.json", {}).get("picks") or {}
    for p in (T.values() if isinstance(T, dict) else T):
        if p.get("result") not in ("win", "loss") or (p.get("day") or "") < cut: continue
        row = {"src": "Canal Picks", "night": p.get("day"), "p": p.get("p"), "price": p.get("price"), "win": p["result"] == "win",
               "kind": p.get("kind"), "stake": p.get("stake"), "label": f"{p.get('label', '')} ({p.get('match', '')})"[:120]}
        if not row["win"]:
            r = p.get("res") or {}
            if p.get("sport") == "nba" and r.get("pid"):
                row["cause"], row["txt"] = nba_prop(r.get("gid"), r["pid"], r.get("stat"), r.get("line"), r.get("side"), hist)
            elif p.get("sport") == "foot" and r.get("home"):
                row["cause"], row["txt"] = foot_bet(p.get("label", ""), r["home"], r["away"], r.get("commence") or p.get("ko", ""), fh)
            else:
                row["cause"], row["txt"] = "other", ""
            # le marche est parti contre nous (cote juste a la cloture plus basse qu'a la publication)
            if p.get("last_p") is not None and p.get("p_fair") is not None and p["last_p"] < p["p_fair"] - 0.02:
                row["txt"] += f" ; marché contre nous ({round(100 * p['p_fair'])} → {round(100 * p['last_p'])} %)"
                if row["cause"] in ("rate", "other"): row["cause"] = "market"
        rows.append(row)
    return rows


def calib(rows):
    """Probabilite annoncee vs reussite reelle, par source, avec l'intervalle du hasard (2 ecarts-types)."""
    out = {}
    for src in sorted({r["src"] for r in rows}):
        R = [r for r in rows if r["src"] == src and r.get("p")]
        if not R: continue
        n, w, p = len(R), sum(r["win"] for r in R), sum(r["p"] for r in R) / len(R)
        units = sum((r["price"] - 1) if r["win"] else -1 for r in R if r.get("price"))
        out[src] = {"n": n, "win": round(w / n, 3), "announced": round(p, 3), "noise": round(2 * math.sqrt(p * (1 - p) / n), 3),
                    "roi": round(units / n, 3)}
    return out


def run(now=None, write=True):
    now = now or datetime.now(timezone.utc)
    rows = collect(now)
    nights = sorted({r["night"] for r in rows if r.get("night")})
    last = nights[-1] if nights else None
    lost = [r for r in rows if not r["win"]]
    causes = Counter(r.get("cause", "other") for r in lost)
    C = calib(rows)
    lessons = []
    if lost:
        luck = sum(causes[c] for c in LUCK)
        lessons.append(f"{len(lost)} paris perdus en {DAYS} jours : {luck} de malchance (bonne lecture, mauvais sort), "
                       f"{len(lost) - luck} d'erreur de lecture.")
        top, k = causes.most_common(1)[0]
        if top not in LUCK and k >= 5 and k >= 0.4 * len(lost):
            lessons.append(f"Erreur la plus fréquente : {CAUSE_FR.get(top, top)} ({k} sur {len(lost)}).")
    for src, c in C.items():
        if c["n"] >= 30 and c["announced"] - c["win"] > c["noise"]:
            lessons.append(f"{src} : {round(100 * c['announced'])} % annoncés pour {round(100 * c['win'])} % gagnés sur "
                           f"{c['n']} paris, au-delà du hasard (±{round(100 * c['noise'])} pts) : les probabilités sont trop optimistes.")
    rep = {"at": now.isoformat(timespec="minutes"), "last_night": last,
           "veille": [r for r in rows if r.get("night") == last], "causes": dict(causes), "calib": C, "lessons": lessons}
    if write:
        OUT.write_text(json.dumps(rep, ensure_ascii=False, indent=1), encoding="utf-8")
    return rep


def text(rep):
    lines = []
    V = rep.get("veille") or []
    if V:
        w = sum(r["win"] for r in V)
        lines.append(f"Post-mortem du {rep['last_night'][8:10]}/{rep['last_night'][5:7]} : {w}/{len(V)} gagnés.")
        for r in V:
            if r["win"]: continue
            tag = "🍀 malchance" if r.get("cause") in LUCK else "🔎 erreur"
            lines.append(f"{tag} · {r['label']} @ {r['price']:.2f} : {CAUSE_FR.get(r.get('cause'), '')} ({r.get('txt', '')})")
    for src, c in (rep.get("calib") or {}).items():
        lines.append(f"{src} sur {DAYS} j : {c['n']} paris, annoncé {round(100 * c['announced'])} % · gagné "
                     f"{round(100 * c['win'])} % (hasard ±{round(100 * c['noise'])} pts) · rendement {100 * c['roi']:+.1f} %")
    return lines + list(rep.get("lessons") or [])


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    print("\n".join(text(run(write="--write" in sys.argv))))
