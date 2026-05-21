"""
learning_stats.py - Analyse retrospective des picks pour identifier les
profils gagnants vs perdants. Objectif: atteindre 70% WR.

A chaque run, lit data/nba_picks_history.json + data/picks_history.json,
groupe les picks RESOLU par 'profil' (combinaison de features) et calcule
WR + ROI par profil.

Profils trackes (NBA) :
  - prop_type    : PTS / REB / AST / FG3M / PRA / PR / PA
  - direction    : over / under
  - edge_bucket  : 0-15% / 15-25% / 25-35% / 35%+
  - conf_bucket  : 60-70% / 70-80% / 80%+
  - has_warning  : avec / sans warning (rotation/last_min/book_div)
  - cote_bucket  : 1.40-1.70 / 1.70-2.00 / 2.00+

Profils trackes (foot) :
  - category    : team / player / fun
  - prop_type   : 1X2 / DC / BTTS / totals / buteur / decisif / passeur

Sortie : data/learning_stats.json + rapport console.

Le moteur (a terme) pourra lire ce fichier pour PENALISER les profils a
faible WR dans le quality_score.
"""
import json
from pathlib import Path
from collections import defaultdict
from datetime import datetime


def _nba_profile(p):
    """Retourne le profil court d'un pick NBA (tuple hashable)."""
    edge = p.get("edge") or 0
    if   edge >= 35: edge_b = "edge35+"
    elif edge >= 25: edge_b = "edge25-35"
    elif edge >= 15: edge_b = "edge15-25"
    else:            edge_b = "edge<15"
    conf = p.get("confidence", 0)
    if   conf >= 80: conf_b = "conf80+"
    elif conf >= 70: conf_b = "conf70-80"
    else:            conf_b = "conf60-70"
    cote = p.get("real_cote") or p.get("cote_min") or 0
    if   cote >= 2.0: cote_b = "cote2.0+"
    elif cote >= 1.7: cote_b = "cote1.7-2.0"
    else:             cote_b = "cote1.4-1.7"
    has_warn = bool(p.get("rotation_warning") or p.get("injury_warning")
                    or p.get("last_min_warning") or p.get("book_divergence_warning"))
    return {
        "prop":       p.get("prop", "?"),
        "direction":  p.get("direction", "?"),
        "edge":       edge_b,
        "conf":       conf_b,
        "cote":       cote_b,
        "warn":       "warn" if has_warn else "clean",
    }


def _foot_profile(p):
    """Retourne le profil court d'un pick foot."""
    cote = p.get("cote") or 0
    if   cote >= 2.5: cote_b = "cote2.5+"
    elif cote >= 1.8: cote_b = "cote1.8-2.5"
    elif cote >= 1.4: cote_b = "cote1.4-1.8"
    else:             cote_b = "cote<1.4"
    conf = p.get("confidence", 0)
    if   conf >= 80: conf_b = "conf80+"
    elif conf >= 70: conf_b = "conf70-80"
    else:            conf_b = "conf<70"
    return {
        "category": p.get("category", "?"),
        "type":     p.get("type", "?"),
        "cote":     cote_b,
        "conf":     conf_b,
    }


def _aggregate(picks, profile_fn):
    """Group picks par profil et calcule WR + ROI."""
    by_profile = defaultdict(lambda: {"win": 0, "loss": 0, "push": 0, "dnp": 0, "roi_units": 0})
    for p in picks:
        result = p.get("result")
        if result not in ("WIN", "LOSS", "PUSH", "DNP"): continue
        prof = profile_fn(p)
        prof_key = " | ".join(f"{k}={v}" for k, v in sorted(prof.items()))
        bucket = by_profile[prof_key]
        if   result == "WIN":  bucket["win"]  += 1
        elif result == "LOSS": bucket["loss"] += 1
        elif result == "PUSH": bucket["push"] += 1
        elif result == "DNP":  bucket["dnp"]  += 1
        # ROI : (cote - 1) si win, -1 si loss
        cote = p.get("real_cote") or p.get("cote") or p.get("cote_min") or 0
        if result == "WIN" and cote:
            bucket["roi_units"] += (cote - 1)
        elif result == "LOSS":
            bucket["roi_units"] -= 1
    return by_profile


def _report(by_profile, label, min_sample=3):
    """Affiche les profils tries par WR desc, filtre par min_sample bets."""
    rows = []
    for prof_key, b in by_profile.items():
        n = b["win"] + b["loss"]
        if n < min_sample: continue
        wr = b["win"] / n * 100
        roi_pct = b["roi_units"] / n * 100 if n else 0
        rows.append((wr, roi_pct, n, b["win"], b["loss"], prof_key))
    rows.sort(reverse=True)

    print(f"\n=== {label} : profils tries par WR (min {min_sample} bets) ===")
    print(f"{'WR%':>5} {'ROI%':>7} {'n':>4} {'W':>3} {'L':>3}   profil")
    for wr, roi, n, w, l, prof in rows:
        flag = "🚀" if wr >= 70 and n >= 5 else ("⚠️" if wr < 50 else "  ")
        print(f"{flag} {wr:>5.1f} {roi:>+7.1f} {n:>4} {w:>3} {l:>3}   {prof}")


def run():
    out = {"generated_at": datetime.now().isoformat(timespec="seconds"), "nba": {}, "foot": {}}

    # NBA
    nba_hist = Path("data/nba_picks_history.json")
    if nba_hist.exists():
        try:
            data = json.loads(nba_hist.read_text(encoding="utf-8"))
            picks = data.get("picks", [])
            by_prof = _aggregate(picks, _nba_profile)
            _report(by_prof, "NBA")
            out["nba"] = {k: dict(v) for k, v in by_prof.items()}
        except Exception as e:
            print(f"[X] NBA stats err: {e}")

    # Foot
    foot_hist = Path("data/picks_history.json")
    if foot_hist.exists():
        try:
            data = json.loads(foot_hist.read_text(encoding="utf-8"))
            picks = data.get("picks", [])
            by_prof = _aggregate(picks, _foot_profile)
            _report(by_prof, "FOOT")
            out["foot"] = {k: dict(v) for k, v in by_prof.items()}
        except Exception as e:
            print(f"[X] Foot stats err: {e}")

    # Sauvegarde
    Path("data").mkdir(exist_ok=True)
    with open("data/learning_stats.json", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"\n[OK] Stats sauvegardees -> data/learning_stats.json")


if __name__ == "__main__":
    run()
