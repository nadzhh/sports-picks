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
    # WC picks utilisent le prefixe "wc_" (wc_over_25, wc_btts_no, wc_home_win...).
    # On strip le prefixe pour reutiliser la meme logique que les team picks.
    if direction.startswith("fun_"):
        direction = direction[4:]
    elif direction.startswith("wc_") and not direction.startswith("wc_score_"):
        direction = direction[3:]
    # Mapping spécifique WC : "home_scores_N" / "away_scores_N" (équipe marque N+ buts)
    # et "home_no_score" / "away_no_score" (équipe ne marque pas)
    hs, as_ = _parse_score(ev.get("score"))
    if hs is None or as_ is None: return "UNKNOWN", None

    # IMPORTANT : FotMob peut inverser home/away (ex: page_url "mali-vs-iran"
    # alors que matches.json a Iran en home). On detecte le swap en comparant
    # le nom dans le matchup du pick avec ev.home_name / ev.away_name.
    matchup = (pick.get("matchup") or "").lower()
    ev_home = (ev.get("home") or "").lower()
    ev_away = (ev.get("away") or "").lower()
    pick_home = matchup.split(" vs ")[0].strip() if " vs " in matchup else ""
    pick_away = matchup.split(" vs ")[1].strip() if " vs " in matchup else ""

    def _norm(s):
        return "".join(c for c in (s or "").lower() if c.isalnum())

    swapped = False
    if pick_home and pick_away and ev_home and ev_away:
        ph, pa = _norm(pick_home), _norm(pick_away)
        eh, ea = _norm(ev_home), _norm(ev_away)
        # Match exact ou inclusion
        home_match = ph and (ph == eh or ph in eh or eh in ph)
        away_match = pa and (pa == ea or pa in ea or ea in pa)
        # Swap detection : pick home matche ev away
        home_swap_match = ph and (ph == ea or ph in ea or ea in ph)
        away_swap_match = pa and (pa == eh or pa in eh or eh in pa)
        if (home_swap_match and away_swap_match) and not (home_match and away_match):
            swapped = True

    if swapped:
        hs, as_ = as_, hs

    score_str = f"{hs}-{as_}"

    # Score exact WC : direction='wc_score_H_A' -> verifie le score exact
    if direction.startswith("wc_score_"):
        try:
            parts = direction.split("_")
            target_h = int(parts[2]); target_a = int(parts[3])
            return ("WIN" if (hs == target_h and as_ == target_a) else "LOSS"), f"{score_str} (cible {target_h}-{target_a})"
        except Exception:
            return "UNKNOWN", score_str

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

    # "X marque plus de 1.5 buts" : 'home_over_15' = home team scored >=2
    if direction in ("home_over_15", "home_over15"):
        return ("WIN" if hs >= 2 else "LOSS"), f"{score_str} ({hs} buts home)"
    if direction in ("away_over_15", "away_over15"):
        return ("WIN" if as_ >= 2 else "LOSS"), f"{score_str} ({as_} buts away)"

    # WC team-specific scoring : "home_scores_N" = home équipe a marqué >= N buts
    # "away_scores_N" = away équipe a marqué >= N buts (suit le swap si appliqué)
    if direction.startswith("home_scores_"):
        try:
            n = int(direction.split("_")[-1])
            return ("WIN" if hs >= n else "LOSS"), f"{score_str} ({hs} buts home, cible {n}+)"
        except Exception:
            return "UNKNOWN", None
    if direction.startswith("away_scores_"):
        try:
            n = int(direction.split("_")[-1])
            return ("WIN" if as_ >= n else "LOSS"), f"{score_str} ({as_} buts away, cible {n}+)"
        except Exception:
            return "UNKNOWN", None
    # WC clean sheet : "home_no_score" = home équipe ne marque pas (clean sheet adverse)
    if direction == "home_no_score":
        return ("WIN" if hs == 0 else "LOSS"), f"{score_str} ({hs} but home)"
    if direction == "away_no_score":
        return ("WIN" if as_ == 0 else "LOSS"), f"{score_str} ({as_} but away)"
    # WC double chance avec format spécifique : "dc_1x" / "dc_x2" / "dc_12"
    if direction == "dc_1x":  return ("WIN" if hs >= as_ else "LOSS"), score_str
    if direction == "dc_x2":  return ("WIN" if as_ >= hs else "LOSS"), score_str
    if direction == "dc_12":  return ("WIN" if hs != as_ else "LOSS"), score_str
    # WC over/under 1.5 et 3.5 sans underscore intermédiaire
    if direction == "over_15":  return ("WIN" if (hs + as_) > 1.5 else "LOSS"), f"{score_str} ({hs+as_} buts)"
    if direction == "under_15": return ("WIN" if (hs + as_) < 1.5 else "LOSS"), f"{score_str} ({hs+as_} buts)"
    if direction == "over_35":  return ("WIN" if (hs + as_) > 3.5 else "LOSS"), f"{score_str} ({hs+as_} buts)"
    if direction == "under_35": return ("WIN" if (hs + as_) < 3.5 else "LOSS"), f"{score_str} ({hs+as_} buts)"

    # Penalty equipe : "1+ penalty marque dans le match" - on detecte via
    # les events goals.description ou label="Penalty" dans home_goals/away_goals
    if direction == "team_penalty":
        all_goals = (ev.get("home_goals") or []) + (ev.get("away_goals") or [])
        n_pens = sum(1 for g in all_goals
                     if "penalty" in ((g.get("description") or "") + " " + (g.get("type") or "")).lower())
        return ("WIN" if n_pens >= 1 else "LOSS"), f"{score_str} ({n_pens} penalty(s))"

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

    # Garde : si la ligue n'est pas top-5 europeen + UEFA, les stats tirs ne sont
    # pas fiables (FotMob n'expose pas ces stats pour les championnats secondaires
    # ou hors Europe). On marque PUSH (rembourse) au lieu de laisser PENDING.
    SHOTS_RELIABLE_LEAGUES = {17, 8, 35, 23, 34, 7, 679, 17015}
    lid = pick.get("league_id") or 0
    if lid and lid not in SHOTS_RELIABLE_LEAGUES:
        return "PUSH", "Stats tirs non disponibles pour ce championnat"

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
    # IMPORTANT : 'marque 2+ buts' (Double buteur) DOIT etre detecte AVANT
    # le 'buteur'/'marque' generique pour ne pas valider a tort un 2+ buts
    # avec 1 seul but.
    #
    # DETECTION : on regarde UNIQUEMENT le label (et non le type, car
    # 'Double Chance Buteur' = pick A OU B marque, et matchait aussi
    # le check 'double in type and buteur in type' -> faux positifs).
    # Pattern fiable : '2+' (ex: 'marque 2+ buts'), ou ' 2 buts' explicite.
    label_low = _clean(pick.get("label") or "")
    is_2plus_buts = (
        "2+ buts" in label_low or "2+buts" in label_low
        or " 2 buts" in label_low or "marque 2 buts" in label_low
    )
    if is_2plus_buts:
        return ("WIN" if goals >= 2 else "LOSS"), actual
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

        # Verification cle : la date du match retourne par FotMob doit
        # coller au pick_date. Si FotMob renvoie un match d'une autre date
        # (bug slug FotMob qui pointe sur un ancien match du meme matchup),
        # on PUSH (remboursement) le pick.
        pick_date_str = (pick_date or "")[:10]
        ev_date_str = (ev.get("utcTime") or "")[:10]
        if pick_date_str and ev_date_str and pick_date_str != ev_date_str:
            # Tolere +/- 1 jour pour les decalages timezone
            from datetime import datetime as _dt, timedelta as _td
            try:
                pd = _dt.strptime(pick_date_str, "%Y-%m-%d")
                ed = _dt.strptime(ev_date_str, "%Y-%m-%d")
                if abs((pd - ed).days) > 1:
                    print(f"  [PUSH] match {mid} - FotMob renvoie une AUTRE date "
                          f"(pick={pick_date_str} vs FotMob={ev_date_str}) - "
                          f"bug slug FotMob, on rembourse")
                    for p in mpicks:
                        p["result"] = "PUSH"
                        p["actual"] = f"FotMob slug pointe sur match {ev_date_str} (mauvaise data)"
                        p["resolved_at"] = datetime.now().strftime("%Y-%m-%d %H:%M")
                        n_resolved += 1
                    continue
            except Exception:
                pass

        # Garde explicite : si kickoff dans le futur, NE PAS résoudre.
        # Évite le bug "France gagne WIN=3-4" sur un match pas joué (cache
        # FotMob pollué qui renvoie un score résiduel d'un autre match).
        ev_utc = ev.get("utcTime")
        if ev_utc:
            try:
                from datetime import timezone
                ko = datetime.fromisoformat(ev_utc.replace("Z", "+00:00"))
                if (ko - datetime.now(tz=timezone.utc)).total_seconds() > 0:
                    print(f"  [pending] match {mid} - kickoff dans le futur ({ev_utc})")
                    n_skipped += len(mpicks)
                    continue
            except Exception:
                pass

        # Si match programmé > 12h passé ET FotMob ne donne pas de score :
        # match probablement annulé/reporté/data foireux → PUSH (rembourse)
        # plutôt que rester en PENDING éternel (vu sur FK TransINVEST).
        if ev_utc:
            try:
                from datetime import timezone
                ko = datetime.fromisoformat(ev_utc.replace("Z", "+00:00"))
                hours_since = (datetime.now(tz=timezone.utc) - ko).total_seconds() / 3600
                if hours_since > 12 and not ev.get("score"):
                    print(f"  [PUSH] match {mid} - kickoff il y a {hours_since:.1f}h sans score (annulé/data foireuse)")
                    for p in mpicks:
                        p["result"] = "PUSH"
                        p["actual"] = "Match data unavailable (likely cancelled/postponed)"
                        p["resolved_at"] = datetime.now().strftime("%Y-%m-%d %H:%M")
                        n_resolved += 1
                    continue
            except Exception:
                pass

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
