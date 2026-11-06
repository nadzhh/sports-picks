"""
sentinel.py — Sentinelle des bots (06/11/2026, Wilson : « met des bots en place, fais ce que tu veux »).

Constat : le 06/11 le bot de nuit (improve.yml) a echoue sans que personne le voie ; du 30/10 au 04/11 les value bets
ont tourne 5 jours sans cle. La sentinelle, appelee a chaque cycle de la veille (nba_live, toutes les heures en CI),
lit l'etat des workflows GitHub (API publique du depot) et previent sur Telegram (canal Bankroll IA) :
  - un workflow dont les N dernieres executions terminees ont echoue (N = 1 pour les bots quotidiens, 2-3 pour ceux
    qui tournent souvent : un echec isole de reseau n'est pas une panne), avec l'etape en cause et le lien ;
  - le bot de nuit pas lance a 9 h UTC, ou les picks sans execution reussie depuis 10 h ;
  - le retour a la normale d'un workflow signale.
Une alerte par incident (memorise dans data/sentinel.json, publie avec la veille). Aucune action corrective ici :
la relance du bot de nuit est faite par nba_live.improve_kick.
"""
import json, os
from datetime import datetime, timedelta, timezone
from pathlib import Path

PATH = Path("data/sentinel.json")
REPO = os.environ.get("GITHUB_REPOSITORY", "nadzhh/sports-picks")
API = f"https://api.github.com/repos/{REPO}/actions"
# workflow -> (nom affiche, echecs consecutifs avant alerte)
WATCH = {"improve.yml": ("Bot d'amélioration (nuit)", 1), "foot-validate.yml": ("Validation base foot", 1),
         "picks-cron.yml": ("Picks (toutes les 30 min)", 3), "nba-live.yml": ("Veille NBA / foot", 3),
         "chat.yml": ("Chat du site", 3)}
PICKS_STALE = timedelta(hours=10)   # le cron « 30 min » tourne en realite toutes les 4 a 8 h (planification GitHub)
NIGHT_LATE = 9 * 60          # minutes UTC : le bot de nuit (01:17, relance a 06:30) doit avoir demarre


def _t(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def load():
    try:
        return json.loads(PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"incidents": {}, "checked_at": None}


def save(st):
    PATH.write_text(json.dumps(st, ensure_ascii=False, indent=1), encoding="utf-8")


def _get(url, params=None):
    import requests
    h = {"Accept": "application/vnd.github+json"}
    if os.environ.get("GH_TOKEN"): h["Authorization"] = f"Bearer {os.environ['GH_TOKEN']}"   # quota plus large ; jamais affiche
    r = requests.get(url, params=params, headers=h, timeout=20)
    r.raise_for_status()
    return r.json()


def _failed_step(run_id):
    try:
        for j in _get(f"{API}/runs/{run_id}/jobs").get("jobs") or []:
            for s in j.get("steps") or []:
                if s.get("conclusion") == "failure": return s.get("name")
    except Exception:
        pass
    return None


def check(now=None):
    """Renvoie la liste des messages a envoyer (et met a jour data/sentinel.json)."""
    now = now or datetime.now(timezone.utc)
    st = load()
    inc = st.setdefault("incidents", {})
    msgs = []
    for wf, (name, n_fail) in WATCH.items():
        try:
            runs = _get(f"{API}/workflows/{wf}/runs", {"per_page": 6}).get("workflow_runs") or []
        except Exception as e:
            print(f"  [!] sentinelle {wf} : {type(e).__name__}")
            continue
        done = [r for r in runs if r.get("status") == "completed" and r.get("conclusion") not in ("cancelled", "skipped")]
        key = f"fail:{wf}"
        if len(done) >= n_fail and all(r.get("conclusion") == "failure" for r in done[:n_fail]):
            last = done[0]
            if key not in inc:
                step = _failed_step(last["id"])
                msgs.append(f"⚠️ <b>Sentinelle</b> : {name} en échec"
                            + (f" ({n_fail} fois de suite)" if n_fail > 1 else "")
                            + f", dernier essai {_t(last['created_at']).strftime('%d/%m %H:%M')} UTC"
                            + (f", étape « {step} »" if step else "") + f".\n{last.get('html_url', '')}")
                inc[key] = {"run": last["id"], "since": last["created_at"], "name": name, "step": step}
        elif key in inc and done and done[0].get("conclusion") == "success":
            msgs.append(f"✅ <b>Sentinelle</b> : {name} refonctionne (en panne depuis le "
                        f"{_t(inc[key]['since']).strftime('%d/%m %H:%M')} UTC).")
            inc.pop(key)
        # retards : bot de nuit pas lance, picks sans succes recent
        if wf == "improve.yml":
            day, key2 = now.strftime("%Y-%m-%d"), "late:improve.yml"
            today = [r for r in runs if r.get("created_at", "")[:10] == day]
            if now.hour * 60 + now.minute >= NIGHT_LATE and not today:
                if (inc.get(key2) or {}).get("day") != day:
                    msgs.append(f"⚠️ <b>Sentinelle</b> : le bot d'amélioration n'a pas tourné cette nuit ({day}), "
                                "même après la relance automatique de 6 h 30 UTC.")
                    inc[key2] = {"day": day, "since": now.isoformat(timespec="minutes"), "name": name}
            elif today and key2 in inc:
                inc.pop(key2)
        if wf == "picks-cron.yml":
            key3 = "stale:picks-cron.yml"
            ok = [r for r in runs if r.get("conclusion") == "success"]
            stale = runs and (not ok or now - _t(ok[0]["updated_at"]) > PICKS_STALE)
            if stale and key3 not in inc:
                since = _t(ok[0]["updated_at"]) if ok else None
                msgs.append("⚠️ <b>Sentinelle</b> : aucune mise à jour réussie des picks depuis "
                            + (f"{int((now - since).total_seconds() // 3600)} h" if since else "longtemps")
                            + " (planification GitHub en retard ou workflow bloqué).")
                inc[key3] = {"since": (since or now).isoformat(timespec="minutes"), "name": name}
            elif not stale and key3 in inc:
                inc.pop(key3)
    st["checked_at"] = now.isoformat(timespec="minutes")
    save(st)
    return msgs


def status_html(esc=lambda s: s):
    """Ligne d'etat pour la carte du bot (page Aujourd'hui)."""
    st = load()
    inc = st.get("incidents") or {}
    if not st.get("checked_at"): return ""
    if not inc:
        return "🛡️ Sentinelle : aucun incident en cours sur les bots."
    return "🛡️ Sentinelle : " + " ; ".join(
        f"⚠️ {esc(v.get('name', k))}" + (" en échec" if k.startswith("fail") else " en retard") for k, v in inc.items())


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8")
    for m in check(): print(m, "\n")
    print(status_html())
