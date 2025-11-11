# Codex Flow Optimizer - Combined Results (Data 1–20)

**Generated:** 2025-10-31 03:32:00 (aggregated from result_stats_20251030_230222.md and result_stats_20251031_000055.md)

## Parameters

- **Python Version:** Python 3.13.2
- **Optimizer venv:** .venv
- **Validator venv:** .venv-ansible-nav
- **Batch Sizes Processed:** [3, 5, 5, 5, 5]
- **Data Processed:** 20
- **Max Iterations for Validator allowed:** 3

## Dataset Results

| Data Index | Time | Status | Has improved_yaml_C | is_diff_yaml_A_C | is_diff_yaml_B_C | C_pass_all_objective_val |
|---------|------|--------|---------------------|------------------|------------------|--------------------------|
| 1 | 9m 08s | ✅ Success | true | true | false | false |
| 2 | 16m 03s | ✅ Success | true | true | true | false |
| 3 | 6m 17s | ✅ Success | true | true | false | false |
| 4 | 2h 10m 01s | ✅ Success | true | true | true | true |
| 5 | 42m 49s | ✅ Success | true | true | true | true |
| 6 | 1h 45m 32s | ✅ Success | true | true | true | false |
| 7 | 7m 09s | ✅ Success | true | true | true | false |
| 8 | 1h 34m 35s | ✅ Success | true | true | true | true |
| 9 | 12m 06s | ✅ Success | true | true | false | false |
| 10 | 16m 23s | ✅ Success | true | true | true | true |
| 11 | 12m 03s | ✅ Success | true | true | true | true |
| 12 | 34m 36s | ✅ Success | true | true | true | false |
| 13 | 14m 10s | ✅ Success | true | true | true | false |
| 14 | 17m 12s | ✅ Success | true | true | true | true |
| 15 | 9m 02s | ✅ Success | true | true | true | true |
| 16 | 16m 23s | ✅ Success | true | true | true | false |
| 17 | 12m 14s | ✅ Success | true | true | false | true |
| 18 | 6m 55s | ✅ Success | true | true | true | true |
| 19 | 19m 40s | ✅ Success | true | true | true | true |
| 20 | 27m 55s | ✅ Success | true | true | false | false |
| **Summary** | **30m 31s avg** | **100% success** | **100% true** | **100% true** | **75% true** | **50% true** |

## Batch Statistics

*Average Playbook Optimization Time* represents the mean runtime per playbook optimization within each batch. *Batch Time* is the total elapsed time to process the entire batch. The summary row averages these two columns across all batches.

| Batch | Range | Processed | Successful | Failed | Average Playbook Optimization Time | Batch Time |
|-------|-------|-----------|-----------|--------|--------------|------------|
| 1 | 1-3 | 3 | 3 | 0 | 10m 29s | 16m 04s |
| 2 | 4-8 | 5 | 5 | 0 | 1h 16m 01s | 2h 10m 01s |
| 3 | 9-13 | 5 | 5 | 0 | 17m 51s | 34m 36s |
| 4 | 14-18 | 5 | 5 | 0 | 12m 21s | 17m 12s |
| 5 | 19-20 | 2 | 2 | 0 | 23m 47s | 27m 56s |
| **Summary** | — | — | — | — | **28m 56s avg** | **45m 10s avg** |

## Overall Statistics

*Average Playbook Optimization Time* here is the mean runtime per playbook optimization across all 20 data. *Total Batch Time* is the aggregate wall-clock time spent running all batches (i.e., the combined execution window for the full dataset 1–20).

| Metric | Value |
|--------|-------|
| Batches Run | 5 |
| Batch Sizes | 3, 5, 5, 5, 5 |
| Playbooks Processed | 20 |
| Successful | 20 |
| Failed | 0 |
| Average Playbook Optimization Time | 30m 31s |
| Total Batch Time | 3h 45m 49s |

## Logs

Individual optimizer logs saved as:
- `optimizer_1.log`
- `optimizer_2.log`
- `optimizer_3.log`
- `optimizer_4.log`
- `optimizer_5.log`
- `optimizer_6.log`
- `optimizer_7.log`
- `optimizer_8.log`
- `optimizer_9.log`
- `optimizer_10.log`
- `optimizer_11.log`
- `optimizer_12.log`
- `optimizer_13.log`
- `optimizer_14.log`
- `optimizer_15.log`
- `optimizer_16.log`
- `optimizer_17.log`
- `optimizer_18.log`
- `optimizer_19.log`
- `optimizer_20.log`
