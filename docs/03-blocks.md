# 3. Blocks

Blocks are where your pipelines' real work is defined, and the one place you write code. This
chapter covers what a block is made of, the rule that shapes every block design (fields versus
`compile`), and how to test blocks.

## Anatomy

```python
class Make(Block):            # 1. a subclass of stagecraft.spec.Block
    """Runs one make target."""

    target: str               # 2. fields: what an author can configure

    def compile(self, ctx):   # 3. compile: the container this block runs as
        return Container(...)
```

1. **A `Block` subclass.** Blocks are Pydantic models: fields are typed and validated, unknown
   arguments are rejected (a typo like `Make(taget="lint")` fails), and instances are immutable.
2. **Fields.** Anything an author should be able to set. Use defaults generously so the common
   case is short.
3. **`compile(self, ctx)`** returns one Hera `Container` or `Script`. Stagecraft names it, wires
   it into the workflow, and adds anything the pipeline asks for (handlers, statuses).

Every block also inherits a few fields from `Block`, covered in later chapters: `name`, `when`,
`status`, `on_success`, `on_failure`, and `finally_`.

## A reusable block

`Shell` from chapter 2 works, but a pipeline full of shell one-liners is hard to read and easy to
get wrong. Purpose-built blocks name the intent and validate their inputs:

```python file=blocks.py
import re

from hera.workflows import Container
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
from stagecraft.spec import Pipeline, Stage

from blocks import Make

pipeline = Pipeline(
    name="service-ci",
    stages=[
        Stage(
            "verify",
            [
                Make(name="lint", target="lint"),
                Make(name="unit-tests", target="test"),
            ],
        ),
        Stage("package", [Make(name="image", target="image")]),
    ],
)
```

`Make` asks for a new value, `images.build`, so the chart's contract grows:

```yaml file=chart/values.yaml
images:
  shell: alpine:3.22
  build: golang:1.25
```

```json file=chart/values.schema.json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "type": "object",
  "required": ["images"],
  "properties": {
    "images": {
      "type": "object",
      "required": ["shell", "build"],
      "properties": {
        "shell": {"type": "string"},
        "build": {"type": "string"}
      }
    }
  }
}
```

## Names

- A block's name defaults to its class name in kebab case: `Make` is `make`, `GitHubStatus` is
  `git-hub-status`.
- Names must be unique within a stage, so two `Make` blocks in one stage each need `name=`.
- Names must be lowercase DNS labels (`a-z`, `0-9`, `-`, at most 63 characters).
- The compiled template is named `<stage>-<block>`, for example `verify-lint`. That's the name you
  see in the Argo UI.

## What `compile` can return

`compile` returns a Hera
[`Container`](https://github.com/argoproj-labs/hera/tree/7.1.0) or `Script`, and Stagecraft
passes it through, apart from setting its name. So a block can set anything Hera allows:
environment variables, resource requests, retries, timeouts, a service account, node selectors,
security contexts, volumes. For example:

```python
from hera.workflows import Container, Env, RetryStrategy


class Flaky(Block):
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


class Python(Block):
    """Runs a Python snippet."""

    source: str

    def compile(self, ctx: CompileContext) -> Script:
        return Script(image=ctx.value("images.python", str), command=["python"], source=self.source)
```

A block can set **container fields** (image, command, args, env, resources, working directory),
which describe the container itself, and **template fields** (retries, timeouts, service account,
node selection), which describe the pod around it.

Rules for `compile`:

- **No I/O.** Don't read files, environment variables, or the network. A block's output must
  depend only on its fields and `ctx`, so a render is reproducible.
- **No installation-specific literals.** Images, Secret names, registries, and resource sizes
  differ per environment: get them from `ctx` (chapter 4).
- **No Helm syntax.** Never write `{{ .Values... }}`; Stagecraft generates it.

## Fields versus `compile`: where placeholders work

Authors put **placeholders** such as `{{ param.sha }}` in block fields (chapter 5 introduces
them). Stagecraft replaces them in every string field, including inside lists, dicts, and nested
models, **before** it calls `compile`. It does not look inside what `compile` returns.

So anything that should accept a placeholder must be a field. A placeholder written inside
`compile` would reach Argo unreplaced:

```python
class Bad(Block):
    def compile(self, ctx):
        return Container(..., args=["echo {{ param.sha }}"])     # never replaced


class Good(Block):
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

## Testing blocks

A block is a plain class, so test it with plain pytest. Give `compile` a small contract with just
the values the block needs:

```python file=tests/test_blocks.py
import pytest
from pydantic import ValidationError

from stagecraft.context import CompileContext
from stagecraft.contract import ValuesContract
from stagecraft.errors import CompileError

from blocks import Make

SCHEMA = {
    "type": "object",
    "required": ["images"],
    "properties": {
        "images": {
            "type": "object",
            "required": ["build"],
            "properties": {"build": {"type": "string"}},
        }
    },
}
CONTEXT = CompileContext(ValuesContract(SCHEMA))


def test_make_runs_its_target():
    container = Make(target="lint").compile(CONTEXT)

    assert container.command == ["make"]
    assert container.args == ["lint"]


def test_make_accepts_one_target_only():
    with pytest.raises(ValidationError, match="single make target"):
        Make(target="lint test")


def test_make_needs_the_build_image_from_the_chart():
    empty = CompileContext(ValuesContract({"type": "object", "properties": {}}))

    with pytest.raises(CompileError, match="images.build"):
        Make(target="lint").compile(empty)
```

Calling `compile` directly skips placeholder replacement. To test how a block behaves inside a
pipeline, compile a whole pipeline with `stagecraft.compile.compile_pipeline`, or run
`stagecraft check` in CI.

## Sharing blocks

Blocks are ordinary Python. Once several pipelines repos want the same blocks, move them into a
package (a block library), version it, and depend on it like any other library.

## Recap

- A block is a validated Pydantic model whose `compile` returns one Hera container.
- `compile` can set anything Hera allows; it must be pure and get installation details from `ctx`.
- Placeholders work in fields, not in what `compile` returns. Design fields accordingly.
- Test blocks by calling `compile` with a small contract.

Next: [4. Helm values and the contract](04-values.md).
