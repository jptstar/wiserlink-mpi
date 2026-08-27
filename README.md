# WiserLink MPI pour Home Assistant

<p align="center">
  <img src="brand/logo@2x.png" alt="WiserLink MPI" width="520">
</p>

Intégration locale pour WiserLink MPI / Wiser Energy EER31600 et EER39300.

> Intégration communautaire non officielle, sans affiliation avec Schneider Electric.

[![Ouvrir HACS dans Home Assistant](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=jptstar&repository=wiserlink-mpi&category=integration)

## À propos de ce projet

J’ai initialement développé cette intégration pour ma propre installation Home Assistant. Comme de nombreux propriétaires rencontrent des difficultés pour accéder localement aux données de leur WiserLink MPI, je la mets à disposition afin que d’autres puissent également en profiter.

## Auteur

**Jean-Philippe TESTART** ([jptstar](https://github.com/jptstar))

## Fonctions

- lecture locale de `/vesta/UsageMeter` ;
- identification des voies par leur identité réelle (`Load1`, `Gas Meter`, `Cold Water Meter`, etc.) et non par leur position dans la liste ;
- CT1 à CT5 reconnus à partir de `Load1` à `Load5` ;
- détection séparée de `Others`, du compteur électrique/TIC et des compteurs de volume ;
- gaz/eau reconnus comme volumes en `m³` sans supposer un index fixe ;
- si une voie disparaît temporairement, son entité devient indisponible : une autre voie ne peut plus prendre sa place ;
- activation, renommage et unité enregistrés avec une clé stable par compteur ;
- migration des anciennes entités basées sur un index vers des identités sémantiques lorsque leur rôle peut être identifié sans ambiguïté ;
- capteurs de puissance, énergie ou volume créés selon les unités réellement renvoyées ;
- attributs `api_identity`, `api_index`, `api_type`, `api_name` et unités brutes pour le diagnostic ;
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
- décodage des événements WiserLink selon la table officielle trouvée dans l’interface Schneider ;
- dernier événement affiché directement, avec historique récent et codes bruts en attribut ;
- configuration et suppression des compteurs impulsionnels MPR EER39300 ;
- redémarrage **manuel** du EER31600 via la session Web locale ;
- surveillance optionnelle de l’heure des variations d’index gaz, sans reboot automatique.

## Correspondance des voies

L’ordre de `UsageMeterList` n’est pas stable. Une voie peut être omise par le Wiser après un redémarrage ou lorsqu’un compteur n’est pas disponible. L’intégration n’utilise donc plus la position de la voie comme identité.

| Type API | Identité stable | Rôle |
| --- | --- | --- |
| `Load1` à `Load5` | `load1` à `load5` | CT1 à CT5 |
| `Heating` | `heating` | usage Chauffage |
| `Cooling` | `cooling` | usage Climatisation |
| `Hot water` | `hot_water` | usage Eau chaude RT2012 |
| `Sockets` | `sockets` | usage Prises |
| `Others` | `others` | autres consommations |
| `Electricity Meter` | `electricity_meter` | compteur principal / TIC |
| `Gas Meter` | `gas_meter` | compteur gaz impulsionnel |
| `Cold Water Meter` | `cold_water_meter` | compteur eau froide |
| `Hot Water Meter` | `hot_water_meter` | compteur eau chaude |

Exemple : si `Gas Meter` disparaît, `Cold Water Meter` peut passer de l’index 11 à l’index 10 dans la réponse HTTP. Il reste néanmoins `cold_water_meter` et ne peut plus alimenter l’ancienne entité gaz.

Un compteur dont `Unit_Energy` vaut `m3` est exposé comme **volume** et non comme énergie électrique.

## Installation avec HACS

1. Cliquez sur le bouton **Ouvrir HACS** ci-dessus.
2. Téléchargez **WiserLink MPI**.
3. Redémarrez Home Assistant.
4. Ouvrez **Paramètres → Appareils et services → Ajouter une intégration**.
5. Recherchez **WiserLink MPI**.

## Configuration

Renseignez l’adresse IP du MPI, le port HTTP, le nom d’utilisateur, le mot de passe et l’intervalle d’actualisation.

Les voies réellement détectées sont ensuite proposées dans **Paramètres → Appareils et services → WiserLink MPI → Configurer** avec leur rôle réel : CT1/Load1, compteur électrique/TIC, gaz, eau froide, etc.

Les identifiants initiaux du EER31600 sont `admin` / `admin`. Le mot de passe est prérempli avec `admin` et reste modifiable.

## Surveillance des relèves gaz

Le comportement radio exact du MPR/MPE n’est pas suffisamment documenté pour considérer qu’un reboot du EER31600 recale de façon fiable l’heure de transmission du MPE.

La surveillance de dérive est donc **observation uniquement** :

1. elle est désactivée par défaut ;
2. l’heure cible et la tolérance sont configurables ;
3. le premier échantillon après démarrage sert uniquement de référence ;
4. un passage `0 → index complet` n’est pas considéré comme une vraie relève quotidienne ;
5. une disparition puis réapparition du `Gas Meter` n’est pas considérée comme une relève ;
6. seules les variations positives de l’index observées alors que le compteur gaz est resté présent sur des polls consécutifs sont horodatées ;
7. **aucun redémarrage automatique n’est effectué** par cette fonction.

Le service `wiserlink_mpi.reboot_mip` reste disponible pour un redémarrage explicitement demandé par l’utilisateur.

L’entité de diagnostic **Dérive relève gaz** expose l’heure de la dernière variation gaz confirmée, l’estimation de la suivante, la dérive par rapport à la cible et rappelle que le reboot automatique est désactivé.

## Protection des mesures et statistiques

Les réponses `/vesta/UsageMeter` sont validées avant d’être transmises à Home Assistant. Les valeurs non numériques, non finies, les index cumulés négatifs et les valeurs correspondant à une corruption/limite 32 bits sont rejetés.

Au démarrage, deux snapshots cohérents sont requis avant de publier les premiers états. En fonctionnement, une variation cumulative suspecte est relue avant adoption. Si deux lectures successives confirment une nouvelle base cohérente, cette base est acceptée sans modifier artificiellement les valeurs reçues.

Les champs `PowerValidity` et `EnergyValidity` restent respectés. Lorsqu’une lecture reste invalide, le coordinateur conserve les dernières valeurs valides pendant le nombre de tentatives configuré, puis marque les entités indisponibles si le défaut persiste.

Les statistiques déjà enregistrées avant une mise à jour ne sont pas réécrites automatiquement par l’intégration.

## Migration des anciennes entités

Les anciennes versions utilisaient des identifiants du type :

```text
<mpi>_10_energyconsumed
<mpi>_11_energyconsumed
```

Ces identifiants étaient vulnérables aux changements d’ordre de `UsageMeterList`.

Lors de la mise à jour, l’intégration tente de reconnaître de manière conservatrice le rôle de l’ancienne entité à partir de son nom, de sa classe (`gas`, `water`) et des noms API actuels. Quand la correspondance est sûre, le `unique_id` est migré vers par exemple :

```text
<mpi>_gas_meter_energyconsumed
<mpi>_cold_water_meter_energyconsumed
```

Le registre Home Assistant est mis à jour afin de conserver l’`entity_id` existant et donc la continuité des statistiques lorsque la migration est identifiable sans ambiguïté. Si elle ne l’est pas, l’ancienne entité n’est pas réaffectée arbitrairement à une autre voie.

## Gaz, eau et compteurs impulsionnels

Le gaz ou l’eau peuvent provenir d’un module impulsionnel MPR/MPE. La configuration radio est visible dans les entités **Configuration MPR** et via `/vesta/MpeEndpoint/instances`.

Un `EnergyConsumed` en `m3` devient un capteur de volume `m³`. Une entrée électrique en `kWh` reste un capteur d’énergie.

## Écriture

Les actions `wiserlink_mpi.configure_mpr` et `wiserlink_mpi.delete_mpr` permettent d’ajouter, modifier ou supprimer un compteur MPR depuis Home Assistant. Le formulaire comprend le type de compteur, l’usage RT2012, le poids et l’unité d’impulsion ainsi que l’adresse radio.

Une configuration MPR strictement identique à celle déjà présente n’est plus réécrite : le matériel a montré qu’un simple `PUT` identique pouvait lancer une vraie procédure radio et finir en échec.

L’action `wiserlink_mpi.reboot_mip` redémarre explicitement le EER31600 via la route `/rs/Device/methods/Reboot` et la session Web locale. **Aucun reboot automatique lié à la dérive gaz n’est actuellement autorisé.**

L’action `wiserlink_mpi.send_command` permet d’envoyer une requête `POST`, `PUT` ou `PATCH` à un endpoint local `/vesta/`.

Utilisez uniquement des commandes confirmées pour votre matériel. L’API d’écriture n’est pas documentée dans la notice EER31600.
