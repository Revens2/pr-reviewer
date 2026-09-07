# Métriques advisory & passage en required — état 2026-09-07

Observation de la phase advisory réelle : données réelles affichées d'abord, aucune statistique
fabriquée, aucun seuil gravé dans GitHub. Rien n'est `required`.

## Commandes (lecture seule, aucun quota Freebuff)

| Commande | Rôle |
|---|---|
| `python3 transport/report.py [--since 7d\|30d\|all] [--json]` | Rapport opérationnel : reviews total/real/fixture, PASS/BLOCK/ERROR, certifiées, latence p50/p95, durée review p50/p95, retries, erreurs par classe, findings par sévérité, findings labellisés |
| `python3 transport/readiness.py [--json]` | Statut `NOT_ENOUGH_DATA` / `NOT_READY` / `CANDIDATE_READY` + raisons — ne modifie rien sur GitHub |
| `python3 transport/feedback.py label <job_id> <finding_idx> <confirmed\|false_positive\|unclear\|not_reviewed>` | Feedback humain sur un finding (fichier `state/feedback.jsonl`) — prépare le calcul `confirmed_BLOCK_rate` / `false_positive_rate` |
| `python3 transport/health.py` | Santé service (jamais de session Muse) |

Source unique : la base SQLite des jobs (`jobs.db`) + verdicts par job. Les métriques par review
sont dérivées des colonnes `head_ref`/`author_association` + JSON `verdict` : timestamp, repo, PR,
head SHA, branche, auteur, `queue_delay_s`, `review_duration_s`, `total_latency_s`, modèle
demandé/vérifié/certifié, verdict, findings par sévérité, retries, `error_class`. Jamais de token,
d'authToken ni de prompt dans ces données.

## Données observées (2026-09-07, base VPS)

```
reviews total: 4  (real=1, fixture=3)
PASS=2  BLOCK=2  ERROR=0
certifiées Muse: 4 (1.0)  non certifiées: 0
latence totale p50/p95: 107.6/838.0 s ; review p50/p95: 105.2/833.5 s
retries total: 0 ; findings: major=6, minor=2
errors par classe: aucun
```

- **1 review réelle** : PR #1 `feat/island-docking-sizing-media` (53 fichiers, OWNER) → **BLOCK
  certifié, 7 findings** (5 major / 2 minor) — première review advisory réelle.
- 3 reviews fixture (E2E A/B/C sur PR #6, branche `test/…`, 2026-09-07 ~07:46–07:51).
- Note d'hygiène : les 4 jobs préexistent à la capture de `head_ref` ; backfill manuel prouvé par
  l'API GitHub (noms de branches des PR #1/#6) pour un classement real/fixture exact.

## Qualification humaine PR #1 (2026-09-07) — première revue réelle qualifiée

`readiness.py` → **NOT_ENOUGH_DATA** (`1 review réelle < floor de signal 5`) mais expose désormais :
`real_block_reviews=1, human_qualified_reviews=1, block_quality=['BLOCK_CORRECT']`,
qualification : `confirmed=7 false_positive=0 unclear=0 obsolete=0 duplicate=0 |
confirmed_rate=1.0 fp_rate=0.0` (dénominateur = confirmed + false_positive, unclear/obsolete/
duplicate exclus), accord sévérité Muse/humain exact=5/7 (**Muse sur-évalue 2** : F4/F5 major→minor).
Détail finding par finding + preuves : `docs/PR1-FINDINGS-QUALIFICATION.md`. Les seuils actuels
(`MIN_REAL_REVIEWS=5`, certification ≥ 90 %, erreurs techniques ≤ 20 %, ≥ 80 % des findings BLOCK
qualifiés, ≤ 25 % faux positifs déclarés) restent **provisoires** et seront calibrés sur l'observation.

## Critères qualitatifs pour le futur required (non activé)

- [ ] plusieurs vraies PR observées — en cours (1) ;
- [ ] 0 incident de corruption / faux BLOCK grave non compris — PR #1 qualifiée
      **BLOCK_CORRECT** (7/7 confirmed, 0 faux positif) ;
- [ ] erreurs techniques maîtrisées (0 à ce jour) ;
- [ ] recovery restart prouvée (réconciliation running orphelin testée) ;
- [ ] consommation Freebucks comprise — **limite** : le solde n'est pas lisible proprement (seul
      point de mesure = UI TUI, jamais une session démarrée pour ça) ; trace ponctuelle dans
      `state/balance.json` ;
- [ ] fallback modèle fail-closed (0 PASS non certifié observé) ;
- [ ] stabilité service plusieurs jours.
- [ ] **Calibration sévérité** : 2/7 findings sur-évalués (major→minor) sur PR #1 — suivi
      nécessaire pour juger si la politique de sévérité de Muse est stable.

## Compatibilité GitHub (revalidée en live le 2026-09-07)

- `Revens2/agent-island` est **privé** (plan gratuit) : branch protection ET rulesets → **403**
  (« Upgrade to GitHub Pro or make this repository public »). Un required status check n'est donc
  techniquement possible **que** si le repo passe public (décision produit) ou sur plan Pro.
- `Revens2/pr-reviewer` (public) : l'API protection répond (404 « Branch not protected ») — un
  required status y serait configurable, mais c'est le repo du composant, pas le repo reviewé.
