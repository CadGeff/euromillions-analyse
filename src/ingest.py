"""
Ingestion et normalisation des archives EuroMillions de la FDJ.

Les archives publiees par la FDJ couvrent plus de vingt ans et leur format a
change plusieurs fois : encodage, format de date, libelle du jour, nom des
colonnes, apparition de nouvelles colonnes (Etoile+, numero de tirage dans le
cycle), et surtout sens des numeros de rang. Ce module ramene tout cela a un
schema unique et signale les anomalies plutot que de les masquer.

Sortie :
  - data/processed/euromillions_tirages.csv  (1 ligne par tirage)
  - data/processed/euromillions_boules.csv   (format long, 1 ligne par boule)
  - data/processed/rapport_qualite.txt       (anomalies detectees)
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from regles import (
    COMBINAISONS,
    REGIMES,
    colonne_gagnants,
    colonne_rapport,
    probabilite,
    regime_de,
    rangs_du_regime,
)

# --------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------

RACINE = Path(__file__).resolve().parents[1]
DOSSIER_BRUT = RACINE / "data" / "raw"
DOSSIER_SORTIE = RACINE / "data" / "processed"

SEPARATEUR = ";"

COLONNES_BOULES = [f"boule_{i}" for i in range(1, 6)]
COLONNES_ETOILES = [f"etoile_{i}" for i in range(1, 3)]
COLONNES_GAGNANTS = [colonne_gagnants(c) for c in COMBINAISONS]
COLONNES_RAPPORTS = [colonne_rapport(c) for c in COMBINAISONS]
JOURS = ["LUNDI", "MARDI", "MERCREDI", "JEUDI", "VENDREDI", "SAMEDI", "DIMANCHE"]


# --------------------------------------------------------------------------
# Utilitaires bas niveau
# --------------------------------------------------------------------------


def detecter_encodage(octets: bytes) -> tuple[str, str]:
    """Renvoie (codec de lecture, etiquette documentaire) d'une archive.

    Piege classique de la detection par essais successifs : un fichier ASCII
    se decode sans erreur en utf-8, en utf-8-sig et en cp1252, et renvoyer le
    premier candidat qui « marche » donne une etiquette trompeuse. On teste
    donc du plus specifique au plus permissif, et l'etiquette ne dit que ce
    que les octets prouvent :

      1. BOM present                  -> utf-8 avec BOM
      2. aucun octet > 127            -> ascii
      3. decodable en utf-8           -> utf-8
      4. octet entre 0x80 et 0x9F     -> cp1252 : ces octets sont des
                                         caracteres en cp1252 et des codes de
                                         controle en latin-1, qu'aucun texte
                                         n'emploie
      5. sinon                        -> « 8 bits occidental » : au-dela de
                                         0xA0, cp1252 et latin-1 donnent les
                                         memes caracteres, rien ne permet de
                                         les departager. On lit en cp1252,
                                         avec un resultat identique.
    """
    if octets.startswith(b"\xef\xbb\xbf"):
        return "utf-8-sig", "utf-8 avec BOM"
    if not any(b > 127 for b in octets):
        return "ascii", "ascii"
    try:
        octets.decode("utf-8")
        return "utf-8", "utf-8"
    except UnicodeDecodeError:
        pass
    try:
        octets.decode("cp1252")
    except UnicodeDecodeError:
        # Octet non defini en cp1252 (0x81, 0x8D, 0x8F, 0x90, 0x9D) : latin-1
        # accepte tout, il ne prouve donc rien, d'ou l'etiquette.
        return "latin-1", "latin-1 (repli, non prouve)"
    if any(0x80 <= b <= 0x9F for b in octets):
        return "cp1252", "cp1252"
    return "cp1252", "8 bits occidental (cp1252 ou latin-1, indiscernables)"


def normaliser_nom_colonne(nom: str) -> str:
    """Rend un nom de colonne comparable d'une archive a l'autre.

    Exemples :
        'numéro_de_tirage_dans_le_cycle'        -> 'numero_de_tirage_dans_le_cycle'
        'rapport_du_rang1_Euro_Millions'        -> 'rapport_du_rang1'
        'numero_Tirage_Exceptionnel_Euro_Million' -> 'numero_tirage_exceptionnel'
    """
    nom = nom.strip()
    nom = unicodedata.normalize("NFKD", nom)
    nom = "".join(c for c in nom if not unicodedata.combining(c))
    nom = nom.replace("\ufffd", "")  # caractere de remplacement Unicode
    nom = nom.lower()
    # Suffixe apparu en septembre 2016 (euromillions_4.csv), avec les
    # colonnes Etoile+. Une archive l'ecrit sans le « s » final.
    nom = re.sub(r"_euro_millions?", "", nom)
    nom = re.sub(r"[^a-z0-9]+", "_", nom).strip("_")
    return nom


def parser_date(valeur: str) -> pd.Timestamp | pd.NaTType:
    """Parse les trois formats de date rencontres dans les archives.

        20110506    -> 2011-05-06   (compact, archive 2004-2011)
        31/01/2014  -> 2014-01-31   (format courant)
        23/09/16    -> 2016-09-23   (annee sur 2 chiffres, archive 2014-2016)

    Le format a 2 chiffres est ambigu par nature ; EuroMillions ayant demarre
    en 2004, on fait le choix explicite du siecle courant. Le controle par
    redondance (jour annonce contre jour calcule) confirme ce choix.
    """
    if not isinstance(valeur, str):
        return pd.NaT
    valeur = valeur.strip()
    if not valeur:
        return pd.NaT

    if re.fullmatch(r"\d{8}", valeur):
        return pd.to_datetime(valeur, format="%Y%m%d", errors="coerce")
    if re.fullmatch(r"\d{2}/\d{2}/\d{4}", valeur):
        return pd.to_datetime(valeur, format="%d/%m/%Y", errors="coerce")
    if re.fullmatch(r"\d{2}/\d{2}/\d{2}", valeur):
        # %y : 00-68 -> 2000-2068, ce qui couvre toute la vie du jeu.
        return pd.to_datetime(valeur, format="%d/%m/%y", errors="coerce")
    return pd.to_datetime(valeur, errors="coerce", dayfirst=True)


def parser_nombre(valeur: str) -> float:
    """Convertit un nombre FDJ en flottant.

    Les archives melangent les conventions : espace comme separateur de
    milliers ('1 037 960'), virgule comme separateur decimal ('47567,7'),
    et champs vides pour les rangs inexistants a l'epoque.
    """
    if not isinstance(valeur, str):
        return float("nan")
    valeur = valeur.strip().replace("\u202f", "").replace("\xa0", "")
    valeur = valeur.replace(" ", "").replace(",", ".")
    if not valeur:
        return float("nan")
    try:
        return float(valeur)
    except ValueError:
        return float("nan")


def normaliser_jour(valeur: str) -> str:
    """'VE', 'VENDREDI', 'MARDI   ' -> 'VENDREDI' / 'MARDI'."""
    if not isinstance(valeur, str):
        return ""
    valeur = valeur.strip().upper()
    abreviations = {j[:2]: j for j in JOURS}
    return abreviations.get(valeur, valeur)


# --------------------------------------------------------------------------
# Lecture
# --------------------------------------------------------------------------


@dataclass
class Source:
    chemin: Path
    encodage: str                    # etiquette documentaire
    caracteres_non_ascii: str        # ce qui a permis (ou non) de trancher
    nb_lignes: int
    colonnes: list[str]              # colonnes NOMMEES de l'en-tete
    separateur_final_entete: bool
    separateur_final_lignes: bool
    ordre: int = 0


def lire_archive(chemin: Path, ordre: int = 0) -> tuple[pd.DataFrame, Source]:
    """Lit un CSV FDJ et renvoie un DataFrame aux colonnes normalisees.

    Piege du separateur final. Toutes les archives terminent leurs lignes de
    donnees par un ';', ce qui ajoute un dernier champ vide. Cinq d'entre
    elles terminent aussi leur en-tete par un ';' : le champ vide y a un nom
    vide, et tout reste aligne. Une seule, euromillions_4.csv, a un en-tete
    SANS ce ';' final : 75 noms face a 76 champs.

    Pandas, trouvant plus de champs que de noms, applique alors une
    convention ancienne : il suppose une premiere colonne d'index sans nom, la
    retire des donnees et decale tout le reste d'un cran, sans le moindre
    avertissement. Les dates deviennent des numeros de cycle et les boules se
    chevauchent.

    On construit donc la liste des noms nous-memes, on nomme explicitement
    les champs surnumeraires, on force index_col=False, puis on verifie que
    ces champs sont bien vides avant de les retirer.
    """
    octets = chemin.read_bytes()
    codec, etiquette = detecter_encodage(octets)
    texte = octets.decode(codec)
    non_ascii = "".join(sorted({c for c in texte if ord(c) > 127}))

    lignes = texte.splitlines()
    entete = lignes[0].split(SEPARATEUR)
    premiere = lignes[1].split(SEPARATEUR)

    sep_entete = entete[-1].strip() == ""
    sep_lignes = premiere[-1].strip() == ""
    noms = [n for n in entete if n.strip()]
    if len(noms) != len(entete) - int(sep_entete):
        raise ValueError(f"{chemin.name} : nom de colonne vide au milieu de l'en-tete")

    surnumeraires = len(premiere) - len(noms)
    if surnumeraires < 0:
        raise ValueError(f"{chemin.name} : moins de champs que de noms de colonnes")
    vides = [f"_champ_vide_{i + 1}" for i in range(surnumeraires)]

    df = pd.read_csv(
        chemin,
        sep=SEPARATEUR,
        encoding=codec,
        dtype=str,
        keep_default_na=False,
        engine="python",
        header=0,
        names=noms + vides,
        index_col=False,
    )
    for colonne in vides:
        remplis = df[colonne].fillna("").str.strip().ne("")
        if remplis.any():
            raise ValueError(f"{chemin.name} : le champ surnumeraire {colonne} contient "
                             f"des donnees ({int(remplis.sum())} lignes) - decalage probable")
    df = df.drop(columns=vides)

    df.columns = [normaliser_nom_colonne(c) for c in df.columns]
    df["_source"] = chemin.name
    df["_ordre"] = ordre

    source = Source(chemin, etiquette, non_ascii, len(df), noms, sep_entete, sep_lignes, ordre)
    return df, source


def charger_toutes(dossier: Path) -> tuple[pd.DataFrame, list[Source]]:
    fichiers = sorted(dossier.rglob("*.csv"))
    if not fichiers:
        raise FileNotFoundError(f"Aucun CSV trouve dans {dossier}")

    cadres, sources = [], []
    for ordre, chemin in enumerate(fichiers):
        df, source = lire_archive(chemin, ordre)
        cadres.append(df)
        sources.append(source)

    # concat tolerant : les colonnes absentes d'une archive deviennent vides
    complet = pd.concat(cadres, ignore_index=True, sort=False)
    return complet, sources


# --------------------------------------------------------------------------
# Normalisation
# --------------------------------------------------------------------------


def colonne_brute(df: pd.DataFrame, nom: str) -> pd.Series:
    """Colonne de l'archive, ou colonne vide si elle n'existe pas."""
    if nom in df:
        return df[nom]
    return pd.Series("", index=df.index, dtype=str)


def normaliser(df: pd.DataFrame) -> pd.DataFrame:
    sortie = pd.DataFrame(index=df.index)

    sortie["date_tirage"] = df["date_de_tirage"].map(parser_date)
    regimes = pd.Series([regime_de(d) for d in sortie["date_tirage"]],
                        index=sortie.index, dtype=object)
    sortie["regime"] = [r.nom if r is not None else "hors regime connu" for r in regimes]
    sortie["jour"] = colonne_brute(df, "jour_de_tirage").map(normaliser_jour)

    for colonne in COLONNES_BOULES + COLONNES_ETOILES:
        sortie[colonne] = pd.to_numeric(colonne_brute(df, colonne), errors="coerce").astype("Int64")

    # Nombre de gagnants en Europe, range par COMBINAISON et non par numero de
    # rang : le rang 6 designait 4+0 avant septembre 2016 et 3+2 depuis. Lire
    # la colonne « rang 6 » comme une seule et meme chose melangeait deux
    # combinaisons sur 47 % de l'historique. Le rang de chaque ligne est donc
    # traduit selon les regles de son propre regime (voir regles.py).
    #
    # Meme traduction pour le rapport, c'est-a-dire le gain verse en France
    # par grille gagnante, en euros. Un rapport nul signifie qu'il n'y a eu
    # aucun gagnant et rien a verser (jackpot remis en jeu), pas un gain de
    # zero euro : l'analyse en tient compte.
    for colonne in COLONNES_GAGNANTS + COLONNES_RAPPORTS:
        sortie[colonne] = np.nan
    for regime in REGIMES:
        lignes = sortie["regime"] == regime.nom
        if not lignes.any():
            continue
        for rang, combinaison in rangs_du_regime(regime).items():
            source = colonne_brute(df, f"nombre_de_gagnant_au_rang{rang}_en_europe")
            sortie.loc[lignes, colonne_gagnants(combinaison)] = source[lignes].map(parser_nombre)
            source = colonne_brute(df, f"rapport_du_rang{rang}")
            sortie.loc[lignes, colonne_rapport(combinaison)] = source[lignes].map(parser_nombre)

    # Certains tirages n'ont pas de chiffres europeens publies : l'archive y
    # laisse des zeros a TOUS les rangs, alors que les colonnes francaises sont
    # remplies. Un zero partout est impossible - le dernier rang compte des
    # centaines de milliers de gagnants a chaque tirage - donc ces lignes sont
    # des donnees manquantes deguisees.
    colonnes_france = [f"nombre_de_gagnant_au_rang{r}_en_france" for r in range(1, 14)
                       if f"nombre_de_gagnant_au_rang{r}_en_france" in df]
    europe_vide = sortie[COLONNES_GAGNANTS].fillna(0).eq(0).all(axis=1)
    if colonnes_france:
        france_remplie = df[colonnes_france].map(parser_nombre).fillna(0).gt(0).any(axis=1)
        suspectes = europe_vide & france_remplie
    else:
        suspectes = europe_vide
    sortie.loc[suspectes, COLONNES_GAGNANTS] = np.nan
    sortie[COLONNES_GAGNANTS] = sortie[COLONNES_GAGNANTS].round().astype("Int64")
    sortie["gagnants_non_publies"] = suspectes

    # Identifiant FDJ conserve tel quel : son format a change (2011018 -> 26074)
    # et il ne sert pas de cle. La date est la cle.
    sortie["id_fdj"] = colonne_brute(df, "annee_numero_de_tirage")
    sortie["source"] = df["_source"]
    sortie["_ordre"] = df["_ordre"]

    # Tri STABLE : a date egale, l'ordre des lignes decide de celle que la
    # deduplication garde. Un tri non stable rendrait ce choix arbitraire.
    return sortie.sort_values("date_tirage", kind="stable").reset_index(drop=True)


def dedupliquer(tirages: pd.DataFrame) -> pd.DataFrame:
    """Une ligne par date, en gardant la plus complete.

    Les archives peuvent se chevaucher. A date egale, on prefere la ligne dont
    les chiffres de gagnants sont publies, puis l'archive la plus recente (la
    FDJ y corrige ses erreurs). Le choix ne depend ainsi ni de l'ordre de
    lecture ni d'un tri instable.
    """
    priorite = tirages.assign(_publie=~tirages["gagnants_non_publies"])
    priorite = priorite.sort_values(["date_tirage", "_publie", "_ordre"],
                                    ascending=[True, False, False], kind="stable")
    uniques = priorite.drop_duplicates(subset="date_tirage", keep="first")
    uniques = uniques.dropna(subset=["date_tirage"])
    return uniques.drop(columns=["_publie", "_ordre"]).reset_index(drop=True)


def au_format_long(df: pd.DataFrame) -> pd.DataFrame:
    """Une ligne par boule tiree : la table de travail pour toutes les stats."""
    morceaux = []
    for type_boule, colonnes in (("boule", COLONNES_BOULES), ("etoile", COLONNES_ETOILES)):
        bloc = df.melt(
            id_vars=["date_tirage", "regime"],
            value_vars=colonnes,
            var_name="position",
            value_name="numero",
        )
        bloc["type"] = type_boule
        morceaux.append(bloc)
    long = pd.concat(morceaux, ignore_index=True)
    long = long.dropna(subset=["numero"])
    long["numero"] = long["numero"].astype(int)
    return long.sort_values(["date_tirage", "type", "numero"], kind="stable").reset_index(drop=True)


# --------------------------------------------------------------------------
# Controles qualite
# --------------------------------------------------------------------------


def section(lignes: list[str], titre: str) -> None:
    lignes.append("")
    lignes.append(titre)
    lignes.append("-" * len(titre))


def controler_redondance(brut: pd.DataFrame) -> list[str]:
    """Confronte la lecture aux champs redondants de la source.

    Les archives FDJ repetent deux informations sous une autre forme :
      - `boules_gagnantes_en_ordre_croissant`, chaine du type '-8-10-15-16-31-'
      - `jour_de_tirage`, le nom du jour de la semaine

    Les cinq colonnes `boule_*` doivent redonner la meme chaine une fois
    triees, et le jour calcule depuis la date parsee doit correspondre au jour
    annonce. C'est ce controle qui prouve qu'aucun decalage de colonne n'a
    survecu et que les trois formats de date sont interpretes correctement.
    Il tourne sur les donnees dans leur ordre d'origine, avant tout tri.
    """
    lignes: list[str] = []
    section(lignes, "Controle par redondance interne")

    def decomposer(chaine: str) -> list[int]:
        return sorted(int(x) for x in str(chaine).strip("-").split("-")
                      if x.strip().isdigit())

    def lire(ligne: pd.Series, colonnes: list[str]) -> list[int] | None:
        valeurs = pd.to_numeric(ligne[colonnes], errors="coerce")
        return None if valeurs.isna().any() else sorted(int(v) for v in valeurs)

    ecarts_boules = ecarts_etoiles = ecarts_jour = 0
    non_controlables = 0

    for _, ligne in brut.iterrows():
        reference = decomposer(ligne.get("boules_gagnantes_en_ordre_croissant", ""))
        lues = lire(ligne, COLONNES_BOULES)
        if len(reference) != 5 or lues is None:
            non_controlables += 1
        elif lues != reference:
            ecarts_boules += 1

        ref_etoiles = decomposer(ligne.get("etoiles_gagnantes_en_ordre_croissant", ""))
        lues = lire(ligne, COLONNES_ETOILES)
        if len(ref_etoiles) == 2 and lues is not None and lues != ref_etoiles:
            ecarts_etoiles += 1

        date = parser_date(ligne.get("date_de_tirage", ""))
        jour = normaliser_jour(ligne.get("jour_de_tirage", ""))
        if not pd.isna(date) and jour in JOURS and JOURS[date.weekday()] != jour:
            ecarts_jour += 1

    lignes.append(f"  Tirages confrontes a leur reference : {len(brut)}")
    lignes.append(f"  Reference inexploitable             : {non_controlables}")
    lignes.append(f"  Desaccords sur les boules           : {ecarts_boules}")
    lignes.append(f"  Desaccords sur les etoiles          : {ecarts_etoiles}")
    lignes.append(f"  Jour annonce != jour de la date     : {ecarts_jour}")
    return lignes


def controler_rangs(df: pd.DataFrame) -> list[str]:
    """Verifie la traduction rang -> combinaison contre les gagnants publies.

    Deux controles independants, parce que le premier ne suffit pas toujours.

    1. Volume implicite. A une combinaison donnee, le nombre moyen de gagnants
       vaut environ (grilles jouees) x (probabilite de la combinaison). Divise
       par cette probabilite, il doit donc redonner le meme volume de grilles
       pour toutes les combinaisons d'un regime. Une combinaison mal attribuee
       s'ecarte du lot : l'ancienne inversion des rangs 6 et 7 le faisait de
       plus de 50 %.

    2. Signature des etoiles. Quand deux combinaisons ont des probabilites trop
       proches (2+2 et 3+1 avant 2011 : 1 sur 538 contre 1 sur 550), le volume
       ne les departage pas. Mais une combinaison exigeant deux etoiles depend
       fortement de la popularite de la paire d'etoiles tiree, et pas les
       autres. Cette popularite se lit dans le rapport entre les rangs 1+2 et
       2+1, sans ambiguite dans tous les regimes. La combinaison qui exige le
       plus d'etoiles doit y etre la plus correlee.
    """
    lignes: list[str] = []
    section(lignes, "Controle des rangs (traduction rang -> combinaison)")
    lignes.append("  Volume implicite : gagnants moyens / probabilite, rapporte a la "
                  "mediane du regime.")
    lignes.append("  Ecart tolere : 10 %, pour les combinaisons a 50 gagnants ou plus "
                  "par tirage.")

    anomalies = 0
    for regime in REGIMES:
        bloc = df[(df["regime"] == regime.nom) & ~df["gagnants_non_publies"]]
        if bloc.empty:
            continue
        rangs = rangs_du_regime(regime)
        volumes = {c: bloc[colonne_gagnants(c)].mean() / probabilite(*c, regime.etoiles)
                   for c in rangs.values()}
        reference = float(np.median(list(volumes.values())))
        lignes.append(f"  {regime.nom}")
        morceaux = []
        for rang, c in rangs.items():
            ecart = volumes[c] / reference - 1
            controle = bloc[colonne_gagnants(c)].mean() >= 50
            alerte = controle and abs(ecart) > 0.10
            anomalies += alerte
            morceaux.append(f"{rang}:{c[0]}+{c[1]} {ecart:+.0%}{' <--' if alerte else ''}")
        for i in range(0, len(morceaux), 5):
            lignes.append("      " + "   ".join(morceaux[i:i + 5]))

        # Signature des etoiles, pour les paires de rangs voisins que le volume
        # ne departage pas.
        log = lambda c: np.log(bloc[colonne_gagnants(c)].replace(0, np.nan))  # noqa: E731
        popularite_paire = log((1, 2)) - log((2, 1))
        for rang in range(1, len(rangs)):
            a, b = rangs[rang], rangs[rang + 1]
            proches = probabilite(*b, regime.etoiles) / probabilite(*a, regime.etoiles) < 1.5
            assez = min(bloc[colonne_gagnants(a)].mean(), bloc[colonne_gagnants(b)].mean()) >= 500
            references = {(3, 0), (1, 2), (2, 1)}   # servent a construire l'indice
            if not (proches and assez and a[1] != b[1]) or {a, b} & references:
                continue
            rho = {c: (log(c) - log((3, 0))).corr(popularite_paire, method="spearman")
                   for c in (a, b)}
            attendu = max((a, b), key=lambda c: c[1])
            conforme = rho[attendu] == max(rho.values())
            anomalies += not conforme
            lignes.append(f"      signature des etoiles, rangs {rang}/{rang + 1} "
                          f"({a[0]}+{a[1]} / {b[0]}+{b[1]}) : "
                          f"{rho[a]:.2f} / {rho[b]:.2f}  -> "
                          f"{'conforme' if conforme else 'NON CONFORME'}")
    lignes.append(f"  Anomalies : {anomalies}")
    return lignes


def controler_regimes(df: pd.DataFrame) -> list[str]:
    """Confronte les regimes aux donnees : bornes des numeros ET dates de bascule.

    Verifier que les etoiles restent sous le maximum de chaque regime ne
    controle qu'un sens : une bascule placee trop tot passerait inapercue,
    puisqu'un tirage a 9 etoiles reste compatible avec un regime a 11. On
    cherche donc aussi, pour chaque bascule, les indices qu'en donnent les
    donnees elles-memes : premier tirage du mardi, premiere etoile nouvelle,
    premier rang 2+0 publie.
    """
    lignes: list[str] = []
    section(lignes, "Controle des regimes")
    for regime in REGIMES:
        bloc = df[df["regime"] == regime.nom]
        if bloc.empty:
            lignes.append(f"  {regime.nom}\n      aucun tirage dans les archives fournies")
            continue
        bmin = int(bloc[COLONNES_BOULES].min().min())
        bmax = int(bloc[COLONNES_BOULES].max().max())
        emin = int(bloc[COLONNES_ETOILES].min().min())
        emax = int(bloc[COLONNES_ETOILES].max().max())
        alerte_b = "" if bmax <= 50 else "  <-- HORS BORNES"
        alerte_e = "" if emax <= regime.etoiles else "  <-- HORS BORNES"
        lignes.append(f"  {regime.nom}")
        lignes.append(f"      {len(bloc):4d} tirages   boules {bmin}-{bmax} "
                      f"(attendu 1-50){alerte_b}")
        lignes.append(f"                    etoiles {emin}-{emax} "
                      f"(attendu 1-{regime.etoiles}){alerte_e}")

    lignes.append("")
    lignes.append("  Dates de bascule confrontees aux donnees :")
    for avant, apres in zip(REGIMES, REGIMES[1:]):
        ancien = df[df["regime"] == avant.nom]
        nouveau = df[df["regime"] == apres.nom].reset_index(drop=True)
        if ancien.empty or nouveau.empty:
            continue
        premier = nouveau["date_tirage"].iloc[0]
        lignes.append(f"  {apres.debut} ({avant.etoiles} -> {apres.etoiles} etoiles) : "
                      f"dernier tirage {ancien['date_tirage'].iloc[-1]:%d/%m/%Y}, "
                      f"premier {premier:%d/%m/%Y}")
        etablie = False

        def indice(libelle: str, masque: pd.Series) -> None:
            nonlocal etablie
            if not masque.any():
                lignes.append(f"      {libelle:34s}: jamais observe")
                return
            position = int(np.flatnonzero(masque.to_numpy())[0])
            date = nouveau["date_tirage"].iloc[position]
            etablie |= position == 0
            lignes.append(f"      {libelle:34s}: {date:%d/%m/%Y} "
                          f"({'1er' if position == 0 else f'{position + 1}e'} tirage du regime)")

        nouvelles = nouveau[COLONNES_ETOILES].gt(avant.etoiles).any(axis=1)
        indice(f"premiere etoile > {avant.etoiles}", nouvelles)
        if apres.rang_2_0 and not avant.rang_2_0:
            indice("premier rang 2+0 publie", nouveau[colonne_gagnants((2, 0))].notna())
        if not ancien["jour"].eq("MARDI").any():
            indice("premier tirage du mardi", nouveau["jour"].eq("MARDI"))
        verdict = ("etablie au tirage pres par les donnees" if etablie
                   else "compatible avec les donnees, mais non etablie par elles")
        lignes.append(f"      -> bascule {verdict}")

    autres = df[~df["jour"].isin(["MARDI", "VENDREDI"])]
    lignes.append("")
    lignes.append(f"  Tirages hors mardi/vendredi : {len(autres)}")
    mardis_tot = df[(df["jour"] == "MARDI") & (df["date_tirage"] < pd.Timestamp(REGIMES[1].debut))]
    lignes.append(f"  Tirages du mardi avant le {pd.Timestamp(REGIMES[1].debut):%d/%m/%Y} : "
                  f"{len(mardis_tot)}")
    return lignes


def controler(df: pd.DataFrame, sources: list[Source]) -> list[str]:
    lignes: list[str] = []

    section(lignes, "Archives lues")
    for s in sources:
        lignes.append(f"  {s.chemin.name:26s} {s.nb_lignes:4d} tirages  "
                      f"{len(s.colonnes)} colonnes nommees  encodage : {s.encodage}"
                      + (f" (caracteres : {s.caracteres_non_ascii})"
                         if s.caracteres_non_ascii else ""))
        etat = (f"';' final : en-tete {'oui' if s.separateur_final_entete else 'NON'}, "
                f"lignes {'oui' if s.separateur_final_lignes else 'non'}")
        if s.separateur_final_entete != s.separateur_final_lignes:
            etat += "  <-- en-tete et lignes divergent : lecture naive decalee d'une colonne"
        lignes.append(f"  {'':26s} {etat}")

    section(lignes, "Couverture")
    valides = df.dropna(subset=["date_tirage"])
    lignes.append(f"  Lignes lues         : {len(df)}")
    lignes.append(f"  Periode             : {valides['date_tirage'].min():%Y-%m-%d} "
                  f"-> {valides['date_tirage'].max():%Y-%m-%d}")
    lignes.append(f"  Dates illisibles    : {df['date_tirage'].isna().sum()}")
    lignes.append(f"  Hors regime connu   : {(df['regime'] == 'hors regime connu').sum()}")

    section(lignes, "Doublons de date")
    doublons = df[df.duplicated("date_tirage", keep=False) & df["date_tirage"].notna()]
    if doublons.empty:
        lignes.append("  Aucun.")
    else:
        comparees = COLONNES_BOULES + COLONNES_ETOILES + COLONNES_GAGNANTS
        lignes.append(f"  {doublons['date_tirage'].nunique()} dates presentes plusieurs fois "
                      f"(chevauchement entre archives) :")
        for date, groupe in doublons.groupby("date_tirage"):
            fichiers = ", ".join(sorted(groupe["source"].unique()))
            differentes = [c for c in comparees
                           if groupe[c].astype(str).nunique(dropna=False) > 1]
            etat = "identiques" if not differentes else "DIVERGENTS : " + ", ".join(differentes)
            lignes.append(f"    {date:%Y-%m-%d}  {fichiers}  {etat}")

    section(lignes, "Chiffres de gagnants europeens")
    manquants = int(df["gagnants_non_publies"].sum())
    lignes.append(f"  Tirages sans chiffres europeens publies : {manquants}")
    for _, ligne in df[df["gagnants_non_publies"]].iterrows():
        lignes.append(f"    {ligne['date_tirage']:%Y-%m-%d}  ({ligne['source']})  "
                      f"-> gagnants marques manquants, pas zero")

    section(lignes, "Coherence des tirages")
    boules = df[COLONNES_BOULES]
    non_distinctes = df[boules.nunique(axis=1) != boules.notna().sum(axis=1)]
    lignes.append(f"  Tirages avec boules repetees   : {len(non_distinctes)}")
    etoiles = df[COLONNES_ETOILES]
    etoiles_repetees = df[etoiles.nunique(axis=1) != etoiles.notna().sum(axis=1)]
    lignes.append(f"  Tirages avec etoiles repetees  : {len(etoiles_repetees)}")
    incomplets = df[boules.notna().sum(axis=1) != 5]
    lignes.append(f"  Tirages sans 5 boules          : {len(incomplets)}")

    lignes += controler_regimes(df)
    lignes += controler_rangs(df)

    section(lignes, "Tirages par an")
    bascule = pd.Timestamp(REGIMES[1].debut)
    lignes.append(f"  Attendu : ~52/an avant {bascule.year} (hebdomadaire), ~104/an apres "
                  f"(bi-hebdomadaire). {bascule.year} est l'annee de bascule.")
    par_an = valides.groupby(valides["date_tirage"].dt.year).size()
    premiere, derniere = par_an.index.min(), par_an.index.max()
    for annee, n in par_an.items():
        if annee in (premiere, derniere):
            drapeau = "  (annee partielle)"
        elif annee < bascule.year:
            drapeau = "" if 48 <= n <= 56 else "  <-- volume inhabituel"
        elif annee == bascule.year:
            drapeau = "  (bascule hebdo -> bi-hebdo)"
        else:
            drapeau = "" if 100 <= n <= 108 else "  <-- volume inhabituel"
        lignes.append(f"  {annee} : {n:3d}{drapeau}")

    section(lignes, "Trous dans la serie")
    lignes.append(f"  Rythme attendu : 1 tirage/semaine jusqu'au {bascule:%d/%m/%Y} "
                  "(vendredi seul), 2/semaine ensuite (mardi et vendredi).")
    uniques = valides.drop_duplicates("date_tirage")
    ecarts = uniques["date_tirage"].diff().dt.days
    seuil = uniques["date_tirage"].map(lambda d: 9 if d < bascule else 5)
    trous = uniques[ecarts > seuil]
    if trous.empty:
        lignes.append("  Aucun trou anormal.")
    else:
        for idx, ligne in trous.iterrows():
            jours = int(ecarts.loc[idx])
            note = "  <-- ARCHIVE MANQUANTE" if jours > 30 else ""
            lignes.append(f"  {ligne['date_tirage']:%Y-%m-%d}  "
                          f"(+{jours} jours depuis le precedent){note}")

    return lignes


# --------------------------------------------------------------------------
# Point d'entree
# --------------------------------------------------------------------------


def main() -> None:
    DOSSIER_SORTIE.mkdir(parents=True, exist_ok=True)

    brut, sources = charger_toutes(DOSSIER_BRUT)
    tirages = normaliser(brut)

    rapport_avant = controler(tirages, sources)
    rapport_avant += controler_redondance(brut)

    avant = len(tirages)
    tirages = dedupliquer(tirages)
    retires = avant - len(tirages)

    long = au_format_long(tirages)

    tirages.to_csv(DOSSIER_SORTIE / "euromillions_tirages.csv", index=False, sep=";")
    long.to_csv(DOSSIER_SORTIE / "euromillions_boules.csv", index=False, sep=";")

    rapport = ["RAPPORT QUALITE - INGESTION EUROMILLIONS",
               "=" * 40]
    rapport += rapport_avant
    rapport += ["", "Deduplication", "-" * 13,
                f"  Lignes retirees (dates en double) : {retires}",
                f"  Tirages uniques conserves         : {len(tirages)}",
                f"  Boules en format long             : {len(long)}"]
    texte = "\n".join(rapport) + "\n"

    (DOSSIER_SORTIE / "rapport_qualite.txt").write_text(texte, encoding="utf-8")
    print(texte)


if __name__ == "__main__":
    main()
