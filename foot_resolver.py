"""
foot_resolver.py — Resolveur pour les picks foot via FotMob (api-football free plan
ne donne pas la saison courante).

Pour chaque pick PENDING dans data/picks_history.json :
1. Recupere le _page_url depuis data/matches.json (snapshot scraper)
   OU stocke directement dans le pick (idealement on devrait le saver)
2. Fetch match_events() de FotMob -> score + buts (avec scorer/assist) + tirs total
3. Resout team picks (1X2, double chance, BTTS, totals buts, totals tirs)
4. Resout player picks (buteur, joueur decisif)
"""
import json
import unicodedata
from datetime import datetime
from pathlib import Path

from fotmob_client import match_events

HISTORY_FILE = Path("data/picks_history.json")
MATCHES_FILE = Path("data/matches.json")


# ─── helpers ─────────────────────────────────────────────────────────────────

def _clean(s):
    s = unicodedata.normalize("NFD", s or "")
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    return s.lower().strip()


def _name_match(target, candidate):
    a, b = _clean(target), _clean(candidate)
    if not a or not b: return False
    if a == b: return True
    if a in b or b in a: return True
    # match sur lastname (utile pour "L. Suarez" vs "Luis Suarez")
    la = a.split()[-1] if a else ""
    lb = b.split()[-1] if b else ""
    return bool(la) and la == lb


def load_page_urls():
    """Map match_id -> _page_url depuis data/matches.json."""
    if not MATCHES_FILE.exists(): return {}
    try:
        ms = json.loads(MATCHES_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return {m["id"]: m.get("_page_url") for m in ms if m.get("id")}


# ─── Resolution par categorie ────────────────────────────────────────────────

def _is_finished(ev):
    """Detect si le match est FT (score present + 2h+ apres kickoff)."""
    if not ev: return False
    s = ev.get("score") or ""
    if not s or "-" not in s: return False
    utc_time = ev.get("utcTime")
    if not utc_time: return True  # on a un score sans heure -> on suppose fini
    try:
        from datetime import timezone
        kickoff = datetime.fromisoformat(utc_time.replace("Z", "+00:00"))
        elapsed_h = (datetime.now(tz=timezone.utc) - kickoff).total_seconds() / 3600
        return elapsed_h >= 2.0
    except Exception:
        return True


def _is_past_kickoff(pick_date_str):
    """Le match du pick (date YYYY-MM-DD) est-il dans le passe (>=2h apres minuit) ?"""
    if not pick_date_str: return False
    try:
        d = datetime.strptime(pick_date_str, "%Y-%m-%d")
        # Si la date est strictement < today, le match est joue depuis 24h+ -> force refresh
        now = datetime.now()
        return d.date() < now.date()
    except Exception:
        return False


def _parse_score(score_str):
    """'1 - 2' -> (1, 2). 'FT 1-2' -> (1, 2)."""
    if not score_str: return None, None
    import re
    m = re.search(r"(\d+)\s*-\s*(\d+)", score_str)
    if not m: return None, None
    return int(m.group(1)), int(m.group(2))


def _resolve_team(pick, ev):
    direction = (pick.get("direction") or "").lower()
    # Fun picks utilisent le prefixe "fun_" (fun_over35, fun_draw, fun_btts...).
    # On strip le prefixe pour reutiliser la meme logique que les team picks.
    if direction.startswith("fun_"):
        direction = direction[4:]
    hs, as_ = _parse_score(ev.get("score"))
    if hs is None or as_ is None: return "UNKNOWN", None
    score_str = f"{hs}-{as_}"

    if direction == "home_win":  return ("WIN" if hs >  as_ else "LOSS"), score_str
    if direction == "away_win":  return ("WIN" if as_ >  hs  else "LOSS"), score_str
    if direction == "draw":      return ("WIN" if hs == as_ else "LOSS"), score_str
    if direction == "home_dc":   return ("WIN" if hs >= as_ else "LOSS"), score_str
    if direction == "away_dc":   return ("WIN" if as_ >= hs  else "LOSS"), score_str
    if direction in ("no_draw", "12"):
        return ("WIN" if hs != as_ else "LOSS"), score_str
    if direction in ("btts_yes", "btts"):
        return ("WIN" if (hs > 0 and as_ > 0) else "LOSS"), score_str
    if direction == "btts_no":
        return ("WIN" if (hs == 0 or as_ == 0) else "LOSS"), score_str
    total = hs + as_
    if direction in ("over15","over_15","over1.5"):  return ("WIN" if total > 1.5 else "LOSS"), f"{score_str} ({total} buts)"
    if direction in ("under15","under_15","under1.5"):return ("WIN" if total < 1.5 else "LOSS"), f"{score_str} ({total} buts)"
    if direction in ("over25","over_25","over2.5"):  return ("WIN" if total > 2.5 else "LOSS"), f"{score_str} ({total} buts)"
    if direction in ("under25","under_25","under2.5"):return ("WIN" if total < 2.5 else "LOSS"), f"{score_str} ({total} buts)"
    if direction in ("over35","over_35","over3.5"):  return ("WIN" if total > 3.5 else "LOSS"), f"{score_str} ({total} buts)"
    if direction in ("under35","under_35","under3.5"):return ("WIN" if total < 3.5 else "LOSS"), f"{score_str} ({total} buts)"

    # First team to score (fts_home / fts_away)
    if direction in ("fts_home", "fts_away"):
        home_goals = ev.get("home_goals") or []
        away_goals = ev.get("away_goals") or []
        # Filtre csc + assemble {team, minute}
        all_goals = []
        for g in home_goals:
            if not g.get("ownGoal"): all_goals.append(("home", g.get("minute") or 999))
        for g in away_goals:
            if not g.get("ownGoal"): all_goals.append(("away", g.get("minute") or 999))
        if not all_goals:
            # 0-0 : aucune equipe n'a marque -> LOSS pour fts (ou parfois "no goalscorer" cashout)
            return "LOSS", f"{score_str} (aucun but)"
        all_goals.sort(key=lambda x: x[1])
        first_team = all_goals[0][0]
        target = "home" if direction == "fts_home" else "away"
        return ("WIN" if first_team == target else "LOSS"), f"{score_str} (1er but: {all_goals[0][0]} {all_goals[0][1]}')"

    return "UNKNOWN", None


def _resolve_team_shots(pick, ev):
    """
    Resout picks tirs / tirs cadres. Detecte :
      - SOT (shots on target = tirs cadres) vs total shots (tirs)
      - Team-specific via le nom d'equipe REEL dans le label (matche home OU away
        en lisant ev['home']/ev['away'] - FotMob)
    """
    label_raw = pick.get("label") or ""
    label     = label_raw.lower()
    direction = (pick.get("direction") or "").lower()
    if "tir" not in label and "shot" not in label and "shot" not in direction:
        return "UNKNOWN", None

    is_sot = ("cadr" in label) or ("sot" in direction)

    # IMPORTANT : utilise les noms d'equipe REELS de FotMob (pas le matchup label
    # car ce dernier peut etre dans l'ordre inverse).
    home_team_name = _clean(ev.get("home", ""))
    away_team_name = _clean(ev.get("away", ""))

    target = None  # 'home' / 'away' / 'total'
    label_clean = _clean(label_raw)
    dir_clean   = _clean(direction.replace("_", " "))
    haystack = label_clean + " " + dir_clean

    if home_team_name and home_team_name in haystack:
        target = "home"; who = ev.get("home", "")
    elif away_team_name and away_team_name in haystack:
        target = "away"; who = ev.get("away", "")
    elif "total" in haystack:
        target = "total"; who = "total"
    else:
        target = "total"; who = "total"

    hs_total = ev.get("home_shots"); as_total = ev.get("away_shots")
    hs_sot   = ev.get("home_sot");   as_sot   = ev.get("away_sot")
    if is_sot:
        h, a = hs_sot, as_sot
        stat_lbl = "tirs cadrés"
    else:
        h, a = hs_total, as_total
        stat_lbl = "tirs"
    if h is None or a is None:
        return "UNKNOWN", None

    if target == "home":   value = int(h)
    elif target == "away": value = int(a)
    else:                  value = int(h) + int(a)

    import re
    m = re.search(r"(\d+(?:\.\d+)?)", label_raw)
    if not m: return "UNKNOWN", None
    line = float(m.group(1))
    is_over  = "plus" in label or "over" in label
    is_under = "moins" in label or "under" in label

    actual_txt = f"{value} {stat_lbl}"
    if target != "total":
        actual_txt += f" ({who})"

    if is_over:
        return ("WIN" if value > line else ("LOSS" if value < line else "PUSH")), actual_txt
    if is_under:
        return ("WIN" if value < line else ("LOSS" if value > line else "PUSH")), actual_txt
    return "UNKNOWN", None


def _resolve_player(pick, ev):
    player = pick.get("player", "")
    type_raw = pick.get("type") or ""
    type_ = _clean(type_raw)  # normalise accents (decisif vs décisif)
    if not player: return "UNKNOWN", None

    home_goals = ev.get("home_goals") or []
    away_goals = ev.get("away_goals") or []

    goals, assists = 0, 0
    for g in home_goals + away_goals:
        if g.get("ownGoal"): continue
        scorer = g.get("scorer") or ""
        if _name_match(player, scorer): goals += 1
        assist = g.get("assist") or ""
        if assist and _name_match(player, assist): assists += 1

    actual = {"goals": goals, "assists": assists}
    if "buteur" in type_ or "marque" in type_:
        return ("WIN" if goals >= 1 else "LOSS"), actual
    if "decisif" in type_ or "decisive" in type_ or "passe" in type_:
        return ("WIN" if (goals >= 1 or assists >= 1) else "LOSS"), actual
    return "UNKNOWN", None


# ─── Main ────────────────────────────────────────────────────────────────────

def run():
    if not HISTORY_FILE.exists():
        print("[!] Pas d'historique foot")
        return

    history = json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
    picks = history.get("picks", [])
    pending = [p for p in picks if p.get("result") in (None, "PENDING")]
    if not pending:
        print("[OK] Aucun pick foot PENDING")
        return

    print(f"=== Foot resolver (FotMob) : {len(pending)} picks PENDING ===")
    page_urls = load_page_urls()  # match_id -> _page_url

    # Group by match_id
    by_match = {}
    for p in pending:
        by_match.setdefault(p.get("match_id"), []).append(p)

    n_resolved, n_skipped = 0, 0
    for mid, mpicks in by_match.items():
        # 1) snapshot scraper actuel  2) URL stockee dans le pick lui-meme
        # (pour resoudre les picks passes une fois matches.json regenere)
        page_url = page_urls.get(mid) or page_urls.get(str(mid))
        if not page_url:
            page_url = mpicks[0].get("page_url")
        if not page_url:
            print(f"  [skip] match {mid} - pas de page_url (ni snapshot ni stocke dans pick)")
            n_skipped += len(mpicks)
            continue
        # Si la date du pick est passee, on FORCE le refetch (le cache pre-match pollue)
        pick_date = mpicks[0].get("date", "")
        force_refresh = _is_past_kickoff(pick_date)
        try:
            ev = match_events(page_url, force=force_refresh)
            # Retry sans cache si on a un score=None alors qu'on s'attendait a un match fini
            if (not ev or not ev.get("score")) and not force_refresh:
                ev = match_events(page_url, force=True)
        except Exception as e:
            print(f"  [match err] {mid}: {e}")
            n_skipped += len(mpicks)
            continue
        if not ev:
            print(f"  [pending] match {mid} - pas de data FotMob")
            n_skipped += len(mpicks)
            continue
        if not _is_finished(ev):
            print(f"  [pending] match {mid} - pas encore termine (score={ev.get('score')})")
            n_skipped += len(mpicks)
            continue

        score_str = ev.get("score") or "?"
        matchup = mpicks[0].get("matchup", "?")
        print(f"\n  [FT] {matchup}  ({score_str})")

        for p in mpicks:
            cat = p.get("category", "")
            status, actual = "UNKNOWN", None
            # Fun picks (Plus 3.5 buts, Match nul, etc.) ont les memes directions
            # que les team picks (over35, draw, ...) - on les traite pareil
            if cat in ("team", "fun"):
                status, actual = _resolve_team(p, ev)
                if status == "UNKNOWN":
                    status, actual = _resolve_team_shots(p, ev)
            elif cat == "player":
                status, actual = _resolve_player(p, ev)
            if status == "UNKNOWN":
                continue
            p["result"]      = status
            p["actual"]      = actual
            p["resolved_at"] = datetime.now().strftime("%Y-%m-%d %H:%M")
            n_resolved += 1
            tag = {"WIN":"OK","LOSS":"KO","PUSH":"=="}.get(status, "?")
            print(f"    [{tag}] {p.get('label','?')[:65]}  -> {actual}")

    HISTORY_FILE.write_text(json.dumps(history, ensure_ascii=False, indent=2), encoding="utf-8")

    from collections import Counter
    c = Counter(p.get("result") for p in history.get("picks", []))
    wins = c.get("WIN", 0); losses = c.get("LOSS", 0)
    wr = round(wins / (wins + losses) * 100, 1) if (wins + losses) else 0
    print(f"\n[OK] {n_resolved} resolus, {n_skipped} pending - Cumul foot : {wins}W / {losses}L -> WR {wr}%")


if __name__ == "__main__":
    run()
