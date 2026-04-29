"""
generate_site.py — v6
- Clic sur un match → panneau stats détaillées (forme, buts, tirs, BTTS)
- Analyse IA sur top picks
- Paris fun en violet
"""

import json, os, urllib.request
from datetime import datetime, timedelta
import picks_engine

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")

# ─── Claude API ──────────────────────────────────────────────────────────────

def ask_claude_players(ctx):
    if not ANTHROPIC_API_KEY or not ctx: return {}
    prompt = (
        "Tu es analyste paris sportifs. Pour chaque pick joueur, "
        "donne UNE phrase de contexte match (max 20 mots). "
        'Réponds UNIQUEMENT en JSON : {"Nom Joueur_Type": "analyse..."}\n\n' + ctx
    )
    payload = json.dumps({
        "model": "claude-haiku-4-5-20251001", "max_tokens": 400,
        "messages": [{"role": "user", "content": prompt}]
    }).encode()
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages", data=payload,
        headers={"x-api-key": ANTHROPIC_API_KEY,
                 "anthropic-version": "2023-06-01", "content-type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            text = json.loads(resp.read())["content"][0]["text"]
            return json.loads(text.strip().strip("```json").strip("```").strip())
    except: return {}

def ask_claude_teams(summary):
    if not ANTHROPIC_API_KEY: return []
    prompt = (
        "Analyste paris sportifs. Pour chaque pick, UNE phrase (max 15 mots). "
        'JSON: [{"pick":"...","analyse":"..."}]\n\n' + summary
    )
    payload = json.dumps({
        "model": "claude-haiku-4-5-20251001", "max_tokens": 300,
        "messages": [{"role": "user", "content": prompt}]
    }).encode()
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages", data=payload,
        headers={"x-api-key": ANTHROPIC_API_KEY,
                 "anthropic-version": "2023-06-01", "content-type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            text = json.loads(resp.read())["content"][0]["text"]
            return json.loads(text.strip().strip("```json").strip("```").strip())
    except: return []

# ─── Helpers visuels ─────────────────────────────────────────────────────────

def conf_color(c):
    if c >= 80: return "#22c55e"
    if c >= 68: return "#84cc16"
    if c >= 55: return "#f59e0b"
    return "#6b7280"

def player_conf_color(c):
    if c >= 65: return "#22c55e"
    if c >= 50: return "#84cc16"
    return "#f59e0b"

def form_badges(results, details=None):
    html = ""
    for i, r in enumerate(results or []):
        c = {"W":"#22c55e","D":"#f59e0b","L":"#ef4444"}.get(r,"#6b7280")
        if details and i < len(details):
            d     = details[i]
            tip   = f"{d.get('date','')} · {d.get('opp','')} {d.get('score','')}"
            if d.get("comp"): tip += f" ({d['comp']})"
            html += (
                f'<span style="position:relative;display:inline-block;margin:1px" '
                f'onmouseover="this.querySelector(\'.tip\').style.opacity=1" '
                f'onmouseout="this.querySelector(\'.tip\').style.opacity=0">'
                f'<span style="background:{c};color:#fff;border-radius:4px;'
                f'padding:2px 6px;font-size:10px;font-weight:bold;cursor:help">{r}</span>'
                f'<span class="tip" style="position:absolute;bottom:calc(100% + 6px);left:50%;'
                f'transform:translateX(-50%);background:#0f172a;color:#f1f5f9;'
                f'font-size:11px;padding:5px 9px;border-radius:6px;white-space:nowrap;'
                f'pointer-events:none;opacity:0;transition:opacity .12s;'
                f'border:1px solid #3b82f6;z-index:100;box-shadow:0 4px 16px rgba(0,0,0,0.6)"'
                f'>{tip}</span></span>'
            )
        else:
            html += (
                f'<span style="background:{c};color:#fff;border-radius:4px;'
                f'padding:2px 6px;font-size:10px;font-weight:bold;margin:1px;'
                f'display:inline-block">{r}</span>'
            )
    return html

def format_datetime(ts):
    try: return datetime.fromtimestamp(ts).strftime("%d/%m %H:%M")
    except: return "?"

def day_label(ts):
    try:
        d = datetime.fromtimestamp(ts).date()
        today = datetime.now().date()
        if d == today: return "Aujourd'hui"
        if d == today + timedelta(1): return "Demain"
        if d == today + timedelta(2): return "Après-demain"
        return datetime.fromtimestamp(ts).strftime("%A %d/%m").capitalize()
    except: return "Autre"

def cote_badge(cote):
    if not cote: return ""
    return (f'<span style="display:inline-flex;align-items:center;gap:3px;background:#1e3a5f;'
            f'color:#60a5fa;border:1px solid #2563eb;border-radius:6px;padding:2px 8px;'
            f'font-size:12px;font-weight:700;margin-left:6px">📊 {cote:.2f}</span>')

def pos_badge(pos):
    c = {"F":"#ef4444","M":"#3b82f6","D":"#22c55e"}.get(pos,"#6b7280")
    l = {"F":"ATT","M":"MIL","D":"DEF"}.get(pos, pos)
    return f'<span style="background:{c}22;color:{c};border:1px solid {c};border-radius:4px;padding:1px 5px;font-size:10px;font-weight:700;margin-right:3px">{l}</span>'

# ─── Stats panel (données détaillées au clic) ─────────────────────────────────

def build_stats_panel(mid_safe, home, away, form_data, home_recent, away_recent, home_ts, away_ts, home_players=None, away_players=None):
    def safe(v, decimals=1):
        if v is None or v == "?" or v == 0: return "—"
        try: return f"{float(v):.{decimals}f}"
        except: return str(v)

    hf  = form_data.get("homeTeam", {}).get("form", [])
    af  = form_data.get("awayTeam", {}).get("form", [])
    hp  = form_data.get("homeTeam", {}).get("position", "?")
    ap  = form_data.get("awayTeam", {}).get("position", "?")
    hrt = form_data.get("homeTeam", {}).get("avgRating", "?")
    art = form_data.get("awayTeam", {}).get("avgRating", "?")

    hr  = home_recent or {}
    ar  = away_recent or {}
    hts = home_ts or {}
    ats = away_ts or {}

    h_n  = hr.get("n_matches", 0)
    a_n  = ar.get("n_matches", 0)
    src_h = f"L{h_n}" if h_n else "saison"
    src_a = f"L{a_n}" if a_n else "saison"

    # Buts
    h_gf   = safe(hr.get("goals_for_pm")  or hts.get("goals_pm"))
    h_ga   = safe(hr.get("goals_ag_pm")   or hts.get("conceded_pm"))
    h_tot  = safe(hr.get("total_goals_pm"))
    a_gf   = safe(ar.get("goals_for_pm")  or ats.get("goals_pm"))
    a_ga   = safe(ar.get("goals_ag_pm")   or ats.get("conceded_pm"))
    a_tot  = safe(ar.get("total_goals_pm"))

    # BTTS
    h_btts = f"{hr.get('btts_count','?')}/{hr.get('btts_n',0)} ({hr.get('btts_rate',0)}%)" if hr.get("btts_n") else "—"
    a_btts = f"{ar.get('btts_count','?')}/{ar.get('btts_n',0)} ({ar.get('btts_rate',0)}%)" if ar.get("btts_n") else "—"

    # Tirs
    h_shots = safe(hr.get("shots_pm") or hts.get("shots_pm"))
    h_sot   = safe(hr.get("sot_pm")   or hts.get("sot_pm"))
    h_xg    = safe(hr.get("xg_pm"), 2)
    h_trend = hr.get("shots_trend", "")
    a_shots = safe(ar.get("shots_pm") or ats.get("shots_pm"))
    a_sot   = safe(ar.get("sot_pm")   or ats.get("sot_pm"))
    a_xg    = safe(ar.get("xg_pm"), 2)
    a_trend = ar.get("shots_trend", "")

    def row(label, hv, av, alt=False):
        bg = "#0a1628" if alt else "#0d1b2e"
        return (
            f'<div style="display:grid;grid-template-columns:1fr 140px 1fr;'
            f'padding:7px 10px;background:{bg}">'
            f'<div style="text-align:right;color:#f1f5f9;font-weight:600;font-size:13px">{hv}</div>'
            f'<div style="text-align:center;color:#475569;font-size:11px;padding:2px 6px">{label}</div>'
            f'<div style="color:#94a3b8;font-size:13px">{av}</div>'
            f'</div>'
        )

    rows = (
        f'<div style="display:grid;grid-template-columns:1fr 140px 1fr;padding:10px;'
        f'background:#0a1628;border-radius:8px 8px 0 0;margin-bottom:2px">'
        f'<div style="text-align:right;color:#60a5fa;font-weight:700">{home}</div>'
        f'<div style="text-align:center;color:#334155;font-size:11px">STATISTIQUES</div>'
        f'<div style="color:#60a5fa;font-weight:700">{away}</div>'
        f'</div>'
    )

    rows += (
        f'<div style="text-align:center;color:#475569;font-size:10px;'
        f'padding:5px 0 3px;text-transform:uppercase;letter-spacing:1px;'
        f'background:#0d1b2e">📋 CLASSEMENT & FORME</div>'
    )
    rows += row("Classement", f"#{hp}", f"#{ap}", True)
    rows += row("Forme 5 matchs",
                form_badges(hf[-5:]) if hf else "—",
                form_badges(af[-5:]) if af else "—")
    rows += row("Note Sofascore", f"{hrt}/10", f"{art}/10", True)

    # ── Détail des 5 derniers matchs ──────────────────────────────
    h_details = hr.get("match_details", [])[-5:]
    a_details = ar.get("match_details", [])[-5:]

    def match_rows(details):
        if not details: return '<span style="color:#475569">—</span>'
        html = '<div style="font-size:11px">'
        for d in details:
            r       = d.get("result","?")
            col     = {"W":"#22c55e","D":"#f59e0b","L":"#ef4444"}.get(r,"#6b7280")
            loc     = "🏠" if d.get("home") else "✈️"
            scorers = d.get("scorers", [])
            scorer_txt = ""
            if scorers:
                scorer_txt = (
                    f'<div style="color:#60a5fa;font-size:10px;'
                    f'padding-left:22px;padding-bottom:3px;line-height:1.5">'
                    + " · ".join(scorers) + '</div>'
                )
            html += (
                f'<div style="padding:3px 0">'
                f'<div style="display:flex;align-items:center;gap:5px">'
                f'<span style="background:{col};color:#fff;border-radius:3px;padding:0 5px;'
                f'font-size:10px;font-weight:bold;min-width:16px;text-align:center">{r}</span>'
                f'<span style="color:#64748b;font-size:10px">{loc}</span>'
                f'<span style="color:#94a3b8;flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">'
                f'{d.get("opp","")}</span>'
                f'<span style="color:#f1f5f9;font-weight:600;margin-left:4px">{d.get("score","")}</span>'
                f'</div>'
                + scorer_txt +
                f'</div>'
            )
        html += '</div>'
        return html

    if h_details or a_details:
        rows += (
            f'<div style="display:grid;grid-template-columns:1fr 140px 1fr;'
            f'padding:8px 10px;background:#0a1628">'
            f'<div style="text-align:right">{match_rows(h_details)}</div>'
            f'<div style="text-align:center;color:#475569;font-size:11px;padding-top:4px">5 derniers</div>'
            f'<div>{match_rows(a_details)}</div>'
            f'</div>'
        )

    rows += (
        f'<div style="text-align:center;color:#475569;font-size:10px;'
        f'padding:5px 0 3px;text-transform:uppercase;letter-spacing:1px;'
        f'background:#0d1b2e;margin-top:2px">⚽ BUTS ({src_h} / {src_a})</div>'
    )
    rows += row("Buts marqués/match", h_gf, a_gf, True)
    rows += row("Buts concédés/match", h_ga, a_ga)
    rows += row("Total buts/match", h_tot, a_tot, True)
    rows += row("BTTS (L10)", h_btts, a_btts)

    rows += (
        f'<div style="text-align:center;color:#475569;font-size:10px;'
        f'padding:5px 0 3px;text-transform:uppercase;letter-spacing:1px;'
        f'background:#0d1b2e;margin-top:2px">🎯 TIRS ({src_h} / {src_a})</div>'
    )
    rows += row(f"Tirs/match", f"{h_shots} {h_trend}", f"{a_shots} {a_trend}", True)
    rows += row("Tirs cadrés/match", h_sot, a_sot)
    rows += row("xG/match", h_xg, a_xg, True)

    # ── Section joueurs décisifs ───────────────────────────────────────────────
    def player_card(p, team_name):
        name    = p.get("shortName", p.get("name",""))
        pos     = p.get("position","")
        apps    = p.get("appearances", 0)
        goals   = p.get("goals", 0)
        assists = p.get("assists", 0)
        xgpm    = p.get("xG_pm", 0)
        xapm    = p.get("xA_pm", 0)
        gpm     = p.get("goals_pm", 0)
        apm     = p.get("assists_pm", 0)
        is_sub  = p.get("is_sub", False)
        # Stats championnat si disponibles (pour matchs européens)
        lg_goals   = p.get("league_goals")
        lg_assists = p.get("league_assists")
        lg_apps    = p.get("league_apps")
        lg_name    = p.get("league_name", "")

        pos_c   = {"F":"#ef4444","M":"#3b82f6","D":"#22c55e"}.get(pos,"#6b7280")
        pos_l   = {"F":"ATT","M":"MIL","D":"DEF"}.get(pos, pos)
        sub_tag = " 🔄" if is_sub else ""

        g_width = min(100, round(gpm * 200))
        a_width = min(100, round(apm * 200))

        # Bloc stats championnat (rouge/orange si dispo)
        league_block = ""
        if lg_goals is not None and lg_apps:
            lg_gpm  = round(lg_goals / lg_apps, 3) if lg_apps else 0
            lg_asts = lg_assists or 0
            lg_apm  = round(lg_asts / lg_apps, 3)  if lg_apps else 0
            lg_g_w  = min(100, round(lg_gpm * 200))
            lg_a_w  = min(100, round(lg_apm * 200))
            league_block = (
                f'<div style="margin-top:6px;padding-top:6px;border-top:1px solid #1e293b">'
                f'<div style="color:#94a3b8;font-size:10px;font-weight:600;margin-bottom:4px">'
                f'📋 {lg_name or "Championnat"} — {lg_apps} matchs</div>'
                f'<div style="display:grid;grid-template-columns:1fr 1fr;gap:6px;font-size:11px">'
                f'<div>'
                f'<div style="color:#fca5a5;margin-bottom:2px">⚽ {lg_goals}G · {round(lg_gpm*100,0):.0f}%/m</div>'
                f'<div style="background:#1e293b;border-radius:2px;height:4px">'
                f'<div style="background:#f87171;height:4px;border-radius:2px;width:{lg_g_w}%"></div></div>'
                f'</div>'
                f'<div>'
                f'<div style="color:#fcd34d;margin-bottom:2px">🎯 {lg_asts}PD · {round(lg_apm*100,0):.0f}%/m</div>'
                f'<div style="background:#1e293b;border-radius:2px;height:4px">'
                f'<div style="background:#fbbf24;height:4px;border-radius:2px;width:{lg_a_w}%"></div></div>'
                f'</div>'
                f'</div>'
                f'</div>'
            )

        return (
            f'<div style="background:#0a1628;border-radius:6px;padding:8px 10px;margin-bottom:6px">'
            f'<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:5px">'
            f'<div style="display:flex;align-items:center;gap:5px">'
            f'<span style="background:{pos_c}22;color:{pos_c};border:1px solid {pos_c};border-radius:3px;'
            f'padding:0 5px;font-size:10px;font-weight:700">{pos_l}</span>'
            f'<span style="color:#f1f5f9;font-weight:600;font-size:13px">{name}{sub_tag}</span>'
            f'</div>'
            f'<span style="color:#475569;font-size:11px">{apps} matchs</span>'
            f'</div>'
            f'<div style="display:grid;grid-template-columns:1fr 1fr;gap:6px;font-size:11px">'
            f'<div>'
            f'<div style="color:#64748b;margin-bottom:2px">⚽ {goals}G · {round(gpm*100,0):.0f}%/match · xG {xgpm:.2f}</div>'
            f'<div style="background:#1e293b;border-radius:2px;height:4px">'
            f'<div style="background:#22c55e;height:4px;border-radius:2px;width:{g_width}%"></div></div>'
            f'</div>'
            f'<div>'
            f'<div style="color:#64748b;margin-bottom:2px">🎯 {assists}PD · {round(apm*100,0):.0f}%/match · xA {xapm:.2f}</div>'
            f'<div style="background:#1e293b;border-radius:2px;height:4px">'
            f'<div style="background:#3b82f6;height:4px;border-radius:2px;width:{a_width}%"></div></div>'
            f'</div>'
            f'</div>'
            + league_block +
            f'</div>'
        )

    def top_players(players, n=4):
        if not players: return []
        # Filtre offensifs avec assez de matchs, trie par G+A et xG+xA
        off = [p for p in players if p.get("position") in ("F","M") and p.get("appearances",0) >= 5]
        off.sort(key=lambda p: (p.get("goals",0)+p.get("assists",0))*0.6 + (p.get("xG_pm",0)+p.get("xA_pm",0))*p.get("appearances",0)*0.4, reverse=True)
        return off[:n]

    h_top = top_players(home_players or [])
    a_top = top_players(away_players or [])

    if h_top or a_top:
        rows += (
            f'<div style="text-align:center;color:#475569;font-size:10px;'
            f'padding:5px 0 3px;text-transform:uppercase;letter-spacing:1px;'
            f'background:#0d1b2e;margin-top:2px">⭐ JOUEURS DÉCISIFS</div>'
        )
        rows += (
            f'<div style="display:grid;grid-template-columns:1fr 1fr;gap:8px;padding:10px;background:#0d1b2e">'
            f'<div>'
            f'<div style="color:#3b82f6;font-size:11px;font-weight:600;margin-bottom:6px;text-align:center">🏠 {home}</div>'
            + "".join(player_card(p, home) for p in h_top) +
            f'</div>'
            f'<div>'
            f'<div style="color:#3b82f6;font-size:11px;font-weight:600;margin-bottom:6px;text-align:center">✈️ {away}</div>'
            + "".join(player_card(p, away) for p in a_top) +
            f'</div>'
            f'</div>'
        )

    return (
        f'<div id="stats-{mid_safe}" style="display:none;border:1px solid #1e293b;'
        f'border-radius:0 0 10px 10px;overflow:hidden;margin-top:-2px">'
        + rows +
        f'</div>'
    )

# ─── Picks cards ─────────────────────────────────────────────────────────────

def build_team_pick(p, ai_txt=""):
    c     = p["confidence"]
    color = conf_color(c)
    form  = form_badges(p.get("stats", {}).get("form"))
    ai_bl = f'<div style="font-size:12px;color:#7dd3fc;margin-top:5px;font-style:italic">🤖 {ai_txt}</div>' if ai_txt else ""
    return (
        f'<div style="background:#1e293b;border-radius:10px;padding:14px 16px;'
        f'margin-bottom:10px;border-left:4px solid {color}">'
        f'<div style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:8px">'
        f'<div style="display:flex;align-items:center;flex-wrap:wrap;gap:4px">'
        f'<span style="color:{color};font-weight:700;font-size:15px">{p["label"]}</span>'
        f'{cote_badge(p.get("cote"))}'
        f'<span style="color:#475569;font-size:12px;margin-left:6px">{p["type"]}</span>'
        f'</div>'
        f'<div style="background:{color};color:#000;font-weight:bold;border-radius:20px;padding:4px 12px;font-size:14px">{c}%</div>'
        f'</div>'
        f'<div style="color:#94a3b8;font-size:13px;margin-top:8px">{p["reasoning"]}</div>'
        f'<div style="margin-top:6px">{form}</div>'
        f'{ai_bl}'
        f'</div>'
    )

def build_player_pick(p, ai_analyses=None):
    c      = p["confidence"]
    color  = player_conf_color(c)
    type_  = p["type"]
    is_sub = p.get("is_sub", False)
    icon   = {"Buteur":"⚽","Passeur décisif":"🎯","Joueur décisif":"⭐"}.get(type_,"🔵")
    pos_b  = pos_badge(p.get("position",""))
    ai_key = f"{p['player']}_{type_}"
    ai_txt = (ai_analyses or {}).get(ai_key, "")
    ai_bl  = f'<div style="font-size:12px;color:#7dd3fc;margin-top:5px;font-style:italic">🤖 {ai_txt}</div>' if ai_txt else ""
    sub_b  = ('<span style="background:#78350f;color:#fbbf24;border:1px solid #f59e0b;'
              'border-radius:4px;padding:1px 6px;font-size:10px;font-weight:700;margin-left:5px">'
              '🔄 Peut ne pas démarrer</span>') if is_sub else ""
    return (
        f'<div style="background:#162032;border-radius:8px;padding:12px 14px;'
        f'margin-bottom:8px;border-left:3px solid {color}">'
        f'<div style="display:flex;justify-content:space-between;align-items:flex-start;flex-wrap:wrap;gap:8px">'
        f'<div style="flex:1">'
        f'<div style="display:flex;align-items:center;flex-wrap:wrap;gap:3px;margin-bottom:4px">'
        f'<span>{icon}</span>{pos_b}'
        f'<span style="color:{color};font-weight:600;font-size:14px">{p["label"]}</span>'
        f'<span style="color:#475569;font-size:12px;margin-left:4px">{type_}</span>'
        f'{sub_b}'
        f'</div>'
        f'<div style="color:#64748b;font-size:12px">{p["reasoning"]}</div>'
        f'{ai_bl}'
        f'</div>'
        f'<div style="background:{color};color:#000;font-weight:bold;border-radius:16px;padding:3px 10px;font-size:13px">{c}%</div>'
        f'</div>'
        f'</div>'
    )

def build_fun_pick(p):
    c     = p["confidence"]
    cote  = p.get("cote")
    cb    = cote_badge(cote) if cote else ""
    return (
        f'<div style="background:#1a1a2e;border-radius:8px;padding:12px 14px;'
        f'margin-bottom:8px;border:1px solid #4c1d95">'
        f'<div style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:8px">'
        f'<div style="flex:1">'
        f'<div style="display:flex;align-items:center;flex-wrap:wrap;gap:4px;margin-bottom:4px">'
        f'<span style="color:#a78bfa;font-weight:700;font-size:14px">{p["label"]}</span>'
        f'{cb}<span style="color:#7c3aed;font-size:11px;margin-left:5px">Paris fun</span>'
        f'</div>'
        f'<div style="color:#9f7aea;font-size:12px;font-style:italic">{p["reasoning"]}</div>'
        f'</div>'
        f'<div style="background:#7c3aed;color:#fff;font-weight:bold;border-radius:16px;padding:3px 10px;font-size:13px">{c}%</div>'
        f'</div>'
        f'</div>'
    )

def build_match_card(m, team_ai_map, player_ai_map, pstats=None):
    home      = m["home"]
    away      = m["away"]
    mid_safe  = str(m["match_id"]).replace("-","")
    dt        = format_datetime(m.get("start_ts"))

    # Picks équipe
    team_html = "".join(build_team_pick(p, team_ai_map.get(p["label"],"")) for p in m["picks"])

    # Paris fun
    fun_html = ""
    if m.get("fun_picks"):
        fun_cards = "".join(build_fun_pick(p) for p in m["fun_picks"])
        fun_html  = (
            '<div style="margin-top:12px;padding-top:12px;border-top:1px solid #1e293b">'
            '<div style="color:#7c3aed;font-size:11px;font-weight:700;text-transform:uppercase;'
            'letter-spacing:1px;margin-bottom:8px">🎲 Paris fun (cote ≥ 2.0)</div>'
            + fun_cards + '</div>'
        )

    # Props joueurs
    all_pp = [(home,"🏠",m.get("home_players",[])), (away,"✈️",m.get("away_players",[]))]
    player_html = ""
    if any(pp for _,_,pp in all_pp):
        sects = ""
        for tname, icon, pp in all_pp:
            if not pp: continue
            cards = "".join(build_player_pick(p, player_ai_map) for p in pp)
            sects += (f'<div style="margin-bottom:12px">'
                      f'<div style="color:#475569;font-size:11px;font-weight:600;'
                      f'text-transform:uppercase;letter-spacing:1px;margin-bottom:6px">'
                      f'{icon} {tname}</div>{cards}</div>')
        player_html = (
            '<div style="margin-top:14px;padding-top:14px;border-top:1px solid #1e293b">'
            '<div style="color:#64748b;font-size:11px;font-weight:700;text-transform:uppercase;'
            'letter-spacing:1px;margin-bottom:10px">⚽ Props Joueurs</div>'
            + sects + '</div>'
        )

    # Stats panel
    ps    = pstats or {}
    form  = (m.get("_form") or {})
    stats_panel = build_stats_panel(
        mid_safe, home, away, form,
        ps.get("home_recent", {}),
        ps.get("away_recent", {}),
        ps.get("home_team_stats", {}),
        ps.get("away_team_stats", {}),
        ps.get("home", []),
        ps.get("away", []),
    )

    return (
        f'<div style="background:#0f172a;border-radius:14px;margin-bottom:18px;'
        f'box-shadow:0 4px 20px rgba(0,0,0,0.4);overflow:hidden">'
        # Header cliquable
        f'<div onclick="toggleStats(\'{mid_safe}\')" style="padding:18px 20px 14px;cursor:pointer;'
        f'user-select:none;transition:background .15s" '
        f'onmouseover="this.style.background=\'#1e293b\'" '
        f'onmouseout="this.style.background=\'\'">'
        f'<div style="display:flex;justify-content:space-between;align-items:flex-start;flex-wrap:wrap;gap:8px">'
        f'<div>'
        f'<div style="color:#475569;font-size:11px;text-transform:uppercase;letter-spacing:1px;font-weight:600">{m["league"]}</div>'
        f'<div style="color:#f1f5f9;font-size:19px;font-weight:700;margin-top:3px">'
        f'{home} <span style="color:#334155">vs</span> {away}</div>'
        f'</div>'
        f'<div style="display:flex;align-items:center;gap:10px">'
        f'<div style="color:#475569;font-size:13px">🕐 {dt}</div>'
        f'<div id="arrow-{mid_safe}" style="color:#334155;font-size:16px;transition:transform .2s">▼</div>'
        f'</div>'
        f'</div>'
        f'</div>'
        # Stats panel (caché)
        + stats_panel +
        # Picks
        f'<div style="padding:0 20px 20px">'
        + team_html + fun_html + player_html +
        f'</div>'
        f'</div>'
    )

# ─── HTML complet ─────────────────────────────────────────────────────────────

def build_html(matches, team_ai, player_ai, pstats_data):
    team_ai_map = {item.get("pick",""):item.get("analyse","") for item in (team_ai or [])}
    now         = datetime.now().strftime("%d/%m/%Y %H:%M")

    days = {}
    for m in matches:
        days.setdefault(day_label(m.get("start_ts")), []).append(m)

    tab_buttons = tab_contents = ""
    for i, (day, day_matches) in enumerate(days.items()):
        sid = f"day{i}"
        active_btn = "active" if i == 0 else ""
        active_div = "block"  if i == 0 else "none"
        tab_buttons += (
            f'<button class="tab-btn {active_btn}" onclick="showDay(\'{sid}\')" id="btn-{sid}">'
            f'{day} <span class="tab-count">{len(day_matches)}</span></button>'
        )
        cards = ""
        for m in day_matches:
            mid_str = str(m["match_id"])
            ps      = pstats_data.get(mid_str, {})
            # Inject form data into match for stats panel
            # _form already injected in main()
            cards += build_match_card(m, team_ai_map, player_ai, ps)
        tab_contents += f'<div id="{sid}" style="display:{active_div}">{cards}</div>'

    total_t = sum(len(m["picks"]) for m in matches)
    total_p = sum(len(m.get("home_players",[])) + len(m.get("away_players",[])) for m in matches)

    return f'''<!DOCTYPE html>
<html lang="fr">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>Sports Picks — {now}</title>
<style>
  *{{box-sizing:border-box;margin:0;padding:0}}
  body{{background:#020617;color:#f1f5f9;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;padding:20px 16px}}
  .container{{max-width:820px;margin:0 auto}}
  h1{{font-size:24px;font-weight:800;margin-bottom:4px}}
  .meta{{color:#475569;font-size:13px;margin-bottom:20px}}
  .legend{{background:#0f172a;border-radius:10px;padding:11px 16px;margin-bottom:20px;font-size:12px;color:#64748b;border:1px solid #1e293b;line-height:2}}
  .tabs{{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:24px}}
  .tab-btn{{background:#1e293b;color:#94a3b8;border:1px solid #334155;border-radius:20px;padding:8px 16px;font-size:13px;font-weight:600;cursor:pointer;transition:all .2s}}
  .tab-btn:hover{{background:#334155;color:#f1f5f9}}
  .tab-btn.active{{background:#3b82f6;color:#fff;border-color:#3b82f6}}
  .tab-count{{background:rgba(255,255,255,0.2);border-radius:10px;padding:1px 7px;font-size:11px;margin-left:5px}}
  footer{{color:#1e293b;font-size:11px;text-align:center;margin-top:30px;padding-top:20px;border-top:1px solid #0f172a}}
</style>
</head>
<body>
<div class="container">
  <h1>⚽ Sports Picks</h1>
  <div class="meta">Généré le {now} · {len(matches)} matchs · {total_t} picks équipe · {total_p} props joueurs</div>
  <div class="legend">
    <b>▼ Cliquer sur un match</b> pour voir les stats détaillées (forme, buts, tirs, BTTS) ·
    <b>📊 Cote</b> = bookmaker Sofascore ·
    <b>xG</b> = buts attendus ·
    <span style="color:#22c55e">■</span>≥80%
    <span style="color:#84cc16">■</span>≥68%
    <span style="color:#f59e0b">■</span>≥55%
  </div>
  <div class="tabs">{tab_buttons}</div>
  {tab_contents}
  <footer>Données Sofascore · Algorithme + IA · À titre informatif uniquement</footer>
</div>
<script>
function showDay(id){{
  document.querySelectorAll('[id^="day"]').forEach(el=>el.style.display='none');
  document.querySelectorAll('.tab-btn').forEach(btn=>btn.classList.remove('active'));
  document.getElementById(id).style.display='block';
  document.getElementById('btn-'+id).classList.add('active');
}}
function toggleForm(id){{
  var el = document.getElementById('form-'+id);
  if(el) el.style.display = el.style.display==='none' ? 'block' : 'none';
}}
function toggleStats(id){{
  var panel = document.getElementById('stats-'+id);
  var arrow = document.getElementById('arrow-'+id);
  if(panel.style.display==='none'){{
    panel.style.display='block';
    arrow.style.transform='rotate(180deg)';
    arrow.style.color='#3b82f6';
  }} else {{
    panel.style.display='none';
    arrow.style.transform='';
    arrow.style.color='#334155';
  }}
}}
</script>
</body>
</html>'''

# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    print("⚙️  Calcul des picks...")
    matches = picks_engine.run()
    if not matches:
        print("❌ Aucun pick. Lance d'abord scraper.py")
        return

    # Charge player_stats pour les stats panels
    try:
        with open("data/player_stats.json", encoding="utf-8") as f:
            pstats_data = json.load(f)
    except Exception:
        pstats_data = {}

    # Charge matches.json pour récupérer pre_match_form
    try:
        with open("data/matches.json", encoding="utf-8") as f:
            raw_matches = {str(m["id"]): m for m in json.load(f)}
    except Exception:
        raw_matches = {}

    # Injecte la form dans chaque match output
    for m in matches:
        mid = str(m["match_id"])
        rm  = raw_matches.get(mid, {})
        m["_form"] = rm.get("pre_match_form") or {}

    team_ai   = []
    player_ai = {}

    if ANTHROPIC_API_KEY:
        top3 = matches[:3]
        summary = "\n".join(
            f"- {m['home']} vs {m['away']} ({m['league']}) : "
            f"{m['top_pick']['label']} ({m['top_pick']['confidence']}%)"
            for m in top3
        )
        print("\n🤖 Analyse équipe...")
        team_ai = ask_claude_teams(summary)
        print(f"  ✅ {len(team_ai)} analyses")

        all_pp = [(m["home"], m["away"], m["league"], p)
                  for m in matches
                  for p in (m.get("home_players",[]) + m.get("away_players",[]))]
        all_pp.sort(key=lambda x: x[3]["confidence"], reverse=True)
        if all_pp[:5]:
            ctx = "\n".join(
                f"- {p['player']} ({p['type']}) | {h} vs {a} ({lg}) | {p['reasoning']}"
                for h, a, lg, p in all_pp[:5]
            )
            print("\n🤖 Analyse joueurs...")
            player_ai = ask_claude_players(ctx)
            print(f"  ✅ {len(player_ai)} analyses")
    else:
        print("  ℹ️  Pas de clé API → IA désactivée")

    print("\n🌐 Génération du site...")
    html = build_html(matches, team_ai, player_ai, pstats_data)
    with open("index.html", "w", encoding="utf-8") as f:
        f.write(html)
    print("✅ index.html prêt — cliquer sur un match pour voir les stats")

if __name__ == "__main__":
    main()