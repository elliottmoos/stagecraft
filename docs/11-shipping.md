# 11. Shipping pipelines

A pipeline is only useful once it's installed. This chapter covers getting the generated chart
into a cluster, and the checks that keep it installable.

## The flow

```
pipelines repo
  actions.py, pipelines/*.py ── stagecraft render ──▶ chart/ (Chart.yaml, values.schema.json, templates/)
  values/<env>.yaml ─────────────────────────────────────┐ │
                                                         ▼ ▼
                                        helm upgrade --install, per environment
                                                          │
                                                          ▼
                                                   Argo Workflows
```

Render into the chart directory and commit it:

```sh
uv run stagecraft render pipelines --out chart
```

Each pipeline becomes one file, `templates/<pipeline>.yaml`, holding its `WorkflowTemplate` and
any `CronWorkflow`s; `values.schema.json` requires every Helm value they use. Rendering is
deterministic: the same pipelines and the same stagecraft version always give the same files, so
reviewers see every change to what's installed as a diff.

The chart is its own Helm release, installed next to whatever provides the things pipelines refer
to by name (the workflow service account, Secrets, the artifact repository):

```sh
helm upgrade --install pipelines chart -f values/staging.yaml -n argo
```

Stagecraft never writes a `values.yaml` into the chart. Keep values per environment, wherever
your deployment tooling (Helm, Argo CD, ...) reads them from.

## Checks in CI

For each environment's values:

```sh
uv run pytest                                  # your action tests
uv run stagecraft render pipelines --out chart
git diff --exit-code chart                     # the committed chart is up to date
helm template pipelines chart -f values/staging.yaml > rendered.yaml
argo lint --offline rendered.yaml
kubeconform -strict -schema-location default \
  -schema-location '<Argo CRD schemas>/{{.ResourceKind}}_{{.ResourceAPIVersion}}.json' \
  rendered.yaml
```

- `render` fails on anything that can't compile: an undeclared param, a value used with two
  types, a misplaced placeholder.
- `git diff --exit-code` catches a pipeline change committed without re-rendering.
- `helm template` validates the environment's values against the generated schema, so a missing
  or wrongly typed value fails here, not at install time.
- `argo lint` checks the workflows the way the Argo controller will. If a pipeline's setup calls
  an existing WorkflowTemplate (`TemplateRef`), pass that template's manifest too
  (`argo lint --offline rendered.yaml parse-webhook.yaml`): lint only resolves references against
  the files it's given.
- kubeconform needs JSON schemas for Argo's CRDs (`WorkflowTemplate`, `CronWorkflow`), for
  example from a CRD schema catalog or generated from the Argo release you run.

`stagecraft check pipelines` compiles without writing, for a quicker local check.

Stagecraft targets Argo Workflows 4.1.6 and Helm 4; use matching versions of `argo` and `helm`
in CI.

## Upgrading stagecraft

Stagecraft is pinned exactly in the pipelines repo. To upgrade, bump the pin, render, and review
the diff of the generated chart like any other change: it's in the pull request.

## Using it day to day

- **Submitting a run**: in the Argo UI under Workflow Templates, or with
  `argo submit --from workflowtemplate/<pipeline> -p revision=<commit> ...`. Params appear as the
  submit form.
- **Hooking up pushes and pull requests**: anything that can submit a workflow with params works
  (Argo Events, a webhook relay, another CI system). Stagecraft only defines what a run does.
- **Reading a run**: each stage is a node in the graph, its steps inside it; handlers and status
  reports appear next to the step they belong to.
- **Seeing a pipeline's shape**: `uv run stagecraft graph pipelines --pipeline <name>`.

## Recap

- `render` writes a complete chart; commit it, and install it as its own release with each
  environment's values.
- CI renders and checks the chart is up to date, then runs `helm template` (which validates the
  values), `argo lint`, and kubeconform per environment.

Next: [12. Designing pipelines](12-designing-pipelines.md), which puts it all together.
