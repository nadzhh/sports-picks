"""
nba_sim_analyse.py — Basket › Analyse simu (Wilson, 18/11/2026) : « tout pareil que l'onglet Analyse, sauf qu'au lieu
que les matchs qui rentrent en compte soient les vrais résultats du joueur, tu mets les résultats des joueurs DANS LA
SIMULATION LA PLUS PROBABLE DU MATCH (et des anciens) » — en distinguant bien la simulation de la vraie vie.

Memes cadres joueur que Basket › Analyse (generate_site._build_player_analyse_card : boutons de stat, L5 / L10 / L20,
ligne reglable, taux, barres vertes / rouges), mais chaque barre = la stat du joueur dans le match le plus probable
simule AVANT ce match (data/replays/<gid>.json, celui de la video ; avant le 07/11 la video etait tiree au sort : ces
matchs-la ne comptent pas). La barre la plus recente est le match de ce soir ; sous chaque barre passee, ce que le
joueur a VRAIMENT fait (nba_chart.logs).

Un fichier par match (lazy/simana/<gid>.html), charge au clic dans l'onglet : les cadres pre-rendus pesent lourd
(l'onglet Analyse fait 24 Mo).
"""
import glob
import html as H
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

DIR = Path("lazy/simana")
NY = ZoneInfo("America/New_York")
MAX_PER_TEAM = 12
STAT_KEY = {"PTS": "PTS", "REB": "REB", "AST": "AST", "3PM": "FG3M", "PR": "PR", "PA": "PA", "RA": "RA", "PRA": "PRA"}
_SIM = {}


def _ko(iso):
    try:
        return datetime.fromisoformat((iso or "").replace("Z", "+00:00"))
    except ValueError:
        return None


def sim_games():
    """{player_id: [match simule, ...]} du plus recent au plus ancien. Un match simule = dict au format des matchs
    de l'onglet Analyse (date, opp, is_home, MIN, PTS, REB, AST, FG3M) + gid, ko."""
    import nba_sim_site as S
    stamp = tuple(sorted((f, os.path.getmtime(f)) for f in glob.glob("data/replays/*.json")))
    if _SIM.get("stamp") == stamp:
        return _SIM["out"]
    out = {}
    for f in glob.glob("data/replays/*.json"):
        if f.endswith("_b.json"): continue
        d, B = S._replay_box(f)
        if not d: continue
        meta = d.get("meta") or {}
        if not (meta.get("scenario") or {}).get("typical"): continue   # video tiree au sort (avant le 07/11)
        ko = _ko(meta.get("date"))
        if not ko: continue
        gid = Path(f).stem
        abbr = [(meta.get("home") or {}).get("abbr", "?"), (meta.get("away") or {}).get("abbr", "?")]
        for j, p in enumerate(d.get("players") or []):
            if B["MIN"][j] < 0.5: continue
            t = p.get("team", 0)
            out.setdefault(str(p["id"]), []).append({
                "date": ko.astimezone(NY).strftime("%Y-%m-%d"), "opp": abbr[1 - t], "is_home": t == 0,
                "MIN": round(B["MIN"][j]), "PTS": int(B["PTS"][j]), "REB": int(B["REB"][j]), "AST": int(B["AST"][j]),
                "FG3M": int(B["3PM"][j]), "gid": gid, "ko": ko})
    for v in out.values(): v.sort(key=lambda x: x["ko"], reverse=True)
    _SIM.clear(); _SIM.update(stamp=stamp, out=out)
    return out


def _real_index(now):
    import nba_chart as C
    season = now.year + 1 if now.month >= 9 else now.year
    idx = {}
    for pid, rows in C.logs((season - 1, season)).items():
        for r in rows:
            idx[(pid, r[0], r[1])] = {"PTS": r[5], "REB": r[6], "AST": r[7], "FG3M": r[8], "MIN": r[4]}
    return idx


def player_games(pid, gid, now, real):
    """Matchs simules du joueur : celui de ce soir (gid) puis les matchs DEJA joues, avec la vraie stat."""
    out = []
    for x in sim_games().get(str(pid), []):
        if x["gid"] == gid:
            out.append(dict(x, sim_tonight=True))
        elif x["ko"] < now:
            out.append(dict(x, sim_real=real.get((str(pid), x["date"], x["opp"]))))
    return [{k: v for k, v in x.items() if k != "ko"} for x in out]


def build_game(g, now=None, real=None):
    """HTML du match g (entree de nba_sim.json) pour l'onglet Analyse simu, ou "" sans match le plus probable."""
    import generate_site as G
    import nba_sim_site as S
    now = now or datetime.now(timezone.utc)
    real = real if real is not None else _real_index(now)
    rp = g.get("replay") or {}
    sc = next((s for s in rp.get("scenarios") or [] if s.get("typical")), None)
    if not sc: return ""
    d, B = S._replay_box(sc["path"])
    if not d: return ""
    gid = str(g["gid"])
    h, a = g["home"], g["away"]
    odds = {}
    for p in g.get("props") or []:
        k = STAT_KEY.get(p.get("stat"))
        if k and p.get("line") is not None:
            odds.setdefault(str(p["id"]), {})[k] = {"line": p["line"], "over": p.get("over"), "under": p.get("under")}
    starters = set(d.get("starters") or [])
    cols = {}
    for t, team in ((0, h), (1, a)):
        js = sorted((j for j, p in enumerate(d["players"]) if p.get("team") == t and B["MIN"][j] >= 0.5),
                    key=lambda j: (j not in starters, -B["MIN"][j]))[:MAX_PER_TEAM]
        opp = (a if t == 0 else h).get("abbr")
        cards = ""
        for j in js:
            p = d["players"][j]
            games = player_games(p["id"], gid, now, real)
            if not games: continue
            pl = {"name": p["name"], "position": p.get("pos", ""), "season_avg": {"MIN": B["MIN"][j]},
                  "l10_games": games, "_sim": True}
            cards += G._build_player_analyse_card(pl, opp_abbr=opp, odds_for_player=odds.get(str(p["id"])),
                                                  side_label=f"🎬 {H.escape(team.get('name') or '')}",
                                                  is_starter=j in starters, match_ctx=None)
        cols[t] = cards
    fin, q = sc["final"], sc.get("quarters") or []
    qt = " · ".join(f"{x[1]}-{x[0]}" for x in q)
    win = h if fin[0] > fin[1] else a
    return (f'<div class="sa-game" data-gid="{H.escape(gid)}">'
            '<div style="background:#1b1530;border:1px solid #7c3aed;border-radius:12px;padding:12px 16px;margin-bottom:14px">'
            '<div style="color:#c4b5fd;font-size:11px;text-transform:uppercase;letter-spacing:1px;font-weight:800">'
            '🎬 Simulation — pas un vrai match</div>'
            f'<div style="color:#f1f5f9;font-size:18px;font-weight:700;margin:4px 0">{H.escape(a.get("name") or "")} '
            f'<b style="color:#c4b5fd">{fin[1]}-{fin[0]}</b> {H.escape(h.get("name") or "")}</div>'
            f'<div style="color:#94a3b8;font-size:12.5px">Match le plus probable de ce soir ({qt}) · victoire '
            f'{H.escape(win.get("name") or "")} dans {round(100 * (sc.get("p") or 0))} % des simulations. Chaque barre = la stat '
            'du joueur dans le match le plus probable simulé <b>avant</b> chaque match ; la plus récente est ce soir '
            '(<span style="color:#c4b5fd;font-weight:700">🎬 ce soir</span>). Sous les barres des matchs déjà joués : '
            '<b>réel</b> = ce qu’il a vraiment fait. Lignes DraftKings de ce soir.</div></div>'
            '<div class="nba-teams2" style="display:grid;grid-template-columns:1fr 1fr;gap:12px">'
            f'<div><div style="color:#a78bfa;font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:1px;margin-bottom:8px">'
            f'🏠 {H.escape(h.get("name") or "")}</div>{cols.get(0) or "Pas de joueurs"}</div>'
            f'<div><div style="color:#a78bfa;font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:1px;margin-bottom:8px">'
            f'✈️ {H.escape(a.get("name") or "")}</div>{cols.get(1) or "Pas de joueurs"}</div></div></div>')


def write_all(sim, now=None):
    """Ecrit lazy/simana/<gid>.html pour chaque match a venir (ecriture atomique, seulement si le contenu change)
    et supprime ceux des matchs sortis de la liste. Renvoie {gid: version} pour le chargeur."""
    now = now or datetime.now(timezone.utc)
    DIR.mkdir(parents=True, exist_ok=True)
    real = _real_index(now)
    vers, keep = {}, set()
    for g in (sim or {}).get("games") or []:
        gid = str(g["gid"])
        try:
            body = build_game(g, now, real)
        except Exception as e:
            print(f"  [!] analyse simu {gid} : {type(e).__name__}: {e}")
            continue
        if not body: continue
        f = DIR / f"{gid}.html"
        keep.add(f.name)
        import hashlib
        vers[gid] = hashlib.sha1(body.encode("utf-8")).hexdigest()[:10]
        try:
            if f.read_text(encoding="utf-8") == body: continue
        except OSError:
            pass
        tmp = f.with_suffix(".tmp")
        tmp.write_text(body, encoding="utf-8")
        os.replace(tmp, f)
    for f in DIR.glob("*.html"):
        if f.name not in keep: f.unlink()
    return vers


def versions(sim):
    """{gid: version} des fichiers deja ecrits (cron principal : il reference les fichiers sans les ecrire,
    seule la veille les publie — pas de conflit entre les deux workflows)."""
    import hashlib
    out = {}
    for g in (sim or {}).get("games") or []:
        f = DIR / f"{g['gid']}.html"
        try:
            out[str(g["gid"])] = hashlib.sha1(f.read_bytes()).hexdigest()[:10]
        except OSError:
            pass
    return out
