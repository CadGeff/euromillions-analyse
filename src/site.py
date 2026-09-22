"""
Generation de la page web du projet.

Lit les donnees normalisees, recalcule les statistiques affichees et produit
un fichier HTML autonome dans docs/, dossier que GitHub Pages sait servir
directement depuis la branche main.

La page n'appelle aucune ressource externe : les donnees sont injectees dans
le HTML et les graphiques sont dessines en SVG. Elle s'ouvre donc aussi bien
en local qu'en ligne, et continuera de fonctionner sans dependance a un CDN.

Le modele probabiliste vit dans modele.py ; les chiffres affiches ici en
decoulent directement, pour qu'il n'y ait qu'un seul endroit ou se tromper.

Sortie : docs/index.html
"""

from __future__ import annotations

import datetime
import json
from pathlib import Path

import pandas as pd

from analyse import (
    COLONNES_BOULES,
    COLONNES_ETOILES,
    ETOILES_PAR_REGIME,
    RANGS,
    RANG_PRINCIPAL,
    RANG_TEMOIN,
    BOULES_MAX,
    BOULES_TIREES,
    charger,
    ecarts_maximaux,
    frequences,
    popularite,
)
from modele import SEUIL, Tirage, p_empirique, verifier
from scipy import stats

RACINE = Path(__file__).resolve().parents[1]
DOSSIER_SITE = RACINE / "docs"
GABARIT = Path(__file__).resolve().parent / "gabarit_site.html"


def construire_donnees() -> dict:
    # charger() reimpose le tri par date : les calculs d'absence lisent la
    # serie dans l'ordre des lignes et seraient faux sur un fichier inverse.
    df = charger()

    t = Tirage(K=BOULES_MAX, B=BOULES_TIREES, N=len(df))
    freq = frequences(df, COLONNES_BOULES, BOULES_MAX)
    controle = verifier(t)
    test = t.test_ajustement(freq.values)
    abs_par_numero = ecarts_maximaux(df, COLONNES_BOULES, BOULES_MAX)

    # Courbe de densite, pour situer la valeur mise a l'echelle.
    x_max = max(test["critique"] * 1.6, test["khi2"] * 1.4)
    pas = x_max / 240
    courbe = [{"x": round(i * pas, 3),
               "y": round(float(stats.chi2.pdf(i * pas, test["ddl"])), 8)}
              for i in range(241)]

    # Les quatre tests reunis : c'est leur nombre qui rend la correction pour
    # comparaisons multiples necessaire.
    def espace(n: int) -> str:
        return f"{n:,}".replace(",", "\u202f")   # espace fine insecable

    tests = [{
        "nom": "Boules 1-50",
        "detail": f"{espace(len(df))} tirages, 50 numéros",
        "p": round(test["p"], 4),
    }]
    for regime, maximum in ETOILES_PAR_REGIME.items():
        bloc = df[df["regime"] == regime]
        if bloc.empty:
            continue
        te = Tirage(K=maximum, B=2, N=len(bloc))
        res = te.test_ajustement(frequences(bloc, COLONNES_ETOILES, maximum).values)
        tests.append({
            "nom": f"Étoiles {regime.split(' : ')[0]}",
            "detail": f"{espace(len(bloc))} tirages, {maximum} étoiles",
            "p": round(res["p"], 4),
        })

    table_rangs, pop = popularite(df)
    medianes = [{"petits": int(k), "gagnants": int(v["median"]), "tirages": int(v["count"])}
                for k, v in pop["medianes"].iterrows()]
    rangs = [{"rang": int(r),
              "boules": int(l["boules_exigees"]),
              "etoiles": int(l["etoiles_exigees"]),
              "tirages": int(l["tirages"]),
              "rho": round(float(l["rho"]), 3)}
             # Tri sur les deux criteres : a nombre de boules egal, les rangs
             # s'ordonnent par nombre d'etoiles. Trier sur les seules boules
             # laissait l'ordre interne au hasard de l'index, ce qui donnait
             # une suite de rangs incomprehensible a la lecture.
             for r, l in table_rangs.sort_values(
                 ["boules_exigees", "etoiles_exigees"]).iterrows()]

    return {
        "periode": {
            "debut": df["date_tirage"].min().strftime("%d/%m/%Y"),
            "fin": df["date_tirage"].max().strftime("%d/%m/%Y"),
            "tirages": len(df),
            # Le jeu de donnees est un instantane : EuroMillions tire deux fois
            # par semaine, la page prend donc du retard des le tirage suivant.
            "generee_le": datetime.date.today().strftime("%d/%m/%Y"),
        },
        "frequences": [
            {
                "numero": int(n),
                "sorties": int(c),
                "absence_max": int(abs_par_numero.loc[int(n), "absence_max"]),
                "absence_finale": int(abs_par_numero.loc[int(n), "absence_finale"]),
            }
            for n, c in freq.items()
        ],
        "reference": {
            "total": int(freq.sum()),
            "attendu": round(t.attendu, 1),
            "ecart_type": round(t.ecart_type, 2),
            "ecart_type_simule": round(controle["ecart_type_simule"], 2),
            "bande_ponctuelle": [round(v) for v in t.bande(SEUIL)],
            "bande_simultanee": [round(v) for v in t.bande(SEUIL, comparaisons=BOULES_MAX)],
            "attendus_hors_bande": round(BOULES_MAX * SEUIL, 1),
        },
        "test": {
            "khi2_brut": round(test["khi2_brut"], 2),
            "facteur": round(test["facteur"], 4),
            "khi2": round(test["khi2"], 2),
            "ddl": test["ddl"],
            "critique": round(test["critique"], 2),
            "p": round(test["p"], 4),
            "p_simulee": round(p_empirique(controle["distribution"], test["khi2_brut"]), 4),
            "courbe": courbe,
        },
        "tests": tests,
        "multiples": {
            "nombre": len(tests),
            "risque_global": round((1 - (1 - SEUIL) ** len(tests)) * 100),
            "bonferroni": round(SEUIL / len(tests), 4),
        },
        "popularite": {
            "moyenne_observee": round(pop["moyenne_observee"], 2),
            "moyenne_theorique": round(pop["moyenne_theorique"], 2),
            "part_observee": round(pop["part_observee"] * 100, 1),
            "part_theorique": round(pop["part_theorique"] * 100, 1),
            "medianes": medianes,
            "rangs": rangs,
            "rang_principal": RANG_PRINCIPAL,
            "rang_temoin": RANG_TEMOIN,
            "rho_principal": round(pop["rho_principal"], 3),
            "rho_temoin": round(pop["rho_temoin"], 3),
            "p_temoin": round(pop["p_temoin"], 2),
            "boules_principal": RANGS[RANG_PRINCIPAL][0],
            "boules_temoin": RANGS[RANG_TEMOIN][0],
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

    ref, test = donnees["reference"], donnees["test"]
    hors = [f["numero"] for f in donnees["frequences"]
            if not ref["bande_ponctuelle"][0] <= f["sorties"] <= ref["bande_ponctuelle"][1]]
    print("docs/index.html genere.")
    print(f"  {donnees['periode']['tirages']} tirages, {ref['total']} boules")
    print(f"  ecart-type {ref['ecart_type']} (simule {ref['ecart_type_simule']})")
    print(f"  khi2 brut {test['khi2_brut']} x{test['facteur']} -> {test['khi2']} "
          f"(seuil {test['critique']}), p = {test['p']} (simulee {test['p_simulee']})")
    print(f"  hors bande ponctuelle : {len(hors)} numero(s) {hors} "
          f"pour {ref['attendus_hors_bande']} attendu(s)")
    print(f"  popularite : rho rang {donnees['popularite']['rang_principal']} = "
          f"{donnees['popularite']['rho_principal']}, "
          f"temoin rang {donnees['popularite']['rang_temoin']} = "
          f"{donnees['popularite']['rho_temoin']}")


if __name__ == "__main__":
    main()
