# infra

Terraform for the AOTY web app (M1, AOTY-2). It runs serverless on AWS in `us-east-1`, beside
kamp.fm, and shares kamp-site's state bucket and lock table under its own keys.

| Root | State key | Applied by |
|------|-----------|------------|
| `global/` | `aoty/global/terraform.tfstate` | the admin, from a laptop only |
| `envs/staging/` | `aoty/staging/terraform.tfstate` | CD as `kamp-aoty-staging-deploy` (or the admin) |
| `envs/prod/` | `aoty/prod/terraform.tfstate` | CD as `kamp-aoty-prod-deploy` (or the admin) |
| `envs/sandbox/` | `aoty/sandbox/terraform.tfstate` | a branch, dispatched as `kamp-aoty-sandbox-deploy`; the admin for the first apply (see "Testing a branch in sandbox") |

`modules/stack/` holds everything for one environment. The env roots are thin wrappers with
their own hard-coded backend, so a plan can't pair prod settings with staging state. Each exposes
a single `stack` output object (`terraform output stack`).

Every resource is named `kamp-aoty-<env>-*` and tagged `Project=kamp-aoty`, `Environment`,
`ManagedBy`, `Repo`.

## What's in an environment

| Resource | Notes |
|----------|-------|
| `kamp-aoty-<env>-site` bucket | Shell + `data/`. kamp.fm's CloudFront reads it through Origin Access Control (`s3:GetObject` on the key prefix only). Keys mirror the public path: prod stores `toys/aoty/...`, staging `toys/aoty-staging/...`. |
| `kamp-aoty-<env>-store` bucket | Private Lambda state: `users/<username>/<year>/weeks.json.gz` (weekly album totals, AOTY-18) and the release index. Never readable by CloudFront. Versioned; old versions expire after 7 days. |
| `users` table | `username` (lowercased). Deletion protection in prod. |
| `albums` table | `artist_key` + `title_key` (`norm(album artist)`, `norm(base_title(album))`). Deletion protection in staging and prod, `prevent_destroy` everywhere; PITR in prod. |
| `sync.fifo` queue | Message group = username. Visibility 5,400 s (6 x the sync timeout). Event source mapping to the sync Lambda: batch 1, `ReportBatchItemFailures`, `maximum_concurrency` 2. |
| `mb-hot`, `mb-cold` queues | Polled by the mb Lambda itself, hot first. Visibility 900 s (the mb timeout). |
| mb lane (AOTY-19) | The mb Lambda has **reserved concurrency 1**: the one MusicBrainz client per environment. Async invoke config: max event age 1 h, no error retries (throttled starts are retried by Lambda anyway). `kamp-aoty-<env>-mb-backstop` is an hourly EventBridge Scheduler schedule that invokes it through the `…-mb-schedule` role; with both queues empty a run makes no MusicBrainz request. |
| Release index build (AOTY-20) | `kamp-aoty-<env>-index` ECS cluster (FARGATE_SPOT only) and task definition: 1 vCPU / 4 GB, x86_64 (Spot runs no ARM64), AWS's public `lambda/python:3.13` image pinned by digest, fetching its code from `s3://<store>/code/index.zip`. Runs in the default VPC's public subnets with a public IP and the default security group: no NAT. `…-index-daily` schedule at 06:15 UTC (off in sandbox) runs the exact revision Terraform made, through the `…-index-schedule` role. Logs in `/ecs/kamp-aoty-<env>-index`. See "Release index" below. |
| DLQs | One per queue, 14-day retention, `maxReceiveCount` 5, redrive allowed only from its source. |
| `api`, `sync`, `mb` Lambdas | python3.13 / arm64. Created from a placeholder (which raises on SQS events, so it never deletes queue messages); CD deploys the real code. Roles at `/kamp-aoty/<env>/`, each with the env's permissions boundary. |
| HTTP API | `$default` route → api Lambda, which sees the full path (`/toys/aoty/api/...`) and strips `BASE_PATH`. Throttled to burst 20 / 10 req/s. |
| Log groups | 14-day retention, including API access logs. |

An idle stack costs about $0/mo: on-demand tables, SQS, Lambda, HTTP API, standard SSM
parameters and the default S3/DynamoDB/SQS encryption (no KMS keys) are all free when nothing
runs. Prod PITR costs about $0.20/GB-month. The index build is about $0.01 a run on Spot (two
real builds a week; the other days exit in seconds), plus a few cents of S3 for the shards.

## Ownership rules

- **CD owns Lambda code; Terraform owns configuration.** The placeholder zip is used only when a
  function is first created (`ignore_changes = [filename, source_code_hash]`). The real code is
  in `lambdas/` and CD deploys it (see "Deploys and releases"). A `terraform apply` never reverts it.
  The same goes for the index build: its task definition is Terraform's, its code
  (`jobs/index/`, `dist/index.zip`) is uploaded by CD to `code/index.zip` in the store bucket.
- **SSM parameter values are never in Terraform.** Refreshing a SecureString would put it in
  state, and the PR plan role would need decrypt rights. Terraform only grants the Lambdas read on
  `/kamp-aoty/<env>/*`.
- **AOTY owns its bucket policies** (the OAC grant uses kamp.fm's distribution ARN). kamp-site
  (AOTY-15) owns the CloudFront origins and behaviours and reads these outputs with
  `terraform_remote_state`.

## CI roles (`global/`)

| Role | Trusted for (GitHub OIDC `sub`) | Can |
|------|----------------------------------|-----|
| `kamp-aoty-plan` | `<prefix>:pull_request` | Read AOTY resource metadata and `aoty/staging`, `aoty/prod`, `aoty/sandbox` state; take the state lock. **No** decrypted parameters, table items, objects, log events or kamp-site state (explicit denies too). |
| `kamp-aoty-staging-deploy` | `<prefix>:environment:staging` | Manage `kamp-aoty-staging-*` resources and `aoty/staging` state. |
| `kamp-aoty-prod-deploy` | `<prefix>:environment:production` | The same for prod. |
| `kamp-aoty-sandbox-deploy` | `<prefix>:environment:sandbox` | The same for sandbox, minus CloudFront, and denied anything that makes a resource public (bucket policies, public access blocks, ACLs, function URLs, `AddPermission` for anyone but API Gateway, table and queue resource policies) or rewrites account-wide log policies. |

`<prefix>` is `repo:eightyeighteyes@6600722/aoty-chart-toy@1466126436`. The repo uses GitHub's
**immutable subject claims**, which embed the owner and repo IDs, so the bare
`repo:eightyeighteyes/aoty-chart-toy:...` form never matches and AWS answers "Not authorized to
perform sts:AssumeRoleWithWebIdentity". Check the prefix with
`gh api repos/eightyeighteyes/aoty-chart-toy/actions/oidc/customization/sub`.

How CI is kept from escalating:

- CI roles and boundary policies live at IAM path `/kamp-aoty-ci/`. Deploy roles are explicitly
  denied all IAM actions on that path.
- A deploy role can create and edit roles only at `/kamp-aoty/<env>/`, only with
  `kamp-aoty-<env>-boundary` attached, and can pass them only to Lambda and EventBridge
  Scheduler (the mb backstop, AOTY-19).
- HTTP APIs (random ids) are scoped by their `Environment` tag. Tag writes on APIs and event
  source mappings check both the requested and the existing tag, so a role can't relabel another
  env's resource as its own.
- The GitHub environments limit who gets those `sub` claims: `staging` deploys only from `main`,
  `production` only from `v*` tags, `sandbox` from any branch after a required reviewer approves.
- Only collaborators with write access can push branches or tags or dispatch workflows, and the
  only one is the owner. Fork PRs get no OIDC token or secrets. **Before adding a collaborator**,
  add a tag ruleset limiting `v*` to admins and the release App, and a reviewer on `production`:
  today anyone with write access could push a tag and deploy prod.

Fork PRs get no OIDC token, so the plan role works only for branches in this repo.

The deploy policies list only what this skeleton uses. A story that adds a new resource type
(SNS, alarms, …) extends `global/ci_roles.tf` and the admin applies `global` first. Event
source mappings (AOTY-18) are scoped by the `lambda:FunctionArn` condition, because their ARNs
are random UUIDs. Schedules (AOTY-19) are scoped by name (`schedule/default/kamp-aoty-<env>-*`)
because they carry no tags. The schedule uses Scheduler rather than an EventBridge rule, because
a rule needs `lambda:AddPermission` for `events.amazonaws.com`, which sandbox is denied.
Reserved concurrency needs nothing extra: it's a `lambda:*` call on the env's own functions.

### Testing the roles from a laptop

`global` has `admin_assume_principal` (default null). Set it to let your IAM user assume the
CI roles, run plans and applies through them, then set it back:

```sh
terraform -chdir=infra/global apply -var admin_assume_principal=arn:aws:iam::<acct>:user/<you>
# a scratch AWS_CONFIG_FILE with role_arn/source_profile profiles, then for example:
AWS_PROFILE=aoty-plan terraform -chdir=infra/envs/staging plan
terraform -chdir=infra/global apply   # removes the trust again
```

## Deploys and releases (AOTY-17)

```
PR merged to main
  └─ CI (lint, test, terraform)            .github/workflows/ci.yml
       └─ on success → CD                  .github/workflows/cd.yml (workflow_run)
            ├─ staging: deploy.yml          package → terraform apply → Lambda code → shell → smoke
            └─ release: release-please      opens/updates "chore(main): release X.Y.Z"
release PR merged → the same path, then release-please tags vX.Y.Z
  └─ tag push → deploy.yml for production   same steps, `production` environment
```

What a deploy (`deploy.yml`) does, as the env's deploy role:

1. `python scripts/package_lambdas.py` builds `dist/{api,sync,mb}.zip` from `lambdas/` (Linux
   arm64 wheels, byte-for-byte reproducible per commit). Each zip carries a `build.json` with the
   commit's SHA (`-dirty` if a packaged file isn't committed), which `/api/health` reports.
2. `terraform apply` for `infra/envs/<env>` (skipped with `skip_terraform`).
3. `update-function-code` for each function, with `wait function-updated` before and after, then
   `publish-version`. Each deploy leaves a new numbered Lambda version described `<sha> by <actor>`,
   a same-commit redeploy included.
4. **Only if `web/index.html` exists:** uploads hashed assets with a year's cache, then
   `index.html` with `no-cache`, then removes deleted files (never `data/`), and invalidates
   `<base>*` on kamp.fm's distribution.
5. Smoke test through kamp.fm:
   - `<base>api/health` → 200, and the body must say `"source": "lambdas/api"` (deployed code, not
     the placeholder) and `"sha"` equal to the checked-out commit (not a stale `$LATEST`). A wrong
     body is retried 5 times, 5 s apart.
   - `<base>data/users/__smoke__/chart.json` → 404
   - `<base>` → 200, once a shell exists

**Rollback** redeploys code and the shell from an older tag without touching infrastructure:

```sh
gh workflow run deploy.yml --ref v0.1.0 -f environment=production   # skip_terraform defaults to true
```

To also roll infrastructure back, add `-f skip_terraform=false`, but check the plan first: an
old tag's Terraform reverts any newer resources.

### Release app (one-time setup)

release-please runs with a GitHub App token, not `GITHUB_TOKEN`, because GitHub runs no workflows
for PRs or tags that `GITHUB_TOKEN` creates. Without the App, release PRs would get no CI (the
rulesets would block them) and tags wouldn't deploy prod.

1. GitHub → Settings → Developer settings → GitHub Apps → **New GitHub App**.
   - Name: e.g. `aoty-release`. Homepage URL: the repo URL. **Webhook: uncheck Active.**
   - Repository permissions:
     - **Contents: Read and write**
     - **Pull requests: Read and write**
     - **Issues: Read and write** (release-please labels its PRs)
     - Metadata: read (default)
   - Where can it be installed: **Only on this account**.
2. Create it. On its page, note the **Client ID**, then **Generate a private key** (downloads a `.pem`).
3. **Install App** → only `aoty-chart-toy`.
4. Add the repo settings:

   ```sh
   gh variable set RELEASE_APP_CLIENT_ID --repo eightyeighteyes/aoty-chart-toy --body "<client id>"
   gh secret set RELEASE_APP_PRIVATE_KEY --repo eightyeighteyes/aoty-chart-toy < aoty-release.pem
   ```

   Then delete the downloaded `.pem`.

Versioning: Conventional Commits since the `bootstrap-sha` in `release-please-config.json`.
`feat:` bumps the minor version (0.x), `fix:` the patch; tags are `vX.Y.Z`. PRs merge with merge
commits, so every branch commit becomes a changelog line: keep them Conventional.

## Bootstrap (done once, 2026-11-21)

1. `terraform -chdir=infra/global init`, then `apply` (as the admin).
2. `terraform -chdir=infra/envs/staging init`, then `apply`; the same for `envs/prod`.
3. Set the parameters (values from `.env`; they never pass through Terraform):

   ```sh
   aws ssm put-parameter --type SecureString --tier Standard \
     --name /kamp-aoty/<env>/lastfm_api_key --value '<key>'
   aws ssm put-parameter --type SecureString --tier Standard \
     --name /kamp-aoty/<env>/mb_contact --value '<email>'
   # to rotate: add --overwrite
   ```

4. Create the GitHub environments with deployment rules:

   ```sh
   echo '{"deployment_branch_policy":{"protected_branches":false,"custom_branch_policies":true}}' > env.json
   gh api -X PUT repos/eightyeighteyes/aoty-chart-toy/environments/staging --input env.json
   gh api -X PUT repos/eightyeighteyes/aoty-chart-toy/environments/production --input env.json
   gh api -X POST repos/eightyeighteyes/aoty-chart-toy/environments/staging/deployment-branch-policies -f name=main -f type=branch
   gh api -X POST repos/eightyeighteyes/aoty-chart-toy/environments/production/deployment-branch-policies -f "name=v*" -f type=tag
   ```

5. Smoke test: `curl https://<api_domain_name>/toys/aoty-staging/api/health` → 200.

## Testing a branch in sandbox (AOTY-33)

`envs/sandbox/` is a third copy of the stack (`kamp-aoty-sandbox-*`) for trying a branch on
real AWS before its PR merges. It's shared and the last deploy wins: each run's job summary
says which ref and SHA it deployed, and who deployed it. To see what's live now, `curl
<api_endpoint>/toys/aoty-sandbox/api/health` shows the SHA, and `aws lambda
list-versions-by-function` shows who published each version.

How it differs from staging:

- **Not on kamp.fm.** No CloudFront path and no OAC grant on the site bucket. The API is reached
  at its `execute-api` URL (`terraform output -json stack | jq -r .api_endpoint`), so the shell
  and the `data/` paths can't be tested here. If AOTY-15 disables the `execute-api` endpoint,
  sandbox needs an exemption.
- **Its own data.** Separate tables, buckets, queues and SSM prefix (`/kamp-aoty/sandbox/`), so a bad
  branch can't pollute staging's release-year cache. Its albums table starts cold.
- **Its own Last.fm API key.** A branch's Lambda could log the key and read it back with the
  sandbox role, so use a separate key, one you can revoke. Never reuse the staging or prod key.
- **Logs are kept 3 days.** The albums table has no deletion protection, so it can be torn down.

**From a branch (CI):**

```sh
gh workflow run deploy.yml --ref my-branch -f environment=sandbox -f skip_terraform=false
```

The run waits for a reviewer on the `sandbox` environment, then packages, applies, updates the
code and checks `<api_endpoint>/toys/aoty-sandbox/api/health`. GitHub only lists a
`workflow_dispatch` once the workflow is on `main`, but `--ref` runs the branch's own copy of
`deploy.yml` and `infra/`. So IAM is the only real guard, not the workflow.

**From a laptop (admin credentials):**

```sh
.venv/bin/python scripts/package_lambdas.py
terraform -chdir=infra/envs/sandbox init
terraform -chdir=infra/envs/sandbox apply
for fn in api sync mb chart; do aws lambda update-function-code --function-name kamp-aoty-sandbox-$fn \
  --zip-file fileb://dist/$fn.zip --publish; done
```

The **first apply must be the admin's**. The sandbox role is denied the calls that create
bucket policies and public access blocks (it could otherwise make them public), so a branch can
only deploy once those exist. A branch that changes them fails its apply.

**Testing a sync:** send the message from "Running a sync by hand" below to
`kamp-aoty-sandbox-sync.fifo`. Then check `users` in `kamp-aoty-sandbox-users`, and
`s3://kamp-aoty-sandbox-store/users/<name>/2026/weeks.json.gz`.

**Reset** (empties the queues, buckets and tables, keeps the infrastructure, admin credentials):

```sh
.venv/bin/python scripts/sandbox_reset.py                       # dry run
.venv/bin/python scripts/sandbox_reset.py --yes                 # wipe everything
.venv/bin/python scripts/sandbox_reset.py --yes --keep-albums   # keep the release-year cache
.venv/bin/python scripts/sandbox_reset.py --yes --keep-index    # keep index/ and code/ (a ~25 min rebuild)
```

It disables the sync mapping while it works. SQS allows one purge per queue per 60 s, and a sync
that was already running can still write for up to 15 min, so run the reset again if that matters.

**Teardown** (admin), in this order:

1. `scripts/sandbox_reset.py --yes`. `destroy` fails on non-empty buckets, and the store is versioned.
2. Remove the albums table from state (it has `prevent_destroy`), then delete it:
   `terraform -chdir=infra/envs/sandbox state rm module.stack.aws_dynamodb_table.albums`, then
   `aws dynamodb delete-table --table-name kamp-aoty-sandbox-albums`.
3. `terraform -chdir=infra/envs/sandbox destroy`.
4. Only to retire sandbox for good: drop it from `environments` in `global/variables.tf` and
   apply `global`. The boundary can't be deleted while roles still carry it.

To bring it back, apply `envs/sandbox` as the admin again (the first-apply rule above).

**Bootstrap (once):**

1. Apply `global` (creates `kamp-aoty-sandbox-deploy` and its boundary).
2. Create the GitHub environment, with a required reviewer and no branch rule:

   ```sh
   # 6600722 = `gh api users/eightyeighteyes --jq .id`
   echo '{"reviewers":[{"type":"User","id":6600722}],"prevent_self_review":false,"deployment_branch_policy":null}' > env.json
   gh api -X PUT repos/eightyeighteyes/aoty-chart-toy/environments/sandbox --input env.json
   ```

3. Put `/kamp-aoty/sandbox/lastfm_api_key` (the separate key) and `/kamp-aoty/sandbox/mb_contact`
   (see "Bootstrap" above).
4. Do the first apply and code deploy from a laptop (above).

## Running a sync by hand (until AOTY-22)

The API doesn't enqueue syncs yet. To sync a user in staging, send the message the API will
send. The group id must be the lowercased username (one sync per user at a time), and the
dedup id must be unique, because content-based dedup would drop a repeat within 5 minutes:

```sh
aws sqs send-message --queue-url "$(aws sqs get-queue-url --queue-name kamp-aoty-staging-sync.fifo --query QueueUrl --output text)" \
  --message-group-id alice --message-deduplication-id "alice-$(date +%s)" \
  --message-body '{"username": "alice", "full": false}'
```

Then check `users` (`state`, `progress`) and `s3://kamp-aoty-staging-store/users/alice/2026/weeks.json.gz`.
`"full": true` refetches every week; `"year"` defaults to 2026.

The sync queues every undated album to `mb-hot`/`mb-cold` and starts the mb Lambda, which fills
`kamp-aoty-<env>-albums`. To start it by hand (it exits at once if both queues are empty):

```sh
aws lambda invoke --function-name kamp-aoty-staging-mb --invocation-type Event --payload '{}' /dev/null
```

Each run logs one EMF line (namespace `kamp-aoty`, dimension `Environment`): `MbRequests`,
`MbThrottled`, `MbRetryAfterSeconds`, `AlbumsResolved`/`Missed`/`Failed`/`AlreadyResolved`,
`YearWasted`, `AlbumsPerSecond`, `HotQueueDepth`, `ColdQueueDepth`, `RunSeconds`, and for the
release index `IndexHits`/`Untrusted`/`Misses`/`Errors`, `IndexAgeHours` (absent with no usable
index), `Rechecks`, `TracksReused`.

## Release index (AOTY-20)

The index build (`jobs/index/build.py`) runs daily at 06:15 UTC. It exits within seconds unless
a new MusicBrainz dump is out (twice a week). Then it takes about 25 minutes: a 1.24 GB download,
the parse, 4,096 shard uploads under `index/<dump>/` and `index/current.json` last. It then
queues re-checks to `mb-cold`. It never calls the MusicBrainz API, so it can't slow the mb lane.
`common/releaseindex.py` has the format and the rules.

To run it by hand (sandbox has no schedule), with admin credentials:

```sh
stack=$(terraform -chdir=infra/envs/sandbox output -json stack)
aws ecs run-task --cluster kamp-aoty-sandbox-index \
  --task-definition "$(jq -r .index_task.task_definition <<<"$stack")" \
  --capacity-provider-strategy capacityProvider=FARGATE_SPOT,weight=1 \
  --network-configuration "awsvpcConfiguration={subnets=[$(jq -r '.index_task.subnets|join(",")' <<<"$stack")],securityGroups=[$(jq -r .index_task.security_group <<<"$stack")],assignPublicIp=ENABLED}"
aws logs tail /ecs/kamp-aoty-sandbox-index --follow
```

The task needs `code/index.zip`, which a deploy uploads. To force a rebuild of the same dump,
delete `index/current.json` first. Without a usable manifest the mb lane goes live for every
album, exactly as before AOTY-20. Its EMF line has `IndexBuildSeconds`, `IndexBuildSkipped`,
`IndexDumpAgeHours`, `IndexRecords`, `IndexTitles`, `RechecksQueued`.

A Spot interruption kills the run with nothing published: the old index stays live, and the
next day's run starts over. Scheduler retries only a RunTask that failed to start (no capacity),
up to three times within an hour.

## Follow-ups outside Terraform

- **Lambda concurrency quota:** raised from 10 to 1,000 (requested 2026-11-21, approved by
  2026-11-22; Service Quotas, `L-B99A9384`). The mb Lambda reserves 1 (AOTY-19); AOTY-26's
  caps can use it too. The sync worker still uses its
  event source mapping's `maximum_concurrency` (2 in every env), which also caps the Last.fm rate.
  Check the quota with `aws service-quotas get-service-quota --service-code lambda --quota-code L-B99A9384`.
- **Cost allocation tags:** activate `Project` and `Environment` in Billing once they appear (about 24 h
  after first use): `aws ce update-cost-allocation-tags-status --cost-allocation-tags-status TagKey=Project,Status=Active TagKey=Environment,Status=Active`.
- **For AOTY-15:** the `execute-api` URL bypasses CloudFront and its throttling, so lock it down
  once CloudFront fronts it (a shared-secret header, or `disable_execute_api_endpoint`). Don't
  forward `Host` to API Gateway.

## Notes

- `dynamodb_table` locking prints a deprecation warning on Terraform ≥ 1.11 (`use_lockfile` is
  its replacement). It works; it's kept so AOTY and kamp-site share one lock mechanism.
- The provider lock files carry hashes for `linux_amd64`, `darwin_amd64` and `darwin_arm64`.
  After a provider upgrade, run `terraform providers lock -platform=...` for all three.
- The repo is public, so a PR plan comment (AOTY-16) shows the account ID and resource ARNs. That's
  accepted: neither is a secret, and no secret values exist in state.
