# EuroMillions — ce que les statistiques disent vraiment

Pipeline d'ingestion et d'analyse statistique des tirages EuroMillions publiés
par la Française des Jeux, du **13 février 2004 au 15 septembre 2026** inclus,
soit 1 981 tirages.

**La conclusion d'abord : il n'existe pas de « numéros chauds ».** Ce projet
construit l'outil qui permettrait de les trouver, puis démontre, khi-deux et
simulations à l'appui, qu'aucun écart ne résiste à la correction pour tests
multiples. Le seul levier réel ne porte pas sur la probabilité de gagner mais
sur le montant du gain, et il est mesuré directement dans les gains versés.

**Page publiée : [cadgeff.github.io/euromillions-analyse](https://cadgeff.github.io/euromillions-analyse)**

## Ce que contient le dépôt

| Chemin | Rôle |
|---|---|
| `src/regles.py` | Règles du jeu : régimes, probabilités, correspondance rang → combinaison |
| `src/ingest.py` | Ingestion, normalisation et contrôles qualité |
| `src/modele.py` | Modèle probabiliste du tirage, vérifié par simulation |
| `src/analyse.py` | Tests d'ajustement, numéro extrême, absences, popularité des grilles |
| `src/site.py` | Génération de la page web à partir des résultats de l'analyse |
| `src/gabarit_site.html` | Gabarit de la page : mise en page et tracé des graphiques |
| `tests/` | Un test par piège documenté ci-dessous |
| `data/raw/` | Archives CSV brutes de la FDJ, telles que téléchargées |
| `data/processed/euromillions_tirages.csv` | 1 981 tirages, une ligne chacun, gagnants et gains par combinaison |
| `data/processed/euromillions_boules.csv` | Format long : une ligne par boule tirée |
| `data/processed/frequences_boules.csv` | Sorties, écart à l'attendu, p-value exacte et absences, par numéro |
| `data/processed/popularite_gagnants.csv` | Effet des petits numéros sur les gagnants et les gains, par combinaison |
| `data/processed/rapport_qualite.txt` | Rapport d'anomalies, régénéré à chaque exécution |
| `data/processed/statistiques.txt` | Résultats des tests, régénérés à chaque exécution |
| `docs/index.html` | Page publiée, générée — ne pas modifier à la main |

## Lancer le projet

```bash
pip install -r requirements.txt
python src/ingest.py     # archives brutes -> données normalisées
python src/analyse.py    # données normalisées -> résultats statistiques
python src/site.py       # résultats -> docs/index.html
python -m pytest         # les tests
```

Les trois scripts s'enchaînent dans cet ordre et sont rejouables à volonté :
ajouter une archive dans `data/raw/` et relancer suffit à tout mettre à jour,
page web comprise. Aucune configuration, aucune clé d'API, aucun état caché.
Les simulations utilisent des graines fixes et la page ne dépend que des
données : relancer sans nouvelle archive redonne des fichiers identiques,
octet pour octet.

Vérifié avec Python 3.11, d'une part avec pandas 3.0, numpy 2.4 et scipy 1.17,
d'autre part avec les versions minimales de `requirements.txt` (pandas 2.1,
numpy 1.26, scipy 1.11) : les sorties sont identiques octet pour octet.

### Fraîcheur des données

Le jeu de données est un **instantané**, arrêté au tirage du 15 septembre 2026.
EuroMillions tirant deux fois par semaine, le mardi et le vendredi, il prend du
retard dès le tirage suivant. Tous les chiffres cités dans ce README et sur la
page décrivent donc cette période close, et non l'état du jeu au jour où vous
les lisez.

Mettre à jour se fait en trois gestes : télécharger l'archive courante depuis
les [pages historique de la FDJ](https://www.fdj.fr/jeux-de-tirage/euromillions-my-million/historique),
déposer le CSV dans `data/raw/`, relancer les trois scripts. Les contrôles
qualité signaleront tout changement de format, et la déduplication se charge du
recouvrement entre archives.

## Le vrai travail : normaliser six archives hétérogènes

Les six archives couvrent 22 ans. Elles se répartissent en **trois schémas de
colonnes** : 51 colonnes nommées (2004-2011), 54 (2011-2016, deux archives qui
ne diffèrent que par le nom d'une colonne, `numero_jokerplus` devenu
`numero_My_Million`) et 75 (depuis septembre 2016, trois archives dont une
écrit `Euro_Million` sans « s » dans un nom de colonne). Rien de tout cela
n'est documenté par la FDJ ; chaque écart a dû être trouvé.

**Des encodages, et ce qu'on peut en prouver.** Trois archives sont en ASCII
pur, une en UTF-8, aucune n'a de BOM. Les deux dernières ne contiennent qu'un
seul caractère non ASCII, le « é » de `numéro`, codé `0xE9` : cp1252 et
latin-1 le lisent de la même façon, et rien ne permet de les départager.
Le rapport les étiquette donc « 8 bits occidental (cp1252 ou latin-1,
indiscernables) » plutôt que de revendiquer un encodage qu'il n'a pas prouvé.

La détection naïve, qui essaie une liste d'encodages et retient le premier qui
ne lève pas d'erreur, donne ici des étiquettes trompeuses : un fichier ASCII se
décode sans broncher en `utf-8`, en `utf-8-sig` **et** en `cp1252`, si bien que
le premier candidat testé gagne toujours. La détection va donc du plus
spécifique au plus permissif — BOM, absence d'octet au-delà de 127, UTF-8 —
et ne conclut à cp1252 que si un octet entre `0x80` et `0x9F` le prouve : ces
octets sont des caractères en cp1252 et des codes de contrôle en latin-1.

**Trois formats de date.** `20110506`, `31/01/2014` et `23/09/16`. Le dernier
est ambigu par nature : le siècle est choisi explicitement, EuroMillions ayant
démarré en 2004, et le contrôle par redondance le confirme (voir plus bas).

**Des libellés instables.** Le jour de tirage s'écrit `VE`, `VENDREDI` ou
`MARDI   ` avec des espaces de remplissage. Les noms de colonnes gagnent un
suffixe `_Euro_Millions` en septembre 2016, en même temps qu'apparaissent les
colonnes Étoile+ et `numéro_de_tirage_dans_le_cycle`.

### Des gagnants absents déguisés en zéros

Le tirage du 31 janvier 2020 affiche **zéro gagnant à tous les rangs** dans les
colonnes européennes, alors que les colonnes françaises sont remplies. C'est
impossible : le dernier rang compte des centaines de milliers de gagnants à
chaque tirage. Les chiffres européens n'ont simplement pas été publiés pour ce
tirage-là.

Lu tel quel, ce zéro entre dans les moyennes et les corrélations comme une
observation légitime. L'ingestion le reconnaît — tous les rangs à zéro alors
que la France en compte — et marque la ligne comme manquante plutôt que nulle.
Le rapport qualité la signale nommément.

### Le piège du séparateur final

Le défaut le plus intéressant du lot, parce qu'il est invisible.

Toutes les archives terminent leurs lignes de données par un `;`, ce qui ajoute
un dernier champ vide. Cinq d'entre elles terminent aussi leur ligne d'en-tête
par un `;` : ce champ vide y a un nom vide, et tout reste aligné. Une seule,
`euromillions_4.csv`, a un en-tête **sans** ce `;` final : **75 noms de
colonnes face à 76 champs**, le 76ᵉ toujours vide. Aucune donnée ne manque, et
un tableur n'y voit rien d'anormal.

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
construire soi-même la liste des noms, à forcer `index_col=False`, puis à
vérifier que les champs surnuméraires sont bien vides avant de les retirer :
s'ils ne l'étaient pas, l'ingestion s'arrête plutôt que de lire des données
décalées.

## Le piège des rangs

Le plus grave, parce qu'il faussait des résultats publiés sans qu'aucun
contrôle ne le voie.

Les archives donnent le nombre de gagnants « au rang 6 », « au rang 7 », etc.
Le projet traitait chaque numéro de rang comme une combinaison fixe — le rang 6
comme 3 bons numéros et 2 étoiles, le rang 7 comme 4 numéros et 0 étoile — sur
tout l'historique. C'est faux. **La FDJ numérote les rangs de la combinaison la
plus rare à la plus fréquente**, et changer le nombre d'étoiles change les
probabilités, donc l'ordre :

| Rang | 2004-2011 (9 étoiles) | 2011-2016 (11 étoiles) | depuis 2016 (12 étoiles) |
|---|---|---|---|
| 6 | **4+0** (1 sur 16 143) | **4+0** (1 sur 14 387) | 3+2 (1 sur 14 125) |
| 7 | **3+2** (1 sur 7 705) | **3+2** (1 sur 11 771) | 4+0 (1 sur 13 811) |
| 8 | **3+1** (1 sur 550) | 2+2 (1 sur 821) | 2+2 (1 sur 985) |
| 9 | **2+2** (1 sur 538) | 3+1 (1 sur 654) | 3+1 (1 sur 706) |

Lire « rang 6 » comme une seule et même chose mélangeait donc deux combinaisons
sur 940 tirages, soit 47 % de l'historique, et les rangs 8 et 9 sur les 378
tirages d'avant 2011.

Les rangs ne sont plus recopiés dans une table : `regles.py` les **dérive des
probabilités** de chaque régime, et les colonnes de gagnants sont désormais
nommées par combinaison (`gagnants_3b_0e`) et non par rang. L'ingestion vérifie
ensuite cette dérivation contre les gagnants publiés, par deux contrôles
indépendants :

- **le volume implicite.** Le nombre moyen de gagnants d'une combinaison,
  divisé par sa probabilité, doit redonner le même volume de grilles pour
  toutes les combinaisons d'un régime. Avec la bonne correspondance, tous les
  écarts restent sous 7 % ; avec l'ancienne, le rang 7 d'avant 2011 s'écartait
  de +110 %.
- **la signature des étoiles.** Quand deux combinaisons sont presque aussi
  probables l'une que l'autre (2+2 et 3+1 avant 2011 : 1 sur 538 contre 1 sur
  550), le volume ne les départage pas. Mais une combinaison qui exige deux
  étoiles dépend fortement de la popularité de la paire d'étoiles tirée, et
  les autres non. Avant 2011, le « rang 9 » y est corrélé à 0,87 et le « rang 8 »
  à 0,10 : le rang 9 était bien 2+2.

Appliqués à l'ancienne correspondance, ces contrôles relèvent 7 anomalies ; à
la nouvelle, aucune. Un test vérifie qu'ils continuent de refuser l'ancienne.

## Le modèle probabiliste, et les erreurs qu'il corrige

Cette partie du projet existe parce qu'une relecture externe y a trouvé une
faute. Elle est documentée plutôt que corrigée en silence : c'est l'erreur la
plus instructive du lot.

**La première version modélisait les sorties comme 5 N tirages indépendants
d'une boule parmi 50**, soit une loi B(5 N, 1/50). C'est faux. Un tirage
EuroMillions sort **cinq boules distinctes** : celles d'un même tirage ne sont
pas indépendantes, et cette dépendance réduit la variance.

Le modèle correct est plus simple : à chaque tirage, un numéro sort ou ne sort
pas, avec p = 5/50. Sur N tirages, ses sorties suivent B(N, 1/10).

| | σ |
|---|---|
| Simulation du tirage réel | **13,334** |
| Modèle erroné B(5 N, 1/50) | 13,933 |
| Modèle correct B(N, 1/10) | 13,353 |

Les deux modèles donnent **la même espérance**, ce qui rend l'erreur invisible
à l'œil : seule la variance diffère. Les bandes de variation publiées étaient
4,3 % trop larges.

### La même erreur affectait le test

La statistique de Pearson ne suit pas χ²(K−1) dans ce cadre. La covariance
entre les effectifs de deux numéros vaut −N p(1−p)/(K−1) : la matrice de
covariance est celle d'une loi multinomiale multipliée par (K−B)/(K−1). La
statistique suit donc, pour N grand, (K−B)/(K−1) × χ²(K−1) — pas seulement en
moyenne, en loi. La mettre à l'échelle par (K−1)/(K−B) la ramène exactement
sur χ²(K−1). La simulation le confirme : moyenne **44,90** pour une valeur
théorique de K − B = 45, variance 84,5 pour 82,7.

Le test était **conservateur** : il rejetait moins qu'il n'aurait dû.

| Test | p publié initialement | p corrigé | p par simulation |
|---|---|---|---|
| Boules 1–50 | 0,326 | 0,187 | 0,185 |
| Étoiles 2004–2011 | 0,716 | 0,631 | 0,624 |
| Étoiles 2011–2016 | 0,049 | **0,026** | 0,029 |
| Étoiles 2016+ | 0,237 | 0,168 | 0,164 |

Aucune conclusion ne change — mais tous les chiffres affichés étaient faux, et
le cas limite des étoiles 2011-2016 devient *plus* significatif, pas moins.

### Les bandes de variation : la loi exacte, pas la loi normale

Les bandes par numéro étaient calculées avec l'approximation normale de la loi
binomiale. Elle est bonne au centre et fausse en queue, précisément là où la
bande sert : B(N, 1/10) est asymétrique, sa queue basse plus courte que ne le
suppose la loi normale. Avec la correction pour 50 comparaisons, la loi normale
plaçait la borne basse à 154,2 sorties ; la loi exacte la place à 156. Le 22,
avec 155 sorties, était dit dans la bande ; il en sort, de justesse.

`modele.py` ne se contente pas d'appliquer ces formules : sa fonction
`verifier()` les confronte à une simulation du tirage réel à chaque exécution.
C'est ce qui a permis de trancher, et ce qui signalera la prochaine erreur de
modèle.

## Les contrôles qualité

Le rapport est régénéré à chaque exécution et vérifie :

- encodage, colonnes nommées et séparateur final de chaque archive ;
- dates illisibles, doublons, tirages incomplets ;
- chiffres de gagnants européens non publiés, marqués manquants et non nuls ;
- boules ou étoiles répétées dans un même tirage ;
- bornes observées confrontées aux règles de chaque régime, et dates de
  bascule confrontées aux indices que donnent les données ;
- correspondance rang → combinaison, par volume implicite et signature des étoiles ;
- volume annuel de tirages, avec un seuil adapté au rythme de l'époque ;
- trous dans la série chronologique ;
- redondance interne : les boules relues doivent redonner la chaîne
  `boules_gagnantes_en_ordre_croissant`, et le jour calculé le jour annoncé.

État actuel : **1 981 tirages, aucune anomalie résiduelle.**

## Les trois régimes de jeu

Les règles ont changé deux fois. Les périodes ne sont donc pas comparables
entre elles : le dénominateur change, et toute statistique globale mélangeant
les trois est fausse par construction.

| Période | Boules | Étoiles | Rangs | Tirages |
|---|---|---|---|---|
| 13/02/2004 → 06/05/2011 | 1–50 | 1–9 | 12 | 378 |
| 10/05/2011 → 23/09/2016 | 1–50 | 1–11 | 13 | 562 |
| depuis le 27/09/2016 | 1–50 | 1–12 | 13 | 1 041 |

Le rythme change lui aussi : un tirage par semaine, le vendredi, jusqu'en mai
2011, puis deux, le mardi et le vendredi.

Vérifier que les étoiles restent sous le maximum de chaque régime ne contrôle
qu'un sens : un changement placé trop tôt passerait inaperçu, puisqu'un tirage à
9 étoiles reste compatible avec un régime à 11. Le rapport cherche donc aussi
les indices de chaque bascule dans les données. En 2016, l'étoile 12 sort dès
le premier tirage du nouveau régime, le 27/09. En 2011, la première étoile 10
ou 11 n'apparaît que le 20/05, au quatrième tirage ; la date du 10/05 est
établie autrement : c'est le premier tirage du mardi et le premier à publier
des gagnants 2+0.

## Les résultats

Le détail complet est dans `data/processed/statistiques.txt`, et la
démonstration visuelle sur la page publiée.

**Les fréquences ne révèlent rien.** Sur 9 905 boules tirées, chaque numéro
devrait sortir 198,1 fois. Le record est partagé par le 42 et le 44 avec 224
sorties, le dernier est le 22 avec 155 — 69 d'écart, de quoi nourrir n'importe
quel site de « numéros chauds ». Mais dans un historique équilibré simulé, le
numéro de tête atteint au moins 224 sorties dans 82 % des cas.

La statistique de Pearson vaut 52,90, soit 57,60 une fois mise à l'échelle,
pour un seuil critique de 66,34 (p = 0,187, confirmé à 0,185 par simulation).
C'est un peu au-dessus de la valeur moyenne sous le hasard (49), au 81ᵉ
centile : des écarts un peu plus marqués que d'ordinaire, sans rien d'anormal.

**Le numéro le plus atypique est un numéro froid.** Un seul numéro sort de la
bande de variation à 95 % (172 à 225 sorties), alors que 2,5 étaient attendus
par pur hasard : le 22. Il sort aussi, de justesse, de la bande corrigée pour
50 comparaisons (156 à 243). La bonne question est alors : dans un historique
équilibré, à quelle fréquence le plus atypique des 50 numéros l'est-il au moins
autant ? Réponse par simulation : **p = 0,044**. C'est sous 5 %, et c'est le
test qui compte le plus ici ; il ne survit pourtant pas à la correction pour
tests multiples (voir plus bas).

**Les absences prolongées sont la norme.** Le record appartient au 16, resté
87 tirages sans sortir. Dans 2 000 historiques équilibrés simulés, le record
vaut 90 tirages en médiane (74 à 124 dans 95 % des cas), et 87 est atteint ou
dépassé dans 65 % d'entre eux. L'absence maximale médiane par numéro, 54
tirages, est celle qu'on attend (53, entre 50 et 57). En observer une longue n'a
donc rien d'exceptionnel.

**Deux tests sur cinq ressortent pourtant « significatifs »** — le numéro le
plus atypique (p = 0,044) et les étoiles de 2011-2016 (p = 0,026). Ils ne
prouvent rien, et le rapport explique pourquoi : le seuil de 5 % est une
convention, pas une frontière, et en enchaînant cinq tests la probabilité d'en
voir au moins un franchir la barre par accident avoisine 23 %. La correction
de Bonferroni ramène le seuil à 0,01, qu'aucun des deux ne franchit. C'est
exactement ainsi que naissent les fausses découvertes.

### Le seul levier réel : le montant du gain

Les boules ignorent la zone des dates de naissance : 3,12 boules ≤ 31 par
tirage en moyenne, pour 3,10 attendues. Restait à montrer que les *joueurs*,
eux, ne l'ignorent pas — et que cela se paie.

Pour une combinaison donnée, le nombre de gagnants vaut approximativement
(grilles jouées) × (probabilité qu'une grille corresponde), et ce second
facteur dépend des numéros que les joueurs cochent. Le gain versé à chaque
grille gagnante vaut, lui, (cagnotte du rang) ÷ (gagnants). Pour 3 bons
numéros et aucune étoile :

| Boules ≤ 31 dans le tirage | Gagnants (médiane) | Gain par grille (médiane) | Tirages |
|---|---|---|---|
| 0 | 78 046 | 16,00 € | 11 |
| 1 | 69 472 | 14,35 € | 82 |
| 2 | 73 053 | 13,00 € | 429 |
| 3 | 80 841 | 12,00 € | 755 |
| 4 | 91 178 | 10,90 € | 544 |
| 5 | **107 815** | **9,10 €** | 160 |

Spearman ρ = +0,290 pour les gagnants (p ≈ 10⁻³⁹), −0,385 pour le gain
(p ≈ 10⁻⁷⁰). Comparé à un tirage typique — 3 boules ≤ 31, le cas le plus
fréquent et le plus proche de la moyenne —, un tirage entièrement composé de
« dates de naissance » produit **33 % de gagnants de plus**, et chacun touche
**24 % de moins**. La ligne à 0 boule ne repose que sur 11 tirages et ne se lit
pas seule.

Les tirages sans aucun gagnant à une combinaison sont **conservés** dans les
corrélations. Les écarter reviendrait à conditionner sur la variable
expliquée : au jackpot, remporté dans moins d'un tirage sur quatre, ne garder
que les tirages gagnés sélectionnerait précisément les combinaisons les plus
jouées. Un zéro est une observation, pas un défaut.

**La preuve par la dose.** Si l'effet vient des numéros cochés, sa taille doit
croître avec le nombre de boules principales qu'exige la combinaison. On la
mesure par la variation du nombre de gagnants pour chaque boule ≤ 31 de plus
dans le tirage (pente log-linéaire, avec un niveau propre à chaque régime) :

| Boules exigées | 0 étoile | 1 étoile | 2 étoiles |
|---|---|---|---|
| 1 | — | — | **+1,1 %** [−0,6 ; +2,9] (témoin) |
| 2 | +6,4 % | +6,1 % | +5,5 % |
| 3 | +10,9 % | +10,8 % | +10,0 % |
| 4 | +15,4 % | +15,4 % | +14,4 % |
| 5 | +18,7 % | non mesurable | non mesurable |

L'effet croît à chaque boule exigée, et le nombre d'étoiles n'y change presque
rien. La tendance d'ensemble est nette ; les dernières marches, prises une à
une, le sont moins : sans étoile, les intervalles à 4 et 5 boules se
chevauchent, et avec deux étoiles ceux à 3 et 4 boules se touchent. La seule
combinaison qui n'exige qu'une boule, 1+2, sert de témoin : son effet ne se
distingue pas de zéro. Le gain par grille suit exactement le même
schéma en sens inverse : −6,6 %, −10,5 %, −13,9 % et −17,7 % par petite boule
pour 2, 3, 4 et 5 bons numéros sans étoile. 5+1 et 5+2 ne sont pas mesurés : 6 %
et 77 % de leurs tirages n'ont aucun gagnant, et une pente calculée sur les
seuls tirages gagnés serait biaisée.

Une grille contenant des numéros supérieurs à 31 a donc exactement la même
probabilité de gagner, mais quand elle gagne, elle partage avec moins de monde.
Pour le jackpot, trop rarement gagné pour être mesuré ainsi, l'effet est
extrapolé, pas observé.

La FDJ écrit sur [sa page de statistiques](https://www.fdj.fr/jeux-de-tirage/euromillions-my-million/statistiques)
qu'« il n'est pas possible de déterminer des probabilités fiables sur les
tirages ». Il faut l'entendre comme « on ne peut pas prévoir le prochain
tirage » : les probabilités elles-mêmes sont parfaitement connues, et ce projet
les calcule.

## Historique des corrections

Les erreurs trouvées en relecture sont documentées ici plutôt que corrigées en
silence.

**Septembre 2026, deuxième relecture.**

- Rangs 6/7 et 8/9 lus comme des combinaisons fixes alors que leur sens change
  avec le régime (voir « Le piège des rangs »). Les chiffres de popularité des
  rangs concernés étaient faux : ρ valait 0,245 pour 4+0 et 0,288 pour 3+2,
  contre 0,353 et 0,189 une fois corrigé.
- Bandes par numéro calculées en loi normale : le 22 était dit dans la bande
  corrigée, il en sort. Le numéro le plus atypique a désormais son propre test,
  inclus dans la correction pour tests multiples (cinq tests au lieu de quatre).
- Témoin mesuré par des corrélations de rang, qui mêlent taille de l'effet et
  bruit ; le README n'en montrait que quatre sur treize, et le tableau complet
  n'était pas monotone. Remplacé par la taille de l'effet, comparée à nombre
  d'étoiles égal.
- « 55 % de gagnants de plus » : comparaison faite au point le plus bas de la
  courbe (un tirage à une seule petite boule), sans le dire. Rapportée
  désormais au tirage typique (+33 %), et complétée par le gain réellement versé.
- Absences prolongées présentées sans référence : « ce que le hasard produit »
  était en fait ce que les données montraient. Confrontées désormais à une
  simulation.
- « Très en deçà de ce qu'un tirage équilibré produit » : faux, la statistique
  est au-dessus de sa moyenne sous le hasard (81ᵉ centile), simplement sous le
  seuil.
- Conclusions écrites en dur dans la page (« aucun numéro n'en sort »,
  « moins que prévu », tuile « Aucun ») : elles sont maintenant calculées.
- Faits d'archives inexacts : le suffixe `_Euro_Millions` date de 2016 et non
  de 2019 ; les « quatre formats à 52, 55, 75 et 76 colonnes » comptaient une
  colonne vide ; l'encodage « cp1252 » de deux archives n'était pas démontrable.
- Déduplication dépendante d'un tri non stable, page non reproductible (date du
  jour inscrite dedans) : corrigés.

## Licence et source

Données publiques publiées par la Française des Jeux. Ce projet est un travail
d'analyse statistique à but pédagogique. Il ne propose aucune méthode de jeu et
n'est affilié à aucun opérateur.
