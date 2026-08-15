# iterate-harness Windows Installer (PowerShell)
# Usage: iex (Invoke-WebRequest -Uri 'https://raw.githubusercontent.com/jingzhao-l/iterate-harness/main/scripts/install.ps1')
#        or: powershell -ExecutionPolicy Bypass -File scripts/install.ps1

param(
    [switch]$FromSource,
    [switch]$Help
)

# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------
function Write-Info { Write-Host "[INFO]  $args" -ForegroundColor Cyan }
function Write-Success { Write-Host "[OK]    $args" -ForegroundColor Green }
function Write-Warn { Write-Host "[WARN]  $args" -ForegroundColor Yellow }
function Write-Error { Write-Host "[ERROR] $args" -ForegroundColor Red }
function Write-Step { Write-Host ""; Write-Host "==>$args" -ForegroundColor Blue -BackgroundColor White }

# ---------------------------------------------------------------------------
# Banner
# ---------------------------------------------------------------------------
Write-Host ""
Write-Host "  ==============================" -ForegroundColor Cyan
Write-Host "    iterate-harness Installer" -ForegroundColor Cyan
Write-Host "    Windows Native Setup" -ForegroundColor Cyan
Write-Host "  ==============================" -ForegroundColor Cyan
Write-Host ""

# ---------------------------------------------------------------------------
# Parse arguments
# ---------------------------------------------------------------------------
# iterate-harness installs from its git source (clone + editable install);
# -FromSource is kept as a compatibility alias for the upstream flag.
if ($Help) {
    Write-Host "Usage: .\install.ps1 [-FromSource]"
    Write-Host ""
    Write-Host "  -FromSource    Compatibility alias; source install is the default"
    Write-Host "                 (iterate-harness is installed from git in editable mode)."
    exit 0
}

# ---------------------------------------------------------------------------
# Step 1: Check PowerShell version
# ---------------------------------------------------------------------------
Write-Step "Checking PowerShell version"

if ($PSVersionTable.PSVersion.Major -lt 5) {
    Write-Error "PowerShell 5.1 or newer is required."
    Write-Host "  Please upgrade PowerShell or use PowerShell Core (pwsh):"
    Write-Host "    https://github.com/PowerShell/PowerShell"
    exit 1
}

Write-Success "PowerShell $($PSVersionTable.PSVersion) detected"

# ---------------------------------------------------------------------------
# Step 2: Check Python 3.10+
# ---------------------------------------------------------------------------
Write-Step "Checking Python version (3.10+ required)"

$PythonCmd = $null
$PythonCommands = @("python", "python3", "py")

foreach ($cmd in $PythonCommands) {
    $pyPath = Get-Command $cmd -ErrorAction SilentlyContinue
    if ($pyPath) {
        $versionOutput = & $cmd --version 2>&1
        $versionMatch = $versionOutput -match "Python (\d+)\.(\d+)"
        if ($versionMatch) {
            $major = [int]$matches[1]
            $minor = [int]$matches[2]
            if ($major -ge 3 -and $minor -ge 10) {
                $PythonCmd = $cmd
                break
            } elseif ($major -eq 3 -and $minor -lt 10) {
                Write-Warn "Python $major.$minor found but version 3.10+ is required"
            }
        }
    }
}

if (-not $PythonCmd) {
    Write-Error "Python 3.10+ not found."
    Write-Host ""
    Write-Host "  Please install Python 3.10 or newer:"
    Write-Host "    Download from: https://www.python.org/downloads/"
    Write-Host "    Or use winget: winget install Python.Python.3.12"
    Write-Host ""
    exit 1
}

$PyVersion = & $PythonCmd --version 2>&1
Write-Success "Found $PyVersion ($PythonCmd)"

# ---------------------------------------------------------------------------
# Step 3: Check Node.js >= 18 (optional)
# ---------------------------------------------------------------------------
Write-Step "Checking Node.js version (>= 18 required for React TUI)"

$NodeOk = $false
$NodePath = Get-Command node -ErrorAction SilentlyContinue
if ($NodePath) {
    $NodeVersionOutput = & node --version 2>&1
    $NodeVersionMatch = $NodeVersionOutput -match "v(\d+)"
    if ($NodeVersionMatch) {
        $NodeMajor = [int]$matches[1]
        if ($NodeMajor -ge 18) {
            $NodeOk = $true
            Write-Success "Found Node.js $NodeVersionOutput"
        } else {
            Write-Warn "Node.js $NodeVersionOutput is too old (need >= 18). React TUI will be skipped."
        }
    }
} else {
    Write-Warn "Node.js not found. React TUI will be skipped."
    Write-Host "  To enable the React terminal UI, install Node.js 18+:"
    Write-Host "    Download from: https://nodejs.org/"
    Write-Host "    Or use winget: winget install OpenJS.NodeJS.LTS"
}

# ---------------------------------------------------------------------------
# Step 4: Install iterate-harness (git clone + editable install)
# ---------------------------------------------------------------------------
Write-Step "Installing iterate-harness"

$RepoUrl = "https://github.com/jingzhao-l/iterate-harness.git"
$InstallDir = "$env:USERPROFILE\.iterate-harness-src"
$VenvDir = "$env:USERPROFILE\.iterate-harness-venv"

# Create virtual environment
if (Test-Path $VenvDir) {
    Write-Info "Virtual environment already exists at $VenvDir"
} else {
    Write-Info "Creating virtual environment at $VenvDir..."
    & $PythonCmd -m venv $VenvDir
    if (-not (Test-Path $VenvDir)) {
        Write-Error "Failed to create virtual environment"
        exit 1
    }
}

# Activate the venv
$ActivateScript = "$VenvDir\Scripts\Activate.ps1"
if (-not (Test-Path $ActivateScript)) {
    Write-Error "Virtual environment activation script not found: $ActivateScript"
    exit 1
}

Write-Info "Activating virtual environment..."
& $ActivateScript

Write-Success "Virtual environment ready: $VenvDir"

# Install iterate-harness (source install is the default; -FromSource is a
# compatibility alias)
$GitPath = Get-Command git -ErrorAction SilentlyContinue
if (-not $GitPath) {
    Write-Error "git is required to install iterate-harness from source."
    Write-Host "  Install git and retry:"
    Write-Host "    winget install Git.Git"
    Write-Host "    Or download from: https://git-scm.com/download/win"
    exit 1
}

if (Test-Path "$InstallDir\.git") {
    Write-Info "Source directory exists, pulling latest changes..."
    Push-Location $InstallDir
    git pull --ff-only
    Pop-Location
} else {
    Write-Info "Cloning iterate-harness into $InstallDir..."
    git clone $RepoUrl $InstallDir
    if (-not (Test-Path $InstallDir)) {
        Write-Error "Failed to clone repository"
        exit 1
    }
}

Write-Info "Installing in editable mode (pip install -e .)..."
pip install -e $InstallDir --quiet

Write-Success "iterate-harness package installed"

# ---------------------------------------------------------------------------
# Step 5: Install frontend/terminal npm dependencies
# ---------------------------------------------------------------------------
if ($NodeOk) {
    $FrontendDir = "$InstallDir\frontend\terminal"

    if ($FrontendDir -and (Test-Path "$FrontendDir\package.json")) {
        Write-Step "Installing React TUI dependencies"
        Write-Info "Running npm install in $FrontendDir..."
        Push-Location $FrontendDir
        npm install --no-fund --no-audit --silent 2>&1 | Out-Null
        Pop-Location
        Write-Success "React TUI dependencies installed"
    } else {
        Write-Info "No frontend/terminal directory found - skipping npm install"
    }
}

# ---------------------------------------------------------------------------
# Step 6: Create iterate-harness config directory
# ---------------------------------------------------------------------------
Write-Step "Setting up iterate-harness config directory"

$ConfigDir = "$env:USERPROFILE\.iterate-harness"
$SkillsDir = "$ConfigDir\skills"
$PluginsDir = "$ConfigDir\plugins"

New-Item -ItemType Directory -Force -Path $ConfigDir | Out-Null
New-Item -ItemType Directory -Force -Path $SkillsDir | Out-Null
New-Item -ItemType Directory -Force -Path $PluginsDir | Out-Null

Write-Success "Config directory ready: ~/.iterate-harness/"

# ---------------------------------------------------------------------------
# Step 7: Add to PATH (Windows environment variable)
# ---------------------------------------------------------------------------
Write-Step "Setting up PATH integration"

$VenvBinDir = "$VenvDir\Scripts"
$CurrentPath = [Environment]::GetEnvironmentVariable("PATH", "User")

if ($CurrentPath -like "*$VenvBinDir*") {
    Write-Info "PATH already contains $VenvBinDir"
} else {
    Write-Info "Adding $VenvBinDir to user PATH..."
    $NewPath = "$VenvBinDir;$CurrentPath"
    [Environment]::SetEnvironmentVariable("PATH", $NewPath, "User")
    Write-Success "Added $VenvBinDir to PATH"
    Write-Warn "You may need to restart your terminal or log out/log in for PATH changes to take effect."
}

# ---------------------------------------------------------------------------
# Step 8: Verify installation
# ---------------------------------------------------------------------------
Write-Step "Verifying installation"

$OhPath = "$VenvBinDir\ih.exe"
$IterateHarnessPath = "$VenvBinDir\iterate-harness.exe"

# Prefer the dedicated 'iterate-harness' launcher (no PowerShell alias
# collisions); fall back to 'ih' (which collides with PowerShell's
# Out-Host alias unless invoked as ih.exe).
$Launcher = $null
$LauncherExe = $null
if (Test-Path $IterateHarnessPath) {
    $Launcher = "iterate-harness"
    $LauncherExe = $IterateHarnessPath
} elseif (Test-Path $OhPath) {
    $Launcher = "ih"
    $LauncherExe = $OhPath
}

if ($LauncherExe) {
    $OhVersion = & $LauncherExe --version 2>&1
    Write-Success "Installation successful!"
    Write-Host ""
    Write-Host "  $Launcher is ready: $OhVersion" -ForegroundColor Green
    if ($Launcher -eq "ih") {
        Write-Host "  Note: 'ih' collides with PowerShell's built-in Out-Host alias." -ForegroundColor Yellow
        Write-Host "        Invoke it as 'ih.exe', or use 'iterate-harness' instead." -ForegroundColor Yellow
    }
} else {
    # Try module execution
    $ModuleVersion = python -m iterate_harness --version 2>&1
    if ($ModuleVersion) {
        Write-Warn "Launcher commands not yet available on PATH. Run via: python -m iterate_harness"
        Write-Host "  Version: $ModuleVersion"
    } else {
        Write-Warn "Could not verify launcher commands. The package may need a PATH update."
        Write-Host "  Try: python -m iterate_harness --version"
    }
}

# ---------------------------------------------------------------------------
# Done
# ---------------------------------------------------------------------------
Write-Host ""
Write-Host "iterate-harness is installed!" -ForegroundColor Green -BackgroundColor White
Write-Host ""
Write-Host "  Next steps:"
Write-Host "    1. Restart terminal, or run: refreshenv (if using Chocolatey)"
Write-Host "       Or manually refresh: `$env:PATH = [System.Environment]::GetEnvironmentVariable('PATH','User')"
Write-Host "    2. Set your API key:        `$env:ANTHROPIC_API_KEY = 'your_key'"
if ($Launcher -eq "iterate-harness") {
    Write-Host "    3. Launch (PowerShell):     iterate-harness"
} else {
    Write-Host "    3. Launch (PowerShell):     ih.exe"
    Write-Host "       ('ih' alone collides with PowerShell's Out-Host alias — use 'ih.exe' or 'iterate-harness'.)"
}
Write-Host "    4. First run in a project:  /iterate review   (dry-run review)"
Write-Host "       or headless:             iterate-harness iterate init"
Write-Host "    5. Docs:                    https://github.com/jingzhao-l/iterate-harness"
Write-Host ""
