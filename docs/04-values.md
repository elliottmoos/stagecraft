# 4. Helm values

Pipelines run in more than one place: a staging cluster and a production cluster may use
different registries, Secrets, and machine sizes. Those details are **Helm values**, supplied per
installation. This chapter covers how actions and pipelines use them, the schema Stagecraft
generates from that use, and how Helm checks each installation against it.

## Two ways to reference a value

**In an action's `compile`**, call `ctx.value(path, type)`:

```python
image=ctx.value("images.build", str)
```

**In an action's fields** (and in `when` conditions and trigger params), write a placeholder:

```python
Notify(channel="{{ values.notify.channel }}")
```

Both mean "the value at `.Values.<path>` in this installation." Neither gives you the value
itself: Stagecraft runs before any installation exists. What you get back is a reference that
renders as a Helm expression, which Helm fills in at install time.

`type` is how the action uses the value: `str` (the default), `int`, `float`, or `bool`.
Placeholders are always strings.

## The generated schema

Every reference is recorded, and `stagecraft render` writes them all into the chart's
`values.schema.json`: each referenced value is **required**, with the type it's used as. Nothing
is optional and nothing has a default, so a pipeline can rely on every value it uses.

The render itself fails, naming the pipeline, stage, step, and path, when a reference can't be
turned into a schema:

- **Paths are dotted identifiers** (`[A-Za-z_][A-Za-z0-9_]*` per key), so they work as
  `.Values.a.b`. A key like `alert-channel` can't be referenced.
- **One type per path.** Using `build.retries` as an `int` in one action and a string in another
  fails.
- **A path is a value or an object, not both.** `images` and `images.build` can't both be values.
- **`stagecraft` is reserved** for Stagecraft's own values.

```
pipeline 'service-ci', stage 'verify', step 'lint': value 'build.retries' is used as str here but as int elsewhere; give it one type
```

## Checking an installation

Helm validates each installation's values against the generated schema at `helm template` and
`helm install`, before anything reaches the cluster:

```
Error: values don't meet the specifications of the schema(s) in the following chart(s):
pipelines:
- at '/images/build': got number, want string
- at '/build': missing property 'memory'
```

A typo in a value path inside an action shows up here too: the misspelled path becomes a new
required value that no values file sets. Run `helm template` with each environment's values in
CI (chapter 10) to catch it before merging.

## Growing the example

Builds need different machine sizes per cluster, so `Make` takes its resource requests from the
installation's values:

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
    """Runs one make target in the chart's build image, with the chart's build resources."""

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
            resources=ResourceRequirements(
                requests={
                    "cpu": ctx.value("build.cpu", str),
                    "memory": ctx.value("build.memory", str),
                },
            ),
        )
```

Note `ResourceRequirements` (from `hera.workflows.models`) rather than Hera's `Resources` helper:
the helper checks units like `Gi` when the action is compiled, and a value reference isn't a
quantity yet. The general rule: if a Hera helper validates a field's content, use the plain model
for value references.

The staging values grow to match:

```yaml file=values/staging.yaml
images:
  shell: alpine:3.22
  build: golang:1.25
build:
  cpu: "1"
  memory: 2Gi
```

`cpu` is quoted because the action uses it as a string: an unquoted `1` is a number, and Helm
would reject it.

`Make`'s test records the new references, which documents what it needs:

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
```

## How values render

Every value lands in the manifest as a **string**:

| Reference | Renders as |
|---|---|
| a string value, as a whole field | `{{ .Values.images.build \| quote }}` |
| a number or boolean, as a whole field | `{{ .Values.build.retries \| toJson \| quote }}` |
| any value inside other text, such as `"retries: {{ values.build.retries }}"` | spliced into a double-quoted string |

Values are always quoted, so a value containing YAML syntax (`a: "b" # c`) can't change the
document. Numbers go through `toJson` because Helm's `quote` would print `1000000` as `1e+06`.

Because references are strings, they fit any string field, and fields Argo or Kubernetes accept
as "int or string" (like a retry limit). They can't go into fields that must be a real integer,
boolean, object, or list.

## Secrets: names, never contents

A Secret's *name* differs per installation, so it's a value. Its contents never pass through
Stagecraft or Helm:

```python
from hera.workflows import SecretEnv

SecretEnv(
    name="GITHUB_TOKEN",
    secret_name=ctx.value("github.tokenSecret", str),
    secret_key="token",
)
```

Chapter 8 uses exactly this.

## As a subchart

The generated chart is self-contained, but it also works as a subchart: vendor it into another
chart's `charts/` directory, list it under that chart's `dependencies`, and put its values under
the chart's name (`pipelines:` by default). Helm validates them against the generated schema
there too.

## What isn't supported (yet)

- **Optional values and defaults.** Every referenced value is required.
- **Objects and arrays** (a whole `tolerations` list or `resources` block from values). Reference
  scalars inside an object the action builds instead, as `Make` does with `requests={...}`.
- **Values in integer-only or boolean fields.**
- **Descriptions in the schema.** The generated schema has types, not documentation; describe
  values next to your values files.

These are listed, with what would justify each, in
[DESIGN.md section 15](../DESIGN.md#15-deferred-features).

## Recap

- `ctx.value(path, type)` in `compile`, `{{ values.a.b }}` in fields: both become Helm
  expressions, and both are recorded.
- The chart's `values.schema.json` is generated from those references: every value required,
  with one scalar type.
- Values come per installation; Helm checks them against the schema at install time.
- Values always render as strings; Secrets are referenced by name.

Next: [5. Stages, params, and conditions](05-stages-and-flow.md).
