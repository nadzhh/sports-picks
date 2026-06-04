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

def _ts_to_paris(ts):
    """Convertit un Unix timestamp en datetime aware Europe/Paris.

    Important : `datetime.fromtimestamp(ts)` (sans tz) renvoie l'heure du
    fuseau systeme. Sur GH Actions (UTC), ca affichait UTC au lieu de Paris.
    On utilise timezone fixe CEST (UTC+2) pour l'ete (toujours +2 entre fin
    mars et fin oct, qui couvre toute la saison de matchs).
    """
    from datetime import timezone as _tz, timedelta as _td
    paris = _tz(_td(hours=2))  # CEST
    return datetime.fromtimestamp(ts, tz=paris)

def format_datetime(ts):
    try: return _ts_to_paris(ts).strftime("%d/%m %H:%M")
    except: return "?"

def day_label(ts):
    try:
        from datetime import timezone as _tz, timedelta as _td
        paris = _tz(_td(hours=2))
        d = _ts_to_paris(ts).date()
        today = datetime.now(paris).date()
        if d == today: return "Aujourd'hui"
        if d == today + timedelta(1): return "Demain"
        if d == today + timedelta(2): return "Après-demain"
        return _ts_to_paris(ts).strftime("%A %d/%m").capitalize()
    except: return "Autre"

def cote_badge(cote):
    if not cote: return ""
    return (f'<span style="display:inline-flex;align-items:center;gap:3px;background:#1e3a5f;'
            f'color:#60a5fa;border:1px solid #2563eb;border-radius:6px;padding:2px 8px;'
            f'font-size:12px;font-weight:700;margin-left:6px">📊 {cote:.2f}</span>')


import html as _html
import json as _json_jsattr

def _js_attr_str(s):
    """Produit un literal JS valide a embarquer dans onclick=\"...\".
    Utilise une chaine JS double-quote avec encodage HTML des " (&quot;), ce
    qui survit au double decodage HTML puis JS du navigateur. Gere correctement
    les apostrophes (ex: De'Aaron Fox) que _html.escape(quote=True) cassait."""
    return _json_jsattr.dumps("" if s is None else str(s)).replace('"', '&quot;')


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

    def match_rows_l5(l5_list, side, team_name=""):
        if not l5_list:
            return '<span style="color:#475569">—</span>'
        html = '<div style="font-size:13px">'
        for d in l5_list:
            opp      = d.get("opponent", "?")
            opp_rank = d.get("opp_rank")  # rang de l'adversaire dans son championnat
            gf       = d.get("gf"); ga = d.get("ga")
            is_home_match = bool(d.get("is_home"))
            # Format scoreboard standard "Domicile X - Y Exterieur" en mettant
            # notre equipe a sa vraie position (home ou away dans ce match-la)
            if gf is not None and ga is not None:
                if is_home_match:
                    left_team, right_team = team_name or "Notre équipe", opp
                    left_score, right_score = gf, ga
                else:
                    left_team, right_team = opp, team_name or "Notre équipe"
                    left_score, right_score = ga, gf
                # Highlight notre equipe en gras (le reste plus discret)
                left_html  = f'<b style="color:#f1f5f9">{left_team}</b>'  if is_home_match else f'<span style="color:#cbd5e1">{left_team}</span>'
                right_html = f'<span style="color:#cbd5e1">{right_team}</span>' if is_home_match else f'<b style="color:#f1f5f9">{right_team}</b>'
                score_line = (
                    f'{left_html} '
                    f'<span style="color:#f1f5f9;font-weight:800;font-size:15px;padding:0 4px">{left_score} - {right_score}</span> '
                    f'{right_html}'
                )
            else:
                score_line = f'<span style="color:#cbd5e1">{team_name or "Notre équipe"} vs {opp} ({d.get("score","?")})</span>'
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

            # Ligne header du mini-match : format scoreboard standard
            # (sans W/L/D — l'utilisateur peut lire le score directement)
            date_html = f'<span style="color:#94a3b8;font-size:11.5px;font-weight:500;min-width:38px">{date_str}</span>' if date_str else ""
            html += (
                f'<div style="padding:10px 4px 8px;border-bottom:1px solid #1e2940">'
                f'<div style="display:flex;align-items:center;gap:8px;font-size:13.5px">'
                f'{date_html}'
                f'<span style="flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">{score_line}</span>'
                f'{opp_rank_badge}'
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
            f'<div style="background:#0a1628;padding:10px 12px">{match_rows_l5(h_l5, "home", home)}</div>'
            f'<div style="background:#0a1628;padding:10px 12px">{match_rows_l5(a_l5, "away", away)}</div>'
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
            date_str = ""
            d = h.get("date","")
            if d:
                try:
                    from datetime import datetime as _dt
                    date_str = _dt.fromisoformat(d.replace("Z","+00:00")).strftime("%d/%m/%Y")
                except: date_str = d[:10]
            score = h.get("score", "?")
            league = h.get("league", "")
            h_team = h.get("home_team","")
            a_team = h.get("away_team","")
            # Decoupe le score "2-1" en deux nombres
            gf_g = ga_g = None
            try:
                if "-" in score:
                    parts = score.split("-")
                    gf_g = int(parts[0].strip()); ga_g = int(parts[1].strip())
            except: pass
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
            # Highlight l'equipe qui correspond au home du match a venir (POV)
            h_bold = "f1f5f9"  # blanc franc
            a_bold = "cbd5e1"  # un poil plus discret
            if h_team and a_team and h_team.lower() == home.lower():
                h_html = f'<b style="color:#{h_bold}">{h_team}</b>'
                a_html = f'<span style="color:#{a_bold}">{a_team}</span>'
            elif a_team and h_team and a_team.lower() == home.lower():
                h_html = f'<span style="color:#{a_bold}">{h_team}</span>'
                a_html = f'<b style="color:#{h_bold}">{a_team}</b>'
            else:
                h_html = f'<span style="color:#{h_bold}">{h_team}</span>'
                a_html = f'<span style="color:#{h_bold}">{a_team}</span>'
            if gf_g is not None and ga_g is not None:
                score_block = (
                    f'<span style="flex:1;font-size:13.5px">'
                    f'{h_html} '
                    f'<span style="color:#f1f5f9;font-weight:800;font-size:15px;padding:0 4px">{gf_g} - {ga_g}</span> '
                    f'{a_html}'
                    f'</span>'
                )
            else:
                score_block = f'<span style="flex:1;font-size:13.5px">{h_html} <span style="color:#64748b">vs</span> {a_html} <span style="color:#94a3b8">{score}</span></span>'
            h2h_html += (
                f'<div style="padding:10px 14px;border-bottom:1px solid #1a2540">'
                f'<div style="display:flex;align-items:center;gap:10px">'
                f'<span style="color:#94a3b8;font-size:12px;font-weight:500;min-width:80px">{date_str}</span>'
                f'{score_block}'
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
            f'<span style="color:#94a3b8;font-size:12px;font-weight:600" title="Stats cumulées sur la saison (toutes competitions)">Saison · {apps} m</span>'
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
            f'background:#0d1b2e;margin-top:2px">⭐ JOUEURS DÉCISIFS (SAISON)</div>'
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
    # Bouton Add bankroll universel
    add_btn = _build_foot_add_btn(p, match_ctx, kind="team")

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
        f'{add_btn}'
        f'</div>'
        f'</div>'
        f'<div style="color:#94a3b8;font-size:13px;margin-top:8px;line-height:1.55">{p["reasoning"].replace(chr(10), "<br>")}</div>'
        f'<div style="margin-top:6px">{form}</div>'
        f'{advice}'
        f'{ai_bl}'
        f'</div>'
    )


_FOOT_LABEL_RE = __import__("re").compile(r"^(Plus de|Moins de)\s+([\d.,]+)\s+(.+)$", __import__("re").IGNORECASE)


def _parse_foot_label(label):
    """Extrait (direction, line) d'un label foot.

    'Plus de 22.5 tirs total match' -> ('over', 22.5)
    'Moins de 2.5 buts'             -> ('under', 2.5)
    'Buteur : Mbappé'               -> (None, None)
    'Double chance X2'              -> (None, None)
    """
    if not label:
        return None, None
    m = _FOOT_LABEL_RE.match(label)
    if not m:
        return None, None
    direction = "over" if m.group(1).lower().startswith("plus") else "under"
    try:
        line = float(m.group(2).replace(",", "."))
    except ValueError:
        return None, None
    return direction, line


def _build_foot_add_btn(p, match_ctx, kind="team"):
    """Bouton Add bankroll pour un pick foot (team/player/fun).

    Parse le label ("Plus de 22.5 tirs total" -> line=22.5, direction='over')
    pour permettre la modification de la ligne dans le modal.
    """
    import json as _json
    if match_ctx is None: match_ctx = {}
    home = match_ctx.get("home", ""); away = match_ctx.get("away", "")
    matchup = (away + " vs " + home) if (home and away) else ""
    player = p.get("player") if kind == "player" else (home or "")
    real_cote = p.get("cote") or p.get("cote_min")
    label = p.get("label", "")
    direction, line = _parse_foot_label(label)
    payload = {
        "sport":      "foot",
        "label":      label,
        "direction":  direction,        # over/under si label parsable, sinon None
        "line":       line,              # numerique si label parsable, sinon None
        "real_cote":  real_cote,
        "player":     player,
        "matchup":    matchup,
        "home":       home,
        "away":       away,
        "match_id":   str(match_ctx.get("match_id", "")),
        "match_date": match_ctx.get("date") or match_ctx.get("match_date"),
        "kind":       p.get("type", ""),
    }
    attr = _json.dumps(payload, ensure_ascii=False).replace('"', '&quot;')
    return (
        '<button onclick="event.stopPropagation();addAnyPick(JSON.parse(this.dataset.p))" '
        f'data-p="{attr}" '
        'style="background:#1e3a8a;color:#bfdbfe;border:1px solid #3b82f6;border-radius:6px;'
        'padding:3px 10px;font-size:11px;font-weight:700;cursor:pointer;white-space:nowrap">'
        '📌 Add</button>'
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
    add_btn = _build_foot_add_btn(p, match_ctx, kind="player")
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
        f'{add_btn}'
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
    add_btn = _build_foot_add_btn(p, match_ctx, kind="fun")
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
        f'{add_btn}'
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
    if n_picks == 0 and n_players == 0:
        picks_label = "💤 Pas de pick"
    else:
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
        # Signal POSITIF venue/H2H : confirmation forte (>=70% hit rate sur direction)
        sig_confirm = p.get("signal_confirm", "")
        if sig_confirm:
            sig_bonus = p.get("signal_bonus", 0)
            rot_html += (
                f'<div style="color:#34d399;font-size:11px;font-weight:700;margin-top:3px;'
                f'background:rgba(52,211,153,0.10);border-left:2px solid #34d399;padding:3px 7px;border-radius:3px">'
                f'✅ {sig_confirm} (+{sig_bonus}pp confiance)'
                f'</div>'
            )
        # Signal warning venue tiede (31-45% hit rate)
        sig_warn = p.get("signal_warning", "")
        if sig_warn:
            rot_html += (
                f'<div style="color:#fbbf24;font-size:11px;font-weight:600;margin-top:3px;'
                f'background:rgba(251,191,36,0.08);border-left:2px solid #fbbf24;padding:3px 7px;border-radius:3px">'
                f'{sig_warn}'
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
        # Bouton Add bankroll
        import json as _json
        nba_payload = {
            "sport":      "nba",
            "prop":       p.get("prop", ""),
            "label":      p.get("label", ""),
            "direction":  p.get("direction", "over"),
            "line":       p.get("line"),
            "real_cote":  real_cote or cote_min,
            "book_over":  p.get("book_over"),
            "book_under": p.get("book_under"),
            "book_line":  p.get("line"),
            "player":     p.get("player", "?"),
            "matchup":    f"{away} @ {home}",
            "home":       home,
            "away":       away,
            "game_id":    str(gid),
            "match_date": date,
        }
        nba_attr = _json.dumps(nba_payload, ensure_ascii=False).replace('"', '&quot;')
        nba_add_btn = (
            f'<button onclick="event.stopPropagation();addAnyPick(JSON.parse(this.dataset.p))" '
            f'data-p="{nba_attr}" title="Ajouter ce pari à la bankroll" '
            f'style="background:#1e3a8a;color:#bfdbfe;border:1px solid #3b82f6;border-radius:6px;'
            f'padding:3px 8px;font-size:12px;font-weight:700;cursor:pointer">📌</button>'
        )
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
            f'<button onclick="event.stopPropagation();goToAnalyse({_js_attr_str(p.get("player","?"))}, {_js_attr_str(p.get("prop","?"))})" '
            f'title="Voir l\'analyse complete du joueur" '
            f'style="background:#1e3a8a;color:#bfdbfe;border:1px solid #3b82f6;border-radius:6px;'
            f'padding:3px 8px;font-size:12px;font-weight:700;cursor:pointer">🔍</button>'
            f'{nba_add_btn}'
            f'{_push_button(_format_push_nba(p, game))}'
            f'</div>'
            f'</div>'
            f'</div>'
        )

    home_html = "".join(render_pick(p) for p in home_picks) or '<div style="color:#475569;font-size:12px;padding:6px">Aucun pick avec value suffisante</div>'
    away_html = "".join(render_pick(p) for p in away_picks) or '<div style="color:#475569;font-size:12px;padding:6px">Aucun pick avec value suffisante</div>'

    # Compte total de picks pour afficher dans le bouton header
    n_picks = len(home_picks) + len(away_picks)
    picks_label = f"🎯 {n_picks} picks" if n_picks else "Aucun pick"
    gid_safe = str(gid).replace("'", "")

    return (
        f'<div style="background:#0f172a;border-radius:14px;margin-bottom:18px;'
        f'box-shadow:0 4px 20px rgba(0,0,0,0.4);overflow:hidden">'
        # Header cliquable -> toggle body
        f'<div class="nba-match-header" onclick="toggleNbaMatch(\'{gid_safe}\')">'
        f'<div style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:8px">'
        f'<div>'
        f'<div style="color:#fb923c;font-size:11px;text-transform:uppercase;letter-spacing:1px;font-weight:700">🏀 NBA</div>'
        f'<div style="color:#f1f5f9;font-size:19px;font-weight:700;margin-top:3px">'
        f'{away} <span style="color:#334155">@</span> {home}</div>'
        f'</div>'
        f'<div style="display:flex;align-items:center;gap:12px">'
        f'<div style="color:#475569;font-size:13px">🕐 {date} · {status}</div>'
        f'<span style="background:#1e3a8a;color:#bfdbfe;border:1px solid #3b82f6;border-radius:6px;padding:3px 10px;font-size:12px;font-weight:700">{picks_label}</span>'
        f'<div id="nba-arrow-{gid_safe}" style="color:#475569;font-size:14px;transition:transform .2s">▼</div>'
        f'</div>'
        f'</div>'
        f'</div>'
        # Body : 2 colonnes, masque par defaut
        f'<div id="nba-body-{gid_safe}" class="nba-match-body">'
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
        "sport":       "NBA",
        "player":      player_name,
        "prop":        prop,
        "line":        ref_line,
        "home":        ctx.get("home", ""),
        "away":        ctx.get("away", ""),
        "game_id":     ctx.get("game_id", ""),
        "match_date":  ctx.get("match_date", ""),  # CRITIQUE en playoffs (meme matchup multiple)
        "opp":         opp_abbr or "",
        "median":      median,
        "mean":        mean,
        "book_line":   book_line,
        "book_over":   book_over,
        "book_under":  book_under,
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


def _enrich_players_injury(players):
    """Annote chaque joueur avec injury_status (designation) et injury_return.
    Source principale : ESPN (gratuit, pas de quota). Fallback : Tank01 si dispo."""
    try:
        from nba_espn_injuries import get_player_injury as _espn_inj
    except ImportError:
        _espn_inj = None
    try:
        from nba_tank01 import get_player_info as _tank01_info
    except ImportError:
        _tank01_info = None
    for p in players:
        if "injury_status" in p:
            continue
        name = p.get("name", "")
        if not name:
            continue
        # 1. ESPN (gratuit) en priorite
        if _espn_inj:
            info = _espn_inj(name)
            if info and info.get("designation"):
                p["injury_status"]      = info["designation"]
                p["injury_return"]      = info.get("return_date", "")
                p["injury_description"] = info.get("description", "")
                continue
        # 2. Fallback Tank01 (RapidAPI, quota)
        if _tank01_info:
            try:
                tk = _tank01_info(name) or {}
            except Exception:
                tk = {}
            inj = (tk.get("injury") or {}) if isinstance(tk, dict) else {}
            desig = (inj.get("designation") or "").strip()
            if desig:
                p["injury_status"]      = desig
                p["injury_return"]      = (inj.get("injReturnDate") or "").strip()
                p["injury_description"] = (inj.get("description") or "").strip()


def _injury_badge_html(player):
    """Petite pastille indiquant le statut blessure (incertain/out/etc)."""
    status = (player.get("injury_status") or "").strip()
    if not status:
        return ""
    low = status.lower()
    if any(k in low for k in ("out", "ruled out")):
        bg, fg, ic = "rgba(239,68,68,0.18)", "#f87171", "❌"
    elif "doubtful" in low:
        bg, fg, ic = "rgba(239,68,68,0.15)", "#fca5a5", "⚠"
    elif "questionable" in low or "day-to-day" in low or "day to day" in low:
        bg, fg, ic = "rgba(251,191,36,0.18)", "#fbbf24", "🩹"
    else:
        bg, fg, ic = "rgba(168,85,247,0.18)", "#c4b5fd", "ℹ"
    ret  = player.get("injury_return", "")
    desc = player.get("injury_description", "")
    tip_parts = []
    if desc: tip_parts.append(desc)
    if ret:  tip_parts.append(f"Retour estime : {ret}")
    tip = _html.escape(" · ".join(tip_parts), quote=True)
    return (
        f'<span title="{tip}" style="background:{bg};color:{fg};border-radius:999px;'
        f'padding:2px 8px;font-size:10px;font-weight:700;letter-spacing:0.3px;'
        f'margin-left:4px;cursor:help">{ic} {status}</span>'
    )


def _build_injuries_card(home_team, away_team, home_abbr="", away_abbr=""):
    """Card recap 'Blessures et suspensions' par match. Source : ESPN (gratuit).
    Renvoie '' si aucun blesse dans les 2 equipes."""
    try:
        from nba_espn_injuries import get_team_injuries
    except ImportError:
        return ""

    def _by_team(team_name, abbr):
        items = get_team_injuries(team_name) or []
        if not items and abbr:
            items = get_team_injuries(abbr) or []
        return items

    home_inj = _by_team(home_team, home_abbr)
    away_inj = _by_team(away_team, away_abbr)
    if not home_inj and not away_inj:
        return ""

    def _chip(i):
        status = (i.get("designation") or "").strip()
        low = status.lower()
        if any(k in low for k in ("out", "ruled out")):
            bg, fg, ic, lbl = "rgba(239,68,68,0.16)", "#fca5a5", "❌", "Blessé"
        elif "doubtful" in low:
            bg, fg, ic, lbl = "rgba(239,68,68,0.12)", "#fda4af", "⚠", "Doubtful"
        elif "questionable" in low or "day-to-day" in low or "day to day" in low:
            bg, fg, ic, lbl = "rgba(251,191,36,0.16)", "#fde68a", "🩹", "Incertain"
        else:
            bg, fg, ic, lbl = "rgba(168,85,247,0.16)", "#c4b5fd", "ℹ", status
        desc = _html.escape(i.get("description") or "", quote=True)
        return (
            f'<div title="{desc}" style="display:flex;align-items:center;gap:8px;padding:6px 10px;'
            f'background:{bg};border:1px solid {fg}44;border-radius:10px;font-size:12.5px;cursor:help">'
            f'<span style="font-size:14px">{ic}</span>'
            f'<span style="color:#f1f5f9;font-weight:600">{i.get("name","?")}</span>'
            f'<span style="color:{fg};font-weight:700;margin-left:auto">{lbl}</span>'
            f'</div>'
        )

    def _col(label, items):
        if not items:
            return (
                f'<div>'
                f'<div style="color:#64748b;font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:0.6px;margin-bottom:6px">{label}</div>'
                f'<div style="color:#475569;font-size:12px;padding:8px 10px;border:1px dashed #1e293b;border-radius:10px">Aucun joueur indisponible signalé</div>'
                f'</div>'
            )
        rows = "".join(_chip(i) for i in items)
        return (
            f'<div>'
            f'<div style="color:#64748b;font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:0.6px;margin-bottom:6px">{label} <span style="color:#94a3b8;font-weight:600">({len(items)})</span></div>'
            f'<div style="display:flex;flex-direction:column;gap:5px">{rows}</div>'
            f'</div>'
        )

    return (
        f'<div style="background:#0a1628;border:1px solid #1e293b;border-radius:12px;padding:12px 14px;margin-bottom:14px">'
        f'<div style="text-align:center;color:#cbd5e1;font-size:12px;font-weight:700;text-transform:uppercase;letter-spacing:1px;margin-bottom:10px">🩹 Blessures et suspensions</div>'
        f'<div style="display:grid;grid-template-columns:1fr 1fr;gap:14px">'
        f'{_col(f"🏠 {home_team}", home_inj)}'
        f'{_col(f"✈️ {away_team}", away_inj)}'
        f'</div>'
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
        f'{_injury_badge_html(player)}'
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


def build_nba_history_section(nba_history, nba_box_scores, nba_player_stats, nba_odds):
    """Section Historique NBA (sous-section de Analyse NBA) : matchs joués
    entre 2h et 48h ago, avec les MEMES cards joueur que la section Analyse.
    Permet d'ajouter retroactivement un pari a la bankroll via les boutons
    "📌 Add OVER / UNDER" existants — le match_date du passé est embarqué
    dans le payload donc auto-resolve via box_scores en arriere-plan.
    """
    if not nba_history or not nba_history.get("picks") or not nba_box_scores:
        return ""
    from datetime import datetime as _dt3, timezone as _tz3, timedelta as _td3
    import re as _re3
    _now_utc3 = _dt3.now(_tz3.utc)

    # Filter past games (2h-48h ago, sample par game_id, pas par pick)
    past_games = []
    seen = set()
    for _p in nba_history.get("picks", []):
        _gid = str(_p.get("game_id") or "")
        if not _gid or _gid in seen: continue
        _ra = _p.get("resolved_at") or ""
        if not _ra: continue
        try:
            _end_utc = _dt3.strptime(_ra, "%Y-%m-%d %H:%M").replace(tzinfo=_tz3.utc)
        except Exception:
            continue
        _hours_ago = (_now_utc3 - _end_utc).total_seconds() / 3600.0
        if _hours_ago < 2.0 or _hours_ago > 48.0: continue
        if _gid not in nba_box_scores: continue
        # Parse matchup "Away @ Home"
        _matchup = _p.get("matchup", "") or ""
        _m = _re3.match(r'^(.+?)\s*@\s*(.+)$', _matchup)
        if not _m: continue
        _away, _home = _m.group(1).strip(), _m.group(2).strip()
        past_games.append({
            "game_id": _gid,
            "date":     _p.get("date", ""),
            "matchup":  _matchup,
            "home":     _home,
            "away":     _away,
            "end_utc":  _end_utc.isoformat(),
            "hours_ago": _hours_ago,
            "box":      nba_box_scores[_gid] or {},
        })
        seen.add(_gid)
    if not past_games:
        return ""

    # Build player_lookup depuis nba_player_stats actuel (donne acces aux
    # l10_games + season_avg + position pour chaque joueur present aujourd'hui)
    player_lookup = {}
    for _gid2, _md in (nba_player_stats or {}).items():
        if _gid2.startswith("_"): continue
        _h_name = _md.get("home_team", ""); _h_abbr = _md.get("home_abbr", "") or ""
        _a_name = _md.get("away_team", ""); _a_abbr = _md.get("away_abbr", "") or ""
        for _pl in _md.get("home_players", []) or []:
            _n = _pl.get("name", "")
            if _n and _n not in player_lookup:
                player_lookup[_n] = {"team_name": _h_name, "team_abbr": _h_abbr, "data": _pl}
        for _pl in _md.get("away_players", []) or []:
            _n = _pl.get("name", "")
            if _n and _n not in player_lookup:
                player_lookup[_n] = {"team_name": _a_name, "team_abbr": _a_abbr, "data": _pl}

    # Sort past games par hours_ago croissant (le plus recent en haut)
    past_games.sort(key=lambda g: g["hours_ago"])

    cards = ""
    for game in past_games:
        gid = game["game_id"]
        home = game["home"]; away = game["away"]
        # Repartit les joueurs du box score entre home et away en utilisant
        # le player_lookup (qui connait l'equipe actuelle de chaque joueur)
        home_players_list = []
        away_players_list = []
        for pname in game["box"].keys():
            lk = player_lookup.get(pname)
            if not lk: continue   # joueur pas dans le lookup actuel, skip (limite acceptable)
            tn = lk["team_name"] or ""
            if tn == home or (lk["team_abbr"] and lk["team_abbr"] in home.upper()):
                home_players_list.append(lk["data"])
            elif tn == away or (lk["team_abbr"] and lk["team_abbr"] in away.upper()):
                away_players_list.append(lk["data"])
            else:
                # Si le team_name ne match pas exactement (ex "Knicks" vs "New York Knicks"), fallback par token
                _tn_low = tn.lower()
                _home_low = home.lower()
                _away_low = away.lower()
                if any(tok in _home_low for tok in _tn_low.split() if len(tok) >= 4):
                    home_players_list.append(lk["data"])
                elif any(tok in _away_low for tok in _tn_low.split() if len(tok) >= 4):
                    away_players_list.append(lk["data"])
        # Tri par minutes saison + cap 12
        home_players_list.sort(key=lambda p: (p.get("season_avg",{}) or {}).get("MIN", 0), reverse=True)
        away_players_list.sort(key=lambda p: (p.get("season_avg",{}) or {}).get("MIN", 0), reverse=True)
        home_players_list = home_players_list[:12]
        away_players_list = away_players_list[:12]
        # Abbreviations a partir du player_lookup
        home_abbr = (player_lookup.get(home_players_list[0]["name"], {}).get("team_abbr", "") if home_players_list else "")
        away_abbr = (player_lookup.get(away_players_list[0]["name"], {}).get("team_abbr", "") if away_players_list else "")
        # Enrichissement statut blessure (mais cache 3h depuis ESPN)
        _enrich_players_injury(home_players_list + away_players_list)
        # match_ctx avec la DATE DU PASSE → les boutons Add OVER/UNDER embarquent
        # cette date dans le payload → le pick stocke est lie au bon match
        match_ctx = {"home": home, "away": away, "game_id": gid, "match_date": game["date"]}
        odds_for_game = {}   # pas de cotes book pour matchs passes
        home_cards = "".join(
            _build_player_analyse_card(p, opp_abbr=away_abbr, odds_for_player=None,
                                       side_label=f"🏠 {home}", is_starter=(i < 5), match_ctx=match_ctx)
            for i, p in enumerate(home_players_list)
        )
        away_cards = "".join(
            _build_player_analyse_card(p, opp_abbr=home_abbr, odds_for_player=None,
                                       side_label=f"✈️ {away}", is_starter=(i < 5), match_ctx=match_ctx)
            for i, p in enumerate(away_players_list)
        )
        # Time label
        h_ago = int(game["hours_ago"])
        if h_ago < 12:    time_label = f"Cette nuit (il y a {h_ago}h)"
        elif h_ago < 24:  time_label = f"Aujourd'hui (il y a {h_ago}h)"
        elif h_ago < 36:  time_label = f"Hier soir (il y a {h_ago}h)"
        elif h_ago < 48:  time_label = f"Hier (il y a {h_ago}h)"
        else:             time_label = f"Avant-hier (il y a {h_ago}h)"
        gid_safe = str(gid).replace("'", "")
        # Recap blessures (meme card que la section Analyse principale)
        injuries_block = _build_injuries_card(home, away, home_abbr, away_abbr)
        cards += (
            f'<div style="background:#0f172a;border-radius:14px;margin-bottom:18px;'
            f'box-shadow:0 4px 20px rgba(0,0,0,0.4);overflow:hidden;border-left:3px solid #fbbf24">'
            # Header cliquable -> toggle body (reuse logique NBA)
            f'<div class="nba-match-header" onclick="toggleNbaMatch(\'hist-{gid_safe}\')">'
            f'<div style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:8px">'
            f'<div>'
            f'<div style="color:#fbbf24;font-size:11px;text-transform:uppercase;letter-spacing:1px;font-weight:700">📅 {time_label}</div>'
            f'<div style="color:#f1f5f9;font-size:19px;font-weight:700;margin-top:3px">'
            f'{away} <span style="color:#334155">@</span> {home}</div>'
            f'</div>'
            f'<div style="display:flex;align-items:center;gap:12px">'
            f'<div style="color:#475569;font-size:13px">{game.get("date","")}</div>'
            f'<div id="nba-arrow-hist-{gid_safe}" style="color:#475569;font-size:14px;transition:transform .2s">▼</div>'
            f'</div>'
            f'</div>'
            f'</div>'
            # Body deroulable
            f'<div id="nba-body-hist-{gid_safe}" class="nba-match-body" style="padding:14px 18px">'
            f'{injuries_block}'
            f'<div style="display:grid;grid-template-columns:1fr 1fr;gap:12px">'
            f'<div><div style="color:#3b82f6;font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:1px;margin-bottom:8px">🏠 {home}</div>{home_cards or "Pas de joueurs"}</div>'
            f'<div><div style="color:#3b82f6;font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:1px;margin-bottom:8px">✈️ {away}</div>{away_cards or "Pas de joueurs"}</div>'
            f'</div>'
            f'</div>'
            f'</div>'
        )
    return cards


def build_nba_analyse_section(nba_picks_data, nba_player_stats, nba_odds):
    """Section Analyse : pour chaque match a venir, panneau de joueurs avec
    bar chart selectable par prop (PTS/REB/AST/3PM/PR/PA/PRA)."""
    if not nba_player_stats:
        return (
            '<div style="text-align:center;padding:40px;color:#64748b">'
            'Pas de stats joueurs NBA disponibles. Lance nba_scraper.py d\'abord.'
            '</div>'
        )
    # Map game_id -> date YYYY-MM-DD (critique en playoffs : meme matchup tous les 2 jours,
    # le pick doit etre lie a la BONNE date sinon les stats du mauvais match s'appliquent).
    import json as _json2
    _date_by_gid = {}
    try:
        with open("data/nba_matches.json", encoding="utf-8") as _mf:
            for _m in _json2.load(_mf):
                _gid = _m.get("game_id")
                _dt  = (_m.get("date") or "")[:10]
                if _gid: _date_by_gid[str(_gid)] = _dt
    except Exception: pass
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
        # Enrichissement statut blessure (Tank01) - cache 12h, max ~50 calls/run
        _enrich_players_injury(home_players + away_players)
        odds_for_game = nba_odds.get(gid) or nba_odds.get(str(gid)) or {}
        match_date = _date_by_gid.get(str(gid), "")
        match_ctx = {"home": home, "away": away, "game_id": gid, "match_date": match_date}

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
        # Recap blessures par equipe (source ESPN, gratuit)
        injuries_block = _build_injuries_card(home, away, home_abbr, away_abbr)
        cards += (
            f'<div style="background:#0f172a;border-radius:14px;margin-bottom:18px;padding:14px 18px;box-shadow:0 4px 20px rgba(0,0,0,0.4)">'
            f'<div style="color:#fb923c;font-size:11px;text-transform:uppercase;letter-spacing:1px;font-weight:700;margin-bottom:4px">🏀 NBA</div>'
            f'<div style="color:#f1f5f9;font-size:18px;font-weight:700;margin-bottom:14px">{away} <span style="color:#334155">@</span> {home}</div>'
            f'{injuries_block}'
            f'<div style="display:grid;grid-template-columns:1fr 1fr;gap:12px">'
            f'<div><div style="color:#3b82f6;font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:1px;margin-bottom:8px">🏠 {home}</div>{home_cards or "Pas de joueurs"}</div>'
            f'<div><div style="color:#3b82f6;font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:1px;margin-bottom:8px">✈️ {away}</div>{away_cards or "Pas de joueurs"}</div>'
            f'</div>'
            f'</div>'
        )
    if not cards:
        return '<div style="text-align:center;padding:40px;color:#64748b">Aucun match NBA a venir.</div>'
    return cards


def build_tennis_section(tennis_picks_data):
    """Section Tennis : grid 2-col de cartes collapsibles triees par confiance."""
    if not tennis_picks_data:
        return ('<div style="text-align:center;padding:40px;color:#64748b">'
                'Pas de tournoi ATP/WTA actif aujourd\'hui.</div>')
    matches = tennis_picks_data.get("matches", [])
    if not matches:
        return ('<div style="text-align:center;padding:40px;color:#64748b">'
                'Aucun pick tennis intéressant détecté pour aujourd\'hui (algo strict).</div>')
    # Tri : matchs AVEC picks (confiance desc) en premier, puis matchs sans picks
    def _sort_key(m):
        has_picks = 1 if m.get("picks") else 0
        max_conf = max((p.get("confidence", 0) or 0) for p in m.get("picks", [])) if m.get("picks") else 0
        return (has_picks, max_conf)
    matches = sorted(matches, key=_sort_key, reverse=True)
    cards = [_build_tennis_card(m, idx) for idx, m in enumerate(matches)]
    # Toolbar : tout ouvrir/fermer + compteur dynamique (mis a jour par JS quand
    # des cards passent l'heure de debut)
    toolbar = (
        '<div style="display:flex;gap:8px;margin-bottom:14px;flex-wrap:wrap;align-items:center">'
        '  <button onclick="collapseAllTennis()" style="background:#1e293b;color:#94a3b8;border:1px solid #334155;border-radius:8px;padding:8px 14px;font-size:12px;font-weight:700;cursor:pointer">📕 Tout fermer</button>'
        '  <button onclick="expandAllTennis()" style="background:#1e293b;color:#94a3b8;border:1px solid #334155;border-radius:8px;padding:8px 14px;font-size:12px;font-weight:700;cursor:pointer">📖 Tout ouvrir</button>'
        f'  <span style="margin-left:auto;color:#64748b;font-size:12px"><span id="tennis-visible-count">{len(matches)}</span> match(s) · triés par confiance algo</span>'
        '</div>'
    )
    grid = '<div class="tennis-grid">' + "\n".join(cards) + '</div>'
    return toolbar + grid


def _build_tennis_card(match, idx=0):
    """Render 1 carte match tennis collapsible. Body cache par defaut.

    Layout :
      - Header (toujours visible) : tournoi + surface + heure · joueurs + cotes
        + chip "meilleur pick" + chevron expand
      - Body (cache) : stats detaillees joueurs + tous les picks avec reasoning
    """
    import html as _h
    home = match.get("home", {}); away = match.get("away", {})
    tournament = match.get("tournament", "?")
    surface    = match.get("surface", "?")
    tour       = match.get("tour", "ATP")
    start_iso  = match.get("start_iso") or ""
    card_id    = f"tennis-card-{idx}"
    surf_color = {"Clay":"#c2410c","Hard":"#1d4ed8","Grass":"#15803d"}.get(surface, "#64748b")
    surf_emoji = {"Clay":"🟧","Hard":"🟦","Grass":"🟩"}.get(surface, "🎾")
    tour_emoji = "♂️" if tour == "ATP" else "♀️"

    when_label = ""
    if start_iso:
        try:
            from datetime import datetime as _dt, timezone as _tz, timedelta as _td
            try:
                from zoneinfo import ZoneInfo as _ZI
                paris = _ZI("Europe/Paris")
            except ImportError:
                paris = _tz(_td(hours=2))
            dt = _dt.fromisoformat(start_iso.replace("Z","+00:00")).astimezone(paris)
            now_p = _dt.now(paris)
            days_diff = (dt.date() - now_p.date()).days
            hhmm = dt.strftime("%H:%M")
            if days_diff == 0:
                when_label = f"Aujourd'hui {hhmm}"
            elif days_diff == 1:
                when_label = f"Demain {hhmm}"
            elif days_diff == -1:
                when_label = f"Hier {hhmm}"
            else:
                when_label = dt.strftime("%d/%m %H:%M")
        except Exception:
            pass

    h_name = home.get("name","?"); a_name = away.get("name","?")
    h_rank = home.get("rank"); a_rank = away.get("rank")
    h_odd  = home.get("best_odd") or home.get("consensus_odd")
    a_odd  = away.get("best_odd") or away.get("consensus_odd")
    h_rk_str = f"#{h_rank}" if h_rank else ""
    a_rk_str = f"#{a_rank}" if a_rank else ""
    h_odd_str = f"@{h_odd:.2f}" if h_odd else ""
    a_odd_str = f"@{a_odd:.2f}" if a_odd else ""

    picks = match.get("picks") or []
    # Picks tries par confidence DESC (deja fait dans engine mais on s'assure)
    picks = sorted(picks, key=lambda p: p.get("confidence", 0) or 0, reverse=True)
    best_pick = picks[0] if picks else None
    best_chip = ""
    if best_pick:
        c = best_pick.get("confidence", 0)
        clr = "#22c55e" if c >= 65 else ("#84cc16" if c >= 55 else "#f59e0b")
        kind = best_pick.get("kind","")
        kind_ic = {"tennis_winner":"🏆","tennis_total_games":"📊","tennis_set_score":"🎯"}.get(kind,"📌")
        best_chip = (
            f'<span style="display:inline-flex;align-items:center;gap:5px;'
            f'background:{clr}1f;color:{clr};border:1px solid {clr}66;'
            f'padding:3px 9px;border-radius:999px;font-size:11px;font-weight:700">'
            f'{kind_ic} {c}%</span>'
        )
        if len(picks) > 1:
            best_chip += f'<span style="color:#475569;font-size:11px;font-weight:600;margin-left:6px">+{len(picks)-1}</span>'

    # ── BODY : stats detaillees + tous les picks ────────────────────────────
    def _player_full(side):
        rank   = side.get("rank")
        rk_str = f"#{rank}" if rank else "—"
        odd    = side.get("best_odd") or side.get("consensus_odd")
        odd_str= f"@{odd:.2f}" if odd else ""
        l10_w  = side.get("l10_w", 0); l10_l = side.get("l10_l", 0); l10_n = side.get("l10_n", 0)
        l10    = f"{l10_w}-{l10_l}" if l10_n else "—"
        sw     = side.get("surface_w", 0); sl = side.get("surface_l", 0); sn = side.get("surface_n", 0)
        surf   = f"{sw}-{sl}" if sn else "—"
        gf     = side.get("avg_games_for"); ga = side.get("avg_games_against")
        games  = f"{gf:.1f}/{ga:.1f}" if (gf is not None and ga is not None) else "—"
        return (
            f'<div style="flex:1;min-width:0">'
            f'<div style="display:flex;align-items:center;gap:6px;margin-bottom:4px">'
            f'<span style="font-size:13px;font-weight:700;color:#f1f5f9;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">{side.get("name","?")}</span>'
            f'<span style="font-size:10.5px;color:#64748b;font-weight:600">{rk_str}</span>'
            f'{("<span style=\"font-size:11.5px;color:#34d399;font-weight:700;margin-left:auto\">"+odd_str+"</span>") if odd_str else ""}'
            f'</div>'
            f'<div style="display:grid;grid-template-columns:auto auto auto;gap:4px 10px;font-size:10.5px;color:#94a3b8">'
            f'<div><span style="color:#64748b">L10</span> <b style="color:#cbd5e1">{l10}</b></div>'
            f'<div><span style="color:#64748b">{surface}</span> <b style="color:#cbd5e1">{surf}</b></div>'
            f'<div><span style="color:#64748b">Jeux</span> <b style="color:#cbd5e1">{games}</b></div>'
            f'</div>'
            f'</div>'
        )

    h2h = match.get("h2h") or {}
    h2h_str = ""
    if h2h.get("total", 0) > 0:
        h2h_str = f'<div style="font-size:10.5px;color:#64748b;margin-top:6px">🤝 H2H : {h_name} {h2h.get("home_wins",0)} — {h2h.get("away_wins",0)} {a_name}</div>'

    picks_html = ""
    for p in picks:
        conf = p.get("confidence", 0)
        conf_color = "#22c55e" if conf >= 65 else ("#84cc16" if conf >= 55 else "#f59e0b")
        value = p.get("value")
        value_chip = ""
        if value:
            ic, lbl, clr = value
            value_chip = f'<span style="background:{clr}20;color:{clr};padding:2px 7px;border-radius:999px;font-size:10px;font-weight:700;margin-left:5px">{ic} {lbl}</span>'
        cote_min = p.get("cote_min")
        cote_str = f"@{cote_min:.2f}" if cote_min else ""
        reasoning = (p.get("reasoning") or "").replace("\n", "<br>")
        # Bouton Add bankroll universel (winner / total_games / set_score)
        kind = p.get("kind", "")
        if kind == "tennis_winner":
            payload_player = home.get("name") if p.get("selection") == "home" else away.get("name")
            payload_direction = "over"
            payload_line = None
        elif kind == "tennis_total_games":
            payload_player = f"{h_name} vs {a_name}"
            payload_direction = p.get("direction")
            payload_line = p.get("line")
        elif kind == "tennis_set_score":
            payload_player = f"{h_name} vs {a_name}"
            payload_direction = "over"
            payload_line = None
        else:
            payload_player = f"{h_name} vs {a_name}"
            payload_direction = "over"
            payload_line = None
        add_payload = {
            "sport":     "tennis",
            "prop":      kind.upper(),
            "label":     p.get("label", ""),
            "direction": payload_direction,
            "line":      payload_line,
            "real_cote": p.get("real_cote") or p.get("cote_min"),
            "player":    payload_player,
            "matchup":   f"{h_name} vs {a_name}",
            "home":      h_name,
            "away":      a_name,
            "match_id":  f"tennis_{match.get('event_id','')}",
            "kind":      kind,
        }
        # JSON-escape pour attribut HTML : remplace " par &quot; (safe dans data-attr)
        payload_attr = json.dumps(add_payload).replace('"', '&quot;')
        add_btn = (
            f'<button onclick="event.stopPropagation();addAnyPick(JSON.parse(this.dataset.p))" '
            f'data-p="{payload_attr}" '
            f'style="background:#1e3a8a;color:#bfdbfe;border:1px solid #3b82f6;border-radius:6px;'
            f'padding:3px 9px;font-size:10.5px;font-weight:700;cursor:pointer;margin-left:6px">'
            f'📌 Add</button>'
        )
        picks_html += (
            f'<div style="background:#0c1525;border-left:3px solid {conf_color};padding:8px 10px;border-radius:6px;margin-top:6px">'
            f'<div style="display:flex;align-items:center;flex-wrap:wrap;gap:3px">'
            f'<span style="font-size:12.5px;font-weight:700;color:#f1f5f9">{p.get("label","")}</span>'
            f'<span style="font-size:11.5px;font-weight:700;color:{conf_color};margin-left:6px">{conf}%</span>'
            f'{value_chip}'
            f'<span style="font-size:10.5px;color:#64748b;margin-left:auto">{cote_str}</span>'
            f'{add_btn}'
            f'</div>'
            f'<div style="font-size:11px;color:#94a3b8;margin-top:5px;line-height:1.5">{reasoning}</div>'
            f'</div>'
        )
    if not picks_html:
        picks_html = '<div style="font-size:11px;color:#475569;margin-top:8px;font-style:italic">Pas de pick intéressant détecté pour ce match.</div>'

    # ── HEADER (toujours visible, cliquable) ────────────────────────────────
    header_top = (
        f'<div style="display:flex;align-items:center;gap:6px;font-size:10.5px;color:#64748b;font-weight:600;text-transform:uppercase;letter-spacing:0.5px;margin-bottom:6px;flex-wrap:wrap">'
        f'<span>{tour_emoji} {tour} · {tournament}</span>'
        f'<span style="color:{surf_color}">{surf_emoji} {surface}</span>'
        f'<span style="margin-left:auto;color:#94a3b8;text-transform:none;font-weight:700">⏰ {when_label}</span>'
        f'</div>'
    )
    header_players = (
        f'<div style="display:flex;align-items:center;gap:8px;font-size:12.5px;flex-wrap:wrap">'
        f'<span style="font-weight:700;color:#f1f5f9">{h_name}</span>'
        f'<span style="color:#64748b;font-size:10.5px">{h_rk_str}</span>'
        f'<span style="color:#34d399;font-weight:700">{h_odd_str}</span>'
        f'<span style="color:#475569;font-size:11px;margin:0 4px">vs</span>'
        f'<span style="font-weight:700;color:#f1f5f9">{a_name}</span>'
        f'<span style="color:#64748b;font-size:10.5px">{a_rk_str}</span>'
        f'<span style="color:#34d399;font-weight:700">{a_odd_str}</span>'
        f'<span style="margin-left:auto;display:inline-flex;align-items:center;gap:4px">{best_chip}<span class="tennis-chevron" style="color:#475569;font-size:14px;transition:transform 0.2s">▾</span></span>'
        f'</div>'
    )
    header = (
        f'<div onclick="toggleTennisMatch(\'{card_id}\')" style="cursor:pointer;padding:10px 12px">'
        f'{header_top}{header_players}'
        f'</div>'
    )
    body = (
        f'<div class="tennis-match-body" id="{card_id}-body" style="display:none;padding:0 12px 12px 12px;border-top:1px solid #1e293b">'
        f'<div style="display:flex;gap:14px;align-items:flex-start;padding:10px 0 8px">'
        f'  {_player_full(home)}'
        f'  <div style="color:#475569;font-size:12px;font-weight:700;padding-top:2px">vs</div>'
        f'  {_player_full(away)}'
        f'</div>'
        f'{h2h_str}'
        f'{picks_html}'
        f'</div>'
    )

    start_ts_attr = ""
    try:
        ts_int = int(match.get("start_ts") or 0)
        if ts_int > 0:
            start_ts_attr = f' data-start-ts="{ts_int}"'
    except Exception:
        pass
    return (
        f'<div class="tennis-match-card" id="{card_id}"{start_ts_attr} '
        f'style="background:#0f172a;border:1px solid #1e293b;border-radius:10px;overflow:hidden">'
        f'{header}{body}'
        f'</div>'
    )


def build_tennis_history(history_data):
    """Historique tennis : picks resolus groupes par date avec WR + ROI."""
    if not history_data or not history_data.get("picks"):
        return ('<div style="text-align:center;padding:40px;color:#64748b;font-size:13px;line-height:1.7">'
                'Aucun historique tennis pour le moment.<br>'
                '<span style="color:#475569">Les picks se resolvent automatiquement après FT '
                'via The Odds API /scores.</span></div>')
    picks = history_data["picks"]
    from collections import defaultdict
    by_date = defaultdict(list)
    for p in picks:
        by_date[p.get("date", "?")].append(p)
    dates = sorted(by_date.keys(), reverse=True)

    resolved = [p for p in picks if p.get("result") in ("WIN", "LOSS", "PUSH")]
    wins   = sum(1 for p in resolved if p.get("result") == "WIN")
    losses = sum(1 for p in resolved if p.get("result") == "LOSS")
    pushes = sum(1 for p in resolved if p.get("result") == "PUSH")
    wr = (wins / (wins + losses) * 100) if (wins + losses) else 0
    roi_units, n_betted = 0.0, 0
    for p in resolved:
        cote = p.get("real_cote") or p.get("cote_min") or 0
        if not cote or p.get("result") == "PUSH": continue
        n_betted += 1
        roi_units += (cote - 1) if p.get("result") == "WIN" else -1
    roi_pct = (roi_units / n_betted * 100) if n_betted else 0

    wr_color  = "#22c55e" if wr >= 55 else ("#84cc16" if wr >= 50 else "#ef4444")
    roi_color = "#22c55e" if roi_units > 0 else "#ef4444"
    summary = (
        f'<div style="background:#0f172a;border-radius:12px;padding:16px 20px;margin-bottom:18px;'
        f'display:grid;grid-template-columns:repeat(auto-fit,minmax(120px,1fr));gap:14px">'
        f'<div><div style="color:#64748b;font-size:11px;text-transform:uppercase;letter-spacing:1px">Résolus</div>'
        f'<div style="color:#f1f5f9;font-size:22px;font-weight:800">{wins+losses+pushes}</div></div>'
        f'<div><div style="color:#64748b;font-size:11px;text-transform:uppercase;letter-spacing:1px">Win Rate</div>'
        f'<div style="color:{wr_color};font-size:22px;font-weight:800">{wr:.0f}%</div>'
        f'<div style="color:#64748b;font-size:10px">{wins}W · {losses}L · {pushes}P</div></div>'
        f'<div><div style="color:#64748b;font-size:11px;text-transform:uppercase;letter-spacing:1px">ROI (1u/pick)</div>'
        f'<div style="color:{roi_color};font-size:22px;font-weight:800">{roi_units:+.2f}u</div>'
        f'<div style="color:#64748b;font-size:10px">{roi_pct:+.1f}% sur {n_betted}</div></div>'
        f'</div>'
    )

    sections = []
    for d in dates:
        d_picks = by_date[d]
        d_picks.sort(key=lambda p: -(p.get("confidence") or 0))
        try:
            from datetime import datetime as _dt
            d_label = _dt.strptime(d, "%Y-%m-%d").strftime("%d/%m/%Y")
        except Exception:
            d_label = d
        rows = []
        for p in d_picks:
            r = p.get("result", "?")
            res_color = {"WIN":"#22c55e","LOSS":"#ef4444","PUSH":"#94a3b8"}.get(r, "#64748b")
            res_icon  = {"WIN":"✓","LOSS":"✗","PUSH":"="}.get(r, "?")
            kind_ic   = {"tennis_winner":"🏆","tennis_total_games":"📊","tennis_set_score":"🎯"}.get(p.get("kind",""), "📌")
            cote_str  = f"@{p.get('real_cote') or p.get('cote_min'):.2f}" if (p.get('real_cote') or p.get('cote_min')) else ""
            actual = p.get("actual")
            actual_str = ""
            if actual is not None:
                if isinstance(actual, (int, float)):
                    actual_str = f" → {actual}"
                else:
                    actual_str = f" → {actual}"
            tour_emoji = "♂️" if p.get("tour") == "ATP" else "♀️"
            surface = p.get("surface", "")
            surf_ic = {"Clay":"🟧","Hard":"🟦","Grass":"🟩"}.get(surface, "🎾")
            rows.append(
                f'<div style="display:grid;grid-template-columns:auto 1fr auto auto auto;gap:10px;'
                f'align-items:center;padding:9px 12px;border-radius:8px;background:#0a1628;border-left:3px solid {res_color}">'
                f'<span style="font-size:14px">{kind_ic}</span>'
                f'<div style="min-width:0">'
                f'  <div style="color:#f1f5f9;font-size:13px;font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">{p.get("label","?")}{actual_str}</div>'
                f'  <div style="color:#64748b;font-size:11px;margin-top:2px">{tour_emoji} {p.get("tour","")} · {surf_ic} {surface} · {p.get("matchup","")}</div>'
                f'</div>'
                f'<span style="color:#cbd5e1;font-size:12px;font-weight:600">{p.get("confidence","?")}%</span>'
                f'<span style="color:#7dd3fc;font-size:11px;font-variant-numeric:tabular-nums">{cote_str}</span>'
                f'<span style="background:{res_color};color:#0a1628;font-weight:800;border-radius:14px;padding:3px 10px;font-size:12px">{res_icon} {r}</span>'
                f'</div>'
            )
        sections.append(
            f'<details data-date="{d}" open style="margin-bottom:14px;background:#0f172a;border-radius:12px;padding:12px 16px">'
            f'<summary style="color:#cbd5e1;font-size:14px;font-weight:700;cursor:pointer;padding:4px 0">'
            f'📅 {d_label} · {len(d_picks)} pick(s)</summary>'
            f'<div style="display:flex;flex-direction:column;gap:6px;margin-top:10px">{"".join(rows)}</div>'
            f'</details>'
        )

    # Filtre de date (reutilise le pattern foot/NBA)
    date_filter = _build_date_filter(dates, "tennishist-container", sport_label="tennis")
    return (
        summary
        + date_filter
        + '<div id="tennishist-container">'
        +   "".join(sections)
        + '</div>'
    )


def build_nba_section(nba_picks_data):
    """Section NBA : liste des matchs avec leurs picks, tries par confiance."""
    if not nba_picks_data:
        return (
            '<div style="text-align:center;padding:40px;color:#64748b">'
            'Aucun match NBA aujourd\'hui ou demain.'
            '</div>'
        )
    # Tri des picks PAR confidence DESC dans chaque match + tri des matchs
    # par max(confidence) DESC. Le pick le + propice remonte en haut.
    def _max_conf(_g):
        _m = 0
        for _p in (_g.get("home_picks", []) or []) + (_g.get("away_picks", []) or []):
            _c = _p.get("confidence", 0) or 0
            if _c > _m: _m = _c
        return _m
    sorted_games = sorted(
        nba_picks_data.items(),
        key=lambda kv: _max_conf(kv[1]),
        reverse=True,
    )
    for _gid, _g in sorted_games:
        if _g.get("home_picks"):
            _g["home_picks"].sort(key=lambda p: (p.get("confidence", 0) or 0), reverse=True)
        if _g.get("away_picks"):
            _g["away_picks"].sort(key=lambda p: (p.get("confidence", 0) or 0), reverse=True)
    cards = ""
    n_picks = 0
    for gid, game in sorted_games:
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

    # Filtre defensive : si data/matches.json a des matchs deja termines
    # (parce que le scraper a fait sys.exit(1) sur un jour sans match et a
    # conserve le fichier de la veille), on les exclut de l'affichage Football.
    # Un match est considere termine 4h apres son start_ts (kickoff + ~2h jeu
    # + buffer pour les prolongations / tirs au but).
    import time as _time_filter
    _now_ts = _time_filter.time()
    _MATCH_DURATION_S = 4 * 3600   # 4h apres kickoff = match termine
    _kept = []
    _dropped = 0
    for _m in (matches or []):
        _ts = _m.get("start_ts")
        if _ts is None:
            _kept.append(_m); continue  # garde si pas d'heure (rare)
        if _ts + _MATCH_DURATION_S < _now_ts:
            _dropped += 1
            continue   # match termine -> on l'exclut
        _kept.append(_m)
    if _dropped:
        print(f"  [filter] {_dropped} match(s) deja termine(s) exclu(s) de la section Football (data/matches.json stale)")
    matches = _kept

    # Tri par confiance MAX du match (le pick le plus propice au top).
    # Pour chaque match, on calcule max(confidence) across team_picks + fun_picks
    # + player_picks. Les matchs avec un pick haute confiance remontent.
    def _match_max_confidence(_m):
        _max = 0
        for _p in (_m.get("picks") or []):
            _c = _p.get("confidence", 0) or 0
            if _c > _max: _max = _c
        for _p in (_m.get("fun_picks") or []):
            _c = _p.get("confidence", 0) or 0
            if _c > _max: _max = _c
        for _player in (_m.get("home_players") or []) + (_m.get("away_players") or []):
            for _p in (_player.get("picks") or []):
                _c = _p.get("confidence", 0) or 0
                if _c > _max: _max = _c
        return _max
    matches.sort(key=_match_max_confidence, reverse=True)
    # Aussi : pour chaque match, tri INTERNE des picks par confidence DESC
    for _m in matches:
        if _m.get("picks"):
            _m["picks"].sort(key=lambda p: (p.get("confidence", 0) or 0), reverse=True)
        if _m.get("fun_picks"):
            _m["fun_picks"].sort(key=lambda p: (p.get("confidence", 0) or 0), reverse=True)
        for _player in (_m.get("home_players") or []) + (_m.get("away_players") or []):
            if _player.get("picks"):
                _player["picks"].sort(key=lambda p: (p.get("confidence", 0) or 0), reverse=True)

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
    # Section Tennis (v1 minimal : matchs ATP/WTA + picks filtres par edge)
    try:
        with open("data/tennis_picks.json", encoding="utf-8") as _tf:
            import json as _jt
            tennis_picks_data = _jt.load(_tf)
    except Exception:
        tennis_picks_data = {"matches": []}
    tennis_section  = build_tennis_section(tennis_picks_data)
    # tennis_hist_html sera defini plus bas apres lecture de tennis_picks_history.json
    nba_analyse_html = build_nba_analyse_section(nba_picks, nba_player_stats, nba_odds)
    # Charge nba_box_scores ICI (avant la section historique) car build_nba_history_section
    # en a besoin. Sera reutilise plus bas pour l'embed window.NBA_BOX_SCORES.
    nba_box_scores = {}
    try:
        with open("data/nba_box_scores.json", encoding="utf-8") as _bf_early:
            import json as _json_early
            nba_box_scores = _json_early.load(_bf_early)
    except Exception:
        nba_box_scores = {}
    nba_history_section_html = build_nba_history_section(nba_history, nba_box_scores, nba_player_stats, nba_odds)

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

    # Box scores : deja charges plus haut (avant build_nba_history_section)
    nba_box_scores_json = _json.dumps(nba_box_scores, ensure_ascii=False).replace("</", "<\\/")
    try:
        with open("data/nba_box_scores_min.json", "w", encoding="utf-8") as _bf:
            _json.dump(nba_box_scores, _bf, ensure_ascii=False)
    except Exception as _e:
        print(f"  ⚠️ Impossible d'ecrire nba_box_scores_min.json: {_e}")

    # ── Embed FOOT history slim (pour auto-resolve user picks foot cote client) ─
    # Keys necessaires : match_id + label/market + result + actual (pour resoudre
    # un user pick a ligne differente : ex algo "Plus de 14.5 tirs", user "Plus de
    # 11.5 tirs" -> on parse actual et on recompute le result).
    foot_hist_min = []
    for p in (foot_history or {}).get("picks", []):
        if p.get("result") not in ("WIN", "LOSS", "PUSH"): continue
        foot_hist_min.append({
            "match_id":  str(p.get("match_id", "")),
            "type":      p.get("type", ""),
            "label":     p.get("label", ""),
            "direction": p.get("direction"),
            "result":    p.get("result"),
            "actual":    p.get("actual"),       # ex "10 tirs (Rayo Vallecano)" / "1-0"
            "date":      p.get("date"),
            "player":    p.get("player"),       # pour player picks (Buteur/etc)
            "matchup":   p.get("matchup", ""),
        })
    foot_history_json = _json.dumps(foot_hist_min, ensure_ascii=False).replace("</", "<\\/")

    # ── Embed TENNIS results + history pour auto-resolve user picks tennis ────
    tennis_results = {}
    try:
        with open("data/tennis_results.json", encoding="utf-8") as _tf:
            tennis_results = _json.load(_tf)
    except Exception:
        tennis_results = {}
    tennis_results_json = _json.dumps(tennis_results, ensure_ascii=False).replace("</", "<\\/")
    tennis_history_data = {"picks": []}
    try:
        with open("data/tennis_picks_history.json", encoding="utf-8") as _thf:
            tennis_history_data = _json.load(_thf)
    except Exception:
        tennis_history_data = {"picks": []}
    tennis_hist_html = build_tennis_history(tennis_history_data)

    # NBA recent games pour le wizard d'ajout de pari NBA.
    # Filtre base sur les HEURES ECOULEES depuis la fin du match :
    #   - >= 30 min apres fin (le temps que les box scores se stabilisent)
    #   - <= 60 h (2.5 jours, on ne montre pas les matchs trop vieux)
    # On envoie aussi end_iso au client pour qu'il calcule "Il y a Xh" / "Hier" /
    # "Avant-hier" precisemment selon son fuseau horaire local.
    from datetime import datetime as _dt2, timezone as _tz2, timedelta as _td2
    try:
        from zoneinfo import ZoneInfo as _ZI2
        _PARIS_TZ = _ZI2("Europe/Paris")
    except ImportError:
        _PARIS_TZ = _tz2(_td2(hours=2))   # fallback CEST approx
    _now_utc = _dt2.now(_tz2.utc)
    nba_recent = {}
    for _p in (nba_history or {}).get("picks", []):
        _gid = str(_p.get("game_id") or "")
        if not _gid or _gid in nba_recent: continue
        # Heure de fin du match : resolved_at (UTC server time) en priorite
        _ra = _p.get("resolved_at") or ""
        _end_utc = None
        if _ra:
            try:
                _end_utc = _dt2.strptime(_ra, "%Y-%m-%d %H:%M").replace(tzinfo=_tz2.utc)
            except Exception:
                pass
        if _end_utc is None:
            # Fallback : date du match (assume fin de journee 23:00 UTC)
            _ds = (_p.get("date") or "")[:10]
            if not _ds: continue
            try:
                _end_utc = _dt2.fromisoformat(_ds).replace(tzinfo=_tz2.utc, hour=23, minute=0)
            except Exception:
                continue
        _hours_ago = (_now_utc - _end_utc).total_seconds() / 3600.0
        # Fenetre 2h-48h : match fini il y a au moins 2h (resolver stabilise) et
        # au plus 48h (= cette nuit + nuit precedente, pas plus).
        if _hours_ago < 2.0 or _hours_ago > 48.0:
            continue
        _end_paris = _end_utc.astimezone(_PARIS_TZ)
        nba_recent[_gid] = {
            "date":      _end_paris.date().isoformat(),
            "end_iso":   _end_utc.isoformat(),    # ISO UTC, le client calcule "Il y a Xh"
            "matchup":   _p.get("matchup") or "",
            "players":   nba_box_scores.get(_gid, {}) or {},
        }
    # Drop games sans box score (impossible auto-detect dessus)
    nba_recent = {_k: _v for _k, _v in nba_recent.items() if _v.get("players")}
    nba_recent_json = _json.dumps(nba_recent, ensure_ascii=False).replace("</", "<\\/")

    total_t = sum(len(m["picks"]) for m in matches)
    total_p = sum(len(m.get("home_players",[])) + len(m.get("away_players",[])) for m in matches)

    return f'''<!DOCTYPE html>
<html lang="fr">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<meta http-equiv="Cache-Control" content="no-cache, must-revalidate">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<title>Sports Picks — {now}</title>

<!-- Anti-flash gate : si on etait deja connecte la derniere fois, on cache
     la gate AVANT meme le rendu (sync, depuis localStorage). Firebase
     confirmera l'auth state en async ensuite. -->
<script>
  if(localStorage.getItem('bk_was_signed_in') === '1'){{
    document.documentElement.classList.add('bk-prelogged');
  }}
</script>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Geist:wght@400;500;600;700;800&display=swap" rel="stylesheet">

<!-- Firebase (Auth + Firestore) pour la sync multi-device des picks bankroll -->
<script type="module">
  import {{ initializeApp }} from "https://www.gstatic.com/firebasejs/10.13.0/firebase-app.js";
  import {{ getAuth, createUserWithEmailAndPassword, signInWithEmailAndPassword,
           signOut, onAuthStateChanged, sendPasswordResetEmail }} from "https://www.gstatic.com/firebasejs/10.13.0/firebase-auth.js";
  import {{ getFirestore, doc, setDoc, getDoc }} from "https://www.gstatic.com/firebasejs/10.13.0/firebase-firestore.js";

  const firebaseConfig = {{
    apiKey: "AIzaSyAdZ3GvbYVapF0gqyh5O7mN1xC0sTT1yJo",
    authDomain: "sportspick-c6955.firebaseapp.com",
    projectId: "sportspick-c6955",
    storageBucket: "sportspick-c6955.firebasestorage.app",
    messagingSenderId: "1027893046012",
    appId: "1:1027893046012:web:135ce6b516fcfba5a34579"
  }};

  const app  = initializeApp(firebaseConfig);
  const auth = getAuth(app);
  const db   = getFirestore(app);

  // Expose au reste du code (modules + scripts classiques)
  window._fb = {{
    auth, db,
    signUp:  (email, pw) => createUserWithEmailAndPassword(auth, email, pw),
    signIn:  (email, pw) => signInWithEmailAndPassword(auth, email, pw),
    signOut: () => signOut(auth),
    resetPw: (email) => sendPasswordResetEmail(auth, email),
    setUserDoc: (uid, data) => setDoc(doc(db, "users", uid, "state", "main"), data, {{merge: true}}),
    getUserDoc: (uid) => getDoc(doc(db, "users", uid, "state", "main")),
  }};
  window._fbReady = true;

  onAuthStateChanged(auth, (user) => {{
    window._fbUser = user;
    if(typeof _bkOnAuthChanged === 'function') {{
      _bkOnAuthChanged(user);
    }}
  }});
</script>
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

  /* Sub-toggle (Picks: Football/NBA, Historique: Foot/NBA) */
  .sub-tg-bar{{
    display:flex;gap:6px;margin-bottom:14px;background:#0f172a;border:1px solid #1e293b;
    border-radius:10px;padding:5px;width:fit-content;max-width:100%;flex-wrap:wrap;
  }}
  .sub-tg{{
    background:transparent;color:#64748b;border:none;border-radius:7px;
    padding:8px 18px;font-size:13px;font-weight:600;cursor:pointer;transition:all .15s;
    letter-spacing:0.2px;
  }}
  .sub-tg:hover{{color:#cbd5e1;background:#1e293b}}
  .sub-tg.active{{background:#1e293b;color:#f1f5f9;box-shadow:inset 0 0 0 1px #334155}}

  /* Tennis grid : 2 colonnes independantes (column-count = flow par colonne,
     pas par ligne, donc expand a gauche n'affecte pas la colonne de droite). */
  .tennis-grid{{
    column-count:2;column-gap:10px;
  }}
  .tennis-grid > .tennis-match-card{{
    display:inline-block;width:100%;margin-bottom:10px;
    break-inside:avoid;-webkit-column-break-inside:avoid;page-break-inside:avoid;
  }}
  .tennis-match-card:hover{{border-color:#334155 !important;}}
  .tennis-match-card.open .tennis-chevron{{transform:rotate(180deg);}}
  @media (max-width: 900px){{
    .tennis-grid{{column-count:1 !important;}}
  }}

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

  /* NBA match : header cliquable + body deroulable (style identique au Foot) */
  .nba-match-header {{
    cursor: pointer; padding: 16px 20px;
    background: #0d1b2e; border-bottom: 1px solid #1e293b;
    transition: background .15s; user-select: none;
  }}
  .nba-match-header:hover {{ background: #122538; }}
  .nba-match-body {{ display: none; }}
  .nba-match-body.show {{ display: block; }}
  .nba-match-header.expanded #nba-arrow,
  .nba-match-header.expanded [id^="nba-arrow-"] {{ transform: rotate(180deg); }}

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
  .bk-chart-stage {{ width: 100%; position: relative; }}
  .bk-chart-line {{ stroke-dasharray: 3000; stroke-dashoffset: 3000; animation: bk-draw 1100ms cubic-bezier(.4,.0,.2,1) forwards; }}
  @keyframes bk-draw {{ to {{ stroke-dashoffset: 0; }} }}
  /* Labels axes Y et X overlay sur le SVG stretche */
  .bk-chart-ylabel {{
    position: absolute; left: 0; width: 46px; text-align: right;
    color: var(--bk-text-muted); font-size: 11px; font-weight: 500;
    line-height: 1; transform: translateY(-50%); pointer-events: none;
    font-variant-numeric: tabular-nums;
  }}
  .bk-chart-xlabel {{
    position: absolute; bottom: 4px; color: var(--bk-text-muted);
    font-size: 10.5px; font-weight: 500; white-space: nowrap;
    pointer-events: none; font-variant-numeric: tabular-nums;
  }}

  /* Prop / market breakdown */
  .bk-prop-row {{ display: grid; grid-template-columns: 130px 1fr 60px 70px 80px; gap: 12px; align-items: center; padding: 10px 4px; }}
  .bk-prop-row + .bk-prop-row {{ border-top: 1px solid var(--bk-hairline); }}
  .bk-prop-name {{ display: flex; align-items: center; gap: 8px; color: var(--bk-text); font-weight: 600; font-size: 13px; }}
  .bk-prop-bar {{ height: 6px; background: var(--bk-text-soft); border-radius: 999px; overflow: hidden; }}
  .bk-prop-bar > div {{ height: 100%; border-radius: 999px; }}
  .bk-prop-wr {{ text-align: right; font-weight: 700; font-size: 12.5px; font-variant-numeric: tabular-nums; }}
  .bk-prop-cote {{ text-align: right; font-weight: 600; font-size: 12px; font-variant-numeric: tabular-nums; color: var(--bk-text-muted); }}
  .bk-prop-profit {{ text-align: right; font-weight: 700; font-size: 13px; font-variant-numeric: tabular-nums; }}

  /* Analyse aggregee (par tipster / par date) */
  .bk-analyse-wrap {{ display: flex; flex-direction: column; gap: 14px; margin-bottom: 18px; }}
  .bk-analyse-row {{ display: grid; grid-template-columns: 1.4fr 70px 90px 70px 70px 90px; gap: 10px; align-items: center; padding: 10px 6px; font-size: 13px; }}
  .bk-analyse-row + .bk-analyse-row {{ border-top: 1px solid var(--bk-hairline); }}
  .bk-analyse-hd {{ font-size: 10.5px; color: var(--bk-text-muted); font-weight: 600; text-transform: uppercase; letter-spacing: 0.4px; padding: 6px 6px 8px; }}
  .bk-analyse-name {{ display: flex; align-items: center; gap: 8px; color: var(--bk-text); font-weight: 600; min-width: 0; }}
  .bk-analyse-name > span:last-child {{ overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }}
  .bk-analyse-num {{ text-align: right; font-variant-numeric: tabular-nums; }}
  .bk-card-close {{ background: transparent; border: 1px solid var(--bk-hairline); color: var(--bk-text-muted); width: 28px; height: 28px; border-radius: 8px; cursor: pointer; font-size: 13px; line-height: 1; display: inline-flex; align-items: center; justify-content: center; }}
  .bk-card-close:hover {{ background: rgba(255,255,255,0.05); color: var(--bk-text); }}

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

  /* Show more / show less */
  #sport-userpicks .bk-more-rows {{ display: none; }}
  #sport-userpicks .bk-more-rows.open {{ display: block; }}
  #sport-userpicks .bk-more-btn {{
    width: 100%; padding: 10px 14px; margin-top: 8px;
    background: var(--bk-surface); border: 1px solid var(--bk-border-strong);
    color: var(--bk-text-muted); font-weight: 600; font-size: 12.5px;
    border-radius: 12px; cursor: pointer; transition: all 150ms ease;
    font-family: inherit; display: flex; align-items: center; justify-content: center; gap: 6px;
  }}
  #sport-userpicks .bk-more-btn:hover {{ background: var(--bk-text-soft); color: var(--bk-text); }}
  #sport-userpicks .bk-filter-section {{ margin-bottom: 6px; }}
  #sport-userpicks .bk-filter-section + .bk-filter-section {{ margin-top: 4px; }}

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

  /* Tipster dropdown (autocomplete a partir des tipsters deja saisis) */
  #bk-modal-root .bk-tipster-wrap {{ position: relative; }}
  #bk-modal-root .bk-tipster-wrap .bk-m-input {{ padding-right: 38px; }}
  #bk-modal-root .bk-tipster-chevron {{
    position: absolute; right: 6px; top: 50%; transform: translateY(-50%);
    background: transparent; border: none; color: #8B8D98; cursor: pointer;
    padding: 6px 8px; border-radius: 8px; font-size: 11px; font-family: inherit;
    transition: all 150ms;
  }}
  #bk-modal-root .bk-tipster-chevron:hover {{ color: #fff; background: rgba(255,255,255,0.05); }}
  #bk-modal-root .bk-tipster-dd {{
    position: absolute; left: 0; right: 0; top: calc(100% + 6px);
    background: #14161B; border: 1px solid rgba(255,255,255,0.10);
    border-radius: 12px; max-height: 220px; overflow-y: auto;
    z-index: 10; display: none;
    box-shadow: 0 10px 28px rgba(0,0,0,0.5);
  }}
  #bk-modal-root .bk-tipster-dd.open {{ display: block; }}
  #bk-modal-root .bk-tipster-item {{
    padding: 10px 14px; cursor: pointer; color: #fff;
    font-size: 14px; display: flex; align-items: center; gap: 10px;
    transition: background 120ms;
  }}
  #bk-modal-root .bk-tipster-item:hover, #bk-modal-root .bk-tipster-item.focus {{ background: rgba(255,255,255,0.05); }}
  #bk-modal-root .bk-tipster-item .name {{ flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }}
  #bk-modal-root .bk-tipster-item .count {{
    padding: 1px 7px; border-radius: 999px; background: rgba(168,85,247,0.12); color: #c4b5fd;
    font-size: 10px; font-weight: 700;
  }}
  #bk-modal-root .bk-tipster-item .del {{
    background: transparent; border: none; color: #F87171; opacity: 0.4;
    font-size: 14px; cursor: pointer; padding: 4px 8px; border-radius: 6px;
    font-family: inherit;
  }}
  #bk-modal-root .bk-tipster-item .del:hover {{ opacity: 1; background: rgba(248,113,113,0.10); }}
  #bk-modal-root .bk-tipster-empty {{ padding: 14px; color: #8B8D98; font-size: 13px; text-align: center; }}

  /* ── Account bar (synchronisation Firebase) ────────────────────────── */
  #sport-userpicks .bk-account-bar {{
    display: flex; align-items: center; gap: 12px;
    padding: 10px 14px; border-radius: 14px;
    background: var(--bk-surface); border: 1px solid var(--bk-border);
    margin-bottom: 14px;
  }}
  #sport-userpicks .bk-account-bar.signed-in {{
    background: linear-gradient(135deg, rgba(52,211,153,0.08), rgba(52,211,153,0.02));
    border-color: rgba(52,211,153,0.22);
  }}
  #sport-userpicks .bk-account-bar .bk-acct-status {{ flex: 1; color: var(--bk-text-muted); font-size: 13px; min-width: 0; }}
  #sport-userpicks .bk-account-bar .bk-acct-status b {{ color: var(--bk-text); font-weight: 600; }}
  #sport-userpicks .bk-acct-sync {{ color: var(--bk-text-muted); font-size: 11.5px; font-weight: 500; display: inline-flex; align-items: center; gap: 5px; }}
  #sport-userpicks .bk-acct-sync.ok {{ color: #34D399; }}
  #sport-userpicks .bk-acct-sync.pending {{ color: #FBBF24; }}
  #sport-userpicks .bk-acct-sync.err {{ color: #F87171; }}

  /* Auth modal (reutilise #bk-modal-root) */
  #bk-modal-root .bk-auth-tabs {{
    display: flex; gap: 4px; padding: 4px; background: #14161B;
    border-radius: 12px; margin-bottom: 18px; border: 1px solid rgba(255,255,255,0.05);
  }}
  #bk-modal-root .bk-auth-tab {{
    flex: 1; padding: 9px 12px; border-radius: 9px; border: none;
    background: transparent; color: #8B8D98; font-weight: 600; font-size: 13.5px;
    cursor: pointer; font-family: inherit; transition: all 180ms;
  }}
  #bk-modal-root .bk-auth-tab.active {{ background: #1F232C; color: #fff; }}
  #bk-modal-root .bk-auth-err {{
    margin-top: 10px; padding: 9px 12px; border-radius: 10px;
    background: rgba(248,113,113,0.10); border: 1px solid rgba(248,113,113,0.22);
    color: #fca5a5; font-size: 12.5px; min-height: 18px; display: none;
  }}
  #bk-modal-root .bk-auth-err.show {{ display: block; }}
  #bk-modal-root .bk-auth-hint {{ color: #64748b; font-size: 11.5px; margin-top: 8px; line-height: 1.5; }}
  #bk-modal-root .bk-auth-link {{
    background: transparent; border: none; color: #34D399;
    font-size: 12.5px; font-weight: 600; cursor: pointer; padding: 6px 0;
    font-family: inherit; text-decoration: underline;
  }}

  /* ── Menu compte global (haut droit, sticky sur tout le site) ─────── */
  #global-account-menu {{
    position: fixed; top: 14px; right: 14px; z-index: 9000;
    font-family: 'Geist', system-ui, sans-serif;
  }}
  #global-account-menu.unauth {{ display: none; }}
  #global-account-menu .acct-btn {{
    display: flex; align-items: center; gap: 8px;
    background: rgba(20,22,27,0.92); backdrop-filter: blur(12px) saturate(180%);
    -webkit-backdrop-filter: blur(12px) saturate(180%);
    border: 1px solid rgba(255,255,255,0.08);
    border-radius: 999px; padding: 5px 14px 5px 5px;
    color: #f1f5f9; font-size: 13px; font-weight: 600;
    cursor: pointer; transition: all 180ms;
    font-family: inherit;
    box-shadow: 0 4px 14px rgba(0,0,0,0.3);
  }}
  #global-account-menu .acct-btn:hover {{ background: rgba(31,35,44,0.95); border-color: rgba(255,255,255,0.18); }}
  #global-account-menu .acct-btn .avatar {{
    width: 30px; height: 30px; border-radius: 999px;
    background: linear-gradient(135deg, #34D399, #0EA5E9);
    display: flex; align-items: center; justify-content: center;
    font-size: 14px; color: #0A0B0F; font-weight: 800; flex-shrink: 0;
  }}
  #global-account-menu .acct-btn .email {{
    max-width: 200px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
  }}
  #global-account-menu .acct-btn .caret {{
    color: #8B8D98; font-size: 9px; margin-left: -2px;
    transition: transform 200ms;
  }}
  #global-account-menu.open .acct-btn .caret {{ transform: rotate(180deg); }}
  #global-account-menu .acct-dropdown {{
    position: absolute; top: calc(100% + 8px); right: 0;
    width: 290px;
    background: #14161B; border: 1px solid rgba(255,255,255,0.08);
    border-radius: 16px; padding: 10px;
    box-shadow: 0 20px 50px rgba(0,0,0,0.55);
    display: none;
    font-family: inherit;
  }}
  #global-account-menu.open .acct-dropdown {{ display: block; }}
  #global-account-menu .acct-dd-header {{
    display: flex; align-items: center; gap: 12px;
    padding: 10px 8px 14px; border-bottom: 1px solid rgba(255,255,255,0.05); margin-bottom: 6px;
  }}
  #global-account-menu .acct-dd-header .avatar-big {{
    width: 44px; height: 44px; border-radius: 999px;
    background: linear-gradient(135deg, #34D399, #0EA5E9);
    display: flex; align-items: center; justify-content: center;
    font-size: 20px; color: #0A0B0F; font-weight: 800; flex-shrink: 0;
  }}
  #global-account-menu .acct-dd-info {{ flex: 1; min-width: 0; }}
  #global-account-menu .acct-dd-email {{
    color: #fff; font-weight: 600; font-size: 14px;
    overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
  }}
  #global-account-menu .acct-dd-sync {{
    color: #34D399; font-size: 11.5px; font-weight: 500; margin-top: 3px;
  }}
  #global-account-menu .acct-dd-item {{
    display: flex; align-items: center; gap: 10px;
    width: 100%; padding: 11px 12px; border-radius: 10px;
    background: transparent; border: none; color: #f1f5f9;
    font-size: 13.5px; font-weight: 500; cursor: pointer; text-align: left;
    font-family: inherit; transition: background 150ms;
  }}
  #global-account-menu .acct-dd-item:hover {{ background: rgba(255,255,255,0.05); }}
  #global-account-menu .acct-dd-item .ic {{ font-size: 16px; width: 22px; text-align: center; }}
  #global-account-menu .acct-dd-item.danger {{ color: #fca5a5; }}
  #global-account-menu .acct-dd-item.danger:hover {{ background: rgba(248,113,113,0.10); color: #fff; }}
  /* Mobile : avatar only, email + caret caches */
  @media (max-width: 720px) {{
    #global-account-menu {{ top: 10px; right: 10px; }}
    #global-account-menu .acct-btn .email {{ display: none; }}
    #global-account-menu .acct-btn .caret {{ display: none; }}
    #global-account-menu .acct-btn {{ padding: 5px; }}
    #global-account-menu .acct-dropdown {{ width: min(290px, calc(100vw - 20px)); }}
  }}

  /* ── Auth Gate (page connexion forcee avant acces au site) ──────────── */
  /* Anti-flash : si on etait deja connecte, on cache la gate avant meme
     que Firebase ne confirme l'auth state */
  html.bk-prelogged #auth-gate {{ display: none; }}
  #auth-gate {{
    position: fixed; inset: 0; z-index: 99999;
    background:
      radial-gradient(circle at 20% 0%,  rgba(52,211,153,0.07), transparent 55%),
      radial-gradient(circle at 80% 100%, rgba(251,191,36,0.05), transparent 55%),
      #08090D;
    display: flex; align-items: center; justify-content: center;
    padding: 20px; overflow-y: auto;
    font-family: 'Geist', -apple-system, BlinkMacSystemFont, system-ui, sans-serif;
  }}
  #auth-gate.hidden {{ display: none; }}
  #auth-gate .gate-card {{
    width: min(440px, 92vw);
    background: #14161B;
    border: 1px solid rgba(255,255,255,0.06);
    border-radius: 22px;
    padding: 32px 26px 28px;
    box-shadow: 0 24px 60px rgba(0,0,0,0.6);
    color: #fff;
  }}
  #auth-gate .gate-logo {{
    text-align: center; margin-bottom: 24px;
  }}
  #auth-gate .gate-logo .emoji {{ font-size: 38px; line-height: 1; margin-bottom: 8px; }}
  #auth-gate .gate-logo h1 {{
    font-size: 26px; font-weight: 800; letter-spacing: -0.8px;
    margin: 0 0 6px; color: #fff;
  }}
  #auth-gate .gate-logo p {{
    color: #8B8D98; font-size: 13px; margin: 0; line-height: 1.5;
  }}
  #auth-gate .bk-auth-tabs {{
    display: flex; gap: 4px; padding: 4px; background: #0F1115;
    border-radius: 12px; margin-bottom: 20px; border: 1px solid rgba(255,255,255,0.05);
  }}
  #auth-gate .bk-auth-tab {{
    flex: 1; padding: 10px 12px; border-radius: 9px; border: none;
    background: transparent; color: #8B8D98; font-weight: 600; font-size: 13.5px;
    cursor: pointer; font-family: inherit; transition: all 180ms;
  }}
  #auth-gate .bk-auth-tab.active {{ background: #1F232C; color: #fff; }}
  #auth-gate label.bk-m-label {{
    color: #8B8D98; font-size: 11.5px; font-weight: 600;
    text-transform: uppercase; letter-spacing: 0.4px; margin-bottom: 6px; display: block;
  }}
  #auth-gate input.bk-m-input {{
    width: 100%; padding: 12px 14px; border-radius: 14px;
    background: #0F1115; border: 1px solid rgba(255,255,255,0.07);
    color: #fff; font-size: 15px; font-weight: 500; outline: none;
    font-family: inherit; box-sizing: border-box;
  }}
  #auth-gate input.bk-m-input:focus {{ border-color: rgba(52,211,153,0.5); box-shadow: 0 0 0 3px rgba(52,211,153,0.12); }}
  #auth-gate .bk-m-cta {{
    width: 100%; padding: 14px; border-radius: 14px; margin-top: 18px;
    background: linear-gradient(180deg, #34D399, #10B981);
    border: none; color: #06120E; font-weight: 700; font-size: 15.5px;
    cursor: pointer; transition: all 200ms; box-shadow: 0 6px 20px rgba(52,211,153,0.35);
    font-family: inherit;
  }}
  #auth-gate .bk-m-cta:disabled {{ background: #1F232C; color: #8B8D98; cursor: default; box-shadow: none; }}
  #auth-gate .bk-auth-err {{
    margin-top: 12px; padding: 10px 13px; border-radius: 10px;
    background: rgba(248,113,113,0.10); border: 1px solid rgba(248,113,113,0.22);
    color: #fca5a5; font-size: 12.5px; min-height: 18px; display: none;
  }}
  #auth-gate .bk-auth-err.show {{ display: block; }}
  #auth-gate .bk-auth-link {{
    background: transparent; border: none; color: #34D399;
    font-size: 12.5px; font-weight: 600; cursor: pointer; padding: 8px;
    font-family: inherit; text-decoration: underline; display: block;
    margin: 14px auto 0; text-align: center; width: 100%;
  }}
  #auth-gate .gate-signup-hint {{
    margin-top: 10px; padding: 10px 13px; border-radius: 10px;
    background: rgba(52,211,153,0.08); border: 1px solid rgba(52,211,153,0.20);
    color: #86efac; font-size: 12px; line-height: 1.5; display: none;
  }}
  #auth-gate .gate-loading {{
    text-align: center; color: #8B8D98; font-size: 13px; margin-top: 16px;
  }}
  #auth-gate.ready .gate-loading {{ display: none; }}
  /* Tant que Firebase n'est pas charge, on grise le formulaire */
  #auth-gate form {{ opacity: 0.45; pointer-events: none; transition: opacity 200ms ease; }}
  #auth-gate.ready form {{ opacity: 1; pointer-events: auto; }}
  /* Mobile : padding plus serre */
  @media (max-width: 460px) {{
    #auth-gate .gate-card {{ padding: 24px 18px 22px; border-radius: 18px; }}
    #auth-gate .gate-logo h1 {{ font-size: 22px; }}
    #auth-gate .gate-logo .emoji {{ font-size: 32px; }}
  }}

  /* ── Bouton flottant refresh (mobile + PWA standalone) ─────────────── */
  .bk-refresh-fab {{
    position: fixed; bottom: 18px; right: 18px; z-index: 10000;
    width: 50px; height: 50px; border-radius: 999px;
    background: linear-gradient(135deg, #34D399, #10B981);
    border: none; color: #06120E; font-size: 22px; cursor: pointer;
    box-shadow: 0 6px 22px rgba(52,211,153,0.5), 0 0 0 4px rgba(8,9,13,0.85);
    display: none; align-items: center; justify-content: center;
    transition: transform 180ms ease, box-shadow 180ms ease;
    -webkit-tap-highlight-color: transparent;
    font-family: inherit;
  }}
  .bk-refresh-fab:active {{ transform: scale(0.92); }}
  /* Visible : (1) mode standalone PWA  (2) ecran mobile */
  @media (display-mode: standalone) {{ .bk-refresh-fab {{ display: flex; }} }}
  @media (max-width: 720px) {{ .bk-refresh-fab {{ display: flex; }} }}
  /* Sur iOS PWA standalone, on remonte le bouton au-dessus de la safe area */
  @supports (padding: max(0px)) {{
    .bk-refresh-fab {{ bottom: max(18px, calc(env(safe-area-inset-bottom, 0px) + 8px)); }}
  }}

  /* ── Mobile (smartphone, max-width 720px) ────────────────────────────── */
  /* IMPORTANT : ne s'applique QU'aux ecrans <= 720px. L'interface PC reste intacte. */
  @media (max-width: 720px) {{
    body {{ padding: 10px 10px !important; }}
    .container {{ max-width: 100% !important; }}
    h1 {{ font-size: 22px !important; }}
    .meta {{ font-size: 11.5px !important; }}
    .legend {{ font-size: 11px !important; padding: 9px 12px !important; line-height: 1.7 !important; }}

    /* Sport switcher : chips plus compacts, defilent horizontalement si overflow */
    .sport-btn {{ padding: 9px 13px !important; font-size: 13px !important; }}
    /* Footer */
    footer {{ font-size: 10px !important; }}

    /* ── Foot match cards ── */
    .match-header {{ padding: 14px 14px 12px !important; }}
    .match-body {{ padding: 0 10px 14px !important; }}
    .picks-btn {{ padding: 8px 12px !important; font-size: 12.5px !important; }}

    /* ── NBA picks (home/away 2-col -> stack) ── */
    #sport-nba .nba-match-header {{ padding: 14px 14px !important; }}
    #sport-nba .nba-match-body > div[style*="grid-template-columns"] {{
      grid-template-columns: 1fr !important;
      gap: 1px !important;
    }}

    /* ── Analyse NBA : grilles 2-col en stack ── */
    #sport-analyse > div[style*="grid-template-columns:1fr 1fr"],
    #sport-analyse div[style*="grid-template-columns:1fr 1fr"] {{
      grid-template-columns: 1fr !important;
      gap: 10px !important;
    }}
    #sport-analyse .player-analyse {{ padding: 9px 11px !important; }}

    /* ── Bankroll : version mobile compactée ── */
    #sport-userpicks {{
      padding: 16px 14px 30px !important;
      border-radius: 18px !important;
      margin-top: 0 !important;
    }}
    #sport-userpicks .bk-hero {{ gap: 14px !important; padding: 0 !important; }}
    #sport-userpicks .bk-hero-left {{ width: 100%; }}
    #sport-userpicks .bk-hero-amount {{ font-size: 40px !important; letter-spacing: -1.5px !important; }}
    #sport-userpicks .bk-hero-amount .bk-cur {{ font-size: 22px !important; }}
    #sport-userpicks .bk-hero-right {{
      flex-direction: row; align-items: center; gap: 10px;
      width: 100%; justify-content: space-between; flex-wrap: wrap;
    }}
    #sport-userpicks .bk-stats {{ grid-template-columns: 1fr 1fr !important; gap: 8px !important; }}
    #sport-userpicks .bk-stats .bk-stat:nth-child(3) {{ grid-column: 1 / -1; }}
    #sport-userpicks .bk-stat {{ padding: 11px 13px !important; border-radius: 14px !important; }}
    #sport-userpicks .bk-stat-value {{ font-size: 19px !important; }}
    #sport-userpicks .bk-stat-label {{ font-size: 10.5px !important; }}
    #sport-userpicks .bk-card {{ padding: 13px !important; border-radius: 16px !important; }}
    #sport-userpicks .bk-card-title {{ font-size: 14.5px !important; }}
    #sport-userpicks .bk-eyebrow {{ font-size: 10.5px !important; }}
    /* 2-col grid Paris en cours / Historique -> stack (deja a 1024 mais on confirme) */
    #sport-userpicks .bk-cols {{ grid-template-columns: 1fr !important; gap: 14px !important; }}
    /* Rows */
    #sport-userpicks .bk-row {{ padding: 10px 8px !important; gap: 10px !important; }}
    #sport-userpicks .bk-row-icon {{ width: 34px !important; height: 34px !important; font-size: 17px !important; border-radius: 10px !important; }}
    #sport-userpicks .bk-row-title {{ font-size: 13px !important; white-space: normal !important; max-width: none !important; }}
    #sport-userpicks .bk-row-sub {{ font-size: 11.5px !important; gap: 6px !important; }}
    #sport-userpicks .bk-row-side {{ gap: 4px !important; }}
    #sport-userpicks .bk-row-amt {{ font-size: 13px !important; }}
    #sport-userpicks .bk-row-actions {{ gap: 3px !important; }}
    #sport-userpicks .bk-mini-btn {{ padding: 4px 7px !important; font-size: 11px !important; }}
    /* Filter chips */
    #sport-userpicks .bk-filter-chip {{ padding: 6px 10px !important; font-size: 11.5px !important; }}
    #sport-userpicks .bk-segment button {{ padding: 5px 10px !important; font-size: 11px !important; }}
    /* Chart : hauteur reduite + padding gauche reduit pour les labels Y */
    #sport-userpicks .bk-chart-stage {{ height: 170px !important; }}
    #sport-userpicks .bk-chart-ylabel {{ font-size: 10px !important; width: 38px !important; }}
    #sport-userpicks .bk-chart-xlabel {{ font-size: 10px !important; }}
    /* Prop breakdown : colonnes plus etroites */
    #sport-userpicks .bk-prop-row {{
      grid-template-columns: 92px 1fr 50px 56px 64px !important;
      gap: 7px !important; font-size: 12px !important;
    }}
    #sport-userpicks .bk-prop-name {{ font-size: 12px !important; }}
    #sport-userpicks .bk-prop-cote {{ font-size: 11px !important; }}
    /* Analyse rows : compact mobile */
    #sport-userpicks .bk-analyse-row {{
      grid-template-columns: 1fr 50px 78px 56px 56px 72px !important;
      gap: 6px !important; font-size: 11.5px !important;
    }}
    #sport-userpicks .bk-analyse-hd {{ font-size: 9.5px !important; }}

    /* ── Modal Nouveau Pari ── */
    #bk-modal-root .bk-modal-card {{ width: 96vw !important; max-height: 92vh !important; border-radius: 18px !important; }}
    #bk-modal-root .bk-m-hd {{ padding: 12px 16px !important; }}
    #bk-modal-root .bk-m-title {{ font-size: 15px !important; }}
    #bk-modal-root .bk-m-body {{ padding: 16px 14px 8px !important; }}
    #bk-modal-root .bk-m-ft {{ padding: 12px 14px 16px !important; }}
    #bk-modal-root .bk-m-context {{ padding: 11px !important; gap: 10px !important; }}
    #bk-modal-root .bk-m-icon {{ width: 36px !important; height: 36px !important; font-size: 18px !important; }}
    #bk-modal-root .bk-m-ctx-title {{ font-size: 14px !important; }}
    #bk-modal-root .bk-m-input {{ padding: 11px 12px !important; font-size: 14px !important; }}
    #bk-modal-root .bk-m-pot-big {{ font-size: 20px !important; }}
    #bk-modal-root .bk-m-cta {{ padding: 13px !important; font-size: 15px !important; }}

    /* ── Card "Blessures et suspensions" en Analyse NBA -> stack ── */
    #sport-analyse div[style*="grid-template-columns:1fr 1fr"] {{
      grid-template-columns: 1fr !important;
    }}

    /* ── Account bar (sync Firebase) : wrap pour que le bouton reste visible ── */
    #sport-userpicks .bk-account-bar {{
      flex-wrap: wrap !important;
      gap: 10px !important;
      padding: 11px 13px !important;
    }}
    #sport-userpicks .bk-account-bar .bk-acct-status {{
      width: 100%;
      font-size: 12.5px !important;
      line-height: 1.45 !important;
    }}
    #sport-userpicks .bk-account-bar > button {{
      width: 100% !important;
      padding: 11px 14px !important;
      font-size: 13px !important;
      text-align: center;
    }}
    /* La sync-badge reste a sa place (petit chip) */
    #sport-userpicks .bk-acct-sync {{ flex: 0 0 auto; }}
  }}
  /* Tres petits ecrans (< 420px) : un cran de plus */
  @media (max-width: 420px) {{
    body {{ padding: 8px 8px !important; }}
    h1 {{ font-size: 19px !important; }}
    .sport-btn {{ padding: 8px 11px !important; font-size: 12px !important; }}
    #sport-userpicks .bk-hero-amount {{ font-size: 34px !important; }}
    #sport-userpicks .bk-stats {{ grid-template-columns: 1fr !important; }}
    #sport-userpicks .bk-stats .bk-stat:nth-child(3) {{ grid-column: auto; }}
    #sport-userpicks .bk-prop-row {{
      grid-template-columns: 76px 1fr 42px 48px 56px !important;
      font-size: 11px !important;
      gap: 6px !important;
    }}
    #sport-userpicks .bk-prop-cote {{ font-size: 10.5px !important; }}
    /* Analyse rows : encore plus compact */
    #sport-userpicks .bk-analyse-row {{
      grid-template-columns: 1fr 38px 64px 48px 48px 60px !important;
      gap: 4px !important; font-size: 10.5px !important;
    }}
  }}
</style>
</head>
<body>

<!-- Gate : page de connexion plein-ecran, obligatoire avant d'acceder au site.
     Visible par defaut (avant que Firebase ne confirme l'auth state).
     Cachee par _bkUpdateGate(user) une fois que l'user est connecte. -->
<div id="auth-gate">
  <div class="gate-card">
    <div class="gate-logo">
      <div class="emoji">🎯</div>
      <h1>Sports Picks</h1>
      <p>Connecte-toi pour accéder à tes paris et ton historique</p>
    </div>
    <div class="bk-auth-tabs">
      <button type="button" class="bk-auth-tab active" id="gate-tab-signin" onclick="_bkGateSwitchTab('signin')">Se connecter</button>
      <button type="button" class="bk-auth-tab" id="gate-tab-signup" onclick="_bkGateSwitchTab('signup')">Créer un compte</button>
    </div>
    <form id="bk-gate-form" autocomplete="on" onsubmit="event.preventDefault(); _bkGateSubmit();">
      <label class="bk-m-label" for="bk-gate-email">Email</label>
      <input class="bk-m-input" type="email" id="bk-gate-email" autocomplete="email" required placeholder="ton@email.com">
      <label class="bk-m-label" for="bk-gate-pw" style="margin-top:14px">Mot de passe</label>
      <input class="bk-m-input" type="password" id="bk-gate-pw" autocomplete="current-password" required minlength="6" placeholder="ton mot de passe">
      <div class="gate-signup-hint" id="gate-signup-hint">
        🛡️ Tes données (picks, bankroll, tipsters) sont stockées sur ton compte et accessibles depuis n'importe quel appareil.
      </div>
      <div class="bk-auth-err" id="bk-gate-err"></div>
      <button type="submit" class="bk-m-cta" id="bk-gate-submit">Se connecter</button>
      <button type="button" class="bk-auth-link" onclick="_bkGateForgotPw()">Mot de passe oublié ?</button>
    </form>
    <div class="gate-loading">⏳ Chargement de l'authentification...</div>
  </div>
</div>

<!-- Menu compte global (haut droit, visible sur toutes les sections) -->
<div id="global-account-menu" class="unauth">
  <button type="button" class="acct-btn" onclick="_bkToggleAccountMenu(event)" aria-label="Menu compte">
    <span class="avatar" id="acct-avatar">?</span>
    <span class="email" id="acct-email"></span>
    <span class="caret">▾</span>
  </button>
  <div class="acct-dropdown">
    <div class="acct-dd-header">
      <div class="avatar-big" id="acct-dd-avatar">?</div>
      <div class="acct-dd-info">
        <div class="acct-dd-email" id="acct-dd-email"></div>
        <div class="acct-dd-sync" id="acct-dd-sync">✓ Synchronisé</div>
      </div>
    </div>
    <button type="button" class="acct-dd-item" onclick="showSport('userpicks');_bkCloseAccountMenu()">
      <span class="ic">💰</span><span>Ma bankroll</span>
    </button>
    <button type="button" class="acct-dd-item danger" onclick="_bkSignOut();_bkCloseAccountMenu()">
      <span class="ic">🚪</span><span>Se déconnecter</span>
    </button>
  </div>
</div>

<div class="container">
  <h1>🎯 Sports Picks</h1>
  <div class="meta">Généré le {now} · ⚽ {len(matches)} matchs foot · 🏀 {len(nba_picks)} matchs NBA</div>
  <!-- Toast non-bloquant : signalement des nouveaux picks -->
  <div id="new-picks-toast" style="display:none;position:fixed;top:18px;right:18px;z-index:9999;background:linear-gradient(90deg,#fb923c,#f97316);color:#0a1628;border-radius:10px;padding:10px 16px;font-weight:700;font-size:14px;box-shadow:0 4px 20px rgba(251,146,60,0.5);transition:opacity 0.5s ease-out;max-width:300px">
    🆕 <span id="new-picks-count">0</span> nouveau(x) pick(s)
    <div style="font-size:11px;font-weight:500;color:#1e293b;margin-top:2px">Repere les badges 🆕 sur les cartes</div>
  </div>
  <!-- Sport switcher (4 menus : Picks | Analyse NBA | Bankroll | Historique) -->
  <div style="display:flex;gap:10px;margin-bottom:14px;flex-wrap:wrap">
    <button class="sport-btn active" onclick="showSport('picks')"     id="sport-btn-picks">📌 Picks</button>
    <button class="sport-btn" onclick="showSport('analyse')"          id="sport-btn-analyse">🔍 Analyse NBA</button>
    <button class="sport-btn" onclick="showSport('userpicks')"        id="sport-btn-userpicks">💰 Bankroll <span id="userpicks-count" style="background:rgba(255,255,255,0.2);border-radius:10px;padding:1px 7px;font-size:11px;margin-left:4px;display:none">0</span></button>
    <button class="sport-btn" onclick="showSport('history')"          id="sport-btn-history">🏆 Historique</button>
  </div>

  <!-- Sub-toggle Picks (Football <-> Basketball NBA <-> Tennis) -->
  <div id="picks-subtoggle" class="sub-tg-bar">
    <button class="sub-tg active" onclick="showSubPicks('football')" id="sub-tg-picks-football">⚽ Football</button>
    <button class="sub-tg"        onclick="showSubPicks('nba')"      id="sub-tg-picks-nba">🏀 Basketball NBA</button>
    <button class="sub-tg"        onclick="showSubPicks('tennis')"   id="sub-tg-picks-tennis">🎾 Tennis</button>
  </div>
  <!-- Sub-toggle Historique (Foot <-> NBA <-> Tennis) -->
  <div id="hist-subtoggle" class="sub-tg-bar" style="display:none">
    <button class="sub-tg active" onclick="showSubHist('foot')"   id="sub-tg-hist-foot">⚽ Historique Foot</button>
    <button class="sub-tg"        onclick="showSubHist('nba')"    id="sub-tg-hist-nba">🏀 Historique NBA</button>
    <button class="sub-tg"        onclick="showSubHist('tennis')" id="sub-tg-hist-tennis">🎾 Historique Tennis</button>
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
    <div style="display:flex;gap:8px;margin-bottom:14px;flex-wrap:wrap">
      <button onclick="collapseAllNba()" style="background:#1e293b;color:#94a3b8;border:1px solid #334155;border-radius:8px;padding:8px 14px;font-size:12px;font-weight:700;cursor:pointer">📕 Tout fermer</button>
      <button onclick="expandAllNba()" style="background:#1e293b;color:#94a3b8;border:1px solid #334155;border-radius:8px;padding:8px 14px;font-size:12px;font-weight:700;cursor:pointer">📖 Tout ouvrir</button>
    </div>
    {nba_section}
  </div>

  <!-- Section Tennis -->
  <div id="sport-tennis" style="display:none">
    <div class="legend">
      <b>🎾 Picks Tennis ATP/WTA</b> — Données Sackmann (rank + L10 + surface + H2H) ·
      Cotes The Odds API · 3 marchés : <b>Vainqueur</b> (edge ≥5%), <b>Total jeux</b> O/U, <b>Score sets exact</b> ·
      Affiche uniquement les matchs avec pick(s) intéressant(s)
    </div>
    {tennis_section}
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
      <button onclick="toggleNbaHistorySection()" id="nba-hist-toggle-btn" style="background:#1c1917;color:#fbbf24;border:1px solid #fbbf24;border-radius:8px;padding:8px 14px;font-size:12px;font-weight:700;cursor:pointer;margin-left:auto">📅 Historique 48h</button>
    </div>
    <!-- Selectors globaux : applique le prop/fenetre choisi a TOUTES les cards joueur en 1 click -->
    <div style="background:#0f172a;border:1px solid #1e293b;border-radius:10px;padding:10px 12px;margin-bottom:14px">
      <div style="display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin-bottom:8px">
        <span style="color:#94a3b8;font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:0.6px;min-width:75px">🌐 Prop pour tous</span>
        <div id="analyse-global-prop" style="display:flex;gap:4px;flex-wrap:wrap;flex:1">
          <button class="tg-global-prop-btn" data-prop="PTS"  onclick="setAnalyseGlobalProp('PTS')"  style="background:#1e293b;color:#94a3b8;border:1px solid #334155;border-radius:6px;padding:5px 11px;font-size:11.5px;font-weight:700;cursor:pointer">PTS</button>
          <button class="tg-global-prop-btn" data-prop="REB"  onclick="setAnalyseGlobalProp('REB')"  style="background:#1e293b;color:#94a3b8;border:1px solid #334155;border-radius:6px;padding:5px 11px;font-size:11.5px;font-weight:700;cursor:pointer">REB</button>
          <button class="tg-global-prop-btn" data-prop="AST"  onclick="setAnalyseGlobalProp('AST')"  style="background:#1e293b;color:#94a3b8;border:1px solid #334155;border-radius:6px;padding:5px 11px;font-size:11.5px;font-weight:700;cursor:pointer">AST</button>
          <button class="tg-global-prop-btn" data-prop="FG3M" onclick="setAnalyseGlobalProp('FG3M')" style="background:#1e293b;color:#94a3b8;border:1px solid #334155;border-radius:6px;padding:5px 11px;font-size:11.5px;font-weight:700;cursor:pointer">3PM</button>
          <button class="tg-global-prop-btn" data-prop="RA"   onclick="setAnalyseGlobalProp('RA')"   style="background:#1e293b;color:#94a3b8;border:1px solid #334155;border-radius:6px;padding:5px 11px;font-size:11.5px;font-weight:700;cursor:pointer">REB+AST</button>
          <button class="tg-global-prop-btn" data-prop="PR"   onclick="setAnalyseGlobalProp('PR')"   style="background:#1e293b;color:#94a3b8;border:1px solid #334155;border-radius:6px;padding:5px 11px;font-size:11.5px;font-weight:700;cursor:pointer">PTS+REB</button>
          <button class="tg-global-prop-btn" data-prop="PA"   onclick="setAnalyseGlobalProp('PA')"   style="background:#1e293b;color:#94a3b8;border:1px solid #334155;border-radius:6px;padding:5px 11px;font-size:11.5px;font-weight:700;cursor:pointer">PTS+AST</button>
          <button class="tg-global-prop-btn" data-prop="PRA"  onclick="setAnalyseGlobalProp('PRA')"  style="background:#1e293b;color:#94a3b8;border:1px solid #334155;border-radius:6px;padding:5px 11px;font-size:11.5px;font-weight:700;cursor:pointer">PRA</button>
        </div>
      </div>
      <div style="display:flex;gap:10px;align-items:center;flex-wrap:wrap">
        <span style="color:#94a3b8;font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:0.6px;min-width:75px">🌐 Fenêtre</span>
        <div id="analyse-global-window" style="display:flex;gap:4px;flex-wrap:wrap;flex:1">
          <button class="tg-global-window-btn" data-window="L5"  onclick="setAnalyseGlobalWindow('L5')"  style="background:#1e293b;color:#94a3b8;border:1px solid #334155;border-radius:6px;padding:5px 11px;font-size:11.5px;font-weight:700;cursor:pointer">L5</button>
          <button class="tg-global-window-btn" data-window="L10" onclick="setAnalyseGlobalWindow('L10')" style="background:#1e293b;color:#94a3b8;border:1px solid #334155;border-radius:6px;padding:5px 11px;font-size:11.5px;font-weight:700;cursor:pointer">L10</button>
          <button class="tg-global-window-btn" data-window="L20" onclick="setAnalyseGlobalWindow('L20')" style="background:#1e293b;color:#94a3b8;border:1px solid #334155;border-radius:6px;padding:5px 11px;font-size:11.5px;font-weight:700;cursor:pointer">L20</button>
        </div>
      </div>
    </div>
    <!-- Section Historique (matchs récents 2h-48h ago) — masquée par défaut -->
    <div id="nba-history-block" style="display:none;margin-bottom:18px">
      <div style="color:#fbbf24;font-size:13px;font-weight:700;margin-bottom:10px;padding:8px 12px;background:rgba(251,191,36,0.06);border-left:3px solid #fbbf24;border-radius:4px;line-height:1.5">
        📅 <b>Historique NBA (48h)</b> — Matchs joués entre 2h et 48h ago. Clique sur un match → ouvre les cards joueur identiques à Analyse → utilise <b style="color:#22c55e">📌 Add OVER</b> ou <b style="color:#ef4444">📌 Add UNDER</b> pour ajouter retroactivement un pari à ta bankroll (le match_date du passé est embarqué, et l'auto-resolve via box scores fera le reste).
      </div>
      {nba_history_section_html}
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

  <!-- Section Historique Tennis -->
  <div id="sport-tennishist" style="display:none">
    <div class="legend">
      <b>🎾 Historique Tennis</b> — Picks tennis résolus automatiquement via The Odds API /scores après FT ·
      <span style="color:#22c55e">✓</span> gagné · <span style="color:#ef4444">✗</span> perdu
    </div>
    {tennis_hist_html}
  </div>
  <footer>⚽ FotMob + api-football · 🏀 stats.nba.com + The Odds API · 🎾 Sackmann + The Odds API · Algorithme + IA · À titre informatif uniquement</footer>
</div>
<script>
window.NBA_HISTORY = {nba_history_json};
window.NBA_BOX_SCORES = {nba_box_scores_json};
window.NBA_RECENT_GAMES = {nba_recent_json};
window.FOOT_HISTORY = {foot_history_json};
window.TENNIS_RESULTS = {tennis_results_json};
</script>
<script>
// ── Navigation : 4 menus principaux + 2 sous-toggles ─────────────────────
// Tabs principaux : picks | analyse | userpicks | history
// Sub-toggles    : picks -> football / nba ; history -> foot / nba
window._currentTab     = window._currentTab     || 'picks';
window._currentSubPick = window._currentSubPick || 'football';
window._currentSubHist = window._currentSubHist || 'foot';

function _hideAllSportSections(){{
  ['football','nba','tennis','analyse','userpicks','foothist','nbahist','tennishist'].forEach(function(s){{
    var el = document.getElementById('sport-'+s);
    if(el) el.style.display = 'none';
  }});
  var pst = document.getElementById('picks-subtoggle'); if(pst) pst.style.display = 'none';
  var hst = document.getElementById('hist-subtoggle');  if(hst) hst.style.display = 'none';
}}

function _setMainBtnActive(tab){{
  document.querySelectorAll('.sport-btn').forEach(function(b){{ b.classList.remove('active'); }});
  var btn = document.getElementById('sport-btn-' + tab);
  if(btn) btn.classList.add('active');
}}

function showSport(sport){{
  // Compat : accepte les anciens noms (football/nba/tennis -> picks ; foothist/nbahist/tennishist -> history)
  if(sport === 'football'){{ window._currentSubPick = 'football'; sport = 'picks'; }}
  else if(sport === 'nba'){{ window._currentSubPick = 'nba';      sport = 'picks'; }}
  else if(sport === 'tennis'){{ window._currentSubPick = 'tennis'; sport = 'picks'; }}
  else if(sport === 'foothist'){{ window._currentSubHist = 'foot'; sport = 'history'; }}
  else if(sport === 'nbahist'){{  window._currentSubHist = 'nba';  sport = 'history'; }}
  else if(sport === 'tennishist'){{ window._currentSubHist = 'tennis'; sport = 'history'; }}

  _hideAllSportSections();
  window._currentTab = sport;

  if(sport === 'picks'){{
    var st = document.getElementById('picks-subtoggle'); if(st) st.style.display = 'flex';
    showSubPicks(window._currentSubPick);
    _setMainBtnActive('picks');
  }} else if(sport === 'history'){{
    var st = document.getElementById('hist-subtoggle');  if(st) st.style.display = 'flex';
    showSubHist(window._currentSubHist);
    _setMainBtnActive('history');
  }} else if(sport === 'analyse'){{
    var el = document.getElementById('sport-analyse'); if(el) el.style.display = 'block';
    _setMainBtnActive('analyse');
  }} else if(sport === 'userpicks'){{
    var el = document.getElementById('sport-userpicks'); if(el) el.style.display = 'block';
    _setMainBtnActive('userpicks');
    renderUserPicks();
  }}
}}

function showSubPicks(which){{
  window._currentSubPick = which;
  var f = document.getElementById('sport-football'); if(f) f.style.display = (which === 'football') ? 'block' : 'none';
  var n = document.getElementById('sport-nba');      if(n) n.style.display = (which === 'nba')      ? 'block' : 'none';
  var t = document.getElementById('sport-tennis');   if(t) t.style.display = (which === 'tennis')   ? 'block' : 'none';
  document.querySelectorAll('#picks-subtoggle .sub-tg').forEach(function(b){{ b.classList.remove('active'); }});
  var btn = document.getElementById('sub-tg-picks-' + which);
  if(btn) btn.classList.add('active');
  // Quand on entre dans la section tennis, on rafraichit pour cacher les matchs
  // qui ont passe l'heure de debut depuis le chargement de la page.
  if(which === 'tennis' && typeof _hideExpiredTennisCards === 'function'){{
    _hideExpiredTennisCards();
  }}
}}

function showSubHist(which){{
  window._currentSubHist = which;
  var f = document.getElementById('sport-foothist');    if(f) f.style.display = (which === 'foot')   ? 'block' : 'none';
  var n = document.getElementById('sport-nbahist');     if(n) n.style.display = (which === 'nba')    ? 'block' : 'none';
  var t = document.getElementById('sport-tennishist'); if(t) t.style.display = (which === 'tennis') ? 'block' : 'none';
  document.querySelectorAll('#hist-subtoggle .sub-tg').forEach(function(b){{ b.classList.remove('active'); }});
  var btn = document.getElementById('sub-tg-hist-' + which);
  if(btn) btn.classList.add('active');
}}

// Add to bankroll - generique pour tous sports/markets (replace l'ancien
// _bkAddTennisPick qui appelait une fonction inexistante).
// Le bouton declenche addAnyPick(payload) ou payload contient :
//   sport, label, prop, direction, line, real_cote, player, matchup, match_id...
// Le payload est dispatche dans _bkOpenForm via onSubmit -> push user_picks.
function addAnyPick(payload){{
  var direction = payload.direction || null;
  _bkOpenForm({{
    direction: direction,
    payload:   payload,
    onSubmit:  function(r){{
      var newLine = (r.line !== '' && r.line != null && !isNaN(parseFloat(r.line)))
                      ? parseFloat(r.line)
                      : (payload.line != null ? parseFloat(payload.line) : null);
      var cote = parseFloat(r.cote);
      var stake = parseFloat(r.stake);
      var sport = (payload.sport || 'other').toLowerCase();
      var isNba = (sport === 'nba');
      var pick;
      if(isNba && payload.player && payload.prop){{
        // NBA-shape : player/prop/direction/line (compat avec _bkRowHtml NBA renderer)
        pick = {{
          id:          'user_' + Date.now() + '_' + Math.random().toString(36).slice(2, 8),
          sport:       'NBA',
          source:      'algo',
          player:      payload.player,
          prop:        payload.prop,
          direction:   direction || 'over',
          line:        newLine,
          cote:        cote,
          stake:       stake,
          tipster:     r.tipster,
          note:        r.note,
          home:        payload.home || '',
          away:        payload.away || '',
          event:       payload.matchup || ((payload.away || '') + ' @ ' + (payload.home || '')),
          game_id:     payload.game_id || payload.match_id || '',
          match_date:  payload.match_date || null,
          median:      payload.median,
          mean:        payload.mean,
          book_line:   payload.book_line,
          book_over:   payload.book_over,
          book_under:  payload.book_under,
          created:     new Date().toISOString(),
          result:      null,
          actual:      null,
        }};
      }} else {{
        // Manual-shape (foot/tennis) : event + market texte libre + line.
        // Si l'user a change la ligne et que le label contient "Plus de X" / "Moins de X",
        // on regenere le market avec la nouvelle ligne pour rester coherent.
        var market = payload.label || ((direction === 'under' ? 'Moins de ' : 'Plus de ') + (newLine != null ? newLine : ''));
        var origLine = payload.line != null ? parseFloat(payload.line) : null;
        if(payload.label && newLine != null && newLine !== origLine){{
          var re = /(Plus de|Moins de)\\s+[\\d.,]+/;
          if(re.test(payload.label)){{
            market = payload.label.replace(re, function(_, prefix){{ return prefix + ' ' + newLine; }});
          }}
        }}
        pick = {{
          id:          'user_' + Date.now() + '_' + Math.random().toString(36).slice(2, 8),
          sport:       (payload.sport || 'other').toUpperCase(),
          source:      'manual',
          event:       payload.matchup || ((payload.away || '') + ' vs ' + (payload.home || '')),
          market:      market,
          line:        newLine,
          direction:   direction,
          cote:        cote,
          stake:       stake,
          tipster:     r.tipster,
          note:        r.note,
          home:        payload.home || '',
          away:        payload.away || '',
          match_id:    payload.match_id || payload.game_id || '',
          match_date:  payload.match_date || null,
          kind:        payload.kind || null,
          label:       market,
          created:     new Date().toISOString(),
          result:      null,
          actual:      null,
        }};
      }}
      var arr = _loadUserPicks();
      arr.push(pick);
      _saveUserPicks(arr);
      if(stake) window._bkLastStake = stake;
      if(r.tipster && r.tipster.trim()){{
        window._bkLastTipster = r.tipster.trim();
        if(typeof _bkAddTipster === 'function') _bkAddTipster(r.tipster.trim());
      }}
      // Toast vert top-right
      var toast = document.createElement('div');
      toast.style.cssText = 'position:fixed;top:20px;right:20px;z-index:99999;background:#16a34a;color:#fff;padding:12px 18px;border-radius:8px;font-weight:700;box-shadow:0 6px 24px rgba(0,0,0,0.4);opacity:0;transition:opacity 0.2s';
      toast.innerHTML = '✓ Pari ajouté à la bankroll';
      document.body.appendChild(toast);
      setTimeout(function(){{ toast.style.opacity = '1'; }}, 10);
      setTimeout(function(){{ toast.style.opacity = '0'; setTimeout(function(){{ toast.remove(); }}, 250); }}, 1800);
    }},
  }});
}}
// Helper : construit le HTML d'un bouton Add a partir d'un payload (JSON-esc).
// Utilise par foot/tennis/nba renderers.
function _addPickBtnHtml(payload, style){{
  var s = style || '';
  var p = JSON.stringify(payload).replace(/'/g, "\\\\'").replace(/"/g, '&quot;');
  return '<button onclick="event.stopPropagation();addAnyPick(JSON.parse(this.dataset.p))" '
    + 'data-p="' + p + '" '
    + 'class="bk-add-btn" '
    + 'style="background:#1e3a8a;color:#bfdbfe;border:1px solid #3b82f6;border-radius:6px;'
    + 'padding:4px 10px;font-size:11px;font-weight:700;cursor:pointer;white-space:nowrap;' + s + '">'
    + '📌 Add</button>';
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

// ── Global prop/window selectors : applique a TOUTES les cards joueur ─────
function setAnalyseGlobalProp(prop){{
  // Met a jour le state visuel des boutons globaux
  document.querySelectorAll('#analyse-global-prop .tg-global-prop-btn').forEach(function(b){{
    var active = b.dataset.prop === prop;
    b.style.background = active ? '#3b82f6' : '#1e293b';
    b.style.color      = active ? '#fff'    : '#94a3b8';
    b.style.borderColor= active ? '#3b82f6' : '#334155';
  }});
  // Applique le prop a chaque card visible (y compris dans Historique 48h)
  document.querySelectorAll('#sport-analyse .player-analyse').forEach(function(c){{
    var btn = c.querySelector('.tg-prop-btn[data-prop="' + prop + '"]');
    if(btn) selectPropChart(btn);
  }});
}}
function setAnalyseGlobalWindow(win){{
  document.querySelectorAll('#analyse-global-window .tg-global-window-btn').forEach(function(b){{
    var active = b.dataset.window === win;
    b.style.background = active ? '#fb923c' : '#1e293b';
    b.style.color      = active ? '#0a1628' : '#94a3b8';
    b.style.borderColor= active ? '#fb923c' : '#334155';
  }});
  document.querySelectorAll('#sport-analyse .player-analyse').forEach(function(c){{
    var btn = c.querySelector('.tg-window-btn[data-window="' + win + '"]');
    if(btn) selectWindow(btn);
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
// Toggle section Historique NBA (matchs joues 2h-48h ago)
function toggleNbaHistorySection(){{
  var block = document.getElementById('nba-history-block');
  var btn = document.getElementById('nba-hist-toggle-btn');
  if(!block) return;
  var opened = block.style.display !== 'none';
  if(opened){{
    block.style.display = 'none';
    if(btn){{ btn.style.background = '#1c1917'; btn.style.color = '#fbbf24'; btn.innerHTML = '📅 Historique 48h'; }}
  }} else {{
    block.style.display = 'block';
    if(btn){{ btn.style.background = '#fbbf24'; btn.style.color = '#0a1628'; btn.innerHTML = '📅 Historique 48h ✕'; }}
    setTimeout(function(){{ block.scrollIntoView({{behavior:'smooth', block:'start'}}); }}, 50);
  }}
}}

// ── Toggle expand/collapse des cartes NBA (comme le Foot) ──
function toggleNbaMatch(gid){{
  var body = document.getElementById('nba-body-' + gid);
  var arrow = document.getElementById('nba-arrow-' + gid);
  if(!body) return;
  var expanded = body.classList.toggle('show');
  if(arrow) arrow.style.transform = expanded ? 'rotate(180deg)' : 'rotate(0deg)';
}}
function collapseAllNba(){{
  document.querySelectorAll('#sport-nba .nba-match-body').forEach(function(b){{ b.classList.remove('show'); }});
  document.querySelectorAll('#sport-nba [id^="nba-arrow-"]').forEach(function(a){{ a.style.transform = 'rotate(0deg)'; }});
}}
// ── Tennis : cache automatiquement les cards dont l'heure de debut est passee ──
// Refresh toutes les 60s + au chargement de la page + au changement de tab.
function _hideExpiredTennisCards(){{
  var nowSec = Math.floor(Date.now() / 1000);
  var cards = document.querySelectorAll('#sport-tennis .tennis-match-card[data-start-ts]');
  var visibleCount = 0;
  cards.forEach(function(c){{
    var ts = parseInt(c.getAttribute('data-start-ts'), 10);
    if(!isNaN(ts) && ts <= nowSec){{
      c.style.display = 'none';
    }} else {{
      // Reaffiche au cas ou (mais ne touche pas les cards sans start_ts qui restent visibles)
      if(c.style.display === 'none') c.style.display = '';
      visibleCount++;
    }}
  }});
  // Cards sans data-start-ts -> visibles par defaut
  var noTs = document.querySelectorAll('#sport-tennis .tennis-match-card:not([data-start-ts])');
  noTs.forEach(function(c){{ if(c.style.display === '') visibleCount++; }});
  // Met a jour le compteur
  var counter = document.getElementById('tennis-visible-count');
  if(counter) counter.textContent = String(visibleCount);
}}
window.addEventListener('DOMContentLoaded', function(){{
  _hideExpiredTennisCards();
  // Refresh toutes les 60s pour cacher les matchs qui passent l'heure pendant
  // que l'user est sur la page (entre 2 cron runs).
  setInterval(_hideExpiredTennisCards, 60000);
}});

// ── Toggle expand/collapse des cartes Tennis ──
function toggleTennisMatch(cardId){{
  var card = document.getElementById(cardId);
  if(!card) return;
  var body = document.getElementById(cardId + '-body');
  if(!body) return;
  var open = body.style.display !== 'none';
  body.style.display = open ? 'none' : 'block';
  card.classList.toggle('open', !open);
}}
function collapseAllTennis(){{
  document.querySelectorAll('#sport-tennis .tennis-match-body').forEach(function(b){{ b.style.display = 'none'; }});
  document.querySelectorAll('#sport-tennis .tennis-match-card').forEach(function(c){{ c.classList.remove('open'); }});
}}
function expandAllTennis(){{
  document.querySelectorAll('#sport-tennis .tennis-match-body').forEach(function(b){{ b.style.display = 'block'; }});
  document.querySelectorAll('#sport-tennis .tennis-match-card').forEach(function(c){{ c.classList.add('open'); }});
}}

function expandAllNba(){{
  document.querySelectorAll('#sport-nba .nba-match-body').forEach(function(b){{ b.classList.add('show'); }});
  document.querySelectorAll('#sport-nba [id^="nba-arrow-"]').forEach(function(a){{ a.style.transform = 'rotate(180deg)'; }});
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
  if(typeof _bkSchedulePush === 'function') _bkSchedulePush();
}}
function _updateUserPicksCount(){{
  var arr = _loadUserPicks();
  var pending = arr.filter(function(p){{ return !p.result || p.result === 'PENDING'; }}).length;
  var badge = document.getElementById('userpicks-count');
  if(badge){{
    badge.textContent = pending;
    badge.style.display = pending > 0 ? 'inline-block' : 'none';
  }}
}}

// ── Store de tipsters (autocomplete dans le formulaire) ─────────────────
var BK_TIPSTERS_KEY = 'bk_tipsters_v1';
function _bkLoadTipstersStore(){{
  try {{ var raw = localStorage.getItem(BK_TIPSTERS_KEY); return raw ? JSON.parse(raw) : []; }}
  catch(e){{ return []; }}
}}
function _bkSaveTipstersStore(arr){{
  try {{ localStorage.setItem(BK_TIPSTERS_KEY, JSON.stringify(arr)); }} catch(e){{}}
  if(typeof _bkSchedulePush === 'function') _bkSchedulePush();
}}
// Liste merge : store explicite + tipsters extraits des picks existants
function _bkAllTipsters(){{
  var store = _bkLoadTipstersStore();
  var picks = _loadUserPicks();
  var counts = {{}};
  picks.forEach(function(p){{
    var t = p.tipster && p.tipster.trim();
    if(!t) return;
    counts[t] = (counts[t] || 0) + 1;
  }});
  // Ajoute store entries (count = 0 si jamais utilise)
  store.forEach(function(t){{ if(t && !(t in counts)) counts[t] = 0; }});
  var list = Object.keys(counts).map(function(t){{ return {{name: t, count: counts[t]}}; }});
  // Tri : count desc, puis alphabetique
  list.sort(function(a, b){{ return (b.count - a.count) || a.name.localeCompare(b.name); }});
  return list;
}}
function _bkAddTipster(name){{
  if(!name) return;
  var store = _bkLoadTipstersStore();
  var norm = name.trim();
  if(!norm) return;
  // dedup case-insensitive
  var exists = store.some(function(t){{ return t.toLowerCase() === norm.toLowerCase(); }});
  if(!exists){{ store.push(norm); _bkSaveTipstersStore(store); }}
}}
function _bkRemoveTipster(name){{
  var store = _bkLoadTipstersStore().filter(function(t){{ return t.toLowerCase() !== name.toLowerCase(); }});
  _bkSaveTipstersStore(store);
}}

// ── Sync Firebase (Auth Email/Password + Firestore) ─────────────────────
// State machine cote client. window._fb est expose par le module Firebase
// charge dans le HEAD ; il fournit signUp/signIn/signOut/setUserDoc/getUserDoc.
window._bkSyncState = window._bkSyncState || 'idle'; // idle | pending | ok | err

function _bkSetSyncState(state){{
  window._bkSyncState = state;
  var labelMap = {{ idle: 'Pas de sync', pending: '⏳ Sync en cours...', ok: '✓ Synchronisé', err: '⚠ Erreur sync' }};
  var colorMap = {{ idle: '#8B8D98', pending: '#FBBF24', ok: '#34D399', err: '#F87171' }};
  // Sync indicator dans le dropdown du menu compte global
  var dd = document.getElementById('acct-dd-sync');
  if(dd){{
    dd.textContent = labelMap[state] || '';
    dd.style.color = colorMap[state] || '#8B8D98';
  }}
  // Compat : badge dans la card bankroll si elle l'affiche encore
  var el = document.getElementById('bk-acct-sync-badge');
  if(el){{
    el.className = 'bk-acct-sync ' + state;
    el.innerHTML = ({{ idle: '', pending: '⏳ Sync...', ok: '✓ Sync', err: '⚠ Erreur sync' }})[state] || '';
  }}
}}

// Push debounce 1.5s : evite de spammer Firestore quand on edite plusieurs picks
var _bkPushTimer = null;
function _bkSchedulePush(){{
  if(!window._fbUser) return;
  if(_bkPushTimer) clearTimeout(_bkPushTimer);
  _bkPushTimer = setTimeout(_bkPushToServer, 1500);
}}

async function _bkPushToServer(){{
  if(!window._fb || !window._fbUser) return;
  _bkSetSyncState('pending');
  try {{
    var data = {{
      picks:    _loadUserPicks(),
      bankroll: parseFloat(localStorage.getItem('user_bankroll_units') || '100'),
      tipsters: _bkLoadTipstersStore(),
      updated_at: new Date().toISOString(),
    }};
    await window._fb.setUserDoc(window._fbUser.uid, data);
    _bkSetSyncState('ok');
  }} catch(e){{
    console.error('[bk push err]', e);
    _bkSetSyncState('err');
  }}
}}

async function _bkPullFromServer(){{
  if(!window._fb || !window._fbUser) return;
  _bkSetSyncState('pending');
  try {{
    var snap = await window._fb.getUserDoc(window._fbUser.uid);
    if(snap.exists()){{
      var data = snap.data() || {{}};
      // Server est source de verite : on REMPLACE le localStorage entierement
      // (evite que les picks d'un user precedent leak vers un autre compte sur
      // le meme browser).
      if(Array.isArray(data.picks)){{
        localStorage.setItem(USERPICKS_KEY, JSON.stringify(data.picks));
      }} else {{
        localStorage.removeItem(USERPICKS_KEY);
      }}
      if(data.bankroll != null && !isNaN(parseFloat(data.bankroll))){{
        localStorage.setItem('user_bankroll_units', String(data.bankroll));
      }} else {{
        localStorage.removeItem('user_bankroll_units');
      }}
      if(Array.isArray(data.tipsters)){{
        localStorage.setItem(BK_TIPSTERS_KEY, JSON.stringify(data.tipsters));
      }} else {{
        localStorage.removeItem(BK_TIPSTERS_KEY);
      }}
    }} else {{
      // Doc serveur inexistant -> 1er login (cas signup avec data locale pre-existante)
      // On push la state locale pour la sauver
      await _bkPushToServer();
    }}
    _bkSetSyncState('ok');
  }} catch(e){{
    console.error('[bk pull err]', e);
    _bkSetSyncState('err');
  }}
}}

// Hook appele par le listener onAuthStateChanged du module Firebase
async function _bkOnAuthChanged(user){{
  if(user){{
    // Marque le flag pour anti-flash au prochain refresh
    try {{ localStorage.setItem('bk_was_signed_in', '1'); }} catch(e){{}}
    await _bkPullFromServer();
    _bkUpdateGate(user);
  }} else {{
    // Session perdue ou pas connecte : on retire le flag et la classe pre-hide
    try {{ localStorage.removeItem('bk_was_signed_in'); }} catch(e){{}}
    document.documentElement.classList.remove('bk-prelogged');
    _bkSetSyncState('idle');
    _bkUpdateGate(null);
  }}
  _bkUpdateAccountMenu(user);
  _updateUserPicksCount();
  var bkTab = document.getElementById('sport-userpicks');
  if(bkTab && bkTab.style.display !== 'none'){{
    if(typeof renderUserPicks === 'function') renderUserPicks();
  }}
}}

async function _bkSignOut(){{
  if(!window._fb) return;
  try {{
    localStorage.removeItem(USERPICKS_KEY);
    localStorage.removeItem('user_bankroll_units');
    localStorage.removeItem(BK_TIPSTERS_KEY);
    localStorage.removeItem('bk_was_signed_in');
  }} catch(e){{}}
  document.documentElement.classList.remove('bk-prelogged');
  await window._fb.signOut();
}}

// ── Menu compte global (haut droit) ──────────────────────────────────────
function _bkToggleAccountMenu(ev){{
  if(ev){{ ev.stopPropagation(); }}
  var menu = document.getElementById('global-account-menu');
  if(menu) menu.classList.toggle('open');
}}
function _bkCloseAccountMenu(){{
  var menu = document.getElementById('global-account-menu');
  if(menu) menu.classList.remove('open');
}}
function _bkUpdateAccountMenu(user){{
  var menu = document.getElementById('global-account-menu');
  if(!menu) return;
  if(!user){{
    menu.classList.add('unauth');
    menu.classList.remove('open');
    return;
  }}
  menu.classList.remove('unauth');
  var email = user.email || '?';
  var initial = (email.charAt(0) || '?').toUpperCase();
  var avatarEl   = document.getElementById('acct-avatar');
  var emailEl    = document.getElementById('acct-email');
  var ddAvatarEl = document.getElementById('acct-dd-avatar');
  var ddEmailEl  = document.getElementById('acct-dd-email');
  if(avatarEl)   avatarEl.textContent = initial;
  if(ddAvatarEl) ddAvatarEl.textContent = initial;
  if(emailEl)    emailEl.textContent = email;
  if(ddEmailEl)  ddEmailEl.textContent = email;
}}
// Ferme le menu si on clique en dehors
document.addEventListener('click', function(e){{
  var menu = document.getElementById('global-account-menu');
  if(!menu || !menu.classList.contains('open')) return;
  if(!menu.contains(e.target)) menu.classList.remove('open');
}});

function _bkAccountBarHtml(){{
  // L'auth est gere par la GATE en pleine page, donc on n'affiche cette barre
  // QUE quand l'user est connecte (info + bouton deconnexion).
  var u = window._fbUser;
  if(!u) return '';
  var syncBadge = '<span id="bk-acct-sync-badge" class="bk-acct-sync ' + (window._bkSyncState || '') + '"></span>';
  var emailEsc = (u.email || '?').replace(/</g, '&lt;');
  return '<div class="bk-account-bar signed-in">'
    + '<span style="font-size:16px">👤</span>'
    + '<div class="bk-acct-status">Connecté : <b>' + emailEsc + '</b></div>'
    + syncBadge
    + '<button class="bk-btn bk-btn-ghost" onclick="_bkSignOut()" style="padding:6px 11px;font-size:12px">Déconnexion</button>'
    + '</div>';
}}

// ── Gate plein-ecran (force la connexion avant d'acceder au site) ────────
function _bkUpdateGate(user){{
  var gate = document.getElementById('auth-gate');
  if(!gate) return;
  gate.classList.add('ready');  // Firebase est charge
  if(user){{
    gate.classList.add('hidden');
  }} else {{
    gate.classList.remove('hidden');
    setTimeout(function(){{ var el = document.getElementById('bk-gate-email'); if(el) el.focus(); }}, 200);
  }}
}}

window._bkGateMode = 'signin';
function _bkGateSwitchTab(mode){{
  window._bkGateMode = mode;
  var s = document.getElementById('gate-tab-signin');
  var u = document.getElementById('gate-tab-signup');
  if(s) s.classList.toggle('active', mode === 'signin');
  if(u) u.classList.toggle('active', mode === 'signup');
  var btn = document.getElementById('bk-gate-submit');
  if(btn) btn.textContent = (mode === 'signup' ? 'Créer le compte' : 'Se connecter');
  var pwInput = document.getElementById('bk-gate-pw');
  if(pwInput){{
    pwInput.autocomplete = (mode === 'signup' ? 'new-password' : 'current-password');
    pwInput.placeholder  = (mode === 'signup' ? 'minimum 6 caractères' : 'ton mot de passe');
  }}
  var hint = document.getElementById('gate-signup-hint');
  if(hint) hint.style.display = (mode === 'signup') ? 'block' : 'none';
  var errEl = document.getElementById('bk-gate-err');
  if(errEl){{ errEl.classList.remove('show'); errEl.textContent = ''; }}
}}

async function _bkGateSubmit(){{
  if(!window._fb){{ alert('Auth pas encore chargée, attends 2 secondes.'); return; }}
  var mode = window._bkGateMode || 'signin';
  var email = (document.getElementById('bk-gate-email').value || '').trim();
  var pw = document.getElementById('bk-gate-pw').value || '';
  var errEl = document.getElementById('bk-gate-err');
  var btn = document.getElementById('bk-gate-submit');
  errEl.classList.remove('show'); errEl.textContent = '';
  if(!email || !pw){{ errEl.textContent = 'Email et mot de passe requis.'; errEl.classList.add('show'); return; }}
  if(mode === 'signup' && pw.length < 6){{ errEl.textContent = 'Mot de passe min 6 caractères.'; errEl.classList.add('show'); return; }}
  btn.disabled = true;
  btn.textContent = (mode === 'signup' ? 'Création...' : 'Connexion...');
  try {{
    if(mode === 'signup') await window._fb.signUp(email, pw);
    else                  await window._fb.signIn(email, pw);
    // La gate sera cachee par _bkOnAuthChanged
  }} catch(err){{
    var msg = (err && err.message) || 'Erreur inconnue';
    if(/auth\\/email-already-in-use/.test(msg))    msg = 'Cet email a déjà un compte. Connecte-toi à la place.';
    else if(/auth\\/invalid-email/.test(msg))     msg = "Email invalide.";
    else if(/auth\\/weak-password/.test(msg))     msg = 'Mot de passe trop court (min 6 caractères).';
    else if(/auth\\/invalid-credential/.test(msg) || /wrong-password|user-not-found/.test(msg)) msg = "Email ou mot de passe incorrect.";
    else if(/auth\\/too-many-requests/.test(msg)) msg = "Trop de tentatives. Réessaie dans quelques minutes.";
    else if(/auth\\/network-request-failed/.test(msg)) msg = "Pas de connexion réseau.";
    errEl.textContent = msg;
    errEl.classList.add('show');
    btn.disabled = false;
    btn.textContent = (mode === 'signup' ? 'Créer le compte' : 'Se connecter');
  }}
}}

async function _bkGateForgotPw(){{
  var email = (document.getElementById('bk-gate-email').value || '').trim();
  if(!email){{ alert('Saisis ton email d\\'abord dans le champ ci-dessus.'); return; }}
  if(!window._fb){{ alert('Auth non chargée.'); return; }}
  try {{
    await window._fb.resetPw(email);
    alert('Email de réinitialisation envoyé à ' + email + '. Vérifie ta boîte mail (et le spam).');
  }} catch(e){{ alert('Erreur : ' + (e.message || e)); }}
}}

function _bkOpenAuthModal(){{
  if(!window._fb){{ alert('Firebase pas encore chargé, réessaie dans 2 secondes.'); return; }}
  var root = _bkEnsureModalRoot();
  window._bkAuthMode = 'signin'; // signin | signup
  function render(){{
    var mode = window._bkAuthMode;
    var html =
      '<div class="bk-modal-bd" onclick="_bkCloseForm()"></div>'
      + '<div class="bk-modal-card" role="dialog" aria-modal="true">'
      +   '<div class="bk-m-hd">'
      +     '<button class="bk-m-cancel" onclick="_bkCloseForm()">Annuler</button>'
      +     '<div class="bk-m-title">' + (mode === 'signup' ? 'Créer un compte' : 'Connexion') + '</div>'
      +     '<div style="width:64px"></div>'
      +   '</div>'
      +   '<div class="bk-m-body">'
      +     '<div class="bk-auth-tabs">'
      +       '<button class="bk-auth-tab ' + (mode === 'signin' ? 'active' : '') + '" onclick="_bkAuthSwitchTab(\\'signin\\')">Se connecter</button>'
      +       '<button class="bk-auth-tab ' + (mode === 'signup' ? 'active' : '') + '" onclick="_bkAuthSwitchTab(\\'signup\\')">Créer un compte</button>'
      +     '</div>'
      +     '<form id="bk-auth-form" autocomplete="on">'
      +       '<div class="bk-m-grp">'
      +         '<label class="bk-m-label">Email</label>'
      +         '<input class="bk-m-input" type="email" id="bk-auth-email" autocomplete="email" required placeholder="ton@email.com">'
      +       '</div>'
      +       '<div class="bk-m-grp" style="margin-top:14px">'
      +         '<label class="bk-m-label">Mot de passe</label>'
      +         '<input class="bk-m-input" type="password" id="bk-auth-pw" autocomplete="' + (mode === 'signup' ? 'new-password' : 'current-password') + '" required minlength="6" placeholder="' + (mode === 'signup' ? 'minimum 6 caractères' : 'ton mot de passe') + '">'
      +       '</div>'
      +       '<div class="bk-auth-err" id="bk-auth-err"></div>'
      +       (mode === 'signin'
        ? '<div style="margin-top:10px;text-align:right"><button type="button" class="bk-auth-link" onclick="_bkAuthForgotPw()">Mot de passe oublié ?</button></div>'
        : '<div class="bk-auth-hint">Tes paris locaux seront automatiquement synchronisés vers ton compte après la création.</div>')
      +     '</form>'
      +   '</div>'
      +   '<div class="bk-m-ft">'
      +     '<button class="bk-m-cta" id="bk-auth-submit" onclick="_bkAuthSubmit()">' + (mode === 'signup' ? 'Créer le compte' : 'Se connecter') + '</button>'
      +   '</div>'
      + '</div>';
    root.innerHTML = html;
    // Soumission par Enter dans le form
    var form = document.getElementById('bk-auth-form');
    if(form) form.addEventListener('submit', function(e){{ e.preventDefault(); _bkAuthSubmit(); }});
    setTimeout(function(){{ var el = document.getElementById('bk-auth-email'); if(el) el.focus(); }}, 100);
  }}
  window._bkAuthRender = render;
  render();
  setTimeout(function(){{ root.classList.add('open'); }}, 10);
  // ESC pour fermer
  window._bkFormEscHandler = function(e){{ if(e.key === 'Escape') _bkCloseForm(); }};
  document.addEventListener('keydown', window._bkFormEscHandler);
}}

function _bkAuthSwitchTab(mode){{
  window._bkAuthMode = mode;
  if(window._bkAuthRender) window._bkAuthRender();
}}

async function _bkAuthSubmit(){{
  var mode = window._bkAuthMode || 'signin';
  var email = (document.getElementById('bk-auth-email').value || '').trim();
  var pw = document.getElementById('bk-auth-pw').value || '';
  var errEl = document.getElementById('bk-auth-err');
  var submitBtn = document.getElementById('bk-auth-submit');
  errEl.classList.remove('show');
  errEl.textContent = '';
  if(!email || !pw){{ errEl.textContent = 'Email et mot de passe requis.'; errEl.classList.add('show'); return; }}
  if(mode === 'signup' && pw.length < 6){{ errEl.textContent = 'Le mot de passe doit faire au moins 6 caractères.'; errEl.classList.add('show'); return; }}
  submitBtn.disabled = true;
  submitBtn.textContent = (mode === 'signup' ? 'Création...' : 'Connexion...');
  try {{
    if(mode === 'signup') await window._fb.signUp(email, pw);
    else                  await window._fb.signIn(email, pw);
    _bkCloseForm();
  }} catch(err){{
    var msg = (err && err.message) || 'Erreur inconnue';
    // Messages Firebase plus lisibles
    if(/auth\\/email-already-in-use/.test(msg))   msg = 'Cet email a déjà un compte. Connecte-toi à la place.';
    else if(/auth\\/invalid-email/.test(msg))     msg = "Email invalide.";
    else if(/auth\\/weak-password/.test(msg))     msg = 'Mot de passe trop court (min 6 caractères).';
    else if(/auth\\/invalid-credential/.test(msg) || /wrong-password|user-not-found/.test(msg)) msg = "Email ou mot de passe incorrect.";
    else if(/auth\\/too-many-requests/.test(msg)) msg = "Trop de tentatives. Réessaie dans quelques minutes.";
    else if(/auth\\/network-request-failed/.test(msg)) msg = "Pas de connexion réseau.";
    errEl.textContent = msg;
    errEl.classList.add('show');
    submitBtn.disabled = false;
    submitBtn.textContent = (mode === 'signup' ? 'Créer le compte' : 'Se connecter');
  }}
}}

async function _bkAuthForgotPw(){{
  var email = (document.getElementById('bk-auth-email').value || '').trim();
  if(!email){{ alert('Saisis ton email d\\'abord dans le champ ci-dessus.'); return; }}
  try {{
    await window._fb.resetPw(email);
    alert('Email de réinitialisation envoyé à ' + email + '. Vérifie ta boîte mail (et le spam).');
  }} catch(e){{ alert('Erreur : ' + (e.message || e)); }}
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
// ── Formulaire "Ajouter un pari manuel" (sport libre, date au choix) ────
// Pour les paris places sur le bookmaker en dehors de l'algo : foot, NBA,
// tennis... peu importe la date du match (jusqu'a plusieurs jours en arriere).
function _bkOpenManualBetForm(prefill){{
  prefill = prefill || {{}};
  var root = _bkEnsureModalRoot();
  var todayStr = new Date().toISOString().slice(0, 10);
  window._bkManualState = {{
    sport:     prefill.sport     || 'nba',         // default = basket maintenant
    event:     prefill.event     || '',
    market:    prefill.market    || '',
    selectedGameId: prefill.selectedGameId || null,
    player:    prefill.player    || '',
    prop:      prefill.prop      || 'PTS',
    direction: prefill.direction || 'over',
    line:      prefill.line != null ? String(prefill.line) : '',
    cote:      prefill.cote != null ? String(prefill.cote) : '',
    stake:     prefill.stake != null ? String(prefill.stake) : String(window._bkLastStake || 2),
    matchDate: prefill.matchDate || todayStr,
    status:    prefill.status    || 'PENDING',
    tipster:   prefill.tipster   || (window._bkLastTipster || ''),
    note:      prefill.note      || '',
  }};
  var SPORTS_LIST = [
    {{id:'foot',   label:'Football', emoji:'⚽'}},
    {{id:'nba',    label:'Basket',   emoji:'🏀'}},
    {{id:'tennis', label:'Tennis',   emoji:'🎾'}},
    {{id:'rugby',  label:'Rugby',    emoji:'🏉'}},
    {{id:'mma',    label:'MMA',      emoji:'🥊'}},
    {{id:'nfl',    label:'NFL',      emoji:'🏈'}},
    {{id:'f1',     label:'F1',       emoji:'🏎️'}},
    {{id:'other',  label:'Autre',    emoji:'🎲'}},
  ];
  function render(){{
    var st = window._bkManualState;
    var c = parseFloat(String(st.cote).replace(',', '.')) || 0;
    var s = parseFloat(String(st.stake).replace(',', '.')) || 0;
    var l = parseFloat(String(st.line).replace(',', '.'));
    var pot = s * c;
    var prof = Math.max(0, pot - s);
    var isNba = st.sport === 'nba';
    var canSubmit;
    if(isNba){{
      canSubmit = st.event.trim().length > 0 && st.player.trim().length > 0 && !isNaN(l) && c > 1 && s > 0;
    }} else {{
      canSubmit = st.event.trim().length > 0 && st.market.trim().length > 0 && c > 1 && s > 0;
    }}

    var sportChips = SPORTS_LIST.map(function(sp){{
      var active = st.sport === sp.id;
      return '<button type="button" onclick="_bkManualSet(\\'sport\\', \\'' + sp.id + '\\')" style="'
        + 'display:inline-flex;align-items:center;gap:6px;padding:7px 12px;border-radius:999px;'
        + 'background:' + (active ? 'rgba(52,211,153,0.16)' : '#14161B') + ';'
        + 'border:1px solid ' + (active ? 'rgba(52,211,153,0.4)' : 'rgba(255,255,255,0.08)') + ';'
        + 'color:' + (active ? '#34D399' : '#cbd5e1') + ';font-size:12.5px;font-weight:600;cursor:pointer;font-family:inherit">'
        + '<span style="font-size:14px">' + sp.emoji + '</span>' + sp.label + '</button>';
    }}).join('');

    var STATUSES = [
      {{id:'PENDING', label:'⏱ En cours',  bg:'rgba(251,191,36,0.16)', fg:'#FBBF24'}},
      {{id:'WIN',     label:'✓ Gagné',     bg:'rgba(52,211,153,0.16)', fg:'#34D399'}},
      {{id:'LOSS',    label:'✕ Perdu',     bg:'rgba(248,113,113,0.16)', fg:'#F87171'}},
      {{id:'PUSH',    label:'↻ Annulé',    bg:'rgba(148,163,184,0.16)', fg:'#94A3B8'}},
    ];
    var statusBtns = STATUSES.map(function(o){{
      var active = st.status === o.id;
      return '<button type="button" onclick="_bkManualSet(\\'status\\', \\'' + o.id + '\\')" style="'
        + 'padding:9px 4px;border-radius:11px;border:1px solid ' + (active ? o.fg + '88' : 'rgba(255,255,255,0.06)') + ';'
        + 'background:' + (active ? o.bg : '#14161B') + ';color:' + (active ? o.fg : '#94A3B8') + ';'
        + 'font-weight:700;font-size:12px;cursor:pointer;font-family:inherit">' + o.label + '</button>';
    }}).join('');

    // Champs specifiques selon sport
    var sportFields = '';
    if(isNba){{
      // ─── Wizard NBA : Match (48h) → Joueur → Prop+Direction+Ligne ───
      var games = window.NBA_RECENT_GAMES || {{}};
      var gameIds = Object.keys(games).sort(function(a, b){{
        return (games[b].date || '').localeCompare(games[a].date || '');
      }});
      // Etape 1 : selection match
      var matchCards;
      if(gameIds.length === 0){{
        matchCards = '<div style="padding:14px;text-align:center;color:#94A3B8;font-size:13px;border:1px dashed rgba(255,255,255,0.10);border-radius:12px">'
          + 'Aucun match récent disponible (les box scores des matchs des 48 dernières heures se mettent à jour automatiquement après chaque cron run).'
          + '</div>';
      }} else {{
        // Label relatif depuis l'heure de fin (heure locale du browser)
        var _nowMs = Date.now();
        var _zPad = function(n){{ return String(n).padStart(2, '0'); }};
        var _localDayStr = function(d){{ return d.getFullYear() + '-' + _zPad(d.getMonth()+1) + '-' + _zPad(d.getDate()); }};
        var _todayLocal = new Date();
        var _yestLocal = new Date(_todayLocal); _yestLocal.setDate(_yestLocal.getDate() - 1);
        var _befLocal  = new Date(_todayLocal); _befLocal.setDate(_befLocal.getDate() - 2);
        var _todayKey = _localDayStr(_todayLocal);
        var _yestKey  = _localDayStr(_yestLocal);
        var _befKey   = _localDayStr(_befLocal);
        function _matchLabel(endIso, dateFallback){{
          if(endIso){{
            var endDate = new Date(endIso);
            if(!isNaN(endDate.getTime())){{
              var hoursAgo = (_nowMs - endDate.getTime()) / 3600000;
              var dayKey = _localDayStr(endDate);
              if(dayKey === _todayKey){{
                if(hoursAgo < 12) return 'Cette nuit (il y a ' + Math.floor(Math.max(1, hoursAgo)) + 'h)';
                return 'Ce matin (il y a ' + Math.floor(hoursAgo) + 'h)';
              }}
              if(dayKey === _yestKey) return 'Hier (il y a ' + Math.floor(hoursAgo) + 'h)';
              if(dayKey === _befKey)  return 'Avant-hier (il y a ' + Math.floor(hoursAgo) + 'h)';
              return _bkFmtDateShort(dayKey);
            }}
          }}
          return _bkFmtDateShort(dateFallback || '');
        }}
        matchCards = '<div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:8px;margin-bottom:6px">'
          + gameIds.map(function(gid){{
              var g = games[gid];
              var active = st.selectedGameId === gid;
              var dayLabel = _matchLabel(g.end_iso, g.date);
              var nPlayers = Object.keys(g.players || {{}}).length;
              return '<button type="button" onclick="_bkManualPickGame(\\'' + gid + '\\')" style="'
                + 'display:flex;flex-direction:column;gap:4px;padding:11px 13px;border-radius:12px;'
                + 'border:1px solid ' + (active ? '#34D399' : 'rgba(255,255,255,0.07)') + ';'
                + 'background:' + (active ? 'rgba(52,211,153,0.10)' : '#14161B') + ';'
                + 'color:#fff;cursor:pointer;font-family:inherit;text-align:left;font-size:13px">'
                + '<div style="font-size:11px;color:#FBBF24;font-weight:700">📅 ' + dayLabel + '</div>'
                + '<div style="font-weight:600">' + (g.matchup || '?') + '</div>'
                + '<div style="font-size:11px;color:#94A3B8">' + nPlayers + ' joueurs</div>'
                + '</button>';
          }}).join('') + '</div>';
      }}
      // Resolu : Joueur + Prop + Direction + Ligne
      var playerStepHtml = '';
      var pickStepHtml = '';
      var autoResultHtml = '';
      if(st.selectedGameId){{
        var game = games[st.selectedGameId];
        var players = game ? (game.players || {{}}) : {{}};
        var playerNames = Object.keys(players).sort();
        // Etape 2 : selection joueur (avec mini-stats du match)
        var playerOptions = playerNames.map(function(n){{
          var stats = players[n];
          var pts = stats.PTS != null ? stats.PTS : 0;
          var reb = stats.REB != null ? stats.REB : 0;
          var ast = stats.AST != null ? stats.AST : 0;
          var f3m = stats.FG3M != null ? stats.FG3M : 0;
          var active = st.player === n;
          var nameEsc = n.replace(/'/g, "\\\\'").replace(/"/g, '&quot;');
          return '<button type="button" onclick="_bkManualPickPlayer(\\'' + nameEsc + '\\')" style="'
            + 'display:flex;justify-content:space-between;align-items:center;gap:8px;padding:9px 12px;border-radius:10px;'
            + 'border:1px solid ' + (active ? '#34D399' : 'rgba(255,255,255,0.06)') + ';'
            + 'background:' + (active ? 'rgba(52,211,153,0.10)' : '#0F1115') + ';'
            + 'color:#fff;cursor:pointer;font-family:inherit;font-size:12.5px;text-align:left">'
            + '<span style="font-weight:600;flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">' + n + '</span>'
            + '<span style="color:#94A3B8;font-size:11px;font-variant-numeric:tabular-nums;flex-shrink:0">' + pts + 'pts ' + reb + 'rb ' + ast + 'pa ' + f3m + '3pm</span>'
            + '</button>';
        }}).join('');
        playerStepHtml = '<div class="bk-m-grp" style="margin-top:16px">'
          + '<label class="bk-m-label">Joueur (' + playerNames.length + ')</label>'
          + '<div style="max-height:260px;overflow-y:auto;display:flex;flex-direction:column;gap:5px;padding:2px;border:1px solid rgba(255,255,255,0.05);border-radius:12px">'
          + playerOptions
          + '</div></div>';
        // Etape 3 : Prop / Direction / Ligne (apparait apres player choisi)
        if(st.player){{
          var NBA_PROPS = [
            {{id:'PTS',  label:'Points'}},
            {{id:'REB',  label:'Rebonds'}},
            {{id:'AST',  label:'Passes'}},
            {{id:'FG3M', label:'3-points'}},
            {{id:'RA',   label:'Rebonds + Passes'}},
            {{id:'PR',   label:'Points + Rebonds'}},
            {{id:'PA',   label:'Points + Passes'}},
            {{id:'PRA',  label:'Points + Rebonds + Passes'}},
          ];
          var propOptions = NBA_PROPS.map(function(p){{
            var sel = st.prop === p.id ? ' selected' : '';
            return '<option value="' + p.id + '"' + sel + '>' + p.label + ' (' + p.id + ')</option>';
          }}).join('');
          var dirChip = function(id, lbl, bg, fg){{
            var active = st.direction === id;
            return '<button type="button" onclick="_bkManualSet(\\'direction\\', \\'' + id + '\\')" style="'
              + 'flex:1;padding:10px 0;border-radius:11px;border:1px solid ' + (active ? fg + '88' : 'rgba(255,255,255,0.06)') + ';'
              + 'background:' + (active ? bg : '#14161B') + ';color:' + (active ? fg : '#94A3B8') + ';'
              + 'font-weight:700;font-size:13px;cursor:pointer;font-family:inherit">' + lbl + '</button>';
          }};
          pickStepHtml =
            '<div class="bk-m-grp" style="margin-top:16px">'
            +   '<label class="bk-m-label">Type de prop</label>'
            +   '<select class="bk-m-input" id="bk-mb-prop" style="cursor:pointer">' + propOptions + '</select>'
            + '</div>'
            + '<div class="bk-m-grp" style="margin-top:14px">'
            +   '<label class="bk-m-label">Direction</label>'
            +   '<div style="display:flex;gap:8px">'
            +     dirChip('over',  '↑ Plus de',  'rgba(52,211,153,0.16)', '#34D399')
            +     dirChip('under', '↓ Moins de', 'rgba(248,113,113,0.16)', '#F87171')
            +   '</div>'
            + '</div>'
            + '<div class="bk-m-grp" style="margin-top:14px">'
            +   '<label class="bk-m-label">Ligne</label>'
            +   '<input class="bk-m-input" id="bk-mb-line" inputmode="decimal" value="' + st.line + '" placeholder="14.5">'
            + '</div>';
          // Auto-detect WIN/LOSS si tous les champs sont remplis
          var box = players[st.player];
          var actual = _bkComputePropFromBox(box, st.prop);
          if(actual !== undefined && st.line !== ''){{
            var lineN = parseFloat(String(st.line).replace(',', '.'));
            if(!isNaN(lineN)){{
              var actualResult;
              if(st.direction === 'over') actualResult = actual > lineN ? 'WIN' : (actual < lineN ? 'LOSS' : 'PUSH');
              else                        actualResult = actual < lineN ? 'WIN' : (actual > lineN ? 'LOSS' : 'PUSH');
              var rColor = actualResult === 'WIN' ? '#34D399' : (actualResult === 'LOSS' ? '#F87171' : '#94A3B8');
              var rIcon  = actualResult === 'WIN' ? '✓' : (actualResult === 'LOSS' ? '✕' : '↻');
              var rLabel = actualResult === 'WIN' ? 'GAGNÉ' : (actualResult === 'LOSS' ? 'PERDU' : 'PUSH');
              var dirLabel = st.direction === 'over' ? 'plus de' : 'moins de';
              autoResultHtml =
                '<div style="margin-top:14px;padding:14px;border-radius:14px;background:rgba(255,255,255,0.03);border:1px solid ' + rColor + '44">'
                + '<div style="color:#94A3B8;font-size:11px;font-weight:600;text-transform:uppercase;letter-spacing:0.4px;margin-bottom:6px">📊 Résultat auto-detecté</div>'
                + '<div style="display:flex;align-items:center;justify-content:space-between;gap:12px;flex-wrap:wrap">'
                +   '<div style="color:#fff;font-size:13px"><b>' + st.player + '</b>: <b style="color:#fff">' + actual + '</b> ' + st.prop + ' (' + dirLabel + ' ' + lineN + ')</div>'
                +   '<div style="font-size:14px;font-weight:800;color:' + rColor + '">' + rIcon + ' ' + rLabel + '</div>'
                + '</div>'
                + '<div style="color:#94A3B8;font-size:11px;margin-top:4px">Tu peux quand même override en bas avec le sélecteur Statut.</div>'
                + '</div>';
              // Auto-set status si pas encore override
              if(!window._bkManualUserSetStatus){{
                st.status = actualResult;
              }}
            }}
          }}
        }}
      }}
      sportFields =
        '<label class="bk-m-label">Match (matchs terminés depuis &lt; 60h)</label>'
        + matchCards
        + playerStepHtml
        + pickStepHtml
        + autoResultHtml
        + '<div class="bk-m-row2" style="margin-top:14px">'
        +   '<div><label class="bk-m-label">Cote</label>'
        +     '<input class="bk-m-input" id="bk-mb-cote" inputmode="decimal" value="' + st.cote + '" placeholder="1.85"></div>'
        +   '<div><label class="bk-m-label">Mise (€)</label>'
        +     '<input class="bk-m-input" id="bk-mb-stake" inputmode="decimal" value="' + st.stake + '" placeholder="10"></div>'
        + '</div>'
        + '<div class="bk-m-quick">'
        +   [1, 2, 5, 10, 25].map(function(v){{ return '<button type="button" onclick="_bkManualSetStake(' + v + ')">' + v + '€</button>'; }}).join('')
        + '</div>';
      // Force "match" et "event" depuis le game selectionne (pour pick storage)
      if(st.selectedGameId && games[st.selectedGameId]){{
        st.event = games[st.selectedGameId].matchup || st.event;
        st.matchDate = games[st.selectedGameId].date || st.matchDate;
      }}
    }} else {{
      sportFields =
        '<div class="bk-m-grp">'
        +   '<label class="bk-m-label">Événement</label>'
        +   '<input class="bk-m-input" id="bk-mb-event" value="' + st.event.replace(/"/g, '&quot;') + '" placeholder="PSG vs OM, Lakers vs Celtics...">'
        + '</div>'
        + '<div class="bk-m-grp" style="margin-top:14px">'
        +   '<label class="bk-m-label">Type de pari</label>'
        +   '<input class="bk-m-input" id="bk-mb-market" value="' + st.market.replace(/"/g, '&quot;') + '" placeholder="PSG vainqueur, Over 2.5 buts...">'
        + '</div>'
        + '<div class="bk-m-row2" style="margin-top:14px">'
        +   '<div><label class="bk-m-label">Cote</label>'
        +     '<input class="bk-m-input" id="bk-mb-cote" inputmode="decimal" value="' + st.cote + '" placeholder="1.85"></div>'
        +   '<div><label class="bk-m-label">Mise (€)</label>'
        +     '<input class="bk-m-input" id="bk-mb-stake" inputmode="decimal" value="' + st.stake + '" placeholder="10"></div>'
        + '</div>'
        + '<div class="bk-m-quick">'
        +   [1, 2, 5, 10, 25].map(function(v){{ return '<button type="button" onclick="_bkManualSetStake(' + v + ')">' + v + '€</button>'; }}).join('')
        + '</div>';
    }}

    var html =
      '<div class="bk-modal-bd" onclick="_bkCloseForm()"></div>'
      + '<div class="bk-modal-card" role="dialog" aria-modal="true">'
      +   '<div class="bk-m-hd">'
      +     '<button class="bk-m-cancel" onclick="_bkCloseForm()">Annuler</button>'
      +     '<div class="bk-m-title">Ajouter un pari</div>'
      +     '<div style="width:64px"></div>'
      +   '</div>'
      +   '<div class="bk-m-body">'
      +     '<label class="bk-m-label">Sport</label>'
      +     '<div style="display:flex;gap:6px;flex-wrap:wrap;margin-bottom:14px">' + sportChips + '</div>'
      +     sportFields
      +     '<div class="bk-m-grp" style="margin-top:14px">'
      +       '<label class="bk-m-label">Date du match</label>'
      +       '<input class="bk-m-input" id="bk-mb-date" type="date" value="' + st.matchDate + '" max="' + todayStr + '">'
      +       '<div style="color:#8B8D98;font-size:11px;margin-top:4px">Tu peux saisir un pari pour un match passé (jusqu\\'à plusieurs jours en arrière).</div>'
      +     '</div>'
      +     '<div class="bk-m-grp" style="margin-top:14px">'
      +       '<label class="bk-m-label">Statut</label>'
      +       '<div style="display:grid;grid-template-columns:repeat(4,1fr);gap:6px">' + statusBtns + '</div>'
      +     '</div>'
      +     '<div class="bk-m-grp" style="margin-top:14px">'
      +       '<label class="bk-m-label">Tipster<span class="opt">· facultatif</span></label>'
      +       '<input class="bk-m-input" id="bk-mb-tipster" value="' + st.tipster.replace(/"/g, '&quot;') + '" placeholder="@PronoKing, instinct, Algo...">'
      +     '</div>'
      +     '<div class="bk-m-grp" style="margin-top:14px">'
      +       '<label class="bk-m-label">Note<span class="opt">· facultatif</span></label>'
      +       '<textarea class="bk-m-input" id="bk-mb-note" rows="2" placeholder="Pourquoi ce pari ?">' + st.note.replace(/</g, '&lt;') + '</textarea>'
      +     '</div>'
      +     '<div class="bk-m-potential">'
      +       '<div>'
      +         '<div class="bk-m-pot-l">Gain potentiel</div>'
      +         '<span class="bk-m-pot-big" id="bk-mb-pot">€' + pot.toFixed(2) + '</span>'
      +       '</div>'
      +       '<div style="text-align:right">'
      +         '<div class="bk-m-pot-l">Bénéfice</div>'
      +         '<span class="bk-m-pot-prof" id="bk-mb-prof">+€' + prof.toFixed(2) + '</span>'
      +       '</div>'
      +     '</div>'
      +   '</div>'
      +   '<div class="bk-m-ft">'
      +     '<button class="bk-m-cta" id="bk-mb-submit"' + (canSubmit ? '' : ' disabled') + '>Enregistrer le pari</button>'
      +   '</div>'
      + '</div>';
    // Capture le scroll + focus AVANT le re-render pour les restaurer apres
    var _prevBody = root.querySelector('.bk-m-body');
    var _prevScroll = _prevBody ? _prevBody.scrollTop : 0;
    var _prevFocusId = (document.activeElement && document.activeElement.id) || null;
    var _prevSelStart = null, _prevSelEnd = null;
    if(_prevFocusId && document.activeElement && document.activeElement.selectionStart != null){{
      try {{ _prevSelStart = document.activeElement.selectionStart; _prevSelEnd = document.activeElement.selectionEnd; }} catch(e){{}}
    }}
    root.innerHTML = html;
    setTimeout(function(){{ root.classList.add('open'); }}, 10);
    // Restaure scroll + focus + position curseur apres re-render
    setTimeout(function(){{
      var newBody = root.querySelector('.bk-m-body');
      if(newBody && _prevScroll > 0) newBody.scrollTop = _prevScroll;
      if(_prevFocusId){{
        var fEl = document.getElementById(_prevFocusId);
        if(fEl){{
          try {{
            fEl.focus({{preventScroll: true}});
            if(_prevSelStart != null && fEl.setSelectionRange){{
              fEl.setSelectionRange(_prevSelStart, _prevSelEnd);
            }}
          }} catch(e){{}}
        }}
      }}
    }}, 0);
    // Wire les inputs (sans re-render pour ne pas perdre le focus)
    var byId = function(id){{ return document.getElementById(id); }};
    function wire(id, key){{
      var el = byId(id); if(!el) return;
      el.addEventListener('input', function(e){{
        window._bkManualState[key] = e.target.value;
        if(key === 'cote' || key === 'stake') _bkManualUpdateCalc();
      }});
    }}
    wire('bk-mb-event', 'event');
    wire('bk-mb-market', 'market');
    wire('bk-mb-player', 'player');
    wire('bk-mb-cote', 'cote');
    wire('bk-mb-stake', 'stake');
    wire('bk-mb-date', 'matchDate');
    wire('bk-mb-tipster', 'tipster');
    wire('bk-mb-note', 'note');
    // Line: re-render apres delay pour mettre a jour l'auto-detect WIN/LOSS
    var lineEl = byId('bk-mb-line');
    if(lineEl){{
      lineEl.addEventListener('input', function(e){{
        window._bkManualState.line = e.target.value;
        clearTimeout(window._bkManualLineDebounce);
        window._bkManualLineDebounce = setTimeout(function(){{
          if(window._bkManualRender) window._bkManualRender();
        }}, 350);
      }});
    }}
    var propSel = byId('bk-mb-prop');
    if(propSel){{ propSel.addEventListener('change', function(e){{
      window._bkManualState.prop = e.target.value;
      if(window._bkManualRender) window._bkManualRender();
    }}); }}
    byId('bk-mb-submit').addEventListener('click', function(){{ _bkManualSubmit(); }});
    setTimeout(function(){{
      var firstEmpty = byId('bk-mb-line') || byId('bk-mb-event');
      if(firstEmpty && !firstEmpty.value) firstEmpty.focus();
    }}, 120);
  }}
  // Reset le flag user-override pour que l'auto-detect s'applique au 1er render
  window._bkManualUserSetStatus = false;
  window._bkManualRender = render;
  render();
  window._bkFormEscHandler = function(e){{ if(e.key === 'Escape') _bkCloseForm(); }};
  document.addEventListener('keydown', window._bkFormEscHandler);
}}
function _bkManualSet(key, val){{
  if(!window._bkManualState) return;
  window._bkManualState[key] = val;
  // Si l'user change manuellement le statut, on memorise ce override pour ne pas
  // que l'auto-detect ecrase a chaque re-render
  if(key === 'status') window._bkManualUserSetStatus = true;
  // Si l'user change de sport, reset wizard NBA
  if(key === 'sport'){{
    window._bkManualState.selectedGameId = null;
    window._bkManualState.player = '';
  }}
  if(window._bkManualRender) window._bkManualRender();
}}
function _bkManualPickGame(gid){{
  if(!window._bkManualState) return;
  window._bkManualState.selectedGameId = gid;
  window._bkManualState.player = '';   // reset player choice when game changes
  window._bkManualUserSetStatus = false;
  if(window._bkManualRender) window._bkManualRender();
}}
function _bkManualPickPlayer(name){{
  if(!window._bkManualState) return;
  window._bkManualState.player = name;
  window._bkManualUserSetStatus = false;
  if(window._bkManualRender) window._bkManualRender();
}}
function _bkManualSetStake(v){{
  if(!window._bkManualState) return;
  window._bkManualState.stake = String(v);
  var el = document.getElementById('bk-mb-stake');
  if(el) el.value = String(v);
  _bkManualUpdateCalc();
}}
function _bkManualUpdateCalc(){{
  var st = window._bkManualState;
  if(!st) return;
  var c = parseFloat(String(st.cote).replace(',', '.')) || 0;
  var s = parseFloat(String(st.stake).replace(',', '.')) || 0;
  var pot = s * c;
  var prof = Math.max(0, pot - s);
  var canSubmit = st.event.trim().length > 0 && st.market.trim().length > 0 && c > 1 && s > 0;
  var potEl  = document.getElementById('bk-mb-pot');
  var profEl = document.getElementById('bk-mb-prof');
  var btn    = document.getElementById('bk-mb-submit');
  if(potEl)  potEl.textContent = '€' + pot.toFixed(2);
  if(profEl) profEl.textContent = '+€' + prof.toFixed(2);
  if(btn) btn.disabled = !canSubmit;
}}
function _bkManualSubmit(){{
  var st = window._bkManualState;
  if(!st) return;
  var isNba = st.sport === 'nba';
  var event = st.event.trim();
  var cote = parseFloat(String(st.cote).replace(',', '.'));
  var stake = parseFloat(String(st.stake).replace(',', '.'));
  if(!event){{ alert(isNba ? 'Match requis (ex: Cavaliers @ Knicks)' : 'Événement requis (ex: PSG vs OM)'); return; }}
  if(isNaN(cote) || cote <= 1){{ alert('Cote invalide (> 1.0)'); return; }}
  if(isNaN(stake) || stake <= 0){{ alert('Mise invalide'); return; }}
  var sportCode = String(st.sport || 'OTHER').toUpperCase();
  var isResolved = st.status === 'WIN' || st.status === 'LOSS' || st.status === 'PUSH';
  var pick = {{
    id:          'user_' + Date.now() + '_' + Math.random().toString(36).slice(2, 8),
    sport:       sportCode,
    source:      'manual',
    cote:        cote,
    stake:       stake,
    match_date:  st.matchDate || null,
    tipster:     st.tipster.trim() || null,
    note:        st.note.trim() || null,
    created:     new Date().toISOString(),
    result:      isResolved ? st.status : null,
    resolved_at: isResolved ? new Date().toISOString() : null,
    manual_override: isResolved,
    actual:      null,
  }};
  if(isNba){{
    // Pick NBA structure (player/prop/direction/line) compatible avec _bkRowHtml
    var player = st.player.trim();
    var line = parseFloat(String(st.line).replace(',', '.'));
    if(!player){{ alert('Joueur requis (ex: Karl-Anthony Towns)'); return; }}
    if(isNaN(line)){{ alert('Ligne invalide (ex: 14.5)'); return; }}
    // Parse "Cavaliers @ Knicks" -> away="Cavaliers", home="Knicks"
    var away = '', home = '';
    var m = event.match(/^(.+?)\\s*@\\s*(.+)$/);
    if(m){{ away = m[1].trim(); home = m[2].trim(); }}
    else  {{ home = event; }}
    pick.player    = player;
    pick.prop      = st.prop || 'PTS';
    pick.direction = st.direction || 'over';
    pick.line      = line;
    pick.home      = home;
    pick.away      = away;
    pick.event     = event;
  }} else {{
    // Pick generique (foot/tennis/etc) : event + market texte libre
    var market = st.market.trim();
    var lineGen = st.line ? parseFloat(String(st.line).replace(',', '.')) : null;
    if(!market){{ alert('Type de pari requis (ex: PSG vainqueur)'); return; }}
    pick.event  = event;
    pick.market = market;
    pick.line   = lineGen;
  }}
  var arr = _loadUserPicks();
  arr.push(pick);
  _saveUserPicks(arr);
  if(stake) window._bkLastStake = stake;
  if(st.tipster.trim()){{
    window._bkLastTipster = st.tipster.trim();
    _bkAddTipster(st.tipster.trim());
  }}
  _bkCloseForm();
  if(typeof renderUserPicks === 'function') renderUserPicks();
}}

// ── Modal "Modifier le pari" (ouvre form pre-remplie pour un pick existant) ──
function _bkEditSet(key, val){{
  if(!window._bkEditState) return;
  window._bkEditState[key] = val;
  if(window._bkEditRender) window._bkEditRender();
}}
function _bkEditSetStake(v){{
  if(!window._bkEditState) return;
  window._bkEditState.stake = String(v);
  var el = document.getElementById('bk-edit-stake');
  if(el) el.value = String(v);
  _bkEditUpdateCalc();
}}
function _bkEditUpdateCalc(){{
  var st = window._bkEditState;
  if(!st) return;
  var c = parseFloat(String(st.cote).replace(',', '.')) || 0;
  var s = parseFloat(String(st.stake).replace(',', '.')) || 0;
  var pot = s * c;
  var prof = Math.max(0, pot - s);
  var canSubmit = c > 1 && s > 0;
  var potEl  = document.getElementById('bk-edit-pot');
  var profEl = document.getElementById('bk-edit-prof');
  var btn    = document.getElementById('bk-edit-submit');
  if(potEl)  potEl.textContent = '€' + pot.toFixed(2);
  if(profEl) profEl.textContent = '+€' + prof.toFixed(2);
  if(btn) btn.disabled = !canSubmit;
}}
function _bkEditDelete(){{
  if(!window._bkEditState) return;
  var pid = window._bkEditState.pickId;
  if(!confirm('Supprimer définitivement ce pari ?')) return;
  var arr = _loadUserPicks().filter(function(x){{ return x.id !== pid; }});
  _saveUserPicks(arr);
  _bkCloseForm();
  if(typeof renderUserPicks === 'function') renderUserPicks();
}}
function _bkEditSubmit(){{
  var st = window._bkEditState;
  if(!st) return;
  var arr = _loadUserPicks();
  var idx = arr.findIndex(function(p){{ return p.id === st.pickId; }});
  if(idx < 0){{ alert('Pick introuvable'); _bkCloseForm(); return; }}
  var p = arr[idx];
  var cote  = parseFloat(String(st.cote).replace(',', '.'));
  var stake = parseFloat(String(st.stake).replace(',', '.'));
  if(isNaN(cote) || cote <= 1){{ alert('Cote invalide (> 1.0)'); return; }}
  if(isNaN(stake) || stake <= 0){{ alert('Mise invalide'); return; }}
  p.cote       = cote;
  p.stake      = stake;
  p.match_date = st.matchDate || null;
  p.tipster    = (st.tipster||'').trim() || null;
  p.note       = (st.note||'').trim() || null;
  if(st.status === 'PENDING'){{
    p.result = null; p.actual = null; p.resolved_at = null;
    p.manual_override = false; p.cashout_amount = null;
  }} else if(st.status === 'CASHOUT'){{
    var cash = parseFloat(String(st.cashoutAmount).replace(',', '.'));
    if(isNaN(cash)){{ alert('Montant cashout invalide (ex: +3.20 ou -2.00)'); return; }}
    p.result          = cash > 0 ? 'WIN' : (cash < 0 ? 'LOSS' : 'PUSH');
    p.cashout_amount  = cash;
    p.manual_override = true;
    p.resolved_at     = new Date().toISOString();
  }} else {{
    p.result          = st.status;
    p.cashout_amount  = null;
    p.manual_override = true;
    p.resolved_at     = new Date().toISOString();
  }}
  if(p.tipster){{
    window._bkLastTipster = p.tipster;
    _bkAddTipster(p.tipster);
  }}
  arr[idx] = p;
  _saveUserPicks(arr);
  _bkCloseForm();
  if(typeof renderUserPicks === 'function') renderUserPicks();
}}
function _bkOpenEditForm(pickId){{
  var arr0 = _loadUserPicks();
  var p = arr0.find(function(x){{ return x.id === pickId; }});
  if(!p){{ alert('Pick introuvable'); return; }}
  var root = _bkEnsureModalRoot();
  var initStatus = p.result;
  if(p.cashout_amount != null) initStatus = 'CASHOUT';
  if(!initStatus || initStatus === 'PENDING') initStatus = 'PENDING';
  window._bkEditState = {{
    pickId:        pickId,
    cote:          p.cote != null ? String(p.cote) : '',
    stake:         p.stake != null ? String(p.stake) : '',
    matchDate:     (p.match_date || '').slice(0, 10),
    status:        initStatus,
    cashoutAmount: p.cashout_amount != null ? String(p.cashout_amount) : '',
    tipster:       p.tipster || '',
    note:          p.note || '',
  }};
  function render(){{
    var st = window._bkEditState;
    var pCur = _loadUserPicks().find(function(x){{ return x.id === pickId; }});
    if(!pCur){{ _bkCloseForm(); return; }}
    var isManual = pCur.source === 'manual' || (!!pCur.event && !!pCur.market && !pCur.player);
    var idTitle, idSub, sportIcon;
    if(isManual){{
      idTitle = (pCur.market || '?').replace(/</g, '&lt;');
      if(pCur.line != null && pCur.line !== '') idTitle += ' ' + pCur.line;
      idSub = (pCur.event || '').replace(/</g, '&lt;');
      var SPORT_EMOJI = {{FOOT:'⚽', NBA:'🏀', TENNIS:'🎾', RUGBY:'🏉', MMA:'🥊', NFL:'🏈', F1:'🏎️', OTHER:'🎲'}};
      sportIcon = SPORT_EMOJI[(pCur.sport || 'OTHER').toUpperCase()] || '🎲';
    }} else {{
      var propLabel = ({{PTS:'pts',REB:'reb',AST:'pas',FG3M:'3PM',RA:'reb+pas',PR:'pts+reb',PA:'pts+ast',PRA:'PRA'}})[pCur.prop] || pCur.prop;
      var dir = pCur.direction === 'over' ? 'plus de' : 'moins de';
      idTitle = (pCur.player || '?') + ' ' + dir + ' ' + pCur.line + ' ' + propLabel;
      idSub = (pCur.away || '') + ' @ ' + (pCur.home || '');
      sportIcon = '🏀';
    }}
    var c = parseFloat(String(st.cote).replace(',', '.')) || 0;
    var s = parseFloat(String(st.stake).replace(',', '.')) || 0;
    var pot = s * c;
    var prof = Math.max(0, pot - s);
    var canSubmit = c > 1 && s > 0;
    var STATUSES = [
      {{id:'PENDING', label:'⏱ En cours', bg:'rgba(251,191,36,0.16)', fg:'#FBBF24'}},
      {{id:'WIN',     label:'✓ Gagné',    bg:'rgba(52,211,153,0.16)', fg:'#34D399'}},
      {{id:'LOSS',    label:'✕ Perdu',    bg:'rgba(248,113,113,0.16)', fg:'#F87171'}},
      {{id:'PUSH',    label:'↻ Annulé',   bg:'rgba(148,163,184,0.16)', fg:'#94A3B8'}},
      {{id:'CASHOUT', label:'💵 Cashout', bg:'rgba(56,189,248,0.16)',  fg:'#38BDF8'}},
    ];
    var statusBtns = STATUSES.map(function(o){{
      var active = st.status === o.id;
      return '<button type="button" onclick="_bkEditSet(\\'status\\', \\'' + o.id + '\\')" style="'
        + 'padding:9px 4px;border-radius:11px;border:1px solid ' + (active ? o.fg + '88' : 'rgba(255,255,255,0.06)') + ';'
        + 'background:' + (active ? o.bg : '#14161B') + ';color:' + (active ? o.fg : '#94A3B8') + ';'
        + 'font-weight:700;font-size:11.5px;cursor:pointer;font-family:inherit">' + o.label + '</button>';
    }}).join('');
    var cashoutInput = '';
    if(st.status === 'CASHOUT'){{
      cashoutInput = '<div class="bk-m-grp" style="margin-top:14px">'
        + '<label class="bk-m-label">Montant net du cashout (€)</label>'
        + '<input class="bk-m-input" id="bk-edit-cashout" inputmode="decimal" value="' + st.cashoutAmount + '" placeholder="+3.20 (gain) ou -2.00 (perte)">'
        + '<div style="color:#8B8D98;font-size:11px;margin-top:4px">Net du cashout. Ex: mise 5€, recu 8.20€ → tape +3.20</div>'
        + '</div>';
    }}
    // Capture scroll/focus avant re-render
    var _prevBody = root.querySelector('.bk-m-body');
    var _prevScroll = _prevBody ? _prevBody.scrollTop : 0;
    var _prevFocusId = (document.activeElement && document.activeElement.id) || null;
    var _prevSelStart = null, _prevSelEnd = null;
    if(_prevFocusId && document.activeElement && document.activeElement.selectionStart != null){{
      try {{ _prevSelStart = document.activeElement.selectionStart; _prevSelEnd = document.activeElement.selectionEnd; }} catch(e){{}}
    }}
    var html =
      '<div class="bk-modal-bd" onclick="_bkCloseForm()"></div>'
      + '<div class="bk-modal-card" role="dialog">'
      +   '<div class="bk-m-hd">'
      +     '<button class="bk-m-cancel" onclick="_bkCloseForm()">Annuler</button>'
      +     '<div class="bk-m-title">Modifier le pari</div>'
      +     '<div style="width:64px"></div>'
      +   '</div>'
      +   '<div class="bk-m-body">'
      +     '<div class="bk-m-context">'
      +       '<div class="bk-m-icon">' + sportIcon + '</div>'
      +       '<div style="flex:1;min-width:0">'
      +         '<div class="bk-m-ctx-title">' + idTitle + '</div>'
      +         '<div class="bk-m-ctx-sub"><span>' + idSub + '</span></div>'
      +       '</div>'
      +     '</div>'
      +     '<div class="bk-m-row2">'
      +       '<div><label class="bk-m-label">Cote</label>'
      +         '<input class="bk-m-input" id="bk-edit-cote" inputmode="decimal" value="' + st.cote + '" placeholder="1.85"></div>'
      +       '<div><label class="bk-m-label">Mise (€)</label>'
      +         '<input class="bk-m-input" id="bk-edit-stake" inputmode="decimal" value="' + st.stake + '" placeholder="10"></div>'
      +     '</div>'
      +     '<div class="bk-m-quick">'
      +       [1, 2, 5, 10, 25].map(function(v){{ return '<button type="button" onclick="_bkEditSetStake(' + v + ')">' + v + '€</button>'; }}).join('')
      +     '</div>'
      +     '<div class="bk-m-grp" style="margin-top:14px">'
      +       '<label class="bk-m-label">Date du match</label>'
      +       '<input class="bk-m-input" id="bk-edit-date" type="date" value="' + st.matchDate + '">'
      +     '</div>'
      +     '<div class="bk-m-grp" style="margin-top:14px">'
      +       '<label class="bk-m-label">Statut</label>'
      +       '<div style="display:grid;grid-template-columns:repeat(5,1fr);gap:5px">' + statusBtns + '</div>'
      +       cashoutInput
      +     '</div>'
      +     '<div class="bk-m-grp" style="margin-top:14px">'
      +       '<label class="bk-m-label">Tipster<span class="opt">· facultatif</span></label>'
      +       '<div class="bk-tipster-wrap">'
      +         '<input class="bk-m-input" id="bk-edit-tipster" value="' + (st.tipster||'').replace(/"/g, '&quot;') + '" placeholder="@PronoKing, Algo, instinct..." autocomplete="off">'
      +         '<button type="button" class="bk-tipster-chevron" id="bk-edit-tipster-chevron" title="Voir les tipsters enregistres">▼</button>'
      +         '<div class="bk-tipster-dd" id="bk-edit-tipster-dd"></div>'
      +       '</div>'
      +     '</div>'
      +     '<div class="bk-m-grp" style="margin-top:14px">'
      +       '<label class="bk-m-label">Note<span class="opt">· facultatif</span></label>'
      +       '<textarea class="bk-m-input" id="bk-edit-note" rows="2" placeholder="Pourquoi ce pari ?">' + (st.note||'').replace(/</g, '&lt;') + '</textarea>'
      +     '</div>'
      +     '<div class="bk-m-potential">'
      +       '<div><div class="bk-m-pot-l">Gain potentiel</div>'
      +         '<span class="bk-m-pot-big" id="bk-edit-pot">€' + pot.toFixed(2) + '</span></div>'
      +       '<div style="text-align:right"><div class="bk-m-pot-l">Bénéfice</div>'
      +         '<span class="bk-m-pot-prof" id="bk-edit-prof">+€' + prof.toFixed(2) + '</span></div>'
      +     '</div>'
      +     '<button type="button" onclick="_bkEditDelete()" style="margin-top:16px;width:100%;padding:11px;border-radius:12px;background:transparent;border:1px solid rgba(248,113,113,0.25);color:#F87171;font-weight:600;font-size:14px;cursor:pointer;font-family:inherit">🗑 Supprimer ce pari</button>'
      +   '</div>'
      +   '<div class="bk-m-ft">'
      +     '<button class="bk-m-cta" id="bk-edit-submit"' + (canSubmit ? '' : ' disabled') + '>💾 Enregistrer</button>'
      +   '</div>'
      + '</div>';
    root.innerHTML = html;
    setTimeout(function(){{ root.classList.add('open'); }}, 10);
    // Restaure scroll + focus + selection
    setTimeout(function(){{
      var newBody = root.querySelector('.bk-m-body');
      if(newBody && _prevScroll > 0) newBody.scrollTop = _prevScroll;
      if(_prevFocusId){{
        var fEl = document.getElementById(_prevFocusId);
        if(fEl){{
          try {{
            fEl.focus({{preventScroll: true}});
            if(_prevSelStart != null && fEl.setSelectionRange) fEl.setSelectionRange(_prevSelStart, _prevSelEnd);
          }} catch(e){{}}
        }}
      }}
    }}, 0);
    // Wire les inputs
    var byId = function(id){{ return document.getElementById(id); }};
    function wire(id, key){{
      var el = byId(id); if(!el) return;
      el.addEventListener('input', function(e){{
        window._bkEditState[key] = e.target.value;
        if(key === 'cote' || key === 'stake') _bkEditUpdateCalc();
      }});
    }}
    wire('bk-edit-cote', 'cote');
    wire('bk-edit-stake', 'stake');
    wire('bk-edit-date', 'matchDate');
    wire('bk-edit-tipster', 'tipster');
    wire('bk-edit-note', 'note');
    var cashEl = byId('bk-edit-cashout');
    if(cashEl) cashEl.addEventListener('input', function(e){{ window._bkEditState.cashoutAmount = e.target.value; }});
    byId('bk-edit-submit').addEventListener('click', _bkEditSubmit);
    // Tipster dropdown (reuse pattern existant)
    var tipsterInput = byId('bk-edit-tipster');
    var tipsterDd    = byId('bk-edit-tipster-dd');
    var tipsterCh    = byId('bk-edit-tipster-chevron');
    function _renderTipsterDdE(filter){{
      var list = _bkAllTipsters();
      var q = (filter || '').trim().toLowerCase();
      if(q){{ list = list.filter(function(t){{ return t.name.toLowerCase().indexOf(q) !== -1; }}); }}
      if(!list.length){{
        tipsterDd.innerHTML = '<div class="bk-tipster-empty">Aucun tipster enregistre.</div>';
        return;
      }}
      tipsterDd.innerHTML = list.map(function(t){{
        var esc = String(t.name).replace(/"/g, '&quot;').replace(/'/g, "&#39;").replace(/</g, '&lt;');
        return '<div class="bk-tipster-item" data-name="' + esc + '"><span class="name">' + esc + '</span>'
          + (t.count > 0 ? '<span class="count">' + t.count + '</span>' : '') + '</div>';
      }}).join('');
    }}
    if(tipsterInput){{
      tipsterInput.addEventListener('focus', function(){{ _renderTipsterDdE(tipsterInput.value); tipsterDd.classList.add('open'); }});
      tipsterInput.addEventListener('input', function(e){{
        window._bkEditState.tipster = e.target.value;
        if(tipsterDd.classList.contains('open')) _renderTipsterDdE(e.target.value);
      }});
    }}
    if(tipsterCh){{
      tipsterCh.addEventListener('click', function(e){{
        e.preventDefault(); e.stopPropagation();
        if(tipsterDd.classList.contains('open')) tipsterDd.classList.remove('open');
        else {{ _renderTipsterDdE(tipsterInput.value); tipsterInput.focus(); tipsterDd.classList.add('open'); }}
      }});
    }}
    if(tipsterDd){{
      tipsterDd.addEventListener('click', function(e){{
        var item = e.target.closest('.bk-tipster-item');
        if(!item) return;
        var name = item.getAttribute('data-name');
        tipsterInput.value = name;
        window._bkEditState.tipster = name;
        tipsterDd.classList.remove('open');
        tipsterInput.focus();
      }});
    }}
  }}
  window._bkEditRender = render;
  render();
  window._bkFormEscHandler = function(e){{ if(e.key === 'Escape') _bkCloseForm(); }};
  document.addEventListener('keydown', window._bkFormEscHandler);
}}

function _bkOpenForm(opts){{
  // opts = {{ direction, payload, onSubmit }}
  // payload supporte plusieurs formes :
  //   - NBA Analyse : {{player, prop, line, home, away, book_over, book_under, median, ...}}
  //   - Foot pick   : {{sport:'foot', label, matchup, real_cote, ...}}
  //   - Tennis pick : {{sport:'tennis', label, matchup, real_cote, line?, ...}}
  // Quand p.label est fourni, on l'utilise comme titre principal et on cache la
  // ligne input + le chip direction (pick a "ligne fixe", typiquement winner /
  // score sets / buteur / pari ferme avec libelle deja construit).
  var root = _bkEnsureModalRoot();
  var p = opts.payload;
  var dir = opts.direction;
  // Mode rendu : si p.label fourni -> on l'utilise comme titre (foot/tennis "Plus
  // de 22.5 tirs total", "Buteur : Mbappé", "Vainqueur Pablo Carreno Busta").
  // Le line input est affiche separement DES QUE p.line est numerique, peu importe
  // le mode -> permet de modifier la ligne meme sur un pick algo predefini.
  var labelMode = !!p.label;
  var hasOU = (dir === 'over' || dir === 'under');
  var hasLine = (p.line !== undefined && p.line !== null && !isNaN(parseFloat(p.line)));
  var defLine = hasLine ? String(p.line) : '';
  var defCoteRaw = p.real_cote
    || (dir === 'over'  ? p.book_over  : null)
    || (dir === 'under' ? p.book_under : null);
  var defCote = defCoteRaw ? String(defCoteRaw) : '1.90';
  var defStake = String(window._bkLastStake || 2);
  var defTipster = window._bkLastTipster || '';
  window._bkFormState = {{
    opts: opts,
    line: defLine, cote: defCote, stake: defStake,
    tipster: defTipster, note: '',
  }};
  // Icone sport + couleur direction chip
  var sportIcon = '🏀';
  var sport = (p.sport || '').toLowerCase();
  if(sport === 'foot' || sport === 'football') sportIcon = '⚽';
  else if(sport === 'tennis') sportIcon = '🎾';
  // Titre + sous-titre
  var titleText = labelMode ? (p.label || p.player || '?') : (p.player || '?');
  var subParts = [];
  if(hasOU){{
    var dirCls = dir === 'over' ? 'over' : 'under';
    var dirLabel = dir === 'over' ? 'OVER' : 'UNDER';
    subParts.push('<span class="bk-dir-chip ' + dirCls + '">' + dirLabel + '</span>');
  }}
  if(!labelMode){{
    var propLabel = ({{PTS:'pts',REB:'reb',AST:'pas',FG3M:'3PM',RA:'reb+pas',PR:'pts+reb',PA:'pts+ast',PRA:'PRA'}})[p.prop] || p.prop || '';
    if(propLabel) subParts.push('<span>' + propLabel + '</span>');
  }}
  var matchup = p.matchup || ((p.away ? p.away + ' @ ' : '') + (p.home || ''));
  if(matchup){{
    if(subParts.length) subParts.push('<span class="sep">·</span>');
    subParts.push('<span>' + matchup + '</span>');
  }}
  var bookHint = [];
  if(!labelMode){{
    if(p.book_line !== undefined && p.book_line !== null) bookHint.push('Ligne book : ' + p.book_line);
    if(defCoteRaw && !p.real_cote) bookHint.push('Cote book : ' + defCoteRaw);
    if(p.median !== undefined && p.median !== null) bookHint.push('Méd L20 : ' + p.median);
  }} else if(p.real_cote){{
    bookHint.push('Cote algo : ' + p.real_cote);
  }}
  var hintLine = bookHint.length ? '<div class="bk-m-hint">' + bookHint.join(' · ') + '</div>' : '';
  // Ligne + cote : si pas de ligne pour ce pick (winner/score sec/buteur/etc),
  // on cache l'input ligne. Sinon ligne editable (foot/tennis modifiables).
  var lineRow;
  if(hasLine){{
    lineRow = (
      '<div class="bk-m-row2">'
      + '<div>'
      +   '<label class="bk-m-label">Ligne</label>'
      +   '<input class="bk-m-input" id="bk-m-line" inputmode="decimal" value="' + defLine + '" placeholder="9.5">'
      + '</div>'
      + '<div>'
      +   '<label class="bk-m-label">Cote</label>'
      +   '<input class="bk-m-input" id="bk-m-cote" inputmode="decimal" value="' + defCote + '" placeholder="1.90">'
      + '</div>'
      + '</div>'
    );
  }} else {{
    lineRow = (
      '<div class="bk-m-grp">'
      + '<label class="bk-m-label">Cote</label>'
      + '<input class="bk-m-input" id="bk-m-cote" inputmode="decimal" value="' + defCote + '" placeholder="1.90">'
      + '<input type="hidden" id="bk-m-line" value="' + defLine + '">'
      + '</div>'
    );
  }}

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
    +       '<div class="bk-m-icon">' + sportIcon + '</div>'
    +       '<div style="flex:1;min-width:0">'
    +         '<div class="bk-m-ctx-title">' + titleText + '</div>'
    +         '<div class="bk-m-ctx-sub">' + subParts.join('') + '</div>'
    +       '</div>'
    +     '</div>'
    +     lineRow
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
    +       '<div class="bk-tipster-wrap">'
    +         '<input class="bk-m-input" id="bk-m-tipster" value="' + defTipster.replace(/"/g, '&quot;') + '" placeholder="Algo, @PronoKing, instinct..." autocomplete="off">'
    +         '<button type="button" class="bk-tipster-chevron" id="bk-m-tipster-chevron" title="Voir les tipsters enregistres">▼</button>'
    +         '<div class="bk-tipster-dd" id="bk-m-tipster-dd"></div>'
    +       '</div>'
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
  // Tipster input + autocomplete dropdown
  var tipsterInput = byId('bk-m-tipster');
  var tipsterDd    = byId('bk-m-tipster-dd');
  var tipsterCh    = byId('bk-m-tipster-chevron');
  function _bkRenderTipsterDd(filter){{
    var list = _bkAllTipsters();
    var q = (filter || '').trim().toLowerCase();
    if(q){{ list = list.filter(function(t){{ return t.name.toLowerCase().indexOf(q) !== -1; }}); }}
    if(!list.length){{
      tipsterDd.innerHTML = '<div class="bk-tipster-empty">Aucun tipster enregistre — tape un nom et clique sur "Placer le pari" pour le sauvegarder.</div>';
      return;
    }}
    tipsterDd.innerHTML = list.map(function(t){{
      var esc = String(t.name).replace(/"/g, '&quot;').replace(/'/g, "&#39;").replace(/</g, '&lt;');
      var countHtml = t.count > 0 ? '<span class="count">' + t.count + '</span>' : '';
      return '<div class="bk-tipster-item" data-name="' + esc + '">'
        + '<span class="name">' + esc + '</span>'
        + countHtml
        + '<button type="button" class="del" title="Retirer">✕</button>'
        + '</div>';
    }}).join('');
  }}
  function _bkOpenTipsterDd(){{
    _bkRenderTipsterDd(tipsterInput.value);
    tipsterDd.classList.add('open');
  }}
  function _bkCloseTipsterDd(){{ tipsterDd.classList.remove('open'); }}
  tipsterInput.addEventListener('input', function(e){{
    window._bkFormState.tipster = e.target.value;
    if(tipsterDd.classList.contains('open')) _bkRenderTipsterDd(e.target.value);
  }});
  tipsterInput.addEventListener('focus', function(){{ _bkOpenTipsterDd(); }});
  tipsterCh.addEventListener('click', function(e){{
    e.preventDefault(); e.stopPropagation();
    if(tipsterDd.classList.contains('open')) _bkCloseTipsterDd();
    else {{ tipsterInput.focus(); _bkOpenTipsterDd(); }}
  }});
  tipsterDd.addEventListener('click', function(e){{
    var del = e.target.closest('.del');
    var item = e.target.closest('.bk-tipster-item');
    if(!item) return;
    var name = item.getAttribute('data-name');
    if(del){{
      e.stopPropagation();
      if(confirm('Retirer "' + name + '" des tipsters enregistres ?')){{
        _bkRemoveTipster(name);
        _bkRenderTipsterDd(tipsterInput.value);
      }}
      return;
    }}
    tipsterInput.value = name;
    window._bkFormState.tipster = name;
    _bkCloseTipsterDd();
    tipsterInput.focus();
  }});
  // Fermer la dd au clic en dehors (mais pas sur le chevron qui a son propre handler)
  document.addEventListener('mousedown', function _bkOutside(e){{
    if(!tipsterDd || !tipsterDd.classList.contains('open')) return;
    if(tipsterInput.contains(e.target) || tipsterDd.contains(e.target) || tipsterCh.contains(e.target)) return;
    _bkCloseTipsterDd();
  }});
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
    if(tipster) _bkAddTipster(tipster);  // memorise pour autocomplete futur
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
        id:         'user_' + Date.now() + '_' + Math.random().toString(36).slice(2, 8),
        sport:      data.sport,
        player:     data.player,
        prop:       data.prop,
        direction:  direction,
        line:       r.line,
        cote:       r.cote,
        stake:      r.stake,
        tipster:    r.tipster,
        note:       r.note,
        home:       data.home,
        away:       data.away,
        game_id:    data.game_id,
        match_date: data.match_date || null,   // critique en playoffs (meme matchup tous les 2j)
        opp:        data.opp,
        median:     data.median,
        mean:       data.mean,
        book_line:  data.book_line,
        book_over:  data.book_over,
        book_under: data.book_under,
        created:    new Date().toISOString(),
        result:     null,
        actual:     null,
        source:     'user',
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
  // Construit le texte selon la shape du pick :
  //   - NBA shape (player + prop + direction + line) -> rendu joueur/prop/over-under
  //   - Manual shape (foot/tennis/etc : event + market) -> rendu market + line si dispo
  var hasNbaShape = !!(p.player && p.prop && p.direction);
  var sportCode = String(p.sport || '').toUpperCase();
  var SPORT_EMOJI = {{FOOT:'⚽', NBA:'🏀', TENNIS:'🎾', RUGBY:'🏉', MMA:'🥊', NFL:'🏈', F1:'🏎️', OTHER:'🎲'}};
  var sportIcon = SPORT_EMOJI[sportCode] || (hasNbaShape ? '🏀' : '🎲');
  var label, matchup;
  if(hasNbaShape){{
    var dir = p.direction === 'over' ? 'plus de' : 'moins de';
    var propLabel = ({{PTS:'pts',REB:'reb',AST:'pas',FG3M:'3PM',RA:'reb+pas',PR:'pts+reb',PA:'pts+ast',PRA:'PRA'}})[p.prop] || p.prop;
    label = (p.player || '?') + ' ' + dir + ' ' + p.line + ' ' + propLabel;
    matchup = (p.away || '') + ' @ ' + (p.home || '');
  }} else {{
    // Manual : utilise market (deja sous forme "Plus de X foo") ou label en fallback.
    // On n'append PAS p.line car le market contient deja la ligne (ex: "Plus de 11.5 tirs Rayo Vallecano").
    label = (p.market || p.label || '?');
    matchup = p.event || ((p.away || '') + ' vs ' + (p.home || ''));
  }}
  var lines = [
    '🎯 <b>PICK PERSO</b>',
    '',
    sportIcon + ' ' + matchup,
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
  await pushTelegram(btn);
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
function _bkNorm(s){{
  // Normalisation pour matching tolerant : strip + lowercase + retire les diacritiques
  return String(s == null ? '' : s).trim().toLowerCase()
    .normalize('NFD').replace(/[\\u0300-\\u036f]/g, '');
}}
// Calcule la valeur du prop a partir des stats brutes du joueur (PTS/REB/AST/FG3M)
function _bkComputePropFromBox(box, prop){{
  if(!box) return undefined;
  var P = +box.PTS || 0, R = +box.REB || 0, A = +box.AST || 0, F = +box.FG3M || 0;
  switch(String(prop || '').toUpperCase()){{
    case 'PTS':  return P;
    case 'REB':  return R;
    case 'AST':  return A;
    case 'FG3M': return F;
    case 'RA':   return R + A;
    case 'PR':   return P + R;
    case 'PA':   return P + A;
    case 'PRA':  return P + R + A;
    default:     return undefined;
  }}
}}
// Cherche le joueur dans un dict {{playerName: box}} avec matching tolerant
function _bkFindPlayerBox(byPlayer, playerName){{
  if(!byPlayer) return undefined;
  if(byPlayer[playerName]) return byPlayer[playerName];
  var target = _bkNorm(playerName);
  // 1. Match strict normalise
  for(var k in byPlayer){{
    if(_bkNorm(k) === target) return byPlayer[k];
  }}
  // 2. Fuzzy : meme last name + first name 1ere lettre identique
  // Couvre "AJ Mitchell" <-> "Ajay Mitchell", "L. Doncic" <-> "Luka Doncic", etc.
  var parts = target.split(/\\s+/).filter(function(x){{ return x; }});
  if(parts.length >= 2){{
    var lastName = parts[parts.length - 1];
    var firstInitial = parts[0].charAt(0);
    var candidates = [];
    for(var k2 in byPlayer){{
      var kp = _bkNorm(k2).split(/\\s+/).filter(function(x){{ return x; }});
      if(kp.length >= 2 && kp[kp.length - 1] === lastName
         && kp[0].charAt(0) === firstInitial){{
        candidates.push(k2);
      }}
    }}
    if(candidates.length === 1) return byPlayer[candidates[0]];
  }}
  return undefined;
}}
// IMPORTANT (playoffs) : on n'a JAMAIS de fallback "player+prop sans game_id".
// En playoffs les memes equipes s'affrontent tous les 2 jours → utiliser une cle
// sans gid resolverait avec les stats du MAUVAIS match. Toujours exact gid match.
function autoResolveUserPicks(){{
  var hist = window.NBA_HISTORY || [];
  var boxScores = window.NBA_BOX_SCORES || {{}};
  if(!hist.length && !Object.keys(boxScores).length) return 0;
  // Map gid -> date (yyyy-mm-dd) pour backfill et verification visuelle
  var dateByGid = {{}};
  hist.forEach(function(h){{
    var gid = _bkNorm(h.game_id);
    if(gid && h.date && !dateByGid[gid]) dateByGid[gid] = String(h.date).slice(0,10);
  }});
  // actualMap : exact (gid|player|prop)
  var actualMap = {{}};
  // dnpMap : (gid|player) -> true si le joueur n'a pas joue (DNP / blesse / etc.)
  // L'algo marque DNP quand un de ses picks est sur un joueur qui n'a pas joue.
  var dnpMap = {{}};
  hist.forEach(function(h){{
    var gid = _bkNorm(h.game_id);
    var pl  = _bkNorm(h.player);
    if(h.result === 'DNP'){{
      dnpMap[gid + '|' + pl] = true;
      return;
    }}
    if(h.actual === null || h.actual === undefined) return;
    var pr  = _bkNorm(h.prop);
    var key = gid + '|' + pl + '|' + pr;
    if(actualMap[key] === undefined) actualMap[key] = h.actual;
  }});
  var boxByGid = {{}};
  Object.keys(boxScores).forEach(function(gid){{ boxByGid[_bkNorm(gid)] = boxScores[gid] || {{}}; }});
  // Helper : check si un joueur est DNP pour ce game (exact ou via fuzzy date)
  function _isDnp(p){{
    var gid = _bkNorm(p.game_id);
    var pl  = _bkNorm(p.player);
    if(dnpMap[gid + '|' + pl]) return true;
    // Fuzzy : si une autre entry history meme date+joueur dit DNP -> applique
    if(p.match_date){{
      var targetDate = String(p.match_date).slice(0, 10);
      for(var k in dnpMap){{
        var parts = k.split('|');
        var dnpGid = parts[0], dnpPl = parts[1];
        if(dnpPl !== pl) continue;
        var d = dateByGid[dnpGid];
        if(d && d.slice(0,10) === targetDate) return true;
      }}
    }}
    return false;
  }}
  // Helper : tente de retrouver l'actual via gid exact (history) puis box score.
  // 3 strategies en cascade :
  //   1. actualMap[gid|player|prop] : algo a deja resolu ce prop sur ce game
  //   2. boxScores[gid] + computeProp : on a la box du game, on calcule le prop
  //   3. Fallback fuzzy : si match_date connu, on cherche dans TOUS les games de
  //      cette date un player matching. Si EXACTEMENT UN game contient ce
  //      joueur ce jour-la -> on resout via cette box. Couvre le cas ou
  //      l'user a ajoute le pick avec un game_id different (ex: ancien
  //      game_id du jour avant que la box du jour soit dispo).
  function _lookupActual(p){{
    var gid = _bkNorm(p.game_id);
    var pl  = _bkNorm(p.player);
    var pr  = _bkNorm(p.prop);
    // Strategie 1 : exact match dans nba_history
    var key = gid + '|' + pl + '|' + pr;
    var a = actualMap[key];
    if(a !== undefined) return a;
    // Strategie 2 : exact game_id + lookup box score
    var byPlayer = boxByGid[gid];
    var box = _bkFindPlayerBox(byPlayer, p.player);
    if(box){{
      var v = _bkComputePropFromBox(box, p.prop);
      if(v !== undefined) return v;
    }}
    // Strategie 3 : fuzzy date+player (game_id mismatch, mais on a une box
    // pour ce joueur a la bonne date). Safe : on n'accepte que si UN SEUL
    // game ce jour-la contient ce joueur (sinon ambiguite).
    if(p.match_date && p.player){{
      var targetDate = String(p.match_date).slice(0, 10);
      var candidates = [];
      Object.keys(boxByGid).forEach(function(g){{
        var gameDate = dateByGid[g];
        if(!gameDate || gameDate.slice(0, 10) !== targetDate) return;
        var b = _bkFindPlayerBox(boxByGid[g], p.player);
        if(b) candidates.push({{ gid: g, box: b }});
      }});
      if(candidates.length === 1){{
        var v2 = _bkComputePropFromBox(candidates[0].box, p.prop);
        if(v2 !== undefined){{
          if(window._bkDebug) console.log('[bk] fuzzy resolve : pick gid=' + p.game_id + ' resolu via gid=' + candidates[0].gid + ' (date+player match)');
          return v2;
        }}
      }}
    }}
    return undefined;
  }}
  var arr = _loadUserPicks();
  var nResolved = 0;
  var nInvalidated = 0;
  var debug = [];
  // ── PASSE 1 : re-validation des picks deja auto-resolus avec ancienne logique ──
  // Si l'actual qu'on retrouve aujourd'hui via gid exact differe de p.actual, on
  // dévalide (= remet en pending). Couvre les anciens picks resolus avec le
  // mauvais match en playoffs.
  arr.forEach(function(p){{
    if(!p.auto_resolved) return;
    if(p.manual_override) return;
    var newActual = _lookupActual(p);
    if(newActual === undefined){{
      // Plus moyen de retrouver l'actual via gid exact → l'ancienne resolution etait
      // probablement basee sur la fallback player+prop dangereuse. On dévalide.
      p.result = null; p.actual = null; p.resolved_at = null; p.auto_resolved = false;
      nInvalidated++;
    }} else if(parseFloat(newActual) !== parseFloat(p.actual)){{
      // Valeur differente → ancienne resolution sur le mauvais match. On dévalide.
      p.result = null; p.actual = null; p.resolved_at = null; p.auto_resolved = false;
      nInvalidated++;
    }}
  }});
  // ── PASSE 2 : resolution normale (gid exact uniquement) + backfill match_date ──
  arr.forEach(function(p){{
    if(p.result && p.result !== 'PENDING') return;
    if(p.manual_override) return;
    // PUSH auto si joueur DNP (= n'a pas joue, blesse, etc.) -> rembourse
    if(_isDnp(p)){{
      p.result = 'PUSH';
      p.actual = 'DNP';
      p.resolved_at = new Date().toISOString();
      p.auto_resolved = true;
      p.dnp = true;
      nResolved++;
      return;
    }}
    // Backfill match_date depuis history si manquant
    if(!p.match_date){{
      var d = dateByGid[_bkNorm(p.game_id)];
      if(d) p.match_date = d;
    }}
    var actual = _lookupActual(p);
    if(actual === undefined){{
      debug.push({{id: p.id, player: p.player, prop: p.prop, gid: p.game_id, date: p.match_date}});
      return;
    }}
    var line = parseFloat(p.line);
    actual = parseFloat(actual);
    if(isNaN(line) || isNaN(actual)) return;
    var result;
    if(p.direction === 'over') result = actual > line ? 'WIN' : (actual < line ? 'LOSS' : 'PUSH');
    else                       result = actual < line ? 'WIN' : (actual > line ? 'LOSS' : 'PUSH');
    p.result = result;
    p.actual = actual;
    p.resolved_at = new Date().toISOString();
    p.auto_resolved = true;
    nResolved++;
  }});
  if(nResolved > 0 || nInvalidated > 0) _saveUserPicks(arr);
  if(window._bkDebug && (debug.length || nInvalidated)){{
    console.log('[bk] resolved:', nResolved, '/ invalidated:', nInvalidated, '/ unresolved:', debug);
  }}
  // ── PASSE 3 : auto-resolve picks FOOT et TENNIS (shape manual) ────────────
  var nFoot   = _autoResolveFootPicks(arr);
  var nTennis = _autoResolveTennisPicks(arr);
  if(nFoot > 0 || nTennis > 0) _saveUserPicks(arr);
  return nResolved + nFoot + nTennis;
}}

// ── Auto-resolve FOOT user picks ────────────────────────────────────────────
// Match user pick par (match_id + label normalise) contre window.FOOT_HISTORY.
// Si match trouve : applique le result du pick algo a notre pick user.
// Helper foot : strip "Plus de X" / "Moins de X" du debut du label pour
// extraire la "base" du prop (ex: "Plus de 14.5 tirs Rayo Vallecano" -> "tirs Rayo Vallecano")
function _footStripDirLine(label){{
  if(!label) return '';
  return _bkNorm(label).replace(/^(plus de|moins de)\\s+[\\d.,]+\\s+/i, '').trim();
}}
// Parse le 1er nombre dans une string actual ("10 tirs (Rayo)" -> 10, "1-0" -> 1)
function _footParseActualNum(actualStr){{
  if(actualStr == null) return null;
  var m = String(actualStr).match(/^\\s*(\\d+(?:[.,]\\d+)?)/);
  return m ? parseFloat(m[1].replace(',', '.')) : null;
}}

function _autoResolveFootPicks(arr){{
  var foot = window.FOOT_HISTORY || [];
  if(!foot.length) return 0;
  // Index match_id -> label_norm -> entry  ET  match_id -> base_norm -> entry
  var byMatchAndLabel = {{}};
  var byMatchAndBase  = {{}};
  foot.forEach(function(h){{
    var mid = String(h.match_id || '');
    if(!mid) return;
    var lbl  = _bkNorm(h.label || '');
    var base = _footStripDirLine(h.label || '');
    if(!byMatchAndLabel[mid]) byMatchAndLabel[mid] = {{}};
    if(!byMatchAndLabel[mid][lbl]) byMatchAndLabel[mid][lbl] = h;
    if(!byMatchAndBase[mid])  byMatchAndBase[mid]  = {{}};
    if(base && !byMatchAndBase[mid][base]) byMatchAndBase[mid][base] = h;
  }});
  var n = 0;
  arr.forEach(function(p){{
    if(p.result && p.result !== 'PENDING') return;
    if(p.manual_override) return;
    if(String(p.sport || '').toUpperCase() !== 'FOOT') return;
    var mid = String(p.match_id || '');
    if(!mid) return;
    var matchEntries = byMatchAndLabel[mid];
    if(!matchEntries) return;
    var market = _bkNorm(p.market || p.label || '');
    // ── 1. Match exact (label normalise) ───────────────────────────────────
    var h = matchEntries[market];
    if(h && h.result){{
      p.result = h.result;
      p.actual = h.actual || null;
      p.resolved_at = new Date().toISOString();
      p.auto_resolved = true;
      n++;
      return;
    }}
    // ── 2. Match "base" (meme prop, ligne potentiellement differente) ──────
    // Ex: user "Plus de 11.5 tirs Rayo Vallecano", algo "Plus de 14.5 tirs Rayo Vallecano"
    // -> on parse l'actual de l'algo et on applique la direction+line du user.
    var userBase = _footStripDirLine(p.market || p.label || '');
    var baseEntries = byMatchAndBase[mid];
    if(baseEntries && userBase && baseEntries[userBase]){{
      var algoEntry = baseEntries[userBase];
      var actualNum = _footParseActualNum(algoEntry.actual);
      var userLine = parseFloat(p.line);
      if(actualNum != null && !isNaN(userLine) && (p.direction === 'over' || p.direction === 'under')){{
        var newResult;
        if(actualNum === userLine) newResult = 'PUSH';
        else if(p.direction === 'over')  newResult = actualNum > userLine ? 'WIN' : 'LOSS';
        else                             newResult = actualNum < userLine ? 'WIN' : 'LOSS';
        p.result = newResult;
        p.actual = actualNum;
        p.resolved_at = new Date().toISOString();
        p.auto_resolved = true;
        n++;
        return;
      }}
    }}
    // ── 3. Fallback : match partiel (substring) ────────────────────────────
    var keys = Object.keys(matchEntries);
    for(var i = 0; i < keys.length; i++){{
      if(market.indexOf(keys[i]) !== -1 || keys[i].indexOf(market) !== -1){{
        var h2 = matchEntries[keys[i]];
        if(h2 && h2.result){{
          p.result = h2.result;
          p.actual = h2.actual || null;
          p.resolved_at = new Date().toISOString();
          p.auto_resolved = true;
          n++;
        }}
        break;
      }}
    }}
  }});
  return n;
}}

// ── Auto-resolve TENNIS user picks ──────────────────────────────────────────
// match_id user = "tennis_<event_id>" -> on regarde window.TENNIS_RESULTS[event_id].
// Applique la regle selon kind du pick (winner / total_games / set_score).
function _autoResolveTennisPicks(arr){{
  var results = window.TENNIS_RESULTS || {{}};
  if(!Object.keys(results).length) return 0;
  var n = 0;
  arr.forEach(function(p){{
    if(p.result && p.result !== 'PENDING') return;
    if(p.manual_override) return;
    if(String(p.sport || '').toUpperCase() !== 'TENNIS') return;
    var mid = String(p.match_id || '');
    if(!mid.indexOf) return;
    var eid = mid.replace(/^tennis_/, '');
    var res = results[eid];
    if(!res || !res.completed) return;
    var kind = String(p.kind || '').toLowerCase();
    var market = String(p.market || p.label || '');
    var marketNorm = _bkNorm(market);
    var result = null, actual = null;
    if(kind === 'tennis_winner' || marketNorm.indexOf('vainqueur') !== -1){{
      // On compare le nom du winner (home_name/away_name) au nom dans le market
      if(!res.winner) return;
      var winnerName = res[res.winner + '_name'] || '';
      var winnerNorm = _bkNorm(winnerName);
      // Le market est de la forme "Vainqueur : Pablo Carreno Busta" -> extrait apres ":"
      var pickName = market.split(':').slice(1).join(':').trim() || market;
      var pickNorm = _bkNorm(pickName);
      var ok = pickNorm && winnerNorm && (pickNorm === winnerNorm || pickNorm.indexOf(winnerNorm) !== -1 || winnerNorm.indexOf(pickNorm) !== -1);
      result = ok ? 'WIN' : 'LOSS';
      actual = winnerName;
    }} else if(kind === 'tennis_total_games' || (p.line != null && p.direction)){{
      if(res.total_games == null) return;
      var line = parseFloat(p.line);
      var total = parseFloat(res.total_games);
      if(isNaN(line) || isNaN(total)) return;
      if(total === line) result = 'PUSH';
      else if(p.direction === 'over')  result = total > line ? 'WIN' : 'LOSS';
      else                             result = total < line ? 'WIN' : 'LOSS';
      actual = total;
    }} else if(kind === 'tennis_set_score' || marketNorm.indexOf('score sets') !== -1){{
      if(!res.set_score) return;
      // Extrait "0-3" / "2-0" / "3-1" / ... du market
      var m = market.match(/(\\d+)-(\\d+)/);
      if(!m) return;
      var pickScore = m[1] + '-' + m[2];
      // Note : set_score dans TENNIS_RESULTS est home-away. Si dans le market
      // le score est POV joueur favori (peut etre away), on essaie les 2 sens.
      var revScore = m[2] + '-' + m[1];
      var ok = res.set_score === pickScore || res.set_score === revScore;
      result = ok ? 'WIN' : 'LOSS';
      actual = res.set_score;
    }}
    if(result){{
      p.result = result;
      p.actual = actual;
      p.resolved_at = new Date().toISOString();
      p.auto_resolved = true;
      n++;
    }}
  }});
  return n;
}}

// ── Auto-refresh : poll history + box_scores toutes les 60s ──────────────────
// Cron regenere le site toutes les 10 min cote serveur. Cote client, on
// recupere les versions a jour sans avoir a recharger la page.
async function _bkPollHistory(){{
  var changed = false;
  // Fetch history
  try {{
    var resp = await fetch('data/nba_history_min.json?t=' + Date.now(), {{cache: 'no-store'}});
    if(resp.ok){{
      var hist = await resp.json();
      if(Array.isArray(hist)){{
        var prev = window.NBA_HISTORY || [];
        var same = prev.length === hist.length && prev.length > 0
          && prev[prev.length-1].game_id === hist[hist.length-1].game_id
          && prev[prev.length-1].player === hist[hist.length-1].player
          && prev[prev.length-1].actual === hist[hist.length-1].actual;
        if(!same) changed = true;
        window.NBA_HISTORY = hist;
      }}
    }}
  }} catch(e){{}}
  // Fetch box scores (donnees brutes par game_id pour resolution de tout prop)
  try {{
    var rb = await fetch('data/nba_box_scores_min.json?t=' + Date.now(), {{cache: 'no-store'}});
    if(rb.ok){{
      var box = await rb.json();
      if(box && typeof box === 'object'){{
        var prevKeys = Object.keys(window.NBA_BOX_SCORES || {{}}).length;
        var newKeys  = Object.keys(box).length;
        if(newKeys !== prevKeys) changed = true;
        window.NBA_BOX_SCORES = box;
      }}
    }}
  }} catch(e){{}}
  // ── Tennis live scores via ESPN (no Cloudflare, public API) ────────────
  // Fetch direct cote client : resout les paris tennis en temps reel sans
  // attendre le prochain cron. ATP + WTA en 1-2 calls.
  try {{
    var today = new Date();
    var yyyymmdd = today.getFullYear() + ('0'+(today.getMonth()+1)).slice(-2) + ('0'+today.getDate()).slice(-2);
    var yday = new Date(today.getTime() - 86400000);
    var yymd = yday.getFullYear() + ('0'+(yday.getMonth()+1)).slice(-2) + ('0'+yday.getDate()).slice(-2);
    var newResults = Object.assign({{}}, window.TENNIS_RESULTS || {{}});
    var addedAny = false;
    for(var li = 0; li < 2; li++){{
      var league = li === 0 ? 'atp' : 'wta';
      for(var di = 0; di < 2; di++){{
        var d = di === 0 ? yyyymmdd : yymd;
        var url = 'https://site.api.espn.com/apis/site/v2/sports/tennis/' + league + '/scoreboard?dates=' + d;
        try {{
          var r = await fetch(url, {{cache: 'no-store'}});
          if(!r.ok) continue;
          var espn = await r.json();
          var events = espn.events || [];
          for(var e of events){{
            for(var g of (e.groupings || [])){{
              for(var c of (g.competitions || [])){{
                var status = (c.status || {{}}).type || {{}};
                if(!status.completed) continue;
                var comps = c.competitors || [];
                if(comps.length !== 2) continue;
                var home = comps.find(function(x){{ return x.homeAway === 'home'; }}) || comps[0];
                var away = comps.find(function(x){{ return x.homeAway === 'away'; }}) || comps[1];
                var hs = (home.linescores || []).map(function(x){{ return parseInt(x.value || 0); }});
                var as = (away.linescores || []).map(function(x){{ return parseInt(x.value || 0); }});
                var hWon = 0, aWon = 0;
                for(var i = 0; i < Math.min(hs.length, as.length); i++){{
                  if(hs[i] > as[i]) hWon++;
                  else if(as[i] > hs[i]) aWon++;
                }}
                var winner = home.winner ? 'home' : (away.winner ? 'away' : null);
                // Construction d'un eid synthetique (ESPN id different de Odds API)
                // On match aussi via le nom des players dans autoResolveTennisPicks
                var eid = c.id;
                if(!newResults[eid]){{
                  newResults[eid] = {{
                    completed: true,
                    winner: winner,
                    home_name: (home.athlete || {{}}).displayName || '',
                    away_name: (away.athlete || {{}}).displayName || '',
                    set_score: hWon + '-' + aWon,
                    total_games: hs.reduce(function(a,b){{ return a+b; }}, 0) + as.reduce(function(a,b){{ return a+b; }}, 0),
                    source: 'espn_live',
                  }};
                  addedAny = true;
                }}
              }}
            }}
          }}
        }} catch(_e){{}}
      }}
    }}
    if(addedAny){{
      window.TENNIS_RESULTS = newResults;
      changed = true;
    }}
  }} catch(e){{}}

  if(!changed) return;
  var n = autoResolveUserPicks();
  _updateUserPicksCount();
  var bkTab = document.getElementById('sport-userpicks');
  if(bkTab && bkTab.style.display !== 'none' && n > 0){{
    renderUserPicks();
  }}
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
  if(typeof _bkSchedulePush === 'function') _bkSchedulePush();
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
  // 1er point = bankroll initial avant le premier pari ; les suivants = apres chaque resolution
  var pts = [bk];
  var dates = [];
  // Premiere date : si on a au moins un pari, la date 1 jour avant le 1er pari ; sinon aujourd'hui
  if(bets.length > 0){{
    var first = new Date(bets[0].resolved_at);
    first.setDate(first.getDate() - 1);
    dates.push(first.toISOString());
  }} else {{
    dates.push(new Date().toISOString());
  }}
  var v = bk;
  bets.forEach(function(b){{
    v += _bkBetDelta(b);
    pts.push(v);
    dates.push(b.resolved_at);
  }});
  return {{pts: pts, dates: dates}};
}}
// Step "rond" pour graduation Y-axis (1, 2, 5, 10, 20, 50, 100...)
function _bkNiceStep(range, target){{
  if(range <= 0) return 1;
  var rough = range / Math.max(1, target);
  var pow = Math.pow(10, Math.floor(Math.log10(rough)));
  var n = rough / pow;
  var nice;
  if(n < 1.5) nice = 1;
  else if(n < 3) nice = 2;
  else if(n < 7) nice = 5;
  else nice = 10;
  return nice * pow;
}}
function _bkFmtDateShort(iso){{
  if(!iso) return '';
  var d = String(iso).slice(0, 10);
  var m = d.match(/^(\\d{{4}})-(\\d{{2}})-(\\d{{2}})/);
  return m ? (m[3] + '/' + m[2]) : d;
}}
function _bkRenderChart(series, accent, hasResolved){{
  var pts, dates;
  if(Array.isArray(series)){{ pts = series; dates = []; }}
  else {{ pts = (series && series.pts) || []; dates = (series && series.dates) || []; }}
  if(!pts || pts.length < 2){{
    var msg = hasResolved
      ? "Aucun pari résolu sur cette période — change l'intervalle pour voir ta courbe ✨"
      : "Pas encore d'historique — résous quelques paris pour voir ta courbe ✨";
    return '<div style="display:flex;align-items:center;justify-content:center;height:220px;color:var(--bk-text-muted);font-size:13px;text-align:center;padding:0 20px">' + msg + '</div>';
  }}
  // Le chart utilise un viewBox SVG "stretche" (preserveAspectRatio=none) pour
  // remplir tout l'espace horizontal. Les LABELS sont rendus en HTML overlay
  // (positionnes en %) pour ne pas etre deformes par le stretch.
  var W = 800, H = 220;
  var padT = 12, padB = 26, padL = 52, padR = 10;
  var iw = W - padL - padR, ih = H - padT - padB;
  var minV = Math.min.apply(null, pts), maxV = Math.max.apply(null, pts);
  var step = _bkNiceStep(maxV - minV, 4);
  var gridMin = Math.floor(minV / step) * step;
  var gridMax = Math.ceil(maxV / step) * step;
  if(gridMax <= gridMin) gridMax = gridMin + step;
  var range = gridMax - gridMin;
  var n = pts.length;
  var x = function(i){{ return padL + (i / Math.max(1, n - 1)) * iw; }};
  var y = function(v){{ return padT + (1 - (v - gridMin) / range) * ih; }};
  var coords = pts.map(function(v, i){{ return [x(i), y(v)]; }});
  var linePath = coords.map(function(p, i){{ return (i === 0 ? 'M' : 'L') + p[0].toFixed(1) + ',' + p[1].toFixed(1); }}).join(' ');
  var baseY = padT + ih;
  var areaPath = linePath + ' L' + coords[n-1][0].toFixed(1) + ',' + baseY.toFixed(1) + ' L' + coords[0][0].toFixed(1) + ',' + baseY.toFixed(1) + ' Z';
  var endP = coords[n-1];
  var uid = 'bk' + Math.random().toString(36).slice(2, 8);
  // Gridlines SVG (stretchent avec le chart)
  var gridLines = '';
  for(var v = gridMin; v <= gridMax + 0.001; v += step){{
    var yy = y(v);
    gridLines += '<line x1="' + padL + '" x2="' + (W - padR) + '" y1="' + yy.toFixed(1) + '" y2="' + yy.toFixed(1) + '" stroke="rgba(255,255,255,0.05)" stroke-dasharray="2,4" vector-effect="non-scaling-stroke"/>';
  }}
  // Labels Y en HTML (en % de la hauteur, taille pixel constante)
  var yLabelsHtml = '';
  for(var v2 = gridMin; v2 <= gridMax + 0.001; v2 += step){{
    var yy2 = y(v2);
    var topPct = (yy2 / H) * 100;
    var label = (Math.abs(v2) >= 1000 ? (v2 / 1000).toFixed(v2 < 10000 ? 1 : 0) + 'k' : Math.round(v2).toString()) + '€';
    yLabelsHtml += '<div class="bk-chart-ylabel" style="top:' + topPct.toFixed(2) + '%">' + label + '</div>';
  }}
  // Labels X en HTML (en % de la largeur)
  var xLabelsHtml = '';
  if(dates.length === n && n >= 2){{
    var nLabels = Math.min(5, n);
    for(var k = 0; k < nLabels; k++){{
      var idx = Math.round((k / Math.max(1, nLabels - 1)) * (n - 1));
      var xx = x(idx);
      var xPct = (xx / W) * 100;
      var lbl = _bkFmtDateShort(dates[idx]);
      var transform = (k === 0) ? 'transform:translateX(0)' : (k === nLabels - 1 ? 'transform:translateX(-100%)' : 'transform:translateX(-50%)');
      xLabelsHtml += '<div class="bk-chart-xlabel" style="left:' + xPct.toFixed(2) + '%;' + transform + '">' + lbl + '</div>';
    }}
  }}
  return '<div class="bk-chart-stage" style="position:relative;height:220px;width:100%">'
    + '<svg viewBox="0 0 ' + W + ' ' + H + '" preserveAspectRatio="none" style="position:absolute;inset:0;width:100%;height:100%;overflow:visible">'
    +   '<defs>'
    +     '<linearGradient id="' + uid + '-f" x1="0" x2="0" y1="0" y2="1">'
    +       '<stop offset="0%" stop-color="' + accent + '" stop-opacity="0.35"/>'
    +       '<stop offset="60%" stop-color="' + accent + '" stop-opacity="0.06"/>'
    +       '<stop offset="100%" stop-color="' + accent + '" stop-opacity="0"/>'
    +     '</linearGradient>'
    +     '<linearGradient id="' + uid + '-s" x1="0" x2="1" y1="0" y2="0">'
    +       '<stop offset="0%" stop-color="' + accent + '" stop-opacity="0.55"/>'
    +       '<stop offset="100%" stop-color="' + accent + '" stop-opacity="1"/>'
    +     '</linearGradient>'
    +   '</defs>'
    +   gridLines
    +   '<path d="' + areaPath + '" fill="url(#' + uid + '-f)"/>'
    +   '<path d="' + linePath + '" fill="none" stroke="url(#' + uid + '-s)" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round" class="bk-chart-line" vector-effect="non-scaling-stroke"/>'
    +   '<circle cx="' + endP[0].toFixed(1) + '" cy="' + endP[1].toFixed(1) + '" r="6" fill="' + accent + '" fill-opacity="0.25" vector-effect="non-scaling-stroke"/>'
    +   '<circle cx="' + endP[0].toFixed(1) + '" cy="' + endP[1].toFixed(1) + '" r="3.5" fill="' + accent + '" vector-effect="non-scaling-stroke"/>'
    + '</svg>'
    + yLabelsHtml
    + xLabelsHtml
    + '</div>';
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
  // Detection : pick manuel (event/market) vs pick NBA (player/prop/direction/line)
  // Manual = pick generique (foot/tennis/etc avec market texte libre) SAUF si NBA-shape
  // detecte (player + prop). Les picks NBA manuels gardent le rendu NBA-style.
  var hasNbaShape = !!(p.player && p.prop && p.direction);
  var isManual = !hasNbaShape && (p.source === 'manual' || (!!p.event && !!p.market));
  var title, match, sportIcon;
  if(isManual){{
    title = (p.market || '?').replace(/</g, '&lt;');
    // N'append la ligne QUE si le market ne la contient pas deja
    // (ex algo : market="Plus de 11.5 tirs Rayo Vallecano" + line=11.5 -> pas de double).
    if(p.line != null && p.line !== '' && title.indexOf(String(p.line)) === -1){{
      title += ' ' + p.line;
    }}
    match = (p.event || '').replace(/</g, '&lt;');
    var SPORT_EMOJI = {{FOOT:'⚽', NBA:'🏀', TENNIS:'🎾', RUGBY:'🏉', MMA:'🥊', NFL:'🏈', F1:'🏎️', OTHER:'🎲'}};
    sportIcon = SPORT_EMOJI[(p.sport || 'OTHER').toUpperCase()] || '🎲';
  }} else {{
    var propLabel = ({{PTS:'pts',REB:'reb',AST:'pas',FG3M:'3PM',RA:'reb+pas',PR:'pts+reb',PA:'pts+ast',PRA:'PRA'}})[p.prop] || p.prop;
    var dir = p.direction === 'over' ? 'plus de' : 'moins de';
    title = (p.player || '?') + ' ' + dir + ' ' + p.line + ' ' + propLabel;
    match = (p.away || '') + ' @ ' + (p.home || '');
    sportIcon = '🏀';
  }}
  // Affiche la stat reelle si dispo (apres resolution) — ex "→ réel 17"
  if(p.actual !== null && p.actual !== undefined){{
    var actNum = parseFloat(p.actual);
    var actStr = isNaN(actNum) ? p.actual : (Math.abs(actNum % 1) < 0.05 ? actNum.toFixed(0) : actNum.toFixed(1));
    var actClr = p.result === 'WIN' ? '#34D399' : (p.result === 'LOSS' ? '#F87171' : '#94A3B8');
    title += ' <span style="color:' + actClr + ';font-weight:700">→ réel ' + actStr + '</span>';
  }}
  var badge = '';
  if(!p.result || p.result === 'PENDING'){{
    // Date-aware : "A venir" si match futur, "En cours" si aujourd'hui,
    // "En attente" si match passe mais pas encore resolu (cron pas encore tourne)
    var pendingLabel = 'En cours';
    if(p.match_date){{
      var today = new Date();
      var pad = function(n){{ return n < 10 ? '0' + n : '' + n; }};
      var todayStr = today.getFullYear() + '-' + pad(today.getMonth()+1) + '-' + pad(today.getDate());
      var md = String(p.match_date).slice(0, 10);
      if(md > todayStr)      pendingLabel = '⏳ À venir';
      else if(md < todayStr) pendingLabel = '⏱ En attente';
    }}
    badge = '<span class="bk-badge pending"><span class="dot"></span>' + pendingLabel + '</span>';
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
    + '<button class="bk-mini-btn edit" onclick="_bkOpenEditForm(\\'' + p.id + '\\')" title="Modifier le pari (cote, mise, date, statut, tipster, note...)">✏</button>'
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
  // Date du match (critique en playoffs : memes equipes tous les 2j → on doit voir
  // visuellement de quel match il s'agit)
  var dateHtml = '';
  if(p.match_date){{
    var d = String(p.match_date);
    // Format compact JJ/MM si on a un YYYY-MM-DD
    var mm = d.match(/^(\\d{{4}})-(\\d{{2}})-(\\d{{2}})/);
    var pretty = mm ? (mm[3] + '/' + mm[2]) : d;
    dateHtml = '<span class="dot">·</span><span style="color:#fbbf24;font-weight:600" title="Date du match">📅 ' + pretty + '</span>';
  }}
  return '<div class="bk-row">'
    + '<div class="bk-row-icon">' + sportIcon + '</div>'
    + '<div class="bk-row-main">'
    + '<div class="bk-row-title">' + title + '</div>'
    + '<div class="bk-row-sub"><span>' + match + '</span>' + dateHtml + '<span class="dot">·</span>' + coteChip
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
  window._bkFilters = window._bkFilters || {{status:'all', tipster:'all', date:'all', prop:'all'}};
  window._bkFilters[kind] = value;
  // Garde la section depliee quand on selectionne un filtre non-default
  window._bkFilterExpand = window._bkFilterExpand || {{status:false, date:false, tipster:false}};
  if(value !== 'all' && kind !== 'prop') window._bkFilterExpand[kind] = true;
  renderUserPicks();
  // Scroll vers l'historique quand on clique sur un prop (UX : on voit direct le filtre)
  if(kind === 'prop' && value !== 'all'){{
    setTimeout(function(){{
      var el = document.querySelector('#sport-userpicks .bk-card .bk-card-title');
      // Trouve la card Historique
      var cards = document.querySelectorAll('#sport-userpicks .bk-card');
      for(var i = 0; i < cards.length; i++){{
        if(cards[i].textContent.indexOf('Historique') === 0){{
          cards[i].scrollIntoView({{behavior:'smooth', block:'start'}});
          break;
        }}
      }}
    }}, 50);
  }}
}}
function _bkToggleFilterExpand(kind){{
  window._bkFilterExpand = window._bkFilterExpand || {{status:false, date:false, tipster:false}};
  window._bkFilterExpand[kind] = !window._bkFilterExpand[kind];
  renderUserPicks();
}}
// Toggle l'analyse aggregee (par tipster / par date) quand l'user clique sur
// la chip "Tous X" deja active
function _bkToggleAnalyse(kind){{
  window._bkAnalyseOpen = window._bkAnalyseOpen || {{tipster:false, date:false}};
  window._bkAnalyseOpen[kind] = !window._bkAnalyseOpen[kind];
  // Synchronise l'etat collapse/expand des chips : ouvert quand l'analyse est ouverte
  window._bkFilterExpand = window._bkFilterExpand || {{status:false, date:false, tipster:false}};
  window._bkFilterExpand[kind] = window._bkAnalyseOpen[kind];
  // Reset le filtre actif vers 'all' (l'analyse couvre l'ensemble)
  window._bkFilters = window._bkFilters || {{status:'all', tipster:'all', date:'all', prop:'all'}};
  window._bkFilters[kind] = 'all';
  renderUserPicks();
  if(window._bkAnalyseOpen[kind]){{
    setTimeout(function(){{
      var id = 'bk-analyse-' + kind;
      var el = document.getElementById(id);
      if(el) el.scrollIntoView({{behavior:'smooth', block:'start'}});
    }}, 50);
  }}
}}
function resetBkFilters(){{
  window._bkFilters = {{status:'all', tipster:'all', date:'all', prop:'all'}};
  window._bkFilterExpand = {{status:false, date:false, tipster:false}};
  renderUserPicks();
}}
// Toggle expand/collapse de listes ("Paris en cours" / "Historique")
function _bkToggleMore(prefix){{
  window._bkExpanded = window._bkExpanded || {{}};
  window._bkExpanded[prefix] = !window._bkExpanded[prefix];
  var rowsEl = document.getElementById('bk-more-' + prefix);
  var btn    = document.getElementById('bk-more-btn-' + prefix);
  if(!rowsEl || !btn) return;
  var open = window._bkExpanded[prefix];
  rowsEl.classList.toggle('open', open);
  var n = parseInt(btn.dataset.hidden || '0', 10);
  btn.innerHTML = open ? '▲ Réduire' : ('▼ Afficher les ' + n + ' autres');
}}
// Filtre date : retourne la date YYYY-MM-DD pertinente du pick (resolved_at sinon match_date sinon created)
function _bkPickDay(p){{
  var d = p.resolved_at || p.match_date || p.created || '';
  return String(d).slice(0, 10);
}}
function _bkPickInDateRange(p, kind){{
  if(!kind || kind === 'all') return true;
  var day = _bkPickDay(p);
  if(!day) return false;
  var now = new Date();
  var pad = function(n){{ return n < 10 ? '0' + n : '' + n; }};
  var fmt = function(d){{ return d.getFullYear() + '-' + pad(d.getMonth() + 1) + '-' + pad(d.getDate()); }};
  var today = fmt(now);
  if(kind === 'today') return day === today;
  if(kind === 'yesterday'){{
    var y = new Date(now); y.setDate(y.getDate() - 1);
    return day === fmt(y);
  }}
  var dt = new Date(day + 'T12:00:00');
  var diffDays = (now - dt) / 86400000;
  if(kind === '7d')  return diffDays <= 7  && diffDays >= -1;
  if(kind === '30d') return diffDays <= 30 && diffDays >= -1;
  return true;
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
    +   '<div style="display:flex;gap:6px;flex-wrap:wrap;justify-content:flex-end">'
    +     '<button class="bk-btn" onclick="_bkOpenManualBetForm()" title="Ajouter un pari manuellement (passe ou recent)" style="background:linear-gradient(180deg,#34D399,#10B981);color:#06120E;border:none;font-weight:700;box-shadow:0 4px 12px rgba(52,211,153,0.25)">➕ Ajouter un pari</button>'
    +     '<button class="bk-btn bk-btn-ghost" onclick="editBankroll()" title="Modifier le bankroll initial">⚙ ' + bk.toFixed(0) + ' € initial</button>'
    +   '</div>'
    +   '<div style="font-size:12px;font-variant-numeric:tabular-nums;color:' + totalCls + ';font-weight:600;margin-top:6px">'
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

  // ── Paris en cours (premiers 5 visibles, reste deroulable) ─────
  window._bkExpanded = window._bkExpanded || {{pending:false, history:false}};
  var PENDING_LIMIT = 5;
  var pendingCard = '';
  if(pending.length > 0){{
    var visibleP = pending.slice(0, PENDING_LIMIT).map(_bkRowHtml).join('');
    var hiddenP  = pending.slice(PENDING_LIMIT);
    var hiddenPHtml = hiddenP.length
      ? '<div class="bk-more-rows ' + (window._bkExpanded.pending ? 'open' : '') + '" id="bk-more-pending">' + hiddenP.map(_bkRowHtml).join('') + '</div>'
        + '<button class="bk-more-btn" id="bk-more-btn-pending" data-hidden="' + hiddenP.length + '" onclick="_bkToggleMore(\\'pending\\')">'
        + (window._bkExpanded.pending ? '▲ Réduire' : '▼ Afficher les ' + hiddenP.length + ' autres')
        + '</button>'
      : '';
    pendingCard =
      '<div class="bk-card">'
      + '<div class="bk-card-hd">'
      +   '<div class="bk-card-title">Paris en cours <span style="padding:2px 9px;border-radius:999px;background:rgba(251,191,36,0.14);color:#FBBF24;font-size:11px;font-weight:700">' + pending.length + '</span></div>'
      + '</div>'
      + '<div class="bk-rows">' + visibleP + '</div>'
      + hiddenPHtml
      + '</div>';
  }}

  // ── Historique (filtres status + tipster + date + prop + collapse) ─────────
  window._bkFilters = window._bkFilters || {{status:'all', tipster:'all', date:'all', prop:'all'}};
  if(!window._bkFilters.date) window._bkFilters.date = 'all';
  if(!window._bkFilters.prop) window._bkFilters.prop = 'all';
  var fStatus  = window._bkFilters.status;
  var fTipster = window._bkFilters.tipster;
  var fDate    = window._bkFilters.date;
  var fPropFilter = window._bkFilters.prop;
  // Tous les paris (pending inclus) pour filtrage
  var allForFilter = arr.slice();
  // Counts par status (sur la base globale)
  var cntAll     = allForFilter.length;
  var cntPending = pending.length;
  var cntWon     = wins;
  var cntLost    = losses;
  var cntPush    = resolved.filter(function(p){{ return p.result === 'PUSH'; }}).length;
  // Counts par date (sur la base globale)
  var cntToday     = allForFilter.filter(function(p){{ return _bkPickInDateRange(p, 'today'); }}).length;
  var cntYesterday = allForFilter.filter(function(p){{ return _bkPickInDateRange(p, 'yesterday'); }}).length;
  var cnt7d        = allForFilter.filter(function(p){{ return _bkPickInDateRange(p, '7d'); }}).length;
  var cnt30d       = allForFilter.filter(function(p){{ return _bkPickInDateRange(p, '30d'); }}).length;
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
    if(!_bkPickInDateRange(p, fDate)) return false;
    if(fPropFilter !== 'all'){{
      var propP = String(p.prop || '').toUpperCase();
      if(propP !== fPropFilter) return false;
    }}
    return true;
  }});
  // State expand/collapse des 3 sections de filtres (par defaut collapsed)
  window._bkFilterExpand = window._bkFilterExpand || {{status:false, date:false, tipster:false}};
  // Build chip generique : peut avoir une fleche de collapse a gauche (si c'est la summary)
  function _chip(args){{
    // args = id, label, count, color (opt), ic (opt), kind, active, isToggle
    // Le chip "Tous X" pour tipster/date est special : il ouvre l'analyse aggregee
    var isAllAnalyse = args.id === 'all' && (args.kind === 'tipster' || args.kind === 'date');
    var analyseOpen  = isAllAnalyse && window._bkAnalyseOpen && window._bkAnalyseOpen[args.kind];
    var caret = '';
    if(isAllAnalyse){{
      caret = '<span style="font-size:11px;margin-right:2px">' + (analyseOpen ? '📊' : '▾') + '</span>';
    }} else if(args.isToggle){{
      caret = '<span style="font-size:9px;opacity:0.8;margin-right:2px">' + (window._bkFilterExpand[args.kind] ? '▴' : '▾') + '</span>';
    }}
    var dot = args.color ? '<span class="dot" style="background:' + args.color + '"></span>' : '';
    var ic = args.ic ? '<span style="font-size:13px">' + args.ic + '</span>' : '';
    var labelHtml = args.maxWidth
      ? '<span style="max-width:' + args.maxWidth + 'px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">' + args.label + '</span>'
      : args.label;
    var onclick;
    if(isAllAnalyse){{
      onclick = '_bkToggleAnalyse(\\'' + args.kind + '\\')';
    }} else if(args.isToggle){{
      onclick = '_bkToggleFilterExpand(\\'' + args.kind + '\\')';
    }} else {{
      onclick = 'setBkFilter(\\'' + args.kind + '\\', \\'' + String(args.id).replace(/'/g, "\\\\'") + '\\')';
    }}
    var title = args.title
      ? ' title="' + args.title + '"'
      : (isAllAnalyse ? ' title="Voir l\\'analyse détaillée par ' + args.kind + '"' : '');
    var activeClass = (args.active || analyseOpen) ? 'active' : '';
    var extraStyle = analyseOpen ? ' style="background:rgba(52,211,153,0.18);border-color:rgba(52,211,153,0.55)"' : '';
    return '<button class="bk-filter-chip ' + activeClass + '" onclick="' + onclick + '"' + title + extraStyle + '>'
      + caret + dot + ic + labelHtml
      + (args.count != null ? '<span class="count">' + args.count + '</span>' : '')
      + '</button>';
  }}
  function _buildFilterRow(defs, kind, currentFilter){{
    var expanded = window._bkFilterExpand[kind];
    var activeDef = null;
    for(var i = 0; i < defs.length; i++){{ if(defs[i].id === currentFilter){{ activeDef = defs[i]; break; }} }}
    if(!activeDef) activeDef = defs[0];
    if(!expanded){{
      // Collapsed : montre uniquement la chip active avec fleche ▾
      return _chip({{
        id: activeDef.id, label: activeDef.label, count: activeDef.count,
        color: activeDef.color, ic: activeDef.ic, kind: kind,
        active: true, isToggle: true, maxWidth: activeDef.maxWidth, title: activeDef.title
      }});
    }}
    // Expanded : montre toutes les chips, l'active a la fleche pour collapse
    return defs.map(function(d){{
      var isActive = d.id === currentFilter;
      return _chip({{
        id: d.id, label: d.label, count: d.count,
        color: d.color, ic: d.ic, kind: kind,
        active: isActive, isToggle: isActive, maxWidth: d.maxWidth, title: d.title
      }});
    }}).join('');
  }}

  // Status defs
  var statusDefs = [
    {{id:'all',     label:'Tous',     count:cntAll}},
    {{id:'pending', label:'En cours', count:cntPending, color:'#FBBF24'}},
    {{id:'won',     label:'Gagnés',   count:cntWon,     color:'#34D399'}},
    {{id:'lost',    label:'Perdus',   count:cntLost,    color:'#F87171'}},
    {{id:'push',    label:'Annulés',  count:cntPush,    color:'#94A3B8'}},
  ];
  var statusChips = _buildFilterRow(statusDefs, 'status', fStatus);

  // Date defs
  var dateDefs = [
    {{id:'all',       label:'Toutes dates',  count:cntAll,       ic:'📅'}},
    {{id:'today',     label:"Aujourd'hui",   count:cntToday,     ic:'🟢'}},
    {{id:'yesterday', label:'Hier',          count:cntYesterday, ic:'🟡'}},
    {{id:'7d',        label:'7 derniers j.', count:cnt7d,        ic:'📆'}},
    {{id:'30d',       label:'30 derniers j.',count:cnt30d,       ic:'📚'}},
  ];
  var dateChips = _buildFilterRow(dateDefs, 'date', fDate);

  // Tipster defs (avec virtual "all" + chaque tipster)
  var tipsterDefs = [{{id:'all', label:'Tous tipsters', count:tipsterList.length, ic:'👥'}}];
  tipsterList.forEach(function(t){{
    var isNone = t === '∅ Sans tipster';
    tipsterDefs.push({{
      id: t,
      label: isNone ? 'Sans tipster' : t,
      count: tipsterCounts[t],
      ic: isNone ? '∅' : '👤',
      maxWidth: 120,
      title: t,
    }});
  }});
  var tipsterChips = _buildFilterRow(tipsterDefs, 'tipster', fTipster);
  var resetBtn = (fStatus !== 'all' || fTipster !== 'all' || fDate !== 'all' || fPropFilter !== 'all')
    ? '<button class="bk-filter-reset" onclick="resetBkFilters()">Réinitialiser ✕</button>'
    : '';
  // ── P&L pour le filtre actif ──────────────────────────
  // Quand un filtre est actif, on affiche le profit/perte cumule sur les picks
  // resolus dans le subset filtre (utile pour voir "bilan du jour" / "bilan
  // vs PronoKing" / "bilan Reb+Pas" etc en un coup d'oeil).
  var anyFilterActive = (fStatus !== 'all' || fTipster !== 'all' || fDate !== 'all' || fPropFilter !== 'all');
  var filterPnlChip = '';
  if(anyFilterActive){{
    var fSettled = filteredArr.filter(function(p){{
      return p.result && p.result !== 'PENDING' && p.result !== 'PUSH';
    }});
    var fProfit = 0;
    var fStake  = 0;
    var fWins = 0, fLosses = 0;
    fSettled.forEach(function(p){{
      var st = (p.stake != null) ? p.stake : 1;
      fStake += st;
      fProfit += _bkBetDelta(p);
      if(p.result === 'WIN')  fWins++;
      if(p.result === 'LOSS') fLosses++;
    }});
    var fPushes = filteredArr.filter(function(p){{ return p.result === 'PUSH'; }}).length;
    var fPending = filteredArr.filter(function(p){{ return !p.result || p.result === 'PENDING'; }}).length;
    if(fSettled.length > 0 || fPending > 0){{
      var clr = fProfit > 0 ? '#34D399' : (fProfit < 0 ? '#F87171' : '#94A3B8');
      var sign = fProfit > 0 ? '+' : (fProfit < 0 ? '−' : '');
      var roi = fStake > 0 ? (fProfit / fStake * 100) : 0;
      var roiSign = roi > 0 ? '+' : (roi < 0 ? '−' : '');
      var pendingChip = fPending > 0
        ? '<span style="margin-left:8px;color:#FBBF24;font-weight:500;font-size:11.5px">· ⏱ ' + fPending + ' en cours</span>'
        : '';
      filterPnlChip =
        '<div style="margin:2px 0 12px;padding:10px 14px;border-radius:12px;'
        + 'background:linear-gradient(90deg, ' + clr + '14 0%, ' + clr + '04 100%);'
        + 'border:1px solid ' + clr + '38;display:flex;align-items:center;gap:12px;flex-wrap:wrap">'
        +   '<div style="font-size:11.5px;font-weight:700;color:#94A3B8;text-transform:uppercase;letter-spacing:0.4px">📊 Bilan du filtre</div>'
        +   '<div style="font-size:18px;font-weight:800;color:' + clr + ';font-variant-numeric:tabular-nums">' + sign + _bkFmt(Math.abs(fProfit)) + ' €</div>'
        +   '<div style="font-size:11.5px;color:#94A3B8;font-weight:600">'
        +     'ROI <span style="color:' + clr + ';font-weight:700">' + roiSign + Math.abs(roi).toFixed(1) + '%</span>'
        +     ' · ' + fSettled.length + ' resolus (' + fWins + 'W·' + fLosses + 'L'
        +     (fPushes > 0 ? '·' + fPushes + 'P' : '') + ')'
        +   '</div>'
        +   pendingChip
        + '</div>';
    }}
  }}
  // Chip "Filtré par prop" affichée a cote du titre Historique si un prop filter est actif
  var propFilterChip = '';
  if(fPropFilter !== 'all'){{
    var fpLabel = ({{PTS:'Points', REB:'Rebonds', AST:'Passes', FG3M:'3-points', PR:'Pts+Reb', PA:'Pts+Pas', PRA:'PRA', RA:'Reb+Pas'}})[fPropFilter] || fPropFilter;
    var fpIcon  = ({{PTS:'🎯', REB:'🛟', AST:'🎁', FG3M:'🏹', PR:'🎯', PA:'🎁', PRA:'🌟', RA:'🛟'}})[fPropFilter] || '📊';
    propFilterChip = '<span style="display:inline-flex;align-items:center;gap:6px;margin-left:10px;padding:3px 10px;border-radius:999px;background:rgba(52,211,153,0.14);color:#34D399;font-size:11.5px;font-weight:700;cursor:pointer" onclick="setBkFilter(\\'prop\\', \\'all\\')" title="Cliquer pour retirer ce filtre">' + fpIcon + ' ' + fpLabel + ' ✕</span>';
  }}

  var recentCard = '';
  if(allForFilter.length > 0){{
    var HISTORY_LIMIT = 5;
    var hiddenH = filteredArr.slice(HISTORY_LIMIT);
    var rowsBlock;
    if(filteredArr.length === 0){{
      rowsBlock = '<div class="bk-filter-empty">🔍 Aucun pari ne correspond aux filtres.</div>';
    }} else {{
      var visibleH = filteredArr.slice(0, HISTORY_LIMIT).map(_bkRowHtml).join('');
      var hiddenHtml = hiddenH.length
        ? '<div class="bk-more-rows ' + (window._bkExpanded.history ? 'open' : '') + '" id="bk-more-history">' + hiddenH.map(_bkRowHtml).join('') + '</div>'
          + '<button class="bk-more-btn" id="bk-more-btn-history" data-hidden="' + hiddenH.length + '" onclick="_bkToggleMore(\\'history\\')">'
          + (window._bkExpanded.history ? '▲ Réduire' : '▼ Afficher les ' + hiddenH.length + ' autres')
          + '</button>'
        : '';
      rowsBlock = '<div class="bk-rows">' + visibleH + '</div>' + hiddenHtml;
    }}
    recentCard =
      '<div class="bk-card">'
      + '<div class="bk-card-hd">'
      +   '<div class="bk-card-title">Historique <span style="padding:2px 9px;border-radius:999px;background:var(--bk-text-soft);color:var(--bk-text-muted);font-size:11px;font-weight:700">' + filteredArr.length + '</span>' + propFilterChip + '</div>'
      +   resetBtn
      + '</div>'
      + '<div class="bk-filter-row">' + statusChips + '</div>'
      + '<div class="bk-filter-row">' + dateChips + '</div>'
      + (tipsterList.length > 0 ? '<div class="bk-filter-row">' + tipsterChips + '</div>' : '')
      + filterPnlChip
      + rowsBlock
      + '</div>';
  }}

  // ── Performance par marché ──────────────────────────
  var byProp = {{}};
  settled.forEach(function(p){{
    var k = p.prop || '?';
    if(!byProp[k]) byProp[k] = {{w: 0, l: 0, profit: 0, cotes: 0, nCotes: 0}};
    if(p.result === 'WIN')  byProp[k].w++;
    if(p.result === 'LOSS') byProp[k].l++;
    byProp[k].profit += _bkBetDelta(p);
    if(p.cote){{ byProp[k].cotes += p.cote; byProp[k].nCotes++; }}
  }});
  var propEntries = Object.keys(byProp).sort(function(a, b){{ return byProp[b].profit - byProp[a].profit; }});
  var fProp = (window._bkFilters && window._bkFilters.prop) || 'all';
  var propRows = propEntries.map(function(k){{
    var b = byProp[k];
    var bn = b.w + b.l;
    var bwr = bn > 0 ? (b.w / bn * 100) : 0;
    var avgCoteK = b.nCotes > 0 ? (b.cotes / b.nCotes) : 0;
    var barColor = bwr >= 55 ? '#34D399' : (bwr >= 50 ? '#86efac' : (bwr >= 40 ? '#FBBF24' : '#F87171'));
    var pColor = b.profit > 0 ? '#34D399' : (b.profit < 0 ? '#F87171' : '#94A3B8');
    var pSign  = b.profit > 0 ? '+' : (b.profit < 0 ? '−' : '');
    var propIcon  = ({{PTS:'🎯', REB:'🛟', AST:'🎁', FG3M:'🏹', PR:'🎯', PA:'🎁', PRA:'🌟', RA:'🛟'}})[k] || '📊';
    var propLabel = ({{PTS:'Points', REB:'Rebonds', AST:'Passes', FG3M:'3-points', PR:'Pts+Reb', PA:'Pts+Pas', PRA:'PRA', RA:'Reb+Pas'}})[k] || k;
    var isActive = fProp === k;
    var nextVal = isActive ? 'all' : k;   // click sur active = unselect
    var activeStyle = isActive
      ? 'background:rgba(52,211,153,0.10);border:1px solid rgba(52,211,153,0.35);border-radius:10px;padding:8px 6px;'
      : '';
    return '<div class="bk-prop-row" onclick="setBkFilter(\\'prop\\', \\'' + nextVal + '\\')" '
      + 'style="cursor:pointer;' + activeStyle + '" '
      + 'title="' + (isActive ? 'Cliquer pour retirer le filtre' : 'Cliquer pour filtrer l\\'historique sur ' + propLabel) + '">'
      + '<div class="bk-prop-name"><span>' + propIcon + '</span><span>' + propLabel + '</span>' + (isActive ? ' <span style="color:#34D399;font-size:11px">✓</span>' : '') + '</div>'
      + '<div class="bk-prop-bar"><div style="width:' + bwr + '%;background:linear-gradient(90deg,' + barColor + '88,' + barColor + ')"></div></div>'
      + '<div class="bk-prop-wr" style="color:' + barColor + '">' + bwr.toFixed(0) + '%<span style="color:var(--bk-text-muted);font-weight:500;font-size:11px"> (' + b.w + '/' + bn + ')</span></div>'
      + '<div class="bk-prop-cote" title="Cote moyenne">' + (avgCoteK > 0 ? '@' + avgCoteK.toFixed(2) : '—') + '</div>'
      + '<div class="bk-prop-profit" style="color:' + pColor + '">' + pSign + _bkFmt(Math.abs(b.profit)) + ' €</div>'
      + '</div>';
  }}).join('');
  // Header row
  var propHeader = '<div class="bk-prop-row" style="padding:4px 4px 6px;border-bottom:1px solid var(--bk-hairline);font-size:10.5px;color:var(--bk-text-muted);font-weight:600;text-transform:uppercase;letter-spacing:0.4px">'
    + '<div>Marché</div>'
    + '<div></div>'
    + '<div style="text-align:right">WR</div>'
    + '<div style="text-align:right">Cote</div>'
    + '<div style="text-align:right">Profit</div>'
    + '</div>';
  var propCard = '';
  if(propRows){{
    propCard =
      '<div class="bk-card">'
      + '<div class="bk-card-hd"><div class="bk-card-title">📊 Performance par marché</div></div>'
      + propHeader
      + propRows
      + '</div>';
  }}

  // ── Helpers analyse aggregee (tipster / date) ──────────────
  window._bkAnalyseOpen = window._bkAnalyseOpen || {{tipster:false, date:false}};
  function _bkBuildAnalyseRows(buckets, totalLabel){{
    // buckets : array de {{label, ic, picks: [...], color (opt)}}
    var allRows = buckets.map(function(b){{
      var s = {{w:0, l:0, push:0, pending:0, profit:0, stake:0, cotes:0, nCotes:0}};
      b.picks.forEach(function(p){{
        if(!p.result || p.result === 'PENDING'){{ s.pending++; return; }}
        if(p.result === 'PUSH'){{ s.push++; return; }}
        if(p.result === 'WIN')  s.w++;
        if(p.result === 'LOSS') s.l++;
        s.profit += _bkBetDelta(p);
        s.stake  += (p.stake != null ? p.stake : 1);
        if(p.cote){{ s.cotes += p.cote; s.nCotes++; }}
      }});
      s.label = b.label; s.ic = b.ic || ''; s.color = b.color || '';
      s.n = s.w + s.l;
      s.wr = s.n > 0 ? (s.w / s.n * 100) : 0;
      s.roi = s.stake > 0 ? (s.profit / s.stake * 100) : 0;
      s.avgCote = s.nCotes > 0 ? (s.cotes / s.nCotes) : 0;
      s.total = b.picks.length;
      return s;
    }});
    // Sort par profit decroissant (mais on garde ordre pour buckets de date qui sont ordonnes)
    return allRows;
  }}
  function _bkAnalyseHeader(label1){{
    return '<div class="bk-analyse-row bk-analyse-hd">'
      + '<div>' + label1 + '</div>'
      + '<div style="text-align:right">Picks</div>'
      + '<div style="text-align:right">WR</div>'
      + '<div style="text-align:right">Cote</div>'
      + '<div style="text-align:right">ROI</div>'
      + '<div style="text-align:right">Profit</div>'
      + '</div>';
  }}
  function _bkAnalyseRow(s){{
    var barColor = s.wr >= 55 ? '#34D399' : (s.wr >= 50 ? '#86efac' : (s.wr >= 40 ? '#FBBF24' : (s.n > 0 ? '#F87171' : '#94A3B8')));
    var pColor   = s.profit > 0 ? '#34D399' : (s.profit < 0 ? '#F87171' : '#94A3B8');
    var pSign    = s.profit > 0 ? '+' : (s.profit < 0 ? '−' : '');
    var rColor   = s.roi > 0 ? '#34D399' : (s.roi < 0 ? '#F87171' : '#94A3B8');
    var rSign    = s.roi > 0 ? '+' : (s.roi < 0 ? '−' : '');
    var wrCell   = s.n > 0
      ? '<span style="color:' + barColor + '">' + s.wr.toFixed(0) + '%</span><span style="color:var(--bk-text-muted);font-weight:500;font-size:11px"> (' + s.w + '/' + s.n + ')</span>'
      : '<span style="color:var(--bk-text-muted)">—</span>';
    return '<div class="bk-analyse-row">'
      + '<div class="bk-analyse-name">' + (s.ic ? '<span style="font-size:13px">' + s.ic + '</span>' : '') + '<span>' + s.label + '</span></div>'
      + '<div class="bk-analyse-num" style="color:var(--bk-text-muted)">' + s.total + (s.pending > 0 ? ' <span style="color:#FBBF24;font-size:10.5px">(' + s.pending + ' en cours)</span>' : '') + '</div>'
      + '<div class="bk-analyse-num">' + wrCell + '</div>'
      + '<div class="bk-analyse-num" style="color:var(--bk-text-muted)">' + (s.avgCote > 0 ? '@' + s.avgCote.toFixed(2) : '—') + '</div>'
      + '<div class="bk-analyse-num" style="color:' + rColor + '">' + (s.n > 0 ? rSign + Math.abs(s.roi).toFixed(1) + '%' : '—') + '</div>'
      + '<div class="bk-analyse-num" style="color:' + pColor + ';font-weight:700">' + (s.n > 0 ? pSign + _bkFmt(Math.abs(s.profit)) + ' €' : '—') + '</div>'
      + '</div>';
  }}

  // ── Analyse par tipster (visible uniquement si toggle ON) ──
  var tipsterAnalyseCard = '';
  if(window._bkAnalyseOpen.tipster){{
    var tipBuckets = tipsterList.map(function(t){{
      var picks = allForFilter.filter(function(p){{
        var tt = (p.tipster && p.tipster.trim()) ? p.tipster.trim() : '∅ Sans tipster';
        return tt === t;
      }});
      var isNone = t === '∅ Sans tipster';
      return {{label: isNone ? 'Sans tipster' : t, ic: isNone ? '∅' : '👤', picks: picks}};
    }});
    var tipRows = _bkBuildAnalyseRows(tipBuckets, 'Total');
    // Sort : profit desc, puis n picks desc
    tipRows.sort(function(a, b){{
      if(b.profit !== a.profit) return b.profit - a.profit;
      return b.total - a.total;
    }});
    var tipBody = tipRows.map(_bkAnalyseRow).join('');
    tipsterAnalyseCard =
      '<div class="bk-card" id="bk-analyse-tipster">'
      + '<div class="bk-card-hd">'
      +   '<div class="bk-card-title">👥 Performance par tipster</div>'
      +   '<button class="bk-card-close" onclick="_bkToggleAnalyse(\\'tipster\\')" title="Fermer">✕</button>'
      + '</div>'
      + (tipBody
          ? _bkAnalyseHeader('Tipster') + tipBody
          : '<div style="padding:16px;color:var(--bk-text-muted);text-align:center;font-size:13px">Aucun tipster enregistré.</div>')
      + '</div>';
  }}

  // ── Analyse par date (visible uniquement si toggle ON) ─────
  var dateAnalyseCard = '';
  if(window._bkAnalyseOpen.date){{
    var dateBuckets = [
      {{label:"Aujourd'hui",     ic:'🟢', picks: allForFilter.filter(function(p){{ return _bkPickInDateRange(p, 'today'); }})}},
      {{label:'Hier',            ic:'🟡', picks: allForFilter.filter(function(p){{ return _bkPickInDateRange(p, 'yesterday'); }})}},
      {{label:'7 derniers j.',   ic:'📆', picks: allForFilter.filter(function(p){{ return _bkPickInDateRange(p, '7d'); }})}},
      {{label:'30 derniers j.',  ic:'📚', picks: allForFilter.filter(function(p){{ return _bkPickInDateRange(p, '30d'); }})}},
      {{label:'Total',           ic:'📅', picks: allForFilter}},
    ];
    var dateRows = _bkBuildAnalyseRows(dateBuckets, 'Période');
    var dateBody = dateRows.map(_bkAnalyseRow).join('');
    dateAnalyseCard =
      '<div class="bk-card" id="bk-analyse-date">'
      + '<div class="bk-card-hd">'
      +   '<div class="bk-card-title">📅 Performance par période</div>'
      +   '<button class="bk-card-close" onclick="_bkToggleAnalyse(\\'date\\')" title="Fermer">✕</button>'
      + '</div>'
      + _bkAnalyseHeader('Période')
      + dateBody
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
    if(wr > be + 5){{
      advice.push({{ic:'💎', clr:'#34D399',
        t:'À cote moyenne <b>' + avgCote.toFixed(2) + '</b>, il faut gagner <b>' + be.toFixed(0) + '%</b> des paris pour être à l\\'équilibre. '
          + 'Toi tu en gagnes <b>' + wr.toFixed(0) + '%</b> → tu es <b>+' + (wr - be).toFixed(0) + ' pts</b> au-dessus du seuil de rentabilité. '
          + 'En clair : tu fais du profit sur la durée 💰'}});
    }} else if(wr < be - 5){{
      advice.push({{ic:'📉', clr:'#FBBF24',
        t:'À cote moyenne <b>' + avgCote.toFixed(2) + '</b>, le bookmaker te paye ' + (avgCote - 1).toFixed(2) + ' € par 1 € misé sur un pari gagné. '
          + 'Pour ne PAS perdre d\\'argent sur la durée, il faudrait gagner au moins <b>' + be.toFixed(0) + '%</b> du temps (= seuil de rentabilité = 1/cote). '
          + 'Toi tu gagnes <b>' + wr.toFixed(0) + '%</b>, soit <b>' + (be - wr).toFixed(0) + ' pts sous le seuil</b> → tu perds en moyenne. '
          + 'Pistes : prends des cotes plus élevées (mais bien valuées) ou trie plus strictement tes picks.'}});
    }}
  }}
  if(avgStake > bk * 0.05 && bk > 0) advice.push({{ic:'⚠️', clr:'#FBBF24',
    t:'Ta mise moyenne est <b>' + _bkFmt(avgStake) + ' €</b>, soit <b>' + Math.round(avgStake / bk * 100) + '% de ton bankroll</b>. '
      + 'C\\'est trop : la gestion saine en pari sportif recommande <b>1 à 3 % par pari</b> '
      + '(~' + _bkFmt(bk * 0.02) + ' € sur ton bankroll). Une mauvaise série peut sinon te ruiner vite.'}});
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

  // ── Compose layout : bandeau account en tete, puis sections ────────────
  var accountBar = (typeof _bkAccountBarHtml === 'function') ? _bkAccountBarHtml() : '';
  // L'analyse aggregee, si ouverte, prend la pleine largeur en haut des deux colonnes
  var analyseBlock = '';
  if(tipsterAnalyseCard || dateAnalyseCard){{
    analyseBlock = '<div class="bk-analyse-wrap">' + tipsterAnalyseCard + dateAnalyseCard + '</div>';
  }}
  var html = accountBar + hero + chartCard + stats + streakHtml
    + analyseBlock
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

<!-- Bouton flottant refresh (mobile / PWA standalone) -->
<button class="bk-refresh-fab" onclick="location.reload(true)" aria-label="Recharger la page" title="Recharger">🔄</button>
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
        # Egalement les fichiers d'historique + box scores pour le polling JS du bankroll
        import os as _os_p
        for _df in ("data/nba_history_min.json", "data/nba_box_scores_min.json"):
            if _os_p.path.exists(_df):
                try:
                    subprocess.run(["git", "add", "-f", _df], check=True, timeout=10)
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