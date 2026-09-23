"""
Tests du projet : chaque piege documente dans le README a le sien.

    python -m pytest

Les tests sur les archives reelles lisent data/raw/ ; les autres fabriquent
leurs propres fichiers, pour isoler le comportement teste.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import ingest  # noqa: E402
import regles  # noqa: E402
from analyse import ecarts_maximaux  # noqa: E402
from modele import Tirage, plus_longues_absences  # noqa: E402

# --------------------------------------------------------------------------
# Regles du jeu
# --------------------------------------------------------------------------


def test_probabilites_officielles():
    """Le jackpot : 1 chance sur 76 275 360, 116 531 800 puis 139 838 160."""
    for regime, attendu in zip(regles.REGIMES, (76_275_360, 116_531_800, 139_838_160)):
        assert round(1 / regles.probabilite(5, 2, regime.etoiles)) == attendu


def test_rangs_par_regime():
    """Les rangs 6/7 et 8/9 n'ont pas designe les memes combinaisons partout."""
    avant_2011, de_2011_a_2016, depuis_2016 = (regles.rangs_du_regime(r) for r in regles.REGIMES)
    assert (avant_2011[6], avant_2011[7], avant_2011[8], avant_2011[9]) == ((4, 0), (3, 2), (3, 1), (2, 2))
    assert (de_2011_a_2016[6], de_2011_a_2016[7], de_2011_a_2016[8], de_2011_a_2016[9]) == (
        (4, 0), (3, 2), (2, 2), (3, 1))
    assert (depuis_2016[6], depuis_2016[7], depuis_2016[8], depuis_2016[9]) == ((3, 2), (4, 0), (2, 2), (3, 1))
    assert len(avant_2011) == 12 and len(depuis_2016) == 13
    # Rangs identiques dans tous les regimes
    for regime in regles.REGIMES:
        rangs = regles.rangs_du_regime(regime)
        assert rangs[1] == (5, 2) and rangs[10] == (3, 0) and rangs[11] == (1, 2) and rangs[12] == (2, 1)


# --------------------------------------------------------------------------
# Ingestion
# --------------------------------------------------------------------------


@pytest.mark.parametrize("octets, codec, etiquette", [
    (b"abc;def\n", "ascii", "ascii"),
    (b"\xef\xbb\xbfabc\n", "utf-8-sig", "utf-8 avec BOM"),
    ("numéro".encode("utf-8"), "utf-8", "utf-8"),
    ("numéro".encode("cp1252"), "cp1252", "8 bits occidental (cp1252 ou latin-1, indiscernables)"),
    ("prix : 5 €".encode("cp1252"), "cp1252", "cp1252"),
])
def test_detecter_encodage(octets, codec, etiquette):
    assert ingest.detecter_encodage(octets) == (codec, etiquette)


@pytest.mark.parametrize("brut, attendu", [
    ("numéro_de_tirage_dans_le_cycle", "numero_de_tirage_dans_le_cycle"),
    ("rapport_du_rang1_Euro_Millions", "rapport_du_rang1"),
    ("numero_Tirage_Exceptionnel_Euro_Million", "numero_tirage_exceptionnel"),
    ("nombre_de_gagnant_au_rang3_Euro_Millions_en_europe", "nombre_de_gagnant_au_rang3_en_europe"),
])
def test_normaliser_nom_colonne(brut, attendu):
    assert ingest.normaliser_nom_colonne(brut) == attendu


@pytest.mark.parametrize("valeur, attendu", [
    ("20110506", "2011-05-06"), ("31/01/2014", "2014-01-31"), ("23/09/16", "2016-09-23"),
])
def test_parser_date(valeur, attendu):
    assert ingest.parser_date(valeur) == pd.Timestamp(attendu)


def test_separateur_final_absent_de_l_entete(tmp_path):
    """Le piege d'euromillions_4.csv : 3 noms, 4 champs. Sans precaution,
    pandas decale toutes les colonnes d'un cran."""
    chemin = tmp_path / "archive.csv"
    chemin.write_bytes(b"date_de_tirage;boule_1;boule_2\n27/09/2016;41;6;\n")
    df, source = ingest.lire_archive(chemin)
    assert df.loc[0, "date_de_tirage"] == "27/09/2016"
    assert df.loc[0, "boule_1"] == "41"
    assert not source.separateur_final_entete and source.separateur_final_lignes


def test_champ_surnumeraire_non_vide_refuse(tmp_path):
    chemin = tmp_path / "archive.csv"
    chemin.write_bytes(b"a;b\n1;2;3\n")
    with pytest.raises(ValueError, match="surnumeraire"):
        ingest.lire_archive(chemin)


def test_deduplication_garde_la_ligne_publiee():
    """A date egale, la ligne dont les gagnants sont publies l'emporte, quel
    que soit l'ordre des archives."""
    date = pd.Timestamp("2020-01-31")
    lignes = pd.DataFrame({
        "date_tirage": [date, date],
        "gagnants_non_publies": [True, False],
        "_ordre": [5, 1],
        "source": ["recente.csv", "ancienne.csv"],
    })
    assert ingest.dedupliquer(lignes)["source"].tolist() == ["ancienne.csv"]


@pytest.fixture(scope="module")
def archives():
    brut, sources = ingest.charger_toutes(ingest.DOSSIER_BRUT)
    return brut, sources, ingest.normaliser(brut)


def test_archives_reelles(archives):
    brut, sources, tirages = archives
    assert len(tirages) == tirages["date_tirage"].nunique()
    assert tirages["date_tirage"].notna().all()
    assert (tirages["regime"] != "hors regime connu").all()
    assert tirages["gagnants_non_publies"].sum() == 1
    texte = "\n".join(ingest.controler_redondance(brut))
    assert "Desaccords sur les boules           : 0" in texte
    assert "Jour annonce != jour de la date     : 0" in texte


def test_controle_des_rangs_passe(archives):
    _, _, tirages = archives
    assert ingest.controler_rangs(tirages)[-1] == "  Anomalies : 0"


def test_controle_des_rangs_detecte_l_ancienne_erreur(archives, monkeypatch):
    """Le controle doit avoir des dents : applique a l'ancienne traduction
    (les rangs actuels pour tout l'historique), il doit la refuser."""
    brut, _, _ = archives
    actuels = regles.rangs_du_regime(regles.REGIMES[-1])
    monkeypatch.setattr(ingest, "rangs_du_regime", lambda r: {
        k: v for k, v in actuels.items() if r.rang_2_0 or v != (2, 0)})
    fausse = ingest.normaliser(brut)
    anomalies = int(ingest.controler_rangs(fausse)[-1].split(":")[1])
    assert anomalies > 0


# --------------------------------------------------------------------------
# Modele
# --------------------------------------------------------------------------


def test_facteur_khi2():
    assert Tirage(K=50, B=5, N=100).facteur_khi2 == pytest.approx(49 / 45)


def test_bandes_binomiales_exactes():
    """L'approximation normale placait la borne basse corrigee a 154,2 et
    gardait le 22 (155 sorties) dans la bande ; la loi exacte la place a 156."""
    t = Tirage(K=50, B=5, N=1981)
    assert t.bande(0.05) == (172, 225)
    assert t.bande(0.05, comparaisons=50) == (156, 243)
    assert t.p_individuelle(155) < 0.05 / 50 < t.p_individuelle(156)


def test_plus_longues_absences():
    presence = np.array([[0, 1], [0, 0], [1, 0], [0, 0], [0, 1]], dtype=bool)
    # colonne 0 : 2 tirages avant la sortie, 2 apres ; colonne 1 : 3 entre deux sorties
    assert plus_longues_absences(presence).tolist() == [2, 3]


def test_ecarts_maximaux_absence_en_cours():
    """3 tirages de 2 boules parmi 3 numeros : le 3 ne sort jamais, le 1
    sort au dernier tirage."""
    df = pd.DataFrame({"b1": [1, 2, 2], "b2": [2, 1, 1]})
    res = ecarts_maximaux(df, ["b1", "b2"], maximum=3)
    assert res.loc[3, "absence_max"] == 3 and res.loc[3, "absence_finale"] == 3
    assert res.loc[1, "absence_finale"] == 0
    assert res.loc[2, "absence_max"] == 0


def test_ecarts_maximaux_refuse_un_tirage_incomplet():
    df = pd.DataFrame({"b1": [1, 2], "b2": [2, None]})
    with pytest.raises(ValueError, match="incomplet"):
        ecarts_maximaux(df, ["b1", "b2"], maximum=3)


# --------------------------------------------------------------------------
# Troisieme relecture : cas limites de l'ingestion et tests ajoutes
# --------------------------------------------------------------------------


def test_archive_sans_donnees_refusee(tmp_path):
    chemin = tmp_path / "archive.csv"
    chemin.write_bytes(b"a;b\n")
    with pytest.raises(ValueError, match="sans aucune ligne"):
        ingest.lire_archive(chemin)


def test_champ_en_trop_sur_une_ligne_tardive_refuse(tmp_path):
    """Un champ en trop sur la 2e ligne seulement etait perdu en silence :
    le nombre de champs n'etait lu que sur la premiere."""
    chemin = tmp_path / "archive.csv"
    chemin.write_bytes(b"a;b\n1;2\n3;4;X\n")
    with pytest.raises(ValueError, match="surnumeraire"):
        ingest.lire_archive(chemin)


def test_borne_basse_controlee():
    tirages = pd.DataFrame({
        "date_tirage": [pd.Timestamp("2020-01-03")],
        "regime": [regles.REGIMES[-1].nom], "jour": ["VENDREDI"],
        **{c: [v] for c, v in zip(ingest.COLONNES_BOULES, [0, 2, 3, 4, 5])},
        **{c: [v] for c, v in zip(ingest.COLONNES_ETOILES, [1, 2])},
        **{c: [np.nan] for c in ingest.COLONNES_GAGNANTS},
    })
    assert "HORS BORNES" in "\n".join(ingest.controler_regimes(tirages))


def test_recence_des_archives_par_date_et_non_par_nom(tmp_path):
    """'z_ancienne.csv' vient apres 'a_recente.csv' dans l'ordre alphabetique,
    mais son dernier tirage est plus ancien : elle doit passer avant."""
    (tmp_path / "z_ancienne.csv").write_bytes(b"date_de_tirage;x\n03/01/2020;1\n")
    (tmp_path / "a_recente.csv").write_bytes(b"date_de_tirage;x\n03/01/2020;1\n07/01/2020;1\n")
    brut, _ = ingest.charger_toutes(tmp_path)
    ordre = brut.groupby("_source")["_ordre"].first()
    assert ordre["a_recente.csv"] > ordre["z_ancienne.csv"]


def test_esperance_exacte_hors_bande():
    """La bande « a 95 % » exacte laisse sortir 4,3 % des numeros, pas 5 %."""
    t = Tirage(K=50, B=5, N=1981)
    assert 50 * t.part_hors_bande(t.bande(0.05)) == pytest.approx(2.154, abs=1e-3)


def test_chaque_marche_de_la_dose_est_etablie():
    import analyse
    _, pop = analyse.popularite(analyse.charger())
    assert pop["dose_effet"]
    assert all(m["etablie"] for f in pop["familles"].values() for m in f["marches"])
