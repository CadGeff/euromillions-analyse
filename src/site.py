"""
Generation de la page web du projet.

Reprend les resultats de analyse.py et produit un fichier HTML autonome dans
docs/, dossier que GitHub Pages sait servir directement depuis la branche main.

La page n'appelle aucune ressource externe : les donnees sont injectees dans
le HTML et les graphiques sont dessines en SVG. Elle s'ouvre donc aussi bien
en local qu'en ligne, et continuera de fonctionner sans dependance a un CDN.

Les chiffres affiches viennent tous de analyse.analyser() : il n'y a qu'un
seul endroit ou se tromper. Les phrases de conclusion de la page sont elles
aussi construites a partir des resultats, jamais ecrites en dur.

La sortie ne depend que des donnees : relancer le script sans nouvelle
archive redonne un fichier identique, octet pour octet.

Sortie : docs/index.html
"""

from __future__ import annotations

import json
import math
from pathlib import Path

from scipy import stats

from analyse import analyser
from modele import SEUIL

RACINE = Path(__file__).resolve().parents[1]
DOSSIER_SITE = RACINE / "docs"
GABARIT = Path(__file__).resolve().parent / "gabarit_site.html"


def arrondi(valeur: float, chiffres: int = 4) -> float | None:
    return None if valeur is None or math.isnan(valeur) else round(float(valeur), chiffres)


def construire_donnees() -> dict:
    a = analyser()
    df, boules, absences = a["df"], a["boules"], a["absences"]
    table, pop, famille = a["table"], a["pop"], a["famille"]
    t, test, ext = boules["tirage"], boules["test"], boules["extreme"]

    # Courbe de densite, pour situer la valeur mise a l'echelle.
    x_max = max(test["critique"] * 1.6, test["khi2"] * 1.4)
    pas = x_max / 240
    courbe = [{"x": round(i * pas, 3),
               "y": round(float(stats.chi2.pdf(i * pas, test["ddl"])), 8)}
              for i in range(241)]

    return {
        "periode": {
            "debut": df["date_tirage"].min().strftime("%d/%m/%Y"),
            "fin": df["date_tirage"].max().strftime("%d/%m/%Y"),
            "tirages": len(df),
        },
        "frequences": [
            {
                "numero": int(n),
                "sorties": int(c),
                "absence_max": int(absences["ecarts"].loc[int(n), "absence_max"]),
                "absence_finale": int(absences["ecarts"].loc[int(n), "absence_finale"]),
            }
            for n, c in boules["freq"].items()
        ],
        "reference": {
            "total": int(boules["freq"].sum()),
            "attendu": round(t.attendu, 1),
            "ecart_type": round(t.ecart_type, 2),
            "ecart_type_simule": round(boules["controle"]["ecart_type_simule"], 2),
            "bande_ponctuelle": list(boules["bande"]),
            "bande_simultanee": list(boules["bande_corrigee"]),
            "attendus_hors_bande": round(boules["attendus_hors_bande"], 1),
            "p_maximum": arrondi(boules["p_maximum"]),
            "p_minimum": arrondi(boules["p_minimum"]),
        },
        "extreme": {
            "numero": ext["numero"],
            "sorties": ext["sorties"],
            "sens": ext["sens"],
            "p_individuelle": arrondi(ext["p_individuelle"], 6),
            "p_bonferroni": arrondi(ext["p_bonferroni"]),
            "p": arrondi(ext["p"]),
        },
        "test": {
            "khi2_brut": round(test["khi2_brut"], 2),
            "facteur": round(test["facteur"], 4),
            "khi2": round(test["khi2"], 2),
            "ddl": test["ddl"],
            "critique": round(test["critique"], 2),
            "p": round(test["p"], 4),
            "p_simulee": round(test["p_simulee"], 4),
            "percentile": round(boules["percentile_khi2"] * 100),
            "courbe": courbe,
        },
        "tests": [{"nom": e["libelle"], "detail": e["detail"], "p": round(e["p"], 4)}
                  for e in famille["tests"]],
        "multiples": {
            "nombre": famille["nombre"],
            "risque_global": round(famille["risque_global"] * 100),
            "bonferroni": round(famille["bonferroni"], 4),
            "seuil": SEUIL,
        },
        "absences": {
            "record": absences["record"],
            "numero_record": absences["numero_record"],
            "mediane": absences["mediane"],
            "record_simule_mediane": absences["record_simule_mediane"],
            "record_simule_ic": absences["record_simule_ic"],
            "p_record": arrondi(absences["p_record"]),
            "mediane_simulee": absences["mediane_simulee"],
            "mediane_simulee_ic": absences["mediane_simulee_ic"],
            "repetitions": absences["repetitions"],
        },
        "popularite": {
            "moyenne_observee": round(pop["moyenne_observee"], 2),
            "moyenne_theorique": round(pop["moyenne_theorique"], 2),
            "part_observee": round(pop["part_observee"] * 100, 1),
            "part_theorique": round(pop["part_theorique"] * 100, 1),
            "principale": pop["principale"],
            "temoin": pop["temoin"],
            "medianes": [{"petits": int(k), "gagnants": arrondi(m["gagnants"], 0),
                          "gain": arrondi(m["gain"], 2), "tirages": int(m["tirages"])}
                         for k, m in pop["medianes"].iterrows()],
            "typique": pop["typique"],
            "haut": pop["haut"],
            "gagnants_vs_typique": round(pop["gagnants_vs_typique"] * 100),
            "gain_vs_typique": round(pop["gain_vs_typique"] * 100),
            "rho_principal": round(pop["rho_principal"], 3),
            "rho_gain": round(pop["rho_gain"], 3),
            "effet_principal_gain": round(pop["effet_principal_gain"] * 100, 1),
            "effet_temoin": round(pop["effet_temoin"] * 100, 1),
            "ic_temoin": [round(v * 100, 1) for v in pop["ic_temoin"]],
            "temoin_nul": pop["temoin_nul"],
            "dose_effet": pop["dose_effet"],
            "volume": {k: round(v, 3) for k, v in pop["volume"].items()},
            "marches": sum(len(f["marches"]) for f in pop["familles"].values()),
            "combinaisons": [
                {"combinaison": c,
                 "boules": int(l["boules_exigees"]),
                 "etoiles": int(l["etoiles_exigees"]),
                 "rang": int(l["rang_actuel"]),
                 "mesurable": bool(l["mesurable"]),
                 "part_sans_gagnant": round(float(l["part_sans_gagnant"]) * 100),
                 # None (null) pour une combinaison non mesurable
                 "effet_gagnants": arrondi(l["effet_gagnants"] * 100, 1),
                 "ic_gagnants": [arrondi(l["effet_gagnants_bas"] * 100, 1),
                                 arrondi(l["effet_gagnants_haut"] * 100, 1)],
                 "effet_gain": arrondi(l["effet_gain"] * 100, 1),
                 "ic_gain": [arrondi(l["effet_gain_bas"] * 100, 1),
                             arrondi(l["effet_gain_haut"] * 100, 1)]}
                for c, l in table.iterrows()],
        },
    }


def main() -> None:
    DOSSIER_SITE.mkdir(parents=True, exist_ok=True)
    donnees = construire_donnees()

    gabarit = GABARIT.read_text(encoding="utf-8")
    marqueur = "/* DONNEES_INJECTEES */"
    if gabarit.count(marqueur) != 1:
        raise ValueError(f"Le gabarit doit contenir exactement un marqueur {marqueur}")
    # allow_nan=False : un NaN oublie ferait echouer la generation plutot que
    # d'injecter dans la page une valeur que JSON ne connait pas.
    html = gabarit.replace(marqueur, "const DONNEES = "
                           + json.dumps(donnees, ensure_ascii=False, allow_nan=False) + ";")
    (DOSSIER_SITE / "index.html").write_text(html, encoding="utf-8")

    ref, test, ext = donnees["reference"], donnees["test"], donnees["extreme"]
    print("docs/index.html genere.")
    print(f"  {donnees['periode']['tirages']} tirages, {ref['total']} boules")
    print(f"  ecart-type {ref['ecart_type']} (simule {ref['ecart_type_simule']})")
    print(f"  khi2 brut {test['khi2_brut']} x{test['facteur']} -> {test['khi2']} "
          f"(seuil {test['critique']}), p = {test['p']} (simulee {test['p_simulee']})")
    print(f"  bandes exactes {ref['bande_ponctuelle']} et {ref['bande_simultanee']} ; "
          f"plus atypique : {ext['numero']} (p corrigee {ext['p']})")
    pop = donnees["popularite"]
    print(f"  popularite {pop['principale']} : rho gagnants {pop['rho_principal']}, "
          f"rho gain {pop['rho_gain']}, dose-effet {'ok' if pop['dose_effet'] else 'NON'}")


if __name__ == "__main__":
    main()
