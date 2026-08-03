# blueprints/

One directory per blueprint. A blueprint is a self-contained, governed unit that deploys into
an AWS account the builder never touches.

```
blueprints/<name>/
├── README.md          builder-facing: what this deploys, how to customize it
└── infra/             CloudFormation. One template per logical stack.
```

Later blueprints add `src/`, `skills/`, `docs/`, `tests/` and `infra/azure/` (Terraform)
alongside `infra/`.

| Blueprint | State |
|---|---|
| `hello-world` | Fully deploys. Proves the pipeline and the tagging convention. |

## Required of every blueprint

**All four `cornell:*` tags on every resource.** `cornell:owner`, `cornell:blueprint`,
`cornell:blueprint-version`, `cornell:deployment-id`. These feed inventory and the cost and
usage dashboard, so a resource without them is invisible to the observability work — which
makes it invisible in the demo.

Owner and deployment id vary per deployment and arrive as stack parameters. Blueprint name
and version are properties of the template itself: hardcode the name, and bump the version
default in the same PR that changes the blueprint.

**Registered in `pipeline/stacks.yml`, and wired to an action in `pipeline/pipeline.yml`.**
Unregistered templates are not linted by PR checks and PR checks fail on finding one. A
template registered `deployed_by: pipeline` with no action also fails — without that check it
would deploy nothing while the PR and every pipeline stage still reported success.

**Every parameter passed explicitly by the pipeline.** A blueprint should deploy identically
by hand and through the pipeline. Parameter defaults exist to make a manual deploy possible,
not to be the real values.
