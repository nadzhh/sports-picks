"""
boost_model.py — Prix « juste » des paris speciaux (cotes boostees) a partir des cotes Pinnacle du match.

Idee (Wilson, 05/11/2026 : « trouve la recette juste ») : les boosts Winamax / Betclic portent sur des paris speciaux
(« X gagne et les deux equipes marquent », « X gagne la 1re mi-temps », « X marque au moins 2 buts »...) sans cote
Pinnacle directe. On cale un modele de buts sur ce que le marche le plus precis dit du match (1X2 sans marge + total),
puis on calcule n'importe quel special avec ce modele. La simulation ne sert pas a contredire le marche (elle le bat
pas : etude du 05/11/2026), seulement a ce qu'il ne donne pas (part des buteurs, a terme).

Modele : buts de chaque equipe par mi-temps ~ Poisson independants, lambda_mt1 = F1 x lambda, avec correction
Dixon-Coles (rho) sur les petits scores ; lambda domicile / exterieur ajustes pour reproduire le 1X2 et le total.
Valide sur l'historique (voir boost_validate.py) avant tout usage.
"""
import math

F1 = 0.442         # part des buts en 1re mi-temps : 0,442 mesure sur 2 548 matchs (boost_validate, 05/11/2026)
RHO = -0.06        # correction Dixon-Coles des scores 0-0 / 1-0 / 0-1 / 1-1 (mesuree sur la base)
MAXG = 10


def _pois(l, k):
    return math.exp(-l) * l ** k / math.factorial(k)


def _dc(h, a, lh, la, rho):
    if h == 0 and a == 0: return 1 - lh * la * rho
    if h == 0 and a == 1: return 1 + lh * rho
    if h == 1 and a == 0: return 1 + la * rho
    if h == 1 and a == 1: return 1 - rho
    return 1.0


def grid(lh, la, rho=RHO, n=MAXG):
    g = [[_pois(lh, h) * _pois(la, a) * _dc(h, a, lh, la, rho) for a in range(n)] for h in range(n)]
    s = sum(map(sum, g))
    return [[x / s for x in row] for row in g]


def p1x2(g):
    n = len(g)
    return (sum(g[h][a] for h in range(n) for a in range(n) if h > a),
            sum(g[h][h] for h in range(n)),
            sum(g[h][a] for h in range(n) for a in range(n) if h < a))


def p_over(g, line):
    n = len(g)
    return sum(g[h][a] for h in range(n) for a in range(n) if h + a > line)


def fit(fair_1x2, total=None, rho=RHO):
    """(lambda_dom, lambda_ext) reproduisant au mieux le 1X2 juste (et le total juste si donne : (ligne, p_plus))."""
    best, bl = 9e9, (1.4, 1.1)
    def err(lh, la):
        g = grid(lh, la, rho)
        e = sum((x - y) ** 2 for x, y in zip(p1x2(g), fair_1x2))
        if total: e += (p_over(g, total[0]) - total[1]) ** 2
        return e
    for lh in [0.2 + 0.1 * i for i in range(35)]:
        for la in [0.2 + 0.1 * i for i in range(35)]:
            e = err(lh, la)
            if e < best: best, bl = e, (lh, la)
    step = 0.05
    for _ in range(40):
        imp = False
        for dh, da in ((step, 0), (-step, 0), (0, step), (0, -step)):
            c = (max(0.05, bl[0] + dh), max(0.05, bl[1] + da)); e = err(*c)
            if e < best: best, bl, imp = e, c, True
        if not imp: step /= 2
    return bl


class Match:
    """Probabilites des evenements d'un match a partir de (lambda_dom, lambda_ext) : FT, 1re et 2e mi-temps."""

    def __init__(self, lh, la, rho=RHO, f1=F1):
        self.lh, self.la = lh, la
        self.ft = grid(lh, la, rho)
        self.h1 = grid(lh * f1, la * f1, rho)
        self.h2 = grid(lh * (1 - f1), la * (1 - f1), rho)

    def prob(self, pred):
        """P(pred(h1_dom, h1_ext, h2_dom, h2_ext)) par enumeration des deux mi-temps (independantes)."""
        n = len(self.h1); s = 0.0
        for a in range(n):
            for b in range(n):
                p1 = self.h1[a][b]
                if p1 < 1e-9: continue
                for c in range(n):
                    for d in range(n):
                        p = p1 * self.h2[c][d]
                        if p > 1e-12 and pred(a, b, c, d): s += p
        return s

    def ft_prob(self, pred):
        n = len(self.ft)
        return sum(self.ft[h][a] for h in range(n) for a in range(n) if pred(h, a))
