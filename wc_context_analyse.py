"""
wc_context_analyse.py — Analyse contextuelle approfondie pour chaque match WC.

Inspiré de la méthode d'analyse "FIFA World Cup 2026" pro-bettor :
au lieu de prédire l'issue (qui gagne ?), on identifie quelle équipe est
LA PLUS SUSCEPTIBLE DE SUR-PERFORMER vs les attentes du marché (cotes book).

Pour chaque match WC, on consolide les signaux issus de :
  - intl_team_sheets       : off/def scores, forme, pédigrée WC
  - wc_match_importance    : qualif acquise / doit gagner / éliminé
  - foot_match_context     : météo, stade, altitude, ville hôte
  - matches[].match_odds   : cotes book (Bovada + ESPN)
  - matches[].pre_match_form: forme L5 + classement

On produit pour chaque match :
  - tournament_dynamics : enjeu match (must_win / rotation possible / etc.)
  - environmental       : conditions stade (chaleur, vent, altitude)
  - physical            : minutes joués cumulés, fatigue, voyages
  - psychological       : forme momentum, confiance
  - market_intelligence : où l'algo voit du value vs marché
  - top_signals         : 5 signaux les plus forts (W/L/Total/BTTS)
  - underestimated      : 3 facteurs sous-évalués par le marché
  - top_risks           : 3 risques principaux
  - recommendation      : "Panama est l'équipe qui peut surperformer si ..."
  - uncertainty_level   : Low / Moderate / High / Extreme
  - top3_bets           : 3 picks classés par EV long-terme

Sortie : enrichit data/matches.json avec match["wc_analysis"].
"""
import json, math
from pathlib import Path
from datetime import datetime, timezone

MATCHES_FILE   = Path("data/matches.json")
SHEETS_FILE    = Path("data/intl_team_sheets.json")
IMPORTANCE_FILE= Path("data/wc_match_importance.json")


def _load(path, default=None):
    if not path.exists(): return default
    try: return json.loads(path.read_text(encoding="utf-8"))
    except Exception: return default


def _norm_slug(name):
    if not name: return ""
    import unicodedata, re
    s = unicodedata.normalize("NFD", name)
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    return re.sub(r"[^a-z0-9_]", "_", s.lower()).strip("_")


def _get_cote(markets, mkt_name, side=None, choice_name=None):
    """Cherche cote depuis match_odds.markets."""
    for mk in (markets or []):
        if mk.get("marketName") != mkt_name: continue
        for c in mk.get("choices", []):
            if side and c.get("side") == side:
                return c.get("cote")
            if choice_name and c.get("name") == choice_name:
                return c.get("cote")
    return None


def _implied_p(cote):
    """Proba implicite depuis cote décimale (sans correction marge)."""
    if not cote or cote <= 1: return None
    return round(1.0 / cote, 3)


def _value_pct(model_p, cote):
    """value (%) = P_modèle × cote × 100. > 100% = edge positif."""
    if model_p is None or not cote: return None
    return round(model_p * cote * 100, 1)


# ─── Analyse par axe ─────────────────────────────────────────────────────────

def _analyse_tournament_dynamics(match, importance_data):
    """Statut tournament : doit gagner / qualif acquise / éliminé."""
    teams = (importance_data or {}).get("teams") or {}
    h_slug = _norm_slug(match.get("home", ""))
    a_slug = _norm_slug(match.get("away", ""))
    h_imp = teams.get(h_slug) or {}
    a_imp = teams.get(a_slug) or {}

    notes = []
    risk_score = 0  # plus haut = plus de risque rotation/désinvestissement
    h_status = h_imp.get("status_fr") or "1er match du tournoi"
    a_status = a_imp.get("status_fr") or "1er match du tournoi"
    h_mod = h_imp.get("importance_modifier", 1.0)
    a_mod = a_imp.get("importance_modifier", 1.0)

    notes.append(f"{match.get('home','?')} : {h_status} (importance λ={h_mod})")
    notes.append(f"{match.get('away','?')} : {a_status} (importance λ={a_mod})")

    if h_imp.get("status") == "qualif_done" or a_imp.get("status") == "qualif_done":
        notes.append("⚠️ Au moins une équipe a déjà sa qualification → rotation/intensité réduite possibles")
        risk_score += 2
    if h_imp.get("status") == "must_win" or a_imp.get("status") == "must_win":
        notes.append("🔥 Au moins une équipe DOIT gagner → engagement maximal")
        risk_score -= 1

    return {
        "home_status":  h_status,
        "away_status":  a_status,
        "home_mod":     h_mod,
        "away_mod":     a_mod,
        "risk_score":   risk_score,
        "notes":        notes,
    }


def _analyse_environmental(match):
    """Conditions stade : météo, altitude, climatisé, heure KO."""
    ctx = match.get("context") or {}
    wx = ctx.get("weather") or {}
    std = ctx.get("stadium") or {}

    notes = []
    severity = 0  # impact attendu sur le jeu
    tmax = wx.get("temp_max") or 0
    humid = wx.get("humidity") or 0
    wind = wx.get("wind_max_kmh") or 0
    prec = wx.get("precipitation_sum_mm") or 0
    alt = std.get("altitude_m") or 0
    climatized = bool(std.get("climatized"))

    if std.get("name"):
        ctx_extra = std.get("context_fr") or ""
        notes.append(f"🏟️ {std.get('name')} ({std.get('city')}) — alt {alt}m, {('🏠 fermé/climatisé' if climatized else '⛅ plein air')}")
        if ctx_extra:
            notes.append(f"   ↳ {ctx_extra}")

    if tmax >= 32 and not climatized:
        notes.append(f"🥵 Chaleur extrême ({tmax}°C) → fatigue + ralentit le rythme")
        severity += 2
    elif tmax >= 28 and not climatized:
        notes.append(f"☀️ Chaleur modérée ({tmax}°C)")
        severity += 1

    if humid >= 70 and not climatized:
        notes.append(f"💧 Humidité élevée ({humid}%) → endurance pénalisée")
        severity += 1

    if alt >= 1500:
        notes.append(f"⛰️ Altitude {alt}m → joueurs non-acclimatés en souffrance")
        severity += 1

    if prec >= 5 and not climatized:
        notes.append(f"🌧️ Pluie attendue ({prec}mm) → jeu défensif, peu de buts")
        severity += 1

    if wind >= 30 and not climatized:
        notes.append(f"💨 Vent fort ({wind}km/h) → passes longues imprécises")
        severity += 1

    return {
        "temp_max":   tmax,
        "humidity":   humid,
        "wind":       wind,
        "altitude":   alt,
        "climatized": climatized,
        "severity":   severity,
        "notes":      notes,
    }


def _analyse_form_and_squad(match, sheets_data):
    """Forme L5/L10 + sheet équipe nationale."""
    teams = (sheets_data or {}).get("teams") or {}
    h_slug = _norm_slug(match.get("home", ""))
    a_slug = _norm_slug(match.get("away", ""))
    h_sh = teams.get(h_slug) or {}
    a_sh = teams.get(a_slug) or {}

    notes = []
    h_off = h_sh.get("off_score", 0)
    h_def = h_sh.get("def_score", 0)
    a_off = a_sh.get("off_score", 0)
    a_def = a_sh.get("def_score", 0)
    h_form = h_sh.get("form", "")
    a_form = a_sh.get("form", "")
    h_ped = (h_sh.get("wc_pedigree") or {}).get("label", "?")
    a_ped = (a_sh.get("wc_pedigree") or {}).get("label", "?")

    home_name = match.get("home","?")
    away_name = match.get("away","?")
    if h_sh:
        notes.append(f"{home_name} : off {h_off:+.2f} · def {h_def:+.2f} · forme {h_form} · 🏆 {h_ped}")
    if a_sh:
        notes.append(f"{away_name} : off {a_off:+.2f} · def {a_def:+.2f} · forme {a_form} · 🏆 {a_ped}")

    # Signal momentum : 4+ W sur L5 = momentum
    if h_form.count("W") >= 4:
        notes.append(f"🔥 {home_name} sur une dynamique exceptionnelle (4+ V sur L5)")
    if a_form.count("W") >= 4:
        notes.append(f"🔥 {away_name} sur une dynamique exceptionnelle (4+ V sur L5)")
    if h_form.count("L") >= 3:
        notes.append(f"📉 {home_name} en crise (3+ défaites sur L5)")
    if a_form.count("L") >= 3:
        notes.append(f"📉 {away_name} en crise (3+ défaites sur L5)")

    return {
        "home_off_score": h_off,  "home_def_score": h_def,
        "away_off_score": a_off,  "away_def_score": a_def,
        "home_form":      h_form, "away_form":      a_form,
        "home_pedigree":  h_ped,  "away_pedigree":  a_ped,
        "notes":          notes,
    }


def _analyse_market(match):
    """Compare cotes book à nos probas modèle pour spotter le value."""
    markets = (match.get("match_odds") or {}).get("markets") or []
    analyse = match.get("analyse") or {}
    ft1x2 = analyse.get("ft_1x2") or {}
    btts = analyse.get("btts") or {}

    # Récupère cotes book
    home_cote = _get_cote(markets, "Full time", side="home")
    draw_cote = _get_cote(markets, "Full time", side="draw")
    away_cote = _get_cote(markets, "Full time", side="away")
    btts_yes_cote = _get_cote(markets, "Both teams to score", choice_name="Yes")
    btts_no_cote  = _get_cote(markets, "Both teams to score", choice_name="No")
    over_25_cote  = _get_cote(markets, "Goals Over/Under (2.5)", choice_name="Over 2.5")
    under_25_cote = _get_cote(markets, "Goals Over/Under (2.5)", choice_name="Under 2.5")

    # Probas modèle (en %)
    p_h_mod = (ft1x2.get("home_pct") or 0) / 100.0
    p_d_mod = (ft1x2.get("draw_pct") or 0) / 100.0
    p_a_mod = (ft1x2.get("away_pct") or 0) / 100.0
    p_btts_y_mod = (btts.get("yes") or 0) / 100.0

    home_val   = _value_pct(p_h_mod, home_cote)
    draw_val   = _value_pct(p_d_mod, draw_cote)
    away_val   = _value_pct(p_a_mod, away_cote)
    btts_y_val = _value_pct(p_btts_y_mod, btts_yes_cote)

    notes = []
    if home_cote:
        notes.append(f"Cote {match.get('home','?')} {home_cote} (book P={_implied_p(home_cote)*100:.0f}%, modèle P={p_h_mod*100:.0f}%) → value {home_val}%")
    if draw_cote:
        notes.append(f"Cote nul {draw_cote} (book P={_implied_p(draw_cote)*100:.0f}%, modèle P={p_d_mod*100:.0f}%) → value {draw_val}%")
    if away_cote:
        notes.append(f"Cote {match.get('away','?')} {away_cote} (book P={_implied_p(away_cote)*100:.0f}%, modèle P={p_a_mod*100:.0f}%) → value {away_val}%")

    return {
        "home_cote": home_cote, "home_value": home_val,
        "draw_cote": draw_cote, "draw_value": draw_val,
        "away_cote": away_cote, "away_value": away_val,
        "btts_yes_cote": btts_yes_cote, "btts_yes_value": btts_y_val,
        "btts_no_cote":  btts_no_cote,
        "over_25_cote":  over_25_cote,
        "under_25_cote": under_25_cote,
        "notes":         notes,
    }


# ─── Synthèse + recommandation ───────────────────────────────────────────────

def _build_top_signals(match, dyn, env, form, mkt):
    """Liste des signaux triés par force."""
    signals = []

    # Forme
    if form.get("home_form", "").count("W") >= 4:
        signals.append(("🔥", "Momentum HOME : 4+ V sur L5", 4))
    if form.get("away_form", "").count("W") >= 4:
        signals.append(("🔥", "Momentum AWAY : 4+ V sur L5", 4))
    if form.get("home_form", "").count("L") >= 3:
        signals.append(("📉", "HOME en crise : 3+ défaites L5", 3))
    if form.get("away_form", "").count("L") >= 3:
        signals.append(("📉", "AWAY en crise : 3+ défaites L5", 3))

    # Off/def score
    if abs(form.get("home_off_score", 0)) >= 0.5:
        s = form.get("home_off_score", 0)
        signals.append(("⚽", f"HOME off score {s:+.2f} (sur/sous-performe Elo)", 3 if abs(s) >= 0.7 else 2))
    if abs(form.get("away_off_score", 0)) >= 0.5:
        s = form.get("away_off_score", 0)
        signals.append(("⚽", f"AWAY off score {s:+.2f}", 3 if abs(s) >= 0.7 else 2))

    # Importance match
    if dyn.get("risk_score", 0) > 0:
        signals.append(("⚠️", "Rotation/désinvestissement possible (qualif déjà jouée)", 4))
    if dyn.get("risk_score", 0) < 0:
        signals.append(("🔥", "Match à enjeu maximal (must win)", 4))

    # Environnement
    if env.get("severity", 0) >= 2:
        signals.append(("🥵", f"Conditions extrêmes (severity {env['severity']})", env["severity"]))

    # Pédigrée WC
    if "élite" in (form.get("home_pedigree") or "").lower():
        signals.append(("🏆", "HOME = élite mondiale WC (expérience)", 3))
    if "élite" in (form.get("away_pedigree") or "").lower():
        signals.append(("🏆", "AWAY = élite mondiale WC", 3))

    # Tri par force descendant
    signals.sort(key=lambda x: -x[2])
    return signals[:5]


def _build_top_bets(match, dyn, env, form, mkt):
    """Identifie le top 3 picks par valeur attendue."""
    bets = []
    home = match.get("home","?"); away = match.get("away","?")

    # Hypothèses :
    # 1) Si value > 110% sur 1X2 → fort pick
    # 2) BTTS / Over Under selon λ
    # 3) Si qualif acquise + chaleur extrême → Under buts probable
    candidates = []
    if mkt.get("home_value") and mkt["home_value"] >= 110 and mkt.get("home_cote"):
        candidates.append({
            "market": "1X2",
            "selection": f"{home} gagne",
            "cote": mkt["home_cote"],
            "model_p": (match.get("analyse", {}).get("ft_1x2", {}).get("home_pct", 0)) / 100,
            "value": mkt["home_value"],
            "signals": [f"value {mkt['home_value']}% vs marché"],
        })
    if mkt.get("away_value") and mkt["away_value"] >= 110 and mkt.get("away_cote"):
        candidates.append({
            "market": "1X2",
            "selection": f"{away} gagne",
            "cote": mkt["away_cote"],
            "model_p": (match.get("analyse", {}).get("ft_1x2", {}).get("away_pct", 0)) / 100,
            "value": mkt["away_value"],
            "signals": [f"value {mkt['away_value']}% vs marché"],
        })
    if mkt.get("draw_value") and mkt["draw_value"] >= 105 and mkt.get("draw_cote"):
        candidates.append({
            "market": "1X2",
            "selection": "Match nul",
            "cote": mkt["draw_cote"],
            "model_p": (match.get("analyse", {}).get("ft_1x2", {}).get("draw_pct", 0)) / 100,
            "value": mkt["draw_value"],
            "signals": [f"value {mkt['draw_value']}% vs marché"],
        })
    if mkt.get("btts_yes_value") and mkt["btts_yes_value"] >= 105 and mkt.get("btts_yes_cote"):
        candidates.append({
            "market": "BTTS",
            "selection": "Les 2 équipes marquent",
            "cote": mkt["btts_yes_cote"],
            "model_p": (match.get("analyse", {}).get("btts", {}).get("yes", 0)) / 100,
            "value": mkt["btts_yes_value"],
            "signals": [f"value {mkt['btts_yes_value']}% vs marché"],
        })

    # Si qualif acquise + chaleur → Under 2.5 forte hypothèse
    if dyn.get("risk_score", 0) >= 2 and env.get("severity", 0) >= 2 and mkt.get("under_25_cote"):
        u_cote = mkt["under_25_cote"]
        candidates.append({
            "market": "Total",
            "selection": "Moins de 2.5 buts",
            "cote": u_cote,
            "model_p": 0.65,  # estimation upgrade
            "value": _value_pct(0.65, u_cote),
            "signals": ["Qualif acquise + chaleur → faible intensité",
                        f"value estimée {_value_pct(0.65, u_cote)}%"],
        })

    # Tri par EV (model_p * cote) descendant
    candidates.sort(key=lambda c: -(c["model_p"] * (c["cote"] or 0)))
    return candidates[:3]


def _build_uncertainty(dyn, env, form, mkt):
    """Note le niveau d'incertitude global du match."""
    risk = 0
    if dyn.get("risk_score", 0) >= 2: risk += 2
    if env.get("severity", 0) >= 2:    risk += 1
    if abs(form.get("home_off_score", 0) - form.get("away_off_score", 0)) < 0.3: risk += 1
    # Pas de pick haute value identifiée → marché efficient
    max_val = max((mkt.get(k) or 0) for k in ("home_value", "draw_value", "away_value", "btts_yes_value"))
    if max_val < 105: risk += 1

    if risk >= 4: return "Extreme"
    if risk >= 2: return "High"
    if risk >= 1: return "Moderate"
    return "Low"


def analyse_match_wc(match, sheets_data, importance_data):
    """Produit la fiche d'analyse WC complète d'un match."""
    dyn = _analyse_tournament_dynamics(match, importance_data)
    env = _analyse_environmental(match)
    form = _analyse_form_and_squad(match, sheets_data)
    mkt = _analyse_market(match)
    top_signals = _build_top_signals(match, dyn, env, form, mkt)
    top_bets = _build_top_bets(match, dyn, env, form, mkt)
    uncertainty = _build_uncertainty(dyn, env, form, mkt)

    # Reco synthétique
    home = match.get("home","?"); away = match.get("away","?")
    recommandation = ""
    if top_bets:
        b = top_bets[0]
        recommandation = (
            f"Meilleur pari long-terme : {b['selection']} @ {b['cote']} "
            f"(value {b.get('value','?')}%). "
        )
    else:
        recommandation = "Marché efficient — aucun edge clair identifié sur ce match."

    return {
        "tournament_dynamics": dyn,
        "environmental":       env,
        "form_and_squad":      form,
        "market_intelligence": mkt,
        "top_signals":         [{"emoji": s[0], "text": s[1], "strength": s[2]} for s in top_signals],
        "top3_bets":           top_bets,
        "uncertainty_level":   uncertainty,
        "recommandation":      recommandation,
        "generated_at":        datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


def run():
    matches = _load(MATCHES_FILE, [])
    sheets = _load(SHEETS_FILE, {})
    importance = _load(IMPORTANCE_FILE, {})
    if not matches:
        print("[!] data/matches.json introuvable")
        return
    n = 0
    for m in matches:
        if "World" not in (m.get("league") or ""): continue
        try:
            ana = analyse_match_wc(m, sheets, importance)
            m["wc_analysis"] = ana
            n += 1
        except Exception as e:
            print(f"  [err] {m.get('home','?')} vs {m.get('away','?')} : {e}")
    MATCHES_FILE.write_text(json.dumps(matches, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[OK] {n} matchs WC analysés en profondeur (contexte + signaux + bets)")


if __name__ == "__main__":
    run()
