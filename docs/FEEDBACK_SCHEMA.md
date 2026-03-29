# Feedback Data Schema

This document describes exactly what data Echo Guard collects at each consent tier.

## Schema Version: 1

### Payload Envelope

Every upload contains:

| Field | Type | Example |
|-------|------|---------|
| `schema_version` | string | `"1"` |
| `echo_guard_version` | string | `"0.4.1"` |
| `model_name` | string | `"codesage-small"` |
| `consent_tier` | string | `"public"` or `"private"` |
| `language_distribution` | object | `{"python": 45, "typescript": 30}` |
| `upload_timestamp` | string | ISO 8601 timestamp |
| `records` | array | See below |

### Private Tier Records

Each record contains **only** anonymized structural features and the user's verdict. No source code, file paths, function names, or repository identifiers are included.

| Field | Type | Description |
|-------|------|-------------|
| `type` | string | Always `"feedback"` |
| `verdict` | string | `"true_positive"`, `"false_positive"`, or `"ignore"` |
| `match_type` | string | `"exact_structure"` or `"embedding_semantic"` |
| `similarity_score` | float | Cosine similarity (0.0-1.0) |
| `severity` | string | `"extract"` or `"review"` |
| `reuse_type` | string | `"direct_import"`, `"reference_only"`, etc. |
| `source_language` | string | Language of the source function |
| `source_param_count` | int | Number of parameters |
| `source_has_return` | bool | Whether function has a return statement |
| `source_line_count` | int | Number of lines |
| `source_call_count` | int | Number of function calls made |
| `source_visibility` | string | `"public"` or `"private"` |
| `source_is_nested` | bool | Whether function is nested inside another |
| `source_has_class` | bool | Whether function belongs to a class |
| `existing_language` | string | Language of the existing function |
| `existing_param_count` | int | Number of parameters |
| `existing_has_return` | bool | Whether function has a return statement |
| `existing_line_count` | int | Number of lines |
| `existing_call_count` | int | Number of function calls made |
| `existing_visibility` | string | `"public"` or `"private"` |
| `existing_is_nested` | bool | Whether function is nested inside another |
| `existing_has_class` | bool | Whether function belongs to a class |
| `same_language` | bool | Whether both functions are the same language |
| `same_file` | bool | Whether both functions are in the same file |
| `same_class` | bool | Whether both functions are in the same class |
| `same_cluster` | bool | Whether both are in the same dependency cluster |
| `crosses_service_boundary` | bool | Whether functions span service boundaries |
| `ast_hash_match` | bool | Whether AST hashes are identical |
| `name_similarity` | float | Edit distance ratio of function names (0.0-1.0) |
| `param_count_diff` | int | Absolute difference in parameter counts |
| `shared_calls_ratio` | float | Ratio of shared function calls (0.0-1.0) |
| `line_count_ratio` | float | Ratio of smaller/larger line count (0.0-1.0) |
| `dismissed_reason` | string | User-provided reason (if any) |
| `filter_matched` | string | Which intent filter would have caught this |
| `cluster_id` | string | Hash linking related findings |
| `cluster_size` | int | Number of copies in the cluster |

**Explicitly NOT included:** source code, file paths, function names, repository URL, git remote, commit hashes, or any other identifying information.

### Public Tier Records

Public tier includes everything from the private tier, **plus** training pair records containing source code.

Each training pair record contains:

| Field | Type | Description |
|-------|------|-------------|
| `type` | string | Always `"training_pair"` |
| `verdict` | string | `"clone"` or `"not_clone"` |
| `language` | string | Programming language |
| `source_code_a` | string | Full source code of function A |
| `source_code_b` | string | Full source code of function B |
| `embedding_score` | float | Current model's cosine similarity |
| `clone_type` | string | Clone type classification |
| `probe_type` | string | How the pair was collected |
| `cluster_id` | string | Hash linking related findings |
| `cluster_size` | int | Number of copies in the cluster |

**Explicitly stripped before upload:** `filepath_a`, `filepath_b`, `function_name_a`, `function_name_b`. No repository URL or identifying information is included.

### Scan Event Records (both tiers)

Uploaded after every `scan` and `check` command. Contains only aggregate counts — no code, paths, or function names.

| Field | Type | Description |
|-------|------|-------------|
| `type` | string | Always `"scan_event"` |
| `command` | string | `"scan"` or `"check"` |
| `total_findings` | int | Number of findings detected |
| `extract_count` | int | Findings with `extract` severity (3+ copies) |
| `review_count` | int | Findings with `review` severity (2 copies) |
| `total_functions` | int | Total functions in the index |
| `cross_language` | int | Findings spanning different languages |
| `cross_service` | int | Findings spanning service boundaries |

**Explicitly NOT included:** file paths, function names, source code, repository URL, or any identifying information. Only aggregate counts.

### None Tier

Nothing is uploaded. All data remains in local `.echo-guard/index.duckdb`.

### Summary: What each tier sends

| Data | Private | Public | None |
|------|:---:|:---:|:---:|
| Scan events (aggregate counts per session) | ✅ | ✅ | — |
| Feedback records (anonymized structural features + verdict) | ✅ | ✅ | — |
| Training pairs (source code, paths/names stripped) | — | ✅ | — |

## Auditing

All upload code is open source in [`echo_guard/upload.py`](../echo_guard/upload.py). Run `echo-guard feedback-preview` to see exactly what would be sent before any upload occurs.

Uploads can be disabled at any time:
- Set consent to `none`: `echo-guard consent none`
- Environment variable: `DO_NOT_TRACK=1` or `ECHO_GUARD_NO_UPLOAD=1`
