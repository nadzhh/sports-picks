"""
foot_validate.py — decide si la base foot etendue (foot_comps : C4, coupes nationales, autres championnats,
selections) entre dans le modele de forces. Ecrit data/foot_model_ext.json, lu par foot_sim_run.model_ext().

Regle (methode Expert / Contradicteur, 04/11/2026) : on n'active que si
  1. la base etendue couvre au moins MIN_COVERAGE des matchs joues de la saison du backtest ;
  2. sur le Top 5 2025-26 rejoue a l'aveugle (walk-forward, cotes de cloture Pinnacle football-data), le 1X2 ET le
     plus / moins 2,5 buts ne sont pas moins bien prevus qu'avec la base historique (tolerance TOL de log-loss).
Sinon le modele reste sur le perimetre historique ; la decision et les chiffres sont gardes dans le fichier.

    python foot_validate.py [--force]      # --force : revalider meme si une decision recente existe
"""
import argparse, json, math, sys, time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

import foot_db

OUT = Path("data/foot_model_ext.json")
START, END = "2025-08-01", "2026-06-30"
BT_SEASONS = ("2025/2026", "2025")
MIN_COVERAGE, TOL, SIMS, REVALIDATE_DAYS = 0.90, 0.001, 2000, 7


def coverage():
    """Part des matchs joues de la saison du backtest presents dans la base etendue."""
    have = total = 0
    for lid, s in foot_db.ext_targets():
        if s not in BT_SEASONS: continue
        fx = [m for m in foot_db.fixtures(lid, s) if (m.get("status") or {}).get("finished")]
        db = foot_db.load(lid, s)
        total += len(fx); have += sum(1 for m in fx if str(m["id"]) in db)
    return (have / total if total else 0.0), have, total


def metrics(rows):
    y1 = lambda r: 0 if r["score"][0] > r["score"][1] else (1 if r["score"][0] == r["score"][1] else 2)
    K = [r for r in rows if r["mkt"].get("1x2")]
    O = [r for r in rows if r["mkt"].get("o25") is not None]
    ll = lambda p, y: -math.log(max(p if y else 1 - p, 1e-4))
    return {"n": len(rows), "n_odds": len(K),
            "ll_1x2": float(np.mean([-math.log(max([r["p1"], r["px"], r["p2"]][y1(r)], 1e-4)) for r in K])) if K else None,
            "ll_1x2_mkt": float(np.mean([-math.log(max(r["mkt"]["1x2"][y1(r)], 1e-4)) for r in K])) if K else None,
            "ll_o25": float(np.mean([ll(r["po25"], sum(r["score"]) > 2.5) for r in O])) if O else None,
            "goals_sim": float(np.mean([r["mg"] for r in rows])) if rows else None,
            "goals_real": float(np.mean([sum(r["score"]) for r in rows])) if rows else None}


def main(force=False):
    prev = {}
    try:
        prev = json.loads(OUT.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        pass
    if not force and prev.get("at") and time.time() - prev.get("ts", 0) < REVALIDATE_DAYS * 86400 and prev.get("enabled"):
        print(f"[validation] decision recente ({prev['at']}) : base etendue active, rien a faire"); return prev
    cov, have, total = coverage()
    now = datetime.now(timezone.utc).isoformat(timespec="minutes")
    res = {"at": now, "ts": time.time(), "coverage": round(cov, 3), "have": have, "total": total, "enabled": False}
    if cov < MIN_COVERAGE:
        res["reason"] = f"base etendue incomplete : {have}/{total} matchs de 2025-26 ({100 * cov:.0f} % < {100 * MIN_COVERAGE:.0f} %)"
        print("[validation] " + res["reason"])
    else:
        import foot_backtest as B
        core = metrics(B.run(START, END, {}, SIMS, "val_core", ext=False)["rows"])
        ext = metrics(B.run(START, END, {}, SIMS, "val_ext", ext=True)["rows"])
        res.update(core=core, ext=ext)
        ok1 = core["ll_1x2"] is not None and ext["ll_1x2"] <= core["ll_1x2"] + TOL
        ok2 = core["ll_o25"] is not None and ext["ll_o25"] <= core["ll_o25"] + TOL
        res["enabled"] = bool(ok1 and ok2)
        res["reason"] = (f"1X2 {core['ll_1x2']:.4f} -> {ext['ll_1x2']:.4f} ; +2,5 {core['ll_o25']:.4f} -> {ext['ll_o25']:.4f} "
                         f"({core['n_odds']} matchs Top 5 avec cotes) : " + ("ACTIVEE" if res["enabled"] else "refusee (moins bien)"))
        print("[validation] " + res["reason"])
    OUT.write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    return res


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true")
    r = main(ap.parse_args().force)
    import os
    if os.environ.get("GITHUB_ACTIONS") and os.environ.get("TELEGRAM_BOT_TOKEN") and r.get("core"):   # un vrai backtest a tourne
        try:
            import notify
            notify.tg_send(f"⚽ <b>Base foot étendue</b> : {r['reason']}", chat_id=notify.TELEGRAM_CHAT_ID_BANKROLL)
        except Exception as e:
            print(f"  [!] telegram : {e}")
