<#
Generate independent numeric-cache fixtures with the .NET Framework BCL only.
Run using WindowsPowerShell/v1.0/powershell.exe, not an EET application assembly.
All outputs use exclusive creation. Supply new paths when reproducing fixtures.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$OutputPath,
    [string]$TablePath
)
$ErrorActionPreference = 'Stop'
foreach ($target in @($OutputPath, $TablePath)) {
    if ($target -and (Test-Path -LiteralPath $target)) { throw "Output already exists: $target" }
}

Add-Type -TypeDefinition @'
using System;
using System.Collections.Generic;
using System.Text.RegularExpressions;
public static class EetBclReferenceRanges {
    public static int[][] Ranges(bool digit) {
        var result = new List<int[]>();
        var regex = new Regex(digit ? @"\d" : @"\AA\b");
        int start = -1;
        for (int cp = 0; cp <= 65536; cp++) {
            bool included = false;
            if (cp < 65536) {
                string text = new string((char)cp, 1);
                included = digit ? regex.IsMatch(text) : !regex.IsMatch("A" + text);
            }
            if (included && start < 0) start = cp;
            if (!included && start >= 0) { result.Add(new int[] { start, cp - 1 }); start = -1; }
        }
        return result.ToArray();
    }
}
'@

function Write-NewJson([string]$Path, $Value) {
    $absolute = [IO.Path]::GetFullPath($Path)
    $json = ConvertTo-Json -InputObject $Value -Depth 20
    $bytes = [Text.Encoding]::UTF8.GetBytes($json + "`n")
    $stream = [IO.File]::Open($absolute, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write)
    try { $stream.Write($bytes, 0, $bytes.Length) } finally { $stream.Dispose() }
}

function Cache-Hex([string]$Template, [string[]]$Values) {
    if ($Values.Count -eq 0) { return '' }
    $stream = New-Object IO.MemoryStream
    $writer = New-Object IO.BinaryWriter($stream, [Text.Encoding]::UTF8)
    try {
        $bytes = [Text.Encoding]::UTF8.GetBytes($Template)
        $writer.Write([uint32]$bytes.Length)
        $writer.Write($bytes)
        $writer.Write([uint32]$Values.Count)
        foreach ($value in $Values) {
            $bytes = [Text.Encoding]::UTF8.GetBytes($value)
            $writer.Write([uint32]$bytes.Length)
            $writer.Write($bytes)
        }
        $writer.Flush()
        return [BitConverter]::ToString($stream.ToArray()).Replace('-', '').ToLowerInvariant()
    } finally { $writer.Dispose(); $stream.Dispose() }
}

$numeric = '[\+\-]?\d+(\.?\d+)?'
$roman = '\b(?=[MDCLXVI]+\b(?!(''| am)))M{0,4}(CM|CD|D?C{0,3})(XC|XL|L?X{0,3})(IX|IV|V?I{0,3})\b(?!(''| am))'
$patterns = @($numeric, ($numeric + '|' + $roman), ($numeric + '|(?!I )' + $roman))
$runtime = [ordered]@{
    powershell_version = $PSVersionTable.PSVersion.ToString()
    clr_version = [Environment]::Version.ToString()
    regex_assembly = [Text.RegularExpressions.Regex].Assembly.FullName
    regex_file_version = [Diagnostics.FileVersionInfo]::GetVersionInfo([Text.RegularExpressions.Regex].Assembly.Location).FileVersion
    culture = [Globalization.CultureInfo]::CurrentCulture.Name
    execution = 'BCL Regex only; no EET executable or application assembly loaded'
}
$vectors = @(
    @{ id = 'ascii_numbers'; text = '0 1 -2 +3 4.5 -0.25 +.5 .5 6. 7e3 1E-4 1,234 1,5 007 --8 +-9'; max = 200 },
    @{ id = 'repeated_numbers'; text = '12 12 0012 12.0 12'; max = 200 },
    @{ id = 'roman_whitespace'; text = "I I`tabc I`nI`rI"; max = 200 },
    @{ id = 'roman_at_end'; text = 'Chapter I'; max = 200 },
    @{ id = 'roman_quotes'; text = 'I'' IV'' "I" "IV" I am IV am I amber'; max = 200 },
    @{ id = 'roman_valid_invalid'; text = 'IV IX XIV XL XC CD CM MMXXIV CIVIL IIII IIIII MMMM MMMMM VX IC IL'; max = 200 },
    @{ id = 'word_adjacency'; text = 'aIV IVa _IV IV_ 1IV IV1 0xFF abc123def'; max = 200 },
    @{ id = 'lowercase_romans'; text = 'i iv xi vi I iV Iv'; max = 200 },
    @{ id = 'no_numbers'; text = 'ordinary text without digits'; max = 200 },
    @{ id = 'empty'; text = ''; max = 200 },
    @{ id = 'bmp_digits'; text = ([string][char]0x0661 + [char]0x0662 + '.' + [char]0x0663 + ' ' + [char]0xFF11 + [char]0xFF12 + ' ' + [char]0x0967 + [char]0x0968); max = 200 },
    @{ id = 'astral_digits'; text = ([char]::ConvertFromUtf32(0x1D7CE) + [char]::ConvertFromUtf32(0x1D7CF) + ' 2'); max = 200 },
    @{ id = 'combining_marks'; text = ('IV' + [char]0x0301 + ' ' + [char]0x0301 + 'IV I' + [char]0x093E + ' ' + [char]0x093E + 'IV'); max = 200 },
    @{ id = 'join_controls'; text = ('IV' + [char]0x200C + ' ' + [char]0x200D + 'IV I' + [char]0x200D + ' ' + [char]0x200C + 'I'); max = 200 },
    @{ id = 'emoji'; text = ([char]::ConvertFromUtf32(0x1F600) + 'IV ' + [char]::ConvertFromUtf32(0x1F600) + '12.5 I' + [char]::ConvertFromUtf32(0x1F600)); max = 200 },
    @{ id = 'more_than_100_matches'; text = ((0..104 | ForEach-Object { [string]$_ }) -join ' '); max = 10000 }
)
$cases = New-Object Collections.Generic.List[object]
foreach ($vector in $vectors) {
    for ($mode = 0; $mode -lt $patterns.Count; $mode++) {
        $regex = New-Object Text.RegularExpressions.Regex($patterns[$mode])
        $all = $regex.Matches($vector.text)
        $selectedMatches = @($all | Select-Object -First 100)
        $template = [string]$vector.text
        for ($index = $selectedMatches.Count - 1; $index -ge 0; $index--) {
            $match = $selectedMatches[$index]
            $template = $template.Remove($match.Index, $match.Length).Insert($match.Index, ('%VAR{0:D2}%' -f $index))
        }
        [string[]]$values = @($selectedMatches | ForEach-Object { $_.Value })
        $cases.Add([ordered]@{
            id = $vector.id + '_mode' + $mode
            pattern_index = $mode
            text = [string]$vector.text
            max_utf16 = $vector.max
            utf16_length = $vector.text.Length
            total_matches = $all.Count
            match_indices_utf16 = @($selectedMatches | ForEach-Object { $_.Index })
            match_lengths_utf16 = @($selectedMatches | ForEach-Object { $_.Length })
            template = $template
            values = @($values)
            payload_hex = Cache-Hex $template $values
        })
    }
}
Write-NewJson $OutputPath ([ordered]@{ schema_version = 1; runtime = $runtime; patterns = $patterns; cases = @($cases.ToArray()) })
if ($TablePath) {
    Write-NewJson $TablePath ([ordered]@{
        schema_version = 1
        runtime = $runtime
        boundary_word_ranges = [EetBclReferenceRanges]::Ranges($false)
        decimal_digit_ranges = [EetBclReferenceRanges]::Ranges($true)
        range_endpoints = 'inclusive BMP UTF-16 code units'
        boundary_probe = 'not Regex.IsMatch("A" + char, @"\AA\b")'
        digit_probe = 'Regex.IsMatch(char, @"\d")'
    })
}
