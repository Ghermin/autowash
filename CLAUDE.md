# Bored Dev Playground

Ce projet fait partie du Bored Dev Playground : https://ghermin.github.io/ (repo `Ghermin/Ghermin.github.io`, en local `../bored-dev-playground`).
Sa page GitHub (`docs/index.html`) suit le kit commun du playground.

Avant de modifier la page ou le style, se demander si le changement est local ou global :

- **Global** (bandeau, cartes, boutons, puces, pied de page, couleurs neutres, comportement clair / sombre…) : ne pas le faire ici. `docs/playground.css` est une copie de `kit/playground.css` du playground : modifier le kit là-bas, lancer `sh kit/sync.sh`, puis commiter dans chaque projet.
- **Local** (couleurs ou police du projet) : le faire dans la page, en redéfinissant les variables `--pg-*` après le kit.
- Si le nom, la description, les tags, l'icône ou l'adresse du projet changent, mettre à jour sa carte dans `index.html` du playground.

À garder sur la page : le bandeau `pg-strip` qui ramène au playground, le thème clair / sombre automatique, le responsive (pas de défilement horizontal sur mobile), 2 polices max (celle du projet + JetBrains Mono).
