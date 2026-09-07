# Qualification humaine des 7 findings — agent-island PR #1 (2026-09-07)

Review analysée : `Revens2/agent-island` PR #1 `feat/island-docking-sizing-media`,
head `58ba5bba434478d2855b2d327359e884ab634a29` (le head n'a PAS changé depuis le BLOCK),
base `45162c28`, 53 fichiers. BLOCK certifié Muse (`meta/muse-spark-1.3-contributor`),
status `failure` + commentaire (08:58Z). Qualification = inspection statique du code au SHA
reviewé, des tests de la PR, du CHANGELOG et du diff base→head. Aucun runtime Windows GUI /
macOS n'a été exécuté (preuves code-level ; vérifications runtime signalées le cas échéant).

Enregistrement : `state/feedback.jsonl` via `transport/feedback.py label … --sev --conf --ev`.

## Table de qualification

| # | Fichier:ligne (SHA) | Sévérité Muse | Classification humaine | Sévérité humaine | Confiance | Preuve (résumé) |
|---|---|---|---|---|---|---|
| 1 | `windows/.../UI/IslandWindow.xaml.cs:278` | major | **CONFIRMED** | major | high | `SyncFullscreenWatcher` ne démarre le watcher que si `FullscreenAutoHideStore.Enabled` ; `PollNow()` no-op quand le timer est stoppé ; **aucun** abonné à `MediaAutoHideStore.PropertyChanged` (grep complet) ; le toggle media seul (fullscreen OFF, défaut) ne démarre jamais le watcher → fonction inerte |
| 2 | `windows/.../Shared/Fullscreen/FullscreenPolicy.cs:112` | major | **CONFIRMED** | major | high | Les deux branches media (`mediaAutoHideEnabled && isMediaPlaying`, dont PlaybackOnly en tête) retournent `true` **avant** le test `Covers(monitor)` → un player fenêtré (800×600) masque l'île en mode par défaut ; contredit CHANGELOG L21 et le docstring de la policy. Réserve : le test cité (SharedTests 397-402) n'active pas `mediaAutoHideEnabled` — il ne contredit donc pas le code, il ne couvre simplement pas le chemin actif |
| 3 | `Sources/Window/IslandWindowController.swift:316` | major | **CONFIRMED** | major | medium | Base = top seul ; la PR ajoute bottom/left/right en déplaçant la **fenêtre**, mais contenu (IslandRootView `.top`+Spacer, `scaleEffect anchor:.top`) et hit-test (IslandHostingView `y=b.maxY-h`) restent top-anchored sur une fenêtre fixe 1440×640 → 3 edges mal dockés. Windows a EdgeDocking + tests ; macOS aucun test. Vérification runtime macOS recommandée |
| 4 | `Sources/Window/IslandWindowController.swift:281` | major | **CONFIRMED** | **minor** | high | Les deux watchers font `startMonitoring()` dans leur `init` (singletons) → toujours actifs ; `observeMediaAutoHide` installé toujours ; `enabled=false` (défaut) → `fadeIn()` (`alpha=0` + `orderFrontRegardless` + 0.4 s) à **chaque** émission → clignotement/remontée de fenêtre à chaque play/pause même feature OFF. Réserve de sévérité : intermittent et cosmétique (défaut OFF) |
| 5 | `.github/workflows/ci.yml:19` | major | **CONFIRMED** | **minor** | medium | La PR introduit `pull_request → [self-hosted, linux, ARM64, vps-etude]` (ci.yml, security.yml) et déplace le job core windows-ci de `ubuntu-latest` vers le runner VPS — anti-pattern GitHub documenté (exécution de code de PR sur runner persistant). **Prémisse corrigée** : le repo était **privé** (1 owner, 0 forks) à la review — « public repo » faux ; risque réel aujourd'hui ≈ nul, latent si public (direction produit évoquée) ou collaborateurs |
| 6 | `windows/.../UI/SettingsWindow.cs:718` | minor | **CONFIRMED** | minor | high | 3 sliders IslandSizingStore → `Preferences.Set` immédiat à chaque `ValueChanged` (drag = dizaines d'écritures/s) ; la PR a corrigé exactement ce pattern pour IslandBarLengthStore (Preview/Commit) quelques lignes plus bas — incohérence interne |
| 7 | `windows/.../Model/IslandBarLengthStore.cs:17` | minor | **CONFIRMED** | minor | high | CHANGELOG L10 « between 60 and 300 pt » vs `MinWidth=32.0`/`MaxWidth=300.0` livré (commentaire : plancher flancs). Réserve : la copie Settings ne mentionne pas la plage (la raison sur-étendait la source du désaccord) |

## Métriques issues de la qualification

- findings qualifiés : **7/7** — confirmed=7, false_positive=0, unclear=0, obsolete=0, duplicate=0
- confirmed_rate = 1.0 · false_positive_rate = 0.0 (dénominateur = confirmed + false_positive ;
  unclear/obsolete/duplicate exclus — documenté)
- accord de sévérité Muse/humain : exact = 5/7 ; **Muse sur-évalue 2/7** (F4, F5 : major → minor)
- BLOCK qualité : **BLOCK_CORRECT** — 3 findings bloquants confirmés (F1, F2, F3 : fonctionnalités
  majeures de la PR cassées/contradictoires, preuve statique)

## BLOCK justifié ?

**OUI — BLOCK_CORRECT.** F1 (media auto-hide inerte en défaut), F2 (playback fenêtré masque l'île,
contredit le CHANGELOG de la même PR) et F3 (edge docking macOS cassé sur 3 bords sur 4) sont des
défauts réels, preuves statiques à l'appui, dans le cœur fonctionnel de la PR. F4/F5 sont vrais
mais de sévérité moindre que major. Le BLOCK global était justifié ; la calibration utile porte
sur la sévérité (2 major → minor).

## Analyse du skill (§11) — modification non retenue

Les deux cas de sur-évaluation (F4, F5) ne proviennent **pas** d'un défaut générique du skill :
- F4 : analyse statique exacte ; le désaccord est un jugement d'impact (cosmétique/intermittent) —
  aucune règle de prompt ne peut trancher ce type d'appréciation à la place d'un humain.
- F5 : la sur-évaluation vient d'une **donnée de contexte fausse** (repo réputé public dans le
  brief du reviewer, alors qu'il était privé) — pas d'une faiblesse de prompt.
Aucune modification de `reviewer/.agents/skills/pr-review/SKILL.md` n'est donc apportée (pas de
sur-ajustement sur une seule PR). À réexaminer si un pattern de faux positifs générique apparaît
sur les prochaines reviews réelles.

## Actions

### À corriger (agent-island, mission séparée — non faite ici)
- **F1** — démarrer le watcher si `(fullscreenEnabled || mediaEnabled)` et s'abonner à
  `MediaAutoHideStore.PropertyChanged` (complexité faible, risque faible).
- **F2** — déplacer les branches media après le test `Covers` ; renforcer SharedTests avec
  `mediaAutoHideEnabled=true` (complexité faible, aligne code+CHANGELOG ; décision produit si
  PlaybackOnly = « masquer à toute lecture » était voulu → corriger alors le CHANGELOG).
- **F3** — ancrer silhouette + hit-test à l'edge docké (macOS) (complexité moyenne ; test runtime
  macOS requis).
- **F5** — si le repo doit devenir public ou accueillir des collaborateurs : runners
  GitHub-hosted pour `pull_request` (ou approval gate) ; sinon rien d'urgent (privé, solo).

### À vérifier manuellement
- F3 runtime macOS (position réelle bottom/left/right) ; F4 ressenti (fréquence du clignotement).

### Aucune action / observation
- F6, F7 (mineurs : cohérence interne PR / CHANGELOG) — corrections rapides opportunes avec la
  prochaine PR.
