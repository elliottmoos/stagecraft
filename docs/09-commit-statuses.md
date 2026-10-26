# 9. Commit statuses

On GitHub, a pull request shows checks next to each commit, and branch protection can require
some of them to pass before merging. Stagecraft can report a check for the pipeline and for any
stage or step, through an action you provide.

## Declaring statuses

Give the pipeline, a stage, or a step a **context**, the check's name:

```python
Pipeline(..., status="ci/pipeline")
Stage("verify", [...], status="ci/verify")
Step("unit-tests", Make(target="test"), status="ci/unit-tests")
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

## The reporter action

Stagecraft doesn't call GitHub itself. Every report goes through the pipeline's
**`status_reporter`**, an ordinary action that you write once. It reads three placeholders:

| Placeholder | Value |
|---|---|
| `{{ status.context }}` | the context being reported, such as `ci/verify` |
| `{{ status.state }}` | `pending`, `success`, `failure`, or `error` |
| `{{ status.description }}` | `Running`, `Succeeded`, `Failed`, `Error`, or `Skipped` |

and gets the commit from the pipeline's source (chapter 7): `{{ source.sha }}` is the commit the
run resolved, and `{{ source.url }}` the repository. Statuses need a source. `{{ status.* }}`
only works in the reporter.

Reporting is **best-effort**: if the reporter fails (GitHub is down, the token expired), the
failure shows in the Argo UI, but the build carries on and its result is unaffected.

## Growing the example

The reporter takes its placeholders as field defaults (placeholders work in fields, not in
`compile`; chapter 3), passes them to curl as environment variables, and reads the token from a
Secret named by a Helm value. GitHub's API wants `owner/name`, which the script cuts out of the
source's URL:

```python file=actions.py
import re

from hera.workflows import Artifact, Container, Env, Parameter, SecretEnv
from hera.workflows.models import ResourceRequirements, ValueFrom
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

    `variables` are passed as make variables (NAME=value). `produces` and `consumes` map artifact
    names to files in the checkout the target writes or reads; `results` maps output names to
    files whose contents become output parameters.
    """

    target: str
    variables: dict[str, str] = {}
    produces: dict[str, str] = {}
    consumes: dict[str, str] = {}
    results: dict[str, str] = {}

    @field_validator("target")
    @classmethod
    def _one_target(cls, target: str) -> str:
        if not re.fullmatch(r"[A-Za-z0-9_.-]+", target):
            raise ValueError(f"target must be a single make target, not {target!r}")
        return target

    def compile(self, ctx: CompileContext) -> Container:
        outputs = [
            *(Artifact(name=name, path=path) for name, path in self.produces.items()),
            *(
                Parameter(name=name, value_from=ValueFrom(path=path))
                for name, path in self.results.items()
            ),
        ]
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
            inputs=[Artifact(name=n, path=p) for n, p in self.consumes.items()] or None,
            outputs=outputs or None,
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


class GitHubStatus(Action):
    """Posts a commit status to GitHub. Used as a pipeline's status_reporter."""

    url: str = "{{ source.url }}"
    sha: str = "{{ source.sha }}"
    context: str = "{{ status.context }}"
    state: str = "{{ status.state }}"
    description: str = "{{ status.description }}"

    def compile(self, ctx: CompileContext) -> Container:
        fields = ("url", "sha", "context", "state", "description")
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
                'REPO="${URL#https://github.com/}"; REPO="${REPO%.git}";'
                ' curl -fsS -H "Authorization: Bearer $GITHUB_TOKEN"'
                ' "https://api.github.com/repos/$REPO/statuses/$SHA"'
                ' -d "{\\"context\\": \\"$CONTEXT\\", \\"state\\": \\"$STATE\\",'
                ' \\"description\\": \\"$DESCRIPTION\\"}"'
            ],
        )
```

The pipeline reports on itself and on the `verify`, `package`, and `deploy` stages:

```python file=pipelines/service_ci.py
from stagecraft.spec import Param, Pipeline, Source, Stage, Step

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
        Stage("build", [Step("binary", Make(target="build", produces={"app": "bin/app"}))]),
        Stage(
            "package",
            [
                Step(
                    "image",
                    Make(
                        target="image",
                        variables={"PUSH": "{{ param.push }}"},
                        consumes={"app": "bin/app"},
                        results={"digest": "image.digest"},
                    ),
                    inputs={"app": "build.binary.app"},
                )
            ],
            status="ci/package",
        ),
        Stage(
            "deploy",
            [
                Step(
                    "staging",
                    Make(
                        target="deploy-staging",
                        variables={"IMAGE": "{{ steps.package.image.digest }}"},
                    ),
                )
            ],
            when="{{ param.deploy }} == 'true'",
            status="ci/deploy",
        ),
    ],
    on_failure=[Notify(message="{{ step.name }} {{ step.outcome }}")],
)
```

Installations add the curl image and the token Secret's name. The Secret itself (with a `token`
key) is created separately, never by Stagecraft:

```yaml file=values/staging.yaml
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
stagecraft:
  sources:
    image: alpine/git:2.47.2
    secret: github-checkout
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

- Statuses need a `status_reporter` and a pipeline source; the render fails without them.
- If the source step fails (say, a revision that doesn't exist), there's no commit to report on,
  so nothing is reported; the failure shows in the Argo UI.
- The reporter is a bare action (not a `Step`), with no template inputs of its own.
- If a run is terminated (`argo terminate`), or stopped while a step with a status is running
  (`argo stop`), that context can stay `pending`. Re-running the workflow updates it.

## Recap

- `status="<context>"` on the pipeline, a stage, or a step reports pending, then the outcome.
- Skipped steps report success ("Skipped"); never-started steps aren't reported; require the
  pipeline's context.
- Your `status_reporter` action makes the API call; reporting never fails the build.

Next: [10. Triggers](10-triggers.md).
