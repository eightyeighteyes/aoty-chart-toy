# infra

Terraform for the AOTY web app (M1, AOTY-2). It runs serverless on AWS in `us-east-1`, beside
kamp.fm, and shares kamp-site's state bucket and lock table under its own keys.

| Root | State key | Applied by |
|------|-----------|------------|
| `global/` | `aoty/global/terraform.tfstate` | the admin, from a laptop only |
| `envs/staging/` | `aoty/staging/terraform.tfstate` | CD as `kamp-aoty-staging-deploy` (or the admin) |
| `envs/prod/` | `aoty/prod/terraform.tfstate` | CD as `kamp-aoty-prod-deploy` (or the admin) |

`modules/stack/` holds everything for one environment. The env roots are thin wrappers with
their own hard-coded backend, so a plan can't pair prod settings with staging state. Each exposes
a single `stack` output object (`terraform output stack`).

Every resource is named `kamp-aoty-<env>-*` and tagged `Project=kamp-aoty`, `Environment`,
`ManagedBy`, `Repo`.

## What's in an environment

| Resource | Notes |
|----------|-------|
| `kamp-aoty-<env>-site` bucket | Shell + `data/`. kamp.fm's CloudFront reads it through Origin Access Control (`s3:GetObject` on the key prefix only). Keys mirror the public path: prod stores `toys/aoty/...`, staging `toys/aoty-staging/...`. |
| `kamp-aoty-<env>-store` bucket | Private Lambda state (daily totals, index, checkpoints). Never readable by CloudFront. Versioned; old versions expire after 7 days. |
| `users` table | `username` (lowercased). Deletion protection in prod. |
| `albums` table | `artist_key` + `title_key` (`norm(album artist)`, `norm(base_title(album))`). Deletion protection **and** `prevent_destroy` in both envs; PITR in prod. |
| `sync.fifo` queue | Message group = username. Visibility 5,400 s (6 x the sync timeout). |
| `mb-hot`, `mb-cold` queues | Polled by the mb Lambda itself, hot first. |
| DLQs | One per queue, 14-day retention, `maxReceiveCount` 5, redrive allowed only from its source. |
| `api`, `sync`, `mb` Lambdas | python3.13 / arm64 placeholders. Roles at `/kamp-aoty/<env>/`, each with the env's permissions boundary. |
| HTTP API | `$default` route → api Lambda, which sees the full path (`/toys/aoty/api/...`) and strips `BASE_PATH`. Throttled to burst 20 / 10 req/s. |
| Log groups | 14-day retention, including API access logs. |

An idle stack costs about $0/mo: on-demand tables, SQS, Lambda, HTTP API, standard SSM
parameters and the default S3/DynamoDB/SQS encryption (no KMS keys) are all free when nothing
runs. Prod PITR costs about $0.20/GB-month.

## Ownership rules

- **CD owns Lambda code; Terraform owns configuration.** The placeholder zip is used only when a
  function is first created (`ignore_changes = [filename, source_code_hash]`). Deploy code with
  `aws lambda update-function-code`. A `terraform apply` never reverts it.
- **SSM parameter values are never in Terraform.** Refreshing a SecureString would put it in
  state, and the PR plan role would need decrypt rights. Terraform only grants the Lambdas read on
  `/kamp-aoty/<env>/*`.
- **AOTY owns its bucket policies** (the OAC grant uses kamp.fm's distribution ARN). kamp-site
  (AOTY-15) owns the CloudFront origins and behaviours and reads these outputs with
  `terraform_remote_state`.

## CI roles (`global/`)

| Role | Trusted for (GitHub OIDC `sub`) | Can |
|------|----------------------------------|-----|
| `kamp-aoty-plan` | `<prefix>:pull_request` | Read AOTY resource metadata and `aoty/staging`, `aoty/prod` state; take the state lock. **No** decrypted parameters, table items, objects, log events or kamp-site state (explicit denies too). |
| `kamp-aoty-staging-deploy` | `<prefix>:environment:staging` | Manage `kamp-aoty-staging-*` resources and `aoty/staging` state. |
| `kamp-aoty-prod-deploy` | `<prefix>:environment:production` | The same for prod. |

`<prefix>` is `repo:eightyeighteyes@6600722/aoty-chart-toy@1466126436`. The repo uses GitHub's
**immutable subject claims**, which embed the owner and repo IDs, so the bare
`repo:eightyeighteyes/aoty-chart-toy:...` form never matches and AWS answers "Not authorized to
perform sts:AssumeRoleWithWebIdentity". Check the prefix with
`gh api repos/eightyeighteyes/aoty-chart-toy/actions/oidc/customization/sub`.

How CI is kept from escalating:

- CI roles and boundary policies live at IAM path `/kamp-aoty-ci/`. Deploy roles are explicitly
  denied all IAM actions on that path.
- A deploy role can create and edit roles only at `/kamp-aoty/<env>/`, only with
  `kamp-aoty-<env>-boundary` attached, and can pass them only to Lambda.
- The GitHub environments limit who gets those `sub` claims: `staging` deploys only from `main`,
  `production` only from `v*` tags.

Fork PRs get no OIDC token, so the plan role works only for branches in this repo.

The deploy policies list only what this skeleton uses. A story that adds a new resource type
(EventBridge rules, SNS, alarms, event source mappings, reserved concurrency) extends
`global/ci_roles.tf` and the admin applies `global` first.

### Testing the roles from a laptop

`global` has `admin_assume_principal` (default null). Set it to let your IAM user assume the
CI roles, run plans and applies through them, then set it back:

```sh
terraform -chdir=infra/global apply -var admin_assume_principal=arn:aws:iam::<acct>:user/<you>
# a scratch AWS_CONFIG_FILE with role_arn/source_profile profiles, then for example:
AWS_PROFILE=aoty-plan terraform -chdir=infra/envs/staging plan
terraform -chdir=infra/global apply   # removes the trust again
```

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

## Follow-ups outside Terraform

- **Lambda concurrency quota:** the account limit was 10, and AWS always keeps 10 unreserved, so
  no function can have reserved concurrency (needed by AOTY-19's single MusicBrainz client and
  AOTY-26's caps). An increase to 1000 was requested on 2026-11-21 (Service Quotas, `L-B99A9384`).
  Check it with `aws service-quotas get-service-quota --service-code lambda --quota-code L-B99A9384`.
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
