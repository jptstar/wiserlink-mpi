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
- capteurs de puissance et d’énergie par voie ;
- adresse IP, port, identifiants et noms des voies personnalisables ;
- noms génériques par défaut (`Voie 1`, `Voie 2`, etc.) ;
- intervalle d’actualisation réglable de 2 à 300 secondes ;
- seuil de reconnexion réglable de 1 à 20 tentatives ;
- conservation des dernières valeurs pendant les tentatives ;
- rejet des valeurs reçues lors d’une erreur ou d’une réponse corrompue ;
- entité **MPI Online** classée dans les diagnostics ;
- état EM5 et communications avec le MIP et le compteur électrique ;
- numéros de série et versions logicielles MIP, EM5 et MPR ;
- batterie et communication de chaque compteur MPR ;
- dernier événement affiché directement, avec l’historique récent en attribut ;
- configuration et suppression des compteurs impulsionnels MPR EER39300 ;
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

Les identifiants initiaux du EER31600 sont `admin` / `admin`. Le mot de passe est prérempli avec `admin` et reste modifiable.

## Gaz et eau

Le gaz et l’eau nécessitent des modules optionnels. Leur absence n’entraîne aucune erreur et n’affecte pas les capteurs électriques ni l’état **MPI Online**. Les modules gaz et eau sont exposés comme des volumes cumulés en mètres cubes (`m³`), avec une icône propre à chaque fluide.

Lors d’une mise à jour depuis une ancienne version, les entités `Gaz Énergie` et `Eau de ville Énergie` sont automatiquement renommées en `Gaz Volume` et `Eau de ville Volume`. Les anciennes entités de puissance gaz/eau sont retirées du registre.

## Écriture

Les actions `wiserlink_mpi.configure_mpr` et `wiserlink_mpi.delete_mpr` permettent d’ajouter, modifier ou supprimer un compteur MPR depuis Home Assistant. Le formulaire comprend le type de compteur, l’usage RT2012, le poids et l’unité d’impulsion ainsi que l’adresse radio.

L’action `wiserlink_mpi.send_command` permet d’envoyer une requête `POST`, `PUT` ou `PATCH` à un endpoint local `/vesta/`.

Utilisez uniquement des commandes confirmées pour votre matériel. L’API d’écriture n’est pas documentée dans la notice EER31600.
