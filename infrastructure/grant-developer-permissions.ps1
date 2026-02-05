# ============================================================================
# Grant Full Developer Permissions to mattrichcreek IAM User
# ============================================================================
#
# Creates THREE policies to stay under AWS character limits:
#   - DeveloperAccess-Core: Lambda, Step Functions, CloudFormation, API Gateway, Logs, Events
#   - DeveloperAccess-Services: S3, DynamoDB, Cognito, Amplify, Secrets Manager, VPC, etc.
#   - DeveloperAccess-IAM: IAM roles, policies, instance profiles (for building programs)
#
# Run as IAM administrator with iam:CreatePolicy and iam:AttachUserPolicy
#
# Usage: .\grant-developer-permissions.ps1
# ============================================================================

$ErrorActionPreference = "Stop"

$USER_NAME = "mattrichcreek"
$ACCOUNT_ID = "087243890715"

$policies = @(
    @{
        Name = "DeveloperAccess-Core"
        File = "iam-developer-policy-1.json"
        Desc = "Core: Lambda, Step Functions, CloudFormation, API Gateway, Logs, Events"
    },
    @{
        Name = "DeveloperAccess-Services"
        File = "iam-developer-policy-2.json"
        Desc = "Services: S3, DynamoDB, Cognito, Amplify, Secrets Manager, VPC, CloudWatch"
    },
    @{
        Name = "DeveloperAccess-IAM"
        File = "iam-developer-policy-3.json"
        Desc = "IAM: Create/manage roles and policies for your applications"
    }
)

Write-Host ""
Write-Host "============================================================================" -ForegroundColor Cyan
Write-Host "  AWS Developer Permissions Setup (3 Policies)" -ForegroundColor Cyan
Write-Host "============================================================================" -ForegroundColor Cyan
Write-Host ""

foreach ($policy in $policies) {
    $policyArn = "arn:aws:iam::${ACCOUNT_ID}:policy/$($policy.Name)"

    Write-Host "Processing: $($policy.Name)" -ForegroundColor Yellow

    # Check if policy exists
    $exists = $false
    try {
        $null = aws iam get-policy --policy-arn $policyArn 2>&1
        if ($LASTEXITCODE -eq 0) { $exists = $true }
    } catch { }

    if ($exists) {
        Write-Host "  Updating existing policy..." -ForegroundColor Gray

        # Delete oldest non-default version if needed
        $versions = aws iam list-policy-versions --policy-arn $policyArn --query "Versions[?IsDefaultVersion==``false``].VersionId" --output text 2>$null
        if ($versions -and $versions -ne "None" -and $versions.Trim()) {
            $versionArray = $versions.Trim() -split "\s+"
            if ($versionArray.Count -ge 4) {
                $oldest = $versionArray[-1]
                Write-Host "  Removing old version: $oldest" -ForegroundColor Gray
                aws iam delete-policy-version --policy-arn $policyArn --version-id $oldest 2>$null
            }
        }

        aws iam create-policy-version `
            --policy-arn $policyArn `
            --policy-document "file://$($policy.File)" `
            --set-as-default 2>$null
    } else {
        Write-Host "  Creating new policy..." -ForegroundColor Gray
        aws iam create-policy `
            --policy-name $policy.Name `
            --policy-document "file://$($policy.File)" `
            --description $policy.Desc 2>$null
    }

    # Attach to user if not already
    $attached = aws iam list-attached-user-policies --user-name $USER_NAME `
        --query "AttachedPolicies[?PolicyArn=='$policyArn'].PolicyArn" --output text 2>$null

    if (-not $attached -or $attached -eq "None") {
        Write-Host "  Attaching to user..." -ForegroundColor Gray
        aws iam attach-user-policy --user-name $USER_NAME --policy-arn $policyArn
    } else {
        Write-Host "  Already attached." -ForegroundColor Gray
    }

    Write-Host "  Done!" -ForegroundColor Green
    Write-Host ""
}

Write-Host "============================================================================" -ForegroundColor Green
Write-Host "  SUCCESS! All 3 policies attached to $USER_NAME" -ForegroundColor Green
Write-Host "============================================================================" -ForegroundColor Green
Write-Host ""
Write-Host "Full developer permissions now include:" -ForegroundColor White
Write-Host "  - Lambda, Step Functions, EventBridge" -ForegroundColor Gray
Write-Host "  - API Gateway, Amplify, Cognito" -ForegroundColor Gray
Write-Host "  - S3, DynamoDB, Secrets Manager, SSM, KMS" -ForegroundColor Gray
Write-Host "  - CloudFormation, CloudWatch, X-Ray" -ForegroundColor Gray
Write-Host "  - IAM (roles/policies for your programs)" -ForegroundColor Gray
Write-Host "  - VPC, SNS, SQS" -ForegroundColor Gray
Write-Host ""
Write-Host "Next: Create the Step Functions state machine:" -ForegroundColor Yellow
Write-Host ""
Write-Host "  cd C:\SFTPProgram\infrastructure" -ForegroundColor Cyan
Write-Host "  aws stepfunctions create-state-machine ``" -ForegroundColor Cyan
Write-Host "    --name 'hacienda-hcm-pipeline-prod' ``" -ForegroundColor Cyan
Write-Host "    --definition file://statemachine/pipeline-resolved.json ``" -ForegroundColor Cyan
Write-Host "    --role-arn 'arn:aws:iam::087243890715:role/hacienda-stepfunctions-role-prod' ``" -ForegroundColor Cyan
Write-Host "    --region us-east-1" -ForegroundColor Cyan
Write-Host ""
