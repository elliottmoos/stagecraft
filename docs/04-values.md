# 4. Helm values and the contract

Pipelines run in more than one place: a staging cluster and a production cluster may use
different registries, Secrets, and machine sizes. Those details are **Helm values**, owned by the
chart. This chapter covers how blocks and pipelines use them, and the contract that makes every
use safe.

## Two ways to reference a value

**In a block's `compile`**, call `ctx.value(path, type)`:

```python
image=ctx.value("images.build", str)
```

**In a block's fields** (and in `when` conditions and trigger params), write a placeholder:

```python
Notify(channel="{{ values.notify.channel }}")
```

Both mean "the value at `.Values.<path>` in this installation." Neither gives you the value
itself: Stagecraft runs before any installation exists. What you get back is a reference that
renders as a Helm expression, which Helm fills in at install time.

`type` is how the block uses the value: `str`, `int`, `float`, or `bool`. Placeholders accept
any scalar type.

## The contract

The chart's `values.schema.json` is the contract. Every reference is checked against it at
render time, and any problem fails the render, naming the pipeline, stage, block, and path. A
referenced value must be:

- **Declared**: under `properties` at every level. `additionalProperties: true` doesn't count: an
  undeclared key isn't guaranteed to exist.
- **Required at every level.** If a value might be missing, a pipeline can't rely on it. Mark it
  required and give it a default in the chart's `values.yaml`.
- **Non-null, with a single scalar type**: `string`, `integer`, `number`, or `boolean`. A
  `["string", "null"]` or `["string", "integer"]` type is rejected.
- **Reachable simply**: through `properties` and local `$ref`s (`#/$defs/...`). A path through
  `oneOf`, `anyOf`, `allOf`, `not`, `if`, `patternProperties`, or `dependentSchemas` is rejected
  rather than guessed at.
- **Keyed by identifiers** (`[A-Za-z_][A-Za-z0-9_]*`), so it works as `.Values.a.b`. A key like
  `alert-channel` can't be referenced.

The type must also match how it's used: `ctx.value("build.retries", str)` fails if the schema
says `integer`.

What the errors look like:

```
pipeline 'service-ci', stage 'verify', block 'lint': value 'build.memory' is not declared in the values schema (no property 'memory' under build)
pipeline 'service-ci', stage 'verify', block 'lint': value 'build.region' is optional in the values schema ('region' is not required); only required values can be referenced
value 'build.retries' is integer in the values schema, but is used as string
```

## Growing the example

Builds need different machine sizes per cluster, so `Make` takes its resource requests from the
chart:

```python file=blocks.py
import re

from hera.workflows import Container
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
the helper checks units like `Gi` when the block is compiled, and a value reference isn't a
quantity yet. The general rule: if a Hera helper validates a field's content, use the plain model
for value references.

The chart declares the new values. `$defs` keeps repeated shapes in one place:

```yaml file=chart/values.yaml
images:
  shell: alpine:3.22
  build: golang:1.25
build:
  cpu: "1"
  memory: 2Gi
```

```json file=chart/values.schema.json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "type": "object",
  "required": ["images", "build"],
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
    }
  },
  "$defs": {
    "image": {"type": "string", "description": "A container image reference"}
  }
}
```

`Make` now needs `build.cpu` and `build.memory`, so its test's contract grows too. A block's
test contract lists exactly what the block needs, which also documents it:

```python file=tests/test_blocks.py
import pytest
from pydantic import ValidationError

from stagecraft.context import CompileContext
from stagecraft.contract import ValuesContract
from stagecraft.errors import CompileError

from blocks import Make


def strings(**groups: list[str]) -> dict:
    """A schema requiring string values, such as strings(images=["build"])."""
    return {
        "type": "object",
        "required": list(groups),
        "properties": {
            group: {
                "type": "object",
                "required": keys,
                "properties": {key: {"type": "string"} for key in keys},
            }
            for group, keys in groups.items()
        },
    }


CONTEXT = CompileContext(ValuesContract(strings(images=["build"], build=["cpu", "memory"])))


def test_make_runs_its_target():
    container = Make(target="lint").compile(CONTEXT)

    assert container.command == ["make"]
    assert container.args == ["lint"]


def test_make_accepts_one_target_only():
    with pytest.raises(ValidationError, match="single make target"):
        Make(target="lint test")


def test_make_needs_its_build_resources_from_the_chart():
    no_resources = CompileContext(ValuesContract(strings(images=["build"])))

    with pytest.raises(CompileError, match="build.cpu"):
        Make(target="lint").compile(no_resources)
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

Chapter 7 uses exactly this.

## A nested chart: `--values-prefix`

If the chart keeps these values under a key, for example `ci:` in an umbrella chart, keep the
block paths short and render with a prefix:

```sh
uv run stagecraft render pipelines --values-schema chart/values.schema.json \
  --values-prefix ci --out chart/templates
```

`ctx.value("images.build", str)` then refers to `.Values.ci.images.build`.

## What isn't supported (yet)

- **Optional values.** There are no fallbacks or guards: a pipeline uses only what the chart
  guarantees.
- **Objects and arrays** (a whole `tolerations` list or `resources` block from values). Reference
  scalars inside a block-built object instead, as `Make` does with `requests={...}`.
- **Values in integer-only or boolean fields.**

These are listed, with what would justify each, in
[DESIGN.md section 15](../DESIGN.md#15-deferred-features).

## Recap

- `ctx.value(path, type)` in `compile`, `{{ values.a.b }}` in fields: both become Helm
  expressions.
- The chart's schema is the contract: required, non-null, single-typed scalars, reached through
  `properties` and local `$ref`s.
- Values always render as strings; Secrets are referenced by name.

Next: [5. Stages, params, and conditions](05-stages-and-flow.md).
