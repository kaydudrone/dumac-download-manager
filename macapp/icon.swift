// Draws the app icon (1024 px): blue rounded square, white download arrow over a tray. Original artwork.
import Cocoa

let size = 1024
let rep = NSBitmapImageRep(bitmapDataPlanes: nil, pixelsWide: size, pixelsHigh: size, bitsPerSample: 8,
                           samplesPerPixel: 4, hasAlpha: true, isPlanar: false, colorSpaceName: .deviceRGB,
                           bytesPerRow: 0, bitsPerPixel: 0)!
NSGraphicsContext.saveGraphicsState()
NSGraphicsContext.current = NSGraphicsContext(bitmapImageRep: rep)

// macOS icon grid: 824 px tile, ~185 px corner radius, centred.
let tile = NSRect(x: 100, y: 100, width: 824, height: 824)
let shape = NSBezierPath(roundedRect: tile, xRadius: 185, yRadius: 185)
NSGradient(starting: NSColor(srgbRed: 0.27, green: 0.56, blue: 0.98, alpha: 1),
           ending: NSColor(srgbRed: 0.09, green: 0.29, blue: 0.80, alpha: 1))!.draw(in: shape, angle: -90)

NSColor.white.setFill()
let arrow = NSBezierPath()            // shaft + head, pointing down
arrow.move(to: NSPoint(x: 452, y: 760))
arrow.line(to: NSPoint(x: 572, y: 760))
arrow.line(to: NSPoint(x: 572, y: 520))
arrow.line(to: NSPoint(x: 690, y: 520))
arrow.line(to: NSPoint(x: 512, y: 330))
arrow.line(to: NSPoint(x: 334, y: 520))
arrow.line(to: NSPoint(x: 452, y: 520))
arrow.close()
arrow.fill()
NSBezierPath(roundedRect: NSRect(x: 300, y: 230, width: 424, height: 56), xRadius: 28, yRadius: 28).fill() // tray

NSGraphicsContext.restoreGraphicsState()
try! rep.representation(using: .png, properties: [:])!.write(to: URL(fileURLWithPath: CommandLine.arguments[1]))
