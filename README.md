# EuroMillions — ce que les statistiques disent vraiment

Pipeline d'ingestion et d'analyse statistique des tirages EuroMillions publiés
par la Française des Jeux, de février 2004 à septembre 2026.

**La conclusion d'abord : il n'existe pas de « numéros chauds ».** Ce projet
construit l'outil qui permettrait de les trouver, puis démontre khi-deux à
l'appui qu'ils n'existent pas. La FDJ le dit elle-même sur ses propres pages :
il n'est pas possible de déterminer des probabilités fiables sur les tirages.

**Page publiée : [cadgeff.github.io/euromillions-analyse](https://cadgeff.github.io/euromillions-analyse)**

## Ce que contient le dépôt

| Chemin | Rôle |
|---|---|
| `src/ingest.py` | Ingestion, normalisation et contrôles qualité |
| `src/analyse.py` | Test du khi-deux, absences, popularité des grilles |
| `src/site.py` | Génération de la page web à partir des données |
| `src/gabarit_site.html` | Gabarit de la page : mise en page et tracé des graphiques |
| `data/raw/` | Archives CSV brutes de la FDJ, telles que téléchargées |
| `data/processed/euromillions_tirages.csv` | 1 981 tirages, une ligne chacun |
| `data/processed/euromillions_boules.csv` | Format long : une ligne par boule tirée |
| `data/processed/frequences_boules.csv` | Sorties, écart à l'attendu et absences par numéro |
| `data/processed/rapport_qualite.txt` | Rapport d'anomalies, régénéré à chaque exécution |
| `data/processed/statistiques.txt` | Résultats des tests, régénérés à chaque exécution |
| `docs/index.html` | Page publiée, générée — ne pas modifier à la main |

## Lancer le projet

```bash
pip install pandas scipy
python src/ingest.py     # archives brutes -> données normalisées
python src/analyse.py    # données normalisées -> résultats statistiques
python src/site.py       # données normalisées -> docs/index.html
```

Les trois scripts s'enchaînent dans cet ordre et sont rejouables à volonté :
ajouter une archive dans `data/raw/` et relancer suffit à tout mettre à jour,
page web comprise. Aucune configuration, aucune clé d'API, aucun état caché.

## Le vrai travail : normaliser six archives hétérogènes

Les six archives couvrent 22 ans et le format a changé quatre fois. Rien de
tout cela n'est documenté par la FDJ ; chaque écart a dû être trouvé.

**Deux encodages réels, et un piège de détection.** Quatre archives sont en
UTF-8 — dont trois en ASCII pur, qui en est un sous-ensemble strict — et deux
en `cp1252`. Aucune n'a de BOM.

La détection naïve, qui essaie une liste d'encodages et retient le premier qui
ne lève pas d'erreur, donne ici un résultat exact mais trompeur : un fichier
ASCII se décode sans broncher en `utf-8`, en `utf-8-sig` **et** en `cp1252`,
si bien que le premier candidat testé gagne toujours. Étiqueter `utf-8-sig` un
fichier dépourvu de BOM revient à documenter un fait qui n'existe pas.

La détection va donc du plus spécifique au plus permissif : BOM, puis absence
d'octet au-delà de 127, puis décodage UTF-8, et seulement ensuite `cp1252`.
`latin-1` ferme la marche en dernier recours — il accepte n'importe quelle
séquence d'octets, donc il ne prouve jamais rien.

**Trois formats de date.** `20110506`, `31/01/2014` et `23/09/16`. Le dernier
est ambigu par nature : le siècle est choisi explicitement, EuroMillions ayant
démarré en 2004.

**Des libellés instables.** Le jour de tirage s'écrit `VE`, `VENDREDI` ou
`MARDI   ` avec des espaces de remplissage. Les noms de colonnes gagnent un
suffixe `_Euro_Millions` en 2019 avec l'arrivée d'Étoile+, et une colonne
`numéro_de_tirage_dans_le_cycle` apparaît en cours de route.

### Le piège du séparateur final

Le défaut le plus intéressant du lot, parce qu'il est invisible.

Dans `euromillions_4.csv`, les lignes de données se terminent par un `;` que la
ligne d'en-tête n'a pas : **75 noms de colonnes face à 76 champs**, le 76ᵉ
toujours vide. Aucune donnée ne manque, et un tableur n'y voit rien d'anormal.

Mais pandas, trouvant plus de champs que de noms, applique une convention
ancienne : il suppose une première colonne d'index sans nom, la retire des
données et **décale tout le reste d'un cran, sans le moindre avertissement**.

| Colonne | Lecture naïve | Après correction |
|---|---|---|
| `date_de_tirage` | `1` (le numéro de cycle) | `27/09/2016` |
| `boule_1` | `6` (une autre boule) | `41` |

Conséquence sur les 253 tirages du fichier : dates illisibles, et des tirages
qui semblent contenir deux fois la même boule — alors qu'il s'agit de deux
colonnes voisines ramenées à la même position. La correction consiste à
construire soi-même la liste des noms et à forcer `index_col=False`.

C'est précisément le genre de défaut qu'un chargement naïf avale sans broncher
et qui fausse ensuite toute l'analyse en aval.

## Les contrôles qualité

Le rapport est régénéré à chaque exécution et vérifie :

- dates illisibles, doublons, tirages incomplets ;
- boules ou étoiles répétées dans un même tirage ;
- bornes observées confrontées aux règles attendues de chaque régime ;
- volume annuel de tirages, avec un seuil adapté au rythme de l'époque ;
- trous dans la série chronologique.

État actuel : **1 981 tirages, aucune anomalie résiduelle.**

## Les trois régimes de jeu

Les règles ont changé deux fois. Les périodes ne sont donc pas comparables
entre elles : le dénominateur change, et toute statistique globale mélangeant
les trois est fausse par construction.

| Période | Boules | Étoiles | Tirages |
|---|---|---|---|
| 13/02/2004 → 10/05/2011 | 1–50 | 1–9 | 378 |
| 10/05/2011 → 24/09/2016 | 1–50 | 1–11 | 562 |
| depuis le 24/09/2016 | 1–50 | 1–12 | 1 041 |

Le rythme change lui aussi : un tirage par semaine jusqu'en mai 2011, deux
ensuite. Ces bornes ne sont pas supposées mais vérifiées contre les données.

## Les résultats

Le détail complet est dans `data/processed/statistiques.txt`, et la
démonstration visuelle sur la page publiée.

**Les fréquences ne révèlent rien.** Sur 9 905 boules tirées, chaque numéro
devrait sortir 198,1 fois. Le record est partagé par le 42 et le 44 avec 224
sorties, le dernier est le 22 avec 155 — 69 d'écart, de quoi nourrir n'importe
quel site de « numéros chauds ». Le test du khi-deux donne pourtant 52,90 pour
un seuil critique de 66,34 (p = 0,326) : l'écart est très en deçà de ce qu'un
tirage parfaitement équilibré produit de lui-même.

**Un seul numéro sort de la bande de variation à 95 %, alors que 2,5 étaient
attendus par pur hasard** — donc moins que prévu. Avec la bande corrigée pour
les 50 comparaisons simultanées, aucun n'en sort.

**Les absences prolongées sont la norme.** Le record appartient au 16, resté
87 tirages sans sortir ; l'absence maximale médiane est de 54 tirages. En
observer une longue n'a donc rien d'exceptionnel.

**Un test sur quatre ressort pourtant « significatif »** — les étoiles de
2011-2016, à p = 0,049. Il ne prouve rien, et le rapport explique pourquoi :
le seuil de 5 % est une convention, pas une frontière, et en enchaînant quatre
tests la probabilité d'en voir au moins un franchir la barre par accident
avoisine 19 %. La correction de Bonferroni ramène le seuil à 0,0125, que ce
résultat ne franchit pas. C'est exactement ainsi que naissent les fausses
découvertes.

**Le seul levier réel ne porte pas sur la probabilité de gagner** mais sur le
montant du gain. Le jackpot étant partagé entre tous les gagnants, et les
joueurs choisissant massivement des dates de naissance (donc 1 à 31), une
grille contenant des numéros supérieurs à 31 a la même probabilité de sortir
mais serait partagée avec moins de monde. Les données confirment que les
boules, elles, ignorent cette zone : 3,12 boules ≤ 31 par tirage en moyenne,
pour 3,10 attendues.

## Licence et source

Données publiques publiées par la Française des Jeux. Ce projet est un travail
d'analyse statistique à but pédagogique. Il ne propose aucune méthode de jeu et
n'est affilié à aucun opérateur.
