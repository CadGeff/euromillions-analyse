"""
Analyse statistique des tirages EuroMillions normalises.

Quatre questions, dans l'ordre ou elles meritent d'etre posees :

  1. Quels numeros sortent le plus ? (la question que tout le monde pose)
  2. Ces ecarts depassent-ils ce que le hasard produit seul ? (tests)
  3. Combien de temps un numero peut-il rester absent sans que ce soit anormal ?
  4. Puisqu'on ne peut pas gagner plus souvent, peut-on gagner davantage ?

Les trois premieres ne trouvent aucun signal qui resiste a la correction pour
tests multiples. La quatrieme est le seul angle ou la statistique a quelque
chose d'utile a dire, et il ne porte pas sur la probabilite de gagner mais sur
le montant du gain.

Toutes les phrases de conclusion sont choisies d'apres les resultats : si les
donnees changent, le texte change avec elles.

Le modele probabiliste et sa verification par simulation vivent dans modele.py,
les regles du jeu dans regles.py.

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

from modele import SEUIL, Tirage, p_empirique, simuler_absences, verifier
from regles import (
    BOULES,
    BOULES_TIREES,
    COMBINAISONS,
    ETOILES_TIREES,
    REGIMES,
    colonne_gagnants,
    colonne_rapport,
    rang_actuel,
)

RACINE = Path(__file__).resolve().parents[1]
DOSSIER = RACINE / "data" / "processed"

COLONNES_BOULES = [f"boule_{i}" for i in range(1, 6)]
COLONNES_ETOILES = [f"etoile_{i}" for i in range(1, 3)]
SEUIL_DATES = 31  # au-dela, un numero ne peut pas etre une date de naissance

# Combinaison de reference : 3 boules et aucune etoile. Elle depend des numeros
# principaux sans que la popularite des etoiles vienne brouiller la mesure, et
# elle compte des dizaines de milliers de gagnants par tirage.
PRINCIPALE = (3, 0)
# Temoin : la seule combinaison qui n'exige qu'une boule principale.
TEMOIN = (1, 2)


# --------------------------------------------------------------------------


def charger() -> pd.DataFrame:
    """Charge les tirages normalises, tries par date.

    Le tri est reimpose ici plutot que suppose. Les calculs d'absence lisent la
    serie dans l'ordre des lignes : un fichier ecrit a l'envers donnerait des
    resultats faux sans lever la moindre erreur.
    """
    df = pd.read_csv(DOSSIER / "euromillions_tirages.csv", sep=";",
                     parse_dates=["date_tirage"])
    return df.sort_values("date_tirage", kind="stable").reset_index(drop=True)


def libelle(combinaison: tuple[int, int]) -> str:
    return f"{combinaison[0]}+{combinaison[1]}"


def frequences(df: pd.DataFrame, colonnes: list[str], maximum: int) -> pd.Series:
    """Compte les sorties de chaque numero, en incluant les zeros."""
    tirees = df[colonnes].stack().dropna().astype(int)
    return tirees.value_counts().reindex(range(1, maximum + 1), fill_value=0).sort_index()


def extremes(effectifs: pd.Series, n: int = 3, en_tete: bool = True) -> pd.Series:
    """Les n premiers (ou derniers), ex aequo compris : couper au milieu d'une
    egalite designerait arbitrairement un numero plutot qu'un autre."""
    ordonnes = effectifs.sort_values(ascending=not en_tete, kind="stable")
    limite = ordonnes.iloc[min(n, len(ordonnes)) - 1]
    garde = ordonnes >= limite if en_tete else ordonnes <= limite
    return ordonnes[garde]


def ecarts_maximaux(df: pd.DataFrame, colonnes: list[str], maximum: int) -> pd.DataFrame:
    """Plus longue absence observee pour chaque numero, en nombre de tirages.

    Les series de debut et de fin d'historique comptent (la simulation de
    reference les compte de la meme facon). `absence_finale` decrit l'etat au
    dernier tirage du jeu de donnees, et non la situation du jour.
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


# --------------------------------------------------------------------------
# Les boules : ajustement global et numero le plus atypique
# --------------------------------------------------------------------------


def etudier_boules(df: pd.DataFrame) -> dict:
    t = Tirage(K=BOULES, B=BOULES_TIREES, N=len(df))
    freq = frequences(df, COLONNES_BOULES, BOULES)
    controle = verifier(t)
    test = t.test_ajustement(freq.values)
    test["p_simulee"] = p_empirique(controle["distribution"], test["khi2_brut"])

    # Le numero le plus atypique, jauge contre la loi du plus atypique des 50
    # dans un historique equilibre : c'est la bonne question, car il y a
    # toujours un numero en tete et un en queue.
    p_indiv = pd.Series(t.p_individuelle(freq.values), index=freq.index)
    atypique = int(p_indiv.idxmin())
    extreme = {
        "numero": atypique,
        "sorties": int(freq[atypique]),
        "sens": "sous" if freq[atypique] < t.attendu else "sur",
        "p_individuelle": float(p_indiv[atypique]),
        "p_bonferroni": float(min(1.0, p_indiv[atypique] * BOULES)),
        "p": p_empirique(controle["p_minimales"], float(p_indiv[atypique]), sens="bas"),
    }

    bande = t.bande(SEUIL)
    bande_corrigee = t.bande(SEUIL, comparaisons=BOULES)
    hors = [int(n) for n, c in freq.items() if not bande[0] <= c <= bande[1]]
    hors_corrigee = [int(n) for n, c in freq.items()
                     if not bande_corrigee[0] <= c <= bande_corrigee[1]]
    return {
        "tirage": t, "freq": freq, "controle": controle, "test": test,
        "extreme": extreme,
        "bande": bande, "bande_corrigee": bande_corrigee,
        "hors_bande": hors, "hors_bande_corrigee": hors_corrigee,
        "attendus_hors_bande": BOULES * SEUIL,
        # Jusqu'ou le hasard pousse le numero de tete et celui de queue.
        "p_maximum": p_empirique(controle["maximums"], int(freq.max()), sens="haut"),
        "p_minimum": p_empirique(controle["minimums"], int(freq.min()), sens="bas"),
        "percentile_khi2": float(stats.chi2.cdf(test["khi2"], test["ddl"])),
    }


def etudier_etoiles(df: pd.DataFrame) -> list[dict]:
    resultats = []
    for regime in REGIMES:
        bloc = df[df["regime"] == regime.nom]
        if bloc.empty:
            continue
        t = Tirage(K=regime.etoiles, B=ETOILES_TIREES, N=len(bloc))
        freq = frequences(bloc, COLONNES_ETOILES, regime.etoiles)
        controle = verifier(t)
        test = t.test_ajustement(freq.values)
        test["p_simulee"] = p_empirique(controle["distribution"], test["khi2_brut"])
        resultats.append({"regime": regime, "tirage": t, "freq": freq, "test": test})
    return resultats


# --------------------------------------------------------------------------
# Les absences, avec leur reference simulee
# --------------------------------------------------------------------------


def etudier_absences(df: pd.DataFrame) -> dict:
    ecarts = ecarts_maximaux(df, COLONNES_BOULES, BOULES)
    reference = simuler_absences(Tirage(K=BOULES, B=BOULES_TIREES, N=len(df)))
    record = int(ecarts["absence_max"].max())
    mediane = float(ecarts["absence_max"].median())
    return {
        "ecarts": ecarts,
        "record": record,
        "numero_record": int(ecarts["absence_max"].idxmax()),
        "mediane": mediane,
        "finale": int(ecarts["absence_finale"].max()),
        "numero_finale": int(ecarts["absence_finale"].idxmax()),
        "record_simule_mediane": float(np.median(reference["record"])),
        "record_simule_ic": [float(v) for v in np.percentile(reference["record"], [2.5, 97.5])],
        "p_record": p_empirique(reference["record"], record, sens="haut"),
        "mediane_simulee": float(np.median(reference["mediane"])),
        "mediane_simulee_ic": [float(v) for v in np.percentile(reference["mediane"], [2.5, 97.5])],
        "repetitions": len(reference["record"]),
    }


# --------------------------------------------------------------------------
# La popularite des numeros aupres des joueurs
# --------------------------------------------------------------------------


def effet_par_petit(valeurs: pd.Series, petits: pd.Series, regimes: pd.Series) -> dict:
    """Variation relative de `valeurs` pour chaque boule <= 31 de plus dans le
    tirage : pente de log(valeurs) sur le nombre de petites boules.

    Les effets fixes par regime (centrage dans chaque regime) absorbent les
    changements de niveau dus aux regles ; le volume de grilles, qui ne depend
    pas des numeros tires, reste un bruit. L'intervalle de confiance utilise
    une erreur-type robuste (HC0), sans supposer une variance constante.

    Le logarithme exige des valeurs positives : ecarter les tirages a zero
    reviendrait a ne garder, au jackpot, que les tirages gagnes - precisement
    les plus joues. Une combinaison n'est donc mesuree que si les tirages a
    zero y sont negligeables (moins de 1 %).
    """
    connues = valeurs.notna()
    part_zero = float((valeurs[connues] == 0).mean())
    garde = connues & (valeurs > 0)
    cadre = pd.DataFrame({"y": np.log(valeurs[garde].astype(float)),
                          "x": petits[garde].astype(float), "g": regimes[garde]})
    cadre["y"] -= cadre.groupby("g")["y"].transform("mean")
    cadre["x"] -= cadre.groupby("g")["x"].transform("mean")
    sxx = float((cadre["x"] ** 2).sum())
    pente = float((cadre["x"] * cadre["y"]).sum() / sxx)
    residus = cadre["y"] - pente * cadre["x"]
    erreur = float(np.sqrt((cadre["x"] ** 2 * residus ** 2).sum()) / sxx)
    return {
        "effet": float(np.expm1(pente)),
        "ic": [float(np.expm1(pente - 1.96 * erreur)), float(np.expm1(pente + 1.96 * erreur))],
        "tirages": int(connues.sum()),
        "part_zero": part_zero,
        "mesurable": part_zero < 0.01,
    }


def popularite(df: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """Mesure les habitudes des JOUEURS a partir des gagnants et des gains.

    A une combinaison donnee, le nombre de gagnants vaut approximativement
    (grilles jouees) x (probabilite qu'une grille corresponde). Le second
    facteur depend des numeros que les joueurs cochent : si les joueurs
    privilegient 1-31, un tirage riche en petits numeros produit plus de
    gagnants. Et comme chaque rang partage une cagnotte proportionnelle aux
    mises, le gain par grille baisse d'autant : c'est lui, et non le nombre
    de gagnants, qui interesse le joueur.

    Le volume de grilles varie fortement (taille du jackpot, jour, epoque),
    mais il ne depend pas des numeros qui vont sortir : c'est du bruit, qui
    affaiblit les mesures, pas un facteur de confusion qui les creerait.

    La demonstration repose sur une relation dose-effet : plus une combinaison
    exige de boules principales, plus elle doit reagir. On la mesure par la
    TAILLE de l'effet (variation par petite boule supplementaire), et non par
    une correlation de rang : rho melange taille de l'effet et bruit, et le
    bruit depend du nombre de gagnants et des etoiles exigees.
    """
    petits = (df[COLONNES_BOULES] <= SEUIL_DATES).sum(axis=1)

    lignes = []
    for combinaison in COMBINAISONS:
        gagnants = pd.to_numeric(df[colonne_gagnants(combinaison)], errors="coerce")
        gains = pd.to_numeric(df[colonne_rapport(combinaison)], errors="coerce")
        if gagnants.notna().sum() < 200:
            continue
        effet_g = effet_par_petit(gagnants, petits, df["regime"])
        effet_r = effet_par_petit(gains, petits, df["regime"])
        valides = gagnants.notna()
        rho, valeur_p = stats.spearmanr(petits[valides], gagnants[valides])
        lignes.append({
            "combinaison": libelle(combinaison),
            "boules_exigees": combinaison[0],
            "etoiles_exigees": combinaison[1],
            "rang_actuel": rang_actuel(combinaison),
            "tirages": effet_g["tirages"],
            "gagnants_medians": float(gagnants[valides].median()),
            "part_sans_gagnant": effet_g["part_zero"],
            "mesurable": effet_g["mesurable"] and effet_r["mesurable"],
            "effet_gagnants": effet_g["effet"],
            "effet_gagnants_bas": effet_g["ic"][0],
            "effet_gagnants_haut": effet_g["ic"][1],
            "effet_gain": effet_r["effet"],
            "effet_gain_bas": effet_r["ic"][0],
            "effet_gain_haut": effet_r["ic"][1],
            "rho_gagnants": float(rho),
            "p_gagnants": float(valeur_p),
        })
    table = (pd.DataFrame(lignes)
             .sort_values(["etoiles_exigees", "boules_exigees"], kind="stable")
             .set_index("combinaison"))
    # Une pente calculee sur les seuls tirages gagnes serait biaisee : on ne
    # la publie pas.
    colonnes_effet = [c for c in table.columns if c.startswith("effet_")]
    table.loc[~table["mesurable"], colonnes_effet] = np.nan

    # Dose-effet : a etoiles egales, l'effet sur les gagnants doit croitre, et
    # celui sur le gain decroitre, avec le nombre de boules exigees.
    familles = {}
    for etoiles, groupe in table[table["mesurable"]].groupby("etoiles_exigees"):
        suite = groupe.sort_values("boules_exigees")
        familles[int(etoiles)] = {
            "combinaisons": list(suite.index),
            "effet_gagnants": [float(v) for v in suite["effet_gagnants"]],
            "effet_gain": [float(v) for v in suite["effet_gain"]],
            "coherente": bool(suite["effet_gagnants"].is_monotonic_increasing
                              and suite["effet_gain"].is_monotonic_decreasing),
        }

    principale = libelle(PRINCIPALE)
    gagnants = pd.to_numeric(df[colonne_gagnants(PRINCIPALE)], errors="coerce")
    gains = pd.to_numeric(df[colonne_rapport(PRINCIPALE)], errors="coerce")
    medianes = pd.DataFrame({
        "gagnants": gagnants.groupby(petits).median(),
        "gain": gains.groupby(petits).median(),
        "tirages": petits.value_counts().sort_index(),
    })
    rho_gain, p_gain = stats.spearmanr(petits[gains.notna()], gains[gains.notna()])

    # Tailles d'effet rapportees a un tirage typique plutot qu'au point le plus
    # bas de la courbe : 3 boules <= 31 est a la fois le cas le plus frequent
    # et le plus proche de la moyenne theorique (3,1).
    typique = int(medianes["tirages"].idxmax())
    haut = int(medianes.index.max())
    temoin = table.loc[libelle(TEMOIN)]
    detail = {
        "medianes": medianes,
        "principale": principale,
        "temoin": libelle(TEMOIN),
        "rho_principal": float(table.loc[principale, "rho_gagnants"]),
        "p_principal": float(table.loc[principale, "p_gagnants"]),
        "rho_gain": float(rho_gain),
        "p_gain": float(p_gain),
        "effet_principal_gagnants": float(table.loc[principale, "effet_gagnants"]),
        "effet_principal_gain": float(table.loc[principale, "effet_gain"]),
        "effet_temoin": float(temoin["effet_gagnants"]),
        "ic_temoin": [float(temoin["effet_gagnants_bas"]), float(temoin["effet_gagnants_haut"])],
        "temoin_nul": bool(temoin["effet_gagnants_bas"] <= 0 <= temoin["effet_gagnants_haut"]),
        "familles": familles,
        "dose_effet": bool(familles) and all(f["coherente"] for f in familles.values()),
        "typique": typique,
        "haut": haut,
        "gagnants_vs_typique": float(medianes.loc[haut, "gagnants"] / medianes.loc[typique, "gagnants"] - 1),
        "gain_vs_typique": float(medianes.loc[haut, "gain"] / medianes.loc[typique, "gain"] - 1),
        "moyenne_observee": float(petits.mean()),
        "moyenne_theorique": BOULES_TIREES * SEUIL_DATES / BOULES,
        "part_observee": float((petits == 5).mean()),
        "part_theorique": comb(SEUIL_DATES, 5) / comb(BOULES, 5),
    }
    return table, detail


# --------------------------------------------------------------------------
# Tests multiples
# --------------------------------------------------------------------------


def famille_de_tests(boules: dict, etoiles: list[dict]) -> dict:
    """Tous les tests menes sur les tirages, et leur correction commune.

    Les tests sont independants entre eux sauf les deux qui portent sur les
    boules (ajustement global et numero extreme). Bonferroni reste valable
    quelle que soit la dependance ; le risque global, lui, est calcule sous
    independance et reste donc un ordre de grandeur.
    """
    # `nom` sert au rapport texte (ASCII), `libelle` et `detail` a la page.
    milliers = lambda n: f"{n:,}".replace(",", "\u202f")  # noqa: E731
    tests = [
        {"nom": "Boules 1-50, ajustement global",
         "libelle": "Boules 1-50, ajustement global",
         "detail": f"{milliers(boules['tirage'].N)} tirages, 50 numéros",
         "p": boules["test"]["p"]},
        {"nom": "Boules 1-50, numero le plus atypique",
         "libelle": "Boules 1-50, numéro le plus atypique",
         "detail": f"le {boules['extreme']['numero']}, parmi 50 numéros",
         "p": boules["extreme"]["p"]},
    ]
    for e in etoiles:
        periode = e["regime"].nom.split(" : ")[0]
        tests.append({"nom": f"Etoiles {periode}",
                      "libelle": f"Étoiles {periode}",
                      "detail": f"{milliers(e['tirage'].N)} tirages, {e['regime'].etoiles} étoiles",
                      "p": e["test"]["p"]})
    n = len(tests)
    return {
        "tests": tests,
        "nombre": n,
        "risque_global": 1 - (1 - SEUIL) ** n,
        "bonferroni": SEUIL / n,
        "sous_seuil": [t["nom"] for t in tests if t["p"] < SEUIL],
        "survivants": [t["nom"] for t in tests if t["p"] < SEUIL / n],
    }


# --------------------------------------------------------------------------
# Rapport texte
# --------------------------------------------------------------------------


def liste_numeros(effectifs: pd.Series) -> str:
    return ", ".join(f"{n} ({c}x)" for n, c in effectifs.items())


def rediger(df: pd.DataFrame, boules: dict, etoiles: list[dict], absences: dict,
            table: pd.DataFrame, pop: dict, famille: dict) -> str:
    t, test, ext = boules["tirage"], boules["test"], boules["extreme"]
    fin = df["date_tirage"].max().strftime("%d/%m/%Y")
    r = ["ANALYSE STATISTIQUE - EUROMILLIONS", "=" * 34, "",
         f"Periode : {df['date_tirage'].min():%d/%m/%Y} -> {fin}   ({len(df)} tirages)",
         "",
         "Modele : a chaque tirage, un numero sort ou ne sort pas, avec une",
         "probabilite B/K. Sur N tirages, ses sorties suivent B(N, B/K).",
         "Les numeros d'un meme tirage etant distincts, la statistique de",
         "Pearson suit (K-B)/(K-1) x chi2(K-1) : elle est mise a l'echelle",
         "avant comparaison. Chaque test est double d'une simulation du",
         "tirage reel (voir modele.py)."]

    titre = "1. Les boules (1-50, historique complet)"
    r += ["", titre, "-" * len(titre),
          f"  Tirages                  : {t.N}",
          f"  Sorties attendues/numero : {t.attendu:.1f}",
          f"  Ecart-type attendu       : {t.ecart_type:.2f}  "
          f"(simule : {boules['controle']['ecart_type_simule']:.2f})", "",
          "  Les plus sortis   : " + liste_numeros(extremes(boules["freq"], 3, True)),
          "  Les moins sortis  : " + liste_numeros(extremes(boules["freq"], 3, False)),
          f"  Dans un historique equilibre, le numero de tete atteint au moins "
          f"{int(boules['freq'].max())} sorties",
          f"  dans {boules['p_maximum'] * 100:.0f} % des cas, et celui de queue descend a "
          f"{int(boules['freq'].min())} ou moins dans {boules['p_minimum'] * 100:.1f} % des cas.",
          "",
          "  a) Ajustement global (khi-deux)",
          f"     Statistique brute : {test['khi2_brut']:.2f}",
          f"     Mise a l'echelle  : x {test['facteur']:.4f}  -> {test['khi2']:.2f}",
          f"     Valeur moyenne sous le hasard : {test['ddl']} ; observee au "
          f"{boules['percentile_khi2'] * 100:.0f}e centile",
          f"     Seuil critique 5% : {test['critique']:.2f}  ({test['ddl']} ddl)",
          f"     p-value           : {test['p']:.4f}   (simulee : {test['p_simulee']:.4f})"]
    r.append("     => Compatible avec l'equiprobabilite." if test["p"] >= SEUIL
             else "     => Sous le seuil de 5 % : voir la correction pour tests multiples.")

    r += ["",
          "  b) Numero par numero (loi binomiale exacte)",
          f"     Bande a 95 %                    : {boules['bande'][0]} a {boules['bande'][1]} sorties",
          f"     Bande corrigee (50 comparaisons) : {boules['bande_corrigee'][0]} a "
          f"{boules['bande_corrigee'][1]} sorties",
          f"     Hors bande a 95 %      : {len(boules['hors_bande'])} numero(s) "
          f"{boules['hors_bande']}  (attendu par hasard : {boules['attendus_hors_bande']:.1f})",
          f"     Hors bande corrigee    : {len(boules['hors_bande_corrigee'])} numero(s) "
          f"{boules['hors_bande_corrigee']}",
          f"     Le plus atypique : le {ext['numero']}, {ext['sorties']} sorties "
          f"({ext['sens']}-represente)",
          f"        p individuelle      : {ext['p_individuelle']:.5f}",
          f"        p corrigee (Bonferroni, x50) : {ext['p_bonferroni']:.4f}",
          f"        p corrigee (simulation)      : {ext['p']:.4f}   <- retenue",
          "     La simulation mesure directement la question posee : dans un",
          "     historique equilibre, a quelle frequence le plus atypique des",
          "     50 numeros l'est-il au moins autant ? Bonferroni en donne une",
          "     borne, un peu plus prudente."]

    titre = "2. Les etoiles, regime par regime"
    r += ["", titre, "-" * len(titre),
          "  Le nombre d'etoiles a change deux fois. Melanger les periodes",
          "  fausserait le denominateur : chaque regime est teste seul."]
    for e in etoiles:
        res = e["test"]
        verdict = "compatible" if res["p"] >= SEUIL else "SOUS LE SEUIL DE 5 %"
        r += ["", f"  {e['regime'].nom}",
              f"     {e['tirage'].N} tirages, {e['regime'].etoiles} etoiles possibles",
              f"     brut={res['khi2_brut']:.2f}  x{res['facteur']:.4f}"
              f"  -> {res['khi2']:.2f}   seuil={res['critique']:.2f}",
              f"     p={res['p']:.4f}   p simulee={res['p_simulee']:.4f}   -> {verdict}"]

    a = absences
    titre = "3. Les absences prolongees"
    r += ["", titre, "-" * len(titre),
          "  L'intuition du 'numero en retard' suppose qu'une longue absence",
          "  appelle une sortie. Pour juger une absence, il faut savoir ce que",
          f"  le hasard produit : {a['repetitions']} historiques equilibres simules.", "",
          "                                   observe    simulation (IC 95 %)",
          f"  Record, tous numeros confondus :  {a['record']:5d}      "
          f"{a['record_simule_mediane']:.0f}  ({a['record_simule_ic'][0]:.0f} a "
          f"{a['record_simule_ic'][1]:.0f})",
          f"  Record median par numero       :  {a['mediane']:5.0f}      "
          f"{a['mediane_simulee']:.0f}  ({a['mediane_simulee_ic'][0]:.0f} a "
          f"{a['mediane_simulee_ic'][1]:.0f})", "",
          f"  Le record observe ({a['record']} tirages, numero {a['numero_record']}) est atteint "
          f"ou depasse",
          f"  dans {a['p_record'] * 100:.0f} % des historiques equilibres.",
          f"  Plus longue absence en cours au {fin} : {a['finale']} tirages "
          f"(numero {a['numero_finale']}).", "",
          "  Les absences observees sont celles d'un tirage equilibre. En voir",
          "  une longue ne change rien a la probabilite du prochain tirage."]

    titre = "4. Ce qu'on peut reellement optimiser"
    r += ["", titre, "-" * len(titre),
          "  Impossible d'augmenter ses chances de gagner. Possible, en",
          "  revanche, d'augmenter ce qu'on gagne SI l'on gagne : chaque rang",
          "  partage une cagnotte entre ses gagnants.", "",
          "  Les boules, elles, ignorent la zone des dates de naissance :", "",
          f"     Boules <= 31 par tirage, en moyenne  : {pop['moyenne_observee']:.2f}  "
          f"(theorie : {pop['moyenne_theorique']:.2f})",
          f"     Tirages dont les 5 boules sont <= 31 : {pop['part_observee'] * 100:.1f} %  "
          f"(theorie : {pop['part_theorique'] * 100:.1f} %)", "",
          "  Restait a montrer que les JOUEURS, eux, ne les ignorent pas, et",
          "  que cela se paie. A combinaison fixee, le nombre de gagnants vaut",
          "  environ (grilles jouees) x (probabilite qu'une grille corresponde),",
          "  et ce second facteur depend des numeros que les joueurs cochent.",
          "  Le gain par grille, lui, vaut (cagnotte du rang) / (gagnants).", "",
          f"  Combinaison {pop['principale']} (3 boules, aucune etoile), selon le tirage :", "",
          "     boules <= 31   gagnants (mediane)   gain par grille (mediane)   tirages"]
    for n_petits, ligne in pop["medianes"].iterrows():
        r.append(f"          {n_petits}         {ligne['gagnants']:>10,.0f}".replace(",", " ")
                 + f"               {ligne['gain']:6.2f} EUR".replace(".", ",")
                 + f"            {int(ligne['tirages']):4d}")
    r += ["",
          f"     Spearman, gagnants : rho = {pop['rho_principal']:+.3f}  (p = {pop['p_principal']:.1e})",
          f"     Spearman, gain     : rho = {pop['rho_gain']:+.3f}  (p = {pop['p_gain']:.1e})",
          f"     5 boules <= 31 contre {pop['typique']} (le cas typique) : "
          f"{pop['gagnants_vs_typique'] * 100:+.0f} % de gagnants, "
          f"{pop['gain_vs_typique'] * 100:+.0f} % de gain.",
          "     La ligne a 0 boule repose sur trop peu de tirages pour etre lue seule.", "",
          "  Relation dose-effet. Si l'effet vient des numeros coches, sa taille",
          "  doit croitre avec le nombre de boules principales qu'exige la",
          "  combinaison. Mesure : variation par boule <= 31 supplementaire dans",
          "  le tirage (pente log-lineaire, effets fixes par regime, IC 95 %).", "",
          "     combinaison   gagnants                   gain par grille"]
    for combinaison, ligne in table.iterrows():
        if not ligne["mesurable"]:
            r.append(f"     {combinaison}  (rang {int(ligne['rang_actuel']):2d})   non mesurable : "
                     f"{ligne['part_sans_gagnant'] * 100:.0f} % de tirages sans gagnant")
            continue
        marque = "   <- temoin" if combinaison == pop["temoin"] else ""
        marque = "   <- reference" if combinaison == pop["principale"] else marque
        r.append(f"     {combinaison}  (rang {int(ligne['rang_actuel']):2d})   "
                 f"{ligne['effet_gagnants'] * 100:+5.1f} % "
                 f"[{ligne['effet_gagnants_bas'] * 100:+5.1f} ; {ligne['effet_gagnants_haut'] * 100:+5.1f}]"
                 f"   {ligne['effet_gain'] * 100:+5.1f} % "
                 f"[{ligne['effet_gain_bas'] * 100:+5.1f} ; {ligne['effet_gain_haut'] * 100:+5.1f}]"
                 f"{marque}")
    r.append("")
    for etoiles_exigees, f in pop["familles"].items():
        suite = "  <  ".join(f"{c} {v * 100:+.1f} %"
                             for c, v in zip(f["combinaisons"], f["effet_gagnants"]))
        r.append(f"     {etoiles_exigees} etoile(s) : {suite}   "
                 f"-> {'coherent' if f['coherente'] else 'NON COHERENT'}")
    ic = pop["ic_temoin"]
    r += ["",
          f"  Le temoin {pop['temoin']}, seule combinaison a n'exiger qu'une boule, "
          f"varie de {pop['effet_temoin'] * 100:+.1f} %",
          f"  par petite boule [{ic[0] * 100:+.1f} ; {ic[1] * 100:+.1f}] : "
          + ("un effet indiscernable de zero." if pop["temoin_nul"]
             else "un effet faible mais non nul."),
          ("  A etoiles egales, l'effet croit avec les boules exigees, pour les"
           if pop["dose_effet"] else
           "  ATTENTION : la relation dose-effet n'est pas verifiee partout,"),
          ("  gagnants comme pour le gain : il vient des numeros que cochent"
           if pop["dose_effet"] else
           "  l'interpretation doit etre revue."),
          ("  les joueurs. Le nombre d'etoiles exigees, lui, n'y change presque rien."
           if pop["dose_effet"] else ""),
          "",
          "  Conclusion : une grille contenant des numeros > 31 a la meme",
          "  probabilite de gagner, mais quand elle gagne, elle partage avec",
          f"  moins de monde. A la combinaison {pop['principale']}, chaque boule <= 31 du tirage",
          f"  change le gain de {pop['effet_principal_gain'] * 100:+.1f} %. Le jackpot, trop rarement gagne,",
          "  echappe a cette mesure : l'effet y est extrapole, pas observe."]

    m = famille
    titre = "5. Le piege des tests multiples"
    r += ["", titre, "-" * len(titre),
          f"  {m['nombre']} tests ont ete menes sur les tirages. Un seuil a 5 % signifie",
          "  qu'un test sur vingt franchit la barre par pur hasard, meme quand",
          f"  rien n'est biaise. Sur {m['nombre']} tests, la probabilite d'en voir au moins",
          f"  un 'significatif' par accident vaut environ {m['risque_global'] * 100:.0f} %.", "",
          f"  La correction de Bonferroni divise le seuil par {m['nombre']} :",
          f"  il passe de {SEUIL} a {m['bonferroni']:.4f}.", ""]
    for essai in m["tests"]:
        etat = "sous le seuil corrige" if essai["p"] < m["bonferroni"] else (
            "sous 5 %, au-dessus du seuil corrige" if essai["p"] < SEUIL else "au-dessus")
        r.append(f"     {essai['nom']:40s} p = {essai['p']:.4f}   {etat}")
    r += ["",
          "  Le seuil de 5 % n'est pas une frontiere entre le vrai et le faux :",
          "  c'est une convention. p = 0.049 et p = 0.051 decrivent des donnees",
          "  pratiquement identiques.", ""]
    if m["survivants"]:
        r.append(f"  => {len(m['survivants'])} test(s) survivent a la correction : "
                 f"{', '.join(m['survivants'])}.")
    else:
        r += [f"  => {len(m['sous_seuil'])} test(s) sous 5 %, aucun ne survit a la correction.",
              "     C'est ainsi que naissent les fausses decouvertes : en multipliant",
              "     les decoupages jusqu'a en trouver un qui passe, puis en ne",
              "     publiant que celui-la."]

    chauds = boules["p_maximum"] >= SEUIL
    r += ["", "Conclusion", "-" * 10]
    if chauds and not m["survivants"]:
        r += ["  Les 'numeros chauds' n'existent pas. Le score du numero de tete",
              f"  ({int(boules['freq'].max())} sorties) est egale ou depasse dans "
              f"{boules['p_maximum'] * 100:.0f} % des historiques",
              "  equilibres, et aucun ecart ne resiste a la correction pour tests",
              "  multiples.",
              f"  Le plus atypique, le {ext['numero']}, est {ext['sens']}-represente "
              f"(p = {ext['p']:.3f}) :",
              f"  un tel extreme apparait dans environ un historique equilibre sur "
              f"{1 / ext['p']:.0f},",
              f"  et il ne franchit pas le seuil corrige ({m['bonferroni']:.2f})."]
    else:
        r += ["  Au moins un ecart resiste a l'analyse : voir les sections 1 et 5.",
              "  Ce resultat doit etre examine avant toute conclusion."]
    r += ["",
          "  La FDJ ecrit sur sa page de statistiques qu'il n'est pas possible",
          "  de determiner des probabilites fiables sur les tirages : entendu",
          "  comme « on ne peut pas prevoir le prochain tirage ». Les probabilites",
          "  elles-memes sont parfaitement connues ; ce projet les calcule.",
          "  https://www.fdj.fr/jeux-de-tirage/euromillions-my-million/statistiques"]
    return "\n".join(r) + "\n"


def analyser() -> dict:
    """Toute l'analyse, sous une forme reutilisable par la page web."""
    df = charger()
    boules = etudier_boules(df)
    etoiles = etudier_etoiles(df)
    absences = etudier_absences(df)
    table, pop = popularite(df)
    famille = famille_de_tests(boules, etoiles)
    return {"df": df, "boules": boules, "etoiles": etoiles, "absences": absences,
            "table": table, "pop": pop, "famille": famille}


def main() -> None:
    a = analyser()
    texte = rediger(a["df"], a["boules"], a["etoiles"], a["absences"],
                    a["table"], a["pop"], a["famille"])
    (DOSSIER / "statistiques.txt").write_text(texte, encoding="utf-8")

    boules = a["boules"]
    export = boules["freq"].rename("sorties").to_frame()
    export.index.name = "numero"
    export["ecart_a_l_attendu"] = (export["sorties"] - boules["tirage"].attendu).round(1)
    export["p_individuelle"] = boules["tirage"].p_individuelle(export["sorties"].values).round(6)
    export = export.join(a["absences"]["ecarts"])
    export.to_csv(DOSSIER / "frequences_boules.csv", sep=";")
    a["table"].to_csv(DOSSIER / "popularite_gagnants.csv", sep=";", float_format="%.6g")

    print(texte)


if __name__ == "__main__":
    main()
