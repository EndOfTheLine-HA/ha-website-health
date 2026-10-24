# Generate the community integration icon: a ring crossed by a pulse line.
Add-Type -AssemblyName System.Drawing
$outDir = Join-Path $PSScriptRoot '..\custom_components\website_health\brand'
New-Item -ItemType Directory -Path $outDir -Force | Out-Null

foreach ($size in @(256, 512)) {
    $bitmap = [Drawing.Bitmap]::new($size, $size)
    $graphics = [Drawing.Graphics]::FromImage($bitmap)
    $tile = [Drawing.Drawing2D.GraphicsPath]::new()
    $background = [Drawing.SolidBrush]::new([Drawing.Color]::FromArgb(21, 128, 61))
    $globe = [Drawing.Pen]::new([Drawing.Color]::White, 11)
    $pulse = [Drawing.Pen]::new([Drawing.Color]::White, 14)
    try {
        $graphics.SmoothingMode = [Drawing.Drawing2D.SmoothingMode]::AntiAlias
        $graphics.Clear([Drawing.Color]::Transparent)
        $graphics.ScaleTransform(($size / 256.0), ($size / 256.0))
        $r = 56
        $tile.AddArc(12, 12, $r, $r, 180, 90)
        $tile.AddArc(244 - $r, 12, $r, $r, 270, 90)
        $tile.AddArc(244 - $r, 244 - $r, $r, $r, 0, 90)
        $tile.AddArc(12, 244 - $r, $r, $r, 90, 90)
        $tile.CloseFigure()
        $graphics.FillPath($background, $tile)
        $graphics.DrawEllipse($globe, 63, 63, 130, 130)
        $pulse.StartCap = [Drawing.Drawing2D.LineCap]::Round
        $pulse.EndCap = [Drawing.Drawing2D.LineCap]::Round
        $pulse.LineJoin = [Drawing.Drawing2D.LineJoin]::Round
        $points = [Drawing.PointF[]]@(
            [Drawing.PointF]::new(40, 128), [Drawing.PointF]::new(92, 128),
            [Drawing.PointF]::new(108, 92), [Drawing.PointF]::new(130, 168),
            [Drawing.PointF]::new(150, 106), [Drawing.PointF]::new(164, 128),
            [Drawing.PointF]::new(216, 128)
        )
        $graphics.DrawLines($pulse, $points)
        $filename = if ($size -eq 256) { 'icon.png' } else { 'icon@2x.png' }
        $bitmap.Save((Join-Path $outDir $filename), [Drawing.Imaging.ImageFormat]::Png)
    }
    finally {
        $pulse.Dispose()
        $globe.Dispose()
        $background.Dispose()
        $tile.Dispose()
        $graphics.Dispose()
        $bitmap.Dispose()
    }
}
