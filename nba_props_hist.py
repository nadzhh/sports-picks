"""
nba_props_hist.py — Historique des lignes de props joueurs NBA (ESPN core API, gratuit).

ESPN conserve, match par match, les props d'avant-match d'un bookmaker partenaire :
ESPN BET (fournisseur 58) en 2025-26, DraftKings (100) en 2026-27. Les fournisseurs
« Live » sont ignores. Sert a tester nba_sim contre de VRAIES lignes.

Format ESPN : une ligne par (joueur, marche, seuil, prix). La ligne principale
apparait en paire, le sens est dans current.over / current.under (au plus recent
horodatage : les paires plus anciennes sont des lignes deplacees). ESPN BET ajoute
une echelle de seuils alternatifs sans sens (« 15+ points », des overs), ignoree ici.

Produit : data/nba_db/props_<annee>.json
          {game_id: {"book": ..., "rows": [[aid, type, seuil, prix, prix_ouverture, maj, sens], ...]}}
          sens : "o" over, "u" under, "" seuil alternatif

Usage :
    python nba_props_hist.py 2026            # toute la saison 2025-26 (incremental)
    python nba_props_hist.py 2027 --max 50
"""
import argparse, json, sys, threading
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed

from nba_boxdb import DB_DIR, _get, load_season, season_year

CORE = "https://sports.core.api.espn.com/v2/sports/basketball/leagues/nba"
WORKERS = 4

TYPES = {  # marches ESPN -> nos stats
    "Total Points": "PTS", "Total Rebounds": "REB", "Total Assists": "AST",
    "Total 3-Point Field Goals": "3PM", "Total Points, Rebounds, and Assists": "PRA",
    "Total Points and Rebounds": "PR", "Total Points and Assists": "PA",
    "Total Rebounds and Assists": "RA", "Total Steals": "STL", "Total Blocks": "BLK",
}


def _american(v):
    if v in (None, ""): return None
    if str(v).lower() in ("even", "ev"): return 100
    try: return int(float(str(v).replace("+", "")))
    except Exception: return None


def fetch_game_props(gid):
    odds = _get(f"{CORE}/events/{gid}/competitions/{gid}/odds?lang=en&region=us")
    provs = [((i.get("provider") or {}).get("id"), (i.get("provider") or {}).get("name", ""))
             for i in (odds or {}).get("items") or []]
    provs = [(pid, name) for pid, name in provs if pid and "live" not in name.lower()]
    for pid, name in provs:
        rows, page = [], 1
        while True:
            pb = _get(f"{CORE}/events/{gid}/competitions/{gid}/odds/{pid}/propBets?lang=en&region=us&limit=1000&page={page}")
            if not pb: break
            for it in pb.get("items") or []:
                t = TYPES.get((it.get("type") or {}).get("name"))
                if not t or "athlete" not in it: continue
                aid = it["athlete"]["$ref"].split("/athletes/")[1].split("?")[0]
                o = it.get("odds") or {}
                try: line = float((o.get("total") or {}).get("value"))
                except Exception: continue
                am = o.get("american") or {}
                cur = it.get("current") or {}
                side = "o" if "over" in cur else "u" if "under" in cur else ""
                rows.append([aid, t, line, _american(am.get("value")), _american(am.get("open")),
                             it.get("lastUpdated", ""), side])
            if page >= (pb.get("pageCount") or 1): break
            page += 1
        if rows:
            return {"book": name, "rows": rows}
    return None


def update(year, max_games=None):
    path = DB_DIR / f"props_{year}.json"
    db = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    box = load_season(year)
    todo = [gid for gid, g in sorted(box.items(), key=lambda x: x[1]["date"]) if gid not in db]
    if max_games: todo = todo[:max_games]
    print(f"=== Props {year - 1}-{year % 100:02d} : {len(box)} matchs en base, {len(db)} deja faits, {len(todo)} a recuperer")
    lock, done = threading.Lock(), 0
    with ThreadPoolExecutor(WORKERS) as ex:
        futs = {ex.submit(fetch_game_props, gid): gid for gid in todo}
        for f in as_completed(futs):
            gid = futs[f]
            try: res = f.result()
            except Exception as e: res = None; print(f"  [err] {gid}: {e}")
            with lock:
                db[gid] = res or {"book": None, "rows": []}   # vide = pas de props (pas de nouvel essai)
                done += 1
                if done % 100 == 0:
                    path.write_text(json.dumps(db, separators=(",", ":")), encoding="utf-8")
                    print(f"  {done}/{len(todo)}")
    path.write_text(json.dumps(db, separators=(",", ":")), encoding="utf-8")
    n_rows = sum(len(v["rows"]) for v in db.values())
    print(f"  [OK] {len(db)} matchs, {n_rows} lignes -> {path} ({path.stat().st_size / 1e6:.1f} Mo)")


def load_props(year):
    p = DB_DIR / f"props_{year}.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


def _prob(am):
    if am is None: return None
    return 100.0 / (am + 100.0) if am > 0 else -am / (-am + 100.0)


def props_main_lines(game_props):
    """Lignes principales {(aid, stat): {line, over, under, p_over_novig}} :
    la paire over/under au seuil le plus recemment mis a jour."""
    groups = defaultdict(lambda: defaultdict(dict))
    unlabeled = defaultdict(list)
    for aid, stat, line, am, am_open, upd, side in (game_props or {}).get("rows") or []:
        if am is None: continue
        if side: groups[(aid, stat)][line][side] = (am, upd, am_open)
        else: unlabeled[(aid, stat, line)].append((am, upd, am_open))
    # DraftKings (2026-27) ne marque pas le sens : la paire est dans l'ordre over puis
    # under (verifie : avec cet ordre le favori gagne 59 % sur 928 lignes, 41 % sinon)
    labeled = set(groups)
    for (aid, stat, line), rs in unlabeled.items():
        if len(rs) == 2 and (aid, stat) not in labeled:
            groups[(aid, stat)][line] = {"o": rs[0], "u": rs[1]}
    out = {}
    for key, by_line in groups.items():
        pairs = [(max(d["o"][1], d["u"][1]), line, d) for line, d in by_line.items() if "o" in d and "u" in d]
        if not pairs: continue
        _, line, d = max(pairs, key=lambda x: x[0])
        po, pu = _prob(d["o"][0]), _prob(d["u"][0])
        out[key] = {"line": line, "over": d["o"][0], "under": d["u"][0], "p_over_novig": po / (po + pu),
                    "over_open": d["o"][2], "under_open": d["u"][2]}
    return out


def props_ladders(game_props):
    """Echelle des seuils alternatifs (ESPN BET 2025-26 : un seul prix, celui de l'over
    « X+ ») : {(aid, stat): [(seuil, prix, prix_ouverture), ...]}. Les paires DraftKings
    sans sens (lignes principales) sont exclues : un seuil alternatif n'a qu'une ligne."""
    rows = defaultdict(list)
    for aid, stat, line, am, am_open, upd, side in (game_props or {}).get("rows") or []:
        if not side: rows[(aid, stat, line)].append((am, am_open))
    out = defaultdict(list)
    for (aid, stat, line), rs in rows.items():
        if len(rs) == 1 and rs[0][0] is not None:
            out[(aid, stat)].append((line, rs[0][0], rs[0][1]))
    return {k: sorted(v) for k, v in out.items()}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("years", type=int, nargs="*")
    ap.add_argument("--max", type=int)
    a = ap.parse_args()
    for y in a.years or [season_year()]:
        update(y, a.max)
