# Review of the supplied scripts

The supplied archive contains three different concerns:

1. `pyhistra`: a separate clean-room HRX reader/numerical project;
2. `modelxml`: HRX selectors and mutations;
3. `histra_automation`: scenario generation, solver execution and result extraction.

The existing `pyproject.toml` builds only `pyhistra`, so the automation code is
not currently installed as a package. It also declares no runtime dependencies
for the automation modules even though some of them import NumPy, SciPy and
pandas.

The local runner was separated because the client should not need scenario
sampling or server-side model generation. The following behaviours were also
changed:

- machine paths moved from a repository-level global config to an explicit
  runner configuration passed to the package;
- `run_scenario` no longer catches errors and then returns as though execution
  finished; failed jobs now raise a typed exception and write durable evidence;
- temporary raw files are retained by default, which is required before a
  future server acknowledgement;
- timeout cleanup targets the process tree created for that job rather than all
  `SolverHistra.exe` processes on the computer;
- extraction uses read-only SQLite queries directly and does not load entire
  result tables into pandas dataframes;
- every attempt receives a unique workspace, so retries do not overwrite the
  evidence from previous attempts;
- analysis dependencies are resolved from `InitialAnalysisKey`, and completed
  dependency states are preserved between solver calls;
- status is recorded in `state.json` instead of relying only on console output.

The scenario-generation, material-update and load-position code can now be
developed as a server/model-builder package independently from this runner.
The exception is the existing foundation-interface scour mutation. Because the
material state must change between sequential HiStrA analyses, the functions
`run_update_foundation_ifaces`, `update_foundation_interfaces`,
`set_default_interface` and `_select_outside_delta_interfaces` are retained in
the client runner and executed immediately before the relevant analysis.
