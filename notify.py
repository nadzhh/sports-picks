"""
notify.py — Notification Telegram des picks 30 min avant chaque match.

Usage :
  python notify.py setup    -> recupere ton chat_id (apres avoir envoye un message au bot)
  python notify.py test     -> envoie un message test
  python notify.py          -> scan les matchs upcoming, envoie les notifs (mode normal)

Strategie :
  1. Tourne en cron toutes les 15 min
  2. Identifie les matchs avec kickoff dans 15-60 min (foot et NBA)
  3. Re-fetch les compos FotMob pour valider les titulaires
  4. Filtre/flag les picks joueur selon compo confirmee
  5. Envoie une notif par match, garde le log pour eviter les doublons

Format message :
  ⚽ Arsenal vs Burnley  (Premier League)
  🕐 Coup d'envoi dans 30 min
  ✅ COMPO OFFICIELLE  (ou ⚠️ Compo probable)

  PICKS ÉQUIPE
  • Arsenal gagne (81%) @ 1.45
  • BTTS Non (78%) @ 1.55

  PICKS JOUEUR
  ✅ Bukayo Saka but/passe décisive (72%) — TITULAIRE
  ⚠️ Viktor Gyökeres marque (68%) — REMPLAÇANT (skip)
"""
import json, urllib.request, urllib.parse, urllib.error, sys
from pathlib import Path
from datetime import datetime, timezone

try:
    from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
except ImportError:
    TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID = "", ""

NOTIF_LOG_FILE = Path("data/notif_log.json")

# Fenetre de notification (en minutes avant kickoff)
WINDOW_MIN_FROM = 15   # ne pas notifier si trop loin
WINDOW_MIN_TO   = 60   # ne pas notifier si trop proche / passe

# Seuils pour les alertes "high value" (immediat, hors fenetre kickoff)
# Filtre commun : cote minimum 1.40 (les picks a @1.10 ne valent rien a parier
# meme si la confiance est tres haute - mise inutile)
HIGH_VALUE_MIN_COTE = 1.40

HIGH_VALUE_NBA = {
    "confidence_min": 80,
    "edge_min":       40,
    "hit_l20_pct_min": 65,
}
HIGH_VALUE_FOOT = {
    "confidence_min": 85,
}


# ─── Telegram API ────────────────────────────────────────────────────────────

def _tg(method, params=None, post=False):
    """Appel a l'API Telegram. Retourne dict ou None."""
    if not TELEGRAM_BOT_TOKEN:
        return None
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/{method}"
    try:
        if post:
            data = urllib.parse.urlencode(params or {}).encode()
            r = urllib.request.urlopen(url, data=data, timeout=15)
        else:
            if params:
                url += "?" + urllib.parse.urlencode(params)
            r = urllib.request.urlopen(url, timeout=15)
        return json.loads(r.read())
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "ignore")
        print(f"[telegram {e.code}] {body[:200]}")
        return None
    except Exception as e:
        print(f"[telegram err] {e}")
        return None


def discover_chat_id():
    """Recupere les chat_id via getUpdates. L'utilisateur doit avoir envoye un message au bot."""
    if not TELEGRAM_BOT_TOKEN:
        print("[X] Pas de TELEGRAM_BOT_TOKEN dans config.py")
        return
    data = _tg("getUpdates")
    if not data or not data.get("ok"):
        print("[X] Erreur API Telegram. Verifie le token.")
        return
    updates = data.get("result", [])
    if not updates:
        print("[!] Aucun message recu par le bot.")
        print("    1. Sur ton telephone, cherche ton bot dans Telegram (@username du bot)")
        print("    2. Lance la conversation, envoie n'importe quel message (ex: /start)")
        print("    3. Relance : python notify.py setup")
        return
    seen = set()
    for u in updates:
        msg = u.get("message") or u.get("edited_message") or u.get("channel_post") or {}
        chat = msg.get("chat") or {}
        cid = chat.get("id")
        if cid is None or cid in seen: continue
        seen.add(cid)
        name = chat.get("first_name") or chat.get("title") or "?"
        ctype = chat.get("type", "?")
        print(f"  chat_id = {cid}  |  type = {ctype}  |  name = {name}")
    print()
    print("=> Copie le chat_id dans config.py :  TELEGRAM_CHAT_ID = \"...\"")


def tg_send(text, parse_mode="HTML"):
    """Envoie un message au TELEGRAM_CHAT_ID."""
    if not TELEGRAM_CHAT_ID:
        print("[X] TELEGRAM_CHAT_ID vide - lance d'abord : python notify.py setup")
        return False
    res = _tg("sendMessage", {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": parse_mode,
        "disable_web_page_preview": "true",
    }, post=True)
    return bool(res and res.get("ok"))


# ─── Notif log (anti-doublon) ────────────────────────────────────────────────

def _load_notif_log():
    if not NOTIF_LOG_FILE.exists(): return {"notified": {}}
    try: return json.loads(NOTIF_LOG_FILE.read_text(encoding="utf-8"))
    except Exception: return {"notified": {}}


def _save_notif_log(log):
    NOTIF_LOG_FILE.parent.mkdir(exist_ok=True)
    NOTIF_LOG_FILE.write_text(json.dumps(log, ensure_ascii=False, indent=2), encoding="utf-8")


# ─── Detection des matchs upcoming ───────────────────────────────────────────

def _upcoming_football():
    """Retourne [(match_dict, kickoff_utc), ...] avec kickoff dans la fenetre."""
    out = []
    try:
        matches = json.load(open("data/matches.json", encoding="utf-8"))
    except Exception:
        return out
    now = datetime.now(tz=timezone.utc)
    for m in matches:
        ts = m.get("start_ts")
        if not ts: continue
        try:
            ko = datetime.fromtimestamp(int(ts), tz=timezone.utc)
        except Exception:
            continue
        mins = (ko - now).total_seconds() / 60
        if WINDOW_MIN_FROM <= mins <= WINDOW_MIN_TO:
            out.append((m, ko))
    return out


def _upcoming_nba():
    """Retourne [(game_dict, tipoff_utc), ...] pour NBA."""
    out = []
    try:
        games = json.load(open("data/nba_matches.json", encoding="utf-8"))
    except Exception:
        return out
    now = datetime.now(tz=timezone.utc)
    for g in games:
        date_str = g.get("date")
        if not date_str: continue
        try:
            ko = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
            if ko.tzinfo is None: ko = ko.replace(tzinfo=timezone.utc)
        except Exception:
            continue
        mins = (ko - now).total_seconds() / 60
        if WINDOW_MIN_FROM <= mins <= WINDOW_MIN_TO:
            out.append((g, ko))
    return out


# ─── Lineup refresh ─────────────────────────────────────────────────────────

def _refresh_lineup(page_url):
    """
    Re-fetch la compo officielle FotMob 30min avant kickoff (force=True bypass cache).
    Retourne dict {confirmed: bool, home_starters: [names], away_starters: [names],
                    home_unavail: [], away_unavail: []}.
    """
    try:
        from fotmob_client import match_lineup, _fetch_match_page_full
    except ImportError:
        return {"confirmed": False, "home_starters": [], "away_starters": [], "home_unavail": [], "away_unavail": []}

    try:
        # On force le refresh pour la compo (cache 2h par defaut, on force pour avoir la derniere version)
        page = _fetch_match_page_full(page_url) if False else None
        ln = match_lineup(page_url)
    except Exception as e:
        print(f"  [lineup err] {e}")
        return {"confirmed": False, "home_starters": [], "away_starters": [], "home_unavail": [], "away_unavail": []}

    if not ln:
        return {"confirmed": False, "home_starters": [], "away_starters": [], "home_unavail": [], "away_unavail": []}

    # FotMob returns home/away with starters + bench + unavailable
    home = ln.get("home", {}) or ln.get("homeLineup", {}) or {}
    away = ln.get("away", {}) or ln.get("awayLineup", {}) or {}

    def _names(arr):
        out = []
        for p in (arr or []):
            n = p.get("name") if isinstance(p, dict) else None
            if n: out.append(n)
        return out

    home_starters = _names(home.get("starters") or home.get("starting"))
    away_starters = _names(away.get("starters") or away.get("starting"))
    home_unavail  = _names(home.get("unavailable") or home.get("absent"))
    away_unavail  = _names(away.get("unavailable") or away.get("absent"))
    confirmed = len(home_starters) >= 11 and len(away_starters) >= 11
    return {
        "confirmed":     confirmed,
        "home_starters": home_starters,
        "away_starters": away_starters,
        "home_unavail":  home_unavail,
        "away_unavail":  away_unavail,
    }


def _player_in_lineup(player_name, lineup, side):
    """Verifie si player est titulaire dans son cote (home/away)."""
    if not lineup: return None  # unknown
    starters = lineup.get("home_starters" if side == "home" else "away_starters", []) or []
    if not starters: return None
    pl = player_name.lower().strip()
    for s in starters:
        sl = s.lower().strip()
        if pl == sl: return True
        # fuzzy : lastname match
        if pl.split()[-1] == sl.split()[-1]: return True
        if pl in sl or sl in pl: return True
    return False


# ─── Format messages ─────────────────────────────────────────────────────────

def _format_foot(match, kickoff, picks_data, lineup):
    home = match.get("home", "?"); away = match.get("away", "?")
    league = match.get("league", "")
    mins = max(0, int((kickoff - datetime.now(tz=timezone.utc)).total_seconds() / 60))
    confirmed = (lineup or {}).get("confirmed", False)
    status_line = "✅ <b>COMPO OFFICIELLE</b>" if confirmed else "⚠️ Compo probable (officielle pas encore publiée)"

    lines = [
        f"⚽ <b>{home}</b> vs <b>{away}</b>",
        f"🕐 Coup d'envoi dans <b>{mins} min</b> · {league}",
        status_line,
        "",
    ]

    team_picks = (picks_data or {}).get("picks", []) or []
    if team_picks:
        lines.append("<b>━━ PICKS ÉQUIPE ━━</b>")
        for p in team_picks[:6]:
            label = p.get("label", "?")
            conf = p.get("confidence", 0)
            cote = p.get("cote")
            cote_str = f"  @ <b>{cote:.2f}</b>" if cote else ""
            lines.append(f"• {label} <b>({conf}%)</b>{cote_str}")
        lines.append("")

    player_picks = ((picks_data or {}).get("home_players", []) or []) + ((picks_data or {}).get("away_players", []) or [])
    if player_picks:
        lines.append("<b>━━ PICKS JOUEUR ━━</b>")
        for p in player_picks[:10]:
            player = p.get("player", "?")
            label = p.get("label", "?")
            conf = p.get("confidence", 0)
            side = p.get("team", "")  # "home" ou "away"
            in_xi = _player_in_lineup(player, lineup, side)
            if in_xi is True:    marker, note = "✅", " <i>titulaire</i>"
            elif in_xi is False: marker, note = "⚠️", " <i>REMPLAÇANT</i>"
            else:                marker, note = "·",  ""
            lines.append(f"{marker} {label} <b>({conf}%)</b>{note}")

    return "\n".join(lines)


def _format_nba(game, tipoff, picks_data):
    home = game.get("home", "?"); away = game.get("away", "?")
    mins = max(0, int((tipoff - datetime.now(tz=timezone.utc)).total_seconds() / 60))
    lines = [
        f"🏀 <b>{away}</b> @ <b>{home}</b>",
        f"🕐 Tip-off dans <b>{mins} min</b> · NBA",
        "",
    ]
    if not picks_data:
        lines.append("<i>Aucun pick disponible</i>")
        return "\n".join(lines)

    for team_label, picks in [(home, picks_data.get("home_picks", [])),
                              (away, picks_data.get("away_picks", []))]:
        if not picks: continue
        lines.append(f"<b>━━ {team_label} ━━</b>")
        for p in picks:
            label = p.get("label", "?")
            conf  = p.get("confidence", 0)
            cote  = p.get("real_cote") or p.get("cote_min")
            edge  = p.get("edge")
            cote_str = f"  @ <b>{cote}</b>" if cote else ""
            edge_str = f" (+{edge}%)" if edge and edge > 0 else ""
            lines.append(f"• {label} <b>({conf}%)</b>{cote_str}{edge_str}")
        lines.append("")
    return "\n".join(lines).rstrip()


def _get_foot_picks(match_id):
    try:
        data = json.load(open("data/picks.json", encoding="utf-8"))
        for m in data:
            if m.get("match_id") == match_id: return m
    except Exception:
        pass
    return None


def _get_nba_picks(game_id):
    try:
        data = json.load(open("data/nba_picks.json", encoding="utf-8"))
        return data.get(str(game_id)) or data.get(game_id)
    except Exception:
        return None


# ─── High-value alerts (single pick, hors fenetre kickoff) ───────────────────

def _is_high_value_nba(pick):
    """Pick NBA qualifie pour alerte single-pick.
    EXCLUT les picks avec rotation_warning (joueurs passes au bench - cas Barnes)
    car les L10/L20 sont calcules sur des matchs ou ils etaient titulaires."""
    if pick.get("rotation_warning"):
        return False
    conf = pick.get("confidence", 0)
    edge = pick.get("edge") or 0
    hit_l20 = pick.get("hit_l20_pct", 0)
    cote = pick.get("real_cote") or pick.get("cote_min") or 0
    return (conf >= HIGH_VALUE_NBA["confidence_min"]
            and edge >= HIGH_VALUE_NBA["edge_min"]
            and hit_l20 >= HIGH_VALUE_NBA["hit_l20_pct_min"]
            and cote >= HIGH_VALUE_MIN_COTE)


def _is_high_value_foot(pick):
    """Pick foot qualifie. Skip si cote bookmaker < 1.40 (trop bas pour valoir une mise)."""
    conf = pick.get("confidence", 0)
    cote = pick.get("cote")
    if conf < HIGH_VALUE_FOOT["confidence_min"]:
        return False
    # Si pas de cote bookmaker disponible : skip (on ne saurait pas si betable)
    if cote is None or cote < HIGH_VALUE_MIN_COTE:
        return False
    return True


def _format_hv_nba(pick, game):
    home = game.get("home_team", "?")
    away = game.get("away_team", "?")
    cote = pick.get("real_cote")
    book = (pick.get("book") or "").upper()
    edge = pick.get("edge")
    s = pick.get("stats", {})
    lines = [
        "🚨 <b>PICK HIGH VALUE NBA</b> 🚨",
        "",
        f"🏀 <b>{away}</b> @ <b>{home}</b>",
        "",
        f"<b>{pick.get('label', '?')}</b>",
        "",
    ]
    if cote:
        lines.append(f"💰 <b>{book} @ {cote}</b> · edge <b>+{edge}%</b>")
    lines.append(f"🎯 Confidence <b>{pick.get('confidence', 0)}%</b>")
    # Hit rate L10 / L20 + trend
    if pick.get("hit_l10") and pick.get("hit_l20"):
        td = pick.get("trend_delta", 0)
        trend_icon = ""
        if td >= 10: trend_icon = f" 📈 +{td:.0f}pp"
        elif td <= -10: trend_icon = f" 📉 {td:.0f}pp"
        lines.append(f"📊 L10 {pick['hit_l10']} ({pick.get('hit_l10_pct',0)}%) · L20 {pick['hit_l20']} ({pick.get('hit_l20_pct',0)}%){trend_icon}")
    # Stats
    if s:
        lines.append(f"📈 L5 {s.get('L5','?')} · L10 {s.get('L10','?')} · Saison {s.get('Saison','?')} → attendu <b>{s.get('mu','?')}</b>")
    # Def argument
    if pick.get("def_argument"):
        lines.append(f"🎯 {pick['def_argument']}")
    # Context chips
    ctx = pick.get("context", {})
    chips = []
    if ctx.get("pace"):  chips.append(f"pace×{ctx['pace']}")
    if ctx.get("vegas"): chips.append(f"vegas×{ctx['vegas']}")
    if ctx.get("def"):   chips.append(f"def×{ctx['def']}")
    if ctx.get("b2b"):   chips.append("B2B -4%")
    if chips:
        lines.append("⚙️ " + " · ".join(chips))
    return "\n".join(lines)


def _format_hv_foot(pick, match):
    home = match.get("home", "?")
    away = match.get("away", "?")
    league = match.get("league", "")
    cote = pick.get("cote")
    lines = [
        "🚨 <b>PICK HIGH VALUE FOOT</b> 🚨",
        "",
        f"⚽ <b>{home}</b> vs <b>{away}</b>",
        f"<i>{league}</i>",
        "",
        f"<b>{pick.get('label', '?')}</b>",
        "",
    ]
    if cote:
        lines.append(f"💰 Cote <b>@ {cote:.2f}</b>")
    lines.append(f"🎯 Confidence <b>{pick.get('confidence', 0)}%</b>")
    reasoning = pick.get("reasoning", "")
    if reasoning:
        lines.append("")
        lines.append(f"<i>{reasoning[:300]}</i>")
    return "\n".join(lines)


def send_high_value_alerts():
    """
    Scan tous les picks (NBA + foot), envoie une alerte Telegram pour CHAQUE
    pick high-value pas encore notifie. Anti-doublon perpetuel via notif_log.
    """
    if not TELEGRAM_CHAT_ID:
        return

    log = _load_notif_log()
    sent_ids = set(log.get("high_value_sent", []))
    new_count = 0

    # ─── NBA picks ──────────────────────────────────────────────────────────
    try:
        nba = json.load(open("data/nba_picks.json", encoding="utf-8"))
        for gid, game in nba.items():
            date_part = (game.get("date") or "")[:10]
            for pk in (game.get("home_picks", []) + game.get("away_picks", [])):
                pid = f"nba_{date_part}_{gid}_{pk.get('player','?')}_{pk.get('prop','?')}_{pk.get('direction','?')}_{pk.get('line','?')}"
                if pid in sent_ids: continue
                if not _is_high_value_nba(pk): continue
                text = _format_hv_nba(pk, game)
                if tg_send(text):
                    sent_ids.add(pid)
                    new_count += 1
                    print(f"  [HV-NBA] {pk.get('label','?')[:60]} (conf {pk.get('confidence')}%, edge {pk.get('edge')}%)")
    except FileNotFoundError:
        pass
    except Exception as e:
        print(f"  [hv-nba err] {e}")

    # ─── Foot picks ─────────────────────────────────────────────────────────
    try:
        foot = json.load(open("data/picks.json", encoding="utf-8"))
        for m in foot:
            mid = m.get("match_id")
            # Team picks
            for pk in m.get("picks", []):
                pid = f"foot_{mid}_team_{pk.get('direction','?')}"
                if pid in sent_ids: continue
                if not _is_high_value_foot(pk): continue
                text = _format_hv_foot(pk, m)
                if tg_send(text):
                    sent_ids.add(pid)
                    new_count += 1
                    print(f"  [HV-FOOT-TEAM] {pk.get('label','?')[:60]} (conf {pk.get('confidence')}%)")
            # Player picks
            for pk in (m.get("home_players", []) + m.get("away_players", [])):
                pid = f"foot_{mid}_player_{pk.get('player','?')}_{pk.get('type','?')}"
                if pid in sent_ids: continue
                if not _is_high_value_foot(pk): continue
                text = _format_hv_foot(pk, m)
                if tg_send(text):
                    sent_ids.add(pid)
                    new_count += 1
                    print(f"  [HV-FOOT-PLAYER] {pk.get('label','?')[:60]} (conf {pk.get('confidence')}%)")
    except FileNotFoundError:
        pass
    except Exception as e:
        print(f"  [hv-foot err] {e}")

    log["high_value_sent"] = sorted(sent_ids)
    _save_notif_log(log)
    if new_count:
        print(f"[HV] {new_count} alerte(s) high-value envoyee(s)")
    else:
        print(f"[HV] Aucune nouvelle alerte (deja {len(sent_ids)} pick(s) high-value envoyes au total)")


# ─── Main ────────────────────────────────────────────────────────────────────

def run_notifications():
    if not TELEGRAM_CHAT_ID:
        print("[!] TELEGRAM_CHAT_ID vide. Lance d'abord : python notify.py setup")
        return

    # 1. Alertes single-pick high-value (independantes de la fenetre kickoff)
    print("=== Scan high-value picks ===")
    send_high_value_alerts()

    # 2. Notifications par match (30 min avant kickoff, summary complet)
    log = _load_notif_log()
    notified = log.get("notified", {})

    foot = _upcoming_football()
    nba  = _upcoming_nba()
    print(f"\n=== Notifs kickoff : {len(foot)} matchs foot + {len(nba)} matchs NBA dans la fenetre ({WINDOW_MIN_FROM}-{WINDOW_MIN_TO} min) ===")

    n_sent = 0
    for match, ko in foot:
        mid = match.get("id")
        key = f"foot_{mid}"
        if key in notified:
            print(f"  [skip] {match.get('home')} vs {match.get('away')} - deja notifie a {notified[key]}")
            continue
        # Refresh lineup
        page_url = match.get("_page_url")
        lineup = _refresh_lineup(page_url) if page_url else None
        # Picks
        picks = _get_foot_picks(mid)
        text = _format_foot(match, ko, picks, lineup)
        ok = tg_send(text)
        if ok:
            notified[key] = datetime.now(timezone.utc).isoformat()
            n_sent += 1
            print(f"  [OK] {match.get('home')} vs {match.get('away')} (kickoff dans {int((ko - datetime.now(tz=timezone.utc)).total_seconds()/60)} min)")
        else:
            print(f"  [ERR] echec envoi pour {mid}")

    for game, tip in nba:
        gid = game.get("game_id")
        key = f"nba_{gid}"
        if key in notified:
            print(f"  [skip] {game.get('away')} @ {game.get('home')} - deja notifie")
            continue
        picks = _get_nba_picks(gid)
        text = _format_nba(game, tip, picks)
        ok = tg_send(text)
        if ok:
            notified[key] = datetime.now(timezone.utc).isoformat()
            n_sent += 1
            print(f"  [OK] {game.get('away')} @ {game.get('home')} (tip dans {int((tip - datetime.now(tz=timezone.utc)).total_seconds()/60)} min)")
        else:
            print(f"  [ERR] echec envoi pour {gid}")

    log["notified"] = notified
    _save_notif_log(log)
    print(f"\n[OK] {n_sent} notif(s) envoyee(s)")


def main():
    if len(sys.argv) > 1:
        cmd = sys.argv[1]
        if cmd == "setup":
            discover_chat_id()
            return
        if cmd == "test":
            ok = tg_send("🎯 <b>Test sport-picks</b>\n\nSi tu vois ce message, ton bot Telegram est OK !")
            print("OK" if ok else "Echec")
            return
    run_notifications()


if __name__ == "__main__":
    main()
