# WiserLink MPI pour Home Assistant

Intégration locale installable via HACS, dérivée du flux Node-RED fourni. Elle interroge directement `GET /vesta/UsageMeter` avec l’authentification Basic du MPI. Aucun MQTT ni Node-RED n’est nécessaire.

## Fonctions

- une seule lecture coordonnée du bus toutes les 5 secondes (réglable de 2 à 300 s) ;
- capteurs `Power` et `EnergyConsumed` créés automatiquement pour chaque élément de `UsageMeterList` ;
- adresse IP, port, identifiants et fréquence modifiables après l’installation ;
- nom personnalisable pour chaque voie (« load ») détectée ;
- activation séparée des modules optionnels de comptage gaz et eau ;
- conservation des valeurs à zéro (le flux d’origine les supprimait) ;
- disponibilité gérée par Home Assistant ;
- entité binaire **MPI Online** indiquant le résultat de la dernière lecture ;
- seuil réglable d’échecs consécutifs avant passage en indisponible ;
- conservation des dernières mesures pendant les tentatives de reconnexion ;
- service d’écriture contrôlé `wiserlink_mpi.send_command` pour les endpoints Vesta connus.

> **Important :** le fichier fourni ne documente aucune commande d’écriture. N’utilisez le service qu’avec un chemin et une charge JSON confirmés pour votre version de MPI. Il refuse les URL externes, les chemins hors de `/vesta/` et les méthodes destructrices.

## Installation locale pour essai

Copiez le dossier `custom_components/wiserlink_mpi` dans le dossier `config/custom_components/` de Home Assistant, puis redémarrez Home Assistant.

Dans **Paramètres → Appareils et services → Ajouter une intégration**, cherchez **WiserLink MPI** et renseignez :

- l’IP du MPI (par exemple `10.89.10.26`) ;
- le port, généralement `80` ;
- l’utilisateur et le mot de passe (le flux fourni utilisait `admin` / `admin`) ;
- l’intervalle de lecture.

Pour changer ensuite l’adresse ou les noms, ouvrez **Paramètres → Appareils et services → WiserLink MPI → Configurer**. Une ligne de nom est proposée pour chaque élément actuellement renvoyé dans `UsageMeterList`. La configuration est automatiquement rechargée après validation.

Le gaz et l’eau ne sont pas des voies électriques intégrées au compteur EER39300. Leurs entités sont désactivées par défaut et ne sont créées que si **Module optionnel gaz installé** ou **Module optionnel eau installé** est activé. Leurs noms deviennent alors personnalisables comme les autres voies.

L’absence de l’un de ces modules est toujours considérée comme normale : elle ne provoque ni erreur de démarrage, ni échec d’actualisation, ni passage de **MPI Online** à déconnecté. Même lorsqu’une option est activée par erreur, aucune entité n’est créée si la voie correspondante n’existe pas dans la réponse du MPI.

La temporisation d’actualisation est réglable entre **2 et 300 secondes** dans ce même écran. La valeur par défaut est de **5 secondes**. L’entité `binary_sensor.*_mpi_online` passe à l’état déconnecté lorsqu’une actualisation échoue et repasse automatiquement à connecté dès que le MPI répond de nouveau.

Le nombre d’échecs consécutifs tolérés est réglable de **1 à 20** (valeur par défaut : **3**). Avant ce seuil, les dernières valeurs restent affichées. Au seuil, les capteurs deviennent indisponibles sans effacer les dernières données mémorisées. Une lecture réussie remet immédiatement le compteur à zéro.

## Particularités du EER31600

La notice Schneider S1B66612-00 indique que l’adresse attribuée au MIP est visible dans **Configuration IP** sur le Wiser EM5. Le navigateur et Home Assistant doivent accéder à cette adresse sur le même réseau local. Les identifiants de première connexion sont `admin` / `admin`, avec changement de mot de passe recommandé par Schneider.

## Installation avec HACS

Après publication de ce dossier dans un dépôt GitHub public :

1. HACS → Intégrations → menu → **Dépôts personnalisés** ;
2. ajoutez l’URL du dépôt avec la catégorie **Intégration** ;
3. installez **WiserLink MPI**, redémarrez Home Assistant et ajoutez l’intégration.

## Écriture

Dans les Outils de développement → Actions, utilisez `wiserlink_mpi.send_command` :

```yaml
action: wiserlink_mpi.send_command
data:
  entry_id: "identifiant_de_configuration"
  method: PUT
  path: /vesta/ENDPOINT_CONFIRME
  payload:
    exemple: valeur
```

Le résultat HTTP peut être demandé comme réponse de l’action. Après une écriture réussie, les capteurs sont relus immédiatement.

## Hypothèse d’unités

`Power` est exposé en W et `EnergyConsumed` en kWh, conformément à l’usage courant du WiserLink MPI. Vérifiez ces unités sur une réponse brute de votre appareil avant d’utiliser les statistiques d’énergie à long terme.
