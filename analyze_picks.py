"""
analyze_picks.py - Analyse les picks resolus de all_picks_history.json et produit
un rapport (calibration + WR par bucket + recommandations) pour guider le tuning
de l'algorithme. Objectif : remonter le WR de ~60% a 80% en etant plus selectif
(reduire le nombre de paris).

Run quotidien (apres generation site). Sortie : data/algo_analysis.json + print
console resume.

Sections du rapport :

1. **Globaux** : WR et ROI par sport (foot / nba / tennis), depuis combien de
   jours, n total.

2. **Calibration** : pour chaque sport, comparer confiance predite vs WR reel
   par bucket (50-59, 60-69, 70-79, 80-89, 90-100). Un ecart > 10pp = modele
   miscalibre dans ce bucket.

3. **WR par market** : table par sport > market avec n / WR / ROI. Markets
   avec n >= 10 et WR < 45% = "killers" (skip ou refondre formule).

4. **WR par context feature** : pour chaque feature dispo (surface, is_b2b,
   is_real_line, ...), compare WR avec/sans la feature. Ecart > 10pp = signal.

5. **Recommandations** : liste de patch suggeres, ex:
   - "Skip market <X> (WR <Y>% sur <N> picks)"
   - "Raise MIN_CONF de <market> a <Z>% (au-dela WR passe a <Y>%)"
   - "Surface <X> sous-performe : skip ou ajuster"
"""
import json, sys
from pathlib import Path
from collections import defaultdict
from datetime import datetime

DATA = Path("data")
IN_PATH  = DATA / "all_picks_history.json"
OUT_PATH = DATA / "algo_analysis.json"

# Constantes
MIN_N_BUCKET    = 10      # taille mini pour reporter un bucket
KILLER_WR       = 45.0    # WR sous ce seuil = "killer" market
SWEET_WR        = 70.0    # WR au-dessus = sweet spot
CALIBRATION_GAP = 10.0    # pp d'ecart pour flagger une miscalibration


def _bucket(conf):
    """Renvoie le bucket de confiance (50-59, 60-69, ..., 90-100)."""
    if conf is None: return None
    c = int(conf)
    if c < 50:  return "<50"
    if c < 60:  return "50-59"
    if c < 70:  return "60-69"
    if c < 80:  return "70-79"
    if c < 90:  return "80-89"
    return "90-100"


def _profit(pick, unit=1.0):
    """Profit unitaire (en unite) pour 1 mise. WIN = +(cote-1), LOSS = -1, PUSH = 0."""
    r = pick.get("result")
    if r == "PUSH": return 0.0
    cote = pick.get("cote")
    if r == "WIN":
        if cote and cote > 1: return (cote - 1) * unit
        return 0.8 * unit  # fallback heuristique si pas de cote (= cote ~1.8 typique)
    if r == "LOSS":
        return -unit
    return 0.0


def _summarize_group(picks):
    """Retourne dict {n, w, l, push, wr, roi, profit, avg_cote}."""
    n = len(picks)
    w = sum(1 for p in picks if p.get("result") == "WIN")
    l = sum(1 for p in picks if p.get("result") == "LOSS")
    push = sum(1 for p in picks if p.get("result") == "PUSH")
    settled = w + l
    wr = (w / settled * 100) if settled > 0 else None
    profit = sum(_profit(p) for p in picks)
    roi = (profit / n * 100) if n > 0 else None
    cotes = [p["cote"] for p in picks if p.get("cote")]
    avg_cote = (sum(cotes) / len(cotes)) if cotes else None
    return {
        "n": n, "w": w, "l": l, "push": push,
        "wr":      round(wr, 1)     if wr     is not None else None,
        "roi":     round(roi, 1)    if roi    is not None else None,
        "profit":  round(profit, 2),
        "avg_cote":round(avg_cote, 2) if avg_cote is not None else None,
    }


def analyze():
    if not IN_PATH.exists():
        print(f"  [analyze] {IN_PATH} manquant. Run picks_history_dump.py d'abord.")
        return None
    raw = json.loads(IN_PATH.read_text(encoding="utf-8"))
    picks = raw.get("picks", [])
    if not picks:
        print("  [analyze] 0 picks dans l'historique.")
        return None

    # ── 1. Globaux par sport ─────────────────────────────────────────────
    by_sport = defaultdict(list)
    for p in picks:
        by_sport[p.get("sport","?")].append(p)
    globals_ = {s: _summarize_group(ps) for s, ps in by_sport.items()}

    # ── 2. Calibration : WR par bucket de confiance, par sport ───────────
    calibration = {}
    for sport, ps in by_sport.items():
        buckets = defaultdict(list)
        for p in ps:
            b = _bucket(p.get("confidence"))
            if b: buckets[b].append(p)
        cal = {}
        for b in ["50-59","60-69","70-79","80-89","90-100"]:
            grp = buckets.get(b, [])
            if not grp: continue
            s = _summarize_group(grp)
            # Mid-point du bucket pour comparaison
            try:
                lo, hi = b.split("-")
                mid = (int(lo) + int(hi)) / 2
            except Exception:
                mid = None
            gap = None
            if s["wr"] is not None and mid is not None:
                gap = round(s["wr"] - mid, 1)
            s["expected_wr"] = mid
            s["calibration_gap"] = gap
            cal[b] = s
        calibration[sport] = cal

    # ── 3. WR par market, par sport ──────────────────────────────────────
    by_market = {}
    for sport, ps in by_sport.items():
        m = defaultdict(list)
        for p in ps:
            m[p.get("market") or "?"].append(p)
        sport_markets = {}
        for mk, grp in m.items():
            s = _summarize_group(grp)
            if s["n"] < 3: continue   # noise filter
            sport_markets[mk] = s
        # Sort par profit decroissant
        sport_markets = dict(sorted(sport_markets.items(),
                                    key=lambda kv: kv[1].get("profit", 0),
                                    reverse=True))
        by_market[sport] = sport_markets

    # ── 4. WR par feature context (sport-specifique) ─────────────────────
    by_context = {}
    # NBA : is_b2b, is_real_line, side (home/away), trend
    if "nba" in by_sport:
        ctx = {}
        nba_picks = by_sport["nba"]
        for feat, getter in [
            ("is_real_line", lambda p: p.get("context",{}).get("is_real_line")),
            ("side",         lambda p: p.get("context",{}).get("side")),
            ("trend",        lambda p: p.get("context",{}).get("trend")),
            ("direction",    lambda p: p.get("direction")),
        ]:
            grp = defaultdict(list)
            for p in nba_picks:
                k = getter(p)
                if k is None or k == "": continue
                grp[str(k)].append(p)
            feat_dict = {}
            for k, gpicks in grp.items():
                s = _summarize_group(gpicks)
                if s["n"] >= 5:
                    feat_dict[k] = s
            if feat_dict:
                ctx[feat] = feat_dict
        if ctx: by_context["nba"] = ctx
    # Foot : league, direction, is_fun
    if "foot" in by_sport:
        ctx = {}
        foot_picks = by_sport["foot"]
        for feat, getter in [
            ("league",    lambda p: p.get("context",{}).get("league")),
            ("direction", lambda p: p.get("direction")),
            ("is_fun",    lambda p: p.get("context",{}).get("is_fun")),
        ]:
            grp = defaultdict(list)
            for p in foot_picks:
                k = getter(p)
                if k is None or k == "": continue
                grp[str(k)].append(p)
            feat_dict = {}
            for k, gpicks in grp.items():
                s = _summarize_group(gpicks)
                if s["n"] >= 5:
                    feat_dict[k] = s
            if feat_dict:
                ctx[feat] = feat_dict
        if ctx: by_context["foot"] = ctx
    # Tennis : surface, tour, market_kind
    if "tennis" in by_sport:
        ctx = {}
        tennis_picks = by_sport["tennis"]
        for feat, getter in [
            ("surface", lambda p: p.get("context",{}).get("surface")),
            ("tour",    lambda p: p.get("context",{}).get("tour")),
        ]:
            grp = defaultdict(list)
            for p in tennis_picks:
                k = getter(p)
                if k is None: continue
                grp[str(k)].append(p)
            feat_dict = {}
            for k, gpicks in grp.items():
                s = _summarize_group(gpicks)
                if s["n"] >= 3:
                    feat_dict[k] = s
            if feat_dict:
                ctx[feat] = feat_dict
        if ctx: by_context["tennis"] = ctx

    # ── 5. Recommandations ───────────────────────────────────────────────
    recos = []
    # 5a. Killer markets (WR < 45% sur n >= 10)
    for sport, mks in by_market.items():
        for mk, s in mks.items():
            if s["n"] >= MIN_N_BUCKET and s["wr"] is not None and s["wr"] < KILLER_WR:
                recos.append({
                    "severity": "high",
                    "type":     "skip_market",
                    "scope":    f"{sport} · {mk}",
                    "message":  f"Market <b>{mk}</b> ({sport}) sous-performe : WR {s['wr']}% sur {s['n']} picks (ROI {s['roi']}%). À skipper ou refondre la formule.",
                    "stats":    s,
                })
    # 5b. Sweet spots a renforcer
    for sport, mks in by_market.items():
        for mk, s in mks.items():
            if s["n"] >= MIN_N_BUCKET and s["wr"] is not None and s["wr"] >= SWEET_WR:
                recos.append({
                    "severity": "info",
                    "type":     "keep_market",
                    "scope":    f"{sport} · {mk}",
                    "message":  f"Market <b>{mk}</b> ({sport}) performe : WR {s['wr']}% sur {s['n']} picks (ROI {s['roi']}%). Continuer.",
                    "stats":    s,
                })
    # 5c. Calibration : flag buckets miscalibres
    for sport, cal in calibration.items():
        for b, s in cal.items():
            if s["n"] < MIN_N_BUCKET: continue
            gap = s.get("calibration_gap")
            if gap is None: continue
            if gap < -CALIBRATION_GAP:
                recos.append({
                    "severity": "medium",
                    "type":     "miscalibrated_overconfident",
                    "scope":    f"{sport} · conf {b}",
                    "message":  f"Bucket conf <b>{b}</b> ({sport}) : modèle annonce ~{s['expected_wr']}% mais WR réel = {s['wr']}% (gap {gap}pp sur {s['n']} picks). Modèle trop confiant → relever MIN_CONF ou ajuster formule.",
                    "stats":    s,
                })
            elif gap > CALIBRATION_GAP:
                recos.append({
                    "severity": "info",
                    "type":     "miscalibrated_underconfident",
                    "scope":    f"{sport} · conf {b}",
                    "message":  f"Bucket conf <b>{b}</b> ({sport}) : modèle annonce ~{s['expected_wr']}% mais WR réel = {s['wr']}% (gap +{gap}pp sur {s['n']} picks). Modèle trop prudent → MIN_CONF peut etre baisse.",
                    "stats":    s,
                })
    # 5d. Context features problematiques
    for sport, ctx in by_context.items():
        for feat, vals in ctx.items():
            wrs = [(k, s) for k, s in vals.items() if s["n"] >= MIN_N_BUCKET and s["wr"] is not None]
            if len(wrs) < 2: continue
            wrs.sort(key=lambda x: x[1]["wr"])
            worst_k, worst_s = wrs[0]
            best_k,  best_s  = wrs[-1]
            if (best_s["wr"] - worst_s["wr"]) >= 15:
                recos.append({
                    "severity": "medium",
                    "type":     "context_signal",
                    "scope":    f"{sport} · {feat}",
                    "message":  f"Signal contexte <b>{feat}</b> ({sport}) : {feat}={worst_k} → WR {worst_s['wr']}% ({worst_s['n']} picks) vs {feat}={best_k} → WR {best_s['wr']}% ({best_s['n']}). Filtrer/ponderer.",
                    "stats":    {"worst": {worst_k: worst_s}, "best": {best_k: best_s}},
                })
    # Trie : severity (high > medium > info) puis n desc
    sev_order = {"high": 0, "medium": 1, "info": 2}
    recos.sort(key=lambda r: (sev_order.get(r["severity"], 99),
                              -(r.get("stats", {}).get("n", 0) or 0)))

    payload = {
        "generated_at": datetime.now().isoformat(),
        "n_total":      len(picks),
        "globals":      globals_,
        "calibration":  calibration,
        "by_market":    by_market,
        "by_context":   by_context,
        "recommendations": recos,
    }
    OUT_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload


def print_summary(payload):
    if not payload:
        return
    print("\n" + "=" * 70)
    print("ANALYSE ALGORITHMIQUE DES PICKS RESOLUS")
    print("=" * 70)
    print(f"Total picks resolus : {payload['n_total']}")
    print()
    print("── Globaux par sport ──────────────────────────────────────────")
    for sport, s in payload["globals"].items():
        wr   = f"{s['wr']:5.1f}%" if s['wr'] is not None else "  N/A"
        roi  = f"{s['roi']:+5.1f}%" if s['roi'] is not None else "  N/A"
        print(f"  {sport:7s} : n={s['n']:4d}  W={s['w']:4d}  L={s['l']:4d}  "
              f"WR={wr}  ROI={roi}  profit={s['profit']:+7.2f}u")
    print()
    print("── Calibration confiance vs WR reel ───────────────────────────")
    for sport, cal in payload["calibration"].items():
        if not cal: continue
        print(f"  [{sport}]")
        for b, s in cal.items():
            gap = s.get("calibration_gap")
            gap_str = f"{gap:+5.1f}pp" if gap is not None else "  N/A"
            flag = "⚠️ " if (gap is not None and gap < -10) else ("✅ " if (gap is not None and -5 <= gap <= 5) else "  ")
            wr_str = f"{s['wr']:.1f}%" if s['wr'] is not None else "N/A"
            print(f"    {flag}{b:7s} : n={s['n']:3d}  WR={wr_str:7s}  attendu={s['expected_wr']}%  gap={gap_str}")
    print()
    print("── Markets : Top + Flops (n>=10) ──────────────────────────────")
    for sport, mks in payload["by_market"].items():
        print(f"  [{sport}]")
        for mk, s in list(mks.items())[:8]:
            if s["n"] < 10: continue
            wr_str = f"{s['wr']:5.1f}%" if s['wr'] is not None else "  N/A"
            roi_str = f"{s['roi']:+5.1f}%" if s['roi'] is not None else "  N/A"
            flag = "🚨" if (s["wr"] is not None and s["wr"] < 45) else ("🎯" if (s["wr"] is not None and s["wr"] >= 70) else "  ")
            print(f"    {flag} {mk:20s} n={s['n']:3d}  WR={wr_str}  ROI={roi_str}  profit={s['profit']:+6.2f}u")
    print()
    print("── Recommandations algo ───────────────────────────────────────")
    recos = payload.get("recommendations", [])
    if not recos:
        print("  Aucune recommandation : echantillon trop petit ou algo bien calibre.")
    else:
        for r in recos[:15]:
            sev = {"high":"🚨","medium":"⚠️","info":"💡"}.get(r["severity"], "•")
            # Strip HTML from message for console
            msg = r["message"].replace("<b>","").replace("</b>","")
            print(f"  {sev} [{r['scope']}] {msg}")
    print("=" * 70)
    print(f"Rapport JSON complet : {OUT_PATH}\n")


def main():
    payload = analyze()
    print_summary(payload)
    return 0


if __name__ == "__main__":
    sys.exit(main())
