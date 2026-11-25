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
| `users` table | `username` (lowercased). Deletion protection in prod. TTL on `expires_at`, which only the api's `not_found` rows set (AOTY-22). |
| `guard` table | `id`. Guardrail counters (AOTY-26): `ip#<hash>#<hour>` (the per-IP new-name limit, TTL on `expires_at`) and `queue` (queue tickets and first-sync timing). Nothing precious: emptying it only restarts the counts. |
| `groups` table (AOTY-48) | `pk` + `sk`: `group#<slug>` with `meta` or `member#<username>` (`lambdas/common/groups.py`). GSIs `by_member` (username → pk, keys only) and `by_owner` (sparse, meta items only). Deletion protection in prod. See "Groups" below. |
| `albums` table | `artist_key` + `title_key` (`norm(album artist)`, `norm(base_title(album))`). Deletion protection in staging and prod, `prevent_destroy` everywhere; PITR in prod. |
| `sync.fifo` queue | Message group = username. Visibility 5,400 s (6 x the sync timeout). Event source mapping to the sync Lambda: batch 1, `ReportBatchItemFailures`, `maximum_concurrency` = `sync_concurrency` (2). The sync Lambda reserves one more. Terraform ignores the mapping's `enabled`, which is the syncs' hard stop (see "Guardrails"). |
| `mb-hot`, `mb-cold` queues | Polled by the mb Lambda itself, hot first. Visibility 900 s (the mb timeout). |
| mb lane (AOTY-19) | The mb Lambda has **reserved concurrency 1**: the one MusicBrainz client per environment. Async invoke config: max event age 1 h, no error retries (throttled starts are retried by Lambda anyway). `kamp-aoty-<env>-mb-backstop` is an hourly EventBridge Scheduler schedule that invokes it through the `…-mb-schedule` role; with both queues empty a run makes no MusicBrainz request. |
| Release index build (AOTY-20) | `kamp-aoty-<env>-index` ECS cluster (FARGATE_SPOT only) and task definition: 1 vCPU / 4 GB, x86_64 (Spot runs no ARM64), AWS's public `lambda/python:3.13` image pinned by digest, fetching its code from `s3://<store>/code/index.zip`. Runs in the default VPC's public subnets with a public IP and the default security group: no NAT. `…-index-daily` schedule at 06:15 UTC (off in sandbox) runs the exact revision Terraform made, through the `…-index-schedule` role. Logs in `/ecs/kamp-aoty-<env>-index`. See "Release index" below. |
| DLQs | One per queue, 14-day retention, `maxReceiveCount` 5, redrive allowed only from its source. |
| Chart builder (AOTY-21) | The chart Lambda has **reserved concurrency 1**, so builds never overlap and an older read can't overwrite a newer chart. Started by async invokes from sync (`{"username", "requested_at"}`), mb (`{"pending": true}`) and the api (`{"group", ...}`, AOTY-48); same invoke config as mb. Writes `<prefix>data/users/<u>/chart.json` (`Cache-Control: no-cache`) to the site bucket and `users/<u>/<year>/chart_pending.json` to the store. See "Charts" below. |
| Uptime check (AOTY-27) | The `uptime` Lambda (60 s, 128 MB), started hourly by the `kamp-aoty-<env>-uptime` schedule (off in sandbox, which isn't on kamp.fm) through the `…-uptime-schedule` role. Fetches `kamp.fm<base>`, `<base>api/health` and the chart of the user in SSM `uptime_user`; EMF `UptimeChecks` / `UptimeFailures`. |
| Alarms (AOTY-27) | CloudWatch alarms to the global `kamp-aoty-alerts` topic; see "Alarms". In prod, a metric filter on the API access log counts `ApiServerErrors-prod`. |
| `api`, `sync`, `mb`, `chart`, `uptime` Lambdas | python3.13 / arm64. Created from a placeholder (which raises on SQS events, so it never deletes queue messages); CD deploys the real code. Roles at `/kamp-aoty/<env>/`, each with the env's permissions boundary. |
| HTTP API | `GET /{proxy+}`, `POST /{proxy+}` and `$default` routes → api Lambda, which sees the full path (`/toys/aoty/api/...`) and strips `BASE_PATH`. Throttles per route: POST burst 5 / 2 req/s, GET (status polls) 50 / 25, the rest 20 / 10 (AOTY-26). |
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
  the names it knows (`ssm.tf`). The origin secret is kamp-site's, in kamp-site's state.
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
extends `global/ci_roles.tf` and the admin applies `global` first. Alarms (AOTY-27) are scoped
by name (`alarm:kamp-aoty-<env>-*`) for staging and prod; the sandbox role gets none, since any
branch could otherwise point an alarm at the owner's inbox. The alert topic and the budget live
in `global/`, so no deploy role touches SNS or Budgets. Event
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

1. `python scripts/package_lambdas.py` builds `dist/{api,sync,mb,chart,uptime}.zip` from `lambdas/` (Linux
   arm64 wheels, byte-for-byte reproducible per commit). Each zip carries a `build.json` with the
   commit's SHA (`-dirty` if a packaged file isn't committed), which `/api/health` reports.
2. `terraform apply` for `infra/envs/<env>` (skipped with `skip_terraform`).
3. `update-function-code` for each function, with `wait function-updated` before and after, then
   `publish-version`. Each deploy leaves a new numbered Lambda version described `<sha> by <actor>`,
   a same-commit redeploy included.
4. **Not in sandbox:** `scripts/build_shell.py --base <base>` builds the user page shell (AOTY-21)
   into `dist/web`. Prod alone also gets `--ga-id` (kamp.fm's GA4 property, AOTY-38). CD uploads its content-hashed `assets/` with a year's immutable cache, never
   deleting old ones (open tabs still ask for them), then `index.html` with `no-cache`, then
   invalidates `<base>*` on kamp.fm's distribution. `data/` belongs to the chart builder and is
   never touched. Old assets pile up at a few KB per deploy; pruning them is AOTY-39.
5. Smoke test through kamp.fm:
   - `<base>api/health` → 200, and the body must say `"source": "lambdas/api"` (deployed code, not
     the placeholder) and `"sha"` equal to the checked-out commit (not a stale `$LATEST`). A wrong
     body is retried 5 times, 5 s apart.
   - `<base>data/users/__smoke__/chart.json` → 404
   - `<base>` and `<base>user/__smoke__` → 200 with this env's shell, and every asset it names is
     in this build and loads.

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

### Alerts (AOTY-27, once)

`global` creates the `kamp-aoty-alerts` topic and the `kamp-aoty-monthly` budget. The email
subscription is made by hand, so the address never enters this public repo:

```sh
topic=$(terraform -chdir=infra/global output -raw alerts_topic_arn)
aws sns subscribe --topic-arn "$topic" --protocol email --notification-endpoint '<address>'
# Don't click the email's link: copy the Token from its URL, so unsubscribing needs AWS credentials
# (a forwarded email's "unsubscribe" link then can't remove it)
aws sns confirm-subscription --topic-arn "$topic" --token '<token>' --authenticate-on-unsubscribe true
aws sns list-subscriptions-by-topic --topic-arn "$topic"   # not PendingConfirmation
```

The budget is account-wide, $40 a month: emails at $10, $20 and $40 actual, and when the
forecast passes $40. It isn't scoped to the `Project` tag, because kamp-site shares the account
and a cost allocation tag only counts spend from the day it was activated.

The uptime check's chart user, per env (a user with a chart; a String, so no username is in
the repo; without it the chart check is skipped):

```sh
aws ssm put-parameter --type String --name /kamp-aoty/prod/uptime_user --value '<username>' --overwrite
```

## Testing a branch in sandbox (AOTY-33)

`envs/sandbox/` is a third copy of the stack (`kamp-aoty-sandbox-*`) for trying a branch on
real AWS before its PR merges. It's shared and the last deploy wins: each run's job summary
says which ref and SHA it deployed, and who deployed it. To see what's live now, `curl
<api_endpoint>/toys/aoty-sandbox/api/health` shows the SHA, and `aws lambda
list-versions-by-function` shows who published each version.

How it differs from staging:

- **Not on kamp.fm.** No CloudFront path and no OAC grant on the site bucket. The API is reached
  at its `execute-api` URL (`terraform output -json stack | jq -r .api_endpoint`), so the shell
  and the `data/` paths can't be tested here. For the same reason sandbox never sets
  `enforce_origin` (AOTY-26).
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
for fn in api sync mb chart uptime; do aws lambda update-function-code --function-name kamp-aoty-sandbox-$fn \
  --zip-file fileb://dist/$fn.zip --publish; done
```

The **first apply must be the admin's**. The sandbox role is denied the calls that create
bucket policies and public access blocks (it could otherwise make them public), so a branch can
only deploy once those exist. A branch that changes them fails its apply. The same goes for any
change to the HTTP API **stage** (throttles, route settings): API Gateway re-checks access logging
on every `UpdateStage`, which needs `logs:UpdateLogDelivery`, and sandbox denies it. Such a branch's
CI apply fails with "Insufficient permissions to enable logging"; apply it from a laptop as the
admin, then rerun the deploy (AOTY-26 hit this). Staging and prod roles allow it.

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

## Syncs: the API, or by hand

Opening `kamp.fm<base>user/<name>` asks for a sync (AOTY-22). The same through the API:

```sh
curl -X POST -H 'content-type: application/json' -d '{}' https://kamp.fm/toys/aoty-staging/api/users/alice/sync
curl https://kamp.fm/toys/aoty-staging/api/users/alice/status
```

POST answers 202 when it queues, or 200 with `reason` `cooldown` (a sync was requested within
`sync_cooldown_seconds`, 10 min by default), `in_progress` or `paused`. A new name counts
against the caller's IP (429 `too_many_new` past `new_users_per_ip_hour`), then is checked with
`user.getInfo`: an unknown one gets 404 and a `not_found` row that expires after a day (TTL on
`expires_at`). New users get 503 `busy` while the sync queue holds more than `max_queue_for_new`
(500) messages, and 503 `paused` while paused. Non-JSON POSTs get 415. Status stages: `queued`,
`reading`, `building`, `dating`, `idle`, `error`; while queued, `queue.position` (and
`queue.wait_s` once 20 first syncs are timed). A sync that hasn't moved for 30 min counts as
dead, so it never blocks a user for good, except that a queued one stays live while the sync
queue holds messages (a long line isn't a dead sync).

To skip the cooldown (or sync without the API), send the message the API sends. The group id
must be the lowercased username (one sync per user at a time), and the dedup id must be
unique, because content-based dedup would drop a repeat within 5 minutes:

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
index), `Rechecks`, `TracksReused`, `LastfmCalls` (its track-count fallback).

The sync worker logs one EMF line per message (AOTY-41): metrics `SyncsDone`, `SyncFailures` and
`LastfmCalls` (this sync's calls only), and plain fields `outcome` (`done`, `partial`,
`not_found`, `private`, `invalid`, `failed`), `first_sync`, `weeks` and `seconds`. The api logs
one per POST sync: metrics `SyncRequests` and `LastfmCalls`, field `outcome` (`queued`,
`new_user`, or the refusal: `cooldown`, `in_progress`, `paused`, `busy`, `too_many_new`,
`lastfm_unavailable`, `queue_unavailable`, `unknown_user`, `not_json`, `bad_name`,
`origin_refused`, `unavailable`). Status polls aren't counted. The outcomes are fields, not
metrics, because each metric name costs about $0.30/month per env: query them in Logs Insights
(the dashboard's tables do).

## Charts (AOTY-21)

After each sync the worker starts the chart Lambda, which writes
`<prefix>data/users/<u>/chart.json`: the top 100 By Play (album plays, AOTY-63), covers with fallbacks
(the scrobbles' Last.fm image or else `album.getInfo`'s, its other sizes, then the Cover Art
Archive), and how many of the user's albums the mb lane hasn't answered yet. The getInfo
answers are cached in the store's `users/<u>/<year>/chart_covers.json`. While any are pending, the mb lane's pokes rebuild the chart as they
resolve. The page is `kamp.fm<base>user/<name>`, in any casing.

To build one by hand (it skips nothing when `requested_at` is left out):

```sh
aws lambda invoke --function-name kamp-aoty-staging-chart --invocation-type Event \
  --cli-binary-format raw-in-base64-out --payload '{"username": "alice"}' /dev/null
aws s3 cp s3://kamp-aoty-staging-site/toys/aoty-staging/data/users/alice/chart.json -
```

Each run logs one EMF line: `ChartsBuilt`, `ChartsSkipped`, `ChartFailures`, `ChartSeconds`,
`CoverLookups`, `LastfmCalls`.

**Page themes (AOTY-65):** the owner's pick (`POST api/users/<name>/theme`) is saved on the users
row (`theme`), and the api sends the chart Lambda `{"username", "restyle": true}`. That copies the
row's theme into the published `chart.json` without a build, which is why the chart role can read
`<prefix>data/users/*` on the site bucket. There's no ListBucket there, so restyle only reads a
chart once `chart_built_at` says one exists. Full builds carry the theme too. To restyle by hand,
send the same payload. `group_writes` (below) is the kill switch for theme writes as well: off,
they answer 503 `writes_off` and the page hides the picker.

**Group charts (AOTY-50):** the chart Lambda takes `{"group": "<id>", "requested_at"?}` and
writes `<prefix>data/groups/<id>/chart.json` with M0's rules (`lambdas/chart/group_core.py`),
from the groups table (see "Groups"). After each member's own chart build it rebuilds their
groups, so the group chart fills in as syncs finish. Members still on their first sync count as
`waiting`; not_found and private ones as `unavailable`. `{"group": "<id>", "deleted": true}`
removes a deleted or moved group's chart. The page is AOTY-51.

## Groups (AOTY-48)

Owned groups live in the `groups` table; the api's routes are in `lambdas/api/handler.py`
("groups"). A group's id is a slug made from its name, and renaming it moves it to the new
slug: the old URL then answers 404. Slugs can be guessed, so anyone can find a group (user
decision 2026-11-23: it's public Last.fm data). Writes need a Last.fm sign-in and are
owner-only; at most 25 members, enforced in the same transaction as each add.

**`group_writes` is the kill switch** (Terraform, `envs/*/main.tf`), on in every env; prod turned it
on after sessions became revocable (AOTY-57, AOTY-62). Off, every write answers 503
`groups_unavailable`; reads, group pages and visit syncs still work. `api/me` reports it
(`group_writes`, signed in or not), and the page hides the sign-in row, "start a group" and the edit
controls while it's off (AOTY-52).

**The page (AOTY-52):** the owner edits on the group page itself ("edit group": add, remove,
rename, delete, copy link); `?edit=1` opens the panel, which is where a new group lands. Preview
every state with `tools/serve_shell.py` (fake groups and special names: `tools/preview_groups.py`),
or against sandbox's real API with `--proxy sandbox --sign-in-as <name>`, which mints a session
from sandbox's `session_key` (admin credentials) because Last.fm's callback can't reach localhost.

**By hand:** `tools/beta_group.py` (admin credentials) writes the same items the api does.
`create --env <env> --name ... --owner <lastfm name>` makes a group from `.env`'s `LASTFM_USERS`
(so no username is in the repo); `sync` queues every member's sync, skipping the api's limits;
`build` rebuilds the chart, `show` prints its counts, and `delete` removes a group and its chart.

**Migrating the beta's S3 groups (once per env, right after AOTY-48 deploys there).** Before
AOTY-48, groups were `s3://<store>/groups/<slug>/group.json`. The new code doesn't read those, so
a beta group's chart keeps serving but stops rebuilding until it's in the table:

```sh
.venv/bin/python tools/beta_group.py migrate --env staging --owner <lastfm name> --dry-run
.venv/bin/python tools/beta_group.py migrate --env staging --owner <lastfm name>
.venv/bin/python tools/beta_group.py show --env staging --id <slug>   # rebuilt just now?
```

It keeps each slug, skips groups already in the table, and leaves the S3 files. The chart
Lambda's `groups/*` read grant stays until every env is migrated, so a rollback still works;
then the grant and the files go (follow-up ticket).

**Acceptance:** `tools/chart_check.py --env staging --user <name>` compares a deployed chart
with M0's rules on the same store and albums items, and exits 1 on a difference.

**Previewing the page** without a deploy (sandbox has no kamp.fm path):
`tools/serve_shell.py --sample alice` serves the shell at `http://127.0.0.1:8000/toys/aoty/`
(the intro page) and `.../user/alice`, with kamp.fm's routing and CSP, a synthetic chart, and a
fake API: a 12 s pretend sync, and special names for the intro's errors (`nobody*`,
`lastfm-down`, `busy-busy`, `broken`, `private-*`, and AOTY-26's `capacity*`, `too-many`,
`throttled`) and for first visits (`newbie*`, the whole first-chart flow in about 40 s;
`nothing*`, no albums from the year; `caller*`, a minute in line first; `crowded*`, throttled
status polls that recover). To preview a real one, copy its
`chart.json` to `data/shell_preview/users/<u>/chart.json` first, or use
`--proxy sandbox|staging|prod` for that environment's real API and data (real syncs).

## Guardrails (AOTY-26)

For a surprise hit. Last.fm (~5 req/s per IP) and MusicBrainz (1 req/s) are the limits, not AWS,
so the aim is a real queue with bounded spend.

**Runtime knobs** (SSM `String` parameters under `/kamp-aoty/<env>/`, read by the api within
about 35 s, no deploy; `lambdas/common/guardrails.py`). None needs to exist: a missing one is its
default.

| Parameter | Default | |
|---|---|---|
| `pause_new_users` | `false` | New users get "we're at capacity" (503 `paused`), before any Last.fm call. Existing users keep syncing; every chart keeps serving from the CDN. |
| `pause_syncs` | `false` | No syncs are queued at all. Existing users' pages show their chart with a quiet banner; new users get `paused`. Messages already queued still drain. |
| `sync_cooldown_seconds` | `600` | Per-user time between page-visit syncs. Raise to `3600` at scale. |
| `new_users_per_ip_hour` | `10` | Distinct new usernames one IP may try per hour (429 `too_many_new`). |
| `max_queue_for_new` | `500` | New users get 503 `busy` while the sync queue is deeper. |
| `group_sync_cooldown_seconds` | `3600` | How often a group page's visit may sync its members (AOTY-55); within it, a visit queues nothing. |

A group page's visit (`POST api/groups/<id>/sync`) needs no sign-in and anyone can send it for
any slug, and slugs are guessable. What bounds the Last.fm calls it can cause is each member's own
`sync_cooldown_seconds`: a member is synced at most once per cooldown however many groups or
visits ask. The group cooldown only saves the api work. `pause_syncs` stops these too.

```sh
aws ssm put-parameter --name /kamp-aoty/prod/pause_new_users --type String --value true --overwrite
aws ssm delete-parameter --name /kamp-aoty/prod/pause_new_users      # back to the default
```

A malformed value is ignored (the last good one stays) with an error log and a
`GuardrailBadValue` metric. An api container that can't read SSM at all treats new users as
paused until it can.

**Hard stop for syncs** (immediate, unlike `pause_syncs`): disable the sync mapping. Terraform
ignores `enabled`, so an apply won't turn it back on. Messages wait in the queue meanwhile.

```sh
uuid=$(aws lambda list-event-source-mappings --function-name kamp-aoty-prod-sync --query 'EventSourceMappings[0].UUID' --output text)
aws lambda update-event-source-mapping --uuid "$uuid" --no-enabled     # --enabled to resume
```

Never use `put-function-concurrency 0` as a kill switch, and never direct-invoke sync in
staging or prod: both throttle the mapping, and throttled messages come back only after the
90-min visibility timeout and can reach the DLQ with the users row never marked `error`.

**Caps (Terraform):**
- `sync_concurrency` (2): the mapping's `maximum_concurrency`. It caps the sync worker's spend
  and its Last.fm rate (2 workers x 0.5 s/call = 4 req/s). The chart builder (`album.getInfo`
  covers) and the mb lane (track-count fallback) also call Last.fm, one at a time each.
- `api_post_throttle`, `api_get_throttle`, `api_throttle_*`: the route throttles above.

**Queue position:** every queued sync takes a ticket (`ADD joined` on the guard table's `queue`
item, stored as the users row's `queue_ticket`); the sync worker uses it up when it starts
(`ADD started`), and the api burns tickets no sync will use. The status's `queue.position` is
the tickets still ahead, clamped to the queue depth; the page only shows it going down.

**The execute-api lock:** the HTTP API's own URL bypasses CloudFront. kamp-site adds an
`x-kamp-origin` header to the api origins with a secret it keeps in SSM
(`/kamp-aoty/<env>/origin_secret`, a SecureString kamp-site creates and owns). With
`enforce_origin = true` (Terraform, per env), the api answers 403 to user routes without it.
Health stays open, so CD's smoke test can't be locked out. Only requests carrying the secret
have their `CloudFront-Viewer-Address` / `X-Forwarded-For` believed for the per-IP limit.

1. Deploy AOTY with `enforce_origin` off (the default), then kamp-site's change.
2. Check the header arrives: a new name's POST logs `ip headers: ... origin_ok=True` in
   `/aws/lambda/kamp-aoty-staging-api`, along with which IP headers came.
3. Set `enforce_origin = true` in `envs/staging`, deploy, check, then prod.
4. `curl -X POST -H 'content-type: application/json' -d '{}' <api_endpoint>/toys/aoty-staging/api/users/alice/sync` → 403.

Rotation: copy the value to `/kamp-aoty/<env>/origin_secret_previous` (SecureString), rotate the
secret in kamp-site and apply, wait for CloudFront to finish deploying, then delete `_previous`.
The api accepts either meanwhile (it rereads them every 5 min). Never set `enforce_origin` in
sandbox: it isn't behind CloudFront.

**Load test** (sandbox, admin credentials): `tools/load_test.py --users 300` turns on a fake
Last.fm (`common/fakelastfm.py`, `LASTFM_FAKE=1` on sandbox's functions; the code refuses it in
any other env), raises the per-IP and queue limits, POSTs synthetic `loadtest-*` users, and
writes `data/load_test/run-<ts>/report.md` (queue depth, sync concurrency, Last.fm calls per
minute, GB-s). It puts the settings back when it ends, even on an error. Then
`scripts/sandbox_reset.py --yes --keep-albums`.

## Sign-in (AOTY-47)

Visitors sign in with Last.fm's web auth. `GET <base>api/auth/start` redirects to Last.fm with
`cb=<PUBLIC_ORIGIN><base>api/auth/callback/<nonce>`. The callback checks the nonce against a
signed state cookie, calls `auth.getSession`, keeps only the username (Last.fm's session key is
dropped), and sets `__Secure-kamp_aoty_session` (HMAC-signed, 30 days, `Path=<base>`).
`GET api/me` says who's signed in; `POST api/auth/signout` clears the cookie, and
`POST api/auth/signout?everywhere=1` also revokes every session of that user (AOTY-57, below).

**Secrets** (SecureStrings, set by hand, readable by the api alone: `auth_param_arns` in
`modules/stack/ssm.tf`, never `ssm_params`, which every function gets):

```sh
# The Last.fm account's shared secret for this env's API key (last.fm/api/accounts).
aws ssm put-parameter --type SecureString --tier Standard \
  --name /kamp-aoty/<env>/lastfm_api_secret --value '<shared secret>'
# The session signing key: at least 32 bytes, or sign-in answers 503.
aws ssm put-parameter --type SecureString --tier Standard \
  --name /kamp-aoty/<env>/session_key --value "$(openssl rand -base64 48)"
```

Without them, `auth/start` and `api/me` answer 503 (never 401, so an SSM outage doesn't look like
being signed out). The api rereads them every 5 min.

**Callback URL:** `cb` overrides the callback registered on the Last.fm API account, so each env
keeps its own key and nothing needs registering per env. Sandbox's `PUBLIC_ORIGIN` is its
execute-api URL, so the flow can be tried there too.

**Rotating the session key:** copy `session_key` to `session_key_previous`, put a new
`session_key`, and delete `session_key_previous` 30 days later (the session length). Sessions
signed with either verify meanwhile; new ones use the new key. To sign everyone out at once, put
a new `session_key` without keeping `_previous`.

**Trust boundary:** the cookie goes with every request under `<base>` on kamp.fm, so script on any
kamp.fm page could call the api as the visitor. A plain sign-out clears this browser's cookie only.
"Sign out everywhere" revokes the rest, copied cookies included.

**Revoking sessions (AOTY-57):** "sign out everywhere" stores a cutoff in the groups table, and a
session signed at or before it (`iat <= revoked_at`) is refused. The item:

| Key | Value |
|-----|-------|
| `pk` | `session#<lowercased name>` |
| `sk` | `meta` |
| `revoked_at` | epoch seconds, as `N` |

- **Where the check runs:** every route that reads the session checks the cutoff. Writes and
  `me/groups` read it consistently; `api/me` doesn't need to.
- **If the cutoff can't be read**, those routes answer 503, never 401. A value that isn't a number
  revokes everything (fail closed).
- **A sign-in in the same second as the revoke** is revoked too. Signing in again fixes it.

To revoke one user's sessions by hand (admin credentials):

```sh
aws dynamodb put-item --table-name kamp-aoty-<env>-groups \
  --item '{"pk":{"S":"session#<lowercased name>"},"sk":{"S":"meta"},"revoked_at":{"N":"'"$(date +%s)"'"}}'
```

Never give these items `owner` or `username`: those attributes would put them in the `by_owner` and
`by_member` indexes. Losing an item revives the revoked sessions, for up to 30 days (the session
length). That happens on a table restore, and on `scripts/sandbox_reset.py` in sandbox, which wipes
the table's items.
CSRF: writes stay JSON-only POSTs and the cookie is `SameSite=Lax`; the only GET that changes
anything is the callback, after its nonce check. Two tabs signing in at once: the second
overwrites the state cookie, so the first lands on `?signin=failed`.

**Logs:** the token is only in the callback's query string. API Gateway's access log records the
path alone, kamp.fm's CloudFront has no access logging (checked 2026-11-23), and the api logs
usernames by hash and never the token or nonce. Each request emits one EMF line with an `auth`
field (`started`, `signed_in`, `bad_state`, `no_state`, `lastfm_failed`, `me`, `anonymous`,
`bad_session`, `signed_out`, `unavailable`, ...), searchable in Logs Insights; no new metrics.

## Alarms (AOTY-27)

Every alarm emails the `kamp-aoty-alerts` topic (`global/alerts.tf`) on ALARM and again on OK.
The alarm names say which env it is (`kamp-aoty-<env>-<alarm>`). `modules/stack/alarms.tf` has
the set, and its description links here. `alarms` per env: prod `full`, staging `basic` (the DLQs
and the uptime check), sandbox `none` (its deploy role can't write alarms: any branch can use it).
About 18 alarms in prod and 5 in staging; the first 10 in the account are free, the rest about
$0.10 each a month.

```sh
aws cloudwatch describe-alarms --alarm-name-prefix kamp-aoty-prod- --query 'MetricAlarms[].[AlarmName,StateValue]' --output table
```

What to do, per alarm:

### `dlq-messages`

`dlq-sync`, `dlq-mb-hot`, `dlq-mb-cold`: a message failed 5 receives. For sync, the users row is
`error` and the logs (`/aws/lambda/kamp-aoty-<env>-sync`, "sync failed for") say why. Read the
message, fix the cause, then redrive or purge:

```sh
aws sqs receive-message --queue-url "$(aws sqs get-queue-url --queue-name kamp-aoty-prod-sync-dlq.fifo --query QueueUrl --output text)" --attribute-names All
aws sqs start-message-move-task --source-arn arn:aws:sqs:us-east-1:<acct>:kamp-aoty-prod-sync-dlq.fifo   # back to its source
aws sqs purge-queue --queue-url <dlq url>                                                                # or drop them
```

The alarm keeps its state while SQS publishes nothing (an untouched queue goes quiet after about
6 h, messages or not), so the OK email comes once the queue is empty and read again. A long sync
continues in fresh messages (AOTY-46), so it doesn't land here; one that made no progress in 5
runs, or ran past 20 continuations, still does.

### `uptime-failing`

The hourly check (`lambdas/uptime`) failed two runs in a row. Its log
(`/aws/lambda/kamp-aoty-<env>-uptime`) names the check: `shell` (kamp.fm's CloudFront or the
site bucket), `health` (the HTTP API or the api Lambda; "not the deployed api" means the
placeholder code), `chart` (the chart data path, or the uptime user's chart was deleted). Re-run
it by hand:

```sh
aws lambda invoke --function-name kamp-aoty-prod-uptime /dev/stdout
```

### `uptime-silent`

No uptime run for 3 hours: the `kamp-aoty-<env>-uptime` schedule is off or failing, or the
function errors before it can report. Check the schedule (`aws scheduler get-schedule --name
kamp-aoty-prod-uptime`) and the function's log.

### `lambda-errors`

`sync-errors`, `mb-errors`, `chart-errors` (2+ in an hour), `api-errors` (over 5% and 5+ in 15
min). Read the function's log for the stack trace. A failed sync usually shows as `retrying` in
its users row and comes back by itself; a bad deploy shows on every invocation: roll back
("Deploys and releases").

### `sync-queue-age`

The oldest queued sync has waited over 4 h: a crowd, more than `max_queue_for_new` was meant to
allow. Stop new users joining the line with `pause_new_users` (see "Guardrails"); existing users
keep their charts. If something is broken rather than busy (sync concurrency stuck at 0), use the
hard stop on the mapping and fix it first.

### `mb-hot-age`

An album users are waiting on has sat in mb-hot over 30 min, so the mb lane isn't draining.
Check `/aws/lambda/kamp-aoty-<env>-mb` for errors and that the hourly `mb-backstop` schedule runs.
Start it by hand with the invoke in "Syncs: the API, or by hand".

### `mb-throttled`

MusicBrainz refused over 85% of requests for two hours (normal is 45–61%). It's MusicBrainz's
load, not ours (AOTY-10/12), so the lane only slows down. Check `/kamp-aoty/<env>/mb_contact` is still
set (a placeholder User-Agent can be blocked), then wait. If it lasts days, raise it on the MusicBrainz
forums.

### `mb-slow`

Under 0.75 albums/s for two hours with at least 50 albums handled (baseline 1.5–2.3). Usually
the same cause as `mb-throttled`. Otherwise look for `MbDeadlineStops` or `IndexErrors` in the mb
EMF line: a broken index sends every album to live searches (see "Release index").

### `lastfm-errors`

20 or more Last.fm calls failed after their retries in 15 min (`LastfmErrors`, emitted by
`common/lastfm.py`; the log line has `method`, `code` and `function`). Code 29 means we're rate
limited: lower `sync_concurrency` or pause syncs (`pause_syncs`). 10 or 26 means the API key was
revoked or suspended: rotate `/kamp-aoty/<env>/lastfm_api_key`. Codes 8, 11, 16 or `net` mean
Last.fm is having trouble: wait, and pause syncs if it lasts.

### `api-5xx`

The api answered 5+ server errors in each of two 5-minute windows, not counting its deliberate
503s (busy, paused). The access log (`/aws/apigateway/kamp-aoty-<env>-api`) has `status` and
`error`; a timeout or crash in the api Lambda shows there with an integration error. Then see
`lambda-errors`.

### `guardrail-bad-value`

A guardrail parameter under `/kamp-aoty/<env>/` doesn't parse. The api ignores it and keeps the
last good value, so a typo can't silently un-pause anything. Its error log names the parameter;
fix it with `put-parameter --overwrite` or delete it (back to the default). See "Guardrails".

### `chart-backlog`

`chart-event-age` (a build waited over 30 min) or `chart-events-dropped` (one waited an hour and
was dropped). The chart builder runs one at a time, so a crowd of syncs piles up builds. A
dropped build is redone at the user's next sync, or by hand ("Charts"). If it keeps happening,
slow the syncs down (`pause_new_users`) until the line clears.

## Dashboard (AOTY-41)

`kamp-aoty-<env>-dashboard` in CloudWatch (`modules/stack/dashboard.tf`), one per env, in six
rows: the mb lane, the release index, charts, syncs and the queue, the API and guardrails, and
Last.fm. Throughput is shown against the AOTY-12 baseline (1.5–2.3 albums/s), and the alarm
thresholds are drawn as lines. Two Logs Insights tables break down sync and sync-request
outcomes.

- **Free tier:** 3 dashboards of up to 50 metrics each. A precondition fails the plan past 50.
  Every metric entry counts, hidden ones feeding an expression too. Logs Insights widgets don't
  count (they bill per GB scanned, only when viewed).
- **Last.fm calls:** `LastfmCalls` is one metric that every function adds its own run's calls to.
  It's lumpy: a sync's calls land in the minute it finishes. In sandbox it includes the
  load-test fake, which also emits `FakeLastfmCalls` per call for `tools/load_test.py`.
- **API Gateway** is per stage, not per route: per-route metrics need detailed metrics, which
  are billed, and the routes are only GET, POST and `$default`. Its 5xx include the api's
  deliberate busy/paused 503s; `ApiServerErrors-prod` (prod only, `api.tf`) leaves them out.
- The deploy roles may write only their env's one dashboard name, so a branch can't create
  billed extra dashboards in sandbox.

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
  2026-11-22; Service Quotas, `L-B99A9384`). The mb and chart Lambdas reserve 1 each, sync
  reserves `sync_concurrency` + 1 (AOTY-26); its mapping's `maximum_concurrency` caps the Last.fm rate.
  Check the quota with `aws service-quotas get-service-quota --service-code lambda --quota-code L-B99A9384`.
- **Cost allocation tags:** activate `Project` and `Environment` in Billing once they appear (about 24 h
  after first use): `aws ce update-cost-allocation-tags-status --cost-allocation-tags-status TagKey=Project,Status=Active TagKey=Environment,Status=Active`.
- **The `execute-api` URL** bypasses CloudFront: the origin-secret lock is in "Guardrails"
  (AOTY-26). Don't forward `Host` to API Gateway, and don't use `disable_execute_api_endpoint`:
  CloudFront reaches the API by that hostname.

## Notes

- `dynamodb_table` locking prints a deprecation warning on Terraform ≥ 1.11 (`use_lockfile` is
  its replacement). It works; it's kept so AOTY and kamp-site share one lock mechanism.
- The provider lock files carry hashes for `linux_amd64`, `darwin_amd64` and `darwin_arm64`.
  After a provider upgrade, run `terraform providers lock -platform=...` for all three.
- The repo is public, so a PR plan comment (AOTY-16) shows the account ID and resource ARNs. That's
  accepted: neither is a secret, and no secret values exist in state.
