"""
nba_live.py — Veille NBA : resimule un match des qu'une information le justifie, puis publie
la section « Simulation NBA » (nba_sim.html, chargee par index.html) sans regenerer tout le site.

Declencheurs, par match a venir dans les 36 h (cycle toutes les 10 min, plus serre avant les coups d'envoi) :
  - nouveau match, ou props DraftKings publiees depuis la derniere simulation : les picks doivent
    sortir le plus tot possible (l'avantage mesure est a la cote d'OUVERTURE) ;
  - statut d'un joueur modifie : rapport officiel NBA (toutes les 15 min), ESPN sinon ;
  - ligne Vegas deplacee (ecart >= 1,5 pt, total >= 3 pts) : le marche a recu une information ;
  - derniere simulation d'avant-match (coup d'envoi dans moins de 35 min : statuts definitifs) ;
  - simulation de plus de 3 h (cotes des props a jour pour le suivi des picks).
Matchs termines : resultats des picks et historique (nba_sim_bilan) mis a jour.

Usage : python nba_live.py              # un cycle
        python nba_live.py --loop       # veille tant qu'un match arrive dans les 36 h (budget --max-minutes)
        python nba_live.py --no-push    # sans commit / push (tests)
"""
import argparse, json, os, shutil, subprocess, sys, tempfile, time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import nba_sim_run as R
from nba_boxdb import BASE, _get, _lines, load_season, update_season
from nba_client import team_roster
from nba_props_hist import fetch_game_props
from nba_sim_site import FRAGMENT, write_fragment
import nba_bankroll as BK
import nba_bankroll_site as BS
import foot_db
import foot_sim_run as FR
import foot_sim_site as FS
import foot_sim_bilan as FB
import ev_scan as EV

PUBLISHED = [FRAGMENT, R.OUT_PATH, str(R.PICKS_PATH), "data/nba_sim_history.json", BS.FRAGMENT, str(BK.BANK_PATH),
             FS.FRAGMENT, str(FR.OUT_PATH), str(FB.HIST_PATH), str(EV.PATH)]
# Foot (Top 5 + coupes d'Europe) : base mise a jour par tranches (premiere constitution en plusieurs
# passages), simulation toutes les 30 min, a chaque cycle quand un match approche (composition officielle)
FOOT_SEASONS = ("2026/2027", "2025/2026")   # saison en cours d'abord
FOOT_EVERY, FOOT_DB_BUDGET, FOOT_REFRESH = 30 * 60, 6 * 60, timedelta(hours=3)
LIVE_KEEP, FOOT_LIVE_KEEP = timedelta(hours=4), timedelta(hours=3)   # suivi en direct apres le coup d'envoi
HORIZON = timedelta(hours=36)
FINAL_BEFORE = timedelta(minutes=35)
REFRESH_AGE = timedelta(hours=3)
PROPS_AGE = timedelta(hours=1)
SPREAD_MOVE, TOTAL_MOVE = 1.5, 3.0
EVENTS_KEPT = 15
CONTINUE_FLAG = ".nba_live_continue"   # lu par le workflow : relancer la veille a la fin du job
STATUS_FR = {"out": "absent", "doubtful": "douteux", "questionable": "incertain", "probable": "probable",
             "day-to-day": "au jour le jour", "suspension": "suspendu", "": "disponible"}


def _t(iso):
    return datetime.fromisoformat(iso.replace("Z", "+00:00"))


def _status_diff(old_sig, new_sts, g):
    """Changements de statut lisibles : « T. Haliburton (IND) incertain → absent »."""
    old = dict(x.split(":", 1) for x in (old_sig or "").split("|") if ":" in x)
    new = {k: v for side in ("home", "away") for k, v in new_sts[side]["status"].items()}
    names, team = {}, {}
    for side in ("home", "away"):
        for p in team_roster(str(g[side].get("id")), ttl=12 * 3600):
            names[p["PLAYER_ID"]], team[p["PLAYER_ID"]] = p["PLAYER_NAME"], g[side].get("abbreviation", "")
    out = []
    for pid in sorted(set(old) | set(new)):
        a, b = old.get(pid, ""), new.get(pid, "")
        if a != b and pid in names:
            out.append(f"{names[pid]} ({team[pid]}) {STATUS_FR.get(a, a)} → {STATUS_FR.get(b, b)}")
    return out


def due_games(games, prev, report, now):
    """[(match, [raisons])] a resimuler."""
    due = []
    for g in games:
        p, why = prev.get(g["gid"]), []
        tip = _t(g["date"])
        if not p:
            why.append("première simulation")
        else:
            age = now - _t(p["sim_at"]) if p.get("sim_at") else REFRESH_AGE
            if tip - now <= FINAL_BEFORE and not p.get("final_sim"):
                why.append("dernière simulation avant le match")
            if age >= REFRESH_AGE:
                why.append("cotes rafraîchies")
            if not p.get("props") and age >= PROPS_AGE and fetch_game_props(g["gid"]):
                why.append("props DraftKings publiées")
            summary = _get(f"{BASE}/summary?event={g['gid']}") or {}
            try:
                sts = R.statuses(g, report, summary)
                if R.signature(sts) != p.get("sig"):
                    why += _status_diff(p.get("sig"), sts, g) or ["statuts modifiés"]
            except Exception as e:
                print(f"  [!] statuts {g['gid']} : {type(e).__name__}: {e}")
            L, L0 = _lines(summary, str(g["home"].get("id"))) or {}, p.get("lines") or {}
            if L.get("home_spread") is not None and L0.get("home_spread") is not None \
                    and abs(L["home_spread"] - L0["home_spread"]) >= SPREAD_MOVE:
                why.append(f"écart Vegas {L0['home_spread']:+g} → {L['home_spread']:+g}")
            if L.get("total") and L0.get("total") and abs(L["total"] - L0["total"]) >= TOTAL_MOVE:
                why.append(f"total Vegas {L0['total']:g} → {L['total']:g}")
        if why:
            due.append((g, why))
    return due


def _git(*a, check=True):
    r = subprocess.run(["git", *a], capture_output=True, text=True)
    if check and r.returncode:
        raise RuntimeError(f"git {' '.join(a)} : {r.stderr.strip()[:300]}")
    return r


def publish(msg):
    """Commit des seuls fichiers NBA (jamais index.html : le cron principal le possede, d'ou aucun
    conflit possible entre les deux workflows), push master puis gh-pages."""
    _git("add", "-f", *[f for f in PUBLISHED if os.path.exists(f)])
    _git("add", "-A", "data/replays")
    if not _git("diff", "--cached", "--quiet", check=False).returncode:
        print("  [publication] rien de nouveau"); return False
    _git("commit", "-q", "-m", msg)
    for i in range(5):
        if _git("pull", "-q", "--rebase", "--autostash", "origin", "master", check=False).returncode:
            # Ne devrait pas arriver (fichiers disjoints) : on repart de master avec nos fichiers
            _git("rebase", "--abort", check=False)
            tmp = Path(tempfile.mkdtemp())
            for f in PUBLISHED + ["data/replays"]:
                if os.path.exists(f): shutil.copytree(f, tmp / f) if os.path.isdir(f) else (
                    (tmp / f).parent.mkdir(parents=True, exist_ok=True), shutil.copy2(f, tmp / f))
            _git("reset", "-q", "--hard", "origin/master")
            for f in PUBLISHED + ["data/replays"]:
                src = tmp / f
                if src.is_dir(): shutil.rmtree(f, ignore_errors=True); shutil.copytree(src, f)
                elif src.exists(): shutil.copy2(src, f)
            _git("add", "-f", *[f for f in PUBLISHED if os.path.exists(f)])
            _git("add", "-A", "data/replays")
            _git("commit", "-q", "-m", msg, check=False)
        if not _git("push", "-q", "origin", "HEAD:master", check=False).returncode:
            _git("push", "-q", "--force", "origin", "HEAD:gh-pages", check=False)
            print(f"  [publication] {msg}"); return True
        time.sleep(3 * (i + 1))
    print("  [!] publication impossible apres 5 tentatives"); return False


class Live:
    def __init__(self, n_sims, push, update=True):
        self.n_sims, self.push, self.update = n_sims, push, update
        self.day = datetime.now(timezone.utc).date()
        self.year, self.st, self.profiles = R.prepare(self.day, update)
        self.n_box = len(load_season(self.year))
        self.box_at = time.time()
        R._clean_replays()
        self.foot_st, self.foot_at = None, 0.0

    def refresh_box(self):
        """Matchs termines depuis le dernier passage -> base, etat du modele, resultats."""
        if not self.update or time.time() - self.box_at < 20 * 60: return False
        self.box_at = time.time()
        update_season(self.year)
        n = len(load_season(self.year))
        if n == self.n_box: return False
        print(f"  [base] {n - self.n_box} match(s) termine(s) : etat du modele reconstruit")
        self.n_box, self.st = n, R.build_state(self.year)
        return True

    def cycle(self):
        now = datetime.now(timezone.utc)
        new_results = self.refresh_box()
        games = [g for g in R.upcoming_games(now.date()) if _t(g["date"]) - now <= HORIZON]
        games = list({g["gid"]: g for g in games}.values())
        prev = {g["gid"]: g for g in R.load_out()}
        report = R.latest_report()
        due = due_games(games, prev, report, now)
        for g, why in due:
            print(f"  -> {g['away'].get('abbreviation')} @ {g['home'].get('abbreviation')} : {' ; '.join(why)}")
        # Video du match type gardee si seuls les cotes / props ont change
        keep_rp = {g["gid"]: prev[g["gid"]]["replay"] for g, why in due
                if prev.get(g["gid"], {}).get("replay") and not self._reroll(why)}
        fresh = R.sim_games(self.st, [g for g, _ in due], self.n_sims, self.profiles, report, keep_rp) if due else []
        whys = {g["gid"]: why for g, why in due}
        for r in fresh:
            old = prev.get(r["gid"]) or {}
            r["final_sim"] = _t(r["date"]) - now <= FINAL_BEFORE
            ev = old.get("events") or []
            if old:   # la premiere simulation n'est pas un evenement
                ev = ev + [{"at": r["sim_at"], "txt": w} for w in whys[r["gid"]]]
            r["events"] = ev[-EVENTS_KEPT:]
        live = {g["gid"] for g in games}
        # matchs commences : gardes 4 h apres le coup d'envoi pour le suivi en direct (score ESPN dans le navigateur)
        keep = {gid: g for gid, g in prev.items() if gid in live or now - _t(g["date"]) < LIVE_KEEP}
        keep.update({r["gid"]: r for r in fresh})
        changed = bool(fresh) or new_results or set(keep) != set(prev)
        if changed:
            R.save(list(keep.values()), fresh, self.year)
            write_fragment()
        bank = self.bankroll(fresh)
        foot = self.foot()
        ev = self.ev()
        if (foot or ev) and not (changed or bank) and self.push:
            publish("Foot live : " + " ; ".join(x for x in (foot, ev) if x))
        if (changed or bank) and self.push:
            parts = [f"{r['away']['abbr']}@{r['home']['abbr']}" for r in fresh]
            publish("NBA live : " + (f"{len(fresh)} match(s) resimule(s) ({', '.join(parts[:6])})" if fresh
                                     else "resultats et liste des matchs" if changed else "bankroll") + bank)
        return games

    def telegram(self, *msgs):
        """Envoi Telegram : uniquement depuis GitHub Actions avec publication (jamais d'un poste local,
        dont le .env contient aussi le jeton du bot)."""
        if not (self.push and os.environ.get("GITHUB_ACTIONS")): return
        try:
            from notify import tg_send
            for m in msgs:
                if not m: continue
                parts, cur = [], ""
                for block in m.split("\n\n"):   # limite Telegram : 4 096 caracteres par message
                    if cur and len(cur) + len(block) > 3800: parts.append(cur); cur = ""
                    cur = f"{cur}\n\n{block}" if cur else block
                parts.append(cur)
                for part in parts: print("  [telegram] " + ("envoye" if tg_send(part) else "ECHEC"))
        except Exception as e:
            print(f"  [!] telegram : {type(e).__name__}: {e}")

    def bankroll(self, fresh):
        """Bankroll IA : regle les paris termines, place les nouveaux sur les matchs simules a l'instant
        (cote actuelle ; une simulation plus ancienne aurait une cote perimee)."""
        try:
            st = BK.load()
            before = {b["id"] for b in st["bets"] if b["status"] == "open"}
            n_set = BK.settle(st, load_season(self.year))
            placed = BK.decide(st, fresh) if fresh else []
            if not (n_set or placed or not Path(BS.FRAGMENT).exists()): return ""
            BK.save(st); BS.write_fragment()
            settled = [b for b in st["bets"] if b["id"] in before and b["status"] != "open"]
            self.telegram(BK.telegram_placed(placed, st) if placed else None,
                          BK.telegram_settled(settled, st) if settled else None)
            for b in placed:
                print(f"  [bankroll] {b['kind']} {b['stake']:.2f} EUR @ {b['odds']:.2f} : "
                      + " + ".join(f"{l['name']} {l['side']}{l['line']:g} {l['stat']}" for l in b["legs"]))
            print(f"  [bankroll] capital {BK.equity(st):.2f} EUR, {n_set} pari(s) regle(s), {len(placed)} place(s)")
            return f" ; bankroll : {len(placed)} pari(s) place(s), {n_set} regle(s)"
        except Exception as e:
            print(f"  [!] bankroll : {type(e).__name__}: {e}")
            return ""

    def ev(self):
        """Value bets (cotes francaises vs cote juste Pinnacle) : ~2 balayages par jour (quota The Odds API)."""
        if not self.update or not EV.due(): return ""
        try:
            new = EV.scan()
            write_fragment(); FS.write_fragment()      # cartes « Value bets » et calculateur des vues Picks
            if new:
                self.telegram(EV.telegram_msg(new))
                st = BK.load()                         # la Bankroll IA joue les value bets (avantage valide)
                placed = BK.decide_value(st, new)
                if placed:
                    BK.save(st); BS.write_fragment()
                    self.telegram(BK.telegram_placed(placed, st))
            return f"value bets : {len(new)} nouveau(x)"
        except Exception as e:
            print(f"  [!] value bets : {type(e).__name__}: {e}")
            return ""

    def foot(self):
        """Simulation foot : nouveaux matchs, simulations de plus de 3 h, et composition officielle
        attendue a moins de 2 h du coup d'envoi. Renvoie un resume si quelque chose a ete publie."""
        try:
            now = datetime.now(timezone.utc)
            prev = {g["id"]: g for g in (json.loads(FR.OUT_PATH.read_text(encoding="utf-8")).get("games") or [])}                 if FR.OUT_PATH.exists() else {}
            soon = any(timedelta(0) < _t(g["ko"]) - now <= timedelta(hours=2) for g in prev.values())
            if not soon and time.time() - self.foot_at < FOOT_EVERY: return ""
            self.foot_at = time.time()
            added = foot_db.update(list(foot_db.LEAGUES), list(FOOT_SEASONS), budget=FOOT_DB_BUDGET) if self.update else 0
            if added or self.foot_st is None:
                self.foot_st = FR.build_state()
            games = FR.upcoming(48)
            due = []
            for g in games:
                p = prev.get(g["id"])
                if not p or now - _t(p["sim_at"]) >= FOOT_REFRESH:
                    due.append(g)
                elif _t(g["ko"]) - now <= timedelta(minutes=120) and now - _t(p["sim_at"]) >= timedelta(minutes=15)                         and "officielle" not in (p["home_info"]["lineup"], p["away_info"]["lineup"]):
                    due.append(g)      # composition officielle attendue : on reverifie
            fresh = []
            for g in due:
                try:
                    r = FR.run_game(self.foot_st, g, self.n_sims)
                    old = prev.get(g["id"])
                    if old and r["home_info"]["lineup"] == old["home_info"]["lineup"]                             and r["away_info"]["lineup"] == old["away_info"]["lineup"] and now - _t(old["sim_at"]) < FOOT_REFRESH:
                        continue   # composition toujours pas officielle : rien de neuf a publier
                    fresh.append(r)
                except Exception as e:
                    print(f"  [!] foot {g['id']} : {type(e).__name__}: {e}")
            live = {g["id"] for g in games}
            keep = {gid: g for gid, g in prev.items() if gid in live or now - _t(g["ko"]) < FOOT_LIVE_KEEP}
            keep.update({r["id"]: r for r in fresh})
            FB.archive(fresh)                      # historique : derniere simulation d'avant-match
            resolved = FB.resolve()                # et resultats des matchs termines
            if not fresh and not resolved and set(keep) == set(prev): return ""
            FR.OUT_PATH.write_text(json.dumps({"generated_at": now.isoformat(timespec="seconds"),
                                               "games": sorted(keep.values(), key=lambda g: g["ko"])}, ensure_ascii=False),
                                   encoding="utf-8")
            FS.write_fragment()
            print(f"  [foot] {len(fresh)} match(s) simule(s), {len(keep)} affiches, base +{added}, {resolved} resultat(s)")
            return f"{len(fresh)} match(s) foot simule(s)"
        except Exception as e:
            print(f"  [!] foot : {type(e).__name__}: {e}")
            return ""

    @staticmethod
    def _reroll(why):
        return any(not w.startswith(("cotes rafraîchies", "props DraftKings", "écart Vegas", "total Vegas")) for w in why)

    def next_wake(self, games, interval):
        """Prochain reveil : intervalle normal, ou juste a temps pour la derniere simulation d'un match."""
        now = datetime.now(timezone.utc)
        wake = now + timedelta(seconds=interval)
        for g in games:
            t = _t(g["date"]) - FINAL_BEFORE + timedelta(minutes=2)
            if now < t < wake: wake = t
        return max((wake - now).total_seconds(), 60)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--loop", action="store_true")
    ap.add_argument("--interval", type=int, default=600, help="secondes entre deux cycles a moins de 6 h d'un match")
    ap.add_argument("--idle", type=int, default=1800, help="secondes entre deux cycles sinon (props, statuts)")
    ap.add_argument("--max-minutes", type=int, default=330)
    ap.add_argument("--sims", type=int, default=10000)
    ap.add_argument("--no-push", action="store_true")
    ap.add_argument("--no-update", action="store_true")
    a = ap.parse_args()
    t0 = time.time()
    live = Live(a.sims, not a.no_push, not a.no_update)
    Path(CONTINUE_FLAG).unlink(missing_ok=True)
    while True:
        print(f"=== Cycle {datetime.now(timezone.utc):%H:%M} UTC ===", flush=True)
        games = live.cycle()
        now = datetime.now(timezone.utc)
        soon = [g for g in games if timedelta(0) < _t(g["date"]) - now <= timedelta(hours=6)]
        left = a.max_minutes * 60 - (time.time() - t0)
        if not a.loop or not games:
            print("Veille terminee" + (" : aucun match dans les 36 h" if a.loop else ""))
            break
        if left < 15 * 60:
            # Budget du job epuise, des matchs arrivent : le workflow relance la veille (nba-live.yml)
            Path(CONTINUE_FLAG).write_text(now.isoformat(), encoding="utf-8")
            print("Budget epuise : relance demandee"); break
        time.sleep(min(live.next_wake(games, a.interval if soon else a.idle), left - 10 * 60))


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
    main()
