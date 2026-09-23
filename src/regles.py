"""
Regles du jeu EuroMillions : regimes, combinaisons gagnantes et rangs.

Un seul endroit pour les regles, importe par l'ingestion, l'analyse et la
page. Les versions precedentes les recopiaient dans chaque script, et c'est
ainsi qu'une erreur de rang a pu y survivre sans que rien ne la signale.

L'erreur en question : le numero d'un rang ne designe pas toujours la meme
combinaison. La FDJ numerote les rangs de la combinaison la plus rare a la
plus frequente, et changer le nombre d'etoiles change les probabilites, donc
l'ordre. Deux paires de rangs ont ainsi echange leur combinaison :

    rang          2004-2011    2011-2016    depuis 09/2016
    6 / 7         4+0 / 3+2    4+0 / 3+2    3+2 / 4+0
    8 / 9         3+1 / 2+2    2+2 / 3+1    2+2 / 3+1

Plutot qu'une table recopiee a la main, les rangs sont donc DERIVES des
probabilites de chaque regime. L'ingestion verifie ensuite cette derivation
contre le nombre de gagnants publie (voir ingest.controler_rangs) : le jour
ou la FDJ changerait de regle, le controle le signalera.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import comb

import pandas as pd

BOULES = 50           # numeros possibles, inchanges depuis 2004
BOULES_TIREES = 5
ETOILES_TIREES = 2


@dataclass(frozen=True)
class Regime:
    """Une periode de regles stables.

    `rang_2_0` : la combinaison 2 boules + 0 etoile n'est un rang de gain que
    depuis le 10/05/2011 (12 rangs avant, 13 ensuite).
    """

    nom: str
    debut: str          # inclus
    fin: str            # exclu
    etoiles: int
    rang_2_0: bool

    def contient(self, date: pd.Timestamp) -> bool:
        return pd.Timestamp(self.debut) <= date < pd.Timestamp(self.fin)


# Les noms sont conserves tels quels : ils figurent dans les CSV produits.
# Bornes : 10/05/2011, premier tirage du mardi, 11 etoiles et rang 2+0 ;
# 27/09/2016, premier tirage a 12 etoiles (la borne est posee au 24/09, entre
# le dernier tirage de l'ancien regime et le premier du nouveau).
# ingest.controler_regimes confronte ces bornes aux donnees.
REGIMES = [
    Regime("2004-2011 : 50 boules / 9 etoiles", "2004-02-13", "2011-05-10", 9, False),
    Regime("2011-2016 : 50 boules / 11 etoiles", "2011-05-10", "2016-09-24", 11, True),
    Regime("2016+ : 50 boules / 12 etoiles", "2016-09-24", "2100-01-01", 12, True),
]
REGIMES_PAR_NOM = {r.nom: r for r in REGIMES}

# Toutes les combinaisons (boules, etoiles) qui ont ete un rang de gain.
COMBINAISONS = [(5, 2), (5, 1), (5, 0), (4, 2), (4, 1), (4, 0), (3, 2),
                (3, 1), (3, 0), (2, 2), (2, 1), (2, 0), (1, 2)]


def regime_de(date: pd.Timestamp) -> Regime | None:
    if pd.isna(date):
        return None
    for regime in REGIMES:
        if regime.contient(date):
            return regime
    return None


def probabilite(boules: int, etoiles: int, nb_etoiles: int) -> float:
    """Probabilite qu'une grille trouve EXACTEMENT `boules` et `etoiles`."""
    p_boules = (comb(BOULES_TIREES, boules) * comb(BOULES - BOULES_TIREES, BOULES_TIREES - boules)
                / comb(BOULES, BOULES_TIREES))
    p_etoiles = (comb(ETOILES_TIREES, etoiles)
                 * comb(nb_etoiles - ETOILES_TIREES, ETOILES_TIREES - etoiles)
                 / comb(nb_etoiles, ETOILES_TIREES))
    return p_boules * p_etoiles


def combinaisons_du_regime(regime: Regime) -> list[tuple[int, int]]:
    """Combinaisons gagnantes, dans l'ordre des rangs : de la plus rare a la
    plus frequente. L'element d'indice i est le rang i + 1."""
    retenues = [c for c in COMBINAISONS if regime.rang_2_0 or c != (2, 0)]
    return sorted(retenues, key=lambda c: probabilite(*c, regime.etoiles))


def rangs_du_regime(regime: Regime) -> dict[int, tuple[int, int]]:
    """{numero de rang: (boules, etoiles)} pour ce regime."""
    return {i + 1: c for i, c in enumerate(combinaisons_du_regime(regime))}


def rang_actuel(combinaison: tuple[int, int]) -> int:
    """Numero de rang de la combinaison dans le regime en vigueur, pour
    l'affichage : c'est celui que les joueurs connaissent aujourd'hui."""
    actuels = {c: r for r, c in rangs_du_regime(REGIMES[-1]).items()}
    return actuels[combinaison]


def colonne_gagnants(combinaison: tuple[int, int]) -> str:
    """Nom de colonne des gagnants d'une combinaison : 'gagnants_3b_0e'.

    Les colonnes sont nommees par combinaison et non par rang, justement
    parce que le numero de rang n'a pas le meme sens d'un regime a l'autre.
    """
    boules, etoiles = combinaison
    return f"gagnants_{boules}b_{etoiles}e"


def colonne_rapport(combinaison: tuple[int, int]) -> str:
    """Nom de colonne du gain par grille gagnante : 'rapport_3b_0e'."""
    boules, etoiles = combinaison
    return f"rapport_{boules}b_{etoiles}e"
