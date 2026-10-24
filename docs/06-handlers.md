# 6. Handlers

Some work belongs *after* a step rather than in it: telling a channel the tests failed, uploading
reports whatever the outcome, cleaning up. Handlers are blocks that run when a step finishes.

## Three kinds

| Field | Runs when the step | Available on |
|---|---|---|
| `on_success` | succeeds | blocks, stages, the pipeline |
| `on_failure` | fails (or errors) | blocks, stages, the pipeline |
| `finally_` | finishes either way | blocks, stages |

Each takes a list of ordinary blocks, which run in parallel. A handler is any block, the same
kind you'd put in a stage.

```python
Stage("verify", [...], on_failure=[Notify(message="verify failed")])
Make(name="unit-tests", target="test", finally_=[UploadReports()])
Pipeline(..., on_success=[...], on_failure=[...])
```

## What a handler knows: `{{ step.* }}`

Inside a handler's fields, two placeholders describe the step it's attached to:

| Placeholder | In a stage or block handler | In a pipeline handler |
|---|---|---|
| `{{ step.name }}` | the step's path: `verify` or `verify/unit-tests` | the run's name, such as `service-ci-x7k2p` |
| `{{ step.status }}` | `Succeeded` or `Failed` | the run's status: `Succeeded`, `Failed`, or `Error` |

`{{ step.* }}` only works in handlers; anywhere else it fails the render. Argo doesn't expose a
failed step's exit code or error message to handlers, so neither does Stagecraft.

## Growing the example

A `Notify` block posts a message to a chat channel. Here it only prints the message, standing in
for whatever chat API you use. Its channel defaults to a Helm value, so authors rarely set it:

```python file=blocks.py
import re

from hera.workflows import Container, Env
from hera.workflows.models import ResourceRequirements
from pydantic import field_validator

from stagecraft.context import CompileContext
from stagecraft.spec import Block


class Shell(Block):
    """Runs a shell script in the chart's shell image."""

    script: str

    def compile(self, ctx: CompileContext) -> Container:
        return Container(
            image=ctx.value("images.shell", str),
            command=["sh", "-c"],
            args=[self.script],
        )


class Make(Block):
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


class Notify(Block):
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
from stagecraft.spec import Param, Pipeline, Stage

from blocks import Make, Notify, Shell

pipeline = Pipeline(
    name="service-ci",
    params=[
        Param("repo", required=True, description="Repository, as owner/name"),
        Param("sha", required=True, description="Commit to build"),
        Param("push", default="false", enum=["true", "false"], description="Push the image"),
        Param("deploy", default="false", enum=["true", "false"], description="Deploy to staging"),
    ],
    stages=[
        Stage("prepare", [Shell(name="fetch", script="echo fetching {{ param.repo }}")]),
        Stage(
            "verify",
            [
                Make(name="lint", target="lint"),
                Make(name="unit-tests", target="test"),
            ],
            on_failure=[Notify(message="{{ step.name }} failed for {{ param.sha }}")],
        ),
        Stage(
            "package",
            [Make(name="image", target="image", variables={"PUSH": "{{ param.push }}"})],
        ),
        Stage(
            "deploy",
            [Make(name="staging", target="deploy-staging")],
            when="{{ param.deploy }} == 'true'",
        ),
    ],
    on_failure=[Notify(message="{{ step.name }} ended {{ step.status }}")],
)
```

The chart provides the channel:

```yaml file=chart/values.yaml
images:
  shell: alpine:3.22
  build: golang:1.25
build:
  cpu: "1"
  memory: 2Gi
notify:
  channel: "#ci"
```

```json file=chart/values.schema.json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "type": "object",
  "required": ["images", "build", "notify"],
  "properties": {
    "images": {
      "type": "object",
      "required": ["shell", "build"],
      "properties": {
        "shell": {"$ref": "#/$defs/image"},
        "build": {"$ref": "#/$defs/image"}
      }
    },
    "build": {
      "type": "object",
      "required": ["cpu", "memory"],
      "properties": {
        "cpu": {"type": "string", "description": "CPU request for build steps"},
        "memory": {"type": "string", "description": "Memory request for build steps"}
      }
    },
    "notify": {
      "type": "object",
      "required": ["channel"],
      "properties": {
        "channel": {"type": "string", "description": "Chat channel for build notifications"}
      }
    }
  },
  "$defs": {
    "image": {"type": "string", "description": "A container image reference"}
  }
}
```

## When handlers run, and when they don't

- **A step skipped by `when` runs no handlers**, not even `finally_`.
- **A step that never starts** (because an earlier stage failed) runs no handlers either.
- **Stopping a run** (`argo stop`) runs pipeline and stage handlers with a `Failed` status, but
  not the handlers of a block that was still running. Terminating it (`argo terminate`) runs
  none.
- Handlers run after the step finishes. Don't assume a stage's handlers finish before the next
  stage starts.

## When a handler fails

- **A failing pipeline handler fails the pipeline**, even if every stage succeeded. Handler
  failures are unexpected, and a build that couldn't report its result shouldn't look green.
- **A failing stage or block handler** shows as failed in the Argo UI but **doesn't** fail the
  pipeline. That's an Argo limitation, not a choice: hooks on individual steps can't fail a
  workflow.

## Rules for handler blocks

A handler only runs; it can't be a step of its own. So a handler block can't set `when`,
`status`, its own handlers, or `workspace` access. Handler names must be unique within each list
(`on_failure=[Notify(...), Notify(name="page", ...)]`).

## Recap

- `on_success`, `on_failure`, and `finally_` attach ordinary blocks to a block, a stage, or (the
  first two) the pipeline.
- `{{ step.name }}` and `{{ step.status }}` tell a handler what happened.
- Skipped and never-started steps run no handlers. Only pipeline handler failures fail the
  pipeline.

Next: [7. Commit statuses](07-commit-statuses.md).
