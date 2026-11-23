#!/usr/bin/swift
// Original Codex Local MCP artwork by Sugar.
// A rounded local window opens into one connection. Drawn from our own paths;
// no font glyphs, SF Symbols, third-party marks, or external image dependencies.
import AppKit
import Foundation

guard CommandLine.arguments.count == 2 else {
    fputs("Usage: generate-app-icon.swift OUTPUT.iconset\n", stderr)
    exit(2)
}
let output = URL(fileURLWithPath: CommandLine.arguments[1], isDirectory: true)
try FileManager.default.createDirectory(at: output, withIntermediateDirectories: true)
let entries: [(String, Int)] = [
    ("icon_16x16.png", 16), ("icon_16x16@2x.png", 32),
    ("icon_32x32.png", 32), ("icon_32x32@2x.png", 64),
    ("icon_128x128.png", 128), ("icon_128x128@2x.png", 256),
    ("icon_256x256.png", 256), ("icon_256x256@2x.png", 512),
    ("icon_512x512.png", 512), ("icon_512x512@2x.png", 1024)
]

func color(_ red: CGFloat, _ green: CGFloat, _ blue: CGFloat, _ alpha: CGFloat = 1) -> NSColor {
    NSColor(srgbRed: red / 255, green: green / 255, blue: blue / 255, alpha: alpha)
}

func drawArtwork(pixels: Int) {
    // Work in a fixed design coordinate system so every exported size is a
    // fresh vector render. Optical adjustments keep the 16/32px mark readable.
    let small = pixels <= 32
    let plate = NSBezierPath(
        roundedRect: NSRect(x: 78, y: 78, width: 868, height: 868),
        xRadius: 198, yRadius: 198)
    if !small {
        NSGraphicsContext.saveGraphicsState()
        let shadow = NSShadow()
        shadow.shadowColor = color(25, 18, 67, 0.28)
        shadow.shadowBlurRadius = 30
        shadow.shadowOffset = NSSize(width: 0, height: -15)
        shadow.set()
        color(67, 51, 154).setFill()
        plate.fill()
        NSGraphicsContext.restoreGraphicsState()
    }

    let surface = NSGradient(starting: color(48, 35, 115), ending: color(118, 99, 245))!
    surface.draw(in: plate, angle: 120)

    if pixels >= 64 {
        NSGraphicsContext.saveGraphicsState()
        plate.addClip()
        let glow = NSBezierPath(ovalIn: NSRect(x: 42, y: 602, width: 890, height: 640))
        NSGradient(starting: color(204, 197, 255, 0.13),
                   ending: color(204, 197, 255, 0))!.draw(in: glow, angle: 270)
        NSGraphicsContext.restoreGraphicsState()
        let rim = NSBezierPath(roundedRect: NSRect(x: 81, y: 81, width: 862, height: 862),
                               xRadius: 196, yRadius: 196)
        color(255, 255, 255, 0.17).setStroke()
        rim.lineWidth = 3
        rim.stroke()
    }

    // An open local window also reads as C. The unequal terminal lengths and
    // the offset connection give the mark its own silhouette at Dock sizes.
    let window = NSBezierPath()
    window.move(to: NSPoint(x: 670, y: 714))
    window.line(to: NSPoint(x: 419, y: 714))
    window.curve(to: NSPoint(x: 299, y: 594),
                 controlPoint1: NSPoint(x: 353, y: 714),
                 controlPoint2: NSPoint(x: 299, y: 660))
    window.line(to: NSPoint(x: 299, y: 430))
    window.curve(to: NSPoint(x: 419, y: 310),
                 controlPoint1: NSPoint(x: 299, y: 364),
                 controlPoint2: NSPoint(x: 353, y: 310))
    window.line(to: NSPoint(x: 625, y: 310))
    window.lineWidth = small ? 112 : 96
    window.lineCapStyle = .round
    window.lineJoinStyle = .round

    if !small {
        NSGraphicsContext.saveGraphicsState()
        let shadow = NSShadow()
        shadow.shadowColor = color(27, 17, 77, 0.28)
        shadow.shadowBlurRadius = 16
        shadow.shadowOffset = NSSize(width: 0, height: -8)
        shadow.set()
        color(247, 247, 255).setStroke()
        window.stroke()
        NSGraphicsContext.restoreGraphicsState()
    } else {
        color(249, 249, 255).setStroke()
        window.stroke()
    }

    // One clear connection, entering the window; no tiny network-node cluster.
    let thickness: CGFloat = small ? 102 : 86
    let link = NSBezierPath(roundedRect: NSRect(x: 484, y: 508 - thickness / 2,
                                               width: 296, height: thickness),
                            xRadius: thickness / 2, yRadius: thickness / 2)
    NSGradient(starting: color(202, 255, 244), ending: color(89, 230, 210))!
        .draw(in: link, angle: 0)
    let terminal = NSBezierPath(ovalIn: NSRect(x: 719, y: 448, width: 120, height: 120))
    NSGradient(starting: color(99, 234, 217), ending: color(180, 255, 235))!
        .draw(in: terminal, angle: 90)
    if pixels >= 64 {
        let center = NSBezierPath(ovalIn: NSRect(x: 760, y: 489, width: 38, height: 38))
        color(43, 83, 118).setFill()
        center.fill()
    }
}

for (name, pixels) in entries {
    let side = CGFloat(pixels)
    guard let bitmap = NSBitmapImageRep(bitmapDataPlanes: nil,
        pixelsWide: pixels, pixelsHigh: pixels, bitsPerSample: 8, samplesPerPixel: 4,
        hasAlpha: true, isPlanar: false, colorSpaceName: .deviceRGB,
        bytesPerRow: 0, bitsPerPixel: 0),
        let context = NSGraphicsContext(bitmapImageRep: bitmap) else {
        fatalError("Could not allocate app icon")
    }
    bitmap.size = NSSize(width: side, height: side)
    NSGraphicsContext.saveGraphicsState()
    NSGraphicsContext.current = context
    context.shouldAntialias = true
    NSColor.clear.setFill()
    NSRect(x: 0, y: 0, width: side, height: side).fill(using: .copy)
    let transform = NSAffineTransform()
    transform.scale(by: side / 1024)
    transform.concat()
    drawArtwork(pixels: pixels)
    NSGraphicsContext.restoreGraphicsState()
    guard let data = bitmap.representation(using: .png, properties: [:]) else {
        fatalError("Could not encode app icon")
    }
    try data.write(to: output.appendingPathComponent(name), options: .atomic)
}
