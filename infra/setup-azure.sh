#!/usr/bin/env bash
# Set up Azure for the Decision-1 code standards workflow. Safe to re-run.
#
# Creates (or reuses):
#   1. A Microsoft-Decision-1 deployment on an existing Foundry (AI Services) resource
#   2. A user-assigned managed identity for GitHub Actions
#   3. "Cognitive Services User" for that identity on the Foundry resource
#   4. Federated credentials so workflows in GITHUB_REPO can sign in with OIDC (no secrets)
#   5. GitHub repository variables used by .github/workflows/code-standards.yml
#
# Requirements: az CLI (signed in), gh CLI (signed in with repo admin), an existing Foundry
# resource in a region that offers Microsoft-Decision-1.
#
# Usage:
#   SUBSCRIPTION=<sub id or name> RESOURCE_GROUP=<rg> FOUNDRY_ACCOUNT=<resource name> \
#   GITHUB_REPO=<owner/repo> ./infra/setup-azure.sh

set -euo pipefail

: "${SUBSCRIPTION:?Set SUBSCRIPTION}"
: "${RESOURCE_GROUP:?Set RESOURCE_GROUP}"
: "${FOUNDRY_ACCOUNT:?Set FOUNDRY_ACCOUNT}"
: "${GITHUB_REPO:?Set GITHUB_REPO (owner/repo)}"

DEPLOYMENT_NAME="${DEPLOYMENT_NAME:-decision-1}"
MODEL_NAME="${MODEL_NAME:-Microsoft-Decision-1}"
MODEL_VERSION="${MODEL_VERSION:-1}"
MODEL_FORMAT="${MODEL_FORMAT:-Microsoft}"
SKU_NAME="${SKU_NAME:-DataZoneStandard}"
SKU_CAPACITY="${SKU_CAPACITY:-10}"       # each unit is roughly 1 request per minute
IDENTITY_NAME="${IDENTITY_NAME:-id-decision-standards-gh}"
SET_GITHUB_VARIABLES="${SET_GITHUB_VARIABLES:-true}"

log() { printf '\n==> %s\n' "$*"; }

az account set --subscription "$SUBSCRIPTION"
TENANT_ID=$(az account show --query tenantId -o tsv)
ACCOUNT_ID=$(az cognitiveservices account show -g "$RESOURCE_GROUP" -n "$FOUNDRY_ACCOUNT" --query id -o tsv)
LOCATION=$(az cognitiveservices account show -g "$RESOURCE_GROUP" -n "$FOUNDRY_ACCOUNT" --query location -o tsv)
ENDPOINT="https://${FOUNDRY_ACCOUNT}.cognitiveservices.azure.com"

log "Model deployment $DEPLOYMENT_NAME ($MODEL_NAME v$MODEL_VERSION, $SKU_NAME x$SKU_CAPACITY)"
if az cognitiveservices account deployment show -g "$RESOURCE_GROUP" -n "$FOUNDRY_ACCOUNT" \
  --deployment-name "$DEPLOYMENT_NAME" -o none 2>/dev/null; then
  echo "Deployment already exists."
else
  az cognitiveservices account deployment create -g "$RESOURCE_GROUP" -n "$FOUNDRY_ACCOUNT" \
    --deployment-name "$DEPLOYMENT_NAME" --model-name "$MODEL_NAME" --model-version "$MODEL_VERSION" \
    --model-format "$MODEL_FORMAT" --sku-name "$SKU_NAME" --sku-capacity "$SKU_CAPACITY" -o none
fi

log "Managed identity $IDENTITY_NAME"
if ! az identity show -g "$RESOURCE_GROUP" -n "$IDENTITY_NAME" -o none 2>/dev/null; then
  az identity create -g "$RESOURCE_GROUP" -n "$IDENTITY_NAME" -l "$LOCATION" -o none
fi
CLIENT_ID=$(az identity show -g "$RESOURCE_GROUP" -n "$IDENTITY_NAME" --query clientId -o tsv)
PRINCIPAL_ID=$(az identity show -g "$RESOURCE_GROUP" -n "$IDENTITY_NAME" --query principalId -o tsv)

log "Role assignment: Cognitive Services User on $FOUNDRY_ACCOUNT"
# Filter by principalId instead of --assignee, which needs a Graph lookup that
# fails for a few minutes after a new managed identity is created.
existing=$(az role assignment list --scope "$ACCOUNT_ID" --role "Cognitive Services User" \
  --query "length([?principalId=='$PRINCIPAL_ID'])" -o tsv)
if [ "$existing" = "0" ]; then
  az role assignment create --assignee-object-id "$PRINCIPAL_ID" --assignee-principal-type ServicePrincipal \
    --role "Cognitive Services User" --scope "$ACCOUNT_ID" -o none
else
  echo "Role assignment already exists."
fi

log "Federated credentials for $GITHUB_REPO"
# Repositories created, renamed or transferred after 2026-07-15 default to an immutable subject
# (repo:owner@<owner_id>/repo@<repo_id>:...), so ask GitHub for the exact prefix.
if [ "$(gh api "repos/${GITHUB_REPO}/actions/oidc/customization/sub" --jq .use_default)" != "true" ]; then
  echo "This repository uses a custom OIDC subject template. Create the federated credentials" >&2
  echo "manually with subjects that match that template." >&2
  exit 1
fi
SUB_PREFIX=$(gh api "repos/${GITHUB_REPO}/actions/oidc/customization/sub" --jq '.sub_claim_prefix // empty')
SUB_PREFIX="${SUB_PREFIX:-repo:${GITHUB_REPO}}"
echo "OIDC subject prefix: $SUB_PREFIX"

add_federated_credential() {
  local name="$1" subject="$2" current
  current=$(az identity federated-credential show -g "$RESOURCE_GROUP" --identity-name "$IDENTITY_NAME" \
    -n "$name" --query subject -o tsv 2>/dev/null || true)
  if [ "$current" = "$subject" ]; then
    echo "Federated credential $name already exists."
    return
  fi
  local action=create
  [ -n "$current" ] && action=update
  az identity federated-credential "$action" -g "$RESOURCE_GROUP" --identity-name "$IDENTITY_NAME" -n "$name" \
    --issuer "https://token.actions.githubusercontent.com" --subject "$subject" \
    --audiences "api://AzureADTokenExchange" -o none
  echo "Federated credential $name: $subject ($action)"
}
add_federated_credential "github-pull-request" "${SUB_PREFIX}:pull_request"
add_federated_credential "github-main" "${SUB_PREFIX}:ref:refs/heads/main"

if [ "$SET_GITHUB_VARIABLES" = "true" ]; then
  log "GitHub repository variables on $GITHUB_REPO"
  gh variable set AZURE_CLIENT_ID --repo "$GITHUB_REPO" --body "$CLIENT_ID"
  gh variable set AZURE_TENANT_ID --repo "$GITHUB_REPO" --body "$TENANT_ID"
  gh variable set DECISION_ENDPOINT --repo "$GITHUB_REPO" --body "$ENDPOINT"
  gh variable set DECISION_DEPLOYMENT --repo "$GITHUB_REPO" --body "$DEPLOYMENT_NAME"
fi

cat <<EOF

Done. Values used by the workflow:
  AZURE_CLIENT_ID     = $CLIENT_ID
  AZURE_TENANT_ID     = $TENANT_ID
  DECISION_ENDPOINT   = $ENDPOINT
  DECISION_DEPLOYMENT = $DEPLOYMENT_NAME

Role assignments can take a few minutes to apply. If the first run gets HTTP 401 or 403, re-run it shortly.
EOF
