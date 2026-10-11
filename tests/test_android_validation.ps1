$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

# Load only validation functions from the AST, never the network/update body.
$scriptPath = Join-Path $PSScriptRoot '..\scripts\update-protocol.ps1'
$tokens = $null
$parseErrors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile($scriptPath, [ref]$tokens, [ref]$parseErrors)
if ($parseErrors.Count -gt 0) { throw 'Android updater PowerShell syntax is invalid.' }
$functions = $ast.FindAll({ param($node) $node -is [System.Management.Automation.Language.FunctionDefinitionAst] }, $true)
foreach ($function in $functions) {
    if ($function.Name -in @('Assert-GrayApkUrl', 'Get-ManifestMetadata', 'Get-BuildToolsSortKey', 'Convert-ProtocolOutputToLf')) {
        Invoke-Expression $function.Extent.Text
    }
}

function Assert-Throws([scriptblock]$Action) {
    $failed = $false
    try { & $Action | Out-Null }
    catch { $failed = $true }
    if (-not $failed) { throw 'Expected validation to reject the input.' }
}

Assert-GrayApkUrl 'https://downv6.qq.com/qqweb/QQ_1/android_apk/test.apk'
foreach ($url in @(
    'http://downv6.qq.com/qqweb/QQ_1/android_apk/test.apk',
    'https://downv6.qq.com.evil.example/qqweb/QQ_1/android_apk/test.apk',
    'https://attacker@downv6.qq.com/qqweb/QQ_1/android_apk/test.apk',
    'https://downv6.qq.com/qqweb/TIM/android_apk/test.apk',
    'https://downv6.qq.com/qqweb/QQ_1/android_apk/test.apk?redirect=evil'
)) { Assert-Throws { Assert-GrayApkUrl $url } }

$fixture = @'
  E: manifest (line=1)
    E: application (line=2)
      E: meta-data (line=3)
        A: android:name(0x01010003)="ANDROID_QQ_BUILD_NO"
        A: android:value(0x01010024)=(type 0x10)0xa505
      E: meta-data (line=4)
        A: android:name(0x01010003)="AppSetting_params"
        A: android:value(0x01010024)="537397314#key#0#GrayOne#flags"
      E: meta-data (line=5)
        A: android:name(0x01010003)="com.huawei.hms.client.appid"
        A: android:value(0x01010024)="first"
      E: meta-data (line=6)
        A: android:name(0x01010003)="com.huawei.hms.client.appid"
        A: android:value(0x01010024)="second"
      E: activity (line=7)
        E: meta-data (line=8)
          A: android:name(0x01010003)="AppSetting_params"
          A: android:value(0x01010024)="not application metadata"
'@
$metadata = Get-ManifestMetadata $fixture
if ($metadata['ANDROID_QQ_BUILD_NO'] -ne '42245' -or
    $metadata['AppSetting_params'] -ne '537397314#key#0#GrayOne#flags' -or $metadata.Count -ne 2) {
    throw 'Application manifest metadata was not parsed correctly.'
}
$duplicate = $fixture + "`n" + @'
      E: meta-data (line=9)
        A: android:name(0x01010003)="AppSetting_params"
        A: android:value(0x01010024)="conflicting application metadata"
'@
Assert-Throws { Get-ManifestMetadata $duplicate }
if ((Get-BuildToolsSortKey '36.1.0') -le (Get-BuildToolsSortKey '9.0.0')) {
    throw 'Android build-tools versions must sort numerically.'
}
Write-Host 'Android gray URL and manifest validation tests: OK'

$fixturePath = [IO.Path]::GetTempFileName()
try {
    [IO.File]::WriteAllText($fixturePath, "{`r`n  `"version`": `"9.3.75.42245`"`r`n}`r`n", [Text.UTF8Encoding]::new($true))
    Convert-ProtocolOutputToLf $fixturePath
    $bytes = [IO.File]::ReadAllBytes($fixturePath)
    if (13 -in $bytes -or $bytes[0] -ne 123) { throw 'Gray outputs must be BOM-free UTF-8 with LF line endings.' }
    $firstHash = (Get-FileHash -LiteralPath $fixturePath -Algorithm SHA256).Hash
    Convert-ProtocolOutputToLf $fixturePath
    if ((Get-FileHash -LiteralPath $fixturePath -Algorithm SHA256).Hash -ne $firstHash) {
        throw 'LF normalization must be idempotent.'
    }
    Write-Host 'Gray output byte stability tests: OK'
}
finally { Remove-Item -LiteralPath $fixturePath -Force }
