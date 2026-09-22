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
pas chi2(K-1) ici : sa moyenne vaut K-B et non K-1. Il faut donc la mettre a
l'echelle par (K-1)/(K-B) avant de la comparer a chi2(K-1).

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

        La statistique brute a pour moyenne K-B au lieu de K-1 : la multiplier
        par (K-1)/(K-B) la ramene sur la loi de reference.
        """
        return (self.K - 1) / (self.K - self.B)

    def bande(self, risque: float, comparaisons: int = 1) -> tuple[float, float]:
        """Intervalle de variation normale autour de l'attendu.

        `comparaisons` applique la correction de Bonferroni : examiner K numeros
        a la fois multiplie les occasions de depasser la bande par hasard.
        """
        z = stats.norm.ppf(1 - risque / (2 * comparaisons))
        return self.attendu - z * self.ecart_type, self.attendu + z * self.ecart_type

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
            "p": float(1 - stats.chi2.cdf(khi2, ddl)),
        }


def verifier(t: Tirage, repetitions: int = 4000, graine: int = 20260922) -> dict:
    """Confronte les formules ci-dessus a une simulation du tirage reel.

    On simule `repetitions` historiques completes de N tirages, chacun tirant B
    numeros distincts parmi K, puis on compare :
      - l'ecart-type simule des effectifs a `ecart_type`
      - la moyenne simulee de la statistique brute a K-B
      - la p-value empirique a celle donnee par la loi corrigee

    Un ecart notable signale une erreur de modele, pas une fluctuation.
    """
    rng = np.random.default_rng(graine)
    base = np.tile(np.arange(t.K), (t.N, 1))

    # On suit les effectifs d'UN numero d'une simulation a l'autre : c'est bien
    # cette dispersion-la que `ecart_type` pretend decrire. Mesurer l'ecart
    # entre les K numeros d'une meme simulation donnerait une autre quantite,
    # sous-estimee, et validerait la formule a tort.
    suivi = np.empty(repetitions)
    khi2 = np.empty(repetitions)
    for i in range(repetitions):
        tires = rng.permuted(base, axis=1)[:, : t.B]
        effectifs = np.bincount(tires.ravel(), minlength=t.K)
        suivi[i] = effectifs[0]
        khi2[i] = ((effectifs - t.attendu) ** 2 / t.attendu).sum()

    return {
        "ecart_type_simule": float(suivi.std()),
        "ecart_type_formule": t.ecart_type,
        "khi2_moyen_simule": float(khi2.mean()),
        "khi2_moyen_attendu": float(t.K - t.B),
        "distribution": khi2,
    }


def p_empirique(distribution: np.ndarray, khi2_brut: float) -> float:
    """Proportion des simulations atteignant ou depassant la valeur observee."""
    return float((distribution >= khi2_brut).mean())


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
        print()
