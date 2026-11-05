"""
boosts.py — Cotes boostees Winamax : collecte automatique, prix juste, verdict (Wilson, 05/11/2026 : « récupère tout
seul et automatiquement les côtes boostées » ; « trouve la recette juste »).

Collecte : pages PUBLIQUES de winamax.fr (accueil + categorie « Grosses Cotes » de chaque sport), lues au plus toutes
les 2 h, une requete par page, sans connexion. Garde-fous : jamais de contournement — au premier refus (code HTTP autre
que 200, page sans donnees, mot « captcha »), la collecte s'arrete et attend la prochaine fenetre ; 3 refus de suite
-> pause de 24 h (data/boosts.json « blocked_until »).

Prix juste : boost_model cale sur la cote juste Pinnacle du match (ev_picks.json, 1X2 + total), puis corrige de
l'ecart mesure sur l'historique pour chaque type de pari (boost_validate, 2 548 matchs Top 5). Paris non reconnus
(buteurs, joueurs, corners...) : affiches « non chiffrable », jamais proposes.
Pick : esperance >= +5 % (marge de securite sur un modele de speciaux), mise plafonnee par Winamax (10 a 50 EUR).

05/11/2026, premier releve reel : la page publique repond a un navigateur (curl : 200) mais refuse un script
(Python : 403). C'est une protection anti-robot : on ne la contourne pas (regle de Wilson) -> collecte automatique
DESACTIVEE (AUTO = False). Le prix juste sert au chat : Wilson colle un boost, le relais le chiffre (check()).
"""
import difflib, json, re, time, unicodedata, urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

import boost_model as BM

PATH = Path("data/boosts.json")
BASE = "https://www.winamax.fr/paris-sportifs"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
EVERY = timedelta(hours=2)
AUTO = False      # collecte automatique coupee : Winamax refuse les scripts (403), on ne contourne pas
MIN_EV = 0.05
SPORTS = {1: "foot"}          # basket (2) : pas encore de modele de prix -> affiche « non chiffrable » plus tard
# ecart reel - modele mesure sur l'historique (boost_validate, 05/11/2026), ajoute a la probabilite du modele
BIAS = {"gagne_2m": 0.000, "marque_n": 0.005, "gagne_mt1": 0.010, "gagne_2mt": -0.006, "gagne_0": -0.021,
        "2m_plus": 0.011, "but_chaque_mt": 0.038, "plus_mt1": -0.016}


def _load():
    try:
        return json.loads(PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"boosts": [], "fails": 0}


def _save(st):
    PATH.write_text(json.dumps(st, ensure_ascii=False, indent=0), encoding="utf-8")


def _get(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "fr-FR,fr;q=0.9"})
    with urllib.request.urlopen(req, timeout=25) as r:
        if r.status != 200: raise RuntimeError(f"HTTP {r.status}")
        h = r.read().decode("utf-8", "ignore")
    if re.search(r"captcha|cf-chl|datadome", h, re.I) and "PRELOADED_STATE" not in h:
        raise RuntimeError("page de controle anti-robot : arret")
    m = re.search(r"var PRELOADED_STATE\s*=\s*(\{.*?\});\s*(?:var|</script>)", h, re.S)
    if not m: raise RuntimeError("donnees absentes")
    return json.loads(m.group(1))


def _norm(s):
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()
    s = re.sub(r"\b(fc|ac|afc|as|ss|sc|cf|club|calcio|de|tc|sk|fk|pfc|nk|1)\b", " ", s)
    return " ".join(s.replace("-", " ").replace(".", " ").split())


def _sim(a, b):
    a, b = _norm(a), _norm(b)
    return difflib.SequenceMatcher(None, a, b).ratio() + (0.3 if a and b and (a in b or b in a) else 0)


def parse_state(st, sport_id, tournament_id):
    """Boosts d'une page « Grosses Cotes » : [{match, home, away, ko, label, odds, max_stake, kind}]. Les matchs
    boostes ont un sportId special (100000) : on filtre sur le tournoi de la page ; le sport vient de la categorie."""
    M, B, O, OD = st.get("matches") or {}, st.get("bets") or {}, st.get("outcomes") or {}, st.get("odds") or {}
    out = []
    for m in M.values():
        if not isinstance(m, dict) or str(m.get("tournamentId")) != str(tournament_id): continue
        title = m.get("title") or ""
        if "boost" not in title.lower() and "grosse" not in title.lower(): continue
        b = B.get(str(m.get("mainBetId"))) or {}
        name = (b.get("betTitle") or b.get("betTypeName") or "")
        cap = re.search(r"mise max\D*(\d+)", name, re.I)
        teams = title.split(":", 1)[1].strip() if ":" in title else title
        h, _, a = teams.partition(" - ")
        for o in b.get("outcomes") or []:
            oc, od = O.get(str(o)) or {}, OD.get(str(o))
            if not od: continue
            out.append({"book": "Winamax", "sport": SPORTS.get(sport_id, str(sport_id)), "match": teams,
                        "home": h.strip(), "away": a.strip(), "ko": datetime.fromtimestamp(m.get("matchStart") or 0, timezone.utc)
                        .isoformat(timespec="minutes"), "label": (oc.get("label") or "").replace("\xa0", " "),
                        "odds": float(od), "max_stake": int(cap.group(1)) if cap else None,
                        "kind": "grosse" if "grosse" in name.lower() else "boost", "id": f"wmx|{o}"})
    return out


def collect():
    """Lit l'accueil, trouve les categories « Grosses Cotes » de chaque sport suivi, puis lit ces pages."""
    home = _get(BASE)
    pages = []
    for cid, c in (home.get("categories") or {}).items():
        sid = c.get("boostedOddSportId") if isinstance(c, dict) else None
        if sid in SPORTS:
            for tid in c.get("tournaments") or []:
                pages.append((sid, tid, f"{BASE}/sports/{sid}/{cid}/{tid}"))
    out = []
    for sid, tid, url in pages[:4]:
        time.sleep(3)
        out += parse_state(_get(url), sid, tid)
    return out


# ─── Prix juste ───────────────────────────────────────────────────────────────
def _fair_match(b, fairs):
    """Ligne Pinnacle (ev_picks.json « fair ») du match du boost, et sens (domicile Winamax = domicile Pinnacle ?)."""
    try:
        ko = datetime.fromisoformat(b["ko"])
    except ValueError:
        return None, None
    best, swap, sc = None, False, 0
    for eid, v in fairs.items():
        if v.get("sport") != "foot": continue
        if abs(datetime.fromisoformat(v["commence"].replace("Z", "+00:00")) - ko) > timedelta(hours=3): continue
        s1 = _sim(b["home"], v["home"]) + _sim(b["away"], v["away"])
        s2 = _sim(b["home"], v["away"]) + _sim(b["away"], v["home"])
        if max(s1, s2) > sc: best, swap, sc = dict(v, _eid=eid), s2 > s1, max(s1, s2)
    return (best, swap) if sc >= 1.2 else (None, None)


def _model(v):
    L = dict(v["lines"])
    p = [1 / L.get(f"Victoire {v['home']}", 0) if L.get(f"Victoire {v['home']}") else None,
         1 / L["Match nul"] if L.get("Match nul") else None,
         1 / L.get(f"Victoire {v['away']}", 0) if L.get(f"Victoire {v['away']}") else None]
    if None in p: return None
    tot = None
    for lab, fo in v["lines"]:
        m = re.match(r"Plus de (\d+(?:\.\d+)?) buts", lab)
        if m and float(m.group(1)) % 1 == 0.5: tot = (float(m.group(1)), 1 / fo); break
    return BM.Match(*BM.fit(p, tot))


def _side(name, b):
    """'h' si le nom designe l'equipe a domicile du boost, 'a' a l'exterieur."""
    return "h" if _sim(name, b["home"]) >= _sim(name, b["away"]) else "a"


def _pred(label, b):
    """(cle de biais, predicat(a, b, c, d) sur les buts dom/ext 1re et 2e mi-temps) ou None si non reconnu."""
    t = label.strip().rstrip(".")
    W = lambda s: (lambda a, b_, c, d: a + c > b_ + d) if s == "h" else (lambda a, b_, c, d: b_ + d > a + c)
    G = lambda s: (lambda a, b_, c, d: a + c) if s == "h" else (lambda a, b_, c, d: b_ + d)
    m = re.match(r"(.+?) gagne et les deux [ée]quipes marquent$", t, re.I)
    if m:
        s = _side(m.group(1), b); w = W(s)
        return "gagne_2m", lambda a, b_, c, d: w(a, b_, c, d) and a + c > 0 and b_ + d > 0
    m = re.match(r"(.+?) marque au moins (\d+) buts?$", t, re.I)
    if m:
        s, n = _side(m.group(1), b), int(m.group(2)); g = G(s)
        return "marque_n", lambda a, b_, c, d: g(a, b_, c, d) >= n
    m = re.match(r"(.+?) (?:gagne|remporte) la premi[eè]re mi-temps$", t, re.I)
    if m:
        s = _side(m.group(1), b)
        return "gagne_mt1", (lambda a, b_, c, d: a > b_) if s == "h" else (lambda a, b_, c, d: b_ > a)
    m = re.match(r"(.+?) (?:gagne|remporte) les deux mi-temps$", t, re.I)
    if m:
        s = _side(m.group(1), b)
        return "gagne_2mt", (lambda a, b_, c, d: a > b_ and c > d) if s == "h" else (lambda a, b_, c, d: b_ > a and d > c)
    m = re.match(r"(.+?) gagne (?:à zéro|a zero|sans encaisser de but)$", t, re.I)
    if m:
        s = _side(m.group(1), b)
        return "gagne_0", (lambda a, b_, c, d: a + c > 0 and b_ + d == 0) if s == "h" else (lambda a, b_, c, d: b_ + d > 0 and a + c == 0)
    m = re.match(r"(.+?) gagne(?: le match)?$", t, re.I)
    if m and "mi-temps" not in t:
        return "victoire", W(_side(m.group(1), b))
    m = re.match(r"Les deux [ée]quipes marquent et plus de (\d+),5 buts$", t, re.I)
    if m:
        n = int(m.group(1)); return "2m_plus", lambda a, b_, c, d: a + c > 0 and b_ + d > 0 and a + b_ + c + d > n
    m = re.match(r"Plus de (\d+),5 buts$", t, re.I)
    if m:
        n = int(m.group(1)); return "total", lambda a, b_, c, d: a + b_ + c + d > n
    if re.match(r"Un but (?:dans|lors de) chaque mi-temps$", t, re.I):
        return "but_chaque_mt", lambda a, b_, c, d: a + b_ > 0 and c + d > 0
    return None


def price(b, fairs):
    """Complete le boost : p (probabilite juste), fair_odds, ev, why. Combines multi-matchs : produit des issues."""
    t = b["label"]
    multi = re.match(r"Plus de (\d+),5 buts lors de chacun des matchs suivants : (.+)$", t, re.I)
    if multi:
        n = int(multi.group(1)); p = 1.0
        for mt in re.split(r"\s+et\s+", multi.group(2)):
            h, _, a = mt.partition(" - ")
            v, _sw = _fair_match({"home": h, "away": a, "ko": b["ko"]}, fairs)
            mm = _model(v) if v else None
            if not mm: return dict(b, p=None, why="match du combiné introuvable chez Pinnacle")
            p *= mm.prob(lambda a_, b_, c, d: a_ + b_ + c + d > n)
        return dict(b, p=round(p, 4), fair=round(1 / p, 2), ev=round(p * b["odds"] - 1, 4), why="combiné de matchs (produit)")
    v, swap = _fair_match(b, fairs)
    if not v: return dict(b, p=None, why="match sans cote Pinnacle")
    rec = _pred(t, b)
    if not rec: return dict(b, p=None, why="type de pari non chiffrable (joueur, corners...)")
    key, pred = rec
    mm = _model(v)
    if not mm: return dict(b, p=None, why="cotes Pinnacle incomplètes")
    pr = (lambda a, b_, c, d: pred(b_, a, d, c)) if swap else pred   # Winamax inverse dom / ext ?
    p = min(max(mm.prob(pr) + BIAS.get(key, 0.0), 0.005), 0.995)
    # eid : identifiant Pinnacle du match -> un seul pari par match entre boosts et lignes normales (picks_du_jour)
    return dict(b, p=round(p, 4), fair=round(1 / p, 2), ev=round(p * b["odds"] - 1, 4), eid=v.get("_eid"),
                why=f"modèle calé sur Pinnacle ({key})")


def scan(force=False, now=None):
    """A appeler par la veille : collecte au plus toutes les 2 h, prix juste, sauvegarde. Renvoie le nb de boosts."""
    now = now or datetime.now(timezone.utc)
    if not AUTO: return 0
    st = _load()
    if st.get("blocked_until") and now < datetime.fromisoformat(st["blocked_until"]): return 0
    if not force and st.get("at") and now - datetime.fromisoformat(st["at"]) < EVERY: return 0
    try:
        raw = collect()
        st["fails"] = 0
    except Exception as e:
        st["fails"] = st.get("fails", 0) + 1
        st["last_error"] = f"{type(e).__name__}: {e}"[:200]
        if st["fails"] >= 3: st["blocked_until"] = (now + timedelta(hours=24)).isoformat(timespec="minutes")
        st["at"] = now.isoformat(timespec="minutes"); _save(st)
        print(f"  [!] cotes boostees : {st['last_error']} (refus {st['fails']})")
        return 0
    try:
        fairs = (json.loads(Path("data/ev_picks.json").read_text(encoding="utf-8")) or {}).get("fair") or {}
    except (OSError, ValueError):
        fairs = {}
    st["boosts"] = [price(b, fairs) for b in raw]
    st["at"] = now.isoformat(timespec="minutes"); st.pop("blocked_until", None); st.pop("last_error", None)
    _save(st)
    return len(st["boosts"])


def _final(home, away, ko):
    """(dom 1re MT, ext 1re MT, dom 2e MT, ext 2e MT) d'un match termine, d'apres l'archive des simulations foot
    (score final et mi-temps), en remettant le sens domicile / exterieur du boost. None si inconnu."""
    try:
        H = json.loads(Path("data/foot_sim_history.json").read_text(encoding="utf-8")).get("games") or {}
    except (OSError, ValueError):
        return None
    t = datetime.fromisoformat(ko)
    best, sc, sw = None, 0, False
    for g in (H.values() if isinstance(H, dict) else H):
        if not g.get("final") or not g.get("ht"): continue
        try:
            if abs(datetime.fromisoformat(g["ko"].replace("Z", "+00:00")) - t) > timedelta(hours=3): continue
        except ValueError:
            continue
        s1, s2 = _sim(home, g["home"]) + _sim(away, g["away"]), _sim(home, g["away"]) + _sim(away, g["home"])
        if max(s1, s2) > sc: best, sc, sw = g, max(s1, s2), s2 > s1
    if not best or sc < 1.2: return None
    (fh, fa), (hh, ha) = best["final"][:2], best["ht"][:2]
    a, b, c, d = hh, ha, fh - hh, fa - ha
    return (b, a, d, c) if sw else (a, b, c, d)


def settle(b):
    """'win' / 'loss' d'un boost dont le match est termine (None tant que le score n'est pas connu)."""
    t = b.get("label") or ""
    multi = re.match(r"Plus de (\d+),5 buts lors de chacun des matchs suivants : (.+)$", t, re.I)
    if multi:
        n = int(multi.group(1)); ok = True
        for mt in re.split(r"\s+et\s+", multi.group(2)):
            h, _, a = mt.partition(" - ")
            f = _final(h, a, b["ko"])
            if f is None: return None
            ok = ok and sum(f) > n
        return "win" if ok else "loss"
    rec = _pred(t, b)
    f = _final(b.get("home"), b.get("away"), b["ko"]) if rec else None
    if f is None: return None
    return "win" if rec[1](*f) else "loss"


def check(req, now=None):
    """Boost colle par Wilson dans le chat -> texte pour Claude (prix juste, esperance, verdict). req = {home, away,
    label, odds, [book], [ko ISO]}. Le match est cherche chez Pinnacle dans les 48 h si l'heure n'est pas donnee."""
    now = now or datetime.now(timezone.utc)
    try:
        fairs = (json.loads(Path("data/ev_picks.json").read_text(encoding="utf-8")) or {}).get("fair") or {}
    except (OSError, ValueError):
        return "Cotes Pinnacle indisponibles : impossible de chiffrer le boost."
    try:
        odds = float(str(req.get("odds")).replace(",", "."))
    except (TypeError, ValueError):
        return "Cote du boost illisible."
    b = {"home": req.get("home") or "", "away": req.get("away") or "", "label": (req.get("label") or "").strip(),
         "odds": odds, "match": f'{req.get("home")} - {req.get("away")}', "ko": req.get("ko") or ""}
    if not b["ko"]:   # heure inconnue : le match de ce nom le plus proche dans les 48 h
        best, sc = None, 0
        for v in fairs.values():
            t = datetime.fromisoformat(v["commence"].replace("Z", "+00:00"))
            if not timedelta(hours=-3) < t - now < timedelta(hours=48): continue
            s = max(_sim(b["home"], v["home"]) + _sim(b["away"], v["away"]), _sim(b["home"], v["away"]) + _sim(b["away"], v["home"]))
            if s > sc: best, sc = v, s
        if best is None or sc < 1.2:
            return (f"Match « {b['match']} » introuvable dans les cotes Pinnacle relevées (championnats suivis : Top 5, "
                    "Ligue des champions, Ligue Europa ; foot seulement pour l'instant).")
        b["ko"] = datetime.fromisoformat(best["commence"].replace("Z", "+00:00")).isoformat(timespec="minutes")
    x = price(b, fairs)
    if x.get("p") is None:
        return (f"Boost « {b['label']} » @ {odds:.2f} : non chiffrable ({x.get('why')}). Types reconnus : victoire, "
                "victoire + les deux marquent, équipe marque au moins N buts, gagne la 1re mi-temps, gagne les deux "
                "mi-temps, gagne à zéro, plus de N,5 buts, les deux marquent + plus de N,5, un but dans chaque mi-temps, "
                "plus de N,5 buts dans plusieurs matchs.")
    ev = x["ev"]
    verdict = ("À JOUER (espérance ≥ +5 %, marge de sécurité du modèle)" if ev >= MIN_EV else
               "légère value, sous le seuil de sécurité de +5 % : jouable à petite mise" if ev >= 0 else
               "PAS ASSEZ BOOSTÉ : en dessous de la cote juste")
    return (f"Boost « {b['label']} » ({b['match']}) @ {odds:.2f} : probabilité juste {100 * x['p']:.1f} %, cote juste "
            f"{x['fair']:.2f}, espérance {100 * ev:+.1f} % → {verdict}. Méthode : {x['why']} ; modèle de buts calé sur la cote "
            "Pinnacle du match (1X2 + total), vérifié sur 2 548 matchs du Top 5 (écart de 1 à 2 points selon le type, "
            "corrigé). Rappelle le plafond de mise du bookmaker s'il est connu (Winamax : souvent 10 à 20 €).")


def current(now=None):
    """Boosts a venir (match pas encore commence)."""
    now = now or datetime.now(timezone.utc)
    return [b for b in _load().get("boosts") or [] if datetime.fromisoformat(b["ko"]) > now]


def picks(now=None):
    """Boosts a jouer : esperance >= +5 %."""
    return sorted((b for b in current(now) if b.get("ev") is not None and b["ev"] >= MIN_EV), key=lambda b: -b["ev"])
