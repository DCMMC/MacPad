import AppKit
import Foundation
import Vision

guard CommandLine.arguments.count == 2 ||
      (CommandLine.arguments.count == 3 &&
       CommandLine.arguments[1] == "--json") else {
    FileHandle.standardError.write(
        Data("usage: vision_ocr.swift [--json] <image>\n".utf8)
    )
    exit(2)
}

let jsonOutput = CommandLine.arguments.count == 3
let url = URL(fileURLWithPath: CommandLine.arguments.last!)
guard let image = NSImage(contentsOf: url),
      let tiff = image.tiffRepresentation,
      let bitmap = NSBitmapImageRep(data: tiff),
      let cgImage = bitmap.cgImage else {
    FileHandle.standardError.write(
        Data("vision_ocr: failed to decode image\n".utf8)
    )
    exit(3)
}

let request = VNRecognizeTextRequest()
request.recognitionLevel = .accurate
request.usesLanguageCorrection = false

do {
    try VNImageRequestHandler(cgImage: cgImage).perform([request])
} catch {
    FileHandle.standardError.write(
        Data("vision_ocr: \(error)\n".utf8)
    )
    exit(4)
}

if jsonOutput {
    var items: [[String: Any]] = []
    for observation in request.results ?? [] {
        if let candidate = observation.topCandidates(1).first {
            let box = observation.boundingBox
            items.append([
                "text": candidate.string,
                "x": box.origin.x,
                "y": box.origin.y,
                "width": box.size.width,
                "height": box.size.height,
            ])
        }
    }
    let data = try JSONSerialization.data(withJSONObject: items)
    FileHandle.standardOutput.write(data)
    FileHandle.standardOutput.write(Data("\n".utf8))
} else {
    for observation in request.results ?? [] {
        if let candidate = observation.topCandidates(1).first {
            print(candidate.string)
        }
    }
}
