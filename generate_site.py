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
    """Pastille Kelly sizing affichee pres de la cote.
    Le label initial est en 'u' (unite Kelly = % bankroll). JS au runtime
    convertit en € en lisant localStorage.user_bankroll_units."""
    if not stake_label:
        return ""
    color = "#22c55e"
    # Extrait la valeur unitaire pour data-units (pour conversion JS en €)
    units_value = 0
    if "0.25" in stake_label: units_value, color = 0.25, "#94a3b8"
    elif "0.5" in stake_label: units_value, color = 0.5, "#94a3b8"
    elif "1.5" in stake_label: units_value, color = 1.5, "#4ade80"
    elif "2"   in stake_label: units_value, color = 2.0, "#4ade80"
    elif "1"   in stake_label: units_value, color = 1.0, "#22c55e"
    title = f"Kelly fractional 1/4 - {kelly_pct}% du bankroll"
    return (
        f'<span class="tg-stake-pill" data-units="{units_value}" title="{title}" '
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
        # Signal POSITIF : bounce back attendu (L3 << saison)
        bounce_back = p.get("bounce_back", "")
        if bounce_back:
            rot_html += (
                f'<div style="color:#22d3ee;font-size:11px;font-weight:700;margin-top:3px;'
                f'background:rgba(34,211,238,0.10);border-left:2px solid #22d3ee;padding:3px 7px;border-radius:3px">'
                f'🔥 Bounce back : {bounce_back}'
                f'</div>'
            )
        # Signal POSITIF : serie playoff en cours
        if p.get("is_series"):
            ss = p.get("series_state", "")
            urgency = p.get("series_urgency", False)
            if urgency:
                rot_html += (
                    f'<div style="color:#f97316;font-size:11px;font-weight:700;margin-top:3px;'
                    f'background:rgba(249,115,22,0.10);border-left:2px solid #f97316;padding:3px 7px;border-radius:3px">'
                    f'⚡ Série {ss} : équipe doit step up, star usage probablement étendu'
                    f'</div>'
                )
            elif ss == "up":
                rot_html += (
                    f'<div style="color:#94a3b8;font-size:11px;font-weight:600;margin-top:3px">'
                    f'🏆 Série {ss} (avantage)'
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
            f'<button onclick="event.stopPropagation();goToAnalyse({_html.escape(p.get("player","?"), quote=True)!r}, {_html.escape(p.get("prop","?"), quote=True)!r})" '
            f'title="Voir l\'analyse complete du joueur" '
            f'style="background:#1e3a8a;color:#bfdbfe;border:1px solid #3b82f6;border-radius:6px;'
            f'padding:3px 8px;font-size:12px;font-weight:700;cursor:pointer">🔍</button>'
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
    """Calcule la valeur du stat pour 1 game donne (PTS, REB, AST, FG3M, RA, PR, PA, PRA)."""
    pts = g.get("PTS", 0) or 0
    reb = g.get("REB", 0) or 0
    ast = g.get("AST", 0) or 0
    fg3 = g.get("FG3M", 0) or 0
    return {
        "PTS":  pts,
        "REB":  reb,
        "AST":  ast,
        "FG3M": fg3,
        "RA":   reb + ast,
        "PR":   pts + reb,
        "PA":   pts + ast,
        "PRA":  pts + reb + ast,
    }.get(prop, 0)


def _build_prop_chart_bars(games_window, ref_line, chart_max, with_labels=True):
    """Construit la zone bar-chart (bars + labels). Helper interne, factorise
    pour pouvoir reutiliser sur L5/L10/L20.
    Les bars ont data-value pour permettre la recoloration JS quand l'user
    deplace la ligne de reference."""
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
            f'<div class="tg-bar" data-value="{val}" '
            f'style="flex:1;display:flex;flex-direction:column;align-items:center;justify-content:flex-end;min-width:0">'
            f'<div style="color:#f1f5f9;font-size:{font_val}px;font-weight:700;margin-bottom:3px">{int(val)}</div>'
            f'<div class="tg-bar-fill" style="width:100%;background:{color};height:{bar_px}px;border-radius:3px 3px 0 0"></div>'
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
        f'<div class="tg-chart-area" data-chart-max="{chart_max}" data-chart-px="{CHART_PX}" '
        f'style="position:relative;height:{CHART_PX + 22}px">'
        f'<div style="display:flex;gap:{gap}px;align-items:flex-end;height:{CHART_PX + 18}px">{bars_html}</div>'
        f'<div class="tg-ref-line" style="position:absolute;left:0;right:0;bottom:{ref_px}px;border-top:2px dashed #fb923c;pointer-events:none">'
        f'<span class="tg-ref-label" style="position:absolute;right:0;top:-9px;background:#fb923c;color:#0a1628;font-size:10px;font-weight:700;padding:1px 6px;border-radius:3px">{ref_line}</span>'
        f'</div>'
        f'</div>'
        f'{labels_block}'
    )


def _build_prop_chart(player, prop, opp_abbr, book_line=None, match_ctx=None, book_over=None, book_under=None):
    """Construit le HTML d'une vue prop : badges hit rates + 3 bar charts
    (L5/L10/L20) selectionnables. Affiche L5 par defaut.
    match_ctx (optionnel): {home, away, game_id} pour les boutons 'add user pick'.
    book_over/book_under (optionnel): cotes proposees par le bookmaker, pre-remplies
    quand on ajoute un user pick."""
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

    import math as _math
    median = sorted(l20_vals)[len(l20_vals)//2] if l20_vals else 0
    mean   = round(sum(l20_vals)/len(l20_vals), 1) if l20_vals else 0
    # Snap au demi-point le + proche (.5) car les bookmakers quotent toujours
    # par x.5 pour eviter les pushes. Ex: mediane 10 -> ligne 10.5, mediane
    # 7.3 -> ligne 7.5, mediane 11 -> ligne 11.5.
    def _to_half_step(v):
        return _math.floor(v) + 0.5
    ref_line = book_line if book_line is not None else _to_half_step(median)

    def _hr(vals):
        if not vals: return None, 0, 0
        hits = sum(1 for v in vals if v > ref_line)
        return round(hits/len(vals)*100), hits, len(vals)
    hr5  = _hr(l5_vals)
    hr10 = _hr(l10_vals)
    hr20 = _hr(l20_vals)
    hr_h2h = _hr(h2h_vals)

    def _badge_html(label, hr_tuple, target=70, data_key=""):
        """Span avec classe tg-hr-badge pour pouvoir le mettre a jour en JS."""
        if hr_tuple[0] is None:
            return f'<span class="tg-hr-badge" data-key="{data_key}" style="color:#475569;font-size:11px;margin-right:8px">{label}: —</span>'
        pct, w, n = hr_tuple
        color = "#22c55e" if pct >= target else ("#f59e0b" if pct >= 50 else "#ef4444")
        return (
            f'<span class="tg-hr-badge" data-key="{data_key}" data-label="{label}" '
            f'style="color:{color};font-size:11px;font-weight:700;margin-right:8px">'
            f'{label}: {pct}%<span style="color:#64748b;font-weight:400"> ({w}/{n})</span></span>'
        )

    badges = (
        _badge_html("L5", hr5, data_key="l5") +
        _badge_html("L10", hr10, data_key="l10") +
        _badge_html("L20", hr20, data_key="l20") +
        (_badge_html(f"H2H {opp_abbr}", hr_h2h, data_key="h2h") if opp_abbr and hr_h2h[0] is not None else "") +
        f'<span style="color:#94a3b8;font-size:11px">Méd. <b>{median}</b> · Moy. <b>{mean}</b></span>'
    )

    # Stocke les valeurs JS pour pouvoir recalculer hit rates a la volee
    import json as _json
    vals_data = _json.dumps({
        "l5":  [v for _, v in pairs_l5],
        "l10": [v for _, v in pairs_l10],
        "l20": [v for _, v in pairs_l20],
        "h2h": [v for _, v in h2h_pairs],
    })
    vals_attr = _html.escape(vals_data, quote=True)

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

    # Boutons "Add to picks" : OVER / UNDER avec la ligne courante (book ou mediane).
    # Inclut les cotes du book (si dispo) pour les pre-remplir au moment du add.
    import json as _json
    player_name = player.get("name", "?")
    ctx = match_ctx or {}
    payload = _json.dumps({
        "sport":      "NBA",
        "player":     player_name,
        "prop":       prop,
        "line":       ref_line,
        "home":       ctx.get("home", ""),
        "away":       ctx.get("away", ""),
        "game_id":    ctx.get("game_id", ""),
        "opp":        opp_abbr or "",
        "median":     median,
        "mean":       mean,
        "book_line":  book_line,
        "book_over":  book_over,
        "book_under": book_under,
    }, ensure_ascii=False)
    payload_esc = _html.escape(payload, quote=True)
    # Affichage des cotes book sur les boutons si dispo
    over_label  = f"📌 Add OVER {ref_line}"  + (f" @ {book_over}"  if book_over  else "")
    under_label = f"📌 Add UNDER {ref_line}" + (f" @ {book_under}" if book_under else "")
    add_buttons = (
        f'<div style="display:flex;gap:6px;margin-top:8px;padding-top:8px;border-top:1px solid #1e293b">'
        f'<button class="tg-userpick-btn" data-direction="over" data-payload="{payload_esc}" '
        f'onclick="addUserPick(this)" '
        f'style="flex:1;background:#16a34a;color:#fff;border:none;border-radius:6px;padding:5px 10px;'
        f'font-size:11px;font-weight:700;cursor:pointer">{over_label}</button>'
        f'<button class="tg-userpick-btn" data-direction="under" data-payload="{payload_esc}" '
        f'onclick="addUserPick(this)" '
        f'style="flex:1;background:#dc2626;color:#fff;border:none;border-radius:6px;padding:5px 10px;'
        f'font-size:11px;font-weight:700;cursor:pointer">{under_label}</button>'
        f'</div>'
    )

    # Controle pour ajuster la ligne de reference (bookmakers quotent en x.5
    # uniquement, donc on bouge par pas de 1 sur la grille x.5)
    line_control = (
        f'<div style="display:flex;align-items:center;gap:6px;margin-bottom:8px;flex-wrap:wrap">'
        f'<span style="color:#94a3b8;font-size:11px;font-weight:700">Ligne :</span>'
        f'<button onclick="adjustLine(this, -2)" '
        f'style="background:#1e293b;color:#94a3b8;border:1px solid #334155;border-radius:5px;'
        f'padding:2px 9px;font-size:13px;font-weight:700;cursor:pointer">−2</button>'
        f'<button onclick="adjustLine(this, -1)" '
        f'style="background:#1e293b;color:#94a3b8;border:1px solid #334155;border-radius:5px;'
        f'padding:2px 9px;font-size:13px;font-weight:700;cursor:pointer">−1</button>'
        f'<input class="tg-line-input" type="number" step="1" min="0.5" value="{ref_line}" '
        f'onchange="onLineInputChange(this)" '
        f'style="background:#0a1628;color:#fb923c;border:1px solid #fb923c;border-radius:5px;'
        f'padding:2px 6px;font-size:13px;font-weight:700;width:64px;text-align:center">'
        f'<button onclick="adjustLine(this, +1)" '
        f'style="background:#1e293b;color:#94a3b8;border:1px solid #334155;border-radius:5px;'
        f'padding:2px 9px;font-size:13px;font-weight:700;cursor:pointer">+1</button>'
        f'<button onclick="adjustLine(this, +2)" '
        f'style="background:#1e293b;color:#94a3b8;border:1px solid #334155;border-radius:5px;'
        f'padding:2px 9px;font-size:13px;font-weight:700;cursor:pointer">+2</button>'
        f'<button onclick="resetLine(this)" data-default="{ref_line}" '
        f'style="background:#1e293b;color:#94a3b8;border:1px solid #334155;border-radius:5px;'
        f'padding:2px 9px;font-size:11px;font-weight:700;cursor:pointer">↺ {("book" if book_line is not None else "med")}</button>'
        f'</div>'
    )

    return (
        f'<div class="tg-prop-chart" data-vals="{vals_attr}" data-default-line="{ref_line}" '
        f'data-book-line="{"" if book_line is None else book_line}" data-median="{median}" '
        f'style="padding:6px 4px">'
        # Controle ligne
        f'{line_control}'
        # Badges hit rates en haut
        f'<div class="tg-badges-row" style="margin-bottom:8px;display:flex;flex-wrap:wrap;gap:4px;align-items:center">{badges}</div>'
        # Zone chart container
        f'<div style="background:#0a1628;border-radius:8px;padding:10px 12px 6px">'
        f'{chart_blocks}'
        f'</div>'
        # Boutons add to picks
        f'{add_buttons}'
        f'</div>'
    )


def _build_player_analyse_card(player, opp_abbr, odds_for_player, side_label, is_starter=True, match_ctx=None):
    """Card d'analyse joueur. Starters : expanded par defaut. Bench : collapsed
    (juste le nom + min). Clic sur header = toggle expand."""
    name = player.get("name", "?")
    pos = player.get("position", "")
    season = player.get("season_avg", {}) or {}
    mins = season.get("MIN", 0)
    pos_b = pos_badge(pos) if pos else ""
    safe_id = "".join(c for c in name if c.isalnum())

    PROPS = ["PTS", "REB", "AST", "FG3M", "RA", "PR", "PA", "PRA"]
    prop_labels = {"PTS":"PTS","REB":"REB","AST":"AST","FG3M":"3PM",
                   "RA":"REB+AST","PR":"PTS+REB","PA":"PTS+AST","PRA":"PTS+REB+AST"}

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
        book_line = book_data.get("line")  if book_data else None
        book_over  = book_data.get("over")  if book_data else None
        book_under = book_data.get("under") if book_data else None
        chart_html = _build_prop_chart(player, pr, opp_abbr, book_line,
                                       match_ctx=match_ctx,
                                       book_over=book_over, book_under=book_under)
        prop_display = "block" if i == 0 else "none"
        contents += (
            f'<div class="tg-prop-content" data-prop="{pr}" style="display:{prop_display}">{chart_html}</div>'
        )

    content_display = "block" if is_starter else "none"
    arrow = "▼" if is_starter else "▶"
    starter_badge = '<span style="background:#16a34a;color:#fff;border-radius:3px;padding:1px 5px;font-size:9px;font-weight:800;margin-left:4px">TITULAIRE</span>' if is_starter else ""

    return (
        f'<div class="player-analyse" data-player-name="{_html.escape(name, quote=True)}" '
        f'data-is-starter="{1 if is_starter else 0}" '
        f'style="background:#162032;border-radius:10px;padding:10px 14px;margin-bottom:8px;'
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
        match_ctx = {"home": home, "away": away, "game_id": gid}

        home_cards = "".join(
            _build_player_analyse_card(p, opp_abbr=away_abbr, odds_for_player=odds_for_game.get(p.get("name","")),
                                       side_label=f"🏠 {home}", is_starter=(i < 5), match_ctx=match_ctx)
            for i, p in enumerate(home_players)
        )
        away_cards = "".join(
            _build_player_analyse_card(p, opp_abbr=home_abbr, odds_for_player=odds_for_game.get(p.get("name","")),
                                       side_label=f"✈️ {away}", is_starter=(i < 5), match_ctx=match_ctx)
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

    # Embed minimal nba_history pour resolution auto JS des user picks.
    # On garde uniquement les fields necessaires : game_id, player, prop, line,
    # direction, result, actual, date.
    import json as _json
    nba_hist_min = []
    for p in (nba_history or {}).get("picks", []):
        if p.get("result") not in ("WIN", "LOSS", "PUSH", "DNP"): continue
        nba_hist_min.append({
            "game_id":   p.get("game_id"),
            "player":    p.get("player"),
            "prop":      p.get("prop"),
            "line":      p.get("line"),
            "direction": p.get("direction"),
            "result":    p.get("result"),
            "actual":    p.get("actual"),
            "date":      p.get("date"),
        })
    nba_history_json = _json.dumps(nba_hist_min, ensure_ascii=False).replace("</", "<\\/")
    # Egalement ecrit sur disque pour polling cote client (auto-refresh statut bets)
    try:
        import os as _os
        _os.makedirs("data", exist_ok=True)
        with open("data/nba_history_min.json", "w", encoding="utf-8") as _hf:
            _json.dump(nba_hist_min, _hf, ensure_ascii=False)
    except Exception as _e:
        print(f"  ⚠️ Impossible d'ecrire nba_history_min.json: {_e}")

    total_t = sum(len(m["picks"]) for m in matches)
    total_p = sum(len(m.get("home_players",[])) + len(m.get("away_players",[])) for m in matches)

    return f'''<!DOCTYPE html>
<html lang="fr">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>Sports Picks — {now}</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Geist:wght@400;500;600;700;800&display=swap" rel="stylesheet">
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

  /* Pulse animation pour badge EN COURS */
  @keyframes pulse {{
    0%, 100% {{ opacity: 1; }}
    50% {{ opacity: 0.65; }}
  }}

  /* ── Bankroll section : redesign mobile-first adapté PC ───────────────── */
  /* Scope étanche : aucune autre section n'est affectée par ces règles. */
  #sport-userpicks {{
    --bk-bg:           #08090D;
    --bk-surface:      #14161B;
    --bk-surface-2:    #0E1014;
    --bk-card-grad:    linear-gradient(180deg, #1A1D24 0%, #14161B 100%);
    --bk-pill-active:  #1F232C;
    --bk-border:       rgba(255,255,255,0.05);
    --bk-border-strong:rgba(255,255,255,0.08);
    --bk-hairline:     rgba(255,255,255,0.04);
    --bk-text:         #ffffff;
    --bk-text-muted:   #8B8D98;
    --bk-text-soft:    rgba(255,255,255,0.06);
    --bk-accent:       #34D399;
    --bk-loss:         #F87171;
    --bk-pending:      #FBBF24;
    --bk-push:         #94A3B8;
    background:
      radial-gradient(circle at 18% 0%,  rgba(52,211,153,0.07), transparent 55%),
      radial-gradient(circle at 82% 100%, rgba(251,191,36,0.045), transparent 55%),
      #0a0a0a;
    border-radius: 24px;
    padding: 28px 30px 40px;
    border: 1px solid var(--bk-border);
    color: var(--bk-text);
    margin-top: -4px;
  }}
  .bk-app, .bk-app * {{
    font-family: 'Geist', -apple-system, BlinkMacSystemFont, 'SF Pro Display', system-ui, sans-serif;
    -webkit-font-smoothing: antialiased;
  }}
  .bk-app {{ display: flex; flex-direction: column; gap: 18px; }}

  /* Hero */
  .bk-hero {{ display: flex; align-items: flex-end; justify-content: space-between; gap: 28px; flex-wrap: wrap; padding: 4px 4px 8px; }}
  .bk-hero-left {{ min-width: 0; flex: 1; }}
  .bk-hero-eyebrow {{ color: var(--bk-text-muted); font-size: 12px; font-weight: 600; margin-bottom: 6px; text-transform: uppercase; letter-spacing: 1.4px; }}
  .bk-hero-amount {{ color: var(--bk-text); font-weight: 800; font-size: 64px; letter-spacing: -2.4px; line-height: 1.02; display: flex; align-items: baseline; gap: 10px; font-variant-numeric: tabular-nums; }}
  .bk-hero-amount .bk-cur {{ color: var(--bk-text-muted); font-weight: 600; font-size: 34px; letter-spacing: -1px; }}
  .bk-hero-meta {{ margin-top: 12px; display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }}
  .bk-hero-delta {{ display: inline-flex; align-items: center; gap: 7px; padding: 6px 12px; border-radius: 999px; font-size: 13px; font-weight: 700; font-variant-numeric: tabular-nums; }}
  .bk-hero-delta.pos {{ background: rgba(52,211,153,0.12); color: #34D399; }}
  .bk-hero-delta.neg {{ background: rgba(248,113,113,0.10); color: #F87171; }}
  .bk-hero-delta.flat {{ background: rgba(148,163,184,0.10); color: #94A3B8; }}
  .bk-hero-sub {{ color: var(--bk-text-muted); font-size: 12.5px; }}
  .bk-hero-right {{ display: flex; flex-direction: column; gap: 8px; align-items: flex-end; }}

  /* Card primitive */
  .bk-card {{ background: var(--bk-card-grad); border: 1px solid var(--bk-border); border-radius: 22px; padding: 18px; }}
  .bk-card-hd {{ display: flex; align-items: center; justify-content: space-between; margin-bottom: 14px; gap: 12px; }}
  .bk-card-title {{ color: var(--bk-text); font-weight: 600; font-size: 16px; letter-spacing: -0.1px; display: flex; align-items: center; gap: 10px; }}
  .bk-eyebrow {{ color: var(--bk-text-muted); font-size: 11.5px; font-weight: 600; text-transform: uppercase; letter-spacing: 0.6px; }}

  /* Pills / segment */
  .bk-segment {{ display: inline-flex; gap: 2px; padding: 3px; background: var(--bk-surface-2); border-radius: 999px; border: 1px solid var(--bk-border); }}
  .bk-segment button {{ padding: 6px 13px; border-radius: 999px; border: none; background: transparent; color: var(--bk-text-muted); font-size: 12px; font-weight: 600; cursor: pointer; transition: all 180ms ease; }}
  .bk-segment button.active {{ background: var(--bk-pill-active); color: var(--bk-text); }}

  /* Buttons */
  .bk-btn {{ background: var(--bk-surface); border: 1px solid var(--bk-border-strong); color: var(--bk-text); padding: 7px 13px; border-radius: 12px; font-size: 12.5px; font-weight: 600; cursor: pointer; transition: background 150ms; }}
  .bk-btn:hover {{ background: var(--bk-surface-2); }}
  .bk-btn-accent {{ background: linear-gradient(180deg, #34D399, #10B981); border: none; color: #06120E; box-shadow: 0 6px 18px rgba(52,211,153,0.25); }}
  .bk-btn-ghost {{ background: transparent; border: 1px solid var(--bk-border-strong); color: var(--bk-text-muted); }}
  .bk-btn-ghost:hover {{ color: var(--bk-text); background: var(--bk-text-soft); }}

  /* StatCards (3 across) */
  .bk-stats {{ display: grid; grid-template-columns: repeat(3, 1fr); gap: 12px; }}
  .bk-stat {{ background: var(--bk-surface); border: 1px solid var(--bk-border); border-radius: 18px; padding: 14px 18px; }}
  .bk-stat-label {{ color: var(--bk-text-muted); font-size: 11.5px; font-weight: 500; margin-bottom: 6px; letter-spacing: 0.4px; text-transform: uppercase; }}
  .bk-stat-value {{ color: var(--bk-text); font-weight: 700; font-size: 24px; letter-spacing: -0.4px; font-variant-numeric: tabular-nums; }}
  .bk-stat-sub {{ font-size: 11.5px; margin-top: 4px; font-weight: 600; font-variant-numeric: tabular-nums; }}

  /* Streak */
  .bk-streak {{ display: flex; align-items: center; gap: 14px; padding: 14px 16px; border-radius: 18px; background: linear-gradient(135deg, rgba(249,115,22,0.16), rgba(220,38,38,0.06)); border: 1px solid rgba(249,115,22,0.22); }}
  .bk-streak-flame {{ width: 44px; height: 44px; border-radius: 14px; background: radial-gradient(circle at 30% 20%, #FBBF24, #F97316 60%, #DC2626 100%); display: flex; align-items: center; justify-content: center; font-size: 22px; box-shadow: 0 0 18px rgba(249,115,22,0.5); animation: bk-flame 2.4s ease-in-out infinite; }}
  .bk-streak-title {{ color: var(--bk-text); font-weight: 700; font-size: 15px; }}
  .bk-streak-sub {{ color: #FBBF24; font-size: 12.5px; font-weight: 500; margin-top: 2px; }}
  @keyframes bk-flame {{
    0%, 100% {{ transform: scale(1) rotate(-2deg); }}
    50%      {{ transform: scale(1.08) rotate(2deg); }}
  }}

  /* 2-column main grid */
  .bk-cols {{ display: grid; grid-template-columns: 1.45fr 1fr; gap: 16px; align-items: start; }}
  @media (max-width: 1024px) {{ .bk-cols {{ grid-template-columns: 1fr; }} }}
  .bk-col {{ display: flex; flex-direction: column; gap: 16px; }}

  /* Bet rows */
  .bk-rows {{ display: flex; flex-direction: column; }}
  .bk-row {{ width: 100%; padding: 12px 14px; display: flex; gap: 14px; align-items: center; background: transparent; border: none; text-align: left; border-radius: 14px; transition: background 150ms ease, transform 120ms; color: inherit; cursor: default; }}
  .bk-row:hover {{ background: var(--bk-text-soft); }}
  .bk-row + .bk-row {{ border-top: 1px solid var(--bk-hairline); }}
  .bk-row-icon {{ width: 40px; height: 40px; border-radius: 12px; background: rgba(251,146,60,0.15); border: 1px solid rgba(251,146,60,0.30); display: flex; align-items: center; justify-content: center; font-size: 20px; flex-shrink: 0; }}
  .bk-row-main {{ flex: 1; min-width: 0; }}
  .bk-row-title {{ color: var(--bk-text); font-weight: 600; font-size: 14.5px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; max-width: 100%; margin-bottom: 2px; }}
  .bk-row-sub {{ color: var(--bk-text-muted); font-size: 12.5px; display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }}
  .bk-row-sub .dot {{ opacity: 0.5; }}
  .bk-row-side {{ text-align: right; display: flex; flex-direction: column; align-items: flex-end; gap: 5px; flex-shrink: 0; }}
  .bk-row-amt {{ font-size: 14.5px; font-weight: 700; font-variant-numeric: tabular-nums; color: var(--bk-text); }}
  .bk-row-amt.pos {{ color: #34D399; }}
  .bk-row-amt.neg {{ color: #F87171; }}
  .bk-row-amt.flat {{ color: #94A3B8; }}
  .bk-row-actions {{ display: flex; gap: 4px; margin-top: 4px; flex-wrap: wrap; justify-content: flex-end; }}
  .bk-mini-btn {{ background: var(--bk-surface); border: 1px solid var(--bk-border-strong); color: var(--bk-text-muted); padding: 4px 9px; border-radius: 8px; font-size: 11px; font-weight: 600; cursor: pointer; transition: all 150ms; }}
  .bk-mini-btn:hover {{ background: var(--bk-text-soft); color: var(--bk-text); }}
  .bk-mini-btn.tg {{ background: rgba(56,189,248,0.12); border-color: rgba(56,189,248,0.25); color: #7dd3fc; }}
  .bk-mini-btn.tg:hover {{ background: rgba(56,189,248,0.18); color: #fff; }}
  .bk-mini-btn.edit {{ background: rgba(168,85,247,0.12); border-color: rgba(168,85,247,0.25); color: #d8b4fe; }}
  .bk-mini-btn.del {{ background: rgba(248,113,113,0.08); border-color: rgba(248,113,113,0.18); color: #fca5a5; }}

  /* Status badge */
  .bk-badge {{ display: inline-flex; align-items: center; gap: 6px; padding: 3px 10px; border-radius: 999px; font-size: 11px; font-weight: 600; letter-spacing: 0.1px; white-space: nowrap; }}
  .bk-badge .dot {{ width: 6px; height: 6px; border-radius: 999px; flex-shrink: 0; }}
  .bk-badge.won {{ background: rgba(52,211,153,0.12); color: #34D399; }}
  .bk-badge.won .dot {{ background: #34D399; }}
  .bk-badge.lost {{ background: rgba(248,113,113,0.10); color: #F87171; }}
  .bk-badge.lost .dot {{ background: #F87171; }}
  .bk-badge.pending {{ background: rgba(251,191,36,0.12); color: #FBBF24; }}
  .bk-badge.pending .dot {{ background: #FBBF24; animation: bk-pulse 1.6s ease-in-out infinite; }}
  .bk-badge.push {{ background: rgba(148,163,184,0.10); color: #94A3B8; }}
  .bk-badge.push .dot {{ background: #94A3B8; }}
  @keyframes bk-pulse {{
    0%, 100% {{ opacity: 1; transform: scale(1); }}
    50%      {{ opacity: 0.55; transform: scale(0.85); }}
  }}

  /* Cote chip */
  .bk-cote {{ display: inline-flex; align-items: center; gap: 4px; background: rgba(56,189,248,0.10); color: #7dd3fc; border: 1px solid rgba(56,189,248,0.22); padding: 2px 8px; border-radius: 999px; font-size: 11.5px; font-weight: 700; font-variant-numeric: tabular-nums; }}
  .bk-cote.warn {{ background: rgba(251,146,60,0.10); border-color: rgba(251,146,60,0.30); color: #fdba74; cursor: pointer; }}

  /* Chart */
  .bk-chart-wrap {{ width: 100%; }}
  .bk-chart-line {{ stroke-dasharray: 3000; stroke-dashoffset: 3000; animation: bk-draw 1100ms cubic-bezier(.4,.0,.2,1) forwards; }}
  @keyframes bk-draw {{ to {{ stroke-dashoffset: 0; }} }}

  /* Prop / market breakdown */
  .bk-prop-row {{ display: grid; grid-template-columns: 130px 1fr 64px 80px; gap: 12px; align-items: center; padding: 10px 4px; }}
  .bk-prop-row + .bk-prop-row {{ border-top: 1px solid var(--bk-hairline); }}
  .bk-prop-name {{ display: flex; align-items: center; gap: 8px; color: var(--bk-text); font-weight: 600; font-size: 13px; }}
  .bk-prop-bar {{ height: 6px; background: var(--bk-text-soft); border-radius: 999px; overflow: hidden; }}
  .bk-prop-bar > div {{ height: 100%; border-radius: 999px; }}
  .bk-prop-wr {{ text-align: right; font-weight: 700; font-size: 12.5px; font-variant-numeric: tabular-nums; }}
  .bk-prop-profit {{ text-align: right; font-weight: 700; font-size: 13px; font-variant-numeric: tabular-nums; }}

  /* Advice rows */
  .bk-advice {{ display: flex; flex-direction: column; gap: 8px; }}
  .bk-advice-row {{ display: flex; gap: 12px; padding: 11px 13px; border-radius: 14px; background: var(--bk-surface); border-left: 3px solid; font-size: 13px; line-height: 1.5; color: #e2e8f0; }}
  .bk-advice-row .ic {{ font-size: 16px; flex-shrink: 0; line-height: 1.4; }}
  .bk-advice-row b {{ color: var(--bk-text); font-weight: 700; }}

  /* Empty state */
  .bk-empty {{ text-align: center; padding: 56px 20px; border-radius: 22px; background: var(--bk-surface); border: 1px dashed var(--bk-border-strong); }}
  .bk-empty-emoji {{ font-size: 48px; margin-bottom: 14px; }}
  .bk-empty-title {{ color: var(--bk-text); font-weight: 700; font-size: 17px; margin-bottom: 6px; }}
  .bk-empty-sub {{ color: var(--bk-text-muted); font-size: 13.5px; line-height: 1.6; max-width: 440px; margin: 0 auto; }}

  /* Chart period selector spacing */
  .bk-period-label {{ color: var(--bk-text-muted); font-size: 12px; font-weight: 600; }}

  /* ── Filter chips (Historique) ─────────────────────── */
  #sport-userpicks .bk-filter-row {{ display: flex; gap: 6px; flex-wrap: wrap; margin-bottom: 12px; }}
  #sport-userpicks .bk-filter-chip {{ display: inline-flex; align-items: center; gap: 7px; padding: 7px 12px; border-radius: 999px; background: var(--bk-surface); border: 1px solid var(--bk-border); color: var(--bk-text-muted); font-size: 12.5px; font-weight: 600; cursor: pointer; transition: all 180ms ease; }}
  #sport-userpicks .bk-filter-chip:hover {{ color: var(--bk-text); }}
  #sport-userpicks .bk-filter-chip.active {{ background: var(--bk-pill-active); border-color: var(--bk-border-strong); color: var(--bk-text); }}
  #sport-userpicks .bk-filter-chip .dot {{ width: 7px; height: 7px; border-radius: 999px; flex-shrink: 0; }}
  #sport-userpicks .bk-filter-chip .count {{ padding: 1px 7px; border-radius: 999px; font-size: 10.5px; font-weight: 700; background: var(--bk-text-soft); color: var(--bk-text-muted); font-variant-numeric: tabular-nums; }}
  #sport-userpicks .bk-filter-chip.active .count {{ background: rgba(255,255,255,0.10); color: var(--bk-text); }}
  #sport-userpicks .bk-filter-reset {{ background: transparent; border: none; color: #34D399; font-size: 12px; font-weight: 600; cursor: pointer; padding: 0 4px; }}
  #sport-userpicks .bk-filter-empty {{ padding: 30px 14px; text-align: center; color: var(--bk-text-muted); font-size: 13px; }}

  /* ── Modal Nouveau Pari (overlay global, ouvert depuis Analyse NBA) ──── */
  #bk-modal-root {{
    position: fixed; inset: 0; z-index: 9999;
    display: none; align-items: center; justify-content: center;
    pointer-events: none;
    font-family: 'Geist', -apple-system, BlinkMacSystemFont, system-ui, sans-serif;
  }}
  #bk-modal-root.open {{ display: flex; pointer-events: auto; }}
  #bk-modal-root .bk-modal-bd {{
    position: absolute; inset: 0;
    background: rgba(0,0,0,0.55);
    backdrop-filter: blur(4px); -webkit-backdrop-filter: blur(4px);
    opacity: 0; transition: opacity 220ms ease;
  }}
  #bk-modal-root.open .bk-modal-bd {{ opacity: 1; }}
  #bk-modal-root .bk-modal-card {{
    position: relative; z-index: 1;
    background: #0F1115; border: 1px solid rgba(255,255,255,0.08);
    border-radius: 22px; width: min(540px, 92vw); max-height: 88vh;
    display: flex; flex-direction: column;
    box-shadow: 0 24px 60px rgba(0,0,0,0.6);
    opacity: 0; transform: translateY(20px) scale(0.97);
    transition: opacity 220ms ease, transform 280ms cubic-bezier(.22,.85,.3,1);
    color: #fff;
  }}
  #bk-modal-root.open .bk-modal-card {{ opacity: 1; transform: translateY(0) scale(1); }}
  #bk-modal-root .bk-m-hd {{
    padding: 16px 22px; display: flex; align-items: center; justify-content: space-between; gap: 12px;
    border-bottom: 1px solid rgba(255,255,255,0.04);
  }}
  #bk-modal-root .bk-m-title {{ color: #fff; font-weight: 600; font-size: 17px; }}
  #bk-modal-root .bk-m-cancel {{
    background: transparent; border: none; color: #8B8D98;
    font-size: 14px; font-weight: 600; cursor: pointer; padding: 6px 10px;
    font-family: inherit;
  }}
  #bk-modal-root .bk-m-cancel:hover {{ color: #fff; }}
  #bk-modal-root .bk-m-body {{ padding: 22px 22px 12px; overflow-y: auto; flex: 1; }}
  #bk-modal-root .bk-m-ft {{
    padding: 14px 22px 18px; border-top: 1px solid rgba(255,255,255,0.04);
    background: #0B0D11;
    border-bottom-left-radius: 22px; border-bottom-right-radius: 22px;
  }}
  #bk-modal-root .bk-m-context {{
    background: #14161B; border: 1px solid rgba(255,255,255,0.05);
    border-radius: 14px; padding: 14px;
    display: flex; gap: 12px; align-items: center; margin-bottom: 18px;
  }}
  #bk-modal-root .bk-m-icon {{
    width: 42px; height: 42px; border-radius: 12px;
    background: rgba(251,146,60,0.15); border: 1px solid rgba(251,146,60,0.30);
    display: flex; align-items: center; justify-content: center;
    font-size: 22px; flex-shrink: 0;
  }}
  #bk-modal-root .bk-m-ctx-title {{ color: #fff; font-weight: 600; font-size: 15px; margin-bottom: 3px; }}
  #bk-modal-root .bk-m-ctx-sub {{ color: #8B8D98; font-size: 12.5px; display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }}
  #bk-modal-root .bk-m-ctx-sub .sep {{ opacity: 0.5; }}
  #bk-modal-root .bk-dir-chip {{ padding: 3px 9px; border-radius: 999px; font-size: 11px; font-weight: 700; letter-spacing: 0.3px; }}
  #bk-modal-root .bk-dir-chip.over {{ background: rgba(52,211,153,0.14); color: #34D399; }}
  #bk-modal-root .bk-dir-chip.under {{ background: rgba(248,113,113,0.10); color: #F87171; }}
  #bk-modal-root .bk-m-label {{
    color: #8B8D98; font-size: 11.5px; font-weight: 600;
    text-transform: uppercase; letter-spacing: 0.4px; margin-bottom: 6px; display: block;
  }}
  #bk-modal-root .bk-m-label .opt {{ color: #8B8D98; font-weight: 500; text-transform: none; letter-spacing: 0; opacity: 0.6; margin-left: 4px; }}
  #bk-modal-root .bk-m-input {{
    width: 100%; padding: 12px 14px; border-radius: 14px;
    background: #14161B; border: 1px solid rgba(255,255,255,0.06);
    color: #fff; font-size: 15px; font-weight: 500; outline: none;
    font-family: inherit; box-sizing: border-box; font-variant-numeric: tabular-nums;
  }}
  #bk-modal-root .bk-m-input:focus {{ border-color: rgba(52,211,153,0.5); box-shadow: 0 0 0 3px rgba(52,211,153,0.12); }}
  #bk-modal-root textarea.bk-m-input {{ resize: none; line-height: 1.5; font-size: 14px; }}
  #bk-modal-root .bk-m-grp + .bk-m-grp {{ margin-top: 14px; }}
  #bk-modal-root .bk-m-row2 {{ display: flex; gap: 12px; }}
  #bk-modal-root .bk-m-row2 > div {{ flex: 1; }}
  #bk-modal-root .bk-m-stake-suffix {{ position: relative; }}
  #bk-modal-root .bk-m-stake-suffix > span {{
    position: absolute; right: 14px; top: 50%; transform: translateY(-50%);
    color: #8B8D98; font-weight: 600; pointer-events: none;
  }}
  #bk-modal-root .bk-m-quick {{ display: flex; gap: 6px; margin-top: 8px; }}
  #bk-modal-root .bk-m-quick button {{
    flex: 1; padding: 8px 0; background: #14161B;
    border: 1px solid rgba(255,255,255,0.06); border-radius: 12px;
    color: #fff; font-weight: 600; font-size: 13px; cursor: pointer;
    font-variant-numeric: tabular-nums; font-family: inherit; transition: all 180ms;
  }}
  #bk-modal-root .bk-m-quick button:hover {{ background: rgba(255,255,255,0.04); }}
  #bk-modal-root .bk-m-quick button.active {{ background: rgba(52,211,153,0.12); border-color: rgba(52,211,153,0.30); color: #34D399; }}
  #bk-modal-root .bk-m-potential {{
    margin-top: 22px; padding: 16px; border-radius: 18px;
    background: linear-gradient(135deg, rgba(52,211,153,0.14), rgba(52,211,153,0.02));
    border: 1px solid rgba(52,211,153,0.22);
    display: flex; align-items: center; justify-content: space-between;
  }}
  #bk-modal-root .bk-m-pot-l {{ color: #8B8D98; font-size: 12px; }}
  #bk-modal-root .bk-m-pot-big {{ display: block; color: #fff; font-weight: 700; font-size: 22px; font-variant-numeric: tabular-nums; margin-top: 4px; letter-spacing: -0.3px; }}
  #bk-modal-root .bk-m-pot-prof {{ display: block; color: #34D399; font-weight: 700; font-size: 18px; font-variant-numeric: tabular-nums; margin-top: 4px; }}
  #bk-modal-root .bk-m-hint {{ color: #8B8D98; font-size: 11.5px; margin-top: 6px; font-weight: 500; }}
  #bk-modal-root .bk-m-cta {{
    width: 100%; padding: 15px; border-radius: 14px;
    background: linear-gradient(180deg, #34D399, #10B981);
    border: none; color: #06120E; font-weight: 700; font-size: 16px;
    letter-spacing: 0.2px; cursor: pointer; transition: all 200ms ease;
    box-shadow: 0 6px 20px rgba(52,211,153,0.35); font-family: inherit;
  }}
  #bk-modal-root .bk-m-cta:disabled {{ background: #1F232C; color: #8B8D98; cursor: default; box-shadow: none; }}
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
    <button class="sport-btn" onclick="showSport('userpicks')" id="sport-btn-userpicks">💰 Bankroll <span id="userpicks-count" style="background:rgba(255,255,255,0.2);border-radius:10px;padding:1px 7px;font-size:11px;margin-left:4px;display:none">0</span></button>
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

  <!-- Section Bankroll (gestion + tracking) — design adapté du prototype mobile -->
  <div id="sport-userpicks" style="display:none">
    <div id="user-picks-list" class="bk-app"></div>
  </div>

  <!-- Section Analyse NBA -->
  <div id="sport-analyse" style="display:none">
    <div class="legend">
      <b>🔍 Analyse joueur</b> — Selectionne un prop (PTS / REB / AST / 3PM / REB+AST / PR / PA / PRA) pour
      visualiser la perf du joueur sur ses 5 derniers matchs · L10/L20/H2H hit rates · médiane/moyenne ·
      <span style="color:#fb923c">ligne pointillée = ligne bookmaker</span> (ou médiane si pas dispo)
    </div>
    <div style="display:flex;gap:8px;margin-bottom:14px;flex-wrap:wrap">
      <button onclick="collapseAllAnalyse()" style="background:#1e293b;color:#94a3b8;border:1px solid #334155;border-radius:8px;padding:8px 14px;font-size:12px;font-weight:700;cursor:pointer">📕 Tout fermer</button>
      <button onclick="expandAllAnalyse()" style="background:#1e293b;color:#94a3b8;border:1px solid #334155;border-radius:8px;padding:8px 14px;font-size:12px;font-weight:700;cursor:pointer">📖 Tout ouvrir</button>
      <button onclick="expandStartersAnalyse()" style="background:#1e3a8a;color:#bfdbfe;border:1px solid #3b82f6;border-radius:8px;padding:8px 14px;font-size:12px;font-weight:700;cursor:pointer">⭐ Titulaires seulement</button>
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
window.NBA_HISTORY = {nba_history_json};
</script>
<script>
function showSport(sport){{
  ['football','nba','analyse','userpicks','foothist','nbahist'].forEach(s=>{{
    var el = document.getElementById('sport-'+s);
    if(el) el.style.display = (s===sport) ? 'block' : 'none';
  }});
  document.querySelectorAll('.sport-btn').forEach(b=>b.classList.remove('active'));
  var btn = document.getElementById('sport-btn-'+sport);
  if(btn) btn.classList.add('active');
  // Refresh user picks list when entering the tab
  if(sport === 'userpicks') renderUserPicks();
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

// ── Ajustement ligne de reference + recalcul live des hit rates / colors ──
function _findPropChart(el){{ return el.closest('.tg-prop-chart'); }}
function _computeHR(vals, line){{
  if(!vals || !vals.length) return null;
  var hits = vals.filter(function(v){{ return v > line; }}).length;
  return {{pct: Math.round(hits/vals.length*100), w: hits, n: vals.length}};
}}
function _updateBadge(badge, hr){{
  var lbl = badge.getAttribute('data-label') || '';
  if(!hr){{ badge.innerHTML = lbl + ': —'; badge.style.color = '#475569'; return; }}
  var color = hr.pct >= 70 ? '#22c55e' : (hr.pct >= 50 ? '#f59e0b' : '#ef4444');
  badge.style.color = color;
  badge.innerHTML = lbl + ': ' + hr.pct + '%<span style="color:#64748b;font-weight:400"> (' + hr.w + '/' + hr.n + ')</span>';
}}
function recalcChart(chart, newLine){{
  var vals;
  try {{ vals = JSON.parse(chart.getAttribute('data-vals') || '{{}}'); }} catch(e) {{ return; }}
  // Update each window block
  chart.querySelectorAll('.tg-window-block').forEach(function(block){{
    var area = block.querySelector('.tg-chart-area');
    if(!area) return;
    var max = parseFloat(area.getAttribute('data-chart-max')) || 1;
    var chartPx = parseFloat(area.getAttribute('data-chart-px')) || 120;
    // Re-color bars
    area.querySelectorAll('.tg-bar').forEach(function(bar){{
      var val = parseFloat(bar.getAttribute('data-value')) || 0;
      var fill = bar.querySelector('.tg-bar-fill');
      if(fill) fill.style.background = (val > newLine) ? '#22c55e' : '#ef4444';
    }});
    // Reposition ref line + label
    var refLine = area.querySelector('.tg-ref-line');
    if(refLine){{
      var refPx = Math.round((newLine / max) * chartPx);
      refPx = Math.max(0, Math.min(chartPx, refPx));
      refLine.style.bottom = refPx + 'px';
      var lbl = refLine.querySelector('.tg-ref-label');
      if(lbl) lbl.textContent = newLine;
    }}
  }});
  // Update hit rate badges (L5/L10/L20/H2H)
  chart.querySelectorAll('.tg-hr-badge').forEach(function(b){{
    var key = b.getAttribute('data-key');
    if(!key) return;
    _updateBadge(b, _computeHR(vals[key], newLine));
  }});
  // Sync les boutons Add OVER/UNDER avec la nouvelle ligne + leur label
  chart.querySelectorAll('.tg-userpick-btn').forEach(function(b){{
    try {{
      var p = JSON.parse(b.dataset.payload);
      p.line = newLine;
      b.dataset.payload = JSON.stringify(p);
      var dir = b.dataset.direction === 'over' ? 'OVER' : 'UNDER';
      var book = b.dataset.direction === 'over' ? p.book_over : p.book_under;
      b.innerHTML = '📌 Add ' + dir + ' ' + newLine + (book ? ' @ ' + book : '');
    }} catch(e) {{}}
  }});
}}
// Snap a la grille bookmaker (x.5) : 0.5, 1.5, 2.5, ...
// floor(v) + 0.5 donne toujours le x.5 le plus proche (cf math).
function _snapToHalf(v){{
  if(v < 0.5) return 0.5;
  return Math.floor(v) + 0.5;
}}
function adjustLine(btn, delta){{
  var chart = _findPropChart(btn);
  if(!chart) return;
  var input = chart.querySelector('.tg-line-input');
  if(!input) return;
  var v = parseFloat(input.value) || 0.5;
  v = _snapToHalf(v + delta);
  input.value = v;
  recalcChart(chart, v);
}}
function onLineInputChange(input){{
  var chart = _findPropChart(input);
  if(!chart) return;
  var v = parseFloat(input.value);
  if(isNaN(v) || v < 0.5) v = 0.5;
  v = _snapToHalf(v);
  input.value = v;
  recalcChart(chart, v);
}}
function resetLine(btn){{
  var chart = _findPropChart(btn);
  if(!chart) return;
  var def = parseFloat(btn.dataset.default) || 0;
  var input = chart.querySelector('.tg-line-input');
  if(input) input.value = def;
  recalcChart(chart, def);
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

// ── Actions globales : tout fermer / tout ouvrir / titulaires seuls ──
function _setExpanded(card, expanded){{
  var content = card.querySelector('.player-content');
  var arrow = card.querySelector('.expand-arrow');
  if(!content) return;
  content.style.display = expanded ? 'block' : 'none';
  if(arrow) arrow.textContent = expanded ? '▼' : '▶';
}}
function collapseAllAnalyse(){{
  document.querySelectorAll('#sport-analyse .player-analyse').forEach(function(c){{
    _setExpanded(c, false);
  }});
}}
function expandAllAnalyse(){{
  document.querySelectorAll('#sport-analyse .player-analyse').forEach(function(c){{
    _setExpanded(c, true);
  }});
}}
function expandStartersAnalyse(){{
  document.querySelectorAll('#sport-analyse .player-analyse').forEach(function(c){{
    var isStarter = c.getAttribute('data-is-starter') === '1';
    _setExpanded(c, isStarter);
  }});
}}

// ── Lien picks NBA -> Analyse : ouvre le tab Analyse + scroll au joueur ──
function goToAnalyse(playerName, prop){{
  showSport('analyse');
  // Petit delai pour laisser le tab s'afficher
  setTimeout(function(){{
    var cards = document.querySelectorAll('#sport-analyse .player-analyse');
    var target = null;
    cards.forEach(function(c){{
      if(target) return;
      var n = (c.getAttribute('data-player-name') || '').trim();
      if(n === playerName) target = c;
    }});
    if(!target){{
      // Fallback : match partiel par nom de famille
      var lastName = playerName.split(' ').pop();
      cards.forEach(function(c){{
        if(target) return;
        var n = (c.getAttribute('data-player-name') || '').trim();
        if(n.indexOf(lastName) !== -1) target = c;
      }});
    }}
    if(!target) return;
    // Expand
    _setExpanded(target, true);
    // Selectionne la prop demandee
    var propBtn = target.querySelector('.tg-prop-btn[data-prop="' + prop + '"]');
    if(propBtn) selectPropChart(propBtn);
    // Scroll + highlight
    target.scrollIntoView({{behavior:'smooth', block:'center'}});
    var origOutline = target.style.outline;
    var origShadow  = target.style.boxShadow;
    target.style.outline = '3px solid #fb923c';
    target.style.boxShadow = '0 0 24px rgba(251,146,60,0.6)';
    setTimeout(function(){{
      target.style.outline = origOutline;
      target.style.boxShadow = origShadow;
    }}, 2500);
  }}, 80);
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

// ── Mes picks perso (user-sent depuis Analyse) ──
// Stockes dans localStorage.user_picks_v1 sous forme de liste de dicts.
const USERPICKS_KEY = 'user_picks_v1';

function _loadUserPicks(){{
  try {{
    var raw = localStorage.getItem(USERPICKS_KEY);
    return raw ? JSON.parse(raw) : [];
  }} catch(e) {{ return []; }}
}}
function _saveUserPicks(arr){{
  localStorage.setItem(USERPICKS_KEY, JSON.stringify(arr));
  _updateUserPicksCount();
}}
function _updateUserPicksCount(){{
  var arr = _loadUserPicks();
  var pending = arr.filter(p => !p.result).length;
  var badge = document.getElementById('userpicks-count');
  if(badge){{
    badge.textContent = arr.length;
    badge.style.display = arr.length > 0 ? 'inline-block' : 'none';
  }}
}}

// ── Formulaire Nouveau Pari (modal centré, ouvert depuis Analyse NBA) ────
function _bkEnsureModalRoot(){{
  var root = document.getElementById('bk-modal-root');
  if(!root){{
    root = document.createElement('div');
    root.id = 'bk-modal-root';
    document.body.appendChild(root);
  }}
  return root;
}}
function _bkCloseForm(){{
  var root = document.getElementById('bk-modal-root');
  if(!root) return;
  root.classList.remove('open');
  setTimeout(function(){{ if(root) root.innerHTML = ''; }}, 280);
  if(window._bkFormEscHandler){{
    document.removeEventListener('keydown', window._bkFormEscHandler);
    window._bkFormEscHandler = null;
  }}
  window._bkFormState = null;
}}
function _bkFormUpdateCalc(){{
  var st = window._bkFormState;
  if(!st) return;
  var c = parseFloat(String(st.cote).replace(',', '.')) || 0;
  var s = parseFloat(String(st.stake).replace(',', '.')) || 0;
  var l = parseFloat(String(st.line).replace(',', '.'));
  var pot = s * c;
  var prof = Math.max(0, pot - s);
  var canSubmit = !isNaN(l) && c > 1 && s > 0;
  var potEl = document.getElementById('bk-m-pot');
  var profEl = document.getElementById('bk-m-prof');
  var btn = document.getElementById('bk-m-submit');
  if(potEl) potEl.textContent = '€' + pot.toFixed(2);
  if(profEl) profEl.textContent = '+€' + prof.toFixed(2);
  if(btn) btn.disabled = !canSubmit;
  // Highlight quick stake button if match
  document.querySelectorAll('#bk-modal-root .bk-m-quick button').forEach(function(b){{
    var v = parseFloat(b.dataset.v);
    b.classList.toggle('active', v === s);
  }});
}}
function _bkSetStake(v){{
  var st = window._bkFormState;
  if(!st) return;
  st.stake = String(v);
  var el = document.getElementById('bk-m-stake');
  if(el) el.value = String(v);
  _bkFormUpdateCalc();
}}
function _bkOpenForm(opts){{
  // opts = {{ direction, payload, onSubmit }}
  var root = _bkEnsureModalRoot();
  var p = opts.payload;
  var dir = opts.direction;
  var defLine = p.line !== undefined && p.line !== null ? String(p.line) : '';
  var defCoteRaw = dir === 'over' ? p.book_over : p.book_under;
  var defCote = defCoteRaw ? String(defCoteRaw) : '1.90';
  var defStake = String(window._bkLastStake || 2);
  var defTipster = window._bkLastTipster || '';
  window._bkFormState = {{
    opts: opts,
    line: defLine, cote: defCote, stake: defStake,
    tipster: defTipster, note: '',
  }};
  var propLabel = ({{PTS:'pts',REB:'reb',AST:'pas',FG3M:'3PM',RA:'reb+pas',PR:'pts+reb',PA:'pts+ast',PRA:'PRA'}})[p.prop] || p.prop;
  var dirLabel = dir === 'over' ? 'OVER' : 'UNDER';
  var dirCls = dir === 'over' ? 'over' : 'under';
  var bookHint = [];
  if(p.book_line !== undefined && p.book_line !== null) bookHint.push('Ligne book : ' + p.book_line);
  if(defCoteRaw) bookHint.push('Cote book : ' + defCoteRaw);
  if(p.median !== undefined && p.median !== null) bookHint.push('Méd L20 : ' + p.median);
  var hintLine = bookHint.length ? '<div class="bk-m-hint">' + bookHint.join(' · ') + '</div>' : '';

  var html =
    '<div class="bk-modal-bd" onclick="_bkCloseForm()"></div>'
    + '<div class="bk-modal-card" role="dialog" aria-modal="true">'
    +   '<div class="bk-m-hd">'
    +     '<button class="bk-m-cancel" onclick="_bkCloseForm()">Annuler</button>'
    +     '<div class="bk-m-title">Nouveau pari</div>'
    +     '<div style="width:64px"></div>'
    +   '</div>'
    +   '<div class="bk-m-body">'
    +     '<div class="bk-m-context">'
    +       '<div class="bk-m-icon">🏀</div>'
    +       '<div style="flex:1;min-width:0">'
    +         '<div class="bk-m-ctx-title">' + p.player + '</div>'
    +         '<div class="bk-m-ctx-sub">'
    +           '<span class="bk-dir-chip ' + dirCls + '">' + dirLabel + '</span>'
    +           '<span>' + propLabel + '</span>'
    +           '<span class="sep">·</span>'
    +           '<span>' + (p.away || '') + ' @ ' + (p.home || '') + '</span>'
    +         '</div>'
    +       '</div>'
    +     '</div>'
    +     '<div class="bk-m-row2">'
    +       '<div>'
    +         '<label class="bk-m-label">Ligne</label>'
    +         '<input class="bk-m-input" id="bk-m-line" inputmode="decimal" value="' + defLine + '" placeholder="9.5">'
    +       '</div>'
    +       '<div>'
    +         '<label class="bk-m-label">Cote</label>'
    +         '<input class="bk-m-input" id="bk-m-cote" inputmode="decimal" value="' + defCote + '" placeholder="1.90">'
    +       '</div>'
    +     '</div>'
    +     hintLine
    +     '<div class="bk-m-grp" style="margin-top:14px">'
    +       '<label class="bk-m-label">Mise</label>'
    +       '<div class="bk-m-stake-suffix">'
    +         '<input class="bk-m-input" id="bk-m-stake" inputmode="decimal" value="' + defStake + '" style="padding-right:32px">'
    +         '<span>€</span>'
    +       '</div>'
    +       '<div class="bk-m-quick">'
    +         [1, 2, 5, 10, 25].map(function(v){{ return '<button type="button" data-v="' + v + '" onclick="_bkSetStake(' + v + ')">' + v + '€</button>'; }}).join('')
    +       '</div>'
    +     '</div>'
    +     '<div class="bk-m-grp" style="margin-top:14px">'
    +       '<label class="bk-m-label">Tipster<span class="opt">· facultatif</span></label>'
    +       '<input class="bk-m-input" id="bk-m-tipster" value="' + defTipster.replace(/"/g, '&quot;') + '" placeholder="Algo, @PronoKing, instinct...">'
    +     '</div>'
    +     '<div class="bk-m-grp" style="margin-top:14px">'
    +       '<label class="bk-m-label">Note d\\'analyse<span class="opt">· facultatif</span></label>'
    +       '<textarea class="bk-m-input" id="bk-m-note" rows="3" placeholder="Pourquoi ce pari ? Forme, blessures, value..."></textarea>'
    +     '</div>'
    +     '<div class="bk-m-potential">'
    +       '<div>'
    +         '<div class="bk-m-pot-l">Gain potentiel</div>'
    +         '<span class="bk-m-pot-big" id="bk-m-pot">€0.00</span>'
    +       '</div>'
    +       '<div style="text-align:right">'
    +         '<div class="bk-m-pot-l">Bénéfice</div>'
    +         '<span class="bk-m-pot-prof" id="bk-m-prof">+€0.00</span>'
    +       '</div>'
    +     '</div>'
    +   '</div>'
    +   '<div class="bk-m-ft">'
    +     '<button class="bk-m-cta" id="bk-m-submit">Placer le pari</button>'
    +   '</div>'
    + '</div>';
  root.innerHTML = html;
  // Open with anim
  setTimeout(function(){{ root.classList.add('open'); }}, 10);
  // Wire inputs
  var byId = function(id){{ return document.getElementById(id); }};
  byId('bk-m-line').addEventListener('input',    function(e){{ window._bkFormState.line    = e.target.value; _bkFormUpdateCalc(); }});
  byId('bk-m-cote').addEventListener('input',    function(e){{ window._bkFormState.cote    = e.target.value; _bkFormUpdateCalc(); }});
  byId('bk-m-stake').addEventListener('input',   function(e){{ window._bkFormState.stake   = e.target.value; _bkFormUpdateCalc(); }});
  byId('bk-m-tipster').addEventListener('input', function(e){{ window._bkFormState.tipster = e.target.value; }});
  byId('bk-m-note').addEventListener('input',    function(e){{ window._bkFormState.note    = e.target.value; }});
  byId('bk-m-submit').addEventListener('click', function(){{
    var st = window._bkFormState;
    if(!st) return;
    var l = parseFloat(String(st.line).replace(',', '.'));
    var c = parseFloat(String(st.cote).replace(',', '.'));
    var s = parseFloat(String(st.stake).replace(',', '.'));
    if(isNaN(l)){{ alert('Ligne invalide'); return; }}
    if(isNaN(c) || c <= 1){{ alert('Cote invalide (doit etre > 1.0)'); return; }}
    if(isNaN(s) || s <= 0){{ alert('Mise invalide'); return; }}
    var tipster = st.tipster.trim();
    var note = st.note.trim();
    window._bkLastStake = s;
    window._bkLastTipster = tipster;
    opts.onSubmit({{ line: l, cote: c, stake: s, tipster: tipster || null, note: note || null }});
    _bkCloseForm();
  }});
  _bkFormUpdateCalc();
  // ESC + initial focus
  window._bkFormEscHandler = function(e){{ if(e.key === 'Escape') _bkCloseForm(); }};
  document.addEventListener('keydown', window._bkFormEscHandler);
  setTimeout(function(){{ var el = byId('bk-m-line'); if(el && !el.value) el.focus(); else if(el) el.select(); }}, 120);
}}

function addUserPick(btn){{
  var direction = btn.dataset.direction;
  var data;
  try {{ data = JSON.parse(btn.dataset.payload); }} catch(e) {{ return alert('Erreur payload'); }}
  _bkOpenForm({{
    direction: direction,
    payload: data,
    onSubmit: function(r){{
      var pick = {{
        id:        'user_' + Date.now() + '_' + Math.random().toString(36).slice(2, 8),
        sport:     data.sport,
        player:    data.player,
        prop:      data.prop,
        direction: direction,
        line:      r.line,
        cote:      r.cote,
        stake:     r.stake,
        tipster:   r.tipster,
        note:      r.note,
        home:      data.home,
        away:      data.away,
        game_id:   data.game_id,
        opp:       data.opp,
        median:    data.median,
        mean:      data.mean,
        book_line: data.book_line,
        book_over: data.book_over,
        book_under:data.book_under,
        created:   new Date().toISOString(),
        result:    null,
        actual:    null,
        source:    'user',
      }};
      var arr = _loadUserPicks();
      arr.push(pick);
      _saveUserPicks(arr);
      // Confirmation visuelle
      var origBg = btn.style.background;
      var origHTML = btn.innerHTML;
      btn.style.background = '#0a1628';
      btn.innerHTML = '✓ @ ' + r.cote + ' ajouté !';
      setTimeout(function(){{
        btn.style.background = origBg;
        btn.innerHTML = origHTML;
      }}, 1800);
    }},
  }});
}}

function deleteUserPick(id){{
  if(!confirm('Supprimer ce pick ?')) return;
  var arr = _loadUserPicks().filter(p => p.id !== id);
  _saveUserPicks(arr);
  renderUserPicks();
}}

function editUserPickCote(id){{
  var arr = _loadUserPicks();
  var idx = arr.findIndex(p => p.id === id);
  if(idx < 0) return;
  var p = arr[idx];
  var currentCote = p.cote || (p.direction === 'over' ? p.book_over : p.book_under) || 1.90;
  var c = prompt('Cote (saisie ou modification) :', String(currentCote));
  if(c === null) return;
  var cNum = parseFloat(c);
  if(isNaN(cNum) || cNum <= 1.0){{ alert('Cote invalide (> 1.0)'); return; }}
  arr[idx].cote = cNum;
  _saveUserPicks(arr);
  renderUserPicks();
}}

// Push d'un user pick : si pas de cote, prompt avant d'envoyer
// async + await pour eviter que renderUserPicks() ne detache le btn avant la fin du fetch
async function pushUserPick(btn, id){{
  var arr = _loadUserPicks();
  var p = arr.find(x => x.id === id);
  if(!p){{ alert('Pick introuvable'); return; }}
  var coteJustAdded = false;
  if(!p.cote){{
    var defaultCote = (p.direction === 'over' ? p.book_over : p.book_under) || 1.90;
    var c = prompt('Cote a laquelle tu joues ? (requis pour push)', String(defaultCote));
    if(c === null) return;
    var cNum = parseFloat(c);
    if(isNaN(cNum) || cNum <= 1.0){{ alert('Cote invalide (> 1.0)'); return; }}
    var idx = arr.findIndex(x => x.id === id);
    arr[idx].cote = cNum;
    _saveUserPicks(arr);
    p = arr[idx];
    coteJustAdded = true;
  }}
  // Construit le texte avec cote garantie + parse_mode HTML
  var dir = p.direction === 'over' ? 'plus de' : 'moins de';
  var propLabel = ({{PTS:'pts',REB:'reb',AST:'pas',FG3M:'3PM',RA:'reb+pas',PR:'pts+reb',PA:'pts+ast',PRA:'PRA'}})[p.prop] || p.prop;
  var label = p.player + ' ' + dir + ' ' + p.line + ' ' + propLabel;
  var lines = [
    '🎯 <b>PICK PERSO</b>',
    '',
    '🏀 ' + (p.away || '') + ' @ ' + (p.home || ''),
    '',
    '📌 <b>' + label + '</b>',
    '💰 <b>Cote : ' + p.cote.toFixed(2) + '</b>',
  ];
  if(p.stake != null) lines.push('💵 Mise : ' + p.stake + ' €');
  if(p.median !== undefined && p.median !== null) lines.push('📊 Médiane L20 : ' + p.median + ' · Moyenne : ' + p.mean);
  if(p.book_line !== null && p.book_line !== undefined) lines.push('📐 Ligne book US : ' + p.book_line);
  if(p.tipster) lines.push('👤 Tipster : ' + p.tipster);
  if(p.note) lines.push('📝 ' + p.note);
  btn.dataset.text = lines.join('\\n');
  // ATTENDRE la fin du push avant tout re-render, sinon le btn est detache pendant le fetch
  await pushTelegram(btn);
  // Refresh seulement si la cote vient d'etre ajoutee (sinon le re-render est inutile et detache le btn)
  if(coteJustAdded) renderUserPicks();
}}

function markUserPickResult(id, result){{
  var arr = _loadUserPicks();
  var idx = arr.findIndex(p => p.id === id);
  if(idx < 0) return;
  arr[idx].result = result;
  arr[idx].resolved_at = new Date().toISOString();
  _saveUserPicks(arr);
  renderUserPicks();
}}

// ── Auto-resolution des user picks via NBA_HISTORY (resolus par l'algo) ──
// Pour chaque user pick PENDING, on cherche un pick algo sur le meme
// (game_id, player, prop) deja resolu (a une autre ligne potentiellement),
// on extrait sa valeur 'actual' (= la stat reelle du joueur), puis on
// applique la regle OVER/UNDER selon la ligne saisie par l'user.
function autoResolveUserPicks(){{
  var hist = window.NBA_HISTORY || [];
  if(!hist.length) return 0;
  // Build lookup map : "{{game_id}}|{{player}}|{{prop}}" -> actual
  var actualMap = {{}};
  hist.forEach(function(h){{
    if(h.actual === null || h.actual === undefined) return;
    var key = (h.game_id || '') + '|' + (h.player || '') + '|' + (h.prop || '');
    if(actualMap[key] === undefined) actualMap[key] = h.actual;
  }});
  var arr = _loadUserPicks();
  var nResolved = 0;
  arr.forEach(function(p){{
    if(p.result && p.result !== 'PENDING') return;  // skip si deja resolu ou cashout
    if(p.manual_override) return;                   // l'user a force un resultat
    var key = (p.game_id || '') + '|' + (p.player || '') + '|' + (p.prop || '');
    var actual = actualMap[key];
    if(actual === undefined) return;  // pas encore resolu cote algo
    var line = parseFloat(p.line);
    actual = parseFloat(actual);
    if(isNaN(line) || isNaN(actual)) return;
    var result;
    if(p.direction === 'over'){{
      result = actual > line ? 'WIN' : (actual < line ? 'LOSS' : 'PUSH');
    }} else {{  // under
      result = actual < line ? 'WIN' : (actual > line ? 'LOSS' : 'PUSH');
    }}
    p.result = result;
    p.actual = actual;
    p.resolved_at = new Date().toISOString();
    p.auto_resolved = true;
    nResolved++;
  }});
  if(nResolved > 0) _saveUserPicks(arr);
  return nResolved;
}}

// ── Auto-refresh : poll data/nba_history_min.json toutes les 60s ─────────────
// Cron regenere le site toutes les 10 min cote serveur. Cote client, on
// recupere la version a jour de NBA_HISTORY sans avoir a recharger la page.
async function _bkPollHistory(){{
  try {{
    var resp = await fetch('data/nba_history_min.json?t=' + Date.now(), {{cache: 'no-store'}});
    if(!resp.ok) return;
    var hist = await resp.json();
    if(!Array.isArray(hist)) return;
    // Detect change : si meme longueur ET meme dernier resolved key, skip
    var prev = window.NBA_HISTORY || [];
    var sameLen = prev.length === hist.length;
    var sameLast = sameLen && prev.length > 0
      && (prev[prev.length-1].game_id === hist[hist.length-1].game_id)
      && (prev[prev.length-1].player === hist[hist.length-1].player)
      && (prev[prev.length-1].actual === hist[hist.length-1].actual);
    window.NBA_HISTORY = hist;
    if(sameLen && sameLast) return;  // rien de neuf, pas de re-render
    // Tente de resoudre les pending avec la nouvelle data
    var n = autoResolveUserPicks();
    _updateUserPicksCount();
    // Re-render uniquement si on est sur la section bankroll
    var bkTab = document.getElementById('sport-userpicks');
    if(bkTab && bkTab.style.display !== 'none' && n > 0){{
      renderUserPicks();
    }}
  }} catch(e){{ /* network error, on retentera plus tard */ }}
}}
// Init poll au chargement + tick toutes les 60s + reload quand l'onglet redevient visible
window.addEventListener('DOMContentLoaded', function(){{
  if(window._bkPollInitDone) return;
  window._bkPollInitDone = true;
  // Premiere passe rapide apres 5s (donne le temps au reste de la page de charger)
  setTimeout(_bkPollHistory, 5000);
  // Tick recurrent
  setInterval(_bkPollHistory, 60000);
  // Quand l'user revient sur l'onglet apres une longue absence
  document.addEventListener('visibilitychange', function(){{
    if(document.visibilityState === 'visible') _bkPollHistory();
  }});
}});

// Modification manuelle (cashout, annulation, override)
function modifyUserPick(id){{
  var arr = _loadUserPicks();
  var idx = arr.findIndex(p => p.id === id);
  if(idx < 0) return;
  var p = arr[idx];
  var choice = prompt(
    'Statut du pari ? Tape un numero :\\n' +
    '  1 = Gagné\\n' +
    '  2 = Perdu\\n' +
    '  3 = Annulé (PUSH ou cashout neutre)\\n' +
    '  4 = Cashout partiel (saisir gain en €)\\n' +
    '  5 = Remettre en pending (annule override)',
    p.result === 'WIN' ? '1' : (p.result === 'LOSS' ? '2' : (p.result === 'PUSH' ? '3' : '1'))
  );
  if(choice === null) return;
  if(choice === '1'){{
    p.result = 'WIN';
    p.manual_override = true;
    p.cashout_amount = null;
  }} else if(choice === '2'){{
    p.result = 'LOSS';
    p.manual_override = true;
    p.cashout_amount = null;
  }} else if(choice === '3'){{
    p.result = 'PUSH';
    p.manual_override = true;
    p.cashout_amount = null;
  }} else if(choice === '4'){{
    var stake = (p.stake != null) ? p.stake : 1;
    var cashStr = prompt('Montant net du cashout en € (mise initiale ' + stake + ' €). Ex : +3.20 pour un gain ou -2 pour une perte partielle :', '0');
    if(cashStr === null) return;
    var cash = parseFloat(cashStr);
    if(isNaN(cash)){{ alert('Montant invalide'); return; }}
    p.result = cash > 0 ? 'WIN' : (cash < 0 ? 'LOSS' : 'PUSH');
    p.cashout_amount = cash;
    p.manual_override = true;
  }} else if(choice === '5'){{
    p.result = null;
    p.actual = null;
    p.resolved_at = null;
    p.manual_override = false;
    p.cashout_amount = null;
    p.auto_resolved = false;
  }} else {{
    return;
  }}
  if(p.result){{ p.resolved_at = new Date().toISOString(); }}
  _saveUserPicks(arr);
  renderUserPicks();
}}

// Edit stake (mise) sur un pick
function editUserPickStake(id){{
  var arr = _loadUserPicks();
  var idx = arr.findIndex(p => p.id === id);
  if(idx < 0) return;
  var p = arr[idx];
  var current = (p.stake != null) ? p.stake : 1;
  var s = prompt('Mise en € (ex 5, 10, 25...) :', String(current));
  if(s === null) return;
  var sNum = parseFloat(s);
  if(isNaN(sNum) || sNum <= 0){{ alert('Mise invalide'); return; }}
  arr[idx].stake = sNum;
  _saveUserPicks(arr);
  renderUserPicks();
}}

// Bankroll initial (localStorage). Stocke en €.
function _getBankroll(){{
  var v = parseFloat(localStorage.getItem('user_bankroll_units') || '100');
  return isNaN(v) ? 100 : v;
}}
function _setBankroll(v){{
  localStorage.setItem('user_bankroll_units', String(v));
}}
function editBankroll(){{
  var current = _getBankroll();
  var v = prompt('Bankroll initial en € (ex 100, 500, 1000) :', String(current));
  if(v === null) return;
  var n = parseFloat(v);
  if(isNaN(n) || n <= 0){{ alert('Valeur invalide'); return; }}
  _setBankroll(n);
  renderUserPicks();
  refreshStakePills();
}}

// ── Conversion stake_pill (u Kelly -> € selon bankroll user) ──
function refreshStakePills(){{
  var bk = _getBankroll();
  document.querySelectorAll('.tg-stake-pill').forEach(function(p){{
    var u = parseFloat(p.getAttribute('data-units')) || 0;
    if(u <= 0) return;
    // 1u Kelly = 1% du bankroll par convention
    var eur = (u / 100) * bk;
    var label = eur < 10 ? eur.toFixed(2) : eur.toFixed(0);
    p.textContent = '💪 ' + label + ' €';
  }});
}}
window.addEventListener('DOMContentLoaded', refreshStakePills);

// ── Helpers Bankroll (nouveau design adapté du prototype mobile) ─────────────
function _bkBetDelta(p){{
  if(p.cashout_amount != null) return p.cashout_amount;
  var stake = (p.stake != null) ? p.stake : 1;
  if(p.result === 'WIN' && p.cote) return stake * (p.cote - 1);
  if(p.result === 'LOSS') return -stake;
  return 0;
}}
function _bkFmt(v, decimals){{
  if(decimals == null) decimals = 2;
  return v.toFixed(decimals).replace('.', ',');
}}
function _bkBuildSeries(arr, bk, period){{
  var bets = arr.filter(function(p){{ return p.result && p.result !== 'PENDING' && p.resolved_at; }});
  bets.sort(function(a, b){{ return (a.resolved_at || '').localeCompare(b.resolved_at || ''); }});
  if(period && period !== 'ALL'){{
    var now = Date.now();
    var days = ({{'7J':7, '1M':30, '3M':90}})[period] || 36500;
    var ms = days * 86400000;
    bets = bets.filter(function(b){{ return now - new Date(b.resolved_at).getTime() <= ms; }});
  }}
  var pts = [bk];
  var v = bk;
  bets.forEach(function(b){{ v += _bkBetDelta(b); pts.push(v); }});
  return pts;
}}
function _bkRenderChart(pts, accent, hasResolved){{
  if(!pts || pts.length < 2){{
    var msg = hasResolved
      ? "Aucun pari résolu sur cette période — change l'intervalle pour voir ta courbe ✨"
      : "Pas encore d'historique — résous quelques paris pour voir ta courbe ✨";
    return '<div style="display:flex;align-items:center;justify-content:center;height:200px;color:var(--bk-text-muted);font-size:13px;text-align:center;padding:0 20px">' + msg + '</div>';
  }}
  var W = 800, H = 200, padT = 18, padB = 14, padL = 8, padR = 8;
  var iw = W - padL - padR, ih = H - padT - padB;
  var min = Math.min.apply(null, pts), max = Math.max.apply(null, pts);
  var range = Math.max(0.01, max - min);
  var n = pts.length;
  var coords = pts.map(function(v, i){{
    var x = padL + (i / Math.max(1, n - 1)) * iw;
    var y = padT + (1 - (v - min) / range) * ih;
    return [x, y];
  }});
  var linePath = coords.map(function(p, i){{ return (i === 0 ? 'M' : 'L') + p[0].toFixed(1) + ',' + p[1].toFixed(1); }}).join(' ');
  var areaPath = linePath + ' L' + coords[n-1][0].toFixed(1) + ',' + H + ' L' + coords[0][0].toFixed(1) + ',' + H + ' Z';
  var endP = coords[n-1];
  var uid = 'bk' + Math.random().toString(36).slice(2, 8);
  return '<svg viewBox="0 0 ' + W + ' ' + H + '" preserveAspectRatio="none" style="width:100%;height:200px;overflow:visible">'
    + '<defs>'
    + '<linearGradient id="' + uid + '-f" x1="0" x2="0" y1="0" y2="1">'
    + '<stop offset="0%" stop-color="' + accent + '" stop-opacity="0.35"/>'
    + '<stop offset="60%" stop-color="' + accent + '" stop-opacity="0.06"/>'
    + '<stop offset="100%" stop-color="' + accent + '" stop-opacity="0"/>'
    + '</linearGradient>'
    + '<linearGradient id="' + uid + '-s" x1="0" x2="1" y1="0" y2="0">'
    + '<stop offset="0%" stop-color="' + accent + '" stop-opacity="0.55"/>'
    + '<stop offset="100%" stop-color="' + accent + '" stop-opacity="1"/>'
    + '</linearGradient>'
    + '</defs>'
    + '<line x1="0" y1="' + (H - 0.5) + '" x2="' + W + '" y2="' + (H - 0.5) + '" stroke="rgba(255,255,255,0.04)"/>'
    + '<path d="' + areaPath + '" fill="url(#' + uid + '-f)"/>'
    + '<path d="' + linePath + '" fill="none" stroke="url(#' + uid + '-s)" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round" class="bk-chart-line"/>'
    + '<circle cx="' + endP[0].toFixed(1) + '" cy="' + endP[1].toFixed(1) + '" r="6" fill="' + accent + '" fill-opacity="0.25"/>'
    + '<circle cx="' + endP[0].toFixed(1) + '" cy="' + endP[1].toFixed(1) + '" r="3.5" fill="' + accent + '"/>'
    + '</svg>';
}}
function _bkStat(label, value, sub, accent){{
  var subHtml = sub ? '<div class="bk-stat-sub" style="color:' + (accent || 'var(--bk-text-muted)') + '">' + sub + '</div>' : '';
  return '<div class="bk-stat">'
    + '<div class="bk-stat-label">' + label + '</div>'
    + '<div class="bk-stat-value" style="color:' + (accent || 'var(--bk-text)') + '">' + value + '</div>'
    + subHtml
    + '</div>';
}}
function _bkRowHtml(p){{
  var stake = (p.stake != null) ? p.stake : 1;
  var propLabel = ({{PTS:'pts',REB:'reb',AST:'pas',FG3M:'3PM',RA:'reb+pas',PR:'pts+reb',PA:'pts+ast',PRA:'PRA'}})[p.prop] || p.prop;
  var dir = p.direction === 'over' ? 'plus de' : 'moins de';
  var title = p.player + ' ' + dir + ' ' + p.line + ' ' + propLabel;
  var match = (p.away || '') + ' @ ' + (p.home || '');
  var badge = '';
  if(!p.result || p.result === 'PENDING'){{
    badge = '<span class="bk-badge pending"><span class="dot"></span>En cours</span>';
  }} else if(p.result === 'WIN'){{
    var tag = p.cashout_amount != null ? ' 💵' : (p.manual_override ? ' ✏' : '');
    badge = '<span class="bk-badge won"><span class="dot"></span>Gagné' + tag + '</span>';
  }} else if(p.result === 'LOSS'){{
    var tag = p.cashout_amount != null ? ' 💵' : (p.manual_override ? ' ✏' : '');
    badge = '<span class="bk-badge lost"><span class="dot"></span>Perdu' + tag + '</span>';
  }} else if(p.result === 'PUSH'){{
    var tag = p.manual_override ? ' ✏' : '';
    badge = '<span class="bk-badge push"><span class="dot"></span>Annulé' + tag + '</span>';
  }}
  var amtHtml = '';
  if(!p.result || p.result === 'PENDING'){{
    amtHtml = '<div class="bk-row-amt">' + _bkFmt(stake) + ' €</div>';
  }} else {{
    var d = _bkBetDelta(p);
    var cls = d > 0 ? 'pos' : (d < 0 ? 'neg' : 'flat');
    var sign = d > 0 ? '+' : (d < 0 ? '−' : '');
    amtHtml = '<div class="bk-row-amt ' + cls + '">' + sign + _bkFmt(Math.abs(d)) + ' €</div>';
  }}
  var coteChip = p.cote
    ? '<span class="bk-cote">@' + p.cote.toFixed(2) + '</span>'
    : '<span class="bk-cote warn" onclick="editUserPickCote(\\'' + p.id + '\\')">⚠️ cote</span>';
  var actions = '<div class="bk-row-actions">'
    + '<button class="bk-mini-btn edit" onclick="modifyUserPick(\\'' + p.id + '\\')" title="Modifier statut">✏</button>'
    + '<button class="bk-mini-btn" onclick="editUserPickStake(\\'' + p.id + '\\')" title="Modifier mise">💵</button>'
    + '<button class="bk-mini-btn tg" data-text="" onclick="pushUserPick(this, \\'' + p.id + '\\')" title="Envoyer sur Telegram">📲</button>'
    + '<button class="bk-mini-btn del" onclick="deleteUserPick(\\'' + p.id + '\\')" title="Supprimer">🗑</button>'
    + '</div>';
  var tipsterHtml = '';
  if(p.tipster && p.tipster.trim()){{
    var tEsc = p.tipster.replace(/</g, '&lt;');
    tipsterHtml = '<span class="dot">·</span><span style="color:#c4b5fd" title="Tipster">👤 ' + tEsc + '</span>';
  }}
  var noteHtml = '';
  if(p.note && p.note.trim()){{
    var nEsc = p.note.replace(/"/g, '&quot;').replace(/</g, '&lt;');
    noteHtml = '<span class="dot">·</span><span style="color:#7dd3fc;cursor:help" title="' + nEsc + '">📝</span>';
  }}
  return '<div class="bk-row">'
    + '<div class="bk-row-icon">🏀</div>'
    + '<div class="bk-row-main">'
    + '<div class="bk-row-title">' + title + '</div>'
    + '<div class="bk-row-sub"><span>' + match + '</span><span class="dot">·</span>' + coteChip
    + '<span class="dot">·</span><span>' + _bkFmt(stake) + ' € misés</span>'
    + tipsterHtml + noteHtml
    + '</div>'
    + '</div>'
    + '<div class="bk-row-side">'
    + amtHtml
    + badge
    + actions
    + '</div>'
    + '</div>';
}}
function setBkPeriod(p){{
  window._bkPeriod = p;
  renderUserPicks();
}}
function setBkFilter(kind, value){{
  window._bkFilters = window._bkFilters || {{status:'all', tipster:'all'}};
  window._bkFilters[kind] = value;
  renderUserPicks();
}}
function resetBkFilters(){{
  window._bkFilters = {{status:'all', tipster:'all'}};
  renderUserPicks();
}}

function renderUserPicks(){{
  autoResolveUserPicks();
  var arr = _loadUserPicks();
  var container = document.getElementById('user-picks-list');
  if(!container) return;
  if(!arr.length){{
    container.innerHTML =
      '<div class="bk-empty">'
      + '<div class="bk-empty-emoji">💸</div>'
      + '<div class="bk-empty-title">Bankroll en attente de paris</div>'
      + '<div class="bk-empty-sub">Va dans l\\'onglet <b style="color:#34D399">🔍 Analyse NBA</b>, choisis un joueur puis clique sur <b>📌 Add OVER</b> ou <b>📌 Add UNDER</b> pour ajouter ton premier pari.</div>'
      + '</div>';
    return;
  }}
  // Tri : pending d'abord (récent en haut), puis settled
  arr.sort(function(a, b){{
    if(!a.result && b.result) return -1;
    if(a.result && !b.result) return 1;
    return (b.created || '').localeCompare(a.created || '');
  }});

  var pending  = arr.filter(function(p){{ return !p.result || p.result === 'PENDING'; }});
  var resolved = arr.filter(function(p){{ return p.result && p.result !== 'PENDING'; }});
  var settled  = resolved.filter(function(p){{ return p.result !== 'PUSH'; }});
  var wins   = settled.filter(function(p){{ return p.result === 'WIN'; }}).length;
  var losses = settled.filter(function(p){{ return p.result === 'LOSS'; }}).length;
  var wrBase = wins + losses;
  var wr     = wrBase > 0 ? (wins / wrBase * 100) : 0;

  var totalStake = 0, totalProfit = 0, totalCotes = 0, nCotes = 0;
  settled.forEach(function(p){{
    var s = (p.stake != null) ? p.stake : 1;
    totalStake += s;
    totalProfit += _bkBetDelta(p);
    if(p.cote){{ totalCotes += p.cote; nCotes++; }}
  }});
  var yieldPct = totalStake > 0 ? (totalProfit / totalStake * 100) : 0;
  var avgCote  = nCotes > 0 ? (totalCotes / nCotes) : 0;
  var avgStake = settled.length > 0 ? (totalStake / settled.length) : 0;

  var bk = _getBankroll();
  var bkCurrent = bk + totalProfit;
  var bkPct = bk > 0 ? (totalProfit / bk * 100) : 0;

  // Today's delta
  var todayStr = new Date().toISOString().slice(0, 10);
  var todayDelta = 0;
  resolved.forEach(function(p){{
    if(p.resolved_at && p.resolved_at.slice(0, 10) === todayStr){{
      todayDelta += _bkBetDelta(p);
    }}
  }});
  var todayBase = bkCurrent - todayDelta;
  var todayPct  = todayBase > 0 ? (todayDelta / todayBase * 100) : 0;

  // Streak
  var seq = settled.slice().sort(function(a, b){{ return (a.resolved_at || '').localeCompare(b.resolved_at || ''); }});
  var maxW = 0, maxL = 0, curW = 0, curL = 0;
  seq.forEach(function(p){{
    if(p.result === 'WIN'){{ curW++; if(curW > maxW) maxW = curW; curL = 0; }}
    else if(p.result === 'LOSS'){{ curL++; if(curL > maxL) maxL = curL; curW = 0; }}
  }});

  // Chart
  window._bkPeriod = window._bkPeriod || 'ALL';
  var period = window._bkPeriod;
  var series = _bkBuildSeries(arr, bk, period);
  var chartAccent = totalProfit >= 0 ? '#34D399' : '#F87171';

  // ── Hero ─────────────────────────────────────────────
  var heroDeltaCls = todayDelta > 0 ? 'pos' : (todayDelta < 0 ? 'neg' : 'flat');
  var heroDeltaSign = todayDelta > 0 ? '+' : (todayDelta < 0 ? '−' : '');
  var arrowUp   = '<svg width="10" height="10" viewBox="0 0 10 10"><path d="M1 7 L5 3 L9 7" stroke="currentColor" stroke-width="2" fill="none" stroke-linecap="round" stroke-linejoin="round"/></svg>';
  var arrowDown = '<svg width="10" height="10" viewBox="0 0 10 10"><path d="M1 3 L5 7 L9 3" stroke="currentColor" stroke-width="2" fill="none" stroke-linecap="round" stroke-linejoin="round"/></svg>';
  var todayHtml = (todayDelta !== 0)
    ? '<span class="bk-hero-delta ' + heroDeltaCls + '">' + (todayDelta >= 0 ? arrowUp : arrowDown) + heroDeltaSign + _bkFmt(Math.abs(todayDelta)) + ' € · ' + heroDeltaSign + Math.abs(todayPct).toFixed(2) + '%</span><span class="bk-hero-sub">aujourd\\'hui</span>'
    : '<span class="bk-hero-sub">Aucun pari résolu aujourd\\'hui</span>';
  var totalSign = totalProfit >= 0 ? '+' : '−';
  var totalCls  = totalProfit > 0 ? '#34D399' : (totalProfit < 0 ? '#F87171' : 'var(--bk-text-muted)');
  var hero =
    '<div class="bk-hero">'
    + '<div class="bk-hero-left">'
    +   '<div class="bk-hero-eyebrow">💎 Bankroll principal</div>'
    +   '<div class="bk-hero-amount"><span class="bk-cur">€</span><span>' + _bkFmt(bkCurrent) + '</span></div>'
    +   '<div class="bk-hero-meta">' + todayHtml + '</div>'
    + '</div>'
    + '<div class="bk-hero-right">'
    +   '<button class="bk-btn bk-btn-ghost" onclick="editBankroll()" title="Modifier le bankroll initial">⚙ ' + bk.toFixed(0) + ' € initial</button>'
    +   '<div style="font-size:12px;font-variant-numeric:tabular-nums;color:' + totalCls + ';font-weight:600">'
    +     totalSign + _bkFmt(Math.abs(totalProfit)) + ' € · ' + (bkPct >= 0 ? '+' : '−') + Math.abs(bkPct).toFixed(2) + '% au total'
    +   '</div>'
    + '</div>'
    + '</div>';

  // ── Chart card ──────────────────────────────────────
  var periodBtns = ['7J', '1M', '3M', 'ALL'].map(function(p){{
    var active = period === p;
    return '<button class="' + (active ? 'active' : '') + '" onclick="setBkPeriod(\\'' + p + '\\')">' + p + '</button>';
  }}).join('');
  var chartCard =
    '<div class="bk-card">'
    + '<div class="bk-card-hd">'
    +   '<span class="bk-eyebrow">Évolution</span>'
    +   '<div class="bk-segment">' + periodBtns + '</div>'
    + '</div>'
    + '<div class="bk-chart-wrap">' + _bkRenderChart(series, chartAccent, resolved.length > 0) + '</div>'
    + '</div>';

  // ── StatCards (ROI / Winrate / Profit) ──────────────
  var stats =
    '<div class="bk-stats">'
    + _bkStat('ROI', (yieldPct >= 0 ? '+' : '') + yieldPct.toFixed(1) + '%', settled.length + ' paris', yieldPct >= 0 ? '#34D399' : '#F87171')
    + _bkStat('Winrate', wr.toFixed(0) + '%', wins + '/' + wrBase, wr >= 50 ? '#34D399' : '#F87171')
    + _bkStat('Profit', (totalProfit >= 0 ? '+' : '−') + '€' + Math.abs(totalProfit).toFixed(0), 'depuis le début', totalProfit >= 0 ? '#34D399' : '#F87171')
    + '</div>';

  // ── Streak badge ────────────────────────────────────
  var streakHtml = '';
  if(curW >= 2){{
    streakHtml =
      '<div class="bk-streak">'
      + '<div class="bk-streak-flame">🔥</div>'
      + '<div style="flex:1">'
      +   '<div class="bk-streak-title">Série de ' + curW + ' victoires</div>'
      +   '<div class="bk-streak-sub">Continue ! Max historique : ' + maxW + 'W · ' + maxL + 'L</div>'
      + '</div>'
      + '</div>';
  }} else if(curL >= 3){{
    streakHtml =
      '<div class="bk-streak" style="background:linear-gradient(135deg,rgba(248,113,113,0.16),rgba(127,29,29,0.06));border-color:rgba(248,113,113,0.22)">'
      + '<div class="bk-streak-flame" style="background:radial-gradient(circle at 30% 20%,#F87171,#DC2626 60%,#7F1D1D 100%);box-shadow:0 0 18px rgba(248,113,113,0.45);animation:none">❄️</div>'
      + '<div style="flex:1">'
      +   '<div class="bk-streak-title">Série de ' + curL + ' défaites</div>'
      +   '<div class="bk-streak-sub" style="color:#F87171">Baisse les mises ou prends une pause analytique</div>'
      + '</div>'
      + '</div>';
  }}

  // ── Paris en cours ──────────────────────────────────
  var pendingCard = '';
  if(pending.length > 0){{
    var pendingRows = pending.map(_bkRowHtml).join('');
    pendingCard =
      '<div class="bk-card">'
      + '<div class="bk-card-hd">'
      +   '<div class="bk-card-title">Paris en cours <span style="padding:2px 9px;border-radius:999px;background:rgba(251,191,36,0.14);color:#FBBF24;font-size:11px;font-weight:700">' + pending.length + '</span></div>'
      + '</div>'
      + '<div class="bk-rows">' + pendingRows + '</div>'
      + '</div>';
  }}

  // ── Historique (avec filtres status + tipster) ──────
  window._bkFilters = window._bkFilters || {{status:'all', tipster:'all'}};
  var fStatus = window._bkFilters.status;
  var fTipster = window._bkFilters.tipster;
  // Tous les paris (pending inclus) pour filtrage
  var allForFilter = arr.slice();
  // Counts par status
  var cntAll     = allForFilter.length;
  var cntPending = pending.length;
  var cntWon     = wins;
  var cntLost    = losses;
  var cntPush    = resolved.filter(function(p){{ return p.result === 'PUSH'; }}).length;
  // Tipsters uniques
  var tipsterCounts = {{}};
  allForFilter.forEach(function(p){{
    var t = (p.tipster && p.tipster.trim()) ? p.tipster.trim() : '∅ Sans tipster';
    tipsterCounts[t] = (tipsterCounts[t] || 0) + 1;
  }});
  var tipsterList = Object.keys(tipsterCounts).sort(function(a, b){{ return tipsterCounts[b] - tipsterCounts[a]; }});
  // Apply filters
  var filteredArr = allForFilter.filter(function(p){{
    if(fStatus !== 'all'){{
      var st = !p.result || p.result === 'PENDING' ? 'pending' : (
        p.result === 'WIN' ? 'won' : (p.result === 'LOSS' ? 'lost' : 'push')
      );
      if(st !== fStatus) return false;
    }}
    if(fTipster !== 'all'){{
      var t = (p.tipster && p.tipster.trim()) ? p.tipster.trim() : '∅ Sans tipster';
      if(t !== fTipster) return false;
    }}
    return true;
  }});
  // Build status filter chips
  var statusDefs = [
    {{id:'all',     label:'Tous',     count:cntAll}},
    {{id:'pending', label:'En cours', count:cntPending, color:'#FBBF24'}},
    {{id:'won',     label:'Gagnés',   count:cntWon,     color:'#34D399'}},
    {{id:'lost',    label:'Perdus',   count:cntLost,    color:'#F87171'}},
    {{id:'push',    label:'Annulés',  count:cntPush,    color:'#94A3B8'}},
  ];
  var statusChips = statusDefs.map(function(f){{
    var active = fStatus === f.id;
    return '<button class="bk-filter-chip ' + (active ? 'active' : '') + '" onclick="setBkFilter(\\'status\\', \\'' + f.id + '\\')">'
      + (f.color ? '<span class="dot" style="background:' + f.color + '"></span>' : '')
      + f.label
      + '<span class="count">' + f.count + '</span>'
      + '</button>';
  }}).join('');
  // Build tipster filter chips
  var tipsterChips = '<button class="bk-filter-chip ' + (fTipster === 'all' ? 'active' : '') + '" onclick="setBkFilter(\\'tipster\\', \\'all\\')">'
    + '<span style="font-size:13px">👥</span>Tous tipsters<span class="count">' + tipsterList.length + '</span>'
    + '</button>';
  tipsterChips += tipsterList.map(function(t){{
    var active = fTipster === t;
    var isNone = t === '∅ Sans tipster';
    var ic = isNone ? '∅' : '👤';
    var tEsc = t.replace(/'/g, "\\\\'").replace(/"/g, '&quot;');
    return '<button class="bk-filter-chip ' + (active ? 'active' : '') + '" onclick="setBkFilter(\\'tipster\\', \\'' + tEsc + '\\')" title="' + t + '">'
      + '<span style="font-size:13px">' + ic + '</span>'
      + '<span style="max-width:120px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">' + (isNone ? 'Sans tipster' : t) + '</span>'
      + '<span class="count">' + tipsterCounts[t] + '</span>'
      + '</button>';
  }}).join('');
  var resetBtn = (fStatus !== 'all' || fTipster !== 'all')
    ? '<button class="bk-filter-reset" onclick="resetBkFilters()">Réinitialiser ✕</button>'
    : '';

  var recentCard = '';
  if(allForFilter.length > 0){{
    var filteredRows = filteredArr.length > 0
      ? filteredArr.slice(0, 30).map(_bkRowHtml).join('')
      : '<div class="bk-filter-empty">🔍 Aucun pari ne correspond aux filtres.</div>';
    recentCard =
      '<div class="bk-card">'
      + '<div class="bk-card-hd">'
      +   '<div class="bk-card-title">Historique <span style="padding:2px 9px;border-radius:999px;background:var(--bk-text-soft);color:var(--bk-text-muted);font-size:11px;font-weight:700">' + filteredArr.length + '</span></div>'
      +   resetBtn
      + '</div>'
      + '<div class="bk-filter-row">' + statusChips + '</div>'
      + (tipsterList.length > 0 ? '<div class="bk-filter-row">' + tipsterChips + '</div>' : '')
      + '<div class="bk-rows">' + filteredRows + '</div>'
      + '</div>';
  }}

  // ── Performance par marché ──────────────────────────
  var byProp = {{}};
  settled.forEach(function(p){{
    var k = p.prop || '?';
    if(!byProp[k]) byProp[k] = {{w: 0, l: 0, profit: 0}};
    if(p.result === 'WIN')  byProp[k].w++;
    if(p.result === 'LOSS') byProp[k].l++;
    byProp[k].profit += _bkBetDelta(p);
  }});
  var propEntries = Object.keys(byProp).sort(function(a, b){{ return byProp[b].profit - byProp[a].profit; }});
  var propRows = propEntries.map(function(k){{
    var b = byProp[k];
    var bn = b.w + b.l;
    var bwr = bn > 0 ? (b.w / bn * 100) : 0;
    var barColor = bwr >= 55 ? '#34D399' : (bwr >= 50 ? '#86efac' : (bwr >= 40 ? '#FBBF24' : '#F87171'));
    var pColor = b.profit > 0 ? '#34D399' : (b.profit < 0 ? '#F87171' : '#94A3B8');
    var pSign  = b.profit > 0 ? '+' : (b.profit < 0 ? '−' : '');
    var propIcon  = ({{PTS:'🎯', REB:'🛟', AST:'🎁', FG3M:'🏹', PR:'🎯', PA:'🎁', PRA:'🌟', RA:'🛟'}})[k] || '📊';
    var propLabel = ({{PTS:'Points', REB:'Rebonds', AST:'Passes', FG3M:'3-points', PR:'Pts+Reb', PA:'Pts+Pas', PRA:'PRA', RA:'Reb+Pas'}})[k] || k;
    return '<div class="bk-prop-row">'
      + '<div class="bk-prop-name"><span>' + propIcon + '</span><span>' + propLabel + '</span></div>'
      + '<div class="bk-prop-bar"><div style="width:' + bwr + '%;background:linear-gradient(90deg,' + barColor + '88,' + barColor + ')"></div></div>'
      + '<div class="bk-prop-wr" style="color:' + barColor + '">' + bwr.toFixed(0) + '%<span style="color:var(--bk-text-muted);font-weight:500;font-size:11px"> (' + b.w + '/' + bn + ')</span></div>'
      + '<div class="bk-prop-profit" style="color:' + pColor + '">' + pSign + _bkFmt(Math.abs(b.profit)) + ' €</div>'
      + '</div>';
  }}).join('');
  var propCard = '';
  if(propRows){{
    propCard =
      '<div class="bk-card">'
      + '<div class="bk-card-hd"><div class="bk-card-title">📊 Performance par marché</div></div>'
      + propRows
      + '</div>';
  }}

  // ── Moyennes ────────────────────────────────────────
  var avgCard = '';
  if(settled.length > 0){{
    avgCard =
      '<div class="bk-card">'
      + '<div class="bk-card-hd"><div class="bk-card-title">📐 Moyennes</div></div>'
      + '<div class="bk-stats" style="gap:10px">'
      +   _bkStat('Cote moy.', avgCote.toFixed(2), null, '#7dd3fc')
      +   _bkStat('Mise moy.', _bkFmt(avgStake) + ' €', null, '#FBBF24')
      +   _bkStat('Total misé', totalStake.toFixed(0) + ' €', null, '#A855F7')
      + '</div>'
      + '</div>';
  }}

  // ── Conseils ────────────────────────────────────────
  var advice = [];
  var propKeys = propEntries.filter(function(k){{ return (byProp[k].w + byProp[k].l) >= 3; }});
  if(propKeys.length > 0){{
    propKeys.sort(function(a, b){{
      var wa = byProp[a].w / (byProp[a].w + byProp[a].l || 1);
      var wb = byProp[b].w / (byProp[b].w + byProp[b].l || 1);
      return wb - wa;
    }});
    var bestK = propKeys[0], worstK = propKeys[propKeys.length - 1];
    var bWR = Math.round(byProp[bestK].w / (byProp[bestK].w + byProp[bestK].l) * 100);
    var wWR = Math.round(byProp[worstK].w / (byProp[worstK].w + byProp[worstK].l) * 100);
    if(bWR >= 60) advice.push({{ic:'✅', clr:'#34D399', t:'Tes paris <b>' + bestK + '</b> performent (' + bWR + '% WR sur ' + (byProp[bestK].w + byProp[bestK].l) + ' bets) — continue cette voie.'}});
    if(wWR < 45 && bestK !== worstK) advice.push({{ic:'⚠️', clr:'#F87171', t:'Tes paris <b>' + worstK + '</b> sous-performent (' + wWR + '% WR sur ' + (byProp[worstK].w + byProp[worstK].l) + ') — revoir ou skipper.'}});
  }}
  if(curL >= 3)      advice.push({{ic:'🔻', clr:'#F87171', t:'Streak <b>' + curL + ' défaites consécutives</b> — baisse les mises ou prends une pause.'}});
  else if(curW >= 4) advice.push({{ic:'🔥', clr:'#34D399', t:'Streak <b>' + curW + ' victoires consécutives</b> — attention au biais hot hand, ne monte pas les mises.'}});
  if(settled.length >= 5 && nCotes > 0){{
    var be = 100 / avgCote;
    if(wr > be + 5)      advice.push({{ic:'💎', clr:'#34D399', t:'Tes ' + wr.toFixed(0) + '% WR sont au-dessus du break-even (' + be.toFixed(0) + '% à cote ' + avgCote.toFixed(2) + ') — tu joues +EV.'}});
    else if(wr < be - 5) advice.push({{ic:'📉', clr:'#FBBF24', t:'WR ' + wr.toFixed(0) + '% sous le break-even (' + be.toFixed(0) + '% requis à cote ' + avgCote.toFixed(2) + ') — sélection à affiner.'}});
  }}
  if(avgStake > bk * 0.05 && bk > 0) advice.push({{ic:'⚠️', clr:'#FBBF24', t:'Mise moy. ' + _bkFmt(avgStake) + ' € élevée (' + Math.round(avgStake / bk * 100) + '% du bankroll) — Kelly recommande 1-3% (' + _bkFmt(bk * 0.02) + ' € suggéré).'}});
  if(pending.length > 10) advice.push({{ic:'📋', clr:'#94A3B8', t:'<b>' + pending.length + ' paris pending</b> — résous les anciens pour des stats à jour.'}});

  var adviceCard = '';
  if(advice.length){{
    var rows = advice.map(function(a){{
      return '<div class="bk-advice-row" style="border-left-color:' + a.clr + '"><span class="ic">' + a.ic + '</span><span>' + a.t + '</span></div>';
    }}).join('');
    adviceCard =
      '<div class="bk-card">'
      + '<div class="bk-card-hd"><div class="bk-card-title">💡 Conseils & insights</div></div>'
      + '<div class="bk-advice">' + rows + '</div>'
      + '</div>';
  }}

  // ── Compose 2-col layout ────────────────────────────
  var html = hero + chartCard + stats + streakHtml
    + '<div class="bk-cols">'
    +   '<div class="bk-col">' + pendingCard + recentCard + '</div>'
    +   '<div class="bk-col">' + propCard + avgCard + adviceCard + '</div>'
    + '</div>';
  container.innerHTML = html;
}}

// Refresh count badge on page load + render
window.addEventListener('DOMContentLoaded', function(){{
  _updateUserPicksCount();
  renderUserPicks();
}});
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
        # Egalement le fichier d'historique minimal pour le polling JS du bankroll
        import os as _os_p
        if _os_p.path.exists("data/nba_history_min.json"):
            try:
                subprocess.run(["git", "add", "-f", "data/nba_history_min.json"], check=True, timeout=10)
            except Exception:
                pass

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