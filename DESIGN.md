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
- The Helm chart owns the values contract. Stagecraft complies with it and refuses to render anything that violates it.
- Output is plain manifests (Helm-compatible templates), committed and reviewable.

Non-goals:
- Hera is a compiler here, not a runtime. Pipelines are never executed from Python.
- Stagecraft does not generate or own the Helm chart (no `Chart.yaml`, `values.yaml`, `values.schema.json`, helpers, or packaging) and does not define the values contract.
- Stagecraft does not define a catalog of concrete CI blocks (clone, build, deploy, and so on). Block authors, or a separate library, own those. The framework only defines the block base type and the Argo features it maps to.
- No replacement for Argo Events or Argo CD, and no custom UI.
- Not every Argo feature is modeled. An escape hatch to raw Hera exists.

## 3. Repository boundaries

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
- `stagecraft check`: composition and contract validation without writing files.
- `stagecraft graph`: Mermaid diagram of a pipeline.
- `stagecraft contexts`: list commit-status contexts (for branch protection setup).

Linting rendered output uses standard tools (`argo lint`, kubeconform), not reimplemented here.

## 5. Authoring model

Target shape of a pipeline file. This is the primary acceptance example, and the blocks shown are illustrative stand-ins for whatever blocks a consumer defines:

```python
pipeline = Pipeline(
    name="service-ci",
    status="ci/pipeline",
    workspace=Workspace(strategy="containerset", size="5Gi"),
    params=[
        Param("repo", required=True),
        Param("sha", required=True),
        Param("push", default="false", enum=["true", "false"]),
        Param("deploy", default="false", enum=["true", "false"]),
    ],
    stages=[
        Stage("prepare", [Clone(repo="{{ param.repo }}", ref="{{ param.sha }}",
                                workspace="write")]),
        Stage("verify", [
            Lint(workspace="read"),
            UnitTests(
                workspace="read",
                status="ci/unit-tests",
                on_failure=[Notify(channel="{{ values.notify.alertChannel }}")],
                finally_=[UploadReports()],
            ),
            TypeCheck(workspace="read"),
        ], status="ci/verify"),
        Stage("package", [BuildImage(push="{{ param.push }}", workspace="read")],
              status="ci/package"),
        Stage("deploy", [DeployStaging()],
              when="{{ param.deploy }} == 'true'", status="ci/deploy"),
    ],
    on_success=[Notify(channel="{{ values.notify.channel }}")],
    on_failure=[Notify(channel="{{ values.notify.alertChannel }}", include_failures=True)],
    triggers=[Trigger.cron("0 2 * * *", params={"push": "true"})],
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
- `workspace=Workspace(strategy=..., size=...)` declares the workspace strategy at compile time. It is a pipeline property, not an installation property, and uses no Helm values (section 7).

## 6. The block base type and its Argo mapping

A block is a unit with a typed contract. The framework's job is to map each contract feature to Argo. Concrete block behavior is out of scope.

| Block feature | Argo mechanism |
|---|---|
| Typed inputs / outputs | Template `inputs` / `outputs` parameters and artifacts |
| `workspace`: `none`, `read`, `write` (+ `paths`) | Volume mounts, `containerSet` grouping, or artifact transfer (section 7) |
| Declared value references (path and expected type) | Helm expressions, validated against the chart's values contract |
| Secret references | `secretKeyRef` env or volume; secret names come from Helm values, never secret contents |
| Resources, retry, timeout, service account, node selection | Template fields, whose values come from Helm values |
| `when` (stage or block) | `when` on the task |
| `on_failure`, `on_success`, `finally_` | One task-level `exit` hook calling a generated handler DAG, whose tasks are guarded by `when` on the step's status |
| `status="ctx/name"` | `running` and `exit` hooks that report pending, then success or failure |
| Pipeline `on_success` / `on_failure` | Generated `onExit` template branching on `{{workflow.status}}` |
| Triggers | `CronWorkflow` |
| Params | Workflow `arguments.parameters` |
| Escape hatch (raw Hera template wrapped in a minimal contract) | Passed through unchanged |

Handler blocks (`on_success`, `on_failure`, `finally_`) are ordinary blocks with the same contract. They receive context through `{{ step.name }}` and `{{ step.status }}`. In stage and block handlers these are fixed at compile time per outcome (`Succeeded` or `Failed`, with Argo's Error reported as Failed); in pipeline handlers they are the workflow's name and status. Argo exposes no exit code or failure message to task hooks. Handler blocks cannot have their own `when` or handlers.

Handler failures: a failing pipeline handler fails the pipeline (Argo's `onExit` default, kept on purpose because handler failures are unexpected). A failing stage or block handler shows as failed in the Argo UI but does not fail the pipeline, because Argo task hooks cannot fail a workflow; this is accepted.

## 7. Lowering rules

**Stages to DAG.** Each stage compiles to its own sub-DAG template, invoked as one task in the top-level DAG. A stage then has a single status, which makes stage-level `when`, hooks, and `status` straightforward. Inter-stage `depends` is generated, one edge per stage. Tradeoff to evaluate: the extra nesting level in the Argo UI graph.

**Hooks.** Prefer task-level hooks over template-level hooks (template-level fires everywhere the template is used), and prefer hooks over sibling tasks with `depends: X.Failed`. Such a sibling marks the failure as handled, so the stage and the workflow succeed despite the failed step (verified on Argo 4.1.6). Use the `exit` hook rather than expression hooks: an expression hook on a sub-DAG (stage) task never fires when another task depends on it, while the `exit` hook always fires and can pass `{{tasks.<name>.status}}` to its template. A step's handlers therefore run from one `exit` hook that calls a handler DAG branching on status, the same shape as the pipeline's `onExit`. A step skipped by `when` runs no handlers.

**Commit statuses.** `status=` expands into two hooks (`running` posts pending, `exit` maps node status to success, failure, or error). Requirements: `sha` and `repo` are params available before any clone, statuses are best-effort and must not fail the build, context names are validated as unique per pipeline, and skipped or cancelled nodes must never leave a required check stuck pending. How skipped nodes are represented is an open decision.

**Workspace strategies.** Constraint: the current workspace is an EBS-backed PVC (ReadWriteOnce), which limits true cross-node parallelism. Blocks declare their workspace access, and the strategy is a compile-time choice made in the pipeline definition (`workspace=Workspace(strategy=..., size=...)`), either stated explicitly or inferred from block declarations with an explicit override. The strategy consumes no Helm values and nothing from the `CompileContext`: each rendered manifest has exactly one shape, visible in the pipeline file and the diff.

1. `containerset`: a stage of read-only blocks becomes one `containerSet` pod with a shared `emptyDir` (the Jenkins pod-per-build model). Default for parallel read-only stages.
2. `node-affinity`: pod affinity on the workflow label so parallel pods share the RWO volume on one node.
3. `artifact`: prepare once, upload to object storage, each step works in its own `emptyDir` and uploads only declared outputs.

`write` blocks declare `paths`, and the compiler rejects overlapping parallel writers.

Strategy options are limited to what the pipeline itself needs (for example workspace `size`). Platform details are never passed to a strategy as values. They resolve on the cluster at runtime:
- Storage class: omitted, so the cluster's default StorageClass applies.
- Artifact repository: the controller's default repository, or an `artifactRepositoryRef` set through `workflowDefaults`.
- Affinity topology key: fixed (`kubernetes.io/hostname`).

A strategy that cannot work without an installation-specific value is not supported until a mechanism for it is agreed. Changing the strategy for a pipeline means editing the pipeline file and re-rendering, which is intended, since it is a structural change that should appear in review.

## 8. Configuration and the values contract

**Principle.** There is no stagecraft-side environment, profile, or cluster concept. Anything that varies per Helm release installation is one of two things:

1. **A Helm value**, referenced by path. Used for structural values (node selectors, tolerations, storage class, resource defaults, service account) and for names of runtime objects.
2. **Runtime late binding**: ConfigMap or Secret key refs, `artifactRepositoryRef`, and `workflowDefaults`, resolved by the cluster when the workflow runs. The ConfigMap or Secret *name* is typically a Helm value.

**Exception by design: workspace strategy.** The workspace strategy is a compile-time pipeline decision and does not read Helm values. Platform details it needs resolve on the cluster through defaults (section 7).

**The chart owns the contract.** The values contract is the chart's `values.schema.json` (Helm's native JSON Schema mechanism). Stagecraft is a consumer of that contract.

**Validation at render time.** Every value reference, whether it comes from a `{{ values.a.b }}` placeholder in a pipeline file or from a block's declared references, is checked against the schema. Rendering fails, with the offending pipeline, block, and path in the error, when:
- the path does not exist in the schema (undeclared keys are treated as nonexistent; the contract should be strict about `additionalProperties`),
- the schema type is incompatible with how the value is used (for example an object interpolated into a string field, or a string used where a list is expected),
- a required block value is optional in the schema without a handling rule (guard or default) available.

Nothing is rendered for a pipeline that fails validation.

**`CompileContext`.** The compiler passes one explicit object into every block's `compile` call. It carries the parsed values contract and mints validated value references. It exists to:
1. Keep the compile layer pure (no env reads or globals).
2. Let blocks request a value by path and expected type, receiving a typed handle that serializes to the right Helm expression, so blocks never emit Helm syntax.
3. Let blocks declare what they need, so contract violations fail at compile time and block tests supply only a small fake schema.

**Helm-compatible output.**
- Output is template files only: `WorkflowTemplate` and `CronWorkflow` manifests with Helm expressions where values are Helm-bound.
- Argo's `{{ }}` collides with Helm's. The serializer escapes every Argo expression in one pass and emits real Helm expressions only for value references. Structured values use indentation-aware `toYaml`, and optional blocks use `with`.
- The chart's values prefix, if the chart nests stagecraft-related values under a key, is a render option.

## 9. Testing strategy

1. **Unit**: plain Python functions, escaping, and contract-validation logic.
2. **Block contract**: a compiled block exposes exactly its declared inputs, outputs, and artifacts, and references only the value paths and types it declares.
3. **Contract validation**: with a fixture schema, assert that a missing path, a wrong type, and an unhandled optional value each fail rendering with a clear error, and that a valid reference renders.
4. **Composition**: each step's required inputs are produced by an earlier stage, status contexts are unique, and parallel `write` paths do not overlap.
5. **Compile and snapshot**: assert structure (dependencies, hooks, `onExit`, resources) and snapshot the rendered templates so every change appears in review.
6. **Static validation**: run `helm template` against a **test-only fixture chart** (with fixture values and schema) inside the stagecraft repo, then `argo lint` and kubeconform on the result. Include escaping tests for Argo expressions in scripts, `when`, and `depends`. The fixture chart is never published.
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
    compile/         # stages->DAG, hooks, onExit, workspace strategies
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
7. Workspace strategies (`containerset` first).
8. Helm escaping and `toYaml` handling hardened with tests.
9. CLI polish (`check`, `graph`, `contexts`) and docs.

Do not generalize before two or three real pipelines exist.

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
- `containerSet` outputs, retries, resource summing, and failure status.
- Template-level versus task-level hook firing.
- Autoscaler behavior with `node-affinity` (a node must fit all parallel pods).
- Whether each strategy works using only cluster defaults (default StorageClass and default artifact repository).
- Argo CD sync order for CRDs versus chart contents, if Argo CD deploys the chart.
- What `values.schema.json` can and cannot express (for example `$ref`, `oneOf`, defaults) and how much of it the validator must support.

## 14. Open decisions

- Where pipeline definition files live: the chart repo or a separate pipelines repo.
- Whether the workspace strategy is always stated explicitly in the pipeline or inferred from block declarations with an override.
- Handling of optional values in the contract: required-only references, guards, or defaults declared by the block.
- Commit rendered templates into the chart repo, or publish them as a build artifact.
- Representation of skipped stages in commit statuses.
- Template naming across multiple releases in one namespace versus stable names in the Argo UI.
- Whether a reference block library belongs in a third package.
- Whether to offer a resolved preview mode (render with a values file) for local debugging, or rely on `helm template`.
