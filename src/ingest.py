"""
Ingestion et normalisation des archives EuroMillions de la FDJ.

Les archives publiees par la FDJ couvrent plusieurs decennies et le format a
change plusieurs fois : encodage, format de date, libelle du jour, nom des
colonnes, apparition de nouvelles colonnes (Etoile+, numero de tirage dans le
cycle). Ce module ramene tout cela a un schema unique et signale les anomalies
plutot que de les masquer.

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

import pandas as pd

# --------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------

RACINE = Path(__file__).resolve().parents[1]
DOSSIER_BRUT = RACINE / "data" / "raw"
DOSSIER_SORTIE = RACINE / "data" / "processed"

SEPARATEUR = ";"

# Regimes de jeu EuroMillions : les regles ont change deux fois.
# On ne les suppose pas, on les verifie (cf. controle_regimes).
REGIMES = [
    # (nom, date_debut, date_fin_exclue, nb_boules, nb_etoiles)
    ("2004-2011 : 50 boules / 9 etoiles", "2004-02-13", "2011-05-10", 50, 9),
    ("2011-2016 : 50 boules / 11 etoiles", "2011-05-10", "2016-09-24", 50, 11),
    ("2016+ : 50 boules / 12 etoiles", "2016-09-24", "2100-01-01", 50, 12),
]

COLONNES_BOULES = [f"boule_{i}" for i in range(1, 6)]
COLONNES_ETOILES = [f"etoile_{i}" for i in range(1, 3)]

# Les 13 rangs de gain EuroMillions, avec le nombre de boules et d'etoiles
# qu'ils exigent. Le nombre de gagnants a un rang depend de la popularite des
# numeros tires : c'est ce qui permet de mesurer les habitudes des JOUEURS, et
# pas seulement le comportement des boules.
RANGS = {
    1: (5, 2), 2: (5, 1), 3: (5, 0), 4: (4, 2), 5: (4, 1), 6: (3, 2),
    7: (4, 0), 8: (2, 2), 9: (3, 1), 10: (3, 0), 11: (1, 2), 12: (2, 1),
    13: (2, 0),
}


# --------------------------------------------------------------------------
# Utilitaires bas niveau
# --------------------------------------------------------------------------


def detecter_encodage(chemin: Path) -> str:
    """Identifie l'encodage d'une archive, et le nomme honnetement.

    Piege classique de la detection par essais successifs : tout fichier ASCII
    se decode sans erreur en utf-8, en utf-8-sig et en cp1252. Renvoyer le
    premier candidat qui « marche » donne donc une etiquette exacte au sens
    technique mais trompeuse au sens documentaire : un fichier sans BOM
    etiquete utf-8-sig laisse croire qu'il en a un.

    On teste donc dans l'ordre du plus specifique au plus permissif :
      1. BOM present            -> utf-8-sig
      2. aucun octet > 127      -> ascii (sous-ensemble strict d'utf-8)
      3. decodable en utf-8     -> utf-8
      4. sinon                  -> cp1252, puis latin-1 en dernier recours
         (latin-1 accepte n'importe quel octet : il ne prouve rien, il se
         contente de ne jamais echouer)
    """
    octets = chemin.read_bytes()

    if octets.startswith(b"\xef\xbb\xbf"):
        return "utf-8-sig"
    if not any(b > 127 for b in octets):
        return "ascii"
    try:
        octets.decode("utf-8")
        return "utf-8"
    except UnicodeDecodeError:
        pass
    for encodage in ("cp1252", "latin-1"):
        try:
            octets.decode(encodage)
            return encodage
        except UnicodeDecodeError:
            continue
    return "latin-1"


def normaliser_nom_colonne(nom: str) -> str:
    """Rend un nom de colonne comparable d'une archive a l'autre.

    Exemples :
        'numero_de_tirage_dans_le_cycle' (accent casse) -> 'numero_de_tirage_dans_le_cycle'
        'rapport_du_rang1_Euro_Millions'                -> 'rapport_du_rang1'
    """
    nom = nom.strip()
    # Supprime les accents (y compris ceux mal decodes en iso-8859-1)
    nom = unicodedata.normalize("NFKD", nom)
    nom = "".join(c for c in nom if not unicodedata.combining(c))
    nom = nom.replace("�", "")  # caractere de remplacement Unicode
    nom = nom.lower()
    # Harmonise les suffixes apparus en 2019 avec l'arrivee d'Etoile+
    nom = nom.replace("_euro_millions", "")
    nom = re.sub(r"[^a-z0-9]+", "_", nom).strip("_")
    return nom


def parser_date(valeur: str) -> pd.Timestamp | pd.NaTType:
    """Parse les trois formats de date rencontres dans les archives.

        20110506    -> 2011-05-06   (compact, archives les plus anciennes)
        31/01/2014  -> 2014-01-31   (format courant)
        23/09/16    -> 2016-09-23   (annee sur 2 chiffres, archives 2016)

    Le format a 2 chiffres est ambigu par nature ; EuroMillions ayant demarre
    en 2004, on fait le choix explicite du siecle courant.
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
    abreviations = {
        "LU": "LUNDI",
        "MA": "MARDI",
        "ME": "MERCREDI",
        "JE": "JEUDI",
        "VE": "VENDREDI",
        "SA": "SAMEDI",
        "DI": "DIMANCHE",
    }
    return abreviations.get(valeur, valeur)


# --------------------------------------------------------------------------
# Lecture
# --------------------------------------------------------------------------


@dataclass
class Source:
    chemin: Path
    encodage: str
    nb_lignes: int
    colonnes_brutes: list[str]
    champs_surnumeraires: int = 0


def lire_archive(chemin: Path) -> tuple[pd.DataFrame, Source]:
    """Lit un CSV FDJ et renvoie un DataFrame aux colonnes normalisees.

    Piege du separateur final. Dans certaines archives (euromillions_4.csv),
    les lignes de donnees se terminent par un ';' que la ligne d'en-tete n'a
    pas : 75 noms de colonnes face a 76 champs, le 76e etant toujours vide.
    Aucune donnee ne manque et un tableur n'y voit rien d'anormal.

    Mais pandas, trouvant plus de champs que de noms, applique une convention
    ancienne : il suppose une premiere colonne d'index sans nom, la retire des
    donnees et decale tout le reste d'un cran, sans le moindre avertissement.
    Les dates deviennent alors des numeros de cycle et les boules se
    chevauchent.

    On construit donc la liste des noms nous-memes, en nommant explicitement
    le champ surnumeraire, et on force index_col=False.
    """
    encodage = detecter_encodage(chemin)

    with chemin.open(encoding=encodage) as f:
        entete = f.readline().rstrip("\n\r").split(SEPARATEUR)
        premiere = f.readline().rstrip("\n\r").split(SEPARATEUR)

    noms = list(entete)
    surnumeraires = len(premiere) - len(entete)
    if surnumeraires > 0:
        noms += [f"champ_surnumeraire_{i + 1}" for i in range(surnumeraires)]

    df = pd.read_csv(
        chemin,
        sep=SEPARATEUR,
        encoding=encodage,
        dtype=str,
        keep_default_na=False,
        engine="python",
        header=0,
        names=noms,
        index_col=False,
    )
    colonnes_brutes = list(entete)
    df.columns = [normaliser_nom_colonne(c) for c in df.columns]
    df["_source"] = chemin.name

    source = Source(chemin, encodage, len(df), colonnes_brutes)
    source.champs_surnumeraires = max(surnumeraires, 0)
    return df, source


def charger_toutes(dossier: Path) -> tuple[pd.DataFrame, list[Source]]:
    fichiers = sorted(dossier.rglob("*.csv"))
    if not fichiers:
        raise FileNotFoundError(f"Aucun CSV trouve dans {dossier}")

    cadres, sources = [], []
    for chemin in fichiers:
        df, source = lire_archive(chemin)
        cadres.append(df)
        sources.append(source)

    # concat tolerant : les colonnes absentes d'une archive deviennent vides
    complet = pd.concat(cadres, ignore_index=True, sort=False)
    return complet, sources


# --------------------------------------------------------------------------
# Normalisation
# --------------------------------------------------------------------------


def normaliser(df: pd.DataFrame) -> pd.DataFrame:
    sortie = pd.DataFrame(index=df.index)

    sortie["date_tirage"] = df["date_de_tirage"].map(parser_date)
    sortie["jour"] = df.get("jour_de_tirage", "").map(normaliser_jour)

    for colonne in COLONNES_BOULES + COLONNES_ETOILES:
        sortie[colonne] = pd.to_numeric(df.get(colonne), errors="coerce").astype("Int64")

    # Nombre de gagnants par rang, en Europe. Sert a mesurer la popularite des
    # numeros aupres des joueurs : un rang exigeant beaucoup de boules
    # principales reagit fortement aux numeros tires, un rang qui n'en exige
    # qu'une n'y reagit presque pas. Ce contraste sert de temoin.
    for rang in RANGS:
        colonne = f"nombre_de_gagnant_au_rang{rang}_en_europe"
        sortie[f"gagnants_rang{rang}"] = (
            df[colonne].map(parser_nombre) if colonne in df else pd.NA
        )

    # Certains tirages n'ont pas de chiffres europeens publies : l'archive y
    # laisse des zeros a TOUS les rangs, alors que les colonnes francaises sont
    # remplies. Un zero partout est impossible - le dernier rang compte des
    # centaines de milliers de gagnants a chaque tirage - donc ces lignes sont
    # des donnees manquantes deguisees. Lues telles quelles, elles inseraient
    # un faux zero dans toute analyse des gagnants.
    colonnes_gagnants = [f"gagnants_rang{r}" for r in RANGS]
    colonnes_france = [f"nombre_de_gagnant_au_rang{r}_en_france" for r in RANGS
                       if f"nombre_de_gagnant_au_rang{r}_en_france" in df]
    europe_vide = sortie[colonnes_gagnants].fillna(0).eq(0).all(axis=1)
    if colonnes_france:
        france_remplie = df[colonnes_france].map(parser_nombre).fillna(0).gt(0).any(axis=1)
        suspectes = europe_vide & france_remplie
    else:
        suspectes = europe_vide
    sortie.loc[suspectes, colonnes_gagnants] = pd.NA
    sortie["gagnants_non_publies"] = suspectes

    # Identifiant FDJ conserve tel quel : son format a change (2011018 -> 26074)
    # et il ne sert pas de cle. La date est la cle.
    sortie["id_fdj"] = df.get("annee_numero_de_tirage", "")
    sortie["source"] = df["_source"]

    sortie = sortie.sort_values("date_tirage").reset_index(drop=True)
    sortie.insert(1, "regime", sortie["date_tirage"].map(attribuer_regime))
    return sortie


def attribuer_regime(date: pd.Timestamp) -> str:
    if pd.isna(date):
        return "inconnu"
    for nom, debut, fin, _, _ in REGIMES:
        if pd.Timestamp(debut) <= date < pd.Timestamp(fin):
            return nom
    return "hors regime connu"


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
    return long.sort_values(["date_tirage", "type", "numero"]).reset_index(drop=True)


# --------------------------------------------------------------------------
# Controles qualite
# --------------------------------------------------------------------------


def controler_redondance(brut: pd.DataFrame) -> list[str]:
    """Confronte la lecture aux champs redondants de la source.

    Les archives FDJ repetent deux informations sous une autre forme :
      - `boules_gagnantes_en_ordre_croissant`, chaine du type '-8-10-15-16-31-'
      - `jour_de_tirage`, le nom du jour de la semaine

    Ces champs sont donc verifiables sans rien supposer : les cinq colonnes
    `boule_*` doivent redonner la meme chaine une fois triees, et le jour
    calcule depuis la date parsee doit correspondre au jour annonce.

    C'est ce controle qui prouve qu'aucun decalage de colonne n'a survecu et
    que les trois formats de date sont interpretes correctement — en
    particulier le format a deux chiffres, ambigu par nature. Il tourne sur
    les donnees dans leur ordre d'origine, avant tout tri.
    """
    lignes = ["", "Controle par redondance interne", "-" * 31]

    jours = ["LUNDI", "MARDI", "MERCREDI", "JEUDI", "VENDREDI", "SAMEDI", "DIMANCHE"]

    def decomposer(chaine: str) -> list[int]:
        return sorted(int(x) for x in str(chaine).strip("-").split("-")
                      if x.strip().isdigit())

    ecarts_boules = ecarts_etoiles = ecarts_jour = 0
    non_controlables = 0

    for _, ligne in brut.iterrows():
        reference = decomposer(ligne.get("boules_gagnantes_en_ordre_croissant", ""))
        if len(reference) != 5:
            non_controlables += 1
        else:
            lues = sorted(int(ligne[c]) for c in COLONNES_BOULES)
            if lues != reference:
                ecarts_boules += 1

        ref_etoiles = decomposer(ligne.get("etoiles_gagnantes_en_ordre_croissant", ""))
        if len(ref_etoiles) == 2:
            lues = sorted(int(ligne[c]) for c in COLONNES_ETOILES)
            if lues != ref_etoiles:
                ecarts_etoiles += 1

        date = parser_date(ligne.get("date_de_tirage", ""))
        jour = normaliser_jour(ligne.get("jour_de_tirage", ""))
        if not pd.isna(date) and jour in jours and jours[date.weekday()] != jour:
            ecarts_jour += 1

    lignes.append(f"  Tirages confrontes a leur reference : {len(brut)}")
    lignes.append(f"  Reference inexploitable             : {non_controlables}")
    lignes.append(f"  Desaccords sur les boules           : {ecarts_boules}")
    lignes.append(f"  Desaccords sur les etoiles          : {ecarts_etoiles}")
    lignes.append(f"  Jour annonce != jour de la date     : {ecarts_jour}")
    return lignes


def controler(df: pd.DataFrame, sources: list[Source]) -> list[str]:
    lignes: list[str] = []

    def section(titre: str) -> None:
        lignes.append("")
        lignes.append(titre)
        lignes.append("-" * len(titre))

    section("Archives lues")
    for s in sources:
        alerte = ""
        if s.champs_surnumeraires:
            alerte = (f"  <-- separateur final absent de l'en-tete : "
                      f"{s.champs_surnumeraires} champ(s) vide(s) en trop par ligne")
        lignes.append(f"  {s.chemin.name:32s} {s.nb_lignes:5d} tirages  "
                      f"encodage={s.encodage:10s} colonnes={len(s.colonnes_brutes)}{alerte}")

    section("Couverture")
    valides = df.dropna(subset=["date_tirage"])
    lignes.append(f"  Tirages retenus     : {len(df)}")
    lignes.append(f"  Periode             : {valides['date_tirage'].min():%Y-%m-%d} "
                  f"-> {valides['date_tirage'].max():%Y-%m-%d}")
    lignes.append(f"  Dates illisibles    : {df['date_tirage'].isna().sum()}")

    section("Doublons de date")
    doublons = df[df.duplicated("date_tirage", keep=False) & df["date_tirage"].notna()]
    if doublons.empty:
        lignes.append("  Aucun.")
    else:
        lignes.append(f"  {doublons['date_tirage'].nunique()} dates presentes plusieurs fois "
                      f"(chevauchement entre archives) :")
        for date, groupe in doublons.groupby("date_tirage"):
            fichiers = ", ".join(sorted(groupe["source"].unique()))
            identiques = groupe[COLONNES_BOULES + COLONNES_ETOILES].drop_duplicates()
            etat = "identiques" if len(identiques) == 1 else "DIVERGENTS"
            lignes.append(f"    {date:%Y-%m-%d}  {etat:12s}  {fichiers}")

    section("Chiffres de gagnants europeens")
    manquants = df["gagnants_non_publies"].sum() if "gagnants_non_publies" in df else 0
    lignes.append(f"  Tirages sans chiffres europeens publies : {manquants}")
    if manquants:
        for _, ligne in df[df["gagnants_non_publies"]].iterrows():
            lignes.append(f"    {ligne['date_tirage']:%Y-%m-%d}  ({ligne['source']})  "
                          f"-> gagnants marques manquants, pas zero")

    section("Coherence des tirages")
    boules = df[COLONNES_BOULES]
    non_distinctes = df[boules.nunique(axis=1) != boules.notna().sum(axis=1)]
    lignes.append(f"  Tirages avec boules repetees   : {len(non_distinctes)}")
    etoiles = df[COLONNES_ETOILES]
    etoiles_repetees = df[etoiles.nunique(axis=1) != etoiles.notna().sum(axis=1)]
    lignes.append(f"  Tirages avec etoiles repetees  : {len(etoiles_repetees)}")
    incomplets = df[boules.notna().sum(axis=1) != 5]
    lignes.append(f"  Tirages sans 5 boules          : {len(incomplets)}")

    section("Controle des regimes (bornes observees vs regles attendues)")
    for nom, debut, fin, max_boule, max_etoile in REGIMES:
        bloc = df[df["regime"] == nom]
        if bloc.empty:
            lignes.append(f"  {nom}\n      aucun tirage dans les archives fournies")
            continue
        bmin = int(bloc[COLONNES_BOULES].min().min())
        bmax = int(bloc[COLONNES_BOULES].max().max())
        emin = int(bloc[COLONNES_ETOILES].min().min())
        emax = int(bloc[COLONNES_ETOILES].max().max())
        alerte_b = "" if bmax <= max_boule else "  <-- HORS BORNES"
        alerte_e = "" if emax <= max_etoile else "  <-- HORS BORNES"
        lignes.append(f"  {nom}")
        lignes.append(f"      {len(bloc):4d} tirages   boules {bmin}-{bmax} "
                      f"(attendu 1-{max_boule}){alerte_b}")
        lignes.append(f"                    etoiles {emin}-{emax} "
                      f"(attendu 1-{max_etoile}){alerte_e}")

    section("Tirages par an")
    lignes.append("  Attendu : ~52/an jusqu'en 2010 (hebdomadaire), ~104/an a partir "
                  "de 2012 (bi-hebdomadaire). 2011 est l'annee de bascule.")
    par_an = valides.groupby(valides["date_tirage"].dt.year).size()
    premiere, derniere = par_an.index.min(), par_an.index.max()
    for annee, n in par_an.items():
        drapeau = ""
        partielle = annee in (premiere, derniere)
        if partielle:
            drapeau = "  (annee partielle)"
        elif annee <= 2010:
            drapeau = "" if 48 <= n <= 56 else "  <-- volume inhabituel"
        elif annee == 2011:
            drapeau = "  (bascule hebdo -> bi-hebdo en mai)"
        else:
            drapeau = "" if 100 <= n <= 108 else "  <-- volume inhabituel"
        lignes.append(f"  {annee} : {n:3d}{drapeau}")

    section("Trous dans la serie")
    lignes.append("  Rythme attendu : 1 tirage/semaine jusqu'au 10/05/2011 "
                  "(vendredi seul), 2/semaine ensuite (mardi et vendredi).")
    ecarts = valides["date_tirage"].diff().dt.days
    bascule = pd.Timestamp("2011-05-10")
    seuil = valides["date_tirage"].map(lambda d: 9 if d < bascule else 5)
    trous = valides[ecarts > seuil]
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

    # Deduplication : les archives FDJ se chevauchent. On garde la premiere
    # occurrence de chaque date, apres avoir verifie plus haut que les
    # doublons portent bien les memes numeros.
    avant = len(tirages)
    tirages = tirages.drop_duplicates(subset="date_tirage", keep="first")
    tirages = tirages.dropna(subset=["date_tirage"]).reset_index(drop=True)
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
    texte = "\n".join(rapport)

    (DOSSIER_SORTIE / "rapport_qualite.txt").write_text(texte, encoding="utf-8")
    print(texte)


if __name__ == "__main__":
    main()
