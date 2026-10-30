# Autowash

Application Windows qui analyse la sécurité et la santé du PC, explique chaque problème et propose de le corriger en un clic.

| Catégorie | Ce qui est vérifié | Corrections proposées |
|---|---|---|
| **Malwares** | Antivirus actif et à jour, menaces détectées par Microsoft Defender, programmes lancés au démarrage (non signés, altérés, scripts cachés) | Supprimer les menaces, scan rapide / complet / hors ligne, analyser un fichier, retirer du démarrage (avec sauvegarde) |
| **Vulnérabilités** | Version de Windows encore supportée, pare-feu, UAC, SMBv1, Bureau à distance, compte Invité, Secure Boot, blocage PUA, protection anti-rançongiciel, chiffrement du disque, programmes qui écoutent sur le réseau (ports ouverts) | Activer pare-feu / UAC / PUA, désactiver SMBv1 / RDP / Invité, bloquer un programme dans le pare-feu |
| **Santé** | État SMART et usure des disques, espace libre, mémoire, plantages et écrans bleus récents, pilotes en erreur, batterie | Réparer Windows (DISM + SFC) |
| **Nettoyage** | Fichiers temporaires, restes de programmes désinstallés (Program Files, données d'applications inutilisées depuis 6 mois), dossiers vides, gros fichiers et doublons (photos, vidéos, documents, archives) des dossiers personnels, ancienne installation de Windows (`Windows.old`) | Nettoyer les fichiers temporaires, mettre un reste ou un gros fichier à la corbeille, garder une seule copie d'un doublon, supprimer les dossiers vides |
| **Pilotes** | Pilotes proposés par Windows Update, dernier pilote NVIDIA (service officiel de NVIDIA), conseils AMD/Intel, pilotes de fabricant de plus de 4 ans | Installer un pilote Windows Update, télécharger et lancer le dernier pilote NVIDIA (signature NVIDIA vérifiée avant lancement) |
| **Optimisation** | Réglages recommandés (extensions de fichiers visibles, publicités et applis promotionnelles, télémétrie, Bing dans Démarrer, widgets, enregistrement de jeu en arrière-plan, touches rémanentes, partage pair à pair des mises à jour) et réglages risqués laissés par un « optimiseur » (SmartScreen, Windows Update, Defender, mises à jour du Store coupés) | Appliquer ou rétablir chaque réglage, annulable depuis l'Historique |
| **Démarrage** | Programmes lancés avec Windows, état activé/désactivé, impact mesuré (mémoire et processeur consommés depuis le démarrage) | Activer / désactiver (comme le Gestionnaire des tâches), retirer du démarrage |
| **Navigateurs** | Chrome, Edge, Brave, Vivaldi, Opera : extensions installées hors du store ou avec accès à tous les sites, mots de passe enregistrés dans le navigateur, taille des caches | Gérer une extension, vider le cache |
| **Programmes** | Liste complète des logiciels installés (recherche, tri), bloatwares classés par priorité : 1 = à supprimer (faux optimiseurs, adwares), 2 = recommandé (antivirus d'essai, applications préinstallées, Java), 3 = selon l'usage (utilitaires de fabricant) | Désinstaller un programme ou plusieurs à la fois |
| **Mises à jour** | Windows Update, logiciels obsolètes via `winget` (navigateurs, runtimes, lecteurs… marqués prioritaires, versions de test ignorées), redémarrage en attente | Installer une ou plusieurs mises à jour, installer toutes les urgences, mise à jour automatique quotidienne |

Autowash s'appuie uniquement sur les outils intégrés à Windows (Defender, Windows Update, winget, PowerShell) : aucun moteur antivirus tiers, aucune donnée envoyée.

## Installation

Windows 10/11, Python 3.11+.

```bash
pip install -r requirements.txt
```

### Version .exe

```bash
powershell -ExecutionPolicy Bypass -File build.ps1
```

Construit `dist\Autowash\Autowash.exe` avec PyInstaller, l'installe dans `%LOCALAPPDATA%\Programs\Autowash` et place un raccourci sur le Bureau (`-NoInstall` pour construire seulement). Python n'est alors plus nécessaire pour lancer l'application.

## Utilisation

```bash
python main.py
```

ou double-cliquer sur `Autowash.bat` (ou le raccourci du Bureau pour la version .exe). L'application demande les droits administrateur au démarrage (nécessaires pour la plupart des corrections) ; en les refusant, elle fonctionne en mode limité.

- **Tout corriger (recommandé)** applique les corrections sûres des problèmes critiques et « à surveiller », après confirmation de la liste.
- Sur chaque page, cochez des éléments puis utilisez le bouton de sélection (« Mettre à jour la sélection », « Désinstaller la sélection »…) pour les traiter d'un coup.
- Dans **Programmes**, cliquez sur une ligne du tableau pour la cocher, double-cliquez pour désinstaller ce seul programme. Les runtimes et pilotes sont masqués par défaut, car d'autres programmes en dépendent.
- Les actions risquées ou intrusives (retirer un programme du démarrage, désactiver le Bureau à distance, mise à niveau de Windows, redémarrage) ne sont jamais appliquées automatiquement.
- Les entrées de démarrage retirées sont sauvegardées dans `%LOCALAPPDATA%\Autowash\startup_backup`.
- Les restes de programmes sont déplacés dans la corbeille, jamais supprimés définitivement. Un dossier est considéré comme un reste s'il n'est rattaché à aucun programme installé (registre, applications du Store, services, pilotes, tâches planifiées, processus en cours).

### Historique, annulation et points de restauration

- Chaque action est inscrite dans **Historique** (`%LOCALAPPDATA%\Autowash\history.jsonl`). Celles qui le permettent ont un bouton **Annuler** : restauration depuis la corbeille, remise d'un programme au démarrage, réactivation, suppression d'une règle de pare-feu, retour aux valeurs d'origine d'un réglage d'optimisation.
- En mode administrateur, Autowash crée un **point de restauration système** avant chaque modification importante (au plus un par heure) et **supprime ses propres points au bout de 7 jours** pour ne pas encombrer le disque. Les points créés par Windows ne sont jamais touchés.
- Le score de chaque analyse complète est conservé (`scores.json`) et affiché en courbe sur le tableau de bord.

### Mise à jour automatique des urgences

Dans **Mises à jour**, « Activer (tous les jours) » crée la tâche planifiée `Autowash AutoUpdate`. Chaque jour à 12 h, elle crée un point de restauration, installe les signatures antivirus, les correctifs de sécurité Windows et les mises à jour des logiciels exposés, puis lance une analyse complète et affiche une **notification Windows** si un problème critique est détecté. Journal : `%LOCALAPPDATA%\Autowash\auto-update.log`.

### Ligne de commande

```bash
python main.py --report
```

```bash
python main.py --auto
```

`--report` affiche l'analyse complète en texte, `--auto` installe les mises à jour urgentes sans interface, `--no-admin` lance l'interface sans demander les droits administrateur.

## Structure

```
main.py                  point d'entrée
build.ps1                construction et installation de la version .exe
tools/make_icon.py       génération de assets/autowash.ico
autowash/
  gui.py                 interface (CustomTkinter)
  malware.py             antivirus, menaces, entrées de démarrage suspectes
  vulnerabilities.py     réglages de sécurité, support de Windows, ports ouverts
  updates.py             Windows Update, winget, tâche planifiée et notifications
  drivers.py             pilotes (Windows Update, NVIDIA, pilotes anciens)
  optimize.py            réglages recommandés et réglages risqués à rétablir
  health.py              disques, mémoire, plantages, batterie
  cleanup.py             fichiers temporaires, restes de programmes, gros fichiers, doublons
  startup.py             gestion du démarrage
  programs.py            programmes installés, bloatwares, désinstallation
  browsers.py            extensions, mots de passe enregistrés, caches
  journal.py             historique, annulation, historique du score
  restore.py             points de restauration (purge après 7 jours)
  models.py              Finding / Action / Severity, score
  ps.py, system.py       PowerShell, registre, corbeille, notifications, droits admin
```

Chaque module expose `NAME`, `DESCRIPTION`, `TOOLS` et `scan() -> list[Finding]` ; une vérification est une fonction qui renvoie des `Finding`, chacun avec ses `Action` de correction. Une `Action` peut décrire son annulation (`undo`), exécutée par `journal.register_undo`.
