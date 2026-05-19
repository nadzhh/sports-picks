"""
nba_resolver.py — Resoudre les picks NBA historiques apres la fin des matchs.

Pour chaque pick avec result="PENDING" dans data/nba_picks_history.json :
1. Fetch le boxscore traditional v2 (stats.nba.com)
2. Match le player name
3. Calcule la stat reelle (PTS / REB / AST / FG3M ou somme pour combos)
4. Compare a la line -> WIN / LOSS / PUSH / DNP / NO_PLAYER
5. Sauvegarde le resultat + horodatage

Pipeline : tourne AVANT nba_picks_engine.py pour avoir les resultats d'hier
disponibles quand on affiche le site.
"""
import json
import unicodedata
from datetime import datetime
from pathlib import Path

from nba_client import boxscore_players

HISTORY_FILE = Path("data/nba_picks_history.json")


# ─── helpers ─────────────────────────────────────────────────────────────────

def _clean_name(s):
    s = unicodedata.normalize("NFD", s or "")
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    return s.lower().replace(".", "").replace("-", " ").strip()


def _match_box_player(name, box_players):
    """Match fuzzy d'un nom joueur dans la liste boxscore."""
    target = _clean_name(name)
    if not target: return None
    # 1) match exact (normalise)
    for p in box_players:
        if _clean_name(p.get("PLAYER_NAME", "")) == target:
            return p
    # 2) match sur le lastname
    last = target.split()[-1] if target else ""
    for p in box_players:
        pn = _clean_name(p.get("PLAYER_NAME", ""))
        if pn and pn.split()[-1] == last:
            return p
    return None


def _stat_value(player_box, prop_key):
    """Extrait la valeur de stat pour un prop key donne."""
    pts = player_box.get("PTS", 0) or 0
    reb = player_box.get("REB", 0) or 0
    ast = player_box.get("AST", 0) or 0
    fg3 = player_box.get("FG3M", 0) or 0
    return {
        "PTS":  pts,
        "REB":  reb,
        "AST":  ast,
        "FG3M": fg3,
        "PRA":  pts + reb + ast,
        "PR":   pts + reb,
        "PA":   pts + ast,
    }.get(prop_key, 0)


def _is_dnp(player_box):
    """Detect DNP - le champ MIN est null/vide ou '0:00'."""
    m = player_box.get("MIN")
    if m is None or m == "" or m == "0:00":
        return True
    return False


def _resolve_pick(pick, box_players):
    """Determine status + valeur reelle pour un pick."""
    player = pick.get("player", "")
    box_p = _match_box_player(player, box_players)
    if not box_p:
        # Pas trouve : joueur traded / inactif / blesse -> DNP (bet rembourse)
        return "DNP", None
    if _is_dnp(box_p):
        return "DNP", 0
    actual = _stat_value(box_p, pick.get("prop", ""))
    line = pick.get("line", 0) or 0
    direction = pick.get("direction", "")
    if direction == "over":
        if   actual > line:  status = "WIN"
        elif actual < line:  status = "LOSS"
        else:                status = "PUSH"
    else:  # under
        if   actual < line:  status = "WIN"
        elif actual > line:  status = "LOSS"
        else:                status = "PUSH"
    return status, actual


# ─── main ────────────────────────────────────────────────────────────────────

def run():
    if not HISTORY_FILE.exists():
        print("[!] Pas d'historique - rien a resoudre")
        return

    try:
        history = json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"[X] Erreur lecture history: {e}")
        return

    picks = history.get("picks", [])
    pending = [p for p in picks if p.get("result", "PENDING") == "PENDING"]
    if not pending:
        print("[OK] Aucun pick PENDING")
        # Petit recap
        from collections import Counter
        c = Counter(p.get("result") for p in picks)
        print(f"  Etat : {dict(c)}")
        return

    print(f"=== Resolveur NBA : {len(pending)} picks PENDING ===")

    # Group by game_id pour ne fetch chaque boxscore qu'une fois
    by_game = {}
    for p in pending:
        by_game.setdefault(p["game_id"], []).append(p)

    n_resolved = 0
    n_pending  = 0
    for gid, gpicks in by_game.items():
        try:
            box = boxscore_players(gid)
        except Exception as e:
            print(f"  [boxscore err] {gid}: {e}")
            n_pending += len(gpicks)
            continue
        if not box:
            print(f"  [pending] {gid} - boxscore vide (match pas encore fini ?)")
            n_pending += len(gpicks)
            continue

        date = gpicks[0].get("date", "?")
        matchup = gpicks[0].get("matchup", "?")
        print(f"\n  [{date}] {matchup}  ({gid}) - {len(box)} joueurs au boxscore")
        for p in gpicks:
            status, actual = _resolve_pick(p, box)
            p["result"]      = status
            p["actual"]      = actual
            p["resolved_at"] = datetime.now().strftime("%Y-%m-%d %H:%M")
            n_resolved += 1
            tag = {"WIN":"OK","LOSS":"KO","PUSH":"==","DNP":"DNP","NO_PLAYER":"??"}[status]
            line = p.get("line"); direction = p.get("direction", "")
            print(f"    [{tag}] {p.get('player','?')} {direction} {line} {p.get('prop')}  ->  actual={actual}")

    HISTORY_FILE.write_text(json.dumps(history, ensure_ascii=False, indent=2), encoding="utf-8")

    # Recap final
    from collections import Counter
    c = Counter(p.get("result") for p in history.get("picks", []))
    total_real = sum(c.get(k, 0) for k in ("WIN","LOSS","PUSH"))
    wins = c.get("WIN", 0)
    losses = c.get("LOSS", 0)
    wr = round(wins / (wins + losses) * 100, 1) if (wins + losses) else 0
    print(f"\n[OK] {n_resolved} picks resolus, {n_pending} encore pending")
    print(f"     Cumul: {wins}W / {losses}L / {c.get('PUSH',0)}push / {c.get('DNP',0)}DNP -> WR {wr}%")


if __name__ == "__main__":
    run()
