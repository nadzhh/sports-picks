"""
nba_injury_report.py — Rapport officiel des blessures NBA (official.nba.com, PDF toutes les 15 min).

Plus fin et plus a jour que les statuts ESPN : Out / Doubtful / Questionable / Probable / Available,
avec la raison (blessure, repos, G League...). Les equipes doivent le remplir avant chaque match ;
« NOT YET SUBMITTED » = equipe pas encore declaree (on garde alors ESPN).

    latest()            -> {"at": "2026-10-28T16:15", "url": ..., "teams": {nom equipe: {...}}}
    fetch(datetime_et)  -> rapport publie a cette heure (historique : calibrage)
    status_map(rep, roster_names) -> {espn_id: statut} pour l'effectif d'une equipe

Usage : python nba_injury_report.py        # affiche le dernier rapport
"""
import io, re, sys, unicodedata
from datetime import datetime, timedelta, timezone

import requests

LIST_URL = "https://official.nba.com/nba-injury-report-{y}-{y2:02d}-season/"
PDF_URL = "https://ak-static.cms.nba.com/referee/injury/Injury-Report_{stamp}.pdf"
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)", "Referer": "https://official.nba.com/"}
STATUSES = ("Out", "Doubtful", "Questionable", "Probable", "Available")
COLS = ("date", "time", "matchup", "team", "player", "status", "reason")
HEADS = ("Game Date", "Game Time", "Matchup", "Team", "Player Name", "Current Status", "Reason")


def _stamp_candidates(dt):
    """Noms de fichier possibles (le format a change en cours de saison 2025-26)."""
    d, h, m, ap = dt.strftime("%Y-%m-%d"), dt.strftime("%I"), dt.strftime("%M"), dt.strftime("%p")
    out = [f"{d}_{h}_{m}{ap}"]
    if m == "00": out.append(f"{d}_{h}{ap}")
    return out


def _get_pdf(stamp):
    r = requests.get(PDF_URL.format(stamp=stamp), headers=UA, timeout=30)
    return r.content if r.status_code == 200 and r.content[:4] == b"%PDF" else None


def _season_list_url(now_et):
    y = now_et.year if now_et.month >= 8 else now_et.year - 1
    return LIST_URL.format(y=y, y2=(y + 1) % 100)


def _norm(s):
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()
    s = re.sub(r"[^a-z ]", "", s.replace("-", " "))
    return " ".join(w for w in s.split() if w not in ("jr", "sr", "ii", "iii", "iv"))


def parse(pdf_bytes):
    """Lignes du rapport : {team: {"submitted": bool, "matchup", "players": [{name, status, reason}]}}.
    Les colonnes sont reperees par la position des titres (le texte brut colle les mots)."""
    import pdfplumber
    teams, cur = {}, {"date": "", "time": "", "matchup": "", "team": ""}
    bounds = None
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for page in pdf.pages:
            words = page.extract_words(x_tolerance=1.2)
            lines = {}
            for w in words:
                lines.setdefault(round(w["top"] / 3), []).append(w)
            rows = [sorted(ws, key=lambda w: w["x0"]) for _, ws in sorted(lines.items())]
            # Titres des colonnes (repetes a chaque page)
            for ws in rows:
                txt = " ".join(w["text"] for w in ws)
                if txt.startswith("Game Date") and "Current Status" in txt:
                    xs = []
                    for h in HEADS:
                        first = h.split()[0]
                        cand = [w for w in ws if w["text"] == first and (not xs or w["x0"] > xs[-1] + 5)]
                        xs.append(cand[0]["x0"] - 3 if cand else None)
                    if None not in xs: bounds = xs
            if not bounds: continue
            parsed = []
            for ws in rows:
                txt = " ".join(w["text"] for w in ws)
                if txt.startswith(("Game Date", "Injury Report:", "Page ")): continue
                cells = [""] * len(COLS)
                for w in ws:
                    k = max(i for i, b in enumerate(bounds) if w["x0"] >= b) if w["x0"] >= bounds[0] else 0
                    cells[k] = (cells[k] + " " + w["text"]).strip()
                parsed.append((ws[0]["top"], dict(zip(COLS, cells))))
            players = []
            for top, c in parsed:
                for k in ("date", "time", "matchup", "team"):
                    if c[k]: cur[k] = c[k] if k != "time" else c[k].replace(" (ET)", "")
                if c["team"] and not c["player"] and "NOT YET SUBMITTED" in (c["status"] + " " + c["reason"] + " " + c["team"]).upper():
                    t = cur["team"].replace("NOT YET SUBMITTED", "").strip()
                    cur["team"] = t
                    teams.setdefault(f'{cur["date"]}|{t}', {"matchup": cur["matchup"], "submitted": False, "players": []})
                    continue
                if c["player"] and c["status"] in STATUSES:
                    # « Udeh, Jr., Ernest » / « Porter Jr., Michael » : le prenom est apres la derniere virgule
                    parts = [x.strip() for x in c["player"].split(",")]
                    first, last = parts[-1], " ".join(parts[:-1])
                    # le rapport couvre souvent 2 jours : une equipe peut y figurer pour 2 matchs
                    e = teams.setdefault(f'{cur["date"]}|{cur["team"]}', {"matchup": cur["matchup"], "submitted": True, "players": []})
                    e["submitted"] = True
                    p = {"name": f"{first.strip()} {last.strip()}".strip(), "status": c["status"],
                         "reason": c["reason"], "top": top}
                    e["players"].append(p); players.append(p)
            # 2e passe : fragments de raison (cellule sur 2-3 lignes, centree verticalement)
            for top, c in parsed:
                if (c["reason"] and not c["player"] and not c["status"] and not c["team"] and players
                        and "NOT YET SUBMITTED" not in c["reason"].upper()):
                    p = min(players, key=lambda p: abs(p["top"] - top))
                    p.setdefault("extra", []).append((top, c["reason"]))
    for t in teams.values():
        for p in t["players"]:
            parts = sorted(p.pop("extra", []) + [(p.pop("top"), p["reason"])])
            p["reason"] = " ".join(r for _, r in parts if r).strip()
    return teams


def fetch(dt_et):
    """Rapport publie a l'heure dt_et (heure de New York), None s'il n'existe pas."""
    for st in _stamp_candidates(dt_et):
        b = _get_pdf(st)
        if b: return {"at": dt_et.strftime("%Y-%m-%dT%H:%M"), "stamp": st, "teams": parse(b)}
    return None


def latest(now_et=None):
    """Dernier rapport publie (lien le plus recent de la page officielle de la saison)."""
    now_et = now_et or datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=4)
    r = requests.get(_season_list_url(now_et), headers=UA, timeout=30)
    stamps = set(re.findall(r"Injury-Report_(\d{4}-\d{2}-\d{2}_\d{2}(?:_\d{2})?[AP]M)\.pdf", r.text))

    def when(s):
        d, rest = s.split("_", 1)
        rest = rest.replace("_", ":")
        return datetime.strptime(f"{d} {rest if ':' in rest[:3] else rest[:2] + ':00' + rest[2:]}", "%Y-%m-%d %I:%M%p")
    for st in sorted(stamps, key=when, reverse=True)[:4]:
        b = _get_pdf(st)
        if b: return {"at": when(st).strftime("%Y-%m-%dT%H:%M"), "stamp": st, "teams": parse(b)}
    return None


def team_entry(rep, team_name, day=None):
    """Entree du rapport pour une equipe ESPN (« Boston Celtics », « LA Clippers »...) et le match du
    jour day (« 10/28/2026 », date de New York ; None = premiere entree)."""
    if not rep: return None
    def key(n):   # « LA Clippers » = « Los Angeles Clippers »
        w = _norm(n).split()
        return " ".join(["los", "angeles"] + w[1:] if w[:1] == ["la"] else w)
    want = key(team_name)
    for k, e in rep["teams"].items():
        d, _, t = k.partition("|")
        if key(t) == want and (day is None or d == day): return e
    return None


def status_map(entry, roster, unmatched=None):
    """{espn_id: (statut en minuscules, raison)} : appariement par nom normalise, puis nom de famille
    + initiale (« Nic Claxton » / « Nicolas Claxton »). roster : [{"id", "name"}]."""
    if not entry: return {}
    by_full = {_norm(p["name"]): p["id"] for p in roster}
    by_last = {}
    for p in roster:
        n = _norm(p["name"]).split()
        if n: by_last.setdefault((n[-1], n[0][:1]), []).append(p["id"])
    out = {}
    for p in entry["players"]:
        n = _norm(p["name"])
        pid = by_full.get(n)
        if not pid and n.split():
            ids = by_last.get((n.split()[-1], n.split()[0][:1]), [])
            pid = ids[0] if len(ids) == 1 else None
        if pid: out[pid] = (p["status"].lower(), p["reason"])
        elif unmatched is not None: unmatched.append(p)   # hors effectif ESPN (two-way, G League...)
    return out


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    rep = latest()
    if not rep: sys.exit("Rapport introuvable")
    print(f"Rapport officiel du {rep['at']} (ET) : {len(rep['teams'])} equipes")
    for t, e in rep["teams"].items():
        print(f"  {t.replace('|', ' ')} ({e['matchup']})" + ("" if e["submitted"] else " : PAS ENCORE DECLARE"))
        for p in e["players"]:
            print(f"     {p['status']:12s} {p['name']:26s} {p['reason']}")
