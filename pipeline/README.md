# pipeline/

The deploy path, adapted from the AI Innovation Lab reference pipeline. Its mechanics are
known-good and were preserved deliberately; what changed is its shape — it deploys stacks
from blueprint subdirectories rather than one application.

| File | What it is |
|---|---|
| `pipeline.yml` | CodePipeline, CodeBuild project, ECR repository and the IAM roles for all of it. |
| `stacks.yml` | Registry of every CloudFormation template in the repo. |
| `validate_stacks.py` | Enforces that `stacks.yml`, the filesystem and `pipeline.yml` agree. Run by PR checks. |
| `codebuild.yml` | Buildspec for container image builds. Ready, not yet wired to a stage. |

## How a merge becomes a deployment

```
merge to main
  └─ Source ............ CodeStarSourceConnection, BranchName = Environment ("main"),
  │                      DetectChanges registers a webhook → starts within seconds
  └─ PipelineDeploy .... deploys pipeline/pipeline.yml over itself
  │                      (so a PR that edits the pipeline takes effect on merge;
  │                       RestartExecutionOnUpdate reruns from the top under the new definition)
  └─ BlueprintDeploy ... one CloudFormation action per blueprint stack
```

`Environment` is the branch name, and the Source stage tracks the branch of that name. So
`Environment=main` is what makes "merges to main deploy", and a `test` branch deployed with
`Environment=test` gets its own parallel pipeline and its own `aidlc-test-*` stacks.

`Environment` has `AllowedPattern: '[a-z0-9]{1,4}'` — **four characters, no hyphens**, because
it is a component of every stack name and of the `stack/${Application}-${Environment}*` prefix
that `BuildPipelineRole` scopes to. `test` fits with nothing to spare; `staging` or
`feature-x` fail CloudFormation's parameter validation before anything is created. Pick a short
branch name for a parallel environment, or widen the pattern in both `pipeline.yml` and every
blueprint template together.

Note what CodePipeline does *not* give you: the GitHub side is what makes "a merge" mean "an
approved PR". Without branch protection this pipeline happily deploys a direct push to main.

## Deploy the pipeline for the first time

Requires `bootstrap/` to be deployed and the connection to read `AVAILABLE`.

```sh
aws cloudformation deploy \
  --profile ai-dlc-workshop \
  --region us-east-1 \
  --stack-name aidlc-main-pipeline \
  --template-file pipeline/pipeline.yml \
  --capabilities CAPABILITY_NAMED_IAM \
  --role-arn arn:aws:iam::<account>:role/cloudformation-deploy-role \
  --parameter-overrides Application=aidlc Environment=main Owner=ai-sei \
                        RemoteGitRepository=cu-aaii/ai-dlc-workshop
```

After this, never deploy it by hand again — edit `pipeline.yml` in a PR and let the
`PipelineDeploy` stage apply it.

## Stack naming is load-bearing

Stacks are `<application>-<environment>-<name>`, e.g. `aidlc-main-hello-world`.
`BuildPipelineRole` scopes its CloudFormation permissions to
`arn:...:stack/${Application}-${Environment}*`, so a stack named outside the convention
cannot be deployed by the pipeline. Same for the CodeBuild project name prefix.

## Adding a blueprint stack

1. Write the template under `blueprints/<name>/infra/`, with the four `cornell:*` tags on
   every resource.
2. Register it in `stacks.yml` — that is what makes PR checks lint it.
3. Add a `BlueprintDeploy` action in `pipeline.yml` modelled on `HelloWorldCloudFormation`.
   Pass every parameter the template needs explicitly; do not rely on template defaults, so
   the stack deploys identically by hand and by pipeline.

Stages are static CloudFormation, so `stacks.yml` and the pipeline actions are mirrored by
hand. That is on purpose — generating stages from the registry would be the framework the
workshop spec says not to build.

Mirrored by hand, but **not** unchecked. Skipping step 3 used to be invisible: the PR went
green, all three pipeline stages reported `Succeeded`, and no stack appeared, because nothing
had been asked to deploy one. `validate_stacks.py` now fails a `deployed_by: pipeline` entry
that no action deploys, and an action whose template is unregistered. If a template genuinely
should not be pipeline-deployed, register it `deployed_by: manual` and say why in its
`description`.

## Adding a container image build

`ContainerBuildProject`, `ContainerRepository` and `codebuild.yml` are defined and ready but
no stage invokes them, because `hello-world` is pure CloudFormation with nothing to build.
When a blueprint needs a Lambda image:

1. Add a `Dockerfile` with a named target for the component.
2. Add a `Build` stage action before `BlueprintDeploy` that runs `ContainerBuildProject` with
   `CONTAINER_TARGET` and `DATE_TAG` set (see the reference pattern in `codebuild.yml`).
3. Pass `#{<Namespace>.CONTAINER_DIGEST}` into the blueprint's CloudFormation action and
   deploy by digest, not by tag.
