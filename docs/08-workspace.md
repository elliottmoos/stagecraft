# 8. The workspace

So far each block has run in its own empty container. Real pipelines check out the code once and
then lint, test, and build that same checkout. The **workspace** is a volume the pipeline's
blocks share.

## Declaring it

Blocks say how they use the workspace; the pipeline says how it's provided:

```python
Clone()                                         # workspace="write" by default (below)
Make(name="lint", target="lint", workspace="read")

Pipeline(..., workspace=Workspace(strategy="containerset", size="5Gi"))
```

A block's `workspace` is `"none"` (the default), `"read"`, or `"write"`. The pipeline's
`workspace` is required as soon as any block uses it, and declaring one that no block uses is an
error.

## What you get

- **One volume per run**: a ReadWriteOnce claim of `size`, from the cluster's default
  StorageClass, deleted when the run ends, whether it succeeded or failed.
- **Mounted at `/workspace`** (`stagecraft.spec.WORKSPACE_PATH`), **read-only** for `read`
  blocks.
- **The working directory** of blocks that use it, unless the block's template sets its own.
- Blocks with `workspace="none"` don't mount it at all.

Stages run one after another, so the checkout written in `prepare` is there for `verify`, and
still there for `package`.

## Why a strategy: parallel blocks and one volume

A ReadWriteOnce volume (like an EBS disk) can only be attached to one node at a time. Two
parallel blocks in separate pods might be scheduled on different nodes, and the second would wait
forever for the volume. The **strategy** decides how parallel blocks share it. Stagecraft has one
so far:

**`containerset`**: when a stage has two or more workspace blocks, they run as **containers of a
single pod**, so only one pod ever mounts the volume. A stage with a single workspace block runs
it in its own pod, as usual.

Sharing a pod has consequences, so blocks in such a group have rules:

- **Only readers.** A block that writes the workspace must be the only workspace block in its
  stage. (Readers running beside a writer would read files while they change.)
- **No `when`, `status`, or handlers on the grouped blocks.** Argo can't attach conditions or
  hooks to one container of a pod. Put them on the stage instead, which works as usual.
- **Container fields only.** A grouped block must compile to a `Container` (not a `Script`) and
  set only container fields: image, command, args, env, resources, working directory. Template
  fields such as retries, timeouts, or a service account belong to a whole pod (chapter 3).
- **Resource requests add up.** The pod requests the sum of its containers' requests.
- **A failing container fails the stage after its siblings finish.** They keep running to
  completion; then the stage fails.

In a grouped stage, the Argo UI shows one `workspace` node (template `<stage>-workspace`) with a
container per block, so `workspace` is a reserved block name there. Blocks without workspace
access in the same stage still get their own pods.

Handler blocks and the status reporter can't use the workspace yet.

## Growing the example

A `Clone` block replaces the stand-in `fetch` step. It defaults to writing the workspace and to
the run's `repo` and `sha`, so `Clone()` is all a pipeline needs:

```python file=blocks.py
import re
from typing import Literal

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


class Clone(Block):
    """Fetches one commit of a GitHub repository into the workspace."""

    repo: str = "{{ param.repo }}"
    sha: str = "{{ param.sha }}"
    workspace: Literal["none", "read", "write"] = "write"

    def compile(self, ctx: CompileContext) -> Container:
        return Container(
            image=ctx.value("images.git", str),
            command=["sh", "-c"],
            args=[
                "git init -q . && git fetch -q --depth 1 https://github.com/$REPO $SHA"
                " && git checkout -q FETCH_HEAD"
            ],
            env=[Env(name="REPO", value=self.repo), Env(name="SHA", value=self.sha)],
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

`Clone` changes the default of a field it inherits (`workspace`), redeclaring its type. The
script uses `git init` and `git fetch` rather than `git clone` because a fresh volume may not be
empty (ext4 volumes contain `lost+found`).

The `make` blocks read the checkout. `lint` and `unit-tests` share a pod; `image` and `staging`
each have their own:

```python file=pipelines/service_ci.py
from stagecraft.spec import Param, Pipeline, Stage, Workspace

from blocks import Clone, GitHubStatus, Make, Notify

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
    workspace=Workspace(strategy="containerset", size="5Gi"),
    stages=[
        Stage("prepare", [Clone()]),
        Stage(
            "verify",
            [
                Make(name="lint", target="lint", workspace="read"),
                Make(name="unit-tests", target="test", workspace="read"),
            ],
            status="ci/verify",
            on_failure=[Notify(message="{{ step.name }} failed for {{ param.sha }}")],
        ),
        Stage(
            "package",
            [
                Make(
                    name="image",
                    target="image",
                    variables={"PUSH": "{{ param.push }}"},
                    workspace="read",
                ),
            ],
            status="ci/package",
        ),
        Stage(
            "deploy",
            [Make(name="staging", target="deploy-staging", workspace="read")],
            when="{{ param.deploy }} == 'true'",
            status="ci/deploy",
        ),
    ],
    on_failure=[Notify(message="{{ step.name }} ended {{ step.status }}")],
)
```

Notice that `verify`'s status and handler sit on the stage. Putting `status="ci/unit-tests"` on
the `unit-tests` block would now fail the render, because it shares a pod with `lint`.

The chart adds the git image:

```yaml file=chart/values.yaml
images:
  shell: alpine:3.22
  build: golang:1.25
  curl: curlimages/curl:8.11.1
  git: alpine/git:2.47.2
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
      "required": ["shell", "build", "curl", "git"],
      "properties": {
        "shell": {"$ref": "#/$defs/image"},
        "build": {"$ref": "#/$defs/image"},
        "curl": {"$ref": "#/$defs/image"},
        "git": {"$ref": "#/$defs/image"}
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

## Choosing the size

`size` is a Kubernetes quantity (`5Gi`, `500Mi`). It's part of the pipeline, not a Helm value:
the workspace strategy and its options are a structural choice, visible in the pipeline file and
in review, and identical in every installation.

## Recap

- Blocks declare `workspace="read"` or `"write"`; the pipeline declares
  `Workspace(strategy="containerset", size=...)`.
- One volume per run, mounted at `/workspace`, read-only for readers, deleted afterwards.
- With `containerset`, a stage's parallel workspace blocks share one pod: readers only, no
  per-block `when`, status, or handlers, container fields only.

Next: [9. Triggers](09-triggers.md).
