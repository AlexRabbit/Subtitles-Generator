# Allow Subtitles Generator through Windows Firewall (run as Administrator)
$ruleName = "Subtitles Generator"
$port = 8765
$existing = Get-NetFirewallRule -DisplayName $ruleName -ErrorAction SilentlyContinue
if ($existing) {
    Write-Host "[OK] Firewall rule already exists: $ruleName"
    exit 0
}
New-NetFirewallRule -DisplayName $ruleName -Direction Inbound -Protocol TCP -LocalPort $port -Action Allow | Out-Null
Write-Host "[OK] Firewall rule added for TCP port $port"
