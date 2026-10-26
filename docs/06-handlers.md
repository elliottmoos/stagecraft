# 6. Handlers

Some work belongs *after* a step rather than in it: telling a channel the tests failed, uploading
reports whatever the outcome, cleaning up. Handlers are steps that run when a step, a stage, or
the pipeline finishes.

## Three kinds

| Field | Runs when the step | Available on |
|---|---|---|
| `on_success` | succeeds | steps, stages, the pipeline |
| `on_failure` | fails (or errors) | steps, stages, the pipeline |
| `finally_` | finishes either way | steps, stages |

Each takes a list of actions, which run in parallel: any action, the same kind you'd put in a
stage. As in a stage, a bare action is named after its class; wrap it in `Step` to name it.

```python
Stage("verify", [...], on_failure=[Notify(message="verify failed")])
Step("unit-tests", Make(target="test"), finally_=[UploadReports()])
Pipeline(..., on_success=[...], on_failure=[...])
```

## What a handler knows: `{{ step.* }}`

Inside a handler's fields, two placeholders describe what it's attached to:

| Placeholder | In a stage or step handler | In a pipeline handler |
|---|---|---|
| `{{ step.name }}` | the path: `verify` or `verify/unit-tests` | the run's name, such as `service-ci-x7k2p` |
| `{{ step.outcome }}` | `succeeded` or `failed` | the run's outcome: `succeeded` or `failed` |

Outcomes are Stagecraft's own words, not Argo's: an Argo `Error` (for example, a pod that couldn't
start) counts as `failed`. A `finally_` handler sees whichever outcome happened.

`{{ step.* }}` only works in handlers; anywhere else it fails the render. Argo doesn't expose a
failed step's exit code or error message to handlers, so neither does Stagecraft.

## Growing the example

A `Notify` action posts a message to a chat channel. Here it only prints the message, standing in
for whatever chat API you use. Its channel defaults to a Helm value, so authors rarely set it:

```python file=actions.py
import re

from hera.workflows import Container, Env
from hera.workflows.models import ResourceRequirements
from pydantic import field_validator

from stagecraft.context import CompileContext
from stagecraft.spec import Action


class Shell(Action):
    """Runs a shell script in the chart's shell image."""

    script: str

    def compile(self, ctx: CompileContext) -> Container:
        return Container(
            image=ctx.value("images.shell", str),
            command=["sh", "-c"],
            args=[self.script],
        )


class Make(Action):
    """Runs one make target in the chart's build image, with the chart's build resources.

    `variables` are passed as make variables (NAME=value).
    """

    target: str
    variables: dict[str, str] = {}

    @field_validator("target")
    @classmethod
    def _one_target(cls, target: str) -> str:
        if not re.fullmatch(r"[A-Za-z0-9_.-]+", target):
            raise ValueError(f"target must be a single make target, not {target!r}")
        return target

    def compile(self, ctx: CompileContext) -> Container:
        return Container(
            image=ctx.value("images.build", str),
            command=["make"],
            args=[self.target, *(f"{k}={v}" for k, v in self.variables.items())],
            resources=ResourceRequirements(
                requests={
                    "cpu": ctx.value("build.cpu", str),
                    "memory": ctx.value("build.memory", str),
                },
            ),
        )


class Notify(Action):
    """Posts a message to a chat channel (this stand-in prints it)."""

    message: str
    channel: str = "{{ values.notify.channel }}"

    def compile(self, ctx: CompileContext) -> Container:
        return Container(
            image=ctx.value("images.shell", str),
            command=["sh", "-c"],
            args=['echo "[$CHANNEL] $MESSAGE"'],
            env=[Env(name="CHANNEL", value=self.channel), Env(name="MESSAGE", value=self.message)],
        )
```

`Notify` passes its fields as environment variables, so the message can contain anything without
breaking the script (chapter 3).

The pipeline tells the channel when verification fails, and how every run ends:

```python file=pipelines/service_ci.py
from stagecraft.conditions import param
from stagecraft.spec import Param, Pipeline, Stage, Step

from actions import Make, Notify, Shell

pipeline = Pipeline(
    name="service-ci",
    params=[
        Param("repo", required=True, description="Repository, as owner/name"),
        Param("sha", required=True, description="Commit to build"),
        Param("push", default="false", enum=["true", "false"], description="Push the image"),
        Param("deploy", default="false", enum=["true", "false"], description="Deploy to staging"),
    ],
    stages=[
        Stage("prepare", [Step("fetch", Shell(script="echo fetching {{ param.repo }}"))]),
        Stage(
            "verify",
            [
                Step("lint", Make(target="lint")),
                Step("unit-tests", Make(target="test")),
            ],
            on_failure=[Notify(message="{{ step.name }} failed for {{ param.sha }}")],
        ),
        Stage(
            "package",
            [Step("image", Make(target="image", variables={"PUSH": "{{ param.push }}"}))],
        ),
        Stage(
            "deploy",
            [Step("staging", Make(target="deploy-staging"))],
            when=param("deploy") == "true",
        ),
    ],
    on_failure=[Notify(message="{{ step.name }} {{ step.outcome }}")],
)
```

Each installation sets the channel:

```yaml file=values/staging.yaml
images:
  shell: alpine:3.22
  build: golang:1.25
build:
  cpu: "1"
  memory: 2Gi
notify:
  channel: "#ci"
```

## When handlers run, and when they don't

- **A step skipped by `when` runs no handlers**, not even `finally_`.
- **A step that never starts** (because an earlier stage failed) runs no handlers either.
- **Stopping a run** (`argo stop`) runs pipeline and stage handlers as `failed`, but not the
  handlers of a step that was still running. Terminating it (`argo terminate`) runs
  none.
- Handlers run after the step finishes. Don't assume a stage's handlers finish before the next
  stage starts.

## When a handler fails

- **A failing pipeline handler fails the pipeline**, even if every stage succeeded. Handler
  failures are unexpected, and a build that couldn't report its result shouldn't look green.
- **A failing stage or step handler** shows as failed in the Argo UI but **doesn't** fail the
  pipeline. That's an Argo limitation, not a choice: hooks on individual steps can't fail a
  workflow.

## Rules for handlers

A handler only runs. So a handler `Step` can set a name but not `when`, `status`, or its own
handlers. Handler names must be unique within each list
(`on_failure=[Notify(...), Step("page", Notify(...))]`).

## Recap

- `on_success`, `on_failure`, and `finally_` attach actions to a step, a stage, or (the first two)
  the pipeline.
- `{{ step.name }}` and `{{ step.outcome }}` tell a handler what happened.
- Skipped and never-started steps run no handlers. Only pipeline handler failures fail the
  pipeline.

Next: [7. Sources](07-sources.md).
