# 9. Triggers

Some runs should happen on a schedule: a nightly build of the main branch, a weekly dependency
check. A **trigger** runs the pipeline on a schedule.

## Declaring a trigger

```python
Trigger.cron("0 2 * * *", name="nightly", params={"revision": "main", "push": "true"})
```

- **The schedule** is a five-field cron expression (`minute hour day month weekday`) or a macro:
  `@hourly`, `@daily` (or `@midnight`), `@weekly`, `@monthly`, `@yearly` (or `@annually`),
  `@every 6h`. It's checked at render time.
- **`timezone`** is an IANA name such as `"Europe/Amsterdam"`. Without it, the schedule uses the
  workflow controller's time zone.
- **`params`** are the run's params. Every required param must be set, every key must be a
  declared param, and values must be in the param's `enum`. Values may use
  `{{ values.a.b }}`, for example to build a branch set per installation.
- **`name`** defaults to `cron`; each trigger in a pipeline needs a distinct name.

## What it compiles to

Each trigger becomes an Argo `CronWorkflow` named `<pipeline>-<name>`, written into the
pipeline's file after its `WorkflowTemplate`. It doesn't copy the pipeline: it refers to the
template by name and supplies the params, so scheduled runs are exactly the pipeline you
reviewed. They appear in the Argo UI like any other run, started by the cron workflow.

Argo limits `CronWorkflow` names to 52 characters, so `<pipeline>-<name>` must fit; the render
fails otherwise.

## Growing the example

A nightly build of `main` that pushes its image:

```python file=pipelines/service_ci.py
from stagecraft.spec import Param, Pipeline, Source, Stage, Step, Trigger

from actions import GitHubStatus, Make, Notify

pipeline = Pipeline(
    name="service-ci",
    params=[
        Param("revision", default="main", description="Branch, tag, or commit to build"),
        Param("push", default="false", enum=["true", "false"], description="Push the image"),
        Param("deploy", default="false", enum=["true", "false"], description="Deploy to staging"),
    ],
    source=Source(url="https://github.com/example/service.git", revision="{{ param.revision }}"),
    status="ci/pipeline",
    status_reporter=GitHubStatus(),
    stages=[
        Stage(
            "verify",
            [
                Step("lint", Make(target="lint")),
                Step("unit-tests", Make(target="test")),
            ],
            status="ci/verify",
            on_failure=[Notify(message="{{ step.name }} failed at {{ source.sha }}")],
        ),
        Stage(
            "package",
            [Step("image", Make(target="image", variables={"PUSH": "{{ param.push }}"}))],
            status="ci/package",
        ),
        Stage(
            "deploy",
            [Step("staging", Make(target="deploy-staging"))],
            when="{{ param.deploy }} == 'true'",
            status="ci/deploy",
        ),
    ],
    on_failure=[Notify(message="{{ step.name }} {{ step.outcome }}")],
    triggers=[
        Trigger.cron(
            "0 2 * * *",
            name="nightly",
            timezone="Europe/Amsterdam",
            params={"revision": "main", "push": "true"},
        ),
    ],
)
```

This renders a `CronWorkflow` named `service-ci-nightly`. Each night's run resolves `main` to
its head commit when it starts, so the whole run, and its commit statuses, use that commit.

## Things to know

- **The first ticks after an install can be missed.** Helm creates a release's `CronWorkflow`s
  before its `WorkflowTemplate`s, and Argo marks a cron whose template doesn't exist yet as
  invalid until it checks again, a few minutes later (at most 20). This only affects a brand-new
  trigger or pipeline; Kubernetes resources are eventually consistent anyway.
- **A trigger can't be switched off per installation yet.** Every environment that installs the
  chart runs the schedule.
- **One schedule per trigger.** For two schedules, add two triggers.

## Recap

- `Trigger.cron(schedule, name=, params=, timezone=)` runs the pipeline on a schedule.
- It compiles to a `CronWorkflow` that refers to the pipeline's template, in the same file.
- Required params must be set; values may come from Helm values.

Next: [10. Shipping pipelines](10-shipping.md).
