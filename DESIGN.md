# Stagecraft: Design Spec

Stagecraft is a Python framework and CLI, built on Hera, for authoring CI/CD pipelines that compile to Argo Workflows `WorkflowTemplate` and `CronWorkflow` manifests. Authors describe *stages* of parallel *steps*, each placing a reusable *action*. Stagecraft compiles that into Helm-compatible template files that a separate Helm chart consumes. Users keep triggering and observing runs in the Argo Workflows UI.

## 1. Problems it solves

1. **Mental model.** Raw Argo YAML and DAGs are hard to read, modify, and review.
2. **Isolated testing.** There is no good way to test one piece of a pipeline on its own.
3. **Missing CI/CD primitives.** Status reporting, post-run steps, finally, notify, and conditional stages are not first-class in Argo.
4. **DAG juggling.** Building those primitives by hand means managing `depends`, `onExit`, and hooks across the whole DAG.

## 2. Goals and non-goals

Goals:
- Authors think in ordered stages of parallel steps and never write `depends`, `onExit`, or `hooks`.
- CI/CD primitives are declared on stages and steps, and the compiler lowers them to Argo mechanics.
- Actions are testable in isolation through a typed contract.
- All installation-specific configuration is either a Helm value or bound at runtime. Pipeline and action code never contains literals that vary per installation.
- Stagecraft generates the pipelines chart and its values contract from what the pipelines use; Helm enforces the contract on each installation's values (section 16).
- Output is plain manifests (Helm-compatible templates), committed and reviewable.

Non-goals:
- Hera is a compiler here, not a runtime. Pipelines are never executed from Python.
- Stagecraft generates only the self-contained pipelines chart (`Chart.yaml`, `values.schema.json`, `templates/`; decided 2026-10-25, built in slice 13). It never writes `values.yaml`, helpers, or packaging, and never generates the resources pipelines depend on (service accounts, RBAC, Secrets); section 16.
- Stagecraft does not define a catalog of concrete CI actions (clone, build, deploy, and so on). Action authors, or a separate library, own those. The framework only defines the action base type and the Argo features it maps to.
- No replacement for Argo Events or Argo CD, and no custom UI.
- Not every Argo feature is modeled. Actions build Hera templates directly; a raw-Hera escape hatch beyond one `Container` or `Script` is deferred (section 15).

## 3. Repository boundaries

Two roles:

- **stagecraft** (the library repo): framework, CLI, and tests. It knows nothing about any specific chart or installation.
- **Pipelines project**: pipeline definition files, any custom action code, and the values for each installation. It renders its own self-contained chart, installed as its own Helm release next to whatever provides the service accounts, Secrets, and artifact repository the pipelines refer to by name.

Pipelines repo shape: a Python project that depends on a pinned stagecraft release (and on any action libraries). It does not need to be a distributable package. A `pyproject.toml` with dependencies only, plus the pipeline files, is enough. Stagecraft's CLI imports each pipeline module and renders it. If pipelines use only actions shipped by installable libraries, the CLI could alternatively run from a container image with the pipeline directory mounted. As soon as the repo contains custom actions, it is a normal Python project.

The seam:
- Stagecraft reads the pipeline files and writes only the chart in its `--out` directory: `Chart.yaml`, `values.schema.json`, and `templates/`.
- The project's CI runs `helm template` with each installation's values (which validates them against the generated schema), `argo lint`, and kubeconform on the result.

## 4. Architecture

Five layers:

1. **Spec layer**: Pydantic models for `Pipeline`, `Stage`, `Step`, `Param`, `Trigger`, and the `Action` base type.
2. **Action base type**: the contract every action follows (sections 6 and 16).
3. **Compiler**: pure functions from a spec plus a `CompileContext` to Hera objects. No cluster access, no I/O, no globals.
4. **Serializer**: turns Hera objects into Helm-compatible YAML. It owns Argo-expression escaping and Helm expression emission.
5. **Chart**: packages the serialized templates with `Chart.yaml` and the values schema generated from the recorded value references.

CLI:
- `stagecraft render <pipelines> --out <dir> [--chart-name <name>]`: compile and write the chart (section 16).
- `stagecraft check <pipelines>`: the same compilation as `render`, without writing files.
- `stagecraft graph <pipelines> [--pipeline <name>]`: Mermaid diagram of a pipeline as authored (stages, steps, `when`, statuses, handlers, triggers), not of the compiled Argo templates. Labels are escaped with Mermaid entity codes; node ids are generated, so names like `end` are safe.
- `stagecraft contexts <pipelines>`: list commit-status contexts as `<pipeline><TAB><context>` lines, in pipeline order (for branch protection setup).

Linting rendered output uses standard tools (`argo lint`, kubeconform), not reimplemented here.

## 5. Authoring model

Target shape of a pipeline file. This is the primary acceptance example, and the actions shown are illustrative stand-ins for whatever actions a consumer defines:

```python
pipeline = Pipeline(
    name="service-ci",
    status="ci/pipeline",
    params=[
        Param("revision", default="main"),
        Param("push", default="false", enum=["true", "false"]),
        Param("deploy", default="false", enum=["true", "false"]),
    ],
    source=Source(url="https://github.com/example/service.git", revision="{{ param.revision }}"),
    stages=[
        Stage("verify", [
            Lint(),
            Step("unit-tests", GoTest(), status="ci/unit-tests"),
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
                           params={"revision": "main", "push": "true"})],
)
```

Rules:
- Stages are ordered. Steps within a stage run in parallel. A step places an action under a name; a bare action is a step named after its class (`Lint()` is the step `lint`).
- Placeholders are lowered to the right expression language:
  - `{{ param.x }}` and `{{ step.name }}` become Argo expressions (workflow parameters, node info); `{{ step.outcome }}` is fixed per handler variant (`succeeded` or `failed`).
  - `{{ values.a.b }}` becomes a Helm expression (`{{ .Values.a.b }}`).
  - Inside `when`, `{{ param.x }}` becomes a quoted string, so conditions compare strings (`{{ param.deploy }} == 'true'`). Argo parses bare words, so an unquoted `true == 'true'` is false.
- Declared `Param`s, including descriptions and enums, become the Argo UI submit form.
- Pipeline files contain no installation-specific literals. They reference Helm values by path.
- Steps share no files: each runs in its own pod, starting from its own copy of the source's checkout. Artifacts between steps are section 16.

## 6. Actions, steps, and their Argo mapping

An action is a unit with a typed contract, placed by a step. The framework's job is to map each contract feature to Argo. Concrete action behavior is out of scope.

| Action or step feature | Argo mechanism |
|---|---|
| Output parameters (slice 15) and artifact inputs and outputs (slice 16; section 16) | Template `outputs` with `globalName`, read by later steps as `{{workflow.outputs.parameters/artifacts.<name>}}` |
| Value references (`ctx.value(path, type)`, `{{ values.a.b }}`) | Helm expressions, each a required, typed property of the generated values schema |
| Secret references | `secretKeyRef` env or volume; secret names come from Helm values, never secret contents |
| Resources, retry, timeout, service account, node selection | Template fields, whose values come from Helm values |
| Pipeline `source` | A generated `source` task first in `main`: a shallow `git fetch` publishing the commit and checkout as global outputs; each step task gets the checkout as an input artifact (section 16) |
| `when` (stage or step) | `when` on the task |
| `on_failure`, `on_success`, `finally_` | One task-level `exit` hook calling a generated handler DAG, whose tasks are guarded by `when` on the step's status |
| `status="ctx/name"` | A "pending" report task the step depends on, then the final state from the step's `exit` hook (pipeline: `onExit`), all through the pipeline's `status_reporter` action |
| Pipeline `on_success` / `on_failure` | Generated `onExit` template branching on `{{workflow.status}}` |
| Triggers | A `CronWorkflow` per trigger, `workflowTemplateRef` to the pipeline's template, in the pipeline's file |
| Params | Workflow `arguments.parameters` |
| Escape hatch (raw Hera template wrapped in a minimal contract; deferred, section 15) | Passed through unchanged |

Handlers (`on_success`, `on_failure`, `finally_`) are steps placing ordinary actions; a bare action is named after its class. They receive context through `{{ step.name }}` (the stage or step path, or the workflow's name in pipeline handlers) and `{{ step.outcome }}`, which is fixed at compile time per handler variant: `succeeded` or `failed`, with Argo's Error counted as failed (`finally_` handlers are compiled once per outcome). Argo exposes no exit code or failure message to task hooks. Handler steps cannot have their own `when`, `status`, or handlers.

Handler failures: a failing pipeline handler fails the pipeline (Argo's `onExit` default, kept on purpose because handler failures are unexpected). A failing stage or step handler shows as failed in the Argo UI but does not fail the pipeline, because Argo task hooks cannot fail a workflow; this is accepted.

## 7. Lowering rules

**Stages to DAG.** Each stage compiles to its own sub-DAG template, invoked as one task in the top-level DAG. A stage then has a single status, which makes stage-level `when`, hooks, and `status` straightforward. Inter-stage `depends` is generated, one edge per stage. Inside a stage's DAG, each step is a task named after the step, running the action's template named `<stage>-<step>`. Tradeoff to evaluate: the extra nesting level in the Argo UI graph.

**Hooks.** Prefer task-level hooks over template-level hooks (template-level fires everywhere the template is used), and prefer hooks over sibling tasks with `depends: X.Failed`. Such a sibling marks the failure as handled, so the stage and the workflow succeed despite the failed step (verified on Argo 4.1.6). Use the `exit` hook rather than expression hooks: an expression hook on a sub-DAG (stage) task never fires when another task depends on it, while the `exit` hook always fires and can pass `{{tasks.<name>.status}}` to its template. A step's handlers therefore run from one `exit` hook that calls a handler DAG branching on status, the same shape as the pipeline's `onExit`. A step skipped by `when` runs no handlers.

**Commit statuses.** Requirements: the commit is the pipeline source's resolved SHA (slice 14, section 16), known before any report runs, statuses are best-effort and must not fail the build, context names are validated as unique per pipeline, and skipped or cancelled nodes must never leave a required check stuck pending.

The API call is made by the pipeline's `status_reporter`, an ordinary action owned by the consumer or an action library (stagecraft stays host-agnostic). It reads `{{ status.context }}`, `{{ status.state }}`, and `{{ status.description }}`, and gets the commit from `{{ source.sha }}` (and the repository from `{{ source.url }}`). It is compiled once and called through a generated `status-report` DAG that runs it with `continueOn`, so a report can never fail the build.

For each step with `status=`: a "pending" report task shares the step's `depends` and `when`, and the step depends on it, so pending always lands before the final state. (A `running` hook was tried: it can miss short steps on Argo 4.1.6, and a late pending would overwrite the final state.) The step's `exit` hook reports success, failure, or error. A step skipped by `when` gets a "success" report with the description "Skipped" from a sibling task with the negated condition, so a required check for a conditional stage still passes. A step that never starts because an earlier stage failed is never reported, so nothing is left pending. The pipeline's own context reports pending once the source step has resolved the commit, and its final state from `onExit`, guarded by a `when` that the commit exists (if the source step failed, there is nothing to report on).

Accepted gaps: `argo terminate` runs no exit handlers, and `argo stop` skips the exit hook of a step that was running. Either can leave a context pending; re-running the workflow updates it.

**Triggers.** `Trigger.cron(schedule, name=, params=, timezone=)` compiles to a `CronWorkflow` named `<pipeline>-<name>` (at most 52 characters, Argo's limit) whose `workflowSpec` is a `workflowTemplateRef` to the pipeline's template plus the trigger's params. It is written into the pipeline's file after the template. One schedule per trigger; schedules are literal and checked against the CRD's pattern. Trigger params must be declared, must set every required param, and must be in the param's enum unless they reference a Helm value (`{{ values.a.b }}` is allowed in trigger params; Argo checks the enum at submission). Concurrency policy and history limits keep Argo's defaults until a pipeline needs otherwise.

Accepted gap (decided 2026-10-24; Kubernetes resources are eventually consistent anyway): Helm applies a release's `CronWorkflow`s before its `WorkflowTemplate`s. Helm 4.1.0 sorts kinds it doesn't know by name and creates resources in batches by kind, waiting for each batch (`lessByKind`, `batchPerform`); watching an install on kind showed the cron created 6.5 ms before its template. Argo 4.1.6 validates a new `CronWorkflow` against its template on arrival. If the template isn't there yet, the cron is marked invalid and not scheduled until the controller processes it again: about 3 minutes on kind, and at most its 20-minute informer resync. So the first ticks after an install that creates a cron can be missed. Inlining the workflow spec in each cron would avoid this, at the cost of bigger manifests and scheduled runs no longer linked to the template in the UI.

**No shared workspace.** Removed in slice 11 (section 16): each step runs in its own pod with private scratch, and files will cross step boundaries only as artifacts. Slice 7 had built a `containerset` strategy (one ReadWriteOnce claim per run; a stage's parallel readers as containers of one `containerSet` pod). Verified on Argo 4.1.6 while it existed, in case shared volumes return: a `containerSet` pod gets a claim's volume only for mounts on `containerSet.volumeMounts`, not on single containers (pod creation fails, and the workflow ends in Error); a failing container doesn't stop its siblings, and the pod fails after they finish; `volumeClaimGC: OnWorkflowCompletion` deletes the claim after failed runs too.

## 8. Configuration and the values contract

**Principle.** There is no stagecraft-side environment, profile, or cluster concept. Anything that varies per Helm release installation is one of two things:

1. **A Helm value**, referenced by path. Used for structural values (node selectors, tolerations, resource defaults, service account) and for names of runtime objects.
2. **Runtime late binding**: ConfigMap or Secret key refs, `artifactRepositoryRef`, and `workflowDefaults`, resolved by the cluster when the workflow runs. The ConfigMap or Secret *name* is typically a Helm value.

**The generated contract** (slice 13; section 16, "The generated chart"). Every value reference, from a `{{ values.a.b }}` placeholder or `ctx.value(path, type)` in an action's `compile`, is recorded with its type, and the generated chart's `values.schema.json` requires each one. Values are supplied per installation at deploy time, and Helm validates them against the schema at `helm template` and install. Until slice 13 the chart owned a hand-written schema that Stagecraft resolved references against; that resolver is gone.

**Validation at render time.** Rendering fails, with the offending pipeline, stage, step, and path in the error, when a reference:
- is not a dotted path of identifiers (keys must work as `.Values.a.b`),
- is under the reserved `stagecraft` key (framework values, section 16),
- uses a path already used with another type, or as both a value and an object (`a` and `a.b`),
- expects something other than a scalar (`str`, `int`, `float`, `bool`).

`{{ values.a.b }}` placeholders are always strings. Nothing is rendered unless every pipeline compiles.

Decided: every referenced value is **required** and non-null, with a single scalar type; there are no guards, defaults, or optional values. Structured values (objects, arrays) are deferred until an action needs one (decided 2026-10-24): Hera validates object and list fields, so a reference could only get there through placeholder workarounds and patching the output. Scalar references inside an action-built object already work, for example `node_selector={"pool": ctx.value("build.pool", str)}`.

**`CompileContext`.** The compiler passes one explicit object into every action's `compile` call. It mints value references and records each with its type (`ctx.references`). It exists to:
1. Keep the compile layer pure (no env reads or globals).
2. Let actions request a value by path and expected type, receiving a typed handle that serializes to the right Helm expression, so actions never emit Helm syntax.
3. Collect the references the values schema is generated from, so action tests can assert exactly which values an action uses.

**Helm-compatible output.**
- Output is template files only: `WorkflowTemplate` and `CronWorkflow` manifests with Helm expressions where values are Helm-bound.
- Argo's `{{ }}` collides with Helm's. The serializer escapes every Argo expression in one pass and emits real Helm expressions only for value references, which it splices in after escaping. Every value lands in the manifest as a string. A field that is exactly one string value renders as `{{ .Values.a.b | quote }}`; a string inside other text forces a double-quoted YAML scalar and is spliced in as `quote` with its surrounding quotes trimmed, so values containing YAML syntax cannot change the document. Numbers and booleans render through `toJson` (`{{ .Values.a.b | toJson | quote }}` as a whole field, bare `toJson` inside text), because `quote` prints large numbers in exponent notation (`1000000` as `"1e+06"`, seen with Helm 4.1). A multi-line string containing a value is therefore a double-quoted one-liner rather than a literal block: less readable in diffs, but safe for any value (decided 2026-10-24). Helm `if` blocks for optional values are not needed while references are required-only.
- The serializer fails rather than emit something Helm would misread: an unbalanced `{{` or `}}` in author text, a value reference in a mapping key, or author text containing its internal placeholder tokens. A round-trip test runs hostile strings (Argo expressions with backticks, author-typed Helm and Go template text, YAML syntax and implicit types, multi-line and non-ASCII text) through `helm template` and requires the exact compiled manifest back.

## 9. Testing strategy

1. **Unit**: plain Python functions, escaping, chart and schema generation.
2. **Action contract**: a compiled action exposes exactly its declared inputs, outputs, and artifacts, and references only the value paths and types it declares. (Inputs and outputs come with artifacts, slice 15. Actions request values inside `compile` rather than declaring them up front; tests assert `ctx.references`.)
3. **Value references**: assert that conflicting types, invalid paths, and the reserved key each fail rendering with a clear error, that references are recorded with their types, and how each renders.
4. **Composition**: each step's required inputs are produced by an earlier stage, and status contexts are unique. (Status contexts are checked; inputs come with section 16.)
5. **Compile and snapshot**: assert structure (dependencies, hooks, `onExit`, resources) and snapshot the rendered templates so every change appears in review.
6. **Static validation**: render the generated chart for the fixture pipelines and run `helm template` with a test-only values file (`tests/fixtures/values.yaml`, never published), then `argo lint` and kubeconform on the result. Include escaping tests for Argo expressions in scripts, `when`, and `depends`.
7. **Behavior** (kind or k3d with Argo installed, small and slow): a `run_step` fixture wraps a step in a one-step workflow, installs it as a generated chart with the test values, submits it, and asserts on phase and outputs. Separate tests deliberately fail and pass steps to prove hooks, `onExit`, and status sequences fire correctly, including skipped and cancelled cases.

The pipelines project has its own CI: `stagecraft render` (and a check that the committed chart is up to date), then `helm template` per environment's values, `argo lint`, and kubeconform.

## 10. Repo layout (stagecraft)

```
stagecraft/
  CLAUDE.md
  DESIGN.md
  pyproject.toml
  src/stagecraft/
    spec.py          # Pipeline, Stage, Step, Param, Trigger, Action base
    context.py       # CompileContext, value references
    chart.py         # the generated chart: Chart.yaml, values schema, templates
    loader.py        # imports pipeline files, collects Pipeline objects
    compile/         # stages->DAG, hooks, onExit, triggers, placeholder lowering
    serialize/       # Helm-compatible serializer, escaping
    graph.py
    cli.py
  tests/{unit,contract,snapshots,integration}/
  tests/fixtures/    # minimal actions, fixture pipelines, test-only values, CRD schemas
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

Slices 1 to 10 are recorded as built; their "blocks" became actions placed by steps in slice 12.

Planned after the concept model (section 16, agreed 2026-10-25):

11. Remove the shared workspace: `Workspace`, `containerset`, the per-run claim, and workspace access flags.
12. Actions and steps: composition replaces `Block` inheritance; Stagecraft's outcome vocabulary for handlers.
13. Generate the pipelines chart: `Chart.yaml`, a `values.schema.json` built from value references, and `templates/`. Removes `--values-schema`, `--values-prefix`, and the test fixture chart.
14. Main source: the source step (revision resolved to a SHA, checkout published as an artifact), injection into every step, `{{ source.* }}`, checks on the resolved SHA, framework values under `stagecraft.sources`. Verify on kind first.
15. The setup step and output parameters: `Pipeline(setup=...)` taking a `Step` or a `TemplateRef` to an existing WorkflowTemplate, declared outputs re-published as global outputs, `{{ setup.<output> }}` everywhere (source fields, steps, `when`, handlers, the reporter). Verify on kind first (added 2026-10-25: Argo Events submits CI runs with the whole GitHub webhook payload, which a parsing step must turn into the source's URL and commit).
16. Artifacts and outputs between steps: declared action inputs and outputs, `"<stage>.<step>.<artifact>"` references and `{{ steps.<stage>.<step>.<output> }}` placeholders, both through global outputs, and artifact GC. Verify against kind's MinIO first.
17. Stage sources.
18. The condition API.

Section 15 lists what was deliberately left out. Do not generalize before two or three real pipelines exist.

## 12. `CLAUDE.md` contents

`CLAUDE.md` holds the pinned versions, the Argo behavior verified on kind, the commands, and the working rules (pure compile layer, values only through `ctx` or placeholders, no Helm syntax outside the serializer, authors never write `depends`/`onExit`/`hooks`, failing test first). Keep it in step with this spec when a slice changes a rule.

## 13. Verify against the pinned Argo and Hera versions

- Hook semantics: field names, expression scope, workflow-level `running` hooks, effect of a failing hook on workflow status, behavior on skipped nodes.
- Template-level versus task-level hook firing.
- Argo CD sync order for CRDs versus chart contents, if Argo CD deploys the chart.
- For section 16, on kind with its MinIO: a DAG template's outputs re-published with `globalName` (for the setup step); global output artifacts consumed by steps in later stages; an input artifact placed inside another (an artifact under the source checkout); `artifactGC` deleting objects when a workflow is deleted.

## 14. Open decisions

- Commit the generated chart (section 16) to a repository, or publish it as a packaged chart.
- Template naming across multiple releases in one namespace versus stable names in the Argo UI.
- Whether a reference action library belongs in a third package.
- Workflow TTL and the artifact GC service account (section 16): from the controller's `workflowDefaults`, or emitted per pipeline.
- Whether to offer a resolved preview mode (render with a values file) for local debugging, or rely on `helm template`.

## 15. Deferred features

Left out on purpose, as of slice 10 (2026-10-24; updated for section 16 on 2026-10-25). Typed inputs and outputs, resolving a branch to its commit, and the workspace strategies left this list when section 16 replaced them. Each waits for a real pipeline that needs it ("do not generalize before two or three real pipelines exist"). The pipeline file or render error that would need it is the signal to pick one up.

| Feature | Why deferred | Revisit when |
|---|---|---|
| Escape hatch: a raw Hera template wrapped in a minimal contract (section 6) | Every fixture need was met by ordinary actions | An action can't be expressed as one `Container` or `Script` |
| Structured values: objects and arrays from Helm values (section 8) | Hera validates object and list fields, so a reference could only get there through placeholder workarounds and patching the output. Scalar references inside an action-built object already work | The chart must own the whole shape of a field, such as `tolerations`, `affinity`, or `resources` |
| Values in non-string fields: integer or boolean fields such as `suspend` or a pure-int Kubernetes field | Value references are strings, and always render as strings | An action or trigger needs a Helm value in such a field (see per-installation trigger switch) |
| Per-installation trigger switch (`suspend` from Helm values) | A boolean field, so it waits for values in non-string fields | An environment must not run a pipeline's schedule |
| Trigger options: `concurrencyPolicy`, history limits, `startingDeadlineSeconds`, several schedules in one trigger | Argo's defaults are fine; several schedules are several triggers | A schedule overlaps its own runs, or history needs tuning |
| Optional action inputs | Every declared input must be bound | An action is useful both with and without an earlier step's files |
| Artifacts for reactions and the reporter (for example, uploading a failed step's test reports) | A failed step's outputs are uploaded when its main container ran (verified in slice 14); how exit hooks would receive them is unverified | A reaction must read a step's files; prove it on kind first |
| Opting a step out of source injection | Every step downloads the sources; cheap from an in-cluster repository | Source download time matters for steps that don't need them |
| SSH keys and other credential kinds for sources | HTTPS with a username and token covers GitHub | A source host needs SSH, or GitHub App tokens must be minted per run |
| Deeper source checkouts (history for `git describe`, changed-file detection) | The checkout is one commit deep, which is all builds and tests need | A step needs history or tags |
| Caches that outlive a run (for example a Go module cache) | A different concept: shared across runs, not handed between steps | Build times need them |
| Multi-line scripts containing a string value as readable literal blocks (section 8) | Kept as double-quoted one-liners: correct for any value, but harder to read in diffs | Rendered templates are reviewed often enough for this to hurt |

Accepted gaps (Argo or Helm behavior, not missing features) are kept with the mechanisms they affect in section 7: `argo terminate` and `argo stop` leaving statuses pending, stage and step handler failures not failing the pipeline, and new CronWorkflows missing their first ticks. Still unverified: an upgrade that changes a trigger's params together with its template's params (Helm updates the cron first, while the old template is still in place).


## 16. Concept model and contracts

Agreed 2026-10-25. This is the model slices 11 to 18 build toward (section 11); slices 11 to 14 are built. Where it conflicts with sections 5 to 8, those sections describe the current code and this section wins for new work. Points marked **open** are settled in the slice that builds them.

### Personas

Two kinds of user, and every concept belongs to one of them:

| Persona | Writes | Should know | Should never need |
|---|---|---|---|
| **Pipeline author** (service team) | Pipelines: configuration | Stagecraft's CI vocabulary | Hera, Argo, Helm |
| **Action author** (platform team) | Reusable units of work: code | Containers, Hera's `Container` and `Script`, Helm values by path and type | DAGs, hooks, Helm syntax, workflow-level Argo |

Until slice 12, one `Block` class served both personas: the action author defined it, and the pipeline author filled in fields it inherited (`when`, `status`, handlers). The split replaces that inheritance with composition: an **action** (what runs) placed by a **step** (where and under which conditions it runs).

### Concepts

| Concept | CI meaning | Defined by | What the framework guarantees | Argo it hides |
|---|---|---|---|---|
| **Pipeline** | A named CI process, installed once per chart | Pipeline author | One installable unit; reactions and checks for the whole run | `WorkflowTemplate`, entrypoint, `onExit` |
| **Run input** (param) | What a run is about | Pipeline author | Declared, validated, shown in the submit form | Workflow parameters |
| **Setup** | Work every run needs before anything else, such as parsing a webhook payload | Pipeline author, using an action or an existing WorkflowTemplate | Runs first; its declared outputs are readable everywhere, the source included | Global outputs, `templateRef` |
| **Source** | A repository at a commit | Pipeline author (pipeline or stage) | Resolved to one commit SHA, fetched once, placed at a framework-chosen path | Git, artifacts, global outputs |
| **Stage** | An ordered phase, and a barrier | Pipeline author | Its steps run concurrently; it fails if any step fails; later stages run after success or skip | Sub-DAG, `depends` |
| **Step** | A named placement of an action in a stage | Pipeline author | Has an outcome; can be conditional, checked, reacted to; inputs wired | Tasks, `when`, hooks, artifact arguments |
| **Action** | Reusable, configurable work that runs as one container | Action author | Configuration validated; value references typed and collected into the chart's schema; run inputs substituted | Template naming and wiring |
| **Artifact** | Files one step hands to steps in later stages | Action author declares; pipeline author wires | Exists before any consumer runs; deleted with the run | Output and input artifacts, stage DAG plumbing, artifact GC |
| **Outcome** | succeeded, failed, or skipped | Framework | One vocabulary for reactions and conditions; checks map it to host-style states | Argo phases (`Succeeded`, `Error`, `Omitted`, ...) |
| **Condition** | Whether a step or stage runs | Pipeline author | Typed, checked at render time | `when` expressions and their quoting |
| **Reaction** (handler) | Work triggered by an outcome | Pipeline author, using actions | When it fires, what context it gets, failure semantics (section 6) | Exit hooks, `onExit` |
| **Check** (commit status) | An outcome shown on the commit | Pipeline author names it; a **reporter** action delivers it | Pending before final; skipped counts as success; best-effort | Report tasks, the `status-report` DAG |
| **Schedule** (trigger) | Runs without a human | Pipeline author | Valid schedule, complete inputs | `CronWorkflow` |
| **Installation config** | Per-environment settings | Supplied at deploy time, against the generated schema | Every referenced value required and typed; Helm rejects bad values before anything is installed | Helm syntax, escaping, the schema |

There is no shared workspace concept any more (see "Files between steps").

### The action contract

The only place an action author plugs in code.

- **Configuration:** the action's own validated fields. Run inputs and Helm values arrive as placeholders in those fields; installation values also through `ctx.value(...)` in `compile`.
- **Interface** (slices 15 and 16): its output parameters, each a name and the file its container writes the value to (`outputs = {"digest": "/tmp/digest"}`); and the artifacts it reads and writes, each a name and a path: `outputs = {"bin": "dist/app"}`, `inputs = {"bin": "dist/app"}`. A relative path is inside the main source checkout. Every declared input is required.
- **Output:** exactly one Hera `Container` or `Script`. Template-level settings (retries, timeouts, service account) are allowed again, since no step shares a pod.
- **Outcome:** exit code 0 means succeeded; anything else means failed.
- **Rules:** pure (no I/O, env reads, or globals), no Helm syntax, no workflow-level Argo. Actions don't declare sources; sources are injected (below).

The **reporter** is an action that reads `{{ status.context }}`, `{{ status.state }}`, `{{ status.description }}`, `{{ source.url }}`, and `{{ source.sha }}`. Checks require a pipeline source, which replaced the `repo`/`sha` param convention in slice 14.

### The step contract

A step binds an action into a stage: its name (unique in the stage), its condition, its check context, its reactions, and the source of every input:

```python
Stage("package", [
    Step("image", DockerBuild(), inputs={"bin": "build.compile.bin"}, status="ci/image"),
])
```

Input references are `"<stage>.<step>.<artifact>"`. The render fails when a reference names a stage, step, or artifact that doesn't exist; names a step in the same or a later stage; names a step or stage that has a condition (it may be skipped, leaving no artifact); binds an input the action doesn't declare; or leaves a declared input unbound.

Decided in slice 12 (2026-10-25): `Step(name, action, when=, status=, on_success=, on_failure=, finally_=)`. A bare action in a stage or handler list is shorthand for a step named after its class in kebab case, so two actions of one class in a stage need a `Step` each. Handler steps can set only a name. The status reporter is a bare action. Built in slice 12 without `inputs`, which come with artifacts (slice 15).

### The setup step and outputs

Decided 2026-10-25, slice 15. Argo Events submits CI runs with the whole GitHub webhook payload as a param, and a parsing step turns it into values the run needs (the repository URL, the commit, the event, the PR number) as output parameters. So:

- **`Pipeline(setup=...)`** is optional and runs first, before the source step. It takes either a `Step` (an action, like any other, declaring its outputs) or a `TemplateRef(name, template=, arguments={...}, outputs=[...])` calling an existing WorkflowTemplate as is. A template's outputs can't be inspected at render time, so a `TemplateRef` lists the output parameter names it publishes; arguments may use `{{ param.x }}` and `{{ values.a.b }}`.
- **`{{ setup.<output> }}`** works in the source's fields, every step, `when` (quoted, like params), handlers, and the status reporter. A reference to an undeclared output fails the render.
- **Lowering.** The setup runs inside a small generated DAG whose outputs re-publish each declared output parameter with a `globalName` (`stagecraft-setup-<output>`), so placeholders lower to `{{workflow.outputs.parameters.stagecraft-setup-<output>}}` and need no routing. A DAG template's re-published global outputs are not verified yet: prove it on kind first.
- **Ordinary steps' outputs** use the same mechanism in slice 16: an action declares output parameters, and later steps, conditions, and handlers use `{{ steps.<stage>.<step>.<output> }}`, checked to come from an earlier stage.
- If the setup fails, the run fails before the source step; `{{ setup.* }}` then has no value, as with a failed source step.

### Sources and revisions

- **Declaring them.** A pipeline has at most one main source: `Pipeline(source=Source(url=..., revision=...))` (built in slice 14; `url` is what `git fetch` takes). A stage can request more: `Stage(..., sources=[Source(name="fixtures", url=..., revision=...)])` (slice 17). Arbitrary extra sources exist only at stage level. `url` and `revision` may use `{{ param.x }}` and `{{ values.a.b }}`. `source` is a reserved stage name.
- **Revisions.** A revision is a commit SHA or any other reference. A reference that isn't a SHA is resolved to its head commit once, and that SHA is used from then on. The main source resolves before the first stage. A stage source resolves when its stage starts, so all of that stage's steps see one commit. `argo retry` keeps succeeded nodes, so a retried run keeps its SHAs.
- **Fetching.** A framework-generated source step runs `git fetch --depth 1 <url> <revision>`, checks out `FETCH_HEAD`, and publishes the commit as a global output parameter and the checkout (shallow, with `.git`) as a global output artifact. Decided in slice 14 after verifying on kind: Argo's git input artifact can only reach a commit that isn't a branch tip with a full clone, which would put the whole history in the artifact every step downloads; git's own shallow fetch reaches any commit. The main source step is a task named `source` before the first stage; the first stage and the pipeline's pending report depend on it. A stage source step will be a task `source-<name>` at the start of its stage's sub-DAG, which its steps depend on. Global outputs are readable everywhere, sub-DAG tasks, exit hooks, and `onExit` included (verified), so steps take the checkout from `{{workflow.outputs.artifacts.stagecraft-source}}` with no routing through stage DAGs.
- **Injection.** Every step gets the main source, and every step in a stage gets that stage's sources, through an input artifact the framework adds. Sources are placed at `/sources/<name>` (the main source is named `main`) and exposed as `STAGECRAFT_SOURCE_<NAME>` (uppercased, `-` as `_`). Paths are unique by construction: source names are unique within a stage and `main` is reserved. The main source's path is the working directory unless the action sets one.
- **Reactions and the reporter get no sources.** They run from exit hooks and `onExit`, which also run when a source step failed. They read `{{ source.url }}`, `{{ source.revision }}` (as given), and `{{ source.sha }}` instead, as every action can.
- **Accepted gap:** if the main source step fails (bad reference, credentials), there is no SHA, so the pipeline's check can't be reported. The failure shows in the Argo UI. The global parameter is then empty (or stays a literal `{{workflow.outputs.parameters...}}` if the step never started) rather than erroring (verified), so the pipeline's final report is guarded by `when: '<sha>' =~ '^[0-9a-f]{40}$'` and is skipped.
- **Settings:** framework values in the generated chart, required as soon as a pipeline declares a source: `stagecraft.sources.image` (an image with `git` and `sh`) and `stagecraft.sources.secret` (a Secret with `username` and `password` keys for HTTPS). The Secret keys are optional at run time, so public repositories need no Secret. Credentials go in an HTTP header for the fetch only, never into the checkout's `.git/config`. SSH keys are deferred (section 15).

### Files between steps

**There is no shared filesystem**, not even between sequential stages (decided 2026-10-25). The cluster's storage is ReadWriteOnce (EBS) on multi-zone clusters, so a shared per-run volume pins a run to one zone and serializes its parallel steps onto one node or pod.

- Each step has private scratch: its own container filesystem, including its copy of the sources.
- Anything that crosses a step boundary is an artifact (sources included), stored in the controller's default artifact repository. Ours is S3-compatible (RustFS). Stagecraft emits no repository configuration.
- Cross-stage references go through global outputs (revised 2026-10-25, after slice 14 verified that a step's global outputs are readable from sub-DAG tasks, exit hooks, and `onExit`): the producing step's output artifact gets a generated `globalName` (`stagecraft-<stage>-<step>-<artifact>`), and the consuming step receives it from `{{workflow.outputs.artifacts.<globalName>}}`. No routing through stage DAG outputs and inputs. Authors only write `"<stage>.<step>.<artifact>"`; the render checks the producer is in an earlier stage.
- **Retention:** Stagecraft sets `artifactGC.strategy: OnWorkflowDeletion`. Deletion therefore needs workflows to be deleted, and the GC pods need a service account allowed to delete from the bucket. **Open:** whether both come from the controller's `workflowDefaults` (recommended: platform settings, like the artifact repository) or from the pipeline.
- Removed: `Workspace`, the `containerset` strategy, the per-run claim, `workspace="read"/"write"`, the writer rule, and the reserved `workspace` name.
- Caches that outlive a run are a separate concept, out of scope for now (section 15).

### Outcomes

Stagecraft's outcomes are `succeeded`, `failed`, and `skipped`, mapped from Argo phases (`Succeeded`; `Failed` and `Error`; `Skipped` and `Omitted`). Handlers see them as `{{ step.outcome }}` (slice 12; skipped steps run no handlers), and conditions will use them. Checks keep host-style states (decided 2026-10-25): the reporter still gets `pending`, `success`, `failure`, or `error` and the descriptions `Running`, `Succeeded`, `Failed`, `Error`, `Skipped`, which map 1:1 to GitHub and keep reporters a plain API call.

### Conditions

A typed condition API replaces raw `when` strings in pipeline files, for example `when=param("deploy") == "true"`. It removes the quoting trap (section 5) and is checked at render time. It covers only comparisons over Stagecraft concepts that map cleanly onto Argo `when` expressions. Params come first; other operands (such as a source's revision or an earlier stage's outcome) are added when a pipeline needs them.

### Escape valves

Kept on purpose, with their use cases unknown yet:
- **Hera inside actions.** Action authors build Hera `Container`s and `Script`s directly.
- **Native Argo expressions in pipeline strings.** Passed through unchecked.

### The generated chart

Decided 2026-10-25, built in slice 13. A pipelines project renders into one self-contained Helm chart, installed as its own release:

```
<out>/
  Chart.yaml           generated; carries a Stagecraft marker annotation
  values.schema.json   generated from the pipelines' value references
  templates/           one file per pipeline (WorkflowTemplate, then its CronWorkflows)
```

- **Ownership.** Stagecraft owns exactly these three entries and replaces them on every render. It never creates or touches `values.yaml` or any other file in the directory, and writes only into a missing or empty directory or one it generated (a `Chart.yaml` with its marker), so a mistyped `--out` can't overwrite anything else.
- **Values come at deploy time**, from a file such as `values-pipelines.yaml` passed to the release. The chart has no defaults: every referenced value is required, and Helm rejects a missing or wrongly typed value at `helm template` or install, naming the path.
- **The schema is generated.** Each value reference, from `ctx.value(path, type)` or a `{{ values.a.b }}` placeholder (always a string), becomes a required, typed property; `global` is declared so the chart also works as a subchart. Two references to the same path with different types fail the render. Objects are not strict (decided in slice 13): `required` already catches a missing or misspelled value, and `additionalProperties: false` would only make unused values fatal, so removing a pipeline would break every installation until its values file changed in lockstep. The cost, accepted: a mistyped path becomes a new required value instead of a render error, so it surfaces at `helm template` rather than `stagecraft render`, and the generated schema has no descriptions, enums, or patterns.
- **Framework values** live under the reserved `stagecraft` key (for example `stagecraft.sources`). A reference into `stagecraft.` from an action or pipeline fails the render.
- **Not generated:** what pipelines depend on by name: the workflow service account and RBAC, the Secrets (GitHub token, git credentials), the artifact repository, and controller defaults. Whatever deploys them today keeps doing so; the chart refers to them through values.
- **As a subchart.** Not needed, but possible: vendored into another chart's `charts/` directory, listed in its `Chart.yaml` dependencies (Helm 4.1 `helm lint` requires the listing), with its values under the chart's name. Helm validates a subchart's schema against its scoped values (verified with Helm 4.1.0).
- **CLI.** `render --out <dir> [--chart-name <name>]` writes the chart (name `pipelines` by default; version fixed at `0.0.0`, since the chart is committed rather than published); `check` compiles without writing. `--values-schema` and `--values-prefix` are removed.
