# WiserLink MPI pour Home Assistant

<p align="center">
  <img src="brand/logo@2x.png" alt="WiserLink MPI" width="520">
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
- protection contre les valeurs 32 bits aberrantes autour de `0x7fffffff` / `0x80000000` ;
- double lecture cohérente avant de publier les premiers états après un redémarrage ;
- relecture immédiate après une lecture invalide ou une variation cumulative suspecte ;
- confirmation des remises à zéro réelles avant adoption d’une nouvelle base ;
- entité **MPI Online** classée dans les diagnostics ;
- état EM5 et communications avec le MIP et le compteur électrique ;
- numéros de série et versions logicielles MIP, EM5 et MPR ;
- batterie et communication de chaque compteur MPR ;
- décodage des événements WiserLink selon la table officielle de l’interface Schneider ;
- dernier événement affiché directement, avec l’historique récent et les codes bruts en attribut ;
- configuration et suppression des compteurs impulsionnels MPR EER39300 ;
- redémarrage explicite du EER31600 via la session Web locale ;
- contrôle optionnel de dérive horaire des relèves gaz MPR/MPE avec heure cible, tolérance et heure de contrôle configurables ;
- apprentissage de l’heure réelle obtenue après un recalage afin d’éviter les reboots quotidiens inutiles.

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

Pour le contrôle de dérive gaz, les options suivantes sont également disponibles dans **Configurer** :

- **Contrôle automatique de dérive gaz** : désactivé par défaut ;
- **Heure cible de relève gaz** : `23:45` par défaut ;
- **Tolérance de dérive gaz** : `10 min` par défaut ;
- **Heure de contrôle / reboot correctif** : `23:35` par défaut.

L’heure de contrôle doit être antérieure à l’heure cible. Si elle est égale ou postérieure, l’intégration n’effectue aucun reboot automatique afin d’éviter un redémarrage tardif ou après minuit.

Les identifiants initiaux du EER31600 sont `admin` / `admin`. Le mot de passe est prérempli avec `admin` et reste modifiable.

## Contrôle de dérive des relèves gaz

Le MPR/MPE peut publier son index suivant un cycle autonome qui dérive progressivement. L’intégration mémorise l’heure à laquelle une nouvelle valeur gaz est réellement observée et compare cette heure à la cible ou, après un recalage réussi, à l’heure réelle du cycle appris.

La logique est volontairement prudente :

1. aucun redémarrage n’est effectué tant que le contrôle automatique n’est pas activé ;
2. à l’heure de contrôle, l’intégration juge **l’heure du dernier cycle réellement observé**, et non le fait que la relève du jour soit déjà arrivée ou non ;
3. avec les valeurs par défaut, la fenêtre de correction est ouverte à `23:35` et fermée à `23:45` : aucun reboot automatique n’est lancé à `23:45` ou après ;
4. si le dernier cycle connu est dans la tolérance, aucun redémarrage n’est effectué ;
5. si le cycle est réellement hors tolérance, un seul redémarrage correctif peut être demandé dans la journée ;
6. après ce reboot, l’intégration attend obligatoirement une nouvelle vraie relève gaz ;
7. si cette nouvelle relève est proche de la cible, son heure réelle devient la référence de cycle ;
8. si le reboot n’a pas suffisamment recalé le cycle, les reboots automatiques sont suspendus au lieu de redémarrer le Wiser tous les jours.

Exemple : le compteur relève à `16:00`. À `23:35`, cette dérive est connue et le Wiser peut être redémarré. Si la première relève obtenue après correction arrive à `23:47`, l’intégration valide le recalage et mémorise `23:47` comme nouvelle référence. Le lendemain à `23:35`, l’absence de la relève du jour — normalement attendue vers `23:47` — ne provoque donc **aucun reboot**. Tant que le cycle reste autour de `23:47` dans la tolérance configurée, le Wiser n’est plus redémarré.

À l’inverse, si un reboot correctif laisse la relève très éloignée de la cible, l’intégration considère la correction comme inefficace et suspend les reboots automatiques suivants. Désactiver puis réactiver le contrôle, ou modifier la cible, permet de repartir sur un nouveau cycle d’apprentissage.

L’intégration ne modifie pas artificiellement l’index gaz pour cette fonction : elle observe la valeur réellement fournie par le WiserLink et utilise uniquement l’heure de changement pour mesurer la dérive.

Une entité de diagnostic **Dérive relève gaz** expose notamment l’heure de la dernière relève détectée, l’estimation de la suivante, la dérive en minutes, les horaires configurés et le dernier redémarrage.

## Protection des mesures et statistiques

Les réponses `/vesta/UsageMeter` sont validées avant d’être transmises à Home Assistant. Les valeurs non numériques, non finies, les index cumulés négatifs et les valeurs correspondant à une corruption/limite 32 bits sont rejetés pour les mesures de puissance, d’énergie et de volume.

Depuis la version 0.8.3, l’acquisition ajoute une seconde couche de sécurité temporelle. Au démarrage, aucune première valeur n’est publiée seule : deux snapshots cohérents sont requis. Si les deux premières lectures ne sont pas cohérentes, une troisième lecture permet de retenir la paire stable. Cela protège notamment contre les zéros ou snapshots partiels transitoires pendant le démarrage du MPI.

En fonctionnement, une lecture rejetée est relue immédiatement. Une variation cumulative suspecte — compteur qui recule, structure des voies qui change ou saut d’énergie disproportionné par rapport aux puissances mesurées — déclenche aussi une lecture de confirmation avant toute publication. Si la lecture suivante revient dans la continuité, l’échantillon transitoire est ignoré. Si deux lectures successives confirment une nouvelle base cohérente, par exemple après une vraie remise à zéro d’un compteur, cette nouvelle base est acceptée sans modifier artificiellement les valeurs reçues.

Les champs `PowerValidity` et `EnergyValidity` restent respectés par les entités : une mesure explicitement invalide n’est pas utilisée pour décider de la continuité d’un compteur.

Lorsqu’une lecture reste invalide, le coordinateur conserve les dernières valeurs valides pendant le nombre de tentatives configuré, puis marque les entités indisponibles si le défaut persiste.

Cette protection empêche de nouvelles valeurs du type `2 147 483,xx kWh` d’entrer dans les statistiques et réduit le risque d’autres valeurs transitoires au redémarrage. Les statistiques déjà enregistrées avant la mise à jour doivent toutefois être corrigées manuellement dans Home Assistant via **Outils de développement → Statistiques**.

## Gaz, eau et compteurs impulsionnels

Le gaz ou l’eau peuvent provenir d’un module impulsionnel MPR/MPE selon l’installation. Ils ne sont pas associés à un index fixe : l’intégration utilise le type et surtout l’unité renvoyés par le MPI.

Un `EnergyConsumed` en `m3` devient un capteur de volume `m³`. Une entrée électrique en `kWh` reste un capteur d’énergie, même si sa position dans la liste correspondait auparavant à un index supposé gaz/eau.

## Mise à jour depuis 0.7.1

La version 0.7.1 supposait à tort que les index 10 et 11 étaient respectivement gaz et eau. Cette hypothèse est supprimée.

Les `unique_id` des mesures restent basés sur l’index API et le champ (`Power` ou `EnergyConsumed`) afin de conserver autant que possible les entités existantes. Si vous aviez explicitement activé les anciens faux capteurs gaz/eau de la 0.7.1, vérifiez leur unité et leurs statistiques après la mise à jour.

Conservez vos anciens capteurs MQTT utilisés par le tableau Énergie tant que les nouvelles mesures WiserLink n’ont pas été validées sur votre installation.

## Écriture

Les actions `wiserlink_mpi.configure_mpr` et `wiserlink_mpi.delete_mpr` permettent d’ajouter, modifier ou supprimer un compteur MPR depuis Home Assistant. Le formulaire comprend le type de compteur, l’usage RT2012, le poids et l’unité d’impulsion ainsi que l’adresse radio.

Une configuration MPR strictement identique à celle déjà présente n’est plus réécrite : le matériel a montré qu’un simple `PUT` identique pouvait lancer une vraie procédure radio et finir en échec.

L’action `wiserlink_mpi.reboot_mip` redémarre explicitement le EER31600 via la route `/rs/Device/methods/Reboot` et la session Web locale.

L’action `wiserlink_mpi.send_command` permet d’envoyer une requête `POST`, `PUT` ou `PATCH` à un endpoint local `/vesta/`.

Utilisez uniquement des commandes confirmées pour votre matériel. L’API d’écriture n’est pas documentée dans la notice EER31600.