[CmdletBinding()]
param(
    [string]$RepositoryRoot = '',
    [ValidateSet('stable', 'gray')]
    [string]$Channel = 'stable',
    [string]$CandidateFile = '',
    [string]$LocalApkPath = '',
    [string]$EdenArchive = '',
    [switch]$Force
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$packageName = 'com.tencent.mobileqq'
$discoveryEndpoint = 'https://upage.html5.qq.com/wechat-apkinfo'
$edenVersion = '1.0.8'
$edenReleaseUrl = "https://github.com/MrXiaoM/Eden/releases/download/$edenVersion/Eden-$edenVersion.zip"
$edenReleaseSha256 = '88a6851a07464db99ce725178e6802b1eb2dbb9acfd0c58ae1c8d8a891b47160'
$trustedQqCertificate = 'ea6e97ad6c34f7039a9c6daba732c97d0e098e83ede2b4d52c76eb0184ac7a38'
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

function Assert-GrayApkUrl([string]$Url) {
    $uri = [uri]$Url
    if (-not $uri.IsAbsoluteUri -or $uri.Scheme -ne 'https' -or
        $uri.Host -ne 'downv6.qq.com' -or -not $uri.IsDefaultPort -or $uri.UserInfo -or
        $uri.AbsolutePath -notmatch '^/qqweb/QQ_1/android_apk/[A-Za-z0-9_.-]+\.apk$' -or
        $uri.Query -or $uri.Fragment) {
        throw 'Gray APK URL must be a direct HTTPS QQ APK on downv6.qq.com.'
    }
}

function Get-ManifestMetadata([string]$XmlTree) {
    $metadata = @{}
    $name = $null
    $applicationIndent = -1
    $inMetadata = $false
    foreach ($line in ($XmlTree -split "`n")) {
        if ($line -match '^(\s*)E: application\b') {
            $applicationIndent = $Matches[1].Length
            $inMetadata = $false
            $name = $null
        }
        elseif ($line -match '^(\s*)E: ([^ ]+)') {
            $indent = $Matches[1].Length
            $element = $Matches[2]
            $inMetadata = $applicationIndent -ge 0 -and $indent -eq ($applicationIndent + 2) -and $element -eq 'meta-data'
            if ($indent -le $applicationIndent) { $applicationIndent = -1 }
            $name = $null
        }
        elseif ($inMetadata -and $line -match 'A: android:name\([^)]*\)="([^"]+)"') {
            $name = $Matches[1]
            if ($name -notin @('ANDROID_QQ_BUILD_NO', 'AppSetting_params', 'AppSetting_params_pad')) {
                $name = $null
            }
        }
        elseif ($inMetadata -and $null -ne $name -and $line -match 'A: android:value\([^)]*\)="([^"]*)"') {
            if ($metadata.ContainsKey($name)) { throw "Duplicate manifest metadata '$name'." }
            $metadata[$name] = $Matches[1]
            $name = $null
        }
        elseif ($inMetadata -and $null -ne $name -and $line -match 'A: android:value\([^)]*\)=\(type 0x10\)0x([0-9a-fA-F]+)') {
            if ($metadata.ContainsKey($name)) { throw "Duplicate manifest metadata '$name'." }
            $metadata[$name] = [Convert]::ToInt64($Matches[1], 16).ToString()
            $name = $null
        }
    }
    return $metadata
}

if ($Channel -eq 'gray') {
    if ([string]::IsNullOrWhiteSpace($CandidateFile)) { throw 'Gray updates require -CandidateFile.' }
    $candidate = Get-Content -Raw -LiteralPath $CandidateFile | ConvertFrom-Json
    if ([string]$candidate.version -notmatch '^\d+\.\d+\.\d+\.\d+$') {
        throw 'The gray announcement must report a complete four-part build version.'
    }
    $fullVersion = [string]$candidate.version
    $versionName = ($fullVersion.Split('.')[0..2] -join '.')
    $versionCode = ''
    $downloadUrl = [string]$candidate.url
    Assert-GrayApkUrl $downloadUrl
    $feedUri = [uri]$candidate.feed_url
    $announcementUri = [uri]$candidate.announcement_url
    if ($feedUri.AbsoluteUri -ne 'https://t.me/s/qq_updatelog' -or
        $announcementUri.Scheme -ne 'https' -or $announcementUri.Host -ne 't.me' -or
        $announcementUri.AbsolutePath -notmatch '^/qq_updatelog/\d+$') {
        throw 'Unexpected gray discovery feed or announcement URL.'
    }
    $expectedCertificate = $trustedQqCertificate
    $phoneDestination = Join-Path $repositoryRootPath "android_gray\android_phone\$fullVersion.json"
    $padDestination = Join-Path $repositoryRootPath "android_gray\android_pad\$fullVersion.json"
    $sourceDestination = Join-Path $repositoryRootPath "android_gray\sources\$fullVersion.json"
    Write-Host "Checking Android QQ gray candidate $fullVersion..."
}
else {
if ($CandidateFile -or $LocalApkPath) { throw 'CandidateFile and LocalApkPath are only supported for the gray channel.' }
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
$downloadUrl = [string]$apkInfo.url
if ($downloadUrl.StartsWith('http://', [StringComparison]::OrdinalIgnoreCase)) {
    $downloadUrl = 'https://' + $downloadUrl.Substring(7)
}
if (-not $downloadUrl.StartsWith('https://', [StringComparison]::OrdinalIgnoreCase)) {
    throw "Refusing non-HTTPS APK URL '$downloadUrl'."
}
$expectedCertificate = ([string]$apkInfo.signatureSha256Molo).ToLowerInvariant()

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
    if ($Channel -eq 'gray') { Write-Host "Obtaining and verifying QQ gray $fullVersion..." }
    else { Write-Host "Downloading QQ $versionName ($versionCode) from Tencent..." }
    if ($LocalApkPath) {
        Copy-Item -LiteralPath ([IO.Path]::GetFullPath($LocalApkPath)) -Destination $apkPath
    }
    elseif ($Channel -eq 'gray') {
        # Do not follow an announcement's redirect to an unapproved host.
        Invoke-WebRequest -Uri $downloadUrl -OutFile $apkPath -MaximumRedirection 0 -TimeoutSec 300
    }
    else {
        Invoke-WebRequest -Uri $downloadUrl -OutFile $apkPath
    }

    $actualSize = (Get-Item -LiteralPath $apkPath).Length
    $actualApkSha256 = Get-Sha256 $apkPath
    if ($Channel -eq 'stable') {
        $expectedSize = [int64]$apkInfo.size_byte
        if ($actualSize -ne $expectedSize) {
            throw "APK size mismatch: expected $expectedSize, got $actualSize."
        }
        $expectedApkSha256 = ([string]$apkInfo.sha256).ToLowerInvariant()
        if ($actualApkSha256 -ne $expectedApkSha256) {
            throw "APK SHA-256 mismatch: expected $expectedApkSha256, got $actualApkSha256."
        }
    }
    elseif ($actualSize -le 0) { throw 'Downloaded gray APK is empty.' }

    $aapt = Find-AndroidTool 'aapt'
    $badgingOutput = (& $aapt dump badging $apkPath 2>&1) -join "`n"
    if ($LASTEXITCODE -ne 0 -or $badgingOutput -notmatch "package: name='([^']+)' versionCode='([^']+)' versionName='([^']+)'") {
        throw 'Unable to read the downloaded APK manifest.'
    }
    $manifestPackage = $Matches[1]
    $manifestCode = $Matches[2]
    $manifestVersion = $Matches[3]
    if ($manifestPackage -ne $packageName -or $manifestVersion -ne $versionName -or
        ($Channel -eq 'stable' -and $manifestCode -ne $versionCode)) {
        throw "APK manifest mismatch: package=$($Matches[1]), code=$($Matches[2]), version=$($Matches[3])."
    }
    if ($Channel -eq 'gray') {
        $versionCode = $manifestCode
        $xmlTree = (& $aapt dump xmltree $apkPath AndroidManifest.xml 2>&1) -join "`n"
        if ($LASTEXITCODE -ne 0) { throw 'Unable to read gray manifest metadata.' }
        $manifestMetadata = Get-ManifestMetadata $xmlTree
        foreach ($key in @('ANDROID_QQ_BUILD_NO', 'AppSetting_params', 'AppSetting_params_pad')) {
            if (-not $manifestMetadata.ContainsKey($key)) { throw "Missing gray metadata '$key'." }
        }
        if ("$versionName.$($manifestMetadata['ANDROID_QQ_BUILD_NO'])" -ne $fullVersion) {
            throw 'Gray announcement version does not match the APK build number.'
        }
        if ($manifestMetadata['AppSetting_params'] -notmatch '^(\d{6,12})#[^#]+#[^#]*#(Gray[^#]+)#') {
            throw 'Phone metadata does not identify a gray channel.'
        }
        $phoneSubId = [int64]$Matches[1]
        $manifestChannel = $Matches[2]
        if ($manifestMetadata['AppSetting_params_pad'] -notmatch '^(\d{6,12})#[^#]+#[^#]*#(Gray[^#]+)#') {
            throw 'Pad metadata does not identify a gray channel.'
        }
        $padSubId = [int64]$Matches[1]
        if ($Matches[2] -ne $manifestChannel) { throw 'Phone and Pad gray channels differ.' }
    }

    $apksigner = Find-AndroidTool 'apksigner'
    $signatureOutput = (& $apksigner verify --verbose --print-certs $apkPath 2>&1) -join "`n"
    if ($LASTEXITCODE -ne 0) {
        throw 'APK signature verification failed.'
    }
    if ($signatureOutput -notmatch '(?im)certificate SHA-256 digest:\s*([0-9a-f]{64})') {
        throw 'APK signer certificate SHA-256 was not reported.'
    }
    $actualCertificate = $Matches[1].ToLowerInvariant()
    if ($actualCertificate -ne $expectedCertificate) {
        throw "APK signing certificate mismatch: expected $expectedCertificate, got $actualCertificate."
    }

    if ($Channel -eq 'gray' -and
        (Test-Path -LiteralPath $phoneDestination) -and (Test-Path -LiteralPath $padDestination) -and
        (Test-Path -LiteralPath $sourceDestination)) {
        $existingSource = Get-Content -Raw -LiteralPath $sourceDestination | ConvertFrom-Json
        if ($existingSource.apk.sha256 -ne $actualApkSha256) {
            throw "A different APK is already recorded for gray build $fullVersion; refusing to overwrite."
        }
        if (-not $Force) {
            Write-Host "QQ gray $fullVersion is already verified; nothing to update."
            return
        }
    }

    Write-Host "Downloading and verifying Eden $edenVersion..."
    if ($EdenArchive) { Copy-Item -LiteralPath $EdenArchive -Destination $edenZip }
    else { Invoke-WebRequest -Uri $edenReleaseUrl -OutFile $edenZip }
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
    if ($Channel -eq 'gray') {
        foreach ($result in @($phoneJson, $padJson)) {
            if ([string]$result.sort_version_name -ne $fullVersion) {
                throw 'Eden output does not match the complete gray build version.'
            }
        }
        if ([int64]$phoneJson.app_id -ne $phoneSubId -or [int64]$phoneJson.sub_app_id -ne $phoneSubId -or
            [int64]$padJson.app_id -ne $padSubId -or [int64]$padJson.sub_app_id -ne $padSubId) {
            throw 'Eden SubIDs do not match signed manifest metadata.'
        }
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
            sha1 = (Get-FileHash -LiteralPath $apkPath -Algorithm SHA1).Hash.ToLowerInvariant()
            md5 = (Get-FileHash -LiteralPath $apkPath -Algorithm MD5).Hash.ToLowerInvariant()
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
    if ($Channel -eq 'gray') {
        $sourceRecord['channel'] = 'gray'
        $sourceRecord['full_version'] = $fullVersion
        $sourceRecord['manifest_channel'] = $manifestChannel
        $sourceRecord.discovery.provider = 'Public QQ update feed; Tencent signature verified'
        $sourceRecord.discovery.endpoint = [string]$candidate.feed_url
        $sourceRecord.discovery['announcement_url'] = [string]$candidate.announcement_url
    }
    $sourceRecord | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $sourceDestination -Encoding utf8
    Write-Host "Generated Phone and Pad protocol data for QQ $versionName."
}
finally {
    if (Test-Path -LiteralPath $taskRoot) {
        $cleanupPath = [IO.Path]::GetFullPath($taskRoot)
        $temporaryBase = [IO.Path]::GetFullPath([IO.Path]::GetTempPath()).TrimEnd('\') + '\'
        if (-not $cleanupPath.StartsWith($temporaryBase, [StringComparison]::OrdinalIgnoreCase) -or
            [IO.Path]::GetFileName($cleanupPath) -notmatch '^qq-protocol-\d+\.\d+\.\d+-[0-9a-f]{32}$') {
            throw 'Refusing cleanup outside the owned temporary APK workspace.'
        }
        Remove-Item -LiteralPath $cleanupPath -Recurse -Force
    }
}
