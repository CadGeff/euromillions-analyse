"""
Modele probabiliste du tirage, et sa verification.

Ce module existe a cause d'une erreur. La premiere version du projet modelisait
les sorties comme 5N tirages independants d'une boule parmi 50, soit une loi
B(5N, 1/50). C'est faux : un tirage EuroMillions sort 5 boules DISTINCTES.
Les boules d'un meme tirage ne sont pas independantes, et cette dependance
reduit la variance.

Le modele correct, pour un numero donne :

    a chaque tirage, il sort ou il ne sort pas, avec p = B/K
    sur N tirages independants, son nombre de sorties suit B(N, B/K)

Les deux modeles donnent la MEME esperance, ce qui rend l'erreur invisible a
l'oeil : seule la variance differe. Pour K=50, B=5, N=1981 :

    faux  : sigma = sqrt(5N x 1/50 x 49/50) = 13.93
    vrai  : sigma = sqrt(N x 1/10 x 9/10)   = 13.35   (4.3 % plus etroit)

Le meme oubli affecte le test d'ajustement. La statistique de Pearson ne suit
pas chi2(K-1) ici. La covariance entre les effectifs de deux numeros vaut
-N p(1-p) / (K-1) : la matrice de covariance est celle d'une loi multinomiale
multipliee par (K-B)/(K-1). La statistique brute suit donc, pour N grand,
(K-B)/(K-1) x chi2(K-1) - pas seulement en moyenne, en loi. La multiplier par
(K-1)/(K-B) la ramene donc sur chi2(K-1) quand N est grand. Pour N fini, ce
n'est qu'une approximation : avec N = 20, la loi s'en ecarte nettement.
`verifier()` la confronte a la simulation pour le N reel (1 981 tirages :
p = 0,187 par la loi, 0,185 par simulation).

Deux approximations ont egalement ete retirees des bandes de variation :
l'approximation normale de la loi binomiale, fausse en queue de distribution
(voir Tirage.bande), et l'absence de reference pour les absences prolongees
(voir simuler_absences).

Plutot que de faire confiance a ces formules, `verifier()` les confronte a une
simulation du tirage reel. C'est ce qui a permis de trancher la premiere fois,
et c'est ce qui detectera une erreur de modele la prochaine fois.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy import stats

SEUIL = 0.05


@dataclass
class Tirage:
    """Parametres d'un jeu : K numeros possibles, B tires par tirage, N tirages."""

    K: int
    B: int
    N: int

    @property
    def p(self) -> float:
        """Probabilite qu'un numero donne sorte lors d'un tirage."""
        return self.B / self.K

    @property
    def attendu(self) -> float:
        """Nombre de sorties attendu par numero."""
        return self.N * self.p

    @property
    def ecart_type(self) -> float:
        """Ecart-type du nombre de sorties d'un numero, sous equiprobabilite."""
        return (self.N * self.p * (1 - self.p)) ** 0.5

    @property
    def facteur_khi2(self) -> float:
        """Mise a l'echelle de la statistique de Pearson vers chi2(K-1).

        La statistique brute suit (K-B)/(K-1) x chi2(K-1) : la multiplier par
        (K-1)/(K-B) la ramene sur la loi de reference.
        """
        return (self.K - 1) / (self.K - self.B)

    def p_individuelle(self, sorties: np.ndarray | int) -> np.ndarray | float:
        """p-value bilaterale exacte d'un effectif, sous la loi B(N, B/K).

        Methode du doublement : min(1, 2 x min(queue basse, queue haute)).
        C'est une convention parmi d'autres pour une loi discrete asymetrique.
        La methode de Fisher (scipy.stats.binomtest) somme les probabilites
        des effectifs au plus aussi probables que l'observe ; pour le 22
        (155 sorties), elle donne 0,00097 contre 0,00099 ici. Le doublement
        n'est pas systematiquement plus prudent : selon l'effectif, il donne
        une valeur plus grande ou plus petite que Fisher.

        Le choix est sans consequence sur le test du numero le plus atypique :
        sa p-value vient de la simulation, qui applique la meme convention aux
        historiques simules et aux donnees reelles (voir verifier).
        """
        sorties = np.asarray(sorties)
        bas = stats.binom.cdf(sorties, self.N, self.p)
        haut = stats.binom.sf(sorties - 1, self.N, self.p)
        return np.minimum(1.0, 2 * np.minimum(bas, haut))

    def bande(self, risque: float, comparaisons: int = 1) -> tuple[int, int]:
        """Plus petit et plus grand effectif qui ne sont PAS signales.

        Calculee sur la loi binomiale exacte. L'approximation normale, utilisee
        auparavant, est fausse precisement la ou la bande sert : en queue. Avec
        la correction pour 50 comparaisons, elle placait la borne basse a 154,2,
        et le 22 (155 sorties) dedans ; la loi exacte la place a 156, et le 22
        dehors. La loi B(N, 1/10) est asymetrique : sa queue basse est plus
        courte que ne le suppose la loi normale.

        `comparaisons` applique la correction de Bonferroni : examiner K numeros
        a la fois multiplie les occasions de depasser la bande par hasard.
        """
        seuil = risque / comparaisons
        valeurs = np.arange(self.N + 1)
        retenues = valeurs[self.p_individuelle(valeurs) >= seuil]
        return int(retenues.min()), int(retenues.max())

    def part_hors_bande(self, bande: tuple[int, int]) -> float:
        """Probabilite exacte qu'un numero sorte de la bande sous l'hypothese
        nulle. Loi discrete oblige, elle est inferieure au risque nominal :
        un peu plus de 4,3 % pour la bande « a 95 % », pas 5 %."""
        bas, haut = bande
        return float(stats.binom.cdf(bas - 1, self.N, self.p)
                     + stats.binom.sf(haut, self.N, self.p))

    def test_ajustement(self, effectifs: np.ndarray) -> dict:
        """Test d'equiprobabilite, avec la correction du modele sans remise."""
        khi2_brut = float(stats.chisquare(effectifs)[0])
        khi2 = khi2_brut * self.facteur_khi2
        ddl = self.K - 1
        return {
            "khi2_brut": khi2_brut,
            "khi2": khi2,
            "facteur": self.facteur_khi2,
            "ddl": ddl,
            "critique": float(stats.chi2.ppf(1 - SEUIL, ddl)),
            "p": float(stats.chi2.sf(khi2, ddl)),
        }


def simuler_effectifs(t: Tirage, repetitions: int, graine: int) -> np.ndarray:
    """`repetitions` historiques complets de N tirages de B numeros distincts
    parmi K. Renvoie les effectifs, un historique par ligne."""
    rng = np.random.default_rng(graine)
    base = np.tile(np.arange(t.K), (t.N, 1))
    effectifs = np.empty((repetitions, t.K), dtype=np.int64)
    for i in range(repetitions):
        # Chaque ligne est melangee independamment ; ses B premieres valeurs
        # sont B numeros distincts, tous les sous-ensembles equiprobables.
        tires = rng.permuted(base, axis=1)[:, : t.B]
        effectifs[i] = np.bincount(tires.ravel(), minlength=t.K)
    return effectifs


def verifier(t: Tirage, repetitions: int = 4000, graine: int = 20260922) -> dict:
    """Confronte les formules ci-dessus a une simulation du tirage reel.

    On simule `repetitions` historiques complets de N tirages, chacun tirant B
    numeros distincts parmi K, puis on compare :
      - l'ecart-type simule des effectifs a `ecart_type`
      - la moyenne simulee de la statistique brute a K-B
      - la p-value empirique a celle donnee par la loi corrigee

    La simulation fournit aussi les lois de reference des extremes : le plus
    grand effectif, le plus petit, et la plus petite p-value individuelle
    parmi les K numeros. C'est la bonne reference pour juger « le numero le
    plus sorti » : il y en a toujours un, et il faut savoir jusqu'ou le hasard
    le pousse.

    Un ecart notable entre formule et simulation signale une erreur de modele,
    pas une fluctuation.
    """
    effectifs = simuler_effectifs(t, repetitions, graine)
    khi2 = ((effectifs - t.attendu) ** 2 / t.attendu).sum(axis=1)

    # On suit les effectifs de CHAQUE numero d'une simulation a l'autre : c'est
    # cette dispersion-la que `ecart_type` pretend decrire. Les K numeros etant
    # interchangeables sous l'hypothese nulle, on moyenne les K estimations.
    return {
        "ecart_type_simule": float(effectifs.std(axis=0).mean()),
        "ecart_type_formule": t.ecart_type,
        "khi2_moyen_simule": float(khi2.mean()),
        "khi2_moyen_attendu": float(t.K - t.B),
        "distribution": khi2,
        "maximums": effectifs.max(axis=1),
        "minimums": effectifs.min(axis=1),
        "p_minimales": t.p_individuelle(effectifs).min(axis=1),
    }


def p_empirique(distribution: np.ndarray, observe: float, sens: str = "haut") -> float:
    """Proportion des simulations au moins aussi extremes que l'observation.

    `sens="haut"` : valeurs >= observe ; `sens="bas"` : valeurs <= observe.
    """
    if sens == "haut":
        return float((distribution >= observe).mean())
    return float((distribution <= observe).mean())


def plus_longues_absences(presence: np.ndarray) -> np.ndarray:
    """Pour chaque colonne d'une matrice booleenne (tirages x numeros), la
    plus longue serie de tirages consecutifs sans sortie.

    Les series de debut et de fin d'historique comptent, comme dans l'analyse
    des donnees reelles : la reference doit etre mesuree de la meme facon que
    l'observation.
    """
    n = presence.shape[0]
    records = np.empty(presence.shape[1], dtype=np.int64)
    for k in range(presence.shape[1]):
        sorties = np.flatnonzero(presence[:, k])
        if sorties.size == 0:
            records[k] = n
            continue
        bornes = np.concatenate(([-1], sorties, [n]))
        records[k] = int((np.diff(bornes) - 1).max())
    return records


def simuler_absences(t: Tirage, repetitions: int = 2000, graine: int = 20260923) -> dict:
    """Lois de reference des absences prolongees, sous l'hypothese nulle.

    Sans elles, montrer les absences observees ne demontre rien : on ne sait
    pas si 87 tirages d'absence est long ou banal. On simule donc des
    historiques complets et on releve, pour chacun, le record toutes boules
    confondues et la mediane des records par numero.
    """
    rng = np.random.default_rng(graine)
    base = np.tile(np.arange(t.K), (t.N, 1))
    record = np.empty(repetitions, dtype=np.int64)
    mediane = np.empty(repetitions)
    for i in range(repetitions):
        tires = rng.permuted(base, axis=1)[:, : t.B]
        presence = np.zeros((t.N, t.K), dtype=bool)
        np.put_along_axis(presence, tires, True, axis=1)
        records = plus_longues_absences(presence)
        record[i] = records.max()
        mediane[i] = np.median(records)
    return {"record": record, "mediane": mediane}


if __name__ == "__main__":
    for nom, t in [
        ("Boules 2004-2026", Tirage(K=50, B=5, N=1981)),
        ("Etoiles 2011-2016", Tirage(K=11, B=2, N=562)),
    ]:
        v = verifier(t, repetitions=3000)
        print(f"{nom}")
        print(f"   ecart-type : formule {v['ecart_type_formule']:.3f}  "
              f"simule {v['ecart_type_simule']:.3f}")
        print(f"   khi2 moyen : attendu {v['khi2_moyen_attendu']:.2f}  "
              f"simule {v['khi2_moyen_simule']:.2f}")
        print(f"   variance du khi2 : attendue {2 * (t.K - t.B) ** 2 / (t.K - 1):.1f}  "
              f"simulee {v['distribution'].var():.1f}")
        print()
