"""
Generation de la page web du projet.

Lit les donnees normalisees, recalcule les statistiques affichees et produit
un fichier HTML autonome dans docs/, dossier que GitHub Pages sait servir
directement depuis la branche main.

La page n'appelle aucune ressource externe : les donnees sont injectees dans
le HTML et les graphiques sont dessines en SVG. Elle s'ouvre donc aussi bien
en local qu'en ligne, et continuera de fonctionner sans dependance a un CDN.

Sortie : docs/index.html
"""

from __future__ import annotations

import datetime
import json
from math import comb
from pathlib import Path

import pandas as pd
from scipy import stats

RACINE = Path(__file__).resolve().parents[1]
DOSSIER_DONNEES = RACINE / "data" / "processed"
DOSSIER_SITE = RACINE / "docs"
GABARIT = Path(__file__).resolve().parent / "gabarit_site.html"

COLONNES_BOULES = [f"boule_{i}" for i in range(1, 6)]
BOULES_MAX = 50
SEUIL = 0.05


def absences(df: pd.DataFrame) -> dict[int, dict[str, int]]:
    """Plus longue absence et absence en cours, pour chaque numero."""
    presence = {n: [] for n in range(1, BOULES_MAX + 1)}
    for _, ligne in df[COLONNES_BOULES].iterrows():
        sortis = set(int(v) for v in ligne.dropna())
        for n in presence:
            presence[n].append(n in sortis)

    resultat = {}
    for n, serie in presence.items():
        record = courant = 0
        for sorti in serie:
            courant = 0 if sorti else courant + 1
            record = max(record, courant)
        resultat[n] = {"max": record, "actuelle": courant}
    return resultat


def construire_donnees() -> dict:
    df = pd.read_csv(DOSSIER_DONNEES / "euromillions_tirages.csv", sep=";",
                     parse_dates=["date_tirage"])

    frequences = (df[COLONNES_BOULES].stack().astype(int)
                  .value_counts()
                  .reindex(range(1, BOULES_MAX + 1), fill_value=0)
                  .sort_index())

    total = int(frequences.sum())
    p = 1 / BOULES_MAX
    attendu = total * p
    # Sous l'hypothese d'equiprobabilite, le nombre de sorties d'un numero suit
    # une loi binomiale B(total, 1/50). L'ecart-type donne la largeur de la
    # variation normale, celle qu'il ne faut pas confondre avec un biais.
    ecart_type = (total * p * (1 - p)) ** 0.5

    z_ponctuel = stats.norm.ppf(1 - SEUIL / 2)
    # Bande simultanee : on regarde 50 numeros a la fois, donc le seuil est
    # divise par 50 (Bonferroni). Sans cette correction, voir deux ou trois
    # numeros « hors norme » est la situation attendue, pas une anomalie.
    z_simultane = stats.norm.ppf(1 - SEUIL / (2 * BOULES_MAX))

    khi2, valeur_p = stats.chisquare(frequences.values)
    ddl = BOULES_MAX - 1
    critique = stats.chi2.ppf(1 - SEUIL, ddl)

    abs_par_numero = absences(df)

    # Courbe de densite du khi-deux, pour situer la valeur observee.
    x_max = max(critique * 1.6, khi2 * 1.4)
    pas = x_max / 240
    courbe = [{"x": round(i * pas, 3),
               "y": round(float(stats.chi2.pdf(i * pas, ddl)), 8)}
              for i in range(241)]

    dans_dates = df[COLONNES_BOULES].apply(
        lambda l: (l.dropna().astype(int) <= 31).sum(), axis=1)

    return {
        "periode": {
            "debut": df["date_tirage"].min().strftime("%d/%m/%Y"),
            "fin": df["date_tirage"].max().strftime("%d/%m/%Y"),
            "tirages": len(df),
            # Le jeu de donnees est un instantane : EuroMillions tire deux fois
            # par semaine, la page prend donc du retard des le tirage suivant.
            # Afficher la date de generation evite de laisser croire que les
            # chiffres valent pour aujourd'hui.
            "generee_le": datetime.date.today().strftime("%d/%m/%Y"),
        },
        "frequences": [
            {
                "numero": int(n),
                "sorties": int(c),
                "absence_max": abs_par_numero[int(n)]["max"],
                "absence_actuelle": abs_par_numero[int(n)]["actuelle"],
            }
            for n, c in frequences.items()
        ],
        "reference": {
            "total": total,
            "attendu": round(attendu, 1),
            "ecart_type": round(ecart_type, 2),
            "bande_ponctuelle": [round(attendu - z_ponctuel * ecart_type),
                                 round(attendu + z_ponctuel * ecart_type)],
            "bande_simultanee": [round(attendu - z_simultane * ecart_type),
                                 round(attendu + z_simultane * ecart_type)],
            "attendus_hors_bande": round(BOULES_MAX * SEUIL, 1),
        },
        "test": {
            "khi2": round(float(khi2), 2),
            "ddl": ddl,
            "critique": round(float(critique), 2),
            "p": round(float(valeur_p), 3),
            "courbe": courbe,
        },
        "popularite": {
            "moyenne_observee": round(float(dans_dates.mean()), 2),
            "moyenne_theorique": round(5 * 31 / BOULES_MAX, 2),
            "part_observee": round(float((dans_dates == 5).mean()) * 100, 1),
            "part_theorique": round(comb(31, 5) / comb(BOULES_MAX, 5) * 100, 1),
        },
    }


def main() -> None:
    DOSSIER_SITE.mkdir(parents=True, exist_ok=True)
    donnees = construire_donnees()

    gabarit = GABARIT.read_text(encoding="utf-8")
    html = gabarit.replace(
        "/* DONNEES_INJECTEES */",
        "const DONNEES = " + json.dumps(donnees, ensure_ascii=False) + ";",
    )
    (DOSSIER_SITE / "index.html").write_text(html, encoding="utf-8")

    ref = donnees["reference"]
    test = donnees["test"]
    hors = [f["numero"] for f in donnees["frequences"]
            if not ref["bande_ponctuelle"][0] <= f["sorties"] <= ref["bande_ponctuelle"][1]]
    print(f"docs/index.html genere.")
    print(f"  {donnees['periode']['tirages']} tirages, {ref['total']} boules")
    print(f"  khi2 = {test['khi2']} (seuil {test['critique']}), p = {test['p']}")
    print(f"  hors bande ponctuelle : {len(hors)} numero(s) {hors} "
          f"pour {ref['attendus_hors_bande']} attendu(s)")


if __name__ == "__main__":
    main()
