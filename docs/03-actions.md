# 3. Actions

Actions are where your pipelines' real work is defined, and the one place you write code. This
chapter covers what an action is made of, the rule that shapes every action design (fields versus
`compile`), how steps name them, and how to test actions.

## Anatomy

```python
class Make(Action):           # 1. a subclass of stagecraft.spec.Action
    """Runs one make target."""

    target: str               # 2. fields: what a pipeline author can configure

    def compile(self, ctx):   # 3. compile: the container this action runs as
        return Container(...)
```

1. **An `Action` subclass.** Actions are Pydantic models: fields are typed and validated, unknown
   arguments are rejected (a typo like `Make(taget="lint")` fails), and instances are immutable.
2. **Fields.** Anything an author should be able to set. Use defaults generously so the common
   case is short.
3. **`compile(self, ctx)`** returns one Hera `Container` or `Script`. Stagecraft names it, wires
   it into the workflow, and adds anything the pipeline asks for (handlers, statuses).

An action has only the fields you give it. Where it runs, under which name, and under which
conditions belong to the `Step` that places it (`when`, `status`, and handlers, covered in later
chapters), so they never collide with your fields.

## A reusable action

`Shell` from chapter 2 works, but a pipeline full of shell one-liners is hard to read and easy to
get wrong. Purpose-built actions name the intent and validate their inputs:

```python file=actions.py
import re

from hera.workflows import Container
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
    """Runs one make target in the chart's build image."""

    target: str

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
            args=[self.target],
        )
```

The pipeline now reads as what it does:

```python file=pipelines/service_ci.py
from stagecraft.spec import Pipeline, Stage, Step

from actions import Make

pipeline = Pipeline(
    name="service-ci",
    stages=[
        Stage(
            "verify",
            [
                Step("lint", Make(target="lint")),
                Step("unit-tests", Make(target="test")),
            ],
        ),
        Stage("package", [Step("image", Make(target="image"))]),
    ],
)
```

`Make` uses a new value, `images.build`. The generated schema now requires it, so every
installation's values must set it:

```yaml file=values/staging.yaml
images:
  shell: alpine:3.22
  build: golang:1.25
```

## Steps and names

Actions have no names; steps do. `Step(name, action)` places an action in a stage:

- A bare action in a stage is shorthand for a step named after its class in kebab case: `Make(...)`
  is the step `make`, `GitHubStatus(...)` is `git-hub-status`.
- Step names must be unique within a stage, so two `Make` actions in one stage need a `Step` each.
- Names must be lowercase DNS labels (`a-z`, `0-9`, `-`, at most 63 characters).
- The compiled template is named `<stage>-<step>`, for example `verify-lint`. That's the name you
  see in the Argo UI.

## What `compile` can return

`compile` returns a Hera
[`Container`](https://github.com/argoproj-labs/hera/tree/7.1.0) or `Script`, and Stagecraft
passes it through, apart from setting its name. So an action can set anything Hera allows:
environment variables, resource requests, retries, timeouts, a service account, node selectors,
security contexts, volumes. For example:

```python
from hera.workflows import Container, Env, RetryStrategy


class Flaky(Action):
    """Runs a command that sometimes fails for reasons outside our control."""

    command: list[str]

    def compile(self, ctx: CompileContext) -> Container:
        return Container(
            image=ctx.value("images.shell", str),
            command=self.command,
            env=[Env(name="CI", value="true")],
            retry_strategy=RetryStrategy(limit=2),
            active_deadline_seconds=600,
        )
```

A `Script` runs inline source with an interpreter:

```python
from hera.workflows import Script


class Python(Action):
    """Runs a Python snippet."""

    source: str

    def compile(self, ctx: CompileContext) -> Script:
        return Script(image=ctx.value("images.python", str), command=["python"], source=self.source)
```

An action can set **container fields** (image, command, args, env, resources, working directory),
which describe the container itself, and **template fields** (retries, timeouts, service account,
node selection), which describe the pod around it.

Rules for `compile`:

- **No I/O.** Don't read files, environment variables, or the network. An action's output must
  depend only on its fields and `ctx`, so a render is reproducible.
- **No installation-specific literals.** Images, Secret names, registries, and resource sizes
  differ per environment: get them from `ctx` (chapter 4).
- **No Helm syntax.** Never write `{{ .Values... }}`; Stagecraft generates it.

## Fields versus `compile`: where placeholders work

Authors put **placeholders** such as `{{ param.sha }}` in action fields (chapter 5 introduces
them). Stagecraft replaces them in every string field, including inside lists, dicts, and nested
models, **before** it calls `compile`. It does not look inside what `compile` returns.

So anything that should accept a placeholder must be a field. A placeholder written inside
`compile` would reach Argo unreplaced:

```python
class Bad(Action):
    def compile(self, ctx):
        return Container(..., args=["echo {{ param.sha }}"])     # never replaced


class Good(Action):
    sha: str = "{{ param.sha }}"                               # replaced before compile

    def compile(self, ctx):
        return Container(..., env=[Env(name="SHA", value=self.sha)])
```

A field default can be a placeholder, as `Good.sha` shows: authors get the common case for free
and can still override it.

Two more consequences:

- **Validators see the author's text,** placeholders included. `Make`'s validator would reject
  `target="{{ param.target }}"`. If a field should accept placeholders, validate loosely or not at
  all.
- **Prefer environment variables to splicing into scripts.** `env=[Env(name="SHA",
  value=self.sha)]` with a script that reads `$SHA` keeps the script literal and readable, and
  avoids quoting problems.

## Testing actions

An action is a plain class, so test it with plain pytest. Give `compile` a fresh
`CompileContext`; afterwards, `ctx.references` lists every Helm value the action used and its
type, which also documents what an installation must provide:

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


def test_make_needs_the_build_image():
    ctx = CompileContext()

    Make(target="lint").compile(ctx)

    assert ctx.references == {"images.build": str}
```

Calling `compile` directly skips placeholder replacement. To test how an action behaves inside a
pipeline, compile a whole pipeline with `stagecraft.compile.compile_pipeline`, or run
`stagecraft check` in CI.

## Sharing actions

Actions are ordinary Python. Once several pipelines repos want the same actions, move them into a
package (an action library), version it, and depend on it like any other library.

## Recap

- An action is a validated Pydantic model whose `compile` returns one Hera container.
- A step places an action in a stage under a name; a bare action is named after its class.
- `compile` can set anything Hera allows; it must be pure and get installation details from `ctx`.
- Placeholders work in fields, not in what `compile` returns. Design fields accordingly.
- Test actions by calling `compile` with a fresh `CompileContext`, and check `ctx.references`.

Next: [4. Helm values](04-values.md).
