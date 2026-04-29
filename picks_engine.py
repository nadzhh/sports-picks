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

def get_pos(fd, s): return (fd or {}).get(s, {}).get("position", 20)
def get_rat(fd, s):
    try: return float((fd or {}).get(s, {}).get("avgRating", 6.5))
    except: return 6.5

def parse_h2h(h):
    try:
        d = h.get("teamDuel", {})
        return d.get("homeWins",0), d.get("draws",0), d.get("awayWins",0)
    except: return 0,0,0

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
    w, d, l = f.count("W"), f.count("D"), f.count("L")
    return f"{w}V/{d}N/{l}D sur {len(f)} matchs"

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
    hw, d, aw = parse_h2h(h2h); h2ht = hw+d+aw

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
                f"H2H: {hw}V/{d}N/{aw}D · Note Sofa: {hr:.1f}/10",hf)

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
                f"H2H: {aw}V/{d}N/{hw}D · Note Sofa: {ar:.1f}/10",af)

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
        h2h_ = (hw+d)/h2ht if h2ht else 0.5
        # Forme récente pondérée davantage
        conf = round(ub * 65 + h_form_score * 0.2 + h2h_ * 15)
        if conf >= 70:
            trend_txt = f", {h_trend}" if h_trend not in ("stable","en légère baisse") else ""
            dc_cands.append(("home_dc","Double chance",f"{home} ou Nul (1X)",c1x,conf,
                f"{home} invaincu {round(ub*100)}% sur {len(hf)} derniers matchs{trend_txt} · "
                f"H2H: {hw+d}/{h2ht} matchs sans défaite",hf))
    if af:
        ub   = unbeaten(af)
        h2h_ = (aw+d)/h2ht if h2ht else 0.5
        conf = round(ub * 65 + a_form_score * 0.2 + h2h_ * 15)
        if conf >= 70:
            trend_txt = f", {a_trend}" if a_trend not in ("stable","en légère baisse") else ""
            dc_cands.append(("away_dc","Double chance",f"Nul ou {away} (X2)",cx2,conf,
                f"{away} invaincu {round(ub*100)}% sur {len(af)} derniers matchs{trend_txt} · "
                f"H2H: {aw+d}/{h2ht} matchs sans défaite",af))
    if dc_cands:
        add(*max(dc_cands, key=lambda x: x[4]))

    # ── BTTS — reasoning explicatif ─────────────────────────────────────────
    if btt:
        py, pn = prob(cy), prob(cn)

        import math as _math

        # Probabilité de scorer via Poisson + fréquence BTTS observée
        h_gf  = h_goals.get("gf", 0)
        a_gf  = a_goals.get("gf", 0)
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
        h_gf_r  = home_rec.get("goals_for_pm", 0)
        a_gf_r  = away_rec.get("goals_for_pm", 0)
        h_ga_r  = home_rec.get("goals_ag_pm", 0)
        a_ga_r  = away_rec.get("goals_ag_pm", 0)
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
                r = (
                    f"{home}: {h_att:.1f} buts/{src_hr} · défense {away} concède {a_def:.1f}/{src_ar} → ~{h_exp:.1f} attendus · "
                    f"{away}: {a_att:.1f} buts/{src_ar} · défense {home} concède {h_def:.1f}/{src_hr} → ~{a_exp:.1f} attendus · "
                    f"Total: ~{total_exp} buts (P={p_data}% via Poisson)"
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
        if ph >= 52 and ph > pa:
            r = (f"{home} a l'avantage du terrain et {form_summary(hf[-3:])} sur ses 3 derniers — "
                 f"tendance à ouvrir le score à domicile")
            add("fts_home","1ère équipe à marquer",f"{home} marque en premier",c_home,
                min(82,round(ph+3)),r)
        elif pa >= 52 and pa > ph:
            r = (f"{away} {form_summary(af[-3:])} sur ses 3 derniers — "
                 f"équipe capable de scorer rapidement même à l'extérieur")
            add("fts_away","1ère équipe à marquer",f"{away} marque en premier",c_away,
                min(82,round(pa+3)),r)

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
    team_picks = sorted(seen.values(), key=lambda x: x["confidence"], reverse=True)[:5]

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
            conf = round(min(65, 40 + d/h2ht*25 if h2ht else 45))
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

    home_conceded = away_ts_data.get("conceded_pm", 0) if away_ts_data else 0
    away_conceded = home_ts_data.get("conceded_pm", 0) if home_ts_data else 0

    home_pp_raw = player_picks_contextual(home_players, ap, ar, home_conceded, btts_p)
    away_pp_raw = player_picks_contextual(away_players, hp, hr, away_conceded, btts_p)

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
                                   h2h_shots_d, home, away, hp, ap)
    team_picks.extend(shots_props)

    return team_picks, home_pp, away_pp, fun_picks

# ─── Tirs équipe ─────────────────────────────────────────────────────────────

def team_shots_props(home_ts, away_ts, home_recent, away_recent, h2h_shots, home_name, away_name, home_pos=10, away_pos=10):
    props = []
    h_shots_r = home_recent.get("shots_pm", 0)
    a_shots_r = away_recent.get("shots_pm", 0)
    h_sot_r   = home_recent.get("sot_pm", 0)
    a_sot_r   = away_recent.get("sot_pm", 0)
    h_trend   = home_recent.get("shots_trend", "→ stable")
    a_trend   = away_recent.get("shots_trend", "→ stable")

    h_shots_s = (home_ts or {}).get("shots_pm", 0)
    a_shots_s = (away_ts or {}).get("shots_pm", 0)
    h_sot_s   = (home_ts or {}).get("sot_pm", 0)
    a_sot_s   = (away_ts or {}).get("sot_pm", 0)

    h2h_avg = h2h_shots.get("avg_total_shots", 0)
    h2h_n   = h2h_shots.get("n_matches", 0)

    h_shots = h_shots_r if h_shots_r else h_shots_s
    a_shots = a_shots_r if a_shots_r else a_shots_s
    h_sot   = h_sot_r   if h_sot_r   else h_sot_s
    a_sot   = a_sot_r   if a_sot_r   else a_sot_s
    src     = "forme récente" if (h_shots_r and a_shots_r) else "stats saison"

    if not h_shots or not a_shots: return props

    pd = away_pos - home_pos
    adj = min(0.28, abs(pd) * 0.015)
    if pd > 3:
        h_adj = round(h_shots * (1 + adj), 1); a_adj = round(a_shots * max(0.72, 1-adj), 1)
        h_sot_adj = round(h_sot * (1 + adj*0.8), 1); a_sot_adj = round(a_sot * max(0.72, 1-adj*0.8), 1)
        ctx = f" · {home_name} favoris ({abs(pd)} places d'écart)"
    elif pd < -3:
        h_adj = round(h_shots * max(0.72, 1-adj), 1); a_adj = round(a_shots * (1 + adj), 1)
        h_sot_adj = round(h_sot * max(0.72, 1-adj*0.8), 1); a_sot_adj = round(a_sot * (1 + adj*0.8), 1)
        ctx = f" · {away_name} favoris ({abs(pd)} places d'écart)"
    else:
        h_adj=h_shots; a_adj=a_shots; h_sot_adj=h_sot; a_sot_adj=a_sot; ctx=""

    total = round(h_adj + a_adj, 1)
    total_sot = round(h_sot_adj + a_sot_adj, 1)

    if h2h_avg and h2h_n >= 3:
        total = round(total * 0.65 + h2h_avg * 0.35, 1)
        h2h_txt = f" · H2H: {h2h_avg} tirs/match ({h2h_n} confrontations)"
    else:
        h2h_txt = ""

    trend_txt = f" · {home_name} {h_trend}, {away_name} {a_trend}" if (h_shots_r and a_shots_r) else ""

    reasoning_shots = (f"{src}: {home_name} {h_shots}→{h_adj}/match · "
                       f"{away_name} {a_shots}→{a_adj}/match{ctx}{h2h_txt}{trend_txt} → ~{total} attendus")

    # Lignes bookmaker réelles pour les tirs
    BOOKMAKER_SHOT_LINES = [20.5, 22.5, 24.5, 26.5, 28.5, 30.5, 32.5]
    closest = min(BOOKMAKER_SHOT_LINES, key=lambda x: abs(total - x))
    diff = total - closest
    if abs(diff) >= 1.5:
        if diff > 0:
            props.append({"direction": f"shots_over_{closest}",
                "type":"Tirs (équipes)","label":f"Plus de {closest} tirs total",
                "cote":None,"confidence":min(82,round(52+diff*2.5)),
                "reasoning":reasoning_shots,"stats":{}})
        else:
            props.append({"direction": f"shots_under_{closest}",
                "type":"Tirs (équipes)","label":f"Moins de {closest} tirs total",
                "cote":None,"confidence":min(80,round(52+abs(diff)*2.5)),
                "reasoning":reasoning_shots,"stats":{}})

    if total_sot > 0:
        reasoning_sot = (f"{src}: {home_name} {h_sot}→{h_sot_adj} cadrés/match · "
                         f"{away_name} {a_sot}→{a_sot_adj} cadrés/match{h2h_txt} → ~{total_sot} attendus")
        BOOKMAKER_SOT_LINES = [6.5, 7.5, 8.5, 9.5, 10.5, 11.5]
        closest_sot = min(BOOKMAKER_SOT_LINES, key=lambda x: abs(total_sot - x))
        diff_sot = total_sot - closest_sot
        if abs(diff_sot) >= 1.0:
            if diff_sot > 0:
                props.append({"direction": f"sot_over_{closest_sot}",
                    "type":"Tirs cadrés (équipes)","label":f"Plus de {closest_sot} tirs cadrés",
                    "cote":None,"confidence":min(80,round(50+diff_sot*3)),
                    "reasoning":reasoning_sot,"stats":{}})
            else:
                props.append({"direction": f"sot_under_{closest_sot}",
                    "type":"Tirs cadrés (équipes)","label":f"Moins de {closest_sot} tirs cadrés",
                    "cote":None,"confidence":min(78,round(50+abs(diff_sot)*3)),
                    "reasoning":reasoning_sot,"stats":{}})

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

    return output

if __name__ == "__main__":
    run()