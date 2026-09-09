param([ValidateRange(1, 90)][int]$LookbackDays = 14)

# Read-only snapshot: does not alter updates, event logs, services or power settings.
# Raw event messages and device/user identifiers are deliberately excluded.
$ErrorActionPreference = 'Stop'
function Unavailable($problem) {
    [ordered]@{
        status = 'unavailable'
        error_type = $problem.Exception.GetType().Name
        error_code = $problem.FullyQualifiedErrorId
        hresult = $problem.Exception.HResult
        meaning = 'No health conclusion is possible from this unavailable check.'
    }
}
$since = (Get-Date).AddDays(-$LookbackDays)
$snapshot = [ordered]@{
    captured_at = (Get-Date).ToString('o')
    lookback_start = $since.ToString('o')
    read_only = $true
}
try {
    $os = Get-CimInstance -ClassName Win32_OperatingSystem -OperationTimeoutSec 10
    $snapshot.last_boot = [ordered]@{status = 'available'; time = $os.LastBootUpTime.ToString('o')}
} catch { $snapshot.last_boot = Unavailable $_ }
try {
    $eventIds = @(41, 6008, 1074, 6005, 6006, 1001)
    $events = @(Get-WinEvent -FilterHashtable @{LogName = 'System'; Id = $eventIds; StartTime = $since} -MaxEvents 200)
    $safeEvents = foreach ($event in $events) {
        $fields = @{}
        if ($event.Id -eq 41) {
            [xml]$xml = $event.ToXml()
            foreach ($field in $xml.Event.EventData.Data) {
                if ($field.Name -in @('BugcheckCode', 'SleepInProgress', 'PowerButtonTimestamp', 'BootAppStatus', 'Checkpoint', 'LongPowerButtonPressDetected')) {
                    $fields[[string]$field.Name] = [string]$field.'#text'
                }
            }
        }
        if ($event.Id -eq 1074) {
            [xml]$xml = $event.ToXml()
            foreach ($field in $xml.Event.EventData.Data) {
                if ($field.Name -eq 'param1') {
                    $baseName = [System.IO.Path]::GetFileName(([string]$field.'#text').Split('(')[0].Trim())
                    if ($baseName -match '^[a-zA-Z0-9_.-]+\.exe$') { $fields['process_basename'] = $baseName }
                }
                if ($field.Name -eq 'param4' -and [string]$field.'#text' -match '^0x[0-9a-fA-F]+$') {
                    $fields['reason_code'] = [string]$field.'#text'
                }
            }
        }
        [ordered]@{time = $event.TimeCreated.ToString('o'); id = $event.Id; provider = $event.ProviderName; level = $event.Level; diagnostic_fields = $fields}
    }
    $snapshot.system_events = [ordered]@{status = 'available'; returned_count = $events.Count; limit = 200; potentially_truncated = ($events.Count -eq 200); events = @($safeEvents)}
} catch {
    if ($_.FullyQualifiedErrorId -like 'NoMatchingEventsFound*') {
        $snapshot.system_events = [ordered]@{status = 'available_no_matching_events'; returned_count = 0; events = @()}
    } else { $snapshot.system_events = Unavailable $_ }
}
try {
    $zones = @(Get-CimInstance -Namespace 'root/wmi' -ClassName MSAcpi_ThermalZoneTemperature -OperationTimeoutSec 10)
    $temperatures = @($zones | ForEach-Object { [math]::Round(($_.CurrentTemperature / 10.0) - 273.15, 1) })
    $snapshot.temperature = [ordered]@{
        status = if ($zones.Count) {'available_acpi_zone_only'} else {'unavailable_no_sensor'}
        degrees_celsius = $temperatures
        limitation = 'ACPI thermal zones are not guaranteed to measure CPU/GPU temperature. A current reading cannot establish historical overheating.'
    }
} catch { $snapshot.temperature = Unavailable $_ }
try {
    $services = @(Get-Service -Name wuauserv, UsoSvc, BITS | Select-Object Name, Status, StartType)
    $snapshot.update_services = [ordered]@{status = 'available'; services = @($services | ForEach-Object { [ordered]@{name = $_.Name; state = [string]$_.Status; start_type = [string]$_.StartType} })}
} catch { $snapshot.update_services = Unavailable $_ }
try {
    $policyPath = 'HKLM:\SOFTWARE\Policies\Microsoft\Windows\WindowsUpdate\AU'
    if (Test-Path -LiteralPath $policyPath) {
        $policy = Get-ItemProperty -LiteralPath $policyPath
        $safePolicy = [ordered]@{}
        foreach ($key in @('NoAutoUpdate', 'AUOptions', 'NoAutoRebootWithLoggedOnUsers', 'AlwaysAutoRebootAtScheduledTime', 'ScheduledInstallDay', 'ScheduledInstallTime')) {
            if ($null -ne $policy.$key) { $safePolicy[$key] = [int]$policy.$key }
        }
        $snapshot.automatic_update_policy = [ordered]@{status = 'available'; explicit_policy_values = $safePolicy; limitation = 'Missing policy values mean not explicitly configured at this registry location, not disabled.'}
    } else {
        $snapshot.automatic_update_policy = [ordered]@{status = 'not_explicitly_configured_at_checked_key'; limitation = 'This does not establish whether automatic updates are enabled or disabled through all settings.'}
    }
} catch { $snapshot.automatic_update_policy = Unavailable $_ }
try {
    $indicators = [ordered]@{}
    $rebootKeys = [ordered]@{
        windows_update_reboot_required = 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\WindowsUpdate\Auto Update\RebootRequired'
        servicing_reboot_pending = 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Component Based Servicing\RebootPending'
    }
    foreach ($item in $rebootKeys.GetEnumerator()) {
        $indicators[$item.Key] = Test-Path -LiteralPath $item.Value -ErrorAction Stop
    }
    $snapshot.pending_reboot_indicators = [ordered]@{status = 'available'; indicators = $indicators; limitation = 'These indicators are not an exhaustive reboot requirement test.'}
} catch { $snapshot.pending_reboot_indicators = Unavailable $_ }
try {
    $updateEvents = @(Get-WinEvent -FilterHashtable @{LogName = 'Microsoft-Windows-WindowsUpdateClient/Operational'; StartTime = $since} -MaxEvents 80)
    $snapshot.update_events = [ordered]@{
        status = 'available'
        returned_count = $updateEvents.Count
        limit = 80
        potentially_truncated = ($updateEvents.Count -eq 80)
        provider = 'Microsoft-Windows-WindowsUpdateClient'
        events = @($updateEvents | ForEach-Object { [ordered]@{time = $_.TimeCreated.ToString('o'); id = $_.Id; level = $_.Level} })
    }
} catch {
    if ($_.FullyQualifiedErrorId -like 'NoMatchingEventsFound*') {
        $snapshot.update_events = [ordered]@{status = 'available_no_matching_events'; returned_count = 0; events = @()}
    } else { $snapshot.update_events = Unavailable $_ }
}
$snapshot.interpretation = 'A restart or thermal cause cannot be inferred from authentication errors. Event 41/6008 establishes an unclean shutdown indication, not its root cause. Interpret the independent system evidence separately.'
$snapshot | ConvertTo-Json -Depth 12
