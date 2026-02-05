#!/bin/bash
# ============================================================================
# Grant Full Developer Permissions to mattrichcreek IAM User
# ============================================================================
#
# Creates TWO policies to stay under AWS 6144 character limit:
#   - DeveloperAccess-Core: Lambda, Step Functions, IAM, CloudFormation, API Gateway, Logs, Events
#   - DeveloperAccess-Services: S3, DynamoDB, Cognito, Amplify, Secrets Manager, SNS, SQS, etc.
#
# Run as IAM administrator with iam:CreatePolicy and iam:AttachUserPolicy
#
# Usage: ./grant-developer-permissions.sh
# ============================================================================

set -e

USER_NAME="mattrichcreek"
ACCOUNT_ID="087243890715"

echo ""
echo "============================================================================"
echo "  AWS Developer Permissions Setup"
echo "============================================================================"
echo ""

# Policy 1: Core
POLICY1_NAME="DeveloperAccess-Core"
POLICY1_ARN="arn:aws:iam::${ACCOUNT_ID}:policy/${POLICY1_NAME}"
POLICY1_FILE="iam-developer-policy-1.json"
POLICY1_DESC="Core dev access: Lambda, Step Functions, IAM roles, CloudFormation, API Gateway, Logs, Events"

# Policy 2: Services
POLICY2_NAME="DeveloperAccess-Services"
POLICY2_ARN="arn:aws:iam::${ACCOUNT_ID}:policy/${POLICY2_NAME}"
POLICY2_FILE="iam-developer-policy-2.json"
POLICY2_DESC="Service access: S3, DynamoDB, Cognito, Amplify, Secrets Manager, VPC, SNS, SQS, CloudWatch, KMS"

create_or_update_policy() {
    local name=$1
    local arn=$2
    local file=$3
    local desc=$4

    echo "Processing: $name"

    if aws iam get-policy --policy-arn "$arn" 2>/dev/null; then
        echo "  Policy exists, creating new version..."

        # Delete oldest version if at limit
        OLD_VER=$(aws iam list-policy-versions --policy-arn "$arn" \
            --query "Versions[?IsDefaultVersion==\`false\`].VersionId | [-1]" --output text 2>/dev/null)

        if [ -n "$OLD_VER" ] && [ "$OLD_VER" != "None" ]; then
            COUNT=$(aws iam list-policy-versions --policy-arn "$arn" \
                --query "length(Versions)" --output text)
            if [ "$COUNT" -ge 5 ]; then
                echo "  Deleting old version: $OLD_VER"
                aws iam delete-policy-version --policy-arn "$arn" --version-id "$OLD_VER"
            fi
        fi

        aws iam create-policy-version \
            --policy-arn "$arn" \
            --policy-document "file://$file" \
            --set-as-default
    else
        echo "  Creating new policy..."
        aws iam create-policy \
            --policy-name "$name" \
            --policy-document "file://$file" \
            --description "$desc"
    fi

    # Attach to user
    ATTACHED=$(aws iam list-attached-user-policies --user-name "$USER_NAME" \
        --query "AttachedPolicies[?PolicyArn=='$arn'].PolicyArn" --output text 2>/dev/null)

    if [ -z "$ATTACHED" ]; then
        echo "  Attaching to user..."
        aws iam attach-user-policy --user-name "$USER_NAME" --policy-arn "$arn"
    else
        echo "  Already attached."
    fi

    echo "  Done!"
    echo ""
}

create_or_update_policy "$POLICY1_NAME" "$POLICY1_ARN" "$POLICY1_FILE" "$POLICY1_DESC"
create_or_update_policy "$POLICY2_NAME" "$POLICY2_ARN" "$POLICY2_FILE" "$POLICY2_DESC"

echo "============================================================================"
echo "  SUCCESS! Both policies attached to $USER_NAME"
echo "============================================================================"
echo ""
echo "Permissions granted:"
echo "  - Lambda, Step Functions, EventBridge"
echo "  - API Gateway, Amplify, Cognito"
echo "  - S3, DynamoDB, Secrets Manager, SSM"
echo "  - CloudFormation, IAM (roles/policies)"
echo "  - CloudWatch, X-Ray, SNS, SQS, KMS, VPC"
echo ""
echo "Next: Create the Step Functions state machine:"
echo ""
echo "  cd C:\SFTPProgram\infrastructure"
echo "  aws stepfunctions create-state-machine \\"
echo "    --name 'hacienda-hcm-pipeline-prod' \\"
echo "    --definition file://statemachine/pipeline-resolved.json \\"
echo "    --role-arn 'arn:aws:iam::087243890715:role/hacienda-stepfunctions-role-prod' \\"
echo "    --region us-east-1"
echo ""
