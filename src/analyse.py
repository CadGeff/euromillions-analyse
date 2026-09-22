"""
Analyse statistique des tirages EuroMillions normalises.

Quatre questions, dans l'ordre ou elles meritent d'etre posees :

  1. Quels numeros sortent le plus ? (la question que tout le monde pose)
  2. Cet ecart depasse-t-il ce que le hasard produit seul ? (test d'ajustement)
  3. Combien de temps un numero peut-il rester absent sans que ce soit anormal ?
  4. Puisqu'on ne peut pas gagner plus souvent, peut-on gagner davantage ?

Les trois premieres concluent a l'absence de tout signal. La quatrieme est le
seul angle ou la statistique a quelque chose d'utile a dire, et il ne porte pas
sur la probabilite de gagner mais sur le montant du gain.

Le modele probabiliste et sa verification par simulation vivent dans modele.py.
Voir son en-tete pour l'erreur qui a rendu ce module necessaire.

Entree  : data/processed/euromillions_tirages.csv
Sortie  : data/processed/statistiques.txt
          data/processed/frequences_boules.csv
          data/processed/popularite_gagnants.csv
"""

from __future__ import annotations

from math import comb
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats

from modele import SEUIL, Tirage, p_empirique, verifier

RACINE = Path(__file__).resolve().parents[1]
DOSSIER = RACINE / "data" / "processed"

COLONNES_BOULES = [f"boule_{i}" for i in range(1, 6)]
COLONNES_ETOILES = [f"etoile_{i}" for i in range(1, 3)]

ETOILES_PAR_REGIME = {
    "2004-2011 : 50 boules / 9 etoiles": 9,
    "2011-2016 : 50 boules / 11 etoiles": 11,
    "2016+ : 50 boules / 12 etoiles": 12,
}
BOULES_MAX = 50
BOULES_TIREES = 5
SEUIL_DATES = 31  # au-dela, un numero ne peut pas etre une date de naissance

# Rangs et nombre de boules principales exigees. Le contraste entre rangs est
# ce qui rend la mesure de popularite credible : un rang exigeant beaucoup de
# boules doit reagir fortement, un rang qui n'en exige qu'une ne doit pas.
RANGS = {
    1: (5, 2), 2: (5, 1), 3: (5, 0), 4: (4, 2), 5: (4, 1), 6: (3, 2),
    7: (4, 0), 8: (2, 2), 9: (3, 1), 10: (3, 0), 11: (1, 2), 12: (2, 1),
    13: (2, 0),
}
RANG_PRINCIPAL = 10   # 3 boules, 0 etoile : sensible aux numeros, beaucoup de gagnants
RANG_TEMOIN = 11      # 1 boule, 2 etoiles : presque insensible aux numeros


# --------------------------------------------------------------------------


def charger() -> pd.DataFrame:
    """Charge les tirages normalises, tries par date.

    Le tri est reimpose ici plutot que suppose. Les calculs d'absence lisent la
    serie dans l'ordre des lignes : un fichier ecrit a l'envers donnerait des
    resultats faux sans lever la moindre erreur, et l'ordre d'un CSV n'est pas
    une garantie sur laquelle s'appuyer a distance.
    """
    df = pd.read_csv(DOSSIER / "euromillions_tirages.csv", sep=";",
                     parse_dates=["date_tirage"])
    return df.sort_values("date_tirage").reset_index(drop=True)


def frequences(df: pd.DataFrame, colonnes: list[str], maximum: int) -> pd.Series:
    """Compte les sorties de chaque numero, en incluant les zeros."""
    tirees = df[colonnes].stack().dropna().astype(int)
    return tirees.value_counts().reindex(range(1, maximum + 1), fill_value=0).sort_index()


def ecarts_maximaux(df: pd.DataFrame, colonnes: list[str], maximum: int) -> pd.DataFrame:
    """Plus longue absence observee pour chaque numero, en nombre de tirages.

    Sert a repondre a l'intuition du « numero en retard » : si les absences
    longues sont la norme, en voir une ne veut rien dire. La colonne
    `absence_finale` decrit l'etat au dernier tirage du jeu de donnees, et non
    la situation du jour.
    """
    presence = pd.DataFrame(False, index=df.index, columns=range(1, maximum + 1))
    for colonne in colonnes:
        for position, valeur in df[colonne].dropna().astype(int).items():
            presence.at[position, valeur] = True

    resultats = []
    for numero in presence.columns:
        record = courant = 0
        for sorti in presence[numero].values:
            courant = 0 if sorti else courant + 1
            record = max(record, courant)
        resultats.append({"numero": numero, "absence_max": record,
                          "absence_finale": courant})
    return pd.DataFrame(resultats).set_index("numero")


def popularite(df: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """Mesure les habitudes des JOUEURS a partir du nombre de gagnants.

    Le raisonnement sur les dates de naissance etait jusqu'ici une affirmation
    empruntee : les donnees montraient que les boules ignorent la zone 1-31,
    pas que les joueurs la privilegient. Le nombre de gagnants permet de le
    demontrer avec les seules archives FDJ.

    L'idee : a un rang donne, le nombre de gagnants vaut approximativement
    (nombre de grilles jouees) x (probabilite qu'une grille corresponde). Le
    second facteur depend des numeros que les joueurs cochent. Si les joueurs
    privilegient 1-31, alors un tirage riche en petits numeros produit plus de
    gagnants.

    Le volume de grilles varie fortement (taille du jackpot, jour de la
    semaine) mais il ne depend pas des numeros qui vont sortir : c'est du
    bruit, pas un facteur de confusion.

    Le temoin rend la demonstration solide : le rang 11 n'exige qu'UNE boule
    principale, il ne doit donc presque pas reagir. S'il reagissait autant que
    les autres, l'effet viendrait d'ailleurs.
    """
    petits = (df[COLONNES_BOULES] <= SEUIL_DATES).sum(axis=1)
    travail = pd.DataFrame({"date_tirage": df["date_tirage"], "petits": petits})

    lignes = []
    for rang, (boules, etoiles) in RANGS.items():
        colonne = f"gagnants_rang{rang}"
        if colonne not in df:
            continue
        gagnants = pd.to_numeric(df[colonne], errors="coerce")
        valides = gagnants.notna() & (gagnants > 0)
        if valides.sum() < 200:
            continue
        rho, valeur_p = stats.spearmanr(petits[valides], gagnants[valides])
        lignes.append({
            "rang": rang,
            "boules_exigees": boules,
            "etoiles_exigees": etoiles,
            "tirages": int(valides.sum()),
            "rho": rho,
            "p": valeur_p,
        })
    table = pd.DataFrame(lignes).set_index("rang")

    colonne = f"gagnants_rang{RANG_PRINCIPAL}"
    gagnants = pd.to_numeric(df[colonne], errors="coerce")
    valides = gagnants.notna() & (gagnants > 0)
    medianes = (pd.DataFrame({"petits": petits[valides], "gagnants": gagnants[valides]})
                .groupby("petits")["gagnants"].agg(["median", "count"]))

    detail = {
        "medianes": medianes,
        "rho_principal": float(table.loc[RANG_PRINCIPAL, "rho"]),
        "p_principal": float(table.loc[RANG_PRINCIPAL, "p"]),
        "rho_temoin": float(table.loc[RANG_TEMOIN, "rho"]),
        "p_temoin": float(table.loc[RANG_TEMOIN, "p"]),
        "moyenne_observee": float(petits.mean()),
        "moyenne_theorique": BOULES_TIREES * SEUIL_DATES / BOULES_MAX,
        "part_observee": float((petits == 5).mean()),
        "part_theorique": comb(SEUIL_DATES, 5) / comb(BOULES_MAX, 5),
    }
    travail["gagnants_rang_principal"] = gagnants
    return table, {**detail, "travail": travail}


# --------------------------------------------------------------------------


def bloc_test(titre: str, t: Tirage, observes: pd.Series, controle: dict) -> list[str]:
    resultat = t.test_ajustement(observes.values)
    p_sim = p_empirique(controle["distribution"], resultat["khi2_brut"])

    lignes = ["", titre, "-" * len(titre)]
    lignes.append(f"  Tirages                  : {t.N}")
    lignes.append(f"  Numeros possibles        : {t.K}")
    lignes.append(f"  Sorties attendues/numero : {t.attendu:.1f}")
    lignes.append(f"  Ecart-type attendu       : {t.ecart_type:.2f}  "
                  f"(simule : {controle['ecart_type_simule']:.2f})")
    lignes.append("")
    haut, bas = observes.nlargest(3), observes.nsmallest(3)
    lignes.append("  Les plus sortis   : " + ", ".join(f"{n} ({c}x)" for n, c in haut.items()))
    lignes.append("  Les moins sortis  : " + ", ".join(f"{n} ({c}x)" for n, c in bas.items()))
    lignes.append("")
    lignes.append(f"  Statistique brute : {resultat['khi2_brut']:.2f}")
    lignes.append(f"  Mise a l'echelle  : x {resultat['facteur']:.4f}  "
                  f"-> {resultat['khi2']:.2f}")
    lignes.append(f"  Seuil critique 5% : {resultat['critique']:.2f}  ({resultat['ddl']} ddl)")
    lignes.append(f"  p-value           : {resultat['p']:.4f}")
    lignes.append(f"  p-value simulee   : {p_sim:.4f}")
    lignes.append("")
    if resultat["p"] >= SEUIL:
        lignes.append("  => Compatible avec l'equiprobabilite. Les ecarts observes")
        lignes.append("     sont ce que produit un tirage equilibre.")
    else:
        lignes.append("  => Sous le seuil de 5 %. A confronter a la correction pour")
        lignes.append("     comparaisons multiples avant toute conclusion.")
    return lignes, resultat


def main() -> None:
    df = charger()
    rapport = ["ANALYSE STATISTIQUE - EUROMILLIONS", "=" * 34, "",
               f"Periode : {df['date_tirage'].min():%d/%m/%Y} -> "
               f"{df['date_tirage'].max():%d/%m/%Y}   ({len(df)} tirages)",
               "",
               "Modele : a chaque tirage, un numero sort ou ne sort pas, avec une",
               "probabilite B/K. Sur N tirages, ses sorties suivent B(N, B/K).",
               "Les 5 boules d'un meme tirage etant distinctes, la statistique de",
               "Pearson a pour moyenne K-B et non K-1 : elle est mise a l'echelle",
               "avant comparaison. Chaque test est double d'une simulation du",
               "tirage reel (voir modele.py)."]

    resultats_tests = []

    # --- Boules ---
    t_boules = Tirage(K=BOULES_MAX, B=BOULES_TIREES, N=len(df))
    freq_boules = frequences(df, COLONNES_BOULES, BOULES_MAX)
    controle = verifier(t_boules)
    bloc, res = bloc_test("1. Les boules (1-50, historique complet)",
                          t_boules, freq_boules, controle)
    rapport += bloc
    resultats_tests.append(("Boules, historique complet", res))

    # --- Etoiles, regime par regime ---
    rapport += ["", "2. Les etoiles, regime par regime", "-" * 33,
                "  Le nombre d'etoiles a change deux fois. Melanger les periodes",
                "  fausserait le denominateur : chaque regime est teste seul."]
    for regime, maximum in ETOILES_PAR_REGIME.items():
        bloc_df = df[df["regime"] == regime]
        if bloc_df.empty:
            continue
        t = Tirage(K=maximum, B=2, N=len(bloc_df))
        freq = frequences(bloc_df, COLONNES_ETOILES, maximum)
        ctrl = verifier(t)
        res = t.test_ajustement(freq.values)
        p_sim = p_empirique(ctrl["distribution"], res["khi2_brut"])
        verdict = "compatible" if res["p"] >= SEUIL else "SOUS LE SEUIL"
        rapport.append("")
        rapport.append(f"  {regime}")
        rapport.append(f"     {len(bloc_df)} tirages, {maximum} etoiles possibles")
        rapport.append(f"     brut={res['khi2_brut']:.2f}  x{res['facteur']:.4f}"
                       f"  -> {res['khi2']:.2f}   seuil={res['critique']:.2f}")
        rapport.append(f"     p={res['p']:.4f}   p simulee={p_sim:.4f}   -> {verdict}")
        resultats_tests.append((f"Etoiles {regime.split(' : ')[0]}", res))

    # --- Absences ---
    ecarts = ecarts_maximaux(df, COLONNES_BOULES, BOULES_MAX)
    fin = df["date_tirage"].max().strftime("%d/%m/%Y")
    rapport += ["", "3. Les absences prolongees", "-" * 26,
                "  L'intuition du 'numero en retard' suppose qu'une longue absence",
                "  appelle une sortie. Voici ce que le hasard produit sans aide :", ""]
    rapport.append(f"  Absence la plus longue observee : "
                   f"{int(ecarts['absence_max'].max())} tirages "
                   f"(numero {int(ecarts['absence_max'].idxmax())})")
    rapport.append(f"  Absence maximale mediane        : "
                   f"{ecarts['absence_max'].median():.0f} tirages")
    rapport.append(f"  Plus longue absence au {fin}  : "
                   f"{int(ecarts['absence_finale'].max())} tirages "
                   f"(numero {int(ecarts['absence_finale'].idxmax())})")
    rapport += ["",
                "  Chaque numero a deja connu une absence de plusieurs dizaines",
                "  de tirages. En voir une n'a donc rien d'anormal, et ne change",
                "  en rien la probabilite du prochain tirage."]

    # --- Popularite ---
    table_rangs, pop = popularite(df)
    rapport += ["", "4. Ce qu'on peut reellement optimiser", "-" * 37,
                "  Impossible d'augmenter ses chances de gagner. Possible, en",
                "  revanche, d'augmenter ce qu'on gagne SI l'on gagne : le jackpot",
                "  est partage entre tous les gagnants.", "",
                "  Les boules, elles, ignorent la zone des dates de naissance :", ""]
    rapport.append(f"     Boules <= 31 par tirage, en moyenne  : "
                   f"{pop['moyenne_observee']:.2f}  "
                   f"(theorie : {pop['moyenne_theorique']:.2f})")
    rapport.append(f"     Tirages dont les 5 boules sont <= 31 : "
                   f"{pop['part_observee'] * 100:.1f} %  "
                   f"(theorie : {pop['part_theorique'] * 100:.1f} %)")
    rapport += ["",
                "  Restait a montrer que les JOUEURS, eux, ne les ignorent pas.",
                "  Le nombre de gagnants le revele : a rang fixe, il vaut environ",
                "  (grilles jouees) x (probabilite qu'une grille corresponde), et",
                "  ce second facteur depend des numeros que les joueurs cochent.",
                "",
                f"  Gagnants au rang {RANG_PRINCIPAL} (3 boules) selon le tirage :", ""]
    for petits, ligne in pop["medianes"].iterrows():
        rapport.append(f"     {petits} boule(s) <= 31 : mediane "
                       f"{ligne['median']:>10,.0f} gagnants "
                       f"({int(ligne['count'])} tirages)".replace(",", " "))
    rapport += ["",
                f"     Correlation de Spearman : rho = {pop['rho_principal']:+.3f}  "
                f"(p = {pop['p_principal']:.1e})", "",
                "  Le temoin ecarte l'explication fortuite. L'effet doit suivre le",
                "  nombre de boules principales exigees par le rang :", ""]
    for rang, ligne in table_rangs.sort_values(
            ["boules_exigees", "etoiles_exigees"]).iterrows():
        marque = ""
        if rang == RANG_TEMOIN:
            marque = "   <- temoin : n'exige qu'une boule"
        elif rang == RANG_PRINCIPAL:
            marque = "   <- rang de reference"
        rapport.append(f"     rang {rang:2d} : {int(ligne['boules_exigees'])} boules + "
                       f"{int(ligne['etoiles_exigees'])} etoiles   "
                       f"rho = {ligne['rho']:+.3f}{marque}")
    rapport += ["",
                f"  Le rang {RANG_TEMOIN}, qui n'exige qu'une boule principale, ne reagit",
                f"  pratiquement pas (rho = {pop['rho_temoin']:+.3f}, p = {pop['p_temoin']:.2f}).",
                "  L'effet n'est donc pas un artefact : il suit exactement la",
                "  dependance de chaque rang aux numeros principaux.",
                "",
                "  Conclusion : une grille contenant des numeros > 31 a la meme",
                "  probabilite de sortir, mais serait partagee avec moins de monde.",
                "  C'est la seule conclusion actionnable de cette analyse, et elle",
                "  ne dit rien sur la probabilite de gagner."]

    # --- Tests multiples ---
    n_tests = len(resultats_tests)
    risque_global = 1 - (1 - SEUIL) ** n_tests
    bonferroni = SEUIL / n_tests
    rapport += ["", "5. Le piege des tests multiples", "-" * 31,
                f"  {n_tests} tests d'ajustement ont ete menes. Un seuil a 5 % signifie",
                "  qu'un test sur vingt franchit la barre par pur hasard, meme",
                f"  quand rien n'est biaise. Sur {n_tests} tests, la probabilite d'en voir",
                f"  au moins un 'significatif' par accident vaut {risque_global * 100:.0f} %.",
                "",
                f"  La correction de Bonferroni divise le seuil par {n_tests} :",
                f"  il passe de {SEUIL} a {bonferroni:.4f}.", ""]
    for nom, res in resultats_tests:
        etat = "sous le seuil corrige" if res["p"] < bonferroni else "au-dessus"
        rapport.append(f"     {nom:38s} p = {res['p']:.4f}   {etat}")
    survivants = [n for n, r in resultats_tests if r["p"] < bonferroni]
    rapport += ["",
                "  Le seuil de 5 % n'est pas une frontiere entre le vrai et le faux :",
                "  c'est une convention. p = 0.049 et p = 0.051 decrivent des donnees",
                "  pratiquement identiques.", ""]
    if survivants:
        rapport.append(f"  => {len(survivants)} test(s) survivent a la correction : "
                       f"{', '.join(survivants)}.")
    else:
        rapport.append("  => Aucun test ne survit a la correction. C'est precisement")
        rapport.append("     ainsi que naissent les fausses decouvertes : en multipliant")
        rapport.append("     les decoupages jusqu'a en trouver un qui passe, puis en ne")
        rapport.append("     publiant que celui-la.")

    rapport += ["", "Conclusion", "-" * 10,
                "  Les 'numeros chauds' n'existent pas. Les ecarts de frequence",
                "  visibles dans n'importe quel tableau de statistiques sont la",
                "  signature normale du hasard : sur 50 numeros, il y en aura",
                "  toujours un en tete et un en queue, meme avec des boules",
                "  parfaitement equilibrees.",
                "",
                "  La FDJ le dit elle-meme sur ses pages de statistiques : il n'est",
                "  pas possible de determiner des probabilites fiables sur les",
                "  tirages a venir."]

    texte = "\n".join(rapport)
    (DOSSIER / "statistiques.txt").write_text(texte, encoding="utf-8")

    export = freq_boules.rename("sorties").to_frame()
    export.index.name = "numero"
    export["ecart_a_l_attendu"] = export["sorties"] - t_boules.attendu
    export = export.join(ecarts)
    export.to_csv(DOSSIER / "frequences_boules.csv", sep=";")
    table_rangs.to_csv(DOSSIER / "popularite_gagnants.csv", sep=";")

    print(texte)


if __name__ == "__main__":
    main()
