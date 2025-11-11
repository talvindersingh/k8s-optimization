# Dataset Classifier Pipeline

This directory holds the cleaned Ansible dataset and staging outputs for the dependency classifier.

## Pipeline Overview
- `pipeline/dependency_pipeline.py` parses each JSONL record’s playbook, infers required Ansible collections & Python packages, and classifies records into four groups: `azure`, `aws`, `cross-azure-aws`, and `non-azure-aws`.
- By default, dependency summaries are written to `output/*.json` and grouped records to `output/jsonl/*.jsonl`. Supplying `--output-dir` lets you point to any other staging directory (the `jsonl/` subfolder is created automatically).

## How to Run
```bash
python3 ansible_optimizer/dataset_classifier/pipeline/dependency_pipeline.py \
  --input ansible_optimizer/dataset_classifier/cleaned_ansible_data.jsonl \
  --limit 10   # drop the flag to process the full dataset
```

Outputs are overwritten on each run; copy any artifacts you need before rerunning.
