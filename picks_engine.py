"""
picks_engine.py — v10
- Forme récente prime sur H2H historique
- Analyse buteur enrichie (forme récente du joueur)
- Max 3 props joueurs par match (pas par équipe)
- Paris "fun" (cote >= 2.0) avec analyse contextuelle
- Reasoning explicatif (plus de "probabilité bookmaker X%")
"""

import json, os, math

def load_matches():
    with open("data/matches.json", encoding="utf-8") as f:
        return json.load(f)

def load_player_stats():
    path = "data/player_stats.json"
    if not os.path.exists(path): return {}
    with open(path, encoding="utf-8") as f:
        return json.load(f)

# ─── Helpers ────────────────────────────────────────────────────────────────

def get_form(fd, side):
    try: return fd.get(side, {}).get("form", [])
    except: return []

def win_rate(f):   return f.count("W") / len(f) if f else 0
def unbeaten(f):   return (f.count("W") + f.count("D")) / len(f) if f else 0
def loss_rate(f):  return f.count("L") / len(f) if f else 0

def get_pos(fd, s):
    pos = (fd or {}).get(s, {}).get("position", None)
    return pos if pos is not None else 20
def get_rat(fd, s):
    try:
        v = (fd or {}).get(s, {}).get("avgRating", None)
        return float(v) if v is not None else 6.5
    except: return 6.5

def parse_h2h(h):
    try:
        d = h.get("teamDuel", {})
        hw = int(d.get("homeWins", 0) or 0)
        dr = int(d.get("draws", 0) or 0)
        aw = int(d.get("awayWins", 0) or 0)
        return hw, dr, aw
    except: return 0,0,0

def is_cup_league(league_id):
    """Détecte si c'est une coupe européenne."""
    return league_id in {7, 679, 17015}

def home_away_context(recent, is_home):
    """
    Retourne les stats pertinentes selon si l'équipe joue à domicile ou extérieur.
    Préfère les stats home/away spécifiques si disponibles (>=3 matchs).
    """
    if not recent:
        return {}
    h_gf = recent.get("home_gf_pm", 0)
    h_ga = recent.get("home_ga_pm", 0)
    a_gf = recent.get("away_gf_pm", 0)
    a_ga = recent.get("away_ga_pm", 0)
    h_n  = recent.get("home_n", 0)
    a_n  = recent.get("away_n", 0)

    if is_home and h_n >= 3:
        return {"gf": h_gf, "ga": h_ga, "n": h_n, "src": "domicile"}
    elif not is_home and a_n >= 3:
        return {"gf": a_gf, "ga": a_ga, "n": a_n, "src": "extérieur"}
    else:
        return {"gf": recent.get("goals_for_pm",0), "ga": recent.get("goals_ag_pm",0),
                "n": recent.get("n_matches",0), "src": "L10"}

def cup_context(recent, cup_league=False):
    """
    Pour les matchs européens, retourne les stats en coupe si disponibles.
    Minimum 5 matchs pour être statistiquement fiable.
    """
    if not recent or not cup_league:
        return {}
    cup_gf = recent.get("cup_gf_pm", 0)
    cup_n  = recent.get("cup_n", 0)
    if cup_n >= 5:  # minimum 5 matchs en coupe
        return {"gf": cup_gf, "ga": recent.get("cup_ga_pm",0), "n": cup_n}
    return {}

def knockout_pressure_bonus(league_id, form):
    """
    En phase KO (demi, quart...) les équipes à domicile ont un bonus de motivation.
    Retourne un bonus 0-10% sur les probabilités offensives.
    """
    if not is_cup_league(league_id):
        return 0
    # En méforme globale mais match KO à domicile → boost motivation
    w_rate = form.count("W") / len(form) if form else 0
    # Même une équipe en méforme peut se surpasser en KO à domicile
    return 0.08  # +8% boost motivation KO

def opponent_quality_adjust(form_details):
    """
    Ajuste la valeur de la forme selon la qualité des adversaires récents.
    Si les victoires sont contre des équipes faibles → dévalue la série.
    Utilise les match_details pour détecter les adversaires.
    """
    # Pas implémentable sans classement adversaires dans details
    # → retourne 1.0 (neutre) pour l'instant
    return 1.0

def poisson_at_least(lam, k):
    """P(X >= k) pour X ~ Poisson(lam) — probabilité réelle."""
    if lam <= 0: return 0
    prob_less = sum(math.exp(-lam) * lam**i / math.factorial(i) for i in range(k))
    return round((1 - prob_less) * 100, 1)

def goals_analysis(recent, season_ts, team_name):
    """
    Retourne un dict d'analyse des buts pour un texte de reasoning.
    Combine forme récente et stats saison.
    """
    r_gf    = recent.get("goals_for_pm", 0)   if recent else 0
    r_ga    = recent.get("goals_ag_pm", 0)    if recent else 0
    r_tot   = recent.get("total_goals_pm", 0) if recent else 0
    r_btts  = recent.get("btts_rate", 0)      if recent else 0
    r_btts_c= recent.get("btts_count", 0)     if recent else 0
    r_n     = recent.get("btts_n", 0)         if recent else 0
    s_gf    = (season_ts or {}).get("goals_pm", 0)
    s_ga    = (season_ts or {}).get("conceded_pm", 0)
    return {
        "gf": r_gf or s_gf, "ga": r_ga or s_ga,
        "total": r_tot or (r_gf+r_ga) or (s_gf+s_ga),
        "btts_rate": r_btts, "btts_count": r_btts_c, "btts_n": r_n,
        "has_recent": bool(r_gf),
        "src": "L10" if r_gf else "saison",
        "s_gf": s_gf, "s_ga": s_ga,
    }

def frac2dec(s):
    try:
        if "/" in str(s):
            n, d = str(s).split("/")
            return round(int(n)/int(d)+1, 2)
        return round(float(s)+1, 2)
    except: return None

def get_mkt(odds, name, cg=None):
    try:
        for m in odds.get("markets", []):
            if m.get("marketName") == name:
                if cg is None or str(m.get("choiceGroup","")) == str(cg):
                    return m
    except: pass
    return None

def get_odds(mkt, choice):
    if not mkt: return None
    for c in mkt.get("choices", []):
        if c.get("name") == choice:
            return frac2dec(c.get("fractionalValue",""))
    return None

def prob(cote):
    return round(100/cote, 1) if cote and cote > 1 else 0

def form_summary(f):
    """Résumé lisible de la forme : ex '4V 1N 0D sur 5 matchs'"""
    if not f: return ""
    w, dn, l = f.count("W"), f.count("D"), f.count("L")
    return f"{w}V/{dn}N/{l}D sur {len(f)} matchs"

def recent_form_score(f, n=5):
    """Score 0-100 basé sur les N derniers matchs (forme récente)."""
    if not f: return 50
    recent = f[-n:]
    return round((recent.count("W") * 3 + recent.count("D")) / (len(recent) * 3) * 100)

def form_trend(f):
    """Compare les 3 derniers vs les 3 précédents pour détecter une tendance."""
    if len(f) < 6: return "stable"
    recent = f[-3:]
    older  = f[-6:-3]
    r_score = recent.count("W") * 3 + recent.count("D")
    o_score = older.count("W") * 3 + older.count("D")
    if r_score > o_score + 2: return "en grande forme"
    if r_score > o_score:     return "en hausse"
    if r_score < o_score - 2: return "en méforme"
    if r_score < o_score:     return "en légère baisse"
    return "stable"

# ─── Scoring contextuel défense adverse ─────────────────────────────────────

def defense_weakness(opp_pos, opp_rat, opp_conceded_pm=0):
    """Score 0-1 de faiblesse défensive adverse."""
    pos_s  = min(1.0, opp_pos / 20)
    rat_s  = max(0, (7.5 - opp_rat) / 2.0)
    conc_s = min(1.0, opp_conceded_pm / 3.0) if opp_conceded_pm else 0
    return round(pos_s * 0.4 + rat_s * 0.3 + conc_s * 0.3, 3)

def defense_label(w):
    if w >= 0.7: return "défense très vulnérable"
    if w >= 0.5: return "défense fragile"
    if w >= 0.3: return "défense correcte"
    return "défense solide"

# ─── Props joueurs ───────────────────────────────────────────────────────────

def player_picks_contextual(players, opp_pos, opp_rat, opp_conceded_pm=0, btts_prob=50, min_apps=5):
    """
    Analyse contextuelle enrichie avec forme récente joueur.
    Forme récente (xG, buts récents) prime sur les stats saison brutes.
    """
    if not players: return []

    weakness  = defense_weakness(opp_pos, opp_rat, opp_conceded_pm)
    def_label = defense_label(weakness)
    picks     = []

    for p in players:
        apps   = p.get("appearances", 0)
        is_sub = p.get("is_sub", False)
        if apps < min_apps: continue

        name    = p.get("shortName", p.get("name",""))
        pos     = p.get("position","")
        goals   = p.get("goals", 0)
        assists = p.get("assists", 0)
        shots   = p.get("shots", 0)
        gpm     = p.get("goals_pm", 0)
        apm     = p.get("assists_pm", 0)
        gapm    = p.get("g_a_pm", 0)
        xgpm    = p.get("xG_pm", 0)
        xapm    = p.get("xA_pm", 0)
        minutes = p.get("minutes", 0)

        sub_penalty = 0.75 if is_sub else 1.0
        sub_tag     = " ⚠️ peut-être pas titulaire" if is_sub else ""
        pos_mult    = {"F": 1.3, "M": 1.0, "D": 0.55}.get(pos, 0.8)
        ctx_bonus   = weakness * 0.35

        # ── Analyse forme récente buteur ────────────────────────────
        # Efficacité sur la saison
        shot_eff = goals / shots if shots > 0 else 0
        xg_total = xgpm * apps
        # Sur ou sous-performance par rapport au xG
        xg_conv_ratio = (goals / xg_total) if xg_total > 0.5 else 1.0

        # Contexte buts concédés adversaire
        opp_conc_txt = ""
        if opp_conceded_pm >= 1.8:
            opp_conc_txt = f", face à une défense qui concède {opp_conceded_pm:.1f} buts/match"
        elif opp_conceded_pm >= 1.3:
            opp_conc_txt = f", défense adverse poreuse ({opp_conceded_pm:.1f} buts concédés/match)"

        # ── Buteur anytime ──────────────────────────────────────────
        if gpm >= 0.15:
            # Calibration Poisson : P(marque) = 1 - e^(-lambda)
            lam_base  = (xgpm + gpm) / 2 if xgpm > 0 else gpm
            p_base    = poisson_at_least(lam_base, 1)

            # Tags efficacité
            if xg_conv_ratio > 1.2:
                eff_tag = f" · finisseur efficace ({round(shot_eff*100)}% de conv.)"
            elif xg_conv_ratio < 0.7 and goals > 3:
                eff_tag = f" · sous-performe son xG (attention)"
            elif shot_eff >= 0.18:
                eff_tag = f" · bonne efficacité ({round(shot_eff*100)}% conv.)"
            else:
                eff_tag = ""

            # Ajustement contextuel — plafond réaliste 68%
            ctx_mult = 1 + ctx_bonus * 0.30
            ctx_mult *= {"F":1.15,"M":1.0,"D":0.7}.get(pos, 0.9)
            ctx_mult *= sub_penalty
            # Contexte sur lambda : faiblesse défense + buts concédés adversaire
            # Plus la défense est mauvaise, plus le lambda augmente
            adj = 1 + ctx_bonus * 0.50 + (opp_conceded_pm / 10 if opp_conceded_pm else 0)
            lam_ctx  = lam_base * adj
            lam_ctx *= {"F":1.15,"M":1.0,"D":0.7}.get(pos, 0.9)
            lam_ctx *= sub_penalty
            ctx_conf = round(poisson_at_least(lam_ctx, 1))

            # Reasoning explicatif
            reasoning = (
                f"{goals} buts en {apps} matchs cette saison ({round(gpm*100,1)}% de chance/match)"
                f"{eff_tag} · xG moyen {xgpm:.2f}/match"
                f"{opp_conc_txt} · {def_label}{sub_tag}"
            )

            if ctx_conf >= 35:
                picks.append({
                    "player": name, "position": pos, "is_sub": is_sub,
                    "type": "Buteur", "label": f"{name} marque",
                    "cote": None, "confidence": ctx_conf,
                    "reasoning": reasoning,
                    "context": {"weakness": weakness},
                    "stats": {"goals": goals, "apps": apps, "gpm": gpm, "xgpm": xgpm}
                })

        # ── Passeur décisif ─────────────────────────────────────────
        if apm >= 0.15:
            lam_ass  = (xapm + apm) / 2 if xapm > 0 else apm
            p_assist = poisson_at_least(lam_ass, 1)
            lam_ass_ctx = lam_ass * (1 + weakness * 0.15) * sub_penalty
            ctx_conf    = round(poisson_at_least(lam_ass_ctx, 1))
            if ctx_conf >= 30:
                picks.append({
                    "player": name, "position": pos, "is_sub": is_sub,
                    "type": "Passeur décisif", "label": f"{name} fait une passe décisive",
                    "cote": None, "confidence": ctx_conf,
                    "reasoning": (f"{assists} passes décisives en {apps} matchs ({round(apm*100,1)}% de chance/match)"
                                  f" · xA moyen {xapm:.2f}/match · {def_label}{sub_tag}"),
                    "context": {"weakness": weakness},
                    "stats": {"assists": assists, "apps": apps, "apm": apm, "xapm": xapm}
                })

        # ── Joueur décisif ──────────────────────────────────────────
        if gapm >= 0.28 and (goals + assists) >= 4:
            # P(but OU passe) = 1 - P(pas de but ET pas de passe)
            lam_g     = (xgpm + gpm) / 2 if xgpm > 0 else gpm
            lam_a     = (xapm + apm) / 2 if xapm > 0 else apm
            p_neither = math.exp(-lam_g) * math.exp(-lam_a)
            p_dec     = round((1 - p_neither) * 100, 1)
            lam_g_ctx = lam_g * (1 + ctx_bonus * 0.25) * sub_penalty
            lam_a_ctx = lam_a * (1 + ctx_bonus * 0.25) * sub_penalty
            p_neither_ctx = math.exp(-lam_g_ctx) * math.exp(-lam_a_ctx)
            ctx_conf      = round((1 - p_neither_ctx) * 100)
            # Garantie mathématique : décisif >= buteur (superset)
            # Récupère le conf buteur depuis picks déjà calculés si dispo
            _buteur_picks = [pk for pk in picks if pk.get("player")==name and pk.get("type")=="Buteur"]
            if _buteur_picks:
                ctx_conf = max(ctx_conf, _buteur_picks[0]["confidence"] + 1)
            if ctx_conf >= 40:
                picks.append({
                    "player": name, "position": pos, "is_sub": is_sub,
                    "type": "Joueur décisif", "label": f"{name} but ou passe décisive",
                    "cote": None, "confidence": ctx_conf,
                    "reasoning": (f"{goals} buts + {assists} passes décisives en {apps} matchs"
                                  f" ({round(gapm*100,1)}% de chance/match) · xG+xA: {round(xgpm+xapm,2)}/match"
                                  f" · {def_label}{sub_tag}"),
                    "context": {"weakness": weakness},
                    "stats": {"goals": goals, "assists": assists, "apps": apps}
                })

    # Trier par confiance
    picks.sort(key=lambda x: x["confidence"], reverse=True)

    # Déduplication : 1 seul pick par joueur (le meilleur type)
    final, seen = [], set()
    for pk in picks:
        key = f"{pk['player']}_{pk['type']}"
        if key not in seen:
            seen.add(key)
            final.append(pk)

    return final

# ─── Analyse équipe ──────────────────────────────────────────────────────────

def analyze_match(match, pstats_all):
    form = match.get("pre_match_form") or {}
    h2h  = match.get("h2h") or {}
    odds = match.get("match_odds") or {}
    home = match["home"]
    away = match["away"]
    mid  = str(match["id"])

    hf = get_form(form,"homeTeam"); af = get_form(form,"awayTeam")
    hp = get_pos(form,"homeTeam");  ap = get_pos(form,"awayTeam")
    hr = get_rat(form,"homeTeam");  ar = get_rat(form,"awayTeam")
    hw, dn, aw = parse_h2h(h2h); h2ht = hw+dn+aw

    # Forme récente (score 0-100 basé sur les 5 derniers)
    h_form_score = recent_form_score(hf)
    a_form_score = recent_form_score(af)
    h_trend = form_trend(hf)
    a_trend = form_trend(af)

    # Stats récentes et saison (disponibles si scraper_players_today a tourné)
    pstats       = pstats_all.get(mid, {})
    home_ts_data = pstats.get("home_team_stats", {})
    away_ts_data = pstats.get("away_team_stats", {})
    home_rec     = pstats.get("home_recent", {})
    away_rec     = pstats.get("away_recent", {})
    h2h_shots_d  = pstats.get("h2h_shots", {})

    h_goals = goals_analysis(home_rec, home_ts_data, home)
    a_goals = goals_analysis(away_rec, away_ts_data, away)

    # ── Contexte domicile/extérieur ──────────────────────────────────────────
    league_id   = match.get("league_id", 0) or 0
    is_cup      = is_cup_league(league_id)
    h_ha_ctx    = home_away_context(home_rec, is_home=True)   # stats dom de l'équipe dom
    a_ha_ctx    = home_away_context(away_rec, is_home=False)  # stats ext de l'équipe ext
    h_cup_ctx   = cup_context(home_rec, is_cup)
    a_cup_ctx   = cup_context(away_rec, is_cup)
    ko_bonus_h  = knockout_pressure_bonus(league_id, hf) if hf else 0
    ko_bonus_a  = knockout_pressure_bonus(league_id, af) if af else 0

    # Buts attendus avec contexte home/away (plus précis que la moyenne globale)
    h_gf_ctx = h_cup_ctx.get("gf") or h_ha_ctx.get("gf") or h_goals.get("gf", 0)
    a_gf_ctx = a_cup_ctx.get("gf") or a_ha_ctx.get("gf") or a_goals.get("gf", 0)
    h_ga_ctx = h_cup_ctx.get("ga") or h_ha_ctx.get("ga") or h_goals.get("ga", 0)
    a_ga_ctx = a_cup_ctx.get("ga") or a_ha_ctx.get("ga") or a_goals.get("ga", 0)
    h_gf_src = h_cup_ctx.get("n","") and "LDC" or h_ha_ctx.get("src","L10")
    a_gf_src = a_cup_ctx.get("n","") and "LDC" or a_ha_ctx.get("src","L10")

    ft  = get_mkt(odds,"Full time")
    dc  = get_mkt(odds,"Double chance")
    btt = get_mkt(odds,"Both teams to score")
    fts = get_mkt(odds,"First team to score")
    crn = get_mkt(odds,"Corners 2-Way","9.5")
    crd = get_mkt(odds,"Cards in match","3.5")
    dnb = get_mkt(odds,"Draw no bet")

    c1=get_odds(ft,"1"); c2=get_odds(ft,"2"); cx=get_odds(ft,"X")
    c1x=get_odds(dc,"1X"); cx2=get_odds(dc,"X2")
    cy=get_odds(btt,"Yes"); cn=get_odds(btt,"No")
    btts_p = prob(cy)

    candidates = []

    def add(direction, type_, label, cote, conf, reasoning, form_data=None, is_fun=False):
        candidates.append({
            "direction": direction, "type": type_, "label": label,
            "cote": cote, "confidence": conf, "reasoning": reasoning,
            "stats": {"form": form_data or []},
            "is_fun": is_fun
        })

    # ── 1X2 — forme récente pondérée davantage que H2H ────────────────────
    # Poids : forme récente 55%, classement 20%, H2H 15%, rating 10%
    if hf:
        form_c = h_form_score * 0.55
        pos_c  = max(0, (20-hp)/20*20) if hp < 20 else 0
        h2h_c  = (hw/h2ht*15) if h2ht else 7
        rat_c  = min(10, (hr-6.5)*15) if hr > 6.5 else 0
        conf   = round(min(94, form_c + pos_c + h2h_c + rat_c))
        if conf >= 58:
            trend_txt = f" ({h_trend})" if h_trend != "stable" else ""
            add("home_win","Victoire domicile",f"{home} gagne",c1,conf,
                f"{home} : {form_summary(hf)}{trend_txt} · #{hp} au classement · "
                f"H2H: {hw}V/{dn}N/{aw}D · Note Sofa: {hr:.1f}/10",hf)

    if af:
        form_c = a_form_score * 0.55
        pos_c  = max(0, (20-ap)/20*20) if ap < 20 else 0
        h2h_c  = (aw/h2ht*15) if h2ht else 7
        rat_c  = min(10, (ar-6.5)*15) if ar > 6.5 else 0
        conf   = round(min(94, form_c + pos_c + h2h_c + rat_c))
        if conf >= 58:
            trend_txt = f" ({a_trend})" if a_trend != "stable" else ""
            add("away_win","Victoire extérieur",f"{away} gagne",c2,conf,
                f"{away} : {form_summary(af)}{trend_txt} · #{ap} au classement · "
                f"H2H: {aw}V/{dn}N/{hw}D · Note Sofa: {ar:.1f}/10",af)

    # ── Favori net ─────────────────────────────────────────────────────────
    if hf and af:
        pd = ap - hp
        if abs(pd) >= 6:
            fav    = home if pd > 0 else away
            fav_f  = hf if pd > 0 else af
            fav_pos= hp if pd > 0 else ap
            fav_rat= hr if pd > 0 else ar
            und_pos= ap if pd > 0 else hp
            cote_f = c1 if pd > 0 else c2
            dir_   = "home_win" if pd > 0 else "away_win"
            fav_trend = h_trend if pd > 0 else a_trend

            conf = round(min(90, 50 + abs(pd)*1.4 + max(0,fav_rat-6.5)*6))
            if conf >= 65:
                trend_txt = f", {fav_trend}" if fav_trend != "stable" else ""
                ex = next((c for c in candidates if c["direction"]==dir_), None)
                if not ex or ex["confidence"] < conf:
                    if ex: candidates.remove(ex)
                    add(dir_,"Favori net",f"{fav} gagne",cote_f,conf,
                        f"{abs(pd)} places d'écart au classement (#{fav_pos} vs #{und_pos})"
                        f"{trend_txt} · {form_summary(fav_f)} · Note Sofa: {fav_rat:.1f}/10",fav_f)

    # ── Double chance — une seule (la meilleure) ────────────────────────────
    dc_cands = []
    if hf:
        ub   = unbeaten(hf)
        h2h_ = (hw+dn)/h2ht if h2ht else 0.5
        # Forme récente pondérée davantage
        conf = round(ub * 65 + h_form_score * 0.2 + h2h_ * 15)
        if conf >= 70:
            trend_txt = f", {h_trend}" if h_trend not in ("stable","en légère baisse") else ""
            dc_cands.append(("home_dc","Double chance",f"{home} ou Nul (1X)",c1x,conf,
                f"{home} invaincu {round(ub*100)}% sur {len(hf)} derniers matchs{trend_txt} · "
                f"H2H: {hw+dn}/{h2ht} matchs sans défaite",hf))
    if af:
        ub   = unbeaten(af)
        h2h_ = (aw+dn)/h2ht if h2ht else 0.5
        conf = round(ub * 65 + a_form_score * 0.2 + h2h_ * 15)
        if conf >= 70:
            trend_txt = f", {a_trend}" if a_trend not in ("stable","en légère baisse") else ""
            dc_cands.append(("away_dc","Double chance",f"Nul ou {away} (X2)",cx2,conf,
                f"{away} invaincu {round(ub*100)}% sur {len(af)} derniers matchs{trend_txt} · "
                f"H2H: {aw+dn}/{h2ht} matchs sans défaite",af))
    if dc_cands:
        add(*max(dc_cands, key=lambda x: x[4]))

    # ── BTTS — reasoning explicatif ─────────────────────────────────────────
    if btt:
        py, pn = prob(cy), prob(cn)

        import math as _math

        # Probabilité de scorer via Poisson + fréquence BTTS observée
        h_gf  = h_gf_ctx or h_goals.get("gf", 0)
        a_gf  = a_gf_ctx or a_goals.get("gf", 0)
        h_score_prob = round((1 - _math.exp(-h_gf)) * 100) if h_gf > 0 else None
        a_score_prob = round((1 - _math.exp(-a_gf)) * 100) if a_gf > 0 else None

        h_btts_c = home_rec.get("btts_count", None)
        h_btts_n = home_rec.get("btts_n", 0)
        a_btts_c = away_rec.get("btts_count", None)
        a_btts_n = away_rec.get("btts_n", 0)

        src_h = h_goals.get("src","saison")
        src_a = a_goals.get("src","saison")

        def btts_block(team_name, gf, ga, btts_c, btts_n, score_prob, src):
            """Génère le bloc d'analyse BTTS pour une équipe."""
            parts = []
            if gf:
                parts.append(f"{team_name}: {gf:.1f} buts marqués/{src}, {ga:.1f} concédés/{src}")
            if score_prob:
                parts.append(f"marque dans ~{score_prob}% des matchs")
            if btts_c is not None and btts_n > 0:
                rate = round(btts_c/btts_n*100)
                parts.append(f"BTTS {btts_c}/{btts_n} derniers matchs ({rate}%)")
            return " · ".join(parts)

        h_block = btts_block(home, h_gf, h_goals.get("ga",0), h_btts_c, h_btts_n, h_score_prob, src_h)
        a_block = btts_block(away, a_gf, a_goals.get("ga",0), a_btts_c, a_btts_n, a_score_prob, src_a)

        if py >= 55:
            reasoning_btts = " | ".join(filter(None, [h_block, a_block]))
            if not reasoning_btts:
                reasoning_btts = "Deux équipes offensives, BTTS probable"
            add("btts_yes","BTTS","Les deux équipes marquent",cy,min(90,round(py+3)),reasoning_btts)

        elif pn >= 60:
            # Identifie l'équipe la moins offensive
            weaker     = home if (h_gf or 0) < (a_gf or 0) else away
            weaker_blk = h_block if weaker == home else a_block
            stronger_blk = a_block if weaker == home else h_block
            reasoning_btts = (f"Risque que {weaker} ne marque pas | {weaker_blk} | {stronger_blk}")
            if not weaker_blk:
                reasoning_btts = f"L'une des deux équipes (#{hp} vs #{ap}) risque de ne pas marquer"
            add("btts_no","BTTS Non","Au moins une équipe ne marque pas",cn,min(88,round(pn+2)),reasoning_btts)

    # ── Over/Under buts — reasoning explicatif ─────────────────────────────
    for thr in ["1.5","2.5","3.5"]:
        mkt = get_mkt(odds,"Match goals",thr)
        if not mkt: continue
        co=get_odds(mkt,"Over"); cu=get_odds(mkt,"Under")
        po,pu=prob(co),prob(cu)
        min_p={"1.5":75,"2.5":60,"3.5":68}.get(thr,60)

        # ── Estimation buts via données réelles + Poisson ────────────────────────
        h_gf_s  = h_goals.get("s_gf", 0)
        a_gf_s  = a_goals.get("s_gf", 0)
        h_ga_s  = h_goals.get("s_ga", 0)
        a_ga_s  = a_goals.get("s_ga", 0)
        # Priorité : contexte home/away/cup > L10 > saison
        h_gf_r  = h_gf_ctx or home_rec.get("goals_for_pm", 0)
        a_gf_r  = a_gf_ctx or away_rec.get("goals_for_pm", 0)
        h_ga_r  = h_ga_ctx or home_rec.get("goals_ag_pm", 0)
        a_ga_r  = a_ga_ctx or away_rec.get("goals_ag_pm", 0)
        h_btts  = home_rec.get("btts_rate", 0) / 100 if home_rec.get("btts_n", 0) >= 5 else None
        a_btts  = away_rec.get("btts_rate", 0) / 100 if away_rec.get("btts_n", 0) >= 5 else None
        h_tot_r = home_rec.get("total_goals_pm", 0)
        a_tot_r = away_rec.get("total_goals_pm", 0)
        src_hr  = "L10" if h_gf_r else "saison"
        src_ar  = "L10" if a_gf_r else "saison"

        h_att = h_gf_r or h_gf_s
        a_def = a_ga_r or a_ga_s
        a_att = a_gf_r or a_gf_s
        h_def = h_ga_r or h_ga_s
        # Buts attendus : moyenne attaque/défense croisée
        h_exp = round((h_att + a_def) / 2, 2) if (h_att and a_def) else (h_att or 0)
        a_exp = round((a_att + h_def) / 2, 2) if (a_att and h_def) else (a_att or 0)
        total_exp = round(h_exp + a_exp, 1) if (h_exp or a_exp) else None

        # Probabilité Poisson depuis nos données (priorité sur cote bookmaker)
        thr_float = float(thr)
        k = int(thr_float) + 1  # nombre de buts nécessaires
        if total_exp and total_exp > 0:
            p_data = poisson_at_least(total_exp, k)
        else:
            p_data = None

        # Confiance = moyenne pondérée : données réelles 60% + bookmaker 40%
        if p_data is not None:
            conf_over  = round(p_data * 0.60 + prob(co) * 0.40)
            conf_under = round((100 - p_data) * 0.60 + prob(cu) * 0.40)
        else:
            conf_over  = round(prob(co))
            conf_under = round(prob(cu))

        # BTTS comme signal supplémentaire pour Over 2.5
        if thr == "2.5" and h_btts is not None and a_btts is not None:
            btts_signal = (h_btts + a_btts) / 2
            if btts_signal > 0.65:
                conf_over  = round(min(conf_over + 5, 92))
            elif btts_signal < 0.40:
                conf_under = round(min(conf_under + 5, 88))

        if conf_over >= 55 and co:
            if total_exp:
                cup_note = f" · contexte LDC ({h_gf_src}/{a_gf_src})" if is_cup else ""
                r = (
                    f"{home}: {h_att:.1f} buts/{src_hr} · défense {away} concède {a_def:.1f}/{src_ar} → ~{h_exp:.1f} attendus · "
                    f"{away}: {a_att:.1f} buts/{src_ar} · défense {home} concède {h_def:.1f}/{src_hr} → ~{a_exp:.1f} attendus · "
                    f"Total: ~{total_exp} buts (P={p_data}%{cup_note})"
                )
            else:
                r = f"Deux équipes offensives, match orienté vers les buts"
            add(f"over{thr.replace('.','')}", f"Over {thr} buts", f"Plus de {thr} buts", co, conf_over, r)

        elif conf_under >= 60 and cu and thr in ["2.5","3.5"]:
            if total_exp:
                r = (
                    f"Défenses solides — {home} concède {h_def:.1f}/{src_hr}, {away} concède {a_def:.1f}/{src_ar} · "
                    f"Total attendu: ~{total_exp} buts (P Under={100-p_data if p_data else '?'}%)"
                )
            else:
                r = f"{home} (#{hp}) et {away} (#{ap}) — défenses solides, match fermé attendu"
            add(f"under{thr.replace('.','')}", f"Under {thr} buts", f"Moins de {thr} buts", cu, conf_under, r)

    # ── Première équipe à marquer ───────────────────────────────────────────
    if fts and hf:
        c_home = get_odds(fts, home); c_away = get_odds(fts, away)
        ph = prob(c_home); pa = prob(c_away)

        # Stats offensives pour FTS
        h_off = h_gf_ctx or h_goals.get("gf", 0)
        a_off = a_gf_ctx or a_goals.get("gf", 0)
        h_def = h_ga_ctx or h_goals.get("ga", 0)
        a_def = a_ga_ctx or a_goals.get("ga", 0)

        if ph >= 52 and ph > pa:
            r = (
                f"{home} marque {h_off:.1f} buts/match ({h_gf_src}) · "
                f"défense {away} concède {a_def:.1f}/match · "
                f"cote bookmaker → {round(ph)}% de probabilité"
            )
            add("fts_home","1ère équipe à marquer",f"{home} marque en premier",c_home,
                min(80,round(ph+2)),r)
        elif pa >= 52 and pa > ph:
            r = (
                f"{away} marque {a_off:.1f} buts/match ({a_gf_src}) · "
                f"défense {home} concède {h_def:.1f}/match · "
                f"cote bookmaker → {round(pa)}% de probabilité"
            )
            add("fts_away","1ère équipe à marquer",f"{away} marque en premier",c_away,
                min(80,round(pa+2)),r)

    # ── Corners ────────────────────────────────────────────────────────────
    if crn:
        co=get_odds(crn,"Over"); cu=get_odds(crn,"Under")
        po,pu=prob(co),prob(cu)
        if po >= 60:
            add("corners_over","Corners","Plus de 9.5 corners",co,min(83,round(po+3)),
                f"{home} et {away} deux équipes actives sur les phases offensives, "
                f"volume de corners élevé attendu")
        elif pu >= 63:
            add("corners_under","Corners","Moins de 9.5 corners",cu,min(83,round(pu+2)),
                f"Match attendu fermé et peu de transitions offensives — peu de corners")

    # ── Cartons ────────────────────────────────────────────────────────────
    if crd:
        co=get_odds(crd,"Over"); cu=get_odds(crd,"Under")
        po,pu=prob(co),prob(cu)
        if po >= 60:
            add("cards_over","Cartons","Plus de 3.5 cartons",co,min(81,round(po+2)),
                f"Rencontre à forts enjeux ou rivalité historique — match tendu, arbitrage attendu strict")
        elif pu >= 63:
            add("cards_under","Cartons","Moins de 3.5 cartons",cu,min(81,round(pu+1)),
                f"Match propre attendu — deux équipes disciplinées sans historique d'incidents")

    # ── Déduplication picks équipe ──────────────────────────────────────────
    seen = {}
    for pk in sorted(candidates, key=lambda x: x["confidence"], reverse=True):
        k = pk.get("direction", pk["label"])
        if k not in seen: seen[k] = pk
    raw_picks = list(seen.values())

    # ── Cohérence Over/Under buts : évite les contradictions ─────────────────
    directions = {p.get("direction","") for p in raw_picks}
    filtered = []
    for pk in raw_picks:
        d = pk.get("direction","")
        # Over 1.5 incompatible avec Under 2.5 ou Under 3.5
        if d == "over15" and ("under25" in directions or "under35" in directions):
            continue
        # Over 1.5 redondant si Over 2.5 existe
        if d == "over15" and "over25" in directions:
            continue
        # Over 2.5 incompatible avec Under 3.5 (quasi-contradiction)
        if d == "over25" and "under35" in directions:
            o = next((p for p in raw_picks if p.get("direction")=="over25"), None)
            u = next((p for p in raw_picks if p.get("direction")=="under35"), None)
            if o and u and u["confidence"] > o["confidence"]:
                continue  # garde under35, supprime over25
        # Under 3.5 redondant si Under 2.5 plus restrictif existe
        if d == "under35" and "under25" in directions:
            continue
        filtered.append(pk)

    team_picks = sorted(filtered, key=lambda x: x["confidence"], reverse=True)[:5]

    # ── Paris fun (cote >= 2.0, analyse sérieuse) ──────────────────────────
    fun_picks = []

    # Victoire extérieure surprise (outsider en forme)
    if af and c2 and c2 >= 2.0:
        a_recent = recent_form_score(af)
        a_trend_  = form_trend(af)
        if a_recent >= 60 and a_trend_ in ("en hausse","en grande forme"):
            if win_rate(af[-5:]) >= 0.4:
                conf = round(min(72, a_recent * 0.6 + (aw/h2ht*20 if h2ht else 10)))
                if conf >= 45:
                    fun_picks.append({
                        "direction": "fun_away_win", "type": "🎲 Paris fun",
                        "label": f"{away} gagne à l'extérieur",
                        "cote": c2, "confidence": conf, "is_fun": True,
                        "stats": {"form": af},
                        "reasoning": (f"{away} est {a_trend_} ({form_summary(af[-5:])}) "
                                      f"et peut créer la surprise à {round(c2,2)} — "
                                      f"outsider mais en confiance")
                    })

    # Nul entre deux équipes proches
    if hf and af and cx and cx >= 2.0:
        pd = abs(ap - hp)
        if pd <= 4 and abs(h_form_score - a_form_score) <= 15:
            conf = round(min(65, 40 + int(dn or 0)/int(h2ht or 1)*25 if h2ht else 45))
            if conf >= 40:
                fun_picks.append({
                    "direction": "fun_draw", "type": "🎲 Paris fun",
                    "label": "Match nul",
                    "cote": cx, "confidence": conf, "is_fun": True,
                    "stats": {"form": hf},
                    "reasoning": (f"Deux équipes au niveau similaire (#{hp} vs #{ap}), "
                                  f"forme équilibrée des deux côtés — nul logique à {round(cx,2)}")
                })

    # Over 3.5 buts si deux équipes très offensives
    o35_mkt = get_mkt(odds,"Match goals","3.5")
    if o35_mkt:
        co35 = get_odds(o35_mkt,"Over")
        if co35 and co35 >= 2.0:
            both_off = (loss_rate(hf) < 0.3 if hf else False) and (loss_rate(af) < 0.3 if af else False)
            if both_off and btts_p >= 60:
                fun_picks.append({
                    "direction": "fun_over35", "type": "🎲 Paris fun",
                    "label": "Plus de 3.5 buts",
                    "cote": co35, "confidence": 52, "is_fun": True,
                    "stats": {},
                    "reasoning": (f"Deux attaques prolifiques ({home} et {away} marquent régulièrement), "
                                  f"BTTS probable — match à buts à {round(co35,2)}")
                })

    # Trier les fun picks par confiance et garder max 2
    fun_picks.sort(key=lambda x: x["confidence"], reverse=True)
    fun_picks = fun_picks[:2]

    # ── Props joueurs ───────────────────────────────────────────────────────
    home_players = pstats.get("home", [])
    away_players = pstats.get("away", [])
    home_recent  = home_rec
    away_recent  = away_rec

    # Filtre joueurs absents/blesses (depuis lineup.unavailable)
    lineup = pstats.get("lineup") or {}
    def _names_unavailable(side):
        t = lineup.get(side) or {}
        out_absent = set()
        out_doubt  = set()
        for u in (t.get("unavailable") or []):
            name = (u.get("name") or "").strip().lower()
            if not name: continue
            ret = (u.get("return") or "").lower()
            # Doubtful = incertain mais peut jouer
            if "doubt" in ret:
                out_doubt.add(name)
            else:
                out_absent.add(name)
        return out_absent, out_doubt

    h_absent, h_doubt = _names_unavailable("home")
    a_absent, a_doubt = _names_unavailable("away")

    def _is_absent(player, side_absent):
        n = (player.get("name") or player.get("shortName") or "").strip().lower()
        if not n: return False
        if n in side_absent: return True
        # Match partiel (au cas ou les noms different legerement)
        for ab in side_absent:
            if ab and ab in n: return True
            if n and n in ab: return True
        return False

    # Annote les doubtful (penalite confiance) et exclut les absents
    home_players_filt = []
    for p in home_players:
        if _is_absent(p, h_absent):
            continue
        if _is_absent(p, h_doubt):
            p = dict(p); p["_doubtful"] = True
        home_players_filt.append(p)
    away_players_filt = []
    for p in away_players:
        if _is_absent(p, a_absent):
            continue
        if _is_absent(p, a_doubt):
            p = dict(p); p["_doubtful"] = True
        away_players_filt.append(p)

    home_conceded = away_ts_data.get("conceded_pm", 0) if away_ts_data else 0
    away_conceded = home_ts_data.get("conceded_pm", 0) if home_ts_data else 0

    home_pp_raw = player_picks_contextual(home_players_filt, ap, ar, home_conceded, btts_p)
    away_pp_raw = player_picks_contextual(away_players_filt, hp, hr, away_conceded, btts_p)

    # Marquer les picks de joueurs doubtful (confidence x0.85)
    for pk in home_pp_raw + away_pp_raw:
        pname = (pk.get("player") or "").strip().lower()
        if pname in h_doubt or pname in a_doubt:
            pk["confidence"] = round(pk["confidence"] * 0.85)
            pk["reasoning"] = f"⚠️ {pname.title()} INCERTAIN · " + pk.get("reasoning", "")

    # ── Max 3 props joueurs PAR MATCH (pas par équipe) ──────────────────────
    # Mélange les deux listes, trie par confiance, garde les 3 meilleurs
    all_pp = []
    for pk in home_pp_raw: pk["team"] = "home"; all_pp.append(pk)
    for pk in away_pp_raw: pk["team"] = "away"; all_pp.append(pk)
    all_pp.sort(key=lambda x: x["confidence"], reverse=True)

    # Assure diversité : max 2 du même joueur, max 2 de la même équipe
    final_pp, seen_player_types, team_count = [], set(), {"home":0,"away":0}
    for pk in all_pp:
        pkey = f"{pk['player']}_{pk['type']}"
        team = pk.get("team","home")
        if pkey in seen_player_types: continue
        if team_count[team] >= 2: continue
        seen_player_types.add(pkey)
        team_count[team] += 1
        final_pp.append(pk)
        if len(final_pp) >= 3: break

    home_pp = [p for p in final_pp if p.get("team") == "home"]
    away_pp = [p for p in final_pp if p.get("team") == "away"]

    # ── Garantie min 1 prop joueur par match ──────────────────────────────────
    if not final_pp:
        # Prend le meilleur attaquant/milieu sans seuil
        candidates_fallback = []
        for side_key, plist in [("home", home_players), ("away", away_players)]:
            for p in plist:
                if p.get("position") in ("F","M") and p.get("appearances",0) >= 3:
                    candidates_fallback.append((side_key, p))
        candidates_fallback.sort(
            key=lambda x: x[1].get("xG_pm",0)*0.6 + x[1].get("goals_pm",0)*0.4,
            reverse=True
        )
        if candidates_fallback:
            side_key, bp = candidates_fallback[0]
            name   = bp.get("shortName", bp.get("name",""))
            gpm    = bp.get("goals_pm", 0)
            xgpm   = bp.get("xG_pm", 0)
            apps   = bp.get("appearances", 0)
            goals  = bp.get("goals", 0)
            pos    = bp.get("position","")
            is_sub = bp.get("is_sub", False)
            opp_p  = ap if side_key=="home" else hp
            opp_r  = ar if side_key=="home" else hr
            opp_c  = away_conceded if side_key=="home" else home_conceded
            weak   = defense_weakness(opp_p, opp_r, opp_c)
            lam    = (xgpm+gpm)/2 if xgpm else gpm
            lam_c  = lam * (1+weak*0.5) * {"F":1.15,"M":1.0}.get(pos,0.9)
            conf   = max(20, round(poisson_at_least(lam_c, 1)))
            fb_pk  = {
                "player": name, "position": pos, "is_sub": is_sub,
                "type": "Buteur", "label": f"{name} marque",
                "team": side_key, "cote": None, "confidence": conf,
                "reasoning": f"{goals}G en {apps} matchs ({round(gpm*100,1)}%/match) · xG: {xgpm:.2f}/match",
                "context": {"weakness": weak}, "stats": {"goals": goals, "apps": apps}
            }
            if side_key == "home":
                home_pp = [fb_pk]
            else:
                away_pp = [fb_pk]

    # ── Tirs équipe ─────────────────────────────────────────────────────────
    shots_props = team_shots_props(home_ts_data, away_ts_data, home_rec, away_rec,
                                   h2h_shots_d, home, away, hp, ap, match_odds=odds,
                                   home_form=hf, away_form=af)
    team_picks.extend(shots_props)

    return team_picks, home_pp, away_pp, fun_picks

# ─── Tirs équipe ─────────────────────────────────────────────────────────────

def _weighted_avg(l5, l10, season, w5=0.50, w10=0.30, ws=0.20):
    """Moyenne ponderee L5/L10/Saison. Skip valeurs None."""
    vals = []
    if l5     is not None: vals.append((float(l5),     w5))
    if l10    is not None: vals.append((float(l10),    w10))
    if season is not None: vals.append((float(season), ws))
    if not vals: return None
    total_w = sum(w for _, w in vals)
    return sum(v * w for v, w in vals) / total_w


def _poisson_over(lam, k):
    """P(X > k) pour X ~ Poisson(lam). k peut etre fractionnaire (line 20.5)."""
    if lam is None or lam <= 0: return 0
    import math
    threshold = int(k)
    # P(X >= threshold + 1) = 1 - P(X <= threshold)
    cum = sum(math.exp(-lam) * lam**i / math.factorial(i) for i in range(threshold + 1))
    return round((1 - cum) * 100, 1)


def _generate_lines(expected, n_lines=3, spread=3.0):
    """Legacy: lignes centrees autour de expected. Pour compat tirs cadres internes."""
    if not expected or expected <= 0: return []
    def to_half(v): return round(v * 2) / 2 - (0 if (round(v * 2) % 2 == 1) else 0.5)
    if n_lines == 3:
        return sorted({to_half(expected - spread), to_half(expected), to_half(expected + spread)})
    return sorted({to_half(expected - spread + i * (2 * spread / (n_lines - 1))) for i in range(n_lines)})


# Lignes bookmaker reelles (basees sur observations Betclic/Bet365/Unibet)
BOOKMAKER_LINES = {
    "total_shots":  [19.5, 21.5, 22.5, 23.5, 24.5, 25.5, 26.5, 27.5, 28.5, 30.5],
    "home_shots":   [7.5, 8.5, 9.5, 10.5, 11.5, 12.5, 13.5, 14.5, 15.5, 16.5],
    "away_shots":   [6.5, 7.5, 8.5, 9.5, 10.5, 11.5, 12.5, 13.5, 14.5],
    "total_sot":    [5.5, 6.5, 7.5, 8.5, 9.5, 10.5, 11.5],
    "home_sot":     [1.5, 2.5, 3.5, 4.5, 5.5, 6.5],
    "away_sot":     [1.5, 2.5, 3.5, 4.5, 5.5],
}


def _fair_cote(probability):
    """
    Cote fair / break-even / seuil-valeur:
    - Si tu joues au-dessus = +EV
    - Si tu joues en-dessous = -EV (bookmaker te gruge)
    """
    if not probability or probability <= 0 or probability >= 100:
        return None
    return round(1 / (probability / 100), 2)


def _value_tier(cote_min):
    """
    Note la qualite d'une opportunite selon le cote_min (= cote requise pour +EV):
    - Plus la cote_min est haute, plus c'est facile a trouver chez un bookmaker
    """
    if not cote_min: return None
    if cote_min >= 1.45: return ("🎯", "Belle value", "#22c55e")    # green - facile a beat
    if cote_min >= 1.30: return ("💎", "Value correcte", "#84cc16") # lime - jouable
    if cote_min >= 1.22: return ("⚠️", "Value serrée", "#f59e0b")  # amber - bookmaker souvent en dessous
    return ("🚫", "Quasi-impossible", "#94a3b8")  # gray - presque toujours -EV


# Sweet spot: conf 60-80% donne cote_min 1.25-1.67
# - >80% conf donne cote_min <1.25 = quasi-impossible a beat (bookmaker offre toujours moins)
# - <60% pas fiable
SWEET_SPOT_CONF = (60, 80)
MIN_CONF        = 60
MIN_COTE_MIN    = 1.20    # filtre: skip si cote_min < 1.20 (vraiment trop dur)


def _pick_line(expected, lines, line_label_prefix, kind, min_conf=MIN_CONF, sweet_spot=SWEET_SPOT_CONF):
    """
    Cherche LA ligne donnant une cote_min raisonnable (1.30+).
    Privilegie zones 60-75% confidence = cote_min 1.33-1.67 = vraies opportunites de value.
    """
    if not expected or expected <= 0 or not lines: return None
    sweet_lo, sweet_hi = sweet_spot
    sweet_candidates = []
    fallback = None
    fallback_dist = 0

    def _make_pick(line, direction, conf):
        cote_min = _fair_cote(conf)
        if cote_min is None or cote_min < MIN_COTE_MIN:
            return None  # filtre: trop pres certitude, no value
        label = (f"Plus de {line} {line_label_prefix}" if direction == "over"
                 else f"Moins de {line} {line_label_prefix}")
        return {
            "line": line, "direction": direction,
            "label": label,
            "confidence": round(conf), "kind": kind,
            "cote_min": cote_min,
            "value": _value_tier(cote_min),
        }

    for line in lines:
        p_over = _poisson_over(expected, line)
        # OVER
        if sweet_lo <= p_over <= sweet_hi:
            pick = _make_pick(line, "over", p_over)
            if pick: sweet_candidates.append(pick)
        elif p_over >= min_conf:
            pick = _make_pick(line, "over", p_over)
            if pick:
                dist = p_over - 50
                if dist > fallback_dist:
                    fallback = pick
                    fallback_dist = dist
        # UNDER
        p_under = 100 - p_over
        if sweet_lo <= p_under <= sweet_hi:
            pick = _make_pick(line, "under", p_under)
            if pick: sweet_candidates.append(pick)
        elif p_under >= min_conf:
            pick = _make_pick(line, "under", p_under)
            if pick:
                dist = p_under - 50
                if dist > fallback_dist:
                    fallback = pick
                    fallback_dist = dist

    if sweet_candidates:
        # Choix: la conf la plus haute DANS la sweet spot (meilleur edge potentiel)
        return max(sweet_candidates, key=lambda x: x["confidence"])
    return fallback


def _extract_bookmaker_shot_lines(match_odds):
    """
    Extrait les lignes bookmaker reelles pour tirs/tirs cadres depuis match_odds.
    """
    out = {
        "total_shots": [], "total_sot": [],
        "home_shots":  [], "away_shots": [],
    }
    if not match_odds:
        return out
    markets = match_odds.get("markets", [])
    for mk in markets:
        name = mk.get("marketName", "")
        cg = mk.get("choiceGroup")
        if not cg: continue
        try:
            line = float(cg)
        except:
            continue
        over_cote = under_cote = None
        for c in mk.get("choices", []):
            cn = c.get("name", "")
            try:
                dec = round(float(c.get("fractionalValue", 0)) + 1, 2)
            except:
                continue
            if cn.lower() == "over":   over_cote = dec
            elif cn.lower() == "under": under_cote = dec
        if over_cote and under_cote:
            if name == "Total shots":
                out["total_shots"].append((line, over_cote, under_cote))
            elif name == "Total shots on target":
                out["total_sot"].append((line, over_cote, under_cote))
            elif name == "Away team total shots":
                out["away_shots"].append((line, over_cote, under_cote))
            elif name == "Home team total shots":
                out["home_shots"].append((line, over_cote, under_cote))
    return out


def team_shots_props(home_ts, away_ts, home_recent, away_recent, h2h_shots, home_name, away_name, home_pos=10, away_pos=10, match_odds=None, home_form=None, away_form=None):
    """
    Genere des picks tirs/SoT.
    - Utilise les splits dom/ext (Betis home -> shots_home, Elche away -> shots_away)
    - Fallback sur overall (L5/L10/saison) si splits trop petits ou indisponibles.
    """
    props = []
    hr = home_recent or {}; ar = away_recent or {}

    # ── Stats contextuelles (selon role dans CE match) ─────────────────────
    def ctx_or_overall(side_dict, ctx_key, ctx_n_key, overall_l5, overall_l10, overall_season,
                       min_split_n=4):
        """
        Prefere la stat contextuelle (ex: shots_home) si echantillon >= min_split_n.
        Sinon blend 60% split + 40% overall pour compenser le petit echantillon.
        Sinon fallback complet sur overall.
        """
        ctx_val = side_dict.get(ctx_key)
        ctx_n   = side_dict.get(ctx_n_key, 0)
        overall = _weighted_avg(
            side_dict.get(overall_l5),
            side_dict.get(overall_l10),
            side_dict.get(overall_season),
        )
        if ctx_val is None:
            return overall, "overall"
        if ctx_n >= min_split_n + 2:
            # Echantillon suffisant: 80% split + 20% overall (pour reduire variance)
            if overall is None: return ctx_val, f"dom/ext ({ctx_n}m)"
            return ctx_val * 0.8 + overall * 0.2, f"dom/ext ({ctx_n}m)"
        if ctx_n >= min_split_n:
            # Echantillon limite: blend 60/40
            if overall is None: return ctx_val, f"dom/ext faible ({ctx_n}m)"
            return ctx_val * 0.6 + overall * 0.4, f"dom/ext blend ({ctx_n}m)"
        return overall, "overall (split trop petit)"

    # Home team joue a domicile -> stats "home"
    # Away team joue a l'exterieur -> stats "away"
    # Adversaire concede: home_team concede a domicile -> opp_shots_home
    #                    away_team concede a l'exterieur -> opp_shots_away

    # ── Stats PONDEREES par qualite adversaire (priorite) ─────────────────
    # Si dispo, on utilise la moyenne L10 ponderee selon similitude opp_rank avec aujourd'hui
    h_shots_w = hr.get("shots_weighted")
    a_shots_w = ar.get("shots_weighted")

    if h_shots_w is not None:
        h_shots_off = h_shots_w
        h_off_src = f"pondere vs opp #{hr.get('target_opp_rank')}"
    else:
        h_shots_off, h_off_src = ctx_or_overall(hr, "shots_home", "shots_home_n",
                                                "shots_l5", "shots_l10", "shots_season")
    if a_shots_w is not None:
        a_shots_off = a_shots_w
        a_off_src = f"pondere vs opp #{ar.get('target_opp_rank')}"
    else:
        a_shots_off, a_off_src = ctx_or_overall(ar, "shots_away", "shots_away_n",
                                                "shots_l5", "shots_l10", "shots_season")

    h_shots_def, h_def_src = ctx_or_overall(hr, "opp_shots_home", "shots_home_n",
                                            "opp_shots_l5", "opp_shots_l10", None)
    a_shots_def, a_def_src = ctx_or_overall(ar, "opp_shots_away", "shots_away_n",
                                            "opp_shots_l5", "opp_shots_l10", None)

    h_sot_off, _   = ctx_or_overall(hr, "sot_home", "shots_home_n",
                                    "sot_l5", "sot_l10", "sot_season")
    a_sot_off, _   = ctx_or_overall(ar, "sot_away", "shots_away_n",
                                    "sot_l5", "sot_l10", "sot_season")
    h_sot_def, _   = ctx_or_overall(hr, "opp_sot_home", "shots_home_n",
                                    "opp_sot_l5", "opp_sot_l10", None)
    a_sot_def, _   = ctx_or_overall(ar, "opp_sot_away", "shots_away_n",
                                    "opp_sot_l5", "opp_sot_l10", None)

    # Attendu = moyenne attaque (contextuelle) + defense adverse (contextuelle)
    def _expect(off, opp_def):
        if off is None and opp_def is None: return None
        if off is None: return opp_def
        if opp_def is None: return off
        return (off + opp_def) / 2

    exp_h_shots = _expect(h_shots_off, a_shots_def)
    exp_a_shots = _expect(a_shots_off, h_shots_def)
    exp_h_sot   = _expect(h_sot_off,   a_sot_def)
    exp_a_sot   = _expect(a_sot_off,   h_sot_def)

    # Source pour reasoning
    src_info = f"{home_name} (att {h_off_src}) · {away_name} (att {a_off_src})"

    # Ajustement classement (favori prend plus de tirs)
    pd = away_pos - home_pos
    if abs(pd) > 3 and exp_h_shots and exp_a_shots:
        adj = min(0.20, abs(pd) * 0.012)
        if pd > 3:  # home favori
            exp_h_shots *= (1 + adj); exp_a_shots *= max(0.80, 1 - adj)
            if exp_h_sot: exp_h_sot *= (1 + adj * 0.7)
            if exp_a_sot: exp_a_sot *= max(0.80, 1 - adj * 0.7)
            rank_ctx = f"{home_name} favori (#{home_pos} vs #{away_pos})"
        else:        # away favori
            exp_a_shots *= (1 + adj); exp_h_shots *= max(0.80, 1 - adj)
            if exp_a_sot: exp_a_sot *= (1 + adj * 0.7)
            if exp_h_sot: exp_h_sot *= max(0.80, 1 - adj * 0.7)
            rank_ctx = f"{away_name} favori (#{away_pos} vs #{home_pos})"
    else:
        rank_ctx = "équipes proches au classement"

    # H2H blend si dispo
    h2h_avg = h2h_shots.get("avg_total_shots", 0)
    h2h_n   = h2h_shots.get("n_matches", 0)

    exp_total_shots = (exp_h_shots or 0) + (exp_a_shots or 0)
    if h2h_avg and h2h_n >= 3 and exp_total_shots:
        exp_total_shots = exp_total_shots * 0.7 + h2h_avg * 0.3

    exp_total_sot = (exp_h_sot or 0) + (exp_a_sot or 0)

    if not exp_total_shots: return props

    # ── Helper pour reasoning detaille (stats brutes + ajustements + forme) ──
    def _build_shots_reasoning(stat_label="tirs"):
        """
        Construit le reasoning multi-ligne pour les picks tirs.
        Format : stats brutes L10/L5 -> defenses adverses -> attendu match -> forme.
        L'utilisateur veut voir les VRAIES stats pour pouvoir verifier.
        """
        h_l10 = hr.get("shots_l10"); h_l5 = hr.get("shots_l5")
        a_l10 = ar.get("shots_l10"); a_l5 = ar.get("shots_l5")
        # Ligne 1 : stats brutes
        def _stat_str(l10, l5):
            if l10 is None and l5 is None: return "?"
            parts = []
            if l10 is not None: parts.append(f"L10 {l10:.1f}")
            if l5 is not None: parts.append(f"L5 {l5:.1f}")
            return " (".join(parts) + (")" if l5 is not None else "")
        l1 = f"📊 Brut : {home_name} {_stat_str(h_l10, h_l5)} {stat_label}/m · {away_name} {_stat_str(a_l10, a_l5)} {stat_label}/m"
        # Ligne 2 : defenses adverses
        h_def_v = h_shots_def if stat_label == "tirs" else h_sot_def
        a_def_v = a_shots_def if stat_label == "tirs" else a_sot_def
        l2_parts = []
        if h_def_v is not None: l2_parts.append(f"{home_name} concède {h_def_v:.1f}/m")
        if a_def_v is not None: l2_parts.append(f"{away_name} concède {a_def_v:.1f}/m")
        l2 = "🛡️ Défenses : " + " · ".join(l2_parts) if l2_parts else ""
        # Ligne 3 : attendu match
        exp_h = exp_h_shots if stat_label == "tirs" else exp_h_sot
        exp_a = exp_a_shots if stat_label == "tirs" else exp_a_sot
        exp_t = (exp_h or 0) + (exp_a or 0)
        l3 = f"🎯 Attendu : {home_name} ~{exp_h:.1f} · {away_name} ~{exp_a:.1f} → total ~{exp_t:.1f}"
        # Ligne 4 : contexte rank + forme si dispo
        l4_parts = [rank_ctx]
        if home_form:
            l4_parts.append(f"{home_name} forme : {'-'.join(home_form[:5])}")
        if away_form:
            l4_parts.append(f"{away_name} forme : {'-'.join(away_form[:5])}")
        l4 = "⚖️ " + " · ".join(l4_parts)
        return "\n".join(filter(None, [l1, l2, l3, l4]))

    # ── Lignes bookmaker realistes (centrees sur l'esperance) ────────────────
    # Le bookmaker propose typiquement 3 lignes autour de l'esperance.
    # On evite les lignes irrealistes qu'aucun bookmaker n'offre.
    def _build_reasoning(off, opp_def, expected, side_name, ctx_label=""):
        off_s = f"{round(off,1)}" if off else "?"
        def_s = f"{round(opp_def,1)}" if opp_def else "?"
        exp_s = f"{round(expected,1)}" if expected else "?"
        ctx_suffix = f" [{ctx_label}]" if ctx_label else ""
        return f"{side_name} prend {off_s}/m{ctx_suffix} · adv. concède {def_s}/m → attendu ~{exp_s} ({rank_ctx})"

    # ── Tous les candidats (max 2 picks finaux) ──────────────────────────
    candidates = []

    def _add(p, pick_type, expected_val, reasoning_text, priority):
        if not p: return
        candidates.append({
            "direction": f"{pick_type.lower().replace(' ','_')}_{p['direction']}_{p['line']}",
            "type": pick_type,
            "label": p["label"],
            "cote": p.get("cote"),  # vrai cote bookmaker si dispo
            "cote_min": p["cote_min"],
            "value": p.get("value"),
            "confidence": p["confidence"],
            "edge": p.get("edge"),
            "reasoning": reasoning_text,
            "stats": {"expected": round(expected_val, 1)},
            "priority": priority,
        })

    # ── Recupere lignes bookmaker reelles si dispo ────────────────────────
    bm_lines = _extract_bookmaker_shot_lines(match_odds)

    def _best_real_line(expected, bm_data, kind):
        """
        Pour chaque ligne bookmaker, calcule l'edge reel (my_prob * cote - 1).
        Retourne le meilleur over/under avec edge positif.
        """
        if not expected or not bm_data: return None
        best = None
        for line, over_cote, under_cote in bm_data:
            p_over = _poisson_over(expected, line) / 100
            # Over edge = my_prob * cote - 1 (>0 = +EV)
            over_edge = p_over * over_cote - 1
            under_edge = (1 - p_over) * under_cote - 1
            # Prefere positif et eleve. Ignore les picks sous 60% confidence
            if over_edge > 0 and p_over >= 0.60:
                conf = round(p_over * 100)
                if not best or over_edge > best["edge"]:
                    best = {
                        "line": line, "direction": "over",
                        "label": f"Plus de {line} {kind}",
                        "confidence": conf, "cote": over_cote,
                        "cote_min": round(1/p_over, 2),
                        "edge": round(over_edge * 100, 1),
                        "value": ("🎯", f"Edge +{round(over_edge*100,1)}%", "#22c55e"),
                    }
            if under_edge > 0 and (1-p_over) >= 0.60:
                conf = round((1-p_over) * 100)
                if not best or under_edge > best["edge"]:
                    best = {
                        "line": line, "direction": "under",
                        "label": f"Moins de {line} {kind}",
                        "confidence": conf, "cote": under_cote,
                        "cote_min": round(1/(1-p_over), 2),
                        "edge": round(under_edge * 100, 1),
                        "value": ("🎯", f"Edge +{round(under_edge*100,1)}%", "#22c55e"),
                    }
        return best

    # Total tirs - prefer real bookmaker lines
    p_real = _best_real_line(exp_total_shots, bm_lines.get("total_shots"), "tirs total")
    if p_real:
        candidates.append({
            "direction": f"shots_{p_real['direction']}_{p_real['line']}",
            "type": "Tirs (total match)",
            "label": p_real["label"],
            "cote": p_real["cote"],
            "cote_min": p_real["cote_min"],
            "value": p_real["value"],
            "confidence": p_real["confidence"],
            "edge": p_real["edge"],
            "reasoning": _build_shots_reasoning("tirs") + f"\n💰 Ligne book {p_real['line']} @ {p_real['cote']} → edge +{p_real['edge']}%",
            "stats": {"expected_total": round(exp_total_shots, 1)},
            "priority": 1,
        })
    else:
        # Fallback hardcoded lines (pas de cotes reelles dispo)
        p = _pick_line(exp_total_shots, BOOKMAKER_LINES["total_shots"], "tirs total", "Tirs total")
        if p:
            candidates.append({
                "direction": f"shots_{p['direction']}_{p['line']}",
                "type": "Tirs (total match)",
                "label": p["label"],
                "cote": None, "cote_min": p["cote_min"], "value": p.get("value"),
                "confidence": p["confidence"],
                "reasoning": _build_shots_reasoning("tirs"),
                "stats": {"expected_total": round(exp_total_shots, 1)},
                "priority": 1,
            })

    # Tirs equipe - vraies cotes si dispo
    if exp_h_shots:
        p_real_h = _best_real_line(exp_h_shots, bm_lines.get("home_shots"), f"tirs {home_name}")
        if p_real_h:
            candidates.append({
                "direction": f"shots_home_{p_real_h['direction']}_{p_real_h['line']}",
                "type": f"Tirs ({home_name})",
                "label": p_real_h["label"],
                "cote": p_real_h["cote"], "cote_min": p_real_h["cote_min"],
                "value": p_real_h["value"], "confidence": p_real_h["confidence"],
                "edge": p_real_h["edge"],
                "reasoning": _build_reasoning(h_shots_off, a_shots_def, exp_h_shots, home_name, h_off_src)
                             + f" | bookmaker {p_real_h['line']} @ {p_real_h['cote']} → edge +{p_real_h['edge']}%",
                "stats": {"expected": round(exp_h_shots, 1)},
                "priority": 2,
            })
        else:
            p = _pick_line(exp_h_shots, BOOKMAKER_LINES["home_shots"], f"tirs {home_name}", "Tirs équipe")
            _add(p, f"Tirs ({home_name})", exp_h_shots,
                 _build_reasoning(h_shots_off, a_shots_def, exp_h_shots, home_name, h_off_src), 2)
    if exp_a_shots:
        p_real_a = _best_real_line(exp_a_shots, bm_lines.get("away_shots"), f"tirs {away_name}")
        if p_real_a:
            candidates.append({
                "direction": f"shots_away_{p_real_a['direction']}_{p_real_a['line']}",
                "type": f"Tirs ({away_name})",
                "label": p_real_a["label"],
                "cote": p_real_a["cote"], "cote_min": p_real_a["cote_min"],
                "value": p_real_a["value"], "confidence": p_real_a["confidence"],
                "edge": p_real_a["edge"],
                "reasoning": _build_reasoning(a_shots_off, h_shots_def, exp_a_shots, away_name, a_off_src)
                             + f" | bookmaker {p_real_a['line']} @ {p_real_a['cote']} → edge +{p_real_a['edge']}%",
                "stats": {"expected": round(exp_a_shots, 1)},
                "priority": 2,
            })
        else:
            p = _pick_line(exp_a_shots, BOOKMAKER_LINES["away_shots"], f"tirs {away_name}", "Tirs équipe")
            _add(p, f"Tirs ({away_name})", exp_a_shots,
                 _build_reasoning(a_shots_off, h_shots_def, exp_a_shots, away_name, a_off_src), 2)

    # Tirs cadres total - prefer real bookmaker lines
    if exp_total_sot:
        p_real_sot = _best_real_line(exp_total_sot, bm_lines.get("total_sot"), "tirs cadrés total")
        if p_real_sot:
            candidates.append({
                "direction": f"sot_{p_real_sot['direction']}_{p_real_sot['line']}",
                "type": "Tirs cadrés (total)",
                "label": p_real_sot["label"],
                "cote": p_real_sot["cote"], "cote_min": p_real_sot["cote_min"],
                "value": p_real_sot["value"],
                "confidence": p_real_sot["confidence"],
                "edge": p_real_sot["edge"],
                "reasoning": _build_shots_reasoning("tirs cadrés") + f"\n💰 Ligne book {p_real_sot['line']} @ {p_real_sot['cote']} → edge +{p_real_sot['edge']}%",
                "stats": {"expected_total": round(exp_total_sot, 1)},
                "priority": 3,
            })
        else:
            p = _pick_line(exp_total_sot, BOOKMAKER_LINES["total_sot"], "tirs cadrés total", "Tirs cadrés total")
            if p:
                candidates.append({
                    "direction": f"sot_{p['direction']}_{p['line']}",
                    "type": "Tirs cadrés (total)",
                    "label": p["label"],
                    "cote": None, "cote_min": p["cote_min"], "value": p.get("value"),
                    "confidence": p["confidence"],
                    "reasoning": _build_shots_reasoning("tirs cadrés"),
                    "stats": {"expected_total": round(exp_total_sot, 1)},
                    "priority": 3,
                })
    if exp_h_sot:
        p = _pick_line(exp_h_sot, BOOKMAKER_LINES["home_sot"], f"tirs cadrés {home_name}", "Tirs cadrés équipe")
        _add(p, f"Tirs cadrés ({home_name})", exp_h_sot,
             _build_reasoning(h_sot_off, a_sot_def, exp_h_sot, home_name, h_off_src), 4)
    if exp_a_sot:
        p = _pick_line(exp_a_sot, BOOKMAKER_LINES["away_sot"], f"tirs cadrés {away_name}", "Tirs cadrés équipe")
        _add(p, f"Tirs cadrés ({away_name})", exp_a_sot,
             _build_reasoning(a_sot_off, h_sot_def, exp_a_sot, away_name, a_off_src), 4)

    if not candidates:
        return props

    # Tri: confidence desc (privilege le pick le plus marque)
    candidates.sort(key=lambda c: (-c["confidence"], c["priority"]))

    # MAX 2 picks final : prefere 1 "total" + 1 "equipe" si dispo, sinon top 2 par confiance
    selected = []
    has_total = False
    has_team = False
    for c in candidates:
        is_total = "(total" in c["type"]
        if is_total and not has_total and len(selected) < 2:
            selected.append(c); has_total = True
        elif (not is_total) and not has_team and len(selected) < 2:
            selected.append(c); has_team = True
        if len(selected) >= 2: break

    # Si on n'a que 1 pick mais d'autres candidats forts existent, prendre le 2eme top
    if len(selected) < 2 and len(candidates) > 1:
        for c in candidates:
            if c not in selected:
                selected.append(c); break

    for c in selected:
        c.pop("priority", None)

    props.extend(selected)
    return props

# ─── Main ────────────────────────────────────────────────────────────────────

def run():
    matches      = load_matches()
    player_stats = load_player_stats()
    output       = []

    for match in matches:
        team_picks, home_pp, away_pp, fun_picks = analyze_match(match, player_stats)
        if team_picks or home_pp or away_pp:
            top = team_picks[0] if team_picks else (home_pp[0] if home_pp else away_pp[0] if away_pp else None)
            output.append({
                "league":       match["league"],
                "home":         match["home"],
                "away":         match["away"],
                "start_ts":     match.get("start_ts"),
                "match_id":     match["id"],
                "picks":        team_picks,
                "home_players": home_pp,
                "away_players": away_pp,
                "fun_picks":    fun_picks,
                "top_pick":     top,
            })

    output.sort(key=lambda x: x["top_pick"]["confidence"] if x["top_pick"] else 0, reverse=True)

    with open("data/picks.json","w",encoding="utf-8") as f:
        json.dump(output,f,ensure_ascii=False,indent=2)

    print(f"✅ {len(output)} matchs → data/picks.json")
    for m in output[:5]:
        tp  = m["top_pick"]
        c   = f" @ {tp['cote']:.2f}" if tp.get("cote") else ""
        nb  = len(m["home_players"]) + len(m["away_players"])
        fun = len(m.get("fun_picks",[]))
        print(f"  {m['home']} vs {m['away']} → {tp['label']} ({tp['confidence']}%){c} · {nb} props joueurs · {fun} fun picks")

    _save_to_history(output)
    return output


def _save_to_history(matches):
    """Sauvegarde les picks foot dans data/picks_history.json (PENDING - resolu plus tard)."""
    from pathlib import Path
    from datetime import datetime, timezone
    hist_path = Path("data/picks_history.json")
    history = {"picks": []}
    if hist_path.exists():
        try: history = json.loads(hist_path.read_text(encoding="utf-8"))
        except Exception: history = {"picks": []}
    existing_ids = {p.get("id") for p in history.get("picks", [])}
    today = datetime.now().strftime("%Y-%m-%d")
    n_added = 0

    for m in matches:
        match_id = m.get("match_id")
        matchup = f"{m.get('home','?')} vs {m.get('away','?')}"
        league = m.get("league", "")
        ts = m.get("start_ts")
        try:
            date = datetime.fromtimestamp(int(ts), tz=timezone.utc).strftime("%Y-%m-%d") if ts else today
        except Exception:
            date = today

        # Team picks
        for pk in m.get("picks", []):
            pid = f"{date}_{match_id}_team_{pk.get('direction','?')}"
            if pid in existing_ids: continue
            e = dict(pk); e.update({"id": pid, "date": date, "match_id": match_id,
                                     "matchup": matchup, "league": league, "category": "team",
                                     "result": "PENDING", "actual": None, "resolved_at": None})
            history["picks"].append(e); existing_ids.add(pid); n_added += 1
        # Player picks
        for plist, side in [(m.get("home_players", []), "home"), (m.get("away_players", []), "away")]:
            for pk in plist:
                pid = f"{date}_{match_id}_player_{pk.get('player','?')}_{pk.get('type','?')}"
                if pid in existing_ids: continue
                e = dict(pk); e.update({"id": pid, "date": date, "match_id": match_id,
                                         "matchup": matchup, "league": league, "category": "player",
                                         "side": side, "result": "PENDING", "actual": None, "resolved_at": None})
                history["picks"].append(e); existing_ids.add(pid); n_added += 1
        # Fun picks
        for pk in m.get("fun_picks", []):
            pid = f"{date}_{match_id}_fun_{pk.get('type','?')}_{pk.get('label','?')[:30]}"
            if pid in existing_ids: continue
            e = dict(pk); e.update({"id": pid, "date": date, "match_id": match_id,
                                     "matchup": matchup, "league": league, "category": "fun",
                                     "result": "PENDING", "actual": None, "resolved_at": None})
            history["picks"].append(e); existing_ids.add(pid); n_added += 1

    hist_path.write_text(json.dumps(history, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[history] {n_added} picks foot ajoutes (total: {len(history['picks'])})")


if __name__ == "__main__":
    run()