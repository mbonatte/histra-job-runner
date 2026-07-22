# Future network adapter contract

The HTTPS worker should depend on the public `histra_runner` API rather than
calling private implementation functions.

```python
from histra_runner import JobRunner, load_runner_config

config = load_runner_config("runner.toml")
outcome = JobRunner(config).run_job_file("inbox/job-123/job.json")
```

After a successful upload, the adapter may delete `outcome.workspace / "run"`.
Before acknowledgement, it must preserve the workspace so an interrupted
upload can be retried without rerunning HiStrA.

Suggested mapping:

- downloaded / validated: runner `validating` and `staging` states;
- active lease heartbeat: any `running` or `extracting` state;
- upload payload: `output/results.json`, `output/run.json`, logs selected by
  server policy;
- server acceptance: local raw-output cleanup;
- upload retry: reuse the completed attempt workspace, never rerun the solver.
