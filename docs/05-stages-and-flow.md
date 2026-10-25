# 5. Stages, params, and conditions

With actions and values in hand, this chapter is about the pipeline's shape: how stages run, how
a run gets its inputs, and how to make steps conditional.

## How stages run

- **Stages run in order.** A stage starts when the one before it has finished successfully.
- **Steps in a stage run in parallel.** They start together.
- **A failing step fails its stage, and the pipeline.** Steps already running in that stage
  keep running; later stages don't start (the Argo UI shows them as Omitted).

Group steps into the same stage when they don't depend on each other (lint and unit tests), and
into later stages when they do (build the image after the tests pass).

Stage names follow the same rules as step names: lowercase DNS labels, unique in the pipeline.

## Params: per-run inputs

Params are what someone chooses when they submit a run: which commit, whether to deploy.

```python
Param("sha", required=True, description="Commit to build")
Param("deploy", default="false", enum=["true", "false"], description="Deploy to staging")
```

- A param is either `required=True` or has a `default`, never both.
- `enum` restricts the choices (the default must be one of them).
- Params become the Argo UI's submit form, with descriptions and drop-downs for enums.
- Param values are always strings.

Use a param in any action field with `{{ param.<name> }}`. Referring to a param that isn't
declared fails the render.

Params and Helm values answer different questions. A param is chosen **per run** ("deploy this
time?"); a value is set **per installation** ("which registry does this cluster push to?"). If
the person submitting a run shouldn't choose it, it isn't a param.

## Growing the example

`Make` gets a `variables` field for make variables. Placeholders work inside the dict's values,
because Stagecraft replaces placeholders in every string a field contains:

```python file=actions.py
import re

from hera.workflows import Container
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
```

The pipeline gains params, a `prepare` stage (a stand-in for a real checkout), and a `deploy`
stage that only runs when asked:

```python file=pipelines/service_ci.py
from stagecraft.spec import Param, Pipeline, Stage, Step

from actions import Make, Shell

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
        ),
        Stage(
            "package",
            [Step("image", Make(target="image", variables={"PUSH": "{{ param.push }}"}))],
        ),
        Stage(
            "deploy",
            [Step("staging", Make(target="deploy-staging"))],
            when="{{ param.deploy }} == 'true'",
        ),
    ],
)
```

In the rendered template, `{{ param.push }}` becomes the Argo expression
`{{workflow.parameters.push}}`, so the `image` step runs `make image PUSH=true` when someone
submits with `push=true`.

## Conditions: `when`

A stage or a step with `when` runs only if the condition is true; otherwise it's **skipped**.

```python
Stage("deploy", [...], when="{{ param.deploy }} == 'true'")
```

- The condition is an [Argo `when` expression](https://argo-workflows.readthedocs.io/en/latest/walk-through/conditionals/):
  comparisons (`==`, `!=`, `<`, ...), `&&`, `||`, `!`, and parentheses.
- **Inside `when`, `{{ param.x }}` becomes a quoted string**, so compare it with quoted values:
  `{{ param.deploy }} == 'true'`. (Argo reads bare words as booleans or numbers, so an unquoted
  comparison would compare the boolean `true` with the string `'true'` and always be false.)
- `{{ values.a.b }}` works in `when` too, for conditions set per installation.
- A skipped stage doesn't stop the pipeline: later stages still run. A skipped step runs no
  handlers (chapter 6) and reports its status as "Skipped" (chapter 7).

## Native Argo expressions

Stagecraft only replaces its own placeholders (`param`, `values`, `step`, `status`). Any other
`{{ ... }}` in a field is passed to Argo unchanged, so Argo's own variables work:

```python
Step("whoami", Shell(script="echo running {{workflow.name}}"))
```

Use them sparingly: Stagecraft can't check them, and `argo lint` in CI is the first
place a typo would show up.

## Recap

- Stages run in order; steps in a stage run in parallel; a failure stops later stages.
- Params are per-run inputs and become the submit form; use them with `{{ param.x }}`.
- `when` skips a stage or step; params inside it are quoted strings.
- Other `{{ ... }}` expressions go to Argo untouched.

Next: [6. Handlers](06-handlers.md).
