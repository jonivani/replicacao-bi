# ===========================================================================
# Instala as tarefas agendadas no Windows para replicação Protheus BI
# Execute como Administrador
#
# Exemplos:
# .\instalar_tarefas.ps1
# .\instalar_tarefas.ps1 -Tarefa todas
# .\instalar_tarefas.ps1 -Tarefa diff
# .\instalar_tarefas.ps1 -Tarefa completo
# .\instalar_tarefas.ps1 -Tarefa integridade
# ===========================================================================

param(
    [string]$PythonExe = "python",

    [string]$ScriptDir = (
        Resolve-Path (
            Join-Path $PSScriptRoot "..\scripts"
        )
    ).Path,

    [ValidateSet('todas', 'diff', 'completo', 'integridade')]
    [string]$Tarefa = 'todas'
)

$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$ScriptPath  = Join-Path $ScriptDir "replicacao.py"
$ConfigPath  = Join-Path $ProjectRoot "config\config.ini"

$VenvPython  = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
if ($PythonExe -eq "python" -and (Test-Path $VenvPython)) {
    $PythonExe = $VenvPython
    Write-Host "[INFO] Ambiente virtual detectado. Configurando tarefa com: $PythonExe" -ForegroundColor Yellow
}

if (-not (Test-Path $ScriptPath)) {
    Write-Error "replicacao.py não encontrado em: $ScriptPath"
    exit 1
}

# Configurações comuns para todas as tarefas
$settings = New-ScheduledTaskSettingsSet `
    -ExecutionTimeLimit (New-TimeSpan -Hours 1) `
    -RestartCount 2 `
    -RestartInterval (New-TimeSpan -Minutes 10) `
    -StartWhenAvailable

Write-Host ""
Write-Host "Instalando tarefa(s): $Tarefa" -ForegroundColor Cyan
Write-Host ""

# ---------------------------------------------------------------------------
# Tarefa DIFF
# ---------------------------------------------------------------------------
if ($Tarefa -eq 'todas' -or $Tarefa -eq 'diff') {

    $nomeDiff = "Protheus BI - Replicacao DIFF"

    $acaoDiff = New-ScheduledTaskAction `
        -Execute "`"$PythonExe`"" `
        -Argument "`"$ScriptPath`" --modo diff --config `"$ConfigPath`"" `
        -WorkingDirectory $ScriptDir

    $gatilhoDiff = New-ScheduledTaskTrigger `
        -Once `
        -At (Get-Date).Date `
        -RepetitionInterval (New-TimeSpan -Minutes 5)

    Unregister-ScheduledTask `
        -TaskName $nomeDiff `
        -Confirm:$false `
        -ErrorAction SilentlyContinue

    Register-ScheduledTask `
        -TaskName $nomeDiff `
        -Action $acaoDiff `
        -Trigger $gatilhoDiff `
        -Settings $settings `
        -RunLevel Highest `
        -Description "Insere apenas registros novos" | Out-Null

    Write-Host "[OK] Tarefa criada: $nomeDiff (a cada 5 minutos)" -ForegroundColor Green
}

# ---------------------------------------------------------------------------
# Tarefa COMPLETO
# ---------------------------------------------------------------------------
if ($Tarefa -eq 'todas' -or $Tarefa -eq 'completo') {

    $nomeCompleto = "Protheus BI - Replicacao COMPLETO"

    $acaoCompleto = New-ScheduledTaskAction `
        -Execute "`"$PythonExe`"" `
        -Argument "`"$ScriptPath`" --modo completo --smart --config `"$ConfigPath`"" `
        -WorkingDirectory $ScriptDir

    $gatilhoCompleto = New-ScheduledTaskTrigger `
        -Daily `
        -At "02:00"

    Unregister-ScheduledTask `
        -TaskName $nomeCompleto `
        -Confirm:$false `
        -ErrorAction SilentlyContinue

    Register-ScheduledTask `
        -TaskName $nomeCompleto `
        -Action $acaoCompleto `
        -Trigger $gatilhoCompleto `
        -Settings $settings `
        -RunLevel Highest `
        -Description "Recarga total das tabelas" | Out-Null

    Write-Host "[OK] Tarefa criada: $nomeCompleto (diariamente às 02:00)" -ForegroundColor Green
}

# ---------------------------------------------------------------------------
# Tarefa INTEGRIDADE
# ---------------------------------------------------------------------------
if ($Tarefa -eq 'todas' -or $Tarefa -eq 'integridade') {

    $validadorScript = Join-Path $ScriptDir "validador_integridade.py"

    if (-not (Test-Path $validadorScript)) {
        Write-Warning "validador_integridade.py não encontrado em: $validadorScript"
    }
    else {

        $nomeValidacao = "Protheus BI - Validacao Integridade"

        $acaoValidacao = New-ScheduledTaskAction `
            -Execute "`"$PythonExe`"" `
            -Argument "`"$validadorScript`"" `
            -WorkingDirectory $ScriptDir

        $gatilhoValidacao = New-ScheduledTaskTrigger `
            -Daily `
            -At "06:00"

        Unregister-ScheduledTask `
            -TaskName $nomeValidacao `
            -Confirm:$false `
            -ErrorAction SilentlyContinue

        Register-ScheduledTask `
            -TaskName $nomeValidacao `
            -Action $acaoValidacao `
            -Trigger $gatilhoValidacao `
            -Settings $settings `
            -RunLevel Highest `
            -Description "Valida integridade entre os bancos, corrige automaticamente duplicados e divergências, e envia e-mail" | Out-Null

        Write-Host "[OK] Tarefa criada: $nomeValidacao (diariamente às 06:00)" -ForegroundColor Green
    }
}

Write-Host ""
Write-Host "Instalação concluída para: $Tarefa" -ForegroundColor Cyan
Write-Host ""