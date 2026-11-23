#!/usr/bin/swift
// Deterministic app artwork by Sugar. No OpenAI mark or external assets.
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
    let inset = side * 0.075
    NSColor(srgbRed: 0.388, green: 0.357, blue: 0.933, alpha: 1).setFill()
    NSBezierPath(roundedRect: NSRect(x: inset, y: inset, width: side-2*inset, height: side-2*inset),
                 xRadius: side*0.20, yRadius: side*0.20).fill()
    let points = [
        NSPoint(x: side*0.32, y: side*0.33),
        NSPoint(x: side*0.68, y: side*0.33),
        NSPoint(x: side*0.50, y: side*0.66)
    ]
    let connections = NSBezierPath()
    connections.move(to: points[0]); connections.line(to: points[2])
    connections.line(to: points[1]); connections.line(to: points[0])
    connections.lineWidth = max(1.0, side*0.033)
    connections.lineCapStyle = .round
    connections.lineJoinStyle = .round
    NSColor.white.withAlphaComponent(0.94).setStroke()
    connections.stroke()
    for p in points {
        let diameter = side*0.135
        let circle = NSBezierPath(ovalIn: NSRect(x:p.x-diameter/2, y:p.y-diameter/2, width:diameter, height:diameter))
        NSColor(srgbRed:0.388, green:0.357, blue:0.933, alpha:1).setFill()
        circle.fill()
        NSColor.white.setStroke()
        circle.lineWidth = max(1.0, side*0.030)
        circle.stroke()
    }
    NSGraphicsContext.restoreGraphicsState()
    guard let data = bitmap.representation(using: .png, properties: [:]) else {
        fatalError("Could not encode app icon")
    }
    try data.write(to: output.appendingPathComponent(name), options: .atomic)
}
