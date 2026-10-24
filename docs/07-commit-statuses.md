# 7. Commit statuses

On GitHub, a pull request shows checks next to each commit, and branch protection can require
some of them to pass before merging. Stagecraft can report a check for the pipeline and for any
stage or block, through a block you provide.

## Declaring statuses

Give a step a **context**, the check's name:

```python
Pipeline(..., status="ci/pipeline")
Stage("verify", [...], status="ci/verify")
Make(name="unit-tests", target="test", status="ci/unit-tests")
```

Contexts must be unique within a pipeline.

## What gets reported

For each step with a status:

| Moment | State | Description |
|---|---|---|
| just before the step starts | `pending` | `Running` |
| the step succeeded | `success` | `Succeeded` |
| the step failed | `failure` | `Failed` |
| the step errored (an Argo error, such as a pod that couldn't start) | `error` | `Error` |
| the step was skipped by `when` | `success` | `Skipped` |

"Pending" always lands before the final state. A skipped step reports success, so a required check
for a conditional stage, like `deploy`, doesn't block a merge forever.

A step that never starts, because an earlier stage failed, is never reported, so it can't be left
pending. The pipeline's own context still reports `failure`, which is why **the pipeline's
context is the one to require** in branch protection; step contexts add detail.

## The reporter block

Stagecraft doesn't call GitHub itself. Every report goes through the pipeline's
**`status_reporter`**, an ordinary block that you write once. It reads three placeholders:

| Placeholder | Value |
|---|---|
| `{{ status.context }}` | the context being reported, such as `ci/verify` |
| `{{ status.state }}` | `pending`, `success`, `failure`, or `error` |
| `{{ status.description }}` | `Running`, `Succeeded`, `Failed`, `Error`, or `Skipped` |

and gets the commit from the `repo` and `sha` params, which every pipeline with statuses must
declare as required. `{{ status.* }}` only works in the reporter.

Reporting is **best-effort**: if the reporter fails (GitHub is down, the token expired), the
failure shows in the Argo UI, but the build carries on and its result is unaffected.

## Growing the example

The reporter takes its placeholders as field defaults (placeholders work in fields, not in
`compile`; chapter 3), passes them to curl as environment variables, and reads the token from a
Secret named by a Helm value:

```python file=blocks.py
import re

from hera.workflows import Container, Env, SecretEnv
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


class GitHubStatus(Block):
    """Posts a commit status to GitHub. Used as a pipeline's status_reporter."""

    repo: str = "{{ param.repo }}"
    sha: str = "{{ param.sha }}"
    context: str = "{{ status.context }}"
    state: str = "{{ status.state }}"
    description: str = "{{ status.description }}"

    def compile(self, ctx: CompileContext) -> Container:
        fields = ("repo", "sha", "context", "state", "description")
        return Container(
            image=ctx.value("images.curl", str),
            env=[
                *(Env(name=f.upper(), value=getattr(self, f)) for f in fields),
                SecretEnv(
                    name="GITHUB_TOKEN",
                    secret_name=ctx.value("github.tokenSecret", str),
                    secret_key="token",
                ),
            ],
            command=["sh", "-c"],
            args=[
                'curl -fsS -H "Authorization: Bearer $GITHUB_TOKEN"'
                ' "https://api.github.com/repos/$REPO/statuses/$SHA"'
                ' -d "{\\"context\\": \\"$CONTEXT\\", \\"state\\": \\"$STATE\\",'
                ' \\"description\\": \\"$DESCRIPTION\\"}"'
            ],
        )
```

The pipeline reports on itself and on each stage after `prepare`:

```python file=pipelines/service_ci.py
from stagecraft.spec import Param, Pipeline, Stage

from blocks import GitHubStatus, Make, Notify, Shell

pipeline = Pipeline(
    name="service-ci",
    params=[
        Param("repo", required=True, description="Repository, as owner/name"),
        Param("sha", required=True, description="Commit to build"),
        Param("push", default="false", enum=["true", "false"], description="Push the image"),
        Param("deploy", default="false", enum=["true", "false"], description="Deploy to staging"),
    ],
    status="ci/pipeline",
    status_reporter=GitHubStatus(),
    stages=[
        Stage("prepare", [Shell(name="fetch", script="echo fetching {{ param.repo }}")]),
        Stage(
            "verify",
            [
                Make(name="lint", target="lint"),
                Make(name="unit-tests", target="test"),
            ],
            status="ci/verify",
            on_failure=[Notify(message="{{ step.name }} failed for {{ param.sha }}")],
        ),
        Stage(
            "package",
            [Make(name="image", target="image", variables={"PUSH": "{{ param.push }}"})],
            status="ci/package",
        ),
        Stage(
            "deploy",
            [Make(name="staging", target="deploy-staging")],
            when="{{ param.deploy }} == 'true'",
            status="ci/deploy",
        ),
    ],
    on_failure=[Notify(message="{{ step.name }} ended {{ step.status }}")],
)
```

The chart adds the curl image and the token Secret's name. The Secret itself (with a `token` key)
is created separately, never by Stagecraft:

```yaml file=chart/values.yaml
images:
  shell: alpine:3.22
  build: golang:1.25
  curl: curlimages/curl:8.11.1
build:
  cpu: "1"
  memory: 2Gi
notify:
  channel: "#ci"
github:
  tokenSecret: github-token
```

```json file=chart/values.schema.json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "type": "object",
  "required": ["images", "build", "notify", "github"],
  "properties": {
    "images": {
      "type": "object",
      "required": ["shell", "build", "curl"],
      "properties": {
        "shell": {"$ref": "#/$defs/image"},
        "build": {"$ref": "#/$defs/image"},
        "curl": {"$ref": "#/$defs/image"}
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
    },
    "github": {
      "type": "object",
      "required": ["tokenSecret"],
      "properties": {
        "tokenSecret": {"type": "string", "description": "Secret with a 'token' key for the GitHub API"}
      }
    }
  },
  "$defs": {
    "image": {"type": "string", "description": "A container image reference"}
  }
}
```

## Setting up branch protection

List every context with:

```sh
uv run stagecraft contexts pipelines
# service-ci	ci/pipeline
# service-ci	ci/verify
# service-ci	ci/package
# service-ci	ci/deploy
```

and require the ones you want in the repository's branch protection rules (at least the
pipeline's own).

## Rules and limits

- Statuses need a `status_reporter` and required `repo` and `sha` params; the render fails
  without them.
- The reporter is a plain block: no `when`, `status`, handlers, workspace access, or template
  inputs of its own.
- If a run is terminated (`argo terminate`), or stopped while a block with a status is running
  (`argo stop`), that context can stay `pending`. Re-running the workflow updates it.
- A block can't have its own status when it shares a pod with other blocks; chapter 8 explains.

## Recap

- `status="<context>"` on the pipeline, a stage, or a block reports pending, then the outcome.
- Skipped steps report success ("Skipped"); never-started steps aren't reported; require the
  pipeline's context.
- Your `status_reporter` block makes the API call; reporting never fails the build.

Next: [8. The workspace](08-workspace.md).
