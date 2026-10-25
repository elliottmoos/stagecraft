# Stagecraft: Design Spec

Stagecraft is a Python framework and CLI, built on Hera, for authoring CI/CD pipelines that compile to Argo Workflows `WorkflowTemplate` and `CronWorkflow` manifests. Authors describe *stages* of *blocks*. Stagecraft compiles that into Helm-compatible template files that a separate Helm chart consumes. Users keep triggering and observing runs in the Argo Workflows UI.

## 1. Problems it solves

1. **Mental model.** Raw Argo YAML and DAGs are hard to read, modify, and review.
2. **Isolated testing.** There is no good way to test one piece of a pipeline on its own.
3. **Missing CI/CD primitives.** Status reporting, post-run steps, finally, notify, and conditional stages are not first-class in Argo.
4. **DAG juggling.** Building those primitives by hand means managing `depends`, `onExit`, and hooks across the whole DAG.

## 2. Goals and non-goals

Goals:
- Authors think in ordered stages of parallel steps and never write `depends`, `onExit`, or `hooks`.
- CI/CD primitives are declared on stages and blocks, and the compiler lowers them to Argo mechanics.
- Blocks are testable in isolation through a typed contract.
- All installation-specific configuration is either a Helm value or bound at runtime. Pipeline and block code never contains literals that vary per installation.
- The Helm chart owns the values contract. Stagecraft complies with it and refuses to render anything that violates it. (From slice 13, Stagecraft generates the pipelines chart and its contract instead; section 16.)
- Output is plain manifests (Helm-compatible templates), committed and reviewable.

Non-goals:
- Hera is a compiler here, not a runtime. Pipelines are never executed from Python.
- Stagecraft does not generate or own the Helm chart (no `Chart.yaml`, `values.yaml`, `values.schema.json`, helpers, or packaging) and does not define the values contract. Reversed from slice 13 (decided 2026-10-25): Stagecraft generates a self-contained pipelines chart (`Chart.yaml`, `values.schema.json`, `templates/`) and nothing else. It still never writes `values.yaml` and never generates the resources pipelines depend on (service accounts, RBAC, Secrets); section 16.
- Stagecraft does not define a catalog of concrete CI blocks (clone, build, deploy, and so on). Block authors, or a separate library, own those. The framework only defines the block base type and the Argo features it maps to.
- No replacement for Argo Events or Argo CD, and no custom UI.
- Not every Argo feature is modeled. An escape hatch to raw Hera exists.

## 3. Repository boundaries

From slice 13 this changes (section 16): the pipelines project renders its own self-contained chart, installed as its own Helm release next to whatever chart provides the service accounts and Secrets. Until then:

Three roles, which can be two or three repos:

- **stagecraft** (the library repo): framework, CLI, and tests. It knows nothing about any specific chart or installation.
- **Chart repo**: owns the Helm chart, `values.yaml`, and `values.schema.json` (the values contract). Rendered templates land in `templates/`.
- **Pipelines**: pipeline definition files and any custom block code. These can live in the chart repo or in their own repo.

Pipelines repo shape: a Python project that depends on a pinned stagecraft release (and on any block libraries). It does not need to be a distributable package. A `pyproject.toml` with dependencies only, plus the pipeline files, is enough. Stagecraft's CLI imports each pipeline module and renders it. If pipelines use only blocks shipped by installable libraries, the CLI could alternatively run from a container image with the pipeline directory mounted. As soon as the repo contains custom blocks, it is a normal Python project.

The seam between repos:
- Stagecraft reads the chart's `values.schema.json` and the pipeline files, and writes only into its `--out` directory.
- The chart's CI runs `helm template`, `argo lint`, and kubeconform on the result.

## 4. Architecture

Four layers:

1. **Spec layer**: Pydantic models for `Pipeline`, `Stage`, `Param`, `Trigger`, and the `Block` base type.
2. **Block base type**: the contract every block follows (section 6).
3. **Compiler**: pure functions from a spec plus a `CompileContext` to Hera objects. No cluster access, no I/O, no globals.
4. **Serializer**: turns Hera objects into Helm-compatible YAML. It owns Argo-expression escaping and Helm expression emission.

CLI:
- `stagecraft render <pipelines> --values-schema <path> --out <dir>`: compile, validate against the contract, write templates.
- `stagecraft check <pipelines> --values-schema <path>`: the same compilation and contract validation as `render`, without writing files.
- `stagecraft graph <pipelines> [--pipeline <name>]`: Mermaid diagram of a pipeline as authored (stages, blocks, `when`, statuses, handlers, triggers), not of the compiled Argo templates. Labels are escaped with Mermaid entity codes; node ids are generated, so names like `end` are safe.
- `stagecraft contexts <pipelines>`: list commit-status contexts as `<pipeline><TAB><context>` lines, in pipeline order (for branch protection setup).

Linting rendered output uses standard tools (`argo lint`, kubeconform), not reimplemented here.

## 5. Authoring model

Target shape of a pipeline file. This is the primary acceptance example, and the blocks shown are illustrative stand-ins for whatever blocks a consumer defines:

```python
pipeline = Pipeline(
    name="service-ci",
    status="ci/pipeline",
    params=[
        Param("repo", required=True),
        Param("sha", required=True),
        Param("push", default="false", enum=["true", "false"]),
        Param("deploy", default="false", enum=["true", "false"]),
    ],
    stages=[
        Stage("verify", [
            Lint(),
            UnitTests(),
            TypeCheck(),
        ], status="ci/verify",
           on_failure=[Notify(channel="{{ values.notify.alertChannel }}")]),
        Stage("package", [BuildImage(push="{{ param.push }}")],
              status="ci/package"),
        Stage("deploy", [DeployStaging()],
              when="{{ param.deploy }} == 'true'", status="ci/deploy"),
    ],
    on_success=[Notify(channel="{{ values.notify.channel }}")],
    on_failure=[Notify(channel="{{ values.notify.alertChannel }}", include_failures=True)],
    triggers=[Trigger.cron("0 2 * * *", name="nightly",
                           params={"repo": "example/service", "sha": "main", "push": "true"})],
)
```

Rules:
- Stages are ordered. Steps within a stage run in parallel.
- Placeholders are lowered to the right expression language:
  - `{{ param.x }}`, `{{ step.name }}`, `{{ step.status }}` become Argo expressions (workflow parameters, node info).
  - `{{ values.a.b }}` becomes a Helm expression (`{{ .Values.a.b }}`).
  - Inside `when`, `{{ param.x }}` becomes a quoted string, so conditions compare strings (`{{ param.deploy }} == 'true'`). Argo parses bare words, so an unquoted `true == 'true'` is false.
- Declared `Param`s, including descriptions and enums, become the Argo UI submit form.
- Pipeline files contain no installation-specific literals. They reference Helm values by path.
- Blocks share no files: each runs in its own pod. Sources and artifacts between jobs are section 16.

## 6. The block base type and its Argo mapping

A block is a unit with a typed contract. The framework's job is to map each contract feature to Argo. Concrete block behavior is out of scope.

| Block feature | Argo mechanism |
|---|---|
| Typed inputs / outputs (deferred, section 15) | Template `inputs` / `outputs` parameters and artifacts |
| Declared value references (path and expected type) | Helm expressions, validated against the chart's values contract |
| Secret references | `secretKeyRef` env or volume; secret names come from Helm values, never secret contents |
| Resources, retry, timeout, service account, node selection | Template fields, whose values come from Helm values |
| `when` (stage or block) | `when` on the task |
| `on_failure`, `on_success`, `finally_` | One task-level `exit` hook calling a generated handler DAG, whose tasks are guarded by `when` on the step's status |
| `status="ctx/name"` | A "pending" report task the step depends on, then the final state from the step's `exit` hook (pipeline: `onExit`), all through the pipeline's `status_reporter` block |
| Pipeline `on_success` / `on_failure` | Generated `onExit` template branching on `{{workflow.status}}` |
| Triggers | A `CronWorkflow` per trigger, `workflowTemplateRef` to the pipeline's template, in the pipeline's file |
| Params | Workflow `arguments.parameters` |
| Escape hatch (raw Hera template wrapped in a minimal contract; deferred, section 15) | Passed through unchanged |

Handler blocks (`on_success`, `on_failure`, `finally_`) are ordinary blocks with the same contract. They receive context through `{{ step.name }}` and `{{ step.status }}`. In stage and block handlers these are fixed at compile time per outcome (`Succeeded` or `Failed`, with Argo's Error reported as Failed); in pipeline handlers they are the workflow's name and status. Argo exposes no exit code or failure message to task hooks. Handler blocks cannot have their own `when` or handlers.

Handler failures: a failing pipeline handler fails the pipeline (Argo's `onExit` default, kept on purpose because handler failures are unexpected). A failing stage or block handler shows as failed in the Argo UI but does not fail the pipeline, because Argo task hooks cannot fail a workflow; this is accepted.

## 7. Lowering rules

**Stages to DAG.** Each stage compiles to its own sub-DAG template, invoked as one task in the top-level DAG. A stage then has a single status, which makes stage-level `when`, hooks, and `status` straightforward. Inter-stage `depends` is generated, one edge per stage. Tradeoff to evaluate: the extra nesting level in the Argo UI graph.

**Hooks.** Prefer task-level hooks over template-level hooks (template-level fires everywhere the template is used), and prefer hooks over sibling tasks with `depends: X.Failed`. Such a sibling marks the failure as handled, so the stage and the workflow succeed despite the failed step (verified on Argo 4.1.6). Use the `exit` hook rather than expression hooks: an expression hook on a sub-DAG (stage) task never fires when another task depends on it, while the `exit` hook always fires and can pass `{{tasks.<name>.status}}` to its template. A step's handlers therefore run from one `exit` hook that calls a handler DAG branching on status, the same shape as the pipeline's `onExit`. A step skipped by `when` runs no handlers.

**Commit statuses.** Requirements: `sha` and `repo` are params available before any clone, statuses are best-effort and must not fail the build, context names are validated as unique per pipeline, and skipped or cancelled nodes must never leave a required check stuck pending.

The API call is made by the pipeline's `status_reporter`, an ordinary block owned by the consumer or a block library (stagecraft stays host-agnostic). It reads `{{ status.context }}`, `{{ status.state }}`, and `{{ status.description }}`, and gets the commit from the `repo` and `sha` params. It is compiled once and called through a generated `status-report` DAG that runs it with `continueOn`, so a report can never fail the build.

For each step with `status=`: a "pending" report task shares the step's `depends` and `when`, and the step depends on it, so pending always lands before the final state. (A `running` hook was tried: it can miss short steps on Argo 4.1.6, and a late pending would overwrite the final state.) The step's `exit` hook reports success, failure, or error. A step skipped by `when` gets a "success" report with the description "Skipped" from a sibling task with the negated condition, so a required check for a conditional stage still passes. A step that never starts because an earlier stage failed is never reported, so nothing is left pending. The pipeline's own context reports pending at start and its final state from `onExit`.

Accepted gaps: `argo terminate` runs no exit handlers, and `argo stop` skips the exit hook of a block that was running. Either can leave a context pending; re-running the workflow updates it.

**Triggers.** `Trigger.cron(schedule, name=, params=, timezone=)` compiles to a `CronWorkflow` named `<pipeline>-<name>` (at most 52 characters, Argo's limit) whose `workflowSpec` is a `workflowTemplateRef` to the pipeline's template plus the trigger's params. It is written into the pipeline's file after the template. One schedule per trigger; schedules are literal and checked against the CRD's pattern. Trigger params must be declared, must set every required param, and must be in the param's enum unless they reference a Helm value (`{{ values.a.b }}` is allowed in trigger params; Argo checks the enum at submission). Concurrency policy and history limits keep Argo's defaults until a pipeline needs otherwise.

Accepted gap (decided 2026-10-24; Kubernetes resources are eventually consistent anyway): Helm applies a release's `CronWorkflow`s before its `WorkflowTemplate`s. Helm 4.1.0 sorts kinds it doesn't know by name and creates resources in batches by kind, waiting for each batch (`lessByKind`, `batchPerform`); watching an install on kind showed the cron created 6.5 ms before its template. Argo 4.1.6 validates a new `CronWorkflow` against its template on arrival. If the template isn't there yet, the cron is marked invalid and not scheduled until the controller processes it again: about 3 minutes on kind, and at most its 20-minute informer resync. So the first ticks after an install that creates a cron can be missed. Inlining the workflow spec in each cron would avoid this, at the cost of bigger manifests and scheduled runs no longer linked to the template in the UI.

**No shared workspace.** Removed in slice 11 (section 16): each block runs in its own pod with private scratch, and files will cross job boundaries only as artifacts. Slice 7 had built a `containerset` strategy (one ReadWriteOnce claim per run; a stage's parallel readers as containers of one `containerSet` pod). Verified on Argo 4.1.6 while it existed, in case shared volumes return: a `containerSet` pod gets a claim's volume only for mounts on `containerSet.volumeMounts`, not on single containers (pod creation fails, and the workflow ends in Error); a failing container doesn't stop its siblings, and the pod fails after they finish; `volumeClaimGC: OnWorkflowCompletion` deletes the claim after failed runs too.

## 8. Configuration and the values contract

**Principle.** There is no stagecraft-side environment, profile, or cluster concept. Anything that varies per Helm release installation is one of two things:

1. **A Helm value**, referenced by path. Used for structural values (node selectors, tolerations, storage class, resource defaults, service account) and for names of runtime objects.
2. **Runtime late binding**: ConfigMap or Secret key refs, `artifactRepositoryRef`, and `workflowDefaults`, resolved by the cluster when the workflow runs. The ConfigMap or Secret *name* is typically a Helm value.

**The chart owns the contract.** The values contract is the chart's `values.schema.json` (Helm's native JSON Schema mechanism). Stagecraft is a consumer of that contract. From slice 13, Stagecraft generates the contract from the value references instead, and Helm enforces it when values are supplied (section 16); the resolver rules below then no longer apply, and the values prefix option is removed.

**Validation at render time.** Every value reference, whether it comes from a `{{ values.a.b }}` placeholder in a pipeline file or from a block's declared references, is checked against the schema. Rendering fails, with the offending pipeline, block, and path in the error, when:
- the path does not exist in the schema (undeclared keys are treated as nonexistent; the contract should be strict about `additionalProperties`),
- the schema type is incompatible with how the value is used (for example an object interpolated into a string field, or a string used where a list is expected),
- a required block value is optional in the schema without a handling rule (guard or default) available.

Nothing is rendered for a pipeline that fails validation.

Decided: references must be **required** at every level of the schema and non-null; there are no guards or block-declared defaults. The chart owns the contract, so a value pipelines use is marked required, with its default in `values.yaml`. The resolver follows `properties`, `required`, `type`, and local `$ref`s; a path through `oneOf`, `anyOf`, `allOf`, `not`, `if`, `patternProperties`, or `dependentSchemas` fails rather than being guessed at. Keys must be identifiers so they work as `.Values.a.b`. Only scalar values (string, number, boolean) are supported, each with a single type (a string-or-number value is rejected, since it would need two renderings). Structured values (objects, arrays) are deferred until a block needs one (decided 2026-10-24): Hera validates object and list fields, so a reference could only get there through placeholder workarounds and patching the output. Scalar references inside a block-built object already work, for example `node_selector={"pool": ctx.value("build.pool", str)}`.

**`CompileContext`.** The compiler passes one explicit object into every block's `compile` call. It carries the parsed values contract and mints validated value references. It exists to:
1. Keep the compile layer pure (no env reads or globals).
2. Let blocks request a value by path and expected type, receiving a typed handle that serializes to the right Helm expression, so blocks never emit Helm syntax.
3. Let blocks declare what they need, so contract violations fail at compile time and block tests supply only a small fake schema.

**Helm-compatible output.**
- Output is template files only: `WorkflowTemplate` and `CronWorkflow` manifests with Helm expressions where values are Helm-bound.
- Argo's `{{ }}` collides with Helm's. The serializer escapes every Argo expression in one pass and emits real Helm expressions only for value references, which it splices in after escaping. Every value lands in the manifest as a string. A field that is exactly one string value renders as `{{ .Values.a.b | quote }}`; a string inside other text forces a double-quoted YAML scalar and is spliced in as `quote` with its surrounding quotes trimmed, so values containing YAML syntax cannot change the document. Numbers and booleans render through `toJson` (`{{ .Values.a.b | toJson | quote }}` as a whole field, bare `toJson` inside text), because `quote` prints large numbers in exponent notation (`1000000` as `"1e+06"`, seen with Helm 4.1). A multi-line string containing a value is therefore a double-quoted one-liner rather than a literal block: less readable in diffs, but safe for any value (decided 2026-10-24). Optional blocks are not needed while references are required-only.
- The serializer fails rather than emit something Helm would misread: an unbalanced `{{` or `}}` in author text, a value reference in a mapping key, or author text containing its internal placeholder tokens. A round-trip test runs hostile strings (Argo expressions with backticks, author-typed Helm and Go template text, YAML syntax and implicit types, multi-line and non-ASCII text) through `helm template` and requires the exact compiled manifest back.
- The chart's values prefix, if the chart nests stagecraft-related values under a key, is a render option.

## 9. Testing strategy

1. **Unit**: plain Python functions, escaping, and contract-validation logic.
2. **Block contract**: a compiled block exposes exactly its declared inputs, outputs, and artifacts, and references only the value paths and types it declares. (Waits for typed inputs and outputs, section 15. Today blocks request values inside `compile` rather than declaring them up front, and every request is checked against the contract.)
3. **Contract validation**: with a fixture schema, assert that a missing path, a wrong type, and an unhandled optional value each fail rendering with a clear error, and that a valid reference renders.
4. **Composition**: each step's required inputs are produced by an earlier stage, and status contexts are unique. (Status contexts are checked; inputs come with section 16.)
5. **Compile and snapshot**: assert structure (dependencies, hooks, `onExit`, resources) and snapshot the rendered templates so every change appears in review.
6. **Static validation** (from slice 13, on the generated chart with a test values file, and the fixture chart is removed): run `helm template` against a **test-only fixture chart** (with fixture values and schema) inside the stagecraft repo, then `argo lint` and kubeconform on the result. Include escaping tests for Argo expressions in scripts, `when`, and `depends`. The fixture chart is never published.
7. **Behavior** (kind or k3d with Argo installed, small and slow): a `run_block` fixture wraps a block in a one-step workflow, installs the fixture chart, submits it, and asserts on phase and outputs. Separate tests deliberately fail and pass steps to prove hooks, `onExit`, and status sequences fire correctly, including skipped and cancelled cases.

The consumer repo has its own CI: `stagecraft check` and `render` against the real schema, then `helm template` per environment's values, `argo lint`, and kubeconform.

## 10. Repo layout (stagecraft)

```
stagecraft/
  CLAUDE.md
  DESIGN.md
  pyproject.toml
  src/stagecraft/
    spec.py          # Pipeline, Stage, Param, Trigger, Block base
    context.py       # CompileContext, value references
    contract.py      # values.schema.json loading and validation
    compile/         # stages->DAG, hooks, onExit, triggers
    serialize/       # Helm-compatible serializer, escaping
    graph.py
    cli.py
  tests/{unit,contract,snapshots,integration}/
  tests/fixtures/    # minimal blocks, fixture schema, test-only fixture chart
```

## 11. Build plan (thin vertical slices)

1. Scaffold, tooling, `CLAUDE.md`, pinned Hera and Argo versions.
2. `Pipeline` / `Stage` / minimal blocks compiled to generated `depends`, with a snapshot, `helm template` through the fixture chart, `argo lint`, and one kind run.
3. Pipeline-level `on_success` / `on_failure` via `onExit`.
4. Step and stage hooks, including stage-as-sub-DAG.
5. `status=` commit statuses with the skip and cancel cases.
6. Block contract, `CompileContext`, value references, and contract validation against a schema.
7. Workspace strategies (`containerset` first). Removed again in slice 11.
8. Helm escaping and `toYaml` handling hardened with tests.
9. CLI polish (`check`, `graph`, `contexts`) and docs.
10. Triggers: `Trigger.cron` compiled to `CronWorkflow` (added after the original plan, which had left it out).

Planned after the concept model (section 16, agreed 2026-10-25):

11. Remove the shared workspace: `Workspace`, `containerset`, the per-run claim, and workspace access flags.
12. Actions and jobs: composition replaces `Block` inheritance; Stagecraft's outcome vocabulary.
13. Generate the pipelines chart: `Chart.yaml`, a `values.schema.json` built from value references, and `templates/`. Removes `--values-schema`, `--values-prefix`, and the test fixture chart.
14. Main source: the source job (revision resolved to a SHA, checkout published as an artifact), injection into every job, `{{ source.* }}`, checks on the resolved SHA, framework values under `stagecraft.sources`. Verify on kind first.
15. Artifacts between jobs: declared action inputs and outputs, `"<stage>.<job>.<artifact>"` references, cross-stage plumbing, artifact GC. Verify against kind's MinIO first.
16. Stage sources.
17. The condition API.

Section 15 lists what was deliberately left out. Do not generalize before two or three real pipelines exist.

## 12. `CLAUDE.md` contents

- Commands: `uv run pytest`, `uv run ruff check .`, `helm template` on the fixture chart, `argo lint` and `kubeconform` on rendered output.
- Rules:
  - The compile layer stays pure: no I/O, no env reads, no globals.
  - Blocks get values only through the `CompileContext`, declared with path and type. Secrets are references.
  - Blocks never emit Helm syntax. Only the serializer does, and it escapes all Argo expressions in one pass.
  - A reference to a value missing from the contract, or of the wrong type, must fail rendering. Never silently default.
  - Authors never write `depends`, `onExit`, or `hooks`. If a primitive cannot be expressed, fix the compiler.
  - Stagecraft never generates chart files or the values contract.
  - Hera's API changes between versions, so check the pinned version's docs rather than guessing.
- Workflow: write a failing compile or snapshot test first, then the implementation.

## 13. Verify against the pinned Argo and Hera versions

- Hook semantics: field names, expression scope, workflow-level `running` hooks, effect of a failing hook on workflow status, behavior on skipped nodes.
- Template-level versus task-level hook firing.
- Argo CD sync order for CRDs versus chart contents, if Argo CD deploys the chart.
- For section 16, on kind with its MinIO: a git input artifact checking out a commit that isn't a branch tip, at shallow depth; a global output parameter read by `onExit` and by exit hooks; artifacts routed through stage DAG outputs and inputs; an input artifact placed inside another (an artifact under the source checkout); whether a failed job's outputs are uploaded; `artifactGC` deleting objects when a workflow is deleted.
- What `values.schema.json` can and cannot express (for example `$ref`, `oneOf`, defaults) and how much of it the validator must support.

## 14. Open decisions

- Where pipeline definition files live: the chart repo or a separate pipelines repo. (Settled by section 16: a pipelines project renders its own chart.)
- Commit rendered templates into the chart repo, or publish them as a build artifact.
- Template naming across multiple releases in one namespace versus stable names in the Argo UI.
- Whether a reference block library belongs in a third package.
- Workflow TTL and the artifact GC service account (section 16): from the controller's `workflowDefaults`, or emitted per pipeline.
- The generated chart's name and version (section 16). Default: a `--chart-name` flag and a fixed version `0.0.0`, since the chart is committed rather than published.
- Whether to offer a resolved preview mode (render with a values file) for local debugging, or rely on `helm template`.

## 15. Deferred features

Left out on purpose, as of slice 10 (2026-10-24; updated for section 16 on 2026-10-25). Typed inputs and outputs, resolving a branch to its commit, and the workspace strategies left this list when section 16 replaced them. Each waits for a real pipeline that needs it ("do not generalize before two or three real pipelines exist"). The pipeline file or render error that would need it is the signal to pick one up.

| Feature | Why deferred | Revisit when |
|---|---|---|
| Escape hatch: a raw Hera template wrapped in a minimal contract (section 6) | Every fixture need was met by ordinary blocks | A block can't be expressed as one `Container` or `Script` |
| Structured values: objects and arrays from Helm values (section 8) | Hera validates object and list fields, so a reference could only get there through placeholder workarounds and patching the output. Scalar references inside a block-built object already work | The chart must own the whole shape of a field, such as `tolerations`, `affinity`, or `resources` |
| Values in non-string fields: integer or boolean fields such as `suspend` or a pure-int Kubernetes field | Value references are strings, and always render as strings | A block or trigger needs a Helm value in such a field (see per-installation trigger switch) |
| Per-installation trigger switch (`suspend` from Helm values) | A boolean field, so it waits for values in non-string fields | An environment must not run a pipeline's schedule |
| Trigger options: `concurrencyPolicy`, history limits, `startingDeadlineSeconds`, several schedules in one trigger | Argo's defaults are fine; several schedules are several triggers | A schedule overlaps its own runs, or history needs tuning |
| Output parameters (small strings such as an image digest) | Section 16 passes files between jobs as artifacts only | A job needs a small value from an earlier job, or a condition needs one |
| Optional action inputs | Every declared input must be bound | An action is useful both with and without an earlier job's files |
| Artifacts for reactions and the reporter (for example, uploading a failed job's test reports) | Unverified whether a failed job's outputs are uploaded, and how exit hooks would receive them | A reaction must read a job's files; prove it on kind first |
| Opting a job out of source injection | Every job downloads the sources; cheap from an in-cluster repository | Source download time matters for jobs that don't need them |
| Caches that outlive a run (for example a Go module cache) | A different concept: shared across runs, not handed between jobs | Build times need them |
| Multi-line scripts containing a string value as readable literal blocks (section 8) | Kept as double-quoted one-liners: correct for any value, but harder to read in diffs | Rendered templates are reviewed often enough for this to hurt |

Accepted gaps (Argo or Helm behavior, not missing features) are kept with the mechanisms they affect in section 7: `argo terminate` and `argo stop` leaving statuses pending, stage and block handler failures not failing the pipeline, and new CronWorkflows missing their first ticks. Still unverified: an upgrade that changes a trigger's params together with its template's params (Helm updates the cron first, while the old template is still in place).


## 16. Concept model and contracts (target, not yet built)

Agreed 2026-10-25. This is the model the next slices build toward (section 11, slices 11 to 17). Where it conflicts with sections 5 to 7, those sections describe the current code and this section wins for new work. Points marked **open** are settled in the slice that builds them.

### Personas

Two kinds of user, and every concept belongs to one of them:

| Persona | Writes | Should know | Should never need |
|---|---|---|---|
| **Pipeline author** (service team) | Pipelines: configuration | Stagecraft's CI vocabulary | Hera, Argo, Helm |
| **Action author** (platform team) | Reusable units of work: code | Containers, Hera's `Container` and `Script`, Helm values by path and type | DAGs, hooks, Helm syntax, workflow-level Argo |

Today's `Block` serves both personas in one class: the action author defines it, and the pipeline author fills in fields it inherits (`when`, `status`, handlers). The split replaces that inheritance with composition: an **action** (what runs) placed by a **job** (where and under which conditions it runs).

### Concepts

| Concept | CI meaning | Defined by | What the framework guarantees | Argo it hides |
|---|---|---|---|---|
| **Pipeline** | A named CI process, installed once per chart | Pipeline author | One installable unit; reactions and checks for the whole run | `WorkflowTemplate`, entrypoint, `onExit` |
| **Run input** (param) | What a run is about | Pipeline author | Declared, validated, shown in the submit form | Workflow parameters |
| **Source** | A repository at a commit | Pipeline author (pipeline or stage) | Resolved to one commit SHA, fetched once, placed at a framework-chosen path | Git, artifacts, global outputs |
| **Stage** | An ordered phase, and a barrier | Pipeline author | Its jobs run concurrently; it fails if any job fails; later stages run after success or skip | Sub-DAG, `depends` |
| **Job** | A named placement of an action in a stage | Pipeline author | Has an outcome; can be conditional, checked, reacted to; inputs wired | Tasks, `when`, hooks, artifact arguments |
| **Action** | Reusable, configurable work that runs as one container | Action author | Configuration validated; value references typed and collected into the chart's schema; run inputs substituted | Template naming and wiring |
| **Artifact** | Files one job hands to jobs in later stages | Action author declares; pipeline author wires | Exists before any consumer runs; deleted with the run | Output and input artifacts, stage DAG plumbing, artifact GC |
| **Outcome** | succeeded, failed, or skipped | Framework | One vocabulary for reactions, checks, and conditions | Argo phases (`Succeeded`, `Error`, `Omitted`, ...) |
| **Condition** | Whether a job or stage runs | Pipeline author | Typed, checked at render time | `when` expressions and their quoting |
| **Reaction** (handler) | Work triggered by an outcome | Pipeline author, using actions | When it fires, what context it gets, failure semantics (section 6) | Exit hooks, `onExit` |
| **Check** (commit status) | An outcome shown on the commit | Pipeline author names it; a **reporter** action delivers it | Pending before final; skipped counts as success; best-effort | Report tasks, the `status-report` DAG |
| **Schedule** (trigger) | Runs without a human | Pipeline author | Valid schedule, complete inputs | `CronWorkflow` |
| **Installation config** | Per-environment settings | Supplied at deploy time, against the generated schema | Every referenced value required and typed; Helm rejects bad values before anything is installed | Helm syntax, escaping, the schema |

There is no shared workspace concept any more (see "Files between jobs").

### The action contract

The only place an action author plugs in code.

- **Configuration:** the action's own validated fields. Run inputs and Helm values arrive as placeholders in those fields; installation values also through `ctx.value(...)` in `compile`.
- **Interface:** the artifacts it reads and writes, each a name and a path: `outputs = {"bin": "dist/app"}`, `inputs = {"bin": "dist/app"}`. A relative path is inside the main source checkout. Every declared input is required.
- **Output:** exactly one Hera `Container` or `Script`. Template-level settings (retries, timeouts, service account) are allowed again, since no job shares a pod.
- **Outcome:** exit code 0 means succeeded; anything else means failed.
- **Rules:** pure (no I/O, env reads, or globals), no Helm syntax, no workflow-level Argo. Actions don't declare sources; sources are injected (below).

The **reporter** is an action that reads `{{ status.context }}`, `{{ status.state }}`, `{{ status.description }}`, `{{ source.repo }}`, and `{{ source.sha }}`. Checks require a pipeline source, which replaces today's `repo`/`sha` param convention.

### The job contract

A job binds an action into a stage: its name (unique in the stage), its condition, its check context, its reactions, and the source of every input:

```python
Stage("package", [
    Job("image", DockerBuild(), inputs={"bin": "build.compile.bin"}, status="ci/image"),
])
```

Input references are `"<stage>.<job>.<artifact>"`. The render fails when a reference names a stage, job, or artifact that doesn't exist; names a job in the same or a later stage; names a job or stage that has a condition (it may be skipped, leaving no artifact); binds an input the action doesn't declare; or leaves a declared input unbound.

**Open:** the exact API, including whether a bare action in a stage is shorthand for a job with a default name.

### Sources and revisions

- **Declaring them.** A pipeline has at most one main source: `Pipeline(source=Source(repo=..., revision=...))`. A stage can request more: `Stage(..., sources=[Source(name="fixtures", repo=..., revision=...)])`. Arbitrary extra sources exist only at stage level. `repo` and `revision` may use `{{ param.x }}` and `{{ values.a.b }}`.
- **Revisions.** A revision is a commit SHA or any other reference. A reference that isn't a SHA is resolved to its head commit once, and that SHA is used from then on. The main source resolves before the first stage. A stage source resolves when its stage starts, so all of that stage's jobs see one commit. `argo retry` keeps succeeded nodes, so a retried run keeps its SHAs.
- **Fetching.** A framework-generated source job resolves the revision, checks it out, and publishes the checkout (with `.git`) as an artifact and the SHA as an output. The main source job is a task named `source` before the first stage. A stage source job is a task `source-<name>` at the start of its stage's sub-DAG, which its jobs depend on. The SHA of the main source is a global output, so pipeline reactions and checks can read it (`{{ source.sha }}`).
- **Injection.** Every job gets the main source, and every job in a stage gets that stage's sources, through an input artifact the framework adds. Sources are placed at `/sources/<name>` (the main source is named `main`) and exposed as `STAGECRAFT_SOURCE_<NAME>` (uppercased, `-` as `_`). Paths are unique by construction: source names are unique within a stage and `main` is reserved. The main source's path is the default working directory.
- **Reactions and the reporter get no sources.** They run from exit hooks and `onExit`, which also run when a source job failed. They read `{{ source.repo }}` and `{{ source.sha }}` instead.
- **Accepted gap:** if the main source job fails (bad reference, credentials), there is no SHA, so the pipeline's check can't be reported. The failure shows in the Argo UI.
- **Settings:** the source job's image and git credentials are framework values at fixed paths under `stagecraft.sources` in the generated chart (below), required as soon as a pipeline declares a source. The exact keys are settled in slice 14.

### Files between jobs

**There is no shared filesystem**, not even between sequential stages (decided 2026-10-25). The cluster's storage is ReadWriteOnce (EBS) on multi-zone clusters, so a shared per-run volume pins a run to one zone and serializes its parallel jobs onto one node or pod.

- Each job has private scratch: its own container filesystem, including its copy of the sources.
- Anything that crosses a job boundary is an artifact (sources included), stored in the controller's default artifact repository. Ours is S3-compatible (RustFS). Stagecraft emits no repository configuration.
- Cross-stage references are routed by the compiler: the producing stage's sub-DAG exports the artifact as a DAG output (`from: {{tasks.<job>.outputs.artifacts.<name>}}`), the consuming stage's task passes it in as an argument, and the consuming job receives it from the stage's inputs. Authors only write `"<stage>.<job>.<artifact>"`.
- **Retention:** Stagecraft sets `artifactGC.strategy: OnWorkflowDeletion`. Deletion therefore needs workflows to be deleted, and the GC pods need a service account allowed to delete from the bucket. **Open:** whether both come from the controller's `workflowDefaults` (recommended: platform settings, like the artifact repository) or from the pipeline.
- Removed: `Workspace`, the `containerset` strategy, the per-run claim, `workspace="read"/"write"`, the writer rule, and the reserved `workspace` job name.
- Caches that outlive a run are a separate concept, out of scope for now (section 15).

### Outcomes

Stagecraft's outcomes are `succeeded`, `failed`, and `skipped`, mapped from Argo phases (`Succeeded`; `Failed` and `Error`; `Skipped` and `Omitted`). Reactions, `{{ step.status }}`, check descriptions, and conditions use them instead of Argo phase names. **Open:** whether checks keep GitHub's separate `error` state for Argo's `Error`.

### Conditions

A typed condition API replaces raw `when` strings in pipeline files, for example `when=param("deploy") == "true"`. It removes the quoting trap (section 5) and is checked at render time. It covers only comparisons over Stagecraft concepts that map cleanly onto Argo `when` expressions. Params come first; other operands (such as a source's revision or an earlier stage's outcome) are added when a pipeline needs them.

### Escape valves

Kept on purpose, with their use cases unknown yet:
- **Hera inside actions.** Action authors build Hera `Container`s and `Script`s directly.
- **Native Argo expressions in pipeline strings.** Passed through unchecked.

### The generated chart

Decided 2026-10-25. A pipelines project renders into one self-contained Helm chart, installed as its own release:

```
<out>/
  Chart.yaml           generated; carries a Stagecraft marker annotation
  values.schema.json   generated from the pipelines' value references
  templates/           one file per pipeline (WorkflowTemplate, then its CronWorkflows)
```

- **Ownership.** Stagecraft owns exactly these three entries and replaces them on every render. It never creates or touches `values.yaml` or any other file in the directory, and refuses to render into a directory whose `Chart.yaml` lacks its marker.
- **Values come at deploy time**, from a file such as `values-pipelines.yaml` passed to the release. The chart has no defaults: every referenced value is required, and Helm rejects a missing or wrongly typed value at `helm template` or install, naming the path.
- **The schema is generated.** Each value reference, from `ctx.value(path, type)` or a `{{ values.a.b }}` placeholder (always a string), becomes a required, typed property; objects are strict (`additionalProperties: false`), except that `global` is allowed so the chart also works as a subchart. Two references to the same path with different types fail the render. The cost, accepted: a mistyped path becomes a new required value instead of a render error, so it surfaces at `helm template` rather than `stagecraft render`, and the generated schema has no descriptions, enums, or patterns.
- **Framework values** live under the reserved `stagecraft` key (for example `stagecraft.sources`). A reference into `stagecraft.` from an action or pipeline fails the render.
- **Not generated:** what pipelines depend on by name: the workflow service account and RBAC, the Secrets (GitHub token, git credentials), the artifact repository, and controller defaults. Whatever deploys them today keeps doing so; the chart refers to them through values.
- **As a subchart.** Not needed, but possible: vendored into another chart's `charts/` directory, listed in its `Chart.yaml` dependencies (Helm 4.1 `helm lint` requires the listing), with its values under the chart's name. Helm validates a subchart's schema against its scoped values (verified with Helm 4.1.0).
- **CLI.** `render` writes the chart; `check` compiles without writing. `--values-schema` and `--values-prefix` are removed.
