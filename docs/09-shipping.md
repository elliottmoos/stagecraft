# 9. Shipping pipelines

A pipeline is only useful once it's installed. This chapter connects the pipelines repo to the
chart that installs its templates, and sets up checks on both sides.

## The flow

```
pipelines repo                          chart repo
  blocks.py, pipelines/*.py               values.schema.json (the contract)
        │                                 values.yaml, values-<env>.yaml
        │  stagecraft render  ◀───────────────┘ (reads the schema)
        ▼
  templates/*.yaml  ──────────────────▶ templates/pipelines/*.yaml
                                              │ helm install / upgrade, per environment
                                              ▼
                                        Argo Workflows
```

Render into a directory of the chart's `templates/` (Helm reads subdirectories):

```sh
uv run stagecraft render pipelines \
  --values-schema ../chart/values.schema.json \
  --out ../chart/templates/pipelines
```

Each pipeline becomes one file, `<pipeline>.yaml`, holding its `WorkflowTemplate` and any
`CronWorkflow`s. Rendering is deterministic: the same pipelines and the same stagecraft version
always give the same files.

How the files get from one repo to the other is up to you: commit them into the chart repo from
the pipelines repo's CI (reviewers then see every change to the generated manifests as a diff), or
publish them as a build artifact the chart's build picks up.

## Checks in the pipelines repo

```sh
uv run pytest                                                    # your block tests
uv run stagecraft check pipelines --values-schema <chart>/values.schema.json
```

`check` does everything `render` does except write files: it loads every pipeline, compiles it,
and checks every value reference against the chart's schema. Run it against the schema of the
chart version you'll install into, so a value the chart removed or retyped fails here, before
anything ships.

## Checks in the chart repo

Stagecraft's output is ordinary templates, so the chart validates it with ordinary tools. For
each environment's values:

```sh
helm template ci . -f values-staging.yaml > rendered.yaml
argo lint --offline rendered.yaml
kubeconform -strict -schema-location default \
  -schema-location '<Argo CRD schemas>/{{.ResourceKind}}_{{.ResourceAPIVersion}}.json' \
  rendered.yaml
```

- `helm template` also validates the values against `values.schema.json`, so an environment
  missing a required value fails here.
- `argo lint` checks the workflows the way the Argo controller will.
- kubeconform needs JSON schemas for Argo's CRDs (`WorkflowTemplate`, `CronWorkflow`), for
  example from a CRD schema catalog or generated from the Argo release you run.

Stagecraft targets Argo Workflows 4.1.6 and Helm 4; use matching versions of `argo` and `helm`
in CI.

## Upgrading stagecraft

Stagecraft is pinned exactly in the pipelines repo. To upgrade, bump the pin, render, and review
the diff of the generated templates like any other change. If the generated templates are
committed, that diff is in the pull request.

## Using it day to day

- **Submitting a run**: in the Argo UI under Workflow Templates, or with
  `argo submit --from workflowtemplate/<pipeline> -p sha=<commit> ...`. Params appear as the
  submit form.
- **Hooking up pushes and pull requests**: anything that can submit a workflow with params works
  (Argo Events, a webhook relay, another CI system). Stagecraft only defines what a run does.
- **Reading a run**: each stage is a node in the graph, its blocks inside it; handlers and status
  reports appear next to the step they belong to.
- **Seeing a pipeline's shape**: `uv run stagecraft graph pipelines --pipeline <name>`.

## Recap

- `render` writes one file per pipeline into the chart's templates; how it reaches the chart repo
  is your choice.
- The pipelines repo runs block tests and `stagecraft check` against the chart's schema.
- The chart repo runs `helm template`, `argo lint`, and kubeconform per environment.

Next: [10. Designing pipelines](10-designing-pipelines.md), which puts it all together.
