# 8. Passing results between steps

Steps share no files: each starts from its own copy of the checkout, and anything one step
writes is gone when it ends. When a later step needs an earlier step's result, a binary to put in
an image or the digest of an image to deploy, the earlier step **publishes** it and the later
step **asks for it** by name.

## Two kinds of result

| Kind | For | Published as | Read with |
|---|---|---|---|
| **Output parameter** | a short string: a digest, a version, a URL | the contents of a file | `{{ steps.<stage>.<step>.<output> }}` |
| **Artifact** | files: a binary, a directory, reports | the file or directory itself | `Step(..., inputs={"<input>": "<stage>.<step>.<artifact>"})` |

An action declares what it publishes and receives the Hera way, on the template `compile`
returns. A path that doesn't start with `/` is inside the checkout:

```python
Container(
    ...,
    outputs=[
        Artifact(name="app", path="bin/app"),                          # publishes bin/app
        Parameter(name="digest", value_from=ValueFrom(path="image.digest")),
    ],
    inputs=[Artifact(name="app", path="bin/app")],                     # receives into bin/app
)
```

## Output parameters

A later step reads an output parameter with a placeholder, anywhere a placeholder works: its
action's fields, a `when` condition (quoted, like params), and handlers.

```python
Step("staging", Make(target="deploy-staging", variables={"IMAGE": "{{ steps.package.image.digest }}"}))
```

## Artifacts

A step receives an artifact into one of its action's input artifacts, by binding it in `inputs`:

```python
Step("image", Make(target="image", consumes={"app": "bin/app"}), inputs={"app": "build.binary.app"})
```

Every input artifact the action declares must be bound (unless it has a source of its own, such
as a Hera `GitArtifact`), and only those can be.

## Rules

- **Only from earlier stages.** Steps in a stage run in parallel, so they can't use each other's
  results. Pipeline handlers can use any stage's, but get no value from a step that didn't run.
- **Not from steps that may be skipped.** A result from a step (or stage) with `when` might not
  exist, so referring to it fails the render.
- **Not in the setup, the source, or the status reporter.** They run before any stage, or for
  every stage.
- **Handlers can't take input artifacts.** They can read output parameters.
- **Results live in the artifact repository**, the one the cluster's Argo is configured with,
  and stay until the run is deleted (see "Retention" below).

The render checks every reference, so a misspelled step or a result from the wrong stage fails
before anything runs:

```
pipeline 'service-ci', stage 'deploy', step 'staging': '{{ steps.package.image.digst }}' is not an output of a step in an earlier stage; available: ['package.image.digest']
```

## Growing the example

`Make` learns to publish and receive files and results: `produces` and `consumes` map artifact
names to files in the checkout, and `results` maps output names to files whose contents become
output parameters:

```python file=actions.py
import re

from hera.workflows import Artifact, Container, Env, Parameter
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
```

A new `build` stage compiles the service once; `package` puts that binary in the image and
publishes the image's digest, which `deploy` hands to the deployment:

```python file=pipelines/service_ci.py
from stagecraft.spec import Param, Pipeline, Source, Stage, Step

from actions import Make, Notify

pipeline = Pipeline(
    name="service-ci",
    params=[
        Param("revision", default="main", description="Branch, tag, or commit to build"),
        Param("push", default="false", enum=["true", "false"], description="Push the image"),
        Param("deploy", default="false", enum=["true", "false"], description="Deploy to staging"),
    ],
    source=Source(url="https://github.com/example/service.git", revision="{{ param.revision }}"),
    stages=[
        Stage(
            "verify",
            [
                Step("lint", Make(target="lint")),
                Step("unit-tests", Make(target="test")),
            ],
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
        ),
    ],
    on_failure=[Notify(message="{{ step.name }} {{ step.outcome }}")],
)
```

`Make`'s tests check what it declares:

```python file=tests/test_actions.py
import pytest
from pydantic import ValidationError

from stagecraft.context import CompileContext

from actions import Make


def test_make_runs_its_target():
    container = Make(target="lint").compile(CompileContext())

    assert container.command == ["make"]
    assert container.args == ["lint"]


def test_make_accepts_one_target_only():
    with pytest.raises(ValidationError, match="single make target"):
        Make(target="lint test")


def test_make_needs_the_build_image_and_resources():
    ctx = CompileContext()

    Make(target="lint").compile(ctx)

    assert ctx.references == {"images.build": str, "build.cpu": str, "build.memory": str}


def test_make_declares_what_it_produces_consumes_and_results():
    container = Make(
        target="image",
        produces={"layer": "out/layer.tar"},
        consumes={"app": "bin/app"},
        results={"digest": "image.digest"},
    ).compile(CompileContext())

    assert [(a.name, a.path) for a in container.inputs] == [("app", "bin/app")]
    assert [o.name for o in container.outputs] == ["layer", "digest"]
```

## Retention

Artifacts stay in the artifact repository until something deletes them. Argo's **artifact GC**
deletes a run's artifacts when the run itself is deleted, and a **TTL** deletes finished runs.
Both are platform settings, configured once for every workflow in the controller's
`workflowDefaults`, not per pipeline:

```yaml
# workflow-controller-configmap
workflowDefaults: |
  spec:
    artifactGC:
      strategy: OnWorkflowDeletion
      serviceAccountName: artifact-gc     # allowed to delete from the bucket
    ttlStrategy:
      secondsAfterCompletion: 604800      # keep finished runs for a week
```

## Recap

- Actions declare output parameters, output artifacts, and input artifacts on the template
  `compile` returns; relative paths are inside the checkout.
- Later steps read parameters as `{{ steps.<stage>.<step>.<output> }}` and bind artifacts with
  `Step(inputs={...})`.
- Results come only from earlier stages, never from steps that may be skipped; the render checks
  every reference.

Next: [9. Commit statuses](09-commit-statuses.md).
