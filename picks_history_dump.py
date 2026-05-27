"""
picks_history_dump.py - Merge tous les picks resolus (foot + NBA + tennis) en
un seul fichier consolide data/all_picks_history.json pour faciliter l'analyse
algorithmique.

Format de sortie :
{
  "generated_at": "...",
  "n_total": <int>,
  "by_sport": {"foot": N, "nba": N, "tennis": N},
  "picks": [
    {
      "sport":       "foot" | "nba" | "tennis",
      "id":          "<unique id>",
      "date":        "YYYY-MM-DD",
      "matchup":     "Team A vs Team B" | "Player A vs Player B",
      "market":      "PTS" | "shots" | "tennis_winner" | "X2" | etc,
      "label":       "Plus de 22.5 jeux",
      "line":        <float|None>,
      "direction":   "over" | "under" | None,
      "confidence":  <int 0-100>,
      "cote":        <float|None>,        # cote bookmaker reelle
      "cote_min":    <float|None>,        # cote minimale (1/conf)
      "edge_pp":     <float|None>,        # edge en pp (pour tennis)
      "result":      "WIN" | "LOSS" | "PUSH" | "DNP",
      "actual":      <value|None>,        # valeur reelle (pour O/U)
      "context": {                        # features pour analyse
        "surface":         "Clay"|"Hard"|"Grass" (tennis),
        "tour":            "ATP"|"WTA" (tennis),
        "is_b2b":          bool (NBA),
        "is_real_line":    bool (NBA),
        "hit_l10_pct":     float (NBA),
        "is_home":         bool (foot/NBA),
        "league":          str (foot),
      },
    }
  ]
}
"""
import json
from pathlib import Path
from datetime import datetime

DATA = Path("data")
OUT = DATA / "all_picks_history.json"


def _load(path):
    if not path.exists(): return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"  [hist err] {path}: {e}")
        return None


def _to_float(v):
    try: return float(v) if v is not None else None
    except: return None


def normalize_foot(p):
    """Convertit pick foot (picks_history.json) en format unifie."""
    if not p or p.get("result") not in ("WIN", "LOSS", "PUSH"):
        return None
    return {
        "sport":      "foot",
        "id":         p.get("id"),
        "date":       p.get("date"),
        "matchup":    p.get("matchup"),
        "market":     p.get("type") or "?",
        "label":      p.get("label"),
        "line":       None,
        "direction":  p.get("direction"),
        "confidence": p.get("confidence"),
        "cote":       _to_float(p.get("cote")),
        "cote_min":   None,
        "edge_pp":    None,
        "result":     p.get("result"),
        "actual":     None,
        "context": {
            "league":      p.get("league"),
            "is_fun":      bool(p.get("is_fun")),
        },
    }


def normalize_nba(p):
    """Convertit pick NBA (nba_picks_history.json) en format unifie."""
    if not p or p.get("result") not in ("WIN", "LOSS", "PUSH"):
        return None
    # DNP : joueur absent, ne compte pas dans le WR mais on le garde pour stats
    return {
        "sport":      "nba",
        "id":         p.get("id"),
        "date":       p.get("date"),
        "matchup":    p.get("matchup"),
        "market":     p.get("prop"),
        "label":      p.get("label"),
        "line":       _to_float(p.get("line")),
        "direction":  p.get("direction"),
        "confidence": p.get("confidence"),
        "cote":       _to_float(p.get("real_cote") or p.get("cote")),
        "cote_min":   _to_float(p.get("cote_min")),
        "edge_pp":    _to_float(p.get("edge")),
        "result":     p.get("result"),
        "actual":     p.get("actual"),
        "context": {
            "player":         p.get("player"),
            "team":           p.get("team"),
            "side":           p.get("side"),
            "is_real_line":   bool(p.get("is_real_line")),
            "hit_l10_pct":    _to_float(p.get("hit_l10_pct")),
            "hit_l20_pct":    _to_float(p.get("hit_l20_pct")),
            "trend":          p.get("trend"),
        },
    }


def normalize_tennis(p):
    """Convertit pick tennis (tennis_picks_history.json, futur) en format unifie."""
    if not p or p.get("result") not in ("WIN", "LOSS", "PUSH"):
        return None
    return {
        "sport":      "tennis",
        "id":         p.get("id") or p.get("event_id"),
        "date":       p.get("date") or (p.get("start_iso") or "")[:10],
        "matchup":    p.get("matchup"),
        "market":     p.get("kind"),
        "label":      p.get("label"),
        "line":       _to_float(p.get("line")),
        "direction":  p.get("direction"),
        "confidence": p.get("confidence"),
        "cote":       _to_float(p.get("real_cote")),
        "cote_min":   _to_float(p.get("cote_min")),
        "edge_pp":    _to_float(p.get("edge_pp")),
        "result":     p.get("result"),
        "actual":     p.get("actual"),
        "context": {
            "surface":     p.get("surface"),
            "tour":        p.get("tour"),
            "tournament":  p.get("tournament"),
            "selection":   p.get("selection"),
        },
    }


def main():
    all_picks = []
    counts = {"foot": 0, "nba": 0, "tennis": 0}

    foot = _load(DATA / "picks_history.json")
    for p in (foot or {}).get("picks", []):
        np = normalize_foot(p)
        if np:
            all_picks.append(np); counts["foot"] += 1

    nba = _load(DATA / "nba_picks_history.json")
    for p in (nba or {}).get("picks", []):
        np = normalize_nba(p)
        if np:
            all_picks.append(np); counts["nba"] += 1

    tennis = _load(DATA / "tennis_picks_history.json")
    for p in (tennis or {}).get("picks", []):
        np = normalize_tennis(p)
        if np:
            all_picks.append(np); counts["tennis"] += 1

    # Tri par date desc
    all_picks.sort(key=lambda p: p.get("date") or "", reverse=True)

    payload = {
        "generated_at": datetime.now().isoformat(),
        "n_total":      len(all_picks),
        "by_sport":     counts,
        "picks":        all_picks,
    }
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"all_picks_history -> {OUT} ({counts['foot']} foot + {counts['nba']} NBA + {counts['tennis']} tennis = {len(all_picks)} total)")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
