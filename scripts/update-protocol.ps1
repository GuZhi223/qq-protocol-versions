[CmdletBinding()]
param(
    [string]$RepositoryRoot = '',
    [switch]$Force
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$packageName = 'com.tencent.mobileqq'
$discoveryEndpoint = 'https://upage.html5.qq.com/wechat-apkinfo'
$edenVersion = '1.0.8'
$edenReleaseUrl = "https://github.com/MrXiaoM/Eden/releases/download/$edenVersion/Eden-$edenVersion.zip"
$edenReleaseSha256 = '88a6851a07464db99ce725178e6802b1eb2dbb9acfd0c58ae1c8d8a891b47160'
if ([string]::IsNullOrWhiteSpace($RepositoryRoot)) {
    $RepositoryRoot = Join-Path $PSScriptRoot '..'
}
$repositoryRootPath = [IO.Path]::GetFullPath($RepositoryRoot)

function Get-Sha256([string]$Path) {
    return (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
}

function Get-BuildToolsSortKey([string]$Name) {
    # Sort key must order build-tools numerically. A plain string sort puts
    # '9.0.0' above '34.0.0', so each dotted component is padded to a fixed
    # width and the result is compared lexicographically.
    $match = [regex]::Match($Name, '^\d+(\.\d+)*')
    if (-not $match.Success) {
        return '0000000000.0000000000'
    }
    $parts = @($match.Value.Split('.') | ForEach-Object { $_.PadLeft(10, '0') })
    while ($parts.Count -lt 2) {
        $parts += '0000000000'
    }
    return ($parts -join '.')
}

function Find-AndroidTool([string]$Name) {
    $command = Get-Command $Name -ErrorAction SilentlyContinue
    if ($null -ne $command) {
        return $command.Source
    }

    $sdkRoots = @(
        $env:ANDROID_HOME
        $env:ANDROID_SDK_ROOT
        (Join-Path $env:LOCALAPPDATA 'Android\Sdk')
        (Join-Path ${env:ProgramFiles} 'Android\android-sdk')
    ) |
        Where-Object { -not [string]::IsNullOrWhiteSpace($_) -and (Test-Path -LiteralPath $_) } |
        Select-Object -Unique
    foreach ($sdkRoot in $sdkRoots) {
        $buildToolsRoot = Join-Path $sdkRoot 'build-tools'
        if (-not (Test-Path -LiteralPath $buildToolsRoot -PathType Container)) {
            continue
        }
        $versions = Get-ChildItem -LiteralPath $buildToolsRoot -Directory |
            Sort-Object -Property @{ Expression = { Get-BuildToolsSortKey $_.Name } } -Descending
        foreach ($version in $versions) {
            foreach ($extension in @('.exe', '.bat', '')) {
                $candidate = Join-Path $version.FullName ($Name + $extension)
                if (Test-Path -LiteralPath $candidate -PathType Leaf) {
                    return $candidate
                }
            }
        }
    }
    throw "Android SDK tool '$Name' was not found. Install Android SDK Build Tools."
}

Write-Host 'Discovering the latest official Android QQ package...'
$requestBody = @{ packagename = $packageName } | ConvertTo-Json -Compress
$discovery = Invoke-RestMethod -Method Post -Uri $discoveryEndpoint `
    -ContentType 'application/json; charset=utf-8' -Body $requestBody

if ([int]$discovery.ret -ne 0) {
    throw "Tencent discovery endpoint returned ret=$($discovery.ret): $($discovery.err_msg)"
}

$record = $discovery.app_detail_records.$packageName
if ($null -eq $record) {
    throw "Tencent discovery response did not contain '$packageName'."
}
$appInfo = $record.app_info
$apkInfo = $record.apk_all_data
$versionName = [string]$apkInfo.version_name
$versionCode = [string]$apkInfo.version_code

if ([string]$apkInfo.package_name -ne $packageName -or [int]$appInfo.is_official -ne 1 -or [int]$appInfo.is_tencent_app -ne 1) {
    throw 'Discovery response did not identify an official Tencent QQ package.'
}
if ($versionName -notmatch '^\d+\.\d+\.\d+$') {
    throw "Unexpected QQ version name '$versionName'."
}

$phoneDestination = Join-Path $repositoryRootPath "android_phone\$versionName.json"
$padDestination = Join-Path $repositoryRootPath "android_pad\$versionName.json"
$sourceDestination = Join-Path $repositoryRootPath "sources\$versionName.json"
if (-not $Force -and (Test-Path -LiteralPath $phoneDestination) -and
    (Test-Path -LiteralPath $padDestination) -and (Test-Path -LiteralPath $sourceDestination)) {
    Write-Host "QQ $versionName is already present; nothing to update."
    exit 0
}

$taskRoot = Join-Path ([IO.Path]::GetTempPath()) ("qq-protocol-{0}-{1}" -f $versionName, [guid]::NewGuid().ToString('N'))
$edenZip = Join-Path $taskRoot "Eden-$edenVersion.zip"
$edenRoot = Join-Path $taskRoot 'eden'
$runRoot = Join-Path $taskRoot 'run'
$apkPath = Join-Path $runRoot 'qq.apk'
$phoneTemporary = Join-Path $taskRoot 'android_phone.json'
$padTemporary = Join-Path $taskRoot 'android_pad.json'

New-Item -ItemType Directory -Force -Path $edenRoot, $runRoot | Out-Null
try {
    Write-Host "Downloading QQ $versionName ($versionCode) from Tencent..."
    $downloadUrl = [string]$apkInfo.url
    if ($downloadUrl.StartsWith('http://', [StringComparison]::OrdinalIgnoreCase)) {
        $downloadUrl = 'https://' + $downloadUrl.Substring(7)
    }
    if (-not $downloadUrl.StartsWith('https://', [StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing non-HTTPS APK URL '$downloadUrl'."
    }
    Invoke-WebRequest -Uri $downloadUrl -OutFile $apkPath

    $actualSize = (Get-Item -LiteralPath $apkPath).Length
    $expectedSize = [int64]$apkInfo.size_byte
    if ($actualSize -ne $expectedSize) {
        throw "APK size mismatch: expected $expectedSize, got $actualSize."
    }
    $actualApkSha256 = Get-Sha256 $apkPath
    $expectedApkSha256 = ([string]$apkInfo.sha256).ToLowerInvariant()
    if ($actualApkSha256 -ne $expectedApkSha256) {
        throw "APK SHA-256 mismatch: expected $expectedApkSha256, got $actualApkSha256."
    }

    $aapt = Find-AndroidTool 'aapt'
    $badgingOutput = (& $aapt dump badging $apkPath 2>&1) -join "`n"
    if ($LASTEXITCODE -ne 0 -or $badgingOutput -notmatch "package: name='([^']+)' versionCode='([^']+)' versionName='([^']+)'") {
        throw 'Unable to read the downloaded APK manifest.'
    }
    if ($Matches[1] -ne $packageName -or $Matches[2] -ne $versionCode -or $Matches[3] -ne $versionName) {
        throw "APK manifest mismatch: package=$($Matches[1]), code=$($Matches[2]), version=$($Matches[3])."
    }

    $apksigner = Find-AndroidTool 'apksigner'
    $signatureOutput = (& $apksigner verify --verbose --print-certs $apkPath 2>&1) -join "`n"
    if ($LASTEXITCODE -ne 0) {
        throw 'APK signature verification failed.'
    }
    $expectedCertificate = ([string]$apkInfo.signatureSha256Molo).ToLowerInvariant()
    if ($signatureOutput -notmatch '(?im)certificate SHA-256 digest:\s*([0-9a-f]{64})') {
        throw 'APK signer certificate SHA-256 was not reported.'
    }
    $actualCertificate = $Matches[1].ToLowerInvariant()
    if ($actualCertificate -ne $expectedCertificate) {
        throw "APK signing certificate mismatch: expected $expectedCertificate, got $actualCertificate."
    }

    Write-Host "Downloading and verifying Eden $edenVersion..."
    Invoke-WebRequest -Uri $edenReleaseUrl -OutFile $edenZip
    $actualEdenSha256 = Get-Sha256 $edenZip
    if ($actualEdenSha256 -ne $edenReleaseSha256) {
        throw "Eden SHA-256 mismatch: expected $edenReleaseSha256, got $actualEdenSha256."
    }
    Expand-Archive -LiteralPath $edenZip -DestinationPath $edenRoot -Force
    $edenCli = Join-Path $edenRoot 'Eden.CLI.exe'
    if (-not (Test-Path -LiteralPath $edenCli -PathType Leaf)) {
        throw 'Eden CLI was not present in the verified release archive.'
    }

    Write-Host "Extracting QQ $versionName protocol data with Eden..."
    Push-Location -LiteralPath $edenRoot
    try {
        & '.\Eden.CLI.exe' --working-dir $runRoot --eden-apk 'qq.apk' `
            --phone-override $phoneTemporary --pad-override $padTemporary
        if ($LASTEXITCODE -ne 0) {
            throw "Eden exited with code $LASTEXITCODE."
        }
    }
    finally {
        Pop-Location
    }

    foreach ($output in @($phoneTemporary, $padTemporary)) {
        if (-not (Test-Path -LiteralPath $output -PathType Leaf)) {
            throw "Eden did not create '$output'."
        }
    }
    $phoneJson = Get-Content -Raw -LiteralPath $phoneTemporary | ConvertFrom-Json
    $padJson = Get-Content -Raw -LiteralPath $padTemporary | ConvertFrom-Json
    foreach ($result in @($phoneJson, $padJson)) {
        if ([string]$result.apk_id -ne $packageName -or
            -not ([string]$result.sort_version_name).StartsWith("$versionName.")) {
            throw 'Eden output identity did not match the downloaded QQ package.'
        }
    }
    if ([int]$phoneJson.protocol_type -ne 1 -or [int]$padJson.protocol_type -ne 6) {
        throw 'Eden outputs did not contain the expected Phone/Pad protocol types.'
    }

    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $phoneDestination), `
        (Split-Path -Parent $padDestination), (Split-Path -Parent $sourceDestination) | Out-Null
    Copy-Item -LiteralPath $phoneTemporary -Destination $phoneDestination -Force
    Copy-Item -LiteralPath $padTemporary -Destination $padDestination -Force

    $sourceRecord = [ordered]@{
        schema_version = 1
        discovered_at = (Get-Date).ToUniversalTime().ToString('o')
        discovery = [ordered]@{
            provider = 'Tencent App Store metadata'
            endpoint = $discoveryEndpoint
        }
        apk = [ordered]@{
            package_name = $packageName
            version_name = $versionName
            version_code = [int64]$versionCode
            url = $downloadUrl
            size_byte = $actualSize
            sha256 = $actualApkSha256
            sha1 = ([string]$apkInfo.sha1).ToLowerInvariant()
            md5 = ([string]$apkInfo.apk_md5).ToLowerInvariant()
            signer_certificate_sha256 = $actualCertificate
        }
        extractor = [ordered]@{
            name = 'MrXiaoM/Eden'
            version = $edenVersion
            release_url = $edenReleaseUrl
            release_sha256 = $actualEdenSha256
        }
        outputs = [ordered]@{
            android_phone_sha256 = Get-Sha256 $phoneDestination
            android_pad_sha256 = Get-Sha256 $padDestination
        }
    }
    $sourceRecord | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $sourceDestination -Encoding utf8
    Write-Host "Generated Phone and Pad protocol data for QQ $versionName."
}
finally {
    if (Test-Path -LiteralPath $taskRoot) {
        Remove-Item -LiteralPath $taskRoot -Recurse -Force
    }
}
