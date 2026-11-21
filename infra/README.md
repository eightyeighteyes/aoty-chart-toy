# infra

Terraform for the AOTY web app (M1, AOTY-2). It runs serverless on AWS in `us-east-1`, beside
kamp.fm, and shares kamp-site's state bucket under its own keys.

| Root | State key | Applied by |
|------|-----------|------------|
| `global/` | `aoty/global/terraform.tfstate` | the admin, from a laptop only |
| `envs/staging/` | `aoty/staging/terraform.tfstate` | CD via `kamp-aoty-staging-deploy` (or the admin) |
| `envs/prod/` | `aoty/prod/terraform.tfstate` | CD via `kamp-aoty-prod-deploy` (or the admin) |

`modules/stack/` holds everything for one environment; the env roots are thin wrappers.
