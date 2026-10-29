# Continuous task adaptation study v1

Status: protocol revision 2 is frozen; no cell is accepted. The first execution stopped on HTTP 401 before training. See CONTINUOUS_ADAPTATION_HANDOFF.md.

## Fixed design

The study has four paired cells: baseline and CAM-Full at seeds 42 and 43.
Cell order is baseline/CAM-Full for seed 42 and CAM-Full/baseline for seed 43.
The canonical codegen split `data/spreadsheetbench_split` remains 80 train / 40
validation / 280 held-out test items. The metadata-only `spreadsheetbench_id_split`
is not used because it lacks the instruction field required by codegen.
Training uses a fixed 20-item batch per step for four epochs, producing four
steps total (one per epoch). Epoch steps alternate Cell-Level, Sheet-Level,
Cell-Level, Sheet-Level. Each seed has a deterministic, homogeneous batch of
20 task IDs for each type; the type batch recurs unchanged at its second
appearance. Both arms in a seed pair use the same task IDs.

The source test split is never replaced. Before outcomes are observed, a
deterministic metadata-stratified subset of 120 test items is selected: 83
Cell-Level and 37 Sheet-Level, sampled with selection seed 20261018 and kept
in source order. Its IDs, counts, and hashes are saved in the freeze and a
standalone test subset manifest. The trainer's initial-skill test is disabled;
after training, the authorized best skill is evaluated once on this fixed
subset. There is no separate final-skill test outcome.

The primary outcome is `test_hard` on the one fixed subset. Report both task
type buckets, paired per-seed differences, update/gate traces, and returned
token and wall-time cost where available. Two training seeds support only an
exploratory conclusion. Task-level uncertainty does not estimate
training-seed variability. Do not change the schedule, subset, or analysis in
response to outcomes.

## Freeze and launch

Prepare the cell configs, exact train task IDs, test subset, and source/data
fingerprints without model calls:

```powershell
& 'E:\MiniConda\envs\skillopt\python.exe' -X utf8 scripts/run_continuous_adaptation_v1.py --prepare
```

Review the generated freeze before launch. The command to launch all four
cells is:

```powershell
& 'E:\MiniConda\envs\skillopt\python.exe' -X utf8 scripts/run_continuous_adaptation_v1.py --run --lanes 1
```

Artifacts are isolated under `outputs/studies/continuous_adaptation_v1/`,
`outputs/continuous_adaptation_v1/`, and
`reports/continuous_adaptation_v1/`. A changed input fingerprint or
incompatible existing cell config stops execution. Only cells that pass the
full completion and evidence checks are skipped. Any partial or unaccepted
cell stops the queue; archive it and use an explicitly revised output
namespace before continuing.

## Additive extension policy

Future experiments may add new versioned namespaces, each with its own frozen
protocol, fingerprints, output roots, cells, and prespecified analysis.
Preserve v1's four-cell denominator and original test subset. Do not append
cells after observing outcomes, replace v1's test subset, change its schedule,
or fold later results into v1's primary outcome. Any added seeds, target
conditions, datasets, or schedules are new cells in a new version and must be
reported as additions alongside v1.

## Preflight correction

The first preflight selected the metadata-only ID split and stopped before a
model call because its rows lacked `instruction`. The failed freeze and
incomplete setup are retained under
`outputs/studies/incidents/continuous_adaptation_v1_id_split_schema_preflight_20261018/`.
The corrected freeze uses the canonical split, which has the same task IDs and
split/type counts and includes the codegen prompt fields. No outcome data was
produced by the failed preflight.

