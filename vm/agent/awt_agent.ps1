<#
.SYNOPSIS
    In-guest agent for the windhawk-agent-testbench self-hosted VM backend.

.DESCRIPTION
    This script runs INSIDE the Windows VM. It speaks the shared-folder protocol
    documented in docs/vm-protocol.md:

        host -> guest   <share>\job.json
        guest -> host   <share>\outbox\heartbeat.json
                        <share>\outbox\result.json
                        <share>\outbox\logs\*.log
                        <share>\outbox\screenshot.png

    It is deliberately dependency-free: Windows PowerShell 5.1, which every
    supported guest already has. It installs nothing, downloads nothing and
    contains no credentials.

    Typical setup - run it once at logon (Task Scheduler "At log on", or the
    Startup folder) so the VM is ready as soon as it boots:

        powershell -NoProfile -ExecutionPolicy Bypass -File S:\awt\awt_agent.ps1 `
            -Share S:\awt -Once

    With -Once it handles a single job and exits (what the backend expects).
    Without -Once it keeps watching, which is useful when you drive the VM by
    hand.

.NOTES
    The host stages everything the agent needs: mod.dll and host.exe are
    cross-compiled on the host machine (MinGW) and copied into <share>\inbox.
    If they are missing, the agent says so in result.json - it never pretends a
    run happened.
#>
[CmdletBinding()]
param(
    [string]$Share = 'S:\awt',
    [switch]$Once,
    [int]$PollSeconds = 2,
    [int]$MaxWaitSeconds = 3600,
    [int]$HostTimeoutSlackSeconds = 180
)

$ErrorActionPreference = 'Continue'
$AgentVersion = 'awt-vm-agent/1'

function Write-JsonAtomic {
    param([string]$Path, [object]$Object)
    $tmp = "$Path.tmp"
    $dir = Split-Path -Parent $Path
    if (-not (Test-Path -LiteralPath $dir)) {
        New-Item -ItemType Directory -Force -Path $dir | Out-Null
    }
    $Object | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $tmp -Encoding UTF8
    Move-Item -LiteralPath $tmp -Destination $Path -Force
}

function Read-JsonFile {
    param([string]$Path)
    if (-not (Test-Path -LiteralPath $Path)) { return $null }
    try {
        return Get-Content -LiteralPath $Path -Raw -Encoding UTF8 | ConvertFrom-Json
    } catch {
        return $null   # the host may still be writing; the caller retries
    }
}

function Get-GuestEnvironment {
    # Everything this guest can honestly say about itself. The host merges these
    # values into run.environment, and they win over the configuration file.
    $env_info = [ordered]@{
        backend          = 'self-hosted-vm'
        os               = 'Windows'
        os_version       = $null
        edition          = $null
        build_number     = $null
        product_type     = $null
        shell_profile    = 'unknown'
        virtualization   = $null
        vm_used          = $true
        arch             = $env:PROCESSOR_ARCHITECTURE
        hostname         = $env:COMPUTERNAME
        collected_by     = $AgentVersion
        collected_at     = (Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ')
        notes            = @()
    }
    try {
        $cv = Get-ItemProperty -Path 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion' -ErrorAction Stop
        $env_info.edition = $cv.ProductName
        if ($cv.DisplayVersion) { $env_info.edition = "$($cv.ProductName) $($cv.DisplayVersion)" }
        $build = $cv.CurrentBuildNumber
        if (-not $build) { $build = $cv.CurrentBuild }
        if ($build) { $env_info.build_number = [int]$build }
        $env_info.os_version = "$([Environment]::OSVersion.Version.Major).$([Environment]::OSVersion.Version.Minor).$build"
        $installationType = $cv.InstallationType
        if ($installationType -eq 'Client') {
            $env_info.product_type = 'client'
            $env_info.shell_profile = 'client'
        } elseif ($installationType -like 'Server*') {
            $env_info.product_type = 'server'
            $env_info.shell_profile = 'server'
            $env_info.notes += 'this guest is Windows Server: shell-related outcomes are indicative'
        }
    } catch {
        $env_info.notes += 'the Windows version registry key could not be read'
    }
    try {
        $os = Get-CimInstance -ClassName Win32_OperatingSystem -ErrorAction Stop
        $env_info.os_version = $os.Version
        if ($os.ProductType -eq 1) {
            $env_info.product_type = 'client'; $env_info.shell_profile = 'client'
        } elseif ($os.ProductType -eq 2) {
            $env_info.product_type = 'domain-controller'; $env_info.shell_profile = 'server'
        } elseif ($os.ProductType -eq 3) {
            $env_info.product_type = 'server'; $env_info.shell_profile = 'server'
        }
    } catch {
        $env_info.notes += 'Win32_OperatingSystem could not be queried'
    }
    try {
        $cs = Get-CimInstance -ClassName Win32_ComputerSystem -ErrorAction Stop
        $env_info.virtualization = $cs.HypervisorPresent
        if ($cs.Manufacturer) { $env_info.notes += "guest hypervisor: $($cs.Manufacturer) $($cs.Model)" }
    } catch { }
    return $env_info
}

function Start-Heartbeat {
    param([string]$Outbox)
    $beat = Get-GuestEnvironment
    $payload = [ordered]@{
        protocol      = 1
        agent_version = $AgentVersion
        heartbeat_at  = (Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ')
        share         = $Share
        powershell    = $PSVersionTable.PSVersion.ToString()
        environment   = $beat
    }
    Write-JsonAtomic -Path (Join-Path $Outbox 'heartbeat.json') -Object $payload
    return $beat
}

function Invoke-Screenshot {
    param([string]$Path)
    try {
        Add-Type -AssemblyName System.Windows.Forms
        Add-Type -AssemblyName System.Drawing
        $bounds = [System.Windows.Forms.SystemInformation]::VirtualScreen
        if ($bounds.Width -le 0 -or $bounds.Height -le 0) { return "no desktop bounds reported" }
        $bmp = New-Object System.Drawing.Bitmap $bounds.Width, $bounds.Height
        $g = [System.Drawing.Graphics]::FromImage($bmp)
        $g.CopyFromScreen($bounds.Location, [System.Drawing.Point]::Empty, $bounds.Size)
        $bmp.Save($Path, [System.Drawing.Imaging.ImageFormat]::Png)
        $g.Dispose(); $bmp.Dispose()
        if ((Get-Item -LiteralPath $Path).Length -lt 200) {
            Remove-Item -LiteralPath $Path -Force
            return 'the capture was smaller than 200 bytes and was discarded'
        }
        return $null
    } catch {
        return "screenshot failed: $($_.Exception.Message)"
    }
}

function Invoke-AwtJob {
    param([object]$Job, [string]$Outbox, [object]$Environment)

    $logs = Join-Path $Outbox 'logs'
    New-Item -ItemType Directory -Force -Path $logs | Out-Null
    $inbox = Join-Path $Share 'inbox'
    $modLog = Join-Path $logs 'mod.log'
    $stubLog = Join-Path $logs 'stub.log'
    $hostOutputLog = Join-Path $logs 'host-output.log'
    $storage = Join-Path $Outbox 'storage'
    New-Item -ItemType Directory -Force -Path $storage | Out-Null

    $result = [ordered]@{
        protocol    = 1
        agent       = $AgentVersion
        job_id      = $Job.job_id
        started     = (Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ')
        status      = 'not_executed'
        reason      = $null
        environment = $Environment
        build       = [ordered]@{ ok = $null; errors = @(); warnings = @() }
        run         = [ordered]@{
            backend     = 'self-hosted-vm'
            target      = $Job.target
            mode        = $Job.mode
            host_output = ''
            mod_log     = ''
            stub_log    = ''
            exit_code   = $null
            timed_out   = $false
        }
        artifacts   = [ordered]@{ screenshot = $null; logs = @() }
    }

    $hostExe = Join-Path $inbox 'host.exe'
    $modDll = if ($Job.mod_dll) { Join-Path $inbox $Job.mod_dll } else { $null }
    if (-not (Test-Path -LiteralPath $hostExe)) {
        $result.reason = "host.exe was not staged in $inbox - the host backend could not cross-compile it"
        Write-JsonAtomic -Path (Join-Path $Outbox 'result.json') -Object $result
        return $result
    }
    if (-not $modDll -or -not (Test-Path -LiteralPath $modDll)) {
        $result.reason = "the mod DLL was not staged in $inbox (job asked for '$($Job.mod_dll)')"
        Write-JsonAtomic -Path (Join-Path $Outbox 'result.json') -Object $result
        return $result
    }

    $env:AWT_MOD_LOG = $modLog
    $env:AWT_STUB_LOG = $stubLog
    $env:AWT_STORAGE_DIR = $storage
    if (-not $env:AWT_SETTINGS_JSON) { $env:AWT_SETTINGS_JSON = '{}' }

    $seconds = [int]$Job.seconds
    if ($seconds -le 0) { $seconds = 6 }
    $mode = if ($Job.mode) { $Job.mode } else { 'auto' }
    $arguments = @('--mod', $modDll, '--mode', $mode, '--seconds', "$seconds")
    if ($Job.target) { $arguments += @('--target', $Job.target) }

    $result.status = 'executed'
    $timeoutMs = ($seconds + $HostTimeoutSlackSeconds) * 1000
    try {
        $proc = Start-Process -FilePath $hostExe -ArgumentList $arguments `
            -NoNewWindow -PassThru -RedirectStandardOutput $hostOutputLog `
            -RedirectStandardError (Join-Path $logs 'host-stderr.log')
        if (-not $proc.WaitForExit($timeoutMs)) {
            $result.run.timed_out = $true
            $result.reason = "host.exe did not exit within $timeoutMs ms and was killed"
            try { $proc.Kill() } catch { }
        }
        $result.run.exit_code = $proc.ExitCode
    } catch {
        $result.status = 'not_executed'
        $result.reason = "host.exe could not be started: $($_.Exception.Message)"
    }

    $result.run.host_output = (Get-Content -LiteralPath $hostOutputLog -Raw -ErrorAction SilentlyContinue)
    if (-not $result.run.host_output) { $result.run.host_output = '' }
    $result.run.mod_log = (Get-Content -LiteralPath $modLog -Raw -ErrorAction SilentlyContinue)
    if (-not $result.run.mod_log) { $result.run.mod_log = '' }
    $result.run.stub_log = (Get-Content -LiteralPath $stubLog -Raw -ErrorAction SilentlyContinue)
    if (-not $result.run.stub_log) { $result.run.stub_log = '' }
    $result.build.ok = $true    # the DLL arrived prebuilt from the host
    $result.artifacts.logs = @(Get-ChildItem -LiteralPath $logs -File | ForEach-Object { $_.Name })

    if ($Job.screenshot) {
        $shotPath = Join-Path $Outbox 'screenshot.png'
        $problem = Invoke-Screenshot -Path $shotPath
        if ($problem) {
            $result.artifacts.screenshot = $null
            $result.artifacts.screenshot_note = $problem
            if (-not $result.reason) { $result.reason = $problem }
        } else {
            $result.artifacts.screenshot = 'screenshot.png'
        }
    }

    $result.finished = (Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ')
    if ($result.status -eq 'executed' -and $result.run.host_output) {
        $result.status = 'completed'
    } elseif ($result.status -eq 'executed') {
        $result.status = 'completed'
        if (-not $result.reason) { $result.reason = 'host.exe produced no output' }
    }
    Write-JsonAtomic -Path (Join-Path $Outbox 'result.json') -Object $result
    return $result
}

function Watch-Share {
    $outbox = Join-Path $Share 'outbox'
    New-Item -ItemType Directory -Force -Path $outbox | Out-Null
    New-Item -ItemType Directory -Force -Path (Join-Path $outbox 'logs') | Out-Null
    $environmentInfo = Start-Heartbeat -Outbox $outbox
    Write-Host "$AgentVersion heartbeat written to $outbox (share: $Share)"

    $jobPath = Join-Path $Share 'job.json'
    $waited = 0
    while ($true) {
        $job = Read-JsonFile -Path $jobPath
        if ($job -and $job.protocol) {
            Write-Host "$AgentVersion job $($job.job_id): running"
            Invoke-AwtJob -Job $job -Outbox $outbox -Environment $environmentInfo | Out-Null
            Write-Host "$AgentVersion job $($job.job_id): result.json written"
            if ($Once) { return }
            # Consume the job so the next one is picked up cleanly.
            Remove-Item -LiteralPath $jobPath -Force -ErrorAction SilentlyContinue
            $waited = 0
            continue
        }
        Start-Sleep -Seconds $PollSeconds
        $waited += $PollSeconds
        # Refresh the heartbeat so the host can tell "booting" from "gone".
        if ($waited % 30 -eq 0) { Start-Heartbeat -Outbox $outbox | Out-Null }
        if ($waited -ge $MaxWaitSeconds) {
            Write-Host "$AgentVersion gave up after $MaxWaitSeconds s with no job.json"
            return
        }
    }
}

Watch-Share
