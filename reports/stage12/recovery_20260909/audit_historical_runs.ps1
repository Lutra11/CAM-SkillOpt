param(
    [string]$OutputRoot = 'C:\CAM-SkillOpt\SkillOpt\outputs'
)

# Read-only audit. Emits aggregate evidence only; never emits prompts, credentials,
# raw trace contents, user identifiers or spreadsheet contents.
$ErrorActionPreference = 'Stop'
$authPattern = '(?i)invalid_refresh_token|refresh_token_reused|(?:status(?:\s+code)?\s*[:=]?\s*|HTTP[/\d. ]*\s*)401\b|\b401\s+Unauthorized|authentication failed|unauthorized|please (?:try )?(?:logging|signing) in again'
$networkPattern = '(?i)connection (?:reset|refused)|network is unreachable|name resolution|TLS handshake|connecterror'
$runPaths = @(
    'formal_cam/spreadsheetbench_terra_cam_full_seed42_w1_a1_fix1',
    'formal_cam/spreadsheetbench_terra_cam_full_seed42_w1_a1_fix2',
    'eval_only/cam_full_fix2_best_valid_seen_8_w1_a1',
    'eval_only/cam_full_fix2_best_valid_unseen_8_w1_a1',
    'formal_cam/spreadsheetbench_terra_cam_no_bootstrap_seed42_w1_a1_fix1',
    'formal_cam/spreadsheetbench_terra_cam_no_adaptive_budget_seed42_w1_a1_fix1',
    'formal_cam/spreadsheetbench_terra_cam_no_memory_seed42_w1_a1_fix1',
    'stage12_probe/spreadsheetbench_terra_cam_full_P0_w1_a1_fix1',
    'stage12_probe/spreadsheetbench_terra_cam_full_P1_w4_a1_fix2',
    'stage12_probe/spreadsheetbench_terra_cam_full_P2_w4_a2_fix2'
)
$runs = foreach ($relativeRun in $runPaths) {
    $runRoot = Join-Path $OutputRoot $relativeRun
    if (-not (Test-Path -LiteralPath $runRoot)) {
        [ordered]@{ run = $relativeRun; status = 'missing_directory' }
        continue
    }
    $files = @(Get-ChildItem -LiteralPath $runRoot -File -Recurse)
    $resultFiles = @($files | Where-Object Name -eq 'results.jsonl')
    $stages = foreach ($resultFile in $resultFiles) {
        $rows = @(Get-Content -LiteralPath $resultFile.FullName | Where-Object { $_.Trim() } | ForEach-Object { $_ | ConvertFrom-Json })
        $failures = @{}
        $derivedFailures = @{}
        foreach ($row in $rows) {
            $key = [string]$row.fail_reason
            if (-not $key) { $key = '(empty)' }
            if ($key -notmatch '^[a-zA-Z0-9_() -]{1,80}$') { $key = '(non-code reason redacted)' }
            $failures[$key] = 1 + $failures[$key]
            $failureType = if ([string]$row.error -match $authPattern) { 'infra_auth_error' }
                elseif ([string]$row.error -match $networkPattern) { 'infra_network_error' }
                elseif (-not $row.llm_ok) { 'llm_failure_unclassified_requires_raw_trace' }
                elseif (-not $row.code_ok) { 'code_failure' }
                elseif (-not $row.exec_ok) { 'execution_failure' }
                elseif ($row.hard -ne 1) { 'scored_task_failure' }
                else { 'scored_task_success' }
            $derivedFailures[$failureType] = 1 + $derivedFailures[$failureType]
        }
        [ordered]@{
            stage = if ($resultFile.DirectoryName.Length -gt $runRoot.Length) { $resultFile.DirectoryName.Substring($runRoot.Length + 1).Replace('\', '/') } else { '.' }
            result_rows = $rows.Count
            auth_error_rows = @($rows | Where-Object { [string]$_.error -match $authPattern }).Count
            network_error_rows = @($rows | Where-Object { [string]$_.error -match $networkPattern }).Count
            llm_ok = @($rows | Where-Object llm_ok -eq $true).Count
            code_ok = @($rows | Where-Object code_ok -eq $true).Count
            exec_ok = @($rows | Where-Object exec_ok -eq $true).Count
            recorded_hard_successes = @($rows | Where-Object hard -eq 1).Count
            fail_reason_counts = $failures
            derived_failure_type_counts = $derivedFailures
        }
    }
    $rawFiles = @($files | Where-Object Name -eq 'codex_raw.txt')
    $authRawCount = 0
    $authRawOccurrences = 0
    $signatureFiles = @{http_401 = 0; invalid_refresh_token = 0; refresh_token_reused = 0}
    foreach ($rawFile in $rawFiles) {
        $rawText = Get-Content -LiteralPath $rawFile.FullName -Raw
        $hits = [regex]::Matches($rawText, $authPattern).Count
        if ($hits -gt 0) { $authRawCount++ }
        $authRawOccurrences += $hits
        if ($rawText -match '\b401\b') { $signatureFiles.http_401++ }
        if ($rawText -match 'invalid_refresh_token') { $signatureFiles.invalid_refresh_token++ }
        if ($rawText -match 'refresh_token_reused') { $signatureFiles.refresh_token_reused++ }
    }
    $steps = @($files | Where-Object Name -eq 'step_record.json' | ForEach-Object { Get-Content -LiteralPath $_.FullName -Raw | ConvertFrom-Json })
    $summaryPath = Join-Path $runRoot 'summary.json'
    $analystCalls = $null
    if (Test-Path -LiteralPath $summaryPath) {
        $runSummary = Get-Content -LiteralPath $summaryPath -Raw | ConvertFrom-Json
        $analystCategories = @($runSummary.token_summary.PSObject.Properties | Where-Object Name -match 'analyst|reflect')
        if ($analystCategories.Count -gt 0) {
            $analystCalls = ($analystCategories.Value.calls | Measure-Object -Sum).Sum
        }
    }
    [ordered]@{
        run = $relativeRun
        summary_present = Test-Path -LiteralPath $summaryPath
        eval_summary_present = Test-Path -LiteralPath (Join-Path $runRoot 'eval_summary.json')
        results_files = $resultFiles.Count
        result_rows = ($stages.result_rows | Measure-Object -Sum).Sum
        auth_error_rows = ($stages.auth_error_rows | Measure-Object -Sum).Sum
        raw_trace_files = $rawFiles.Count
        auth_error_raw_files = $authRawCount
        auth_error_raw_occurrences = $authRawOccurrences
        auth_signature_file_counts = $signatureFiles
        conversation_files = @($files | Where-Object Name -eq 'conversation.json').Count
        steps = $steps.Count
        skip_no_patches_steps = @($steps | Where-Object action -eq 'skip_no_patches').Count
        patches = ($steps.n_patches | Measure-Object -Sum).Sum
        cam_gate_used_steps = @($steps | Where-Object cam_gate_used -eq $true).Count
        analyst_calls_reported = $analystCalls
        stages = @($stages)
    }
}
[ordered]@{
    captured_at = (Get-Date).ToString('o')
    audit_type = 'read_only_sanitized_historical_evidence'
    source_root = $OutputRoot
    auth_count_note = 'Counts match explicit authentication signatures in error fields and codex_raw.txt. Occurrences include repeated retries; they are not request counts. Null analyst_calls_reported means absent in summary, not proof of zero calls.'
    runs = @($runs)
} | ConvertTo-Json -Depth 12
