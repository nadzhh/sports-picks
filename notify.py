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
# Le user veut attendre ~30 min avant kickoff pour avoir la compo OFFICIELLE
# (probable XI -> officielle ~60 min avant). Fenetre 15-35 min cible bien ce moment.
WINDOW_MIN_FROM = 15   # ne pas notifier si trop proche / passe
WINDOW_MIN_TO   = 35   # ne pas notifier si trop loin

# Seuils pour les alertes "high value" (immediat, hors fenetre kickoff)
# Filtre commun : cote minimum 1.60 pour Telegram (les picks a @1.10 ne valent
# rien a parier meme si la confiance est tres haute - mise inutile)
HIGH_VALUE_MIN_COTE = 1.60

# Marge bookmaker estimee : quand on n'a pas la cote book, on simule
# cote_estimee = (1 / proba_modele) * (1 - MARGIN). Le book serre la cote
# de ~8% vs la cote "juste" pour son benefice. Ex : conf 50% -> 2.00 juste
# -> 1.84 chez le book.
BOOK_MARGIN_ESTIMATE = 0.08


def estimate_book_cote(confidence):
    """Simule la cote book a partir de la confidence du modele.
    cote_book ~= (1/proba) * (1 - margin)
    confidence=50  -> 1.84
    confidence=60  -> 1.53
    confidence=65  -> 1.41
    confidence=70  -> 1.31
    """
    if not confidence or confidence <= 0: return None
    p = confidence / 100.0
    fair = 1.0 / p
    return round(fair * (1 - BOOK_MARGIN_ESTIMATE), 2)
# Pour la NBA : on alerte UNIQUEMENT sur les matchs jouables dans la nuit a venir
# (ex: si on est le 19/05 a 12h, on alerte que les matchs jusqu'au 20/05 12h max,
# pas les matchs du 21/05 qui sont 2 nuits plus loin).
HIGH_VALUE_NBA_MAX_HOURS_AHEAD = 30

# Seuils generous : on envoie tout pick "interessant" dans la journee.
# L'idee : recevoir un large panel de propositions sur Telegram, l'user
# choisit ce qu'il joue. Le top 10/jour seulement est sauvegarde en
# historique (pour le tracking WR/ROI propre).
HIGH_VALUE_NBA = {
    "confidence_min":  65,
    "edge_min":        15,
    "hit_l20_pct_min": 55,
}
HIGH_VALUE_FOOT = {
    "confidence_min": 70,
}

# Edge minimum (en pp) pour pousser un pick foot Telegram, par categorie.
# L'edge_pp est calcule par picks_engine quand on a la cote book :
# edge_pp = (P_modele - 1/cote_book) * 100
HIGH_VALUE_FOOT_EDGE_TEAM   = 5    # team picks : >=5pp d'edge suffit
HIGH_VALUE_FOOT_EDGE_PLAYER = 7    # player picks : seuil + strict (compo incertaine)

# Fenetre kickoff pour push de picks joueur : doit etre dans les 4h pour que
# la compo officielle soit dispo (sort generalement 1h avant le KO)
PUSH_PLAYER_KICKOFF_WINDOW_HOURS = 4

# Throttle alertes HV : 30 min entre 2 batches (permet d'envoyer plusieurs
# picks dans la journee sans spammer toutes les 10 min du cron).
HIGH_VALUE_THROTTLE_HOURS = 0.5


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
    Re-fetch la compo FotMob (force=True bypass cache) au moment de la notif.
    Compo type :
      - 'standard' / 'confirmed' = OFFICIELLE (sortie ~60 min avant kickoff)
      - 'predicted' = PROBABLE (XI suppose)
    On veut envoyer la notif UNIQUEMENT quand confirmed - sinon on attend.

    Retourne {confirmed, lineup_type, home_starters, away_starters,
              home_unavail, away_unavail}.
    """
    empty = {"confirmed": False, "lineup_type": None, "home_starters": [],
             "away_starters": [], "home_unavail": [], "away_unavail": []}
    try:
        from fotmob_client import match_lineup
    except ImportError:
        return empty
    try:
        # force=True : on veut TOUJOURS la derniere version (cache 30 min OK)
        ln = match_lineup(page_url, ttl=30 * 60, force=True)
    except TypeError:
        # Fallback si signature sans force
        try: ln = match_lineup(page_url)
        except Exception: return empty
    except Exception as e:
        print(f"  [lineup err] {e}")
        return empty
    if not ln:
        return empty

    lineup_type = (ln.get("type") or "").lower()
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

    # Compo officielle uniquement si FotMob indique 'standard' ou 'confirmed'
    # (PAS de fallback "11 joueurs == confirme" car les predicted XI ont aussi 11 joueurs)
    confirmed = lineup_type in ("standard", "confirmed") and len(home_starters) >= 11 and len(away_starters) >= 11
    return {
        "confirmed":     confirmed,
        "lineup_type":   lineup_type or "unknown",
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

def _player_unavailable(player_name, lineup):
    """Verifie si player est dans la liste des indisponibles (blessure / suspendu)."""
    if not lineup: return False
    unavail = (lineup.get("home_unavail", []) or []) + (lineup.get("away_unavail", []) or [])
    if not unavail: return False
    pl = player_name.lower().strip()
    for s in unavail:
        sl = s.lower().strip()
        if pl == sl or pl in sl or sl in pl: return True
        if pl.split()[-1] == sl.split()[-1]: return True
    return False


def _format_foot(match, kickoff, picks_data, lineup):
    """
    Format Telegram foot. Retourne None si :
     - lineup non confirmee (on attend la prochaine notif)
     - aucun pick a envoyer (cotes manquantes ou joueurs indispos)
    """
    home = match.get("home", "?"); away = match.get("away", "?")
    league = match.get("league", "")
    mins = max(0, int((kickoff - datetime.now(tz=timezone.utc)).total_seconds() / 60))
    confirmed = (lineup or {}).get("confirmed", False)
    if not confirmed:
        print(f"  [skip foot {home} vs {away}] lineup_type={(lineup or {}).get('lineup_type')} - on attend la compo officielle")
        return None

    # ── PICKS ÉQUIPE (uniquement avec cote dispo) ──
    team_picks = (picks_data or {}).get("picks", []) or []
    team_rows = []
    for p in team_picks[:8]:
        cote = p.get("cote")
        if not cote: continue  # skip picks sans cote (non betable)
        label = p.get("label", "?")
        conf = p.get("confidence", 0)
        reasoning = (p.get("reasoning") or "").strip()
        # Reasoning court : on prend juste la 1ere ligne (avant le \n) ou tronque a 130 chars
        first_line = reasoning.split("\n")[0][:160] if reasoning else ""
        cote_str = f" @ <b>{cote:.2f}</b>"
        row = f"• <b>{label}</b> ({conf}%){cote_str}"
        if first_line:
            row += f"\n  <i>{first_line}</i>"
        team_rows.append(row)

    # ── PICKS JOUEUR (skip indispos, skip si pas titulaire) ──
    player_picks = ((picks_data or {}).get("home_players", []) or []) + ((picks_data or {}).get("away_players", []) or [])
    player_rows = []
    for p in player_picks[:12]:
        player = p.get("player", "?")
        side = p.get("team", "")
        # Skip si dans la liste des indispos (blesse/suspendu)
        if _player_unavailable(player, lineup):
            continue
        in_xi = _player_in_lineup(player, lineup, side)
        # On garde uniquement les titulaires (le filtre principal de qualite)
        if in_xi is not True:
            continue
        label = p.get("label", "?")
        conf = p.get("confidence", 0)
        cote = p.get("cote")
        cote_str = f" @ <b>{cote:.2f}</b>" if cote else ""
        reasoning = (p.get("reasoning") or "").strip()
        first_line = reasoning.split("\n")[0][:160] if reasoning else ""
        row = f"✅ <b>{label}</b> ({conf}%){cote_str}"
        if first_line:
            row += f"\n  <i>{first_line}</i>"
        player_rows.append(row)

    # Si rien d'envoyable -> skip toute la notif
    if not team_rows and not player_rows:
        return None

    lines = [
        f"⚽ <b>{home}</b> vs <b>{away}</b>",
        f"🕐 Coup d'envoi dans <b>{mins} min</b> · {league}",
        f"✅ <b>COMPO OFFICIELLE</b>",
        "",
    ]
    if team_rows:
        lines.append("<b>━━ PICKS ÉQUIPE ━━</b>")
        lines.extend(team_rows)
        lines.append("")
    if player_rows:
        lines.append("<b>━━ PICKS JOUEUR ━━</b>")
        lines.extend(player_rows)
    return "\n".join(lines)


def _format_nba(game, tipoff, picks_data):
    """Format NBA. Retourne None si aucun pick a envoyer (evite messages vides)."""
    home = game.get("home", "?"); away = game.get("away", "?")
    mins = max(0, int((tipoff - datetime.now(tz=timezone.utc)).total_seconds() / 60))

    home_picks = (picks_data or {}).get("home_picks", []) or []
    away_picks = (picks_data or {}).get("away_picks", []) or []
    # Skip rotation_warning (joueurs hors rotation) sur l'alerte Telegram
    home_picks = [p for p in home_picks if not p.get("rotation_warning")]
    away_picks = [p for p in away_picks if not p.get("rotation_warning")]
    if not home_picks and not away_picks:
        return None

    lines = [
        f"🏀 <b>{away}</b> @ <b>{home}</b>",
        f"🕐 Tip-off dans <b>{mins} min</b> · NBA",
        "",
    ]
    for team_label, picks in [(home, home_picks), (away, away_picks)]:
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
    EXCLUT les picks suspects (flags du moteur) :
    - rotation_warning      : joueur sorti de rotation
    - injury_warning        : Tank01 Day-to-Day / Questionable
    - last_min_warning      : dernier match MIN<<mediane (rotation evolutive)
    - book_divergence_warning : book line << notre mu (bookmaker voit autre chose)
    Resultat : ne pousse que les picks "propres" (pas de piege detecte)."""
    if pick.get("rotation_warning"):         return False
    if pick.get("injury_warning"):           return False
    if pick.get("last_min_warning"):         return False
    if pick.get("book_divergence_warning"):  return False
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


def _effective_cote(pick):
    """Retourne (cote, source) ou cote = cote_book si dispo, sinon
    estimation = (1/proba_modele)*(1-margin). source = 'book' ou 'simule'.
    """
    cote = pick.get("cote")
    if cote and cote > 0:
        return float(cote), "book"
    conf = pick.get("confidence", 0)
    sim = estimate_book_cote(conf)
    return sim, "simule"


def _is_push_eligible_team(pick):
    """Critere stricte pour push Telegram d'un team pick :
    - cote (book ou simulee) >= 1.60 sinon pas de mise interessante
    - confiance >= 50 minimum (sinon trop random meme avec une bonne cote)
    - bonus : edge_pp >= 5 si dispo
    Retourne (eligible_bool, reason_str)."""
    conf = pick.get("confidence", 0)
    edge = pick.get("edge_pp")
    cote, src = _effective_cote(pick)

    if cote is None:
        return False, "pas de cote ni de conf"
    if cote < HIGH_VALUE_MIN_COTE:
        return False, f"cote {cote:.2f} ({src}) < {HIGH_VALUE_MIN_COTE}"
    if conf < 50:
        return False, f"conf {conf}% trop basse (min 50)"
    # OK : cote >= 1.60 et conf >= 50. On signale l'edge si pertinent.
    extra = ""
    if edge is not None:
        if edge >= HIGH_VALUE_FOOT_EDGE_TEAM:
            extra = f", edge +{edge}pp"
        elif edge < 0:
            return False, f"conf {conf}% mais edge {edge}pp negatif"
    return True, f"conf {conf}% @ {cote:.2f} ({src}){extra}"


def _is_push_eligible_player(pick, lineup_state):
    """Critere stricte pour push Telegram d'un player pick :
    - cote (book ou simulee) >= 1.60
    - confiance >= 60 ET edge_pp >= 7 (si edge dispo) OU conf >= 65
    - lineup CONFIRMED (sinon on ne sait pas s'il sera titulaire)
    - joueur EFFECTIVEMENT dans le XI confirme (re-verif)
    - joueur PAS dans la liste des indisponibles
    Retourne (eligible_bool, reason_str)."""
    conf = pick.get("confidence", 0)
    edge = pick.get("edge_pp")
    player = pick.get("player", "")
    team_side = pick.get("team") or pick.get("side") or "home"

    # 1) Compo confirmee obligatoire
    if not lineup_state or not lineup_state.get("confirmed"):
        return False, "compo non confirmee"

    # 2) Joueur titulaire dans la compo officielle (re-verif)
    in_xi = _player_in_lineup(player, lineup_state, team_side)
    if in_xi is False:
        return False, f"{player} pas dans le XI confirme"
    if in_xi is None:
        return False, "compo dispo mais XI inconnu"

    # 3) Joueur pas blesse / suspendu
    if _player_unavailable(player, lineup_state):
        return False, f"{player} dans la liste des indisponibles"

    # 4) Cote (book ou simulee) + edge
    cote, src = _effective_cote(pick)
    if cote is None:
        return False, "pas de cote ni de conf"
    if cote < HIGH_VALUE_MIN_COTE:
        return False, f"cote {cote:.2f} ({src}) < {HIGH_VALUE_MIN_COTE}"
    if conf < 50:
        return False, f"conf {conf}% trop basse (min 50)"
    # OK : compo confirmee + titulaire + cote >= 1.60 + conf >= 50
    extra = ""
    if edge is not None:
        if edge >= HIGH_VALUE_FOOT_EDGE_PLAYER:
            extra = f", edge +{edge}pp"
        elif edge < 0:
            return False, f"conf {conf}% mais edge {edge}pp negatif"
    return True, f"conf {conf}% @ {cote:.2f} ({src}) - compo confirmee{extra}"


BOOK_LABELS = {
    "draftkings": "DK", "fanduel": "FanDuel", "betmgm": "BetMGM", "caesars": "Caesars",
    "pointsbetus": "PointsBet", "pinnacle": "Pinnacle", "unibet_eu": "Unibet",
    "unibet_uk": "Unibet UK", "betfair_ex_eu": "Betfair", "marathonbet": "Marathon",
    "betclic": "Betclic", "bwin": "Bwin",
}


def _format_hv_nba(pick, game):
    home = game.get("home_team", "?")
    away = game.get("away_team", "?")
    cote = pick.get("real_cote")
    book = BOOK_LABELS.get(pick.get("book"), (pick.get("book") or "").upper())
    edge = pick.get("edge")
    s = pick.get("stats", {})
    books_list = pick.get("books") or []
    lines = [
        "🚨 <b>PICK HIGH VALUE NBA</b> 🚨",
        "",
        f"🏀 <b>{away}</b> @ <b>{home}</b>",
        "",
        f"<b>{pick.get('label', '?')}</b>",
        "",
    ]
    if cote:
        lines.append(f"💰 Meilleure cote : <b>{book} @ {cote}</b> · edge <b>+{edge}%</b>")
        # Autres books si plusieurs
        others = [b for b in books_list if b.get("book") != pick.get("book")][:4]
        if others:
            others_str = " · ".join(
                f'{BOOK_LABELS.get(b["book"], b["book"])} @ {b["cote"]}'
                for b in others
            )
            lines.append(f"   <i>Autres : {others_str}</i>")
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
    edge = pick.get("edge_pp")
    tier = pick.get("tier", "")
    lineup_status = pick.get("lineup_status") or (pick.get("context") or {}).get("lineup_status", "")
    is_player = bool(pick.get("player"))
    tier_emoji = {"safe": "🛡️", "ok": "🎯", "fun": "🎲"}.get(tier, "🚨")
    lines = [
        f"{tier_emoji} <b>PICK HIGH VALUE FOOT</b> {tier_emoji}",
        "",
        f"⚽ <b>{home}</b> vs <b>{away}</b>",
        f"<i>{league}</i>",
        "",
        f"<b>{pick.get('label', '?')}</b>",
        "",
    ]
    if cote:
        line = f"💰 Cote book <b>@ {cote:.2f}</b>"
        if edge is not None:
            color = "🟢" if edge >= 5 else ("🔵" if edge >= 0 else "🔴")
            line += f" · {color} edge <b>{edge:+.1f}pp</b>"
        lines.append(line)
    else:
        # Pas de cote book - on affiche la cote simulee comme reference
        sim = estimate_book_cote(pick.get("confidence", 0))
        if sim:
            lines.append(f"💰 Cote estimee ~<b>{sim:.2f}</b> (book inconnu - cherche au moins {HIGH_VALUE_MIN_COTE})")
    lines.append(f"🎯 Confidence <b>{pick.get('confidence', 0)}%</b>")
    if is_player and lineup_status == "confirmed":
        lines.append("✅ <b>Compo officielle - titulaire verifie</b>")
    elif is_player and lineup_status == "predicted":
        lines.append("⚠️ Compo PROBABLE (a re-confirmer 1h avant)")
    reasoning = pick.get("reasoning", "")
    if reasoning:
        lines.append("")
        lines.append(f"<i>{reasoning[:300]}</i>")
    return "\n".join(lines)


def send_high_value_alerts():
    """
    Scan tous les picks (NBA + foot), envoie une alerte Telegram pour CHAQUE
    pick high-value pas encore notifie. Throttle : pas plus de 1 batch par
    HIGH_VALUE_THROTTLE_HOURS (default 1h) pour permettre un cron toutes les
    5 min sans spammer le mobile.
    """
    if not TELEGRAM_CHAT_ID:
        return

    log = _load_notif_log()
    sent_ids = set(log.get("high_value_sent", []))
    new_count = 0

    # Throttle : check timestamp du dernier batch HV
    last_batch = log.get("last_hv_batch_at")
    if last_batch:
        try:
            last_dt = datetime.fromisoformat(last_batch)
            if last_dt.tzinfo is None: last_dt = last_dt.replace(tzinfo=timezone.utc)
            elapsed_h = (datetime.now(tz=timezone.utc) - last_dt).total_seconds() / 3600
            if elapsed_h < HIGH_VALUE_THROTTLE_HOURS:
                print(f"  [throttle HV] {elapsed_h:.2f}h depuis dernier batch (min {HIGH_VALUE_THROTTLE_HOURS}h) - on attend")
                return
        except Exception:
            pass

    # ─── NBA picks (uniquement matchs <= 30h ahead) ────────────────────────
    try:
        nba = json.load(open("data/nba_picks.json", encoding="utf-8"))
        now = datetime.now(tz=timezone.utc)
        for gid, game in nba.items():
            # Skip si match trop loin (eviter d'alerter sur matchs de demain+1)
            game_date = game.get("date") or ""
            try:
                ko = datetime.fromisoformat(game_date.replace("Z", "+00:00"))
                if ko.tzinfo is None: ko = ko.replace(tzinfo=timezone.utc)
                hours_ahead = (ko - now).total_seconds() / 3600
                if hours_ahead > HIGH_VALUE_NBA_MAX_HOURS_AHEAD:
                    print(f"  [HV-NBA skip] {game.get('away_team')} @ {game.get('home_team')} dans {hours_ahead:.1f}h (>30h)")
                    continue
                if hours_ahead < 0:
                    continue  # match passe
            except Exception:
                pass
            date_part = game_date[:10]
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
    # Strategie : 2 critere distincts
    #  - team picks   : edge_pp >= 5 (ou conf >= 75) - pas besoin de lineup
    #  - player picks : compo CONFIRMEE + joueur dans le XI + edge_pp >= 7
    #                   (on refetch la compo en force)
    try:
        foot = json.load(open("data/picks.json", encoding="utf-8"))
        now = datetime.now(tz=timezone.utc)
        # Cache lineup re-fetched par match (1 call par match max)
        lineup_cache = {}
        def _get_lineup_state(match):
            mid = match.get("match_id")
            if mid in lineup_cache: return lineup_cache[mid]
            url = match.get("page_url") or match.get("_page_url")
            if not url:
                lineup_cache[mid] = None
                return None
            state = _refresh_lineup(url)
            lineup_cache[mid] = state
            return state

        for m in foot:
            mid = m.get("match_id")
            ko_ts = m.get("start_ts")
            hours_to_ko = None
            if ko_ts:
                try:
                    ko_dt = datetime.fromtimestamp(int(ko_ts), tz=timezone.utc)
                    hours_to_ko = (ko_dt - now).total_seconds() / 3600
                    if hours_to_ko < -2:  # match deja joue depuis 2h+
                        continue
                except Exception:
                    pass

            # ── Team picks ──────────────────────────────────────────────────
            for pk in m.get("picks", []):
                pid = f"foot_{mid}_team_{pk.get('direction','?')}"
                if pid in sent_ids: continue
                eligible, reason = _is_push_eligible_team(pk)
                if not eligible: continue
                text = _format_hv_foot(pk, m)
                if tg_send(text):
                    sent_ids.add(pid)
                    new_count += 1
                    print(f"  [HV-FOOT-TEAM] {pk.get('label','?')[:60]} - {reason}")

            # ── Player picks (lineup confirmee + dans XI) ──────────────────
            # Seulement si kickoff est dans la fenetre (compo confirmee
            # generalement 1h avant -> on regarde 0-4h avant le KO)
            if hours_to_ko is not None and hours_to_ko > PUSH_PLAYER_KICKOFF_WINDOW_HOURS:
                continue  # trop tot - lineup encore predicted

            ln_state = _get_lineup_state(m)
            for pk in (m.get("home_players", []) + m.get("away_players", [])):
                pid = f"foot_{mid}_player_{pk.get('player','?')}_{pk.get('type','?')}"
                if pid in sent_ids: continue
                eligible, reason = _is_push_eligible_player(pk, ln_state)
                if not eligible:
                    # Log soft les skip pour debug
                    if pk.get("confidence", 0) >= 70:
                        print(f"  [skip player] {pk.get('player','?')} {pk.get('type','?')[:20]} : {reason}")
                    continue
                text = _format_hv_foot(pk, m)
                if tg_send(text):
                    sent_ids.add(pid)
                    new_count += 1
                    print(f"  [HV-FOOT-PLAYER] {pk.get('player','?')[:25]} {pk.get('label','?')[:40]} - {reason}")

            # ── Pick score exact WC (fun) ──────────────────────────────────
            # Push UNIQUEMENT si compo confirmee (= 30-60 min avant KO).
            # Score exact = pari fun a cote elevee, c'est la valeur ajoutee
            # par la confirmation tardive de la compo.
            for pk in m.get("fun_picks", []):
                dir_str = (pk.get("direction") or "")
                if not dir_str.startswith("wc_score_"):
                    continue
                pid = f"foot_{mid}_wcscore_{dir_str}"
                if pid in sent_ids: continue
                # Compo doit etre confirmee
                if ln_state is None or not ln_state.get("confirmed"):
                    continue
                # Cote >= 4.0 minimum (pour valoir la mise sur un fun pick)
                cote = pk.get("cote") or 0
                if cote < 4.0:
                    continue
                text = _format_hv_foot(pk, m)
                if tg_send(text):
                    sent_ids.add(pid)
                    new_count += 1
                    print(f"  [HV-FOOT-WC-EXACT] {pk.get('label','?')[:60]} @ {cote} - compo confirmee")
    except FileNotFoundError:
        pass
    except Exception as e:
        print(f"  [hv-foot err] {e}")

    log["high_value_sent"] = sorted(sent_ids)
    if new_count > 0:
        # On marque le timestamp uniquement si on a effectivement envoye -> evite
        # de bloquer le throttle inutilement si pas de pick HV ce run.
        log["last_hv_batch_at"] = datetime.now(tz=timezone.utc).isoformat()
    _save_notif_log(log)
    if new_count:
        print(f"[HV] {new_count} alerte(s) high-value envoyee(s) - throttle {HIGH_VALUE_THROTTLE_HOURS}h activee")
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
        if text is None:
            # Compo pas encore officielle OU rien d'envoyable - on attend la prochaine notif
            # On NE marque PAS comme notified pour retenter au prochain cron
            continue
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
        if text is None:
            print(f"  [skip {game.get('away')} @ {game.get('home')}] aucun pick a notifier")
            continue
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
