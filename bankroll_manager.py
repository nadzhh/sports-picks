"""
bankroll_manager.py — Gestion de bankroll pro (tier system 1/2/3%).

Stratégie validée avec l'utilisateur :
  - BR initiale 550€ (éditable dans data/bankroll.json)
  - Auto-retrait : si BR >= 600€ → retire 50€ → reset à 550€
  - Tier system basé sur confidence du pick :
      T1 (conf 55-65%)  → 1% BR
      T2 (conf 65-75%)  → 2% BR
      T3 (conf 75%+)    → 3% BR  (maximum, jamais au-delà)
  - Cap journalier : max 10% BR exposé/jour
  - Stop-loss : si BR <= 80% du peak → tiers réduits 0.5/1/2%

Usage :
  from bankroll_manager import compute_stake, get_summary
  stake = compute_stake(confidence=72)   # → {tier: T2, amount: 11.0}
  summary = get_summary()                # pour affichage panneau global

L'historique des paris n'est PAS géré ici (c'est `analyze_picks.py` qui
calcule le P&L réel depuis picks_history.json). Ce module est un calculator
+ persistance d'état.
"""
import json
from pathlib import Path
from datetime import datetime, timezone

BANKROLL_FILE = Path("data/bankroll.json")

DEFAULT_STATE = {
    "initial_br": 550.0,
    "current_br": 550.0,
    "peak_br": 550.0,
    "auto_withdraw_threshold": 600.0,
    "auto_withdraw_amount": 50.0,
    "withdrawn_total": 0.0,
    "tier_pcts": {"T1": 0.01, "T2": 0.02, "T3": 0.03},
    "tier_thresholds": {"T1": 55, "T2": 65, "T3": 75},
    "tier_pcts_stoploss": {"T1": 0.005, "T2": 0.01, "T3": 0.02},
    "daily_cap_pct": 0.10,
    "stoploss_pct": 0.80,
    "last_updated": "",
}


def load_state():
    """Charge l'état bankroll depuis data/bankroll.json (ou initialise)."""
    if BANKROLL_FILE.exists():
        try:
            disk = json.loads(BANKROLL_FILE.read_text(encoding="utf-8"))
            return {**DEFAULT_STATE, **disk}
        except Exception:
            pass
    return dict(DEFAULT_STATE)


def save_state(state):
    """Persiste l'état (timestamp inclus)."""
    state["last_updated"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    BANKROLL_FILE.parent.mkdir(parents=True, exist_ok=True)
    BANKROLL_FILE.write_text(
        json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def is_in_stoploss(state):
    """Mode stoploss : BR <= 80% du peak → tiers réduits."""
    return state["current_br"] <= state["peak_br"] * state["stoploss_pct"]


def get_effective_tier_pcts(state):
    """Tier % à appliquer : normaux ou réduits (stoploss)."""
    return state["tier_pcts_stoploss"] if is_in_stoploss(state) else state["tier_pcts"]


def compute_stake(confidence, state=None):
    """Mise recommandée selon la confidence du pick.

    Args:
        confidence: % de confiance du pick (0-100)
        state: état BR (chargé auto si None)

    Returns:
        {
          "tier": "T1"/"T2"/"T3",
          "pct": 0.01/0.02/0.03,
          "amount": montant en € (float),
          "stoploss_mode": bool,
          "label": "T2 — 11€ (2% BR)"
        }
        ou None si confidence < seuil T1 (pas de pari recommandé).
    """
    if state is None:
        state = load_state()
    if not confidence or confidence < state["tier_thresholds"]["T1"]:
        return None
    pcts = get_effective_tier_pcts(state)
    th = state["tier_thresholds"]
    if confidence >= th["T3"]:
        tier = "T3"
    elif confidence >= th["T2"]:
        tier = "T2"
    else:
        tier = "T1"
    pct = pcts[tier]
    amount = round(state["current_br"] * pct, 2)
    stoploss = is_in_stoploss(state)
    label = f"{tier} — {amount:.2f}€ ({int(pct*100*10)/10}% BR{' · STOPLOSS' if stoploss else ''})"
    return {
        "tier": tier, "pct": pct, "amount": amount,
        "stoploss_mode": stoploss, "label": label,
    }


def get_summary(state=None):
    """Résumé bankroll pour affichage panneau global."""
    if state is None:
        state = load_state()
    pcts = get_effective_tier_pcts(state)
    return {
        "current_br": state["current_br"],
        "initial_br": state["initial_br"],
        "peak_br": state["peak_br"],
        "withdrawn_total": state["withdrawn_total"],
        "net_pnl": round(
            state["current_br"] - state["initial_br"] + state["withdrawn_total"], 2
        ),
        "daily_cap": round(state["current_br"] * state["daily_cap_pct"], 2),
        "tier_amounts": {
            tier: round(state["current_br"] * pct, 2)
            for tier, pct in pcts.items()
        },
        "tier_thresholds": state["tier_thresholds"],
        "stoploss_mode": is_in_stoploss(state),
        "auto_withdraw_threshold": state["auto_withdraw_threshold"],
        "auto_withdraw_amount":    state["auto_withdraw_amount"],
    }


def apply_auto_withdraw(state=None, persist=True):
    """Auto-retrait : si BR >= seuil → retire X€, persiste."""
    if state is None:
        state = load_state()
    threshold = state["auto_withdraw_threshold"]
    amount    = state["auto_withdraw_amount"]
    n_withdraws = 0
    while state["current_br"] >= threshold:
        state["current_br"] -= amount
        state["withdrawn_total"] += amount
        n_withdraws += 1
    if n_withdraws > 0 and persist:
        save_state(state)
    return state, n_withdraws


def update_peak(state=None, persist=True):
    """Met à jour le peak BR si current dépasse."""
    if state is None:
        state = load_state()
    if state["current_br"] > state["peak_br"]:
        state["peak_br"] = state["current_br"]
        if persist:
            save_state(state)
    return state


if __name__ == "__main__":
    state = load_state()
    if not BANKROLL_FILE.exists():
        save_state(state)
        print(f"[init] Bankroll initialisé : {state['initial_br']}€")

    summary = get_summary(state)
    print("=" * 50)
    print(f"  BR actuelle : {summary['current_br']:.2f}€")
    print(f"  BR peak     : {summary['peak_br']:.2f}€")
    print(f"  Retraits    : {summary['withdrawn_total']:.2f}€")
    print(f"  P&L net     : {summary['net_pnl']:+.2f}€")
    print(f"  Cap/jour    : {summary['daily_cap']:.2f}€")
    mode = "🚨 STOPLOSS" if summary["stoploss_mode"] else "✅ normal"
    print(f"  Mode        : {mode}")
    print("-" * 50)
    print(f"  Tier amounts :")
    for tier, amt in summary["tier_amounts"].items():
        th = summary["tier_thresholds"][tier]
        print(f"    {tier} (conf ≥ {th}%) : {amt:.2f}€")
    print("=" * 50)

    # Test : simule différentes confidences
    print("\n[test] compute_stake() :")
    for c in (50, 60, 70, 80, 90):
        s = compute_stake(c, state)
        if s:
            print(f"  conf {c}% → {s['label']}")
        else:
            print(f"  conf {c}% → ❌ pas de pari (< seuil T1)")
