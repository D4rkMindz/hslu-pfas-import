#!/usr/bin/env bash
# The steps of `make setup` (and `make up`, `make tf-destroy`). Runs with macOS's bash 3.2, too.
#
#   env      create .env/.env.docker from the examples if missing; stop while <placeholders> remain
#   cloud    TARGET=cloud only: gcloud login, project (created if GCP_PROJECT_ID is empty), billing,
#            Terraform, the service account key (secrets/) and its HMAC key (.env); keys are reused
#   secrets  write .env.kestra: the pipeline's passwords and keys as Kestra secrets SECRET_PFAS_* (base64)
#   kv       write the flow's settings from .env into the KV store of its namespace (Kestra must run)
#   forget-keys  remove the local copies of the keys (after `make tf-destroy` deleted the service account)
set -euo pipefail
cd "$(dirname "$0")/.."
umask 077

ENV_FILE=.env
DOCKER_ENV_FILE=.env.docker
KESTRA_ENV_FILE=.env.kestra
SA_KEY_FILE=secrets/gcp-service-account.json
FLOW_FILE=docker/kestra/files/flows/main_pfas-trace.import.yml
# The image docker compose builds (docker-compose.yaml, service pfas-import)
LOCAL_IMAGE=pfas-import

info() { printf '==> %s\n' "$*"; }
fail() { printf 'Error: %s\n' "$*" >&2; exit 1; }

# value KEY [FILE...]: the value of KEY in the files (default .env), the last file wins; quotes removed.
value() {
  local key=$1 result="" file line
  shift
  [ $# -gt 0 ] || set -- "$ENV_FILE"
  for file in "$@"; do
    [ -f "$file" ] || continue
    line=$(grep -E "^${key}=" "$file" | tail -n 1 || true)
    [ -z "$line" ] || result=${line#*=}
  done
  result=${result%\"}
  printf '%s' "${result#\"}"
}

# The value the containers see: .env.docker overrides .env (as in docker compose).
container_value() { value "$1" "$ENV_FILE" "$DOCKER_ENV_FILE"; }

# set_value KEY VALUE: replace KEY's line in .env, or append it.
set_value() {
  local key=$1 val=$2 tmp
  tmp=$(mktemp)
  if grep -qE "^${key}=" "$ENV_FILE"; then
    awk -v key="$key" -v val="$val" 'index($0, key "=") == 1 { print key "=" val; next } { print }' \
      "$ENV_FILE" > "$tmp"
  else
    cat "$ENV_FILE" > "$tmp"
    printf '%s=%s\n' "$key" "$val" >> "$tmp"
  fi
  cat "$tmp" > "$ENV_FILE"
  rm -f "$tmp"
}

target() { value TARGET; }

require() {
  local tool
  for tool in "$@"; do
    command -v "$tool" >/dev/null || fail "$tool is not installed (see README, Setup)."
  done
}

step_env() {
  local pair src dst placeholders
  for pair in ".env.example:$ENV_FILE" ".env.docker.example:$DOCKER_ENV_FILE"; do
    src=${pair%%:*}
    dst=${pair#*:}
    if [ ! -f "$dst" ]; then
      cp "$src" "$dst"
      chmod 600 "$dst"
      info "Created $dst from $src"
    fi
  done

  placeholders=$(grep -nE '^[^#].*<[^>]+>' "$ENV_FILE" "$DOCKER_ENV_FILE" || true)
  if [ -n "$placeholders" ]; then
    printf 'Replace these <placeholders>, then run make setup again:\n%s\n' "$placeholders" >&2
    exit 1
  fi

  case "$(target)" in
    local | cloud) info "TARGET=$(target)" ;;
    *) fail "TARGET in $ENV_FILE must be local or cloud, not '$(target)'." ;;
  esac
}

gcloud_login() {
  if [ -z "$(gcloud auth list --filter=status:ACTIVE --format='value(account)' 2>/dev/null)" ]; then
    info "Logging in to gcloud (opens the browser)"
    gcloud auth login
  fi
  # Terraform uses the Application Default Credentials
  if ! gcloud auth application-default print-access-token >/dev/null 2>&1; then
    info "Logging in to the Application Default Credentials for Terraform (opens the browser)"
    gcloud auth application-default login
  fi
}

# Prints the project id; creates a project if GCP_PROJECT_ID is empty and saves its id to .env.
gcp_project() {
  local project
  project=$(value GCP_PROJECT_ID)
  if [ -z "$project" ]; then
    project="pfas-import-$(od -An -N3 -tx1 /dev/urandom | tr -d ' \n')"
    info "GCP_PROJECT_ID is empty: creating the project $project" >&2
    gcloud projects create "$project" --name="PFAS import" >&2
    set_value GCP_PROJECT_ID "$project"
  elif ! gcloud projects describe "$project" >/dev/null 2>&1; then
    fail "GCP project $project does not exist or you have no access. Empty GCP_PROJECT_ID creates a new one."
  fi
  printf '%s' "$project"
}

# Links a billing account unless the project has one: GCP_BILLING_ACCOUNT, else the only open account.
gcp_billing() {
  local project=$1 enabled account count
  enabled=$(gcloud billing projects describe "$project" --format='value(billingEnabled)')
  if [ "$(printf '%s' "$enabled" | tr '[:upper:]' '[:lower:]')" = true ]; then
    return
  fi

  account=$(value GCP_BILLING_ACCOUNT)
  if [ -z "$account" ]; then
    account=$(gcloud billing accounts list --filter=open=true --format='value(name.basename())')
    count=$(printf '%s\n' "$account" | grep -c . || true)
    [ "$count" = 1 ] || fail "Found $count open billing accounts; set GCP_BILLING_ACCOUNT in $ENV_FILE to the one \
for $project (gcloud billing accounts list)."
  fi
  info "Linking billing account $account to $project"
  gcloud billing projects link "$project" --billing-account="$account" >/dev/null
}

# The service account key for BigQuery in the Kestra tasks, reused while it is still a key of the account.
sa_key() {
  local sa=$1 project=$2 key_id
  if [ -f "$SA_KEY_FILE" ]; then
    if grep -q '"type": *"authorized_user"' "$SA_KEY_FILE"; then
      info "Using the gcloud login in $SA_KEY_FILE instead of a service account key"
      return
    fi
    key_id=$(sed -n 's/.*"private_key_id": *"\([^"]*\)".*/\1/p' "$SA_KEY_FILE")
    if [ -n "$key_id" ] && gcloud iam service-accounts keys list --iam-account="$sa" --project="$project" \
      --managed-by=user --format='value(name.basename())' | grep -qx "$key_id"; then
      info "Reusing the service account key $SA_KEY_FILE"
      return
    fi
  fi

  info "Creating a key for $sa in $SA_KEY_FILE"
  mkdir -p "$(dirname "$SA_KEY_FILE")"
  gcloud iam service-accounts keys create "$SA_KEY_FILE" --iam-account="$sa" --project="$project" ||
    fail "Could not create the key; your organisation may block service account keys. See README, \
Cloud > Credentials, for using your own login instead."
}

# The HMAC key for the buckets (S3 API), in .env. Reused while it is active and belongs to the account.
hmac_key() {
  local sa=$1 project=$2 access_id secret state email created
  access_id=$(value GCS_HMAC_ACCESS_ID)
  if [ -n "$access_id" ] && [ -n "$(value GCS_HMAC_SECRET)" ]; then
    read -r state email <<<"$(gcloud storage hmac describe "$access_id" --project="$project" \
      --format='value(state,serviceAccountEmail)' 2>/dev/null || true)"
    if [ "$state" = ACTIVE ] && [ "$email" = "$sa" ]; then
      info "Reusing the HMAC key $access_id"
      return
    fi
  fi

  info "Creating an HMAC key for $sa (GCS_HMAC_* in $ENV_FILE)"
  created=$(gcloud storage hmac create "$sa" --project="$project" --format='value(metadata.accessId,secret)')
  read -r access_id secret <<<"$created"
  [ -n "$access_id" ] && [ -n "$secret" ] || fail "gcloud storage hmac create returned no key."
  set_value GCS_HMAC_ACCESS_ID "$access_id"
  set_value GCS_HMAC_SECRET "$secret"
}

step_cloud() {
  local project sa
  if [ "$(target)" != cloud ]; then
    info "TARGET=local: no cloud setup"
    return
  fi
  require gcloud terraform

  gcloud_login
  project=$(gcp_project)
  gcp_billing "$project"

  # New make processes, so they read GCP_PROJECT_ID from .env even if it was just written
  make --no-print-directory tf-init
  make --no-print-directory tf-apply

  sa=$(terraform -chdir=infra output -raw service_account_email)
  sa_key "$sa" "$project"
  hmac_key "$sa" "$project"
}

step_secrets() {
  local tmp
  tmp=$(mktemp)
  secret() {
    if [ -n "$2" ]; then
      printf 'SECRET_%s=%s\n' "$1" "$(printf '%s' "$2" | base64 | tr -d '\n')" >> "$tmp"
    fi
  }
  secret PFAS_POSTGRES_PASSWORD "$(container_value POSTGRES_PASSWORD)"
  secret PFAS_S3_SECRET_KEY "$(container_value S3_SECRET_KEY)"
  secret PFAS_GCS_HMAC_SECRET "$(value GCS_HMAC_SECRET)"
  if [ -f "$SA_KEY_FILE" ]; then
    secret PFAS_GCP_SA_KEY "$(cat "$SA_KEY_FILE")"
  fi
  cat "$tmp" > "$KESTRA_ENV_FILE"
  rm -f "$tmp"
  chmod 600 "$KESTRA_ENV_FILE"
}

step_kv() {
  local port auth namespace url code i key val
  require curl
  port=$(container_value KESTRA_PORT)
  auth="$(value KESTRA_UI_USER):$(value KESTRA_UI_PASSWORD)"
  namespace=$(sed -n 's/^namespace: *//p' "$FLOW_FILE")
  url="http://localhost:${port:-8080}/api/v1/main/namespaces/$namespace/kv"

  info "Waiting for Kestra on port ${port:-8080}"
  for i in $(seq 1 90); do
    code=$(curl -s -o /dev/null -w '%{http_code}' -u "$auth" "$url" || true)
    case "$code" in
      200) break ;;
      401 | 403) fail "Kestra rejects KESTRA_UI_USER/KESTRA_UI_PASSWORD from $ENV_FILE." ;;
    esac
    [ "$i" -lt 90 ] || fail "Kestra did not answer within 3 minutes (docker compose logs kestra)."
    sleep 2
  done

  info "Writing the flow settings to the KV store of namespace $namespace"
  kv() {
    key=$1
    val=${2//\\/\\\\}
    val=${val//\"/\\\"}
    if [ -n "$val" ]; then
      # Kestra 1.1 wants text/plain; the body is a JSON value, quoted so that it is stored as a STRING
      curl -fsS -o /dev/null -X PUT -u "$auth" -H 'Content-Type: text/plain' \
        --data-binary "\"$val\"" "$url/$key" || fail "Could not write $key to the KV store."
    else
      # Mirror .env exactly: an empty value removes a stale key
      curl -s -o /dev/null -X DELETE -u "$auth" "$url/$key" || true
    fi
  }
  kv TARGET "$(target)"
  kv IMAGE "$LOCAL_IMAGE"
  kv PULL_POLICY NEVER
  kv NETWORK development
  kv S3_ENDPOINT_URL "$(container_value S3_ENDPOINT_URL)"
  kv S3_RAW_BUCKET "$(container_value S3_RAW_BUCKET)"
  kv S3_IMPORT_BUCKET "$(container_value S3_IMPORT_BUCKET)"
  kv POSTGRES_HOST "$(container_value POSTGRES_HOST)"
  kv POSTGRES_PORT "$(container_value POSTGRES_PORT)"
  kv POSTGRES_DB "$(container_value POSTGRES_DB)"
  kv POSTGRES_USER "$(container_value POSTGRES_USER)"
  kv S3_ACCESS_KEY "$(container_value S3_ACCESS_KEY)"
  kv GCP_PROJECT_ID "$(value GCP_PROJECT_ID)"
  kv BIGQUERY_DATASET "$(value BIGQUERY_DATASET)"
  kv BIGQUERY_LOCATION "$(value BIGQUERY_LOCATION)"
  kv GCS_HMAC_ACCESS_ID "$(value GCS_HMAC_ACCESS_ID)"
  info "Kestra is ready: http://localhost:${port:-8080} (flow $namespace.import)"
}

step_forget_keys() {
  rm -f "$SA_KEY_FILE"
  if [ -f "$ENV_FILE" ]; then
    set_value GCS_HMAC_ACCESS_ID ""
    set_value GCS_HMAC_SECRET ""
  fi
  info "Removed the local copies of the deleted service account's keys"
}

case "${1:-}" in
  env) step_env ;;
  cloud) step_cloud ;;
  secrets) step_secrets ;;
  kv) step_kv ;;
  forget-keys) step_forget_keys ;;
  *) fail "usage: $0 env|cloud|secrets|kv|forget-keys" ;;
esac
