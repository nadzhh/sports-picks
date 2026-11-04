"""
chat_bot.py — relais du chat du site (bouton 💬) vers Claude Code, avec l'abonnement Claude de Wilson.

Pas de serveur ni de cle d'API : le site ecrit le message dans Firestore (users/<uid>/chat, protege par
la connexion Firebase deja en place), ce relais (workflow chat.yml) le lit, fait repondre Claude Code
(`claude -p`, jeton CLAUDE_CODE_OAUTH_TOKEN cree par `claude setup-token`) dans le depot a jour, et ecrit
la reponse au meme endroit ; le site l'affiche en direct (onSnapshot).

Securite :
  - le relais se connecte avec un compte Firebase dedie (CHAT_BOT_EMAIL / CHAT_BOT_PASSWORD) que les
    regles Firestore autorisent sur users/*/chat seulement ;
  - il ne repond qu'aux comptes de CHAT_ALLOWED_UIDS (Wilson) : un inscrit quelconque ne consomme rien ;
  - Claude n'a que des outils de lecture (Read, Grep, Glob ; ni Bash ni reseau), limites au depot,
    et un environnement sans les secrets du relais ;
  - depot public : rien de la conversation n'est ecrit dans les journaux du workflow (compteurs seulement).

    python chat_bot.py --loop --max-minutes 340     # CI
    python chat_bot.py --once                        # un passage (test)
"""
import argparse, json, os, shutil, subprocess, sys, time
from datetime import datetime, timezone
from pathlib import Path

import requests

API_KEY = "AIzaSyAdZ3GvbYVapF0gqyh5O7mN1xC0sTT1yJo"   # cle web Firebase, publique (deja dans index.html)
PROJECT = "sportspick-c6955"
DOCS = f"https://firestore.googleapis.com/v1/projects/{PROJECT}/databases/(default)/documents"
SYSTEM_FILE = Path("chat/system.md")
POLL_S = 6                 # attente entre deux lectures de Firestore
PULL_EVERY_S = 300         # depot remis a jour (donnees publiees par la veille et le cron)
CLAUDE_TIMEOUT_S = 300
HISTORY = 16               # messages precedents donnes a Claude
MAX_PER_HOUR = 30          # garde-fou sur l'abonnement
CONTINUE_FLAG = Path(".chat_continue")
# Lecture seule, sans Bash : Claude Code autorise d'office certaines commandes « sans risque » (echo...),
# on ne depend pas de cette liste. Les bilans sont calcules par le relais (state_summary).
# Lecture limitee au depot (les regles Read valent aussi pour Grep / Glob) : sans cela Read ouvrait n'importe
# quel fichier, dont /proc/self/environ qui contient le jeton de l'abonnement (verifie le 04/11/2026).
CLAUDE_TOOLS = "Read,Grep,Glob"
CLAUDE_ALLOWED = "Read(./**)"
# (pas « ~/** » : en CI le depot est DANS le dossier personnel, /home/runner/work/... — 04/11/2026)
CLAUDE_DENIED = ("Read(//proc/**),Read(//etc/**),Read(~/.claude/**),Read(~/.config/**),Read(~/.ssh/**),"
                 "Read(~/.gitconfig),Read(~/work/_temp/**),Read(./.git/**),Read(./.env)")


# ─── Firebase (REST) ─────────────────────────────────────────────────────────
class Fire:
    def __init__(self, email, password):
        self.email, self.password, self.tok, self.exp, self.refresh = email, password, None, 0, None

    def token(self):
        if self.tok and time.time() < self.exp - 300:
            return self.tok
        if self.refresh:
            r = requests.post(f"https://securetoken.googleapis.com/v1/token?key={API_KEY}",
                              data={"grant_type": "refresh_token", "refresh_token": self.refresh}, timeout=20)
            if r.ok:
                d = r.json()
                self.tok, self.refresh, self.exp = d["id_token"], d["refresh_token"], time.time() + int(d["expires_in"])
                return self.tok
        r = requests.post(f"https://identitytoolkit.googleapis.com/v1/accounts:signInWithPassword?key={API_KEY}",
                          json={"email": self.email, "password": self.password, "returnSecureToken": True}, timeout=20)
        if not r.ok:
            raise RuntimeError(f"connexion Firebase du relais refusee ({r.status_code})")
        d = r.json()
        self.tok, self.refresh, self.exp = d["idToken"], d["refreshToken"], time.time() + int(d["expiresIn"])
        return self.tok

    def _h(self):
        return {"Authorization": f"Bearer {self.token()}"}

    def query(self, uid, where=None, order_desc=False, limit=10):
        q = {"from": [{"collectionId": "chat"}], "limit": limit}
        if where:
            q["where"] = {"fieldFilter": {"field": {"fieldPath": where[0]}, "op": "EQUAL", "value": _val(where[1])}}
        if order_desc:
            q["orderBy"] = [{"field": {"fieldPath": "at"}, "direction": "DESCENDING"}]
        r = requests.post(f"{DOCS}/users/{uid}:runQuery", headers=self._h(), json={"structuredQuery": q}, timeout=20)
        r.raise_for_status()
        return [_doc(x["document"]) for x in r.json() if "document" in x]

    def add(self, uid, fields):
        r = requests.post(f"{DOCS}/users/{uid}/chat", headers=self._h(),
                          json={"fields": {k: _val(v) for k, v in fields.items()}}, timeout=20)
        r.raise_for_status()

    def patch(self, name, fields):
        mask = "&".join(f"updateMask.fieldPaths={k}" for k in fields)
        r = requests.patch(f"https://firestore.googleapis.com/v1/{name}?{mask}", headers=self._h(),
                           json={"fields": {k: _val(v) for k, v in fields.items()}}, timeout=20)
        r.raise_for_status()


def _val(v):
    if isinstance(v, bool): return {"booleanValue": v}
    if isinstance(v, int): return {"integerValue": str(v)}
    if isinstance(v, datetime): return {"timestampValue": v.isoformat().replace("+00:00", "Z")}
    return {"stringValue": str(v)}


def _doc(d):
    out = {"_name": d["name"], "_id": d["name"].rsplit("/", 1)[-1]}
    for k, v in (d.get("fields") or {}).items():
        out[k] = next(iter(v.values()))
    return out


# ─── Etat du site resume pour Claude ─────────────────────────────────────────
def _paris_now():
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo("Europe/Paris"))
    except Exception:
        return datetime.now(timezone.utc)


def state_summary():
    """Quelques chiffres a jour, pour que les questions simples n'obligent pas Claude a fouiller."""
    out = []
    try:
        b = json.loads(Path("data/nba_bankroll.json").read_text(encoding="utf-8"))
        bal, w, l = b["start"], 0, 0
        for x in b["bets"]:
            if x["status"] == "won": bal += x["stake"] * (x["odds"] - 1); w += 1
            elif x["status"] == "lost": bal -= x["stake"]; l += 1
        opn = [x for x in b["bets"] if x["status"] == "open"]
        out.append(f"Bankroll IA : {bal:.2f} EUR (depart {b['start']:.0f}), {w}-{l} regles, {len(opn)} paris en cours "
                   f"({sum(x['stake'] for x in opn):.2f} EUR), mode {(b.get('mode') or {}).get('mode', '?')}.")
    except Exception:
        pass
    try:
        P = json.loads(Path("data/nba_sim_picks.json").read_text(encoding="utf-8"))["picks"].values()
        done = [p for p in P if p.get("result") in ("win", "loss")]
        dec = lambda a: 1 + (a / 100 if a > 0 else 100 / -a)
        u = sum(dec(p["first_price"]) - 1 if p["result"] == "win" else -1 for p in done)
        w = sum(p["result"] == "win" for p in done)
        out.append(f"Selection du simulateur NBA (suivi reel) : {w}-{len(done) - w}, {u:+.1f} u sur {len(done)} picks.")
    except Exception:
        pass
    # bilans des deux dernieres soirees NBA terminees (sans la liste des picks) et du simulateur foot
    try:
        from datetime import timedelta
        import nba_sim_bilan
        nights = 0
        for back in range(1, 6):
            d = (_paris_now() - timedelta(days=back)).strftime("%Y-%m-%d")
            lines = [l for l in nba_sim_bilan.text_report(d).splitlines()
                     if l.strip() and not l.lstrip().startswith(("GAGNE", "PERDU", "rembourse"))]
            if not lines or "(0/0 matchs" in lines[0]: continue
            out.append("\n".join(lines[:30])); nights += 1
            if nights == 2: break
    except Exception:
        pass
    try:
        import foot_sim_bilan
        out.append("Simulateur foot : " + " ".join(foot_sim_bilan.insights()))
    except Exception:
        pass
    return "\n\n".join(out)


# ─── Claude Code ─────────────────────────────────────────────────────────────
MEMO_MAX = 40   # lecons / preferences retenues des echanges (role « memo » dans users/<uid>/chat)


def ask_claude(history, msg, memos=()):
    convo = "\n\n".join(f"{'Wilson' if h.get('role') == 'user' else 'Claude'} : {h.get('text', '')}" for h in history)
    try:
        import chat_context
        brief = chat_context.briefing()
    except Exception as e:
        brief = f"(briefing indisponible : {type(e).__name__})"
    prompt = (f"Date et heure (Paris) : {_paris_now():%A %d/%m/%Y %H:%M}.\n"
              f"Page du site ouverte par Wilson : {msg.get('ctx') or 'inconnue'}.\n\n"
              + (("Ta mémoire (retenu des échanges précédents, du plus ancien au plus récent) :\n"
                  + "\n".join(f"- {m}" for m in memos) + "\n\n") if memos else "")
              + f"{brief}\n\nBilans des dernières soirées :\n{state_summary()}\n\n"
              + (f"Conversation precedente :\n{convo}\n\n" if convo else "")
              + f"Nouveau message de Wilson :\n{msg.get('text', '')}")
    # environnement minimal : le jeton de l'abonnement, pas les secrets du relais
    env = {k: os.environ[k] for k in ("PATH", "HOME", "LANG", "CLAUDE_CODE_OAUTH_TOKEN", "SYSTEMROOT", "USERPROFILE",
                                      "APPDATA", "TEMP", "TMP") if k in os.environ}
    env["PYTHONUTF8"] = "1"
    cmd = [shutil.which("claude") or "claude", "-p", "--output-format", "json", "--no-session-persistence",
           "--tools", CLAUDE_TOOLS, "--allowedTools", CLAUDE_ALLOWED, "--disallowedTools", CLAUDE_DENIED,
           # refuse tout ce qui n'est pas dans CLAUDE_ALLOWED, sans tenir compte des reglages du poste ou du depot
           "--permission-mode", "dontAsk", "--setting-sources", "project",
           "--append-system-prompt", SYSTEM_FILE.read_text(encoding="utf-8")]
    if os.environ.get("CHAT_MODEL"):
        cmd += ["--model", os.environ["CHAT_MODEL"]]
    r = subprocess.run(cmd, input=prompt, capture_output=True, text=True, encoding="utf-8", env=env,
                       timeout=CLAUDE_TIMEOUT_S)
    try:
        d = json.loads(r.stdout)
    except ValueError:
        raise RuntimeError(f"Claude Code sans reponse lisible (code {r.returncode})")
    if d.get("is_error"):
        raise RuntimeError(f"Claude Code : {str(d.get('result') or d.get('subtype'))[:200]}")
    return (d.get("result") or "").strip() or "(réponse vide)"


# ─── Boucle ──────────────────────────────────────────────────────────────────
def split_memos(txt):
    """Retire de la reponse les lignes « MEMO: ... » (ce que Claude veut retenir) : (texte affiche, [memos])."""
    keep, memos = [], []
    for line in txt.splitlines():
        t = line.strip().lstrip("*_` ").rstrip("*_` ")
        if t.upper().startswith("MEMO:") or t.upper().startswith("MEMO :"):
            m = t.split(":", 1)[1].strip()
            if m: memos.append(m[:300])
        else:
            keep.append(line)
    return "\n".join(keep).strip(), memos[:3]


def handle(fire, uid, msg, recent):
    fire.patch(msg["_name"], {"status": "thinking"})
    memos = sorted(fire.query(uid, where=("role", "memo"), limit=200), key=lambda m: m.get("at") or "")
    memos = [m.get("text", "") for m in memos][-MEMO_MAX:]
    hist = [h for h in fire.query(uid, order_desc=True, limit=HISTORY + 30) if h["_id"] != msg["_id"]
            and h.get("role") != "memo"]
    hist.sort(key=lambda h: h.get("at") or "")
    cut = max((i for i, h in enumerate(hist) if h.get("role") == "reset"), default=-1)   # « nouvelle conversation »
    # questions posees avant celle-ci, et toutes les reponses deja ecrites (meme apres, si deux messages se suivent)
    hist = [h for h in hist[cut + 1:] if h.get("role") == "assistant"
            or (h.get("role") == "user" and (h.get("at") or "") < (msg.get("at") or "~"))]
    try:
        txt, st = ask_claude(hist[-HISTORY:], msg, memos), "done"
    except subprocess.TimeoutExpired:
        txt, st = "Je n'ai pas pu terminer à temps (plus de 5 min). Reformule ou découpe la question.", "error"
    except Exception as e:
        txt, st = f"Erreur du relais : {e}", "error"
    txt, new_memos = split_memos(txt)
    for m in new_memos:   # memoire durable : relue a chaque question (« Nouvelle » ne l'efface pas)
        fire.add(uid, {"role": "memo", "text": m, "at": datetime.now(timezone.utc), "reply_to": msg["_id"]})
    fire.add(uid, {"role": "assistant", "text": txt or "(réponse vide)", "at": datetime.now(timezone.utc), "reply_to": msg["_id"]})
    fire.patch(msg["_name"], {"status": st})
    recent.append(time.time())
    print(f"  [chat] reponse envoyee ({st}, {len(txt)} car.)", flush=True)


def cycle(fire, uids, recent):
    n = 0
    for uid in uids:
        for msg in sorted(fire.query(uid, where=("status", "pending"), limit=5), key=lambda m: m.get("at") or ""):
            recent[:] = [t for t in recent if time.time() - t < 3600]
            if len(recent) >= MAX_PER_HOUR:
                fire.add(uid, {"role": "assistant", "at": datetime.now(timezone.utc), "reply_to": msg["_id"],
                               "text": f"Limite de {MAX_PER_HOUR} messages par heure atteinte : réessaie un peu plus tard."})
                fire.patch(msg["_name"], {"status": "error"})
                continue
            handle(fire, uid, msg, recent); n += 1
    return n


def git_pull(start):
    """Donnees a jour ; vrai si le code du relais a change depuis le demarrage (il se relance alors)."""
    subprocess.run(["git", "pull", "--ff-only", "-q", "origin", "master"], capture_output=True, timeout=120)
    r = subprocess.run(["git", "diff", "--quiet", start, "HEAD", "--", "chat_bot.py", "chat", "chat_context.py",
                        "nba_sim_bilan.py", "foot_sim_bilan.py", "notify.py", "nba_bankroll.py"], capture_output=True, timeout=60)
    return bool(start) and r.returncode == 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--loop", action="store_true")
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--max-minutes", type=float, default=340)
    a = ap.parse_args()
    email, pw = os.environ.get("CHAT_BOT_EMAIL"), os.environ.get("CHAT_BOT_PASSWORD")
    uids = [u.strip() for u in (os.environ.get("CHAT_ALLOWED_UIDS") or "").split(",") if u.strip()]
    if not (email and pw and uids and os.environ.get("CLAUDE_CODE_OAUTH_TOKEN")):
        print("[chat] secrets manquants (CHAT_BOT_EMAIL, CHAT_BOT_PASSWORD, CHAT_ALLOWED_UIDS, CLAUDE_CODE_OAUTH_TOKEN) : arret")
        return
    fire, recent, t0, pulled, errs = Fire(email, pw), [], time.time(), time.time(), 0
    fire.token()
    start = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    print(f"[chat] relais pret ({len(uids)} compte(s) autorise(s))", flush=True)
    while True:
        try:
            cycle(fire, uids, recent); errs = 0
        except Exception as e:
            errs += 1
            print(f"  [!] chat : {type(e).__name__}", flush=True)
            if errs >= 20: raise
            time.sleep(min(60, 5 * errs))
        if a.once or not a.loop: break
        if time.time() - t0 > a.max_minutes * 60:
            CONTINUE_FLAG.write_text("1")
            print("[chat] fin du budget : relance du workflow", flush=True)
            break
        if time.time() - pulled > PULL_EVERY_S:
            pulled = time.time()
            if git_pull(start):
                # Redemarrage DANS le meme job avec le nouveau code : la relance par un nouveau run (gh workflow
                # run) a echoue le 04/11/2026 et le cron horaire de GitHub n'est pas fiable -> chat coupe 2 h 30.
                left = a.max_minutes - (time.time() - t0) / 60
                print("[chat] nouveau code : redemarrage du relais", flush=True)
                os.execv(sys.executable, [sys.executable, os.path.abspath(__file__), "--loop", "--max-minutes", f"{max(left, 10):.0f}"])
        time.sleep(POLL_S)


if __name__ == "__main__":
    main()
