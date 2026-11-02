import Foundation
import Vision
import AppKit

// macOS Vision による文字認識(ローカル、外部通信なし)。
// usage: vision_ocr <img1> <img2> ...
// stdout: 画像ごとに JSON 1行 {"path":..., "lines":[{"t":text,"c":conf,"x":..,"y":..,"w":..,"h":..}]}
// 座標は 0〜1 に正規化(原点は画像の左上、y は下向き)。
let paths = Array(CommandLine.arguments.dropFirst())
if paths.isEmpty {
    FileHandle.standardError.write("usage: vision_ocr <img>...\n".data(using: .utf8)!)
    exit(2)
}
var fails = 0
for path in paths {
    autoreleasepool {
        var lines: [[String: Any]] = []
        if let img = NSImage(contentsOfFile: path),
           let cg = img.cgImage(forProposedRect: nil, context: nil, hints: nil) {
            let req = VNRecognizeTextRequest()
            req.recognitionLevel = .accurate
            req.recognitionLanguages = ["ja-JP", "en-US"]
            req.usesLanguageCorrection = false  // 店舗No・品番の英数字を言語補正で書き換えさせない
            let handler = VNImageRequestHandler(cgImage: cg, options: [:])
            do {
                try handler.perform([req])
                for o in req.results ?? [] {
                    guard let c = o.topCandidates(1).first else { continue }
                    let b = o.boundingBox
                    lines.append(["t": c.string, "c": Double(c.confidence), "x": Double(b.minX),
                                  "y": Double(1.0 - b.maxY), "w": Double(b.width), "h": Double(b.height)])
                }
            } catch { fails += 1 }
        } else { fails += 1 }
        let obj: [String: Any] = ["path": path, "lines": lines]
        if let d = try? JSONSerialization.data(withJSONObject: obj, options: []),
           let s = String(data: d, encoding: .utf8) { print(s) }
    }
}
exit(fails > 0 ? 1 : 0)
