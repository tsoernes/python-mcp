# Incremental job output

This is the current output contract for smart-async jobs. The old `job-stream://{job_id}` resource, `stream` launch parameter, and `py_get_job_output` tool are not part of the current MCP API. See [Job management contract](JOB_MANAGEMENT.md).

## Reading output

When an execution call switches to a background job, use `py_job_status` with its returned ID:

```text
py_job_status(job_id="<job_id>", incremental=true, markdown_output=false)
```

For a running job, the `job` object can include:

- `partial_stdout` and `partial_stderr`: current output snapshots.
- `new_stdout` and `new_stderr`: output newly captured since the prior incremental status read in this server process.

`incremental=false` returns the current status without advancing output offsets. Once complete, inspect `job.result.stdout` and `job.result.stderr` for the final captured output. The live partial fields are cleared when the job completes because the final result is authoritative.

The current execution tool `py_run_script_in_dir` forwards child-process stdout and stderr while a background job is running. Other tools may expose output in their final result but do not necessarily publish live partial output. Do not assume every tool streams live output merely because it uses smart-async.

## Suggested polling pattern

1. Save `job_id` from the background response.
2. Request status with `incremental=true` if only newly observed output is useful, or `incremental=false` for a full status snapshot.
3. Consume `new_stdout` / `new_stderr` once; do not append the full `partial_*` snapshot repeatedly.
4. Continue while status is `pending` or `running`.
5. After completion, use the final result fields rather than expecting additional incremental output.

Use `markdown_output=false` for programmatic handling. No separate resource subscription or `enable_streaming` option is required or currently registered.
