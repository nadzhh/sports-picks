"""
generate_site.py — v6
- Clic sur un match → panneau stats détaillées (forme, buts, tirs, BTTS)
- Analyse IA sur top picks
- Paris fun en violet
"""

import json, os, urllib.request
from datetime import datetime, timedelta
try:
    from zoneinfo import ZoneInfo
    TZ_PARIS = ZoneInfo("Europe/Paris")
except Exception:
    TZ_PARIS = None


def _now_paris():
    """datetime.now() en heure de Paris (Europe/Paris) - utile en CI ou le runner est UTC."""
    if TZ_PARIS:
        return datetime.now(TZ_PARIS)
    return datetime.now()
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


def hit_rate_color(pct):
    """Traffic-light Outlier-style pour les hit rates L10/L20."""
    if pct >= 80: return "#4ade80"   # vert vif
    if pct >= 65: return "#22c55e"   # vert
    if pct >= 51: return "#84cc16"   # vert-jaune
    if pct >= 50: return "#94a3b8"   # neutre
    if pct >= 30: return "#f59e0b"   # orange
    if pct >= 15: return "#ef4444"   # rouge
    return "#dc2626"                  # rouge fonce


def stake_pill(stake_label, kelly_pct):
    """Pastille Kelly sizing affichee pres de la cote."""
    if not stake_label:
        return ""
    color = "#22c55e"
    if "0.25" in stake_label or "0.5" in stake_label: color = "#94a3b8"
    elif "1.5" in stake_label or "2" in stake_label:  color = "#4ade80"
    title = f"Kelly fractional 1/4 - {kelly_pct} pct bankroll"
    return (
        f'<span title="{title}" '
        f'style="background:{color};color:#fff;font-size:10px;font-weight:700;'
        f'padding:2px 6px;border-radius:10px;margin-left:6px">{stake_label}</span>'
    )


_PROP_UNIT = {"PTS":"pts", "REB":"reb", "AST":"ast", "FG3M":"3PM",
              "PRA":"PRA", "PR":"pts+reb", "PA":"pts+ast"}

def splits_chip(p):
    """Affiche H2H + Venue stats (style Outlier) avec label explicite."""
    parts = []
    unit = _PROP_UNIT.get(p.get("prop", ""), "")
    if p.get("h2h_avg") is not None and (p.get("h2h_n") or 0) >= 3:
        opp = p.get("opp_abbr", "?")
        parts.append(
            f'<span title="Moyenne du joueur sur ses {p["h2h_n"]} derniers matchs '
            f'face a cette equipe ({opp})" style="background:#1e293b;color:#94a3b8;'
            f'font-size:11px;padding:2px 6px;border-radius:4px">'
            f'Moy. vs {opp} ({p["h2h_n"]} matchs) : <b style="color:#e2e8f0">{p["h2h_avg"]} {unit}</b></span>'
        )
    if p.get("venue_avg") is not None and (p.get("venue_n") or 0) >= 5:
        venue = p.get("venue", "")
        icon = "🏠" if venue == "domicile" else "✈️"
        venue_label = "a domicile" if venue == "domicile" else "a l'exterieur"
        parts.append(
            f'<span title="Moyenne du joueur sur ses {p["venue_n"]} derniers matchs '
            f'joues {venue_label}" style="background:#1e293b;color:#94a3b8;font-size:11px;'
            f'padding:2px 6px;border-radius:4px">'
            f'{icon} Moy. {venue_label} ({p["venue_n"]} matchs) : '
            f'<b style="color:#e2e8f0">{p["venue_avg"]} {unit}</b></span>'
        )
    if not parts: return ""
    return f'<div style="display:flex;gap:6px;flex-wrap:wrap;margin-top:4px">{"".join(parts)}</div>'

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


import html as _html

def _push_button(text):
    """Petit bouton 📲 qui envoie `text` au bot Telegram (token en localStorage)."""
    if not text: return ""
    esc = _html.escape(text, quote=True)
    return (
        f'<button class="tg-push-btn" type="button" data-text="{esc}" '
        f'onclick="event.stopPropagation();pushTelegram(this)" '
        f'title="Envoyer ce pick sur Telegram" '
        f'style="background:#0088cc;color:#fff;border:none;border-radius:6px;'
        f'padding:3px 8px;font-size:12px;font-weight:700;cursor:pointer;'
        f'margin-left:6px;line-height:1">📲</button>'
    )


def _format_push_team(p, home, away, league=""):
    """Texte HTML du push Telegram pour un team pick."""
    cote = p.get("cote") or p.get("cote_min")
    cote_str = f" @ <b>{cote:.2f}</b>" if cote else ""
    lg = f" ({league})" if league else ""
    reasoning = (p.get("reasoning") or "").replace("\n", " · ")[:200]
    conf = p.get("confidence", "?")
    return (
        f"🎯 <b>PICK SPORT-PICKS</b>\n\n"
        f"⚽ <b>{home}</b> vs <b>{away}</b>{lg}\n\n"
        f"📌 <b>{p.get('label','?')}</b>{cote_str}\n"
        f"📊 Confiance : <b>{conf}%</b>\n"
        f"💡 {reasoning}"
    )


def _format_push_player_foot(p, home, away, league=""):
    """Texte HTML du push Telegram pour un player pick foot."""
    cote = p.get("cote")
    book = (p.get("book") or "").upper()
    cote_str = f" @ <b>{book} {cote:.2f}</b>" if cote else ""
    lg = f" ({league})" if league else ""
    reasoning = (p.get("reasoning") or "").replace("\n", " · ")[:200]
    conf = p.get("confidence", "?")
    type_ = p.get("type", "")
    return (
        f"🎯 <b>PICK SPORT-PICKS</b>\n\n"
        f"⚽ <b>{home}</b> vs <b>{away}</b>{lg}\n\n"
        f"📌 <b>{p.get('label','?')}</b> ({type_}){cote_str}\n"
        f"📊 Confiance : <b>{conf}%</b>\n"
        f"💡 {reasoning}"
    )


def _format_push_nba(p, game):
    """Texte HTML du push Telegram pour un pick NBA."""
    home = game.get("home_team", "?")
    away = game.get("away_team", "?")
    cote = p.get("real_cote") or p.get("cote_min")
    book = (p.get("book") or "").upper() or ""
    cote_str = f" @ <b>{book + ' ' if book else ''}{cote:.2f}</b>" if cote else ""
    edge = p.get("edge")
    edge_str = f" · edge <b>+{edge}%</b>" if edge else ""
    conf = p.get("confidence", "?")
    s = p.get("stats", {})
    stats_line = ""
    if s:
        stats_line = f"📊 L5: {s.get('L5','?')} · L10: {s.get('L10','?')} · S: {s.get('Saison','?')} → attendu {s.get('mu','?')}"
    hit_l10 = p.get("hit_l10")
    hit_line = f"\n📈 L10 {hit_l10} ({p.get('hit_l10_pct','?')}%)" if hit_l10 else ""
    return (
        f"🎯 <b>PICK SPORT-PICKS NBA</b>\n\n"
        f"🏀 <b>{away}</b> @ <b>{home}</b>\n\n"
        f"📌 <b>{p.get('label','?')}</b>{cote_str}{edge_str}\n"
        f"🎯 Confiance : <b>{conf}%</b>{hit_line}\n"
        f"{stats_line}"
    )

def pos_badge(pos):
    c = {"F":"#ef4444","M":"#3b82f6","D":"#22c55e"}.get(pos,"#6b7280")
    l = {"F":"ATT","M":"MIL","D":"DEF"}.get(pos, pos)
    return f'<span style="background:{c}22;color:{c};border:1px solid {c};border-radius:4px;padding:1px 5px;font-size:10px;font-weight:700;margin-right:3px">{l}</span>'

# ─── Stats panel (données détaillées au clic) ─────────────────────────────────

def build_stats_panel(mid_safe, home, away, form_data, home_recent, away_recent, home_ts, away_ts, home_players=None, away_players=None, home_odds_hist=None, away_odds_hist=None, home_l5=None, away_l5=None, home_dec_l5=None, home_dec_l10=None, away_dec_l5=None, away_dec_l10=None, lineup=None, h2h_details=None):
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
    src_h = f"saison {h_n}m" if h_n else "saison"
    src_a = f"saison {a_n}m" if a_n else "saison"

    # Buts
    h_gf   = safe(hr.get("goals_for_pm")  or hts.get("goals_pm"))
    h_ga   = safe(hr.get("goals_ag_pm")   or hts.get("conceded_pm"))
    h_tot  = safe(hr.get("total_goals_pm"))
    a_gf   = safe(ar.get("goals_for_pm")  or ats.get("goals_pm"))
    a_ga   = safe(ar.get("goals_ag_pm")   or ats.get("conceded_pm"))
    a_tot  = safe(ar.get("total_goals_pm"))

    # BTTS L10 (vraie L10 calculée depuis les fixtures)
    def fmt_btts(r):
        c = r.get("btts_count"); n = r.get("btts_n", 0); rate = r.get("btts_rate", 0)
        if not n or c is None: return "—"
        return f"{c}/{n} ({rate}%)"
    h_btts = fmt_btts(hr)
    a_btts = fmt_btts(ar)

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
            f'<div style="display:grid;grid-template-columns:1fr 1.5fr 1fr;'
            f'padding:9px 14px;background:{bg};align-items:center;gap:6px;border-bottom:1px solid #1a2540">'
            f'<div style="text-align:right;color:#f1f5f9;font-weight:700;font-size:14px">{hv}</div>'
            f'<div style="text-align:center;color:#cbd5e1;font-size:13px;font-weight:500;padding:2px 6px">{label}</div>'
            f'<div style="color:#f1f5f9;font-size:14px;font-weight:700">{av}</div>'
            f'</div>'
        )

    def bar_row(label, h_val, a_val, decimals=1, alt=False, fmt_pct=False, sub_text="", lower_is_better=False):
        """
        Ligne FotMob-style: [h_value]  Label  [a_value]
        Le dominant a un PILL doré (style FotMob).
        Optimisé pour fond sombre (haute contraste).
        """
        bg = "#0a1628" if alt else "#0d1b2e"

        def _to_f(v):
            try: return float(v) if v is not None and v != "" else None
            except: return None

        h_f = _to_f(h_val)
        a_f = _to_f(a_val)

        def _fmt(v):
            if v is None: return "—"
            if fmt_pct: return f"{int(round(v))}%"
            return f"{v:.{decimals}f}"

        # Determine dominance
        h_dom = a_dom = False
        if h_f is not None and a_f is not None and h_f != a_f:
            if not lower_is_better:
                if h_f > a_f: h_dom = True
                else: a_dom = True
            else:
                if h_f < a_f: h_dom = True
                else: a_dom = True

        def make_val(val, is_dom):
            if val == "—":
                return f'<span style="color:#475569;font-size:16px">—</span>'
            color = "#fbbf24" if is_dom else "#f1f5f9"
            weight = "800" if is_dom else "700"
            return f'<span style="color:{color};font-size:17px;font-weight:{weight}">{val}</span>'

        sub = f'<div style="color:#64748b;font-size:10px;text-align:center;margin-top:2px">{sub_text}</div>' if sub_text else ""

        return (
            f'<div style="padding:9px 14px;background:{bg};border-bottom:1px solid #1a2540">'
            f'<div style="display:grid;grid-template-columns:1fr 1.5fr 1fr;align-items:center;gap:6px">'
            f'<div style="text-align:right">{make_val(_fmt(h_f), h_dom)}</div>'
            f'<div style="text-align:center;color:#cbd5e1;font-size:13px;font-weight:500;padding:0 4px">{label}</div>'
            f'<div style="text-align:left">{make_val(_fmt(a_f), a_dom)}</div>'
            f'</div>'
            f'{sub}'
            f'</div>'
        )

    def tri_cell(l5, l10, season, decimals=1, side="home", dominant=False, weaker=False):
        """
        Cellule style Sofascore: valeur simple, couleur dorée si dominant.
        Plus de pills géants - texte uniforme.
        """
        def fmt(v):
            if v is None or v == "" or v == 0: return "—"
            try: return f"{float(v):.{decimals}f}"
            except: return str(v)

        # Tendance
        arrow = ""
        ref = season if season not in (None, 0) else l10
        if l5 not in (None, 0) and ref not in (None, 0):
            try:
                diff = (float(l5) - float(ref)) / float(ref)
                if diff > 0.10:    arrow = "<span style='color:#22c55e;font-size:11px;margin-left:3px'>↑</span>"
                elif diff < -0.10: arrow = "<span style='color:#ef4444;font-size:11px;margin-left:3px'>↓</span>"
            except: pass

        s_html = f"S {fmt(season)}" if season is not None else ""
        sep_l10 = f"L10 {fmt(l10)}" if l10 is not None else ""
        sub_parts = [p for p in [sep_l10, s_html] if p]
        sub = " · ".join(sub_parts) if sub_parts else ""

        # Couleurs simples (sans pill) - Sofascore style
        if dominant:
            color = "#fbbf24"
            weight = "800"
        elif weaker:
            color = "#94a3b8"
            weight = "600"
        else:
            color = "#f1f5f9"
            weight = "700"

        align = "right" if side == "home" else "left"
        return (
            f'<div style="text-align:{align};line-height:1.4">'
            f'<span style="color:{color};font-size:17px;font-weight:{weight}">{fmt(l5)}{arrow}</span>'
            f'<div style="color:#94a3b8;font-size:11px;margin-top:3px;font-weight:500">{sub}</div>'
            f'</div>'
        )

    def detect_dominance(h_l5, a_l5, threshold_ratio=1.30, min_abs_gap=2.0, inverted=False):
        """
        Detecte si une equipe domine fortement l'autre sur une stat.
        Retourne (home_dominant, away_dominant) bool tuple.
        threshold_ratio: ratio min (1.30 = 30% sup)
        min_abs_gap: ecart absolu minimum
        inverted: True si "moins = mieux" (ex: buts concedes)
        """
        try:
            h = float(h_l5) if h_l5 not in (None, 0) else None
            a = float(a_l5) if a_l5 not in (None, 0) else None
        except: return (False, False)
        if h is None or a is None: return (False, False)
        gap = abs(h - a)
        if gap < min_abs_gap: return (False, False)
        if min(h, a) == 0: return (False, False)
        ratio = max(h, a) / min(h, a)
        if ratio < threshold_ratio: return (False, False)
        # Le plus grand domine, sauf si inverted (moins = mieux)
        if not inverted:
            return (h > a, a > h)
        else:
            return (h < a, a < h)

    def tri_row(label, hr_dict, ar_dict, key_l5, key_l10, key_season, decimals=1, alt=False, inverted=False, abs_gap=2.0):
        """Ligne tri-period qui detecte automatiquement la dominance."""
        h_l5, h_l10, h_s = hr_dict.get(key_l5), hr_dict.get(key_l10), hr_dict.get(key_season)
        a_l5, a_l10, a_s = ar_dict.get(key_l5), ar_dict.get(key_l10), ar_dict.get(key_season)
        h_dom, a_dom = detect_dominance(h_l5, a_l5, min_abs_gap=abs_gap, inverted=inverted)

        bg = "#0a1628" if alt else "#0d1b2e"
        # Background plus vif si dominance détectée
        if h_dom or a_dom:
            bg = "#1a1f3a"

        h_cell = tri_cell(h_l5, h_l10, h_s, decimals, "home", dominant=h_dom, weaker=a_dom)
        a_cell = tri_cell(a_l5, a_l10, a_s, decimals, "away", dominant=a_dom, weaker=h_dom)
        return (
            f'<div style="display:grid;grid-template-columns:1fr 1.5fr 1fr;'
            f'padding:9px 14px;background:{bg};align-items:center;gap:6px;border-bottom:1px solid #1a2540">'
            f'<div>{h_cell}</div>'
            f'<div style="text-align:center;color:#cbd5e1;font-size:13px;font-weight:500;padding:2px 6px">{label}</div>'
            f'<div>{a_cell}</div>'
            f'</div>'
        )

    def split_cell(home_val, away_val, home_n, away_n, side, context_is_home, decimals=1):
        """
        Affiche dom/ext avec echantillon. Highlight le pertinent (selon contexte).
        side: 'home'/'away' pour alignement
        context_is_home: True si cette equipe joue a domicile dans CE match
        """
        def fmt(v):
            if v is None or v == "" or v == 0: return "—"
            try: return f"{float(v):.{decimals}f}"
            except: return str(v)

        dom_rel = context_is_home
        ext_rel = not context_is_home

        dom_color = "#fbbf24" if dom_rel else "#475569"
        dom_size = "13px" if dom_rel else "10px"
        dom_weight = "700" if dom_rel else "500"
        ext_color = "#fbbf24" if ext_rel else "#475569"
        ext_size = "13px" if ext_rel else "10px"
        ext_weight = "700" if ext_rel else "500"

        dom_bg = "background:rgba(251,191,36,0.10);padding:1px 5px;border-radius:3px;" if dom_rel else ""
        ext_bg = "background:rgba(251,191,36,0.10);padding:1px 5px;border-radius:3px;" if ext_rel else ""

        # Echantillon entre parentheses
        dom_n_str = f"<span style='color:#475569;font-size:9px'>({home_n}m)</span>" if home_n else ""
        ext_n_str = f"<span style='color:#475569;font-size:9px'>({away_n}m)</span>" if away_n else ""

        align = "right" if side == "home" else "left"
        sep = "<span style='color:#334155;margin:0 4px'>·</span>"
        dom_html = f"<span style='color:{dom_color};font-size:{dom_size};font-weight:{dom_weight};{dom_bg}'>🏠{fmt(home_val)} {dom_n_str}</span>"
        ext_html = f"<span style='color:{ext_color};font-size:{ext_size};font-weight:{ext_weight};{ext_bg}'>✈️{fmt(away_val)} {ext_n_str}</span>"
        return (
            f'<div style="text-align:{align};line-height:1.3;font-size:12px">'
            f'{dom_html}{sep}{ext_html}'
            f'</div>'
        )

    def split_row(label, hr_dict, ar_dict, key_home, key_away, decimals=1, alt=False):
        """Ligne dom/ext: home team domicile vs away team exterieur."""
        bg = "#0a1628" if alt else "#0d1b2e"
        h_n_home = hr_dict.get("shots_home_n", 0)
        h_n_away = hr_dict.get("shots_away_n", 0)
        a_n_home = ar_dict.get("shots_home_n", 0)
        a_n_away = ar_dict.get("shots_away_n", 0)
        h_cell = split_cell(hr_dict.get(key_home), hr_dict.get(key_away),
                            h_n_home, h_n_away,
                            "home", context_is_home=True, decimals=decimals)
        a_cell = split_cell(ar_dict.get(key_home), ar_dict.get(key_away),
                            a_n_home, a_n_away,
                            "away", context_is_home=False, decimals=decimals)
        return (
            f'<div style="display:grid;grid-template-columns:1fr 1.5fr 1fr;'
            f'padding:7px 14px;background:{bg};align-items:center;gap:6px;border-bottom:1px solid #1a2540">'
            f'<div>{h_cell}</div>'
            f'<div style="text-align:center;color:#94a3b8;font-size:11px;font-weight:500;padding:2px 6px">'
            f'{label}<br><span style="font-size:10px;color:#64748b">sur L10</span></div>'
            f'<div>{a_cell}</div>'
            f'</div>'
        )

    def tri_row_sum(label, h_vals, a_vals, decimals=1, alt=False, abs_gap=2.0):
        """tri_row pour valeurs derivees (somme buts marques+concedes)."""
        h_l5, h_l10, h_s = h_vals
        a_l5, a_l10, a_s = a_vals
        h_dom, a_dom = detect_dominance(h_l5, a_l5, min_abs_gap=abs_gap)
        bg = "#0a1628" if alt else "#0d1b2e"
        if h_dom or a_dom:
            bg = "#1a1f3a"
        h_cell = tri_cell(h_l5, h_l10, h_s, decimals, "home", dominant=h_dom, weaker=a_dom)
        a_cell = tri_cell(a_l5, a_l10, a_s, decimals, "away", dominant=a_dom, weaker=h_dom)
        return (
            f'<div style="display:grid;grid-template-columns:1fr 1.5fr 1fr;'
            f'padding:9px 14px;background:{bg};align-items:center;gap:6px;border-bottom:1px solid #1a2540">'
            f'<div>{h_cell}</div>'
            f'<div style="text-align:center;color:#cbd5e1;font-size:13px;font-weight:500;padding:2px 6px">{label}</div>'
            f'<div>{a_cell}</div>'
            f'</div>'
        )

    rows = (
        f'<div style="display:grid;grid-template-columns:1fr 1.5fr 1fr;padding:14px 16px;'
        f'background:#0a1628;border-radius:8px 8px 0 0;margin-bottom:0;align-items:center;border-bottom:1px solid #1e293b">'
        f'<div style="text-align:right;color:#60a5fa;font-weight:800;font-size:17px">{home}</div>'
        f'<div style="text-align:center;color:#94a3b8;font-size:12px;font-weight:700;letter-spacing:1.5px">STATISTIQUES</div>'
        f'<div style="color:#60a5fa;font-weight:800;font-size:17px">{away}</div>'
        f'</div>'
    )

    rows += (
        f'<div style="text-align:center;color:#94a3b8;font-size:11px;'
        f'padding:5px 0 3px;text-transform:uppercase;letter-spacing:1px;'
        f'background:#0d1b2e">📋 CLASSEMENT & FORME</div>'
    )
    rows += row("Classement", f"#{hp}" if hp else "—", f"#{ap}" if ap else "—", True)
    # Forme: list est [newest, ..., oldest]. On reverse pour afficher [ancien -> recent]
    def _form_with_arrow(form_list):
        if not form_list: return "—"
        ordered = list(reversed(form_list[-5:]))  # ancien gauche -> recent droite
        return (
            f'<div style="display:inline-flex;align-items:center;gap:4px">'
            f'<span style="color:#64748b;font-size:9px">←ancien</span>'
            f'{form_badges(ordered)}'
            f'<span style="color:#64748b;font-size:9px">récent→</span>'
            f'</div>'
        )
    rows += row("Forme 5 matchs",
                _form_with_arrow(hf),
                _form_with_arrow(af),
                True)

    # ── COTES vs REALITE (style Sofascore) ────────────────────────────────
    # Recupere les cotes 1X2 depuis form_data (qui contient match_odds via _form)
    # Calcul: cote -> implied prob. Compare au win-rate L10 actuel.
    fr = form_data or {}
    odds_1x2 = fr.get("_odds_1x2") or {}
    h_cote = odds_1x2.get("home")
    a_cote = odds_1x2.get("away")

    # Win rate L10 depuis form (W counts)
    def _wrate(form_list):
        if not form_list: return None
        w = sum(1 for f in form_list if f == "W")
        return round(w / len(form_list) * 100)
    h_wr = _wrate(hf)
    a_wr = _wrate(af)

    def fmt_cote_row(cote, win_rate, team_name):
        if not cote: return None
        try:
            implied = round(100 / float(cote))
        except: return None
        edge = (win_rate or 0) - implied
        if edge > 5:    edge_html = f'<span style="color:#22c55e;font-weight:700">+{edge}%</span> 🎯'
        elif edge > 0:  edge_html = f'<span style="color:#84cc16;font-weight:600">+{edge}%</span>'
        elif edge < -5: edge_html = f'<span style="color:#ef4444;font-weight:600">{edge}%</span>'
        else:           edge_html = f'<span style="color:#64748b">{edge:+d}%</span>'
        wr_str = f"{win_rate}%" if win_rate is not None else "—"
        return (
            f'<div style="padding:9px 14px;background:#0d1b2e;border-bottom:1px solid #1a2540">'
            f'<div style="display:flex;justify-content:space-between;align-items:center">'
            f'<div style="color:#cbd5e1;font-size:13px;font-weight:600">{team_name}</div>'
            f'<div style="display:flex;align-items:center;gap:14px">'
            f'<span style="color:#94a3b8;font-size:12px">Cote <b style="color:#f1f5f9;font-size:14px">{cote}</b> = <b style="color:#94a3b8">{implied}%</b></span>'
            f'<span style="color:#94a3b8;font-size:12px">Forme L5 <b style="color:#f1f5f9;font-size:14px">{wr_str}</b></span>'
            f'<span style="font-size:13px">{edge_html}</span>'
            f'</div>'
            f'</div>'
            f'</div>'
        )

    h_cote_row = fmt_cote_row(h_cote, h_wr, "🏠 " + (home or "?"))
    a_cote_row = fmt_cote_row(a_cote, a_wr, "✈️ " + (away or "?"))
    if h_cote_row or a_cote_row:
        rows += (
            f'<div style="text-align:center;color:#94a3b8;font-size:12px;font-weight:700;'
            f'padding:8px 0 4px;text-transform:uppercase;letter-spacing:1px;'
            f'background:#0d1b2e">💰 Cotes vs Réalité (forme récente)</div>'
        )
        rows += (h_cote_row or "") + (a_cote_row or "")

    # ── COMPARAISON RAPIDE (style FotMob avec barres) ─────────────────────
    # Utilise les valeurs saison (les + stables) ou L10 si saison indispo
    def pick_val(*keys):
        for k in keys:
            v = hr.get(k)
            if v is not None and v != 0: return v
        return None
    def pick_val_a(*keys):
        for k in keys:
            v = ar.get(k)
            if v is not None and v != 0: return v
        return None

    h_goals_for = pick_val("goals_for_season", "goals_for_l10", "goals_for_pm")
    a_goals_for = pick_val_a("goals_for_season", "goals_for_l10", "goals_for_pm")
    h_xg = pick_val("xg_season", "xg_l10")
    a_xg = pick_val_a("xg_season", "xg_l10")
    h_shots_v = pick_val("shots_season", "shots_l10")
    a_shots_v = pick_val_a("shots_season", "shots_l10")
    h_sot_v = pick_val("sot_season", "sot_l10")
    a_sot_v = pick_val_a("sot_season", "sot_l10")
    h_corn = hr.get("corners_pm")
    a_corn = ar.get("corners_pm")
    h_big = hr.get("big_chance_pm")
    a_big = ar.get("big_chance_pm")
    h_poss = hr.get("possession_pct")
    a_poss = ar.get("possession_pct")

    h_conceded = pick_val("goals_ag_season", "goals_ag_l10", "goals_ag_pm")
    a_conceded = pick_val_a("goals_ag_season", "goals_ag_l10", "goals_ag_pm")
    h_opp_shots = hr.get("opp_shots_l10") or hr.get("opp_shots_l5")
    a_opp_shots = ar.get("opp_shots_l10") or ar.get("opp_shots_l5")

    has_compare = any(v is not None for v in [h_goals_for, h_xg, h_shots_v, h_sot_v, h_corn, h_big, h_poss])
    if has_compare:
        # Colonne gauche: possession + buts
        left_col = ""
        if h_poss or a_poss:
            left_col += bar_row("Possession", h_poss, a_poss, decimals=0, alt=True, fmt_pct=True)
        if h_xg or a_xg:
            left_col += bar_row("Buts attendus (xG)", h_xg, a_xg, decimals=2)
        if h_goals_for or a_goals_for:
            left_col += bar_row("Buts marqués/match", h_goals_for, a_goals_for, decimals=2, alt=True)
        if h_conceded or a_conceded:
            left_col += bar_row("Buts concédés/match", h_conceded, a_conceded, decimals=2, lower_is_better=True)

        # Colonne droite: tirs + offensif
        right_col = ""
        if h_shots_v or a_shots_v:
            right_col += bar_row("Tirs/match", h_shots_v, a_shots_v, decimals=1, alt=True)
        if h_sot_v or a_sot_v:
            right_col += bar_row("Tirs cadrés/match", h_sot_v, a_sot_v, decimals=1)
        if h_opp_shots or a_opp_shots:
            right_col += bar_row("Tirs concédés/match", h_opp_shots, a_opp_shots, decimals=1, alt=True, lower_is_better=True)
        if h_big or a_big:
            right_col += bar_row("Grosses occasions/match", h_big, a_big, decimals=2)
        if h_corn or a_corn:
            right_col += bar_row("Corners/match", h_corn, a_corn, decimals=1, alt=True)

        rows += (
            f'<div style="text-align:center;color:#94a3b8;font-size:12px;font-weight:700;'
            f'padding:8px 0 4px;text-transform:uppercase;letter-spacing:1px;'
            f'background:#0d1b2e;margin-top:2px">📊 Comparaison rapide (saison)</div>'
        )
        rows += (
            f'<div style="display:grid;grid-template-columns:1fr 1fr;gap:1px;background:#1e293b">'
            f'<div style="background:#0a1628">{left_col}</div>'
            f'<div style="background:#0a1628">{right_col}</div>'
            f'</div>'
        )

    # ── Detail des 5 derniers matchs (FotMob) ─────────────────────
    h_l5 = home_l5 or []
    a_l5 = away_l5 or []

    def match_rows_l5(l5_list, side):
        if not l5_list:
            return '<span style="color:#475569">—</span>'
        html = '<div style="font-size:13px">'
        for d in l5_list:
            r       = d.get("result", "?")
            col     = {"W": "#22c55e", "D": "#f59e0b", "L": "#ef4444"}.get(r, "#6b7280")
            loc     = "🏠" if d.get("is_home") else "✈️"
            opp     = d.get("opponent", "?")
            opp_rank = d.get("opp_rank")  # rang de l'adversaire dans son championnat
            score   = d.get("score", "")
            gf      = d.get("gf"); ga = d.get("ga")
            score_txt = f"{gf}-{ga}" if gf is not None and ga is not None else score
            # Date formatee
            date_iso = d.get("date", "")
            date_str = ""
            if date_iso:
                try:
                    from datetime import datetime as _dt
                    date_str = _dt.fromisoformat(date_iso.replace("Z","+00:00")).strftime("%d/%m")
                except: date_str = date_iso[:10]

            # Tirs / SoT / xG pour CE match
            t_shots  = d.get("team_shots")
            t_sot    = d.get("team_sot")
            t_xg     = d.get("team_xg")
            o_shots  = d.get("opp_shots")
            o_sot    = d.get("opp_sot")
            valid    = d.get("data_valid", True)
            stats_line = ""
            if not valid:
                stats_line = (
                    f'<div style="font-size:10px;color:#94a3b8;margin-top:2px;padding:2px 6px;'
                    f'background:rgba(239,68,68,0.10);border-left:2px solid #ef4444;border-radius:3px">'
                    f'⚠️ stats tirs indisponibles (conflit URL FotMob — match exclu de la moyenne)'
                    f'</div>'
                )
            elif t_shots is not None or t_sot is not None:
                def _fmt(v, dec=0):
                    if v is None: return "?"
                    try: return f"{float(v):.{dec}f}"
                    except: return str(v)
                stats_line = (
                    f'<div style="font-size:12px;color:#94a3b8;margin-top:4px;padding:0 8px;line-height:1.5">'
                    f'<span style="color:#60a5fa;font-weight:600">🎯 {_fmt(t_shots)} tirs · {_fmt(t_sot)} cadrés</span> · '
                    f'<span style="color:#a78bfa;font-weight:600">xG {_fmt(t_xg,2)}</span>'
                    f'<span style="color:#64748b"> · adv: {_fmt(o_shots)} tirs · {_fmt(o_sot)} cadrés</span>'
                    f'</div>'
                )

            # Badge classement adversaire (qualite contexte)
            opp_rank_badge = ""
            if opp_rank is not None:
                if opp_rank <= 5:    rk_color, rk_label = "#fbbf24", "TOP"
                elif opp_rank <= 10: rk_color, rk_label = "#60a5fa", "MID"
                else:                rk_color, rk_label = "#94a3b8", "LOW"
                opp_rank_badge = (
                    f'<span style="background:{rk_color};color:#0a1628;border-radius:4px;'
                    f'padding:2px 6px;font-size:11px;font-weight:700;margin-left:6px">'
                    f'#{opp_rank} {rk_label}</span>'
                )

            # Liste buteurs (notre equipe) + buteurs adverses
            our_goals = d.get("goals", []) or []
            opp_goals = d.get("opp_goals", []) or []

            scorers_html = ""
            if our_goals or opp_goals:
                lines = []
                for g in our_goals:
                    s = f"<span style='color:#22c55e;font-weight:600'>&#9679; {g.get('minute', '?')}\\' {g.get('scorer','?')}</span>"
                    if g.get('assist'):
                        s += f"<span style='color:#94a3b8'> (p. {g['assist']})</span>"
                    if g.get('ownGoal'):
                        s += "<span style='color:#ef4444'> CSC</span>"
                    lines.append(s)
                for g in opp_goals:
                    s = f"<span style='color:#ef4444;font-weight:600'>&#9679; {g.get('minute', '?')}\\' {g.get('scorer','?')}</span>"
                    if g.get('assist'):
                        s += f"<span style='color:#94a3b8'> (p. {g['assist']})</span>"
                    lines.append(s)
                ta = "right" if side == "home" else "left"
                pad = "padding-right:24px" if side == "home" else "padding-left:24px"
                scorers_html = (
                    f'<div style="font-size:12px;line-height:1.7;{pad};text-align:{ta};margin-top:4px">'
                    + "<br>".join(lines)
                    + '</div>'
                )

            # Ligne header du mini-match
            ta = "right" if side == "home" else "left"
            flex_dir = "row-reverse" if side == "home" else "row"
            date_html = f'<span style="color:#94a3b8;font-size:12px;font-weight:500">{date_str}</span>' if date_str else ""
            html += (
                f'<div style="padding:10px 4px 8px;border-bottom:1px solid #1e2940">'
                f'<div style="display:flex;flex-direction:{flex_dir};align-items:center;gap:8px">'
                f'<span style="background:{col};color:#fff;border-radius:4px;padding:3px 9px;'
                f'font-size:13px;font-weight:bold;min-width:22px;text-align:center">{r}</span>'
                f'{date_html}'
                f'<span style="color:#94a3b8;font-size:14px">{loc}</span>'
                f'<span style="color:#e2e8f0;font-size:14px;font-weight:600;flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;text-align:{ta}">'
                f'{opp}{opp_rank_badge}</span>'
                f'<span style="color:#f1f5f9;font-weight:800;font-size:15px">{score_txt}</span>'
                f'</div>'
                + stats_line +
                scorers_html +
                f'</div>'
            )
        html += '</div>'
        return html

    if h_l5 or a_l5:
        rows += (
            f'<div style="text-align:center;color:#94a3b8;font-size:11px;'
            f'padding:6px 0 3px;text-transform:uppercase;letter-spacing:1px;'
            f'background:#0d1b2e;margin-top:2px">📅 5 derniers matchs (détail)</div>'
        )
        rows += (
            f'<div style="display:grid;grid-template-columns:1fr 1fr;gap:1px;'
            f'background:#1e293b;padding:1px">'
            f'<div style="background:#0a1628;padding:10px 12px">{match_rows_l5(h_l5, "home")}</div>'
            f'<div style="background:#0a1628;padding:10px 12px">{match_rows_l5(a_l5, "away")}</div>'
            f'</div>'
        )

    # ── Collecte BUTS section dans une variable ────────────────────────
    buts_rows = (
        f'<div style="text-align:center;color:#94a3b8;font-size:12px;font-weight:700;'
        f'padding:8px 0 4px;text-transform:uppercase;letter-spacing:1px;'
        f'background:#0d1b2e">⚽ BUTS</div>'
    )
    buts_rows += tri_row("Buts marqués/match", hr, ar,
                    "goals_for_l5", "goals_for_l10", "goals_for_season",
                    decimals=2, alt=True, abs_gap=0.6)
    buts_rows += tri_row("Buts concédés/match", hr, ar,
                    "goals_ag_l5", "goals_ag_l10", "goals_ag_season",
                    decimals=2, inverted=True, abs_gap=0.5)
    def _sum(a, b):
        if a is None or b is None: return None
        try: return round(float(a) + float(b), 2)
        except: return None
    h_tot_vals = (_sum(hr.get("goals_for_l5"),  hr.get("goals_ag_l5")),
                  _sum(hr.get("goals_for_l10"), hr.get("goals_ag_l10")),
                  _sum(hr.get("goals_for_season"), hr.get("goals_ag_season")))
    a_tot_vals = (_sum(ar.get("goals_for_l5"),  ar.get("goals_ag_l5")),
                  _sum(ar.get("goals_for_l10"), ar.get("goals_ag_l10")),
                  _sum(ar.get("goals_for_season"), ar.get("goals_ag_season")))
    buts_rows += tri_row_sum("Total buts/match", h_tot_vals, a_tot_vals,
                       decimals=2, alt=True, abs_gap=0.8)
    buts_rows += row("Les 2 équipes ont marqué<br><span style='font-size:10px;color:#64748b'>sur les 10 derniers matchs</span>", h_btts, a_btts)

    h_home_gf = safe(hr.get("home_gf_pm")); h_home_ga = safe(hr.get("home_ga_pm")); h_home_n = hr.get("home_n",0)
    h_away_gf = safe(hr.get("away_gf_pm")); h_away_ga = safe(hr.get("away_ga_pm")); h_away_n = hr.get("away_n",0)
    a_home_gf = safe(ar.get("home_gf_pm")); a_home_ga = safe(ar.get("home_ga_pm")); a_home_n = ar.get("home_n",0)
    a_away_gf = safe(ar.get("away_gf_pm")); a_away_ga = safe(ar.get("away_ga_pm")); a_away_n = ar.get("away_n",0)

    if h_home_n > 0 or h_away_n > 0:
        buts_rows += row(
            f"Buts marqués<br><span style='font-size:10px;color:#64748b'>dom / ext</span>",
            f"🏠{h_home_gf} <span style='color:#64748b;font-size:11px'>({h_home_n}m)</span> · ✈️{h_away_gf} <span style='color:#64748b;font-size:11px'>({h_away_n}m)</span>",
            f"🏠{a_home_gf} <span style='color:#64748b;font-size:11px'>({a_home_n}m)</span> · ✈️{a_away_gf} <span style='color:#64748b;font-size:11px'>({a_away_n}m)</span>",
        )
        buts_rows += row(
            f"Buts concédés<br><span style='font-size:10px;color:#64748b'>dom / ext</span>",
            f"🏠{h_home_ga} <span style='color:#64748b;font-size:11px'>({h_home_n}m)</span> · ✈️{h_away_ga} <span style='color:#64748b;font-size:11px'>({h_away_n}m)</span>",
            f"🏠{a_home_ga} <span style='color:#64748b;font-size:11px'>({a_home_n}m)</span> · ✈️{a_away_ga} <span style='color:#64748b;font-size:11px'>({a_away_n}m)</span>",
            True
        )

    # ── Collecte TIRS section dans une variable ────────────────────────
    tirs_rows = (
        f'<div style="text-align:center;color:#94a3b8;font-size:12px;font-weight:700;'
        f'padding:8px 0 4px;text-transform:uppercase;letter-spacing:1px;'
        f'background:#0d1b2e">🎯 TIRS</div>'
    )
    def all_comp_row(label, hr_dict, ar_dict, key_l5_all, key_l10_all, decimals=1, alt=False):
        """Ligne 'toutes competitions' avec L5/L10 toutes comp."""
        bg = "#0a1628" if alt else "#0d1b2e"
        h_n5 = hr_dict.get("all_comp_n_l5", 0)
        h_n10 = hr_dict.get("all_comp_n_l10", 0)
        a_n5 = ar_dict.get("all_comp_n_l5", 0)
        a_n10 = ar_dict.get("all_comp_n_l10", 0)
        def fmt(v):
            if v is None or v == 0: return "—"
            try: return f"{float(v):.{decimals}f}"
            except: return "—"
        def cell(l5, l10, n5, n10, side):
            align = "right" if side == "home" else "left"
            return (
                f'<div style="text-align:{align};line-height:1.3;font-size:11px;color:#94a3b8">'
                f'<span style="color:#a78bfa">L5: {fmt(l5)}</span> '
                f'<span style="color:#64748b">({n5}m)</span>'
                f'<span style="color:#334155;margin:0 4px">·</span>'
                f'<span style="color:#a78bfa">L10: {fmt(l10)}</span> '
                f'<span style="color:#64748b">({n10}m)</span>'
                f'</div>'
            )
        h_cell = cell(hr_dict.get(key_l5_all), hr_dict.get(key_l10_all), h_n5, h_n10, "home")
        a_cell = cell(ar_dict.get(key_l5_all), ar_dict.get(key_l10_all), a_n5, a_n10, "away")
        return (
            f'<div style="display:grid;grid-template-columns:1fr 1.5fr 1fr;'
            f'padding:7px 14px;background:{bg};align-items:center;gap:6px;border-bottom:1px solid #1a2540">'
            f'<div>{h_cell}</div>'
            f'<div style="text-align:center;color:#a78bfa;font-size:11px;font-weight:500;padding:2px 6px">'
            f'{label}<br><span style="color:#64748b;font-size:10px">toutes compétitions</span></div>'
            f'<div>{a_cell}</div>'
            f'</div>'
        )

    tirs_rows += tri_row("Tirs/match", hr, ar,
                    "shots_l5", "shots_l10", "shots_season",
                    decimals=1, alt=True, abs_gap=3.0)
    if hr.get("shots_home") is not None or ar.get("shots_home") is not None:
        tirs_rows += split_row("↳ split dom · ext", hr, ar,
                          "shots_home", "shots_away", decimals=1, alt=True)
    if hr.get("shots_l10_all") or ar.get("shots_l10_all"):
        tirs_rows += all_comp_row("↳ TC", hr, ar, "shots_l5_all", "shots_l10_all", decimals=1, alt=True)

    tirs_rows += tri_row("Tirs cadrés/match", hr, ar,
                    "sot_l5", "sot_l10", "sot_season",
                    decimals=1, abs_gap=1.5)
    if hr.get("sot_home") is not None or ar.get("sot_home") is not None:
        tirs_rows += split_row("↳ split dom · ext", hr, ar,
                          "sot_home", "sot_away", decimals=1)
    if hr.get("sot_l10_all") or ar.get("sot_l10_all"):
        tirs_rows += all_comp_row("↳ TC", hr, ar, "sot_l5_all", "sot_l10_all", decimals=1)

    tirs_rows += tri_row("xG/match", hr, ar,
                    "xg_l5", "xg_l10", "xg_season",
                    decimals=2, alt=True, abs_gap=0.4)
    if hr.get("xg_home") is not None or ar.get("xg_home") is not None:
        tirs_rows += split_row("↳ split dom · ext", hr, ar,
                          "xg_home", "xg_away", decimals=2, alt=True)
    if hr.get("xg_l10_all") or ar.get("xg_l10_all"):
        tirs_rows += all_comp_row("↳ TC", hr, ar, "xg_l5_all", "xg_l10_all", decimals=2, alt=True)

    if hr.get("opp_shots_l5") or ar.get("opp_shots_l5"):
        tirs_rows += tri_row("Tirs concédés/match", hr, ar,
                        "opp_shots_l5", "opp_shots_l10", None,
                        decimals=1, inverted=True, abs_gap=3.0)
        if hr.get("opp_shots_home") is not None or ar.get("opp_shots_home") is not None:
            tirs_rows += split_row("↳ split dom · ext", hr, ar,
                              "opp_shots_home", "opp_shots_away", decimals=1)
        if hr.get("opp_shots_l10_all") or ar.get("opp_shots_l10_all"):
            tirs_rows += all_comp_row("↳ TC", hr, ar, "opp_shots_l5_all", "opp_shots_l10_all", decimals=1)

    # ── Section BUTS + TIRS detaillee MASQUEE par defaut, accessible via bouton ───
    details_id = f"details-{mid_safe}"
    rows += (
        f'<div style="background:#0d1b2e;padding:8px;text-align:center">'
        f'<button onclick="document.getElementById(\'{details_id}\').style.display='
        f'document.getElementById(\'{details_id}\').style.display===\'none\'?\'block\':\'none\'" '
        f'style="background:#1e293b;color:#94a3b8;border:1px solid #334155;border-radius:6px;'
        f'padding:6px 14px;font-size:12px;font-weight:600;cursor:pointer">'
        f'📊 Voir détails L5/L10/Saison + splits dom/ext'
        f'</button>'
        f'</div>'
        f'<div id="{details_id}" style="display:none">'
        f'<div style="display:grid;grid-template-columns:1fr 1fr;gap:1px;background:#1e293b">'
        f'<div style="background:#0a1628">{buts_rows}</div>'
        f'<div style="background:#0a1628">{tirs_rows}</div>'
        f'</div>'
        f'</div>'
    )
    # Bonus: corners, big chances, possession
    h_corn = safe(hr.get("corners_pm"))
    a_corn = safe(ar.get("corners_pm"))
    h_bc   = safe(hr.get("big_chance_pm"))
    a_bc   = safe(ar.get("big_chance_pm"))
    h_poss = safe(hr.get("possession_pct"))
    a_poss = safe(ar.get("possession_pct"))
    # (Corners/Grosses occ/Possession deja dans Comparaison rapide ci-dessus)

    # ── Section H2H (confrontations directes) ─────────────────────────────
    if h2h_details:
        h2h_html = ""
        for h in h2h_details[:5]:
            res = h.get("result_for_home", "?")
            col = {"W":"#22c55e","D":"#f59e0b","L":"#ef4444"}.get(res, "#6b7280")
            date_str = ""
            d = h.get("date","")
            if d:
                try:
                    from datetime import datetime as _dt
                    date_str = _dt.fromisoformat(d.replace("Z","+00:00")).strftime("%d/%m/%Y")
                except: date_str = d[:10]
            score = h.get("score", "?")
            league = h.get("league", "")
            # Du POV de l'equipe home aujourd'hui
            h_team = h.get("home_team","")
            a_team = h.get("away_team","")
            # Tirs (si dispo)
            h_shots = h.get("home_shots"); a_shots = h.get("away_shots")
            h_sot = h.get("home_sot"); a_sot = h.get("away_sot")
            shots_line = ""
            if h_shots is not None or a_shots is not None:
                def _fmt(v):
                    if v is None: return "?"
                    try: return f"{int(v)}"
                    except: return str(v)
                shots_line = (
                    f'<div style="font-size:11px;color:#64748b;margin-top:3px">'
                    f'🎯 {_fmt(h_shots)}-{_fmt(a_shots)} tirs · {_fmt(h_sot)}-{_fmt(a_sot)} cadrés'
                    f'</div>'
                )
            h2h_html += (
                f'<div style="padding:10px 14px;border-bottom:1px solid #1a2540">'
                f'<div style="display:flex;align-items:center;gap:10px">'
                f'<span style="background:{col};color:#fff;border-radius:4px;padding:3px 9px;'
                f'font-size:13px;font-weight:bold;min-width:22px;text-align:center">{res}</span>'
                f'<span style="color:#94a3b8;font-size:12px;font-weight:500;min-width:80px">{date_str}</span>'
                f'<span style="color:#e2e8f0;font-size:13px;flex:1">{h_team} <span style="color:#64748b">vs</span> {a_team}</span>'
                f'<span style="color:#f1f5f9;font-weight:800;font-size:15px">{score}</span>'
                f'</div>'
                f'{shots_line}'
                f'{f"<div style=\"font-size:10px;color:#64748b;margin-top:2px\">{league}</div>" if league else ""}'
                f'</div>'
            )
        # Summary
        n = len(h2h_details)
        n_w = sum(1 for h in h2h_details if h.get("result_for_home") == "W")
        n_d = sum(1 for h in h2h_details if h.get("result_for_home") == "D")
        n_l = sum(1 for h in h2h_details if h.get("result_for_home") == "L")
        rows += (
            f'<div style="text-align:center;color:#94a3b8;font-size:12px;font-weight:700;'
            f'padding:8px 0 4px;text-transform:uppercase;letter-spacing:1px;'
            f'background:#0d1b2e">'
            f'⚔️ H2H · {n} confrontations · '
            f'<span style="color:#22c55e">{n_w}V</span> '
            f'<span style="color:#f59e0b">{n_d}N</span> '
            f'<span style="color:#ef4444">{n_l}D</span> '
            f'<span style="color:#64748b;font-size:10px">(POV {home})</span>'
            f'</div>'
            f'{h2h_html}'
        )

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
            f'<div style="background:#0a1628;border-radius:8px;padding:12px 14px;margin-bottom:8px">'
            f'<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px">'
            f'<div style="display:flex;align-items:center;gap:8px">'
            f'<span style="background:{pos_c}22;color:{pos_c};border:1px solid {pos_c};border-radius:4px;'
            f'padding:3px 8px;font-size:11px;font-weight:700">{pos_l}</span>'
            f'<span style="color:#f1f5f9;font-weight:700;font-size:15px">{name}{sub_tag}</span>'
            f'</div>'
            f'<span style="color:#94a3b8;font-size:12px;font-weight:600">{apps} matchs</span>'
            f'</div>'
            f'<div style="display:grid;grid-template-columns:1fr 1fr;gap:10px;font-size:13px">'
            f'<div>'
            f'<div style="color:#e2e8f0;margin-bottom:4px;font-weight:600">⚽ {goals}G · {round(gpm*100,0):.0f}%/m · <span style="color:#94a3b8">xG {xgpm:.2f}</span></div>'
            f'<div style="background:#1e293b;border-radius:3px;height:5px">'
            f'<div style="background:#22c55e;height:5px;border-radius:3px;width:{g_width}%"></div></div>'
            f'</div>'
            f'<div>'
            f'<div style="color:#e2e8f0;margin-bottom:4px;font-weight:600">🎯 {assists}PD · {round(apm*100,0):.0f}%/m · <span style="color:#94a3b8">xA {xapm:.2f}</span></div>'
            f'<div style="background:#1e293b;border-radius:3px;height:5px">'
            f'<div style="background:#3b82f6;height:5px;border-radius:3px;width:{a_width}%"></div></div>'
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

    # ── Historique cotes ─────────────────────────────────────────────────────
    _oh_idx = [0]

    def odds_hist_block(oh, team_name):
        if not oh or oh.get("total", 0) < 2:
            return ""
        wr    = oh["win_rate"]
        impl  = oh["implied"]
        ovp   = oh["overperf"]
        total = oh["total"]
        wins  = oh["wins"]
        cote  = oh["current_odds"]
        rng   = oh["cote_range"]
        sign  = "+" if ovp >= 0 else ""
        color = "#22c55e" if ovp >= 5 else ("#ef4444" if ovp <= -5 else "#f59e0b")
        bar_w = min(100, wr)
        if total >= 10:
            rel_txt = str(total) + " matchs ✅"; rel_col = "#475569"
        elif total >= 5:
            rel_txt = str(total) + " matchs"; rel_col = "#f59e0b"
        else:
            rel_txt = str(total) + " matchs ⚠️"; rel_col = "#ef4444"

        games = oh.get("matched_games", [])
        uid   = "oh" + str(_oh_idx[0]); _oh_idx[0] += 1

        rows = ""
        for g in sorted(games, key=lambda x: x.get("date",""), reverse=True):
            r  = g.get("result","?")
            rc = {"W":"#22c55e","D":"#f59e0b","L":"#ef4444"}.get(r,"#6b7280")
            loc = "🏠" if g.get("is_home") else "✈️"
            rows += (
                "<div style=\"display:flex;align-items:center;gap:5px;padding:2px 0;font-size:11px\">"
                + "<span style=\"background:" + rc + ";color:#fff;border-radius:3px;padding:0 4px;font-size:10px;font-weight:bold\">" + r + "</span>"
                + "<span style=\"color:#64748b;font-size:10px\">" + loc + "</span>"
                + "<span style=\"color:#94a3b8;flex:1\">" + g["home"] + " vs " + g["away"] + "</span>"
                + "<span style=\"color:#f1f5f9;font-weight:600\">" + g["score"] + "</span>"
                + "<span style=\"color:#475569;font-size:10px\"> @" + str(g["cote"]) + "</span>"
                + "</div>"
            )

        detail = ("<div id=\"" + uid + "\" style=\"display:none;margin-top:6px;padding-top:6px;border-top:1px solid #1e293b\">" + rows + "</div>") if rows else ""
        toggle_attr = ("onclick=\"var d=document.getElementById('" + uid + "');d.style.display=d.style.display==='none'?'block':'none'\" style=\"cursor:pointer\"") if rows else ""
        arrow = " 🔽" if rows else ""

        return (
            "<div style=\"background:#0a1628;border-radius:6px;padding:8px 10px;margin-bottom:6px\" " + toggle_attr + ">"
            + "<div style=\"display:flex;justify-content:space-between;margin-bottom:5px\">"
            + "<div style=\"color:#94a3b8;font-size:10px\">Coté " + str(cote) + " · plage " + rng + arrow + "</div>"
            + "<div style=\"color:" + rel_col + ";font-size:10px\">" + rel_txt + "</div>"
            + "</div>"
            + "<div style=\"display:flex;align-items:center;gap:8px;margin-bottom:4px\">"
            + "<div style=\"flex:1;background:#1e293b;border-radius:3px;height:8px\">"
            + "<div style=\"background:" + color + ";width:" + str(bar_w) + "%;height:8px;border-radius:3px\"></div></div>"
            + "<span style=\"color:" + color + ";font-weight:700;font-size:13px\">" + str(wr) + "%</span>"
            + "</div>"
            + "<div style=\"display:flex;justify-content:space-between;font-size:10px\">"
            + "<span style=\"color:#64748b\">" + str(wins) + "V sur " + str(total) + " matchs</span>"
            + "<span style=\"color:" + color + ";font-weight:600\">vs " + str(impl) + "% implicite · " + sign + str(ovp) + "%</span>"
            + "</div>"
            + detail
            + "</div>"
        )

    hoh = odds_hist_block(home_odds_hist, home)
    aoh = odds_hist_block(away_odds_hist, away)
    if hoh or aoh:
        rows += (
            f'<div style="text-align:center;color:#94a3b8;font-size:11px;'
            f'padding:5px 0 3px;text-transform:uppercase;letter-spacing:1px;'
            f'background:#0d1b2e;margin-top:2px">📈 HISTORIQUE COTES</div>'
        )
        rows += (
            f'<div style="display:grid;grid-template-columns:1fr 1fr;gap:8px;padding:10px;background:#0d1b2e">'
            f'<div>{hoh or "—"}</div><div>{aoh or "—"}</div></div>'
        )

    h_top = top_players(home_players or [])
    a_top = top_players(away_players or [])

    if h_top or a_top:
        rows += (
            f'<div style="text-align:center;color:#94a3b8;font-size:11px;'
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

    # ── Top decisifs L5/L10 (depuis les matchs reels) ──────────────────────
    def decisive_card(p, side):
        l5  = p.get("l5",  {})
        l10 = p.get("l10", {})
        name = p.get("name", "?")
        l5_g  = l5.get("goals", 0)
        l5_a  = l5.get("assists", 0)
        l5_m  = l5.get("matches_decisive", 0)
        l5_n  = l5.get("n_matches", 0)
        l10_g = l10.get("goals", 0)
        l10_a = l10.get("assists", 0)
        l10_m = l10.get("matches_decisive", 0)
        l10_n = l10.get("n_matches", 0)
        is_hot = l5_n and (l5_m / l5_n) >= 0.6
        flame = "<span style='color:#f97316;margin-left:6px;font-size:16px'>🔥</span>" if is_hot else ""
        return (
            f'<div style="padding:12px 14px;background:#0a1628;border-radius:8px;margin-bottom:8px;'
            f'border-left:4px solid {"#f97316" if is_hot else "#475569"}">'
            f'<div style="color:#f1f5f9;font-weight:800;font-size:16px;margin-bottom:8px">{name}{flame}</div>'
            f'<div style="display:grid;grid-template-columns:1fr 1fr;gap:10px;font-size:13px">'
            f'<div>'
            f'<div style="color:#fbbf24;font-weight:700;font-size:11px;text-transform:uppercase;letter-spacing:0.5px;margin-bottom:3px">L5</div>'
            f'<div style="color:#e2e8f0;font-weight:600">{l5_g}G · {l5_a}PD</div>'
            f'<div style="color:#94a3b8;font-size:11px;margin-top:2px">décisif {l5_m}/{l5_n}m</div>'
            f'</div>'
            f'<div>'
            f'<div style="color:#fb923c;font-weight:700;font-size:11px;text-transform:uppercase;letter-spacing:0.5px;margin-bottom:3px">L10</div>'
            f'<div style="color:#e2e8f0;font-weight:600">{l10_g}G · {l10_a}PD</div>'
            f'<div style="color:#94a3b8;font-size:11px;margin-top:2px">décisif {l10_m}/{l10_n}m</div>'
            f'</div>'
            f'</div>'
            f'</div>'
        )

    def merge_decisive(dec_l5, dec_l10, top_n=2):
        """Renvoie les top_n joueurs decisifs (priorise L5 puis L10)."""
        idx_l5  = {p["name"]: p for p in (dec_l5  or [])}
        idx_l10 = {p["name"]: p for p in (dec_l10 or [])}
        all_names = list(idx_l5.keys()) + [n for n in idx_l10.keys() if n not in idx_l5]
        merged = []
        for name in all_names:
            l5_data  = idx_l5.get(name, {})
            l10_data = idx_l10.get(name, {})
            # Score = decisive_L5 * 2 + decisive_L10
            score = l5_data.get("decisive", 0) * 2 + l10_data.get("decisive", 0)
            merged.append({"name": name, "score": score, "l5": l5_data, "l10": l10_data})
        merged.sort(key=lambda x: x["score"], reverse=True)
        return merged[:top_n]

    h_decs = merge_decisive(home_dec_l5, home_dec_l10, top_n=2)
    a_decs = merge_decisive(away_dec_l5, away_dec_l10, top_n=2)

    if h_decs or a_decs:
        rows += (
            f'<div style="text-align:center;color:#94a3b8;font-size:11px;'
            f'padding:6px 0 4px;text-transform:uppercase;letter-spacing:1px;'
            f'background:#0d1b2e;margin-top:2px">🔥 Plus décisifs récemment</div>'
        )
        rows += (
            f'<div style="display:grid;grid-template-columns:1fr 1fr;gap:8px;padding:8px;background:#0d1b2e">'
            f'<div>'
            f'<div style="color:#3b82f6;font-size:11px;font-weight:600;margin-bottom:6px;text-align:center">🏠 {home}</div>'
            + "".join(decisive_card(p, "home") for p in h_decs) +
            f'</div>'
            f'<div>'
            f'<div style="color:#3b82f6;font-size:11px;font-weight:600;margin-bottom:6px;text-align:center">✈️ {away}</div>'
            + "".join(decisive_card(p, "away") for p in a_decs) +
            f'</div>'
            f'</div>'
        )

    # ── LINEUP (11 probable + indisponibles) ──────────────────────────────
    if lineup and (lineup.get("home") or lineup.get("away")):
        ltype = lineup.get("type") or "predicted"
        lsource = lineup.get("source") or ""
        type_label = "Composition probable" if ltype == "predicted" else "Composition confirmée"
        type_color = "#f59e0b" if ltype == "predicted" else "#22c55e"

        def render_starter(p):
            rating = p.get("rating")
            rate_html = ""
            if rating:
                color = "#fbbf24" if rating >= 7.5 else ("#84cc16" if rating >= 7 else ("#f59e0b" if rating >= 6.5 else "#94a3b8"))
                rate_html = f'<span style="color:{color};font-weight:800;font-size:14px;margin-left:8px">{rating:.2f}</span>'
            shirt = p.get("shirt") or ""
            shirt_html = f'<span style="color:#94a3b8;font-size:12px;background:#1e293b;border-radius:4px;padding:3px 8px;margin-right:8px;font-weight:600;display:inline-block;min-width:24px;text-align:center">{shirt}</span>' if shirt else ""
            return (
                f'<div style="padding:8px 12px;font-size:14px;color:#e2e8f0;font-weight:500;border-bottom:1px solid #1a2540">'
                f'{shirt_html}{p.get("name","?")}{rate_html}'
                f'</div>'
            )

        def render_unavailable(p):
            t = (p.get("type") or "").lower()
            ret = p.get("return") or ""
            if "injury" in t or "blesse" in t:
                icon = "🤕"; icon_color = "#ef4444"
            elif "suspension" in t or "suspend" in t:
                icon = "🟥"; icon_color = "#dc2626"
            elif "doubtful" in t.lower() or "doubt" in t.lower():
                icon = "❓"; icon_color = "#f59e0b"
            else:
                icon = "⚠️"; icon_color = "#94a3b8"
            if "doubt" in ret.lower():
                icon = "❓"; icon_color = "#f59e0b"
            ret_html = f'<span style="color:#94a3b8;font-size:11px;margin-left:8px;font-weight:500">→ {ret}</span>' if ret else ""
            return (
                f'<div style="padding:6px 12px;font-size:13px;color:#cbd5e1;display:flex;align-items:center;gap:8px;font-weight:500">'
                f'<span style="color:{icon_color};font-size:14px">{icon}</span>'
                f'<span style="flex:1">{p.get("name","?")}</span>'
                f'{ret_html}'
                f'</div>'
            )

        def render_team_lineup(t, side):
            if not t: return '<div style="color:#475569;text-align:center;padding:20px">—</div>'
            starters = t.get("starters") or []
            unavail  = t.get("unavailable") or []
            form     = t.get("formation") or "?"
            coach    = t.get("coach") or ""

            inc = [u for u in unavail if "doubt" in (u.get("return") or "").lower()]
            absent = [u for u in unavail if "doubt" not in (u.get("return") or "").lower()]

            starters_html = "".join(render_starter(p) for p in starters)
            absent_html = "".join(render_unavailable(p) for p in absent) if absent else '<div style="color:#64748b;font-size:12px;padding:6px 12px;font-style:italic">Aucun absent confirmé</div>'
            inc_html = "".join(render_unavailable(p) for p in inc) if inc else ""

            return (
                f'<div>'
                f'<div style="padding:12px 14px;background:#0d1b2e;border-bottom:2px solid #1e3a8a">'
                f'<div style="color:#60a5fa;font-size:16px;font-weight:800">{t.get("name","?")} <span style="color:#94a3b8;font-size:13px;font-weight:600">({form})</span></div>'
                f'{f"<div style=\"color:#94a3b8;font-size:12px;margin-top:4px;font-weight:500\">🎯 Coach: {coach}</div>" if coach else ""}'
                f'</div>'
                f'<div style="padding:0">{starters_html}</div>'
                f'<div style="padding:10px 14px;background:#0a1628;border-top:1px solid #1e293b">'
                f'<div style="color:#ef4444;font-size:12px;font-weight:700;text-transform:uppercase;margin-bottom:6px;letter-spacing:0.5px">Absents/blessés</div>'
                f'{absent_html}'
                f'{f"<div style=\"color:#f59e0b;font-size:12px;font-weight:700;text-transform:uppercase;margin-top:10px;margin-bottom:6px;letter-spacing:0.5px\">Incertains</div>{inc_html}" if inc else ""}'
                f'</div>'
                f'</div>'
            )

        compo_html = (
            f'<div style="text-align:center;color:#94a3b8;font-size:12px;font-weight:700;'
            f'padding:8px 0 4px;text-transform:uppercase;letter-spacing:1px;'
            f'background:#0d1b2e;margin-top:2px">'
            f'👥 COMPOSITION <span style="color:{type_color};margin-left:6px">[{type_label}]</span>'
            f'</div>'
            f'<div style="display:grid;grid-template-columns:1fr 1fr;gap:1px;background:#1e293b">'
            f'<div style="background:#0a1628">{render_team_lineup(lineup.get("home"), "home")}</div>'
            f'<div style="background:#0a1628">{render_team_lineup(lineup.get("away"), "away")}</div>'
            f'</div>'
        )
        # Insertion AVANT le bloc "5 derniers matchs" (apres Comparaison rapide)
        marker = '📅 5 derniers matchs (détail)'
        if marker in rows:
            idx = rows.find(marker)
            # Trouver le debut du div parent ("<div style=...padding:6px 0 3px...📅 5 derniers...")
            div_start = rows.rfind('<div', 0, idx)
            if div_start > 0:
                rows = rows[:div_start] + compo_html + rows[div_start:]
            else:
                rows += compo_html
        else:
            rows += compo_html

    return (
        f'<div id="stats-{mid_safe}" style="border:1px solid #1e293b;'
        f'border-radius:10px;overflow:hidden">'
        + rows +
        f'</div>'
    )

# ─── Picks cards ─────────────────────────────────────────────────────────────

def _pick_id_foot_team(p, match_ctx):
    mid = (match_ctx or {}).get("match_id") or (match_ctx or {}).get("home", "?")
    return f"f_t_{mid}_{p.get('direction','?')}_{p.get('label','?')[:30]}"

def _pick_id_foot_player(p, match_ctx):
    mid = (match_ctx or {}).get("match_id") or (match_ctx or {}).get("home", "?")
    return f"f_p_{mid}_{p.get('player','?')}_{p.get('type','?')}"

def _pick_id_foot_fun(p, match_ctx):
    mid = (match_ctx or {}).get("match_id") or (match_ctx or {}).get("home", "?")
    return f"f_fun_{mid}_{p.get('direction','?')}_{p.get('label','?')[:30]}"

def _pick_id_nba(p, game):
    gid = (game or {}).get("game_id") or (game or {}).get("home_team","?")
    return f"n_{gid}_{p.get('player','?')}_{p.get('prop','?')}_{p.get('direction','?')}_{p.get('line','?')}"


def build_team_pick(p, ai_txt="", match_ctx=None):
    c     = p["confidence"]
    color = conf_color(c)
    form  = form_badges(p.get("stats", {}).get("form"))
    ai_bl = f'<div style="font-size:12px;color:#7dd3fc;margin-top:5px;font-style:italic">🤖 {ai_txt}</div>' if ai_txt else ""

    cote_real = p.get("cote")
    cote_min  = p.get("cote_min")
    value     = p.get("value")  # tuple (icon, label, color) ou None

    # Bouton push Telegram
    push_btn = ""
    if match_ctx:
        text = _format_push_team(p, match_ctx.get("home",""), match_ctx.get("away",""), match_ctx.get("league",""))
        push_btn = _push_button(text)

    # Badge cote
    cote_block = ""
    if cote_real:
        cote_block = cote_badge(cote_real)

    # Bloc cote minimum + value rating (seulement si pas de cote reelle)
    advice = ""
    if cote_min and not cote_real:
        v_icon, v_label, v_color = value or ("", "", "#94a3b8")
        v_badge = ""
        if value:
            v_badge = (
                f'<span style="background:rgba({",".join(str(int(v_color[i:i+2],16)) for i in (1,3,5))},0.15);'
                f'color:{v_color};border:1px solid {v_color};border-radius:4px;'
                f'padding:2px 7px;font-size:10px;font-weight:700;margin-left:6px">'
                f'{v_icon} {v_label}'
                f'</span>'
            )
        advice = (
            f'<div style="font-size:12px;margin-top:8px;padding:7px 10px;'
            f'background:rgba(34,197,94,0.06);border-left:3px solid #22c55e;border-radius:4px">'
            f'<b style="color:#22c55e">✅ Prendre uniquement si cote ≥ {cote_min}</b>{v_badge}'
            f'<div style="color:#64748b;font-size:11px;margin-top:3px">'
            f'En dessous de cette cote, l\'espérance est négative (le bookmaker te gruge sa marge). '
            f'Au-dessus = edge positif sur le long terme.'
            f'</div>'
            f'</div>'
        )

    pid = _pick_id_foot_team(p, match_ctx) if match_ctx else ""
    return (
        f'<div class="pick-card" data-pick-id="{pid}" style="background:#1e293b;border-radius:10px;padding:14px 16px;'
        f'margin-bottom:10px;border-left:4px solid {color}">'
        f'<div style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:8px">'
        f'<div style="display:flex;align-items:center;flex-wrap:wrap;gap:4px">'
        f'<span style="color:{color};font-weight:700;font-size:15px">{p["label"]}</span>'
        f'{cote_block}'
        f'<span class="new-badge" style="display:none;background:#fb923c;color:#0a1628;border-radius:4px;padding:1px 6px;font-size:10px;font-weight:800;margin-left:6px">🆕 NOUVEAU</span>'
        f'<span style="color:#475569;font-size:12px;margin-left:6px">{p["type"]}</span>'
        f'</div>'
        f'<div style="display:flex;align-items:center;gap:6px">'
        f'<div style="background:{color};color:#000;font-weight:bold;border-radius:20px;padding:4px 12px;font-size:14px">{c}%</div>'
        f'{push_btn}'
        f'</div>'
        f'</div>'
        f'<div style="color:#94a3b8;font-size:13px;margin-top:8px;line-height:1.55">{p["reasoning"].replace(chr(10), "<br>")}</div>'
        f'<div style="margin-top:6px">{form}</div>'
        f'{advice}'
        f'{ai_bl}'
        f'</div>'
    )

def build_player_pick(p, ai_analyses=None, match_ctx=None):
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
    # Badge cote inline a cote du label (meme style que les team picks)
    cote_b = cote_badge(p.get("cote"))
    # Bouton push Telegram
    push_btn = ""
    if match_ctx:
        text = _format_push_player_foot(p, match_ctx.get("home",""), match_ctx.get("away",""), match_ctx.get("league",""))
        push_btn = _push_button(text)
    pid = _pick_id_foot_player(p, match_ctx) if match_ctx else ""
    return (
        f'<div class="pick-card" data-pick-id="{pid}" style="background:#162032;border-radius:8px;padding:12px 14px;'
        f'margin-bottom:8px;border-left:3px solid {color}">'
        f'<div style="display:flex;justify-content:space-between;align-items:flex-start;flex-wrap:wrap;gap:8px">'
        f'<div style="flex:1">'
        f'<div style="display:flex;align-items:center;flex-wrap:wrap;gap:3px;margin-bottom:4px">'
        f'<span>{icon}</span>{pos_b}'
        f'<span style="color:{color};font-weight:600;font-size:14px">{p["label"]}</span>'
        f'{cote_b}'
        f'<span class="new-badge" style="display:none;background:#fb923c;color:#0a1628;border-radius:4px;padding:1px 6px;font-size:10px;font-weight:800;margin-left:6px">🆕</span>'
        f'<span style="color:#475569;font-size:12px;margin-left:4px">{type_}</span>'
        f'{sub_b}'
        f'</div>'
        f'<div style="color:#64748b;font-size:12px;line-height:1.55">{p["reasoning"].replace(chr(10), "<br>")}</div>'
        f'{ai_bl}'
        f'</div>'
        f'<div style="display:flex;align-items:center;gap:6px">'
        f'<div style="background:{color};color:#000;font-weight:bold;border-radius:16px;padding:3px 10px;font-size:13px">{c}%</div>'
        f'{push_btn}'
        f'</div>'
        f'</div>'
        f'</div>'
    )

def build_fun_pick(p, match_ctx=None):
    c     = p["confidence"]
    cote  = p.get("cote")
    cb    = cote_badge(cote) if cote else ""
    push_btn = ""
    if match_ctx:
        text = _format_push_team(p, match_ctx.get("home",""), match_ctx.get("away",""), match_ctx.get("league",""))
        push_btn = _push_button(text)
    pid = _pick_id_foot_fun(p, match_ctx) if match_ctx else ""
    return (
        f'<div class="pick-card" data-pick-id="{pid}" style="background:#1a1a2e;border-radius:8px;padding:12px 14px;'
        f'margin-bottom:8px;border:1px solid #4c1d95">'
        f'<div style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:8px">'
        f'<div style="flex:1">'
        f'<div style="display:flex;align-items:center;flex-wrap:wrap;gap:4px;margin-bottom:4px">'
        f'<span style="color:#a78bfa;font-weight:700;font-size:14px">{p["label"]}</span>'
        f'{cb}<span class="new-badge" style="display:none;background:#fb923c;color:#0a1628;border-radius:4px;padding:1px 6px;font-size:10px;font-weight:800;margin-left:6px">🆕</span><span style="color:#7c3aed;font-size:11px;margin-left:5px">Paris fun</span>'
        f'</div>'
        f'<div style="color:#9f7aea;font-size:12px;font-style:italic">{p["reasoning"]}</div>'
        f'</div>'
        f'<div style="display:flex;align-items:center;gap:6px">'
        f'<div style="background:#7c3aed;color:#fff;font-weight:bold;border-radius:16px;padding:3px 10px;font-size:13px">{c}%</div>'
        f'{push_btn}'
        f'</div>'
        f'</div>'
        f'</div>'
    )

def build_match_card(m, team_ai_map, player_ai_map, pstats=None):
    home      = m["home"]
    away      = m["away"]
    mid_safe  = str(m["match_id"]).replace("-","")
    dt        = format_datetime(m.get("start_ts"))

    # Contexte commun pour boutons push Telegram + pick_ids stables
    match_ctx = {"home": home, "away": away, "league": m.get("league", ""), "match_id": m.get("match_id", mid_safe)}

    # Picks équipe
    team_html = "".join(build_team_pick(p, team_ai_map.get(p["label"],""), match_ctx=match_ctx) for p in m["picks"])

    # Paris fun
    fun_html = ""
    if m.get("fun_picks"):
        fun_cards = "".join(build_fun_pick(p, match_ctx=match_ctx) for p in m["fun_picks"])
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
            cards = "".join(build_player_pick(p, player_ai_map, match_ctx=match_ctx) for p in pp)
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
    form  = dict(m.get("_form") or {})
    # Extrait les cotes 1X2 depuis match_odds pour la section "Cotes vs Realite"
    mo = m.get("match_odds") or {}
    for mk in (mo.get("markets") or []):
        if mk.get("marketName") == "Full time":
            odds_1x2 = {}
            for c in mk.get("choices", []):
                cn = c.get("name", "")
                try:
                    dec_cote = round(float(c.get("fractionalValue", 0)) + 1, 2)
                except: continue
                if cn == "1": odds_1x2["home"] = dec_cote
                elif cn == "X": odds_1x2["draw"] = dec_cote
                elif cn == "2": odds_1x2["away"] = dec_cote
            if odds_1x2:
                form["_odds_1x2"] = odds_1x2
            break
    stats_panel = build_stats_panel(
        mid_safe, home, away, form,
        ps.get("home_recent", {}),
        ps.get("away_recent", {}),
        ps.get("home_team_stats", {}),
        ps.get("away_team_stats", {}),
        ps.get("home", []),
        ps.get("away", []),
        ps.get("home_odds_hist", {}),
        ps.get("away_odds_hist", {}),
        ps.get("home_l5", []),
        ps.get("away_l5", []),
        ps.get("home_decisive_l5",  []),
        ps.get("home_decisive_l10", []),
        ps.get("away_decisive_l5",  []),
        ps.get("away_decisive_l10", []),
        ps.get("lineup"),
        ps.get("h2h_details", []),
    )

    # Compte total picks pour badge
    n_picks = len(m.get("picks", [])) + len(m.get("fun_picks", []))
    n_players = len(m.get("home_players", [])) + len(m.get("away_players", []))
    picks_label = f"🎯 {n_picks} picks"
    if n_players: picks_label += f" · {n_players} joueurs"

    return (
        f'<div style="background:#0f172a;border-radius:14px;margin-bottom:18px;'
        f'box-shadow:0 4px 20px rgba(0,0,0,0.4);overflow:hidden">'
        # Header : click pour stats, bouton separe pour picks
        f'<div class="match-header" onclick="toggleStats(\'{mid_safe}\')">'
        f'<div style="display:flex;justify-content:space-between;align-items:flex-start;flex-wrap:wrap;gap:10px">'
        f'<div>'
        f'<div style="color:#475569;font-size:11px;text-transform:uppercase;letter-spacing:1px;font-weight:600">{m["league"]}</div>'
        f'<div style="color:#f1f5f9;font-size:19px;font-weight:700;margin-top:3px">'
        f'{home} <span style="color:#334155">vs</span> {away}</div>'
        f'</div>'
        f'<div style="display:flex;align-items:center;gap:12px">'
        f'<div style="color:#475569;font-size:13px">🕐 {dt}</div>'
        f'<button class="picks-btn" id="picks-btn-{mid_safe}" '
        f'onclick="event.stopPropagation();togglePicks(\'{mid_safe}\')">{picks_label}</button>'
        f'<div id="arrow-{mid_safe}" style="color:#334155;font-size:16px;transition:transform .2s">▼</div>'
        f'</div>'
        f'</div>'
        f'</div>'
        # Body : stats full-width quand expand-stats, picks toggle separe
        f'<div id="body-{mid_safe}" class="match-body">'
        f'  <div class="stats-col">{stats_panel}</div>'
        f'  <div class="picks-col">{team_html}{fun_html}{player_html}</div>'
        f'</div>'
        f'</div>'
    )

# ─── HTML complet ─────────────────────────────────────────────────────────────

def build_nba_card(game):
    """Render une carte de match NBA avec picks joueurs."""
    gid = game.get("game_id")
    home = game.get("home_team", "?")
    away = game.get("away_team", "?")
    status = game.get("status", "")
    date = game.get("date", "")
    home_picks = game.get("home_picks", [])
    away_picks = game.get("away_picks", [])

    def render_pick(p):
        v = p.get("value")
        v_html = ""
        if v:
            v_html = (
                f'<span style="background:rgba({",".join(str(int(v[2][i:i+2],16)) for i in (1,3,5))},0.15);'
                f'color:{v[2]};border:1px solid {v[2]};border-radius:4px;'
                f'padding:2px 7px;font-size:10px;font-weight:700;margin-left:6px">'
                f'{v[0]} {v[1]}</span>'
            )
        # Stats: L5/L10/Saison
        s = p.get("stats", {})
        stats_html = ""
        if s:
            stats_html = (
                f'<div style="color:#64748b;font-size:11px;margin-top:4px">'
                f'L5: {s.get("L5","?")} · L10: {s.get("L10","?")} · Saison: {s.get("Saison", s.get("S","?"))} → attendu {s.get("mu","?")}'
                f'</div>'
            )
        # Hit rate L10 / L20 + tendance
        hit_l10     = p.get("hit_l10")
        hit_l10_pct = p.get("hit_l10_pct", 0)
        hit_l20     = p.get("hit_l20")
        hit_l20_pct = p.get("hit_l20_pct", 0)
        trend       = p.get("trend", "stable")
        trend_delta = p.get("trend_delta", 0)
        trend_icon  = p.get("trend_icon", "")
        hit_html = ""
        if hit_l10 and hit_l20:
            c10 = hit_rate_color(hit_l10_pct)
            c20 = hit_rate_color(hit_l20_pct)
            # Couleur du trend
            if   trend == "hot":  trend_color, trend_txt = "#22c55e", f"+{trend_delta:.0f}pp"
            elif trend == "cold": trend_color, trend_txt = "#ef4444", f"{trend_delta:.0f}pp"
            else:                 trend_color, trend_txt = "#94a3b8", ""
            trend_html = f'<span style="color:{trend_color};font-weight:700;margin-left:4px">{trend_icon} {trend_txt}</span>' if trend != "stable" else ""
            hit_html = (
                f'<div style="font-size:11px;margin-top:3px;display:flex;gap:8px;align-items:center;flex-wrap:wrap">'
                f'<span style="background:{c10};color:#fff;font-weight:700;padding:2px 6px;border-radius:4px">L10 {hit_l10} ({hit_l10_pct}%)</span>'
                f'<span style="background:{c20};color:#fff;font-weight:700;padding:2px 6px;border-radius:4px">L20 {hit_l20} ({hit_l20_pct}%)</span>'
                f'{trend_html}'
                f'</div>'
            )
        elif hit_l10:
            c10 = hit_rate_color(hit_l10_pct)
            hit_html = (
                f'<div style="margin-top:3px"><span style="background:{c10};color:#fff;'
                f'font-size:11px;font-weight:700;padding:2px 6px;border-radius:4px">'
                f'L10 {hit_l10} ({hit_l10_pct}%)</span></div>'
            )
        # Splits Outlier-style : H2H + Venue
        splits_html = splits_chip(p)
        # Warning rotation reduite (joueur passe au bench - stats L10/L20 biaisees)
        rot_warning = p.get("rotation_warning", "")
        rot_html = ""
        if rot_warning:
            rot_html = (
                f'<div style="color:#ef4444;font-size:11px;font-weight:700;margin-top:3px;'
                f'background:rgba(239,68,68,0.10);border-left:2px solid #ef4444;padding:3px 7px;border-radius:3px">'
                f'⚠️ {rot_warning}'
                f'</div>'
            )
        # Warning blessure (Day-To-Day / Questionable - joueur peut ne pas jouer)
        injury_warning = p.get("injury_warning", "")
        if injury_warning:
            rot_html += (
                f'<div style="color:#fb923c;font-size:11px;font-weight:700;margin-top:3px;'
                f'background:rgba(251,146,60,0.10);border-left:2px solid #fb923c;padding:3px 7px;border-radius:3px">'
                f'🩹 Blessure : {injury_warning}'
                f'</div>'
            )
        # Warning "last game MIN crash" : dernier match temps de jeu reduit
        last_min_warning = p.get("last_min_warning", "")
        if last_min_warning:
            rot_html += (
                f'<div style="color:#fbbf24;font-size:11px;font-weight:700;margin-top:3px;'
                f'background:rgba(251,191,36,0.10);border-left:2px solid #fbbf24;padding:3px 7px;border-radius:3px">'
                f'⏱️ {last_min_warning}'
                f'</div>'
            )
        # Warning divergence book : le bookmaker quote tres en-dessous de notre mu
        book_div_warning = p.get("book_divergence_warning", "")
        if book_div_warning:
            rot_html += (
                f'<div style="color:#a78bfa;font-size:11px;font-weight:700;margin-top:3px;'
                f'background:rgba(167,139,250,0.10);border-left:2px solid #a78bfa;padding:3px 7px;border-radius:3px">'
                f'🤔 {book_div_warning}'
                f'</div>'
            )

        # Argument defensif (faille/force de l'adversaire)
        def_argument = p.get("def_argument", "")
        def_html = ""
        if def_argument:
            # Couleur selon nature : faille (encaisse bcp) vert, force (solide) orange
            is_weak = "beaucoup" in def_argument or "encaisse" in def_argument and "solide" not in def_argument
            color = "#22c55e" if is_weak else "#f59e0b"
            icon = "🎯" if is_weak else "🛡️"
            def_html = (
                f'<div style="color:{color};font-size:11px;font-weight:600;margin-top:3px;'
                f'background:rgba({",".join(str(int(color[i:i+2],16)) for i in (1,3,5))},0.08);'
                f'border-left:2px solid {color};padding:3px 7px;border-radius:3px">'
                f'{icon} {def_argument}'
                f'</div>'
            )
        # Contexte multipliers (pace/vegas/B2B) en mode compact
        ctx_data = p.get("context", {})
        ctx_html = ""
        ctx_chips = []
        for k, label, threshold_up in [("pace", "pace", 1.02), ("vegas", "vegas", 1.03), ("def", "def", 1.04)]:
            v = ctx_data.get(k)
            if v is None: continue
            if v >= threshold_up:    color, sign = "#22c55e", "+"
            elif v <= 2 - threshold_up: color, sign = "#ef4444", ""
            else: continue  # neutre, on skip
            pct = round((v - 1) * 100)
            ctx_chips.append(f'<span style="background:rgba({",".join(str(int(color[i:i+2],16)) for i in (1,3,5))},0.15);color:{color};border-radius:3px;padding:1px 5px;font-size:10px;font-weight:700;margin-right:3px">{label} {sign}{pct}%</span>')
        if ctx_data.get("b2b"):
            ctx_chips.append('<span style="background:rgba(239,68,68,0.15);color:#ef4444;border-radius:3px;padding:1px 5px;font-size:10px;font-weight:700;margin-right:3px">B2B -4%</span>')
        if ctx_chips:
            ctx_html = f'<div style="margin-top:4px">{" ".join(ctx_chips)}</div>'
        conf = p.get("confidence", 0)
        conf_color = "#22c55e" if conf >= 70 else ("#84cc16" if conf >= 65 else "#f59e0b")
        cote_min = p.get("cote_min")
        real_cote = p.get("real_cote")
        book = p.get("book")
        books_list = p.get("books") or []
        edge = p.get("edge")
        is_real = p.get("is_real_line")

        # Cote inline a cote du label (meme style que les picks foot).
        # On garde le badge `cote_badge` minimaliste. real_cote en priorite,
        # sinon cote_min (heuristique).
        cote_to_show = real_cote if real_cote else cote_min
        cote_inline  = cote_badge(cote_to_show)
        # Petit hint texte pour l'edge (utile pour value bets)
        edge_hint = ""
        if edge is not None and edge >= 3:
            edge_hint = (
                f'<span style="color:#22c55e;font-size:11px;font-weight:700;margin-left:6px">'
                f'+{edge}% edge</span>'
            )
        pid = _pick_id_nba(p, game)
        return (
            f'<div class="pick-card" data-pick-id="{pid}" style="background:#0a1628;border-radius:8px;padding:10px 14px;margin-bottom:8px;'
            f'border-left:3px solid {conf_color}">'
            f'<div style="display:flex;justify-content:space-between;align-items:flex-start;flex-wrap:wrap;gap:6px">'
            f'<div style="flex:1">'
            f'<div style="display:flex;align-items:center;flex-wrap:wrap;gap:3px">'
            f'<span style="color:#f1f5f9;font-weight:700;font-size:14px">{p.get("label","?")}</span>'
            f'{cote_inline}'
            f'<span class="new-badge" style="display:none;background:#fb923c;color:#0a1628;border-radius:4px;padding:1px 6px;font-size:10px;font-weight:800;margin-left:6px">🆕</span>'
            f'{v_html}'
            f'{edge_hint}'
            f'</div>'
            f'{stats_html}'
            f'{hit_html}'
            f'{splits_html}'
            f'{rot_html}'
            f'{def_html}'
            f'{ctx_html}'
            f'</div>'
            f'<div style="display:flex;align-items:center;gap:6px">'
            f'<span style="background:{conf_color};color:#0a1628;font-weight:800;border-radius:14px;padding:3px 10px;font-size:13px">{conf}%</span>'
            f'{stake_pill(p.get("stake_label"), p.get("kelly_pct"))}'
            f'{_push_button(_format_push_nba(p, game))}'
            f'</div>'
            f'</div>'
            f'</div>'
        )

    home_html = "".join(render_pick(p) for p in home_picks) or '<div style="color:#475569;font-size:12px;padding:6px">Aucun pick avec value suffisante</div>'
    away_html = "".join(render_pick(p) for p in away_picks) or '<div style="color:#475569;font-size:12px;padding:6px">Aucun pick avec value suffisante</div>'

    return (
        f'<div style="background:#0f172a;border-radius:14px;margin-bottom:18px;'
        f'box-shadow:0 4px 20px rgba(0,0,0,0.4);overflow:hidden">'
        # Header
        f'<div style="padding:16px 20px;background:#0d1b2e;border-bottom:1px solid #1e293b">'
        f'<div style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:8px">'
        f'<div>'
        f'<div style="color:#fb923c;font-size:11px;text-transform:uppercase;letter-spacing:1px;font-weight:700">🏀 NBA</div>'
        f'<div style="color:#f1f5f9;font-size:19px;font-weight:700;margin-top:3px">'
        f'{away} <span style="color:#334155">@</span> {home}</div>'
        f'</div>'
        f'<div style="color:#475569;font-size:13px">🕐 {date} · {status}</div>'
        f'</div>'
        f'</div>'
        # Picks 2 colonnes
        f'<div style="display:grid;grid-template-columns:1fr 1fr;gap:1px;background:#1e293b">'
        f'<div style="background:#0f172a;padding:14px">'
        f'<div style="color:#3b82f6;font-size:12px;font-weight:700;text-transform:uppercase;letter-spacing:1px;margin-bottom:10px">🏠 {home}</div>'
        f'{home_html}'
        f'</div>'
        f'<div style="background:#0f172a;padding:14px">'
        f'<div style="color:#3b82f6;font-size:12px;font-weight:700;text-transform:uppercase;letter-spacing:1px;margin-bottom:10px">✈️ {away}</div>'
        f'{away_html}'
        f'</div>'
        f'</div>'
        f'</div>'
    )


def _compose_prop_value(g, prop):
    """Calcule la valeur du stat pour 1 game donne (PTS, REB, AST, FG3M, PR, PA, PRA)."""
    pts = g.get("PTS", 0) or 0
    reb = g.get("REB", 0) or 0
    ast = g.get("AST", 0) or 0
    fg3 = g.get("FG3M", 0) or 0
    return {
        "PTS":  pts,
        "REB":  reb,
        "AST":  ast,
        "FG3M": fg3,
        "PR":   pts + reb,
        "PA":   pts + ast,
        "PRA":  pts + reb + ast,
    }.get(prop, 0)


def _build_prop_chart_bars(games_window, ref_line, chart_max, with_labels=True):
    """Construit la zone bar-chart (bars + labels). Helper interne, factorise
    pour pouvoir reutiliser sur L5/L10/L20."""
    CHART_PX = 120
    if not games_window or chart_max <= 0:
        return f'<div style="color:#475569;font-size:12px;padding:10px;text-align:center">Pas assez de games</div>'
    n = len(games_window)
    # Adapte gap et font selon la densite
    gap = 6 if n <= 5 else (3 if n <= 10 else 2)
    font_val = 13 if n <= 5 else (11 if n <= 10 else 9)
    font_lbl = 10 if n <= 5 else (9 if n <= 10 else 8)
    bars_html = ""
    labels_html = ""
    for g, val in games_window:
        pct_h = (val / chart_max) * 100 if chart_max else 0
        bar_px = max(4, round(pct_h * CHART_PX / 100))
        is_hit = val > ref_line
        color = "#22c55e" if is_hit else "#ef4444"
        date_short = (g.get("date","")[:10][-5:] or "?").replace("-","/")
        opp = (g.get("opp","?") or "?")[:4]
        loc = "vs" if g.get("is_home") else "@"
        mins = int(g.get("MIN") or 0)
        # Couleur MIN : rouge si < 15 (joueur a peine joue, performance pas
        # representative), orange si 15-25, gris sinon.
        if   mins < 15: min_color = "#ef4444"
        elif mins < 25: min_color = "#f59e0b"
        else:           min_color = "#94a3b8"
        min_suffix = "min" if n <= 5 else "m"
        bars_html += (
            f'<div style="flex:1;display:flex;flex-direction:column;align-items:center;justify-content:flex-end;min-width:0">'
            f'<div style="color:#f1f5f9;font-size:{font_val}px;font-weight:700;margin-bottom:3px">{int(val)}</div>'
            f'<div style="width:100%;background:{color};height:{bar_px}px;border-radius:3px 3px 0 0"></div>'
            f'</div>'
        )
        if with_labels:
            if n <= 5:
                label_text = (
                    f'{date_short}<br>{loc} {opp}<br>'
                    f'<span style="color:{min_color};font-weight:700">{mins} {min_suffix}</span>'
                )
            elif n <= 10:
                label_text = (
                    f'{date_short}<br>{loc} {opp}<br>'
                    f'<span style="color:{min_color};font-weight:700">{mins}{min_suffix}</span>'
                )
            else:
                label_text = (
                    f'{loc}{opp}<br>'
                    f'<span style="color:{min_color};font-weight:700">{mins}{min_suffix}</span>'
                )
            labels_html += (
                f'<div style="flex:1;color:#475569;font-size:{font_lbl}px;text-align:center;line-height:1.3;padding-top:5px;min-width:0;overflow:hidden">'
                f'{label_text}</div>'
            )
    ref_px = round((ref_line / chart_max) * CHART_PX) if chart_max else 0
    labels_block = f'<div style="display:flex;gap:{gap}px">{labels_html}</div>' if with_labels else ''
    return (
        f'<div style="position:relative;height:{CHART_PX + 22}px">'
        f'<div style="display:flex;gap:{gap}px;align-items:flex-end;height:{CHART_PX + 18}px">{bars_html}</div>'
        f'<div style="position:absolute;left:0;right:0;bottom:{ref_px}px;border-top:2px dashed #fb923c;pointer-events:none">'
        f'<span style="position:absolute;right:0;top:-9px;background:#fb923c;color:#0a1628;font-size:10px;font-weight:700;padding:1px 6px;border-radius:3px">{ref_line}</span>'
        f'</div>'
        f'</div>'
        f'{labels_block}'
    )


def _build_prop_chart(player, prop, opp_abbr, book_line=None):
    """Construit le HTML d'une vue prop : badges hit rates + 3 bar charts
    (L5/L10/L20) selectionnables. Affiche L5 par defaut."""
    games = player.get("l10_games", [])
    games = [g for g in games if (g.get("MIN") or 0) > 0]
    if not games:
        return '<div style="color:#475569;font-size:12px;padding:10px">Pas de stats dispo</div>'

    # Series de valeurs (avec games associes)
    pairs_l20 = [(g, _compose_prop_value(g, prop)) for g in games[:20]]
    pairs_l10 = pairs_l20[:10]
    pairs_l5  = pairs_l20[:5]
    l20_vals = [v for _, v in pairs_l20]
    l10_vals = [v for _, v in pairs_l10]
    l5_vals  = [v for _, v in pairs_l5]

    h2h_pairs = [(g, _compose_prop_value(g, prop))
                 for g in games[:20]
                 if opp_abbr and (g.get("opp","") or "").upper() == opp_abbr.upper()]
    h2h_vals = [v for _, v in h2h_pairs]

    median = sorted(l20_vals)[len(l20_vals)//2] if l20_vals else 0
    mean   = round(sum(l20_vals)/len(l20_vals), 1) if l20_vals else 0
    ref_line = book_line if book_line is not None else median

    def _hr(vals):
        if not vals: return None, 0, 0
        hits = sum(1 for v in vals if v > ref_line)
        return round(hits/len(vals)*100), hits, len(vals)
    hr5  = _hr(l5_vals)
    hr10 = _hr(l10_vals)
    hr20 = _hr(l20_vals)
    hr_h2h = _hr(h2h_vals)

    def _badge(label, hr_tuple, target=70):
        if hr_tuple[0] is None:
            return f'<span style="color:#475569;font-size:11px;margin-right:8px">{label}: —</span>'
        pct, w, n = hr_tuple
        color = "#22c55e" if pct >= target else ("#f59e0b" if pct >= 50 else "#ef4444")
        return f'<span style="color:{color};font-size:11px;font-weight:700;margin-right:8px">{label}: {pct}%<span style="color:#64748b;font-weight:400"> ({w}/{n})</span></span>'

    badges = (
        _badge("L5", hr5) +
        _badge("L10", hr10) +
        _badge("L20", hr20) +
        (_badge(f"H2H {opp_abbr}", hr_h2h) if opp_abbr and hr_h2h[0] is not None else "") +
        f'<span style="color:#94a3b8;font-size:11px">Méd. <b>{median}</b> · Moy. <b>{mean}</b></span>'
    )

    # Build les 3 charts (L5/L10/L20) avec leur propre chart_max
    chart_blocks = ""
    for window_key, pairs, with_lbl in [("L5", pairs_l5, True), ("L10", pairs_l10, True), ("L20", pairs_l20, True)]:
        if not pairs:
            content = '<div style="color:#475569;font-size:12px;padding:10px;text-align:center">Pas assez de games</div>'
        else:
            window_vals = [v for _, v in pairs]
            cmax = max(window_vals + [ref_line])
            if cmax <= 0: cmax = 1
            content = _build_prop_chart_bars(pairs, ref_line, cmax, with_labels=with_lbl)
        display = "block" if window_key == "L5" else "none"
        chart_blocks += (
            f'<div class="tg-window-block" data-window="{window_key}" style="display:{display}">{content}</div>'
        )

    return (
        f'<div style="padding:6px 4px">'
        # Badges en haut
        f'<div style="margin-bottom:8px;display:flex;flex-wrap:wrap;gap:4px;align-items:center">{badges}</div>'
        # Zone chart container
        f'<div style="background:#0a1628;border-radius:8px;padding:10px 12px 6px">'
        f'{chart_blocks}'
        f'</div>'
        f'</div>'
    )


def _build_player_analyse_card(player, opp_abbr, odds_for_player, side_label, is_starter=True):
    """Card d'analyse joueur. Starters : expanded par defaut. Bench : collapsed
    (juste le nom + min). Clic sur header = toggle expand."""
    name = player.get("name", "?")
    pos = player.get("position", "")
    season = player.get("season_avg", {}) or {}
    mins = season.get("MIN", 0)
    pos_b = pos_badge(pos) if pos else ""
    safe_id = "".join(c for c in name if c.isalnum())

    PROPS = ["PTS", "REB", "AST", "FG3M", "PR", "PA", "PRA"]
    prop_labels = {"PTS":"PTS","REB":"REB","AST":"AST","FG3M":"3PM","PR":"PTS+REB","PA":"PTS+AST","PRA":"PTS+REB+AST"}

    # Boutons prop
    prop_btns = ""
    for i, pr in enumerate(PROPS):
        active = "tg-prop-active" if i == 0 else ""
        prop_btns += (
            f'<button class="tg-prop-btn {active}" data-prop="{pr}" '
            f'onclick="selectPropChart(this)" '
            f'style="background:#1e293b;color:#94a3b8;border:1px solid #334155;border-radius:6px;padding:4px 10px;'
            f'font-size:11px;font-weight:700;cursor:pointer">{prop_labels[pr]}</button>'
        )

    # Boutons fenetre L5/L10/L20
    window_btns = ""
    for w in ["L5", "L10", "L20"]:
        active = "tg-window-active" if w == "L5" else ""
        window_btns += (
            f'<button class="tg-window-btn {active}" data-window="{w}" '
            f'onclick="selectWindow(this)" '
            f'style="background:#1e293b;color:#94a3b8;border:1px solid #334155;border-radius:6px;padding:4px 10px;'
            f'font-size:11px;font-weight:700;cursor:pointer">{w}</button>'
        )

    # Contenus : pour chaque prop, on inclut les 3 windows (L5/L10/L20).
    # Affichage controle par le combo data-prop + data-window.
    contents = ""
    for i, pr in enumerate(PROPS):
        book_data = (odds_for_player or {}).get(pr)
        book_line = book_data.get("line") if book_data else None
        chart_html = _build_prop_chart(player, pr, opp_abbr, book_line)
        prop_display = "block" if i == 0 else "none"
        contents += (
            f'<div class="tg-prop-content" data-prop="{pr}" style="display:{prop_display}">{chart_html}</div>'
        )

    content_display = "block" if is_starter else "none"
    arrow = "▼" if is_starter else "▶"
    starter_badge = '<span style="background:#16a34a;color:#fff;border-radius:3px;padding:1px 5px;font-size:9px;font-weight:800;margin-left:4px">TITULAIRE</span>' if is_starter else ""

    return (
        f'<div class="player-analyse" style="background:#162032;border-radius:10px;padding:10px 14px;margin-bottom:8px;'
        f'border-left:3px solid {"#3b82f6" if is_starter else "#475569"}">'
        # Header (cliquable pour toggle expand)
        f'<div class="player-header" onclick="togglePlayerExpand(this)" '
        f'style="display:flex;align-items:center;flex-wrap:wrap;gap:6px;cursor:pointer;user-select:none">'
        f'<span class="expand-arrow" style="color:#64748b;font-size:11px;width:14px">{arrow}</span>'
        f'<span style="font-size:10px;color:#475569;font-weight:700">{side_label}</span>'
        f'{pos_b}'
        f'<span style="color:#f1f5f9;font-weight:700;font-size:14px">{name}</span>'
        f'{starter_badge}'
        f'<span style="color:#64748b;font-size:11px;margin-left:auto">{round(mins,1) if mins else "?"} min</span>'
        f'</div>'
        # Bloc expandable
        f'<div class="player-content" style="display:{content_display};margin-top:10px">'
        f'<div style="display:flex;flex-wrap:wrap;gap:4px;margin-bottom:6px">{prop_btns}</div>'
        f'<div style="display:flex;flex-wrap:wrap;gap:4px;margin-bottom:8px">{window_btns}</div>'
        f'{contents}'
        f'</div>'
        f'</div>'
    )


def build_nba_analyse_section(nba_picks_data, nba_player_stats, nba_odds):
    """Section Analyse : pour chaque match a venir, panneau de joueurs avec
    bar chart selectable par prop (PTS/REB/AST/3PM/PR/PA/PRA)."""
    if not nba_player_stats:
        return (
            '<div style="text-align:center;padding:40px;color:#64748b">'
            'Pas de stats joueurs NBA disponibles. Lance nba_scraper.py d\'abord.'
            '</div>'
        )
    cards = ""
    for gid, mdata in nba_player_stats.items():
        if gid.startswith("_"): continue
        home = mdata.get("home_team", "?")
        away = mdata.get("away_team", "?")
        home_abbr = mdata.get("home_abbr") or ""
        away_abbr = mdata.get("away_abbr") or ""
        # Top 12 joueurs par minutes : 5 premiers expanded (starters), reste collapsed
        home_players = sorted(mdata.get("home_players", []),
                              key=lambda p: (p.get("season_avg",{}) or {}).get("MIN", 0), reverse=True)[:12]
        away_players = sorted(mdata.get("away_players", []),
                              key=lambda p: (p.get("season_avg",{}) or {}).get("MIN", 0), reverse=True)[:12]
        odds_for_game = nba_odds.get(gid) or nba_odds.get(str(gid)) or {}

        home_cards = "".join(
            _build_player_analyse_card(p, opp_abbr=away_abbr, odds_for_player=odds_for_game.get(p.get("name","")),
                                       side_label=f"🏠 {home}", is_starter=(i < 5))
            for i, p in enumerate(home_players)
        )
        away_cards = "".join(
            _build_player_analyse_card(p, opp_abbr=home_abbr, odds_for_player=odds_for_game.get(p.get("name","")),
                                       side_label=f"✈️ {away}", is_starter=(i < 5))
            for i, p in enumerate(away_players)
        )
        cards += (
            f'<div style="background:#0f172a;border-radius:14px;margin-bottom:18px;padding:14px 18px;box-shadow:0 4px 20px rgba(0,0,0,0.4)">'
            f'<div style="color:#fb923c;font-size:11px;text-transform:uppercase;letter-spacing:1px;font-weight:700;margin-bottom:4px">🏀 NBA</div>'
            f'<div style="color:#f1f5f9;font-size:18px;font-weight:700;margin-bottom:14px">{away} <span style="color:#334155">@</span> {home}</div>'
            f'<div style="display:grid;grid-template-columns:1fr 1fr;gap:12px">'
            f'<div><div style="color:#3b82f6;font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:1px;margin-bottom:8px">🏠 {home}</div>{home_cards or "Pas de joueurs"}</div>'
            f'<div><div style="color:#3b82f6;font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:1px;margin-bottom:8px">✈️ {away}</div>{away_cards or "Pas de joueurs"}</div>'
            f'</div>'
            f'</div>'
        )
    if not cards:
        return '<div style="text-align:center;padding:40px;color:#64748b">Aucun match NBA a venir.</div>'
    return cards


def build_nba_section(nba_picks_data):
    """Section NBA : liste des matchs avec leurs picks."""
    if not nba_picks_data:
        return (
            '<div style="text-align:center;padding:40px;color:#64748b">'
            'Aucun match NBA aujourd\'hui ou demain.'
            '</div>'
        )
    cards = ""
    n_picks = 0
    for gid, game in nba_picks_data.items():
        cards += build_nba_card(game)
        n_picks += len(game.get("home_picks", [])) + len(game.get("away_picks", []))
    meta = (
        f'<div style="color:#94a3b8;font-size:13px;margin-bottom:16px">'
        f'🏀 {len(nba_picks_data)} matchs NBA · {n_picks} picks joueurs · '
        f'<span style="color:#64748b">Algo v2 : L5/L10/Saison + pace + Vegas total + B2B + def opp + edge ≥3%</span>'
        f'</div>'
    )
    return meta + cards


# ─── Filtre de date (commun foot + NBA history) ─────────────────────────────

def _build_date_filter(dates, container_id, sport_label="picks"):
    """
    Genere un selecteur (dropdown + boutons periode) pour filtrer l'historique
    par date. JS pure client-side qui toggle visibilite des <details data-date="...">.

    Le container_id doit etre unique par section (foot vs nba).
    """
    if not dates: return ""
    # Option de dropdown : toutes + chaque date
    opts = ['<option value="all">📅 Toutes les dates</option>']
    for d in dates:
        try:
            from datetime import datetime as _dt
            dt = _dt.strptime(d, "%Y-%m-%d")
            label = dt.strftime("%d/%m/%Y")
        except Exception:
            label = d
        opts.append(f'<option value="{d}">{label}</option>')
    options_html = "".join(opts)

    return (
        f'<div style="background:#0f172a;border-radius:10px;padding:12px 16px;margin-bottom:14px;'
        f'display:flex;align-items:center;gap:10px;flex-wrap:wrap">'
        f'<span style="color:#94a3b8;font-size:12px;font-weight:600;text-transform:uppercase;letter-spacing:1px">Filtrer :</span>'
        # Boutons periode
        f'<button onclick="filterHistory(\'{container_id}\',\'all\')" class="hist-btn" data-period="all" style="background:#3b82f6;color:#fff;border:none;border-radius:6px;padding:5px 12px;font-size:12px;font-weight:600;cursor:pointer">Tout</button>'
        f'<button onclick="filterHistory(\'{container_id}\',\'1\')" class="hist-btn" data-period="1" style="background:#1e293b;color:#94a3b8;border:none;border-radius:6px;padding:5px 12px;font-size:12px;font-weight:600;cursor:pointer">Hier</button>'
        f'<button onclick="filterHistory(\'{container_id}\',\'7\')" class="hist-btn" data-period="7" style="background:#1e293b;color:#94a3b8;border:none;border-radius:6px;padding:5px 12px;font-size:12px;font-weight:600;cursor:pointer">7 jours</button>'
        f'<button onclick="filterHistory(\'{container_id}\',\'30\')" class="hist-btn" data-period="30" style="background:#1e293b;color:#94a3b8;border:none;border-radius:6px;padding:5px 12px;font-size:12px;font-weight:600;cursor:pointer">30 jours</button>'
        # Dropdown date specifique
        f'<select onchange="filterHistoryDate(\'{container_id}\',this.value)" '
        f'style="background:#1e293b;color:#f1f5f9;border:1px solid #334155;border-radius:6px;padding:5px 10px;font-size:12px;cursor:pointer">'
        f'{options_html}'
        f'</select>'
        f'<span style="color:#475569;font-size:11px;margin-left:auto">{len(dates)} jour(s) de {sport_label} archives</span>'
        f'</div>'
    )


# ─── Historique Football ────────────────────────────────────────────────────

def build_foot_history(history_data):
    """Section historique foot : picks resolus par date avec details."""
    if not history_data or not history_data.get("picks"):
        return '<div style="color:#64748b;padding:30px;text-align:center">Aucun historique foot pour le moment.</div>'

    picks = history_data["picks"]
    from collections import defaultdict
    by_date = defaultdict(list)
    for p in picks:
        by_date[p.get("date","?")].append(p)
    dates = sorted(by_date.keys(), reverse=True)

    resolved = [p for p in picks if p.get("result") in ("WIN", "LOSS", "PUSH")]
    wins   = sum(1 for p in resolved if p.get("result") == "WIN")
    losses = sum(1 for p in resolved if p.get("result") == "LOSS")
    pushes = sum(1 for p in resolved if p.get("result") == "PUSH")
    pending = sum(1 for p in picks if p.get("result") in (None, "PENDING"))
    wr = (wins / (wins + losses) * 100) if (wins + losses) else 0
    roi_units, n_betted = 0.0, 0
    for p in resolved:
        cote = p.get("cote") or 0
        if not cote or p.get("result") == "PUSH": continue
        n_betted += 1
        roi_units += (cote - 1) if p.get("result") == "WIN" else -1
    roi_pct = (roi_units / n_betted * 100) if n_betted else 0

    wr_color = "#22c55e" if wr >= 55 else ("#84cc16" if wr >= 50 else "#ef4444")
    roi_color = "#22c55e" if roi_units > 0 else "#ef4444"
    summary = (
        f'<div style="background:#0f172a;border-radius:12px;padding:16px 20px;margin-bottom:18px;'
        f'display:grid;grid-template-columns:repeat(auto-fit,minmax(120px,1fr));gap:14px">'
        f'<div><div style="color:#64748b;font-size:11px;text-transform:uppercase;letter-spacing:1px">Résolus</div>'
        f'<div style="color:#f1f5f9;font-size:22px;font-weight:800">{wins+losses+pushes}</div></div>'
        f'<div><div style="color:#64748b;font-size:11px;text-transform:uppercase;letter-spacing:1px">Win Rate</div>'
        f'<div style="color:{wr_color};font-size:22px;font-weight:800">{wr:.1f}%</div>'
        f'<div style="color:#64748b;font-size:10px">{wins}W · {losses}L · {pushes}P</div></div>'
        f'<div><div style="color:#64748b;font-size:11px;text-transform:uppercase;letter-spacing:1px">ROI</div>'
        f'<div style="color:{roi_color};font-size:22px;font-weight:800">{roi_units:+.2f}u</div>'
        f'<div style="color:#64748b;font-size:10px">{roi_pct:+.1f}% sur {n_betted} bets</div></div>'
        f'<div><div style="color:#64748b;font-size:11px;text-transform:uppercase;letter-spacing:1px">Pending</div>'
        f'<div style="color:#fb923c;font-size:22px;font-weight:800">{pending}</div></div>'
        f'</div>'
    )

    date_html = ""
    for date in dates[:180]:  # 6 mois max (sinon la page devient lourde)
        day_picks = by_date[date]
        d_wins   = sum(1 for p in day_picks if p.get("result") == "WIN")
        d_losses = sum(1 for p in day_picks if p.get("result") == "LOSS")
        d_push   = sum(1 for p in day_picks if p.get("result") == "PUSH")
        d_pend   = sum(1 for p in day_picks if p.get("result") in (None, "PENDING"))
        d_wr     = (d_wins / (d_wins + d_losses) * 100) if (d_wins + d_losses) else None
        wr_text  = f"{d_wr:.0f}%" if d_wr is not None else "—"
        wr_col   = "#22c55e" if d_wr is not None and d_wr >= 55 else ("#84cc16" if d_wr is not None and d_wr >= 50 else "#ef4444")
        if d_wr is None: wr_col = "#94a3b8"

        # Group picks per match - puis sub-buckets : gagnes / perdus / autres
        from collections import OrderedDict
        per_match = OrderedDict()
        for p in day_picks:
            mid = p.get("match_id","?")
            per_match.setdefault(mid, {
                "matchup": p.get("matchup","?"),
                "league":  p.get("league",""),
                "won_picks":  [],
                "lost_picks": [],
                "other_picks":[],
            })
            result = (p.get("result") or "PENDING").upper()
            if   result == "WIN":  per_match[mid]["won_picks"].append(p)
            elif result == "LOSS": per_match[mid]["lost_picks"].append(p)
            else:                  per_match[mid]["other_picks"].append(p)

        def _render_foot_pick(p):
            result = p.get("result", "PENDING")
            actual = p.get("actual")
            cote = p.get("cote") or 0
            conf = p.get("confidence", 0)
            if result == "WIN":
                bg, fg, icon, lbl = "rgba(34,197,94,0.10)",  "#22c55e", "✓",  "GAGNÉ"
            elif result == "LOSS":
                bg, fg, icon, lbl = "rgba(239,68,68,0.10)",  "#ef4444", "✗",  "PERDU"
            elif result == "PUSH":
                bg, fg, icon, lbl = "rgba(148,163,184,0.10)","#94a3b8", "=",  "PUSH"
            else:
                bg, fg, icon, lbl = "rgba(251,146,60,0.08)", "#fb923c", "…",  "ATTENTE"
            cote_html = f'<div style="background:#1e293b;color:#fb923c;border-radius:4px;padding:3px 8px;font-size:13px;font-weight:700;white-space:nowrap">@ {cote:.2f}</div>' if cote else ""
            conf_color = "#22c55e" if conf >= 70 else ("#84cc16" if conf >= 65 else "#f59e0b")
            conf_html = f'<div style="background:{conf_color};color:#0a1628;font-weight:800;border-radius:12px;padding:2px 10px;font-size:13px;text-align:center">{conf}%</div>' if conf else ""
            reason = p.get("reasoning", "")
            # Multi-line reasoning : \n -> <br>, on monte la limite a 500 chars pour
            # accueillir les nouveaux reasoning detailes (stats brutes + ajustements + forme)
            reason_safe = (reason or "")[:500].replace("\n", "<br>")
            reason_html = f'<div style="color:#94a3b8;font-size:12px;margin-top:4px;line-height:1.55">{reason_safe}</div>' if reason_safe else ""
            actual_str = ""
            if actual is not None:
                if isinstance(actual, dict):
                    actual_str = ", ".join(f"{k}={v}" for k, v in actual.items())
                else:
                    actual_str = str(actual)
            actual_html = f'<span style="color:{fg};font-weight:700;font-size:12px">Réel {actual_str}</span>' if actual_str else ""
            cat = p.get("category", "")
            cat_chip = ""
            if cat == "team":
                cat_chip = '<span style="background:#1e3a8a;color:#bfdbfe;border-radius:3px;padding:1px 5px;font-size:10px;font-weight:700;margin-left:5px">ÉQ</span>'
            elif cat == "player":
                cat_chip = '<span style="background:#3b3a8a;color:#c4b5fd;border-radius:3px;padding:1px 5px;font-size:10px;font-weight:700;margin-left:5px">JOUEUR</span>'
            return (
                f'<div style="background:{bg};border-left:3px solid {fg};padding:9px 12px;'
                f'border-radius:6px;margin-bottom:6px">'
                f'<div style="display:flex;justify-content:space-between;align-items:flex-start;gap:8px">'
                f'<div style="flex:1;min-width:0">'
                f'<div style="color:#f1f5f9;font-size:14px;font-weight:700;line-height:1.35">{p.get("label","?")}{cat_chip}</div>'
                f'{reason_html}'
                f'</div>'
                f'<div style="display:flex;flex-direction:column;align-items:flex-end;gap:3px;flex-shrink:0">'
                f'{conf_html}{cote_html}'
                f'</div>'
                f'</div>'
                f'<div style="display:flex;justify-content:space-between;align-items:center;margin-top:5px;padding-top:5px;border-top:1px solid rgba(255,255,255,0.04);font-size:12px">'
                f'<span style="color:{fg};font-weight:800;letter-spacing:0.5px">{icon} {lbl}</span>'
                f'{actual_html}'
                f'</div>'
                f'</div>'
            )

        match_blocks = ""
        for mid, mdata in per_match.items():
            won_html  = "".join(_render_foot_pick(p) for p in mdata["won_picks"])
            lost_html = "".join(_render_foot_pick(p) for p in mdata["lost_picks"])
            other_html = "".join(_render_foot_pick(p) for p in mdata["other_picks"])
            n_won  = len(mdata["won_picks"])
            n_lost = len(mdata["lost_picks"])
            empty = '<div style="color:#475569;font-size:11px;text-align:center;padding:14px;font-style:italic">aucun</div>'
            match_blocks += (
                f'<div style="background:#0a1628;border-radius:8px;padding:10px 12px;margin-bottom:10px">'
                f'<div style="color:#3b82f6;font-size:13px;font-weight:700;margin-bottom:10px">'
                f'⚽ {mdata["matchup"]} <span style="color:#475569;font-weight:500">· {mdata["league"]}</span></div>'
                f'<div style="display:grid;grid-template-columns:1fr 1fr;gap:10px">'
                # Col gauche : GAGNES
                f'<div>'
                f'<div style="color:#22c55e;font-size:11px;font-weight:800;letter-spacing:1px;text-transform:uppercase;margin-bottom:6px;border-bottom:1px solid rgba(34,197,94,0.25);padding-bottom:4px">✓ Gagnés ({n_won})</div>'
                f'{won_html or empty}'
                f'</div>'
                # Col droite : PERDUS
                f'<div>'
                f'<div style="color:#ef4444;font-size:11px;font-weight:800;letter-spacing:1px;text-transform:uppercase;margin-bottom:6px;border-bottom:1px solid rgba(239,68,68,0.25);padding-bottom:4px">✗ Perdus ({n_lost})</div>'
                f'{lost_html or empty}'
                f'</div>'
                f'</div>'
                # PUSH / PENDING en pleine largeur
                f'{other_html}'
                f'</div>'
            )

        try:
            dt = datetime.strptime(date, "%Y-%m-%d")
            date_fr = dt.strftime("%d/%m/%Y")
        except Exception:
            date_fr = date
        pending_chip = f' · <span style="color:#fb923c">{d_pend} pending</span>' if d_pend else ""
        date_html += (
            f'<details data-date="{date}" style="background:#0f172a;border-radius:10px;margin-bottom:10px;padding:0">'
            f'<summary style="cursor:pointer;list-style:none;padding:14px 18px;display:flex;'
            f'justify-content:space-between;align-items:center;gap:10px">'
            f'<div>'
            f'<div style="color:#f1f5f9;font-size:15px;font-weight:700">{date_fr}</div>'
            f'<div style="color:#94a3b8;font-size:11px">{len(day_picks)} picks · {d_wins}W · {d_losses}L · {d_push}P{pending_chip}</div>'
            f'</div>'
            f'<span style="background:rgba(255,255,255,0.05);color:{wr_col};font-weight:800;font-size:14px;'
            f'padding:5px 12px;border-radius:10px">{wr_text}</span>'
            f'</summary>'
            f'<div style="padding:0 14px 14px 14px">{match_blocks}</div>'
            f'</details>'
        )

    # Wrap dans un container avec id pour le filtre JS
    filter_html = _build_date_filter(dates, "foothist-list", "foot")
    return summary + filter_html + f'<div id="foothist-list">{date_html}</div>'


# ─── Historique NBA ─────────────────────────────────────────────────────────

def build_nba_history(history_data):
    """
    Section historique NBA : groupe les picks resolus par date.
    history_data = dict {"picks": [list of pick entries with result/actual]}.
    """
    if not history_data or not history_data.get("picks"):
        return '<div style="color:#64748b;padding:30px;text-align:center">Aucun historique pour le moment.</div>'

    picks = history_data["picks"]
    # Trie par date desc
    from collections import defaultdict
    by_date = defaultdict(list)
    for p in picks:
        by_date[p.get("date", "?")].append(p)
    dates = sorted(by_date.keys(), reverse=True)

    # Stats globales sur les picks resolus
    resolved = [p for p in picks if p.get("result") in ("WIN", "LOSS", "PUSH")]
    wins   = sum(1 for p in resolved if p.get("result") == "WIN")
    losses = sum(1 for p in resolved if p.get("result") == "LOSS")
    pushes = sum(1 for p in resolved if p.get("result") == "PUSH")
    dnp    = sum(1 for p in picks if p.get("result") == "DNP")
    pending = sum(1 for p in picks if p.get("result") in (None, "PENDING"))
    wr = (wins / (wins + losses) * 100) if (wins + losses) else 0
    # ROI cumule (mise 1 par pick, gain = mise × cote - 1)
    roi_units = 0.0
    n_betted = 0
    for p in resolved:
        cote = p.get("real_cote") or p.get("cote_min") or 0
        if cote <= 0 or p.get("result") == "PUSH": continue
        n_betted += 1
        if p.get("result") == "WIN":
            roi_units += (cote - 1)
        else:
            roi_units -= 1
    roi_pct = (roi_units / n_betted * 100) if n_betted else 0

    # Header global
    wr_color = "#22c55e" if wr >= 55 else ("#84cc16" if wr >= 50 else "#ef4444")
    roi_color = "#22c55e" if roi_units > 0 else "#ef4444"
    summary = (
        f'<div style="background:#0f172a;border-radius:12px;padding:16px 20px;margin-bottom:18px;'
        f'display:grid;grid-template-columns:repeat(auto-fit,minmax(120px,1fr));gap:14px">'
        f'<div><div style="color:#64748b;font-size:11px;text-transform:uppercase;letter-spacing:1px">Résolus</div>'
        f'<div style="color:#f1f5f9;font-size:22px;font-weight:800">{wins+losses+pushes}</div></div>'
        f'<div><div style="color:#64748b;font-size:11px;text-transform:uppercase;letter-spacing:1px">Win Rate</div>'
        f'<div style="color:{wr_color};font-size:22px;font-weight:800">{wr:.1f}%</div>'
        f'<div style="color:#64748b;font-size:10px">{wins}W · {losses}L · {pushes}P</div></div>'
        f'<div><div style="color:#64748b;font-size:11px;text-transform:uppercase;letter-spacing:1px">ROI</div>'
        f'<div style="color:{roi_color};font-size:22px;font-weight:800">{roi_units:+.2f}u</div>'
        f'<div style="color:#64748b;font-size:10px">{roi_pct:+.1f}% sur {n_betted} bets</div></div>'
        f'<div><div style="color:#64748b;font-size:11px;text-transform:uppercase;letter-spacing:1px">DNP</div>'
        f'<div style="color:#94a3b8;font-size:22px;font-weight:800">{dnp}</div></div>'
        f'<div><div style="color:#64748b;font-size:11px;text-transform:uppercase;letter-spacing:1px">Pending</div>'
        f'<div style="color:#fb923c;font-size:22px;font-weight:800">{pending}</div></div>'
        f'</div>'
    )

    # Liste par date (collapsible)
    date_html = ""
    for date in dates[:180]:  # 6 mois max (sinon la page devient lourde)  # max 30 dernieres dates
        day_picks = by_date[date]
        d_wins   = sum(1 for p in day_picks if p.get("result") == "WIN")
        d_losses = sum(1 for p in day_picks if p.get("result") == "LOSS")
        d_push   = sum(1 for p in day_picks if p.get("result") == "PUSH")
        d_dnp    = sum(1 for p in day_picks if p.get("result") == "DNP")
        d_pend   = sum(1 for p in day_picks if p.get("result") in (None, "PENDING"))
        d_wr     = (d_wins / (d_wins + d_losses) * 100) if (d_wins + d_losses) else None
        wr_text  = f"{d_wr:.0f}%" if d_wr is not None else "—"
        wr_col   = "#22c55e" if d_wr is not None and d_wr >= 55 else ("#84cc16" if d_wr is not None and d_wr >= 50 else "#ef4444")
        if d_wr is None: wr_col = "#94a3b8"

        # Group picks par match, puis 2 colonnes GAGNES (gauche) / PERDUS (droite)
        from collections import OrderedDict
        per_match = OrderedDict()
        for p in day_picks:
            mid = p.get("game_id", "?")
            per_match.setdefault(mid, {
                "matchup": p.get("matchup", "?"),
                "won_picks": [], "lost_picks": [], "other_picks": []
            })
            result = (p.get("result") or "PENDING").upper()
            if   result == "WIN":  per_match[mid]["won_picks"].append(p)
            elif result == "LOSS": per_match[mid]["lost_picks"].append(p)
            else:                  per_match[mid]["other_picks"].append(p)  # PUSH/DNP/PENDING

        def _render_pick_compact(p):
            result = p.get("result", "PENDING")
            actual = p.get("actual")
            cote = p.get("real_cote") or p.get("cote_min") or 0
            edge = p.get("edge")
            book = p.get("book", "")
            conf = p.get("confidence", 0)
            s   = p.get("stats", {})
            ctx_data = p.get("context", {})
            if result == "WIN":
                bg, fg, icon, lbl = "rgba(34,197,94,0.10)",  "#22c55e", "✓",  "GAGNÉ"
            elif result == "LOSS":
                bg, fg, icon, lbl = "rgba(239,68,68,0.10)",  "#ef4444", "✗",  "PERDU"
            elif result == "PUSH":
                bg, fg, icon, lbl = "rgba(148,163,184,0.10)","#94a3b8", "=",  "PUSH"
            elif result == "DNP":
                bg, fg, icon, lbl = "rgba(148,163,184,0.06)","#64748b", "DNP","ABSENT"
            else:
                bg, fg, icon, lbl = "rgba(251,146,60,0.08)", "#fb923c", "…",  "ATTENTE"

            stats_html = ""
            if s:
                stats_html = (
                    f'<div style="color:#64748b;font-size:12px;margin-top:3px;line-height:1.45">'
                    f'L5 {s.get("L5","?")} · L10 {s.get("L10","?")} · S {s.get("Saison","?")} → {s.get("mu","?")}'
                    f'</div>'
                )

            hit_html = ""
            hit_l10 = p.get("hit_l10"); hit_l20 = p.get("hit_l20")
            if hit_l10 and hit_l20:
                pct10 = p.get("hit_l10_pct", 0); pct20 = p.get("hit_l20_pct", 0)
                td = p.get("trend_delta", 0)
                trend = p.get("trend", "stable"); trend_icon = p.get("trend_icon","")
                c10 = hit_rate_color(pct10)
                c20 = hit_rate_color(pct20)
                if   trend == "hot":  tc, tt = "#22c55e", f"+{td:.0f}pp"
                elif trend == "cold": tc, tt = "#ef4444", f"{td:.0f}pp"
                else:                 tc, tt = "#94a3b8", ""
                trend_chip = f'<span style="color:{tc};font-weight:700">{trend_icon}{tt}</span>' if trend != "stable" else ""
                hit_html = (
                    f'<div style="font-size:12px;margin-top:3px;display:flex;gap:7px;flex-wrap:wrap;align-items:center;line-height:1.45">'
                    f'<span style="background:{c10};color:#fff;font-weight:700;padding:2px 6px;border-radius:4px">L10 {hit_l10} ({pct10}%)</span>'
                    f'<span style="background:{c20};color:#fff;font-weight:700;padding:2px 6px;border-radius:4px">L20 {hit_l20} ({pct20}%)</span>'
                    f'{trend_chip}'
                    f'</div>'
                )

            def_argument = p.get("def_argument", "")
            def_html = ""
            if def_argument:
                is_weak = "beaucoup" in def_argument
                color = "#22c55e" if is_weak else "#94a3b8"
                icon_d = "🎯" if is_weak else "🛡️"
                def_html = (
                    f'<div style="color:{color};font-size:12px;font-weight:600;margin-top:3px;line-height:1.45">'
                    f'{icon_d} {def_argument}</div>'
                )

            chips = []
            for k, lblc, up in [("pace","pace",1.02),("vegas","vegas",1.03),("def","def",1.04)]:
                v = ctx_data.get(k)
                if v is None: continue
                if v >= up:        ccolor, sign = "#22c55e", "+"
                elif v <= 2 - up:  ccolor, sign = "#ef4444", ""
                else: continue
                pct = round((v - 1) * 100)
                chips.append(f'<span style="background:rgba({",".join(str(int(ccolor[i:i+2],16)) for i in (1,3,5))},0.15);color:{ccolor};border-radius:3px;padding:1px 5px;font-size:11px;font-weight:700">{lblc}{sign}{pct}%</span>')
            if ctx_data.get("b2b"):
                chips.append('<span style="background:rgba(239,68,68,0.15);color:#ef4444;border-radius:3px;padding:1px 5px;font-size:11px;font-weight:700">B2B</span>')
            chips_html = f'<div style="margin-top:4px;display:flex;gap:4px;flex-wrap:wrap">{"".join(chips)}</div>' if chips else ""

            BOOK_LABELS_HIST = {
                "draftkings": "DK", "fanduel": "FD", "betmgm": "MGM", "caesars": "Caesars",
                "pointsbetus": "PointsBet", "pinnacle": "Pin", "unibet_eu": "Unibet",
                "unibet_uk": "UnibetUK", "betfair_ex_eu": "Betfair", "marathonbet": "Marathon",
                "betclic": "Betclic", "bwin": "Bwin",
            }
            book_label = BOOK_LABELS_HIST.get(book, (book or "").upper()[:8])
            books_list_h = p.get("books") or []
            cote_block = ""
            if cote:
                edge_txt = ""
                if edge is not None:
                    ec = "#22c55e" if edge >= 5 else ("#84cc16" if edge > 0 else "#94a3b8")
                    edge_txt = f'<div style="color:{ec};font-size:11px;font-weight:700">{"+" if edge>0 else ""}{edge}%</div>'
                # Liste autres books
                others_h = [b for b in books_list_h if b.get("book") != book][:3]
                others_html = ""
                if others_h:
                    rows = "".join(
                        f'<div style="display:flex;justify-content:flex-end;gap:4px;font-size:10px;color:#94a3b8;line-height:1.3">'
                        f'<span>{BOOK_LABELS_HIST.get(b["book"], b["book"][:8])}</span>'
                        f'<span style="color:#cbd5e1">@{b["cote"]}</span>'
                        f'</div>'
                        for b in others_h
                    )
                    others_html = f'<div style="margin-top:3px;border-top:1px solid #1e293b;padding-top:2px">{rows}</div>'
                cote_block = (
                    f'<div style="background:#1e293b;color:#fb923c;border-radius:4px;padding:3px 8px;font-size:13px;font-weight:700;white-space:nowrap"><b>{book_label}</b> @ <b>{cote}</b></div>'
                    f'{edge_txt}'
                    f'{others_html}'
                )

            conf_color = "#22c55e" if conf >= 70 else ("#84cc16" if conf >= 65 else "#f59e0b")
            conf_html = f'<div style="background:{conf_color};color:#0a1628;font-weight:800;border-radius:12px;padding:2px 10px;font-size:13px;text-align:center">{conf}%</div>' if conf else ""

            actual_html = ""
            if actual is not None:
                actual_html = f'<span style="color:{fg};font-weight:700;font-size:12px">Réel {actual}</span>'

            return (
                f'<div style="background:{bg};border-left:3px solid {fg};padding:9px 12px;'
                f'border-radius:6px;margin-bottom:6px">'
                f'<div style="display:flex;justify-content:space-between;align-items:flex-start;gap:8px">'
                f'<div style="flex:1;min-width:0">'
                f'<div style="color:#f1f5f9;font-size:14px;font-weight:700;line-height:1.35">{p.get("label","?")}</div>'
                f'{stats_html}{hit_html}{def_html}{chips_html}'
                f'</div>'
                f'<div style="display:flex;flex-direction:column;align-items:flex-end;gap:3px;flex-shrink:0">'
                f'{conf_html}{cote_block}'
                f'</div>'
                f'</div>'
                f'<div style="display:flex;justify-content:space-between;align-items:center;margin-top:5px;padding-top:5px;border-top:1px solid rgba(255,255,255,0.04);font-size:12px">'
                f'<span style="color:{fg};font-weight:800;letter-spacing:0.5px">{icon} {lbl}</span>'
                f'{actual_html}'
                f'</div>'
                f'</div>'
            )

        picks_html = ""
        for mid, mdata in per_match.items():
            won_html  = "".join(_render_pick_compact(p) for p in mdata["won_picks"])
            lost_html = "".join(_render_pick_compact(p) for p in mdata["lost_picks"])
            other_html = "".join(_render_pick_compact(p) for p in mdata["other_picks"])
            n_won  = len(mdata["won_picks"])
            n_lost = len(mdata["lost_picks"])
            # Headers de colonnes (tjrs affiches meme si une est vide)
            won_empty  = '<div style="color:#475569;font-size:11px;text-align:center;padding:14px;font-style:italic">aucun</div>'
            lost_empty = '<div style="color:#475569;font-size:11px;text-align:center;padding:14px;font-style:italic">aucun</div>'
            picks_html += (
                f'<div style="background:#0a1628;border-radius:8px;padding:10px 12px;margin-bottom:10px">'
                f'<div style="color:#fb923c;font-size:13px;font-weight:700;margin-bottom:10px">🏀 {mdata["matchup"]}</div>'
                f'<div style="display:grid;grid-template-columns:1fr 1fr;gap:10px">'
                # Col gauche : GAGNES
                f'<div>'
                f'<div style="color:#22c55e;font-size:11px;font-weight:800;letter-spacing:1px;text-transform:uppercase;margin-bottom:6px;border-bottom:1px solid rgba(34,197,94,0.25);padding-bottom:4px">✓ Gagnés ({n_won})</div>'
                f'{won_html or won_empty}'
                f'</div>'
                # Col droite : PERDUS
                f'<div>'
                f'<div style="color:#ef4444;font-size:11px;font-weight:800;letter-spacing:1px;text-transform:uppercase;margin-bottom:6px;border-bottom:1px solid rgba(239,68,68,0.25);padding-bottom:4px">✗ Perdus ({n_lost})</div>'
                f'{lost_html or lost_empty}'
                f'</div>'
                f'</div>'
                # PUSH / DNP / PENDING en pleine largeur
                f'{other_html}'
                f'</div>'
            )

        # FR date
        try:
            dt = datetime.strptime(date, "%Y-%m-%d")
            date_fr = dt.strftime("%d/%m/%Y")
        except Exception:
            date_fr = date

        pending_chip = f' · <span style="color:#fb923c">{d_pend} pending</span>' if d_pend else ""
        date_html += (
            f'<details data-date="{date}" style="background:#0f172a;border-radius:10px;margin-bottom:10px;padding:0">'
            f'<summary style="cursor:pointer;list-style:none;padding:14px 18px;display:flex;'
            f'justify-content:space-between;align-items:center;gap:10px">'
            f'<div>'
            f'<div style="color:#f1f5f9;font-size:15px;font-weight:700">{date_fr}</div>'
            f'<div style="color:#94a3b8;font-size:11px">{len(day_picks)} picks · {d_wins}W · {d_losses}L · {d_push}P · {d_dnp}DNP{pending_chip}</div>'
            f'</div>'
            f'<span style="background:rgba(255,255,255,0.05);color:{wr_col};font-weight:800;font-size:14px;'
            f'padding:5px 12px;border-radius:10px">{wr_text}</span>'
            f'</summary>'
            f'<div style="padding:0 14px 14px 14px">{picks_html}</div>'
            f'</details>'
        )

    filter_html = _build_date_filter(dates, "nbahist-list", "NBA")
    return summary + filter_html + f'<div id="nbahist-list">{date_html}</div>'


def build_html(matches, team_ai, player_ai, pstats_data, nba_picks=None, nba_history=None, foot_history=None, nba_player_stats=None, nba_odds=None):
    team_ai_map = {item.get("pick",""):item.get("analyse","") for item in (team_ai or [])}
    now         = _now_paris().strftime("%d/%m/%Y %H:%M")
    nba_picks = nba_picks or {}
    nba_history = nba_history or {"picks": []}
    foot_history = foot_history or {"picks": []}
    nba_player_stats = nba_player_stats or {}
    nba_odds = nba_odds or {}

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
            cards += build_match_card(m, team_ai_map, player_ai, ps)
        tab_contents += f'<div id="{sid}" style="display:{active_div}">{cards}</div>'

    # Section NBA
    nba_section    = build_nba_section(nba_picks)
    nba_hist_html  = build_nba_history(nba_history)
    foot_hist_html = build_foot_history(foot_history)
    nba_analyse_html = build_nba_analyse_section(nba_picks, nba_player_stats, nba_odds)

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
  body{{background:#020617;color:#f1f5f9;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;padding:20px 24px}}
  .container{{max-width:1600px;margin:0 auto}}
  h1{{font-size:28px;font-weight:800;margin-bottom:4px}}
  .meta{{color:#475569;font-size:13px;margin-bottom:20px}}
  .legend{{background:#0f172a;border-radius:10px;padding:11px 16px;margin-bottom:20px;font-size:12px;color:#64748b;border:1px solid #1e293b;line-height:2}}
  .tabs{{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:24px}}
  .tab-btn{{background:#1e293b;color:#94a3b8;border:1px solid #334155;border-radius:20px;padding:8px 16px;font-size:13px;font-weight:600;cursor:pointer;transition:all .2s}}
  .tab-btn:hover{{background:#334155;color:#f1f5f9}}
  .tab-btn.active{{background:#3b82f6;color:#fff;border-color:#3b82f6}}
  .tab-count{{background:rgba(255,255,255,0.2);border-radius:10px;padding:1px 7px;font-size:11px;margin-left:5px}}
  footer{{color:#1e293b;font-size:11px;text-align:center;margin-top:30px;padding-top:20px;border-top:1px solid #0f172a}}
  .sport-btn{{
    background:#1e293b;color:#94a3b8;border:1px solid #334155;border-radius:10px;
    padding:12px 24px;font-size:15px;font-weight:700;cursor:pointer;transition:all .2s;
    letter-spacing:0.3px;
  }}
  .sport-btn:hover{{background:#334155;color:#f1f5f9}}
  .sport-btn.active{{background:#3b82f6;color:#fff;border-color:#3b82f6;box-shadow:0 4px 14px rgba(59,130,246,0.45)}}

  /* Match card body : stats full-width, picks toggleable separement */
  .match-body {{
    padding: 0 16px 16px;
  }}
  .stats-col {{ display: none; }}
  .picks-col {{ display: none; margin-top: 12px; }}
  .match-body.show-stats .stats-col {{ display: block; }}
  .match-body.show-picks .picks-col {{ display: block; }}

  /* Header hover */
  .match-header {{ cursor:pointer; padding:18px 20px 14px; transition:background .15s; user-select:none; }}
  .match-header:hover {{ background:#1e293b; }}

  /* Bouton picks dans le header */
  .picks-btn {{
    background:#1e3a8a; color:#bfdbfe; border:1px solid #3b82f6;
    border-radius:8px; padding:10px 18px; font-size:14px; font-weight:700;
    cursor:pointer; transition:all .15s; box-shadow:0 2px 8px rgba(59,130,246,0.25);
    letter-spacing:0.3px;
  }}
  .picks-btn:hover {{
    background:#1e40af; color:#fff; box-shadow:0 4px 12px rgba(59,130,246,0.45);
    transform:translateY(-1px);
  }}
  .picks-btn.active {{
    background:#3b82f6; color:#fff; box-shadow:0 4px 14px rgba(59,130,246,0.55);
  }}
</style>
</head>
<body>
<div class="container">
  <h1>🎯 Sports Picks</h1>
  <div class="meta">Généré le {now} · ⚽ {len(matches)} matchs foot · 🏀 {len(nba_picks)} matchs NBA</div>
  <!-- Toast non-bloquant : signalement des nouveaux picks -->
  <div id="new-picks-toast" style="display:none;position:fixed;top:18px;right:18px;z-index:9999;background:linear-gradient(90deg,#fb923c,#f97316);color:#0a1628;border-radius:10px;padding:10px 16px;font-weight:700;font-size:14px;box-shadow:0 4px 20px rgba(251,146,60,0.5);transition:opacity 0.5s ease-out;max-width:300px">
    🆕 <span id="new-picks-count">0</span> nouveau(x) pick(s)
    <div style="font-size:11px;font-weight:500;color:#1e293b;margin-top:2px">Repere les badges 🆕 sur les cartes</div>
  </div>
  <!-- Sport switcher -->
  <div style="display:flex;gap:10px;margin-bottom:18px;flex-wrap:wrap">
    <button class="sport-btn active" onclick="showSport('football')" id="sport-btn-football">⚽ Football</button>
    <button class="sport-btn" onclick="showSport('nba')"     id="sport-btn-nba">🏀 Basketball NBA</button>
    <button class="sport-btn" onclick="showSport('analyse')" id="sport-btn-analyse">🔍 Analyse NBA</button>
    <button class="sport-btn" onclick="showSport('foothist')" id="sport-btn-foothist">🏆 Historique Foot</button>
    <button class="sport-btn" onclick="showSport('nbahist')"  id="sport-btn-nbahist">🏆 Historique NBA</button>
  </div>

  <!-- Section Football -->
  <div id="sport-football">
    <div class="legend">
      <b>▼ Cliquer sur un match</b> pour voir les stats détaillées (forme, buts, tirs, BTTS) ·
      <b>📊 Cote</b> = bookmaker ·
      <b>xG</b> = buts attendus ·
      <span style="color:#22c55e">■</span>≥80%
      <span style="color:#84cc16">■</span>≥68%
      <span style="color:#f59e0b">■</span>≥55%
    </div>
    <div class="tabs">{tab_buttons}</div>
    {tab_contents}
  </div>

  <!-- Section NBA -->
  <div id="sport-nba" style="display:none">
    <div class="legend">
      <b>🏀 Picks joueurs NBA v2</b> — L5/L10/Saison · pace · Vegas total · B2B · def opp · edge ≥3% ·
      📈 hot streak (L10 &gt; L20) · 📉 cold streak ·
      🎯 faille adverse · 🛡️ défense solide
    </div>
    {nba_section}
  </div>

  <!-- Section Analyse NBA -->
  <div id="sport-analyse" style="display:none">
    <div class="legend">
      <b>🔍 Analyse joueur</b> — Selectionne un prop (PTS / REB / AST / 3PM / PR / PA / PRA) pour
      visualiser la perf du joueur sur ses 5 derniers matchs · L10/L20/H2H hit rates · médiane/moyenne ·
      <span style="color:#fb923c">ligne pointillée = ligne bookmaker</span> (ou médiane si pas dispo)
    </div>
    {nba_analyse_html}
  </div>

  <!-- Section Historique Foot -->
  <div id="sport-foothist" style="display:none">
    <div class="legend">
      <b>🏆 Historique Foot</b> — Picks foot résolus automatiquement via api-football après FT ·
      <span style="color:#22c55e">✓</span> gagné · <span style="color:#ef4444">✗</span> perdu · <span style="color:#94a3b8">=</span> push
    </div>
    {foot_hist_html}
  </div>

  <!-- Section Historique NBA -->
  <div id="sport-nbahist" style="display:none">
    <div class="legend">
      <b>🏆 Historique NBA</b> — Picks NBA résolus automatiquement via stats.nba.com après FT ·
      <b>Win Rate</b> = % succès sur picks décidés · <b>ROI</b> = profit/perte cumulé (mise 1u/pick aux cotes réelles) ·
      <span style="color:#22c55e">✓</span> gagné · <span style="color:#ef4444">✗</span> perdu · DNP = joueur absent
    </div>
    {nba_hist_html}
  </div>
  <footer>⚽ FotMob + api-football · 🏀 stats.nba.com + The Odds API · Algorithme + IA · À titre informatif uniquement</footer>
</div>
<script>
function showSport(sport){{
  ['football','nba','analyse','foothist','nbahist'].forEach(s=>{{
    var el = document.getElementById('sport-'+s);
    if(el) el.style.display = (s===sport) ? 'block' : 'none';
  }});
  document.querySelectorAll('.sport-btn').forEach(b=>b.classList.remove('active'));
  var btn = document.getElementById('sport-btn-'+sport);
  if(btn) btn.classList.add('active');
}}

// ── Section Analyse : selection prop (PTS/REB/...) + fenetre (L5/L10/L20) ──
function _stylePropBtn(btn, active){{
  btn.style.background = active ? '#3b82f6' : '#1e293b';
  btn.style.color      = active ? '#fff'    : '#94a3b8';
  btn.style.borderColor= active ? '#3b82f6' : '#334155';
  btn.classList.toggle('tg-prop-active', active);
}}
function _styleWindowBtn(btn, active){{
  btn.style.background = active ? '#fb923c' : '#1e293b';
  btn.style.color      = active ? '#0a1628' : '#94a3b8';
  btn.style.borderColor= active ? '#fb923c' : '#334155';
  btn.classList.toggle('tg-window-active', active);
}}

function selectPropChart(btn){{
  var card = btn.closest('.player-analyse');
  if(!card) return;
  card.querySelectorAll('.tg-prop-btn').forEach(b=>_stylePropBtn(b, false));
  _stylePropBtn(btn, true);
  // Affiche le content du prop selectionne
  var prop = btn.dataset.prop;
  card.querySelectorAll('.tg-prop-content').forEach(c=>{{
    c.style.display = (c.dataset.prop === prop) ? 'block' : 'none';
  }});
}}

function selectWindow(btn){{
  var card = btn.closest('.player-analyse');
  if(!card) return;
  card.querySelectorAll('.tg-window-btn').forEach(b=>_styleWindowBtn(b, false));
  _styleWindowBtn(btn, true);
  // Affiche les blocs du window selectionne (toutes props confondues)
  var win = btn.dataset.window;
  card.querySelectorAll('.tg-window-block').forEach(b=>{{
    b.style.display = (b.dataset.window === win) ? 'block' : 'none';
  }});
}}

function togglePlayerExpand(headerEl){{
  var card = headerEl.parentElement;
  var content = card.querySelector('.player-content');
  var arrow = headerEl.querySelector('.expand-arrow');
  if(!content) return;
  var isHidden = content.style.display === 'none';
  content.style.display = isHidden ? 'block' : 'none';
  if(arrow) arrow.textContent = isHidden ? '▼' : '▶';
}}

// Init : applique le style actif sur les boutons par defaut
window.addEventListener('DOMContentLoaded', function(){{
  document.querySelectorAll('.tg-prop-btn.tg-prop-active').forEach(b=>_stylePropBtn(b, true));
  document.querySelectorAll('.tg-window-btn.tg-window-active').forEach(b=>_styleWindowBtn(b, true));
}});

// ── Historique : filtrage par periode (boutons "Hier"/"7j"/"30j") ──
function filterHistory(containerId, period){{
  var container = document.getElementById(containerId);
  if(!container) return;
  var entries = container.querySelectorAll('details[data-date]');
  var today = new Date(); today.setHours(0,0,0,0);
  entries.forEach(function(el){{
    var dStr = el.getAttribute('data-date');
    if(period === 'all'){{ el.style.display = ''; return; }}
    var dParts = dStr.split('-');
    var dDate = new Date(parseInt(dParts[0]), parseInt(dParts[1])-1, parseInt(dParts[2]));
    var diffDays = Math.floor((today - dDate) / (1000*60*60*24));
    var n = parseInt(period);
    el.style.display = (diffDays >= 0 && diffDays <= n) ? '' : 'none';
  }});
  // Met a jour les boutons actifs
  // Container parent du filtre : on cherche le premier ancetre qui a des hist-btn
  var section = container.previousElementSibling;
  while(section && !section.querySelector){{ section = section.previousElementSibling; }}
  if(section){{
    var btns = section.querySelectorAll('button.hist-btn');
    btns.forEach(function(b){{
      var active = b.getAttribute('data-period') === period;
      b.style.background = active ? '#3b82f6' : '#1e293b';
      b.style.color = active ? '#fff' : '#94a3b8';
    }});
  }}
}}

// ── Historique : filtrage par date exacte (dropdown) ──
function filterHistoryDate(containerId, dateValue){{
  var container = document.getElementById(containerId);
  if(!container) return;
  var entries = container.querySelectorAll('details[data-date]');
  entries.forEach(function(el){{
    if(dateValue === 'all') {{ el.style.display = ''; return; }}
    el.style.display = (el.getAttribute('data-date') === dateValue) ? '' : 'none';
  }});
  // Reset les boutons periode
  var section = container.previousElementSibling;
  while(section && !section.querySelector){{ section = section.previousElementSibling; }}
  if(section){{
    section.querySelectorAll('button.hist-btn').forEach(function(b){{
      b.style.background = '#1e293b'; b.style.color = '#94a3b8';
    }});
  }}
}}
function showDay(id){{
  document.querySelectorAll('[id^="day"]').forEach(el=>el.style.display='none');
  document.querySelectorAll('#sport-football .tab-btn').forEach(btn=>btn.classList.remove('active'));
  document.getElementById(id).style.display='block';
  document.getElementById('btn-'+id).classList.add('active');
}}
function toggleForm(id){{
  var el = document.getElementById('form-'+id);
  if(el) el.style.display = el.style.display==='none' ? 'block' : 'none';
}}
function toggleStats(id){{
  var body  = document.getElementById('body-'+id);
  var arrow = document.getElementById('arrow-'+id);
  if(!body) return;
  if(body.classList.contains('show-stats')){{
    body.classList.remove('show-stats');
    arrow.style.transform='';
    arrow.style.color='#334155';
  }} else {{
    body.classList.add('show-stats');
    arrow.style.transform='rotate(180deg)';
    arrow.style.color='#3b82f6';
  }}
}}
function togglePicks(id){{
  var body = document.getElementById('body-'+id);
  var btn  = document.getElementById('picks-btn-'+id);
  if(!body) return;
  if(body.classList.contains('show-picks')){{
    body.classList.remove('show-picks');
    btn.classList.remove('active');
  }} else {{
    body.classList.add('show-picks');
    btn.classList.add('active');
  }}
}}

// ── Push Telegram (token + chat_id stockes en localStorage du navigateur) ──
async function pushTelegram(btn){{
  var token  = localStorage.getItem('tg_token');
  var chatId = localStorage.getItem('tg_chat_id');
  if(!token || !chatId){{
    token = prompt('Bot token Telegram (stocke localement dans ton navigateur uniquement, jamais sur le serveur) :', token || '');
    if(!token) return;
    chatId = prompt('Chat ID (ton ID perso ou @ton_canal) :', chatId || '');
    if(!chatId) return;
    localStorage.setItem('tg_token',  token.trim());
    localStorage.setItem('tg_chat_id', chatId.trim());
  }}
  var text = btn.dataset.text || '';
  if(!text){{ alert('Pas de message a envoyer'); return; }}
  var origBg = btn.style.background;
  btn.disabled = true;
  btn.innerText = '⏳';
  try {{
    var resp = await fetch('https://api.telegram.org/bot' + token + '/sendMessage', {{
      method: 'POST',
      headers: {{'Content-Type': 'application/json'}},
      body: JSON.stringify({{
        chat_id: chatId,
        text: text,
        parse_mode: 'HTML',
        disable_web_page_preview: true
      }})
    }});
    var j = await resp.json();
    if(j.ok){{
      btn.innerText = '✓';
      btn.style.background = '#22c55e';
    }} else {{
      btn.innerText = '✗';
      btn.style.background = '#ef4444';
      alert('Erreur Telegram : ' + (j.description || 'inconnue'));
      // Si auth fail, on reset le token pour reprompt
      if(j.error_code === 401){{ localStorage.removeItem('tg_token'); localStorage.removeItem('tg_chat_id'); }}
    }}
  }} catch(e){{
    btn.innerText = '✗';
    btn.style.background = '#ef4444';
    alert('Erreur reseau : ' + e.message);
  }}
  setTimeout(function(){{
    btn.disabled = false;
    btn.innerText = '📲';
    btn.style.background = origBg || '#0088cc';
  }}, 2500);
}}

// Bouton de reset des credentials Telegram (pour debug / changement de bot)
function resetTelegramCreds(){{
  localStorage.removeItem('tg_token');
  localStorage.removeItem('tg_chat_id');
  alert('Credentials Telegram effaces. Le prochain push te demandera de les re-entrer.');
}}

// ── Detection auto des nouveaux picks depuis derniere visite ──
// Au chargement : compare les pick_ids du DOM aux ids deja vus
// (localStorage). Badges 🆕 + halo orange restent visibles toute la session.
// Auto-marque comme vu immediatement => prochain refresh = nouveaux picks
// reels uniquement. Toast en haut a droite, auto-fade en 6 secondes.
function detectNewPicks(){{
  var seenRaw = localStorage.getItem('seen_picks');
  var seen = new Set();
  try {{
    if(seenRaw) seen = new Set(JSON.parse(seenRaw));
  }} catch(e){{}}

  var cards = document.querySelectorAll('[data-pick-id]');
  var newCount = 0;
  var isFirstVisit = !seenRaw;
  var currentIds = [];

  cards.forEach(function(card){{
    var pid = card.getAttribute('data-pick-id');
    if(!pid) return;
    currentIds.push(pid);
    if(!isFirstVisit && !seen.has(pid)){{
      var badge = card.querySelector('.new-badge');
      if(badge) badge.style.display = 'inline-block';
      card.style.boxShadow = '0 0 0 2px rgba(251,146,60,0.4)';
      newCount++;
    }}
  }});

  // Auto-marque immediatement comme vu (pas besoin de clic)
  localStorage.setItem('seen_picks', JSON.stringify(currentIds));

  // Toast non-bloquant uniquement si vraiment nouveau
  if(!isFirstVisit && newCount > 0){{
    var toast = document.getElementById('new-picks-toast');
    var cnt = document.getElementById('new-picks-count');
    if(toast && cnt){{
      cnt.textContent = newCount;
      toast.style.display = 'block';
      toast.style.opacity = '1';
      // Auto-fade apres 6 secondes
      setTimeout(function(){{
        toast.style.opacity = '0';
        setTimeout(function(){{ toast.style.display = 'none'; }}, 600);
      }}, 6000);
    }}
  }}
}}

// Reset manuel via console : resetSeenPicks()
function resetSeenPicks(){{
  localStorage.removeItem('seen_picks');
  alert('Liste des picks vus effacee. Refresh la page pour voir tous les picks comme nouveaux.');
}}

// Lance la detection au chargement de la page
window.addEventListener('DOMContentLoaded', detectNewPicks);
</script>
</body>
</html>'''

# ─── Main ─────────────────────────────────────────────────────────────────────

def push_to_github():
    """
    Pousse index.html directement sur gh-pages à chaque génération.
    Fonctionne même si index.html est dans .gitignore.
    En CI (GitHub Actions), skip cette logique : le workflow gère le push lui-même
    (evite les double-commits + conflicts).
    """
    import subprocess, os
    if os.environ.get("GITHUB_ACTIONS"):
        print("  ℹ️ CI detected - push gere par le workflow GitHub Actions")
        return
    try:
        # Vérifie git dispo
        result = subprocess.run(["git", "status"], capture_output=True, text=True, timeout=10)
        if result.returncode != 0:
            return

        result = subprocess.run(["git", "remote"], capture_output=True, text=True, timeout=10)
        if not result.stdout.strip():
            return

        print("\n📤 Push vers GitHub Pages...")

        # Force l'ajout même si dans .gitignore
        subprocess.run(["git", "add", "-f", "index.html"], check=True, timeout=10)

        # Vérifie s'il y a des changements
        result = subprocess.run(
            ["git", "diff", "--cached", "--quiet"],
            capture_output=True, timeout=10
        )
        if result.returncode == 0:
            print("  ℹ️ Aucun changement à pousser")
            return

        now = _now_paris().strftime("%d/%m/%Y %H:%M")
        subprocess.run(
            ["git", "commit", "-m", f"Update picks {now}"],
            check=True, timeout=15
        )

        # Push master:gh-pages (force pour écraser)
        subprocess.run(
            ["git", "push", "origin", "master:gh-pages", "--force"],
            check=True, timeout=30
        )
        print("  ✅ Publié sur GitHub Pages !")
        print("  🌐 https://nadzhh.github.io/sports-picks/")

    except subprocess.CalledProcessError as e:
        print(f"  ⚠️ Git push échoué: {e}")
    except FileNotFoundError:
        pass
    except Exception as e:
        print(f"  ⚠️ Push ignoré: {e}")


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

    # Charge NBA picks si dispo
    nba_picks = {}
    try:
        with open("data/nba_picks.json", encoding="utf-8") as f:
            nba_picks = json.load(f)
    except Exception:
        pass

    # Charge stats joueurs NBA (L20 games) pour la section Analyse
    nba_player_stats_data = {}
    try:
        with open("data/nba_player_stats.json", encoding="utf-8") as f:
            nba_player_stats_data = json.load(f)
    except Exception:
        pass

    # Charge cotes NBA pour les references de bar chart (ligne bookmaker)
    nba_odds_data = {}
    try:
        with open("data/nba_odds.json", encoding="utf-8") as f:
            raw = json.load(f)
            nba_odds_data = {k: v for k, v in raw.items() if not k.startswith("_")}
    except Exception:
        pass

    # Charge l'historique NBA + Foot
    nba_history = {"picks": []}
    foot_history = {"picks": []}
    try:
        with open("data/nba_picks_history.json", encoding="utf-8") as f:
            nba_history = json.load(f)
    except Exception:
        pass
    try:
        with open("data/picks_history.json", encoding="utf-8") as f:
            foot_history = json.load(f)
    except Exception:
        pass

    print("\n🌐 Génération du site...")
    html = build_html(matches, team_ai, player_ai, pstats_data, nba_picks=nba_picks, nba_history=nba_history, foot_history=foot_history, nba_player_stats=nba_player_stats_data, nba_odds=nba_odds_data)
    with open("index.html", "w", encoding="utf-8") as f:
        f.write(html)
    print(f"✅ index.html prêt — ⚽ {len(matches)} foot · 🏀 {len(nba_picks)} NBA")

    # Auto-push vers GitHub Pages si git configuré
    push_to_github()

if __name__ == "__main__":
    main()