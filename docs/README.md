# Learning Stagecraft

This guide takes you from nothing to designing your own pipelines. Each chapter adds one layer
on top of the previous ones, and all of them build the same project: a CI pipeline called
`service-ci` that checks out a service, lints and tests it, builds an image, optionally deploys
it, reports GitHub checks, and runs nightly.

Read it in order the first time. Afterwards, the [reference](reference.md) has everything on one
page.

## The path

| Chapter | You'll learn | Builds on |
|---|---|---|
| [1. The mental model](01-mental-model.md) | Pipelines, stages, and blocks; the four moments of a pipeline's life; who owns what | - |
| [2. Your first pipeline](02-first-pipeline.md) | Setting up a pipelines repo; a first block and pipeline; `check`, `render`, `graph` | 1 |
| [3. Blocks](03-blocks.md) | Writing reusable blocks: fields, `compile`, names, what a container can do, testing | 2 |
| [4. Helm values and the contract](04-values.md) | Per-installation configuration and the `values.schema.json` contract | 3 |
| [5. Stages, params, and conditions](05-stages-and-flow.md) | How stages run, per-run params, `when` conditions | 4 |
| [6. Handlers](06-handlers.md) | `on_success`, `on_failure`, and `finally_` on blocks, stages, and the pipeline | 5 |
| [7. Commit statuses](07-commit-statuses.md) | GitHub checks for the pipeline and its steps, through your own reporter block | 6 |
| [8. The workspace](08-workspace.md) | Sharing a checkout between steps, and the `containerset` strategy's rules | 7 |
| [9. Triggers](09-triggers.md) | Running pipelines on a schedule | 8 |
| [10. Shipping pipelines](10-shipping.md) | Rendering into a chart, and the checks on both sides | 9 |
| [11. Designing pipelines](11-designing-pipelines.md) | The finished pipeline; exactly what you can customize; patterns; troubleshooting | all |

## Before you start

You'll need Python 3.12+ and [uv](https://docs.astral.sh/uv/). Familiarity with containers helps;
you don't need to know Argo Workflows or Helm in depth, but you'll meet both. To run pipelines,
you need a cluster with Argo Workflows 4.1.6 and Helm 4; to render them, you don't.

## How to read the code

Code blocks marked with a file name in the source (for example `blocks.py` or
`pipelines/service_ci.py`) are complete files of the project; a later chapter's version of a file
replaces the earlier one. Other snippets illustrate a point and aren't part of the project.
Every complete file is compiled by Stagecraft's own test suite, so the examples work with this
version.
