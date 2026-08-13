# WiserLink MPI pour Home Assistant

Intégration locale pour WiserLink MPI / Wiser Energy EER31600 et EER39300.

[![Ouvrir HACS dans Home Assistant](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=jptstar&repository=wiserlink-mpi&category=integration)

## Fonctions

- lecture locale de `/vesta/UsageMeter` sans MQTT ni Node-RED ;
- capteurs de puissance et d’énergie par voie ;
- adresse IP, port, identifiants et noms des voies personnalisables ;
- intervalle d’actualisation réglable de 2 à 300 secondes ;
- seuil de reconnexion réglable de 1 à 20 tentatives ;
- conservation des dernières valeurs pendant les tentatives ;
- entité **MPI Online** ;
- modules gaz et eau optionnels, désactivés par défaut.

## Installation avec HACS

1. Cliquez sur le bouton **Ouvrir HACS** ci-dessus.
2. Téléchargez **WiserLink MPI**.
3. Redémarrez Home Assistant.
4. Ouvrez **Paramètres → Appareils et services → Ajouter une intégration**.
5. Recherchez **WiserLink MPI**.

## Configuration

Renseignez l’adresse IP du MPI, le port HTTP, le nom d’utilisateur, le mot de passe et l’intervalle d’actualisation.

Les paramètres et les noms des voies peuvent ensuite être modifiés dans **Paramètres → Appareils et services → WiserLink MPI → Configurer**.

Les identifiants initiaux du EER31600 sont généralement `admin` / `admin`. Il est recommandé de modifier ce mot de passe.

## Gaz et eau

Le gaz et l’eau nécessitent des modules optionnels. Leur absence n’entraîne aucune erreur et n’affecte pas les capteurs électriques ni l’état **MPI Online**.

## Écriture

L’action `wiserlink_mpi.send_command` permet d’envoyer une requête `POST`, `PUT` ou `PATCH` à un endpoint local `/vesta/`.

Utilisez uniquement des commandes confirmées pour votre matériel. L’API d’écriture n’est pas documentée dans la notice EER31600.
