# WiserLink MPI pour Home Assistant

<p align="center">
  <img src="https://raw.githubusercontent.com/jptstar/wiserlink-mpi/main/custom_components/wiserlink_mpi/brand/icon@2x.png" alt="Logo WiserLink MPI" width="220">
</p>

Intégration locale pour WiserLink MPI / Wiser Energy EER31600 et EER39300.

> Intégration communautaire non officielle, sans affiliation avec Schneider Electric.

[![Ouvrir HACS dans Home Assistant](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=jptstar&repository=wiserlink-mpi&category=integration)

## À propos de ce projet

J’ai initialement développé cette intégration par plaisir et pour ma propre installation Home Assistant. Comme de nombreux propriétaires rencontrent des difficultés pour accéder localement aux données de leur WiserLink MPI, je la mets à disposition afin que d’autres puissent également en profiter.

## Auteur

**Jean-Philippe TESTART** ([jptstar](https://github.com/jptstar))

## Fonctions

- lecture locale de `/vesta/UsageMeter` ;
- détection des compteurs à partir de `Type`, `Name`, `Unit_Power` et `Unit_Energy` ;
- CT1 à CT5 reconnus à partir de `Load1` à `Load5` ;
- détection séparée de `Others`, du compteur électrique/TIC et des compteurs de volume ;
- gaz/eau reconnus comme volumes en `m³` sans supposer un index fixe ;
- activation ou désactivation individuelle de chaque entrée détectée ;
- noms préremplis depuis l’API et personnalisables ;
- capteurs de puissance, énergie ou volume créés selon les unités réellement renvoyées ;
- attributs `api_index`, `api_type`, `api_name` et unités brutes pour vérifier facilement la correspondance physique ;
- intervalle d’actualisation réglable de 2 à 300 secondes ;
- seuil de reconnexion réglable de 1 à 20 tentatives ;
- conservation des dernières valeurs pendant les tentatives ;
- rejet des valeurs reçues lors d’une erreur ou d’une réponse corrompue ;
- protection contre les valeurs 32 bits aberrantes autour de `0x7fffffff` / `0x80000000`, y compris lors de la première lecture après un redémarrage ;
- entité **MPI Online** classée dans les diagnostics ;
- état EM5 et communications avec le MIP et le compteur électrique ;
- numéros de série et versions logicielles MIP, EM5 et MPR ;
- batterie et communication de chaque compteur MPR ;
- dernier événement affiché directement, avec l’historique récent en attribut ;
- configuration et suppression des compteurs impulsionnels MPR EER39300.

## Correspondance des voies

L’intégration ne déduit plus la nature d’un compteur uniquement de sa position dans `UsageMeterList`.

Sur les installations récentes exposant les cinq pinces séparément, on rencontre généralement :

| Type API | Rôle |
| --- | --- |
| `Load1` à `Load5` | CT1 à CT5 |
| `Heating`, `Cooling`, `Hot water`, `Sockets` | usages RT2012 agrégés |
| `Others` | autres consommations calculées |
| `Electricity Meter` | compteur électrique principal / TIC |
| `Gas Meter`, `Cold Water Meter`, `Hot Water Meter` | compteurs impulsionnels selon l’installation |

Les index exacts peuvent varier avec le firmware et la configuration. Un compteur dont `Unit_Energy` vaut `m3` est exposé comme **volume** et non comme énergie électrique.

Quand `Load1` à `Load5` sont présents, ils sont activés par défaut avec `Others`, le compteur électrique/TIC et les compteurs en `m³`. Les anciens usages RT2012 agrégés sont alors désactivés par défaut pour éviter les doublons, mais peuvent être réactivés dans **Configurer**.

La logique CT1–CT5 / Others / Electricity Meter est notamment cohérente avec le projet de référence [mathoudebine/homeassistant-wiser-em5](https://github.com/mathoudebine/homeassistant-wiser-em5).

## Installation avec HACS

1. Cliquez sur le bouton **Ouvrir HACS** ci-dessus.
2. Téléchargez **WiserLink MPI**.
3. Redémarrez Home Assistant.
4. Ouvrez **Paramètres → Appareils et services → Ajouter une intégration**.
5. Recherchez **WiserLink MPI**.

## Configuration

Renseignez l’adresse IP du MPI, le port HTTP, le nom d’utilisateur, le mot de passe et l’intervalle d’actualisation.

Les paramètres, l’état activé/désactivé et le nom de chaque voie détectée peuvent ensuite être modifiés dans **Paramètres → Appareils et services → WiserLink MPI → Configurer**.

Les identifiants initiaux du EER31600 sont `admin` / `admin`. Le mot de passe est prérempli avec `admin` et reste modifiable.

## Protection des mesures et statistiques

Les réponses `/vesta/UsageMeter` sont validées avant d’être transmises à Home Assistant. Les valeurs non numériques, non finies, les index cumulés négatifs et les valeurs correspondant à une corruption/limite 32 bits sont rejetés pour les mesures de puissance, d’énergie et de volume.

La validation est absolue et ne dépend pas d’une mesure précédente : une lecture aberrante reçue juste après le redémarrage de Home Assistant ou du MPI est donc rejetée avant la création ou la mise à jour des états. Lorsqu’une lecture échoue après le démarrage, le coordinateur conserve les dernières valeurs valides pendant le nombre de tentatives configuré, puis marque les entités indisponibles si le défaut persiste.

Cette protection empêche de nouvelles valeurs du type `2 147 483,xx kWh` d’entrer dans les statistiques. Les statistiques déjà enregistrées avant la mise à jour doivent toutefois être corrigées manuellement dans Home Assistant via **Outils de développement → Statistiques**.

## Gaz, eau et compteurs impulsionnels

Le gaz ou l’eau peuvent provenir d’un module impulsionnel MPR/MPE selon l’installation. Ils ne sont pas associés à un index fixe : l’intégration utilise le type et surtout l’unité renvoyés par le MPI.

Un `EnergyConsumed` en `m3` devient un capteur de volume `m³`. Une entrée électrique en `kWh` reste un capteur d’énergie, même si sa position dans la liste correspondait auparavant à un index supposé gaz/eau.

## Mise à jour depuis 0.7.1

La version 0.7.1 supposait à tort que les index 10 et 11 étaient respectivement gaz et eau. Cette hypothèse est supprimée.

Les `unique_id` des mesures restent basés sur l’index API et le champ (`Power` ou `EnergyConsumed`) afin de conserver autant que possible les entités existantes. Si vous aviez explicitement activé les anciens faux capteurs gaz/eau de la 0.7.1, vérifiez leur unité et leurs statistiques après la mise à jour.

Conservez vos anciens capteurs MQTT utilisés par le tableau Énergie tant que les nouvelles mesures WiserLink n’ont pas été validées sur votre installation.

## Écriture

Les actions `wiserlink_mpi.configure_mpr` et `wiserlink_mpi.delete_mpr` permettent d’ajouter, modifier ou supprimer un compteur MPR depuis Home Assistant. Le formulaire comprend le type de compteur, l’usage RT2012, le poids et l’unité d’impulsion ainsi que l’adresse radio.

L’action `wiserlink_mpi.send_command` permet d’envoyer une requête `POST`, `PUT` ou `PATCH` à un endpoint local `/vesta/`.

Utilisez uniquement des commandes confirmées pour votre matériel. L’API d’écriture n’est pas documentée dans la notice EER31600.
