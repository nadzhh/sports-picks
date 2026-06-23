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


def _devig_1x2(home_cote, draw_cote, away_cote):
    """Retire la marge bookmaker pour récupérer les vraies probas 'fair'.
    Bovada marge typique 5-8%. Méthode : normalisation directe."""
    if not (home_cote and draw_cote and away_cote): return None, None, None
    p_h_raw = 1.0 / home_cote
    p_d_raw = 1.0 / draw_cote
    p_a_raw = 1.0 / away_cote
    total = p_h_raw + p_d_raw + p_a_raw
    if total <= 0: return None, None, None
    return p_h_raw / total, p_d_raw / total, p_a_raw / total


def _devig_binary(cote_yes, cote_no):
    """Retire la marge bookmaker pour un marché binaire (BTTS, Over/Under)."""
    if not (cote_yes and cote_no): return None, None
    p_y_raw = 1.0 / cote_yes
    p_n_raw = 1.0 / cote_no
    total = p_y_raw + p_n_raw
    if total <= 0: return None, None
    return p_y_raw / total, p_n_raw / total


def _shrink(p_model, p_market, alpha=0.75):
    """Bayesian shrinkage : p_final = (1-alpha) × p_model + alpha × p_market.
    alpha=0.75 → on fait confiance au marché 75% car peu de matchs WC pour
    calibrer notre modèle Poisson. Évite les value 240% absurdes sur les
    outsiders extrêmes (Ghana, Uzbekistan, Haïti, etc.)."""
    if p_model is None or p_market is None: return p_model
    return (1 - alpha) * p_model + alpha * p_market


def _analyse_market(match):
    """Compare cotes book à nos probas modèle pour spotter le value.

    Sur WC : shrinkage Bayésien fort (alpha=0.75) vers le marché car :
      - Peu de matchs WC pour calibrer notre Poisson
      - Les modèles statistiques surestiment les outsiders extrêmes
      - Le marché agrège plus d'info (forme, news, blessures) que notre code
    """
    markets = (match.get("match_odds") or {}).get("markets") or []
    analyse = match.get("analyse") or {}
    ft1x2 = analyse.get("ft_1x2") or {}
    btts = analyse.get("btts") or {}

    home_cote = _get_cote(markets, "Full time", side="home")
    draw_cote = _get_cote(markets, "Full time", side="draw")
    away_cote = _get_cote(markets, "Full time", side="away")
    btts_yes_cote = _get_cote(markets, "Both teams to score", choice_name="Yes")
    btts_no_cote  = _get_cote(markets, "Both teams to score", choice_name="No")
    over_25_cote  = _get_cote(markets, "Goals Over/Under (2.5)", choice_name="Over 2.5")
    under_25_cote = _get_cote(markets, "Goals Over/Under (2.5)", choice_name="Under 2.5")

    # Devig pour récupérer P_marché fair (sans marge book)
    p_h_mkt, p_d_mkt, p_a_mkt = _devig_1x2(home_cote, draw_cote, away_cote)
    p_btts_y_mkt, p_btts_n_mkt = _devig_binary(btts_yes_cote, btts_no_cote)

    # Probas modèle brut (Poisson)
    p_h_mod_raw = (ft1x2.get("home_pct") or 0) / 100.0
    p_d_mod_raw = (ft1x2.get("draw_pct") or 0) / 100.0
    p_a_mod_raw = (ft1x2.get("away_pct") or 0) / 100.0
    p_btts_y_mod_raw = (btts.get("yes") or 0) / 100.0

    # Shrink vers le marché (alpha=0.75)
    p_h_final = _shrink(p_h_mod_raw, p_h_mkt)
    p_d_final = _shrink(p_d_mod_raw, p_d_mkt)
    p_a_final = _shrink(p_a_mod_raw, p_a_mkt)
    p_btts_y_final = _shrink(p_btts_y_mod_raw, p_btts_y_mkt)

    home_val   = _value_pct(p_h_final, home_cote)
    draw_val   = _value_pct(p_d_final, draw_cote)
    away_val   = _value_pct(p_a_final, away_cote)
    btts_y_val = _value_pct(p_btts_y_final, btts_yes_cote)

    notes = []
    if home_cote and p_h_mkt is not None:
        notes.append(
            f"Cote {match.get('home','?')} {home_cote} "
            f"(book P={p_h_mkt*100:.0f}% · modèle P={p_h_mod_raw*100:.0f}% · "
            f"calibré P={p_h_final*100:.0f}%) → value {home_val}%"
        )
    if draw_cote and p_d_mkt is not None:
        notes.append(
            f"Cote nul {draw_cote} "
            f"(book P={p_d_mkt*100:.0f}% · modèle P={p_d_mod_raw*100:.0f}% · "
            f"calibré P={p_d_final*100:.0f}%) → value {draw_val}%"
        )
    if away_cote and p_a_mkt is not None:
        notes.append(
            f"Cote {match.get('away','?')} {away_cote} "
            f"(book P={p_a_mkt*100:.0f}% · modèle P={p_a_mod_raw*100:.0f}% · "
            f"calibré P={p_a_final*100:.0f}%) → value {away_val}%"
        )
    notes.append(
        "ℹ️ Probas calibrées = 25% modèle + 75% marché (shrinkage WC : "
        "marché plus fiable que notre Poisson sur si peu de matchs)"
    )

    return {
        "home_cote": home_cote, "home_value": home_val, "home_p_final": p_h_final,
        "draw_cote": draw_cote, "draw_value": draw_val, "draw_p_final": p_d_final,
        "away_cote": away_cote, "away_value": away_val, "away_p_final": p_a_final,
        "btts_yes_cote": btts_yes_cote, "btts_yes_value": btts_y_val,
        "btts_yes_p_final": p_btts_y_final,
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
    """Top bets WC : UNIQUEMENT marchés stables (Over/Under buts, BTTS, MT).
    PAS de 1X2 outsider (cote > 4) ni de pari sur l'outsider extrême
    (Ghana @16, Uzbekistan @19) — sur CDM avec si peu de matchs, le modèle ne
    peut PAS identifier de la value sur les outsiders ; les seuls vrais edges
    viennent des marchés totaux/BTTS où la statistique est plus stable."""
    home = match.get("home","?"); away = match.get("away","?")
    markets = (match.get("match_odds") or {}).get("markets") or []
    analyse = match.get("analyse") or {}

    candidates = []
    MAX_COTE = 3.50  # Exclut tous les outsiders extrêmes
    MIN_VALUE = 102  # Edge raisonnable (pas extrême)
    MAX_VALUE = 130  # Au-delà = bruit modèle, pas une vraie value

    def _add(market, selection, cote, p_final, signals):
        """Ajoute un candidat si cote raisonnable + value entre 102 et 130%."""
        if not cote or cote > MAX_COTE: return
        if p_final is None or p_final <= 0: return
        val = round(p_final * cote * 100, 1)
        if val < MIN_VALUE or val > MAX_VALUE: return
        candidates.append({
            "market": market, "selection": selection, "cote": cote,
            "model_p": p_final, "value": val, "signals": signals,
        })

    # ── 1X2 : uniquement favori (cote ≤ 3.50) ──
    _add("1X2", f"{home} gagne", mkt.get("home_cote"), mkt.get("home_p_final"),
         [f"value {mkt.get('home_value','?')}% (proba calibrée 25% modèle + 75% marché)"])
    _add("1X2", f"{away} gagne", mkt.get("away_cote"), mkt.get("away_p_final"),
         [f"value {mkt.get('away_value','?')}% (proba calibrée 25% modèle + 75% marché)"])
    _add("1X2", "Match nul", mkt.get("draw_cote"), mkt.get("draw_p_final"),
         [f"value {mkt.get('draw_value','?')}% (proba calibrée)"])

    # ── BTTS Yes/No (plus stables sur WC) ──
    btts_y = mkt.get("btts_yes_p_final")
    if btts_y and mkt.get("btts_yes_cote"):
        _add("BTTS", "Les 2 équipes marquent", mkt["btts_yes_cote"], btts_y,
             [f"value {mkt.get('btts_yes_value','?')}% (BTTS plus stable que 1X2 sur peu de matchs)"])
    if mkt.get("btts_no_cote") and btts_y:
        # Devig binary derived: P(no) = 1 - P(yes_market). Combine with model 25/75.
        # Si btts_y déjà shrinké, P(no) = 1 - btts_y
        p_no_final = 1.0 - btts_y
        _add("BTTS", "Une équipe ne marque pas", mkt["btts_no_cote"], p_no_final,
             [f"BTTS NO calibré P={p_no_final*100:.0f}% (plus stable que 1X2)"])

    # ── Over/Under 2.5 buts (très stables sur WC) ──
    if mkt.get("over_25_cote") or mkt.get("under_25_cote"):
        from_book_over = (1.0 / mkt["over_25_cote"]) if mkt.get("over_25_cote") else None
        from_book_under = (1.0 / mkt["under_25_cote"]) if mkt.get("under_25_cote") else None
        if from_book_over and from_book_under:
            total_p = from_book_over + from_book_under
            p_over_mkt = from_book_over / total_p
            p_under_mkt = from_book_under / total_p
            # Modèle Poisson : P(over 2.5)
            tot = analyse.get("total_buts") or {}
            p_over_mod = (tot.get("over_25") or 0) / 100.0 if tot.get("over_25") else None
            if p_over_mod is not None:
                p_over_final = _shrink(p_over_mod, p_over_mkt)
                p_under_final = 1.0 - p_over_final
            else:
                p_over_final = p_over_mkt
                p_under_final = p_under_mkt
            if mkt.get("over_25_cote"):
                _add("Total", "Plus de 2.5 buts", mkt["over_25_cote"], p_over_final,
                     [f"Over 2.5 calibré P={p_over_final*100:.0f}%"])
            if mkt.get("under_25_cote"):
                _add("Total", "Moins de 2.5 buts", mkt["under_25_cote"], p_under_final,
                     [f"Under 2.5 calibré P={p_under_final*100:.0f}%"])

    # ── Autres totaux : 1.5 / 3.5 ──
    for line, label_o, label_u in [(1.5, "Plus de 1.5 buts", "Moins de 1.5 buts"),
                                     (3.5, "Plus de 3.5 buts", "Moins de 3.5 buts")]:
        over_c = _get_cote(markets, f"Goals Over/Under ({line})", choice_name=f"Over {line}")
        under_c = _get_cote(markets, f"Goals Over/Under ({line})", choice_name=f"Under {line}")
        if over_c and under_c:
            p_o_raw = 1.0 / over_c
            p_u_raw = 1.0 / under_c
            tot = p_o_raw + p_u_raw
            p_over_mkt = p_o_raw / tot
            p_under_mkt = p_u_raw / tot
            _add("Total", label_o, over_c, p_over_mkt, [f"Cote book {over_c}"])
            _add("Total", label_u, under_c, p_under_mkt, [f"Cote book {under_c}"])

    # ── Mi-temps : Over 0.5 / Over 1.5 buts ──
    for line, label_o, label_u in [(0.5, "Plus de 0.5 but (mi-temps)", "Moins de 0.5 but (mi-temps)"),
                                     (1.5, "Plus de 1.5 buts (mi-temps)", "Moins de 1.5 buts (mi-temps)")]:
        over_c = _get_cote(markets, f"Half time goals Over/Under ({line})", choice_name=f"Over {line}")
        under_c = _get_cote(markets, f"Half time goals Over/Under ({line})", choice_name=f"Under {line}")
        if over_c and under_c:
            p_o_raw = 1.0 / over_c
            p_u_raw = 1.0 / under_c
            tot = p_o_raw + p_u_raw
            _add("Mi-temps", label_o, over_c, p_o_raw / tot, [f"Cote book {over_c}"])
            _add("Mi-temps", label_u, under_c, p_u_raw / tot, [f"Cote book {under_c}"])

    # ── Double chance favori (cote 1.01-1.30 = filler de sécurité) ──
    for mk_obj in markets:
        if mk_obj.get("marketName") != "Double chance": continue
        for c in mk_obj.get("choices", []):
            cote_dc = c.get("cote")
            if not cote_dc or cote_dc < 1.05 or cote_dc > 2.50: continue
            sd = c.get("side") or ""
            nm = c.get("name") or ""
            if sd == "1X" or sd == "X2":
                # Compose proba calibrée
                if sd == "1X":
                    p_dc = (mkt.get("home_p_final") or 0) + (mkt.get("draw_p_final") or 0)
                else:
                    p_dc = (mkt.get("away_p_final") or 0) + (mkt.get("draw_p_final") or 0)
                if p_dc > 0:
                    _add("Double chance", nm, cote_dc, p_dc,
                         [f"DC calibré P={p_dc*100:.0f}%"])

    # Dédup par selection (priorité au plus haut value)
    by_sel = {}
    for c in candidates:
        key = c["selection"]
        if key not in by_sel or c["value"] > by_sel[key]["value"]:
            by_sel[key] = c
    final = list(by_sel.values())

    # Tri par value desc, mais aussi pondéré par stabilité (BTTS/Total > 1X2)
    def _weight(c):
        bonus = 1.05 if c["market"] in ("BTTS", "Total", "Mi-temps") else 1.0
        return c["value"] * bonus
    final.sort(key=lambda c: -_weight(c))
    return final[:3]


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
