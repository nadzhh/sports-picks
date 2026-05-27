"""
tennis_picks_engine.py - Genere des picks tennis pour les matchs de
data/tennis_matches.json. 3 marches couverts :

  1. Vainqueur du match       (h2h - principal)
  2. Total jeux Over/Under    (lignes 20.5/21.5/22.5/23.5/24.5)
  3. Score sets exact         (2-0/2-1/0-2/1-2 pour BO3, 3-0/3-1/3-2 pour BO5)

Algo Vainqueur :
  - implied_prob = (1/odd) / sum(1/odd_a + 1/odd_b)   (de-vigging consensus)
  - model_prob   = blend(rank_elo, l10_form, surface_form, h2h)
  - edge         = model - implied
  - emit pick si |edge| >= MIN_EDGE et conf >= MIN_CONF

Algo Total jeux :
  - expected = moyenne des 2 joueurs sur (games_for + games_against) / 2
  - compare a chaque ligne standard, emit si |expected - line| >= 1.5 et conf >= 60

Algo Sets :
  - p_set deduit de p_match (modele Bernoulli iteratif)
  - emit le score le plus probable si proba >= 35%

Sortie : data/tennis_picks.json
"""
import json, math, sys
from pathlib import Path
from datetime import datetime

DATA = Path("data")
IN_PATH  = DATA / "tennis_matches.json"
OUT_PATH = DATA / "tennis_picks.json"

# Tuning thresholds
MIN_EDGE_WIN   = 0.05      # 5% d'edge mini pour vainqueur
MIN_CONF_WIN   = 55        # 55% mini de proba modele
MIN_CONF_TOTAL = 60        # 60% mini pour O/U jeux
MIN_DELTA_GAMES = 1.5      # ecart mini expected vs line
MIN_CONF_SET   = 35        # 35% mini pour score exact (rare donc seuil bas)


def _implied_devigged(odd_a, odd_b):
    """Probabilites de-viggees (somme = 1)."""
    if not (odd_a and odd_b and odd_a > 1 and odd_b > 1):
        return None, None
    pa = 1.0 / odd_a
    pb = 1.0 / odd_b
    s  = pa + pb
    return pa / s, pb / s


def _rank_elo_prob(rank_a, rank_b):
    """ELO-like proba que A batte B, base sur le rank ATP/WTA.

    Pas une vraie ELO mais une approximation utilisable :
      - 50 places d'ecart -> ~62% pour le mieux classe
      - 100 places       -> ~73%
      - 200 places       -> ~85%
    """
    if not (rank_a and rank_b):
        return 0.5
    # Plus on est haut (rank=1 > rank=100), mieux on est
    diff = rank_b - rank_a  # > 0 si A mieux classe
    return 1.0 / (1.0 + 10 ** (-diff / 200.0))


def _form_adjust(p_base, side_a, side_b):
    """Ajuste p_base selon ecart de forme L10 + surface."""
    def wr(side, key_w, key_n):
        n = side.get(key_n, 0) or 0
        if n < 3: return None
        return side.get(key_w, 0) / n
    l10_a = wr(side_a, "l10_w", "l10_n")
    l10_b = wr(side_b, "l10_w", "l10_n")
    sf_a  = wr(side_a, "surface_w", "surface_n")
    sf_b  = wr(side_b, "surface_w", "surface_n")
    # Ajustement L10 : max +-5% sur p
    if l10_a is not None and l10_b is not None:
        delta = (l10_a - l10_b) * 0.10   # 0.10 max si full 100% vs 0%
        p_base = max(0.05, min(0.95, p_base + delta))
    # Ajustement surface : max +-7%
    if sf_a is not None and sf_b is not None:
        delta = (sf_a - sf_b) * 0.14
        p_base = max(0.05, min(0.95, p_base + delta))
    return p_base


def _h2h_adjust(p_base, h2h, total_n):
    """Ajustement H2H (~3% max)."""
    if not h2h or total_n < 2:
        return p_base
    hw = h2h.get("home_wins", 0) or 0
    aw = h2h.get("away_wins", 0) or 0
    if hw + aw < 2: return p_base
    rate_a = hw / (hw + aw)
    delta = (rate_a - 0.5) * 0.06
    return max(0.05, min(0.95, p_base + delta))


def _model_prob(side_a, side_b, h2h):
    p = _rank_elo_prob(side_a.get("rank"), side_b.get("rank"))
    p = _form_adjust(p, side_a, side_b)
    p = _h2h_adjust(p, h2h, (h2h or {}).get("total", 0))
    return p


def _p_set_from_p_match(p_match, best_of=3):
    """Resout numeriquement p_set pour un match BO3 ou BO5.

    BO3 : p_match = p_set^2 * (3 - 2*p_set)
    BO5 : p_match = p_set^3 * (10*p_set^2 - 24*p_set + 15)
            (decomposition via P(3-0)+P(3-1)+P(3-2))
    """
    if best_of == 5:
        def f(ps):
            return ps**3 * (10*ps**2 - 24*ps + 15)
    else:
        def f(ps):
            return ps**2 * (3 - 2*ps)
    # Bissection [0.5, 0.99] si p_match >= 0.5, sinon symetrique
    if p_match >= 0.5:
        lo, hi = 0.5, 0.99
    else:
        lo, hi = 0.01, 0.5
    for _ in range(40):
        mid = (lo + hi) / 2
        if f(mid) < p_match:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def _set_score_probs(p_set, best_of=3):
    """Renvoie dict de probas pour chaque score (point de vue joueur p_set).

    BO3 : 2-0, 2-1, 1-2, 0-2
    BO5 : 3-0, 3-1, 3-2, 2-3, 1-3, 0-3
    """
    q = 1 - p_set
    if best_of == 5:
        return {
            "3-0": p_set**3,
            "3-1": 3 * p_set**3 * q,
            "3-2": 6 * p_set**3 * q**2,
            "2-3": 6 * p_set**2 * q**3,
            "1-3": 3 * p_set * q**3,
            "0-3": q**3,
        }
    return {
        "2-0": p_set**2,
        "2-1": 2 * p_set**2 * q,
        "1-2": 2 * p_set * q**2,
        "0-2": q**2,
    }


# ── Builders de picks ─────────────────────────────────────────────────────────

def _value_tier(cote_min):
    if not cote_min: return None
    if cote_min >= 1.45: return ("🎯", "Belle value", "#22c55e")
    if cote_min >= 1.30: return ("💎", "Value correcte", "#84cc16")
    if cote_min >= 1.22: return ("⚠️", "Value serrée", "#f59e0b")
    return ("🚫", "Quasi-impossible", "#94a3b8")


def _winner_pick(match):
    home = match["home"]; away = match["away"]
    o_a = home.get("consensus_odd") or home.get("best_odd")
    o_b = away.get("consensus_odd") or away.get("best_odd")
    if not (o_a and o_b):
        return None
    impl_a, impl_b = _implied_devigged(o_a, o_b)
    if impl_a is None:
        return None
    model_a = _model_prob(home, away, match.get("h2h"))
    model_b = 1 - model_a
    edge_a = model_a - impl_a
    edge_b = model_b - impl_b
    # Pick le cote avec le plus grand edge si seuils respectes
    best_side, best_edge, best_model, best_impl = None, 0, 0, 0
    if edge_a >= MIN_EDGE_WIN and model_a * 100 >= MIN_CONF_WIN:
        best_side, best_edge, best_model, best_impl = "home", edge_a, model_a, impl_a
    if edge_b > best_edge and edge_b >= MIN_EDGE_WIN and model_b * 100 >= MIN_CONF_WIN:
        best_side, best_edge, best_model, best_impl = "away", edge_b, model_b, impl_b
    if not best_side:
        return None
    player = home if best_side == "home" else away
    other  = away if best_side == "home" else home
    real_cote = (player.get("best_odd") or 0) or (player.get("consensus_odd") or 0)
    cote_min = round(1 / best_model, 2) if best_model > 0 else None
    reasoning_parts = [
        f"📊 Cote {real_cote:.2f} (book : {match.get('best_book') or 'consensus'}) → implied {best_impl*100:.1f}%",
        f"🎯 Modèle : {best_model*100:.1f}% (edge +{best_edge*100:.1f} pts)",
    ]
    # Detail des composantes
    if player.get("rank") and other.get("rank"):
        reasoning_parts.append(f"📈 Ranks : #{player['rank']} vs #{other['rank']}")
    if player.get("l10_n", 0) >= 3:
        reasoning_parts.append(f"🔥 Forme {player['name']} : L10 {player['l10_w']}-{player['l10_l']}")
    if other.get("l10_n", 0) >= 3:
        reasoning_parts.append(f"🔥 Forme {other['name']} : L10 {other['l10_w']}-{other['l10_l']}")
    if player.get("surface_n", 0) >= 3 or other.get("surface_n", 0) >= 3:
        reasoning_parts.append(
            f"🌍 {match['surface']} : "
            f"{player['name']} {player.get('surface_w',0)}-{player.get('surface_l',0)} · "
            f"{other['name']} {other.get('surface_w',0)}-{other.get('surface_l',0)}"
        )
    h2h = match.get("h2h") or {}
    if h2h.get("total", 0) >= 1:
        reasoning_parts.append(f"🤝 H2H : {player['name']} mène {h2h.get('home_wins' if best_side=='home' else 'away_wins',0)}-{h2h.get('away_wins' if best_side=='home' else 'home_wins',0)}")
    return {
        "kind":       "tennis_winner",
        "label":      f"Vainqueur : {player['name']}",
        "selection":  best_side,
        "confidence": round(best_model * 100),
        "edge_pp":    round(best_edge * 100, 1),
        "real_cote":  round(real_cote, 2) if real_cote else None,
        "cote_min":   cote_min,
        "value":      _value_tier(cote_min),
        "reasoning":  "\n".join(reasoning_parts),
    }


def _total_games_pick(match, p_match):
    """Pick O/U jeux. p_match = proba modele que home gagne (utile pour weighting des sets)."""
    home = match["home"]; away = match["away"]
    # On a besoin des moyennes pour les 2 joueurs (games_for + games_against)
    def avg_total(side):
        gf = side.get("avg_games_for"); ga = side.get("avg_games_against")
        if gf is None or ga is None: return None
        return gf + ga
    t_a = avg_total(home); t_b = avg_total(away)
    if t_a is None or t_b is None:
        return None
    expected = (t_a + t_b) / 2
    # Ajustement surface : terre battue = matchs plus longs (+1.5), gazon = plus courts (-1.5)
    surface = (match.get("surface") or "").lower()
    if surface == "clay":   expected += 1.0
    elif surface == "grass": expected -= 1.0
    # Best-of-5 (men's Grand Slam) -> on multiplie par ratio attendu (5 sets max vs 3)
    is_bo5 = ("french_open" in match.get("sport_key","") or
              "wimbledon"  in match.get("sport_key","") or
              "us_open"    in match.get("sport_key","") or
              "aus_open"   in match.get("sport_key","")) and match.get("tour") == "ATP"
    # Ajustement quand un joueur est tres favori (match plus court)
    # p_match passe en arg dans la fonction parente -> on l'utilise pour reduire expected
    lopsidedness = abs(p_match - 0.5) * 2  # 0 = equilibre, 1 = ecrasement
    expected *= (1 - lopsidedness * 0.10)  # jusqu'a -10% si total mismatch
    if is_bo5:
        # BO5 typique : 28-38 jeux selon competitivite. Multiplier modere.
        expected = expected * 1.30
    # Lignes standard
    if is_bo5:
        lines = [30.5, 32.5, 34.5, 36.5, 38.5, 40.5]
    else:
        lines = [19.5, 20.5, 21.5, 22.5, 23.5, 24.5]
    # Choisir la ligne la plus proche de expected (plus de chance d'avoir edge)
    best_pick = None
    best_delta = 0
    for line in lines:
        delta = expected - line
        # Pour "Over X", confidence = P(games > line). On approxime via Poisson(expected)
        # Plus simple : si delta > 0, over plus probable
        # Confidence = mapping lineaire de delta vers proba (heuristique simple)
        if abs(delta) < MIN_DELTA_GAMES: continue
        direction = "over" if delta > 0 else "under"
        # Mapping conservateur : 1 jeu d'ecart ≈ 54%, 2 ≈ 58%, 3 ≈ 62%
        conf = min(72, 50 + abs(delta) * 4)
        if conf < MIN_CONF_TOTAL: continue
        if abs(delta) > abs(best_delta):
            best_pick = (line, direction, conf, expected)
            best_delta = delta
    if not best_pick:
        return None
    line, direction, conf, expected = best_pick
    cote_min = round(100 / conf, 2) if conf > 0 else None
    label = f"{'Plus de' if direction == 'over' else 'Moins de'} {line} jeux"
    reasoning = (
        f"📊 Moyenne jeux/match : {home['name']} ~{t_a:.1f} · {away['name']} ~{t_b:.1f}"
        + (f"\n🎾 Ajustement {match['surface']} : "
           + ("+1.0 (terre battue, matchs plus longs)" if surface == "clay"
              else "-1.0 (gazon, matchs plus courts)" if surface == "grass"
              else "neutre")
           if surface in ("clay","grass") else "")
        + (f"\n🏆 Best of 5 (Grand Slam H) → x1.45" if is_bo5 else "")
        + f"\n🎯 Total attendu ~{expected:.1f} jeux → ligne {line} {direction} ({conf:.0f}%)"
    )
    return {
        "kind":       "tennis_total_games",
        "label":      label,
        "line":       line,
        "direction":  direction,
        "confidence": round(conf),
        "expected":   round(expected, 1),
        "cote_min":   cote_min,
        "value":      _value_tier(cote_min),
        "reasoning":  reasoning,
    }


def _set_score_pick(match, p_match):
    """Pick score sets exact - emit le plus probable si proba >= MIN_CONF_SET."""
    is_bo5 = ("french_open" in match.get("sport_key","") or
              "wimbledon"  in match.get("sport_key","") or
              "us_open"    in match.get("sport_key","") or
              "aus_open"   in match.get("sport_key","")) and match.get("tour") == "ATP"
    best_of = 5 if is_bo5 else 3
    p_set = _p_set_from_p_match(p_match, best_of=best_of)
    probs = _set_score_probs(p_set, best_of=best_of)
    # Trouve le score le plus probable
    best_score, best_p = max(probs.items(), key=lambda x: x[1])
    conf = best_p * 100
    if conf < MIN_CONF_SET:
        return None
    cote_min = round(1 / best_p, 2) if best_p > 0 else None
    # Determine qui gagne (sens du score)
    home = match["home"]; away = match["away"]
    a_sets, b_sets = best_score.split("-")
    winner = home if int(a_sets) > int(b_sets) else away
    label = f"Score sets : {winner['name']} {best_score.replace('-', '-')}"
    reasoning = (
        f"📊 P(match) modèle : {p_match*100:.1f}% pour {home['name']}\n"
        f"📐 P(set) déduit : {p_set*100:.1f}% (BO{best_of})\n"
        f"🎯 Score le plus probable : {best_score} ({conf:.1f}%)"
    )
    return {
        "kind":       "tennis_set_score",
        "label":      label,
        "score":      best_score,
        "confidence": round(conf),
        "cote_min":   cote_min,
        "value":      _value_tier(cote_min),
        "reasoning":  reasoning,
    }


def generate_for_match(match):
    """Genere la liste des picks pour 1 match. Retourne [] si rien d'interessant."""
    picks = []
    # 1. Vainqueur
    win_pick = _winner_pick(match)
    if win_pick:
        picks.append(win_pick)
    # On a besoin de p_match pour les autres calculs
    p_match = _model_prob(match["home"], match["away"], match.get("h2h"))
    # 2. Total jeux
    tg_pick = _total_games_pick(match, p_match)
    if tg_pick:
        picks.append(tg_pick)
    # 3. Score sets (seulement si forte confiance sur le match)
    if max(p_match, 1 - p_match) >= 0.65:
        ss_pick = _set_score_pick(match, p_match)
        if ss_pick:
            picks.append(ss_pick)
    return picks


def main():
    if not IN_PATH.exists():
        print(f"  [tennis engine] {IN_PATH} introuvable - lance tennis_scraper d'abord")
        OUT_PATH.write_text(json.dumps({"matches":[]}, ensure_ascii=False), encoding="utf-8")
        return 0
    raw = json.loads(IN_PATH.read_text(encoding="utf-8"))
    matches = raw.get("matches", [])
    print(f"Tennis picks engine -> {len(matches)} matchs en entree")
    out_matches = []
    n_picks = 0
    for m in matches:
        picks = generate_for_match(m)
        if not picks: continue
        out_matches.append({**m, "picks": picks})
        n_picks += len(picks)
    payload = {
        "generated_at": datetime.now().isoformat(),
        "n_matches":    len(out_matches),
        "n_picks":      n_picks,
        "matches":      out_matches,
    }
    OUT_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  -> {OUT_PATH} ({len(out_matches)} matchs, {n_picks} picks)")
    # Resume console
    for m in out_matches[:10]:
        labels = " · ".join(p["label"] + f" ({p['confidence']}%)" for p in m["picks"])
        print(f"  · {m['home']['name']} vs {m['away']['name']} : {labels}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
