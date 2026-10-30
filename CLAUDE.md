# CLAUDE.md

This file provides guidance to Claude Code when working in this repository.

<!-- vergil:template:claude-md:begin -->
## Memory

Never write, edit or delete memory files without explicit human approval.
Propose the entry and where it belongs first, then wait for the decision.

## Worktrees

- Sessions always start at the project root, never inside `.worktrees/`.
- One worktree per issue: `.worktrees/issue-<N>-<slug>/` on branch
  `feature/<N>-<slug>`. Remove it once its branch lands.
- The main worktree is read-only, bar ignored `build/` scratch; every edit goes through a worktree.
- Read, Edit and Write use the worktree's absolute path. Bash commands `cd`
  into the worktree first or use absolute paths.
- The full convention: <https://github.com/vergil-project/vergil-tooling/blob/develop/docs/specs/worktree-convention.md>.
- Brief each parallel sub-agent with its [Agent prompt contract](https://github.com/vergil-project/vergil-tooling/blob/develop/docs/specs/worktree-convention.md#agent-prompt-contract).

## Git and GitHub

Use `vrg-git` and `vrg-gh` only; the hook guard blocks raw `git` and `gh`. If a
command is not available through the wrappers, explain why and ask the human to
run it with `! <command>` in the prompt.

## Validation

`vrg-container-run -- vrg-validate` is the only validation command. Don't run
linters, formatters or tests outside it; a tool it doesn't invoke is not part
of the pipeline.

## PR handoff

- Agents never run `vrg-submit-pr`. Submitting, merging and finalizing a PR are
  human actions.
- Hand off with `vrg-pr-workflow report-ready --issue <N> --title --summary
  --notes`. It pushes the branch itself. Leave `--linkage` at its default (`Ref`).
- A delta touching `.github/workflows/**` makes GitHub refuse the agent push.
  That is expected: report-ready records the state anyway and the human's
  `vrg-submit-pr` pushes the branch.
- After report-ready the branch is frozen: no new commits and no push that
  advances it; more work is a new follow-up issue. Re-running report-ready to
  fix the prose is allowed. `vrg-pr-workflow unfreeze` is the only way to lift it.
- The report-ready title, summary and notes are public (the relay ref is
  world-readable): never put secrets in them.

## Local settings

`.claude/settings.local.json` must stay absent or exactly `{}`; the agent tools
refuse otherwise. See the [ownership model](https://github.com/vergil-project/vergil-tooling/blob/develop/docs/site/docs/guides/claude-config-ownership.md).

## Engineering principles

- Correctness over cost over speed. Never ship known-broken code with a
  follow-up fix; if something is wrong, fix it now.
- No silent failures: no swallowed exceptions, no `except: return ""`, no
  fallbacks that hide errors. A failure hidden in code becomes a hallucination
  at the AI layer.

## paad

paad usage policy and deviations:
<https://github.com/vergil-project/vergil-claude-plugin/blob/develop/docs/paad-policy.md>.
<!-- vergil:template:claude-md:end -->

## Project overview

`mqro` collects IBM MQ HA/DR state that MQI cannot reach (Native HA, RDQM,
Pacemaker/DRBD), using only the CLI, and writes it as Prometheus metrics. It
also renders the Grafana dashboards that read those metrics, as a supplement
to the stock `mq_prometheus` exporter. The integration prerequisites and the
metrics contract are in the [README](README.md).

- `src/mqro/collectors/`: one collector per cluster technology, plus the shared
  DRBD parser.
- `src/mqro/detect.py`: decides which collector a node runs. An explicit
  operator choice always wins. Auto-detection goes by precedence, so an RDQM
  node never also runs the generic Pacemaker collector.
- `src/mqro/dashboards/`: builds Grafana dashboard JSON from a
  `DashboardProfile`. Output is deterministic, and no lab-specific name may be
  hardcoded.
- `tests/fixtures/`: captured CLI output that the collector tests parse.

This repo owns no VM. Its sessions run inside the
`mq-resiliency-lab-for-linux` box (`[vm] shared_from` in `vergil.toml`).

## Constraints

- **The runtime is stdlib-only.** The collectors are deployed verbatim onto
  production MQ nodes, so `[project].dependencies` in `pyproject.toml` stays
  empty. Never add `pymqi` or any third-party package to the runtime; tools
  used only in development go in the `dev` dependency group.
- **`src/mqro/contract.py` is the authoritative emitted-metrics contract.**
  When you add, rename or relabel a metric family, update the contract in the
  same change. `tests/test_contract.py` checks in both directions that the
  contract matches what the collectors emit.
- **Never change node_exporter's configuration.** The textfile directory is an
  operator input: the collectors check it and report a failure loudly, but
  never reconfigure node_exporter or change the directory's permissions
  (README, "Integration").
