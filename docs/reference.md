# Reference

Everything in one place. The [guide](README.md) explains each item in context.

## Imports

| Import | What |
|---|---|
| `from stagecraft.spec import Pipeline, Stage, Block, Param, Trigger` | The authoring models |
| `from stagecraft.context import CompileContext` | The `ctx` passed to `Block.compile` |
| `from stagecraft.contract import ValuesContract` | A values contract, for block tests |
| `from stagecraft.compile import compile_pipeline, compile_triggers` | Compile in tests |
| `from stagecraft.errors import CompileError` | Raised for contract and composition errors at compile time |
| `from hera.workflows import Container, Script, Env, SecretEnv, ...` | What `compile` returns (Hera 7.1.0) |

## `Pipeline`

| Field | Type | Default | Notes |
|---|---|---|---|
| `name` | str | required | DNS label; unique among pipelines; the `WorkflowTemplate`'s name |
| `params` | list[Param] | `[]` | Unique names |
| `stages` | list[Stage] | required | At least one; unique names; run in order |
| `on_success`, `on_failure` | list[Block] | `[]` | Run after the run ends; a failure here fails the run |
| `status` | str | None | Commit-status context for the whole run |
| `status_reporter` | Block | None | Required if any step has a status |
| `triggers` | list[Trigger] | `[]` | Unique names |

Statuses also require params `repo` and `sha`, both `required=True`. Status contexts are unique
across the pipeline.

## `Stage(name, blocks, /, ...)`

| Field | Type | Default | Notes |
|---|---|---|---|
| `name` | str | required (positional) | DNS label |
| `blocks` | list[Block] | required (positional) | At least one; unique block names; run in parallel |
| `when` | str | None | Argo condition; `{{ param.x }}` is quoted inside it |
| `status` | str | None | Commit-status context |
| `on_success`, `on_failure`, `finally_` | list[Block] | `[]` | Handlers |

## `Block`

Subclass it, add fields, and implement `compile(self, ctx: CompileContext) -> Container | Script`.

| Inherited field | Type | Default | Notes |
|---|---|---|---|
| `name` | str | kebab-case class name | DNS label; unique within its stage or handler list |
| `when` | str | None | As for stages |
| `status` | str | None | As for stages |
| `on_success`, `on_failure`, `finally_` | list[Block] | `[]` | Handlers |

Handler blocks and the status reporter can't set `when`, `status`, or handlers. Each block runs
in its own pod; no files are shared between blocks.

## `Param(name, /, ...)`

| Field | Type | Default | Notes |
|---|---|---|---|
| `name` | str | required (positional) | `[A-Za-z_][A-Za-z0-9_-]*` |
| `required` | bool | False | Exactly one of `required=True` or `default` |
| `default` | str | None | Must be in `enum` if both are set |
| `enum` | list[str] | None | Drop-down in the submit form |
| `description` | str | None | Shown in the submit form |

Param fields are literal: placeholders aren't replaced in them.

## `Trigger.cron(schedule, /, ...)`

| Field | Type | Default | Notes |
|---|---|---|---|
| `schedule` | str | required (positional) | Five cron fields, or `@hourly`, `@daily`, `@midnight`, `@weekly`, `@monthly`, `@yearly`, `@annually`, `@every <duration>` |
| `name` | str | `"cron"` | DNS label; `<pipeline>-<name>` at most 52 characters |
| `params` | dict[str, str] | `{}` | Declared params only; must set every required param; values in `enum` unless they use `{{ values.a.b }}` |
| `timezone` | str | None | IANA name; the controller's time zone if unset |

## `CompileContext.value(path, expected=None)`

Returns a reference to the Helm value at `path` (dotted, relative to `--values-prefix`).
`expected` is `str`, `int`, `float`, or `bool`; `None` accepts any scalar. Raises `CompileError`
unless the schema guarantees the value: declared and required at every level, non-null, a single
scalar type matching `expected`, reached through `properties` and local `$ref`s only, with
identifier keys.

## Placeholders

| Placeholder | Allowed in | Becomes |
|---|---|---|
| `{{ param.x }}` | block fields, `when` | `{{workflow.parameters.x}}`; quoted in `when` |
| `{{ values.a.b }}` | block fields, `when`, trigger params | A Helm expression for `.Values.a.b` |
| `{{ step.name }}`, `{{ step.status }}` | handler block fields | The step's path and outcome (stage and block handlers), or the run's name and status (pipeline handlers) |
| `{{ status.context }}`, `{{ status.state }}`, `{{ status.description }}` | `status_reporter` fields | The report being posted |

"Block fields" means every string a block's fields contain, including inside lists, dicts, and
nested models, but not strings created in `compile`. Names, `status` contexts, param fields, and
trigger schedules and time zones are literal. Any other `{{ ... }}` is passed to Argo unchanged;
a lone `{{` or `}}` fails the render.

## How values render

| Reference | Renders as |
|---|---|
| string, whole field | `{{ .Values.a.b \| quote }}` |
| number or boolean, whole field | `{{ .Values.a.b \| toJson \| quote }}` |
| inside other text | spliced into a double-quoted string |

Values always render as strings.

## Generated names

| Template | Name |
|---|---|
| Entrypoint DAG | `main` |
| A stage's DAG | `<stage>` |
| A block | `<stage>-<block>` |
| A step's handler DAG / a handler | `<stage>[-<block>]-on-exit` / `<stage>[-<block>]-<on-success\|on-failure\|finally>-<handler>[-succeeded\|-failed]` |
| Pipeline handlers | `on-exit`, `on-success-<handler>`, `on-failure-<handler>` |
| Status reporting | `status-report`, `status-reporter` |
| A trigger's `CronWorkflow` | `<pipeline>-<trigger>` |

Two generated names colliding fails the render.

## CLI

| Command | Does |
|---|---|
| `stagecraft render <paths> --values-schema <file> --out <dir> [--values-prefix <key>]` | Compile, validate, and write `<pipeline>.yaml` per pipeline; writes nothing if any fails |
| `stagecraft check <paths> --values-schema <file> [--values-prefix <key>]` | The same, without writing |
| `stagecraft graph <paths> [--pipeline <name>]` | Mermaid diagram of a pipeline |
| `stagecraft contexts <paths>` | `<pipeline><TAB><context>` per status context |
| `stagecraft version` | Stagecraft and Hera versions |

`<paths>` are pipeline files or directories (their `*.py` files, not recursive, skipping names
starting with `_`). Pipelines are the module-level `Pipeline` objects; their names must be unique.
Run from the project root: pipeline files import blocks relative to the working directory.

## Targets

Hera 7.1.0 (pinned by Stagecraft), Argo Workflows 4.1.6, Helm 4, Python 3.12+.
